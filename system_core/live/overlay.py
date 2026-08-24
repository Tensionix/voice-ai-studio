"""Frameless, always-on-top dictation overlay (iteration 4).

A small floating bar near the bottom of the active screen that shows live state
and the running transcript while you dictate, then fades out. It does not accept
keyboard focus, so the target app remains the auto-paste destination, but its
Stop and Cancel controls deliberately accept mouse clicks.

Focus/click discipline (the whole reason this is a separate window):
  - flags: Frameless | StaysOnTop | Tool | WindowDoesNotAcceptFocus
  - attrs: WA_ShowWithoutActivating (show without stealing focus),
           WA_TranslucentBackground (rounded panel)
  - never call raise_()/activateWindow() — StaysOnTop keeps it visible.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (
    QEasingCurve,
    QElapsedTimer,
    QEvent,
    QPointF,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..ui.icons import icon, record_icon

_HIDE_AFTER_MS = 1500  # how long the final transcript lingers before hiding
_PARTIAL_UPDATE_MS = 80
_MAX_TEXT_CHARS = 1200
_BASE_HEIGHT = 52
_SCALE_MIN = 70
_SCALE_MAX = 130
_SCALE_STEP = 5


class _RoundedPanel(QFrame):
    """Paint a real antialiased capsule; Qt stylesheet radii were DPI-fragile."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._background = QColor("#17212B")
        self._border = QColor("#46505E")
        self._scale = 1.0

    def set_scale(self, scale: float) -> None:
        self._scale = max(0.1, float(scale))
        self.update()

    def set_colors(self, background: str, border: str) -> None:
        self._background = QColor(background)
        self._border = QColor(border)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        inset = max(1, round(self._scale))
        rect = self.rect().adjusted(inset, inset, -inset, -inset)
        # The expanded controller is a rounded rectangle, not a capsule: its
        # squarer silhouette visually agrees with the drag handle and Stop.
        # The 12 px idle target still naturally resolves to a pill.
        radius = max(5.0 * self._scale, min(16.0 * self._scale, rect.height() / 2.0))
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.fillPath(path, self._background)
        painter.setPen(QPen(self._border, max(1.0, self._scale)))
        painter.drawPath(path)


class _StopButton(QPushButton):
    """Paint a true rounded square even if a platform style widens buttons."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._scale = 1.0

    def set_scale(self, scale: float) -> None:
        self._scale = max(0.1, float(scale))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        del event
        color = "#6F4138" if not self.isEnabled() else "#F25532"
        if self.isEnabled() and self.isDown():
            color = "#D94427"
        elif self.isEnabled() and self.underMouse():
            color = "#FF6845"
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        inset = max(1, round(2 * self._scale))
        side = min(round(30 * self._scale), self.width() - inset, self.height() - inset)
        x = (self.width() - side) / 2.0
        y = (self.height() - side) / 2.0
        path = QPainterPath()
        radius = 8.0 * self._scale
        path.addRoundedRect(x, y, side, side, radius, radius)
        painter.fillPath(path, QColor(color))


class _CloseButton(QPushButton):
    """Draw a crisp, optically centred close mark instead of a font glyph."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._scale = 1.0

    def set_scale(self, scale: float) -> None:
        self._scale = max(0.1, float(scale))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self.isDown() or self.underMouse():
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#303038" if self.isDown() else "#24242A"))
            hover_rect = self.rect().adjusted(1, 1, -1, -1)
            hover_radius = 9.0 * self._scale
            painter.drawRoundedRect(hover_rect, hover_radius, hover_radius)

        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        arm = 7.5 * self._scale
        pen = QPen(QColor("#F4F4F6"), max(1.8, 2.0 * self._scale))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(
            QPointF(center.x() - arm, center.y() - arm),
            QPointF(center.x() + arm, center.y() + arm),
        )
        painter.drawLine(
            QPointF(center.x() + arm, center.y() - arm),
            QPointF(center.x() - arm, center.y() + arm),
        )


class _DragHandle(QLabel):
    """A visible three-dot drag handle with DPI-independent geometry."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scale = 1.0

    def set_scale(self, scale: float) -> None:
        self._scale = max(0.1, float(scale))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#87919D"))
        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        radius = 2.25 * self._scale
        gap = 6.5 * self._scale
        for offset in (-gap, 0.0, gap):
            painter.drawEllipse(QPointF(center.x(), center.y() + offset), radius, radius)


class LiveOverlay(QWidget):
    record_requested = Signal()
    pause_requested = Signal(bool)
    stop_requested = Signal()
    cancel_requested = Signal()
    context_menu_requested = Signal(object)

    def __init__(
        self,
        tr,
        tokens: dict,
        parent: Optional[QWidget] = None,
        *,
        scale_percent: int = 100,
    ):
        super().__init__(
            parent,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus,
        )
        self._tr = tr
        self._dot_color = "#378ADD"
        self._blink_on = True
        self._drag_offset = None
        self._user_positioned = False
        self._anchor_center = None
        self._compact = False
        self._ready = False
        self._record_transition = False
        self._file_recording = False
        self._file_recording_paused = False
        self._file_recording_cancelling = False
        self._record_action_key = "overlay_record"
        self._scale_percent = 100
        self._scale = 1.0
        self._expanded_height = _BASE_HEIGHT
        self._stable_width: int | None = None
        self._tokens = tokens or {}
        self._pending_text = ""
        self._pending_dot = ("color-accent-primary", "#378ADD")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)

        outer = QVBoxLayout(self)
        self._outer = outer
        outer.setContentsMargins(0, 0, 0, 0)
        self._panel = _RoundedPanel(self)
        self._panel.setObjectName("LiveOverlayPanel")
        outer.addWidget(self._panel)

        row = QHBoxLayout(self._panel)
        self._row = row
        row.setContentsMargins(15, 3, 22, 3)
        row.setSpacing(12)
        self._dot = QLabel("●")
        self._dot.setObjectName("LiveOverlayDot")
        self._text = QLabel("")
        self._text.setObjectName("LiveOverlayText")
        self._text.setWordWrap(False)
        self._drag_handle = _DragHandle()
        self._drag_handle.setObjectName("LiveOverlayDrag")
        self._drag_handle.setAlignment(Qt.AlignCenter)
        self._drag_handle.setFixedSize(24, 38)
        self._drag_handle.setCursor(Qt.SizeAllCursor)
        self._drag_handle.installEventFilter(self)
        self._ready_drag_handle = _DragHandle(self._panel)
        self._ready_drag_handle.setObjectName("LiveOverlayReadyDrag")
        self._ready_drag_handle.setAlignment(Qt.AlignCenter)
        self._ready_drag_handle.setFixedSize(24, 38)
        self._ready_drag_handle.setCursor(Qt.SizeAllCursor)
        self._ready_drag_handle.installEventFilter(self)
        self._ready_drag_handle.setContextMenuPolicy(Qt.CustomContextMenu)
        self._ready_drag_handle.customContextMenuRequested.connect(
            lambda point: self._request_context_menu(
                self._ready_drag_handle.mapToGlobal(point)
            )
        )
        self._ready_drag_handle.hide()
        self._meter = QLabel("▁▁▁▁")
        self._meter.setObjectName("LiveOverlayMeter")
        self._meter.setFixedWidth(42)
        self._cloud = QLabel("☁ …")
        self._cloud.setObjectName("LiveOverlayCloud")
        self._cloud.setAlignment(Qt.AlignCenter)
        self._cloud.setFixedWidth(56)
        self._elapsed = QLabel("00:00")
        self._elapsed.setObjectName("LiveOverlayElapsed")
        self._elapsed.setAlignment(Qt.AlignCenter)
        self._elapsed.setFixedWidth(90)
        self._stop = _StopButton(self._tr.tr("stop"))
        self._stop.setObjectName("LiveOverlayStop")
        self._stop.setText("")
        self._stop.setAccessibleName(self._tr.tr("stop"))
        self._stop.setFocusPolicy(Qt.NoFocus)
        self._stop.setFixedSize(34, 34)
        self._stop.clicked.connect(self._request_stop)
        self._pause = QPushButton()
        self._pause.setObjectName("LiveOverlayPause")
        self._pause.setAccessibleName(self._tr.tr("pause"))
        self._pause.setFocusPolicy(Qt.NoFocus)
        self._pause.setFixedSize(34, 34)
        self._pause.clicked.connect(self._request_pause)
        self._cancel = _CloseButton()
        self._cancel.setObjectName("LiveOverlayCancel")
        self._cancel.setAccessibleName(self._tr.tr("cancel"))
        self._cancel.setFocusPolicy(Qt.NoFocus)
        self._cancel.setFixedSize(34, 34)
        self._cancel.clicked.connect(self.cancel_requested)
        # The whole ready capsule is the Record button.  Keeping it out of the
        # row layout means the microphone can never reflow or re-anchor the
        # launcher: its hit area and geometry are exactly the rounded panel.
        self._record = QPushButton("", self._panel)
        self._record.setObjectName("LiveOverlayRecord")
        self._record.setAccessibleName(self._tr.tr(self._record_action_key))
        self._record.setIcon(icon("mic", "#D7D9DE", 32))
        self._record.setIconSize(QSize(32, 32))
        self._record.setFocusPolicy(Qt.NoFocus)
        self._record.clicked.connect(self._request_record)
        self._record.setContextMenuPolicy(Qt.CustomContextMenu)
        self._record.customContextMenuRequested.connect(
            lambda point: self._request_context_menu(self._record.mapToGlobal(point))
        )
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda point: self._request_context_menu(self.mapToGlobal(point))
        )
        self._record_opacity = QGraphicsOpacityEffect(self._record)
        self._record.setGraphicsEffect(self._record_opacity)
        self._record_fade = QPropertyAnimation(self._record_opacity, b"opacity", self)
        # The former shorter fade was technically animated but visually read as an abrupt swap on
        # high-refresh displays.  Keep the launcher still long enough for the mic
        # to visibly dissolve before the recording controller appears.
        self._record_fade.setDuration(220)
        self._record_fade.setStartValue(1.0)
        self._record_fade.setEndValue(0.0)
        self._record_fade.setEasingCurve(QEasingCurve.OutCubic)
        self._record_fade.finished.connect(self._finish_record_request)
        self._model_switch_callback = None
        self._window_fade_mode = ""
        self._window_fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._window_fade.finished.connect(self._finish_window_fade)
        row.addWidget(self._drag_handle, 0, Qt.AlignVCenter)
        row.addWidget(self._cancel, 0, Qt.AlignVCenter)
        row.addWidget(self._meter, 0, Qt.AlignVCenter)
        row.addWidget(self._dot, 0, Qt.AlignVCenter)
        row.addWidget(self._text, 1)
        row.addWidget(self._cloud, 0, Qt.AlignVCenter)
        row.addWidget(self._elapsed, 0, Qt.AlignVCenter)
        row.addWidget(self._pause, 0, Qt.AlignVCenter)
        row.addWidget(self._stop, 0, Qt.AlignVCenter)

        self._expanded_widgets = (
            self._drag_handle,
            self._cancel,
            self._meter,
            self._dot,
            self._text,
            self._cloud,
            self._elapsed,
            self._pause,
            self._stop,
        )

        self._elapsed_clock = QElapsedTimer()
        self._elapsed_accumulated_ms = 0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(250)
        self._elapsed_timer.timeout.connect(self._update_elapsed)

        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(480)
        self._blink_timer.timeout.connect(self._toggle_blink)

        self._idle_collapse_timer = QTimer(self)
        self._idle_collapse_timer.setSingleShot(True)
        self._idle_collapse_timer.setInterval(280)
        self._idle_collapse_timer.timeout.connect(self.show_idle)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.show_idle)

        self._partial_timer = QTimer(self)
        self._partial_timer.setSingleShot(True)
        self._partial_timer.setInterval(_PARTIAL_UPDATE_MS)
        self._partial_timer.timeout.connect(self._flush_pending)

        self.set_scale_percent(scale_percent)

    def _px(self, value: float) -> int:
        if value <= 0:
            return 0
        return max(1, round(value * self._scale))

    @property
    def scale_percent(self) -> int:
        return self._scale_percent

    def set_scale_percent(self, percent: int) -> None:
        try:
            requested = int(percent)
        except (TypeError, ValueError):
            requested = 100
        requested = round(requested / _SCALE_STEP) * _SCALE_STEP
        self._scale_percent = max(_SCALE_MIN, min(_SCALE_MAX, requested))
        self._scale = self._scale_percent / 100.0
        self._expanded_height = self._px(_BASE_HEIGHT)

        self._panel.set_scale(self._scale)
        self._stop.set_scale(self._scale)
        self._cancel.set_scale(self._scale)
        self._drag_handle.set_scale(self._scale)
        self._drag_handle.setFixedSize(self._px(24), self._px(38))
        self._ready_drag_handle.set_scale(self._scale)
        self._ready_drag_handle.setFixedSize(self._px(24), self._px(38))
        self._meter.setFixedWidth(self._px(42))
        self._cloud.setFixedWidth(self._px(56))
        self._elapsed.setFixedWidth(self._px(90))
        control_side = self._px(34)
        self._stop.setFixedSize(control_side, control_side)
        self._pause.setFixedSize(control_side, control_side)
        self._cancel.setFixedSize(control_side, control_side)
        self._sync_record_action_visual()
        self._sync_pause_visual()
        if not self._compact:
            self._row.setContentsMargins(
                self._px(15), self._px(3), self._px(22), self._px(3)
            )
        self._row.setSpacing(self._px(12))
        self.apply_tokens(self._tokens)

        center = self.frameGeometry().center() if self.isVisible() else self._anchor_center
        if self._stable_width is not None:
            self._stable_width = self._expanded_width()
            self._resize_around_center(self._stable_width, self._expanded_height, center)
            if self._compact and not self._ready:
                self._set_idle_shape()
        else:
            self._resize_around_center(self._px(560), self._expanded_height, center)

    # --- styling -------------------------------------------------------------
    def apply_tokens(self, tokens: dict) -> None:
        self._tokens = tokens or {}
        text_fg = self._tokens.get("color-text-secondary", "#C9D0D8")
        info_fg = self._tokens.get("color-text-tertiary", "#87919D")
        border = self._tokens.get("color-border-primary", "#2A3A4F")
        self._panel.set_colors("#17212B", border)
        self._panel.setStyleSheet(
            f"""
            QFrame#LiveOverlayPanel {{
                background: transparent; border: none;
            }}
            QLabel#LiveOverlayText {{
                color: {text_fg}; font-size: {self._px(18)}px; font-weight: 700; background: transparent;
            }}
            QLabel#LiveOverlayDot {{ font-size: {self._px(16)}px; background: transparent; }}
            QLabel#LiveOverlayDrag, QLabel#LiveOverlayReadyDrag {{ background: transparent; }}
            QLabel#LiveOverlayMeter {{
                color: #D7D9DE;
                font-size: {self._px(16)}px; font-weight: 700; background: transparent;
            }}
            QLabel#LiveOverlayCloud {{ color: {info_fg}; font-size: {self._px(16)}px; font-weight: 600; background: transparent; }}
            QLabel#LiveOverlayElapsed {{
                color: #F4F4F6; font-size: {self._px(24)}px; font-weight: 700;
                font-variant-numeric: tabular-nums;
                background: transparent;
            }}
            QPushButton#LiveOverlayCancel {{
                background: transparent; border: none; padding: 0;
                min-width: {self._px(34)}px; max-width: {self._px(34)}px;
                min-height: {self._px(34)}px; max-height: {self._px(34)}px;
            }}
            QPushButton#LiveOverlayStop {{
                background: transparent; border: none; padding: 0;
                min-width: {self._px(34)}px; max-width: {self._px(34)}px;
                min-height: {self._px(34)}px; max-height: {self._px(34)}px;
            }}
            QPushButton#LiveOverlayPause {{
                background: transparent; border: none; padding: 0;
                min-width: {self._px(34)}px; max-width: {self._px(34)}px;
                min-height: {self._px(34)}px; max-height: {self._px(34)}px;
            }}
            QPushButton#LiveOverlayPause:hover {{
                background: {self._tokens.get("color-control-hover", "#242F3D")};
                border-radius: {self._px(8)}px;
            }}
            QPushButton#LiveOverlayRecord {{
                background: transparent; border: none; padding: 0;
                color: #F4F4F6; font-size: {self._px(17)}px; font-weight: 700;
            }}
            QPushButton#LiveOverlayRecord:hover,
            QPushButton#LiveOverlayRecord:pressed {{ background: transparent; }}
            """
        )
        self._apply_dot()

    def _apply_dot(self) -> None:
        color = self._dot_color if self._blink_on else "transparent"
        self._dot.setStyleSheet(
            f"color: {color}; font-size: {self._px(16)}px; background: transparent;"
        )

    def set_record_action(self, translation_key: str) -> None:
        """Change the ready action for Live dictation or long-form recording."""
        self._record_action_key = str(translation_key or "overlay_record")
        self._sync_record_action_visual()

    def _sync_record_action_visual(self) -> None:
        text = self._tr.tr(self._record_action_key)
        self._record.setAccessibleName(text)
        if self._record_action_key == "recorder_start":
            icon_side = self._px(25)
            self._record.setText(text)
            self._record.setIcon(record_icon(size=icon_side))
        else:
            icon_side = self._px(32)
            self._record.setText("")
            self._record.setIcon(icon("mic", "#D7D9DE", icon_side))
        self._record.setIconSize(QSize(icon_side, icon_side))

    def _sync_pause_visual(self) -> None:
        key = "resume" if self._file_recording_paused else "pause"
        glyph = "play" if self._file_recording_paused else "pause"
        self._pause.setAccessibleName(self._tr.tr(key))
        side = self._px(19)
        self._pause.setIcon(icon(glyph, "#F4F4F6", side))
        self._pause.setIconSize(QSize(side, side))

    def _set_dot(self, token: str, fallback: str) -> None:
        self._dot_color = self._tokens.get(token, fallback)
        self._apply_dot()

    # --- placement -----------------------------------------------------------
    def _reposition(self) -> None:
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + max(self._px(24), int(geo.height() * 0.055))
        self.move(x, y)
        self._anchor_center = self.frameGeometry().center()

    def _expanded_width(self) -> int:
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        available = screen.availableGeometry().width() if screen else self._px(728)
        return min(self._px(680), max(self._px(420), available - self._px(48)))

    def _resize_around_center(self, width: int, height: int, center=None) -> None:
        if center is None and self.isVisible():
            # The native frame can be nudged by Windows after first show (DPI and
            # work-area correction).  Its current center is therefore more
            # reliable than the cached pre-show anchor.
            center = self.frameGeometry().center()
        if center is None:
            center = self._anchor_center
        # Release the old fixed-size constraint, then resize and move in one
        # native geometry operation. Calling setFixedSize() followed by move()
        # exposes an intermediate top-left-anchored frame on Windows.
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        if center is not None:
            # QRect::center() uses (size - 1) / 2 for even dimensions.
            x = center.x() - (width - 1) // 2
            y = center.y() - (height - 1) // 2
            self.setGeometry(x, y, width, height)
            self._anchor_center = center
        else:
            self.resize(width, height)
        self.setMinimumSize(width, height)
        self.setMaximumSize(width, height)

    def _set_expanded(self) -> None:
        if not self._compact:
            return
        self._compact = False
        self._ready = False
        self._record_transition = False
        self._idle_collapse_timer.stop()
        self.unsetCursor()
        # Idle, ready and recording share one wide native geometry.  Expanding
        # is now only a child-content swap; there is no top-level resize for DWM
        # to re-anchor under the cursor.
        self.clearMask()
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._row.setContentsMargins(
            self._px(15), self._px(3), self._px(22), self._px(3)
        )
        self._record.hide()
        self._ready_drag_handle.hide()
        self._record_opacity.setOpacity(1.0)
        for widget in self._expanded_widgets:
            widget.show()
        self.update()

    def _set_idle_shape(self) -> None:
        idle_width = self._px(120)
        idle_height = self._px(12)
        left = max(0, (self.width() - idle_width) // 2)
        top = max(0, (self.height() - idle_height) // 2)
        self._outer.setContentsMargins(left, top, left, top)
        # Keep the native input region compact, but do not use an elliptical
        # QRegion here: Windows regions are one-bit and visibly staircase a
        # 12 px pill.  The panel's antialiased QPainter path owns the shape.
        self.setMask(QRegion(QRect(left, top, idle_width, idle_height)))

    def show_idle(self) -> None:
        """Persistent thin hover target shown whenever no dictation is active."""
        self._hide_timer.stop()
        self._partial_timer.stop()
        self._elapsed_timer.stop()
        self._blink_timer.stop()
        self._idle_collapse_timer.stop()
        self._record_fade.stop()
        self._compact = True
        self._ready = False
        self._record_transition = False
        self._file_recording = False
        self._file_recording_paused = False
        self._file_recording_cancelling = False
        # Reserve the final controller geometry before anything is visible.
        # The window mask keeps only the central 120x12 hover target interactive
        # while idle, so the transparent wide margins do not block other apps.
        if self._stable_width is None:
            self._stable_width = self._expanded_width()
        center = self.frameGeometry().center() if self.isVisible() else self._anchor_center
        self._resize_around_center(self._stable_width, self._expanded_height, center)
        self._set_idle_shape()
        self._row.setContentsMargins(0, 0, 0, 0)
        for widget in self._expanded_widgets:
            widget.hide()
        self._ready_drag_handle.hide()
        self._record.hide()
        self._record_opacity.setOpacity(1.0)
        self.setCursor(Qt.PointingHandCursor)
        self._present()

    def _show_ready(self) -> None:
        if not self._compact or self._record_transition:
            return
        self._idle_collapse_timer.stop()
        self._ready = True
        self.clearMask()
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.activate()
        self._drag_handle.hide()
        self._record.setGeometry(self._panel.rect())
        self._record_opacity.setOpacity(1.0)
        self._record.show()
        self._record.raise_()
        # Keep the move handle above the full-panel Record hit target.  Moving
        # the overlay must never require starting a microphone capture first.
        self._position_ready_drag_handle()
        self._ready_drag_handle.show()
        self._ready_drag_handle.raise_()
        self._present()

    def _position_ready_drag_handle(self) -> None:
        handle = self._ready_drag_handle
        handle.move(
            self._px(10),
            max(0, (self._panel.height() - handle.height()) // 2),
        )

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().enterEvent(event)
        if self._compact:
            self._show_ready()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().leaveEvent(event)
        if self._compact and not self._record_transition:
            self._idle_collapse_timer.start()

    def _request_record(self) -> None:
        if not self._compact or not self._ready or self._record_transition:
            return
        self._record_transition = True
        self._idle_collapse_timer.stop()
        self._record.setEnabled(False)
        self._record_fade.start()

    def _request_context_menu(self, global_position) -> None:
        if not self._compact:
            return
        self._idle_collapse_timer.stop()
        self.context_menu_requested.emit(global_position)

    def _finish_record_request(self) -> None:
        if not self._record_transition:
            return
        self._record.hide()
        self._record.setEnabled(True)
        self._record_opacity.setOpacity(1.0)
        self.record_requested.emit()

    def fade_out_for_model_switch(self, callback) -> None:
        """Hide the complete native overlay before a resident model is replaced."""
        self._window_fade.stop()
        self._model_switch_callback = callback
        self._window_fade_mode = "out"
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self._finish_window_fade()
            return
        self._window_fade.setDuration(180)
        self._window_fade.setStartValue(self.windowOpacity())
        self._window_fade.setEndValue(0.0)
        self._window_fade.setEasingCurve(QEasingCurve.OutCubic)
        self._window_fade.start()

    def fade_in_after_model_switch(self) -> None:
        self._window_fade.stop()
        self._model_switch_callback = None
        self._window_fade_mode = "in"
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        if not self.isVisible():
            self.setWindowOpacity(1.0)
            self._window_fade_mode = ""
            return
        self._window_fade.setDuration(240)
        self._window_fade.setStartValue(self.windowOpacity())
        self._window_fade.setEndValue(1.0)
        self._window_fade.setEasingCurve(QEasingCurve.InOutCubic)
        self._window_fade.start()

    def _finish_window_fade(self) -> None:
        mode = self._window_fade_mode
        self._window_fade_mode = ""
        if mode == "out":
            self.setWindowOpacity(0.0)
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            callback, self._model_switch_callback = self._model_switch_callback, None
            if callable(callback):
                callback()
        elif mode == "in":
            self.setWindowOpacity(1.0)

    def _present(self) -> None:
        self._hide_timer.stop()
        if not self.isVisible():
            if not self._user_positioned:
                self._reposition()
            self.show()  # WA_ShowWithoutActivating -> does not steal focus

    def _display_text(self, text: str) -> str:
        text = " ".join(str(text or "").split())
        if len(text) > _MAX_TEXT_CHARS:
            return "..." + text[-(_MAX_TEXT_CHARS - 3):]
        return text

    def _queue_update(self, text: str, dot_token: str, dot_fallback: str, *, immediate: bool) -> None:
        self._pending_text = self._display_text(text)
        self._pending_dot = (dot_token, dot_fallback)
        if immediate:
            self._partial_timer.stop()
            self._flush_pending()
        elif not self._partial_timer.isActive():
            self._partial_timer.start()

    def _flush_pending(self) -> None:
        token, fallback = self._pending_dot
        self._set_dot(token, fallback)
        if self._text.text() != self._pending_text:
            self._text.setText(self._pending_text)
        self._present()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._record.setGeometry(self._panel.rect())
        if self._compact and self._ready:
            self._position_ready_drag_handle()
        if self._pending_text:
            self._partial_timer.start(0)

    # --- state-driven API ----------------------------------------------------
    def show_file_recording(self) -> None:
        """Minimal long-form recorder state: label, elapsed time and controls."""
        self._set_expanded()
        self._file_recording = True
        self._file_recording_paused = False
        self._file_recording_cancelling = False
        self._partial_timer.stop()
        self._blink_timer.stop()
        self._hide_timer.stop()
        self._elapsed_clock.restart()
        self._elapsed_accumulated_ms = 0
        self._elapsed.setText("00:00")
        self._elapsed_timer.start()
        self._stop.setEnabled(True)
        self._pause.setEnabled(True)
        self._sync_pause_visual()
        for widget in (
            self._drag_handle,
            self._cancel,
            self._text,
            self._elapsed,
            self._pause,
            self._stop,
        ):
            widget.show()
        for widget in (self._meter, self._dot, self._cloud):
            widget.hide()
        self._text.setText(self._tr.tr("recorder_recording"))
        self._present()

    def show_file_recording_finalizing(self, *, cancel: bool = False) -> None:
        if not self._file_recording:
            return
        self._file_recording_cancelling = bool(cancel)
        if self._elapsed_clock.isValid():
            self._elapsed_accumulated_ms += self._elapsed_clock.elapsed()
            self._elapsed_clock.invalidate()
        self._elapsed_timer.stop()
        self._update_elapsed()
        self._pause.setEnabled(False)
        self._pause.hide()
        self._stop.setEnabled(False)
        self._cancel.hide()
        self._text.setText(
            self._tr.tr("recorder_cancelling" if cancel else "recorder_finalizing")
        )
        self._present()

    def show_listening(self) -> None:
        self._file_recording = False
        self._file_recording_paused = False
        self._set_expanded()
        self._elapsed_clock.restart()
        self._elapsed_accumulated_ms = 0
        self._elapsed.setText("00:00")
        self._elapsed_timer.start()
        self._stop.setEnabled(True)
        self._stop.show()
        self._pause.hide()
        self._cancel.show()
        self._blink_on = True
        self._blink_timer.start()
        self.set_audio_level(0.0)
        self.set_transport_state("connecting")
        self._queue_update(
            self._tr.tr("overlay_listening"),
            "color-status-success",
            "#35C982",
            immediate=True,
        )

    def set_partial(self, text: str) -> None:
        self._queue_update(
            text or self._tr.tr("overlay_listening"),
            "color-status-success",
            "#35C982",
            immediate=False,
        )

    def show_transcribing(self) -> None:
        self._set_expanded()
        self._blink_timer.stop()
        self._blink_on = True
        self._elapsed_timer.stop()
        self._update_elapsed()
        self._stop.setEnabled(False)
        # keep whatever partial is showing; only swap the placeholder
        text = self._text.text()
        if not text or text == self._tr.tr("overlay_listening"):
            text = self._tr.tr("overlay_transcribing")
        self._queue_update(text, "color-accent-tertiary", "#85B7EB", immediate=True)

    def show_final(self, text: str) -> None:
        self._set_expanded()
        self._blink_timer.stop()
        self._blink_on = True
        self._elapsed_timer.stop()
        self._stop.hide()
        self._pause.hide()
        self._cancel.hide()
        self._queue_update(text, "color-status-success", "#1D9E75", immediate=True)
        self._hide_timer.start(_HIDE_AFTER_MS)

    def dismiss_if_not_showing_final(self) -> None:
        """Collapse a stale listening/transcribing panel, preserving final linger."""
        if not self._hide_timer.isActive():
            self.show_idle()

    def retranslate(self, tr) -> None:
        self._tr = tr
        self._stop.setAccessibleName(self._tr.tr("stop"))
        self._cancel.setAccessibleName(self._tr.tr("cancel"))
        self._sync_record_action_visual()
        self._sync_pause_visual()
        if self._file_recording:
            if self._file_recording_cancelling:
                key = "recorder_cancelling"
            elif not self._pause.isEnabled():
                key = "recorder_finalizing"
            elif self._file_recording_paused:
                key = "recorder_paused"
            else:
                key = "recorder_recording"
            self._text.setText(self._tr.tr(key))

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if watched in (self._drag_handle, self._ready_drag_handle):
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()
                return True
            if event.type() == QEvent.MouseMove and self._drag_offset is not None:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                self._user_positioned = True
                self._anchor_center = self.frameGeometry().center()
                event.accept()
                return True
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self._drag_offset = None
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def set_audio_level(self, level: float) -> None:
        level = max(0.0, min(1.0, float(level or 0.0)))
        if level < 0.015:
            bars = "▁▁▁▁"
        elif level < 0.06:
            bars = "▁▂▁▁"
        elif level < 0.16:
            bars = "▁▃▂▁"
        elif level < 0.35:
            bars = "▂▅▃▂"
        else:
            bars = "▃▇▅▃"
        self._meter.setText(bars)

    def set_transport_state(self, state: str) -> None:
        states = {
            "connecting": ("☁ …", "overlay_cloud_connecting"),
            "ready": ("☁ ✓", "overlay_cloud_ready"),
            "sending": ("☁ ↑", "overlay_cloud_sending"),
            "receiving": ("☁ ↕", "overlay_cloud_receiving"),
        }
        text, _key = states.get(state, states["connecting"])
        self._cloud.setText(text)

    def _request_stop(self) -> None:
        if not self._stop.isEnabled():
            return
        self._stop.setEnabled(False)
        self.stop_requested.emit()

    def _request_pause(self) -> None:
        if not self._file_recording or not self._pause.isEnabled():
            return
        self.pause_requested.emit(not self._file_recording_paused)

    def set_file_recording_paused(self, paused: bool) -> None:
        if not self._file_recording or self._file_recording_cancelling:
            return
        paused = bool(paused)
        if paused == self._file_recording_paused:
            return
        if paused:
            if self._elapsed_clock.isValid():
                self._elapsed_accumulated_ms += self._elapsed_clock.elapsed()
                self._elapsed_clock.invalidate()
            self._elapsed_timer.stop()
        else:
            self._elapsed_clock.restart()
            self._elapsed_timer.start()
        self._file_recording_paused = paused
        self._sync_pause_visual()
        self._text.setText(
            self._tr.tr("recorder_paused" if paused else "recorder_recording")
        )
        self._update_elapsed()

    def _toggle_blink(self) -> None:
        self._blink_on = not self._blink_on
        self._apply_dot()

    def _update_elapsed(self) -> None:
        if not self._elapsed_clock.isValid() and self._elapsed_accumulated_ms <= 0:
            return
        elapsed_ms = self._elapsed_accumulated_ms
        if self._elapsed_clock.isValid():
            elapsed_ms += self._elapsed_clock.elapsed()
        seconds = max(0, elapsed_ms // 1000)
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        value = f"{hours:d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"
        self._elapsed.setText(value)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._elapsed_timer.stop()
        self._blink_timer.stop()
        self._blink_on = True
        super().hideEvent(event)
