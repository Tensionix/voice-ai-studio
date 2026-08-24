"""Open an OpenAI Realtime *transcription* session over WebSocket.

This is the only place that touches the OpenAI Realtime SDK, kept tiny and apart
from the streaming/threading logic in `realtime_provider.py` so that logic stays
unit-testable with a fake connection. The SDK's realtime support needs the
`websockets` package (opt-in via install/Install-Live-Deps.cmd) — absent it, we
raise a clear, actionable error.

NOTE: the exact session payload (audio format, transcription model, turn
detection) follows the SDK's typed shapes as of openai 2.42, but the live wire
behaviour needs on-device validation against the API (mic + network).
"""

from __future__ import annotations

from typing import Optional

from ..core.credentials import require_api_key
from ..core.paths import ProjectPaths
from ..live.config import OPENAI_LIVE_REALTIME_MODEL
from ..providers.base import LiveOptions, LiveRegionBlockedError

# Default STT model for the transcription session (audio family, not a chat model).
DEFAULT_REALTIME_MODEL = OPENAI_LIVE_REALTIME_MODEL


def _update_transcription_session(conn, session: dict) -> None:
    """Send the right update event for a Realtime transcription session.

    openai>=2.42 exposes a dedicated `transcription_session.update` resource.
    Older or fake connections may only expose `session.update`, so keep a narrow
    fallback for compatibility and tests.
    """
    transcription_session = getattr(conn, "transcription_session", None)
    update = getattr(transcription_session, "update", None)
    if callable(update):
        update(session=session)
        return

    generic_session = getattr(conn, "session", None)
    update = getattr(generic_session, "update", None)
    if callable(update):
        update(session=session)
        return

    raise RuntimeError("Realtime connection cannot update transcription session")


def open_openai_transcription_connection(
    paths: ProjectPaths, options: LiveOptions, model: Optional[str] = None
):
    """Connect and configure a transcription session; return the open connection.

    The caller owns it (reads events, appends audio, commits, closes)."""
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - env guard
        raise RuntimeError("openai package is not available") from exc

    # The SDK's realtime transport needs `websockets`; pre-check for a clear,
    # actionable message instead of the SDK's terse "install openai[realtime]".
    try:
        import websockets  # noqa: F401
    except Exception as exc:  # pragma: no cover - env guard
        raise RuntimeError(
            "Realtime streaming needs the 'websockets' package — run "
            "install/Install-Live-Deps.cmd"
        ) from exc

    api_key = require_api_key(paths, "openai")
    client = OpenAI(api_key=api_key)

    stt_model = (model or options.model or DEFAULT_REALTIME_MODEL).strip() or DEFAULT_REALTIME_MODEL
    language = options.language if options.language and options.language != "auto" else None

    transcription: dict = {"model": stt_model}
    if language:
        transcription["language"] = language
    if options.context and stt_model != "gpt-realtime-whisper":
        transcription["prompt"] = options.context

    turn_detection = None if stt_model == "gpt-realtime-whisper" else {
        "type": "server_vad",
        # Keep a slightly wider server-side lead-in around detected speech; this
        # complements the local prebuffer in LiveController.
        "prefix_padding_ms": 500,
        "silence_duration_ms": 350,
        "threshold": 0.45,
    }

    session = {
        "type": "transcription",
        "audio": {
            "input": {
                "format": {"type": "audio/pcm", "rate": options.sample_rate},
                "transcription": transcription,
                # server-side VAD streams partials as the user speaks (the whole
                # point of realtime); segments are accumulated by the provider.
                "turn_detection": turn_detection,
            }
        },
    }

    try:
        manager = client.realtime.connect(extra_query={"intent": "transcription"})
        conn = manager.enter()  # open the websocket (raises if `websockets` missing)
    except ImportError as exc:  # pragma: no cover - env guard
        raise RuntimeError(
            "Realtime streaming needs the 'websockets' package — run "
            "install/Install-Live-Deps.cmd"
        ) from exc
    except Exception as exc:
        # Translate OpenAI's edge geo-block (HTTP 403 + unsupported_country...)
        # into a clear, actionable error instead of "server rejected ... HTTP 403".
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
        body = ""
        try:
            body = bytes(getattr(resp, "body", b"") or b"").decode("utf-8", "replace")
        except Exception:
            body = ""
        if status == 403 or "unsupported_country_region_territory" in body:
            raise LiveRegionBlockedError(
                "OpenAI Realtime refused this region (HTTP 403). Enable a VPN — "
                "same as for the rest of the OpenAI API."
            ) from exc
        raise

    _update_transcription_session(conn, session)
    return conn
