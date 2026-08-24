"""Small persistent clipboard of completed Live dictations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from ..core.paths import ProjectPaths
from ..writers.atomic import write_text_atomic


HISTORY_CACHE_LIMIT = 200
OVERLAY_HISTORY_LIMIT = 20


@dataclass(frozen=True)
class DictationEntry:
    entry_id: str
    text: str
    created_at: str

    @classmethod
    def from_mapping(cls, value: object) -> "DictationEntry | None":
        if not isinstance(value, dict):
            return None
        text = str(value.get("text") or "").strip()
        if not text:
            return None
        entry_id = str(value.get("entry_id") or uuid4().hex)
        created_at = str(value.get("created_at") or "")
        return cls(entry_id=entry_id, text=text, created_at=created_at)


class DictationHistory:
    def __init__(self, path: Path, entries: list[DictationEntry] | None = None) -> None:
        self.path = Path(path)
        self._entries = list(entries or [])[:HISTORY_CACHE_LIMIT]

    @classmethod
    def load(cls, paths: ProjectPaths) -> "DictationHistory":
        path = paths.config / "dictation_history.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            payload = []
        raw_entries = payload.get("entries", []) if isinstance(payload, dict) else payload
        entries = []
        if isinstance(raw_entries, list):
            for value in raw_entries:
                entry = DictationEntry.from_mapping(value)
                if entry is not None:
                    entries.append(entry)
        return cls(path, entries)

    def entries(self) -> tuple[DictationEntry, ...]:
        return tuple(self._entries)

    def latest(self) -> DictationEntry | None:
        return self._entries[0] if self._entries else None

    def add(self, text: str) -> DictationEntry | None:
        clean = str(text or "").strip()
        if not clean:
            return None
        entry = DictationEntry(
            entry_id=uuid4().hex,
            text=clean,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        self._entries.insert(0, entry)
        del self._entries[HISTORY_CACHE_LIMIT:]
        self._save()
        return entry

    def delete(self, entry_id: str) -> bool:
        before = len(self._entries)
        self._entries = [entry for entry in self._entries if entry.entry_id != entry_id]
        changed = len(self._entries) != before
        if changed:
            self._save()
        return changed

    def _save(self) -> None:
        payload = {
            "version": 1,
            "limit": HISTORY_CACHE_LIMIT,
            "entries": [asdict(entry) for entry in self._entries],
        }
        write_text_atomic(
            self.path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )
