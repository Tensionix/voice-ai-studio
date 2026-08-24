"""Small reusable widgets: the drag-and-drop input zone and the queue table model."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QToolButton,
    QApplication,
    QSizePolicy,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)


class ElidedLabel(QLabel):
    """Single-line read-only text with native, resize-aware ellipsis."""

    def __init__(
        self,
        text: str = "",
        parent=None,
        *,
        mode=Qt.ElideRight,
        horizontal_inset: int = 0,
    ):
        self._full_text = ""
        self._auto_tooltip = ""
        self._elide_mode = mode
        self._horizontal_inset = max(0, int(horizontal_inset))
        super().__init__("", parent)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full_text = str(text or "")
        self._sync_elision()

    def setFullText(self, text: str) -> None:  # noqa: N802
        self.setText(text)

    def fullText(self) -> str:  # noqa: N802
        return self._full_text

    def text(self) -> str:  # noqa: N802
        # Keep the semantic/accessibility value intact for callers; only the
        # QLabel paint payload stored in Qt is elided.
        return self._full_text

    def _sync_elision(self) -> None:
        width = max(20, self.contentsRect().width() - self._horizontal_inset)
        shown = self.fontMetrics().elidedText(
            self._full_text, self._elide_mode, width
        )
        QLabel.setText(self, shown)
        current_tooltip = self.toolTip()
        if shown != self._full_text:
            if not current_tooltip or current_tooltip == self._auto_tooltip:
                self._auto_tooltip = self._full_text
                self.setToolTip(self._auto_tooltip)
        elif current_tooltip == self._auto_tooltip:
            self.setToolTip("")
            self._auto_tooltip = ""

    def minimumSizeHint(self):  # noqa: N802
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._sync_elision()

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._sync_elision()


class ElidedComboBox(QComboBox):
    """Non-editable combo whose closed label elides; popup strings stay full."""

    def __init__(self, parent=None, *, mode=Qt.ElideRight):
        super().__init__(parent)
        self._elide_mode = mode
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(4)

    def paintEvent(self, event):  # noqa: N802, ARG002
        if self.isEditable():
            super().paintEvent(event)
            return
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        edit_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxEditField,
            self,
        )
        icon_room = option.iconSize.width() + 6 if not option.currentIcon.isNull() else 0
        option.currentText = self.fontMetrics().elidedText(
            option.currentText,
            self._elide_mode,
            max(20, edit_rect.width() - icon_room),
        )
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)


class _InlineSpinButtons:
    """Two large horizontal chevrons embedded in a spin-box field."""

    def _init_inline_buttons(self) -> None:
        self.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.setProperty("inlineButtons", True)
        self._step_down_button = QToolButton(self)
        self._step_down_button.setObjectName("SpinStepDown")
        self._step_up_button = QToolButton(self)
        self._step_up_button.setObjectName("SpinStepUp")
        for button in (self._step_down_button, self._step_up_button):
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setAutoRepeat(True)
            button.setAutoRepeatDelay(350)
            button.setAutoRepeatInterval(80)
        self._step_down_button.setAccessibleName("Decrease")
        self._step_up_button.setAccessibleName("Increase")
        self._step_down_button.setToolTip("Уменьшить / Decrease")
        self._step_up_button.setToolTip("Увеличить / Increase")
        self._step_down_button.clicked.connect(lambda: self.stepDown())
        self._step_up_button.clicked.connect(lambda: self.stepUp())
        self.valueChanged.connect(self._sync_inline_buttons)

    def _sync_inline_buttons(self, *_args) -> None:
        enabled = self.stepEnabled()
        self._step_down_button.setEnabled(bool(enabled & QAbstractSpinBox.StepDownEnabled))
        self._step_up_button.setEnabled(bool(enabled & QAbstractSpinBox.StepUpEnabled))

    def _place_inline_buttons(self) -> None:
        size = max(28, min(32, self.height() - 8))
        gap = 2
        right = 6
        y = max(0, (self.height() - size) // 2)
        up_x = self.width() - right - size
        self._step_down_button.setGeometry(up_x - gap - size, y, size, size)
        self._step_up_button.setGeometry(up_x, y, size, size)
        self._step_down_button.raise_()
        self._step_up_button.raise_()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._place_inline_buttons()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._place_inline_buttons()
        self._sync_inline_buttons()

    def wheelEvent(self, event):  # noqa: N802
        # Settings panels live inside scroll areas.  Consuming the wheel here
        # makes accidental value changes (especially overlay scale) too easy.
        event.ignore()


class InlineSpinBox(_InlineSpinButtons, QSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_inline_buttons()


class InlineDoubleSpinBox(_InlineSpinButtons, QDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_inline_buttons()


class Card(QFrame):
    """A titled section drawn as a flat card: header label + rule + body.

    Avoids QGroupBox's title-on-border rendering (the recurring overlap/notch
    source). When `checkable`, the title is a checkbox and the body is disabled
    while off, mirroring the QGroupBox checkable API (`isChecked`/`setChecked`/
    `toggled`/`setTitle`)."""

    toggled = Signal(bool)

    def __init__(self, title: str, *, checkable: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self._checkable = checkable

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 18)
        outer.setSpacing(12)

        self._head = QHBoxLayout()
        # Qt styles paint a rounded 1 px outline on the control boundary. A
        # small internal gutter prevents that outline from being clipped at
        # fractional Windows DPI and gives longer RU labels breathing room.
        self._head.setContentsMargins(2, 2, 2, 2)
        self._head.setSpacing(8)
        if checkable:
            self._check: QCheckBox | None = QCheckBox(title)
            self._check.setProperty("role", "card-title")
            self._check.toggled.connect(self.toggled)
            self._check.toggled.connect(self._on_toggle)
            self._head.addWidget(self._check)
            self._title_label: QLabel | None = None
        else:
            self._check = None
            self._title_label = QLabel(title)
            self._title_label.setProperty("role", "card-title")
            self._head.addWidget(self._title_label)
        self._head.addStretch(1)
        outer.addLayout(self._head)

        rule = QFrame()
        rule.setObjectName("CardRule")
        rule.setFrameShape(QFrame.HLine)
        rule.setFixedHeight(1)
        outer.addWidget(rule)

        self._body = QWidget()
        self._body.setObjectName("CardBody")
        outer.addWidget(self._body, 1)

    def body(self) -> QWidget:
        return self._body

    def addHeaderWidget(self, widget: QWidget) -> None:  # noqa: N802
        self._head.addWidget(widget)

    def addHeaderLeadingWidget(self, widget: QWidget) -> None:  # noqa: N802
        self._head.insertWidget(0, widget)

    def setTitle(self, text: str) -> None:  # noqa: N802 (Qt-style API)
        if self._check is not None:
            self._check.setText(text)
        elif self._title_label is not None:
            self._title_label.setText(text)

    def isChecked(self) -> bool:  # noqa: N802
        return self._check.isChecked() if self._check is not None else True

    def setChecked(self, on: bool) -> None:  # noqa: N802
        if self._check is not None:
            self._check.setChecked(on)

    def _on_toggle(self, on: bool) -> None:
        self._body.setEnabled(on)


class CurrentPageStackedWidget(QStackedWidget):
    """A stacked widget whose layout size follows only the visible page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.currentChanged.connect(self._refresh_geometry)

    def sizeHint(self):  # noqa: N802
        widget = self.currentWidget()
        return widget.sizeHint() if widget is not None else super().sizeHint()

    def minimumSizeHint(self):  # noqa: N802
        widget = self.currentWidget()
        return widget.minimumSizeHint() if widget is not None else super().minimumSizeHint()

    def _refresh_geometry(self) -> None:
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None:
            parent.updateGeometry()


class UnderlineTabBar(QWidget):
    """Compact text tabs with an underline-selected state.

    The app uses these instead of heavy tab frames: they keep navigation visible
    without adding another card-like container around already dense tool areas.
    """

    currentChanged = Signal(int)
    orderChanged = Signal(list)

    def __init__(self, tabs: list[tuple[str, str]] | None = None, parent=None, *, fill_width: bool = False):
        super().__init__(parent)
        self._keys: list[str] = []
        self._buttons: dict[str, QPushButton] = {}
        self._fill_width = fill_width
        self._drag_key = ""
        self._drag_start = QPoint()
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(self._on_clicked)

        self._layout = QHBoxLayout(self)
        # Reserve pixels for the active underline and translated text. Without
        # this, 125/150% Windows scaling can clip the first/last antialiased pixel.
        self._layout.setContentsMargins(2, 2, 2, 3)
        self._layout.setSpacing(10)

        for key, text in tabs or []:
            self.add_tab(key, text)
        if not self._fill_width:
            self._layout.addStretch(1)

    def add_tab(self, key: str, text: str) -> QPushButton:
        index = len(self._keys)
        button = QPushButton(text)
        button.setCheckable(True)
        button.setProperty("variant", "section-tab")
        button.setCursor(Qt.PointingHandCursor)
        button.setSizePolicy(
            QSizePolicy.Expanding if self._fill_width else QSizePolicy.Preferred,
            QSizePolicy.Fixed,
        )
        button.installEventFilter(self)
        self._group.addButton(button, index)
        self._keys.append(key)
        self._buttons[key] = button
        self._layout.insertWidget(index, button, 1 if self._fill_width else 0)
        return button

    def buttons(self) -> dict[str, QPushButton]:
        return self._buttons

    def keys(self) -> list[str]:
        return list(self._keys)

    def current_index(self) -> int:
        return self._group.checkedId()

    def set_current_index(self, index: int) -> None:
        if 0 <= index < len(self._keys):
            self._buttons[self._keys[index]].setChecked(True)

    def set_current_key(self, key: str) -> None:
        button = self._buttons.get(key)
        if button is not None:
            button.setChecked(True)

    def set_tab_text(self, key: str, text: str) -> None:
        button = self._buttons.get(key)
        if button is not None:
            button.setText(text)

    def set_order(self, keys: list[str]) -> None:
        wanted = [key for key in keys if key in self._buttons]
        wanted.extend(key for key in self._keys if key not in wanted)
        if wanted == self._keys:
            return
        self._keys = wanted
        self._rebuild_order()

    def move_tab(self, key: str, target_key: str) -> None:
        if key == target_key or key not in self._keys or target_key not in self._keys:
            return
        old = self._keys.index(key)
        new = self._keys.index(target_key)
        self._keys.pop(old)
        self._keys.insert(new, key)
        self._rebuild_order()
        self.orderChanged.emit(self.keys())

    def _on_clicked(self, index: int) -> None:
        self.currentChanged.emit(index)

    def _rebuild_order(self) -> None:
        for key in self._keys:
            self._layout.removeWidget(self._buttons[key])
        for index, key in enumerate(self._keys):
            button = self._buttons[key]
            self._layout.insertWidget(index, button, 1 if self._fill_width else 0)
            self._group.setId(button, index)

    def eventFilter(self, watched, event):  # noqa: N802
        key = next((k for k, button in self._buttons.items() if button is watched), "")
        if not key:
            return super().eventFilter(watched, event)

        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._drag_key = key
            self._drag_start = event.position().toPoint()
        elif (
            event.type() == QEvent.MouseMove
            and self._drag_key
            and event.buttons() & Qt.LeftButton
        ):
            if (event.position().toPoint() - self._drag_start).manhattanLength() < QApplication.startDragDistance():
                return super().eventFilter(watched, event)
            target_key = self._key_at_global(event.globalPosition().toPoint())
            if target_key and target_key != self._drag_key:
                self.move_tab(self._drag_key, target_key)
        elif event.type() == QEvent.MouseButtonRelease:
            self._drag_key = ""
        return super().eventFilter(watched, event)

    def _key_at_global(self, point) -> str:
        local = self.mapFromGlobal(point)
        for key in self._keys:
            if self._buttons[key].geometry().contains(local):
                return key
        return ""


class QueueCheckDelegate(QStyledItemDelegate):
    """Paint the queue selector with the same crisp accent language as the UI."""

    def paint(self, painter, option, index) -> None:
        base = QStyleOptionViewItem(option)
        self.initStyleOption(base, index)
        base.features &= ~QStyleOptionViewItem.HasCheckIndicator
        base.text = ""
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, base, painter, option.widget)

        side = min(18, max(14, option.rect.height() - 18))
        x = option.rect.x() + (option.rect.width() - side) / 2.0
        y = option.rect.y() + (option.rect.height() - side) / 2.0
        rect = option.rect.__class__(round(x), round(y), side, side)
        checked = index.data(Qt.CheckStateRole) == Qt.Checked
        accent = option.palette.highlight().color()
        foreground = option.palette.highlightedText().color()

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(foreground if checked else option.palette.mid().color(), 1.2))
        painter.setBrush(foreground if checked else option.palette.base().color())
        painter.drawRoundedRect(rect, 4, 4)
        if checked:
            path = QPainterPath()
            path.moveTo(rect.left() + side * 0.24, rect.top() + side * 0.53)
            path.lineTo(rect.left() + side * 0.43, rect.top() + side * 0.72)
            path.lineTo(rect.left() + side * 0.78, rect.top() + side * 0.31)
            pen = QPen(accent, max(1.7, side * 0.12))
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        del option
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            checked = index.data(Qt.CheckStateRole) == Qt.Checked
            return model.setData(
                index,
                Qt.Unchecked if checked else Qt.Checked,
                Qt.CheckStateRole,
            )
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Space, Qt.Key_Select):
            checked = index.data(Qt.CheckStateRole) == Qt.Checked
            return model.setData(
                index,
                Qt.Unchecked if checked else Qt.Checked,
                Qt.CheckStateRole,
            )
        return False


class QueueTableView(QTableView):
    """The queue table doubles as the drop target — drag files/folders straight
    onto the list (no separate drop-zone needed)."""

    paths_dropped = Signal(list)  # list[str]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._placeholder = ""
        self._corner_radius = 10

    def setPlaceholder(self, text: str) -> None:  # noqa: N802 (Qt-style API)
        self._placeholder = text
        self.viewport().update()

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        model = self.model()
        if self._placeholder and (model is None or model.rowCount() == 0):
            painter = QPainter(self.viewport())
            painter.setPen(QColor("#6B7C90"))
            painter.drawText(self.viewport().rect(), Qt.AlignCenter, self._placeholder)
            painter.end()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply_rounded_viewport_mask()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._apply_rounded_viewport_mask()

    def _apply_rounded_viewport_mask(self) -> None:
        # Clip the entire table, including the horizontal header. Masking only
        # the viewport rounded the first selected row while leaving the header's
        # outer corners square and visibly clipped against the card border.
        self.viewport().clearMask()
        rect = self.rect()
        if rect.isEmpty():
            return
        path = QPainterPath()
        path.addRoundedRect(rect, self._corner_radius, self._corner_radius)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))


class RoundedPlainTextEdit(QPlainTextEdit):
    """Plain text surface whose viewport is clipped to the visual border radius."""

    paths_dropped = Signal(list)  # list[str]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._corner_radius = 10

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply_rounded_viewport_mask()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._apply_rounded_viewport_mask()

    def _apply_rounded_viewport_mask(self) -> None:
        rect = self.viewport().rect()
        if rect.isEmpty():
            return
        path = QPainterPath()
        path.addRoundedRect(rect, self._corner_radius, self._corner_radius)
        self.viewport().setMask(QRegion(path.toFillPolygon().toPolygon()))

    def dragEnterEvent(self, event):  # noqa: N802 - Qt naming
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):  # noqa: N802
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()


class QueueModel(QAbstractTableModel):
    """Holds the list of source files and their live status for the queue view."""

    def __init__(self, status_label: Callable[[str], str], status_color: Callable[[str], str], parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []          # {"path": Path, "status": str, "checked": bool}
        self._index: dict[str, int] = {}     # resolved path str -> row
        self._status_label = status_label
        self._status_color = status_color
        self._headers = ["", "", ""]

    # --- header text is refreshed on language change -------------------------
    def set_headers(self, select_header: str, file_header: str, status_header: str) -> None:
        self._headers = [select_header, file_header, status_header]
        if self._rows:
            self.headerDataChanged.emit(Qt.Horizontal, 0, 2)

    # --- Qt model API --------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 3

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self._headers[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        if role == Qt.CheckStateRole and index.column() == 0:
            return Qt.Checked if row["checked"] else Qt.Unchecked
        if role == Qt.DisplayRole:
            if index.column() == 0:
                return ""
            if index.column() == 1:
                return Path(row["path"]).name
            return self._status_label(row["status"])
        if role == Qt.ForegroundRole and index.column() == 2:
            return QColor(self._status_color(row["status"]))
        if role == Qt.ToolTipRole and index.column() == 1:
            return str(row["path"])
        if role == Qt.TextAlignmentRole and index.column() == 0:
            return Qt.AlignCenter
        return None

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and index.column() == 0:
            flags |= Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable
        return flags

    def setData(self, index, value, role=Qt.EditRole):  # noqa: N802
        if not index.isValid() or index.column() != 0 or role != Qt.CheckStateRole:
            return False
        checked = value == Qt.Checked or value == Qt.CheckState.Checked
        if self._rows[index.row()]["checked"] == checked:
            return True
        self._rows[index.row()]["checked"] = checked
        self.dataChanged.emit(index, index, [Qt.CheckStateRole])
        return True

    # --- mutations -----------------------------------------------------------
    def set_files(self, paths: list[Path]) -> None:
        self.beginResetModel()
        self._rows = [
            {"path": p, "status": "pending", "checked": False}
            for p in paths
        ]
        self._index = {str(Path(p).resolve()).lower(): i for i, p in enumerate(paths)}
        self.endResetModel()

    def clear(self) -> None:
        self.set_files([])

    def remove_rows(self, rows: list[int]) -> None:
        selected = {row for row in rows if 0 <= row < len(self._rows)}
        if not selected:
            return
        self.beginResetModel()
        self._rows = [row for index, row in enumerate(self._rows) if index not in selected]
        self._index = {
            str(Path(row["path"]).resolve()).lower(): index
            for index, row in enumerate(self._rows)
        }
        self.endResetModel()

    def files(self) -> list[Path]:
        return [Path(r["path"]) for r in self._rows]

    def checked_rows(self) -> list[int]:
        return [index for index, row in enumerate(self._rows) if row["checked"]]

    def checked_files(self) -> list[Path]:
        return [Path(self._rows[index]["path"]) for index in self.checked_rows()]

    def is_empty(self) -> bool:
        return not self._rows

    def update_status(self, path_str: str, status: str) -> None:
        key = str(Path(path_str).resolve()).lower()
        row = self._index.get(key)
        if row is None:
            return
        self._rows[row]["status"] = status
        top = self.index(row, 0)
        bottom = self.index(row, 2)
        self.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.ForegroundRole])

    def reset_statuses(self) -> None:
        for row in self._rows:
            row["status"] = "pending"
        if self._rows:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self._rows) - 1, 2), [Qt.DisplayRole, Qt.ForegroundRole]
            )
