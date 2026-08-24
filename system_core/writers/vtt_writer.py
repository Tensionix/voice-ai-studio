"""WebVTT subtitle writer (TZ section 10) — generated from JSON segments."""

from __future__ import annotations

from pathlib import Path

from ..core.models import Segment
from .atomic import write_text_atomic
from .subtitle_format import SubtitleSettings, build_cues, format_timestamp


def render_vtt(segments: list[Segment], settings: SubtitleSettings) -> str:
    cues = build_cues(segments, settings)
    blocks: list[str] = ["WEBVTT", ""]
    for cue in cues:
        start = format_timestamp(cue.start, comma=False)
        end = format_timestamp(cue.end, comma=False)
        body = "\n".join(cue.lines)
        blocks.append(f"{start} --> {end}\n{body}\n")
    return "\n".join(blocks)


def write_vtt(dest: Path, segments: list[Segment], settings: SubtitleSettings) -> Path:
    return write_text_atomic(dest, render_vtt(segments, settings))
