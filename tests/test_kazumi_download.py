from pathlib import Path

import amine_downloader.kazumi.download as download_module
from amine_downloader.config import AppConfig
from amine_downloader.kazumi.client import SearchItem
from amine_downloader.kazumi.download import KazumiSearchHit, KazumiService
from amine_downloader.kazumi.hls import HlsPlaylist
from amine_downloader.kazumi.rule import KazumiRule, RuleStore
from amine_downloader.models import ParsedTitle
from amine_downloader.store import Store


class FakeAria2:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.dirs: list[str | None] = []
        self.counter = 0

    def add_uri(self, uri, *, out=None, save_path=None, options=None):
        self.counter += 1
        target = Path(save_path) / out
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"SEG:{uri}".encode())
        self.calls.append((uri, out))
        self.dirs.append(save_path)
        return f"gid{self.counter}"

    def wait_for(self, gids, *, timeout=600.0, poll=1.0):
        return {gid: True for gid in gids}

    def remove_result(self, gid):
        pass

    def set_global_option(self, options):
        pass

    def supports_media(self):
        return False

    def close(self):
        pass


class FakeNativeAria2(FakeAria2):
    def supports_media(self):
        return True

    def add_media(self, uri, *, out=None, save_path=None, quality="", media_format="mp4", timeout=60.0):
        self.calls.append((uri, out))
        self.dirs.append(save_path)
        return "native-gid"


def build_service(tmp_path, aria2):
    config = AppConfig()
    config.kazumi = {"rename_template": "{title} S{season}E{episode}"}
    return KazumiService(
        config,
        store=Store(tmp_path / "data.db"),
        aria2=aria2,
        rules=RuleStore(tmp_path / "rules"),
    )


def make_hit():
    rule = KazumiRule.from_dict({"name": "Test", "baseURL": "https://site.test/"})
    return KazumiSearchHit(rule=rule, item=SearchItem(name="葬送的芙莉莲", source="https://site.test/v/1"))


def test_hls_download_merges_segments(tmp_path, monkeypatch):
    fake = FakeAria2()
    service = build_service(tmp_path, fake)
    playlist = HlsPlaylist(
        url="https://cdn.test/index.m3u8",
        segments=["https://cdn.test/seg-1.ts", "https://cdn.test/seg-2.ts"],
        extension=".ts",
    )
    monkeypatch.setattr(download_module, "fetch_playlist", lambda *a, **k: playlist)
    monkeypatch.setattr(service, "_ffmpeg_path", lambda: None)

    parsed = ParsedTitle(raw="葬送的芙莉莲", title="葬送的芙莉莲", episode="1")
    result = service._download_hls(
        make_hit(), parsed, "葬送的芙莉莲", "1", playlist.url, quality="", save_path=str(tmp_path)
    )

    assert result.ok
    assert result.output == "葬送的芙莉莲 S01E01.ts"
    merged = (tmp_path / result.output).read_bytes()
    assert merged == b"SEG:https://cdn.test/seg-1.tsSEG:https://cdn.test/seg-2.ts"


def test_direct_file_goes_to_aria2(tmp_path):
    fake = FakeAria2()
    service = build_service(tmp_path, fake)
    hit = make_hit()

    from amine_downloader.kazumi.client import Episode

    episode = Episode(name="第1集", page_url="https://site.test/play/1")
    result = service._download_episode(
        hit,
        episode,
        0,
        quality="",
        save_path=str(tmp_path),
        dry_run=False,
        sniffed_url="https://cdn.test/movie.mp4",
    )
    assert result.ok
    assert result.kind == "file"
    assert result.output == "葬送的芙莉莲 S01E01.mp4"
    assert fake.calls == [("https://cdn.test/movie.mp4", "葬送的芙莉莲 S01E01.mp4")]


def test_hls_native_uses_single_task(tmp_path):
    fake = FakeNativeAria2()
    service = build_service(tmp_path, fake)
    hit = make_hit()
    from amine_downloader.kazumi.client import Episode

    result = service._download_episode(
        hit,
        Episode(name="第1集", page_url="https://site.test/play/1"),
        0,
        quality="1080p",
        save_path=str(tmp_path),
        dry_run=False,
        sniffed_url="https://cdn.test/index.m3u8",
    )
    assert result.ok
    assert result.kind == "hls"
    assert result.task_id == "native-gid"
    assert result.output == "葬送的芙莉莲 S01E01.mp4"
    assert fake.calls == [("https://cdn.test/index.m3u8", "葬送的芙莉莲 S01E01")]


def test_title_override_changes_filename(tmp_path):
    fake = FakeNativeAria2()
    service = build_service(tmp_path, fake)
    hit = make_hit()
    from amine_downloader.kazumi.client import Episode

    result = service._download_episode(
        hit,
        Episode(name="第1集", page_url="https://site.test/play/1"),
        0,
        quality="",
        save_path=str(tmp_path),
        dry_run=False,
        sniffed_url="https://cdn.test/movie.mp4",
        title_override="自定义番剧名",
    )
    assert result.title == "自定义番剧名"
    assert result.output == "自定义番剧名 S01E01.mp4"
    assert fake.calls == [("https://cdn.test/movie.mp4", "自定义番剧名 S01E01.mp4")]


def test_kazumi_library_path_and_download_dir(tmp_path):
    fake = FakeAria2()
    service = build_service(tmp_path, fake)
    service.config.library = {"enabled": True, "root": "/media/anime"}
    parsed = ParsedTitle(raw="x", title="葬送的芙莉莲", season=1, episode="1")
    assert service.library_path(parsed) == "/media/anime/葬送的芙莉莲/Season 01"
    assert service.target_path(parsed) == "/media/anime/葬送的芙莉莲/Season 01"

    native = FakeNativeAria2()
    service2 = build_service(tmp_path, native)
    service2.config.library = {"enabled": True, "root": str(tmp_path / "lib")}
    hit = make_hit()
    from amine_downloader.kazumi.client import Episode

    result = service2._download_episode(
        hit,
        Episode(name="第1集", page_url="https://site.test/play/1"),
        0,
        quality="",
        save_path=None,
        dry_run=False,
        sniffed_url="https://cdn.test/movie.mp4",
    )
    assert result.ok
    assert "Season 01" in str(native.dirs[-1])


def test_dry_run_does_not_touch_aria2(tmp_path):
    fake = FakeAria2()
    service = build_service(tmp_path, fake)
    hit = make_hit()
    from amine_downloader.kazumi.client import Episode

    result = service._download_episode(
        hit,
        Episode(name="第1集", page_url="https://site.test/play/1"),
        0,
        quality="",
        save_path=str(tmp_path),
        dry_run=True,
        sniffed_url="https://cdn.test/index.m3u8",
    )
    assert result.kind == "hls"
    assert fake.calls == []
