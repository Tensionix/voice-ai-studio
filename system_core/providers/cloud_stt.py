"""Cloud STT providers beyond OpenAI.

These providers are intentionally file/batch oriented. Live streaming has a
different contract and should be wired through `LiveTranscriber` implementations
so the GUI can present Live and Files as separate choices.
"""

from __future__ import annotations

import json
import mimetypes
import random
import re
import time
import uuid
import wave
from pathlib import Path
from typing import Any, Optional

import httpx

from ..core.credentials import require_api_key
from ..core.models import Segment
from ..core.paths import ProjectPaths
from .base import TranscriptionOptions, TranscriptionProvider, TranscriptResult


def _language(options: TranscriptionOptions) -> Optional[str]:
    value = (options.language or "").strip()
    if not value or value.lower() == "auto":
        return None
    return value


def _mime_type(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        return mime
    suffix = path.suffix.lower()
    if suffix == ".wav":
        return "audio/wav"
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix == ".m4a":
        return "audio/mp4"
    if suffix == ".flac":
        return "audio/flac"
    return "application/octet-stream"


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate() or 1
            return frames / float(rate)
    except Exception:
        return 0.0


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _speaker(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text if text.lower().startswith("speaker") else f"Speaker {text}"


def _whole_segment(text: str, duration: float, *, speaker: Optional[str] = None) -> list[Segment]:
    text = text.strip()
    if not text:
        return []
    return [Segment(index=0, start=0.0, end=duration, speaker=speaker, text=text)]


def _segments_from_words(
    words: list[Any],
    *,
    fallback_text: str,
    fallback_duration: float,
    diarize: bool,
) -> list[Segment]:
    segments: list[Segment] = []
    bucket: list[str] = []
    start: Optional[float] = None
    end = 0.0
    speaker: Optional[str] = None

    def flush() -> None:
        nonlocal bucket, start, end, speaker
        text = " ".join(bucket).strip()
        if text:
            segments.append(
                Segment(
                    index=len(segments),
                    start=start if start is not None else 0.0,
                    end=end or fallback_duration,
                    speaker=speaker if diarize else None,
                    text=text,
                )
            )
        bucket = []
        start = None
        end = 0.0
        speaker = None

    for item in words:
        if not isinstance(item, dict):
            continue
        token = str(item.get("text") or item.get("word") or "").strip()
        if not token:
            continue
        word_start = _float(item.get("start"), start or 0.0)
        word_end = _float(item.get("end"), word_start)
        word_speaker = _speaker(item.get("speaker"))
        speaker_changed = diarize and speaker is not None and word_speaker != speaker
        gap = start is not None and word_start - end > 1.2
        long_bucket = start is not None and word_end - start > 24.0
        if bucket and (speaker_changed or gap or long_bucket):
            flush()
        if start is None:
            start = word_start
            speaker = word_speaker
        end = max(end, word_end)
        bucket.append(token)
        if token.endswith((".", "!", "?")) and len(bucket) >= 12:
            flush()
    flush()

    if segments:
        return segments
    return _whole_segment(fallback_text, fallback_duration)


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text


def _parse_timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    parts = text.split(":")
    try:
        nums = [float(p.replace(",", ".")) for p in parts]
    except ValueError:
        return _float(text)
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    return nums[0]


def _segments_from_generated_text(
    text: str,
    *,
    fallback_duration: float,
    diarize: bool,
) -> tuple[list[Segment], Optional[str]]:
    raw = _strip_json_fence(text)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return _whole_segment(text, fallback_duration), None

    if not isinstance(payload, dict):
        return _whole_segment(text, fallback_duration), None

    language = payload.get("language") or payload.get("language_code")
    raw_segments = payload.get("segments") or payload.get("transcript") or []
    if not isinstance(raw_segments, list):
        return _whole_segment(text, fallback_duration), str(language) if language else None

    segments: list[Segment] = []
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        content = str(item.get("text") or item.get("content") or "").strip()
        if not content:
            continue
        start = _parse_timestamp(item.get("start", item.get("timestamp", 0.0)))
        end = _parse_timestamp(item.get("end", 0.0))
        if end <= start:
            end = min(fallback_duration, start + max(1.0, len(content) / 14.0))
        segments.append(
            Segment(
                index=len(segments),
                start=start,
                end=end,
                speaker=_speaker(item.get("speaker")) if diarize else None,
                text=content,
            )
        )
    return segments or _whole_segment(text, fallback_duration), str(language) if language else None


def _context_terms(context: str) -> list[str]:
    terms: list[str] = []
    for raw in re.split(r"[,;\n]", context or ""):
        term = raw.strip()
        if term and len(term) <= 50 and term not in terms:
            terms.append(term)
        if len(terms) >= 100:
            break
    return terms


DEFAULT_XAI_KEYTERMS = [
    "NiceGUI",
    "PySide6",
    "PowerShell",
    "VapourSynth",
    "shlex",
    "Codex",
    "DaVinci Resolve",
    "FFmpeg",
]


def _dedupe_terms(*groups: list[str]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for term in group:
            key = term.casefold()
            if key not in seen:
                seen.add(key)
                terms.append(term)
    return terms


class XAITranscribeProvider(TranscriptionProvider):
    """xAI `/v1/stt` file transcription provider."""

    name = "xai"
    supports_live = True
    supports_live_diarization = True
    supports_batch_diarization = True
    # Display/profile id for the Audion UI. The native xAI `/v1/stt` request
    # does not send a model field.
    model = "grok-transcribe"

    def __init__(
        self,
        paths: ProjectPaths,
        timeout: float = 300.0,
        keyterms: Optional[list[str]] = None,
        max_attempts: int = 4,
    ) -> None:
        self.paths = paths
        self.timeout = timeout
        self.keyterms = list(DEFAULT_XAI_KEYTERMS if keyterms is None else keyterms)
        self.max_attempts = max(1, int(max_attempts))

    def transcribe(self, audio_path: Path, options: TranscriptionOptions) -> TranscriptResult:
        key = require_api_key(self.paths, "xai")
        fallback_duration = _wav_duration(audio_path)
        fields: list[tuple[str, str]] = []
        language = _language(options)
        if language:
            fields.append(("language", language))
            fields.append(("format", "true"))
        if options.diarize:
            fields.append(("diarize", "true"))
        for term in _dedupe_terms(self.keyterms, options.keyterms, _context_terms(options.context)):
            fields.append(("keyterm", term))

        transient_statuses = {408, 429, 500, 502, 503, 504, 520, 522, 524}
        payload: dict[str, Any] | None = None
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                # Reopen the stream on every attempt: a consumed multipart body
                # cannot be reused safely after a transport failure.
                with audio_path.open("rb") as handle:
                    multipart: list[tuple[str, Any]] = [(name, (None, value)) for name, value in fields]
                    multipart.append(("file", (audio_path.name, handle, _mime_type(audio_path))))
                    timeout = httpx.Timeout(self.timeout, connect=min(20.0, self.timeout))
                    with httpx.Client(timeout=timeout) as client:
                        response = client.post(
                            "https://api.x.ai/v1/stt",
                            headers={"Authorization": f"Bearer {key}"},
                            files=multipart,
                        )
                if response.status_code in transient_statuses and attempt < self.max_attempts:
                    retry_after = getattr(response, "headers", {}).get("Retry-After", "")
                    delay = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else min(20.0, 1.5 * (2 ** (attempt - 1)))
                    time.sleep(delay + random.uniform(0.0, 0.35))
                    continue
                response.raise_for_status()
                raw = getattr(response, "content", b"")
                decoded = raw.decode("utf-8", "strict") if raw else response.text
                loaded = json.loads(decoded)
                if not isinstance(loaded, dict):
                    raise ValueError("xAI STT returned a non-object JSON response")
                transcript_text = str(loaded.get("text") or "")
                if "\ufffd" in transcript_text:
                    raise UnicodeError("xAI STT response contains Unicode replacement characters")
                payload = loaded
                break
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text[:500] if exc.response is not None else str(exc)
                status = exc.response.status_code if exc.response is not None else "?"
                raise RuntimeError(f"xAI STT failed: HTTP {status}: {detail}") from exc
            except (httpx.TransportError, UnicodeError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt >= self.max_attempts:
                    break
                time.sleep(min(20.0, 1.5 * (2 ** (attempt - 1))) + random.uniform(0.0, 0.35))
            except Exception as exc:
                raise RuntimeError(f"xAI STT failed: {exc}") from exc
        if payload is None:
            raise RuntimeError(f"xAI STT failed after {self.max_attempts} attempts: {last_error}") from last_error

        text = str(payload.get("text") or "").strip()
        duration = _float(payload.get("duration"), fallback_duration)
        words = payload.get("words") if isinstance(payload, dict) else None
        segments = _segments_from_words(
            words if isinstance(words, list) else [],
            fallback_text=text,
            fallback_duration=duration or fallback_duration,
            diarize=options.diarize,
        )
        return TranscriptResult(
            segments=segments,
            language=str(payload.get("language") or language) if (payload.get("language") or language) else None,
            model=self.model,
            provider=self.name,
            diarization=options.diarize and any(seg.speaker for seg in segments),
        )


class GeminiTranscribeProvider(TranscriptionProvider):
    """Gemini audio understanding via Files API + `generateContent`."""

    name = "gemini"

    def __init__(self, paths: ProjectPaths, model: str = "gemini-3.5-flash", timeout: float = 300.0) -> None:
        self.paths = paths
        self.model = model or "gemini-3.5-flash"
        self.timeout = timeout

    def _upload_file(self, client: httpx.Client, key: str, audio_path: Path, mime: str) -> dict[str, Any]:
        size = audio_path.stat().st_size
        start = client.post(
            "https://generativelanguage.googleapis.com/upload/v1beta/files",
            headers={
                "x-goog-api-key": key,
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(size),
                "X-Goog-Upload-Header-Content-Type": mime,
                "Content-Type": "application/json",
            },
            json={"file": {"display_name": audio_path.name}},
        )
        start.raise_for_status()
        upload_url = start.headers.get("x-goog-upload-url")
        if not upload_url:
            raise RuntimeError("Gemini Files API did not return an upload URL.")

        with audio_path.open("rb") as handle:
            upload = client.post(
                upload_url,
                headers={
                    "Content-Length": str(size),
                    "X-Goog-Upload-Offset": "0",
                    "X-Goog-Upload-Command": "upload, finalize",
                },
                content=handle.read(),
            )
        upload.raise_for_status()
        payload = upload.json()
        file_info = payload.get("file") if isinstance(payload, dict) else None
        if not isinstance(file_info, dict) or not file_info.get("uri"):
            raise RuntimeError("Gemini Files API returned no file URI.")
        return file_info

    def _delete_file(self, client: httpx.Client, key: str, name: str) -> None:
        if not name:
            return
        try:
            client.delete(
                f"https://generativelanguage.googleapis.com/v1beta/files/{name}",
                headers={"x-goog-api-key": key},
            )
        except Exception:
            return

    def transcribe(self, audio_path: Path, options: TranscriptionOptions) -> TranscriptResult:
        key = require_api_key(self.paths, "gemini")
        mime = _mime_type(audio_path)
        fallback_duration = _wav_duration(audio_path)
        language = _language(options) or "auto"
        prompt = (
            "Transcribe the attached audio. Return JSON only with this shape: "
            '{"language":"ru","segments":[{"start":0.0,"end":1.0,"text":"...","speaker":"Speaker 1"}]}. '
            "Use seconds for start/end. Preserve Russian and English code-switching exactly. "
            f"Language hint: {language}. "
            f"Speaker diarization requested: {bool(options.diarize)}. "
            f"Recording context: {options.prompt_context()}"
        )

        file_name = ""
        try:
            with httpx.Client(timeout=self.timeout) as client:
                file_info = self._upload_file(client, key, audio_path, mime)
                file_name = str(file_info.get("name") or "")
                payload = {
                    "contents": [
                        {
                            "parts": [
                                {
                                    "file_data": {
                                        "mime_type": str(file_info.get("mimeType") or mime),
                                        "file_uri": file_info["uri"],
                                    }
                                },
                                {"text": prompt},
                            ]
                        }
                    ],
                    "generation_config": {"temperature": options.temperature},
                }
                response = client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                    headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                result = response.json()
                self._delete_file(client, key, file_name)
        except Exception as exc:
            raise RuntimeError(f"Gemini transcription failed: {exc}") from exc

        text = _gemini_text(result)
        segments, detected_language = _segments_from_generated_text(
            text,
            fallback_duration=fallback_duration,
            diarize=options.diarize,
        )
        return TranscriptResult(
            segments=segments,
            language=detected_language or (None if language == "auto" else language),
            model=self.model,
            provider=self.name,
            diarization=options.diarize and any(seg.speaker for seg in segments),
        )


def _gemini_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list):
        return ""
    parts = []
    for candidate in candidates:
        content = candidate.get("content") if isinstance(candidate, dict) else None
        raw_parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(raw_parts, list):
            continue
        for part in raw_parts:
            if isinstance(part, dict) and part.get("text"):
                parts.append(str(part["text"]))
    return "\n".join(parts).strip()


class GigaChatTranscribeProvider(TranscriptionProvider):
    """GigaChat file transcription via attachments.

    This is a generative audio-file route, not SaluteSpeech's synchronous STT.
    It is useful for A/B and RU-first experiments, while deterministic subtitle
    timing should still prefer providers with native word timestamps.
    """

    name = "gigachat"

    def __init__(
        self,
        paths: ProjectPaths,
        model: str = "GigaChat",
        *,
        scope: str = "GIGACHAT_API_PERS",
        base_url: str = "https://gigachat.devices.sberbank.ru/api/v1",
        auth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
        verify_ssl: bool = False,
        timeout: float = 300.0,
    ) -> None:
        self.paths = paths
        self.model = model or "GigaChat"
        self.scope = scope or "GIGACHAT_API_PERS"
        self.base_url = base_url.rstrip("/")
        self.auth_url = auth_url
        self.verify_ssl = verify_ssl
        self.timeout = timeout

    def _token(self, client: httpx.Client) -> str:
        auth_key = require_api_key(self.paths, "gigachat")
        response = client.post(
            self.auth_url,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "RqUID": str(uuid.uuid4()),
                "Authorization": f"Basic {auth_key}",
            },
            data={"scope": self.scope},
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise RuntimeError("GigaChat OAuth returned no access token.")
        return str(token)

    def _upload(self, client: httpx.Client, token: str, audio_path: Path) -> str:
        with audio_path.open("rb") as handle:
            response = client.post(
                f"{self.base_url}/files",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                data={"purpose": "general"},
                files={"file": (audio_path.name, handle, _mime_type(audio_path))},
            )
        response.raise_for_status()
        file_id = response.json().get("id")
        if not file_id:
            raise RuntimeError("GigaChat file upload returned no file id.")
        return str(file_id)

    def _delete(self, client: httpx.Client, token: str, file_id: str) -> None:
        try:
            client.post(
                f"{self.base_url}/files/{file_id}/delete",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        except Exception:
            return

    def transcribe(self, audio_path: Path, options: TranscriptionOptions) -> TranscriptResult:
        fallback_duration = _wav_duration(audio_path)
        language = _language(options) or "auto"
        prompt = (
            "Transcribe the attached audio. Return JSON only with this shape: "
            '{"language":"ru","segments":[{"start":0.0,"end":1.0,"text":"...","speaker":"Speaker 1"}]}. '
            "Use seconds for start/end when possible. Preserve code-switching exactly. "
            f"Language hint: {language}. "
            f"Speaker diarization requested: {bool(options.diarize)}. "
            f"Recording context: {options.prompt_context()}"
        )

        file_id = ""
        try:
            with httpx.Client(timeout=self.timeout, verify=self.verify_ssl) as client:
                token = self._token(client)
                file_id = self._upload(client, token, audio_path)
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "function_call": "auto",
                        "messages": [
                            {
                                "role": "user",
                                "content": prompt,
                                "attachments": [file_id],
                            }
                        ],
                        "temperature": options.temperature,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                self._delete(client, token, file_id)
        except Exception as exc:
            raise RuntimeError(f"GigaChat transcription failed: {exc}") from exc

        text = _openai_style_message_text(payload)
        segments, detected_language = _segments_from_generated_text(
            text,
            fallback_duration=fallback_duration,
            diarize=options.diarize,
        )
        return TranscriptResult(
            segments=segments,
            language=detected_language or (None if language == "auto" else language),
            model=self.model,
            provider=self.name,
            diarization=options.diarize and any(seg.speaker for seg in segments),
        )


def _openai_style_message_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict)).strip()
    return str(content or "").strip()
