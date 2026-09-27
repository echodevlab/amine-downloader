"""Small JSON API + static file server for the web UI."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .config import AppConfig, kazumi_rules_dir
from .downloaders.base import DownloadError
from .errors import AmineError
from .jobs import manager
from .kazumi import KazumiService, RuleStore
from .parser import parse_title
from .renamer import render
from .service import DownloadService

_CONFIG_PATH: str | None = None
_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


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
            else:
                raise AmineError(f"未知操作: {action}")
        finally:
            service.close()
        return {"ok": True}

    return await _run(work)


async def tasks(request) -> JSONResponse:
    def work():
        service = DownloadService(_load_config())
        try:
            return [asdict(item) for item in service.store.list()]
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


async def rss_feeds(request) -> JSONResponse:
    config = _load_config()
    return JSONResponse([asdict(feed) for feed in config.rss])


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
            return [
                {"rule": hit.rule.name, "name": hit.item.name, "source": hit.item.source}
                for hit in service.search(keyword, rule_name=rule)
            ]
        finally:
            service.close()

    return await _run(work)


async def kazumi_chapters(request) -> JSONResponse:
    keyword = request.query_params.get("q", "")
    rule = request.query_params.get("rule") or None
    hit_index = int(request.query_params.get("hit", "0"))

    def work():
        service = KazumiService(_load_config())
        try:
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
                hit_index=int(body.get("hit", 0)),
                road_index=int(body.get("road", 0)),
                episode=body.get("episode") or None,
                limit=body.get("limit"),
                quality=body.get("quality") or None,
                save_path=body.get("save_path") or None,
                dry_run=bool(body.get("dry_run")),
                sniffed_url=body.get("url") or None,
            )
            return [asdict(item) for item in results]
        finally:
            service.close()

    return await _run(work)


async def kazumi_subscriptions(request) -> JSONResponse:
    config = _load_config()
    return JSONResponse([asdict(sub) for sub in config.kazumi_subscriptions])


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
            return [asdict(item) for item in results]
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
                return [
                    asdict(result)
                    for result in service.run(
                        names=params.get("names") or None,
                        dry_run=bool(params.get("dry_run")),
                        limit=params.get("limit"),
                        on_event=emit,
                    )
                ]
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
                        hit_index=int(params.get("hit", 0)),
                        road_index=int(params.get("road", 0)),
                        episode=params.get("episode") or None,
                        limit=params.get("limit"),
                        quality=params.get("quality") or None,
                        save_path=params.get("save_path") or None,
                        dry_run=bool(params.get("dry_run")),
                        sniffed_url=params.get("url") or None,
                        on_event=emit,
                    )
                ]
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
    job = manager.submit(kind, params, work)
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
            if job.status in ("done", "error", "cancelled") and sent >= len(job.events):
                return
            await asyncio.sleep(0.3)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# -- static ---------------------------------------------------------------
async def spa(request):
    path = request.path_params.get("path", "")
    if _DIST.exists():
        candidate = _DIST / path
        if path and candidate.is_file():
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
    Route("/api/tasks", tasks),
    Route("/api/rename", rename_all, methods=["POST"]),
    Route("/api/rss", rss_feeds),
    Route("/api/run", run_rss, methods=["GET", "POST"]),
    Route("/api/parse", parse_title_endpoint, methods=["GET", "POST"]),
    Route("/api/kazumi/rules", kazumi_rules),
    Route("/api/kazumi/import", kazumi_import, methods=["POST"]),
    Route("/api/kazumi/search", kazumi_search),
    Route("/api/kazumi/chapters", kazumi_chapters),
    Route("/api/kazumi/download", kazumi_download, methods=["POST"]),
    Route("/api/kazumi/subscriptions", kazumi_subscriptions),
    Route("/api/kazumi/run", kazumi_run, methods=["GET", "POST"]),
    Route("/api/jobs", jobs_endpoint, methods=["GET", "POST"]),
    Route("/api/jobs/{id}/events", job_events),
    Route("/api/jobs/{id}/cancel", job_cancel, methods=["POST"]),
    Route("/api/jobs/{id}", job_detail),
]

if _DIST.exists():
    routes.append(Mount("/assets", app=StaticFiles(directory=_DIST / "assets")))
routes.append(Route("/{path:path}", spa))

app = Starlette(routes=routes)


def serve(host: str = "127.0.0.1", port: int = 8420, config_path: str | None = None) -> None:
    import uvicorn

    configure(config_path)
    uvicorn.run(app, host=host, port=port, log_level="info")
