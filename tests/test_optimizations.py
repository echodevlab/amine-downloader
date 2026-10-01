"""Tests for the behaviour changes described in docs/OPTIMIZATION.md."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import amine_downloader.server as server
import amine_downloader.service as service_module
from amine_downloader.config import AppConfig, KazumiSubscription, RssFeed
from amine_downloader.downloaders.base import BaseDownloader
from amine_downloader.jobs import JobManager
from amine_downloader.kazumi.client import Episode, Road, SearchItem
from amine_downloader.kazumi.download import KazumiSearchHit, KazumiService
from amine_downloader.kazumi.rule import KazumiRule, RuleStore
from amine_downloader.models import RssEpisode
from amine_downloader.parser import parse_title
from amine_downloader.service import DownloadService
from amine_downloader.store import Store


# -- P0-1 stable search order + rule/source --------------------------------
def test_search_all_order_is_stable(tmp_path, monkeypatch):
    import amine_downloader.kazumi.download as download_module

    rules = [
        KazumiRule.from_dict({"name": "A"}),
        KazumiRule.from_dict({"name": "B"}),
        KazumiRule.from_dict({"name": "C"}),
    ]

    class FakeClient:
        def __init__(self, rule):
            self.rule = rule

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def search(self, keyword):
            # Make the last rule answer first to exercise ordering.
            if self.rule.name == "A":
                time.sleep(0.05)
            return [SearchItem(name=f"{self.rule.name}-hit", source=f"src-{self.rule.name}")]

    monkeypatch.setattr(download_module, "RuleClient", FakeClient)
    config = AppConfig()
    rule_store = RuleStore(tmp_path / "rules")
    for rule in rules:
        rule_store.save(rule)
    service = KazumiService(
        config, store=Store(tmp_path / "data.db"), rules=rule_store
    )
    hits, errors = service.search_all("kw", rules=rules)
    assert [hit.rule.name for hit in hits] == ["A", "B", "C"]
    assert errors == []

    hit = service.hit_from("B", "src-B", name="B-hit")
    assert hit.rule.name == "B"
    assert hit.item.source == "src-B"


# -- P0-2 cancellation -----------------------------------------------------
class FakeDownloader(BaseDownloader):
    name = "fake"

    def __init__(self):
        self.added: list[str] = []

    def test_connection(self):
        return "fake"

    def add(self, source, *, save_path=None, name=None, category=None, paused=False, rename_plan=None):
        self.added.append(source)
        return "id"

    def get(self, torrent_id):
        return None

    def list(self):
        return []

    def remove(self, torrent_id, *, delete_files=False):
        pass

    def rename_file(self, torrent_id, old_path, new_path):
        return True


def _episode(title: str, url: str) -> RssEpisode:
    return RssEpisode(title=title, torrent_url=url, parsed=parse_title(title))


def test_run_stops_when_cancelled(tmp_path, monkeypatch):
    episodes = [
        _episode("[A] Anime - 01 [1080p]", "http://t/1"),
        _episode("[A] Anime - 02 [1080p]", "http://t/2"),
    ]
    monkeypatch.setattr(service_module, "fetch_feed", lambda url, **kwargs: episodes)
    service = DownloadService(AppConfig(), client=FakeDownloader(), store=Store(tmp_path / "d.db"))
    service.config.rss = [RssFeed(name="F", url="http://f", initial="all", one_per_episode=False)]
    cancel = threading.Event()
    cancel.set()
    results = service.run(cancel=cancel)
    assert results == []
    assert not service.store.has("http://t/1")


# -- P0-4 / P0-5 initial policy and one-per-episode ------------------------
def test_one_per_episode_picks_by_resolution(tmp_path, monkeypatch):
    episodes = [
        _episode("[A] Anime - 01 [720p]", "http://t/a1"),
        _episode("[B] Anime - 01 [1080p]", "http://t/b1"),
        _episode("[A] Anime - 02 [720p]", "http://t/a2"),
    ]
    monkeypatch.setattr(service_module, "fetch_feed", lambda url, **kwargs: episodes)
    service = DownloadService(AppConfig(), client=FakeDownloader(), store=Store(tmp_path / "d.db"))
    service.config.resolution_preference = ["1080p", "720p"]
    service.config.rss = [RssFeed(name="F", url="http://f", one_per_episode=True, initial="all")]
    results = service.run()
    assert [item.episode.title for item in results] == [
        "[B] Anime - 01 [1080p]",  # 1080p preferred over 720p
        "[A] Anime - 02 [720p]",
    ]


def test_initial_latest_marks_others_seen(tmp_path, monkeypatch):
    episodes = [
        _episode("[A] Anime - 01 [1080p]", "http://t/1"),
        _episode("[A] Anime - 02 [1080p]", "http://t/2"),
        _episode("[A] Anime - 03 [1080p]", "http://t/3"),
    ]
    monkeypatch.setattr(service_module, "fetch_feed", lambda url, **kwargs: episodes)
    service = DownloadService(AppConfig(), client=FakeDownloader(), store=Store(tmp_path / "d.db"))
    service.config.rss = [
        RssFeed(name="F", url="http://f", initial="latest", one_per_episode=False)
    ]
    results = service.run()
    assert [item.episode.title for item in results] == ["[A] Anime - 03 [1080p]"]
    assert service.store.count(status="seen") == 2
    assert service.run() == []


def test_initial_none_marks_all_seen(tmp_path, monkeypatch):
    episodes = [
        _episode("[A] Anime - 01 [1080p]", "http://t/1"),
        _episode("[A] Anime - 02 [1080p]", "http://t/2"),
    ]
    monkeypatch.setattr(service_module, "fetch_feed", lambda url, **kwargs: episodes)
    service = DownloadService(AppConfig(), client=FakeDownloader(), store=Store(tmp_path / "d.db"))
    service.config.rss = [RssFeed(name="F", url="http://f", initial="none", one_per_episode=False)]
    assert service.run() == []
    assert service.store.count(status="seen") == 2


def test_failed_records_are_retried(tmp_path, monkeypatch):
    from amine_downloader.models import DownloadTask

    episodes = [_episode("[A] Anime - 01 [1080p]", "http://t/1")]
    monkeypatch.setattr(service_module, "fetch_feed", lambda url, **kwargs: episodes)
    service = DownloadService(AppConfig(), client=FakeDownloader(), store=Store(tmp_path / "d.db"))
    service.config.rss = [RssFeed(name="F", url="http://f", initial="all", one_per_episode=False)]
    service.store.upsert(DownloadTask(key="http://t/1", raw_title="x", status="failed"))
    results = service.run()
    assert len(results) == 1
    assert service.store.get("http://t/1").status == "added"


# -- rename_all skips settled statuses ------------------------------------
def test_rename_all_skips_settled_statuses(tmp_path):
    from amine_downloader.models import DownloadTask

    service = DownloadService(AppConfig(), client=FakeDownloader(), store=Store(tmp_path / "d.db"))
    for key, status in (
        ("a", "downloaded"),
        ("b", "no-video"),
        ("c", "failed"),
        ("d", "seen"),
    ):
        service.store.upsert(DownloadTask(key=key, raw_title=key, status=status))
    assert service.rename_all() == 0


# -- P1-14 episode parsing ------------------------------------------------
@pytest.mark.parametrize(
    "name,expected",
    [
        ("第2季 第05集", "5"),
        ("2024 01", "1"),
        ("EP 12", "12"),
        ("S02E07", "7"),
        ("第10话", "10"),
    ],
)
def test_episode_number_prefers_markers(name, expected):
    assert KazumiService._episode_number(Episode(name=name), 0) == expected


def test_episode_spec_ranges():
    assert KazumiService._episode_spec("1-3") == {"1", "2", "3"}
    assert KazumiService._episode_spec("1,3,5") == {"1", "3", "5"}
    assert KazumiService._episode_spec("1-2, 5") == {"1", "2", "5"}


# -- re-fetch link on error ------------------------------------------------
def test_retry_rss_refetches_fresh_link(tmp_path, monkeypatch):
    import amine_downloader.rss as rss_module
    from amine_downloader.models import DownloadTask

    fresh = _episode("[A] Anime - 02 [1080p]", "http://t/fresh-2")
    monkeypatch.setattr(rss_module, "fetch_feed", lambda url, **kwargs: [fresh])

    config = AppConfig()
    config.rss = [RssFeed(name="F", url="http://feed")]
    service = DownloadService(config, client=FakeDownloader(), store=Store(tmp_path / "d.db"))
    task = DownloadTask(
        key="http://t/old-2",
        raw_title="[A] Anime - 02 [1080p]",
        client="qbittorrent",
        title="Anime",
        season=1,
        episode="2",
        source="http://t/old-2",
    )
    result = server._retry_rss(config, service, task)
    assert result["ok"] and result["source"] == "http://t/fresh-2"
    assert service.client.added[0] == "http://t/fresh-2"
    assert service.store.has("http://t/old-2")


def test_retry_kazumi_needs_rule_info(tmp_path):
    from amine_downloader.models import DownloadTask

    config = AppConfig()
    task = DownloadTask(key="kazumi:", raw_title="x", client="aria2", title="x")
    with pytest.raises(Exception):
        server._retry_kazumi(config, task)


def test_feed_from_body_parses_and_validates_regex():
    feed = server._feed_from_body(
        {
            "url": "http://feed",
            "names": ["A"],
            "exclude_names": ["B"],
            "name_regex": ["^A"],
            "exclude_name_regex": ["B|C"],
        }
    )
    assert feed.names == ["A"]
    assert feed.exclude_names == ["B"]
    assert feed.name_regex == ["^A"]
    assert feed.exclude_name_regex == ["B|C"]
    with pytest.raises(Exception):
        server._feed_from_body({"url": "http://feed", "name_regex": ["("]})


def test_parse_kazumi_key_handles_url_source():
    # 作品地址里含 ":"，不能简单 split(":")
    assert server._parse_kazumi_key("kazumi:DM84:https://dmbus.cc/v/1484.html:1") == (
        "DM84",
        "https://dmbus.cc/v/1484.html",
        "1",
    )
    assert server._parse_kazumi_key("kazumi:AGE:http://a/b?x=1:2:12") == (
        "AGE",
        "http://a/b?x=1:2",
        "12",
    )
    assert server._parse_kazumi_key("kazumi:") is None
    assert server._parse_kazumi_key("magnet:?xt=abc") is None


# -- preview does not collide with a running download ----------------------
def test_preview_kind_is_separate():
    assert server._PREVIEW_KINDS["rss_run"] == "rss_preview"
    assert server._PREVIEW_KINDS["kazumi_download"] == "kazumi_download_preview"
    assert "rss_run" not in server._PREVIEW_KINDS.values()


# -- P1-12 job mutex -------------------------------------------------------
def test_job_manager_same_kind_mutex():
    manager = JobManager()
    release = threading.Event()

    def work(emit, cancel):
        release.wait(2)
        return "done"

    first = manager.submit("rss_run", {}, work)
    second = manager.submit("rss_run", {}, work)
    assert first.id == second.id
    release.set()
    time.sleep(0.05)


# -- P2 path traversal -----------------------------------------------------
def test_spa_rejects_path_traversal():
    dist = server._DIST
    if not dist.exists():
        pytest.skip("frontend not built")
    secret = dist.parent / "secret_test.txt"
    secret.write_text("TOPSECRET", encoding="utf-8")
    try:
        client = TestClient(server.app)
        response = client.get("/a/..%2f..%2fsecret_test.txt")
        assert "TOPSECRET" not in response.text
    finally:
        secret.unlink(missing_ok=True)


# -- P1-9 config defaults --------------------------------------------------
def test_config_defaults_match_appconfig():
    assert server.CONFIG_DEFAULTS["app"]["downloader"] == AppConfig().downloader
    assert server.CONFIG_DEFAULTS["library"]["enabled"] is False
    assert server.CONFIG_DEFAULTS["aria2"]["rpc_url"] == "http://127.0.0.1:6800/jsonrpc"


# -- P1-8 config editing preserves comments --------------------------------
def test_rss_subscription_endpoints(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text("# hello\n[app]\ncategory = \"amine\"\n", encoding="utf-8")
    monkeypatch.setattr(server, "_CONFIG_PATH", str(path))
    client = TestClient(server.app)

    assert client.post("/api/rss", json={"name": "F", "url": "http://feed"}).status_code == 200
    feeds = client.get("/api/rss").json()
    assert feeds[0]["name"] == "F"
    assert client.put(
        "/api/rss/0", json={"name": "F2", "url": "http://feed2", "initial": "none"}
    ).status_code == 200
    assert client.delete("/api/rss/0").status_code == 200
    assert client.get("/api/rss").json() == []
    # comments outside the edited section survive
    assert "# hello" in path.read_text(encoding="utf-8")


def test_put_config_preserves_comments(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(
        "# keep this comment\n[app]\n# and this one\ndownloader = \"qbittorrent\"\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "_CONFIG_PATH", str(path))
    doc = server._read_doc()
    server._merge_into_doc(doc, {"app": {"category": "anime"}})
    server._write_doc(doc)
    text = path.read_text(encoding="utf-8")
    assert "# keep this comment" in text
    assert "# and this one" in text
    assert 'category = "anime"' in text
