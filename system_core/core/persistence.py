"""SQLite job/chunk persistence for resume & skip (TZ section 14).

Holds queue state across restarts so the pipeline can resume after an
interruption and skip files whose outputs already exist. One row per source
file in `jobs`; one row per audio chunk in `chunks` (cached transcript JSON so
re-runs don't re-transcribe already-finished chunks).
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional
import json
import sqlite3

from .logging_utils import timestamp

# Job/chunk lifecycle statuses (TZ section 2.1).
PENDING = "pending"
PROCESSING = "processing"
COMPLETED = "completed"
FAILED = "failed"
SKIPPED = "skipped"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path   TEXT NOT NULL UNIQUE,
    status        TEXT NOT NULL DEFAULT 'pending',
    created_at    TEXT,
    started_at    TEXT,
    completed_at  TEXT,
    error         TEXT,
    options_json  TEXT,
    outputs_json  TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL,
    chunk_index     INTEGER NOT NULL,
    start_seconds   REAL,
    end_seconds     REAL,
    status          TEXT NOT NULL DEFAULT 'pending',
    transcript_json TEXT,
    error           TEXT,
    UNIQUE(job_id, chunk_index),
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_chunks_job ON chunks(job_id);
"""


@dataclass
class JobRecord:
    id: int
    source_path: str
    status: str
    created_at: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]
    error: Optional[str]
    options: dict[str, Any]
    outputs: dict[str, Any]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "JobRecord":
        return cls(
            id=row["id"],
            source_path=row["source_path"],
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            error=row["error"],
            options=json.loads(row["options_json"] or "{}"),
            outputs=json.loads(row["outputs_json"] or "{}"),
        )


class JobStore:
    """Thin SQLite wrapper. Safe to open/close per run; resume-friendly."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "JobStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # --- jobs ---------------------------------------------------------------

    def upsert_job(self, source_path: str, options: dict[str, Any] | None = None) -> JobRecord:
        """Insert a job for a source path, or return the existing one."""
        norm = str(Path(source_path).resolve())
        existing = self.get_job(norm)
        if existing is not None:
            return existing
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO jobs (source_path, status, created_at, options_json) VALUES (?, ?, ?, ?)",
                (norm, PENDING, timestamp(), json.dumps(options or {}, ensure_ascii=False)),
            )
        record = self.get_job(norm)
        assert record is not None
        return record

    def get_job(self, source_path: str) -> Optional[JobRecord]:
        norm = str(Path(source_path).resolve())
        row = self._conn.execute("SELECT * FROM jobs WHERE source_path = ?", (norm,)).fetchone()
        return JobRecord.from_row(row) if row else None

    def list_jobs(self, status: str | None = None) -> list[JobRecord]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY id", (status,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        return [JobRecord.from_row(row) for row in rows]

    def mark_processing(self, job_id: int) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, started_at = ?, error = NULL WHERE id = ?",
                (PROCESSING, timestamp(), job_id),
            )

    def mark_completed(self, job_id: int, outputs: dict[str, Any]) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, completed_at = ?, outputs_json = ?, error = NULL WHERE id = ?",
                (COMPLETED, timestamp(), json.dumps(outputs, ensure_ascii=False), job_id),
            )

    def mark_failed(self, job_id: int, error: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, error = ? WHERE id = ?",
                (FAILED, error[:4000], job_id),
            )

    def mark_skipped(self, job_id: int) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (SKIPPED, job_id))

    def reset_stale_processing(self) -> int:
        """On startup, any job left 'processing' from a crash returns to pending."""
        with self._tx() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET status = ? WHERE status = ?", (PENDING, PROCESSING)
            )
            return cursor.rowcount

    def delete_job(self, source_path: str) -> bool:
        """Forget one source and cascade-delete its cached chunk transcripts."""
        norm = str(Path(source_path).resolve())
        with self._tx() as conn:
            cursor = conn.execute("DELETE FROM jobs WHERE source_path = ?", (norm,))
            return cursor.rowcount > 0

    # --- chunks -------------------------------------------------------------

    def upsert_chunk(self, job_id: int, chunk_index: int, start: float, end: float) -> None:
        with self._tx() as conn:
            conn.execute(
                """INSERT INTO chunks (job_id, chunk_index, start_seconds, end_seconds, status)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(job_id, chunk_index) DO NOTHING""",
                (job_id, chunk_index, start, end, PENDING),
            )

    def save_chunk_transcript(self, job_id: int, chunk_index: int, transcript: dict[str, Any]) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE chunks SET status = ?, transcript_json = ?, error = NULL WHERE job_id = ? AND chunk_index = ?",
                (COMPLETED, json.dumps(transcript, ensure_ascii=False), job_id, chunk_index),
            )

    def fail_chunk(self, job_id: int, chunk_index: int, error: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE chunks SET status = ?, error = ? WHERE job_id = ? AND chunk_index = ?",
                (FAILED, error[:4000], job_id, chunk_index),
            )

    def get_chunk_transcript(self, job_id: int, chunk_index: int) -> Optional[dict[str, Any]]:
        row = self._conn.execute(
            "SELECT transcript_json, status FROM chunks WHERE job_id = ? AND chunk_index = ?",
            (job_id, chunk_index),
        ).fetchone()
        if not row or row["status"] != COMPLETED or not row["transcript_json"]:
            return None
        return json.loads(row["transcript_json"])

    def clear_chunks(self, job_id: int) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM chunks WHERE job_id = ?", (job_id,))
