"""Kazumi-style download: rule parsing -> stream sniffing -> aria2 -> rename."""

from __future__ import annotations

import posixpath
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from ..config import AppConfig, KazumiSubscription, default_data_path, kazumi_rules_dir
from ..downloaders.aria2 import Aria2Downloader
from ..errors import AmineError
from ..models import DownloadTask, ParsedTitle
from ..parser import parse_title
from ..renamer import render
from ..store import Store
from .client import Episode, Road, RuleClient, SearchItem
from .hls import fetch_playlist
from .rule import KazumiRule, RuleStore
from .sniffer import BrowserSniffer, classify, pick_best

_EPISODE_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")


@dataclass(slots=True)
class KazumiResult:
    title: str
    episode: str
    stream_url: str = ""
    kind: str = ""
    task_id: str = ""
    output: str = ""
    skipped: str = ""

    @property
    def ok(self) -> bool:
        return not self.skipped


@dataclass(slots=True)
class KazumiSearchHit:
    rule: KazumiRule
    item: SearchItem


class KazumiService:
    """Ties the rule engine, the sniffer and aria2 together."""

    def __init__(
        self,
        config: AppConfig,
        *,
        store: Store | None = None,
        aria2: Aria2Downloader | None = None,
        rules: RuleStore | None = None,
    ) -> None:
        self.config = config
        self.settings: dict = dict(config.kazumi or {})
        self.rules = rules or RuleStore(kazumi_rules_dir(config))
        self.store = store or Store(default_data_path())
        self._aria2 = aria2
        self._sniffer: BrowserSniffer | None = None
        self._sniffer_error = ""

    @property
    def aria2(self) -> Aria2Downloader:
        if self._aria2 is None:
            self._aria2 = self._build_aria2()
        return self._aria2

    def _build_aria2(self) -> Aria2Downloader:
        settings = self.config.aria2 or {}
        rpc_url = settings.get("rpc_url")
        if not rpc_url:
            raise AmineError("解析下载需要配置 [aria2].rpc_url（aria2 负责实际下载）")
        return Aria2Downloader(
            rpc_url=str(rpc_url),
            secret=str(settings.get("secret", "")),
            download_dir=str(settings.get("download_dir", "")),
        )

    # -- settings helpers -------------------------------------------------
    @property
    def rename_template(self) -> str:
        return str(self.settings.get("rename_template") or "{title} S{season}E{episode}")

    @property
    def preferred_quality(self) -> str:
        return str(self.settings.get("preferred_quality") or "")

    def save_path(self, override: str | None = None) -> str | None:
        for candidate in (
            override,
            self.settings.get("save_path"),
            self.config.save_path,
            (self.config.aria2 or {}).get("download_dir"),
        ):
            if candidate:
                return str(candidate)
        return None

    def library_path(self, parsed: ParsedTitle) -> str | None:
        """Season folder inside the media library, or ``None`` when disabled."""

        library = self.config.library or {}
        if not library.get("enabled"):
            return None
        root = str(library.get("root") or "").strip()
        if not root:
            return None
        series = render(parsed, str(library.get("series_template") or "{title}")) or parsed.title
        season = render(parsed, str(library.get("season_template") or "Season {season}"))
        if not season:
            season = f"Season {parsed.season:02d}"
        return posixpath.join(root, series, season)

    def target_path(self, parsed: ParsedTitle, override: str | None = None) -> str | None:
        return override or self.library_path(parsed) or self.save_path()

    def close(self) -> None:
        if self._sniffer is not None:
            self._sniffer.close()
        if self._aria2 is not None:
            self._aria2.close()
        self.store.close()

    def _sniff(self, page_url: str, hit: KazumiSearchHit) -> list[str]:
        if self._sniffer is None:
            self._sniffer = BrowserSniffer(
                headless=bool(self.settings.get("headless", True)),
                humanize=bool(self.settings.get("humanize", False)),
                user_agent=str(self.settings.get("user_agent") or "") or None,
                referer=hit.rule.referer or hit.rule.base_url or None,
                proxy=str(self.settings.get("proxy") or "") or None,
                license_key=str(self.settings.get("license_key") or "") or None,
                temp_dir=str(self.settings.get("temp_dir") or "") or None,
                auto_install=bool(self.settings.get("auto_install_browser", True)),
            )
        return self._sniffer.sniff(page_url, timeout=float(self.settings.get("sniff_timeout", 30)))

    # -- rules ------------------------------------------------------------
    def available_rules(self) -> list[KazumiRule]:
        return self.rules.list()

    def require_rule(self, name: str | None) -> KazumiRule:
        if name:
            rule = self.rules.get(name)
            if rule is None:
                raise AmineError(f"未找到规则: {name}（可用规则见 `kazumi rules`）")
            return rule
        rules = self.available_rules()
        if not rules:
            raise AmineError("规则目录为空，请先导入规则：kazumi import <文件或URL>")
        if len(rules) == 1:
            return rules[0]
        raise AmineError("存在多条规则，请用 --rule 指定（可用规则见 `kazumi rules`）")

    # -- search / chapters ------------------------------------------------
    def search_all(
        self,
        keyword: str,
        *,
        rules: list[KazumiRule] | None = None,
        max_workers: int | None = None,
        on_event=None,
    ) -> tuple[list[KazumiSearchHit], list[str]]:
        """Search every rule **concurrently**; a failing source is skipped."""

        selected = rules if rules is not None else self.available_rules()
        hits: list[KazumiSearchHit] = []
        errors: list[str] = []
        if not selected:
            return hits, errors

        workers = max_workers or int(self.settings.get("search_workers", 8) or 8)
        workers = max(1, min(workers, len(selected)))

        def query(rule: KazumiRule):
            with RuleClient(rule) as client:
                return client.search(keyword)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(query, rule): rule for rule in selected}
            for future in as_completed(futures):
                rule = futures[future]
                try:
                    items = future.result()
                except Exception as exc:  # noqa: BLE001 - 单个源失败不影响其它
                    errors.append(f"{rule.name}: {exc}")
                    if on_event is not None:
                        on_event({"type": "log", "message": f"[{rule.name}] 失败: {exc}"})
                    continue
                if on_event is not None:
                    on_event({"type": "log", "message": f"[{rule.name}] {len(items)} 条"})
                for item in items:
                    hits.append(KazumiSearchHit(rule=rule, item=item))
        return hits, errors

    def search(self, keyword: str, *, rule_name: str | None = None, on_event=None) -> list[KazumiSearchHit]:
        if rule_name:
            rules = [self.require_rule(rule_name)]
        else:
            rules = self.available_rules()
            if not rules:
                raise AmineError("规则目录为空，请先导入规则")
        hits, _errors = self.search_all(keyword, rules=rules, on_event=on_event)
        return hits

    def chapters(self, rule: KazumiRule, item: SearchItem) -> list[Road]:
        with RuleClient(rule) as client:
            return client.chapters(item.source)

    # -- download ---------------------------------------------------------
    def download(
        self,
        keyword: str,
        *,
        rule_name: str | None = None,
        hit_index: int = 0,
        road_index: int = 0,
        episode: str | None = None,
        limit: int | None = None,
        quality: str | None = None,
        save_path: str | None = None,
        dry_run: bool = False,
        sniffed_url: str | None = None,
        title: str | None = None,
        on_event=None,
    ) -> list[KazumiResult]:
        def emit(event: dict) -> None:
            if on_event is not None:
                on_event(event)

        hits = self.search(keyword, rule_name=rule_name)
        if not hits:
            raise AmineError(f"没有搜索到: {keyword}")
        if hit_index >= len(hits):
            raise AmineError(f"搜索结果不足 {hit_index + 1} 条")
        hit = hits[hit_index]
        emit({"type": "log", "message": f"匹配到作品: {hit.item.name}（{hit.rule.name}）"})
        roads = self.chapters(hit.rule, hit.item)
        if not roads:
            raise AmineError(f"没有解析到剧集: {hit.item.name}")
        if road_index >= len(roads):
            raise AmineError(f"线路不足 {road_index + 1} 条")
        road = roads[road_index]
        episodes = road.episodes
        if episode is not None:
            episodes = [
                ep for index, ep in enumerate(road.episodes) if self._same_episode(ep, index, str(episode))
            ]
            if not episodes:
                raise AmineError(f"未找到第 {episode} 集")
        if limit:
            episodes = episodes[:limit]
        emit({"type": "log", "message": f"共 {len(episodes)} 集待处理"})

        quality = quality or self.preferred_quality
        results: list[KazumiResult] = []
        for index, ep in enumerate(episodes):
            result = self._download_episode(
                hit,
                ep,
                index,
                quality=quality,
                save_path=save_path,
                dry_run=dry_run,
                sniffed_url=sniffed_url,
                title_override=title,
            )
            results.append(result)
            emit({"type": "item", "item": asdict(result)})
        return results

    # -- subscriptions ----------------------------------------------------
    def subscriptions(self, names: list[str] | None = None) -> list[KazumiSubscription]:
        subs = [sub for sub in self.config.kazumi_subscriptions if sub.enabled]
        if names:
            wanted = {name.lower() for name in names}
            subs = [sub for sub in subs if sub.name.lower() in wanted]
        return subs

    def run(
        self,
        *,
        names: list[str] | None = None,
        dry_run: bool = False,
        limit: int | None = None,
        on_event=None,
    ) -> list[KazumiResult]:
        """Process every enabled Kazumi subscription, skipping seen episodes."""

        def emit(event: dict) -> None:
            if on_event is not None:
                on_event(event)

        results: list[KazumiResult] = []
        for subscription in self.subscriptions(names):
            emit({"type": "log", "message": f"订阅: {subscription.name}"})
            try:
                items = self._run_subscription(subscription, dry_run=dry_run, limit=limit)
            except AmineError as exc:
                items = [KazumiResult(title=subscription.name, episode="", skipped=str(exc))]
            for item in items:
                results.append(item)
                emit({"type": "item", "item": asdict(item)})
        return results

    def _run_subscription(
        self, subscription: KazumiSubscription, *, dry_run: bool, limit: int | None
    ) -> list[KazumiResult]:
        hits = self.search(subscription.name, rule_name=subscription.rule or None)
        if not hits:
            return [KazumiResult(title=subscription.name, episode="", skipped="没有搜索结果")]
        hit = hits[min(max(subscription.hit, 0), len(hits) - 1)]
        roads = self.chapters(hit.rule, hit.item)
        if not roads:
            return [KazumiResult(title=subscription.name, episode="", skipped="没有解析到剧集")]
        road = roads[min(max(subscription.road, 0), len(roads) - 1)]

        pending: list[tuple[int, Episode, str, str]] = []
        for index, episode in enumerate(road.episodes):
            number = self._episode_number(episode, index)
            key = f"kazumi:{hit.rule.name}:{hit.item.source}:{number}"
            if self.store.has(key):
                continue
            pending.append((index, episode, number, key))
        if limit:
            pending = pending[:limit]

        results: list[KazumiResult] = []
        for index, episode, number, key in pending:
            results.append(
                self._download_episode(
                    hit,
                    episode,
                    index,
                    quality=subscription.quality or self.preferred_quality,
                    save_path=subscription.save_path or None,
                    dry_run=dry_run,
                    sniffed_url=None,
                    key=key,
                    title_override=subscription.title or None,
                )
            )
        return results

    def _download_episode(
        self,
        hit: KazumiSearchHit,
        episode: Episode,
        index: int,
        *,
        quality: str,
        save_path: str | None,
        dry_run: bool,
        sniffed_url: str | None,
        key: str | None = None,
        title_override: str | None = None,
    ) -> KazumiResult:
        number = self._episode_number(episode, index)
        override = (title_override or "").strip()
        title = override or self._clean_title(hit.item.name)
        base = parse_title(title)
        if not override:
            alias = str(self.config.title_aliases.get(base.title, "")).strip()
            if alias:
                base = replace(base, title=alias)
        parsed = ParsedTitle(
            raw=title,
            group=base.group,
            title=base.title or title,
            season=base.season,
            episode=number,
        )

        stream_url = sniffed_url or episode.url
        if stream_url and not classify(stream_url):
            stream_url = ""
        if not stream_url:
            if not episode.page_url:
                return KazumiResult(title=title, episode=number, skipped="没有播放页地址")
            if self._sniffer_error:
                return KazumiResult(title=title, episode=number, skipped=self._sniffer_error)
            try:
                urls = self._sniff(episode.page_url, hit)
            except AmineError as exc:
                self._sniffer_error = str(exc)
                return KazumiResult(title=title, episode=number, skipped=str(exc))
            stream_url = pick_best(urls, quality) or ""
        if not stream_url:
            return KazumiResult(title=title, episode=number, skipped="未嗅探到视频流")

        kind = classify(stream_url) or "file"
        if dry_run:
            return KazumiResult(title=title, episode=number, stream_url=stream_url, kind=kind)

        if kind == "file":
            ext = Path(stream_url.split("?")[0]).suffix or ".mp4"
            filename = render(parsed, self.rename_template, ext=ext)
            try:
                gid = self.aria2.add_uri(stream_url, out=filename, save_path=self.target_path(parsed, save_path))
            except AmineError as exc:
                return KazumiResult(title=title, episode=number, stream_url=stream_url, kind=kind, skipped=str(exc))
            self._record(hit, parsed, stream_url, gid, filename, key)
            return KazumiResult(
                title=title, episode=number, stream_url=stream_url, kind=kind, task_id=gid, output=filename
            )

        return self._download_hls(
            hit, parsed, title, number, stream_url, quality=quality, save_path=save_path, key=key
        )

    def _download_hls(
        self,
        hit: KazumiSearchHit,
        parsed: ParsedTitle,
        title: str,
        number: str,
        stream_url: str,
        *,
        quality: str,
        save_path: str | None,
        key: str | None = None,
    ) -> KazumiResult:
        mode = str(self.settings.get("media_mode") or "auto").lower()
        native = False
        if mode != "segments":
            try:
                native = self.aria2.supports_media()
            except AmineError:
                native = False
        if mode == "native" and not native:
            return KazumiResult(
                title=title, episode=number, stream_url=stream_url, kind="hls",
                skipped="aria2 不支持原生 HLS（需要 aria2-next），或改用 media_mode=segments",
            )
        if native:
            return self._download_hls_native(
                hit, parsed, title, number, stream_url, quality=quality, save_path=save_path, key=key
            )
        return self._download_hls_segments(
            hit, parsed, title, number, stream_url, quality=quality, save_path=save_path, key=key
        )

    def _download_hls_native(
        self,
        hit: KazumiSearchHit,
        parsed: ParsedTitle,
        title: str,
        number: str,
        stream_url: str,
        *,
        quality: str,
        save_path: str | None,
        key: str | None = None,
    ) -> KazumiResult:
        out_base = render(parsed, self.rename_template)
        try:
            gid = self.aria2.add_media(
                stream_url,
                out=out_base,
                save_path=self.target_path(parsed, save_path),
                quality=quality,
            )
        except AmineError as exc:
            return KazumiResult(title=title, episode=number, stream_url=stream_url, kind="hls", skipped=str(exc))
        output = f"{out_base}.mp4"
        self._record(hit, parsed, stream_url, gid, output, key)
        return KazumiResult(
            title=title, episode=number, stream_url=stream_url, kind="hls", task_id=gid, output=output
        )

    def _download_hls_segments(
        self,
        hit: KazumiSearchHit,
        parsed: ParsedTitle,
        title: str,
        number: str,
        stream_url: str,
        *,
        quality: str,
        save_path: str | None,
        key: str | None = None,
    ) -> KazumiResult:
        playlist = fetch_playlist(stream_url, preferred=quality)
        target_dir = Path(self.target_path(parsed, save_path) or ".")
        ffmpeg = self._ffmpeg_path()

        if playlist.encrypted:
            if not ffmpeg:
                return KazumiResult(
                    title=title, episode=number, stream_url=stream_url, kind="hls",
                    skipped="HLS 已加密，需要 ffmpeg 才能下载（在 [kazumi].ffmpeg 配置）",
                )
            output = render(parsed, self.rename_template, ext=".mp4")
            self._run_ffmpeg([ffmpeg, "-y", "-i", stream_url, "-c", "copy", str(target_dir / output)])
            self._record(hit, parsed, stream_url, "", output, key)
            return KazumiResult(title=title, episode=number, stream_url=stream_url, kind="hls", output=output)

        if not playlist.segments:
            return KazumiResult(title=title, episode=number, stream_url=stream_url, kind="hls", skipped="播放列表为空")

        pieces: list[tuple[str, str]] = []
        if playlist.init_segment:
            pieces.append((playlist.init_segment, "00000.seg"))
        for offset, segment in enumerate(playlist.segments, start=1):
            pieces.append((segment, f"{offset:05d}.seg"))

        temp_root = str(self.settings.get("temp_dir") or "") or None
        if temp_root:
            Path(temp_root).mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix="amine-hls-", dir=temp_root))
        try:
            self.aria2.set_global_option({"max-concurrent-downloads": self.settings.get("concurrency", 8)})
            gids: list[str] = []
            options = {"auto-file-renaming": "false", "allow-overwrite": "true", "split": "1"}
            try:
                for segment_url, name in pieces:
                    gids.append(
                        self.aria2.add_uri(
                            segment_url,
                            out=name,
                            save_path=str(temp_dir),
                            options=options,
                        )
                    )
            except AmineError as exc:
                return KazumiResult(title=title, episode=number, stream_url=stream_url, kind="hls", skipped=str(exc))

            timeout = max(600.0, len(gids) * 15.0)
            statuses = self.aria2.wait_for(gids, timeout=timeout)
            failed = [gid for gid, ok in statuses.items() if not ok]
            if failed:
                return KazumiResult(
                    title=title, episode=number, stream_url=stream_url, kind="hls",
                    skipped=f"{len(failed)}/{len(gids)} 个分片下载失败",
                )

            merged = temp_dir / f"merged{playlist.extension}"
            with merged.open("wb") as output_handle:
                for _, name in pieces:
                    piece = temp_dir / name
                    if piece.exists():
                        output_handle.write(piece.read_bytes())

            ext = playlist.extension
            if ext == ".ts" and ffmpeg:
                final_name = render(parsed, self.rename_template, ext=".mp4")
                final_path = target_dir / final_name
                self._run_ffmpeg([ffmpeg, "-y", "-i", str(merged), "-c", "copy", str(final_path)])
                output_name = final_name
            else:
                output_name = render(parsed, self.rename_template, ext=ext)
                final_path = target_dir / output_name
                final_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(merged, final_path)

            for gid in gids:
                self.aria2.remove_result(gid)
            self._record(hit, parsed, stream_url, "", output_name, key)
            return KazumiResult(
                title=title, episode=number, stream_url=stream_url, kind="hls", output=output_name
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # -- helpers ----------------------------------------------------------
    def _record(
        self,
        hit: KazumiSearchHit,
        parsed: ParsedTitle,
        source: str,
        task_id: str,
        output: str,
        key: str | None = None,
    ) -> None:
        key = key or f"kazumi:{hit.rule.name}:{hit.item.source}:{parsed.episode}"
        self.store.upsert(
            DownloadTask(
                key=key,
                raw_title=f"{parsed.title} - {parsed.episode}",
                client="aria2",
                torrent_id=task_id,
                group=parsed.group,
                title=parsed.title,
                season=parsed.season,
                episode=parsed.episode,
                new_name=output,
                source=source,
                status="downloaded",
            )
        )

    @staticmethod
    def _clean_title(name: str) -> str:
        cleaned = parse_title(name)
        return cleaned.title or name.strip()

    @staticmethod
    def _episode_number(episode: Episode, index: int) -> str:
        match = _EPISODE_NUMBER_RE.search(episode.name or "")
        if match:
            return match.group(1)
        return str(index + 1)

    @classmethod
    def _same_episode(cls, episode: Episode, index: int, wanted: str) -> bool:
        actual = cls._episode_number(episode, index)
        try:
            return float(actual) == float(wanted)
        except ValueError:
            return actual == wanted

    def _ffmpeg_path(self) -> str | None:
        configured = self.settings.get("ffmpeg")
        if configured:
            return str(configured)
        return shutil.which("ffmpeg")

    @staticmethod
    def _run_ffmpeg(command: list[str]) -> None:
        try:
            subprocess.run(command, check=True, capture_output=True)
        except FileNotFoundError as exc:
            raise AmineError(f"找不到 ffmpeg: {command[0]}") from exc
        except subprocess.CalledProcessError as exc:
            message = exc.stderr.decode(errors="ignore") if exc.stderr else str(exc)
            raise AmineError(f"ffmpeg 执行失败: {message.strip()[:200]}") from exc
