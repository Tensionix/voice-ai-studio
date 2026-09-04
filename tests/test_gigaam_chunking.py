"""GigaAM file transcription must be fed short chunks on every backend.

GigaAM is trained on utterances of tens of seconds; DirectML additionally
rejects the 600 s default chunk outright (error 80070057). The orchestrator
therefore caps the chunk length for the GigaAM engine regardless of
diarization. This regression test guards the helper the cap depends on."""

from __future__ import annotations

from system_core.core.paths import get_project_paths
from system_core.pipeline.orchestrator import _effective_chunk_seconds
from system_core.providers import registry


def _settings(**overrides):
    settings = {
        "compute_mode": "vulkan",
        "vulkan": {"engine": "gigaam", "model": "gigaam-v3-e2e-rnnt", "backend": "directml"},
        "diarization": {"enabled": False, "gigaam_chunk_seconds": 45},
        "pipeline": {"chunk_seconds": 600},
    }
    settings.update(overrides)
    return settings


def test_registry_resolves_explicit_compute_mode(tmp_path):
    paths = get_project_paths(tmp_path)
    assert registry.resolved_compute_mode(paths, _settings()) == registry.MODE_VULKAN
    assert registry.resolved_compute_mode(paths, _settings(compute_mode="api")) == registry.MODE_API


def test_gigaam_keeps_pipeline_chunk_without_diarization(tmp_path):
    # The provider itself cuts each chunk into <=25 s pieces.
    paths = get_project_paths(tmp_path)
    settings = _settings()
    assert _effective_chunk_seconds(paths, settings, settings["pipeline"]) == 600.0


def test_gigaam_diarization_caps_chunk(tmp_path):
    paths = get_project_paths(tmp_path)
    settings = _settings(diarization={"enabled": True, "gigaam_chunk_seconds": 45})
    assert _effective_chunk_seconds(paths, settings, settings["pipeline"]) == 45.0
    settings = _settings(diarization={"enabled": True, "gigaam_chunk_seconds": 45}, pipeline={"chunk_seconds": 30})
    assert _effective_chunk_seconds(paths, settings, settings["pipeline"]) == 30.0


def test_whispercpp_keeps_pipeline_chunk(tmp_path):
    paths = get_project_paths(tmp_path)
    settings = _settings(vulkan={"engine": "whispercpp", "model": "turbo", "backend": "auto"})
    assert _effective_chunk_seconds(paths, settings, settings["pipeline"]) == 600.0
