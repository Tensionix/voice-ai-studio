"""GigaAM long-audio path: cut into <=25 s pieces at the quietest points.

No model is loaded: the ASR adapter and the VAD loader are faked. The tests pin
the contract the orchestrator and the SRT writer rely on: pieces cover the
whole file (nothing is discarded), carry real start/end, short audio bypasses
the splitter, and a missing VAD payload only changes the cut criterion."""

from __future__ import annotations

import struct
import wave

import pytest

np = pytest.importorskip("numpy", reason="numpy arrives with the GigaAM ONNX pack")

from system_core.core.model_assets import gigaam_vad_available, gigaam_vad_cache_dir
from system_core.core.paths import get_project_paths
from system_core.pipeline.orchestrator import _effective_chunk_seconds
from system_core.providers import gigaam_provider
from system_core.providers.base import TranscriptionOptions
from system_core.providers.gigaam_provider import (
    PIECE_MAX_SECONDS,
    PIECE_MIN_SECONDS,
    GigaAMTranscribeProvider,
    _cut_points,
)


def _write_wav(path, seconds: float, rate: int = 16000, amplitude: int = 0) -> None:
    frames = int(seconds * rate)
    samples = [amplitude] * frames
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack(f"<{frames}h", *samples))


class _FakeModel:
    """Records what it was asked to recognise; pieces come back as 'p<i>'."""

    def __init__(self):
        self.batches: list[list[np.ndarray]] = []
        self.plain_calls = 0

    def recognize(self, waveform, sample_rate=16000):
        if isinstance(waveform, list):
            self.batches.append(waveform)
            return [f"p{len(self.batches)}-{i}" for i, _ in enumerate(waveform)]
        self.plain_calls += 1
        return "whole file"


def _provider(monkeypatch, paths, model, scores=None):
    monkeypatch.setattr(GigaAMTranscribeProvider, "_get_model", lambda self: model)
    if scores is None:
        monkeypatch.setattr(GigaAMTranscribeProvider, "_speech_scores", lambda self, pcm, rate, hop: None)
    else:
        monkeypatch.setattr(GigaAMTranscribeProvider, "_speech_scores", lambda self, pcm, rate, hop: scores)
    return GigaAMTranscribeProvider(paths, backend="cpu")


def test_vad_cache_detection(tmp_path):
    paths = get_project_paths(tmp_path)
    assert not gigaam_vad_available(paths)
    (gigaam_vad_cache_dir(paths) / "snapshots").mkdir(parents=True)
    assert gigaam_vad_available(paths)


def test_cut_points_cover_everything_and_prefer_quiet_frames():
    rate, hop = 16000, 512
    total = 60 * rate
    scores = np.ones(total // hop + 2, dtype=np.float32)
    quiet_frame = int(18.0 * rate) // hop  # a pause 18 s in
    scores[quiet_frame] = 0.0
    pieces = _cut_points(scores, hop, rate, total)

    assert pieces[0][0] == 0 and pieces[-1][1] == total
    assert all(a[1] == b[0] for a, b in zip(pieces, pieces[1:]))  # contiguous, no gaps
    assert pieces[0][1] == quiet_frame * hop
    for start, end in pieces:
        assert end - start <= PIECE_MAX_SECONDS * rate + hop
    for start, end in pieces[:-1]:
        assert end - start >= PIECE_MIN_SECONDS * rate - hop


def test_long_audio_is_split_with_timestamps(tmp_path, monkeypatch):
    paths = get_project_paths(tmp_path)
    wav = tmp_path / "long.wav"
    _write_wav(wav, seconds=60.0, amplitude=1000)
    model = _FakeModel()
    result = _provider(monkeypatch, paths, model).transcribe(wav, TranscriptionOptions(language="ru"))

    assert model.plain_calls == 0
    pieces = [arr for batch in model.batches for arr in batch]
    assert sum(len(arr) for arr in pieces) == 60 * 16000  # nothing discarded
    assert len(result.segments) == len(pieces)
    assert result.segments[0].start == 0.0
    assert abs(result.segments[-1].end - 60.0) < 0.05
    assert [s.index for s in result.segments] == list(range(len(pieces)))
    for a, b in zip(result.segments, result.segments[1:]):
        assert b.start == a.end


def test_silero_scores_drive_the_cut(tmp_path, monkeypatch):
    paths = get_project_paths(tmp_path)
    wav = tmp_path / "long.wav"
    _write_wav(wav, seconds=40.0, amplitude=1000)
    rate, hop = 16000, 512
    scores = np.ones(40 * rate // hop + 2, dtype=np.float32)
    scores[int(20.0 * rate) // hop] = 0.0
    model = _FakeModel()
    result = _provider(monkeypatch, paths, model, scores=scores).transcribe(
        wav, TranscriptionOptions(language="ru")
    )
    assert abs(result.segments[0].end - 20.0) < 0.05


def test_short_audio_skips_split(tmp_path, monkeypatch):
    paths = get_project_paths(tmp_path)
    wav = tmp_path / "short.wav"
    _write_wav(wav, seconds=3.0)
    model = _FakeModel()
    result = _provider(monkeypatch, paths, model).transcribe(wav, TranscriptionOptions(language="ru"))

    assert model.batches == []
    assert model.plain_calls == 1
    assert result.segments[0].text == "whole file"
    assert result.segments[0].end == 3.0


def test_missing_vad_payload_still_splits_by_energy(tmp_path, monkeypatch):
    paths = get_project_paths(tmp_path)
    wav = tmp_path / "long.wav"
    _write_wav(wav, seconds=40.0, amplitude=1000)
    model = _FakeModel()
    monkeypatch.setattr(GigaAMTranscribeProvider, "_get_model", lambda self: model)

    def _boom(_paths):
        raise RuntimeError("offline")

    monkeypatch.setattr(gigaam_provider, "_load_cached_vad", _boom)
    result = GigaAMTranscribeProvider(paths, backend="cpu").transcribe(wav, TranscriptionOptions(language="ru"))

    assert model.plain_calls == 0
    assert len(model.batches) >= 1
    assert result.segments and abs(result.segments[-1].end - 40.0) < 0.05


def test_vad_filter_off_uses_whole_file(tmp_path, monkeypatch):
    paths = get_project_paths(tmp_path)
    wav = tmp_path / "long.wav"
    _write_wav(wav, seconds=40.0)
    model = _FakeModel()
    result = _provider(monkeypatch, paths, model).transcribe(
        wav, TranscriptionOptions(language="ru", vad_filter=False)
    )
    assert model.batches == []
    assert result.segments[0].text == "whole file"


def test_pipeline_chunk_is_only_capped_for_diarization(tmp_path):
    paths = get_project_paths(tmp_path)
    settings = {
        "compute_mode": "vulkan",
        "vulkan": {"engine": "gigaam"},
        "diarization": {"enabled": False, "gigaam_chunk_seconds": 45},
        "pipeline": {"chunk_seconds": 600},
    }
    assert _effective_chunk_seconds(paths, settings, settings["pipeline"]) == 600.0
    settings["diarization"]["enabled"] = True
    assert _effective_chunk_seconds(paths, settings, settings["pipeline"]) == 45.0
