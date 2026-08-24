from pathlib import Path

from system_core.media.probe import is_supported, supported_extensions, supported_extensions_label


def test_supported_media_extensions_include_ffmpeg_ingest_set():
    for suffix in (".mp3", ".wav", ".m4a", ".flac", ".opus", ".aiff", ".caf", ".amr", ".ac3", ".wv"):
        assert is_supported(Path(f"audio{suffix}"))
    for suffix in (".mp4", ".mkv", ".mov", ".webm", ".avi", ".m2ts", ".mts", ".3gp", ".ogv", ".vob"):
        assert is_supported(Path(f"video{suffix}"))
    assert not is_supported(Path("notes.docx"))


def test_supported_extensions_label_is_human_readable():
    exts = supported_extensions()
    assert exts == sorted(exts)
    label = supported_extensions_label()
    assert "MP3" in label
    assert "M2TS" in label
    assert "." not in label

