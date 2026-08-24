"""Merge per-chunk transcripts into one absolute timeline (TZ section 6, step 8).

Each chunk's segments are chunk-relative; we shift them by the chunk's start
offset, drop empties, sort by start, and re-index. Overlap regions (when chunk
overlap > 0) are de-duplicated by dropping later segments that start before the
previous segment's end with near-identical text.
"""

from __future__ import annotations

from ..core.models import Segment


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def merge_chunk_segments(chunk_segments: list[tuple[float, list[Segment]]]) -> list[Segment]:
    shifted: list[Segment] = []
    for offset, segments in chunk_segments:
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            shifted.append(
                Segment(
                    index=0,
                    start=seg.start + offset,
                    end=seg.end + offset,
                    speaker=seg.speaker,
                    text=text,
                    confidence=seg.confidence,
                )
            )

    shifted.sort(key=lambda s: (s.start, s.end))

    merged: list[Segment] = []
    for seg in shifted:
        if merged:
            prev = merged[-1]
            # Drop duplicate text introduced by chunk overlap.
            if seg.start < prev.end and _normalize(seg.text) == _normalize(prev.text):
                continue
        merged.append(seg)

    for index, seg in enumerate(merged):
        seg.index = index
    return merged
