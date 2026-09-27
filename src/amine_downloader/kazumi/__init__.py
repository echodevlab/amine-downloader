"""Kazumi rule-based parsing and download."""

from __future__ import annotations

from .client import Episode, Road, RuleClient, SearchItem
from .download import KazumiResult, KazumiSearchHit, KazumiService
from .hls import HlsPlaylist, HlsVariant, fetch_playlist, parse_playlist, pick_variant
from .rule import KazumiRule, RuleStore
from .sniffer import BrowserSniffer, SniffResult, classify, pick_best, sniff

__all__ = [
    "BrowserSniffer",
    "Episode",
    "HlsPlaylist",
    "HlsVariant",
    "KazumiResult",
    "KazumiRule",
    "KazumiSearchHit",
    "KazumiService",
    "Road",
    "RuleClient",
    "RuleStore",
    "SearchItem",
    "SniffResult",
    "classify",
    "fetch_playlist",
    "parse_playlist",
    "pick_best",
    "pick_variant",
    "sniff",
]
