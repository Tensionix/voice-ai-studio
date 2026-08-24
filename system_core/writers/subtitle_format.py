"""Shared subtitle cue formatting for SRT/VTT (TZ section 10).

Splits long segment text into readable lines, clamps cue duration, and
optionally prefixes the speaker label. Produces vendor-neutral cues that both
SRT and VTT writers render.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.models import Segment


@dataclass
class SubtitleSettings:
    max_chars_per_line: int = 42
    max_lines: int = 2
    min_duration: float = 1.0
    max_duration: float = 7.0
    include_speakers: bool = False


@dataclass
class Cue:
    index: int
    start: float
    end: float
    lines: list[str] = field(default_factory=list)


def wrap_text(text: str, max_chars: int, max_lines: int) -> list[str]:
    """Greedy word wrap into at most max_lines lines of <= max_chars each."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)

    if len(lines) > max_lines:
        # Collapse the overflow into the last allowed line.
        head = lines[: max_lines - 1]
        tail = " ".join(lines[max_lines - 1:])
        lines = head + [tail]
    return lines


def _clamp_end(start: float, end: float, settings: SubtitleSettings) -> float:
    duration = end - start
    if duration < settings.min_duration:
        end = start + settings.min_duration
    elif duration > settings.max_duration:
        end = start + settings.max_duration
    return end


def build_cues(segments: list[Segment], settings: SubtitleSettings) -> list[Cue]:
    cues: list[Cue] = []
    cue_index = 1
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        if settings.include_speakers and seg.speaker:
            text = f"{seg.speaker}: {text}"
        start = max(0.0, float(seg.start))
        end = _clamp_end(start, max(start, float(seg.end)), settings)
        lines = wrap_text(text, settings.max_chars_per_line, settings.max_lines)
        cues.append(Cue(index=cue_index, start=start, end=end, lines=lines))
        cue_index += 1
    return cues


def format_timestamp(seconds: float, *, comma: bool) -> str:
    """HH:MM:SS,mmm (SRT) or HH:MM:SS.mmm (VTT)."""
    if seconds < 0:
        seconds = 0.0
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    sep = "," if comma else "."
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{millis:03d}"
