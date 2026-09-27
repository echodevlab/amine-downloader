"""Minimal HLS (m3u8) playlist parsing for the Kazumi download path."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx

from ..errors import AmineError


@dataclass(slots=True)
class HlsVariant:
    url: str
    bandwidth: int = 0
    resolution: str = ""


@dataclass(slots=True)
class HlsPlaylist:
    url: str
    kind: str = "media"  # "master" | "media"
    variants: list[HlsVariant] = field(default_factory=list)
    segments: list[str] = field(default_factory=list)
    init_segment: str = ""
    encrypted: bool = False
    extension: str = ".ts"


def _attributes(line: str) -> dict[str, str]:
    _, _, rest = line.partition(":")
    result: dict[str, str] = {}
    for part in rest.split(","):
        key, _, value = part.partition("=")
        result[key.strip().upper()] = value.strip().strip('"')
    return result


def parse_master(text: str, url: str) -> list[HlsVariant]:
    """Parse ``#EXT-X-STREAM-INF`` variants from a master playlist."""

    variants: list[HlsVariant] = []
    lines = [line.strip() for line in text.splitlines()]
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        attributes = _attributes(line)
        uri = next(
            (follow for follow in lines[index + 1 :] if follow and not follow.startswith("#")),
            "",
        )
        if not uri:
            continue
        variants.append(
            HlsVariant(
                url=urljoin(url, uri),
                bandwidth=int(attributes.get("BANDWIDTH", "0") or 0),
                resolution=attributes.get("RESOLUTION", ""),
            )
        )
    return variants


def parse_media(text: str, url: str) -> HlsPlaylist:
    """Parse a media playlist (segments, init segment, encryption flag)."""

    playlist = HlsPlaylist(url=url, kind="media")
    pending = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MAP"):
            attributes = _attributes(line)
            if attributes.get("URI"):
                playlist.init_segment = urljoin(url, attributes["URI"])
            continue
        if line.startswith("#EXT-X-KEY"):
            attributes = _attributes(line)
            if attributes.get("METHOD", "NONE").upper() != "NONE":
                playlist.encrypted = True
            continue
        if line.startswith("#EXTINF"):
            pending = True
            continue
        if line.startswith("#"):
            continue
        if pending:
            playlist.segments.append(urljoin(url, line))
            pending = False
    if playlist.init_segment or any(
        segment.endswith((".m4s", ".mp4")) for segment in playlist.segments
    ):
        playlist.extension = ".mp4"
    return playlist


def parse_playlist(text: str, url: str) -> HlsPlaylist:
    """Parse an m3u8 document (master or media)."""

    if "#EXT-X-STREAM-INF" in text:
        playlist = HlsPlaylist(url=url, kind="master")
        playlist.variants = parse_master(text, url)
        return playlist
    return parse_media(text, url)


def pick_variant(variants: list[HlsVariant], preferred: str = "") -> HlsVariant:
    """Choose the best variant, preferring *preferred* (e.g. ``1080p``)."""

    if not variants:
        raise AmineError("播放列表中没有可用的清晰度")
    if preferred:
        wanted = preferred.lower().replace("p", "")
        if wanted:
            for variant in variants:
                if wanted in variant.resolution:
                    return variant
    return max(variants, key=lambda item: item.bandwidth)


def fetch_playlist(
    url: str, *, client: httpx.Client | None = None, timeout: float = 20.0, preferred: str = ""
) -> HlsPlaylist:
    """Fetch and parse an m3u8 URL, resolving a master playlist to a media one."""

    owns_client = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        response = client.get(url)
        response.raise_for_status()
        playlist = parse_playlist(response.text, url)
        if playlist.kind == "master":
            variant = pick_variant(playlist.variants, preferred)
            response = client.get(variant.url)
            response.raise_for_status()
            return parse_media(response.text, variant.url)
        return playlist
    except httpx.HTTPError as exc:
        raise AmineError(f"获取 m3u8 失败 ({url}): {exc}") from exc
    finally:
        if owns_client:
            client.close()
