"""Small JSON API + static file server for the web UI."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import tomllib
from dataclasses import asdict
from pathlib import Path

import tomli_w
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .config import AppConfig, default_config_path, kazumi_rules_dir
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
                        title=params.get("title") or None,
                        on_event=emit,
                    )
                ]
            finally:
                service.close()

        return work

    if kind == "set_title":

        def work(emit, cancel):
            title = str(params.get("title") or "")
            emit({"type": "log", "message": f"设置标题: {title}"})
            service = DownloadService(_load_config())
            try:
                task = service.store.get(str(params.get("key") or "")) if params.get("key") else None
                if task is None and params.get("torrent_id"):
                    task = next(
                        (item for item in service.store.list() if item.torrent_id == params["torrent_id"]),
                        None,
                    )
                if task is None:
                    raise AmineError("未找到任务")
                service.store.set_title(task.key, title)
                task.title = title
                renamed = service.rename_task(task)
                return {"renamed": renamed, "title": title}
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


# -- config ---------------------------------------------------------------
_SECRET_KEYS = {"secret", "api_key", "password"}
_CONFIG_SECTIONS = ("app", "aria2", "qbittorrent", "library", "kazumi")

#: 表单留空时使用的默认值（也是设置页里 placeholder 的建议值）。
CONFIG_DEFAULTS: dict[str, dict] = {
    "app": {
        "downloader": "aria2",
        "rename": True,
        "rename_template": DEFAULT_TEMPLATE,
        "save_path": "",
        "category": "amine",
        "episode_offset": 0,
    },
    "aria2": {
        "rpc_url": "http://aria2-next:6800/jsonrpc",
        "download_dir": "/wenwen/media/acg",
        "local_dir": "/wenwen/media/acg",
    },
    "qbittorrent": {
        "url": "http://qbittorrent:8085",
        "username": "admin",
    },
    "library": {
        "enabled": True,
        "root": "/wenwen/media/acg",
        "local_root": "/wenwen/media/acg",
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
                    continue  # 留空 = 保持原值
                target[key] = value
                continue
            if value is None or value == "":
                # 留空 = 用默认值；没有默认值就保持原值
                if key in defaults:
                    target[key] = defaults[key]
                continue
            target[key] = value
        merged[section] = target
    return merged


async def get_config(request) -> JSONResponse:
    config = _load_config()
    raw = _read_raw()

    def section(name: str) -> dict:
        data = dict(CONFIG_DEFAULTS.get(name, {}))
        data.update(raw.get(name) or {})
        return data

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
            "app": section("app"),
            "aria2": aria2,
            "qbittorrent": qb,
            "library": section("library"),
            "kazumi": section("kazumi"),
        }
    )


async def put_config(request) -> JSONResponse:
    body = await request.json()

    def work():
        path = _config_file()
        merged = _merge_raw(_read_raw(), body)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.with_suffix(path.suffix + ".bak").write_text(
                path.read_text(encoding="utf-8"), encoding="utf-8"
            )
        path.write_text(tomli_w.dumps(merged), encoding="utf-8")
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
    Route("/api/config", config_endpoint, methods=["GET", "PUT"]),
    Route("/api/config/test", test_config, methods=["POST"]),
]

if _DIST.exists():
    routes.append(Mount("/assets", app=StaticFiles(directory=_DIST / "assets")))
routes.append(Route("/{path:path}", spa))

app = Starlette(routes=routes)


def serve(host: str = "127.0.0.1", port: int = 8420, config_path: str | None = None) -> None:
    import uvicorn

    configure(config_path)
    uvicorn.run(app, host=host, port=port, log_level="info")
