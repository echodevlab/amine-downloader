"""Download client abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path

from ..errors import AmineError
from ..models import TorrentInfo

#: Called with the full file list ``[(index, path, size), ...]``; returns a
#: mapping of file index to the desired relative path (using ``/``).
RenamePlan = Callable[[list[tuple[int, str, int]]], dict[int, str]]


class DownloadError(AmineError):
    """Raised when a download client operation fails."""


def source_kind(source: str) -> str:
    """Classify *source* as ``magnet``, ``url``, ``torrent`` or ``path``."""

    lowered = source.strip().lower()
    if lowered.startswith("magnet:"):
        return "magnet"
    if lowered.startswith(("http://", "https://")):
        return "url"
    path = Path(source)
    if path.suffix.lower() == ".torrent" and path.exists():
        return "path"
    return "path"


class BaseDownloader(ABC):
    """Common interface implemented by qBittorrent and aria2."""

    name = "base"
    #: aria2 can only rename files on disk after the download finished.
    rename_requires_complete = False
    #: "posix" -> relative paths with "/" (qBittorrent), "local" -> OS paths (aria2).
    path_style = "posix"

    @abstractmethod
    def test_connection(self) -> str:
        """Return a human readable description of the server, or raise."""

    @abstractmethod
    def add(
        self,
        source: str,
        *,
        save_path: str | None = None,
        name: str | None = None,
        category: str | None = None,
        paused: bool = False,
        rename_plan: RenamePlan | None = None,
    ) -> str:
        """Add a torrent/url and return its client id (hash or gid).

        ``rename_plan`` lets a client that supports pre-naming (aria2's
        ``index-out``) rename files at add time.
        """

    @abstractmethod
    def get(self, torrent_id: str) -> TorrentInfo | None:
        """Return information about one torrent, or ``None`` if unknown."""

    @abstractmethod
    def list(self) -> list[TorrentInfo]:
        """Return all torrents known to the client."""

    @abstractmethod
    def remove(self, torrent_id: str, *, delete_files: bool = False) -> None:
        """Remove a torrent from the client."""

    def pause(self, torrent_id: str) -> None:
        raise NotImplementedError

    def resume(self, torrent_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def rename_file(self, torrent_id: str, old_path: str, new_path: str) -> bool:
        """Rename one file inside a torrent. Paths use the client's own format."""

    def rename_folder(self, torrent_id: str, old_path: str, new_path: str) -> bool:
        return False

    def close(self) -> None:
        """Release any held resources."""
