"""Shared term-dictionary helpers for Live, file STT and cleanup."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping


def normalize_keyterms(
    value: object,
    *,
    limit: int = 100,
    max_length: int = 50,
) -> tuple[str, ...]:
    """Normalize exact spellings while preserving user order."""
    raw: Iterable[object]
    if isinstance(value, (list, tuple)):
        raw = value
    else:
        raw = re.split(r"[,;\n]+", str(value or ""))

    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        term = " ".join(str(item or "").split()).strip()
        folded = term.casefold()
        if not term or len(term) > max_length or folded in seen:
            continue
        seen.add(folded)
        result.append(term)
        if len(result) >= limit:
            break
    return tuple(result)


def build_stt_prompt(
    description: str,
    keyterms: object,
    *,
    max_chars: int = 1000,
) -> str:
    """Combine narrative recording context with an exact-spelling vocabulary."""
    parts: list[str] = []
    narrative = " ".join(str(description or "").split()).strip()
    if narrative:
        parts.append(narrative)
    terms = normalize_keyterms(keyterms)
    if terms:
        parts.append("Exact spellings: " + ", ".join(terms))
    return "\n".join(parts)[: max(0, int(max_chars))].rstrip()


def shared_keyterms(settings: object) -> tuple[str, ...]:
    """Read the canonical dictionary with fallback for pre-migration configs."""
    if not isinstance(settings, Mapping):
        return ()
    value = settings.get("term_dictionary")
    if value:
        return normalize_keyterms(value)
    postprocessing = settings.get("postprocessing")
    if not isinstance(postprocessing, Mapping):
        return ()
    cleanup = postprocessing.get("cleanup")
    if not isinstance(cleanup, Mapping):
        return ()
    return normalize_keyterms(cleanup.get("hotwords", ()))
