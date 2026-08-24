"""Markdown writer (TZ section 9) — generated from the canonical JSON doc.

Obsidian-friendly: YAML frontmatter + H1 title + Summary + Action Items +
Transcript grouped by speaker with [HH:MM:SS] timecodes.
"""

from __future__ import annotations

from pathlib import Path

from ..core.models import Segment, TranscriptDoc
from .atomic import write_text_atomic
from .transcript_layout import transcript_paragraphs


def _hms(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _yaml_list(items: list[str]) -> str:
    if not items:
        return "[]"
    quoted = ", ".join(f'"{item}"' for item in items)
    return f"[{quoted}]"


def _frontmatter(doc: TranscriptDoc) -> str:
    md = doc.metadata
    src = doc.source
    title = md.title or Path(src.filename).stem
    date = (src.created_at or "")[:10]
    lines = [
        "---",
        f'title: "{title}"',
        f'date: "{date}"',
        f'source_file: "{src.filename}"',
        f'duration: "{_hms(src.duration_seconds)}"',
        f'language: "{doc.transcription.language or ""}"',
        f"speakers: {_yaml_list(doc.speakers)}",
        f"tags: {_yaml_list(md.tags)}",
        "---",
    ]
    return "\n".join(lines)


def _transcript_body(segments: list[Segment]) -> str:
    lines: list[str] = []
    has_speakers = any(seg.speaker for seg in segments)
    current_speaker: str | None = None
    for paragraph in transcript_paragraphs(segments, marker_interval_seconds=30.0):
        if has_speakers and paragraph.speaker and paragraph.speaker != current_speaker:
            lines.append("")
            lines.append(f"### {paragraph.speaker}")
            lines.append("")
            current_speaker = paragraph.speaker
        lines.append(f"[{_hms(paragraph.start)}] {paragraph.text}")
        lines.append("")
    return "\n".join(lines).strip()


def render_markdown(doc: TranscriptDoc) -> str:
    md = doc.metadata
    title = md.title or Path(doc.source.filename).stem
    parts = [_frontmatter(doc), "", f"# {title}", ""]

    if md.summary:
        parts += ["## Summary", "", md.summary.strip(), ""]

    if md.action_items:
        parts += ["## Action Items", ""]
        parts += [f"- {item}" for item in md.action_items]
        parts += [""]

    # Prefer the polished cleanup body; fall back to raw timestamped segments.
    body = doc.cleaned_markdown.strip() if doc.cleaned_markdown else _transcript_body(
        doc.transcription.segments
    )
    parts += ["## Transcript", "", body, ""]
    return "\n".join(parts)


def write_markdown(dest: Path, doc: TranscriptDoc) -> Path:
    return write_text_atomic(dest, render_markdown(doc))
