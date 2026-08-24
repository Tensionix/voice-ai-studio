"""Readable paragraphs derived from exact canonical ASR segments.

The canonical JSON keeps every timestamp for diarization and subtitles. Human
TXT/Markdown exports intentionally expose a calmer document: speaker blocks and
roughly one paragraph/time marker per interval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.models import Segment


@dataclass(frozen=True)
class TranscriptParagraph:
    start: float
    speaker: str | None
    text: str


def normalize_asr_text(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = re.sub(r"\s+([,.;:!?%])", r"\1", value)
    value = re.sub(r"([«(])\s+", r"\1", value)
    value = re.sub(r"\s+([»)])", r"\1", value)
    return value


def _join_text(left: str, right: str) -> str:
    left = normalize_asr_text(left)
    right = normalize_asr_text(right)
    if not left:
        return right
    if not right:
        return left
    return normalize_asr_text(f"{left} {right}")


def transcript_paragraphs(
    segments: list[Segment], *, marker_interval_seconds: float = 30.0
) -> list[TranscriptParagraph]:
    interval = max(1.0, float(marker_interval_seconds or 30.0))
    paragraphs: list[TranscriptParagraph] = []
    start = 0.0
    speaker: str | None = None
    text = ""

    def flush() -> None:
        nonlocal text
        clean = normalize_asr_text(text)
        if clean:
            paragraphs.append(TranscriptParagraph(start, speaker, clean))
        text = ""

    for segment in segments:
        clean = normalize_asr_text(segment.text)
        if not clean:
            continue
        segment_speaker = segment.speaker or None
        should_break = bool(text) and (
            segment_speaker != speaker
            or float(segment.start) - start >= interval
        )
        if should_break:
            flush()
        if not text:
            start = max(0.0, float(segment.start))
            speaker = segment_speaker
            text = clean
        else:
            text = _join_text(text, clean)
    flush()
    return paragraphs
