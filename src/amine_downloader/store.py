"""Small SQLite store used to remember what has already been downloaded."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .config import ensure_dir
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

CREATE TABLE IF NOT EXISTS meta (
    name  TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        ensure_dir(self.path.parent)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def has(self, key: str) -> bool:
        cursor = self._conn.execute("SELECT 1 FROM tasks WHERE key = ?", (key,))
        return cursor.fetchone() is not None

    #: 这些状态不算「已下载」，下次运行应重试。
    RETRYABLE_STATUSES = ("failed", "removed")

    def has_done(self, key: str) -> bool:
        """Whether *key* has been handled and should be skipped as a duplicate.

        ``failed`` / ``removed`` records are intentionally ignored so a later
        run can retry them.
        """

        cursor = self._conn.execute(
            "SELECT status FROM tasks WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        if row is None:
            return False
        return str(row[0]) not in self.RETRYABLE_STATUSES

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

    def mark_seen(self, key: str, raw_title: str = "") -> None:
        """Record a key as seen without downloading it (first-run policy)."""

        if self.has(key):
            return
        self.upsert(
            DownloadTask(key=key, raw_title=raw_title or key, status="seen")
        )

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

    def set_status_by_torrent(self, torrent_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE torrent_id = ?",
            (status, _now(), torrent_id),
        )
        self._conn.commit()

    def set_title(self, key: str, title: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET title = ?, updated_at = ? WHERE key = ?",
            (title, _now(), key),
        )
        self._conn.commit()

    def set_title_all(self, old_title: str, title: str) -> int:
        cursor = self._conn.execute(
            "UPDATE tasks SET title = ?, updated_at = ? WHERE title = ?",
            (title, _now(), old_title),
        )
        self._conn.commit()
        return cursor.rowcount

    def delete(self, key: str) -> bool:
        cursor = self._conn.execute("DELETE FROM tasks WHERE key = ?", (key,))
        self._conn.commit()
        return cursor.rowcount > 0

    def get(self, key: str) -> DownloadTask | None:
        cursor = self._conn.execute("SELECT * FROM tasks WHERE key = ?", (key,))
        row = cursor.fetchone()
        return self._row_to_task(row) if row else None

    def get_by_torrent(self, torrent_id: str) -> DownloadTask | None:
        cursor = self._conn.execute(
            "SELECT * FROM tasks WHERE torrent_id = ? ORDER BY updated_at DESC", (torrent_id,)
        )
        row = cursor.fetchone()
        return self._row_to_task(row) if row else None

    def list(
        self,
        limit: int | None = None,
        *,
        status: str | None = None,
        title: str | None = None,
        search: str | None = None,
        offset: int = 0,
    ) -> list[DownloadTask]:
        where: list[str] = []
        params: list = []
        if status:
            where.append("status = ?")
            params.append(status)
        if title:
            where.append("title = ?")
            params.append(title)
        if search:
            where.append("(raw_title LIKE ? OR title LIKE ? OR grp LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like])
        query = "SELECT * FROM tasks"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY created_at DESC"
        if limit:
            query += " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset)])
        return [self._row_to_task(row) for row in self._conn.execute(query, params)]

    def count(self, *, status: str | None = None, title: str | None = None, search: str | None = None) -> int:
        where: list[str] = []
        params: list = []
        if status:
            where.append("status = ?")
            params.append(status)
        if title:
            where.append("title = ?")
            params.append(title)
        if search:
            where.append("(raw_title LIKE ? OR title LIKE ? OR grp LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like])
        query = "SELECT COUNT(*) FROM tasks"
        if where:
            query += " WHERE " + " AND ".join(where)
        return int(self._conn.execute(query, params).fetchone()[0])

    def titles(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT title FROM tasks WHERE title != '' ORDER BY title"
        )
        return [str(row[0]) for row in rows]

    def get_meta(self, name: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE name = ?", (name,)).fetchone()
        return str(row[0]) if row else None

    def set_meta(self, name: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta (name, value) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            (name, value),
        )
        self._conn.commit()

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
