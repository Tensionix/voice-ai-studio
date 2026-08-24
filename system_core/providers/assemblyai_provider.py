"""AssemblyAI provider — optional diarization / long-audio route (TZ decision 4).

Enabled by a checkbox; not part of the base flow (OpenAI diarizes natively).
Implemented behind a lazy import so the base env needs no AssemblyAI SDK.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..core.credentials import require_api_key
from ..core.models import Segment
from ..core.paths import ProjectPaths
from .base import TranscriptionOptions, TranscriptionProvider, TranscriptResult


class AssemblyAITranscribeProvider(TranscriptionProvider):
    name = "assemblyai"

    def __init__(self, paths: ProjectPaths, model: Optional[str] = None) -> None:
        self.paths = paths
        self.model = model

    def transcribe(self, audio_path: Path, options: TranscriptionOptions) -> TranscriptResult:
        try:
            import assemblyai as aai  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "AssemblyAI is enabled but its SDK is not installed. "
                "Run install/Install-AssemblyAI.cmd (or pip install assemblyai)."
            ) from exc

        aai.settings.api_key = require_api_key(self.paths, "assemblyai")
        config = aai.TranscriptionConfig(
            speaker_labels=options.diarize,
            language_code=None if (options.language or "auto") == "auto" else options.language,
        )
        transcript = aai.Transcriber().transcribe(str(audio_path), config=config)
        if getattr(transcript, "status", None) == "error":
            raise RuntimeError(f"AssemblyAI error: {getattr(transcript, 'error', 'unknown')}")

        segments: list[Segment] = []
        utterances = getattr(transcript, "utterances", None) or []
        for idx, utt in enumerate(utterances):
            segments.append(
                Segment(
                    index=idx,
                    start=float(getattr(utt, "start", 0)) / 1000.0,
                    end=float(getattr(utt, "end", 0)) / 1000.0,
                    speaker=f"Speaker {getattr(utt, 'speaker', '?')}",
                    text=str(getattr(utt, "text", "")).strip(),
                )
            )
        if not segments and getattr(transcript, "text", None):
            segments.append(Segment(index=0, start=0.0, end=0.0, text=str(transcript.text).strip()))

        return TranscriptResult(
            segments=segments,
            language=options.language,
            model="assemblyai",
            provider=self.name,
            diarization=options.diarize,
        )
