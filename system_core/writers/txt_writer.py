"""Plain-text writer — generated from JSON segments."""

from __future__ import annotations

from pathlib import Path

from ..core.models import Segment
from .atomic import write_text_atomic
from .transcript_layout import transcript_paragraphs


def _hms(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def render_txt(segments: list[Segment], *, include_speakers: bool = True) -> str:
    lines: list[str] = []
    current_speaker: str | None = None
    for paragraph in transcript_paragraphs(segments, marker_interval_seconds=30.0):
        if include_speakers and paragraph.speaker and paragraph.speaker != current_speaker:
            lines.append("")
            lines.append(f"{paragraph.speaker}:")
            current_speaker = paragraph.speaker
        lines.append(f"[{_hms(paragraph.start)}] {paragraph.text}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def write_txt(dest: Path, segments: list[Segment], *, include_speakers: bool = True) -> Path:
    return write_text_atomic(dest, render_txt(segments, include_speakers=include_speakers))
