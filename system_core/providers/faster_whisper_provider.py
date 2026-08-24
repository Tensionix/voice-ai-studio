"""Local faster-whisper provider — CPU/GPU modes (TZ decision 2).

Opt-in: the base env ships without faster-whisper. Installed via
install/Install-Whisper-Local.cmd. CTranslate2 backend (no torch). VAD filter
on by default to cut hallucinations on silence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..core.models import Segment
from ..core.paths import ProjectPaths
from .base import TranscriptionOptions, TranscriptionProvider, TranscriptResult

DEFAULT_LOCAL_MODEL = "large-v2"  # safe default for Russian (TZ discussion)
REMOVED_LOCAL_MODELS = {"large-v3"}


class FasterWhisperProvider(TranscriptionProvider):
    name = "faster-whisper"

    def __init__(
        self,
        paths: ProjectPaths,
        model: Optional[str] = None,
        *,
        device: str = "cpu",
        compute_type: Optional[str] = None,
        batched: bool = False,
        batch_size: int = 16,
    ) -> None:
        self.paths = paths
        model_value = str(model or DEFAULT_LOCAL_MODEL).strip()
        if model_value.lower() in REMOVED_LOCAL_MODELS:
            model_value = DEFAULT_LOCAL_MODEL
        self.model = model_value
        self.device = device
        self.compute_type = compute_type or ("float16" if device == "cuda" else "int8")
        self.batched = bool(batched)
        self.batch_size = max(1, int(batch_size or 16))
        self._engine = None
        self._batched_engine = None

    def _get_engine(self):
        if self._engine is None:
            try:
                from faster_whisper import WhisperModel  # type: ignore
            except Exception as exc:  # pragma: no cover - optional dependency
                raise RuntimeError(
                    "faster-whisper is not installed. Run install/Install-Whisper-Local.cmd."
                ) from exc
            self._engine = WhisperModel(
                self.model,
                device=self.device,
                compute_type=self.compute_type,
                download_root=str(self.paths.models),
            )
        return self._engine

    def _get_batched_engine(self):
        if self._batched_engine is None:
            try:
                from faster_whisper import BatchedInferencePipeline  # type: ignore
            except Exception:  # pragma: no cover - optional dependency version guard
                return None
            self._batched_engine = BatchedInferencePipeline(model=self._get_engine())
        return self._batched_engine

    def transcribe(self, audio_path: Path, options: TranscriptionOptions) -> TranscriptResult:
        engine = self._get_engine()
        language = options.language
        if language and language.lower() == "auto":
            language = None

        transcriber = engine
        kwargs = {
            "language": language,
            "vad_filter": options.vad_filter,
            "temperature": options.temperature,
            "without_timestamps": False,
        }
        if self.batched and self.device == "cuda" and options.vad_filter:
            batched_engine = self._get_batched_engine()
            if batched_engine is not None:
                transcriber = batched_engine
                kwargs["batch_size"] = self.batch_size

        segments_iter, info = transcriber.transcribe(str(audio_path), **kwargs)
        segments: list[Segment] = []
        for idx, seg in enumerate(segments_iter):
            text = str(seg.text).strip()
            if not text:
                continue
            segments.append(
                Segment(index=idx, start=float(seg.start), end=float(seg.end), text=text)
            )

        return TranscriptResult(
            segments=segments,
            language=getattr(info, "language", language),
            model=self.model,
            provider=self.name,
            diarization=False,
        )
