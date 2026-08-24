"""Streaming Live STT over the OpenAI Realtime API (behind `LiveTranscriber`).

Push-to-talk with *live partials*: PCM frames are streamed to the API as the user
speaks; the server (server-VAD) transcribes incrementally and emits delta events,
which we surface via `on_partial`. Completed segments are accumulated; on release
we return the joined final transcript.

All OpenAI SDK contact is isolated in `realtime_session.py`; here we only drive a
connection object with this minimal shape (so tests inject a fake):
    conn.session ...                          (configured by the factory)
    conn.input_audio_buffer.append(audio=str) (base64 PCM16)
    conn.recv() -> event | None               (blocking; None/raise = closed)
    conn.close()
Events expose `.type` plus `.delta` / `.transcript` / `.error` (attr or dict key).

For server-VAD models we never call `input_audio_buffer.commit()`: the server
commits segments itself. A manual commit races it and errors with "buffer too
small" once the server has already drained the buffer. To finalize a
still-in-progress segment on key-release we append a short tail of silence so
server-VAD detects end-of-speech. `gpt-realtime-whisper` is the exception: the
SDK requires `turn_detection=null`, so that model is finalized with a manual
commit on release.
"""

from __future__ import annotations

import base64
import threading
from typing import Callable, Optional

from ..core.paths import ProjectPaths
from ..providers.base import LiveChunkResult, LiveOptions, LiveTranscriber

_DELTA = "conversation.item.input_audio_transcription.delta"
_COMPLETED = "conversation.item.input_audio_transcription.completed"
_FAILED = "conversation.item.input_audio_transcription.failed"


def _get(event, field: str):
    if isinstance(event, dict):
        return event.get(field)
    return getattr(event, field, None)


class RealtimeLiveTranscriber(LiveTranscriber):
    name = "openai-realtime"
    streaming = True

    def __init__(
        self,
        paths: ProjectPaths,
        *,
        connection_factory: Optional[Callable[[LiveOptions], object]] = None,
        model: Optional[str] = None,
        on_partial: Optional[Callable[[str], None]] = None,
        final_timeout: float = 4.0,
    ):
        self.paths = paths
        self._model = model
        self.on_partial = on_partial
        self._connection_factory = connection_factory
        self._final_timeout = final_timeout

        self._conn = None
        self._rate = 24000               # set from options at start()
        self._reader: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._committed: list[str] = []   # finished segment transcripts
        self._delta = ""                  # in-progress segment text
        self._error: Optional[str] = None
        self._finishing = False
        self._manual_commit = False
        self._post_commit = threading.Event()  # set when a segment/err lands post-commit

    # --- helpers -------------------------------------------------------------
    def _joined(self) -> str:
        parts = [p for p in (self._committed + [self._delta]) if p.strip()]
        return " ".join(s.strip() for s in parts).strip()

    def _emit_partial(self) -> None:
        cb = self.on_partial
        if not cb:
            return
        with self._lock:
            text = self._joined()
        try:
            cb(text)
        except Exception:
            pass

    # --- LiveTranscriber seam ------------------------------------------------
    def start(self, options: LiveOptions) -> None:
        self._rate = options.sample_rate or 24000
        model_id = (self._model or options.model or "").strip().lower()
        self._manual_commit = model_id == "gpt-realtime-whisper"
        with self._lock:
            self._committed = []
            self._delta = ""
            self._error = None
            self._finishing = False
        self._post_commit.clear()

        factory = self._connection_factory or self._default_factory
        self._conn = factory(options)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _default_factory(self, options: LiveOptions):
        from .realtime_session import open_openai_transcription_connection

        return open_openai_transcription_connection(self.paths, options, self._model)

    def _read_loop(self) -> None:
        conn = self._conn
        try:
            while conn is not None:
                event = conn.recv()
                if event is None:
                    break
                etype = _get(event, "type")
                if etype == _DELTA:
                    delta = _get(event, "delta") or ""
                    if delta:
                        with self._lock:
                            self._delta += delta
                        self._emit_partial()
                elif etype == _COMPLETED:
                    transcript = (_get(event, "transcript") or "").strip()
                    with self._lock:
                        if transcript:
                            self._committed.append(transcript)
                        self._delta = ""
                        finishing = self._finishing
                    self._emit_partial()
                    if transcript:
                        cb = self.on_segment
                        if cb:
                            try:
                                cb(transcript)
                            except Exception:
                                pass
                    if finishing:
                        self._post_commit.set()
                elif etype in (_FAILED, "error"):
                    with self._lock:
                        self._error = str(_get(event, "error") or "transcription failed")
                    self._post_commit.set()
                    break
        except Exception as exc:  # connection closed / network
            with self._lock:
                if self._error is None:
                    self._error = str(exc)
            self._post_commit.set()

    def feed(self, pcm: bytes) -> Optional[LiveChunkResult]:
        conn = self._conn
        if pcm and conn is not None:
            try:
                conn.input_audio_buffer.append(audio=base64.b64encode(pcm).decode("ascii"))
            except Exception as exc:
                with self._lock:
                    if self._error is None:
                        self._error = str(exc)
                self._post_commit.set()
        with self._lock:
            partial = self._joined()
        return LiveChunkResult(partial, False) if partial else None

    def finish(self) -> LiveChunkResult:
        conn = self._conn
        with self._lock:
            self._finishing = True
            # A segment is still open (delta in flight) or nothing committed yet.
            pending = bool(self._delta) or not self._committed
        # Server-VAD owns commits for the default models; gpt-realtime-whisper has
        # no VAD and must be manually committed.
        if conn is not None and pending:
            try:
                if self._manual_commit:
                    conn.input_audio_buffer.commit()
                else:
                    silence = b"\x00\x00" * int(self._rate * 0.5)  # ~500ms mono PCM16
                    conn.input_audio_buffer.append(audio=base64.b64encode(silence).decode("ascii"))
            except Exception:
                pass
            self._post_commit.wait(timeout=self._final_timeout)
        with self._lock:
            text = self._joined()
            err = self._error if not text else None
        self.close()
        if err:
            raise RuntimeError(err)
        return LiveChunkResult(text, True)

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        self._post_commit.set()
