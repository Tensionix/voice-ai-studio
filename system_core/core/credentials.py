"""API key resolution: config/*.txt file first, then environment variable."""

from __future__ import annotations

import os
from typing import Optional

from .paths import ProjectPaths

_KEY_FILES = {
    "openai": ("api_key_openai.txt", "OPENAI_API_KEY"),
    "assemblyai": ("api_key_assemblyai.txt", "ASSEMBLYAI_API_KEY"),
    "xai": ("api_key_xai.txt", "XAI_API_KEY"),
    "elevenlabs": ("api_key_elevenlabs.txt", "ELEVENLABS_API_KEY"),
    "gemini": ("api_key_gemini.txt", "GEMINI_API_KEY"),
    "gigachat": ("api_key_gigachat.txt", "GIGACHAT_AUTH_KEY"),
    "giga": ("api_key_gigachat.txt", "GIGACHAT_AUTH_KEY"),
    "notion": ("api_key_notion.txt", "NOTION_API_KEY"),
}


def _key_entry(provider: str) -> tuple[str, str, tuple[str, ...]]:
    entry = _KEY_FILES.get(provider, (f"api_key_{provider}.txt", ""))
    filename = entry[0]
    env_var = entry[1] if len(entry) > 1 else ""
    aliases = entry[2] if len(entry) > 2 else ()
    return filename, env_var, tuple(aliases)


def read_api_key(paths: ProjectPaths, provider: str) -> Optional[str]:
    filename, env_var, aliases = _key_entry(provider)
    for candidate in (filename, *aliases):
        key_file = paths.config / candidate
        if key_file.exists():
            text = key_file.read_text(encoding="utf-8").strip()
            if text:
                return text
    if env_var:
        env_value = os.environ.get(env_var, "").strip()
        if env_value:
            return env_value
    return None


def write_api_key(paths: ProjectPaths, provider: str, key: str) -> None:
    """Save an API key to config/api_key_<provider>.txt (atomic). These files are
    gitignored — never committed."""
    from ..writers.atomic import write_text_atomic

    filename, _, _ = _key_entry(provider)
    paths.config.mkdir(parents=True, exist_ok=True)
    write_text_atomic(paths.config / filename, (key or "").strip() + "\n")


def mask_api_key(key: Optional[str]) -> str:
    """A safe-to-display fingerprint: '…' + last 4 chars (never the full key)."""
    key = (key or "").strip()
    if not key:
        return ""
    return "…" + key[-4:] if len(key) > 4 else "…"


def require_api_key(paths: ProjectPaths, provider: str) -> str:
    key = read_api_key(paths, provider)
    if not key:
        filename, _, _ = _key_entry(provider)
        raise RuntimeError(
            f"Missing {provider} API key. Put it in config/{filename} or the environment."
        )
    return key
