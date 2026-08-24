"""Local GigaAM v3 provider through onnx-asr / ONNX Runtime.

GigaAM is the Russian-first local path. The UI chooses the model variant
explicitly: CTC for Live, RNN-T for files. Backend "auto" is resolved from the
hardware profile to CUDA/DirectML/CPU where available.
"""

from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Optional

from ..core.local_hardware import recommended_gigaam_backend
from ..core.models import Segment
from ..core.paths import ProjectPaths
from .base import (
    LiveChunkResult,
    LiveOptions,
    LiveTranscriber,
    TranscriptionOptions,
    TranscriptionProvider,
    TranscriptResult,
)

DEFAULT_GIGAAM_FILE_MODEL = "gigaam-v3-e2e-rnnt"
DEFAULT_GIGAAM_LIVE_MODEL = "gigaam-v3-e2e-ctc"

_BACKEND_PROVIDERS: dict[str, list[str]] = {
    "cuda": ["CUDAExecutionProvider", "CPUExecutionProvider"],
    "directml": ["DmlExecutionProvider", "CPUExecutionProvider"],
    "cpu": ["CPUExecutionProvider"],
}
_MODEL_CACHE: dict[tuple[str, str, tuple[str, ...]], object] = {}
_MODEL_CACHE_LOCK = Lock()


def _provider_list(paths: ProjectPaths, backend: str) -> list[str] | None:
    resolved = (backend or "auto").strip().lower()
    if resolved == "auto":
        resolved = recommended_gigaam_backend(paths)
    return _BACKEND_PROVIDERS.get(resolved)


def _load_onnx_asr():
    try:
        import onnx_asr  # type: ignore

        return onnx_asr
    except Exception as exc:
        raise RuntimeError(
            "GigaAM local runtime is not installed. Run install\\Install-GigaAM-ONNX.cmd "
            "or open Settings -> Installations -> GigaAM ONNX pack. It installs "
            "onnx-asr, an ONNX Runtime provider, and preloads the GigaAM v3 payload."
        ) from exc


def _configure_gigaam_cache(paths: ProjectPaths) -> None:
    cache_root = paths.models / "huggingface"
    os.environ.setdefault("HF_HOME", str(cache_root))
    os.environ.setdefault("HF_HUB_CACHE", str(cache_root / "hub"))
    cache_root.mkdir(parents=True, exist_ok=True)


def _preload_cuda_dlls(providers: list[str] | None) -> None:
    if not providers or not any("CUDA" in item for item in providers):
        return
    try:
        import onnxruntime as ort  # type: ignore
    except Exception:
        return
    preload = getattr(ort, "preload_dlls", None)
    if not callable(preload):
        return
    try:
        preload(cuda=True, cudnn=True, msvc=True)
    except Exception:
        pass


def _onnx_asr_load_kwargs(provider_key: tuple[str, ...]) -> dict[str, object]:
    if not provider_key:
        return {}
    return {"providers": list(provider_key)}


def _load_cached_model(paths: ProjectPaths, model: str, providers: list[str] | None):
    """Keep GigaAM warm for the whole process; Python releases it on app exit."""
    provider_key = tuple(providers or ())
    cache_key = (str(paths.root), model, provider_key)
    with _MODEL_CACHE_LOCK:
        cached = _MODEL_CACHE.get(cache_key)
        if cached is not None:
            return cached
        _configure_gigaam_cache(paths)
        _preload_cuda_dlls(providers)
        onnx_asr = _load_onnx_asr()
        loaded = onnx_asr.load_model(model, **_onnx_asr_load_kwargs(provider_key))
        _MODEL_CACHE[cache_key] = loaded
        return loaded


class GigaAMTranscribeProvider(TranscriptionProvider):
    name = "gigaam"

    def __init__(
        self,
        paths: ProjectPaths,
        model: Optional[str] = None,
        *,
        backend: str = "auto",
    ) -> None:
        self.paths = paths
        self.model = model or DEFAULT_GIGAAM_FILE_MODEL
        self.backend = backend or "auto"
        self._model = None
        self._provider_key: tuple[str, ...] = ()

    def _get_model(self):
        if self._model is None:
            providers = _provider_list(self.paths, self.backend)
            try:
                self._model = _load_cached_model(self.paths, self.model, providers)
                self._provider_key = tuple(providers or ())
            except Exception:
                if providers and providers != ["CPUExecutionProvider"]:
                    self._model = _load_cached_model(self.paths, self.model, ["CPUExecutionProvider"])
                    self._provider_key = ("CPUExecutionProvider",)
                else:
                    raise
        return self._model

    def close(self) -> None:
        model = self._model
        if model is not None:
            cache_key = (str(self.paths.root), self.model, self._provider_key)
            with _MODEL_CACHE_LOCK:
                if _MODEL_CACHE.get(cache_key) is model:
                    _MODEL_CACHE.pop(cache_key, None)
        self._model = None
        self._provider_key = ()

    def transcribe(self, audio_path: Path, options: TranscriptionOptions) -> TranscriptResult:
        model = self._get_model()
        text = str(model.recognize(str(audio_path)) or "").strip()
        segments = [Segment(index=0, start=0.0, end=0.0, text=text)] if text else []
        return TranscriptResult(
            segments=segments,
            language=options.language,
            model=self.model,
            provider=self.name,
            diarization=False,
        )


class GigaAMLiveTranscriber(LiveTranscriber):
    name = "gigaam-live"
    streaming = False

    def __init__(
        self,
        paths: ProjectPaths,
        *,
        model: str | None = None,
        backend: str = "auto",
        provider: Optional[GigaAMTranscribeProvider] = None,
    ) -> None:
        self.paths = paths
        self.model = model or DEFAULT_GIGAAM_LIVE_MODEL
        self.backend = backend or "auto"
        self._provider = provider
        self._buf = bytearray()
        self._opts: Optional[LiveOptions] = None
        self._closed = False

    def _get_provider(self) -> GigaAMTranscribeProvider:
        if self._provider is None:
            self._provider = GigaAMTranscribeProvider(self.paths, self.model, backend=self.backend)
        return self._provider

    def start(self, options: LiveOptions) -> None:
        self._opts = options
        self._buf = bytearray()

    def warm(self) -> None:
        if self._closed:
            return
        provider = self._get_provider()
        provider._get_model()
        if self._closed:
            provider.close()

    def feed(self, pcm: bytes) -> Optional[LiveChunkResult]:
        if pcm:
            self._buf.extend(pcm)
        return None

    def finish(self) -> LiveChunkResult:
        opts = self._opts or LiveOptions(model=self.model)
        if not self._buf:
            return LiveChunkResult("", True)

        from ..live.transcriber import write_wav_pcm16

        self.paths.workspace.mkdir(parents=True, exist_ok=True)
        wav = self.paths.workspace / "live_utterance_gigaam.wav"
        write_wav_pcm16(wav, bytes(self._buf), opts.sample_rate)
        try:
            result = self._get_provider().transcribe(
                wav,
                TranscriptionOptions(
                    language=opts.language,
                    model=opts.model or self.model,
                    diarize=False,
                    context=opts.context,
                    vad_filter=opts.vad_filter,
                ),
            )
            text = " ".join(segment.text for segment in result.segments).strip()
            return LiveChunkResult(text, True)
        finally:
            try:
                wav.unlink()
            except Exception:
                pass
            self._buf = bytearray()

    def close(self) -> None:
        self._closed = True
        self._buf = bytearray()
        if self._provider is not None:
            self._provider.close()
