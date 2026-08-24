"""Speaker rename dialog (TZ section 11).

Lists the speaker labels found in a `*.transcript.json` and lets the user type a
real name for each. On accept it applies the mapping to the canonical JSON and
regenerates the existing sidecars (`pipeline.speaker_rename`). No re-transcription.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from ..core.paths import ProjectPaths
from ..writers.json_writer import read_json
from .i18n import Translator
from .tooltips import seed_tooltips


class SpeakerRenameDialog(QDialog):
    def __init__(
        self,
        paths: ProjectPaths,
        tr: Translator,
        settings: dict[str, Any],
        json_path: Path,
        parent=None,
    ):
        super().__init__(parent)
        self._paths = paths
        self._tr = tr
        self._settings = settings
        self._json_path = json_path
        self._edits: dict[str, QLineEdit] = {}
        self.written: list[Path] = []

        self.setWindowTitle(tr.tr("speaker_rename"))
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        doc = read_json(json_path)
        speakers = doc.speakers

        if not speakers:
            root.addWidget(QLabel(tr.tr("speaker_rename_none")))
        else:
            hint = QLabel(tr.tr("speaker_rename_hint"))
            hint.setProperty("role", "muted")
            hint.setWordWrap(True)
            root.addWidget(hint)
            form = QFormLayout()
            form.setHorizontalSpacing(10)
            form.setVerticalSpacing(8)
            for name in speakers:
                edit = QLineEdit()
                edit.setText(name)
                edit.setPlaceholderText(name)
                self._edits[name] = edit
                form.addRow(QLabel(name), edit)
            root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        if not speakers:
            buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        root.addWidget(buttons)
        seed_tooltips(self)

    def mapping(self) -> dict[str, str]:
        """Only labels the user actually changed to a non-empty new value."""
        out: dict[str, str] = {}
        for old, edit in self._edits.items():
            new = edit.text().strip()
            if new and new != old:
                out[old] = new
        return out

    def _apply(self) -> None:
        from ..pipeline.speaker_rename import rename_speakers_in_file

        mapping = self.mapping()
        if not mapping:
            self.reject()
            return
        self.written = rename_speakers_in_file(self._json_path, mapping, self._settings)
        self.accept()
