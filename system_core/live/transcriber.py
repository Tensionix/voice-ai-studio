"""Live STT implementations behind `providers.base.LiveTranscriber`.

`BatchLiveTranscriber` is the first, pragmatic implementation: it buffers the
whole push-to-talk utterance and transcribes it in one request on release,
reusing the tested OpenAI file-transcription path. A true streaming Realtime-WS
provider (live partials) drops in behind the same seam later — the controller
doesn't change.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Optional

from ..core.paths import ProjectPaths
from ..providers.base import (
    LiveChunkResult,
    LiveOptions,
    LiveTranscriber,
    TranscriptionOptions,
)


def write_wav_pcm16(path: Path, pcm: bytes, sample_rate: int, channels: int = 1) -> None:
    """Write raw PCM16 bytes as a WAV file (mono 16-bit by default)."""
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)  # 16-bit
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)


class BatchLiveTranscriber(LiveTranscriber):
    name = "openai-batch"

    def __init__(self, paths: ProjectPaths, provider=None):
        self.paths = paths
        self._provider = provider  # injectable for tests
        self._buf = bytearray()
        self._opts: Optional[LiveOptions] = None

    def _get_provider(self):
        if self._provider is None:
            from ..providers.openai_transcribe import OpenAITranscribeProvider

            self._provider = OpenAITranscribeProvider(self.paths)
        return self._provider

    # --- LiveTranscriber seam ------------------------------------------------
    def start(self, options: LiveOptions) -> None:
        self._opts = options
        self._buf = bytearray()

    def feed(self, pcm: bytes) -> Optional[LiveChunkResult]:
        if pcm:
            self._buf.extend(pcm)
        return None  # batch mode has no partials

    def finish(self) -> LiveChunkResult:
        opts = self._opts or LiveOptions()
        if not self._buf:
            return LiveChunkResult("", True)

        self.paths.workspace.mkdir(parents=True, exist_ok=True)
        wav = self.paths.workspace / "live_utterance.wav"
        write_wav_pcm16(wav, bytes(self._buf), opts.sample_rate)
        try:
            result = self._get_provider().transcribe(
                wav,
                TranscriptionOptions(
                    language=opts.language,
                    model=opts.model or None,
                    diarize=False,
                    context=opts.context,
                    vad_filter=opts.vad_filter,
                ),
            )
            text = " ".join(s.text.strip() for s in result.segments if s.text.strip()).strip()
            return LiveChunkResult(text, True)
        finally:
            try:
                wav.unlink()
            except Exception:
                pass
            self._buf = bytearray()

    def close(self) -> None:
        self._buf = bytearray()
