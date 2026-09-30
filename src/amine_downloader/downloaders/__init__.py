"""Download client factory."""

from __future__ import annotations

from .aria2 import Aria2Downloader
from .base import BaseDownloader, DownloadError, source_kind
from .qbittorrent import QBittorrentDownloader

__all__ = [
    "Aria2Downloader",
    "BaseDownloader",
    "DownloadError",
    "QBittorrentDownloader",
    "create_downloader",
    "source_kind",
]


def create_downloader(config) -> BaseDownloader:
    """Build the configured download client from an :class:`AppConfig`."""

    name = (config.downloader or "").lower()
    if name in ("qbittorrent", "qb"):
        settings = config.qbittorrent or {}
        url = settings.get("url")
        if not url:
            raise DownloadError("未配置 qbittorrent.url")
        return QBittorrentDownloader(
            url=str(url),
            username=str(settings.get("username", "")),
            password=str(settings.get("password", "")),
            api_key=str(settings.get("api_key", "")),
            verify=bool(settings.get("verify", True)),
        )
    if name == "aria2":
        settings = config.aria2 or {}
        rpc_url = settings.get("rpc_url")
        if not rpc_url:
            raise DownloadError("未配置 aria2.rpc_url")
        path_map: list[tuple[str, str]] = []
        library = config.library or {}
        if library.get("enabled") and library.get("root") and library.get("local_root"):
            path_map.append((str(library["root"]), str(library["local_root"])))
        # 显式 path_map 优先级最高，其次是上面的 library 映射。
        path_map.extend((str(src), str(dst)) for src, dst in (config.path_map or []))
        return Aria2Downloader(
            rpc_url=str(rpc_url),
            secret=str(settings.get("secret", "")),
            download_dir=str(settings.get("download_dir", "")),
            local_dir=str(settings.get("local_dir", "")),
            path_map=path_map,
        )
    raise DownloadError(f"未知的下载器: {config.downloader!r}（可选 qbittorrent / aria2）")
