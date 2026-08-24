"""Shared OpenAI helpers: robust retries + strict-JSON Responses call.

Condensed from Audion Office OCR AI `openai_provider.py` (streaming kept the
connection alive on long generations; transport/timeout/rate-limit treated as
retryable with backoff).
"""

from __future__ import annotations

import json
import random
import re
import time
from typing import Any, Optional


def is_timeout_or_transient(msg: str) -> bool:
    markers = (
        "timeout", "timed out", "apitimeouterror", "readtimeout",
        "connection reset", "connection aborted", "server disconnected",
        "502", "503", "504",
    )
    return any(marker in msg for marker in markers)


def is_transport_error(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    msg = str(exc).lower()
    if any(k in name for k in ("apiconnectionerror", "protocolerror", "connecterror", "readerror", "writeerror")):
        return True
    markers = (
        "server disconnected", "connection error", "connection reset",
        "connection aborted", "broken pipe", "remote end closed connection",
        "unexpected eof", "ssl", "handshake",
    )
    return any(m in msg for m in markers)


def is_rate_limited(msg: str) -> bool:
    markers = ("rate limit", "rate_limit_exceeded", "tokens per min", "too many requests", "error code: 429")
    return any(marker in msg for marker in markers)


def _retry_after(msg: str) -> Optional[float]:
    match = re.search(r"try again in\s+([0-9]+(?:\.[0-9]+)?)s", msg, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _sleep_for(attempt: int, msg: str) -> float:
    retry_after = _retry_after(msg)
    if retry_after is not None:
        return retry_after + random.uniform(0.2, 1.0)
    if is_rate_limited(msg):
        return min(45.0, 5.0 * attempt) + random.uniform(0.3, 1.2)
    return min(120.0, 4.0 * (1.9 ** (attempt - 1))) + random.uniform(0.3, 1.2)


def _normalize_json_text(raw: str) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    if text and not text.lstrip().startswith("{"):
        left, right = text.find("{"), text.rfind("}")
        if left != -1 and right > left:
            text = text[left:right + 1]
    return text.strip()


def call_text(
    client: Any,
    *,
    model: str,
    instructions: str,
    user_prompt: str,
    max_output_tokens: int = 8000,
    max_retries: int = 4,
    timeout_sec: float | None = 180.0,
) -> str:
    """Call the Responses API for free-form text output, with retries."""
    for attempt in range(1, max_retries + 1):
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "instructions": instructions,
                "input": user_prompt,
                "max_output_tokens": max_output_tokens,
            }
            if timeout_sec is not None:
                kwargs["timeout"] = timeout_sec
            try:
                resp = client.responses.create(**kwargs)
            except TypeError:
                kwargs.pop("timeout", None)
                resp = client.responses.create(**kwargs)
            text = (getattr(resp, "output_text", None) or "").strip()
            if not text:
                raise ValueError("Empty model response text")
            return text
        except Exception as exc:
            msg = str(exc).lower()
            retryable = is_timeout_or_transient(msg) or is_rate_limited(msg) or is_transport_error(exc)
            if attempt < max_retries:
                time.sleep(_sleep_for(attempt, msg) if retryable else min(12.0, 2.0 * attempt))
                continue
            raise RuntimeError(f"OpenAI text call failed: {exc}") from exc
    raise RuntimeError("Unreachable")


def call_json_object(
    client: Any,
    *,
    model: str,
    instructions: str,
    user_prompt: str,
    max_output_tokens: int = 4000,
    max_retries: int = 4,
    timeout_sec: float | None = 120.0,
) -> dict[str, Any]:
    """Call the Responses API and parse a strict JSON object, with retries."""
    last_raw = ""
    for attempt in range(1, max_retries + 1):
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "instructions": instructions,
                "input": user_prompt,
                "text": {"format": {"type": "json_object"}},
                "max_output_tokens": max_output_tokens,
            }
            if timeout_sec is not None:
                kwargs["timeout"] = timeout_sec
            try:
                resp = client.responses.create(**kwargs)
            except TypeError:
                kwargs.pop("timeout", None)
                resp = client.responses.create(**kwargs)

            last_raw = (getattr(resp, "output_text", None) or "").strip()
            normalized = _normalize_json_text(last_raw)
            if not normalized:
                raise ValueError("Empty model response text")
            return json.loads(normalized)

        except Exception as exc:
            msg = str(exc).lower()
            retryable = is_timeout_or_transient(msg) or is_rate_limited(msg) or is_transport_error(exc)
            if attempt < max_retries:
                time.sleep(_sleep_for(attempt, msg) if retryable else min(12.0, 2.0 * attempt))
                continue
            raise RuntimeError(f"OpenAI call failed: {exc}\nRAW(head): {last_raw[:400]}") from exc

    raise RuntimeError("Unreachable")
