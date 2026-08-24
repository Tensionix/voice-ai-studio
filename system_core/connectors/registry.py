"""Connector selection + isolated fan-out export.

`build_connectors` instantiates every connector from the `connectors:` settings
block; `export_doc` runs the enabled+configured ones, isolating failures so one
destination (or a missing key/path) never breaks the others or the pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from ..core.models import TranscriptDoc
from ..core.paths import ProjectPaths
from .base import Connector, ExportResult
from .notion import NotionConnector
from .obsidian import ObsidianConnector

Logger = Callable[[str], None]


def _noop(_: str) -> None:
    pass


def build_connectors(paths: ProjectPaths, settings: dict[str, Any]) -> list[Connector]:
    cfg = settings.get("connectors", {}) or {}
    return [
        ObsidianConnector(paths, cfg.get("obsidian", {}) or {}),
        NotionConnector(paths, cfg.get("notion", {}) or {}),
    ]


def any_enabled(paths: ProjectPaths, settings: dict[str, Any]) -> bool:
    return any(c.is_enabled() for c in build_connectors(paths, settings))


def export_doc(
    paths: ProjectPaths,
    settings: dict[str, Any],
    doc: TranscriptDoc,
    *,
    source_path: Optional[Path] = None,
    only: Optional[set[str]] = None,
    log: Logger = _noop,
) -> list[ExportResult]:
    """Export `doc` through enabled connectors. `only` forces a subset by name
    (used by the CLI flags) and overrides the enabled flag for those names."""
    results: list[ExportResult] = []
    for connector in build_connectors(paths, settings):
        forced = only is not None and connector.name in only
        if not forced and not connector.is_enabled():
            continue
        if only is not None and not forced:
            continue
        ok, reason = connector.is_configured()
        if not ok:
            log(f"[connector:{connector.name}] skipped — {reason}")
            results.append(ExportResult(connector.name, False, message=reason))
            continue
        try:
            result = connector.export(doc, source_path=source_path)
        except Exception as exc:  # last-resort guard; connectors shouldn't raise
            result = ExportResult(connector.name, False, message=f"Unexpected error: {exc}")
        if result.ok:
            log(f"[connector:{connector.name}] {result.message}: {result.target}")
        else:
            log(f"[connector:{connector.name}] failed — {result.message}")
        results.append(result)
    return results
