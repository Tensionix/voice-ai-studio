"""Material Design icons rendered to QIcon (no emoji in the UI).

Each entry is the 24x24 viewBox path data of a Material icon (Apache 2.0). We
rasterize on demand via QtSvg, tinted to a requested color, and cache the result.
Call only after a QApplication exists (QPixmap requires it).
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QIconEngine, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

PATHS: dict[str, str] = {
    "mic": (
        "M12 14c1.66 0 2.99-1.34 2.99-3L15 5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 "
        "1.66 1.34 3 3 3zm5.3-3c0 3-2.54 5.1-5.3 5.1S6.7 14 6.7 11H5c0 3.41 2.72 "
        "6.23 6 6.72V21h2v-3.28c3.28-.48 6-3.3 6-6.72h-1.7z"
    ),
    "delete": "M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z",
    "folder_open": (
        "M20 6h-8l-2-2H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 "
        "2-2V8c0-1.1-.9-2-2-2zm0 12H4V8h16v10z"
    ),
    "file": (
        "M6 2C4.9 2 4.01 2.9 4.01 4L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 "
        "2-2V8l-6-6H6zm7 7V3.5L18.5 9H13z"
    ),
    "refresh": (
        "M17.65 6.35C16.2 4.9 14.21 4 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 "
        "8c3.73 0 6.84-2.55 7.73-6h-2.08c-.82 2.33-3.04 4-5.65 4-3.31 0-6-2.69-6-6s2.69-6 "
        "6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"
    ),
    "history": (
        "M13 3c-4.97 0-9 4.03-9 9H1l4 4 4-4H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 "
        "7-7 7c-1.93 0-3.68-.79-4.95-2.05l-1.42 1.42C9.27 18.01 11.03 19 13 19c4.42 "
        "0 8-3.58 8-8s-3.58-8-8-8zm-1 5v5l4.25 2.52.77-1.28-3.52-2.09V8H12z"
    ),
    "copy": (
        "M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 "
        "2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"
    ),
    "add": "M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z",
    "play": "M8 5v14l11-7z",
    "pause": "M6 19h4V5H6v14zm8-14v14h4V5h-4z",
    "stop": "M6 6h12v12H6z",
    "lock": (
        "M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 "
        "2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zM9 6c0-1.66 1.34-3 3-3s3 1.34 "
        "3 3v2H9V6zm3 11c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2z"
    ),
    "pin": (
        "M16 9V4l1 0c.55 0 1-.45 1-1s-.45-1-1-1H7c-.55 0-1 .45-1 1s.45 1 1 1l1 "
        "0v5c0 1.66-1.34 3-3 3v2h5.97v7l1 1 1-1v-7H19v-2c-1.66 0-3-1.34-3-3z"
    ),
    "save": (
        "M17 3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V7l-4-4zm-5 "
        "16c-1.66 0-3-1.34-3-3s1.34-3 3-3 3 1.34 3 3-1.34 3-3 3zm3-10H5V5h10v4z"
    ),
    "check": "M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z",
    "close": (
        "M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 "
        "13.41 17.59 19 19 17.59 13.41 12z"
    ),
    "expand_more": "M16.59 8.59L12 13.17 7.41 8.59 6 10l6 6 6-6z",
    "chevron_right": "M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z",
    "launch": (
        "M19 19H5V5h7V3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 "
        "2-2v-7h-2v7zM14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3h-7z"
    ),
    "tune": (
        "M3 17v2h6v-2H3zM3 5v2h10V5H3zm10 16v-2h8v-2h-8v-2h-2v6h2zM7 9v2H3v2h4v2h2V9H7zm14 "
        "4v-2H11v2h10zm-6-4h2V7h4V5h-4V3h-2v6z"
    ),
    "power": (
        "M13 3h-2v10h2V3zm4.83 2.17l-1.42 1.42C17.99 7.86 19 9.81 19 12c0 3.87-3.13 "
        "7-7 7s-7-3.13-7-7c0-2.19 1.01-4.14 2.58-5.42L6.17 5.17C4.23 6.82 3 9.26 3 12c0 "
        "4.97 4.03 9 9 9s9-4.03 9-9c0-2.74-1.23-5.18-3.17-6.83z"
    ),
    "download": "M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z",
    "upload": "M5 20h14v-2H5v2zm0-10h4v6h6v-6h4l-7-7-7 7z",
    "fullscreen": "M5 5h5V3H3v7h2V5zm5 14H5v-5H3v7h7v-2zm4-16v2h5v5h2V3h-7zm5 16h-5v2h7v-7h-2v5z",
    "fullscreen_exit": "M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z",
}


def _svg(name: str, color: str) -> bytes:
    path = PATHS[name]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
        f'<path d="{path}" fill="{color}"/></svg>'
    ).encode("utf-8")


def _device_pixel_ratio() -> float:
    app = QGuiApplication.instance()
    if app is not None:
        screen = app.primaryScreen()
        if screen is not None:
            return float(screen.devicePixelRatio())
    return 1.0


class _SvgIconEngine(QIconEngine):
    """Renders an SVG fresh at the exact pixel size Qt requests, so the glyph stays
    crisp at any DPI / fractional scaling (125%, 150%) instead of upscaling a
    baked bitmap."""

    def __init__(self, svg_bytes: bytes):
        super().__init__()
        self._svg = svg_bytes

    def paint(self, painter: QPainter, rect, mode, state) -> None:  # noqa: ARG002
        painter.setRenderHint(QPainter.Antialiasing)
        QSvgRenderer(QByteArray(self._svg)).render(painter, QRectF(rect))

    def pixmap(self, size, mode, state) -> QPixmap:  # noqa: ARG002
        dpr = _device_pixel_ratio()
        w = max(1, round(size.width() * dpr))
        h = max(1, round(size.height() * dpr))
        pix = QPixmap(w, h)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        QSvgRenderer(QByteArray(self._svg)).render(painter, QRectF(0, 0, w, h))
        painter.end()
        pix.setDevicePixelRatio(dpr)
        return pix

    def actualSize(self, size, mode, state):  # noqa: ARG002, N802
        return size

    def clone(self) -> QIconEngine:
        return _SvgIconEngine(self._svg)


@lru_cache(maxsize=256)
def icon(name: str, color: str = "#E6EDF5", size: int = 18) -> QIcon:
    """A tinted Material icon as a scalable QIcon (HiDPI-crisp at any scale).

    `size` is retained for call-site compatibility but no longer caps resolution —
    the engine re-rasterizes per request."""
    return QIcon(_SvgIconEngine(_svg(name, color)))


@lru_cache(maxsize=16)
def record_icon(
    ring_color: str = "#F4F4F6",
    fill_color: str = "#E5484D",
    size: int = 24,
) -> QIcon:
    """Record transport icon: a red disc inside a high-contrast white ring."""
    del size  # Kept for the same call-site contract as ``icon``.
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24">'
        f'<circle cx="12" cy="12" r="10" fill="{ring_color}"/>'
        f'<circle cx="12" cy="12" r="7" fill="{fill_color}"/>'
        '</svg>'
    ).encode("utf-8")
    return QIcon(_SvgIconEngine(svg))


def app_icon(
    color_bg: str = "#0F1622",
    color_glyph: str = "#85B7EB",
    size: int = 64,
    *,
    glyph_scale: float = 0.56,
) -> QIcon:
    """The window/app icon: the Material mic on a branded rounded square."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(color_bg))
    painter.setPen(Qt.NoPen)
    inset = max(2, size // 32)
    radius = size // 4
    painter.drawRoundedRect(inset, inset, size - 2 * inset, size - 2 * inset, radius, radius)
    glyph = round(size * max(0.4, min(0.82, float(glyph_scale))))
    offset = (size - glyph) / 2
    QSvgRenderer(QByteArray(_svg("mic", color_glyph))).render(
        painter, QRectF(offset, offset, glyph, glyph)
    )
    painter.end()
    return QIcon(pix)
