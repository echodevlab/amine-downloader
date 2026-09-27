"""High level orchestration: RSS -> download client -> rename."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, replace
from pathlib import Path

from .config import AppConfig, default_data_path
from .downloaders import BaseDownloader, create_downloader
from .models import SUBTITLE_EXTENSIONS, VIDEO_EXTENSIONS, DownloadTask, ParsedTitle, RssEpisode
from .parser import parse_title
from .renamer import render
from .rss import fetch_feed
from .store import Store


@dataclass(slots=True)
class RunItem:
    episode: RssEpisode
    task: DownloadTask | None = None
    skipped: str = ""


def _subtitle_lang(stem: str) -> str:
    """Extract a trailing language tag from a subtitle file stem."""

    match = re.search(r"[-. _]([a-zA-Z]{2,3})$", stem)
    return f".{match.group(1).lower()}" if match else ""


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

    def resolve_title(self, parsed: ParsedTitle, override: str | None = None) -> ParsedTitle:
        """Apply an explicit title override, else the configured alias map."""

        title = (override or "").strip()
        if not title:
            title = str(self.config.title_aliases.get(parsed.title, "")).strip()
        if title and title != parsed.title:
            return replace(parsed, title=title)
        return parsed

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
        title_override: str | None = None,
    ) -> DownloadTask:
        raw_title = raw_title or source
        parsed = parsed or parse_title(raw_title)
        parsed = self.apply_offset(parsed)
        parsed = self.resolve_title(parsed, title_override)
        new_name = self.name_for(parsed) if rename else ""

        library = self.library_target(parsed)
        if library is not None and save_path is None:
            save_path = library[0]
        save_path = save_path if save_path is not None else (self.config.save_path or None)
        category = category if category is not None else (self.config.category or None)

        torrent_id = self.client.add(
            source,
            save_path=save_path,
            name=None,
            category=category,
            paused=paused,
            rename_plan=self._build_rename_plan(parsed, flatten=library is not None) if rename else None,
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

    def library_target(self, parsed: ParsedTitle) -> tuple[str, str] | None:
        """Return ``(save_path, season_folder)`` for library mode, or ``None``.

        aria2 can target the season folder directly (``index-out`` is relative to
        ``dir``); qBittorrent gets the series folder and its torrent folder is
        renamed to the season afterwards.
        """

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
        if self.client.name == "aria2":
            return posixpath.join(root, series, season), ""
        return posixpath.join(root, series), season

    def _rename_torrent_folder(self, task: DownloadTask, new_name: str) -> bool:
        """Rename a multi-file torrent's top folder (qBittorrent)."""

        info = self.client.get(task.torrent_id)
        if info is None or not info.files:
            return False
        first = info.files[0].path.replace("\\", "/")
        if "/" not in first:
            return False
        top = first.split("/", 1)[0]
        if not top or top == new_name:
            return False
        return self.client.rename_folder(task.torrent_id, top, new_name)

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
    def _build_rename_plan(self, parsed: ParsedTitle, *, flatten: bool = False):
        """Return ``files -> {index: new_relative_path}`` for a torrent.

        Videos are named from the template; when the torrent has exactly one
        video, external subtitles are renamed to match it (``name.de.srt``).
        Used for qBittorrent's post-add rename and aria2's ``index-out``.
        """

        def plan(files: list[tuple[int, str, int]]) -> dict[int, str]:
            used_names: set[str] = set()
            video_bases: dict[int, str] = {}
            for index, path, _size in files:
                suffix = Path(path).suffix
                if suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                file_parsed = parse_title(Path(path).stem)
                item = replace(parsed)
                if file_parsed.episode:
                    item = replace(item, episode=file_parsed.episode)
                if file_parsed.season > 1:
                    item = replace(item, season=file_parsed.season)
                base = self.name_for(item)
                if not base:
                    continue
                candidate = base + suffix
                if candidate in used_names:
                    counter = 2
                    while f"{base} - {counter}{suffix}" in used_names:
                        counter += 1
                    candidate = f"{base} - {counter}{suffix}"
                used_names.add(candidate)
                video_bases[index] = candidate

            single_video = next(iter(video_bases.values())) if len(video_bases) == 1 else ""
            result: dict[int, str] = {}
            for index, path, _size in files:
                suffix = Path(path).suffix
                if index in video_bases:
                    candidate = video_bases[index]
                elif single_video and suffix.lower() in SUBTITLE_EXTENSIONS:
                    candidate = f"{Path(single_video).stem}{_subtitle_lang(Path(path).stem)}{suffix}"
                else:
                    continue
                if flatten:
                    result[index] = candidate
                else:
                    parent = posixpath.dirname(path)
                    result[index] = posixpath.join(parent, candidate) if parent else candidate
            return result

        return plan

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

        library = self.library_target(base_parsed)
        top_folder = ""
        if library is not None and info.files:
            first = info.files[0].path.replace("\\", "/")
            if "/" in first:
                top_folder = first.split("/", 1)[0]

        plan = self._build_rename_plan(base_parsed, flatten=library is not None)(
            [(index, entry.path, entry.size) for index, entry in enumerate(info.files)]
        )
        renamed = False
        has_video = any(
            Path(entry.path).suffix.lower() in VIDEO_EXTENSIONS for entry in info.files
        )
        for index, entry in enumerate(info.files):
            new_path = plan.get(index)
            if not new_path or new_path == entry.path:
                continue
            if self.client.rename_file(task.torrent_id, entry.path, new_path):
                renamed = True

        if not has_video:
            task.status = "no-video"
            self.store.set_status(task.key, "no-video")
            return False

        season_folder = library[1] if library is not None else ""
        if top_folder and season_folder and top_folder != season_folder:
            if self.client.rename_folder(task.torrent_id, top_folder, season_folder):
                renamed = True

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

    @staticmethod
    def _group_allowed(group: str, wanted: list[str]) -> bool:
        lowered = (group or "").lower()
        return any(item.lower() in lowered for item in wanted if item)

    def _match_group(self, episode: RssEpisode, feed) -> bool:
        group = (episode.parsed.group if episode.parsed else "") or ""
        if feed.groups and not self._group_allowed(group, feed.groups):
            return False
        if feed.exclude_groups and self._group_allowed(group, feed.exclude_groups):
            return False
        return True

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
                if not self._match_group(episode, feed):
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
                        title_override=feed.title or None,
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
