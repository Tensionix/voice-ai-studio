"""System tray icon + menu for Live dictation (iteration 3).

The tray is the app's resident presence: it summons the main window, arms/disarms
Live dictation, opens the output folder and quits. It owns no app logic — it
emits intent signals the main window wires to the `LiveController` and itself.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..ui.i18n import Translator
from ..ui.icons import icon
from .controller import LiveState


class VoiceTray(QSystemTrayIcon):
    """Tray presence. Reflects Live state in the checkable live action."""

    show_window = Signal()
    menu_opened = Signal()
    show_dictation_history = Signal()
    toggle_live = Signal()
    live_profile_requested = Signal(str)
    save_log = Signal()
    open_output = Signal()
    quit_app = Signal()
    # (source, destination): source in {"live","file"}, destination in {"notion","obsidian"}
    export_requested = Signal(str, str)
    recording_export_requested = Signal(str)

    def __init__(self, app_icon: QIcon, tr: Translator, parent=None):
        super().__init__(app_icon, parent)
        self._tr = tr

        menu = QMenu()
        self._act_show = menu.addAction(icon("launch", "#E6EDF5"), "")
        self._act_show.triggered.connect(self.show_window)

        # Checkable: the check mark is the on/off state (no ambiguous verb label).
        self._act_live = menu.addAction(icon("mic", "#85B7EB"), "")
        self._act_live.setCheckable(True)
        self._act_live.triggered.connect(self.toggle_live)
        self._act_live_xai = menu.addAction(icon("mic", "#85B7EB"), "")
        self._act_live_xai.triggered.connect(lambda: self.live_profile_requested.emit("xai"))
        self._act_live_gigaam = menu.addAction(icon("mic", "#1D9E75"), "")
        self._act_live_gigaam.triggered.connect(lambda: self.live_profile_requested.emit("gigaam"))
        self._act_history = menu.addAction(icon("history", "#85B7EB"), "")
        self._act_history.triggered.connect(self.show_dictation_history)

        menu.addSeparator()
        # Export the last dictation, or an existing transcript file, to a connector.
        self._menu_export = menu.addMenu(icon("upload", "#9FB0C3"), "")
        self._sub_live = self._menu_export.addMenu(icon("mic", "#85B7EB"), "")
        self._act_live_notion = self._sub_live.addAction("Notion")
        self._act_live_notion.triggered.connect(lambda: self.export_requested.emit("live", "notion"))
        self._act_live_obsidian = self._sub_live.addAction("Obsidian")
        self._act_live_obsidian.triggered.connect(lambda: self.export_requested.emit("live", "obsidian"))
        self._sub_file = self._menu_export.addMenu(icon("folder_open", "#9FB0C3"), "")
        self._act_file_notion = self._sub_file.addAction("Notion")
        self._act_file_notion.triggered.connect(lambda: self.export_requested.emit("file", "notion"))
        self._act_file_obsidian = self._sub_file.addAction("Obsidian")
        self._act_file_obsidian.triggered.connect(lambda: self.export_requested.emit("file", "obsidian"))
        self._menu_export.addSeparator()
        self._sub_recording = self._menu_export.addMenu(icon("download", "#9FB0C3"), "")
        self._act_recording_m4a = self._sub_recording.addAction("")
        self._act_recording_m4a.triggered.connect(
            lambda: self.recording_export_requested.emit("m4a")
        )
        self._act_recording_wav = self._sub_recording.addAction("")
        self._act_recording_wav.triggered.connect(
            lambda: self.recording_export_requested.emit("wav")
        )

        menu.addSeparator()
        self._act_save_log = menu.addAction(icon("download", "#9FB0C3"), "")
        self._act_save_log.triggered.connect(self.save_log)
        self._act_output = menu.addAction(icon("folder_open", "#9FB0C3"), "")
        self._act_output.triggered.connect(self.open_output)

        menu.addSeparator()
        self._act_quit = menu.addAction(icon("power", "#D85A30"), "")
        self._act_quit.triggered.connect(self.quit_app)

        self.setContextMenu(menu)
        self._menu = menu
        menu.aboutToShow.connect(self.menu_opened)
        self.activated.connect(self._on_activated)
        self.retranslate(tr)

    # --- interaction ---------------------------------------------------------
    def _on_activated(self, reason) -> None:
        # Left click / double click reopens the window (context menu handles the rest).
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window.emit()

    # --- state reflection ----------------------------------------------------
    def set_live_state(self, state: LiveState) -> None:
        self._act_live.setChecked(state != LiveState.DISABLED)

    # --- i18n ----------------------------------------------------------------
    def retranslate(self, tr: Translator) -> None:
        self._tr = tr
        self._act_show.setText(tr.tr("tray_show"))
        self._act_live.setText(tr.tr("tray_live"))
        self._act_live_xai.setText(tr.tr("tray_live_xai"))
        self._act_live_gigaam.setText(tr.tr("tray_live_gigaam"))
        self._act_history.setText(tr.tr("dictation_history_all_title"))
        self._menu_export.setTitle(tr.tr("tray_export"))
        self._sub_live.setTitle(tr.tr("tray_export_live"))
        self._sub_file.setTitle(tr.tr("tray_export_file"))
        self._sub_recording.setTitle(tr.tr("recorder_export"))
        self._act_recording_m4a.setText(tr.tr("recorder_export_m4a"))
        self._act_recording_wav.setText(tr.tr("recorder_export_wav"))
        self._act_save_log.setText(tr.tr("save_log_md"))
        self._act_output.setText(tr.tr("tray_open_output"))
        self._act_quit.setText(tr.tr("tray_quit"))
