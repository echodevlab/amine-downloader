from pathlib import Path

from amine_downloader.config import AppConfig, KazumiSubscription
from amine_downloader.kazumi.client import Episode, Road, SearchItem
from amine_downloader.kazumi.download import KazumiSearchHit, KazumiService
from amine_downloader.kazumi.rule import KazumiRule, RuleStore
from amine_downloader.store import Store


class FakeAria2:
    def __init__(self, base=None):
        self.base = Path(base) if base else Path(".")
        self.calls: list[tuple[str, str]] = []
        self.counter = 0

    def add_uri(self, uri, *, out=None, save_path=None, options=None):
        self.counter += 1
        target = (Path(save_path) if save_path else self.base) / out
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"SEG:{uri}".encode())
        self.calls.append((uri, out))
        return f"gid{self.counter}"

    def wait_for(self, gids, *, timeout=600.0, poll=1.0):
        return {gid: True for gid in gids}

    def remove_result(self, gid):
        pass

    def set_global_option(self, options):
        pass

    def close(self):
        pass


def build_service(tmp_path, aria2, subscriptions):
    config = AppConfig()
    config.kazumi = {"rename_template": "{title} S{season}E{episode}"}
    config.kazumi_subscriptions = subscriptions
    return KazumiService(
        config,
        store=Store(tmp_path / "data.db"),
        aria2=aria2,
        rules=RuleStore(tmp_path / "rules"),
    )


def make_hit():
    rule = KazumiRule.from_dict({"name": "Test", "baseURL": "https://site.test/"})
    return KazumiSearchHit(rule=rule, item=SearchItem(name="葬送的芙莉莲", source="https://site.test/v/1"))


def patch(service, roads):
    service.search = lambda *a, **k: [make_hit()]
    service.chapters = lambda *a, **k: roads


def test_search_all_concurrent_and_tolerant(tmp_path, monkeypatch):
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
            if self.rule.name == "B":
                raise RuntimeError("boom")
            return [SearchItem(name=f"{self.rule.name}-hit", source="x")]

    monkeypatch.setattr(download_module, "RuleClient", FakeClient)
    service = build_service(tmp_path, FakeAria2(tmp_path), [])
    hits, errors = service.search_all("kw", rules=rules)
    assert sorted(hit.item.name for hit in hits) == ["A-hit", "C-hit"]
    assert len(errors) == 1 and errors[0].startswith("B:")


def test_subscription_downloads_and_dedups(tmp_path):
    fake = FakeAria2(tmp_path)
    service = build_service(
        tmp_path, fake, [KazumiSubscription(name="芙莉莲", rule="Test", initial="all")]
    )
    roads = [
        Road(
            name="线路1",
            episodes=[
                Episode(name="第1集", url="https://cdn.test/1.mp4"),
                Episode(name="第2集", url="https://cdn.test/2.mp4"),
            ],
        )
    ]
    patch(service, roads)

    first = service.run()
    assert [r.episode for r in first] == ["1", "2"]
    assert all(r.ok for r in first)
    assert len(fake.calls) == 2

    second = service.run()
    assert second == []


def test_subscription_limit_and_names(tmp_path):
    fake = FakeAria2(tmp_path)
    service = build_service(
        tmp_path,
        fake,
        [
            KazumiSubscription(name="芙莉莲", rule="Test", initial="all"),
            KazumiSubscription(name="别的番", rule="Test", initial="all"),
        ],
    )
    roads = [
        Road(
            name="线路1",
            episodes=[Episode(name=f"第{i}集", url=f"https://cdn.test/{i}.mp4") for i in range(1, 5)],
        )
    ]
    patch(service, roads)

    results = service.run(names=["芙莉莲"], limit=2)
    assert [r.episode for r in results] == ["1", "2"]


def test_subscription_dry_run_does_not_download(tmp_path):
    fake = FakeAria2(tmp_path)
    service = build_service(tmp_path, fake, [KazumiSubscription(name="芙莉莲", rule="Test")])
    patch(service, [Road(name="线路1", episodes=[Episode(name="第1集", url="https://cdn.test/1.mp4")])])

    results = service.run(dry_run=True)
    assert len(results) == 1
    assert results[0].stream_url == "https://cdn.test/1.mp4"
    assert fake.calls == []
