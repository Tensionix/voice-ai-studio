"""Export connectors — push a finished transcript to an external destination.

A connector reads the canonical `TranscriptDoc` (the source of truth), never the
sidecar files, so it works the same whether it runs right after the pipeline or
later from a stored `*.transcript.json`. Connectors are opt-in and isolated: a
failing or unconfigured connector never breaks transcription or the other
connectors (see `registry.export_doc`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..core.models import TranscriptDoc
from ..core.paths import ProjectPaths


@dataclass
class ExportResult:
    """Outcome of one connector run (never raised — always returned)."""

    connector: str
    ok: bool
    target: str = ""        # written path (Obsidian) or page URL (Notion)
    message: str = ""       # human-readable detail (success note or error)


class Connector:
    """Base class for export destinations.

    Subclasses are constructed from the merged settings dict + project paths and
    expose a single `export(doc)` call. `is_enabled`/`is_configured` let the
    registry skip connectors that the user hasn't turned on or filled in.
    """

    name = "connector"

    def __init__(self, paths: ProjectPaths, config: dict[str, Any]) -> None:
        self.paths = paths
        self.config = config or {}

    # --- gating --------------------------------------------------------------
    def is_enabled(self) -> bool:
        return bool(self.config.get("enabled", False))

    def is_configured(self) -> tuple[bool, str]:
        """Return (ok, reason). Reason explains what is missing when not ok."""
        return True, ""

    # --- action --------------------------------------------------------------
    def export(self, doc: TranscriptDoc, *, source_path: Optional[Path] = None) -> ExportResult:
        raise NotImplementedError


def doc_title(doc: TranscriptDoc) -> str:
    """The display title for a transcript: metadata title or the source stem."""
    if doc.metadata.title:
        return doc.metadata.title.strip()
    return Path(doc.source.filename or "transcript").stem
