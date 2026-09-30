"""Small JSON API + static file server for the web UI."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import tomllib
from dataclasses import asdict
from pathlib import Path

import tomli_w
import tomlkit
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .config import (
    AppConfig,
    KazumiSubscription,
    RssFeed,
    default_config_path,
    default_data_path,
    ensure_dir,
    kazumi_rules_dir,
)
from .downloaders import create_downloader
from .downloaders.base import DownloadError
from .errors import AmineError
from .jobs import manager
from .kazumi import KazumiService, RuleStore
from .parser import parse_title
from .renamer import DEFAULT_TEMPLATE, render
from .service import DownloadService

_CONFIG_PATH: str | None = None


def _find_dist() -> Path:
    """Locate the built frontend (works in the repo and inside the Docker image)."""

    candidates: list[Path] = []
    override = os.environ.get("AMINE_DOWNLOADER_WEB_DIR")
    if override:
        candidates.append(Path(override))
    here = Path(__file__).resolve()
    candidates.append(here.parents[2] / "web" / "dist")  # repo / editable install
    candidates.append(here.parents[1] / "web" / "dist")  # installed next to package
    candidates.append(Path.cwd() / "web" / "dist")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


_DIST = _find_dist()


def configure(config_path: str | None) -> None:
    global _CONFIG_PATH
    _CONFIG_PATH = config_path


def _load_config() -> AppConfig:
    return AppConfig.load(_CONFIG_PATH)


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def _run(work):
    try:
        data = await run_in_threadpool(work)
    except (AmineError, DownloadError) as exc:
        return _error(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _error(f"{type(exc).__name__}: {exc}", 500)
    if isinstance(data, JSONResponse):
        return data
    return JSONResponse(data)


# -- endpoints ------------------------------------------------------------
async def status(request) -> JSONResponse:
    def work():
        config = _load_config()
        service = DownloadService(config)
        try:
            connection = service.client.test_connection()
            name = service.client.name
        finally:
            service.close()
        return {
            "downloader": name,
            "connection": connection,
            "config_path": str(config.path) if config.path else "",
            "rss": len(config.rss),
            "kazumi_subscriptions": len(config.kazumi_subscriptions),
            "interval": config.interval,
        }

    return await _run(work)


async def torrents(request) -> JSONResponse:
    def work():
        service = DownloadService(_load_config())
        try:
            return [asdict(item) for item in service.client.list()]
        finally:
            service.close()

    return await _run(work)


async def torrent_action(request) -> JSONResponse:
    torrent_id = request.path_params["tid"]
    action = request.path_params["action"]

    def work():
        service = DownloadService(_load_config())
        try:
            if action == "pause":
                service.client.pause(torrent_id)
            elif action == "resume":
                service.client.resume(torrent_id)
            elif action == "remove":
                delete = request.query_params.get("delete_files") in ("1", "true")
                service.client.remove(torrent_id, delete_files=delete)
                service.store.set_status_by_torrent(torrent_id, "removed")
            else:
                raise AmineError(f"未知操作: {action}")
        finally:
            service.close()
        return {"ok": True}

    return await _run(work)


async def tasks(request) -> JSONResponse:
    if request.method == "DELETE":
        key = request.query_params.get("key") or ""
        if not key:
            return _error("缺少 key")

        def delete_work():
            service = DownloadService(_load_config())
            try:
                if not service.store.delete(key):
                    raise AmineError("记录不存在")
                return {"ok": True}
            finally:
                service.close()

        return await _run(delete_work)

    status = request.query_params.get("status") or None
    title = request.query_params.get("title") or None
    search = request.query_params.get("q") or None
    limit = request.query_params.get("limit")
    offset = request.query_params.get("offset") or 0

    def work():
        service = DownloadService(_load_config())
        try:
            items = service.store.list(
                int(limit) if limit else None,
                status=status,
                title=title,
                search=search,
                offset=int(offset),
            )
            return {
                "items": [asdict(item) for item in items],
                "total": service.store.count(status=status, title=title, search=search),
                "titles": service.store.titles(),
            }
        finally:
            service.close()

    return await _run(work)


def _retry_kazumi(config: AppConfig, task, on_event=None) -> dict:
    """Re-resolve a Kazumi episode: re-fetch chapters, re-sniff, download.

    The original stream URL may have expired; re-running the rule gives a fresh
    one. The task key encodes ``kazumi:<rule>:<work-source>:<episode>``.
    """

    parts = task.key.split(":", 3)
    if len(parts) < 4 or not parts[1] or not parts[2]:
        raise AmineError("这条记录缺少规则/作品信息，无法重新获取链接，请在解析页重新提交")
    _, rule_name, work_source, number = parts

    service = KazumiService(config)
    try:
        hit = service.hit_from(rule_name, work_source, name=task.title)
        roads = service.chapters(hit.rule, hit.item)
        if not roads:
            raise AmineError("没有解析到剧集（可能已下架）")
        road_index = None
        for index, road in enumerate(roads):
            if any(
                service._episode_number(ep, position) == number
                for position, ep in enumerate(road.episodes)
            ):
                road_index = index
                break
        if road_index is None:
            raise AmineError(f"作品里没有第 {number} 集（可能已下架）")
        results = service.download(
            task.title or hit.item.name,
            rule_name=rule_name,
            source=work_source,
            hit_name=hit.item.name,
            road_index=road_index,
            episode=number,
            quality=None,
            save_path=task.save_path or None,
            force=True,
            title=task.title or None,
            on_event=on_event,
        )
        ok = [item for item in results if item.ok]
        if not ok:
            reason = results[0].skipped if results else "重新获取链接失败"
            raise AmineError(reason)
        return {
            "ok": True,
            "kind": "kazumi",
            "task_id": ok[0].task_id,
            "output": ok[0].output,
            "source": ok[0].stream_url,
        }
    finally:
        service.close()


def _retry_rss(config: AppConfig, service: DownloadService, task) -> dict:
    """Re-fetch the feed and download the same episode from a fresh link."""

    from .rss import fetch_feed

    def matches(episode) -> bool:
        if episode.dedup_key == task.key:
            return True
        parsed = episode.parsed
        if parsed is None:
            return False
        same_title = (parsed.title or "").strip() == (task.title or "").strip()
        same_episode = (parsed.episode or "") == (task.episode or "")
        same_season = int(parsed.season or 1) == int(task.season or 1)
        return bool(task.episode) and same_title and same_episode and same_season

    for feed in config.rss:
        if not feed.enabled:
            continue
        try:
            episodes = fetch_feed(feed.url)
        except AmineError:
            continue
        for episode in episodes:
            if not episode.torrent_url or not matches(episode):
                continue
            new_task = service.add_torrent(
                episode.torrent_url,
                raw_title=episode.title,
                parsed=episode.parsed,
                save_path=task.save_path or None,
                rename=bool(task.new_name),
                key=task.key,
                title_override=feed.title or None,
                episode_offset=getattr(feed, "episode_offset", None),
                season=getattr(feed, "season", None),
            )
            return {"ok": True, "kind": "rss", "task_id": new_task.torrent_id, "source": episode.torrent_url}
    raise AmineError("没有在任何启用的订阅源里找到该条目的新链接")


async def task_retry(request) -> JSONResponse:
    body = await request.json() if request.method == "POST" else {}
    key = str(body.get("key") or "")

    def work():
        config = _load_config()
        service = DownloadService(config)
        try:
            task = service.store.get(key)
            if task is None:
                raise AmineError("记录不存在")
            if task.key.startswith("kazumi:"):
                return _retry_kazumi(config, task)
            if task.client == "aria2":
                raise AmineError("解析下载的任务缺少规则信息，请在解析页重新提交")
            return _retry_rss(config, service, task)
        finally:
            service.close()

    return await _run(work)


async def rename_all(request) -> JSONResponse:
    def work():
        service = DownloadService(_load_config())
        try:
            return {"renamed": service.rename_all()}
        finally:
            service.close()

    return await _run(work)


def _rss_item(item) -> dict:
    return {
        "title": item.episode.title,
        "group": item.episode.parsed.group if item.episode.parsed else "",
        "name": item.episode.parsed.title if item.episode.parsed else "",
        "episode": item.episode.parsed.episode if item.episode.parsed else "",
        "task_id": item.task.torrent_id if item.task else "",
        "skipped": item.skipped,
    }


async def run_rss(request) -> JSONResponse:
    body = await request.json() if request.method == "POST" else {}

    def work():
        service = DownloadService(_load_config())
        try:
            results = service.run(
                feed_names=body.get("feeds") or None,
                limit=body.get("limit"),
                dry_run=bool(body.get("dry_run")),
                rename=not body.get("no_rename"),
            )
            if not body.get("dry_run") and service.client.rename_requires_complete:
                service.rename_all()
            return [_rss_item(item) for item in results]
        finally:
            service.close()

    return await _run(work)


async def parse_title_endpoint(request) -> JSONResponse:
    body = await request.json() if request.method == "POST" else {}
    title = str(body.get("title") or "")
    template = body.get("template") or None
    parsed = parse_title(title)
    return JSONResponse({"parsed": parsed.to_dict(), "renamed": render(parsed, template)})


# -- kazumi ---------------------------------------------------------------
async def kazumi_rules(request) -> JSONResponse:
    config = _load_config()
    store = RuleStore(kazumi_rules_dir(config))
    return JSONResponse(
        [
            {
                "name": rule.name,
                "version": rule.version,
                "api": rule.api,
                "base_url": rule.base_url,
                "search_mode": rule.search_mode,
                "chapter_mode": rule.chapter_mode,
            }
            for rule in store.list()
        ]
    )


async def kazumi_import(request) -> JSONResponse:
    body = await request.json()
    sources = body.get("sources") or ([body["source"]] if body.get("source") else [])

    def work():
        config = _load_config()
        store = RuleStore(kazumi_rules_dir(config))
        written: list[str] = []
        for source in sources:
            written.extend(str(path) for path in store.import_from(str(source)))
        return {"imported": written}

    return await _run(work)


async def kazumi_search(request) -> JSONResponse:
    keyword = request.query_params.get("q", "")
    rule = request.query_params.get("rule") or None

    def work():
        service = KazumiService(_load_config())
        try:
            if rule:
                rules = [service.require_rule(rule)]
            else:
                rules = service.available_rules()
                if not rules:
                    raise AmineError("规则目录为空，请先导入规则")
            hits, errors = service.search_all(keyword, rules=rules)
            return {
                "hits": [
                    {"rule": hit.rule.name, "name": hit.item.name, "source": hit.item.source}
                    for hit in hits
                ],
                "errors": errors,
            }
        finally:
            service.close()

    return await _run(work)


async def kazumi_chapters(request) -> JSONResponse:
    keyword = request.query_params.get("q", "")
    rule = request.query_params.get("rule") or None
    source = request.query_params.get("source") or None
    name = request.query_params.get("name", "")
    hit_index = int(request.query_params.get("hit", "0"))

    def work():
        service = KazumiService(_load_config())
        try:
            if source:
                hit = service.hit_from(rule, source, name=name)
            else:
                hits = service.search(keyword, rule_name=rule)
                if not hits:
                    raise AmineError("没有搜索结果")
                hit = hits[min(hit_index, len(hits) - 1)]
            roads = service.chapters(hit.rule, hit.item)
            return {
                "name": hit.item.name,
                "roads": [
                    {
                        "name": road.name,
                        "episodes": [
                            {"name": ep.name, "page_url": ep.page_url, "url": ep.url}
                            for ep in road.episodes
                        ],
                    }
                    for road in roads
                ],
            }
        finally:
            service.close()

    return await _run(work)


async def kazumi_download(request) -> JSONResponse:
    body = await request.json()

    def work():
        service = KazumiService(_load_config())
        try:
            results = service.download(
                str(body.get("keyword") or ""),
                rule_name=body.get("rule") or None,
                source=body.get("source") or None,
                hit_name=str(body.get("name") or ""),
                hit_index=int(body.get("hit", 0)),
                road_index=int(body.get("road", 0)),
                episode=body.get("episode") or None,
                limit=body.get("limit"),
                quality=body.get("quality") or None,
                save_path=body.get("save_path") or None,
                dry_run=bool(body.get("dry_run")),
                sniffed_url=body.get("url") or None,
                force=bool(body.get("force")),
            )
            return [asdict(item) for item in results]
        finally:
            service.close()

    return await _run(work)


async def kazumi_run(request) -> JSONResponse:
    body = await request.json() if request.method == "POST" else {}

    def work():
        service = KazumiService(_load_config())
        try:
            results = service.run(
                names=body.get("names") or None,
                dry_run=bool(body.get("dry_run")),
                limit=body.get("limit"),
            )
            if not body.get("dry_run"):
                service.sync_statuses()
            return [asdict(item) for item in results]
        finally:
            service.close()

    return await _run(work)


async def kazumi_sync(request) -> JSONResponse:
    def work():
        service = KazumiService(_load_config())
        try:
            return service.sync_statuses()
        finally:
            service.close()

    return await _run(work)


# -- jobs -----------------------------------------------------------------
def _build_work(kind: str, params: dict):
    if kind == "rss_run":

        def work(emit, cancel):
            service = DownloadService(_load_config())
            try:
                results = service.run(
                    feed_names=params.get("feeds") or None,
                    limit=params.get("limit"),
                    dry_run=bool(params.get("dry_run")),
                    rename=not params.get("no_rename"),
                    on_event=emit,
                    cancel=cancel,
                )
                if not params.get("dry_run") and service.client.rename_requires_complete:
                    service.rename_all()
                return [_rss_item(item) for item in results]
            finally:
                service.close()

        return work

    if kind == "kazumi_run":

        def work(emit, cancel):
            service = KazumiService(_load_config())
            try:
                results = service.run(
                    names=params.get("names") or None,
                    dry_run=bool(params.get("dry_run")),
                    limit=params.get("limit"),
                    on_event=emit,
                    cancel=cancel,
                )
                if not params.get("dry_run"):
                    service.sync_statuses(on_event=emit)
                return [asdict(result) for result in results]
            finally:
                service.close()

        return work

    if kind == "kazumi_download":

        def work(emit, cancel):
            service = KazumiService(_load_config())
            try:
                return [
                    asdict(result)
                    for result in service.download(
                        str(params.get("keyword") or ""),
                        rule_name=params.get("rule") or None,
                        source=params.get("source") or None,
                        hit_name=str(params.get("name") or ""),
                        hit_index=int(params.get("hit", 0)),
                        road_index=int(params.get("road", 0)),
                        episode=params.get("episode") or None,
                        limit=params.get("limit"),
                        quality=params.get("quality") or None,
                        save_path=params.get("save_path") or None,
                        dry_run=bool(params.get("dry_run")),
                        sniffed_url=params.get("url") or None,
                        title=params.get("title") or None,
                        force=bool(params.get("force")),
                        on_event=emit,
                        cancel=cancel,
                    )
                ]
            finally:
                service.close()

        return work

    if kind == "kazumi_sync":

        def work(emit, cancel):
            service = KazumiService(_load_config())
            try:
                emit({"type": "log", "message": "同步下载状态…"})
                return service.sync_statuses(on_event=emit)
            finally:
                service.close()

        return work

    if kind == "set_title":

        def work(emit, cancel):
            title = str(params.get("title") or "")
            apply_all = bool(params.get("apply_all"))
            save_alias = bool(params.get("save_alias"))
            emit({"type": "log", "message": f"设置标题: {title}"})
            service = DownloadService(_load_config())
            try:
                task = service.store.get(str(params.get("key") or "")) if params.get("key") else None
                if task is None and params.get("torrent_id"):
                    task = service.store.get_by_torrent(str(params["torrent_id"]))
                if task is None:
                    raise AmineError("未找到任务")
                old_title = task.title
                if apply_all and old_title:
                    service.store.set_title_all(old_title, title)
                else:
                    service.store.set_title(task.key, title)
                if save_alias and old_title:
                    _save_title_alias(old_title, title)
                task.title = title
                renamed = service.rename_task(task)
                return {"renamed": renamed, "title": title, "apply_all": apply_all}
            finally:
                service.close()

        return work

    if kind == "rename":

        def work(emit, cancel):
            emit({"type": "log", "message": "重命名已完成任务…"})
            service = DownloadService(_load_config())
            try:
                return {"renamed": service.rename_all()}
            finally:
                service.close()

        return work

    raise AmineError(f"未知任务类型: {kind}")


#: 预览（dry-run）使用独立的 kind，避免和正式任务互相被同类互斥挡住。
_PREVIEW_KINDS = {
    "rss_run": "rss_preview",
    "kazumi_run": "kazumi_preview",
    "kazumi_download": "kazumi_download_preview",
}


async def jobs_endpoint(request):
    if request.method == "GET":
        return JSONResponse([job.summary() for job in manager.list()])
    body = await request.json()
    kind = str(body.get("kind") or "")
    params = body.get("params") or {}
    try:
        work = _build_work(kind, params)
    except AmineError as exc:
        return _error(str(exc))
    job_kind = _PREVIEW_KINDS.get(kind, kind) if params.get("dry_run") else kind
    job = manager.submit(job_kind, params, work)
    return JSONResponse(job.summary())


async def job_detail(request):
    job = manager.get(request.path_params["id"])
    if job is None:
        return _error("任务不存在", 404)
    data = job.summary()
    data["events"] = job.events
    return JSONResponse(data)


async def job_cancel(request):
    job = manager.get(request.path_params["id"])
    if job is None:
        return _error("任务不存在", 404)
    job.cancel.set()
    return JSONResponse({"ok": True})


async def job_events(request):
    job = manager.get(request.path_params["id"])
    if job is None:
        return _error("任务不存在", 404)

    async def stream():
        sent = 0
        while True:
            while sent < len(job.events):
                event = job.events[sent]
                sent += 1
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") == "end":
                    return
            if job.status in ("done", "error", "cancelled", "interrupted") and sent >= len(
                job.events
            ):
                return
            await asyncio.sleep(0.3)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# -- config ---------------------------------------------------------------
_SECRET_KEYS = {"secret", "api_key", "password", "web_password"}
_CONFIG_SECTIONS = ("app", "aria2", "qbittorrent", "library", "kazumi")

#: 表单留空时使用的默认值（也是设置页里 placeholder 的建议值）。
#: 统一从 :class:`AppConfig` 取，避免和代码里的默认行为不一致。
_APP_DEFAULTS = AppConfig()
CONFIG_DEFAULTS: dict[str, dict] = {
    "app": {
        "downloader": _APP_DEFAULTS.downloader,
        "rename": _APP_DEFAULTS.rename,
        "rename_template": _APP_DEFAULTS.rename_template,
        "save_path": _APP_DEFAULTS.save_path,
        "category": _APP_DEFAULTS.category,
        "episode_offset": _APP_DEFAULTS.episode_offset,
        "resolution_preference": _APP_DEFAULTS.resolution_preference,
        "interval": _APP_DEFAULTS.interval,
    },
    "aria2": {
        "rpc_url": "http://127.0.0.1:6800/jsonrpc",
        "download_dir": "",
        "local_dir": "",
    },
    "qbittorrent": {
        "url": "http://127.0.0.1:8080",
        "username": "admin",
    },
    "library": {
        "enabled": False,
        "root": "",
        "local_root": "",
        "series_template": "{title}",
        "season_template": "Season {season}",
    },
    "kazumi": {
        "rename_template": "{title} S{season}E{episode}",
        "preferred_quality": "1080p",
        "media_mode": "auto",
        "auto_install_browser": True,
        "headless": True,
    },
}


def _config_file() -> Path:
    return Path(_CONFIG_PATH) if _CONFIG_PATH else default_config_path()


def _read_raw() -> dict:
    path = _config_file()
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _read_doc():
    """Parse the config with tomlkit so comments / formatting are preserved."""

    path = _config_file()
    if path.exists():
        try:
            return tomlkit.parse(path.read_text(encoding="utf-8"))
        except (OSError, tomlkit.exceptions.ParseError):
            pass
    return tomlkit.document()


def _backup_config(path: Path) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    Path(str(path) + ".bak").write_text(text, encoding="utf-8")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    Path(f"{path}.{stamp}.bak").write_text(text, encoding="utf-8")
    backups = sorted(path.parent.glob(path.name + ".*.bak"))
    for old in backups[:-5]:
        try:
            old.unlink()
        except OSError:
            pass


def _write_doc(doc) -> Path:
    path = _config_file()
    ensure_dir(path.parent)
    _backup_config(path)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return path


def _merge_into_doc(doc, body: dict) -> None:
    for section in _CONFIG_SECTIONS:
        incoming = body.get(section)
        if not isinstance(incoming, dict):
            continue
        if section not in doc or not isinstance(doc.get(section), dict):
            doc[section] = tomlkit.table()
        target = doc[section]
        defaults = CONFIG_DEFAULTS.get(section, {})
        for key, value in incoming.items():
            if key in _SECRET_KEYS:
                if value in ("", "******"):
                    continue  # 留空 = 保持原值
                target[key] = value
                continue
            if value is None or value == "":
                # 留空 = 用默认值；没有默认值就保持原值
                if key in defaults:
                    target[key] = defaults[key]
                continue
            target[key] = value


def _merge_raw(raw: dict, body: dict) -> dict:
    merged = dict(raw)
    for section in _CONFIG_SECTIONS:
        incoming = body.get(section)
        if not isinstance(incoming, dict):
            continue
        target = dict(merged.get(section) or {})
        defaults = CONFIG_DEFAULTS.get(section, {})
        for key, value in incoming.items():
            if key in _SECRET_KEYS:
                if value in ("", "******"):
                    continue
                target[key] = value
                continue
            if value is None or value == "":
                if key in defaults:
                    target[key] = defaults[key]
                continue
            target[key] = value
        merged[section] = target
    return merged


def _feed_table(feed: RssFeed):
    table = tomlkit.table()
    table["name"] = feed.name
    table["url"] = feed.url
    table["enabled"] = feed.enabled
    if feed.title:
        table["title"] = feed.title
    if feed.names:
        table["names"] = list(feed.names)
    if feed.exclude_names:
        table["exclude_names"] = list(feed.exclude_names)
    table["one_per_episode"] = feed.one_per_episode
    table["initial"] = feed.initial
    if feed.season is not None:
        table["season"] = feed.season
    if feed.episode_offset is not None:
        table["episode_offset"] = feed.episode_offset
    return table


def _subscription_table(sub: KazumiSubscription):
    table = tomlkit.table()
    table["name"] = sub.name
    if sub.rule:
        table["rule"] = sub.rule
    if sub.source:
        table["source"] = sub.source
    if sub.hit:
        table["hit"] = sub.hit
    if sub.road:
        table["road"] = sub.road
    if sub.quality:
        table["quality"] = sub.quality
    if sub.save_path:
        table["save_path"] = sub.save_path
    table["enabled"] = sub.enabled
    if sub.title:
        table["title"] = sub.title
    table["initial"] = sub.initial
    if sub.season is not None:
        table["season"] = sub.season
    if sub.episode_offset is not None:
        table["episode_offset"] = sub.episode_offset
    return table


def _write_feeds(feeds: list[RssFeed]) -> Path:
    doc = _read_doc()
    aot = tomlkit.aot()
    for feed in feeds:
        aot.append(_feed_table(feed))
    doc["rss"] = aot
    return _write_doc(doc)


def _write_subscriptions(subs: list[KazumiSubscription]) -> Path:
    doc = _read_doc()
    if "kazumi" not in doc or not isinstance(doc.get("kazumi"), dict):
        doc["kazumi"] = tomlkit.table()
    aot = tomlkit.aot()
    for sub in subs:
        aot.append(_subscription_table(sub))
    doc["kazumi"]["subscribe"] = aot
    return _write_doc(doc)


def _save_title_alias(old_title: str, new_title: str) -> None:
    if not old_title or not new_title:
        return
    doc = _read_doc()
    titles = doc.get("titles")
    if not isinstance(titles, dict):
        titles = tomlkit.table()
        doc["titles"] = titles
    titles[old_title] = new_title
    _write_doc(doc)


async def get_config(request) -> JSONResponse:
    config = _load_config()
    raw = _read_raw()

    def section(name: str) -> dict:
        data = dict(CONFIG_DEFAULTS.get(name, {}))
        data.update(raw.get(name) or {})
        return data

    app_section = section("app")
    app_section["web_password_set"] = bool((raw.get("app") or {}).get("web_password"))
    app_section.pop("web_password", None)
    aria2 = section("aria2")
    aria2["secret_set"] = bool((raw.get("aria2") or {}).get("secret"))
    aria2.pop("secret", None)
    qb = section("qbittorrent")
    qb["api_key_set"] = bool((raw.get("qbittorrent") or {}).get("api_key"))
    qb["password_set"] = bool((raw.get("qbittorrent") or {}).get("password"))
    qb.pop("api_key", None)
    qb.pop("password", None)
    return JSONResponse(
        {
            "config_path": str(config.path) if config.path else "",
            "app": app_section,
            "aria2": aria2,
            "qbittorrent": qb,
            "library": section("library"),
            "kazumi": section("kazumi"),
        }
    )


async def put_config(request) -> JSONResponse:
    body = await request.json()

    def work():
        doc = _read_doc()
        _merge_into_doc(doc, body)
        path = _write_doc(doc)
        return {"ok": True, "config_path": str(path)}

    return await _run(work)


async def test_config(request) -> JSONResponse:
    body = await request.json() if request.method == "POST" else {}

    def work():
        raw = _merge_raw(_read_raw(), body)
        handle = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8")
        try:
            handle.write(tomli_w.dumps(raw))
            handle.close()
            config = AppConfig.load(handle.name)
            client = create_downloader(config)
            try:
                return {
                    "ok": True,
                    "downloader": client.name,
                    "connection": client.test_connection(),
                }
            finally:
                client.close()
        finally:
            Path(handle.name).unlink(missing_ok=True)

    return await _run(work)


async def config_endpoint(request) -> JSONResponse:
    if request.method == "GET":
        return await get_config(request)
    return await put_config(request)


# -- subscription management ----------------------------------------------
def _feed_from_body(body: dict) -> RssFeed:
    url = str(body.get("url") or "").strip()
    if not url:
        raise AmineError("订阅 URL 不能为空")
    season = body.get("season")
    offset = body.get("episode_offset")
    return RssFeed(
        name=str(body.get("name") or url),
        url=url,
        enabled=bool(body.get("enabled", True)),
        title=str(body.get("title") or ""),
        names=[str(item) for item in body.get("names") or []],
        exclude_names=[str(item) for item in body.get("exclude_names") or []],
        one_per_episode=bool(body.get("one_per_episode", True)),
        initial=str(body.get("initial") or "latest"),
        season=int(season) if season not in (None, "") else None,
        episode_offset=int(offset) if offset not in (None, "") else None,
    )


def _subscription_from_body(body: dict) -> KazumiSubscription:
    name = str(body.get("name") or "").strip()
    if not name:
        raise AmineError("订阅名称不能为空")
    season = body.get("season")
    offset = body.get("episode_offset")
    return KazumiSubscription(
        name=name,
        rule=str(body.get("rule") or ""),
        source=str(body.get("source") or ""),
        hit=int(body.get("hit") or 0),
        road=int(body.get("road") or 0),
        quality=str(body.get("quality") or ""),
        save_path=str(body.get("save_path") or ""),
        enabled=bool(body.get("enabled", True)),
        title=str(body.get("title") or ""),
        initial=str(body.get("initial") or "latest"),
        season=int(season) if season not in (None, "") else None,
        episode_offset=int(offset) if offset not in (None, "") else None,
    )


async def rss_manage(request) -> JSONResponse:
    config = _load_config()
    feeds = list(config.rss)
    if request.method == "GET":
        return JSONResponse([asdict(feed) for feed in feeds])
    body = await request.json() if request.method in ("POST", "PUT") else {}
    index_param = request.path_params.get("index")

    def work():
        if request.method == "DELETE":
            index = int(index_param)
            if index < 0 or index >= len(feeds):
                raise AmineError("订阅不存在")
            feeds.pop(index)
            _write_feeds(feeds)
            return {"ok": True}
        if request.method == "PUT":
            index = int(index_param)
            if index < 0 or index >= len(feeds):
                raise AmineError("订阅不存在")
            feeds[index] = _feed_from_body(body)
            _write_feeds(feeds)
            return {"ok": True, "feed": asdict(feeds[index])}
        feeds.append(_feed_from_body(body))
        _write_feeds(feeds)
        return {"ok": True}

    return await _run(work)


async def kazumi_subscriptions_manage(request) -> JSONResponse:
    config = _load_config()
    subs = list(config.kazumi_subscriptions)
    if request.method == "GET":
        return JSONResponse([asdict(sub) for sub in subs])
    body = await request.json() if request.method in ("POST", "PUT") else {}
    index_param = request.path_params.get("index")

    def work():
        if request.method == "DELETE":
            index = int(index_param)
            if index < 0 or index >= len(subs):
                raise AmineError("订阅不存在")
            subs.pop(index)
            _write_subscriptions(subs)
            return {"ok": True}
        if request.method == "PUT":
            index = int(index_param)
            if index < 0 or index >= len(subs):
                raise AmineError("订阅不存在")
            subs[index] = _subscription_from_body(body)
            _write_subscriptions(subs)
            return {"ok": True, "subscription": asdict(subs[index])}
        subs.append(_subscription_from_body(body))
        _write_subscriptions(subs)
        return {"ok": True}

    return await _run(work)


async def rss_preview(request) -> JSONResponse:
    url = request.query_params.get("url") or ""
    name = request.query_params.get("name") or ""
    if not url and name:
        config = _load_config()
        for feed in config.rss:
            if feed.name.lower() == name.lower():
                url = feed.url
                break
    if not url:
        return _error("缺少订阅 URL")

    def work():
        from .rss import fetch_feed

        episodes = fetch_feed(url)
        rows = []
        for episode in episodes:
            parsed = episode.parsed
            rows.append(
                {
                    "title": episode.title,
                    "group": parsed.group if parsed else "",
                    "name": parsed.title if parsed else "",
                    "season": parsed.season if parsed else 1,
                    "episode": parsed.episode if parsed else "",
                    "resolution": parsed.resolution if parsed else "",
                    "torrent_url": episode.torrent_url,
                }
            )
        return {"url": url, "total": len(rows), "items": rows}

    return await _run(work)


# -- static ---------------------------------------------------------------
def _safe_dist_file(path: str) -> Path | None:
    """Resolve *path* inside the frontend dist directory, or ``None``.

    Guards against path traversal such as ``/a/..%2f..%2fsecret.txt``.
    """

    if not path:
        return None
    root = _DIST.resolve()
    try:
        candidate = (root / path).resolve()
    except (OSError, ValueError):
        return None
    try:
        if not candidate.is_relative_to(root):
            return None
    except ValueError:  # pragma: no cover - Python < 3.9 fallback
        return None
    return candidate if candidate.is_file() else None


async def spa(request):
    path = request.path_params.get("path", "")
    if _DIST.exists():
        candidate = _safe_dist_file(path)
        if candidate is not None:
            return FileResponse(candidate)
        index = _DIST / "index.html"
        if index.is_file():
            return FileResponse(index)
    return JSONResponse(
        {"error": "前端尚未构建：请在 web/ 目录运行 `bun run build`"}, status_code=404
    )


routes = [
    Route("/api/status", status),
    Route("/api/torrents", torrents),
    Route("/api/torrents/{tid}/{action}", torrent_action, methods=["POST"]),
    Route("/api/tasks", tasks, methods=["GET", "DELETE"]),
    Route("/api/tasks/retry", task_retry, methods=["POST"]),
    Route("/api/rename", rename_all, methods=["POST"]),
    Route("/api/rss", rss_manage, methods=["GET", "POST"]),
    Route("/api/rss/preview", rss_preview),
    Route("/api/rss/{index}", rss_manage, methods=["PUT", "DELETE"]),
    Route("/api/run", run_rss, methods=["GET", "POST"]),
    Route("/api/parse", parse_title_endpoint, methods=["GET", "POST"]),
    Route("/api/kazumi/rules", kazumi_rules),
    Route("/api/kazumi/import", kazumi_import, methods=["POST"]),
    Route("/api/kazumi/search", kazumi_search),
    Route("/api/kazumi/chapters", kazumi_chapters),
    Route("/api/kazumi/download", kazumi_download, methods=["POST"]),
    Route("/api/kazumi/subscriptions", kazumi_subscriptions_manage, methods=["GET", "POST"]),
    Route(
        "/api/kazumi/subscriptions/{index}",
        kazumi_subscriptions_manage,
        methods=["PUT", "DELETE"],
    ),
    Route("/api/kazumi/run", kazumi_run, methods=["GET", "POST"]),
    Route("/api/kazumi/sync", kazumi_sync, methods=["POST"]),
    Route("/api/jobs", jobs_endpoint, methods=["GET", "POST"]),
    Route("/api/jobs/{id}/events", job_events),
    Route("/api/jobs/{id}/cancel", job_cancel, methods=["POST"]),
    Route("/api/jobs/{id}", job_detail),
    Route("/api/config", config_endpoint, methods=["GET", "PUT"]),
    Route("/api/config/test", test_config, methods=["POST"]),
]


# -- auth / scheduler ------------------------------------------------------
class BasicAuthMiddleware:
    """Optional HTTP Basic Auth, enabled by ``[app] web_password``."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        password = ""
        try:
            password = _load_config().web_password or ""
        except Exception:  # noqa: BLE001 - never block on a bad config
            password = ""
        if not password:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        supplied = ""
        raw = headers.get(b"authorization", b"").decode("latin-1")
        if raw.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(raw.split(" ", 1)[1]).decode("utf-8")
                supplied = decoded.partition(":")[2]
            except Exception:  # noqa: BLE001
                supplied = ""
        if secrets.compare_digest(supplied, password):
            await self.app(scope, receive, send)
            return
        response = PlainTextResponse(
            "需要登录",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="amine-downloader"'},
        )
        await response(scope, receive, send)


if _DIST.exists():
    routes.append(Mount("/assets", app=StaticFiles(directory=_DIST / "assets")))
routes.append(Route("/{path:path}", spa))

app = Starlette(routes=routes)
app = BasicAuthMiddleware(app)

_scheduler_stop = threading.Event()
_scheduler_started = False


def _run_scheduled_jobs() -> None:
    """Submit the periodic RSS / Kazumi / rename jobs (mutex dedupes them)."""

    config = _load_config()
    if config.rss:
        manager.submit("rss_run", {}, _build_work("rss_run", {}))
    if config.kazumi_subscriptions:
        manager.submit("kazumi_run", {}, _build_work("kazumi_run", {}))
    manager.submit("rename", {}, _build_work("rename", {}))


def _scheduler_loop(interval_seconds: float) -> None:
    while not _scheduler_stop.wait(interval_seconds):
        try:
            _run_scheduled_jobs()
        except Exception:  # noqa: BLE001 - the scheduler must never die
            pass


def start_scheduler(interval_minutes: int | float) -> bool:
    """Start the background scheduler if ``interval_minutes`` > 0."""

    global _scheduler_started
    try:
        interval = float(interval_minutes or 0)
    except (TypeError, ValueError):
        interval = 0.0
    if interval <= 0 or _scheduler_started:
        return False
    _scheduler_started = True
    threading.Thread(
        target=_scheduler_loop, args=(interval * 60.0,), name="amine-scheduler", daemon=True
    ).start()
    return True


def serve(host: str = "127.0.0.1", port: int = 8420, config_path: str | None = None) -> None:
    import uvicorn

    configure(config_path)
    try:
        ensure_dir(_config_file().parent)
    except AmineError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if not manager.enable_persistence(default_data_path().with_name("jobs.db")):
        print(
            "提示: 任务持久化不可用（数据目录不可写），任务记录仅保存在内存中。",
            file=sys.stderr,
        )
    start_scheduler(_load_config().interval)
    uvicorn.run(app, host=host, port=port, log_level="info")
