"""Small UI-only state persistence.

Settings that affect the backend live in config/*.yaml. Ephemeral desktop state
that only helps the GUI resume where the user left off lives in workspace/.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.paths import ProjectPaths
from ..writers.atomic import write_text_atomic


def _state_path(paths: ProjectPaths) -> Path:
    return paths.workspace / "ui_state.json"


def _load_state(paths: ProjectPaths) -> dict[str, Any]:
    path = _state_path(paths)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(paths: ProjectPaths, state: dict[str, Any]) -> None:
    payload = json.dumps(state, ensure_ascii=False, indent=2)
    write_text_atomic(_state_path(paths), payload + "\n")


def load_queue_files(paths: ProjectPaths) -> list[Path]:
    state = _load_state(paths)
    raw = state.get("queue_files", [])
    if not isinstance(raw, list):
        return []
    files: list[Path] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        path = Path(item)
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        files.append(path)
    return files


def save_queue_files(paths: ProjectPaths, files: list[Path]) -> None:
    state = _load_state(paths)
    state["queue_files"] = [str(path) for path in files]
    _save_state(paths, state)


def load_tab_order(paths: ProjectPaths, name: str, default: list[str]) -> list[str]:
    state = _load_state(paths)
    groups = state.get("tab_order", {})
    raw = groups.get(name) if isinstance(groups, dict) else None
    if not isinstance(raw, list):
        return list(default)

    valid = set(default)
    ordered: list[str] = []
    for item in raw:
        if isinstance(item, str) and item in valid and item not in ordered:
            ordered.append(item)
    ordered.extend(key for key in default if key not in ordered)
    return ordered


def save_tab_order(paths: ProjectPaths, name: str, order: list[str]) -> None:
    state = _load_state(paths)
    groups = state.setdefault("tab_order", {})
    if not isinstance(groups, dict):
        groups = {}
        state["tab_order"] = groups
    groups[name] = list(order)
    _save_state(paths, state)


# --- first-run setup prompt ---------------------------------------------------
SETUP_PROMPT_INSTALL = "install"
SETUP_PROMPT_LATER = "later"
SETUP_PROMPT_NEVER = "never"


def load_setup_prompt_answer(paths: ProjectPaths) -> str:
    """Last answer given to the first-run download prompt ('' when never asked)."""
    state = _load_state(paths)
    entry = state.get("setup_prompt")
    if not isinstance(entry, dict):
        return ""
    answer = str(entry.get("answer", "") or "").strip().lower()
    return answer if answer in {SETUP_PROMPT_INSTALL, SETUP_PROMPT_LATER, SETUP_PROMPT_NEVER} else ""


def save_setup_prompt_answer(paths: ProjectPaths, answer: str) -> None:
    from datetime import datetime, timezone

    state = _load_state(paths)
    state["setup_prompt"] = {
        "answer": str(answer),
        "answered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save_state(paths, state)
