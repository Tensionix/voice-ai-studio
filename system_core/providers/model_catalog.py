"""Dynamic model catalog (TZ section 12; reuse of OCR AI's dynamic-options pattern).

Model lists are NOT hardcoded: the OpenAI snapshots retire over time
(`gpt-4o-transcribe`/`whisper-1`/`gpt-4o-mini-transcribe` ~June 2026). We pull
the live list from `/v1/models`, classify each id by role (STT vs chat), cache
the result on disk with a TTL, and fall back to a curated list when offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import json
import time

from ..core.credentials import read_api_key
from ..core.paths import ProjectPaths

ROLE_STT = "stt"          # file transcription via audio.transcriptions
ROLE_CHAT = "chat"        # post-processing LLM

# Sensible defaults (TZ decisions 3 & 5).
DEFAULT_STT_MODEL = "gpt-4o-transcribe"
DEFAULT_DIARIZE_STT_MODEL = "gpt-4o-transcribe-diarize"
DEFAULT_POSTPROCESS_MODEL = "gpt-5.4-mini"

# Curated fallback used when the API is unreachable.
STT_FALLBACK = [
    "gpt-4o-transcribe-diarize",
    "gpt-4o-transcribe",
    "gpt-4o-mini-transcribe",
    "whisper-1",
]
CHAT_FALLBACK = ["gpt-4o", "gpt-4o-mini", "gpt-5.4-mini", "gpt-5.4-nano"]

_CACHE_TTL_SECONDS = 24 * 3600
_EXCLUDE_MARKERS = ("tts", "embedding", "dall", "image", "moderation", "audio-preview")


@dataclass
class CatalogResult:
    models: list[str]
    source: str  # "api" | "cache" | "fallback"


def classify_model(model_id: str) -> set[str]:
    """Return the set of roles a model id qualifies for."""
    mid = model_id.lower()
    roles: set[str] = set()
    if ("transcribe" in mid or "whisper" in mid) and "realtime" not in mid:
        roles.add(ROLE_STT)
    # Chat/LLM models for post-processing. Exclude non-text and pure-audio endpoints.
    if mid.startswith(("gpt-", "o1", "o3", "o4", "chatgpt")) and not any(
        marker in mid for marker in _EXCLUDE_MARKERS
    ):
        if "transcribe" not in mid and "whisper" not in mid and "realtime" not in mid:
            roles.add(ROLE_CHAT)
    return roles


def filter_models(model_ids: list[str], role: str) -> list[str]:
    selected = [mid for mid in model_ids if role in classify_model(mid)]
    return sorted(set(selected))


def _cache_path(paths: ProjectPaths) -> Path:
    return paths.workspace / "models_cache.json"


def _read_cache(paths: ProjectPaths) -> Optional[dict]:
    cache_file = _cache_path(paths)
    if not cache_file.exists():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_cache(paths: ProjectPaths, model_ids: list[str]) -> None:
    cache_file = _cache_path(paths)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": time.time(), "models": sorted(set(model_ids))}
    cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _fetch_from_api(paths: ProjectPaths) -> Optional[list[str]]:
    key = read_api_key(paths, "openai")
    if not key:
        return None
    try:
        from openai import OpenAI  # type: ignore
    except Exception:
        return None
    try:
        client = OpenAI(api_key=key)
        response = client.models.list()
        return [item.id for item in response.data]
    except Exception:
        return None


def get_all_models(paths: ProjectPaths, *, refresh: bool = False) -> CatalogResult:
    """Return all model ids, preferring fresh API data, then cache, then fallback."""
    if not refresh:
        cache = _read_cache(paths)
        if cache and (time.time() - float(cache.get("fetched_at", 0))) < _CACHE_TTL_SECONDS:
            return CatalogResult(models=list(cache.get("models", [])), source="cache")

    api_models = _fetch_from_api(paths)
    if api_models:
        _write_cache(paths, api_models)
        return CatalogResult(models=sorted(set(api_models)), source="api")

    cache = _read_cache(paths)
    if cache and cache.get("models"):
        return CatalogResult(models=list(cache["models"]), source="cache")

    return CatalogResult(models=sorted(set(STT_FALLBACK + CHAT_FALLBACK)), source="fallback")


def list_models(paths: ProjectPaths, role: str, *, refresh: bool = False) -> CatalogResult:
    """Return models for a given role (ROLE_STT / ROLE_CHAT)."""
    catalog = get_all_models(paths, refresh=refresh)
    models = filter_models(catalog.models, role)
    if not models:
        models = STT_FALLBACK if role == ROLE_STT else CHAT_FALLBACK
    return CatalogResult(models=models, source=catalog.source)
