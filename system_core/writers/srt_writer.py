"""SRT subtitle writer (TZ section 10) — generated from JSON segments."""

from __future__ import annotations

from pathlib import Path

from ..core.models import Segment
from .atomic import write_text_atomic
from .subtitle_format import SubtitleSettings, build_cues, format_timestamp


def render_srt(segments: list[Segment], settings: SubtitleSettings) -> str:
    cues = build_cues(segments, settings)
    blocks: list[str] = []
    for cue in cues:
        start = format_timestamp(cue.start, comma=True)
        end = format_timestamp(cue.end, comma=True)
        body = "\n".join(cue.lines)
        blocks.append(f"{cue.index}\n{start} --> {end}\n{body}\n")
    return "\n".join(blocks)


def write_srt(dest: Path, segments: list[Segment], settings: SubtitleSettings) -> Path:
    return write_text_atomic(dest, render_srt(segments, settings))
