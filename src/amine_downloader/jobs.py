"""Background job manager for long-running operations.

A job runs a callable in a worker thread and records events. The web layer
streams those events over SSE (by replaying the event list), so no cross-thread
queue or event loop plumbing is needed.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .errors import AmineError

EventFn = Callable[[dict], None]
WorkFn = Callable[[EventFn, "threading.Event"], Any]


@dataclass
class Job:
    id: str
    kind: str
    params: dict
    status: str = "pending"  # pending | running | done | error | cancelled
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
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, kind: str, params: dict) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, params=params or {})
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, limit: int = 50) -> list[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda item: item.created_at, reverse=True)
        return jobs[:limit]

    def emit(self, job: Job, event: dict) -> None:
        job.events.append({**event, "t": time.time()})

    def submit(self, kind: str, params: dict, work: WorkFn) -> Job:
        job = self.create(kind, params)

        def runner() -> None:
            job.status = "running"
            job.started_at = time.time()
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

        threading.Thread(target=runner, name=f"job-{job.id}", daemon=True).start()
        return job


manager = JobManager()
