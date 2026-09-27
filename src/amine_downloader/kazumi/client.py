"""Execute Kazumi rules: search works and enumerate episodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit

import httpx
from lxml import html as lxml_html
from lxml.etree import XPathError

from ..errors import AmineError
from .jsonpath import JsonPathError, first, query, render
from .rule import KazumiRule

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass(slots=True)
class SearchItem:
    name: str
    source: str
    page_url: str = ""


@dataclass(slots=True)
class Episode:
    name: str
    page_url: str = ""
    url: str = ""


@dataclass(slots=True)
class Road:
    name: str
    episodes: list[Episode] = field(default_factory=list)


def _relative(path: str) -> str:
    path = (path or "").strip()
    if not path:
        return ""
    if path.startswith("//") or path.startswith("/"):
        return "." + path
    return path


def _select(node, path: str) -> list:
    xpath = _relative(path)
    if not xpath:
        return []
    try:
        return node.xpath(xpath)
    except XPathError as exc:
        raise AmineError(f"XPath 语法错误: {path!r} ({exc})") from exc


def _text(node, path: str) -> str:
    results = _select(node, path)
    for result in results:
        if isinstance(result, str):
            value = result.strip()
        elif hasattr(result, "text_content"):
            value = result.text_content().strip()
        else:
            value = str(result).strip()
        if value:
            return value
    return ""


def _attr(node, path: str, attribute: str = "href") -> str:
    for result in _select(node, path):
        if not hasattr(result, "get"):
            continue
        value = result.get(attribute)
        if value:
            return value.strip()
        # Fall back to a nested anchor when the selector points at a container.
        anchors = result.xpath(".//a[@href]")
        if anchors:
            return anchors[0].get("href", "").strip()
    return ""


def _subst(value: Any, mapping: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return render(value, mapping)
    if isinstance(value, dict):
        return {key: _subst(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [_subst(item, mapping) for item in value]
    return value


class RuleClient:
    """Runs a single :class:`KazumiRule` against a website."""

    def __init__(
        self,
        rule: KazumiRule,
        *,
        timeout: float = 20.0,
        user_agent: str | None = None,
        referer: str | None = None,
    ) -> None:
        self.rule = rule
        self.base_url = rule.base_url or ""
        headers = {"User-Agent": user_agent or rule.user_agent or DEFAULT_USER_AGENT}
        effective_referer = referer or rule.referer or rule.base_url
        if effective_referer:
            headers["Referer"] = effective_referer
        self._http = httpx.Client(timeout=timeout, follow_redirects=True, headers=headers)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> RuleClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- helpers ----------------------------------------------------------
    def _resolve(self, url: str) -> str:
        return urljoin(self.base_url or url, url)

    def _request(self, config, mapping: dict[str, Any]) -> httpx.Response:
        method = config.method.upper()
        url = _subst(config.url, mapping)
        params = _subst(config.query or None, mapping)
        headers = _subst(config.headers or None, mapping)
        kwargs: dict[str, Any] = {"headers": headers} if headers else {}
        if method == "POST":
            body = _subst(config.body, mapping) if config.body is not None else params
            if config.body_type.lower() == "form":
                kwargs["data"] = body
            else:
                kwargs["json"] = body if body is not None else {}
        elif params:
            kwargs["params"] = params
        try:
            response = self._http.request(method, url, **kwargs)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AmineError(f"请求失败 ({url}): {exc}") from exc
        return response

    # -- search -----------------------------------------------------------
    def search(self, keyword: str) -> list[SearchItem]:
        if self.rule.search_mode == "api" and self.rule.search_api:
            return self._search_api(keyword)
        return self._search_xpath(keyword)

    def _search_xpath(self, keyword: str) -> list[SearchItem]:
        rule = self.rule
        if not rule.search_url:
            raise AmineError(f"规则 {rule.name} 未配置 searchURL")
        encoded = quote(keyword, safe="")
        if rule.use_post:
            parts = urlsplit(rule.search_url)
            form = dict(parse_qsl(parts.query, keep_blank_values=True))
            form = {key: value.replace("@keyword", keyword) for key, value in form.items()}
            url = f"{parts.scheme}://{parts.netloc}{parts.path}"
            response = self._request(
                _SimpleConfig(method="POST", url=url, body=form, body_type="form"), {}
            )
        else:
            response = self._request(
                _SimpleConfig(method="GET", url=rule.search_url.replace("@keyword", encoded)), {}
            )

        document = lxml_html.fromstring(response.text)
        items: list[SearchItem] = []
        for node in _select(document, rule.search_list):
            name = _text(node, rule.search_name)
            href = _attr(node, rule.search_result)
            page_url = self._resolve(href) if href else ""
            if not name and not page_url:
                continue
            items.append(SearchItem(name=name or page_url, source=page_url, page_url=page_url))
        return items

    def _search_api(self, keyword: str) -> list[SearchItem]:
        config = self.rule.search_api
        assert config is not None
        response = self._request(config.request, {"keyword": keyword})
        data = _json(response)
        items: list[SearchItem] = []
        try:
            entries = query(data, config.list_path)
        except JsonPathError as exc:
            raise AmineError(f"规则 {self.rule.name} 的 listPath 无效: {exc}") from exc
        for entry in entries:
            name = first(entry, config.name_path, "")
            source = first(entry, config.source_path, "")
            if name == "" and source == "":
                continue
            items.append(SearchItem(name=str(name), source=str(source)))
        return items

    # -- chapters ---------------------------------------------------------
    def chapters(self, source: str) -> list[Road]:
        if self.rule.chapter_mode == "api" and self.rule.chapter_api:
            return self._chapters_api(source)
        return self._chapters_xpath(source)

    def _chapters_xpath(self, source: str) -> list[Road]:
        rule = self.rule
        url = source if source.startswith(("http://", "https://")) else self._resolve(source)
        try:
            response = self._http.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AmineError(f"请求失败 ({url}): {exc}") from exc
        document = lxml_html.fromstring(response.text)

        roads: list[Road] = []
        for index, road_node in enumerate(_select(document, rule.chapter_roads), start=1):
            episodes: list[Episode] = []
            for episode_node in _select(road_node, rule.chapter_result):
                name = (
                    episode_node.text_content().strip()
                    if hasattr(episode_node, "text_content")
                    else str(episode_node).strip()
                )
                href = ""
                if hasattr(episode_node, "get"):
                    href = episode_node.get("href") or ""
                    if not href:
                        anchors = episode_node.xpath(".//a[@href]")
                        if anchors:
                            href = anchors[0].get("href", "")
                if not href:
                    continue
                episodes.append(Episode(name=name or f"第{len(episodes) + 1}集", page_url=self._resolve(href)))
            roads.append(Road(name=f"线路{index}", episodes=episodes))
        return roads

    def _chapters_api(self, source: str) -> list[Road]:
        config = self.rule.chapter_api
        assert config is not None
        response = self._request(config.request, {"source": source})
        data = _json(response)

        variables: dict[str, Any] = {}
        for key, value in (config.variables or {}).items():
            if isinstance(value, str) and value.startswith("$"):
                variables[key] = first(data, value, "")
            else:
                variables[key] = value

        roads: list[Road] = []
        try:
            if config.format == "flat":
                road_entries = [data]
                episodes_path = config.episodes_path
            else:
                road_entries = query(data, config.roads_path)
                episodes_path = config.episodes_path
        except JsonPathError as exc:
            raise AmineError(f"规则 {self.rule.name} 的 roadsPath 无效: {exc}") from exc

        for road_index, road in enumerate(road_entries):
            road_name = first(road, config.road_name_path, "") if config.road_name_path else ""
            episodes: list[Episode] = []
            for episode_index, entry in enumerate(query(road, episodes_path)):
                episode_name = first(entry, config.episode_name_path, "")
                episode_url = first(entry, config.episode_url_path, "") if config.episode_url_path else ""
                page_url = self._episode_page_url(config, variables, road_index, episode_index, episode_url)
                episodes.append(
                    Episode(
                        name=str(episode_name) or f"第{episode_index + 1}集",
                        page_url=page_url,
                        url=str(episode_url) if episode_url else "",
                    )
                )
            roads.append(Road(name=str(road_name) or f"线路{road_index + 1}", episodes=episodes))
        return roads

    def _episode_page_url(
        self,
        config,
        variables: dict[str, Any],
        road_index: int,
        episode_index: int,
        fallback: str,
    ) -> str:
        page = config.episode_page
        if not page or not page.url:
            return self._resolve(str(fallback)) if fallback else ""
        mapping = {
            **variables,
            "roadIndex": road_index,
            "episodeIndex": episode_index,
            "roadNumber": road_index + 1,
            "episodeNumber": episode_index + 1,
        }
        url = render(page.url, mapping)
        if page.query:
            query_params = {key: render(str(value), mapping) for key, value in page.query.items()}
            url = f"{url}?{urlencode(query_params)}"
        return self._resolve(url)


@dataclass(slots=True)
class _SimpleConfig:
    """Adapter so xpath requests can reuse :meth:`RuleClient._request`."""

    method: str = "GET"
    url: str = ""
    headers: dict = field(default_factory=dict)
    query: dict = field(default_factory=dict)
    body: Any = None
    body_type: str = ""


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise AmineError("接口未返回合法 JSON") from exc
