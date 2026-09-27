"""High level orchestration: RSS -> download client -> rename."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, replace
from pathlib import Path

from .config import AppConfig, default_data_path
from .downloaders import BaseDownloader, create_downloader
from .models import VIDEO_EXTENSIONS, DownloadTask, ParsedTitle, RssEpisode
from .parser import parse_title
from .renamer import render
from .rss import fetch_feed
from .store import Store


@dataclass(slots=True)
class RunItem:
    episode: RssEpisode
    task: DownloadTask | None = None
    skipped: str = ""


class DownloadService:
    def __init__(
        self,
        config: AppConfig,
        *,
        client: BaseDownloader | None = None,
        store: Store | None = None,
    ) -> None:
        self.config = config
        self.client = client or create_downloader(config)
        self.store = store or Store(default_data_path())

    # -- parsing / naming -------------------------------------------------
    def apply_offset(self, parsed: ParsedTitle) -> ParsedTitle:
        offset = self.config.episode_offset
        if not offset or not parsed.episode:
            return parsed
        try:
            number = float(parsed.episode)
        except ValueError:
            return parsed
        if number.is_integer():
            return replace(parsed, episode=str(int(number) + offset))
        return replace(parsed, episode=str(number + offset))

    def name_for(self, parsed: ParsedTitle, *, ext: str = "", episode: str | None = None) -> str:
        return render(parsed, self.config.rename_template, episode=episode, ext=ext)

    # -- adding -----------------------------------------------------------
    def add_torrent(
        self,
        source: str,
        *,
        raw_title: str | None = None,
        parsed: ParsedTitle | None = None,
        save_path: str | None = None,
        category: str | None = None,
        rename: bool = True,
        key: str | None = None,
        paused: bool = False,
    ) -> DownloadTask:
        raw_title = raw_title or source
        parsed = parsed or parse_title(raw_title)
        parsed = self.apply_offset(parsed)
        new_name = self.name_for(parsed) if rename else ""
        save_path = save_path if save_path is not None else (self.config.save_path or None)
        category = category if category is not None else (self.config.category or None)

        torrent_id = self.client.add(
            source,
            save_path=save_path,
            name=None,
            category=category,
            paused=paused,
            rename_fn=self._build_rename_fn(parsed) if rename else None,
        )
        task = DownloadTask(
            key=key or source,
            raw_title=raw_title,
            client=self.client.name,
            torrent_id=torrent_id,
            group=parsed.group,
            title=parsed.title,
            season=parsed.season,
            episode=parsed.episode,
            resolution=parsed.resolution,
            new_name=new_name,
            source=source,
            save_path=save_path or "",
            category=category or "",
            status="added",
        )
        self.store.upsert(task)

        if rename and not self.client.rename_requires_complete:
            self._rename_with_retry(task)
        return task

    def _rename_with_retry(self, task: DownloadTask, *, attempts: int = 3, delay: float = 1.0) -> bool:
        """qBittorrent may still be initialising metadata right after add."""

        import time

        for attempt in range(attempts):
            try:
                if self.rename_task(task):
                    return True
            except Exception:  # noqa: BLE001 - rename is best effort
                pass
            if attempt < attempts - 1:
                time.sleep(delay)
        return False

    # -- renaming ---------------------------------------------------------
    def _build_rename_fn(self, parsed: ParsedTitle):
        """Return ``(index, path, size) -> new relative path`` for a torrent.

        Used both for qBittorrent's post-add rename and for aria2's ``index-out``
        (which renames at add time). Only video files are touched.
        """

        used_names: set[str] = set()

        def rename_fn(index: int, path: str, size: int) -> str | None:
            suffix = Path(path).suffix
            if suffix.lower() not in VIDEO_EXTENSIONS:
                return None
            file_parsed = parse_title(Path(path).stem)
            item = replace(parsed)
            if file_parsed.episode:
                item = replace(item, episode=file_parsed.episode)
            if file_parsed.season > 1:
                item = replace(item, season=file_parsed.season)

            new_base = self.name_for(item)
            if not new_base:
                return None
            candidate = new_base + suffix
            if candidate in used_names:
                counter = 2
                while f"{new_base} - {counter}{suffix}" in used_names:
                    counter += 1
                candidate = f"{new_base} - {counter}{suffix}"
            used_names.add(candidate)

            parent = posixpath.dirname(path)
            return posixpath.join(parent, candidate) if parent else candidate

        return rename_fn

    def rename_task(self, task: DownloadTask) -> bool:
        if not task.torrent_id:
            return False
        info = self.client.get(task.torrent_id)
        if info is None:
            return False
        if self.client.rename_requires_complete and info.progress < 0.999:
            task.status = "waiting-complete"
            self.store.set_status(task.key, "waiting-complete")
            return False

        base_parsed = parse_title(task.raw_title)
        base_parsed = replace(
            base_parsed,
            group=task.group,
            title=task.title,
            season=task.season,
            episode=task.episode,
            resolution=task.resolution,
        )

        rename_fn = self._build_rename_fn(base_parsed)
        renamed = False
        has_video = False
        for index, entry in enumerate(info.files):
            new_path = rename_fn(index, entry.path, entry.size)
            if new_path is None:
                continue
            has_video = True
            if new_path == entry.path:
                continue
            if self.client.rename_file(task.torrent_id, entry.path, new_path):
                renamed = True

        if not has_video:
            task.status = "no-video"
            self.store.set_status(task.key, "no-video")
            return False

        if renamed:
            task.status = "renamed"
            self.store.set_status(task.key, "renamed")
        return renamed

    def rename_all(self, *, only_torrent_id: str | None = None, limit: int | None = None) -> int:
        count = 0
        for task in self.store.list(limit=limit):
            if task.status in ("renamed",):
                continue
            if only_torrent_id and task.torrent_id != only_torrent_id:
                continue
            if self.rename_task(task):
                count += 1
        return count

    # -- RSS --------------------------------------------------------------
    def enabled_feeds(self, names: list[str] | None = None):
        feeds = [feed for feed in self.config.rss if feed.enabled]
        if names:
            wanted = {name.lower() for name in names}
            feeds = [feed for feed in feeds if feed.name.lower() in wanted or feed.url.lower() in wanted]
        return feeds

    def _match_resolution(self, episode: RssEpisode) -> bool:
        preference = self.config.resolution_preference
        if not preference or episode.parsed is None:
            return True
        return episode.parsed.resolution in preference

    def run(
        self,
        *,
        feed_names: list[str] | None = None,
        limit: int | None = None,
        dry_run: bool = False,
        rename: bool = True,
        on_event=None,
    ) -> list[RunItem]:
        def emit(event: dict) -> None:
            if on_event is not None:
                on_event(event)

        results: list[RunItem] = []
        for feed in self.enabled_feeds(feed_names):
            episodes = fetch_feed(feed.url)
            emit({"type": "log", "message": f"[{feed.name}] 获取到 {len(episodes)} 条"})
            selected: list[RssEpisode] = []
            for episode in episodes:
                if not episode.torrent_url:
                    continue
                if self.store.has(episode.dedup_key):
                    continue
                if not self._match_resolution(episode):
                    continue
                selected.append(episode)
            if limit:
                selected = selected[:limit]
            for episode in selected:
                if dry_run:
                    results.append(RunItem(episode=episode, skipped="dry-run"))
                    emit({"type": "item", "item": {"title": episode.title, "skipped": "dry-run"}})
                    continue
                try:
                    task = self.add_torrent(
                        episode.torrent_url,
                        raw_title=episode.title,
                        parsed=episode.parsed,
                        rename=rename,
                        key=episode.dedup_key,
                    )
                except Exception as exc:  # noqa: BLE001
                    results.append(RunItem(episode=episode, skipped=f"error: {exc}"))
                    emit({"type": "item", "item": {"title": episode.title, "skipped": str(exc)}})
                    continue
                results.append(RunItem(episode=episode, task=task))
                emit(
                    {
                        "type": "item",
                        "item": {
                            "title": episode.title,
                            "name": task.title,
                            "episode": task.episode,
                            "task_id": task.torrent_id,
                        },
                    }
                )
        return results

    def close(self) -> None:
        self.client.close()
        self.store.close()
