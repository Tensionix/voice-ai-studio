"""Wire concrete providers from config + compute mode.

compute_mode:
  api     -> OpenAI transcription (+ native diarization).
  vulkan  -> Local Models: GigaAM/ONNX or portable whisper.cpp
             (+ optional pyannote diarization for file processing).
  cuda    -> resident whisper.cpp/cuBLAS + optional pyannote diarization.
"""

from __future__ import annotations

from typing import Any, Optional

from ..core.editions import MODE_API, MODE_CUDA, MODE_VULKAN
from ..core.paths import ProjectPaths
from .base import DiarizationProvider, PostprocessProvider, TranscriptionProvider

MODE_CPU = "cpu"
MODE_GPU = "gpu"


def _get(settings: dict[str, Any], *keys: str, default: Any = None) -> Any:
    node: Any = settings
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def compute_mode(settings: dict[str, Any]) -> str:
    value = str(_get(settings, "compute_mode", default=MODE_API)).strip().lower()
    if value in {"openai", "cloud"}:
        return MODE_API
    if value == MODE_GPU:
        return MODE_CUDA
    if value in {MODE_API, MODE_VULKAN, MODE_CUDA, MODE_CPU}:
        return value
    return MODE_API


def resolved_compute_mode(paths: ProjectPaths, settings: dict[str, Any]) -> str:
    """Return the effective compute mode, including the local auto detector."""
    raw = str(_get(settings, "compute_mode", default="")).strip().lower()
    if raw == "auto":
        from ..core.local_hardware import recommended_compute_mode

        return recommended_compute_mode(paths, settings)
    return compute_mode(settings)


def assemblyai_enabled(settings: dict[str, Any]) -> bool:
    return bool(_get(settings, "assemblyai", "enabled", default=False))


def stt_provider(settings: dict[str, Any]) -> str:
    """Cloud provider used when compute_mode=api.

    `assemblyai.enabled` is kept as a legacy compatibility switch; new configs
    should use `stt.provider`.
    """
    value = str(_get(settings, "stt", "provider", default="") or "").strip().lower()
    if not value and assemblyai_enabled(settings):
        value = "assemblyai"
    aliases = {
        "": "openai",
        "grok": "xai",
        "x.ai": "xai",
        "giga": "gigachat",
        "sber": "gigachat",
    }
    return aliases.get(value, value)


def default_local_engine(settings: dict[str, Any]) -> str:
    language = str(_get(settings, "ui_language", default="ru") or "ru").strip().lower()
    return "gigaam" if language == "ru" else "whispercpp"


def local_engine(settings: dict[str, Any], *, scope: str = "file") -> str:
    if scope == "live":
        node = _get(settings, "live", "local", default={}) or {}
    else:
        node = _get(settings, "vulkan", default={}) or {}
    value = ""
    if isinstance(node, dict):
        value = str(node.get("engine") or node.get("provider") or "").strip().lower()
    aliases = {
        "giga": "gigaam",
        "giga-am": "gigaam",
        "giga_am": "gigaam",
        "whisper": "whispercpp",
        "whisper.cpp": "whispercpp",
        "vulkan": "whispercpp",
    }
    value = aliases.get(value, value)
    return value if value in {"gigaam", "whispercpp"} else default_local_engine(settings)


def get_transcription_provider(
    paths: ProjectPaths, settings: dict[str, Any]
) -> TranscriptionProvider:
    mode = resolved_compute_mode(paths, settings)

    if mode == MODE_VULKAN:
        vulkan = _get(settings, "vulkan", default={}) or {}
        live_local = _get(settings, "live", "local", default={}) or {}
        model = _get(settings, "local", "model")
        backend = "auto"
        if isinstance(live_local, dict):
            model = live_local.get("model") or model
            backend = live_local.get("backend") or backend
        if isinstance(vulkan, dict):
            model = vulkan.get("model") or model
            backend = vulkan.get("backend") or backend
        if local_engine(settings, scope="file") == "gigaam":
            from .gigaam_provider import DEFAULT_GIGAAM_FILE_MODEL, GigaAMTranscribeProvider

            gigaam_model = str(model or "").strip()
            if not gigaam_model.startswith("gigaam-"):
                gigaam_model = DEFAULT_GIGAAM_FILE_MODEL
            return GigaAMTranscribeProvider(
                paths,
                model=gigaam_model,
                backend=backend,
            )
        from .whispercpp_provider import WhisperCppProvider

        return WhisperCppProvider(
            paths,
            model=model,
            backend=backend,
        )

    if mode == MODE_CUDA:
        from .whispercpp_provider import WhisperCppProvider

        return WhisperCppProvider(
            paths,
            model=_get(settings, "local", "model", default="large-v2") or "large-v2",
            backend="cublas",
        )

    if mode == MODE_CPU:
        from .whispercpp_provider import WhisperCppProvider

        return WhisperCppProvider(
            paths,
            model=_get(settings, "local", "model"),
            backend="cpu",
        )

    # API/cloud file mode. Live providers are selected separately by
    # `live.engine`; this switch is for long file processing.
    provider = stt_provider(settings)
    if provider == "assemblyai":
        from .assemblyai_provider import AssemblyAITranscribeProvider

        return AssemblyAITranscribeProvider(paths)

    if provider == "xai":
        from .cloud_stt import XAITranscribeProvider

        return XAITranscribeProvider(paths)

    if provider == "gemini":
        from .cloud_stt import GeminiTranscribeProvider

        return GeminiTranscribeProvider(
            paths,
            model=_get(settings, "stt", "gemini_model", default="gemini-3.5-flash"),
        )

    if provider == "gigachat":
        from .cloud_stt import GigaChatTranscribeProvider

        return GigaChatTranscribeProvider(
            paths,
            model=_get(settings, "stt", "gigachat_model", default="GigaChat"),
            scope=_get(settings, "stt", "gigachat_scope", default="GIGACHAT_API_PERS"),
            base_url=_get(
                settings,
                "stt",
                "gigachat_base_url",
                default="https://gigachat.devices.sberbank.ru/api/v1",
            ),
            verify_ssl=bool(_get(settings, "stt", "gigachat_verify_ssl", default=False)),
        )

    from .openai_transcribe import OpenAITranscribeProvider

    return OpenAITranscribeProvider(paths, model=_get(settings, "stt", "model"))


def get_postprocess_provider(
    paths: ProjectPaths, settings: dict[str, Any]
) -> PostprocessProvider:
    # Post-processing is always an OpenAI chat model regardless of compute mode.
    from .openai_postprocess import OpenAIPostprocessProvider

    return OpenAIPostprocessProvider(paths, model=_get(settings, "postprocess", "model"))


def get_diarization_provider(
    paths: ProjectPaths, settings: dict[str, Any]
) -> Optional[DiarizationProvider]:
    """Return a SEPARATE diarization step only when the provider needs one.

    API mode diarizes inline (OpenAI diarize model / AssemblyAI utterances), so
    no separate provider is returned there. Studio local file modes can use
    pyannote as a second pass: faster-whisper CUDA and Local Models
    (GigaAM/whisper.cpp) produce timestamps/text, then pyannote assigns speaker
    labels by timeline overlap.
    """
    wants_diarization = bool(_get(settings, "diarization", "enabled", default=False)) or bool(
        _get(settings, "transcription", "diarize", default=False)
    )
    if not wants_diarization:
        return None

    mode = resolved_compute_mode(paths, settings)
    if mode == MODE_API:
        return None
    if mode not in {MODE_CUDA, MODE_VULKAN}:
        return None

    from .diarization_pyannote import PyannoteDiarizationProvider

    return PyannoteDiarizationProvider(paths)
