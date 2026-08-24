"""Prompt library with pins + MRU cache (pattern from OCR AI path-cache-with-pins).

Two tiers (TZ-adjacent UX decision):
  - BUILT-IN ("general") prompts: shipped in code, always present, permanently
    pinned and NOT deletable ("неубиваемые пины"). Users can't edit them in place;
    to tweak one, clone it into a user prompt.
  - USER prompts: full CRUD — add / edit / delete, plus pin and MRU cache
    (`count` + `last_used`) so recently/often used ones surface first.

Storage: config/prompts.json holds only the user layer + pins/active selection;
built-ins are injected from code so updates ship with the app. UI-agnostic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .logging_utils import timestamp
from .paths import ProjectPaths

# Roles a prompt can belong to (extend as features grow).
ROLE_CLEANUP = "cleanup"
ROLE_METADATA = "metadata"

# Built-in prompts. `id` is stable; editing text here ships to all users.
BUILTIN_PROMPTS: list[dict[str, str]] = [
    {
        "id": "builtin.cleanup.default",
        "role": ROLE_CLEANUP,
        "name": "Readable transcript (RU/EN)",
        "text": (
            "Ты — консервативный редактор расшифровок. ПРИОРИТЕТ №1 — восстановить "
            "пунктуацию на ТОМ ЖЕ языке, что и оригинал:\n"
            "- расставь точки, запятые, двоеточия, тире, кавычки и вопросительные знаки;\n"
            "- восстанови регистр, границы предложений и естественные абзацы;\n"
            "- НЕ ПЕРЕФРАЗИРУЙ, не меняй порядок мыслей и не заменяй слова синонимами;\n"
            "- удаляй только очевидные речевые филлеры, фальстарты и случайные повторы;\n"
            "- сохраняй смешанную RU/EN речь, аббревиатуры, форматы, числа, факты, "
            "доменные термины и метки спикеров без изменений;\n"
            "- ничего не добавляй и не сокращай содержание.\n"
            "Верни только исправленный текст."
        ),
    },
]


@dataclass
class PromptEntry:
    id: str
    role: str
    name: str
    text: str
    builtin: bool = False
    pinned: bool = False
    deletable: bool = True
    count: int = 0
    last_used: Optional[str] = None

    def to_user_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "name": self.name,
            "text": self.text,
            "pinned": self.pinned,
            "count": self.count,
            "last_used": self.last_used,
        }


@dataclass
class PromptStore:
    paths: ProjectPaths
    _user: list[PromptEntry] = field(default_factory=list)
    _active: dict[str, str] = field(default_factory=dict)

    # --- persistence ---------------------------------------------------------

    @property
    def _file(self) -> Path:
        return self.paths.config / "prompts.json"

    @classmethod
    def load(cls, paths: ProjectPaths) -> "PromptStore":
        store = cls(paths=paths)
        data: dict[str, Any] = {}
        if store._file.exists():
            try:
                data = json.loads(store._file.read_text(encoding="utf-8")) or {}
            except (json.JSONDecodeError, OSError):
                data = {}
        for item in data.get("user", []) or []:
            try:
                store._user.append(
                    PromptEntry(
                        id=str(item["id"]),
                        role=str(item.get("role", ROLE_CLEANUP)),
                        name=str(item.get("name", "Untitled")),
                        text=str(item.get("text", "")),
                        builtin=False,
                        pinned=bool(item.get("pinned", False)),
                        deletable=True,
                        count=int(item.get("count", 0) or 0),
                        last_used=item.get("last_used"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        active = data.get("active", {})
        if isinstance(active, dict):
            store._active = {str(k): str(v) for k, v in active.items()}
        return store

    def save(self) -> None:
        payload = {
            "user": [entry.to_user_dict() for entry in self._user],
            "active": self._active,
        }
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- queries -------------------------------------------------------------

    def _builtins(self, role: Optional[str] = None) -> list[PromptEntry]:
        out = []
        for item in BUILTIN_PROMPTS:
            if role and item["role"] != role:
                continue
            out.append(
                PromptEntry(
                    id=item["id"], role=item["role"], name=item["name"], text=item["text"],
                    builtin=True, pinned=True, deletable=False,
                )
            )
        return out

    def list(self, role: Optional[str] = None) -> list[PromptEntry]:
        """Built-ins (pinned) first, then pinned user prompts, then user by MRU."""
        builtins = self._builtins(role)
        users = [e for e in self._user if role is None or e.role == role]
        pinned_users = [e for e in users if e.pinned]
        rest = sorted(
            (e for e in users if not e.pinned),
            key=lambda e: (e.last_used or "", e.count),
            reverse=True,
        )
        return builtins + pinned_users + rest

    def get(self, prompt_id: str) -> Optional[PromptEntry]:
        for entry in self._builtins():
            if entry.id == prompt_id:
                return entry
        for entry in self._user:
            if entry.id == prompt_id:
                return entry
        return None

    def active(self, role: str) -> PromptEntry:
        """The selected prompt for a role, or the first built-in as a safe default."""
        active_id = self._active.get(role)
        if active_id:
            entry = self.get(active_id)
            if entry is not None and entry.role == role:
                return entry
        candidates = self.list(role)
        if not candidates:
            raise RuntimeError(f"No prompts available for role '{role}'.")
        return candidates[0]

    # --- mutations (user layer only) ----------------------------------------

    def _new_id(self, role: str) -> str:
        n = 1
        existing = {e.id for e in self._user}
        while f"user.{role}.{n}" in existing:
            n += 1
        return f"user.{role}.{n}"

    def add_user(self, role: str, name: str, text: str, *, pinned: bool = False) -> PromptEntry:
        entry = PromptEntry(
            id=self._new_id(role), role=role, name=name.strip() or "Untitled",
            text=text, builtin=False, pinned=pinned, deletable=True,
            count=0, last_used=timestamp(),
        )
        self._user.append(entry)
        self.save()
        return entry

    def update_user(self, prompt_id: str, *, name: Optional[str] = None, text: Optional[str] = None) -> None:
        for entry in self._user:
            if entry.id == prompt_id:
                if name is not None:
                    entry.name = name.strip() or entry.name
                if text is not None:
                    entry.text = text
                self.save()
                return
        raise KeyError(f"User prompt not found: {prompt_id}")

    def delete_user(self, prompt_id: str) -> None:
        entry = self.get(prompt_id)
        if entry is None:
            raise KeyError(f"Prompt not found: {prompt_id}")
        if not entry.deletable:
            raise PermissionError(f"Built-in prompt cannot be deleted: {prompt_id}")
        self._user = [e for e in self._user if e.id != prompt_id]
        self._active = {r: i for r, i in self._active.items() if i != prompt_id}
        self.save()

    def set_pinned(self, prompt_id: str, pinned: bool) -> None:
        entry = self.get(prompt_id)
        if entry is None:
            raise KeyError(f"Prompt not found: {prompt_id}")
        if entry.builtin:
            # Built-ins are permanently pinned ("неубиваемые пины").
            return
        for user_entry in self._user:
            if user_entry.id == prompt_id:
                user_entry.pinned = pinned
                self.save()
                return

    def set_active(self, role: str, prompt_id: str) -> None:
        entry = self.get(prompt_id)
        if entry is None or entry.role != role:
            raise KeyError(f"No prompt '{prompt_id}' for role '{role}'")
        self._active[role] = prompt_id
        self.save()

    def touch(self, prompt_id: str) -> None:
        """Bump MRU stats for a user prompt (built-ins are not tracked)."""
        for entry in self._user:
            if entry.id == prompt_id:
                entry.count += 1
                entry.last_used = timestamp()
                self.save()
                return
