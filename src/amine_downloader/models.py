"""Data models shared across amine-downloader."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Extensions that are considered playable video files when renaming.
VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".mkv",
        ".mp4",
        ".avi",
        ".webm",
        ".ts",
        ".m2ts",
        ".mov",
        ".flv",
        ".wmv",
        ".m4v",
        ".rmvb",
        ".rm",
        ".mpg",
        ".mpeg",
    }
)

SUBTITLE_EXTENSIONS: frozenset[str] = frozenset(
    {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup", ".smi"}
)


@dataclass(slots=True)
class ParsedTitle:
    """Structured information extracted from a raw torrent / RSS title."""

    raw: str
    group: str = ""
    title: str = ""
    season: int = 1
    episode: str = ""
    resolution: str = ""
    language: str = ""
    source: str = ""
    extra: list[str] = field(default_factory=list)
    matched: bool = False

    @property
    def episode_number(self) -> float | None:
        try:
            return float(self.episode)
        except (TypeError, ValueError):
            return None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class RssEpisode:
    """A single entry from a BT / Kazumi style RSS feed."""

    title: str
    torrent_url: str = ""
    page_url: str = ""
    guid: str = ""
    published: str = ""
    parsed: ParsedTitle | None = None

    @property
    def dedup_key(self) -> str:
        return self.guid or self.torrent_url or self.title


@dataclass(slots=True)
class TorrentFile:
    path: str
    size: int = 0
    progress: float = 0.0


@dataclass(slots=True)
class TorrentInfo:
    id: str
    name: str = ""
    save_path: str = ""
    progress: float = 0.0
    state: str = ""
    size: int = 0
    category: str = ""
    files: list[TorrentFile] = field(default_factory=list)


@dataclass(slots=True)
class DownloadTask:
    """A record of something we asked a download client to fetch."""

    key: str
    raw_title: str
    client: str = ""
    torrent_id: str = ""
    group: str = ""
    title: str = ""
    season: int = 1
    episode: str = ""
    resolution: str = ""
    new_name: str = ""
    source: str = ""
    save_path: str = ""
    category: str = ""
    status: str = "pending"
    created_at: str = ""
    updated_at: str = ""
