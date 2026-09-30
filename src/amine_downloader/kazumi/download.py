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


def _to_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalise_number(value: str) -> str:
    """``05`` -> ``5`` while keeping ``5.5`` intact."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return str(value)


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
        library = self.config.library or {}
        path_map: list[tuple[str, str]] = []
        if library.get("enabled") and library.get("root") and library.get("local_root"):
            path_map.append((str(library["root"]), str(library["local_root"])))
        path_map.extend((str(src), str(dst)) for src, dst in (self.config.path_map or []))
        return Aria2Downloader(
            rpc_url=str(rpc_url),
            secret=str(settings.get("secret", "")),
            download_dir=str(settings.get("download_dir", "")),
            local_dir=str(settings.get("local_dir", "")),
            path_map=path_map,
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

        # Collect results per rule, then flatten in the original rule order so
        # that "第 N 个结果" stays stable no matter which source answers first.
        items_by_rule: dict[int, list[SearchItem]] = {}
        error_by_rule: dict[int, str] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(query, rule): index for index, rule in enumerate(selected)}
            for future in as_completed(futures):
                index = futures[future]
                rule = selected[index]
                try:
                    items_by_rule[index] = future.result()
                except Exception as exc:  # noqa: BLE001 - 单个源失败不影响其它
                    error_by_rule[index] = str(exc)

        for index, rule in enumerate(selected):
            error = error_by_rule.get(index)
            if error is not None:
                errors.append(f"{rule.name}: {error}")
                if on_event is not None:
                    on_event({"type": "log", "message": f"[{rule.name}] 失败: {error}"})
                continue
            items = items_by_rule.get(index, [])
            if on_event is not None:
                on_event({"type": "log", "message": f"[{rule.name}] {len(items)} 条"})
            for item in items:
                hits.append(KazumiSearchHit(rule=rule, item=item))
        return hits, errors

    def hit_from(
        self, rule_name: str | None, source: str, *, name: str = "", page_url: str = ""
    ) -> KazumiSearchHit:
        """Rebuild a search hit from ``rule + source`` without searching again."""

        if not source:
            raise AmineError("缺少作品地址（source）")
        if rule_name:
            rule = self.require_rule(rule_name)
        else:
            try:
                rule = self.require_rule(None)
            except AmineError:
                # No unambiguous rule: xpath-style chapters only need base_url.
                rule = KazumiRule(name="(inline)", base_url=source)
        return KazumiSearchHit(
            rule=rule,
            item=SearchItem(name=name or source, source=source, page_url=page_url or source),
        )

    def resolve_hit(
        self,
        keyword: str,
        *,
        rule_name: str | None = None,
        source: str | None = None,
        name: str = "",
        hit_index: int = 0,
    ) -> KazumiSearchHit:
        """Locate a work by ``rule + source`` when given, else by searching."""

        if source:
            return self.hit_from(rule_name, source, name=name)
        hits = self.search(keyword, rule_name=rule_name)
        if not hits:
            raise AmineError(f"没有搜索到: {keyword}")
        if hit_index >= len(hits):
            raise AmineError(f"搜索结果不足 {hit_index + 1} 条")
        return hits[hit_index]

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
        source: str | None = None,
        hit_name: str = "",
        hit_index: int = 0,
        road_index: int = 0,
        episode: str | None = None,
        limit: int | None = None,
        quality: str | None = None,
        save_path: str | None = None,
        dry_run: bool = False,
        sniffed_url: str | None = None,
        title: str | None = None,
        force: bool = False,
        on_event=None,
        cancel=None,
    ) -> list[KazumiResult]:
        def emit(event: dict) -> None:
            if on_event is not None:
                on_event(event)

        def cancelled() -> bool:
            return cancel is not None and cancel.is_set()

        hit = self.resolve_hit(
            keyword, rule_name=rule_name, source=source, name=hit_name, hit_index=hit_index
        )
        emit({"type": "log", "message": f"匹配到作品: {hit.item.name}（{hit.rule.name}）"})
        roads = self.chapters(hit.rule, hit.item)
        if not roads:
            raise AmineError(f"没有解析到剧集: {hit.item.name}")
        if road_index >= len(roads):
            raise AmineError(f"线路不足 {road_index + 1} 条")
        road = roads[road_index]
        episodes = road.episodes
        if episode is not None and str(episode).strip():
            wanted = self._episode_spec(str(episode))
            episodes = [
                ep
                for index, ep in enumerate(road.episodes)
                if self._episode_number(ep, index) in wanted
            ]
            if not episodes:
                raise AmineError(f"未找到第 {episode} 集")
        if limit:
            episodes = episodes[:limit]
        emit({"type": "log", "message": f"共 {len(episodes)} 集待处理"})

        quality = quality or self.preferred_quality
        total = len(episodes)
        emit({"type": "progress", "done": 0, "total": total, "message": "解析下载"})
        results: list[KazumiResult] = []
        for index, ep in enumerate(episodes):
            if cancelled():
                emit({"type": "log", "message": "任务已取消"})
                break
            number = self._episode_number(ep, index)
            key = f"kazumi:{hit.rule.name}:{hit.item.source}:{number}"
            if not dry_run and not force and self.store.has_done(key):
                result = KazumiResult(
                    title=hit.item.name, episode=number, skipped="已下载过（勾选「重新下载」可强制）"
                )
                results.append(result)
                emit({"type": "item", "item": asdict(result)})
                emit({"type": "progress", "done": index + 1, "total": total, "message": "解析下载"})
                continue
            result = self._download_episode(
                hit,
                ep,
                index,
                quality=quality,
                save_path=save_path,
                dry_run=dry_run,
                sniffed_url=sniffed_url,
                title_override=title,
                key=key,
            )
            results.append(result)
            emit({"type": "item", "item": asdict(result)})
            emit({"type": "progress", "done": index + 1, "total": total, "message": "解析下载"})
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
        cancel=None,
    ) -> list[KazumiResult]:
        """Process every enabled Kazumi subscription, skipping seen episodes."""

        def emit(event: dict) -> None:
            if on_event is not None:
                on_event(event)

        results: list[KazumiResult] = []
        subscriptions = self.subscriptions(names)
        total = len(subscriptions)
        emit({"type": "progress", "done": 0, "total": total, "message": "追番"})
        for index, subscription in enumerate(subscriptions):
            if cancel is not None and cancel.is_set():
                emit({"type": "log", "message": "任务已取消"})
                break
            emit({"type": "log", "message": f"订阅: {subscription.name}"})
            try:
                items = self._run_subscription(subscription, dry_run=dry_run, limit=limit)
            except AmineError as exc:
                items = [KazumiResult(title=subscription.name, episode="", skipped=str(exc))]
            for item in items:
                results.append(item)
                emit({"type": "item", "item": asdict(item)})
            emit({"type": "progress", "done": index + 1, "total": total, "message": subscription.name})
        return results

    def _run_subscription(
        self, subscription: KazumiSubscription, *, dry_run: bool, limit: int | None
    ) -> list[KazumiResult]:
        hit = self.resolve_hit(
            subscription.name,
            rule_name=subscription.rule or None,
            source=subscription.source or None,
        )
        roads = self.chapters(hit.rule, hit.item)
        if not roads:
            return [KazumiResult(title=subscription.name, episode="", skipped="没有解析到剧集")]
        road = roads[min(max(subscription.road, 0), len(roads) - 1)]

        offset = subscription.episode_offset
        if offset is None:
            offset = self.config.episode_offset
        pending: list[tuple[int, Episode, str, str]] = []
        for index, episode in enumerate(road.episodes):
            number = self._offset_number(self._episode_number(episode, index), offset)
            key = f"kazumi:{hit.rule.name}:{hit.item.source}:{number}"
            if self.store.has_done(key):
                continue
            pending.append((index, episode, number, key))

        if not dry_run:
            pending = self._apply_initial(subscription, pending)
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
                    number=number,
                    season=subscription.season,
                    title_override=subscription.title or None,
                )
            )
        return results

    def _apply_initial(self, subscription, pending):
        """First run of a subscription: honour the ``initial`` policy."""

        marker = f"kazumi:{subscription.name}:initialized"
        if self.store.get_meta(marker):
            return pending
        self.store.set_meta(marker, "1")
        initial = str(getattr(subscription, "initial", "latest") or "latest").lower()
        if initial == "all":
            return pending
        if initial == "none":
            for _index, episode, _number, key in pending:
                self.store.mark_seen(key, episode.name)
            return []
        if not pending:
            return []
        latest = max(pending, key=lambda item: _to_float(item[2], -1.0))
        for _index, episode, _number, key in pending:
            if key != latest[3]:
                self.store.mark_seen(key, episode.name)
        return [latest]

    @staticmethod
    def _offset_number(number: str, offset: int | None) -> str:
        if not offset:
            return number
        try:
            value = float(number)
        except (TypeError, ValueError):
            return number
        if value.is_integer():
            return str(int(value) + offset)
        return str(value + offset)

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
        number: str | None = None,
        season: int | None = None,
        title_override: str | None = None,
    ) -> KazumiResult:
        number = number or self._episode_number(episode, index)
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
            season=season if season is not None else base.season,
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
        target_dir = self._local_target(self.target_path(parsed, save_path) or ".")
        ffmpeg = self._ffmpeg_path()

        if playlist.encrypted:
            if not ffmpeg:
                return KazumiResult(
                    title=title, episode=number, stream_url=stream_url, kind="hls",
                    skipped="HLS 已加密，需要 ffmpeg 才能下载（在 [kazumi].ffmpeg 配置）",
                )
            output = render(parsed, self.rename_template, ext=".mp4")
            self._run_ffmpeg([ffmpeg, "-y", "-i", stream_url, "-c", "copy", str(target_dir / output)])
            self._record(hit, parsed, stream_url, "", output, key, status="downloaded")
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
        previous_concurrency = self._global_option("max-concurrent-downloads")
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
            self._record(hit, parsed, stream_url, "", output_name, key, status="downloaded")
            return KazumiResult(
                title=title, episode=number, stream_url=stream_url, kind="hls", output=output_name
            )
        finally:
            if previous_concurrency is not None:
                self.aria2.set_global_option(
                    {"max-concurrent-downloads": previous_concurrency}
                )
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
        *,
        status: str = "added",
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
                status=status,
            )
        )

    #: 需要向 aria2 回查状态的状态。``downloaded`` 也回查，用于纠正旧版本
    #: “刚添加就标记已下载”的记录。
    _PENDING_STATUSES = ("added", "waiting-complete", "downloaded")

    def sync_statuses(self, *, on_event=None) -> dict[str, int]:
        """Update Kazumi records from aria2's real status.

        ``complete`` becomes ``downloaded``; ``error`` / ``removed`` becomes
        ``failed`` so it can be retried (重新获取链接).  A record falsely
        marked ``downloaded`` while aria2 is still working is corrected back to
        ``added``.  Returns a small counter.
        """

        counts = {"downloaded": 0, "failed": 0, "pending": 0}
        tell = getattr(self.aria2, "tell", None)
        if tell is None:
            return counts
        for task in self.store.list():
            if task.client != "aria2" or task.status not in self._PENDING_STATUSES:
                continue
            if not task.torrent_id:
                continue
            status = tell(task.torrent_id)
            if status is None:
                counts["pending"] += 1
                continue
            state = str(status.get("status") or "")
            if state == "complete":
                if task.status != "downloaded":
                    self.store.set_status(task.key, "downloaded")
                counts["downloaded"] += 1
            elif state in ("error", "removed"):
                self.store.set_status(task.key, "failed")
                counts["failed"] += 1
                if on_event is not None:
                    on_event({"type": "log", "message": f"下载失败: {task.raw_title}"})
            else:
                # 还没下完：把误标的 downloaded 纠正回 added，方便重命名/重试
                if task.status == "downloaded":
                    self.store.set_status(task.key, "added")
                counts["pending"] += 1
        return counts

    @staticmethod
    def _clean_title(name: str) -> str:
        cleaned = parse_title(name)
        return cleaned.title or name.strip()

    @staticmethod
    def _episode_number(episode: Episode, index: int) -> str:
        """Extract the episode number from an episode name.

        Prefers explicit markers (``第 N 集`` / ``EP N`` / ``SxxEyy``) so names
        like ``第2季 第05集`` or ``2024 01`` are not mis-parsed as season/year.
        """

        name = episode.name or ""
        for pattern in (
            r"第\s*(\d{1,4}(?:\.\d+)?)\s*[集话話回]",
            r"(?i)\bS\d{1,2}E(\d{1,4})\b",
            r"(?i)\bEP?\s*[-_.]?\s*(\d{1,4})\b",
            r"(?i)\bEpisode\s*[-_.]?\s*(\d{1,4})\b",
        ):
            match = re.search(pattern, name)
            if match:
                return _normalise_number(match.group(1))
        parsed = parse_title(name)
        if parsed.episode:
            return _normalise_number(parsed.episode)
        match = _EPISODE_NUMBER_RE.search(name)
        if match:
            return _normalise_number(match.group(1))
        return str(index + 1)

    @staticmethod
    def _episode_spec(spec: str) -> set[str]:
        """Parse ``1-12`` / ``1,3,5`` / ``1-12,15`` into a set of numbers."""

        wanted: set[str] = set()
        for part in re.split(r"[,\s、，]+", spec.strip()):
            if not part:
                continue
            match = re.fullmatch(r"(\d{1,4}(?:\.\d+)?)\s*[-~至]\s*(\d{1,4}(?:\.\d+)?)", part)
            if match:
                start = _to_float(match.group(1), 0.0)
                end = _to_float(match.group(2), start)
                step = 1.0
                value = start
                while value <= end + 1e-9:
                    wanted.add(_normalise_number(str(value)))
                    value += step
            else:
                wanted.add(_normalise_number(part))
        return wanted

    @classmethod
    def _same_episode(cls, episode: Episode, index: int, wanted: str) -> bool:
        actual = cls._episode_number(episode, index)
        try:
            return float(actual) == float(wanted)
        except ValueError:
            return actual == wanted

    def _local_target(self, path: str) -> Path:
        """Map an aria2-side path to the path this machine can write."""

        mapper = getattr(self.aria2, "local_target", None) or getattr(
            self.aria2, "_local_target", None
        )
        if mapper is not None:
            return Path(mapper(path))
        return Path(path)

    def _global_option(self, name: str) -> str | None:
        getter = getattr(self.aria2, "get_global_option", None)
        if getter is None:
            return None
        try:
            value = getter(name)
        except Exception:  # noqa: BLE001 - best effort restore
            return None
        return str(value) if value not in (None, "") else None

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
