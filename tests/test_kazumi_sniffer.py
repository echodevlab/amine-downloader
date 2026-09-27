import sys
import types

from amine_downloader.kazumi.sniffer import BrowserSniffer, classify, pick_best


def test_classify():
    assert classify("https://a.test/v/index.m3u8") == "hls"
    assert classify("https://a.test/v/movie.mp4?token=1") == "file"
    assert classify("https://a.test/page.html") is None
    assert classify("") is None


def test_pick_best_prefers_hls():
    urls = ["https://a.test/720.mp4", "https://a.test/index.m3u8"]
    assert pick_best(urls) == "https://a.test/index.m3u8"


def test_pick_best_quality_hint():
    urls = ["https://a.test/480/index.m3u8", "https://a.test/1080/index.m3u8"]
    assert pick_best(urls, "1080p") == "https://a.test/1080/index.m3u8"


def test_pick_best_empty():
    assert pick_best(["https://a.test/page.html"]) is None


class _FakeLocator:
    @property
    def first(self):
        return self

    def count(self):
        return 0

    def is_visible(self):
        return False

    def click(self, timeout=0):
        pass


class _FakePage:
    def __init__(self):
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler

    def goto(self, url, wait_until=None, timeout=None):
        self.handlers["request"](types.SimpleNamespace(url="https://cdn.test/index.m3u8"))

    def wait_for_timeout(self, ms):
        pass

    def locator(self, selector):
        return _FakeLocator()

    def close(self):
        pass


class _FakeContext:
    def new_page(self):
        return _FakePage()


class _FakeBrowser:
    def __init__(self):
        self.closed = False

    def new_context(self, **kwargs):
        return _FakeContext()

    def close(self):
        self.closed = True


def test_browser_sniffer_auto_installs(monkeypatch):
    calls = {"ensure": 0, "launch": 0}
    browser = _FakeBrowser()

    def fake_ensure():
        calls["ensure"] += 1

    def fake_launch(**kwargs):
        calls["launch"] += 1
        return browser

    module = types.ModuleType("cloakbrowser")
    module.ensure_binary = fake_ensure
    module.launch = fake_launch
    monkeypatch.setitem(sys.modules, "cloakbrowser", module)

    sniffer = BrowserSniffer(auto_install=True)
    sniffer.sniff("https://site.test/play/1")
    assert calls == {"ensure": 1, "launch": 1}
    sniffer.close()


def test_browser_sniffer_reuses_one_browser(monkeypatch):
    browser = _FakeBrowser()
    launches = {"count": 0}

    def fake_launch(**kwargs):
        launches["count"] += 1
        return browser

    module = types.ModuleType("cloakbrowser")
    module.launch = fake_launch
    monkeypatch.setitem(sys.modules, "cloakbrowser", module)

    sniffer = BrowserSniffer()
    first = sniffer.sniff("https://site.test/play/1")
    second = sniffer.sniff("https://site.test/play/2")
    assert launches["count"] == 1
    assert first == ["https://cdn.test/index.m3u8"]
    assert second == ["https://cdn.test/index.m3u8"]

    sniffer.close()
    assert browser.closed is True
