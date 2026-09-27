import json

import httpx

from amine_downloader.bencode import encode
from amine_downloader.downloaders.aria2 import Aria2Downloader, _pick_track
from amine_downloader.downloaders.base import source_kind
from amine_downloader.downloaders.qbittorrent import QBittorrentDownloader


def test_source_kind(tmp_path):
    torrent = tmp_path / "a.torrent"
    torrent.write_bytes(b"d4:infode")
    assert source_kind("magnet:?xt=urn:btih:abc") == "magnet"
    assert source_kind("https://example.com/a.torrent") == "url"
    assert source_kind(str(torrent)) == "path"


def test_qbittorrent_add_get_rename():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path.endswith("/auth/login"):
            return httpx.Response(200, text="Ok.")
        if path.endswith("/torrents/add"):
            return httpx.Response(200, text="Ok.")
        if path.endswith("/torrents/info"):
            return httpx.Response(
                200,
                json=[
                    {
                        "hash": "abcdef",
                        "name": "[Group] Anime - 01 [1080p].mkv",
                        "save_path": "/dl",
                        "progress": 1.0,
                        "state": "uploading",
                        "size": 100,
                        "category": "amine",
                    }
                ],
            )
        if path.endswith("/torrents/files"):
            return httpx.Response(
                200, json=[{"name": "[Group] Anime - 01 [1080p].mkv", "size": 100, "progress": 1.0}]
            )
        if path.endswith("/torrents/renameFile"):
            return httpx.Response(200, text="Ok.")
        return httpx.Response(404)

    client = QBittorrentDownloader("http://qb:8080", "admin", "admin")
    client._client.close()
    client._client = httpx.Client(
        base_url="http://qb:8080", transport=httpx.MockTransport(handler)
    )

    torrent_id = client.add("magnet:?xt=urn:btih:ABCDEF", save_path="/dl", category="amine")
    assert torrent_id == "abcdef"

    info = client.get(torrent_id)
    assert info is not None
    assert info.files[0].path == "[Group] Anime - 01 [1080p].mkv"
    assert client.rename_file(torrent_id, info.files[0].path, "[Group] Anime S01E01 [1080p].mkv") is True
    assert any(c.url.path.endswith("/torrents/renameFile") for c in calls)


def test_qbittorrent_api_key_auth():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(500, text="login should not be called with an API key")
        if request.url.path.endswith("/app/version"):
            return httpx.Response(200, text="v5.2.3")
        if request.url.path.endswith("/app/webapiVersion"):
            return httpx.Response(200, text="2.15.1")
        return httpx.Response(404)

    client = QBittorrentDownloader("http://qb:8085", api_key="qbt_testkey")
    headers = dict(client._client.headers)
    client._client.close()
    client._client = httpx.Client(
        base_url="http://qb:8085", headers=headers, transport=httpx.MockTransport(handler)
    )
    assert client.test_connection() == "qBittorrent v5.2.3 (WebAPI 2.15.1)"
    assert seen["auth"] == "Bearer qbt_testkey"


def test_aria2_index_out():
    info = {
        b"name": b"Anime",
        b"files": [
            {b"length": 10, b"path": [b"01.mkv"]},
            {b"length": 20, b"path": [b"sub", b"02.mkv"]},
            {b"length": 5, b"path": [b"readme.txt"]},
        ],
    }
    torrent = encode({b"info": info})

    def plan(files):
        return {index: "new/" + path.rsplit("/", 1)[-1] for index, path, _size in files if path.endswith(".mkv")}

    assert Aria2Downloader._index_out(torrent, plan) == ["1=new/01.mkv", "2=new/02.mkv"]
    assert Aria2Downloader._index_out(torrent, None) == []


def test_pick_track():
    tracks = [
        {"id": "a", "type": "video", "height": "720"},
        {"id": "b", "type": "video", "height": "1080"},
        {"id": "c", "type": "audio", "height": "0"},
    ]
    assert _pick_track(tracks, "1080p") == "b"
    assert _pick_track(tracks, "720p") == "a"
    assert _pick_track(tracks, "1000p") == "b"
    assert _pick_track(tracks, "800p") == "a"
    assert _pick_track(tracks, "") is None


def test_aria2_local_path_mapping(tmp_path):
    server_dir = "/wenwen/downloads"
    local_dir = tmp_path / "downloads"
    (local_dir / "Anime").mkdir(parents=True)
    (local_dir / "Anime" / "old.mp4").write_bytes(b"x")

    client = Aria2Downloader(
        "http://aria:6800/jsonrpc", download_dir=server_dir, local_dir=str(local_dir)
    )
    assert client._local_target(f"{server_dir}/Anime/new.mp4") == local_dir / "Anime" / "new.mp4"
    assert (
        client.rename_file(
            "gid", f"{server_dir}/Anime/old.mp4", f"{server_dir}/Anime/new.mp4"
        )
        is True
    )
    assert (local_dir / "Anime" / "new.mp4").exists()
    assert not (local_dir / "Anime" / "old.mp4").exists()


def test_aria2_add_and_status():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body["method"]
        if method == "aria2.addUri":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": "gid1"})
        if method == "aria2.tellStatus":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "gid": "gid1",
                        "status": "complete",
                        "totalLength": "100",
                        "completedLength": "100",
                        "dir": "/dl",
                        "files": [
                            {
                                "path": "/dl/[Group] Anime - 01 [1080p].mkv",
                                "length": "100",
                                "completedLength": "100",
                            }
                        ],
                        "bittorrent": {"name": "[Group] Anime - 01 [1080p]"},
                    },
                },
            )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"code": -1, "message": "x"}})

    client = Aria2Downloader("http://aria:6800/jsonrpc", secret="tok")
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(handler))

    gid = client.add("magnet:?xt=urn:btih:abc")
    assert gid == "gid1"

    info = client.get("gid1")
    assert info is not None
    assert info.progress == 1.0
    assert info.name == "[Group] Anime - 01 [1080p]"
    assert info.files[0].path == "/dl/[Group] Anime - 01 [1080p].mkv"
