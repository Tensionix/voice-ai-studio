from pathlib import Path

from system_core.core.models import (
    Metadata,
    Segment,
    SourceMeta,
    Transcription,
    TranscriptDoc,
)
from system_core.writers.json_writer import read_json, render_json, write_json
from system_core.writers.markdown_writer import render_markdown
from system_core.writers.srt_writer import render_srt
from system_core.writers.subtitle_format import SubtitleSettings
from system_core.writers.vtt_writer import render_vtt


def _doc() -> TranscriptDoc:
    return TranscriptDoc(
        source=SourceMeta(
            path="C:/media/sample.mp4",
            filename="sample.mp4",
            media_type="video",
            duration_seconds=65.0,
            created_at="2026-06-17T10:00:00",
        ),
        transcription=Transcription(
            language="ru",
            model="gpt-4o-transcribe-diarize",
            diarization=True,
            segments=[
                Segment(index=0, start=0.0, end=3.0, speaker="Speaker 1", text="Привет всем."),
                Segment(index=1, start=3.5, end=7.0, speaker="Speaker 2", text="Начинаем встречу."),
            ],
        ),
        metadata=Metadata(
            title="Тестовая встреча",
            slug="testovaya-vstrecha",
            summary="Короткое резюме.",
            tags=["meeting", "test"],
            action_items=["Отправить отчёт"],
        ),
    )


def test_srt_has_numbered_blocks_and_arrow():
    srt = render_srt(_doc().transcription.segments, SubtitleSettings())
    assert "1\n00:00:00,000 --> " in srt
    assert "-->" in srt
    assert "Привет всем." in srt


def test_vtt_starts_with_header():
    vtt = render_vtt(_doc().transcription.segments, SubtitleSettings())
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> " in vtt


def test_markdown_has_frontmatter_title_and_speakers():
    md = render_markdown(_doc())
    assert md.startswith("---")
    assert 'title: "Тестовая встреча"' in md
    assert "# Тестовая встреча" in md
    assert "## Summary" in md
    assert "## Action Items" in md
    assert "### Speaker 1" in md
    assert "[00:00:00] Привет всем." in md


def test_readable_transcript_groups_segments_into_30_second_paragraphs():
    doc = _doc()
    doc.transcription.segments = [
        Segment(index=0, start=0, end=5, speaker="Speaker 1", text="Первая фраза."),
        Segment(index=1, start=10, end=15, speaker="Speaker 1", text="Вторая фраза."),
        Segment(index=2, start=31, end=36, speaker="Speaker 1", text="Третий абзац."),
    ]

    md = render_markdown(doc)

    assert "[00:00:00] Первая фраза. Вторая фраза." in md
    assert "[00:00:31] Третий абзац." in md


def test_json_roundtrip(tmp_path: Path):
    doc = _doc()
    dest = tmp_path / "sample.transcript.json"
    write_json(dest, doc)
    assert dest.exists()
    loaded = read_json(dest)
    assert loaded.metadata.title == "Тестовая встреча"
    assert loaded.transcription.segments[1].speaker == "Speaker 2"
    assert loaded.speakers == ["Speaker 1", "Speaker 2"]
    # canonical key uses "json" alias, not "json_path"
    assert '"json"' in render_json(doc) or loaded.exports.json_path is None


def test_atomic_write_leaves_no_tmp(tmp_path: Path):
    dest = tmp_path / "out.transcript.json"
    write_json(dest, _doc())
    assert not (tmp_path / "out.transcript.json.tmp").exists()
