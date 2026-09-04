"""First-run download prompt.

The distribution ships without model weights and engine packs. On the first
GUI start (and on every later start until the user either installs them or
chooses "Don't ask again") this dialog lists the recommended modules that are
still missing, with their approximate download size, and offers to install all
of them in one go. The actual installation runs through the ordinary
`ModulesPanel` queue on the Maintenance page, so progress, logs and the
readiness matrix stay in their usual place.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..core.modules import ModuleInfo
from .i18n import Translator
from .icons import icon
from .state_io import SETUP_PROMPT_INSTALL, SETUP_PROMPT_LATER, SETUP_PROMPT_NEVER
from .tooltips import seed_tooltips

_ICON_MUTED = "#9FB0C3"


def format_download_size(megabytes: int, language: str) -> str:
    """'≈ 1,7 ГБ' / '≈ 1.7 GB' style size for the prompt rows and the total."""
    if megabytes <= 0:
        return ""
    if megabytes >= 1000:
        value = f"{megabytes / 1024:.1f}"
        unit = "ГБ" if language == "ru" else "GB"
    else:
        value = str(int(megabytes))
        unit = "МБ" if language == "ru" else "MB"
    if language == "ru":
        value = value.replace(".", ",")
    return f"≈ {value} {unit}"


class SetupPromptDialog(QDialog):
    """Offer to download the missing recommended modules."""

    def __init__(self, modules: list[ModuleInfo], tr: Translator, parent=None):
        super().__init__(parent)
        self._tr = tr
        self._modules = list(modules)
        self._checks: dict[str, QCheckBox] = {}
        self.answer = SETUP_PROMPT_LATER

        self.setObjectName("SetupPromptDialog")
        self.setWindowTitle(tr.tr("setup_prompt_title"))
        self.setModal(True)
        self.setMinimumWidth(640)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        head = QLabel(tr.tr("setup_prompt_title"))
        head.setProperty("role", "heading")
        root.addWidget(head)

        intro = QLabel(tr.tr("setup_prompt_intro"))
        intro.setWordWrap(True)
        root.addWidget(intro)

        for mod in self._modules:
            root.addWidget(self._build_row(mod))

        self._total = QLabel("")
        self._total.setProperty("role", "card-title")
        self._total.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        root.addWidget(self._total)

        note = QLabel(tr.tr("setup_prompt_note"))
        note.setProperty("role", "muted")
        note.setWordWrap(True)
        root.addWidget(note)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.btn_never = QPushButton(icon("close", _ICON_MUTED), tr.tr("setup_prompt_never"))
        self.btn_never.setToolTip(tr.tr("setup_prompt_never_tip"))
        self.btn_never.clicked.connect(self._on_never)
        footer.addWidget(self.btn_never)
        footer.addStretch(1)
        self.btn_later = QPushButton(tr.tr("setup_prompt_later"))
        self.btn_later.setToolTip(tr.tr("setup_prompt_later_tip"))
        self.btn_later.clicked.connect(self._on_later)
        footer.addWidget(self.btn_later)
        self.btn_install = QPushButton(icon("download", "#FFFFFF"), tr.tr("setup_prompt_install"))
        self.btn_install.setProperty("accent", "primary")
        self.btn_install.setToolTip(tr.tr("setup_prompt_install_tip"))
        self.btn_install.setDefault(True)
        self.btn_install.clicked.connect(self._on_install)
        footer.addWidget(self.btn_install)
        root.addLayout(footer)

        self._refresh_total()
        seed_tooltips(self)

    # --- rows ----------------------------------------------------------------
    def _build_row(self, mod: ModuleInfo) -> QFrame:
        frame = QFrame()
        frame.setObjectName("SetupPromptRow")
        row = QHBoxLayout(frame)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(12)

        check = QCheckBox()
        check.setChecked(True)
        check.setAccessibleName(self._tr.tr(mod.name_key))
        check.toggled.connect(self._refresh_total)
        row.addWidget(check)
        self._checks[mod.key] = check

        texts = QVBoxLayout()
        texts.setSpacing(2)
        name = QLabel(self._tr.tr(mod.name_key))
        name.setProperty("role", "card-title")
        desc = QLabel(self._tr.tr(mod.desc_key))
        desc.setProperty("role", "muted")
        desc.setWordWrap(True)
        texts.addWidget(name)
        texts.addWidget(desc)
        row.addLayout(texts, 1)

        size = QLabel(format_download_size(mod.download_mb, self._tr.language))
        size.setProperty("role", "muted")
        size.setMinimumWidth(96)
        size.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(size)
        return frame

    def selected_keys(self) -> list[str]:
        return [mod.key for mod in self._modules if self._checks[mod.key].isChecked()]

    def _refresh_total(self) -> None:
        selected = set(self.selected_keys())
        total_mb = sum(mod.download_mb for mod in self._modules if mod.key in selected)
        size = format_download_size(total_mb, self._tr.language) or "0"
        self._total.setText(self._tr.tr("setup_prompt_total").replace("{size}", size))
        self.btn_install.setEnabled(bool(selected))

    # --- answers -------------------------------------------------------------
    def _on_install(self) -> None:
        self.answer = SETUP_PROMPT_INSTALL
        self.accept()

    def _on_later(self) -> None:
        self.answer = SETUP_PROMPT_LATER
        self.reject()

    def _on_never(self) -> None:
        self.answer = SETUP_PROMPT_NEVER
        self.reject()

    def reject(self) -> None:  # noqa: D401 - Esc / window close mean "later"
        if self.answer == SETUP_PROMPT_INSTALL:
            self.answer = SETUP_PROMPT_LATER
        super().reject()
