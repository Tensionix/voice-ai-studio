"""Scrollable, reusable clipboard window for the last Live dictations."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QGradient, QLinearGradient
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..live.history import (
    DictationEntry,
    HISTORY_CACHE_LIMIT,
    OVERLAY_HISTORY_LIMIT,
)
from .icons import icon


def _display_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%d.%m.%Y  %H:%M")
    except (TypeError, ValueError):
        return ""


class _FadePreviewLabel(QLabel):
    """Two-line preview that softly masks text only when more content exists."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self._fade_effect = QGraphicsOpacityEffect(self)
        self._fade_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._fade_effect)
        self.setProperty("fadeActive", False)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._refresh_fade()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._refresh_fade()

    def _refresh_fade(self) -> None:
        rect = self.contentsRect()
        if rect.width() <= 0 or rect.height() <= 0:
            return
        text_bounds = self.fontMetrics().boundingRect(
            0,
            0,
            rect.width(),
            100_000,
            Qt.TextWordWrap | Qt.TextExpandTabs,
            self.text(),
        )
        overflow = text_bounds.height() > rect.height()
        if bool(self.property("fadeActive")) == overflow:
            return
        self.setProperty("fadeActive", overflow)
        if not overflow:
            self._fade_effect.setOpacityMask(QBrush())
            return

        gradient = QLinearGradient(0.0, 0.0, 0.0, 1.0)
        gradient.setCoordinateMode(QGradient.ObjectBoundingMode)
        gradient.setColorAt(0.0, QColor(0, 0, 0, 255))
        gradient.setColorAt(0.52, QColor(0, 0, 0, 255))
        gradient.setColorAt(0.82, QColor(0, 0, 0, 128))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 0))
        self._fade_effect.setOpacityMask(QBrush(gradient))


class _HistoryCard(QFrame):
    paste_requested = Signal(str)
    copy_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, entry: DictationEntry, tr, parent=None) -> None:
        super().__init__(parent)
        self._entry = entry
        self.setObjectName("DictationHistoryItem")
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(tr.tr("dictation_history_paste"))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 10, 10, 10)
        root.setSpacing(10)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(5)
        self.preview = _FadePreviewLabel(entry.text)
        self.preview.setObjectName("DictationHistoryPreview")
        self.preview.setWordWrap(True)
        self.preview.setCursor(Qt.PointingHandCursor)
        self.preview.setAccessibleName(tr.tr("dictation_history_paste"))
        line_height = self.preview.fontMetrics().lineSpacing()
        self.preview.setFixedHeight(line_height * 2 + 6)
        self.preview.setToolTip(entry.text)
        self.preview.installEventFilter(self)
        text_column.addWidget(self.preview)

        stamp = QLabel(_display_time(entry.created_at))
        stamp.setObjectName("DictationHistoryTime")
        stamp.setProperty("role", "muted")
        stamp.setCursor(Qt.PointingHandCursor)
        stamp.installEventFilter(self)
        text_column.addWidget(stamp)
        root.addLayout(text_column, 1)

        self.btn_copy = QPushButton()
        self.btn_copy.setProperty("variant", "history-action")
        self.btn_copy.setIcon(icon("copy", "#E6EDF5"))
        self.btn_copy.setIconSize(QSize(17, 17))
        self.btn_copy.setFixedSize(34, 34)
        self.btn_copy.setToolTip(tr.tr("dictation_history_copy"))
        self.btn_copy.setAccessibleName(tr.tr("dictation_history_copy"))
        self.btn_copy.clicked.connect(lambda: self.copy_requested.emit(entry.text))
        root.addWidget(self.btn_copy, 0, Qt.AlignBottom)

        self.btn_delete = QPushButton()
        self.btn_delete.setProperty("variant", "history-action")
        self.btn_delete.setProperty("accent", "danger")
        self.btn_delete.setIcon(icon("delete", "#E6EDF5"))
        self.btn_delete.setIconSize(QSize(17, 17))
        self.btn_delete.setFixedSize(34, 34)
        self.btn_delete.setToolTip(tr.tr("dictation_history_delete"))
        self.btn_delete.setAccessibleName(tr.tr("dictation_history_delete"))
        self.btn_delete.clicked.connect(lambda: self.delete_requested.emit(entry.entry_id))
        root.addWidget(self.btn_delete, 0, Qt.AlignBottom)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.LeftButton:
            self.paste_requested.emit(self._entry.text)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        line_height = self.preview.fontMetrics().lineSpacing()
        self.preview.setFixedHeight(line_height * 2 + 6)
        super().showEvent(event)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            self.paste_requested.emit(self._entry.text)
            event.accept()
            return True
        return super().eventFilter(watched, event)


class DictationHistoryDialog(QDialog):
    paste_requested = Signal(str)
    copy_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(
        self,
        tr,
        entries: tuple[DictationEntry, ...],
        parent=None,
        *,
        limit: int = OVERLAY_HISTORY_LIMIT,
        title_key: str = "dictation_history_title",
    ) -> None:
        super().__init__(parent)
        self._tr = tr
        self._limit = self._normalise_limit(limit)
        self._title_key = title_key
        self.setObjectName("DictationHistoryDialog")
        self.setWindowTitle(tr.tr(self._title_key))
        self.setMinimumSize(560, 420)
        self.resize(720, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 18)
        root.setSpacing(12)

        heading = QHBoxLayout()
        self._title = QLabel(tr.tr(self._title_key))
        self._title.setProperty("role", "heading")
        heading.addWidget(self._title)
        heading.addStretch(1)
        self._count = QLabel()
        self._count.setProperty("role", "muted")
        heading.addWidget(self._count)
        root.addLayout(heading)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("DictationHistoryScroll")
        self._scroll.setWidgetResizable(True)
        self._content = QWidget()
        self._content.setObjectName("DictationHistoryContent")
        self._items = QVBoxLayout(self._content)
        self._items.setContentsMargins(0, 0, 0, 0)
        self._items.setSpacing(8)
        self._scroll.setWidget(self._content)
        root.addWidget(self._scroll, 1)
        self.set_entries(entries)

    @staticmethod
    def _normalise_limit(value: int) -> int:
        return max(1, min(int(value), HISTORY_CACHE_LIMIT))

    def set_entries(
        self,
        entries: tuple[DictationEntry, ...],
        *,
        limit: int | None = None,
        title_key: str | None = None,
    ) -> None:
        if limit is not None:
            self._limit = self._normalise_limit(limit)
        if title_key is not None:
            self._title_key = title_key
        self.setWindowTitle(self._tr.tr(self._title_key))
        self._title.setText(self._tr.tr(self._title_key))
        self._entry_values = tuple(entries)[: self._limit]
        while self._items.count():
            item = self._items.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        values = self._entry_values
        self._count.setText(f"{len(values)} / {self._limit}")
        if not values:
            empty = QLabel(self._tr.tr("dictation_history_empty"))
            empty.setProperty("role", "muted")
            empty.setAlignment(Qt.AlignCenter)
            self._items.addWidget(empty, 1)
            return

        for entry in values:
            card = _HistoryCard(entry, self._tr)
            card.paste_requested.connect(self.paste_requested)
            card.copy_requested.connect(self.copy_requested)
            card.delete_requested.connect(self.delete_requested)
            self._items.addWidget(card)
        self._items.addStretch(1)

    def retranslate(self, tr) -> None:
        self._tr = tr
        self.setWindowTitle(tr.tr(self._title_key))
        self._title.setText(tr.tr(self._title_key))
        self.set_entries(self._entry_values)
