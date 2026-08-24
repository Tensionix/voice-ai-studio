"""RU/EN string lookup for the GUI (iteration 2 foundation).

Strings live in `config/i18n.yaml` so they can be edited/translated without code
changes. UI-agnostic: the PySide6 layer calls `Translator.tr(key)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..core.config import load_yaml_or_json
from ..core.paths import ProjectPaths


@dataclass
class Translator:
    language: str
    default_language: str
    languages: list[str]
    _strings: dict[str, dict[str, str]]

    @classmethod
    def load(cls, paths: ProjectPaths, language: Optional[str] = None) -> "Translator":
        data = load_yaml_or_json(paths.config / "i18n.yaml")
        default_lang = str(data.get("default_language", "ru"))
        languages = [str(x) for x in data.get("languages", ["ru", "en"])]
        strings = data.get("strings", {}) or {}
        lang = (language or default_lang).lower()
        if lang not in languages:
            lang = default_lang
        return cls(
            language=lang,
            default_language=default_lang,
            languages=languages,
            _strings={str(k): dict(v) for k, v in strings.items() if isinstance(v, dict)},
        )

    def tr(self, key: str) -> str:
        entry = self._strings.get(key)
        if not entry:
            return key  # show the key itself if a string is missing
        return entry.get(self.language) or entry.get(self.default_language) or key

    def set_language(self, language: str) -> None:
        lang = language.lower()
        if lang in self.languages:
            self.language = lang
