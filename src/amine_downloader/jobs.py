"""Background job manager for long-running operations.

A job runs a callable in a worker thread and records events. The web layer
streams those events over SSE (by replaying the event list), so no cross-thread
queue or event loop plumbing is needed.

Two safeguards on top of that:

* only one job of a given ``kind`` may run at a time, so double-clicking
  "下载新剧集" (or a scheduled run overlapping a manual one) cannot add the
  same torrent twice;
* job summaries are persisted to SQLite, so the task page still shows past
  runs after the service restarts.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import AmineError

EventFn = Callable[[dict], None]
WorkFn = Callable[[EventFn, "threading.Event"], Any]

_ACTIVE = ("pending", "running")
_PERSIST_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    kind        TEXT,
    params      TEXT,
    status      TEXT,
    created_at  REAL,
    started_at  REAL,
    finished_at REAL,
    error       TEXT,
    result      TEXT,
    event_count INTEGER DEFAULT 0
);
"""


@dataclass
class Job:
    id: str
    kind: str
    params: dict
    status: str = "pending"  # pending | running | done | error | cancelled | interrupted
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    error: str = ""
    result: Any = None
    events: list[dict] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)

    def summary(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "params": self.params,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "result": self.result,
            "event_count": len(self.events),
        }


class JobManager:
    def __init__(self, persist_path: str | Path | None = None) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._submit_lock = threading.Lock()
        self._persist_path: Path | None = None
        self._conn: sqlite3.Connection | None = None
        if persist_path is not None:
            self.enable_persistence(persist_path)

    # -- persistence ------------------------------------------------------
    def enable_persistence(self, path: str | Path) -> bool:
        """Persist job summaries to SQLite. Returns whether it succeeded.

        Persistence is best-effort: an unwritable config directory must not
        stop the server from starting.
        """

        if self._conn is not None:
            return True
        target = Path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(target), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute(_PERSIST_SCHEMA)
            self._conn.commit()
        except (OSError, sqlite3.Error):
            self._conn = None
            return False
        self._persist_path = target
        self._load()
        return True

    def _load(self) -> None:
        if self._conn is None:
            return
        rows = list(
            self._conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 200")
        )
        for row in rows:
            job = Job(
                id=row["id"],
                kind=row["kind"],
                params=_loads(row["params"], {}),
                status=row["status"],
                created_at=row["created_at"] or 0.0,
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                error=row["error"] or "",
                result=_loads(row["result"], None),
            )
            if job.status in _ACTIVE:
                job.status = "interrupted"
                job.error = job.error or "服务重启，任务已中断"
            self._jobs.setdefault(job.id, job)

    def _save(self, job: Job) -> None:
        if self._conn is None:
            return
        try:
            self._conn.execute(
                """
                INSERT INTO jobs (id, kind, params, status, created_at, started_at,
                                  finished_at, error, result, event_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    started_at = excluded.started_at,
                    finished_at = excluded.finished_at,
                    error = excluded.error,
                    result = excluded.result,
                    event_count = excluded.event_count
                """,
                (
                    job.id,
                    job.kind,
                    json.dumps(job.params, ensure_ascii=False, default=str),
                    job.status,
                    job.created_at,
                    job.started_at,
                    job.finished_at,
                    job.error,
                    json.dumps(job.result, ensure_ascii=False, default=str),
                    len(job.events),
                ),
            )
            self._conn.commit()
        except sqlite3.Error:
            pass

    # -- job access -------------------------------------------------------
    def create(self, kind: str, params: dict) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, params=params or {})
        with self._lock:
            self._jobs[job.id] = job
        self._save(job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def find_active(self, kind: str) -> Job | None:
        with self._lock:
            for job in self._jobs.values():
                if job.kind == kind and job.status in _ACTIVE:
                    return job
        return None

    def list(self, limit: int = 50) -> list[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda item: item.created_at, reverse=True)
        return jobs[:limit]

    def emit(self, job: Job, event: dict) -> None:
        job.events.append({**event, "t": time.time()})

    def submit(self, kind: str, params: dict, work: WorkFn) -> Job:
        # 同一 kind 同时只允许运行一个：已有在跑的直接返回它。
        with self._submit_lock:
            existing = self.find_active(kind)
            if existing is not None:
                return existing
            job = self.create(kind, params)

        def runner() -> None:
            job.status = "running"
            job.started_at = time.time()
            self._save(job)
            try:
                job.result = work(lambda event: self.emit(job, event), job.cancel)
                job.status = "cancelled" if job.cancel.is_set() else "done"
            except AmineError as exc:
                job.error = str(exc)
                job.status = "error"
            except Exception as exc:  # noqa: BLE001
                job.error = f"{type(exc).__name__}: {exc}"
                job.status = "error"
            finally:
                job.finished_at = time.time()
                self.emit(
                    job,
                    {
                        "type": "end",
                        "status": job.status,
                        "error": job.error,
                        "result": job.result,
                    },
                )
                self._save(job)

        threading.Thread(target=runner, name=f"job-{job.id}", daemon=True).start()
        return job


def _loads(value, default):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


manager = JobManager()
