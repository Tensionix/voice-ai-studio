"""Streaming Live STT providers for non-OpenAI cloud APIs.

They use the same `LiveTranscriber` contract as OpenAI Realtime: stream PCM
frames while the mic is open, emit partial text for the overlay, and emit
committed segments for hands-free incremental paste.
"""

from __future__ import annotations

import base64
import json
import re
import threading
from typing import Callable, Optional
from urllib.parse import urlencode

from ..core.credentials import require_api_key
from ..core.paths import ProjectPaths
from .base import LiveChunkResult, LiveOptions, LiveTranscriber


def _json(text: object) -> dict:
    if isinstance(text, dict):
        return text
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise RuntimeError("Realtime STT returned invalid UTF-8") from exc
    try:
        payload = json.loads(str(text))
    except Exception as exc:
        raise RuntimeError("Realtime STT returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Realtime STT returned a non-object JSON event")
    return payload


def _event_type(payload: dict) -> str:
    return str(
        payload.get("type")
        or payload.get("message_type")
        or payload.get("event")
        or ""
    ).strip()


def _text(payload: dict) -> str:
    value = payload.get("text")
    if value is None:
        value = payload.get("transcript")
    if isinstance(value, dict):
        value = value.get("text")
    return str(value or "").strip()


class _StreamingWebSocketTranscriber(LiveTranscriber):
    streaming = True

    def __init__(
        self,
        paths: ProjectPaths,
        *,
        model: str,
        connection_factory: Optional[Callable[[LiveOptions], object]] = None,
        final_timeout: float = 12.0,
        ready_timeout: float = 10.0,
    ) -> None:
        self.paths = paths
        self.model = model
        self._connection_factory = connection_factory
        self._final_timeout = final_timeout
        self._ready_timeout = ready_timeout
        self._conn = None
        self._reader: threading.Thread | None = None
        self._rate = 16000
        self._lock = threading.Lock()
        self._committed: list[str] = []
        self._partial = ""
        self._error: str | None = None
        self._ready = threading.Event()
        self._done = threading.Event()

    def _joined(self) -> str:
        parts = [p for p in (*self._committed, self._partial) if p.strip()]
        return " ".join(part.strip() for part in parts).strip()

    def _emit_partial(self) -> None:
        if not self.on_partial:
            return
        with self._lock:
            text = self._joined()
        if text:
            try:
                self.on_partial(text)
            except Exception:
                pass

    def _commit(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        with self._lock:
            if not self._committed or self._committed[-1] != text:
                self._committed.append(text)
            self._partial = ""
        if self.on_segment:
            try:
                self.on_segment(text)
            except Exception:
                pass
        self._emit_partial()

    def start(self, options: LiveOptions) -> None:
        self._rate = options.sample_rate or 16000
        with self._lock:
            self._committed = []
            self._partial = ""
            self._error = None
        self._ready.clear()
        self._done.clear()
        factory = self._connection_factory or self._default_factory
        self._conn = factory(options)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        if not self._ready.wait(timeout=self._ready_timeout):
            with self._lock:
                error = self._error
            self.close()
            detail = f": {error}" if error else ""
            raise RuntimeError(f"Realtime STT did not become ready within {self._ready_timeout:g}s{detail}")
        with self._lock:
            error = self._error
        if error:
            self.close()
            raise RuntimeError(f"Realtime STT failed before ready: {error}")

    def _default_factory(self, options: LiveOptions):
        raise NotImplementedError

    def _read_loop(self) -> None:
        conn = self._conn
        try:
            while conn is not None:
                raw = conn.recv()
                if raw is None:
                    break
                payload = _json(raw)
                self._handle_event(payload)
                if self._done.is_set():
                    break
        except Exception as exc:
            with self._lock:
                if self._error is None:
                    self._error = str(exc).strip() or exc.__class__.__name__
            self._done.set()
            self._ready.set()
        finally:
            if not self._ready.is_set():
                with self._lock:
                    if self._error is None:
                        self._error = "connection closed before the ready event"
                self._ready.set()

    def _handle_event(self, payload: dict) -> None:
        raise NotImplementedError

    def _send(self, payload) -> None:
        conn = self._conn
        if conn is None:
            return
        try:
            conn.send(payload)
        except Exception as exc:
            with self._lock:
                if self._error is None:
                    self._error = str(exc).strip() or exc.__class__.__name__
            self._done.set()

    def feed(self, pcm: bytes) -> Optional[LiveChunkResult]:
        if pcm:
            self._send_audio(pcm, commit=False)
        with self._lock:
            partial = self._joined()
        return LiveChunkResult(partial, False) if partial else None

    def _send_audio(self, pcm: bytes, *, commit: bool) -> None:
        raise NotImplementedError

    def finish(self) -> LiveChunkResult:
        self._finish_audio()
        self._done.wait(timeout=self._final_timeout)
        with self._lock:
            text = self._joined()
            err = self._error if not text else None
        self.close()
        if err:
            raise RuntimeError(err)
        return LiveChunkResult(text, True)

    def _finish_audio(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        self._done.set()


class XAIRealtimeLiveTranscriber(_StreamingWebSocketTranscriber):
    name = "xai-realtime"

    def __init__(
        self,
        paths: ProjectPaths,
        *,
        model: str | None = None,
        connection_factory: Optional[Callable[[LiveOptions], object]] = None,
        final_timeout: float = 12.0,
        ready_timeout: float = 10.0,
    ) -> None:
        super().__init__(
            paths,
            model=model or "grok-transcribe",
            connection_factory=connection_factory,
            final_timeout=final_timeout,
            ready_timeout=ready_timeout,
        )
        self._utterance_chunks: list[str] = []

    @staticmethod
    def _novel_suffix(previous: str, cumulative: str) -> str:
        """Return only text not already represented by a cumulative final.

        xAI chunk finals are locked portions, while speech_final/transcript.done
        may contain a stitched utterance or full-session transcript. Compare by
        words so punctuation/case normalization does not cause a full re-paste.
        """
        previous = " ".join((previous or "").split())
        cumulative = " ".join((cumulative or "").split())
        if not cumulative:
            return ""
        if not previous:
            return cumulative
        prev_words = [m.group(0).casefold() for m in re.finditer(r"\w+", previous)]
        curr_matches = list(re.finditer(r"\w+", cumulative))
        curr_words = [m.group(0).casefold() for m in curr_matches]
        if not prev_words or not curr_words:
            return "" if cumulative == previous else cumulative
        if curr_words == prev_words or (
            len(curr_words) <= len(prev_words) and prev_words[-len(curr_words):] == curr_words
        ):
            return ""
        if len(curr_words) >= len(prev_words) and curr_words[: len(prev_words)] == prev_words:
            end = curr_matches[len(prev_words) - 1].end()
            return cumulative[end:].lstrip(" \t,.;:—-")
        # This event is a new non-cumulative chunk rather than a stitched final.
        return cumulative

    def _commit_cumulative(self, text: str, previous: str) -> None:
        suffix = self._novel_suffix(previous, text)
        if suffix:
            self._commit(suffix)
        else:
            with self._lock:
                self._partial = ""

    def _default_factory(self, options: LiveOptions):
        try:
            from websockets.sync.client import connect
        except Exception as exc:
            raise RuntimeError(
                "xAI realtime needs the 'websockets' package — run install/Install-Live-Deps.cmd"
            ) from exc

        params: dict[str, object] = {
            "sample_rate": str(options.sample_rate or 16000),
            "encoding": "pcm",
            "diarize": "true",
            "interim_results": "true",
            "smart_turn": "0.65",
            "smart_turn_timeout": "3000",
        }
        if options.language and options.language != "auto":
            params["language"] = options.language
        if options.keyterms:
            params["keyterm"] = list(options.keyterms[:100])
        uri = f"wss://api.x.ai/v1/stt?{urlencode(params, doseq=True)}"
        return connect(
            uri,
            additional_headers={"Authorization": f"Bearer {require_api_key(self.paths, 'xai')}"},
            open_timeout=15,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10,
        )

    def _handle_event(self, payload: dict) -> None:
        event = _event_type(payload)
        if event == "transcript.created":
            self._ready.set()
            return
        if event == "transcript.partial":
            text = _text(payload)
            is_final = bool(payload.get("is_final"))
            speech_final = bool(payload.get("speech_final"))
            if is_final:
                if speech_final:
                    previous = " ".join(self._utterance_chunks)
                    self._commit_cumulative(text, previous)
                    self._utterance_chunks = []
                else:
                    self._commit(text)
                    if text:
                        self._utterance_chunks.append(text)
            else:
                with self._lock:
                    self._partial = text
                self._emit_partial()
            return
        if event in {"chunk_final", "transcript.chunk_final"}:
            self._commit(_text(payload))
            return
        if event in {"speech_final", "transcript.speech_final"}:
            self._commit_cumulative(_text(payload), " ".join(self._utterance_chunks))
            self._utterance_chunks = []
            return
        if event == "transcript.done":
            with self._lock:
                previous = " ".join(self._committed)
            self._commit_cumulative(_text(payload), previous)
            self._done.set()
            return
        if event == "error":
            with self._lock:
                self._error = str(payload.get("message") or payload.get("error") or "xAI STT failed")
            self._done.set()

    def _send_audio(self, pcm: bytes, *, commit: bool) -> None:
        self._send(pcm)

    def _finish_audio(self) -> None:
        self._send(json.dumps({"type": "audio.done"}))


class ElevenLabsRealtimeLiveTranscriber(_StreamingWebSocketTranscriber):
    name = "elevenlabs-realtime"

    def __init__(
        self,
        paths: ProjectPaths,
        *,
        model: str | None = None,
        connection_factory: Optional[Callable[[LiveOptions], object]] = None,
        final_timeout: float = 4.0,
    ) -> None:
        super().__init__(
            paths,
            model=model or "scribe_v2_realtime",
            connection_factory=connection_factory,
            final_timeout=final_timeout,
        )

    def _default_factory(self, options: LiveOptions):
        try:
            from websockets.sync.client import connect
        except Exception as exc:
            raise RuntimeError(
                "ElevenLabs realtime needs the 'websockets' package — run install/Install-Live-Deps.cmd"
            ) from exc

        params: dict[str, object] = {
            "model_id": self.model,
            "audio_format": "pcm_16000",
            "commit_strategy": "vad",
            "include_timestamps": "false",
        }
        if options.language and options.language != "auto":
            params["language_code"] = options.language
        keyterms = [term for term in options.keyterms if len(term) <= 20][:50]
        if keyterms:
            params["keyterms"] = keyterms
        uri = f"wss://api.elevenlabs.io/v1/speech-to-text/realtime?{urlencode(params, doseq=True)}"
        return connect(
            uri,
            additional_headers={"xi-api-key": require_api_key(self.paths, "elevenlabs")},
        )

    def _handle_event(self, payload: dict) -> None:
        event = _event_type(payload)
        if event == "session_started":
            self._ready.set()
            return
        if event == "partial_transcript":
            with self._lock:
                self._partial = _text(payload)
            self._emit_partial()
            return
        if event in {"committed_transcript", "committed_transcript_with_timestamps"}:
            self._commit(_text(payload))
            return
        if "error" in event or event.startswith("scribe_"):
            with self._lock:
                self._error = str(payload.get("message") or payload.get("error") or "ElevenLabs STT failed")
            self._done.set()

    def _send_audio(self, pcm: bytes, *, commit: bool) -> None:
        self._send(
            json.dumps(
                {
                    "message_type": "input_audio_chunk",
                    "audio_base_64": base64.b64encode(pcm).decode("ascii"),
                    "commit": commit,
                    "sample_rate": self._rate,
                }
            )
        )

    def _finish_audio(self) -> None:
        silence = b"\x00\x00" * int(self._rate * 0.45)
        self._send_audio(silence, commit=True)
