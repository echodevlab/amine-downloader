"""Parse raw BT / Kazumi style titles into structured information.

The heuristics here are inspired by Auto_Bangumi's ``raw_parser`` and by the
naming rules used by Kazumi.  A torrent title such as::

    [Lilith-Raws] 葬送的芙莉莲 - 05 [1080p][Baha][WEB-DL][AAC AVC][CHT]

is turned into a :class:`~amine_downloader.models.ParsedTitle` with group,
title, season, episode, resolution, language and source filled in.
"""

from __future__ import annotations

import re

from .models import ParsedTitle

_BRACKET_RE = re.compile(r"[\[\(【（]([^\[\]\(\)【】（）]*)[\]\)】）]")

_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

_RESOLUTION_RE = re.compile(
    r"(?i)\b(4320p|8k|2160p|4k|1440p|1080p|1080i|720p|576p|480p|360p)\b"
)
_RESOLUTION_MAP = {
    "8k": "2160p",
    "4k": "2160p",
}

_LANGUAGE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)简繁|繁简|chs?&cht|cht&chs|简繁日|繁简日|简日繁"), "CHS&CHT"),
    (re.compile(r"(?i)简日|简中|简体|chs(?![a-z])|gb(?![a-z])"), "CHS"),
    (re.compile(r"(?i)繁日|繁中|繁体|cht(?![a-z])|big5"), "CHT"),
    (re.compile(r"(?i)日文|日语|\bjpn?\b|jpsc|jptc"), "JP"),
    (re.compile(r"(?i)双语|multi[- ]?sub|多语|简繁英"), "Multi"),
    (re.compile(r"(?i)\beng\b|\benglish\b"), "EN"),
]

_SOURCE_KEYWORDS = (
    "BDRemux",
    "BDRip",
    "BluRay",
    "Blu-ray",
    "WEB-DL",
    "WEBRip",
    "WebRip",
    "WEB",
    "TVRip",
    "HDTV",
    "DVDRip",
    "DVD",
    "Baha",
    "B-Global",
    "Bilibili",
    "Netflix",
    "Amazon",
    "Abema",
    "Hulu",
)

_CONTAINER_RE = re.compile(
    r"(?i)^(mkv|mp4|avi|webm|flv|ts|m2ts|mov|wmv|rmvb|rm|mpg|mpeg)$"
)
_TAG_RE = re.compile(
    r"(?i)(?:x26[45]|h\.?26[45]|hevc|avc|aac|flac|ac3|eac3|opus|dts|"
    r"ma10p|hi10p|10bit|8bit|yuv420p8|yuv420p10|hdr10\+?|hdr|dovi|\bdv\b|sdr|"
    r"web-?dl|web-?rip|bdrip|bdremux|bluray|blu-ray|hdtv|dvdrip|"
    r"ass|srt|sub|sup|chapter|fonts?|cd\d|sp\d|ncop|nced|op\d?|ed\d?)"
)
_GROUP_HINT_RE = re.compile(
    r"(?i)(字幕组|字幕社|汉化组|压制组|fansub|subs?\b|raws?\b|lolihouse|"
    r"sweetsub|sakurato|桜都|喵萌|悠哈|幻樱|极影|动漫国|豌豆|银光|星空|"
    r"爱恋|轻之国度|北宇治|ani\b|lilith|nekomoe|skymoon|airota|\bvcb\b|\buha\b|"
    r"jysub|\bdmg\b|kamigami|kisssub|ohys|judas|nc-raws|magicstar|"
    r"prejudice|snow-raws|mawen1250|littlebakas)"
)

# Episode patterns searched in the "main" text (brackets removed).
_EP_MAIN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\bS\d{1,2}E(\d{1,4})(?:v\d+)?\b"),
    re.compile(r"第\s*([0-9]{1,4}(?:\.\d+)?)\s*[集话話回]"),
    re.compile(r"(?i)\b(?:EP?|Episode)\s*[-_.]?\s*(\d{1,4})(?:v\d+)?\b"),
    re.compile(r"[-–—_]\s*(\d{1,4}(?:\.\d+)?)(?:v\d+)?\s*$"),
    re.compile(r"(?:^|\s)(\d{1,4}(?:\.\d+)?)(?:v\d+)?\s*$"),
    re.compile(r"\b(\d{1,4}(?:\.\d+)?)(?:v\d+)?\s*[集话話回]"),
]

# Season patterns searched in the "main" text.
_SEASON_MAIN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\bS(\d{1,2})E\d{1,4}"),
    re.compile(r"第\s*([0-9一二三四五六七八九十]{1,3})\s*[季期部]"),
    re.compile(r"(?i)\bSeason\s*(\d{1,2})\b"),
    re.compile(r"(?i)\b(\d{1,2})(?:st|nd|rd|th)\s+Season\b"),
    re.compile(r"(?i)\bS(\d{1,2})\b"),
]

_EP_TOKEN_RE = re.compile(r"^(?:第)?\s*(\d{1,4}(?:\.\d+)?)(?:v\d+)?\s*[集话話回]?$")
_SEASON_TOKEN_RE = re.compile(r"^(?:第\s*([0-9一二三四五六七八九十]{1,3})\s*[季期部])$")
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_JUNK_RE = re.compile(
    r"(招募|招新|新番|月新番|长期|字幕组|字幕社|汉化组|压制组|搬运|合集|"
    r"★|☆|※|◆|●|▲|■|□|♪|♬)"
)


def _cn_to_int(text: str) -> int | None:
    text = text.strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        if left and left not in _CN_DIGITS:
            return None
        if right and right not in _CN_DIGITS:
            return None
        return tens * 10 + ones
    value = 0
    for char in text:
        if char not in _CN_DIGITS:
            return None
        value = value * 10 + _CN_DIGITS[char]
    return value


def _normalise_episode(value: str) -> str:
    value = value.strip()
    try:
        number = float(value)
    except ValueError:
        return value
    if number.is_integer():
        return str(int(number))
    return value


def _looks_like_tag(token: str) -> bool:
    token = token.strip()
    if not token:
        return True
    if _JUNK_RE.search(token):
        return True
    if _CONTAINER_RE.match(token):
        return True
    if _RESOLUTION_RE.search(token):
        return True
    if _TAG_RE.search(token):
        return True
    for pattern, _ in _LANGUAGE_PATTERNS:
        if pattern.search(token):
            return True
    lowered = token.lower()
    return any(keyword.lower() in lowered for keyword in _SOURCE_KEYWORDS)


def _find_resolution(text: str) -> str:
    match = _RESOLUTION_RE.search(text)
    if not match:
        return ""
    value = match.group(1).lower()
    return _RESOLUTION_MAP.get(value, value)


def _find_language(text: str) -> str:
    for pattern, value in _LANGUAGE_PATTERNS:
        if pattern.search(text):
            return value
    return ""


def _find_source(text: str) -> str:
    lowered = text.lower()
    for keyword in _SOURCE_KEYWORDS:
        if keyword.lower() in lowered:
            return keyword
    return ""


def _clean_title(text: str) -> str:
    text = re.sub(r"[\[\]【】（）()]", " ", text)
    text = _RESOLUTION_RE.sub(" ", text)
    for keyword in _SOURCE_KEYWORDS:
        text = re.sub(re.escape(keyword), " ", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub(" ", text)
    text = re.sub(r"\d{1,2}\s*月新番", " ", text)
    text = re.sub(r"(招募翻译|招募字幕|长期招募|招募|招新|新番)", " ", text)
    text = re.sub(r"[★☆※◆●▲■□♪♬]", " ", text)
    text = re.sub(r"[「」『』]", " ", text)
    text = re.sub(r"[\s\-–—_~·|:：]+", " ", text)
    result = text.strip(" -–—_~·|:：,.")
    if not re.search(r"[A-Za-z\u4e00-\u9fff\u3040-\u30ff]", result):
        return ""
    return result


def _strip_span(text: str, start: int, end: int) -> str:
    return (text[:start] + " " + text[end:]).strip()


def parse_title(raw: str) -> ParsedTitle:
    """Parse *raw* into a :class:`ParsedTitle`."""

    result = ParsedTitle(raw=raw or "")
    if not raw or not raw.strip():
        return result

    text = raw.strip()
    tokens = [match.group(1).strip() for match in _BRACKET_RE.finditer(text)]
    main = _BRACKET_RE.sub(" ", text)
    main = re.sub(r"[「」『』]", " ", main)
    main = re.sub(r"\s+", " ", main).strip()

    # --- group -----------------------------------------------------------
    group = ""
    used_tokens: set[int] = set()
    if tokens:
        first = tokens[0]
        stripped = text.lstrip()
        starts_with_bracket = stripped[:1] in "[（(【"
        if starts_with_bracket and first and not _looks_like_tag(first):
            if not re.fullmatch(r"\d{1,4}(?:\.\d+)?(?:v\d+)?", first):
                group = first
                used_tokens.add(0)
    if not group:
        for index, token in enumerate(tokens[:3]):
            if _GROUP_HINT_RE.search(token):
                group = token
                used_tokens.add(index)
                break

    # --- season / episode ------------------------------------------------
    season = 1
    episode = ""

    combined = re.search(r"(?i)\bS(\d{1,2})E(\d{1,4})(?:v\d+)?\b", main)
    if combined:
        season = int(combined.group(1))
        episode = _normalise_episode(combined.group(2))
        main = _strip_span(main, combined.start(), combined.end())
    else:
        for pattern in _SEASON_MAIN_PATTERNS:
            match = pattern.search(main)
            if not match:
                continue
            value = _cn_to_int(match.group(1))
            if value is None:
                continue
            season = value
            main = _strip_span(main, match.start(), match.end())
            break

        for pattern in _EP_MAIN_PATTERNS:
            match = pattern.search(main)
            if not match:
                continue
            value = match.group(1)
            if _YEAR_RE.match(value) and not re.search(r"[集话話回]", match.group(0)):
                continue
            episode = _normalise_episode(value)
            main = _strip_span(main, match.start(), match.end())
            break

    # Fall back to bracketed tokens for episode / season.
    if not episode:
        best: tuple[int, int, str] | None = None
        for index, token in enumerate(tokens):
            if index in used_tokens:
                continue
            match = _EP_TOKEN_RE.match(token)
            if not match:
                continue
            value = match.group(1)
            has_suffix = bool(re.search(r"[集话話回]", token))
            if _YEAR_RE.match(value) and not has_suffix:
                continue
            score = 0
            if has_suffix:
                score += 10
            if len(value.split(".")[0]) <= 3:
                score += 5
            if value.startswith("0") and len(value) > 1:
                score += 2
            if best is None or score > best[0]:
                best = (score, index, _normalise_episode(value))
        if best is not None:
            episode = best[2]
            used_tokens.add(best[1])
    if season == 1:
        for index, token in enumerate(tokens):
            if index in used_tokens:
                continue
            match = _SEASON_TOKEN_RE.match(token)
            if not match:
                continue
            value = _cn_to_int(match.group(1))
            if value is not None:
                season = value
                used_tokens.add(index)
            break

    # --- title -----------------------------------------------------------
    title = _clean_title(main)
    if not title:
        candidates: list[str] = []
        for index, token in enumerate(tokens):
            if index in used_tokens or not token:
                continue
            if _looks_like_tag(token):
                continue
            if re.fullmatch(r"\d{1,4}(?:\.\d+)?(?:v\d+)?", token):
                continue
            if re.fullmatch(r"\d{1,4}[-~]\d{1,4}", token):
                continue
            candidates.append(token)
        title = _clean_title(" ".join(candidates))
    if not title:
        title = _clean_title(text) or text

    # --- resolution / language / source ---------------------------------
    haystack = " ".join([main, *tokens])
    resolution = _find_resolution(haystack)
    language = _find_language(haystack)
    source = _find_source(haystack)

    # --- leftover tags ---------------------------------------------------
    extra: list[str] = []
    for index, token in enumerate(tokens):
        if index in used_tokens or not token:
            continue
        if _looks_like_tag(token):
            extra.append(token)

    result.group = group
    result.title = title
    result.season = season
    result.episode = episode
    result.resolution = resolution
    result.language = language
    result.source = source
    result.extra = extra
    result.matched = bool(episode or (group and title and title != raw))
    return result
