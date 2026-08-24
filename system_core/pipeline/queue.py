"""Queue runner: expand inputs, persist jobs, skip/resume, isolate failures.

Implements the Pipeline Mode contract (TZ sections 2.1, 14, 15): a failure on
one file never stops the queue; existing outputs are skipped unless forced; a
killed run resumes from SQLite on the next pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core.paths import ProjectPaths
from ..core.persistence import JobStore
from ..core.workspace_cleanup import remove_source_workspace
from ..media.probe import is_supported
from .orchestrator import outputs_exist, process_file

Logger = Callable[[str], None]
Canceller = Callable[[], bool]
StatusSink = Callable[[Path, str], None]

# Per-file status values emitted to `on_status` (mirror persistence statuses).
ST_PROCESSING = "processing"
ST_COMPLETED = "completed"
ST_FAILED = "failed"
ST_SKIPPED = "skipped"


def _noop(_: str) -> None:
    pass


def _noop_status(_: Path, __: str) -> None:
    pass


@dataclass
class QueueSummary:
    completed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.completed) + len(self.failed) + len(self.skipped)


def collect_sources(inputs: list[Path], *, recursive: bool) -> list[Path]:
    """Expand a mix of files and folders into a sorted list of media files."""
    found: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        resolved = path.resolve()
        if resolved not in seen and is_supported(resolved):
            seen.add(resolved)
            found.append(resolved)

    for raw in inputs:
        path = raw.resolve()
        if path.is_file():
            add(path)
        elif path.is_dir():
            iterator = path.rglob("*") if recursive else path.glob("*")
            for child in iterator:
                if child.is_file():
                    add(child)
    return sorted(found, key=lambda p: str(p).lower())


def run_queue(
    paths: ProjectPaths,
    inputs: list[Path],
    settings: dict[str, Any],
    *,
    log: Logger = _noop,
    cancel: Canceller = lambda: False,
    on_status: StatusSink = _noop_status,
) -> QueueSummary:
    pipeline_cfg = settings.get("pipeline", {})
    recursive = bool(pipeline_cfg.get("recursive", False))
    skip_existing = bool(pipeline_cfg.get("skip_existing", True))
    force = bool(pipeline_cfg.get("force", False))

    sources = collect_sources(inputs, recursive=recursive)
    summary = QueueSummary()
    if not sources:
        log("No supported media files found.")
        return summary

    with JobStore(paths.db_file) as store:
        recovered = store.reset_stale_processing()
        if recovered:
            log(f"Recovered {recovered} interrupted job(s) -> pending")

        for source in sources:
            if cancel():
                log("Queue cancelled.")
                break

            job = store.upsert_job(str(source), options=settings)

            if skip_existing and not force and outputs_exist(source, settings, paths):
                store.mark_skipped(job.id)
                store.clear_chunks(job.id)
                try:
                    remove_source_workspace(paths, source)
                except Exception as exc:
                    log(f"WARN: could not clean workspace for {source.name}: {exc}")
                summary.skipped.append(str(source))
                on_status(source, ST_SKIPPED)
                log(f"SKIP (outputs exist): {source.name}")
                continue

            if force:
                store.clear_chunks(job.id)

            store.mark_processing(job.id)
            on_status(source, ST_PROCESSING)
            log(f"--- Processing: {source.name} ---")
            try:
                outputs = process_file(
                    paths, source, settings, store=store, job_id=job.id, log=log, cancel=cancel
                )
                store.mark_completed(job.id, outputs)
                store.clear_chunks(job.id)
                summary.completed.append(str(source))
                on_status(source, ST_COMPLETED)
                log(f"DONE: {source.name}")
            except Exception as exc:
                message = f"{exc.__class__.__name__}: {exc}"
                store.mark_failed(job.id, message)
                summary.failed.append((str(source), message))
                on_status(source, ST_FAILED)
                log(f"FAILED: {source.name} -> {message}")

    log(
        f"Queue finished: {len(summary.completed)} done, "
        f"{len(summary.skipped)} skipped, {len(summary.failed)} failed."
    )
    return summary
