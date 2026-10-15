"""SQLite persistence for jobs, listing pages, threads, output batches and the processing log."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    start_url TEXT NOT NULL,
    config TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT DEFAULT '',
    platform TEXT DEFAULT '',
    current_page_url TEXT DEFAULT '',
    current_page_number INTEGER,
    current_output_file TEXT DEFAULT '',
    last_confidence REAL,
    created_at TEXT, updated_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    page_number INTEGER,
    url TEXT NOT NULL,
    status TEXT NOT NULL,
    thread_count INTEGER DEFAULT 0,
    next_url TEXT,
    next_method TEXT,
    error TEXT DEFAULT '',
    updated_at TEXT,
    UNIQUE(job_id, seq)
);
CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    page_seq INTEGER NOT NULL,
    page_number INTEGER,
    listing_url TEXT,
    position INTEGER,
    thread_url TEXT NOT NULL,
    thread_key TEXT NOT NULL,
    title TEXT,
    status TEXT NOT NULL,
    confidence REAL,
    method TEXT DEFAULT '',
    output_file TEXT DEFAULT '',
    error TEXT DEFAULT '',
    retry_count INTEGER DEFAULT 0,
    content_hash TEXT DEFAULT '',
    updated_at TEXT,
    UNIQUE(job_id, page_seq, thread_key)
);
CREATE INDEX IF NOT EXISTS idx_threads_key ON threads(thread_key, status);
CREATE INDEX IF NOT EXISTS idx_threads_job ON threads(job_id, status);
CREATE TABLE IF NOT EXISTS batches (
    job_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    first_page INTEGER,
    last_page INTEGER,
    planned_last INTEGER,
    filename TEXT,
    closed INTEGER DEFAULT 0,
    PRIMARY KEY(job_id, idx)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    ts TEXT,
    level TEXT,
    event TEXT,
    message TEXT,
    data TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, id);
"""

DONE_STATES = ("done", "duplicate", "skipped")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def _exec(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            for attempt in range(5):
                try:
                    cur = self.conn.execute(sql, params)
                    self.conn.commit()
                    return cur
                except sqlite3.OperationalError as exc:
                    if "locked" in str(exc) and attempt < 4:
                        time.sleep(0.2 * (attempt + 1))
                        continue
                    raise
        raise RuntimeError("unreachable")

    def _one(self, sql: str, params: tuple = ()) -> dict | None:
        with self._lock:
            row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    # ------------------------------------------------------------------ jobs
    def create_job(self, start_url: str, config: dict[str, Any]) -> str:
        job_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        ts = now_iso()
        self._exec("INSERT INTO jobs(id,start_url,config,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                   (job_id, start_url, json.dumps(config), "created", ts, ts))
        return job_id

    def get_job(self, job_id: str) -> dict | None:
        row = self._one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if row:
            row["config"] = json.loads(row["config"])
        return row

    def list_jobs(self, limit: int = 50) -> list[dict]:
        rows = self._all("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        for r in rows:
            r["config"] = json.loads(r["config"])
        return rows

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        if "config" in fields and not isinstance(fields["config"], str):
            fields["config"] = json.dumps(fields["config"])
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))

    def mark_interrupted_jobs(self) -> int:
        cur = self._exec("UPDATE jobs SET status='interrupted', message='application stopped while running' "
                         "WHERE status IN ('running','paused','awaiting_confirmation')")
        return cur.rowcount

    def delete_job(self, job_id: str) -> None:
        with self._lock:
            for table in ("threads", "pages", "batches", "events"):
                self.conn.execute(f"DELETE FROM {table} WHERE job_id=?", (job_id,))
            self.conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            self.conn.commit()

    # ------------------------------------------------------------------ pages
    def upsert_page(self, job_id: str, seq: int, url: str, page_number: int | None, status: str, **extra) -> None:
        existing = self._one("SELECT id FROM pages WHERE job_id=? AND seq=?", (job_id, seq))
        ts = now_iso()
        if existing:
            sets = {"url": url, "page_number": page_number, "status": status, "updated_at": ts, **extra}
            cols = ", ".join(f"{k}=?" for k in sets)
            self._exec(f"UPDATE pages SET {cols} WHERE id=?", (*sets.values(), existing["id"]))
        else:
            cols = ["job_id", "seq", "url", "page_number", "status", "updated_at", *extra.keys()]
            vals = [job_id, seq, url, page_number, status, ts, *extra.values()]
            self._exec(f"INSERT INTO pages({','.join(cols)}) VALUES({','.join('?' * len(cols))})", vals)

    def get_pages(self, job_id: str) -> list[dict]:
        return self._all("SELECT * FROM pages WHERE job_id=? ORDER BY seq", (job_id,))

    def get_page(self, job_id: str, seq: int) -> dict | None:
        return self._one("SELECT * FROM pages WHERE job_id=? AND seq=?", (job_id, seq))

    # ------------------------------------------------------------------ threads
    def add_thread(self, job_id: str, page_seq: int, page_number: int | None, listing_url: str, position: int,
                   url: str, key: str, title: str) -> dict:
        self._exec(
            "INSERT OR IGNORE INTO threads(job_id,page_seq,page_number,listing_url,position,thread_url,thread_key,"
            "title,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (job_id, page_seq, page_number, listing_url, position, url, key, title, "pending", now_iso()))
        return self._one("SELECT * FROM threads WHERE job_id=? AND page_seq=? AND thread_key=?",
                         (job_id, page_seq, key))

    def update_thread(self, thread_id: int, **fields: Any) -> None:
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE threads SET {cols} WHERE id=?", (*fields.values(), thread_id))

    def page_threads(self, job_id: str, page_seq: int) -> list[dict]:
        return self._all("SELECT * FROM threads WHERE job_id=? AND page_seq=? ORDER BY position", (job_id, page_seq))

    def failed_threads(self, job_id: str) -> list[dict]:
        return self._all("SELECT * FROM threads WHERE job_id=? AND status='failed' ORDER BY page_seq, position",
                         (job_id,))

    def key_done_in_job(self, job_id: str, key: str, exclude_id: int) -> bool:
        return self._one("SELECT 1 FROM threads WHERE job_id=? AND thread_key=? AND status IN ('done','failed') "
                         "AND id<>? LIMIT 1", (job_id, key, exclude_id)) is not None

    def key_done_elsewhere(self, job_id: str, key: str) -> bool:
        return self._one("SELECT 1 FROM threads WHERE job_id<>? AND thread_key=? AND status='done' LIMIT 1",
                         (job_id, key)) is not None

    def hash_done_in_job(self, job_id: str, content_hash: str, exclude_id: int) -> bool:
        return self._one("SELECT 1 FROM threads WHERE job_id=? AND content_hash=? AND status='done' AND id<>? LIMIT 1",
                         (job_id, content_hash, exclude_id)) is not None

    def thread_counts(self, job_id: str) -> dict[str, int]:
        rows = self._all("SELECT status, COUNT(*) AS n FROM threads WHERE job_id=? GROUP BY status", (job_id,))
        counts = {r["status"]: r["n"] for r in rows}
        counts["discovered"] = sum(counts.values())
        return counts

    def avg_confidence(self, job_id: str) -> float | None:
        row = self._one("SELECT AVG(confidence) AS a FROM threads WHERE job_id=? AND status='done'", (job_id,))
        return row["a"] if row else None

    # ------------------------------------------------------------------ batches
    def get_batch(self, job_id: str, idx: int) -> dict | None:
        return self._one("SELECT * FROM batches WHERE job_id=? AND idx=?", (job_id, idx))

    def save_batch(self, job_id: str, idx: int, **fields: Any) -> None:
        if self.get_batch(job_id, idx):
            cols = ", ".join(f"{k}=?" for k in fields)
            self._exec(f"UPDATE batches SET {cols} WHERE job_id=? AND idx=?", (*fields.values(), job_id, idx))
        else:
            cols = ["job_id", "idx", *fields.keys()]
            self._exec(f"INSERT INTO batches({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                       (job_id, idx, *fields.values()))

    def batches(self, job_id: str) -> list[dict]:
        return self._all("SELECT * FROM batches WHERE job_id=? ORDER BY idx", (job_id,))

    def rename_output(self, job_id: str, old: str, new: str) -> None:
        self._exec("UPDATE threads SET output_file=? WHERE job_id=? AND output_file=?", (new, job_id, old))

    # ------------------------------------------------------------------ events / log
    def log_event(self, job_id: str | None, level: str, event: str, message: str, **data: Any) -> None:
        self._exec("INSERT INTO events(job_id,ts,level,event,message,data) VALUES(?,?,?,?,?,?)",
                   (job_id, now_iso(), level, event, message, json.dumps(data, default=str)))

    def events(self, job_id: str, level: str | None = None, limit: int | None = None,
               newest_first: bool = False) -> list[dict]:
        sql = "SELECT * FROM events WHERE job_id=?"
        params: list[Any] = [job_id]
        if level:
            sql += " AND level=?"
            params.append(level)
        sql += " ORDER BY id " + ("DESC" if newest_first else "ASC")
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return self._all(sql, tuple(params))
