"""Live dictation through the portable whisper.cpp local pack.

The mic is captured immediately, PCM is buffered during push-to-talk, then the
utterance is transcribed on release. Two paths sit behind the same seam:

  * **Warm server** (preferred): a resident ``whisper-server`` keeps the model
    in VRAM, so each utterance only pays inference. Warming starts in the
    background on ``start()`` (the model load overlaps with the user speaking);
    ``finish()`` runs on a worker thread and naturally waits for the warm via
    the server lock.
  * **Batch CLI** (fallback): when the pack ships no server binary, or the warm
    path fails, the one-shot ``whisper-cli`` provider is used instead.
"""

from __future__ import annotations

import threading
from typing import Optional

from ..core.paths import ProjectPaths
from ..providers.base import (
    LiveChunkResult,
    LiveOptions,
    LiveTranscriber,
    TranscriptionOptions,
)
from ..providers.whispercpp_provider import DEFAULT_WHISPERCPP_MODEL, WhisperCppProvider
from ..providers.whispercpp_server import WhisperCppServer
from .transcriber import write_wav_pcm16


class WhisperCppLiveTranscriber(LiveTranscriber):
    name = "whisper.cpp-live"
    streaming = False

    def __init__(
        self,
        paths: ProjectPaths,
        *,
        model: str | None = None,
        backend: str = "auto",
        idle_unload_seconds: int = 0,
        provider: Optional[WhisperCppProvider] = None,
        server: Optional[WhisperCppServer] = None,
    ) -> None:
        self.paths = paths
        self.model = model or DEFAULT_WHISPERCPP_MODEL
        self.backend = backend or "auto"
        self.idle_unload_seconds = idle_unload_seconds
        self._provider = provider           # batch fallback (injectable)
        self._server = server               # warm server (injectable)
        self._server_resolved = server is not None
        self._buf = bytearray()
        self._opts: Optional[LiveOptions] = None
        self._warm_thread: Optional[threading.Thread] = None
        self._closed = False

    def _get_server(self) -> Optional[WhisperCppServer]:
        if not self._server_resolved:
            self._server_resolved = True
            candidate = WhisperCppServer(
                self.paths,
                self.model,
                backend=self.backend,
                idle_unload_seconds=self.idle_unload_seconds,
            )
            # No server binary in the pack -> stay on the batch CLI path.
            self._server = candidate if candidate.available() else None
        return self._server

    def _get_provider(self) -> WhisperCppProvider:
        if self._provider is None:
            self._provider = WhisperCppProvider(self.paths, self.model, backend=self.backend)
        return self._provider

    def warm(self) -> None:
        """Pre-load the resident model in the background (called when Live is
        armed, so the first utterance is instant too). Idempotent and silent."""
        if self._closed:
            return
        server = self._get_server()
        if server is None:
            return
        if self._warm_thread is not None and self._warm_thread.is_alive():
            return

        def _warm() -> None:
            try:
                server.ensure_running()
                if self._closed:
                    server.close()
            except Exception:
                pass  # finish() will retry and fall back to batch if needed

        self._warm_thread = threading.Thread(
            target=_warm, name="whispercpp-warm", daemon=True
        )
        self._warm_thread.start()

    def start(self, options: LiveOptions) -> None:
        self._opts = options
        self._buf = bytearray()
        # Warm the resident model (overlaps with the user speaking). If pre-warm
        # on arm already started it, ensure_running() inside is a fast no-op.
        self.warm()

    def feed(self, pcm: bytes) -> Optional[LiveChunkResult]:
        if pcm:
            self._buf.extend(pcm)
        return None

    def finish(self) -> LiveChunkResult:
        opts = self._opts or LiveOptions(model=self.model)
        if not self._buf:
            return LiveChunkResult("", True)

        self.paths.workspace.mkdir(parents=True, exist_ok=True)
        wav = self.paths.workspace / "live_utterance_whispercpp.wav"
        write_wav_pcm16(wav, bytes(self._buf), opts.sample_rate)
        try:
            text = self._transcribe(wav, opts)
            return LiveChunkResult(text, True)
        finally:
            try:
                wav.unlink()
            except Exception:
                pass
            self._buf = bytearray()

    def _transcribe(self, wav, opts: LiveOptions) -> str:
        language = opts.language
        prompt = opts.context
        server = self._get_server()
        if server is not None:
            try:
                return server.transcribe_wav(wav, language=language, prompt=prompt)
            except Exception:
                pass  # warm path failed -> batch CLI for this utterance
        result = self._get_provider().transcribe(
            wav,
            TranscriptionOptions(
                language=language,
                model=opts.model or self.model,
                diarize=False,
                context=prompt,
                vad_filter=opts.vad_filter,
            ),
        )
        return " ".join(s.text.strip() for s in result.segments if s.text.strip()).strip()

    def close(self) -> None:
        self._closed = True
        self._buf = bytearray()
        if self._server is not None:
            try:
                self._server.close()
            except Exception:
                pass
