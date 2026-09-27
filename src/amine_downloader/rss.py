"""Fetch and parse BT / Kazumi style RSS feeds (Mikan, DMHY, ...)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import httpx

from .errors import AmineError
from .models import RssEpisode
from .parser import parse_title

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 amine-downloader"
)

_MAGNET_RE = re.compile(r"magnet:\?[^\s\"'<>]+")
_TORRENT_RE = re.compile(r"https?://[^\s\"'<>]+\.torrent", re.IGNORECASE)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _first_text(element: ET.Element, *names: str) -> str:
    for child in element:
        if _local(child.tag) in names:
            if child.text and child.text.strip():
                return child.text.strip()
            for attr in ("href", "url", "src"):
                if child.get(attr):
                    return child.get(attr, "").strip()
    return ""


def _is_torrent_url(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered.endswith(".torrent") or lowered.startswith("magnet:")


def _find_torrent_url(element: ET.Element, description: str) -> str:
    for child in element.iter():
        for attr in ("url", "href", "src"):
            value = child.get(attr)
            if value and _is_torrent_url(value):
                return value.strip()
    magnet = _MAGNET_RE.search(description or "")
    if magnet:
        return magnet.group(0)
    torrent = _TORRENT_RE.search(description or "")
    if torrent:
        return torrent.group(0)
    return ""


def parse_feed(xml_text: str | bytes) -> list[RssEpisode]:
    """Parse an RSS 2.0 or Atom document into a list of episodes."""

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise AmineError(f"RSS 解析失败: {exc}") from exc
    episodes: list[RssEpisode] = []
    for element in root.iter():
        if _local(element.tag) not in ("item", "entry"):
            continue
        title = _first_text(element, "title")
        if not title:
            continue
        description = _first_text(element, "description", "summary", "content")
        page_url = _first_text(element, "link")
        enclosure = ""
        for child in element.iter():
            if _local(child.tag) in ("enclosure", "link") and child.get("url", "").lower().endswith(
                ".torrent"
            ):
                enclosure = child.get("url", "").strip()
                break
        torrent_url = enclosure or _find_torrent_url(element, description)
        guid = _first_text(element, "guid", "id")
        published = _first_text(element, "pubdate", "published", "updated")
        episodes.append(
            RssEpisode(
                title=title,
                torrent_url=torrent_url,
                page_url=page_url,
                guid=guid,
                published=published,
                parsed=parse_title(title),
            )
        )
    return episodes


def fetch_feed(url: str, *, timeout: float = 20.0) -> list[RssEpisode]:
    """Download and parse a feed at *url*."""

    try:
        response = httpx.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            },
            follow_redirects=True,
            timeout=timeout,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise AmineError(f"获取 RSS 失败 ({url}): {exc}") from exc
    return parse_feed(response.content)
