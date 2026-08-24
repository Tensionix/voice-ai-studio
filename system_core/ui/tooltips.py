"""Automatic tooltip coverage for PySide widgets.

Manual tooltips remain authoritative. This helper fills the gaps from nearby
form labels, button text, placeholders, card titles, or object names so dense
tool panels do not leave controls unexplained.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QProxyStyle,
    QSlider,
    QStyle,
    QTabWidget,
    QTextEdit,
    QWidget,
)

_AUTO_TOOLTIP = "_audion_auto_tooltip"
TOOLTIP_WAKEUP_DELAY_MS = 1500


class AudionTooltipStyle(QProxyStyle):
    def __init__(self, base_style: QStyle, delay_ms: int = TOOLTIP_WAKEUP_DELAY_MS) -> None:
        super().__init__(base_style)
        self._tooltip_wakeup_delay_ms = int(delay_ms)

    def set_tooltip_wakeup_delay(self, delay_ms: int) -> None:
        self._tooltip_wakeup_delay_ms = int(delay_ms)

    def styleHint(self, hint, option=None, widget=None, returnData=None):  # noqa: N802
        if hint == QStyle.SH_ToolTip_WakeUpDelay:
            return self._tooltip_wakeup_delay_ms
        return super().styleHint(hint, option, widget, returnData)


def apply_tooltip_delay(app, delay_ms: int = TOOLTIP_WAKEUP_DELAY_MS) -> AudionTooltipStyle:
    """Install the global Qt style hook that controls tooltip wake-up delay."""
    style = app.style()
    if isinstance(style, AudionTooltipStyle):
        style.set_tooltip_wakeup_delay(delay_ms)
        return style
    proxy = AudionTooltipStyle(style, delay_ms)
    app.setStyle(proxy)
    app._audion_tooltip_style = proxy
    return proxy


def _clean(text: str | None) -> str:
    if not text:
        return ""
    value = re.sub(r"<[^>]+>", " ", str(text))
    value = value.replace("&&", "\0").replace("&", "").replace("\0", "&")
    value = re.sub(r"\s+", " ", value).strip(" \t\r\n:")
    return value


def _object_hint(widget: QWidget) -> str:
    name = _clean(widget.objectName())
    if not name or name.startswith("qt_"):
        return ""
    name = re.sub(r"^(btn|cmb|chk|spin|txt|lbl)_", "", name)
    return _clean(name.replace("_", " "))


def _layout_contains(layout, widget: QWidget) -> bool:
    if layout is None:
        return False
    for index in range(layout.count()):
        item = layout.itemAt(index)
        child = item.widget()
        if child is widget:
            return True
        if child is not None and child.isAncestorOf(widget):
            return True
        nested = item.layout()
        if nested is not None and _layout_contains(nested, widget):
            return True
    return False


def _form_label_in_layout(layout, widget: QWidget) -> str:
    if layout is None:
        return ""
    if isinstance(layout, QFormLayout):
        for row in range(layout.rowCount()):
            field_item = layout.itemAt(row, QFormLayout.FieldRole)
            if field_item is None:
                continue
            field_widget = field_item.widget()
            field_layout = field_item.layout()
            field_matches = (
                field_widget is widget
                or (field_widget is not None and field_widget.isAncestorOf(widget))
                or (field_layout is not None and _layout_contains(field_layout, widget))
            )
            if not field_matches:
                continue
            label_item = layout.itemAt(row, QFormLayout.LabelRole)
            if label_item is None:
                return ""
            label = label_item.widget()
            if isinstance(label, QLabel):
                return _clean(label.text())
    for index in range(layout.count()):
        nested = layout.itemAt(index).layout()
        label = _form_label_in_layout(nested, widget)
        if label:
            return label
    return ""


def _form_label(widget: QWidget) -> str:
    parent = widget.parentWidget()
    while parent is not None:
        label = _form_label_in_layout(parent.layout(), widget)
        if label:
            return label
        parent = parent.parentWidget()
    return ""


def _card_title(widget: QWidget) -> str:
    parent = widget.parentWidget()
    while parent is not None:
        if parent.objectName() == "Card":
            for child in [*parent.findChildren(QLabel), *parent.findChildren(QAbstractButton)]:
                if child.property("role") == "card-title":
                    text = _clean(child.text())
                    if text:
                        return text
            return ""
        parent = parent.parentWidget()
    return ""


def _ancestor_combo(widget: QWidget) -> QComboBox | None:
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QComboBox):
            return parent
        parent = parent.parentWidget()
    return None


def _combo_owner(widget: QWidget, combos: list[QComboBox]) -> QComboBox | None:
    ancestor = _ancestor_combo(widget)
    if ancestor is not None:
        return ancestor
    for combo in combos:
        if combo is widget:
            continue
        line_edit = combo.lineEdit()
        if line_edit is widget or (line_edit is not None and line_edit.isAncestorOf(widget)):
            return combo
        view = combo.view()
        if view is widget or (view is not None and view.isAncestorOf(widget)):
            return combo
    return None


def _combo_tip(combo: QComboBox) -> str:
    return _clean(combo.toolTip()) or _form_label(combo) or _object_hint(combo) or _clean(combo.currentText())


def _widget_tip(widget: QWidget, combo_owner: QComboBox | None = None) -> str:
    if combo_owner is not None and combo_owner is not widget:
        return _combo_tip(combo_owner)
    if isinstance(widget, QAbstractButton):
        text = _clean(widget.text())
        if text:
            return text
    if isinstance(widget, QComboBox):
        return _combo_tip(widget)
    if isinstance(widget, QLineEdit):
        return _form_label(widget) or _clean(widget.placeholderText()) or _object_hint(widget)
    if isinstance(widget, (QPlainTextEdit, QTextEdit)):
        return _form_label(widget) or _clean(widget.placeholderText()) or _card_title(widget) or _object_hint(widget)
    if isinstance(widget, (QAbstractSpinBox, QSlider, QProgressBar, QAbstractItemView)):
        return _form_label(widget) or _card_title(widget) or _object_hint(widget)
    if isinstance(widget, QGroupBox):
        return _clean(widget.title())
    return _object_hint(widget) or _card_title(widget)


def seed_tooltips(root: QWidget) -> None:
    """Fill empty tooltips for interactive controls under ``root``."""
    widgets = [root, *root.findChildren(QWidget)]
    combos = [widget for widget in widgets if isinstance(widget, QComboBox)]
    for widget in widgets:
        if isinstance(widget, QTabWidget):
            for index in range(widget.count()):
                if not widget.tabToolTip(index):
                    widget.setTabToolTip(index, _clean(widget.tabText(index)))
        if isinstance(widget, QComboBox):
            for index in range(widget.count()):
                if not widget.itemData(index, Qt.ToolTipRole):
                    widget.setItemData(index, _clean(widget.itemText(index)), Qt.ToolTipRole)

        if not isinstance(
            widget,
            (
                QAbstractButton,
                QComboBox,
                QLineEdit,
                QPlainTextEdit,
                QTextEdit,
                QAbstractSpinBox,
                QSlider,
                QProgressBar,
                QAbstractItemView,
                QGroupBox,
            ),
        ):
            continue

        manual = bool(widget.toolTip()) and not bool(widget.property(_AUTO_TOOLTIP))
        if manual:
            continue
        tip = _widget_tip(widget, _combo_owner(widget, combos))
        if tip:
            widget.setToolTip(tip)
            widget.setProperty(_AUTO_TOOLTIP, True)
