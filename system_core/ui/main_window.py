"""Main application window (TZ section 5.1): input, queue, settings, run, logs."""

from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QApplication,
    QInputDialog,
    QLabel,
    QPushButton,
    QMainWindow,
    QMenu,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QSystemTrayIcon,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..core.editions import EDITION_LIVE, compute_mode_label_key, current_edition, display_compute_mode
from ..core.live_dependencies import check_live_dependencies
from ..core.paths import ProjectPaths, open_folder
from ..core.persistence import JobStore
from ..core.prompt_store import PromptStore
from ..core.workspace_cleanup import remove_source_workspace
from ..live import LiveConfig, LiveController, LiveState
from ..live.config import fixed_live_api_model
from ..live.history import (
    DictationHistory,
    HISTORY_CACHE_LIMIT,
    OVERLAY_HISTORY_LIMIT,
)
from ..live.tray import VoiceTray
from ..media.probe import is_supported, supported_extensions, supported_extensions_label
from ..media.recorder import FileRecorder, find_recordings
from ..pipeline.queue import collect_sources
from ..settings import DEFAULT_SETTINGS, load_settings
from ..writers.atomic import write_text_atomic
from . import theme as theme_mod
from .dictation_history import DictationHistoryDialog
from .i18n import Translator
from .icons import app_icon, icon
from .settings_io import save_settings
from .state_io import load_queue_files, load_tab_order, save_queue_files, save_tab_order
from .settings_panel import SettingsPanel
from .tooltips import seed_tooltips
from .widgets import (
    Card,
    ElidedComboBox,
    ElidedLabel,
    QueueCheckDelegate,
    QueueModel,
    QueueTableView,
    RoundedPlainTextEdit,
    UnderlineTabBar,
)
from .workers import AudioExportWorker, InstallWorker, ModelWorker, QueueWorker


class CenteredComboBox(QComboBox):
    """Compact combobox that keeps the current value centered in the closed state."""

    def wheelEvent(self, event):  # noqa: N802
        event.ignore()

    def paintEvent(self, event):  # noqa: N802, ARG002
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        text = option.currentText
        option.currentText = ""
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        # Center the visible composition, not the text alone.  The top-bar
        # chevron occupies an 18 px zone on the right, so moving the text centre
        # left by half that zone balances both short (RU) and long theme names.
        text_rect = self.rect().adjusted(1, 1, -19, -1)
        painter.setPen(option.palette.buttonText().color())
        painter.drawText(text_rect, Qt.AlignCenter | Qt.TextSingleLine, text)


class LiveAudioDeviceComboBox(ElidedComboBox):
    """Session-only mic selector that refreshes hot-plug state before opening."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.before_popup = None

    def wheelEvent(self, event):  # noqa: N802
        event.ignore()

    def showPopup(self):  # noqa: N802
        callback = self.before_popup
        if callable(callback):
            callback()
        super().showPopup()

class MainWindow(QMainWindow):
    def __init__(self, paths: ProjectPaths):
        super().__init__()
        self._paths = paths
        self._settings: dict[str, Any] = load_settings(paths)
        self._prompts = PromptStore.load(paths)

        lang = self._settings.get("ui_language") or "ru"
        if lang == "auto":
            lang = "ru"
        self._tr = Translator.load(paths, lang)
        self._theme = theme_mod.get_theme(paths, self._settings.get("ui_theme"))

        self._queue_worker: Optional[QueueWorker] = None
        self._model_worker: Optional[ModelWorker] = None
        self._live_deps_worker: Optional[InstallWorker] = None
        self._audio_export_worker: Optional[AudioExportWorker] = None
        self._file_recorder: Optional[FileRecorder] = None
        self._file_recorder_cancelled = False
        self._recorder_restore_live_armed = False
        self._recorder_created_overlay = False
        self._close_after_recorder = False
        self._close_after_audio_export = False
        self._overlay_model_switch_pending = False
        self._overlay_model_switch_signature = None

        # Live dictation / tray (iteration 3).
        self._live_cfg = LiveConfig.from_settings(self._settings)
        self._live: Optional[LiveController] = None
        self._tray: Optional[VoiceTray] = None
        self._overlay = None  # frameless dictation overlay (optional)
        self._dictation_history = DictationHistory.load(paths)
        self._dictation_history_dialog: Optional[DictationHistoryDialog] = None
        self._dictation_target_hwnd = None
        # Microphone override is intentionally process-local. Never put it in
        # self._settings: every application restart must return to auto-detect.
        self._live_input_device_preference: tuple[str, str] | None = None
        self._live_active_input_device = ""
        self._live_audio_candidates: tuple[object, ...] = ()
        self._refreshing_live_audio_devices = False
        self._dictation_lines: list[str] = []  # current session's dictation, for tray export
        self._really_quit = False
        self._log_expanded = False
        self._autosave_ready = False
        self._export_actions: list[tuple[Any, str]] = []

        self._settings_save_timer = QTimer(self)
        self._settings_save_timer.setSingleShot(True)
        self._settings_save_timer.setInterval(500)
        self._settings_save_timer.timeout.connect(self._save_settings_now)

        self._queue_save_timer = QTimer(self)
        self._queue_save_timer.setSingleShot(True)
        self._queue_save_timer.setInterval(500)
        self._queue_save_timer.timeout.connect(self._save_queue_state_now)

        self._recorder_timer = QTimer(self)
        self._recorder_timer.setInterval(250)
        self._recorder_timer.timeout.connect(self._poll_file_recorder)

        self.setWindowTitle(self._tr.tr("app_title"))
        self.resize(1600, 900)  # 16:9
        self._build_ui()
        self._restore_queue_state()
        self._retranslate()
        self.apply_theme(self._theme.name)
        self._setup_tray()
        self._autosave_ready = True

        # Populate model pickers from cache/fallback on startup (no forced API hit).
        self._start_model_fetch(refresh=False)

    # --- construction --------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(10)

        outer.addLayout(self._build_topbar())

        top_rule = QFrame()
        top_rule.setObjectName("ShellRule")
        top_rule.setFixedHeight(1)
        outer.addWidget(top_rule)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("MainSplitter")
        splitter.setHandleWidth(11)
        self._main_splitter = splitter
        outer.addWidget(splitter, 1)
        left = self._build_left()
        left.setMinimumWidth(360)
        splitter.addWidget(left)
        self._right_panel = self._build_right()
        splitter.addWidget(self._right_panel)
        # The four subtitle controls intentionally stay on one line, so the
        # settings side needs more of the initial 1600 px shell.  The queue
        # remains comfortably usable at 500 px and both sides still resize.
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 11)
        splitter.setSizes([500, 1100])

        bottom_rule = QFrame()
        bottom_rule.setObjectName("ShellRule")
        bottom_rule.setFixedHeight(1)
        outer.addWidget(bottom_rule)

        self.status_bar = self.statusBar()
        self.status_bar.setSizeGripEnabled(False)

    def _build_topbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        self.lbl_title = QLabel(self._tr.tr("app_title"))
        self.lbl_title.setProperty("role", "heading")
        bar.addWidget(self.lbl_title)
        bar.addStretch(1)

        self.lbl_lang = QLabel(self._tr.tr("app_language"))
        bar.addWidget(self.lbl_lang)
        self.cmb_lang = self._topbar_combo(58)
        for code in self._tr.languages:
            self.cmb_lang.addItem(code.upper(), code)
        idx = self.cmb_lang.findData(self._tr.language)
        self.cmb_lang.setCurrentIndex(idx if idx >= 0 else 0)
        self.cmb_lang.currentIndexChanged.connect(self._on_language_changed)
        bar.addWidget(self.cmb_lang)
        bar.addSpacing(50)

        self.lbl_theme = QLabel(self._tr.tr("theme"))
        bar.addWidget(self.lbl_theme)
        self.cmb_theme = self._topbar_combo(136)
        for info in theme_mod.list_themes(self._paths):
            label = info.label_ru if self._tr.language == "ru" else info.label
            self.cmb_theme.addItem(label, info.name)
        tidx = self.cmb_theme.findData(self._theme.name)
        self.cmb_theme.setCurrentIndex(tidx if tidx >= 0 else 0)
        self.cmb_theme.currentIndexChanged.connect(
            lambda: self.apply_theme(self.cmb_theme.currentData())
        )
        bar.addWidget(self.cmb_theme)
        return bar

    def _topbar_combo(self, width: int) -> QComboBox:
        combo = CenteredComboBox()
        combo.setProperty("variant", "topbar")
        combo.setFixedSize(width, 34)
        return combo

    def _build_left(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("LeftWorkspace")
        layout = QVBoxLayout(panel)
        # Leave room for antialiased circular borders and the active tab line;
        # drawing them directly against the splitter clip cuts their left edge.
        layout.setContentsMargins(14, 0, 8, 0)
        layout.setSpacing(8)

        self._workspace_keys = load_tab_order(
            self._paths,
            "workspace_tabs",
            self._default_workspace_order(),
        )
        self._workspace_tabs = UnderlineTabBar(
            [(key, self._tr.tr(key)) for key in self._workspace_keys],
            self,
            fill_width=True,
        )
        self._workspace_tabs.currentChanged.connect(self._set_workspace_section)
        self._workspace_tabs.orderChanged.connect(self._save_workspace_tab_order)
        layout.addWidget(self._workspace_tabs)

        self._workspace_stack = QStackedWidget()
        layout.addWidget(self._workspace_stack, 1)

        # Queue table — doubles as the drop target (no separate dashed drop-zone).
        self.queue_box = Card(self._tr.tr("queue"))
        self.queue_box.layout().setContentsMargins(12, 12, 12, 12)
        self.queue_box.layout().setSpacing(10)
        self.lbl_formats = QLabel()
        self.lbl_formats.setProperty("role", "muted")
        self.queue_box.addHeaderWidget(self.lbl_formats)
        qlayout = QVBoxLayout(self.queue_box.body())
        qlayout.setContentsMargins(0, 0, 0, 0)
        qlayout.setSpacing(8)
        self.queue_model = QueueModel(
            status_label=lambda s: self._tr.tr(f"status_{s}"),
            status_color=lambda s: theme_mod.status_color(self._theme, s),
        )
        self.queue_view = QueueTableView()
        self.queue_view.setObjectName("WorkTable")
        self.queue_view.setModel(self.queue_model)
        self.queue_view.setItemDelegateForColumn(0, QueueCheckDelegate(self.queue_view))
        self.queue_view.setSelectionBehavior(QTableView.SelectRows)
        self.queue_view.setSelectionMode(QTableView.ExtendedSelection)
        self.queue_view.setAlternatingRowColors(True)
        self.queue_view.setShowGrid(False)
        self.queue_view.verticalHeader().setVisible(False)
        self.queue_view.verticalHeader().setDefaultSectionSize(36)
        # Shown as a placeholder hint while the queue is empty.
        self.queue_view.paths_dropped.connect(self._add_paths)
        self.queue_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.queue_view.customContextMenuRequested.connect(self._on_queue_context_menu)
        header = self.queue_view.horizontalHeader()
        header.setFixedHeight(38)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.resizeSection(0, 46)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.queue_surface = QFrame()
        self.queue_surface.setObjectName("WorkSurface")
        qsurface_layout = QVBoxLayout(self.queue_surface)
        qsurface_layout.setContentsMargins(3, 3, 3, 3)
        qsurface_layout.setSpacing(0)
        qsurface_layout.addWidget(self.queue_view)

        # Queue operations (Material icons, no emoji).
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        self.btn_export = QPushButton()
        self.btn_export.setProperty("variant", "toolbar-text")
        self.btn_export.setText(self._tr.tr("tray_export"))
        self.btn_export.setIcon(icon("upload", "#E6EDF5"))
        self.btn_export.setMinimumWidth(152)
        self.btn_export.setMaximumWidth(16777215)
        self.btn_export.setFixedHeight(34)
        self.btn_export.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_export.setMenu(self._make_export_menu(self.btn_export))
        self.btn_run = QPushButton()
        self.btn_run.setProperty("variant", "toolbar")
        self.btn_run.setProperty("accent", "primary")
        self.btn_run.setIcon(icon("play", "#FFFFFF"))
        self.btn_run.clicked.connect(self._on_run)
        self.btn_stop = QPushButton()
        self.btn_stop.setProperty("variant", "toolbar")
        self.btn_stop.setProperty("accent", "danger")
        self.btn_stop.setIcon(icon("stop", "#E6EDF5"))
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_retry = QPushButton()
        self.btn_retry.setProperty("variant", "toolbar")
        self.btn_retry.setIcon(icon("refresh", "#E6EDF5"))
        self.btn_retry.clicked.connect(self._on_retry)
        self.btn_clear = QPushButton()
        self.btn_clear.setProperty("variant", "toolbar")
        self.btn_clear.setIcon(icon("delete", "#9FB0C3"))
        self.btn_clear.setToolTip(self._tr.tr("clear_queue"))
        self.btn_clear.clicked.connect(self._on_clear)

        # Source row: a label plus two unambiguous round picker actions, followed
        # by the two project locations. No menu means no popup can cover the row.
        self.lbl_add_sources = QLabel()
        self.lbl_add_sources.setProperty("variant", "chip")
        self.lbl_add_sources.setFixedHeight(34)
        self.btn_add_files = QPushButton()
        self.btn_add_files.setProperty("variant", "source-picker")
        self.btn_add_files.setProperty("accent", "primary")
        self.btn_add_files.setIcon(icon("file", "#E6EDF5"))
        self.btn_add_files.setFixedSize(34, 34)
        self.btn_add_files.clicked.connect(self._browse_files)
        self.btn_add_folder = QPushButton()
        self.btn_add_folder.setProperty("variant", "source-picker")
        self.btn_add_folder.setProperty("accent", "primary")
        self.btn_add_folder.setIcon(icon("folder_open", "#E6EDF5"))
        self.btn_add_folder.setFixedSize(34, 34)
        self.btn_add_folder.clicked.connect(self._browse_folder)
        self.btn_open_in = QPushButton()
        self.btn_open_in.setProperty("variant", "queue-folder")
        self.btn_open_in.setIcon(icon("folder_open", "#9FB0C3"))
        self.btn_open_in.setMinimumWidth(100)
        self.btn_open_in.setMaximumWidth(16777215)
        self.btn_open_in.setFixedHeight(34)
        self.btn_open_in.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_open_in.clicked.connect(lambda: open_folder(self._paths.input))
        self.btn_open_out = QPushButton()
        self.btn_open_out.setProperty("variant", "queue-folder")
        self.btn_open_out.setIcon(icon("folder_open", "#9FB0C3"))
        self.btn_open_out.setMinimumWidth(100)
        self.btn_open_out.setMaximumWidth(16777215)
        self.btn_open_out.setFixedHeight(34)
        self.btn_open_out.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_open_out.setToolTip(self._tr.tr("open_output"))
        self.btn_open_out.clicked.connect(lambda: open_folder(self._paths.output))
        for btn in (self.btn_add_files, self.btn_add_folder, self.btn_export, self.btn_run,
                    self.btn_stop, self.btn_retry, self.btn_clear, self.btn_open_in,
                    self.btn_open_out):
            btn.setIconSize(QSize(18, 18))
        controls.addWidget(self.btn_export, 1)
        controls.addWidget(self.btn_run)
        controls.addWidget(self.btn_stop)
        controls.addWidget(self.btn_retry)
        controls.addWidget(self.btn_clear)
        self.queue_controls_layout = controls
        source_controls = QHBoxLayout()
        source_controls.setContentsMargins(0, 0, 0, 0)
        source_controls.setSpacing(6)
        source_controls.addWidget(self.lbl_add_sources)
        source_controls.addWidget(self.btn_add_files)
        source_controls.addWidget(self.btn_add_folder)
        source_controls.addSpacing(8)
        source_controls.addWidget(self.btn_open_in, 1)
        source_controls.addWidget(self.btn_open_out, 1)
        qlayout.addLayout(source_controls)
        qlayout.addLayout(controls)
        # Run-summary strip: a one-glance read of what the next Run will do
        # (compute mode / STT model / diarization / cleanup), so you don't have
        # to scan the settings column before pressing play.
        self.lbl_run_summary = QLabel()
        self.lbl_run_summary.setObjectName("RunSummary")
        self.lbl_run_summary.setProperty("variant", "hint")
        self.lbl_run_summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        qlayout.addWidget(self.lbl_run_summary)
        qlayout.addWidget(self.queue_surface, 1)
        self._workspace_pages = {"workspace_tab_queue": self.queue_box}
        self._workspace_stack.addWidget(self.queue_box)

        # Logs
        self.log_box = Card(self._tr.tr("logs"))
        # Keep circular header controls clear of the rounded card edge even at
        # fractional Windows DPI scaling.
        self.log_box.layout().setContentsMargins(10, 10, 10, 10)
        self.log_box.layout().setSpacing(6)
        self.btn_live_record = QPushButton()
        self.btn_live_record.setProperty("variant", "header-icon")
        self.btn_live_record.setProperty("accent", "danger")
        self.btn_live_record.setCheckable(True)
        self.btn_live_record.setEnabled(False)
        self.btn_live_record.setIcon(icon("mic", "#FFFFFF"))
        self.btn_live_record.setIconSize(QSize(16, 16))
        self.btn_live_record.clicked.connect(self._on_toggle_live)
        self.lbl_live_audio_device = ElidedLabel(horizontal_inset=24)
        self.lbl_live_audio_device.setObjectName("LiveAudioDeviceBadge")
        self.lbl_live_audio_device.setProperty("role", "audio-device")
        self.lbl_live_audio_device.setFixedHeight(34)
        self.lbl_live_audio_device.setMinimumWidth(0)
        self.lbl_live_audio_device.setMaximumWidth(16777215)
        self.lbl_live_audio_device.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.lbl_live_audio_device.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.cmb_live_audio_device = LiveAudioDeviceComboBox()
        self.cmb_live_audio_device.setObjectName("LiveAudioDeviceSelect")
        self.cmb_live_audio_device.setProperty("variant", "audio-device")
        self.cmb_live_audio_device.setMinimumWidth(0)
        self.cmb_live_audio_device.setMaximumWidth(16777215)
        self.cmb_live_audio_device.setFixedHeight(34)
        self.cmb_live_audio_device.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.cmb_live_audio_device.view().setMinimumWidth(420)
        self.cmb_live_audio_device.before_popup = self._refresh_live_audio_devices
        self.cmb_live_audio_device.currentIndexChanged.connect(
            self._on_live_audio_device_selected
        )
        self.btn_log_export = QPushButton()
        self.btn_log_export.setProperty("variant", "header-text")
        self.btn_log_export.setText(self._tr.tr("tray_export"))
        self.btn_log_export.setIcon(icon("upload", "#E6EDF5"))
        self.btn_log_export.setIconSize(QSize(16, 16))
        self.btn_log_export.setFixedWidth(152)
        self.btn_log_export.setFixedHeight(34)
        self.btn_log_export.setMenu(self._make_export_menu(self.btn_log_export))
        self.btn_log_clear = QPushButton()
        self.btn_log_clear.setProperty("variant", "header-icon")
        self.btn_log_clear.setIcon(icon("delete", "#9FB0C3"))
        self.btn_log_clear.setIconSize(QSize(16, 16))
        self.btn_log_save = QPushButton()
        self.btn_log_save.setProperty("variant", "header-icon")
        self.btn_log_save.setIcon(icon("download", "#9FB0C3"))
        self.btn_log_save.setIconSize(QSize(16, 16))
        self.btn_log_save.clicked.connect(self._save_log_markdown_with_picker)
        self.btn_log_expand = QPushButton()
        self.btn_log_expand.setProperty("variant", "header-icon")
        self.btn_log_expand.setIcon(icon("fullscreen", "#9FB0C3"))
        self.btn_log_expand.setIconSize(QSize(16, 16))
        self.btn_log_expand.clicked.connect(self._toggle_log_expanded)
        self.log_box.addHeaderLeadingWidget(self.btn_live_record)
        self.log_box.addHeaderWidget(self.btn_log_export)
        self.log_box.addHeaderWidget(self.btn_log_clear)
        self.log_box.addHeaderWidget(self.btn_log_save)
        self.log_box.addHeaderWidget(self.btn_log_expand)
        self.live_audio_bar = QWidget()
        self.live_audio_bar.setObjectName("LiveAudioDeviceBar")
        live_audio_row = QHBoxLayout(self.live_audio_bar)
        # Paint-safe inset keeps rounded 1 px borders inside the widget clip at
        # fractional Windows DPI. Both controls share all remaining width.
        live_audio_row.setContentsMargins(2, 2, 2, 2)
        live_audio_row.setSpacing(10)
        live_audio_row.addWidget(self.lbl_live_audio_device, 1)
        live_audio_row.addWidget(self.cmb_live_audio_device, 1)
        self.live_audio_bar.setFixedHeight(38)
        self.log_box.layout().insertWidget(0, self.live_audio_bar)
        llayout = QVBoxLayout(self.log_box.body())
        llayout.setContentsMargins(0, 0, 0, 0)
        llayout.setSpacing(0)
        self.log_view = RoundedPlainTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        # Read-only logs remain mouse-selectable, but never trap Tab navigation.
        self.log_view.setFocusPolicy(Qt.ClickFocus)
        self.log_view.setMaximumBlockCount(5000)
        self.btn_log_clear.clicked.connect(self.log_view.clear)
        self.log_surface = QFrame()
        self.log_surface.setObjectName("WorkSurface")
        log_surface_layout = QVBoxLayout(self.log_surface)
        log_surface_layout.setContentsMargins(1, 1, 1, 1)
        log_surface_layout.setSpacing(0)
        log_surface_layout.addWidget(self.log_view)
        llayout.addWidget(self.log_surface)
        self.log_box.setMinimumHeight(0)
        self.log_box.setMaximumHeight(16777215)
        self._workspace_pages["workspace_tab_live_log"] = self.log_box
        self._workspace_stack.addWidget(self.log_box)
        self._workspace_tabs.set_current_index(0)
        self._workspace_stack.setCurrentWidget(self._workspace_pages[self._workspace_keys[0]])
        return panel

    def _build_right(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("SettingsScroll")
        scroll.setWidgetResizable(True)
        # Settings pages never scroll sideways; let cards compress to fit.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.settings_panel = SettingsPanel(self._paths, self._tr, self._settings, self._prompts)
        self._connect_settings_panel()
        scroll.setWidget(self.settings_panel)
        return scroll

    def _connect_settings_panel(self) -> None:
        self.settings_panel.refresh_requested.connect(lambda: self._start_model_fetch(refresh=True))
        self.settings_panel.reset_requested.connect(self._reset_app_settings)
        self.settings_panel.changed.connect(self._schedule_settings_save)
        self.settings_panel.changed.connect(self._update_run_summary)
        if hasattr(self.settings_panel, "modules_panel"):
            self.settings_panel.modules_panel.log_line.connect(self._append_install_log)
            self.settings_panel.modules_panel.module_installed.connect(self._on_module_installed)

    def _make_export_menu(self, parent: QWidget) -> QMenu:
        menu = QMenu(parent)
        entries = [
            ("export_live_notion", "live", "notion"),
            ("export_live_obsidian", "live", "obsidian"),
            ("export_transcript_notion", "file", "notion"),
            ("export_transcript_obsidian", "file", "obsidian"),
        ]
        for label_key, source, destination in entries:
            action = menu.addAction(self._tr.tr(label_key))
            action.setData(f"{source}:{destination}")
            action.triggered.connect(
                lambda _checked=False, s=source, d=destination: self._on_export_requested(s, d)
            )
            self._export_actions.append((action, label_key))
        menu.addSeparator()
        for label_key, output_format in (
            ("recorder_export_m4a", "m4a"),
            ("recorder_export_wav", "wav"),
        ):
            action = menu.addAction(icon("download", "#9FB0C3"), self._tr.tr(label_key))
            action.triggered.connect(
                lambda _checked=False, fmt=output_format: self._export_recording_as(fmt)
            )
            self._export_actions.append((action, label_key))
        return menu

    def _default_workspace_order(self) -> list[str]:
        # The shared shell keeps navigation stable between Live and Studio.
        return ["workspace_tab_live_log", "workspace_tab_queue"]

    def _set_workspace_section(self, index: int) -> None:
        keys = self._workspace_tabs.keys()
        if 0 <= index < len(keys):
            self._workspace_stack.setCurrentWidget(self._workspace_pages[keys[index]])
            self._sync_overlay_record_action()

    def _save_workspace_tab_order(self, keys: list[str]) -> None:
        self._workspace_keys = list(keys)
        save_tab_order(self._paths, "workspace_tabs", self._workspace_keys)

    # --- input ---------------------------------------------------------------
    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, self._tr.tr("add_folder"))
        if folder:
            self._add_paths([folder])

    def _browse_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            self._tr.tr("add_files"),
            "",
            self._media_file_filter(),
        )
        if files:
            self._add_paths(files)

    def _add_paths(self, raw_paths: list[str]) -> None:
        # Always recurse a dropped/chosen folder: pull every extractable media file
        # from the whole tree straight into the queue.
        inputs = [Path(p) for p in raw_paths]
        unsupported = [p for p in inputs if p.is_file() and not is_supported(p)]
        expanded = collect_sources(inputs, recursive=True)
        existing = {str(p.resolve()).lower() for p in self.queue_model.files()}
        merged = self.queue_model.files()
        added = 0
        for path in expanded:
            if str(path.resolve()).lower() not in existing:
                merged.append(path)
                added += 1
        self.queue_model.set_files(merged)
        self._append_log(f"+ {added} file(s) added to queue ({len(merged)} total).")
        if unsupported:
            msg = self._tr.tr("unsupported_files_skipped").replace(
                "{count}", str(len(unsupported))
            ).replace("{formats}", supported_extensions_label())
            self._append_log(f"WARN: {msg}")
            self.status_bar.showMessage(msg, 8000)
        if added:
            self._schedule_queue_save()

    def _media_file_filter(self) -> str:
        patterns = " ".join(f"*{ext}" for ext in supported_extensions())
        return f"{self._tr.tr('media_files')} ({patterns});;{self._tr.tr('all_files')} (*.*)"

    def _update_format_hint(self) -> None:
        text = self._tr.tr("supported_formats").replace("{formats}", supported_extensions_label())
        self.lbl_formats.setText(self._tr.tr("supported_formats_short"))
        self.lbl_formats.setToolTip(text)
        self.queue_view.setToolTip(text)
        self.queue_surface.setToolTip(text)

    def _recording_sources(self) -> list[Path]:
        return find_recordings(self._paths)

    def _pick_recording_source(self, dialog_parent=None) -> Path | None:
        recordings = self._recording_sources()
        if not recordings:
            QMessageBox.information(
                dialog_parent,
                self._tr.tr("app_title"),
                self._tr.tr("recorder_export_empty"),
            )
            return None
        if len(recordings) == 1:
            return recordings[0]
        labels = [f"{path.name} — {path.parent.name}" for path in recordings]
        chosen, ok = QInputDialog.getItem(
            dialog_parent,
            self._tr.tr("recorder_export"),
            self._tr.tr("recorder_export_select"),
            labels,
            0,
            False,
        )
        if not ok:
            return None
        return recordings[labels.index(chosen)]

    def _export_recording_as(self, output_format: str) -> None:
        if self._audio_export_worker and self._audio_export_worker.isRunning():
            return
        dialog_parent = self if self.isVisible() else None
        source = self._pick_recording_source(dialog_parent)
        if source is None:
            return
        suffix = ".wav" if str(output_format).lower() == "wav" else ".m4a"
        default_path = self._paths.output / f"{source.stem}{suffix}"
        file_filter = (
            "WAV PCM 16-bit (*.wav)"
            if suffix == ".wav"
            else "M4A / AAC 128 kbit/s (*.m4a)"
        )
        destination, _selected_filter = QFileDialog.getSaveFileName(
            dialog_parent,
            self._tr.tr("recorder_export"),
            str(default_path),
            file_filter,
        )
        if not destination:
            return
        target = Path(destination)
        if target.suffix.lower() != suffix:
            target = target.with_suffix(suffix)
        worker = AudioExportWorker(self._paths, source, target, parent=self)
        self._audio_export_worker = worker
        worker.done.connect(self._on_audio_export_done)
        worker.failed.connect(self._on_audio_export_failed)
        worker.finished.connect(lambda: self._clear_audio_export_worker(worker))
        worker.finished.connect(worker.deleteLater)
        self.status_bar.showMessage(self._tr.tr("recorder_exporting"))
        worker.start()

    def _on_audio_export_done(self, path: str) -> None:
        message = self._tr.tr("recorder_exported").replace("{path}", path)
        self._append_log(message)
        self.status_bar.showMessage(message, 8000)

    def _on_audio_export_failed(self, error: str) -> None:
        message = self._tr.tr("recorder_export_failed").replace("{error}", error)
        self._append_log(f"WARN: {message}")
        QMessageBox.warning(
            self if self.isVisible() else None,
            self._tr.tr("app_title"),
            message,
        )

    def _clear_audio_export_worker(self, worker: AudioExportWorker) -> None:
        if self._audio_export_worker is worker:
            self._audio_export_worker = None
        if self._close_after_audio_export:
            self._close_after_audio_export = False
            QTimer.singleShot(0, self.close)

    # --- run / stop ----------------------------------------------------------
    def _on_run(self) -> None:
        if self._file_recorder and (
            self._file_recorder.is_recording or self._file_recorder.is_finalizing
        ):
            self.status_bar.showMessage(self._tr.tr("recorder_recording"), 5000)
            return
        if self.queue_model.is_empty():
            QMessageBox.information(self, self._tr.tr("app_title"), self._tr.tr("input_area"))
            return
        # Keep the CLI and a restart in sync with the visible GUI state.
        self._save_settings_now(force=True)

        self.queue_model.reset_statuses()
        # Explicit checks narrow the operation; with no checks the familiar
        # one-click behaviour still processes the complete queue.
        inputs = self.queue_model.checked_files() or self.queue_model.files()

        self._queue_worker = QueueWorker(self._paths, inputs, self._settings)
        self._queue_worker.log.connect(self._append_log)
        self._queue_worker.status.connect(self.queue_model.update_status)
        self._queue_worker.finished_summary.connect(self._on_finished)
        self._set_running(True)
        self._append_log("=== Run started ===")
        self._queue_worker.start()

    def _on_stop(self) -> None:
        if self._queue_worker and self._queue_worker.isRunning():
            self._queue_worker.cancel()
            self._append_log("Stopping after the current file...")
            self.btn_stop.setEnabled(False)

    def _on_retry(self) -> None:
        # Failed files left no outputs, so a normal run retries them and skips
        # the completed ones. Just re-run the queue.
        if not self.queue_model.is_empty():
            self._on_run()

    def _on_clear(self) -> None:
        if self._queue_worker and self._queue_worker.isRunning():
            return
        selected = self._selected_queue_items()
        if not selected:
            QMessageBox.information(self, self._tr.tr("app_title"), self._tr.tr("queue_select_files"))
            return
        input_root = self._paths.input.resolve()
        if any(not source.resolve().is_relative_to(input_root) for _row, source in selected):
            QMessageBox.warning(self, self._tr.tr("app_title"), self._tr.tr("delete_input_outside"))
            return
        prompt = self._tr.tr("delete_input_confirm").replace("{count}", str(len(selected)))
        answer = QMessageBox.question(
            self,
            self._tr.tr("app_title"),
            prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        removed_rows: list[int] = []
        errors: list[str] = []
        with JobStore(self._paths.db_file) as store:
            for row, source in selected:
                try:
                    resolved = source.resolve()
                    if resolved.exists():
                        if not resolved.is_file():
                            raise OSError(f"Not a file: {resolved}")
                        resolved.unlink()
                    removed_rows.append(row)
                except Exception as exc:
                    errors.append(f"{source.name}: {exc}")
                    continue
                try:
                    remove_source_workspace(self._paths, source)
                    store.delete_job(str(source))
                except Exception as exc:
                    errors.append(f"{source.name} (temp): {exc}")

        self.queue_model.remove_rows(removed_rows)
        self._schedule_queue_save()
        if removed_rows:
            message = self._tr.tr("delete_input_done").replace("{count}", str(len(removed_rows)))
            self.status_bar.showMessage(message, 6000)
            self._append_log(message)
        if errors:
            QMessageBox.warning(
                self,
                self._tr.tr("app_title"),
                self._tr.tr("delete_input_failed").replace("{error}", "\n".join(errors)),
            )

    def _selected_queue_items(self) -> list[tuple[int, Path]]:
        files = self.queue_model.files()
        rows = self.queue_model.checked_rows()
        if not rows:
            selection = self.queue_view.selectionModel()
            if selection is None:
                return []
            rows = sorted({index.row() for index in selection.selectedRows()})
        return [(row, files[row]) for row in rows if 0 <= row < len(files)]

    def _remove_selected_from_queue(self) -> None:
        selected = self._selected_queue_items()
        if not selected:
            return
        self.queue_model.remove_rows([row for row, _source in selected])
        self._schedule_queue_save()

    def _cleanup_selected_temp(self) -> None:
        selected = self._selected_queue_items()
        if not selected:
            QMessageBox.information(self, self._tr.tr("app_title"), self._tr.tr("queue_select_files"))
            return
        cleaned = 0
        errors: list[str] = []
        with JobStore(self._paths.db_file) as store:
            for _row, source in selected:
                try:
                    removed = remove_source_workspace(self._paths, source)
                    forgotten = store.delete_job(str(source))
                    cleaned += int(removed or forgotten)
                    self.queue_model.update_status(str(source), "pending")
                except Exception as exc:
                    errors.append(f"{source.name}: {exc}")
        message = self._tr.tr("cleanup_temp_done").replace("{count}", str(cleaned))
        self.status_bar.showMessage(message, 6000)
        self._append_log(message)
        if errors:
            QMessageBox.warning(self, self._tr.tr("app_title"), "\n".join(errors))

    # --- speaker rename (TZ §11) --------------------------------------------
    def _on_queue_context_menu(self, pos) -> None:
        index = self.queue_view.indexAt(pos)
        if not index.isValid():
            return
        files = self.queue_model.files()
        if index.row() >= len(files):
            return
        source = files[index.row()]
        selected_rows = {item.row() for item in self.queue_view.selectionModel().selectedRows()}
        if index.row() not in selected_rows:
            self.queue_view.clearSelection()
            self.queue_view.selectRow(index.row())
        menu = QMenu(self)
        act_rename = menu.addAction(icon("tune", "#9FB0C3"), self._tr.tr("speaker_rename_action"))
        menu.addSeparator()
        act_remove = menu.addAction(icon("close", "#9FB0C3"), self._tr.tr("remove_queue_selected"))
        act_cleanup = menu.addAction(icon("refresh", "#9FB0C3"), self._tr.tr("cleanup_temp_selected"))
        act_delete = menu.addAction(icon("delete", "#9FB0C3"), self._tr.tr("clear_queue"))
        chosen = menu.exec(self.queue_view.viewport().mapToGlobal(pos))
        if chosen is act_rename:
            self._open_speaker_rename(source)
        elif chosen is act_remove:
            self._remove_selected_from_queue()
        elif chosen is act_cleanup:
            self._cleanup_selected_temp()
        elif chosen is act_delete:
            self._on_clear()

    def _open_speaker_rename(self, source: Path) -> None:
        from ..pipeline.orchestrator import sidecar_paths
        from .speaker_dialog import SpeakerRenameDialog

        json_path = sidecar_paths(source.resolve(), self._settings, self._paths)["json"]
        if not json_path.exists():
            QMessageBox.information(
                self, self._tr.tr("app_title"), self._tr.tr("speaker_no_transcript")
            )
            return
        dialog = SpeakerRenameDialog(self._paths, self._tr, self._settings, json_path, parent=self)
        if dialog.exec() and dialog.written:
            self.status_bar.showMessage(
                self._tr.tr("speaker_rename_done").replace("{count}", str(len(dialog.written))),
                6000,
            )
            self._append_log(
                self._tr.tr("speaker_rename_done").replace("{count}", str(len(dialog.written)))
            )

    def _on_finished(self, summary) -> None:
        self._set_running(False)
        if summary is None:
            self._append_log("=== Run aborted ===")
            return
        self._append_log(
            f"=== Done: {len(summary.completed)} ok, "
            f"{len(summary.skipped)} skipped, {len(summary.failed)} failed ==="
        )
        self.status_bar.showMessage(
            f"{len(summary.completed)} / {summary.total}", 8000
        )

    def _set_running(self, running: bool) -> None:
        self.btn_run.setEnabled(not running)
        self.btn_retry.setEnabled(not running)
        self.btn_clear.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_add_files.setEnabled(not running)
        self.btn_add_folder.setEnabled(not running)
        self.settings_panel.setEnabled(not running)

    # --- models --------------------------------------------------------------
    def _start_model_fetch(self, *, refresh: bool) -> None:
        if self._model_worker and self._model_worker.isRunning():
            return
        if refresh:
            self.status_bar.showMessage(self._tr.tr("refresh_models") + "...", 4000)
        worker = ModelWorker(self._paths, refresh=refresh, parent=self)
        self._model_worker = worker
        worker.done.connect(self._on_models)
        worker.failed.connect(lambda msg: self._append_log(f"Models: {msg}"))
        worker.finished.connect(lambda: self._clear_model_worker(worker))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _clear_model_worker(self, worker: ModelWorker) -> None:
        if self._model_worker is worker:
            self._model_worker = None

    def _on_models(self, payload: dict) -> None:
        stt_models, source = payload.get("stt", ([], "fallback"))
        chat_models, _ = payload.get("chat", ([], "fallback"))
        self.settings_panel.set_model_lists(stt_models, chat_models, source)

    # --- run summary ---------------------------------------------------------
    def _update_run_summary(self) -> None:
        """Refresh the at-a-glance strip under the Run button from live settings."""
        if not hasattr(self, "lbl_run_summary"):
            return
        tr = self._tr
        try:
            s = self.settings_panel.values()
        except Exception:
            s = self._settings
        mode = display_compute_mode(s.get("compute_mode", "api"))
        mode_label = tr.tr(compute_mode_label_key(mode))
        if mode == "api":
            stt_cfg = s.get("stt") or {}
            provider = str(stt_cfg.get("provider") or "openai").strip().lower()
            provider_keys = {
                "openai": "file_provider_openai",
                "xai": "file_provider_xai",
                "gemini": "file_provider_gemini",
                "gigachat": "file_provider_gigachat",
                "assemblyai": "file_provider_assemblyai",
            }
            model_keys = {
                "openai": "model",
                "xai": "xai_model",
                "gemini": "gemini_model",
                "gigachat": "gigachat_model",
            }
            provider_label = tr.tr(provider_keys.get(provider, "file_provider_openai"))
            model = stt_cfg.get(model_keys.get(provider, "model")) or stt_cfg.get("model") or "—"
            stt = f"{provider_label}: {model}"
        elif mode == "vulkan":
            stt = (s.get("vulkan") or {}).get("model") or "—"
        else:
            stt = (s.get("local") or {}).get("model") or "—"
        diar_on = bool((s.get("transcription") or {}).get("diarize")) or \
            bool((s.get("diarization") or {}).get("enabled"))
        cleanup_on = bool(((s.get("postprocessing") or {}).get("cleanup") or {}).get("enabled"))

        # Color the "on" states (diarization / cleanup) with the theme's success
        # accent so the toggles that actually change cost/behaviour stand out;
        # everything else stays muted. Rich text keeps it one compact strip.
        tok = self._theme.tokens
        muted = tok.get("color-text-secondary", "#8090A4")
        strong = tok.get("color-text-primary", "#F4F4F4")
        on_col = tok.get("color-status-success", "#1D9E75")

        def esc(text: str) -> str:
            return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        def toggle(label: str, is_on: bool) -> str:
            col = on_col if is_on else muted
            weight = "600" if is_on else "400"
            value = tr.tr("run_on") if is_on else tr.tr("run_off")
            return (f"{esc(label)}: <span style='color:{col}; font-weight:{weight}'>"
                    f"{esc(value)}</span>")

        sep = f"<span style='color:{muted}'>&nbsp;&nbsp;·&nbsp;&nbsp;</span>"
        parts = [
            f"<span style='color:{muted}'>{esc(mode_label)}</span>",
            f"<span style='color:{strong}'>{esc(stt)}</span>",
            f"<span style='color:{muted}'>{toggle(tr.tr('run_diar'), diar_on)}</span>",
            f"<span style='color:{muted}'>{toggle(tr.tr('run_cleanup'), cleanup_on)}</span>",
        ]
        self.lbl_run_summary.setText(sep.join(parts))

    # --- theme / language ----------------------------------------------------
    def _collect_settings(self) -> dict[str, Any]:
        settings = self.settings_panel.values()
        settings["ui_language"] = self._tr.language
        settings["ui_theme"] = self._theme.name
        return settings

    def apply_theme(self, name: str) -> None:
        from PySide6.QtWidgets import QApplication

        self._theme = theme_mod.get_theme(self._paths, name)
        self._settings["ui_theme"] = self._theme.name
        QApplication.instance().setStyleSheet(theme_mod.build_stylesheet(self._theme))
        self._apply_theme_icons()
        self._sync_text_menu_button_sizes()
        self.settings_panel.apply_theme_tokens(self._theme.tokens)
        self._apply_work_surface_colors()
        # Recolor existing status cells under the new palette.
        self.queue_model.layoutChanged.emit()
        if self._overlay:
            self._overlay.apply_tokens(self._theme.tokens)
        self._update_run_summary()
        self._schedule_settings_save()

    def _apply_work_surface_colors(self) -> None:
        """Darken only the inner work surfaces, not the cards or app window."""
        surface = self._theme.tokens.get(
            "color-background-work",
            self._theme.tokens.get("color-background-primary", "#0F1622"),
        )
        if hasattr(self, "queue_view"):
            self.queue_view.viewport().setStyleSheet(f"background-color: {surface};")
        if hasattr(self, "log_view"):
            self.log_view.viewport().setStyleSheet(f"background-color: {surface};")

    def _apply_theme_icons(self) -> None:
        fg = self._theme.tokens.get("color-text-primary", "#E6EDF5")
        muted = self._theme.tokens.get("color-text-secondary", "#9FB0C3")
        icon_primary = self._theme.tokens.get("color-icon-primary", fg)
        icon_secondary = self._theme.tokens.get("color-icon-secondary", muted)
        self.btn_add_files.setIcon(icon("file", icon_primary))
        self.btn_add_folder.setIcon(icon("folder_open", icon_primary))
        self.btn_export.setIcon(icon("upload", icon_primary))
        self.btn_run.setIcon(icon("play", icon_primary))
        self.btn_stop.setIcon(icon("stop", icon_primary))
        self.btn_retry.setIcon(icon("refresh", icon_primary))
        self.btn_clear.setIcon(icon("delete", icon_secondary))
        self.btn_open_in.setIcon(icon("folder_open", icon_secondary))
        self.btn_open_out.setIcon(icon("folder_open", icon_secondary))
        self.btn_log_export.setIcon(icon("upload", icon_primary))
        self.btn_log_clear.setIcon(icon("delete", icon_secondary))
        self.btn_log_save.setIcon(icon("download", icon_secondary))
        self.btn_log_expand.setIcon(
            icon("fullscreen_exit" if self._log_expanded else "fullscreen", icon_secondary)
        )
        self._sync_live_record_button()

    def _sync_live_record_button(self, state: Optional[LiveState] = None) -> None:
        if not hasattr(self, "btn_live_record"):
            return
        live_state = (
            state if state is not None else
            (self._live.state() if self._live else LiveState.DISABLED)
        )
        active = live_state != LiveState.DISABLED
        recorder_busy = bool(
            self._file_recorder
            and (self._file_recorder.is_recording or self._file_recorder.is_finalizing)
        )
        self.btn_live_record.setEnabled(self._live is not None and not recorder_busy)
        self.btn_live_record.blockSignals(True)
        self.btn_live_record.setChecked(active)
        self.btn_live_record.blockSignals(False)
        danger = self._theme.tokens.get("color-status-error", "#D85A30")
        self.btn_live_record.setIcon(icon("mic", "#FFFFFF" if active else danger))
        hotkey = self._live_cfg.hotkey.replace("ralt", "Right Alt").replace("+", " + ")
        state_key = "live_armed_msg" if active else "live_disarmed_msg"
        state_text = self._tr.tr(state_key).replace("{hotkey}", hotkey)
        self.btn_live_record.setToolTip(f"{state_text}\n{self._tr.tr('live_record_hint')}")
        self.btn_live_record.setAccessibleName(state_text)

    def _on_language_changed(self) -> None:
        code = self.cmb_lang.currentData()
        self._tr.set_language(code)
        self._settings["ui_language"] = code
        self._retranslate()
        self.settings_panel.retranslate(self._tr)
        self._schedule_settings_save()

    # --- persistence ---------------------------------------------------------
    def _schedule_settings_save(self) -> None:
        if self._autosave_ready:
            self._settings_save_timer.start()

    def _save_settings_now(self, *, force: bool = False) -> None:
        if not (self._autosave_ready or force):
            return
        self._settings_save_timer.stop()
        try:
            self._settings = self._collect_settings()
            save_settings(self._paths, self._settings)
            self._apply_live_runtime_settings()
        except Exception as exc:
            if hasattr(self, "log_view"):
                self._append_log(f"WARN: could not save settings: {exc}")

    def _reset_app_settings(self) -> None:
        answer = QMessageBox.question(
            self,
            self._tr.tr("reset_app"),
            self._tr.tr("reset_app_confirm"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        edition = current_edition(self._paths, self._settings)
        defaults = copy.deepcopy(DEFAULT_SETTINGS)
        defaults["edition"] = edition
        self._settings = defaults
        self._live_input_device_preference = None
        self._live_active_input_device = ""
        if self._live:
            self._live.set_input_device_preference(None)
        self._tr.set_language(str(self._settings.get("ui_language") or "ru"))
        save_settings(self._paths, self._settings)
        self._rebuild_settings_panel()
        self._retranslate()
        self.apply_theme(str(self._settings.get("ui_theme") or "dark_blue"))
        self._apply_live_runtime_settings()
        self._start_model_fetch(refresh=False)
        self._append_log(self._tr.tr("reset_app_done"))

    def _rebuild_settings_panel(self) -> None:
        old = self._right_panel.takeWidget()
        if old is not None:
            if hasattr(old, "stop_background_tasks"):
                old.stop_background_tasks()
            old.deleteLater()
        self.settings_panel = SettingsPanel(self._paths, self._tr, self._settings, self._prompts)
        self._connect_settings_panel()
        self._right_panel.setWidget(self.settings_panel)

    def _restore_queue_state(self) -> None:
        files = load_queue_files(self._paths)
        if files:
            self.queue_model.set_files(files)

    def _schedule_queue_save(self) -> None:
        if self._autosave_ready:
            self._queue_save_timer.start()

    def _save_queue_state_now(self, *, force: bool = False) -> None:
        if not (self._autosave_ready or force):
            return
        self._queue_save_timer.stop()
        try:
            save_queue_files(self._paths, self.queue_model.files())
        except Exception as exc:
            if hasattr(self, "log_view"):
                self._append_log(f"WARN: could not save queue state: {exc}")

    def _flush_autosave(self) -> None:
        self._save_settings_now(force=True)
        self._save_queue_state_now(force=True)

    def _retranslate(self) -> None:
        tr = self._tr
        self.setWindowTitle(tr.tr("app_title"))
        self.lbl_title.setText(tr.tr("app_title"))
        self.lbl_lang.setText(tr.tr("app_language"))
        self.lbl_theme.setText(tr.tr("theme"))
        self.cmb_lang.setToolTip(tr.tr("app_language"))
        self.cmb_theme.setToolTip(tr.tr("theme"))
        for key in self._workspace_keys:
            self._workspace_tabs.set_tab_text(key, tr.tr(key))
        current_theme = self.cmb_theme.currentData()
        self.cmb_theme.blockSignals(True)
        self.cmb_theme.clear()
        for info in theme_mod.list_themes(self._paths):
            label = info.label_ru if tr.language == "ru" else info.label
            self.cmb_theme.addItem(label, info.name)
        tidx = self.cmb_theme.findData(current_theme or self._theme.name)
        self.cmb_theme.setCurrentIndex(tidx if tidx >= 0 else 0)
        self.cmb_theme.blockSignals(False)
        self.queue_box.setTitle(tr.tr("queue"))
        self.log_box.setTitle(tr.tr("logs"))
        self.queue_view.setPlaceholder(tr.tr("queue_hint"))
        self._update_format_hint()
        self.queue_model.set_headers(
            tr.tr("col_select"), tr.tr("col_file"), tr.tr("col_status")
        )
        self.lbl_add_sources.setText(tr.tr("add_sources"))
        self.btn_add_files.setToolTip(tr.tr("add_files_tip"))
        self.btn_add_files.setAccessibleName(tr.tr("add_files"))
        self.btn_add_folder.setToolTip(tr.tr("add_folder_tip"))
        self.btn_add_folder.setAccessibleName(tr.tr("add_folder"))
        self.btn_export.setText(tr.tr("tray_export"))
        self.btn_export.setToolTip(tr.tr("export_menu_hint"))
        self.btn_log_export.setText(tr.tr("tray_export"))
        self.btn_run.setToolTip(tr.tr("run"))
        self.btn_stop.setToolTip(tr.tr("stop"))
        self.btn_retry.setToolTip(tr.tr("retry_failed"))
        self.btn_clear.setToolTip(tr.tr("clear_queue"))
        self.btn_clear.setAccessibleName(tr.tr("clear_queue"))
        self.btn_open_in.setText(tr.tr("input_folder_button"))
        self.btn_open_in.setToolTip(tr.tr("open_input"))
        self.btn_open_in.setAccessibleName(tr.tr("open_input"))
        self.btn_open_out.setText(tr.tr("output_folder_button"))
        self.btn_open_out.setToolTip(tr.tr("open_output"))
        self.btn_open_out.setAccessibleName(tr.tr("open_output"))
        self._sync_live_record_button()
        self._populate_live_audio_devices()
        self.btn_log_export.setToolTip(tr.tr("export_menu_hint"))
        self.btn_log_clear.setToolTip(tr.tr("clear_log"))
        self.btn_log_save.setToolTip(tr.tr("save_log_md"))
        self.btn_log_expand.setToolTip(tr.tr("collapse_log" if self._log_expanded else "expand_log"))
        self.btn_log_export.setAccessibleName(tr.tr("tray_export"))
        self.btn_log_clear.setAccessibleName(tr.tr("clear_log"))
        self.btn_log_save.setAccessibleName(tr.tr("save_log_md"))
        self.btn_log_expand.setAccessibleName(
            tr.tr("collapse_log" if self._log_expanded else "expand_log")
        )
        for action, label_key in self._export_actions:
            action.setText(tr.tr(label_key))
        self._sync_overlay_record_action()
        if self._tray:
            self._tray.retranslate(tr)
        if self._overlay:
            self._overlay.retranslate(tr)
        if self._dictation_history_dialog:
            self._dictation_history_dialog.retranslate(tr)
        self._update_run_summary()
        self._sync_text_menu_button_sizes()
        seed_tooltips(self)

    def _sync_text_menu_button_sizes(self) -> None:
        self.btn_export.setMinimumWidth(152)
        self.btn_export.setMaximumWidth(16777215)
        self.btn_export.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.btn_export.updateGeometry()
        self.btn_log_export.setFixedWidth(152)
        self.btn_log_export.updateGeometry()

    # --- live dictation / tray ----------------------------------------------
    @staticmethod
    def _audio_device_preference(data) -> tuple[str, str] | None:
        if isinstance(data, (tuple, list)) and len(data) == 2:
            return str(data[0] or ""), str(data[1] or "")
        return None

    def _set_live_audio_badge(self, device: str) -> None:
        value = str(device or "").strip() or self._tr.tr("live_audio_auto_windows")
        full = self._tr.tr("live_audio_badge").replace("{device}", value)
        self.lbl_live_audio_device.setFullText(full)
        self.lbl_live_audio_device.setToolTip(full)
        self.lbl_live_audio_device.setAccessibleName(full)

    def _populate_live_audio_devices(self) -> None:
        candidates = self._live_audio_candidates
        preference = self._live_input_device_preference
        self._refreshing_live_audio_devices = True
        self.cmb_live_audio_device.blockSignals(True)
        try:
            self.cmb_live_audio_device.clear()
            self.cmb_live_audio_device.addItem(
                icon("mic", "#85B7EB", 16),
                self._tr.tr("live_audio_select_auto"),
                None,
            )
            selected_index = 0
            selected_candidate = None
            for candidate in candidates:
                key = (str(candidate.host_api or ""), str(candidate.name or ""))
                self.cmb_live_audio_device.addItem(
                    icon("mic", "#9FB0C3", 16), candidate.label, key
                )
                if key == preference:
                    selected_index = self.cmb_live_audio_device.count() - 1
                    selected_candidate = candidate
            if preference is not None and selected_candidate is None:
                host, name = preference
                label = f"{host} / {name}" if host else name
                unavailable = self._tr.tr("live_audio_unavailable").replace(
                    "{device}", label
                )
                self.cmb_live_audio_device.addItem(
                    icon("mic", "#D85A30", 16), unavailable, preference
                )
                selected_index = self.cmb_live_audio_device.count() - 1
            self.cmb_live_audio_device.setCurrentIndex(selected_index)
        finally:
            self.cmb_live_audio_device.blockSignals(False)
            self._refreshing_live_audio_devices = False

        if preference is None:
            predicted = candidates[0].label if candidates else ""
        elif selected_candidate is not None:
            predicted = selected_candidate.label
        else:
            predicted = self._tr.tr("live_audio_device_missing")
        self._set_live_audio_badge(self._live_active_input_device or predicted)
        current = self.cmb_live_audio_device.currentText()
        hint = self._tr.tr("live_audio_device_hint")
        self.cmb_live_audio_device.setToolTip(f"{hint}\n{current}")
        self.cmb_live_audio_device.setAccessibleName(hint)
        self._sync_live_audio_device_controls()

    def _refresh_live_audio_devices(self) -> None:
        # Refreshing is safe only while no stream is active; the selector is
        # disabled during listening/transcription for exactly this reason.
        try:
            import sounddevice as sd  # type: ignore

            from ..live.mic_check import input_device_candidates, refresh_device_inventory

            refresh_device_inventory(sd)
            self._live_audio_candidates = input_device_candidates(sd)
            self._live_active_input_device = ""
        except Exception:
            self._live_audio_candidates = ()
        self._populate_live_audio_devices()

    def _on_live_audio_device_selected(self, index: int) -> None:
        if self._refreshing_live_audio_devices or index < 0:
            return
        preference = self._audio_device_preference(
            self.cmb_live_audio_device.itemData(index)
        )
        old_preference = self._live_input_device_preference
        if self._live and not self._live.set_input_device_preference(preference):
            self._live_input_device_preference = old_preference
            self._populate_live_audio_devices()
            return
        self._live_input_device_preference = preference
        self._live_active_input_device = ""
        self._populate_live_audio_devices()

    def _on_live_input_device_changed(self, device: str) -> None:
        self._live_active_input_device = str(device or "").strip()
        self._set_live_audio_badge(self._live_active_input_device)

    def _sync_live_audio_device_controls(self, state: LiveState | None = None) -> None:
        if state is None and self._live is not None:
            state = self._live.state()
        busy = state in (LiveState.LISTENING, LiveState.TRANSCRIBING) or bool(
            self._file_recorder
            and (self._file_recorder.is_recording or self._file_recorder.is_finalizing)
        )
        has_choice = bool(
            self._live_audio_candidates or self._live_input_device_preference
        )
        self.cmb_live_audio_device.setEnabled(
            self._live is not None and has_choice and not busy
        )

    def _setup_tray(self) -> None:
        """Create the resident tray icon + Live controller (no-op if Live is
        disabled or the OS has no system tray, e.g. headless/offscreen)."""
        if not self._live_cfg.enabled or self._live is not None:
            return
        dependency_status = check_live_dependencies()
        if not dependency_status.ready:
            self._start_live_deps_install(dependency_status)
            return
        self._setup_live_runtime()

    def _setup_live_runtime(self) -> None:
        """Create and arm Live after its small runtime dependency check."""
        if self._live is not None or not self._live_cfg.enabled:
            return
        self._live = LiveController(self._paths, self._live_cfg, parent=self)

        self._overlay = None
        if self._live_cfg.show_overlay:
            self._overlay = self._create_live_overlay()
            self._overlay.show_idle()

        self._live.state_changed.connect(self._on_live_state)
        self._live.notice.connect(self._on_live_notice)
        self._live.error.connect(self._append_log)
        # Dictation is pasted into the focused app; the log mirrors it and the
        # overlay shows the final briefly before fading.
        self._live.text_committed.connect(self._on_live_committed)
        # Live partials (realtime engine): status bar + overlay.
        self._live.partial.connect(self._on_live_partial)
        self._live.audio_level.connect(self._on_live_audio_level)
        self._live.input_device_changed.connect(self._on_live_input_device_changed)
        self._live.transport_state.connect(self._on_live_transport_state)
        self._live.model_switch_finished.connect(self._on_live_model_switch_finished)
        self._live.set_input_device_preference(self._live_input_device_preference)
        self._refresh_live_audio_devices()

        if self._live_cfg.minimize_to_tray:
            self._ensure_tray_icon()
        self._sync_live_record_button(self._live.state())

        # While the application is resident, its overlay, tray, hotkey and
        # selected local Live models stay ready together.
        self._live.set_armed(True)
        if self._tray:
            self._tray.set_live_state(self._live.state())
        self._sync_live_record_button(self._live.state())

    def _start_live_deps_install(self, status) -> None:
        worker = self._live_deps_worker
        if worker is not None and worker.isRunning():
            return
        script = self._paths.root / "install" / "Install-Live-Deps.cmd"
        missing = ", ".join(status.missing) or status.detail
        if not script.exists():
            self._append_install_log(
                self._tr.tr("live_deps_auto_missing_installer").replace("{path}", str(script))
            )
            return
        self._append_install_log(
            self._tr.tr("live_deps_auto_start").replace("{missing}", missing)
        )
        panel = getattr(self.settings_panel, "modules_panel", None)
        if panel is not None:
            panel._set_busy(True)
            panel._reset_progress(self._tr.tr("mod_live"))
        worker = InstallWorker(self._paths, script, parent=self)
        self._live_deps_worker = worker
        worker.log.connect(self._append_install_log)
        if panel is not None:
            worker.progress.connect(panel._on_progress)
        worker.done.connect(self._on_live_deps_install_done)
        worker.finished.connect(lambda w=worker: self._clear_live_deps_worker(w))
        worker.start()

    def _clear_live_deps_worker(self, worker: InstallWorker) -> None:
        if self._live_deps_worker is worker:
            self._live_deps_worker = None
        worker.deleteLater()

    def _on_live_deps_install_done(self, code: int) -> None:
        panel = getattr(self.settings_panel, "modules_panel", None)
        if panel is not None:
            panel._set_busy(False)
            panel._refresh_all()
        status = check_live_dependencies()
        if code == 0 and status.ready:
            if panel is not None:
                panel._progress_bar.setValue(1000)
                panel._progress_detail.setText(self._tr.tr("install_progress_done"))
            self._append_install_log(self._tr.tr("live_deps_auto_done"))
            self._setup_live_runtime()
            return
        detail = status.detail or f"exit code {code}"
        self._append_install_log(
            self._tr.tr("live_deps_auto_failed")
            .replace("{code}", str(code))
            .replace("{error}", detail)
        )

    def _on_module_installed(self, key: str, code: int) -> None:
        if key == "live" and code == 0 and self._live is None:
            self._setup_tray()

    def _ensure_tray_icon(self) -> None:
        if self._tray or not QSystemTrayIcon.isSystemTrayAvailable():
            return
        # A standalone glyph uses the tiny Windows tray slot much better than
        # the branded rounded-square app icon (which adds two layers of padding).
        self._tray = VoiceTray(icon("mic", "#85B7EB"), self._tr, parent=self)
        self._tray.menu_opened.connect(self._capture_dictation_target)
        self._tray.show_window.connect(self._show_from_tray)
        self._tray.show_dictation_history.connect(
            lambda: self._show_dictation_history(
                limit=HISTORY_CACHE_LIMIT,
                title_key="dictation_history_all_title",
            )
        )
        self._tray.toggle_live.connect(self._on_toggle_live)
        self._tray.live_profile_requested.connect(self._on_live_profile_requested)
        self._tray.save_log.connect(self._save_log_markdown_with_picker)
        self._tray.open_output.connect(lambda: open_folder(self._paths.output))
        self._tray.export_requested.connect(self._on_export_requested)
        self._tray.recording_export_requested.connect(self._export_recording_as)
        self._tray.quit_app.connect(self._quit_app)
        if self._live:
            self._tray.set_live_state(self._live.state())
        self._tray.show()

    def _remove_tray_icon(self) -> None:
        if not self._tray:
            return
        self._tray.hide()
        self._tray.deleteLater()
        self._tray = None

    def _apply_live_runtime_settings(self) -> None:
        old_overlay = self._live_cfg.show_overlay
        next_config = LiveConfig.from_settings(self._settings)
        requires_reload = getattr(
            self._live, "config_requires_transcriber_reload", lambda _config: False
        )
        model_switch = bool(
            self._live
            and requires_reload(next_config)
            and self._live.state() in (LiveState.ARMED, LiveState.DISABLED)
        )
        self._live_cfg = next_config
        recorder_busy = bool(
            self._file_recorder
            and (self._file_recorder.is_recording or self._file_recorder.is_finalizing)
        )
        animate_model_switch = bool(
            model_switch
            and self._overlay
            and self._overlay.isVisible()
            and next_config.show_overlay
            and not recorder_busy
        )

        # Push changes into the controller after the old overlay has completely
        # dissolved.  The new resident model signals when it is safe to fade in.
        def update_controller() -> None:
            if animate_model_switch:
                self._overlay_model_switch_pending = True
                self._overlay_model_switch_signature = self._live.transcriber_signature(
                    next_config
                ) if self._live else None
            if not self._live:
                self._on_live_model_switch_finished(None)
                return
            can_prewarm = self._live.state() in (LiveState.ARMED, LiveState.DISABLED)
            self._live.update_config(next_config)
            if not can_prewarm:
                self._on_live_model_switch_finished(self._overlay_model_switch_signature)

        if animate_model_switch:
            self._overlay.fade_out_for_model_switch(update_controller)
        else:
            update_controller()
        if self._live_cfg.minimize_to_tray:
            self._ensure_tray_icon()
        else:
            self._remove_tray_icon()
        if self._overlay and not self._live_cfg.show_overlay:
            self._overlay.hide()
        elif self._live and self._overlay is None and self._live_cfg.show_overlay and old_overlay != self._live_cfg.show_overlay:
            self._overlay = self._create_live_overlay()
            self._overlay.show_idle()
        if self._overlay:
            self._overlay.set_scale_percent(self._live_cfg.overlay_scale_percent)

    def _on_live_model_switch_finished(self, signature=None) -> None:
        if not self._overlay_model_switch_pending:
            return
        if (
            self._overlay_model_switch_signature is not None
            and signature != self._overlay_model_switch_signature
        ):
            return
        self._overlay_model_switch_pending = False
        self._overlay_model_switch_signature = None
        if self._overlay:
            self._overlay.fade_in_after_model_switch()

    def _create_live_overlay(self):
        from ..live.overlay import LiveOverlay

        overlay = LiveOverlay(
            self._tr,
            self._theme.tokens,
            scale_percent=self._live_cfg.overlay_scale_percent,
        )
        overlay.record_requested.connect(self._on_live_overlay_record)
        overlay.pause_requested.connect(self._on_file_recorder_pause)
        overlay.stop_requested.connect(self._on_live_overlay_stop)
        overlay.cancel_requested.connect(self._on_live_overlay_cancel)
        overlay.context_menu_requested.connect(self._show_overlay_context_menu)
        return overlay

    def _select_overlay_workspace(self, key: str) -> None:
        page = self._workspace_pages.get(key)
        if page is None:
            return
        self._workspace_tabs.set_current_key(key)
        self._workspace_stack.setCurrentWidget(page)
        self._sync_overlay_record_action()
        if self._overlay:
            self._overlay.show_idle()

    def _show_settings_from_overlay(self) -> None:
        self.settings_panel.show_section("settings_section_settings")
        self._show_from_tray()

    def _show_overlay_context_menu(self, global_position) -> None:
        self._capture_dictation_target()

        menu = QMenu(self)
        menu.setObjectName("OverlayQuickMenu")
        menu.setWindowOpacity(0.85)
        menu.setAttribute(Qt.WA_TranslucentBackground, True)
        menu.setAttribute(Qt.WA_DeleteOnClose, True)

        history = menu.addAction(
            icon("history", "#85B7EB"), self._tr.tr("dictation_history_title")
        )
        history.triggered.connect(
            lambda: self._show_dictation_history(
                limit=OVERLAY_HISTORY_LIMIT,
                title_key="dictation_history_title",
            )
        )
        paste_last = menu.addAction(
            icon("copy", "#E6EDF5"), self._tr.tr("dictation_history_paste_last")
        )
        latest = self._dictation_history.latest()
        paste_last.setEnabled(latest is not None)
        if latest is not None:
            paste_last.triggered.connect(
                lambda _checked=False, text=latest.text: self._paste_dictation(text)
            )

        menu.addSeparator()
        live_mode = menu.addAction(
            icon("mic", "#85B7EB"), self._tr.tr("overlay_menu_live_mode")
        )
        live_mode.setCheckable(True)
        live_mode.setChecked(self._workspace_stack.currentWidget() is self.log_box)
        live_mode.triggered.connect(
            lambda: self._select_overlay_workspace("workspace_tab_live_log")
        )

        file_mode = menu.addAction(
            icon("file", "#85B7EB"), self._tr.tr("overlay_menu_file_mode")
        )
        file_mode.setCheckable(True)
        file_mode.setChecked(self._workspace_stack.currentWidget() is self.queue_box)
        file_mode.triggered.connect(
            lambda: self._select_overlay_workspace("workspace_tab_queue")
        )

        menu.addSeparator()
        open_input = menu.addAction(
            icon("folder_open", "#9FB0C3"), self._tr.tr("open_input")
        )
        open_input.triggered.connect(lambda: open_folder(self._paths.input))
        open_output = menu.addAction(
            icon("folder_open", "#9FB0C3"), self._tr.tr("open_output")
        )
        open_output.triggered.connect(lambda: open_folder(self._paths.output))

        menu.addSeparator()
        show_window = menu.addAction(
            icon("launch", "#E6EDF5"), self._tr.tr("tray_show")
        )
        show_window.triggered.connect(self._show_from_tray)
        settings = menu.addAction(
            icon("tune", "#9FB0C3"), self._tr.tr("settings_section_settings")
        )
        settings.triggered.connect(self._show_settings_from_overlay)
        quit_action = menu.addAction(
            icon("power", "#D85A30"), self._tr.tr("tray_quit")
        )
        quit_action.triggered.connect(self._quit_app)
        menu.popup(global_position)

    def _capture_dictation_target(self) -> None:
        from ..live.focus import get_foreground_window

        target = get_foreground_window()
        if target:
            self._dictation_target_hwnd = target

    def _show_dictation_history(
        self,
        *,
        limit: int = OVERLAY_HISTORY_LIMIT,
        title_key: str = "dictation_history_title",
    ) -> None:
        dialog = self._dictation_history_dialog
        if dialog is None:
            dialog = DictationHistoryDialog(
                self._tr,
                self._dictation_history.entries(),
                parent=self,
                limit=limit,
                title_key=title_key,
            )
            dialog.paste_requested.connect(self._paste_dictation)
            dialog.copy_requested.connect(self._copy_dictation)
            dialog.delete_requested.connect(self._delete_dictation)
            dialog.destroyed.connect(
                lambda _object=None: setattr(self, "_dictation_history_dialog", None)
            )
            self._dictation_history_dialog = dialog
        else:
            dialog.set_entries(
                self._dictation_history.entries(),
                limit=limit,
                title_key=title_key,
            )
        dialog.showNormal()
        dialog.raise_()
        dialog.activateWindow()

    def _paste_dictation(self, text: str) -> None:
        from ..live.focus import paste_text, set_clipboard_text

        value = str(text or "").strip()
        if not value:
            return
        target = self._dictation_target_hwnd
        if not target:
            set_clipboard_text(value)
            self.status_bar.showMessage(self._tr.tr("dictation_history_copied"), 3500)
            return
        method = self._live_cfg.paste_method
        QTimer.singleShot(0, lambda: paste_text(value, method, target))
        self.status_bar.showMessage(self._tr.tr("dictation_history_pasted"), 3500)

    def _copy_dictation(self, text: str) -> None:
        from ..live.focus import set_clipboard_text

        set_clipboard_text(str(text or ""))
        self.status_bar.showMessage(self._tr.tr("dictation_history_copied"), 3500)

    def _delete_dictation(self, entry_id: str) -> None:
        if not self._dictation_history.delete(entry_id):
            return
        if self._dictation_history_dialog is not None:
            self._dictation_history_dialog.set_entries(self._dictation_history.entries())
        self.status_bar.showMessage(self._tr.tr("dictation_history_deleted"), 3500)

    def _on_live_overlay_record(self) -> None:
        if self._workspace_stack.currentWidget() is self.queue_box:
            self._start_file_recorder()
            return
        if not self._live:
            return
        if self._live.state() == LiveState.DISABLED:
            self._live.set_armed(True)
        if self._live.state() in (LiveState.ARMED, LiveState.ERROR):
            self._live.begin_utterance()

    def _on_live_overlay_cancel(self) -> None:
        if self._file_recorder and self._file_recorder.is_recording:
            self._stop_file_recorder(cancel=True)
            return
        if self._live:
            self._live.cancel_utterance()
        if self._overlay:
            self._overlay.show_idle()

    def _on_live_overlay_stop(self) -> None:
        if self._file_recorder and self._file_recorder.is_recording:
            self._stop_file_recorder(cancel=False)
            return
        if self._live:
            self._live.stop_utterance()

    def _sync_overlay_record_action(self) -> None:
        if not self._overlay:
            return
        if self._file_recorder and (
            self._file_recorder.is_recording or self._file_recorder.is_finalizing
        ):
            return
        key = (
            "recorder_start"
            if self._workspace_stack.currentWidget() is self.queue_box
            else "overlay_record"
        )
        self._overlay.set_record_action(key)

    def _start_file_recorder(self) -> None:
        if self._file_recorder and (
            self._file_recorder.is_recording or self._file_recorder.is_finalizing
        ):
            return
        if self._queue_worker and self._queue_worker.isRunning():
            self.status_bar.showMessage(self._tr.tr("recorder_queue_busy"), 6000)
            return
        if self._live and self._live.state() in (LiveState.LISTENING, LiveState.TRANSCRIBING):
            self.status_bar.showMessage(self._tr.tr("recorder_live_busy"), 6000)
            return

        self._recorder_restore_live_armed = bool(
            self._live and self._live.state() != LiveState.DISABLED
        )
        if self._live:
            self._live.set_armed(False)
        recorder = FileRecorder(
            self._paths,
            sample_rate=48_000,
            preferred_device=self._live_input_device_preference,
        )
        try:
            destination = recorder.start()
        except Exception as exc:
            if self._live and self._recorder_restore_live_armed:
                self._live.set_armed(True)
            self._recorder_restore_live_armed = False
            message = self._tr.tr("recorder_failed").replace("{error}", str(exc))
            self._append_log(f"WARN: {message}")
            self.status_bar.showMessage(message, 8000)
            return

        self._file_recorder = recorder
        self._file_recorder_cancelled = False
        if self._overlay is None:
            self._overlay = self._create_live_overlay()
            self._recorder_created_overlay = True
        self._overlay.show_file_recording()
        self._recorder_timer.start()
        self._set_recorder_busy(True)
        message = self._tr.tr("recorder_started").replace("{path}", str(destination))
        self._append_log(message)
        device = recorder.selected_device
        self.status_bar.showMessage(
            f"{self._tr.tr('recorder_recording')} — {device}" if device else self._tr.tr("recorder_recording")
        )

    def _stop_file_recorder(self, *, cancel: bool) -> None:
        recorder = self._file_recorder
        if not recorder or not recorder.is_recording:
            return
        self._file_recorder_cancelled = bool(cancel)
        recorder.request_stop(cancel=cancel)
        if self._overlay:
            self._overlay.show_file_recording_finalizing(cancel=cancel)

    def _on_file_recorder_pause(self, paused: bool) -> None:
        recorder = self._file_recorder
        if not recorder or not recorder.set_paused(paused):
            return
        if self._overlay:
            self._overlay.set_file_recording_paused(paused)
        key = "recorder_paused" if paused else "recorder_resumed"
        message = self._tr.tr(key)
        self._append_log(message)
        self.status_bar.showMessage(message, 4000)

    def _poll_file_recorder(self) -> None:
        recorder = self._file_recorder
        if recorder is None:
            self._recorder_timer.stop()
            return
        if recorder.error and recorder.is_recording:
            self._stop_file_recorder(cancel=False)
        if recorder.is_done:
            self._finish_file_recorder(restore_live=not self._close_after_recorder)

    def _finish_file_recorder(self, *, restore_live: bool = True) -> None:
        recorder = self._file_recorder
        if recorder is None:
            return
        self._recorder_timer.stop()
        result = recorder.result
        error = recorder.error
        cancelled = self._file_recorder_cancelled
        self._file_recorder = None

        if result is not None and not cancelled:
            self._add_paths([str(result.path)])
            row = self.queue_model.rowCount() - 1
            if row >= 0:
                self.queue_view.selectRow(row)
            message = self._tr.tr("recorder_saved").replace("{path}", str(result.path))
            self._append_log(message)
            self.status_bar.showMessage(message, 8000)
        elif cancelled:
            message = self._tr.tr("recorder_cancelled")
            self._append_log(message)
            self.status_bar.showMessage(message, 5000)
        else:
            message = self._tr.tr("recorder_failed").replace("{error}", error or "unknown error")
            self._append_log(f"WARN: {message}")
            self.status_bar.showMessage(message, 8000)

        self._set_recorder_busy(False)
        if restore_live and self._live and self._recorder_restore_live_armed:
            self._live.set_armed(True)
        elif self._overlay:
            self._overlay.show_idle()
        self._recorder_restore_live_armed = False
        if self._recorder_created_overlay and not self._live_cfg.show_overlay and self._overlay:
            self._overlay.hide()
            self._overlay.deleteLater()
            self._overlay = None
        self._recorder_created_overlay = False
        self._sync_live_record_button()
        self._sync_live_audio_device_controls()
        self._sync_overlay_record_action()
        if self._close_after_recorder:
            self._close_after_recorder = False
            QTimer.singleShot(0, self.close)

    def _set_recorder_busy(self, busy: bool) -> None:
        self.btn_run.setEnabled(not busy)
        self.btn_retry.setEnabled(not busy)
        self.btn_clear.setEnabled(not busy)

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_toggle_live(self) -> None:
        if not self._live:
            return
        armed = self._live.toggle()
        msg_key = "live_armed_msg" if armed else "live_disarmed_msg"
        hotkey = self._live_cfg.hotkey.replace("ralt", "Right Alt").replace("+", " + ")
        msg = self._tr.tr(msg_key).replace("{hotkey}", hotkey)
        self.status_bar.showMessage(msg, 3500)
        if self._tray:
            # Re-sync the check even when arming didn't change state (e.g. failed),
            # since clicking a checkable action already flipped it.
            self._tray.set_live_state(self._live.state())
        self._sync_live_record_button(self._live.state())

    def _on_live_profile_requested(self, profile: str) -> None:
        if not self._live:
            return
        self._settings_save_timer.stop()
        self._settings = self._collect_settings()
        live = self._settings.setdefault("live", {})
        api = live.setdefault("api", {})
        local = live.setdefault("local", {})
        if profile == "xai":
            live["source"] = "api"
            live["engine"] = "xai_realtime"
            api["provider"] = "xai"
            api["mode"] = "realtime"
            api_model = fixed_live_api_model("xai", "realtime")
            api["model"] = api_model
            api.setdefault("models", {})["xai"] = api_model
            live["model"] = api_model
            profile_label = self._tr.tr("tray_live_xai")
        elif profile == "gigaam":
            live["source"] = "local"
            live["engine"] = "vulkan"
            local["engine"] = "gigaam"
            local["model"] = "gigaam-v3-e2e-ctc"
            local.setdefault("backend", "auto")
            local["idle_unload_seconds"] = 0
            live["model"] = local["model"]
            profile_label = self._tr.tr("tray_live_gigaam")
        else:
            return
        save_settings(self._paths, self._settings)
        self._apply_live_runtime_settings()
        self._rebuild_settings_panel()
        self._update_run_summary()
        self._live.set_armed(True)
        msg = self._tr.tr("live_quick_profile_msg").format(
            profile=profile_label,
            hotkey=self._live_cfg.hotkey.replace("ralt", "Right Alt").replace("+", " + "),
        )
        self.status_bar.showMessage(msg, 3500)
        if self._tray:
            self._tray.set_live_state(self._live.state())
        self._sync_live_record_button(self._live.state())

    def _on_live_state(self, state: LiveState) -> None:
        if self._tray:
            self._tray.set_live_state(state)
        self._sync_live_record_button(state)
        self._sync_live_audio_device_controls(state)
        if self._file_recorder and (
            self._file_recorder.is_recording or self._file_recorder.is_finalizing
        ):
            return
        if self._overlay:
            if state == LiveState.LISTENING:
                self._overlay.show_listening()
            elif state == LiveState.TRANSCRIBING:
                self._overlay.show_transcribing()
            elif state == LiveState.ARMED:
                self._overlay.dismiss_if_not_showing_final()
            elif state == LiveState.DISABLED:
                self._overlay.show_idle()

    def _on_live_notice(self, key: str) -> None:
        self._append_log(self._tr.tr(key))
        if key == "live_capture_failed":
            # Capture re-enumerates hardware on every activation. If this
            # particular failure is instead a damaged/missing Live runtime,
            # repair it through the same offline-first installer used at boot.
            status = check_live_dependencies()
            if not status.ready:
                self._start_live_deps_install(status)
        # Nothing heard / errored: dismiss the overlay rather than leave it hanging.
        recorder_busy = bool(
            self._file_recorder
            and (self._file_recorder.is_recording or self._file_recorder.is_finalizing)
        )
        if (
            self._overlay
            and not recorder_busy
            and key in ("live_empty", "live_error", "live_capture_failed")
        ):
            self._overlay.show_idle()

    def _on_live_committed(self, text: str) -> None:
        self._append_dictation(text)
        line = (text or "").strip()
        if line:
            self._dictation_lines.append(line)
            self._dictation_history.add(line)
        if self._overlay and (not self._live or self._live.state() != LiveState.LISTENING):
            self._overlay.show_final(text)

    def _on_live_partial(self, text: str) -> None:
        # Transient running transcript while the user is still speaking.
        if text:
            self.status_bar.showMessage(f"{self._tr.tr('tray_live')}: {text}", 4000)
        if self._overlay and self._live and self._live.state() == LiveState.LISTENING:
            self._overlay.set_partial(text)

    def _on_live_audio_level(self, level: float) -> None:
        if self._overlay and self._live and self._live.state() == LiveState.LISTENING:
            self._overlay.set_audio_level(level)

    def _on_live_transport_state(self, state: str) -> None:
        if self._overlay and self._live and self._live.state() == LiveState.LISTENING:
            self._overlay.set_transport_state(state)

    # --- tray export (Notion / Obsidian) ------------------------------------
    def _on_export_requested(self, source: str, destination: str) -> None:
        """Export the last dictation ('live') or a chosen transcript file ('file')
        to one connector. The tray action forces that connector even if it isn't
        enabled in settings, but it still needs to be configured (vault / key)."""
        from ..connectors import export_doc

        dialog_parent = self if self.isVisible() else None
        if source == "live":
            doc = self._build_live_doc()
            if doc is None:
                QMessageBox.information(
                    dialog_parent, self._tr.tr("app_title"), self._tr.tr("export_nothing")
                )
                return
            src_path = None
        else:
            src_path = self._pick_transcript_json(dialog_parent)
            if src_path is None:
                return
            from ..writers.json_writer import read_json

            try:
                doc = read_json(src_path)
            except Exception as exc:
                QMessageBox.warning(
                    dialog_parent,
                    self._tr.tr("app_title"),
                    self._tr.tr("export_failed").replace("{error}", str(exc)),
                )
                return

        # Refresh settings so a vault path / Notion parent set in the panel is current.
        self._save_settings_now(force=True)
        results = export_doc(
            self._paths, self._settings, doc, source_path=src_path,
            only={destination}, log=self._append_log,
        )
        self._report_export(results, dialog_parent)

    def _build_live_doc(self):
        if not self._dictation_lines:
            return None
        from ..core.logging_utils import timestamp
        from ..core.models import Metadata, Segment, SourceMeta, Transcription, TranscriptDoc

        body = "\n\n".join(self._dictation_lines)
        when = datetime.now()
        title = f"{self._tr.tr('tray_live')} {when.strftime('%Y-%m-%d %H:%M')}"
        slug = f"live-dictation-{when.strftime('%Y%m%d-%H%M%S')}"
        segments = [Segment(index=i, start=0.0, end=0.0, text=line)
                    for i, line in enumerate(self._dictation_lines)]
        return TranscriptDoc(
            source=SourceMeta(
                path="", filename=f"{slug}.txt", media_type="audio",
                duration_seconds=0.0, created_at=timestamp(),
            ),
            transcription=Transcription(language=self._settings.get("language"), segments=segments),
            metadata=Metadata(title=title, slug=slug),
            # Live has no real timestamps; the clean body is the dictated prose.
            cleaned_markdown=body,
        )

    def _pick_transcript_json(self, dialog_parent) -> Optional[Path]:
        start_dir = str(self._paths.output)
        selected, _ = QFileDialog.getOpenFileName(
            dialog_parent,
            self._tr.tr("export_pick_json"),
            start_dir,
            self._tr.tr("transcript_files"),
        )
        return Path(selected) if selected else None

    def _report_export(self, results, dialog_parent) -> None:
        if not results:
            return
        result = results[0]
        if result.ok:
            self.status_bar.showMessage(
                self._tr.tr("export_done").replace("{target}", result.target), 8000
            )
        else:
            QMessageBox.warning(
                dialog_parent,
                self._tr.tr("app_title"),
                self._tr.tr("export_failed").replace("{error}", result.message),
            )

    def _quit_app(self) -> None:
        self._really_quit = True
        self.close()

    # --- misc ----------------------------------------------------------------
    def _save_log_markdown_with_picker(self) -> None:
        dialog_parent = self if self.isVisible() else None
        if not self.log_view.toPlainText().strip():
            QMessageBox.information(
                dialog_parent,
                self._tr.tr("app_title"),
                self._tr.tr("save_log_empty"),
            )
            return

        default_path = self._paths.output / "audion-log.md"
        selected, _ = QFileDialog.getSaveFileName(
            dialog_parent,
            self._tr.tr("save_log_dialog"),
            str(default_path),
            self._tr.tr("markdown_files"),
        )
        if not selected:
            return

        try:
            path = self._write_log_markdown(Path(selected))
        except Exception as exc:
            QMessageBox.warning(
                dialog_parent,
                self._tr.tr("app_title"),
                self._tr.tr("save_log_failed").replace("{error}", str(exc)),
            )
            return
        self.status_bar.showMessage(
            self._tr.tr("save_log_done").replace("{path}", str(path)),
            6000,
        )

    def _write_log_markdown(self, path: Path) -> Path:
        if path.suffix.lower() != ".md":
            path = path.with_suffix(".md")
        return write_text_atomic(path, self._log_markdown_body())

    def _log_markdown_body(self) -> str:
        text = self.log_view.toPlainText().rstrip()
        fence = "```"
        while fence in text:
            fence += "`"
        saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        title = f"{self.windowTitle()} - {self._tr.tr('logs')}"
        return (
            f"# {title}\n\n"
            f"{self._tr.tr('saved_at')}: {saved_at}\n\n"
            f"{fence}text\n{text}\n{fence}\n"
        )

    def _append_log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def _append_install_log(self, message: str) -> None:
        self._workspace_tabs.set_current_key("workspace_tab_live_log")
        self._workspace_stack.setCurrentWidget(self.log_box)
        prefix = self._tr.tr("install_log_prefix")
        self._append_log(f"{prefix} {message}" if message else prefix)

    def _append_dictation(self, text: str) -> None:
        # Separate each dictated entry with a blank line for readability.
        if not self.log_view.document().isEmpty():
            self.log_view.appendPlainText("")
        self.log_view.appendPlainText(text)

    def _toggle_log_expanded(self) -> None:
        self._log_expanded = not self._log_expanded
        self._workspace_tabs.set_current_key("workspace_tab_live_log")
        self._workspace_stack.setCurrentWidget(self.log_box)
        self._workspace_tabs.setVisible(not self._log_expanded)
        if hasattr(self, "_right_panel"):
            self._right_panel.setVisible(not self._log_expanded)
        self.log_box.setMaximumHeight(16777215)
        self.log_box.updateGeometry()
        self._apply_theme_icons()
        self.btn_log_expand.setToolTip(self._tr.tr("collapse_log" if self._log_expanded else "expand_log"))

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        if (
            event.type() == QEvent.WindowStateChange
            and self.isMinimized()
            and getattr(self, "_tray", None)
            and self._live_cfg.minimize_to_tray
        ):
            self._flush_autosave()
            QTimer.singleShot(0, self.hide)

    def closeEvent(self, event):  # noqa: N802
        recorder = self._file_recorder
        if recorder is not None:
            if recorder.is_recording:
                self._file_recorder_cancelled = False
                recorder.request_stop(cancel=False)
            if not recorder.is_done:
                self._close_after_recorder = True
                if self._overlay:
                    self._overlay.show_file_recording_finalizing()
                self._recorder_timer.start()
                self.status_bar.showMessage(self._tr.tr("recorder_finalizing"))
                event.ignore()
                return
            self._finish_file_recorder(restore_live=False)
        if self._audio_export_worker and self._audio_export_worker.isRunning():
            self._close_after_audio_export = True
            self.status_bar.showMessage(self._tr.tr("recorder_exporting"))
            event.ignore()
            return
        if self._live_deps_worker and self._live_deps_worker.isRunning():
            self._live_deps_worker.cancel()
            self._live_deps_worker.requestInterruption()
            self._live_deps_worker.wait(5000)
        if self._queue_worker and self._queue_worker.isRunning():
            self._queue_worker.cancel()
            self._queue_worker.wait(3000)
        if self._model_worker:
            if self._model_worker.isRunning():
                self._model_worker.wait(3000)
            QApplication.processEvents()
        if hasattr(self, "settings_panel"):
            self.settings_panel.stop_background_tasks()
        if self._live:
            self._live.shutdown()
        if self._overlay:
            self._overlay.hide()
        if self._tray:
            self._remove_tray_icon()
        # Persist current settings and queue on exit (best-effort).
        self._flush_autosave()
        super().closeEvent(event)
        if self._really_quit and event.isAccepted():
            # Tray applications must not rely on Qt's "last window" heuristic:
            # the tray object can keep the event loop alive on Windows.  Run
            # this only after the same clean close path used by the title bar.
            app = QApplication.instance()
            if app is not None:
                QTimer.singleShot(0, app.quit)
