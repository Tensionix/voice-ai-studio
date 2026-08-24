"""Obsidian connector — write the transcript as a Markdown note into a vault.

Obsidian vaults are plain folders of Markdown files, so this is a filesystem
export: render the same Markdown the pipeline produces (YAML frontmatter with
tags/speakers is already Obsidian-friendly) and drop it into
`<vault_path>/<subfolder>/<slug>.md`, written atomically. Fully offline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..core.models import TranscriptDoc
from ..writers.atomic import write_text_atomic
from ..writers.markdown_writer import render_markdown
from .base import Connector, ExportResult, doc_title

_INVALID = '<>:"/\\|?*'


def _safe_filename(name: str) -> str:
    """A vault-safe note name. Prefer python-slugify but keep Unicode letters
    (Obsidian and Windows handle Cyrillic note names fine)."""
    name = (name or "").strip() or "transcript"
    cleaned = "".join("-" if ch in _INVALID else ch for ch in name)
    cleaned = " ".join(cleaned.split()).strip(" .")  # collapse spaces, no trailing dots
    return cleaned[:120] or "transcript"


class ObsidianConnector(Connector):
    name = "obsidian"

    def vault_path(self) -> Optional[Path]:
        raw = str(self.config.get("vault_path", "")).strip()
        return Path(raw) if raw else None

    def is_configured(self) -> tuple[bool, str]:
        vault = self.vault_path()
        if vault is None:
            return False, "Obsidian vault path is not set."
        if not vault.exists():
            return False, f"Obsidian vault folder does not exist: {vault}"
        return True, ""

    def _note_name(self, doc: TranscriptDoc) -> str:
        base = doc.metadata.slug or doc.metadata.title or doc_title(doc)
        return _safe_filename(base) + ".md"

    def export(self, doc: TranscriptDoc, *, source_path: Optional[Path] = None) -> ExportResult:
        ok, reason = self.is_configured()
        if not ok:
            return ExportResult(self.name, False, message=reason)

        vault = self.vault_path()
        assert vault is not None  # is_configured guarantees this
        subfolder = str(self.config.get("subfolder", "") or "").strip().strip("/\\")
        dest_dir = vault / subfolder if subfolder else vault
        dest_dir.mkdir(parents=True, exist_ok=True)

        dest = dest_dir / self._note_name(doc)
        try:
            write_text_atomic(dest, render_markdown(doc))
        except Exception as exc:  # noqa: BLE001 — surfaced, never raised
            return ExportResult(self.name, False, message=f"Write failed: {exc}")
        return ExportResult(self.name, True, target=str(dest), message=f"Wrote {dest.name}")
