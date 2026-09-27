"""Small SQLite store used to remember what has already been downloaded."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .models import DownloadTask

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    key        TEXT PRIMARY KEY,
    raw_title  TEXT NOT NULL,
    client     TEXT DEFAULT '',
    torrent_id TEXT DEFAULT '',
    grp        TEXT DEFAULT '',
    title      TEXT DEFAULT '',
    season     INTEGER DEFAULT 1,
    episode    TEXT DEFAULT '',
    resolution TEXT DEFAULT '',
    new_name   TEXT DEFAULT '',
    source     TEXT DEFAULT '',
    save_path  TEXT DEFAULT '',
    category   TEXT DEFAULT '',
    status     TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT '',
    updated_at TEXT DEFAULT ''
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def has(self, key: str) -> bool:
        cursor = self._conn.execute("SELECT 1 FROM tasks WHERE key = ?", (key,))
        return cursor.fetchone() is not None

    def upsert(self, task: DownloadTask) -> None:
        now = _now()
        created = task.created_at or now
        self._conn.execute(
            """
            INSERT INTO tasks (key, raw_title, client, torrent_id, grp, title, season,
                               episode, resolution, new_name, source, save_path, category,
                               status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                client = excluded.client,
                torrent_id = excluded.torrent_id,
                new_name = excluded.new_name,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                task.key,
                task.raw_title,
                task.client,
                task.torrent_id,
                task.group,
                task.title,
                task.season,
                task.episode,
                task.resolution,
                task.new_name,
                task.source,
                task.save_path,
                task.category,
                task.status,
                created,
                now,
            ),
        )
        self._conn.commit()

    def set_status(self, key: str, status: str, torrent_id: str | None = None) -> None:
        if torrent_id is None:
            self._conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE key = ?",
                (status, _now(), key),
            )
        else:
            self._conn.execute(
                "UPDATE tasks SET status = ?, torrent_id = ?, updated_at = ? WHERE key = ?",
                (status, torrent_id, _now(), key),
            )
        self._conn.commit()

    def get(self, key: str) -> DownloadTask | None:
        cursor = self._conn.execute("SELECT * FROM tasks WHERE key = ?", (key,))
        row = cursor.fetchone()
        return self._row_to_task(row) if row else None

    def list(self, limit: int | None = None) -> list[DownloadTask]:
        query = "SELECT * FROM tasks ORDER BY created_at DESC"
        if limit:
            query += f" LIMIT {int(limit)}"
        return [self._row_to_task(row) for row in self._conn.execute(query)]

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> DownloadTask:
        return DownloadTask(
            key=row["key"],
            raw_title=row["raw_title"],
            client=row["client"],
            torrent_id=row["torrent_id"],
            group=row["grp"],
            title=row["title"],
            season=row["season"],
            episode=row["episode"],
            resolution=row["resolution"],
            new_name=row["new_name"],
            source=row["source"],
            save_path=row["save_path"],
            category=row["category"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def close(self) -> None:
        self._conn.close()
