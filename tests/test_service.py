from amine_downloader.config import AppConfig, RssFeed
from amine_downloader.downloaders.base import BaseDownloader
from amine_downloader.models import DownloadTask, RssEpisode, TorrentFile, TorrentInfo
from amine_downloader.parser import parse_title
from amine_downloader.service import DownloadService
from amine_downloader.store import Store


class FakeDownloader(BaseDownloader):
    name = "fake"

    def __init__(self, files):
        self.files = files
        self.added: list[tuple[str, dict]] = []
        self.renames: list[tuple[str, str]] = []
        self.rename_plan = None

    def test_connection(self) -> str:
        return "fake"

    def add(self, source, *, save_path=None, name=None, category=None, paused=False, rename_plan=None) -> str:
        self.added.append((source, {"save_path": save_path, "category": category}))
        self.rename_plan = rename_plan
        return "hash1"

    def get(self, torrent_id):
        return TorrentInfo(id=torrent_id, name="torrent", progress=1.0, files=self.files)

    def list(self):
        return []

    def remove(self, torrent_id, *, delete_files=False):
        pass

    def rename_file(self, torrent_id, old_path, new_path):
        self.renames.append((old_path, new_path))
        return True


def build_service(tmp_path, files):
    config = AppConfig()
    client = FakeDownloader(files)
    store = Store(tmp_path / "data.db")
    return DownloadService(config, client=client, store=store), client, store


def test_add_torrent_records_task(tmp_path):
    service, client, store = build_service(tmp_path, [])
    task = service.add_torrent(
        "magnet:?xt=urn:btih:abc",
        raw_title="[Group] Anime - 02 [1080p]",
        rename=False,
    )
    assert task.torrent_id == "hash1"
    assert task.title == "Anime"
    assert task.episode == "2"
    assert store.has("magnet:?xt=urn:btih:abc")
    assert client.added[0][0] == "magnet:?xt=urn:btih:abc"


def test_rename_task_uses_standard_name(tmp_path):
    files = [TorrentFile(path="[Group] Anime - 01 [1080p].mkv", size=100, progress=1.0)]
    service, client, store = build_service(tmp_path, files)
    task = service.add_torrent(
        "magnet:?xt=urn:btih:abc",
        raw_title="[Group] Anime - 01 [1080p]",
        rename=False,
    )
    assert service.rename_task(task) is True
    assert client.renames == [("[Group] Anime - 01 [1080p].mkv", "[Group] Anime S01E01 [1080p].mkv")]
    assert store.get(task.key).status == "renamed"


class FakeAria2Client(BaseDownloader):
    name = "aria2"
    rename_requires_complete = True
    path_style = "local"

    def __init__(self, files):
        self.files = files
        self.renames: list[tuple[str, str]] = []

    def test_connection(self) -> str:
        return "aria2"

    def add(self, source, *, save_path=None, name=None, category=None, paused=False, rename_fn=None) -> str:
        return "gid1"

    def get(self, torrent_id):
        return TorrentInfo(id=torrent_id, name="torrent", progress=1.0, files=self.files)

    def list(self):
        return []

    def remove(self, torrent_id, *, delete_files=False):
        pass

    def rename_file(self, torrent_id, old_path, new_path):
        self.renames.append((old_path, new_path))
        return True


def test_aria2_renames_after_complete(tmp_path):
    files = [TorrentFile(path="/wenwen/downloads/Sintel/Sintel.mp4", size=1, progress=1.0)]
    config = AppConfig()
    client = FakeAria2Client(files)
    service = DownloadService(config, client=client, store=Store(tmp_path / "data.db"))
    task = DownloadTask(
        key="k",
        raw_title="[Group] Sintel the Movie - 01 [1080p]",
        torrent_id="gid1",
        group="Group",
        title="Sintel the Movie",
        season=1,
        episode="1",
        resolution="1080p",
    )
    service.store.upsert(task)
    assert service.rename_task(task) is True
    assert client.renames == [
        (
            "/wenwen/downloads/Sintel/Sintel.mp4",
            "/wenwen/downloads/Sintel/[Group] Sintel the Movie S01E01 [1080p].mp4",
        )
    ]


def test_resolve_title_alias_and_override(tmp_path):
    service, _, _ = build_service(tmp_path, [])
    service.config.title_aliases = {"旧名": "新名"}
    parsed = parse_title("旧名 - 01 [1080p]")
    assert service.resolve_title(parsed).title == "新名"
    assert service.resolve_title(parsed, "手动名").title == "手动名"

    task = service.add_torrent(
        "magnet:?xt=urn:btih:xyz",
        raw_title="旧名 - 01 [1080p]",
        rename=False,
        title_override="手动名",
    )
    assert task.title == "手动名"


def test_library_target(tmp_path):
    files = [TorrentFile(path="Torrent/Sintel.mp4", size=1, progress=1.0)]
    service, client, _ = build_service(tmp_path, files)
    service.config.library = {"enabled": True, "root": "/media/anime"}
    parsed = parse_title("[Group] Sintel the Movie - 01 [1080p]")
    # qBittorrent: series folder + season folder to rename into
    assert service.library_target(parsed) == ("/media/anime/Sintel the Movie", "Season 01")

    config = AppConfig()
    config.library = {"enabled": True, "root": "/media/anime"}
    aria2_client = FakeAria2Client([])
    aria2_service = DownloadService(config, client=aria2_client, store=Store(tmp_path / "a.db"))
    # aria2: season folder is the download dir directly
    assert aria2_service.library_target(parsed) == ("/media/anime/Sintel the Movie/Season 01", "")


def test_add_torrent_library_save_path_and_flatten(tmp_path):
    files = [TorrentFile(path="Torrent/Sintel.mp4", size=1, progress=1.0)]
    service, client, _ = build_service(tmp_path, files)
    service.config.library = {"enabled": True, "root": "/media/anime"}
    task = service.add_torrent("magnet:?xt=urn:btih:abc", raw_title="[Group] Sintel the Movie - 01 [1080p]")
    assert task.save_path == "/media/anime/Sintel the Movie"
    assert client.rename_plan is not None
    assert client.rename_plan([(0, "Torrent/Sintel.mp4", 1)]) == {
        0: "[Group] Sintel the Movie S01E01 [1080p].mp4"
    }


def _fake_episodes():
    return [
        RssEpisode(
            title="[Lilith-Raws] Anime - 01 [1080p]",
            torrent_url="http://tracker/1.torrent",
            parsed=parse_title("[Lilith-Raws] Anime - 01 [1080p]"),
        ),
        RssEpisode(
            title="[ANi] Anime - 02 [1080p]",
            torrent_url="http://tracker/2.torrent",
            parsed=parse_title("[ANi] Anime - 02 [1080p]"),
        ),
    ]


def test_run_allowlists_groups(tmp_path, monkeypatch):
    import amine_downloader.service as service_module

    monkeypatch.setattr(service_module, "fetch_feed", lambda url, **kwargs: _fake_episodes())
    service, _, _ = build_service(tmp_path, [])
    service.config.rss = [RssFeed(name="F", url="http://feed", groups=["Lilith"])]
    results = service.run()
    assert [item.episode.title for item in results] == ["[Lilith-Raws] Anime - 01 [1080p]"]


def test_run_excludes_groups(tmp_path, monkeypatch):
    import amine_downloader.service as service_module

    monkeypatch.setattr(service_module, "fetch_feed", lambda url, **kwargs: _fake_episodes())
    service, _, _ = build_service(tmp_path / "b", [])
    service.config.rss = [RssFeed(name="F", url="http://feed", exclude_groups=["ANi"])]
    results = service.run()
    assert [item.episode.title for item in results] == ["[Lilith-Raws] Anime - 01 [1080p]"]


def test_run_emits_progress_events(tmp_path, monkeypatch):
    import amine_downloader.service as service_module

    monkeypatch.setattr(service_module, "fetch_feed", lambda url, **kwargs: _fake_episodes())
    service, _, _ = build_service(tmp_path / "p", [])
    service.config.rss = [RssFeed(name="F", url="http://feed")]
    events: list[dict] = []
    service.run(on_event=events.append)
    progress = [event for event in events if event["type"] == "progress"]
    assert progress[0] == {"type": "progress", "done": 0, "total": 2, "message": "F"}
    assert progress[-1]["done"] == 2 and progress[-1]["total"] == 2


def test_build_rename_plan(tmp_path):
    service, _, _ = build_service(tmp_path, [])
    parsed = parse_title("[Group] Sintel the Movie - 01 [1080p]")
    plan = service._build_rename_plan(parsed)(
        [(5, "Sintel/Sintel.mp4", 100), (0, "Sintel/Sintel.en.srt", 10)]
    )
    assert plan[5] == "Sintel/[Group] Sintel the Movie S01E01 [1080p].mp4"
    # single video -> subtitle renamed to match it
    assert plan[0] == "Sintel/[Group] Sintel the Movie S01E01 [1080p].en.srt"


def test_episode_offset(tmp_path):
    service, _, _ = build_service(tmp_path, [])
    task = service.add_torrent(
        "magnet:?xt=urn:btih:abc",
        raw_title="[Group] Anime - 01 [1080p]",
        rename=False,
    )
    service.config.episode_offset = 12
    task2 = service.add_torrent(
        "magnet:?xt=urn:btih:def",
        raw_title="[Group] Anime - 01 [1080p]",
        rename=False,
    )
    assert task.episode == "1"
    assert task2.episode == "13"
