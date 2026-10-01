"""aria2 JSON-RPC client."""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path

import httpx

from ..bencode import BencodeError, torrent_files
from ..models import TorrentFile, TorrentInfo
from .base import BaseDownloader, DownloadError, source_kind


class Aria2Downloader(BaseDownloader):
    name = "aria2"
    # aria2 has no rename RPC, so files are renamed on disk after completion.
    rename_requires_complete = True
    path_style = "local"

    def __init__(
        self,
        rpc_url: str,
        secret: str = "",
        download_dir: str = "",
        *,
        local_dir: str = "",
        path_map: list[tuple[str, str]] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.rpc_url = rpc_url
        self.secret = secret
        self.download_dir = download_dir
        #: Same directory as seen by this machine (mapped drive / UNC path).
        self.local_dir = local_dir
        #: (server_prefix, local_prefix) pairs for translating paths.
        self.path_map: list[tuple[str, str]] = list(path_map or [])
        if download_dir and local_dir:
            self.path_map.append((download_dir, local_dir))
        self._client = httpx.Client(timeout=timeout)
        self._counter = 0
        self._media_supported: bool | None = None

    # -- low level --------------------------------------------------------
    def _call(self, method: str, params: list | None = None):
        self._counter += 1
        payload = {
            "jsonrpc": "2.0",
            "id": f"amine-{self._counter}",
            "method": method,
            "params": ([f"token:{self.secret}"] if self.secret else []) + (params or []),
        }
        try:
            response = self._client.post(self.rpc_url, json=payload)
        except httpx.HTTPError as exc:
            raise DownloadError(f"aria2 RPC 请求失败: {exc}") from exc
        data = None
        try:
            data = response.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("error"):
            message = data["error"].get("message") if isinstance(data["error"], dict) else data["error"]
            raise DownloadError(f"aria2 错误: {message}")
        if response.status_code >= 400:
            raise DownloadError(f"aria2 RPC 请求失败: HTTP {response.status_code} {response.text[:200]}")
        return data.get("result") if isinstance(data, dict) else None

    # -- interface --------------------------------------------------------
    def test_connection(self) -> str:
        result = self._call("aria2.getVersion")
        version = result.get("version", "?") if isinstance(result, dict) else "?"
        return f"aria2 {version}"

    def add(
        self,
        source: str,
        *,
        save_path: str | None = None,
        name: str | None = None,
        category: str | None = None,
        paused: bool = False,
        rename_plan=None,
    ) -> str:
        options: dict = {}
        directory = save_path or self.download_dir
        if directory:
            options["dir"] = directory
        if paused:
            options["pause"] = "true"

        kind = source_kind(source)
        torrent_bytes: bytes | None = None
        if kind == "path":
            torrent_path = Path(source)
            if not torrent_path.is_file():
                raise DownloadError(f"找不到种子文件: {source}")
            torrent_bytes = torrent_path.read_bytes()
        elif kind == "url":
            torrent_bytes = self._download_torrent(source)

        if torrent_bytes:
            index_out = self._index_out(torrent_bytes, rename_plan)
            if index_out:
                options["index-out"] = index_out
            result = self._call(
                "aria2.addTorrent",
                [base64.b64encode(torrent_bytes).decode(), [], options],
            )
        else:
            if kind != "magnet" and name:
                options["out"] = name
            result = self._call("aria2.addUri", [[source], options])
        if not result:
            raise DownloadError("aria2 未返回 GID")
        return str(result)

    @staticmethod
    def _index_out(torrent_bytes: bytes, rename_plan) -> list[str]:
        """Build aria2 ``index-out`` entries from a torrent's file list."""

        if rename_plan is None:
            return []
        try:
            files = torrent_files(torrent_bytes)
        except BencodeError:
            return []
        entries_input = [(index, path, size) for index, (path, size) in enumerate(files)]
        plan = rename_plan(entries_input)
        entries: list[str] = []
        for index, new_path in plan.items():
            if new_path and new_path != files[index][0]:
                # aria2's index-out uses 1-based indices (as shown by --show-files).
                entries.append(f"{index + 1}={new_path}")
        return entries

    def _download_torrent(self, url: str) -> bytes | None:
        # 只有 .torrent 才需要预取；视频/媒体直链直接交给 aria2，避免白白下载一遍
        if not url.split("?")[0].lower().endswith(".torrent"):
            return None
        try:
            response = httpx.get(url, timeout=30.0, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        content = response.content
        if not content or content[:1] != b"d" or len(content) > 32 * 1024 * 1024:
            return None
        return content

    @staticmethod
    def _to_info(raw: dict) -> TorrentInfo:
        files: list[TorrentFile] = []
        for entry in raw.get("files", []) or []:
            path = str(entry.get("path", ""))
            size = int(entry.get("length", 0) or 0)
            completed = int(entry.get("completedLength", 0) or 0)
            files.append(
                TorrentFile(
                    path=path,
                    size=size,
                    progress=(completed / size) if size else 0.0,
                )
            )
        total = int(raw.get("totalLength", 0) or 0)
        completed = int(raw.get("completedLength", 0) or 0)
        name = ""
        bittorrent = raw.get("bittorrent") or {}
        if isinstance(bittorrent, dict):
            name = str(bittorrent.get("name", ""))
        if not name and files:
            name = os.path.basename(files[0].path.rstrip("/\\"))
        return TorrentInfo(
            id=str(raw.get("gid", "")),
            name=name,
            save_path=str(raw.get("dir", "")),
            progress=(completed / total) if total else 0.0,
            state=str(raw.get("status", "")),
            size=total,
            files=files,
        )

    def get(self, torrent_id: str) -> TorrentInfo | None:
        try:
            raw = self._call("aria2.tellStatus", [torrent_id])
        except DownloadError:
            return None
        if not isinstance(raw, dict):
            return None
        return self._to_info(raw)

    def list(self) -> list[TorrentInfo]:
        infos = [self._to_info(raw) for raw in (self._call("aria2.tellActive") or [])]
        for method in ("aria2.tellWaiting", "aria2.tellStopped"):
            try:
                extra = self._call(method, [0, 1000])
            except DownloadError:
                extra = []
            infos.extend(self._to_info(raw) for raw in (extra or []))
        return infos

    def remove(self, torrent_id: str, *, delete_files: bool = False) -> None:
        info = self.get(torrent_id)
        try:
            self._call("aria2.forceRemove", [torrent_id])
        except DownloadError:
            pass
        try:
            self._call("aria2.removeDownloadResult", [torrent_id])
        except DownloadError:
            pass
        if delete_files and info:
            for entry in info.files:
                local = self._local_path(entry.path)
                if local and local.is_file():
                    try:
                        local.unlink()
                    except OSError:
                        pass

    def pause(self, torrent_id: str) -> None:
        self._call("aria2.forcePause", [torrent_id])

    def resume(self, torrent_id: str) -> None:
        self._call("aria2.unpause", [torrent_id])

    # -- direct URI helpers (used by the Kazumi download path) ------------
    def add_uri(
        self,
        uri: str,
        *,
        out: str | None = None,
        save_path: str | None = None,
        options: dict | None = None,
    ) -> str:
        """Add a single HTTP(S) URI as its own download."""

        opts: dict[str, str] = {}
        directory = save_path or self.download_dir
        if directory:
            opts["dir"] = directory
        if out:
            opts["out"] = out
        if options:
            opts.update({key: str(value) for key, value in options.items()})
        result = self._call("aria2.addUri", [[uri], opts])
        if not result:
            raise DownloadError("aria2 未返回 GID")
        return str(result)

    def tell(self, gid: str) -> dict | None:
        try:
            result = self._call("aria2.tellStatus", [gid])
        except DownloadError:
            return None
        return result if isinstance(result, dict) else None

    def wait_for(self, gids: list[str], *, timeout: float = 600.0, poll: float = 1.0) -> dict[str, bool]:
        """Wait until every gid is complete (or failed). Returns gid -> ok."""

        import time

        remaining = list(gids)
        results: dict[str, bool] = {}
        deadline = time.time() + timeout
        while remaining and time.time() < deadline:
            for gid in list(remaining):
                status = self.tell(gid)
                if status is None:
                    results[gid] = False
                    remaining.remove(gid)
                    continue
                state = status.get("status")
                if state == "complete":
                    results[gid] = True
                    remaining.remove(gid)
                elif state in ("error", "removed"):
                    results[gid] = False
                    remaining.remove(gid)
            if remaining:
                time.sleep(poll)
        for gid in remaining:
            results[gid] = False
        return results

    def remove_result(self, gid: str) -> None:
        try:
            self._call("aria2.removeDownloadResult", [gid])
        except DownloadError:
            pass

    def set_global_option(self, options: dict) -> None:
        try:
            self._call(
                "aria2.changeGlobalOption",
                [{key: str(value) for key, value in options.items()}],
            )
        except DownloadError:
            pass

    def get_global_option(self, name: str) -> str | None:
        try:
            result = self._call("aria2.getGlobalOption")
        except DownloadError:
            return None
        if isinstance(result, dict):
            value = result.get(name)
            return str(value) if value not in (None, "") else None
        return None

    def local_target(self, path: str) -> Path:
        """Public alias of :meth:`_local_target` for path mapping callers."""

        return self._local_target(path)

    def change_option(self, gid: str, options: dict) -> None:
        self._call(
            "aria2.changeOption",
            [gid, {key: str(value) for key, value in options.items()}],
        )

    # -- native HLS / DASH (aria2-next) -----------------------------------
    def supports_media(self) -> bool:
        """Whether the server can download HLS/DASH natively (aria2-next)."""

        if self._media_supported is None:
            try:
                result = self._call("aria2.getVersion")
            except DownloadError:
                self._media_supported = False
            else:
                if isinstance(result, dict):
                    features = result.get("enabledFeatures") or []
                    self._media_supported = bool(result.get("mediaFeatures")) or "HLS/DASH" in features
                else:
                    self._media_supported = False
        return self._media_supported

    def add_media(
        self,
        uri: str,
        *,
        out: str | None = None,
        save_path: str | None = None,
        quality: str = "",
        media_format: str = "mp4",
        timeout: float = 60.0,
    ) -> str:
        """Add an HLS/DASH URL as a single native media task.

        ``out`` is a base name without extension; aria2-next appends the
        container extension. When *quality* is given the stream is probed and
        the closest matching video track is selected.
        """

        options: dict = {"media": "auto", "media-format": media_format}
        if quality:
            options["media-pause-after-probe"] = "true"
        else:
            options["media-video"] = "best"

        gid = self.add_uri(uri, out=out, save_path=save_path, options=options)
        if not quality:
            return gid

        track = self._wait_track(gid, quality, timeout=timeout)
        update: dict = {"media-pause-after-probe": "false"}
        if track:
            update["media-video"] = track
        try:
            self.change_option(gid, update)
            self.resume(gid)
        except DownloadError:
            pass
        return gid

    def _wait_track(self, gid: str, quality: str, *, timeout: float) -> str | None:
        import time

        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.tell(gid) or {}
            media = status.get("media") or {}
            if media.get("state") in ("awaiting-selection", "downloading", "recording", "complete", "error"):
                return _pick_track(media.get("tracks") or [], quality)
            time.sleep(1)
        return None

    @staticmethod
    def _norm(path: str) -> str:
        return path.replace("\\", "/").rstrip("/")

    def _local_target(self, path: str) -> Path:
        """Translate an aria2-side path to the path this machine can access."""

        if path:
            current = self._norm(path)
            for server, local in self.path_map:
                server_norm = self._norm(str(server))
                if not server_norm:
                    continue
                if current == server_norm:
                    return Path(local)
                if current.startswith(server_norm + "/"):
                    relative = current[len(server_norm) + 1 :]
                    return Path(local) / relative
        return Path(path)

    def _local_path(self, path: str) -> Path | None:
        if not path:
            return None
        candidate = self._local_target(path)
        if candidate.exists():
            return candidate
        if self.download_dir:
            fallback = Path(self.download_dir) / Path(path).name
            if fallback.exists():
                return fallback
        return None

    def rename_file(self, torrent_id: str, old_path: str, new_path: str) -> bool:
        source = self._local_path(old_path)
        if source is None:
            return False
        target = self._local_target(new_path)
        if not target.is_absolute():
            target = source.parent / target.name
        if source == target:
            return True
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
        except OSError:
            return False
        return True

    def rename_folder(self, torrent_id: str, old_path: str, new_path: str) -> bool:
        return self.rename_file(torrent_id, old_path, new_path)

    def close(self) -> None:
        self._client.close()


def _pick_track(tracks: list[dict], quality: str) -> str | None:
    """Pick the video track whose height best matches *quality* (e.g. ``1080p``)."""

    match = re.search(r"\d+", quality or "")
    if not match:
        return None
    wanted = int(match.group(0))
    candidates = [t for t in tracks if t.get("type") in ("video", "muxed") and t.get("id")]
    best_id: str | None = None
    best_diff: int | None = None
    for track in candidates:
        height = int(track.get("height") or 0)
        if height == wanted:
            return str(track["id"])
        if height:
            diff = abs(height - wanted)
            if best_diff is None or diff < best_diff:
                best_id, best_diff = str(track["id"]), diff
    return best_id
