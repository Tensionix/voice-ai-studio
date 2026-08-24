"""PySide6 GUI entry point (iteration 2).

Run from the project root:
    runtime\\python.exe system_core\\ui\\app.py
The portable Python is embeddable (ignores PYTHONPATH), so we bootstrap sys.path
the same way cli.py / main.py do.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running this file directly with the portable interpreter.
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from system_core.core.paths import ensure_project_dirs, get_project_paths  # noqa: E402


def _app_icon():
    """Prefer the shipped .ico (system_core/icons/app.ico); fall back to the
    drawn Material mic if it's missing."""
    from PySide6.QtGui import QIcon

    from system_core.ui.icons import app_icon

    paths = get_project_paths()
    for candidate in (
        paths.system_core / "icons" / "app.ico",
        paths.root / "assets" / "app.ico",
    ):
        if candidate.exists():
            return QIcon(str(candidate))
    return app_icon()


def _set_taskbar_identity() -> None:
    """Windows groups taskbar icons by AppUserModelID. Set an explicit one so the
    app's own icon (not the generic pythonw icon) shows when launched directly."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Audion.VoiceAI")
    except Exception:
        pass


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from system_core.ui.main_window import MainWindow
    from system_core.ui.tooltips import apply_tooltip_delay

    paths = get_project_paths()
    ensure_project_dirs(paths)
    _set_taskbar_identity()

    app = QApplication(sys.argv)
    apply_tooltip_delay(app)
    app.setApplicationName("Audion Voice AI")
    app.setOrganizationName("Audion")
    icon = _app_icon()
    app.setWindowIcon(icon)

    window = MainWindow(paths)
    window.setWindowIcon(icon)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
