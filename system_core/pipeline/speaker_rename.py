"""Speaker rename — a post layer over the canonical transcript (TZ section 11).

Diarization labels speakers generically (`Speaker 0`, `SPEAKER_01`, …). This maps
those labels to real names the user assigns, rewrites them in the canonical JSON,
and regenerates the sidecars that exist next to it (MD/SRT/VTT/TXT) so every
artifact shows the real names. It never re-transcribes — it edits the source of
truth and re-renders from it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.models import TranscriptDoc
from ..writers.json_writer import read_json, write_json
from ..writers.markdown_writer import write_markdown
from ..writers.srt_writer import write_srt
from ..writers.txt_writer import write_txt
from ..writers.vtt_writer import write_vtt
from .orchestrator import _subtitle_settings


def apply_speaker_mapping(doc: TranscriptDoc, mapping: dict[str, str]) -> TranscriptDoc:
    """Rewrite speaker labels in place. An empty/blank target leaves a label as is
    (so the user can rename a subset). Also patches any speaker labels embedded in
    the cleaned-up Markdown body, since that prose drives the Markdown transcript."""
    clean = {old: new.strip() for old, new in (mapping or {}).items() if (new or "").strip()}
    if not clean:
        return doc
    for seg in doc.transcription.segments:
        if seg.speaker and seg.speaker in clean:
            seg.speaker = clean[seg.speaker]
    if doc.cleaned_markdown:
        body = doc.cleaned_markdown
        # Longest labels first so "Speaker 1" isn't half-replaced by "Speaker 10".
        for old in sorted(clean, key=len, reverse=True):
            body = body.replace(old, clean[old])
        doc.cleaned_markdown = body
    return doc


def _regen_sidecars(json_path: Path, doc: TranscriptDoc, settings: dict[str, Any]) -> list[Path]:
    """Rewrite the JSON and regenerate whichever sidecars already exist beside it."""
    written = [write_json(json_path, doc)]
    stem = json_path.name.replace(".transcript.json", "")
    base = json_path.parent / stem
    segments = doc.transcription.segments
    sub = _subtitle_settings(settings)

    md = base.with_suffix(".md")
    if md.exists():
        written.append(write_markdown(md, doc))
    srt = base.with_suffix(".srt")
    if srt.exists():
        written.append(write_srt(srt, segments, sub))
    vtt = base.with_suffix(".vtt")
    if vtt.exists():
        written.append(write_vtt(vtt, segments, sub))
    txt = base.with_suffix(".txt")
    if txt.exists():
        written.append(write_txt(txt, segments, include_speakers=sub.include_speakers))
    return written


def rename_speakers_in_file(
    json_path: Path, mapping: dict[str, str], settings: dict[str, Any]
) -> list[Path]:
    """Load `*.transcript.json`, apply the rename map, write it back and regenerate
    the existing sidecars. Returns the list of rewritten paths."""
    doc = read_json(json_path)
    apply_speaker_mapping(doc, mapping)
    return _regen_sidecars(json_path, doc, settings)
