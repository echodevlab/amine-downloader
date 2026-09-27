"""Turn a parsed title into a standard, human friendly file name."""

from __future__ import annotations

import re

from .models import ParsedTitle

DEFAULT_TEMPLATE = "[{group}] {title} S{season}E{episode} [{resolution}]"

_TOKEN_RE = re.compile(r"\{(\w+)(?::(\d+))?\}")
_INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _format_episode(value: str, width: int) -> str:
    if not value:
        return ""
    try:
        number = float(value)
    except ValueError:
        return value
    if number.is_integer():
        return f"{int(number):0{width}d}"
    integer, _, fraction = value.partition(".")
    return f"{int(integer):0{width}d}.{fraction}"


def _cleanup(name: str) -> str:
    name = re.sub(r"[\[\(【（]\s*[\]\)】）]", "", name)
    name = re.sub(r"([\[\(【（])\s+", r"\1", name)
    name = re.sub(r"\s+([\]\)】）])", r"\1", name)
    name = re.sub(r"\s{2,}", " ", name)
    name = _INVALID_CHARS_RE.sub(" ", name)
    name = re.sub(r"\s{2,}", " ", name)
    return name.strip(" -–—_~·|:.,")


def render(
    parsed: ParsedTitle,
    template: str | None = None,
    *,
    episode: str | None = None,
    ext: str = "",
) -> str:
    """Render a file/folder base name (without extension unless given in *ext*)."""

    template = template or DEFAULT_TEMPLATE
    episode_value = parsed.episode if episode is None else episode

    values = {
        "group": parsed.group,
        "title": parsed.title,
        "season": parsed.season,
        "episode": episode_value,
        "resolution": parsed.resolution,
        "language": parsed.language,
        "source": parsed.source,
        "extra": " ".join(parsed.extra),
        "raw": parsed.raw,
    }

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        width = int(match.group(2)) if match.group(2) else 2
        if key not in values:
            return match.group(0)
        if key == "season":
            try:
                return f"{int(values['season']):0{width}d}"
            except (TypeError, ValueError):
                return str(values["season"])
        if key == "episode":
            return _format_episode(str(values["episode"]), width)
        value = values[key]
        return str(value) if value is not None else ""

    name = _TOKEN_RE.sub(replace, template)
    name = _cleanup(name)
    if ext:
        ext = ext if ext.startswith(".") else f".{ext}"
        return f"{name}{ext}"
    return name


def render_folder(parsed: ParsedTitle) -> str:
    """Standard folder name for a multi-file (batch) torrent."""

    return _cleanup(f"[{parsed.group}] {parsed.title}".strip()) if (parsed.group or parsed.title) else ""
