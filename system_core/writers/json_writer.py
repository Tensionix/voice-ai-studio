"""Canonical JSON writer/reader (TZ section 8) — the source of truth.

All other sidecars are generated from this document, and it can be re-loaded to
regenerate MD/SRT/VTT without re-transcribing.
"""

from __future__ import annotations

from pathlib import Path
import json

from ..core.models import TranscriptDoc
from .atomic import write_text_atomic


def render_json(doc: TranscriptDoc) -> str:
    return json.dumps(doc.to_json_dict(), ensure_ascii=False, indent=2)


def write_json(dest: Path, doc: TranscriptDoc) -> Path:
    return write_text_atomic(dest, render_json(doc))


def read_json(path: Path) -> TranscriptDoc:
    data = json.loads(path.read_text(encoding="utf-8"))
    return TranscriptDoc.from_json_dict(data)
