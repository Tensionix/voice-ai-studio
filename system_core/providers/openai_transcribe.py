"""OpenAI transcription provider (TZ sections 6-7, 11).

Timestamp/diarization support differs by model, which dictates response_format:
  - `*-transcribe-diarize`  -> diarized_json (segment timestamps + speakers;
    verbose_json is NOT accepted by this model). Used even when diarize=False,
    because it is the only way to get per-segment timestamps from this model.
  - `whisper-1`             -> verbose_json (segment timestamps, no speakers).
  - `gpt-4o-transcribe`/mini -> json only (no timestamps -> one segment spanning
    the chunk; fine for plain transcript/summary, poor for subtitles).
Operates on ONE chunk file; the orchestrator applies the chunk's time offset.
"""

from __future__ import annotations

import time
import wave
from pathlib import Path
from typing import Any, Optional

from ..core.credentials import require_api_key
from ..core.models import Segment
from ..core.paths import ProjectPaths
from .base import TranscriptionOptions, TranscriptionProvider, TranscriptResult
from ._openai_common import is_rate_limited, is_timeout_or_transient, is_transport_error, _sleep_for
from .model_catalog import DEFAULT_DIARIZE_STT_MODEL, DEFAULT_STT_MODEL

DIARIZE_MODEL_MARKER = "diarize"


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _segment_field(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _wav_duration(path: Path) -> float:
    """Duration of a PCM WAV chunk (used as a fallback cue length)."""
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate() or 1
            return frames / float(rate)
    except Exception:
        return 0.0


def _normalize_speaker(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    label = str(raw).strip()
    if not label:
        return None
    return label if label.lower().startswith("speaker") else f"Speaker {label}"


def _parse_segments(resp: Any, fallback_duration: float, *, keep_speakers: bool) -> list[Segment]:
    """Turn an OpenAI transcription response into chunk-relative Segments."""
    raw_segments = _segment_field(resp, "segments", None)
    segments: list[Segment] = []
    if raw_segments:
        for idx, item in enumerate(raw_segments):
            text = str(_segment_field(item, "text", "") or "").strip()
            if not text:
                continue
            speaker = _normalize_speaker(_segment_field(item, "speaker", None)) if keep_speakers else None
            segments.append(
                Segment(
                    index=idx,
                    start=_coerce_float(_segment_field(item, "start", 0.0)),
                    end=_coerce_float(_segment_field(item, "end", 0.0)),
                    speaker=speaker,
                    text=text,
                )
            )
    if segments:
        return segments

    # Plain json/text response: one segment spanning the whole chunk.
    text = str(_segment_field(resp, "text", "") or "").strip()
    if text:
        return [Segment(index=0, start=0.0, end=fallback_duration, text=text)]
    return []


class OpenAITranscribeProvider(TranscriptionProvider):
    name = "openai"

    def __init__(self, paths: ProjectPaths, model: Optional[str] = None) -> None:
        self.paths = paths
        self.model = model or DEFAULT_STT_MODEL
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI  # type: ignore

            self._client = OpenAI(api_key=require_api_key(self.paths, "openai"))
        return self._client

    def _response_format(self, model: str) -> str:
        """Pick the richest response_format each model actually supports."""
        mid = model.lower()
        if DIARIZE_MODEL_MARKER in mid:
            return "diarized_json"   # only timestamps source for this model
        if "whisper" in mid:
            return "verbose_json"    # segment timestamps, no speakers
        return "json"               # gpt-4o-transcribe(-mini): text only

    def transcribe(self, audio_path: Path, options: TranscriptionOptions) -> TranscriptResult:
        model = options.model or self.model
        if options.diarize and DIARIZE_MODEL_MARKER not in model.lower():
            model = DEFAULT_DIARIZE_STT_MODEL
        response_format = self._response_format(model)
        language = options.language
        if language and language.lower() == "auto":
            language = None
        fallback_duration = _wav_duration(audio_path)

        client = self._get_client()
        max_retries = 4
        last_exc: Optional[Exception] = None

        for attempt in range(1, max_retries + 1):
            try:
                with audio_path.open("rb") as handle:
                    kwargs: dict[str, Any] = {
                        "model": model,
                        "file": handle,
                        "response_format": response_format,
                    }
                    if response_format == "verbose_json":
                        kwargs["timestamp_granularities"] = ["segment"]
                    if language:
                        kwargs["language"] = language
                    prompt = options.prompt_context()
                    if prompt and response_format != "diarized_json":
                        kwargs["prompt"] = prompt
                    try:
                        resp = client.audio.transcriptions.create(**kwargs)
                    except Exception as fmt_exc:
                        # Model rejected the format; retry with plain json (no timestamps).
                        msg = str(fmt_exc).lower()
                        if "response_format" in msg or "verbose" in msg or "diarized" in msg:
                            kwargs["response_format"] = "json"
                            kwargs.pop("timestamp_granularities", None)
                            handle.seek(0)
                            resp = client.audio.transcriptions.create(**kwargs)
                        else:
                            raise

                segments = _parse_segments(
                    resp, fallback_duration=fallback_duration, keep_speakers=options.diarize
                )
                detected_language = _segment_field(resp, "language", None) or language
                diarized = options.diarize and any(seg.speaker for seg in segments)
                return TranscriptResult(
                    segments=segments,
                    language=str(detected_language) if detected_language else None,
                    model=model,
                    provider=self.name,
                    diarization=diarized,
                )

            except Exception as exc:
                last_exc = exc
                msg = str(exc).lower()
                retryable = (
                    is_timeout_or_transient(msg) or is_rate_limited(msg) or is_transport_error(exc)
                )
                if attempt < max_retries:
                    time.sleep(_sleep_for(attempt, msg) if retryable else min(12.0, 2.0 * attempt))
                    continue
                raise RuntimeError(f"OpenAI transcription failed: {exc}") from exc

        raise RuntimeError(f"OpenAI transcription failed: {last_exc}")
