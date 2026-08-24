"""Prompt library manager (cleanup role) — built-in pins + user add/edit/delete/pin.

Thin UI over `core.prompt_store.PromptStore`. Built-in prompts show a lock and
can't be edited/deleted ("неубиваемые пины"); user prompts get full CRUD. On
close, the selected prompt id is exposed via `selected_prompt_id`.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..core.prompt_store import ROLE_CLEANUP, PromptStore
from .i18n import Translator
from .icons import icon
from .tooltips import seed_tooltips

_ICON_MUTED = "#9FB0C3"


class PromptLibraryDialog(QDialog):
    def __init__(self, store: PromptStore, tr: Translator, *, role: str = ROLE_CLEANUP,
                 active_id: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._store = store
        self._tr = tr
        self._role = role
        self.selected_prompt_id: Optional[str] = active_id

        self.setWindowTitle(tr.tr("cleanup_prompt"))
        self.setMinimumSize_safe(560, 460)

        root = QVBoxLayout(self)

        body = QHBoxLayout()
        root.addLayout(body, 1)

        # Left: the list of prompts.
        left = QVBoxLayout()
        left.addWidget(self._heading(tr.tr("prompts_user")))
        self._list = QListWidget()
        self._list.setObjectName("prompt_list")
        self._list.currentItemChanged.connect(self._on_select)
        left.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        self._btn_add = QPushButton(icon("add", _ICON_MUTED), tr.tr("prompt_add"))
        self._btn_pin = QPushButton(icon("pin", _ICON_MUTED), tr.tr("prompt_pin"))
        self._btn_del = QPushButton(icon("delete", _ICON_MUTED), tr.tr("prompt_delete"))
        self._btn_add.clicked.connect(self._on_add)
        self._btn_pin.clicked.connect(self._on_pin)
        self._btn_del.clicked.connect(self._on_delete)
        btn_row.addWidget(self._btn_add, 1)
        btn_row.addWidget(self._btn_pin, 1)
        btn_row.addWidget(self._btn_del, 1)
        left.addLayout(btn_row)
        body.addLayout(left, 1)

        # Right: the prompt text (editable for user prompts only).
        right = QVBoxLayout()
        right.addWidget(self._heading(tr.tr("cleanup_prompt")))
        self._text = QPlainTextEdit()
        self._text.setObjectName("prompt_text")
        self._text.setReadOnly(True)
        right.addWidget(self._text, 1)
        self._btn_save_text = QPushButton(icon("save", _ICON_MUTED), tr.tr("save"))
        self._btn_save_text.clicked.connect(self._on_save_text)
        right.addWidget(self._btn_save_text, alignment=Qt.AlignRight)
        body.addLayout(right, 2)

        # Footer: choose this prompt / cancel.
        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton(icon("close", _ICON_MUTED), tr.tr("cancel"))
        cancel.clicked.connect(self.reject)
        self._btn_choose = QPushButton(icon("check", "#FFFFFF"), tr.tr("prompt_choose"))
        self._btn_choose.setProperty("accent", "primary")
        self._btn_choose.clicked.connect(self._on_choose)
        footer_button_width = max(cancel.sizeHint().width(), self._btn_choose.sizeHint().width())
        cancel.setFixedWidth(footer_button_width)
        self._btn_choose.setFixedWidth(footer_button_width)
        footer.addWidget(cancel)
        footer.addWidget(self._btn_choose)
        root.addLayout(footer)

        self._reload(select_id=active_id)
        seed_tooltips(self)

    # Qt's setMinimumSize exists; wrapper avoids a typo crashing construction.
    def setMinimumSize_safe(self, w: int, h: int) -> None:  # noqa: N802
        self.setMinimumSize(w, h)

    def _heading(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "heading")
        return label

    # --- data ----------------------------------------------------------------
    def _reload(self, *, select_id: Optional[str] = None) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        target_row = 0
        for i, entry in enumerate(self._store.list(self._role)):
            item = QListWidgetItem(entry.name)
            if entry.builtin:
                item.setIcon(icon("lock", _ICON_MUTED))
            elif entry.pinned:
                item.setIcon(icon("pin", _ICON_MUTED))
            item.setData(Qt.UserRole, entry.id)
            self._list.addItem(item)
            if select_id and entry.id == select_id:
                target_row = i
        self._list.blockSignals(False)
        if self._list.count():
            self._list.setCurrentRow(target_row)
        seed_tooltips(self)

    def _current_id(self) -> Optional[str]:
        item = self._list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _on_select(self, *_args) -> None:
        prompt_id = self._current_id()
        entry = self._store.get(prompt_id) if prompt_id else None
        if not entry:
            self._text.setPlainText("")
            return
        self._text.setPlainText(entry.text)
        editable = not entry.builtin
        self._text.setReadOnly(not editable)
        self._btn_save_text.setEnabled(editable)
        self._btn_del.setEnabled(entry.deletable)
        self._btn_pin.setEnabled(not entry.builtin)

    # --- actions -------------------------------------------------------------
    def _on_add(self) -> None:
        name, ok = QInputDialog.getText(self, self._tr.tr("prompt_add"), self._tr.tr("cleanup_prompt"))
        if not ok or not name.strip():
            return
        entry = self._store.add_user(self._role, name.strip(), self._text.toPlainText() or "")
        self._reload(select_id=entry.id)

    def _on_pin(self) -> None:
        prompt_id = self._current_id()
        entry = self._store.get(prompt_id) if prompt_id else None
        if not entry or entry.builtin:
            return
        self._store.set_pinned(entry.id, not entry.pinned)
        self._reload(select_id=entry.id)

    def _on_delete(self) -> None:
        prompt_id = self._current_id()
        entry = self._store.get(prompt_id) if prompt_id else None
        if not entry or not entry.deletable:
            return
        confirm = QMessageBox.question(
            self, self._tr.tr("prompt_delete"), f"{self._tr.tr('prompt_delete')}: {entry.name}?"
        )
        if confirm == QMessageBox.Yes:
            self._store.delete_user(entry.id)
            if self.selected_prompt_id == entry.id:
                self.selected_prompt_id = None
            self._reload()

    def _on_save_text(self) -> None:
        prompt_id = self._current_id()
        entry = self._store.get(prompt_id) if prompt_id else None
        if not entry or entry.builtin:
            return
        self._store.update_user(entry.id, text=self._text.toPlainText())
        QMessageBox.information(self, self._tr.tr("settings"), "OK")

    def _on_choose(self) -> None:
        prompt_id = self._current_id()
        if not prompt_id:
            self.reject()
            return
        self.selected_prompt_id = prompt_id
        self._store.set_active(self._role, prompt_id)
        self.accept()
