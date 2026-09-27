"""Sniff direct video streams (m3u8 / mp4) from a playback page.

Kazumi uses a WebView and hooks network requests. In Python we do the same
with CloakBrowser (a stealth Chromium with a Playwright-compatible API).

A :class:`BrowserSniffer` keeps one browser alive so a whole batch of episodes
only pays the launch cost once.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from ..errors import AmineError

MEDIA_RE = re.compile(r"\.(m3u8|mp4|flv|mkv|webm|mp3|m4s|ts)(?:[?#]|$)", re.IGNORECASE)
_HLS_RE = re.compile(r"\.m3u8(?:[?#]|$)", re.IGNORECASE)

_PLAY_SELECTORS = (
    "video",
    ".dplayer-play-icon",
    ".vjs-big-play-button",
    ".play-btn",
    "button:has-text('播放')",
    "text=播放",
)


@dataclass(slots=True)
class SniffResult:
    url: str
    kind: str  # "hls" or "file"


def classify(url: str) -> str | None:
    if not url or not MEDIA_RE.search(url):
        return None
    return "hls" if _HLS_RE.search(url) else "file"


def _score(url: str, preferred: str) -> tuple[int, int]:
    kind = classify(url)
    kind_score = 2 if kind == "hls" else 1
    quality_score = 0
    if preferred:
        wanted = preferred.lower().replace("p", "")
        if wanted and wanted in url.lower():
            quality_score = 1
    return (kind_score, quality_score)


def pick_best(urls: list[str], preferred: str = "") -> str | None:
    """Pick the best sniffed URL: prefer HLS, then a matching quality."""

    unique = list(dict.fromkeys(url for url in urls if classify(url)))
    if not unique:
        return None
    return max(unique, key=lambda url: _score(url, preferred))


class BrowserSniffer:
    """Reusable stealth browser that captures media URLs from pages."""

    def __init__(
        self,
        *,
        headless: bool = True,
        humanize: bool = False,
        user_agent: str | None = None,
        referer: str | None = None,
        proxy: str | None = None,
        license_key: str | None = None,
        temp_dir: str | None = None,
        auto_install: bool = True,
    ) -> None:
        self.headless = headless
        self.humanize = humanize
        self.user_agent = user_agent
        self.referer = referer
        self.proxy = proxy
        self.license_key = license_key
        self.temp_dir = temp_dir
        self.auto_install = auto_install
        self._browser = None
        self._context = None

    def _install_binary(self) -> None:
        """Download the CloakBrowser Chromium binary on first use."""

        ensure = None
        try:
            from cloakbrowser import ensure_binary as ensure  # type: ignore[attr-defined]
        except ImportError:
            try:
                from cloakbrowser.download import ensure_binary as ensure  # type: ignore[no-redef]
            except ImportError:
                return
        try:
            ensure()
        except Exception as exc:  # noqa: BLE001 - cloakbrowser raises many types
            raise AmineError(f"下载 CloakBrowser 二进制失败: {exc}") from exc

    def _ensure(self) -> None:
        if self._context is not None:
            return
        try:
            from cloakbrowser import launch
        except ImportError as exc:  # pragma: no cover - depends on optional install
            raise AmineError("需要 CloakBrowser 才能嗅探视频：uv add cloakbrowser") from exc

        if self.auto_install:
            self._install_binary()

        if self.temp_dir:
            directory = Path(self.temp_dir).expanduser()
            directory.mkdir(parents=True, exist_ok=True)
            resolved = str(directory.resolve())
            # Playwright's driver reads these when it starts, so set before launch.
            for key in ("TMP", "TEMP", "TMPDIR"):
                os.environ[key] = resolved

        kwargs: dict = {"headless": self.headless}
        if self.humanize:
            kwargs["humanize"] = True
        if self.proxy:
            kwargs["proxy"] = self.proxy
        if self.license_key:
            kwargs["license_key"] = self.license_key

        try:
            self._browser = launch(**kwargs)
        except Exception as exc:  # noqa: BLE001 - cloakbrowser raises many types
            raise AmineError(f"启动浏览器失败: {exc}") from exc

        context_kwargs: dict = {}
        if self.user_agent:
            context_kwargs["user_agent"] = self.user_agent
        if self.referer:
            context_kwargs["extra_http_headers"] = {"Referer": self.referer}
        try:
            self._context = self._browser.new_context(**context_kwargs)
        except Exception as exc:  # noqa: BLE001
            self.close()
            raise AmineError(f"创建浏览器上下文失败: {exc}") from exc

    def sniff(self, url: str, *, timeout: float = 30.0) -> list[str]:
        """Open *url* and return every media URL seen, deduplicated."""

        self._ensure()
        assert self._context is not None
        captured: list[str] = []

        def record(candidate: str) -> None:
            if classify(candidate) and candidate not in captured:
                captured.append(candidate)

        page = self._context.new_page()
        try:
            page.on("request", lambda request: record(request.url))
            page.on("response", lambda response: record(response.url))
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            except Exception as exc:  # noqa: BLE001
                if not captured:
                    raise AmineError(f"打开播放页失败 ({url}): {exc}") from exc

            deadline = time.time() + timeout
            clicked = False
            while time.time() < deadline and not captured:
                page.wait_for_timeout(500)
                if not clicked:
                    _try_click_play(page)
                    clicked = True
            return captured
        finally:
            try:
                page.close()
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
            self._context = None


def sniff(
    url: str,
    *,
    timeout: float = 30.0,
    headless: bool = True,
    user_agent: str | None = None,
    referer: str | None = None,
    humanize: bool = False,
    proxy: str | None = None,
    license_key: str | None = None,
    temp_dir: str | None = None,
    auto_install: bool = True,
) -> list[str]:
    """One-shot convenience wrapper around :class:`BrowserSniffer`."""

    sniffer = BrowserSniffer(
        headless=headless,
        humanize=humanize,
        user_agent=user_agent,
        referer=referer,
        proxy=proxy,
        license_key=license_key,
        temp_dir=temp_dir,
        auto_install=auto_install,
    )
    try:
        return sniffer.sniff(url, timeout=timeout)
    finally:
        sniffer.close()


def _try_click_play(page) -> None:
    for selector in _PLAY_SELECTORS:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible():
                locator.click(timeout=2000)
                return
        except Exception:  # noqa: BLE001 - best effort
            continue
