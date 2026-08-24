from __future__ import annotations

import wave

import pytest

from system_core.core.paths import get_project_paths
from system_core.pipeline import orchestrator
from system_core.providers import registry
from system_core.providers.base import TranscriptionOptions
from system_core.providers.gigaam_provider import GigaAMTranscribeProvider


def _write_wav(path, *, seconds: float = 1.0, sample_rate: int = 16000) -> None:
    frames = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * frames)


def test_local_vulkan_diarization_uses_pyannote_provider(tmp_path):
    paths = get_project_paths(tmp_path)

    provider = registry.get_diarization_provider(
        paths,
        {
            "compute_mode": "vulkan",
            "vulkan": {"engine": "gigaam"},
            "diarization": {"enabled": True},
        },
    )

    assert provider is not None
    assert provider.name == "pyannote"


def test_local_vulkan_diarization_accepts_transcription_flag(tmp_path):
    paths = get_project_paths(tmp_path)

    provider = registry.get_diarization_provider(
        paths,
        {
            "compute_mode": "vulkan",
            "vulkan": {"engine": "gigaam"},
            "transcription": {"diarize": True},
        },
    )

    assert provider is not None
    assert provider.name == "pyannote"


def test_api_diarization_does_not_request_separate_pyannote(tmp_path):
    paths = get_project_paths(tmp_path)

    provider = registry.get_diarization_provider(
        paths,
        {
            "compute_mode": "api",
            "diarization": {"enabled": True},
        },
    )

    assert provider is None


def test_gigaam_diarization_chunk_seconds_are_shorter_for_overlap_labels(tmp_path):
    paths = get_project_paths(tmp_path)

    chunk_seconds = orchestrator._effective_chunk_seconds(
        paths,
        {
            "compute_mode": "vulkan",
            "vulkan": {"engine": "gigaam"},
            "diarization": {"enabled": True, "gigaam_chunk_seconds": 45},
        },
        {"chunk_seconds": 600},
    )

    assert chunk_seconds == 45


def test_gigaam_transcribe_segments_keep_wav_duration(tmp_path, monkeypatch):
    paths = get_project_paths(tmp_path)
    wav_path = tmp_path / "sample.wav"
    _write_wav(wav_path, seconds=1.25)

    class FakeModel:
        def recognize(self, _path):
            return "hello"

    monkeypatch.setattr(GigaAMTranscribeProvider, "_get_model", lambda self: FakeModel())

    result = GigaAMTranscribeProvider(paths, backend="cpu").transcribe(
        wav_path, TranscriptionOptions(language="ru")
    )

    assert result.segments[0].start == 0.0
    assert result.segments[0].end == pytest.approx(1.25)
    assert result.segments[0].text == "hello"
