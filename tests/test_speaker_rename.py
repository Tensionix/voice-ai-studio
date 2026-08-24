from pathlib import Path

from system_core.core.models import (
    Metadata,
    Segment,
    SourceMeta,
    Transcription,
    TranscriptDoc,
)
from system_core.pipeline.speaker_rename import apply_speaker_mapping, rename_speakers_in_file
from system_core.writers.json_writer import read_json, write_json
from system_core.writers.markdown_writer import write_markdown
from system_core.writers.srt_writer import write_srt
from system_core.writers.subtitle_format import SubtitleSettings


def _doc() -> TranscriptDoc:
    return TranscriptDoc(
        source=SourceMeta(path="x/m.mp4", filename="m.mp4", duration_seconds=20.0,
                          created_at="2026-06-18T10:00:00"),
        transcription=Transcription(
            language="ru",
            diarization=True,
            segments=[
                Segment(index=0, start=0.0, end=3.0, speaker="Speaker 0", text="Привет."),
                Segment(index=1, start=3.0, end=6.0, speaker="Speaker 1", text="Здравствуйте."),
                Segment(index=2, start=6.0, end=9.0, speaker="Speaker 0", text="Как дела?"),
            ],
        ),
        metadata=Metadata(title="Встреча", slug="vstrecha"),
    )


def test_apply_mapping_renames_only_given_nonempty():
    doc = _doc()
    apply_speaker_mapping(doc, {"Speaker 0": "Иван", "Speaker 1": ""})
    speakers = doc.speakers
    assert "Иван" in speakers
    assert "Speaker 1" in speakers  # empty target left unchanged
    assert "Speaker 0" not in speakers


def test_apply_mapping_patches_cleaned_markdown():
    doc = _doc()
    doc.cleaned_markdown = "### Speaker 0\n\nПривет.\n\n### Speaker 1\n\nЗдравствуйте."
    apply_speaker_mapping(doc, {"Speaker 0": "Иван", "Speaker 1": "Пётр"})
    assert "### Иван" in doc.cleaned_markdown
    assert "### Пётр" in doc.cleaned_markdown
    assert "Speaker" not in doc.cleaned_markdown


def test_rename_in_file_rewrites_json_and_existing_sidecars(tmp_path: Path):
    doc = _doc()
    json_path = tmp_path / "m.transcript.json"
    write_json(json_path, doc)
    md_path = tmp_path / "m.md"
    write_markdown(md_path, doc)
    srt_path = tmp_path / "m.srt"
    write_srt(srt_path, doc.transcription.segments, SubtitleSettings(include_speakers=True))
    # No .vtt/.txt written -> they must NOT be created by the rename.

    written = rename_speakers_in_file(json_path, {"Speaker 0": "Иван"}, {})

    names = {p.name for p in written}
    assert names == {"m.transcript.json", "m.md", "m.srt"}
    assert not (tmp_path / "m.vtt").exists()
    assert not (tmp_path / "m.txt").exists()

    reloaded = read_json(json_path)
    assert "Иван" in reloaded.speakers
    assert "Иван" in md_path.read_text(encoding="utf-8")
