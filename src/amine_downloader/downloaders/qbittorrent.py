"""qBittorrent WebUI API client."""

from __future__ import annotations

from pathlib import Path

import httpx

from ..bencode import BencodeError, info_hash
from ..models import TorrentFile, TorrentInfo
from .base import BaseDownloader, DownloadError, source_kind


class QBittorrentDownloader(BaseDownloader):
    name = "qbittorrent"
    rename_requires_complete = False

    def __init__(
        self,
        url: str,
        username: str = "",
        password: str = "",
        *,
        api_key: str = "",
        verify: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = url.rstrip("/")
        self.username = username
        self.password = password
        self.api_key = api_key
        headers = {"Referer": self.base_url}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            verify=verify,
            timeout=timeout,
            follow_redirects=True,
        )
        # An API key authenticates every request statelessly (qB >= 5.2).
        self._logged_in = bool(api_key)

    # -- low level --------------------------------------------------------
    def _ensure_login(self) -> None:
        if self._logged_in:
            return
        if self.api_key:
            self._logged_in = True
            return
        if not self.username:
            self._logged_in = True
            return
        response = self._post("/api/v2/auth/login", data={"username": self.username, "password": self.password})
        if response.status_code == 403:
            raise DownloadError("qBittorrent 拒绝了登录（IP 可能被暂时封禁），请稍后重试")
        if response.status_code >= 400 or response.text.strip() == "Fails.":
            raise DownloadError(
                f"qBittorrent 登录失败: {response.status_code} {response.text.strip() or '(无响应体)'}"
            )
        self._logged_in = True

    def _post(self, path: str, **kwargs) -> httpx.Response:
        try:
            return self._client.post(path, **kwargs)
        except httpx.HTTPError as exc:
            raise DownloadError(f"无法连接 qBittorrent ({self.base_url}): {exc}") from exc

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        self._ensure_login()
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise DownloadError(f"qBittorrent 请求失败: {exc}") from exc
        if response.status_code == 403 and not self.api_key:
            # Session expired, retry once after a fresh login (cookie auth only).
            self._logged_in = False
            self._ensure_login()
            try:
                response = self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                raise DownloadError(f"qBittorrent 请求失败: {exc}") from exc
        if response.status_code == 403:
            raise DownloadError("qBittorrent 认证失败，请检查 API Key / 用户名 / 密码")
        return response

    # -- interface --------------------------------------------------------
    def test_connection(self) -> str:
        version = self._request("GET", "/api/v2/app/version")
        version.raise_for_status()
        api = self._request("GET", "/api/v2/app/webapiVersion")
        api_version = api.text.strip() if api.status_code == 200 else "?"
        return f"qBittorrent {version.text.strip()} (WebAPI {api_version})"

    def add(
        self,
        source: str,
        *,
        save_path: str | None = None,
        name: str | None = None,
        category: str | None = None,
        paused: bool = False,
        rename_fn=None,
    ) -> str:
        data: dict[str, str] = {}
        files = None
        kind = source_kind(source)
        torrent_bytes: bytes | None = None
        torrent_hash = ""

        if kind == "path":
            torrent_path = Path(source)
            if not torrent_path.is_file():
                raise DownloadError(f"找不到种子文件: {source}")
            torrent_bytes = torrent_path.read_bytes()
        elif kind == "url":
            torrent_bytes = self._download_torrent(source)

        if torrent_bytes:
            try:
                torrent_hash = info_hash(torrent_bytes)
            except BencodeError:
                torrent_bytes = None

        if torrent_bytes:
            files = {
                "torrents": (
                    name or "download.torrent",
                    torrent_bytes,
                    "application/x-bittorrent",
                )
            }
        else:
            data["urls"] = source

        if save_path:
            data["savepath"] = save_path
            data["autoTMM"] = "false"
        if category:
            data["category"] = category
        if paused:
            data["paused"] = "true"
            data["stopped"] = "true"

        response = self._request("POST", "/api/v2/torrents/add", data=data, files=files)
        if response.status_code != 200:
            raise DownloadError(f"添加任务失败: {response.status_code} {response.text.strip()}")
        if response.text.strip() == "Fails.":
            raise DownloadError("qBittorrent 添加任务失败（种子可能无效）")

        if kind == "magnet":
            torrent_hash = _hash_from_magnet(source)
            if torrent_hash:
                return torrent_hash
        if torrent_hash:
            return torrent_hash
        return self._find_hash(name=name, source=source, save_path=save_path)

    def _download_torrent(self, url: str) -> bytes | None:
        try:
            response = httpx.get(url, timeout=30.0, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        content = response.content
        if not content or content[:1] != b"d" or len(content) > 32 * 1024 * 1024:
            return None
        return content

    def _find_hash(self, *, name: str | None, source: str, save_path: str | None) -> str:
        import time

        for _ in range(5):
            torrents = self._list_raw(sort="added_on", reverse="true", limit="20")
            if name:
                for torrent in torrents:
                    if torrent.get("name") == name:
                        return str(torrent["hash"])
            candidates = torrents
            if save_path:
                filtered = [t for t in candidates if t.get("save_path") == save_path]
                if filtered:
                    candidates = filtered
            if candidates:
                newest = max(candidates, key=lambda t: t.get("added_on", 0))
                return str(newest["hash"])
            time.sleep(0.5)
        raise DownloadError("任务已添加，但无法确定其 hash，请稍后用 list 查看")

    def _list_raw(self, **params) -> list[dict]:
        response = self._request("GET", "/api/v2/torrents/info", params=params or None)
        response.raise_for_status()
        return list(response.json())

    @staticmethod
    def _to_info(raw: dict, files: list[TorrentFile] | None = None) -> TorrentInfo:
        return TorrentInfo(
            id=str(raw.get("hash", "")),
            name=str(raw.get("name", "")),
            save_path=str(raw.get("save_path", "")),
            progress=float(raw.get("progress", 0.0)),
            state=str(raw.get("state", "")),
            size=int(raw.get("size", 0)),
            category=str(raw.get("category", "")),
            files=files or [],
        )

    def get(self, torrent_id: str) -> TorrentInfo | None:
        response = self._request("GET", "/api/v2/torrents/info", params={"hashes": torrent_id})
        response.raise_for_status()
        raw_list = list(response.json())
        if not raw_list:
            return None
        files: list[TorrentFile] = []
        files_response = self._request("GET", "/api/v2/torrents/files", params={"hash": torrent_id})
        if files_response.status_code == 200:
            for entry in files_response.json():
                files.append(
                    TorrentFile(
                        path=str(entry.get("name", "")),
                        size=int(entry.get("size", 0)),
                        progress=float(entry.get("progress", 0.0)),
                    )
                )
        return self._to_info(raw_list[0], files)

    def list(self) -> list[TorrentInfo]:
        return [self._to_info(raw) for raw in self._list_raw()]

    def remove(self, torrent_id: str, *, delete_files: bool = False) -> None:
        response = self._request(
            "POST",
            "/api/v2/torrents/delete",
            data={"hashes": torrent_id, "deleteFiles": "true" if delete_files else "false"},
        )
        response.raise_for_status()

    def pause(self, torrent_id: str) -> None:
        self._request_with_fallback("/api/v2/torrents/stop", "/api/v2/torrents/pause", torrent_id)

    def resume(self, torrent_id: str) -> None:
        self._request_with_fallback("/api/v2/torrents/start", "/api/v2/torrents/resume", torrent_id)

    def _request_with_fallback(self, primary: str, fallback: str, torrent_id: str) -> None:
        response = self._request("POST", primary, data={"hashes": torrent_id})
        if response.status_code == 404:
            response = self._request("POST", fallback, data={"hashes": torrent_id})
        response.raise_for_status()

    def rename_file(self, torrent_id: str, old_path: str, new_path: str) -> bool:
        response = self._request(
            "POST",
            "/api/v2/torrents/renameFile",
            data={"hash": torrent_id, "oldPath": old_path, "newPath": new_path},
        )
        return response.status_code == 200 and response.text.strip() != "Fails."

    def rename_folder(self, torrent_id: str, old_path: str, new_path: str) -> bool:
        response = self._request(
            "POST",
            "/api/v2/torrents/renameFolder",
            data={"hash": torrent_id, "oldPath": old_path, "newPath": new_path},
        )
        return response.status_code == 200 and response.text.strip() != "Fails."

    def close(self) -> None:
        self._client.close()


def _hash_from_magnet(magnet: str) -> str:
    import urllib.parse

    query = urllib.parse.urlparse(magnet).query
    for key, value in urllib.parse.parse_qsl(query):
        if key.lower() == "xt" and value.startswith("urn:btih:"):
            return value[len("urn:btih:") :].lower()
    return ""
