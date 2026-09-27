"""Kazumi rule model: load, store and inspect rule JSON files."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..errors import AmineError


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


@dataclass(slots=True)
class ApiRequest:
    method: str = "GET"
    url: str = ""
    headers: dict = field(default_factory=dict)
    query: dict = field(default_factory=dict)
    body: Any = None
    body_type: str = ""

    @classmethod
    def from_dict(cls, data: dict | None) -> ApiRequest:
        data = _as_dict(data)
        return cls(
            method=str(data.get("method", "GET")).upper(),
            url=str(data.get("url", "")),
            headers=dict(_as_dict(data.get("headers"))),
            query=dict(_as_dict(data.get("query"))),
            body=data.get("body"),
            body_type=str(data.get("bodyType") or data.get("body_type") or ""),
        )


@dataclass(slots=True)
class SearchApiConfig:
    request: ApiRequest = field(default_factory=ApiRequest)
    list_path: str = ""
    name_path: str = ""
    source_path: str = ""

    @classmethod
    def from_dict(cls, data: dict | None) -> SearchApiConfig:
        data = _as_dict(data)
        return cls(
            request=ApiRequest.from_dict(data.get("request")),
            list_path=str(data.get("listPath", "")),
            name_path=str(data.get("namePath", "")),
            source_path=str(data.get("sourcePath", "")),
        )


@dataclass(slots=True)
class EpisodePage:
    url: str = ""
    query: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict | None) -> EpisodePage:
        data = _as_dict(data)
        return cls(url=str(data.get("url", "")), query=dict(_as_dict(data.get("query"))))


@dataclass(slots=True)
class ChapterApiConfig:
    request: ApiRequest = field(default_factory=ApiRequest)
    format: str = "nested"
    roads_path: str = ""
    road_name_path: str = ""
    episodes_path: str = ""
    episode_name_path: str = ""
    episode_url_path: str = ""
    variables: dict = field(default_factory=dict)
    episode_page: EpisodePage = field(default_factory=EpisodePage)

    @classmethod
    def from_dict(cls, data: dict | None) -> ChapterApiConfig:
        data = _as_dict(data)
        return cls(
            request=ApiRequest.from_dict(data.get("request")),
            format=str(data.get("format", "nested")),
            roads_path=str(data.get("roadsPath", "")),
            road_name_path=str(data.get("roadNamePath", "")),
            episodes_path=str(data.get("episodesPath", "")),
            episode_name_path=str(data.get("episodeNamePath", "")),
            episode_url_path=str(data.get("episodeUrlPath", "")),
            variables=dict(_as_dict(data.get("variables"))),
            episode_page=EpisodePage.from_dict(data.get("episodePage")),
        )


@dataclass(slots=True)
class KazumiRule:
    api: str = "1"
    type: str = "anime"
    name: str = ""
    version: str = "1.0"
    base_url: str = ""
    search_url: str = ""
    search_list: str = ""
    search_name: str = ""
    search_result: str = ""
    chapter_roads: str = ""
    chapter_result: str = ""
    use_post: bool = False
    referer: str = ""
    user_agent: str = ""
    use_webview: bool = True
    search_mode: str = "xpath"
    chapter_mode: str = "xpath"
    search_api: SearchApiConfig | None = None
    chapter_api: ChapterApiConfig | None = None
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> KazumiRule:
        search_api = data.get("searchApiConfig")
        chapter_api = data.get("chapterApiConfig")
        search_mode = str(data.get("searchMode") or ("api" if search_api else "xpath")).lower()
        chapter_mode = str(data.get("chapterMode") or ("api" if chapter_api else "xpath")).lower()
        return cls(
            api=str(data.get("api", "1")),
            type=str(data.get("type", "anime")),
            name=str(data.get("name", "")),
            version=str(data.get("version", "1.0")),
            base_url=str(data.get("baseURL", "")),
            search_url=str(data.get("searchURL", "")),
            search_list=str(data.get("searchList", "")),
            search_name=str(data.get("searchName", "")),
            search_result=str(data.get("searchResult", "")),
            chapter_roads=str(data.get("chapterRoads", "")),
            chapter_result=str(data.get("chapterResult", "")),
            use_post=bool(data.get("usePost", False)),
            referer=str(data.get("referer", "")),
            user_agent=str(data.get("userAgent", "")),
            use_webview=bool(data.get("useWebview", True)),
            search_mode=search_mode,
            chapter_mode=chapter_mode,
            search_api=SearchApiConfig.from_dict(search_api) if search_api else None,
            chapter_api=ChapterApiConfig.from_dict(chapter_api) if chapter_api else None,
            raw=dict(data),
        )

    @classmethod
    def from_json(cls, text: str | bytes) -> KazumiRule:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AmineError(f"规则 JSON 解析失败: {exc}") from exc
        if not isinstance(data, dict):
            raise AmineError("规则文件必须是一个 JSON 对象")
        return cls.from_dict(data)

    def to_dict(self) -> dict:
        return dict(self.raw) if self.raw else {
            "api": self.api,
            "type": self.type,
            "name": self.name,
            "version": self.version,
            "baseURL": self.base_url,
            "searchURL": self.search_url,
            "searchList": self.search_list,
            "searchName": self.search_name,
            "searchResult": self.search_result,
            "chapterRoads": self.chapter_roads,
            "chapterResult": self.chapter_result,
        }


class RuleStore:
    """Load and store Kazumi rule files in a directory."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)

    def _files(self) -> list[Path]:
        if not self.directory.exists():
            return []
        return sorted(self.directory.glob("*.json"))

    def list(self) -> list[KazumiRule]:
        rules: list[KazumiRule] = []
        for path in self._files():
            try:
                rules.append(KazumiRule.from_json(path.read_text(encoding="utf-8")))
            except AmineError:
                continue
        return rules

    def get(self, name: str) -> KazumiRule | None:
        wanted = name.lower()
        for rule in self.list():
            if rule.name.lower() == wanted or Path(rule.name).stem.lower() == wanted:
                return rule
        # Also allow selecting by file name.
        for path in self._files():
            if path.stem.lower() == wanted:
                try:
                    return KazumiRule.from_json(path.read_text(encoding="utf-8"))
                except AmineError:
                    continue
        return None

    def save(self, rule: KazumiRule, filename: str | None = None) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch for ch in (filename or rule.name or "rule") if ch not in '<>:"/\\|?*')
        target = self.directory / f"{safe or 'rule'}.json"
        target.write_text(
            json.dumps(rule.to_dict(), ensure_ascii=False, indent=4), encoding="utf-8"
        )
        return target

    def import_from(self, source: str) -> list[Path]:
        """Import rules from a local path or URL.

        Supports: a single rule object, an array of rule objects, or the
        KazumiRules ``index.json`` (an array of metadata), in which case every
        ``<name>.json`` next to it is fetched.
        """

        is_url = source.lower().startswith(("http://", "https://"))
        if is_url:
            payload, fallback_name = self._download(source)
        else:
            path = Path(source)
            if not path.is_file():
                raise AmineError(f"找不到规则文件: {source}")
            payload = path.read_text(encoding="utf-8")
            fallback_name = path.stem

        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise AmineError(f"规则 JSON 解析失败: {exc}") from exc

        if isinstance(data, list) and data and self._looks_like_index(data):
            if not is_url:
                raise AmineError("规则索引需要从 URL 导入，才能下载其中的规则文件")
            return self._import_index(source, data)

        entries: list[dict]
        if isinstance(data, list):
            entries = [item for item in data if isinstance(item, dict)]
        elif isinstance(data, dict) and isinstance(data.get("rules"), list):
            entries = [item for item in data["rules"] if isinstance(item, dict)]
        elif isinstance(data, dict):
            entries = [data]
        else:
            raise AmineError("无法识别的规则格式")

        written: list[Path] = []
        for entry in entries:
            rule = KazumiRule.from_dict(entry)
            written.append(self.save(rule, filename=rule.name or fallback_name))
        return written

    @staticmethod
    def _looks_like_index(entries: list) -> bool:
        rule_keys = {
            "baseURL",
            "searchURL",
            "searchList",
            "searchMode",
            "searchApiConfig",
            "chapterRoads",
            "chapterApiConfig",
        }
        for entry in entries:
            if not isinstance(entry, dict) or "name" not in entry:
                return False
            if any(key in entry for key in rule_keys):
                return False
        return True

    def _import_index(self, source: str, entries: list) -> list[Path]:
        base = source.rsplit("/", 1)[0]
        written: list[Path] = []
        errors: list[str] = []
        for entry in entries:
            name = entry.get("name")
            if not name:
                continue
            url = f"{base}/{name}.json"
            try:
                payload, _ = self._download(url)
                rule = KazumiRule.from_json(payload)
            except AmineError as exc:
                errors.append(f"{name}: {exc}")
                continue  # 单条失败不影响其余
            written.append(self.save(rule, filename=rule.name or str(name)))
        if not written and errors:
            raise AmineError("全部规则导入失败: " + "; ".join(errors[:3]))
        return written

    @staticmethod
    def _download(url: str) -> tuple[str, str]:
        import time

        last: Exception | None = None
        for attempt in range(3):
            try:
                response = httpx.get(url, timeout=30.0, follow_redirects=True)
                response.raise_for_status()
                return response.text, Path(url.split("?")[0]).stem
            except httpx.HTTPError as exc:
                last = exc
                time.sleep(0.5 * (attempt + 1))
        raise AmineError(f"下载规则失败 ({url}): {last}")
