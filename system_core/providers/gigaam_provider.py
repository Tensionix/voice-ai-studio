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

try:  # numpy arrives with the GigaAM ONNX pack; the provider module itself must import without it.
    import numpy as np
except ImportError:  # pragma: no cover - base runtime without local models
    np = None  # type: ignore[assignment]

from ..core.local_hardware import recommended_gigaam_backend
from ..core.model_assets import GIGAAM_VAD_MODEL, gigaam_vad_available
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
_VAD_CACHE: dict[str, object] = {}

# Audio at least this long is cut into pieces before recognition; Live
# utterances and short clips go to the model whole.
SPLIT_MIN_SECONDS = 15.0
# GigaAM is trained on short utterances and DirectML rejects very long inputs
# (error 80070057), so every piece stays within this range. The cut lands on
# the quietest frame of the window: Silero VAD speech probability when the VAD
# payload is available, signal energy otherwise. Nothing is discarded: quiet,
# echoing speech that a VAD would drop still reaches the model.
PIECE_MIN_SECONDS = 12.0
PIECE_MAX_SECONDS = 25.0
# Silero frame: 512 samples (32 ms) with a 64-sample context at 16 kHz.
_VAD_HOP = 512
_VAD_CONTEXT = 64
_RECOGNIZE_BATCH = 8


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


def _wav_duration(path: Path) -> float:
    try:
        import wave

        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate() or 0
            return handle.getnframes() / float(rate) if rate else 0.0
    except Exception:
        return 0.0


def _read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    """Load a 16-bit PCM WAV (the pipeline's own format) as float32 mono."""
    import wave

    with wave.open(str(path), "rb") as handle:
        if handle.getsampwidth() != 2:
            raise ValueError(f"unsupported WAV sample width: {handle.getsampwidth()}")
        rate = handle.getframerate()
        channels = handle.getnchannels()
        data = np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data.astype(np.float32) / 32768.0, rate


def _energy_scores(pcm: np.ndarray, hop: int) -> np.ndarray:
    """Per-frame RMS energy: the fallback 'how quiet is it here' score."""
    frames = len(pcm) // hop
    if frames <= 0:
        return np.zeros(1, dtype=np.float32)
    window = pcm[: frames * hop].reshape(frames, hop)
    return np.sqrt(np.mean(window * window, axis=1)).astype(np.float32)


def _cut_points(scores: np.ndarray, hop: int, rate: int, total: int) -> list[tuple[int, int]]:
    """Split [0, total) samples into pieces of PIECE_MIN..PIECE_MAX seconds.

    Each cut is placed on the lowest-scoring frame inside the allowed window,
    i.e. the quietest moment, so words are not chopped in the middle."""
    min_len = int(PIECE_MIN_SECONDS * rate)
    max_len = int(PIECE_MAX_SECONDS * rate)
    pieces: list[tuple[int, int]] = []
    start = 0
    while total - start > max_len:
        lo = (start + min_len) // hop
        hi = (start + max_len) // hop
        window = scores[lo:hi]
        if len(window) == 0:
            cut = start + max_len
        else:
            cut = (lo + int(np.argmin(window))) * hop
        if cut <= start:
            cut = start + max_len
        pieces.append((start, cut))
        start = cut
    pieces.append((start, total))
    return pieces


def _load_cached_vad(paths: ProjectPaths):
    """Silero VAD is tiny; it always runs on CPU next to the GigaAM session."""
    key = str(paths.root)
    with _MODEL_CACHE_LOCK:
        cached = _VAD_CACHE.get(key)
        if cached is not None:
            return cached
        _configure_gigaam_cache(paths)
        onnx_asr = _load_onnx_asr()
        vad = onnx_asr.load_vad(GIGAAM_VAD_MODEL, providers=["CPUExecutionProvider"])
        _VAD_CACHE[key] = vad
        return vad


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

    def _speech_scores(self, pcm: np.ndarray, rate: int, hop: int) -> np.ndarray | None:
        """Per-frame speech probability from Silero VAD; None when unavailable."""
        try:
            vad = _load_cached_vad(self.paths)
            context = _VAD_CONTEXT if rate == 16_000 else _VAD_CONTEXT // 2
            probs = [float(frame[0]) for frame in vad._encode(pcm[np.newaxis, :], rate, hop, context)]
        except Exception:
            return None
        if not probs:
            return None
        return np.asarray(probs, dtype=np.float32)

    def _transcribe_in_pieces(self, model, audio_path: Path) -> list[Segment] | None:
        """Cut the WAV into <=25 s pieces at the quietest points and recognise them all.

        Returns None only when the WAV cannot be read as 16-bit PCM; the
        caller then falls back to whole-file recognition."""
        if np is None:
            return None
        try:
            pcm, rate = _read_wav_mono(audio_path)
        except Exception:
            return None
        hop = _VAD_HOP if rate == 16_000 else _VAD_HOP // 2
        scores = self._speech_scores(pcm, rate, hop)
        if scores is None:
            scores = _energy_scores(pcm, hop)
        pieces = _cut_points(scores, hop, rate, len(pcm))

        texts: list[str] = []
        for offset in range(0, len(pieces), _RECOGNIZE_BATCH):
            batch = pieces[offset : offset + _RECOGNIZE_BATCH]
            results = model.recognize([pcm[start:end] for start, end in batch], sample_rate=rate)
            texts.extend(str(item or "") for item in results)

        segments: list[Segment] = []
        for (start, end), text in zip(pieces, texts):
            text = text.strip()
            if not text:
                continue
            segments.append(
                Segment(index=len(segments), start=start / rate, end=end / rate, text=text)
            )
        return segments

    def transcribe(self, audio_path: Path, options: TranscriptionOptions) -> TranscriptResult:
        model = self._get_model()
        duration = _wav_duration(audio_path)
        segments: list[Segment] | None = None
        if options.vad_filter and duration >= SPLIT_MIN_SECONDS:
            segments = self._transcribe_in_pieces(model, audio_path)
        if segments is None:
            text = str(model.recognize(str(audio_path)) or "").strip()
            # GigaAM returns chunk-level text without timestamps; the segment
            # must still span the whole WAV so timeline merging and the Studio
            # diarization pass can place it.
            segments = [Segment(index=0, start=0.0, end=duration, text=text)] if text else []
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
