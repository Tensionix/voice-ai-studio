from system_core.core.paths import get_project_paths
from system_core.providers.registry import get_transcription_provider


def _settings(batched: bool) -> dict:
    return {
        "compute_mode": "cuda",
        "local": {
            "model": "large-v2",
            "compute_type": "float16",
            "batched": batched,
            "batch_size": 16,
        },
    }


def test_cuda_file_mode_uses_whispercpp_large_v2_cublas():
    provider = get_transcription_provider(get_project_paths(), _settings(False))

    assert provider.name == "whisper.cpp"
    assert provider.model == "large-v2"
    assert provider.backend == "cublas"


def test_legacy_batched_flag_does_not_replace_resident_cuda_backend():
    provider = get_transcription_provider(get_project_paths(), _settings(True))

    assert provider.name == "whisper.cpp"
    assert provider.model == "large-v2"
    assert provider.backend == "cublas"
