"""Headless smoke test for the PySide6 GUI.

Skips automatically when PySide6 isn't installed (the base/API env stays light;
the GUI lands only when requirements_full.in is installed). Uses the offscreen
Qt platform so it runs in CI / over SSH without a display.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

# Dev convenience: if PySide6 was installed into a project-local .devlibs (so it
# stays out of a sibling project's runtime), make it importable here too.
_DEVLIBS = Path(__file__).resolve().parents[1] / ".devlibs"
if _DEVLIBS.exists():
    import sys

    sys.path.insert(0, str(_DEVLIBS))

pytest.importorskip("PySide6", reason="GUI deps not installed (requirements_full.in)")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QAbstractButton,
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStyle,
    QTabWidget,
    QTextEdit,
    QWidget,
)

from system_core.core.config import load_yaml_or_json  # noqa: E402
from system_core.core.editions import MODE_CUDA, MODE_VULKAN, current_edition  # noqa: E402
from system_core.core.paths import get_project_paths  # noqa: E402
from system_core.live import LiveState  # noqa: E402
from system_core.live.history import (  # noqa: E402
    DictationEntry,
    HISTORY_CACHE_LIMIT,
    OVERLAY_HISTORY_LIMIT,
)
from system_core.live.mic_check import InputDeviceCandidate  # noqa: E402
from system_core.ui import theme as theme_mod  # noqa: E402
from system_core.ui import main_window as main_window_mod  # noqa: E402
from system_core.ui.main_window import CenteredComboBox, MainWindow  # noqa: E402
from system_core.ui.dictation_history import DictationHistoryDialog  # noqa: E402
from system_core.ui.tooltips import TOOLTIP_WAKEUP_DELAY_MS, apply_tooltip_delay  # noqa: E402
from system_core.ui.widgets import (  # noqa: E402
    CurrentPageStackedWidget,
    ElidedComboBox,
    ElidedLabel,
    InlineDoubleSpinBox,
    InlineSpinBox,
    RoundedPlainTextEdit,
    UnderlineTabBar,
)


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    apply_tooltip_delay(instance)
    yield instance


@pytest.fixture
def window(app):
    paths = get_project_paths()
    state_files = [
        paths.config / "app_settings.yaml",
        paths.config / "providers.yaml",
        paths.config / "dictation_history.json",
        paths.workspace / "ui_state.json",
    ]
    snapshots = {
        path: path.read_text(encoding="utf-8") if path.exists() else None
        for path in state_files
    }
    try:
        (paths.workspace / "ui_state.json").unlink()
    except FileNotFoundError:
        pass
    win = MainWindow(paths)
    try:
        yield win
    finally:
        win.close()
        for path, text in snapshots.items():
            if text is None:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")


def _combo_values(combo) -> list[str]:
    return [str(combo.itemData(i) or combo.itemText(i)) for i in range(combo.count())]


def test_close_exits_even_with_tray_enabled(window):
    window._tray = SimpleNamespace(
        hide=lambda: None,
        deleteLater=lambda: None,
        set_live_state=lambda *_args: None,
    )
    window._live_cfg = replace(window._live_cfg, minimize_to_tray=True)
    event = QCloseEvent()

    window.closeEvent(event)

    assert event.isAccepted()


def test_tray_exit_uses_clean_close_then_explicitly_ends_event_loop(window, monkeypatch):
    closed: list[bool] = []
    monkeypatch.setattr(window, "close", lambda: closed.append(True))

    window._quit_app()

    assert window._really_quit is True
    assert closed == [True]

    callbacks = []
    monkeypatch.undo()
    monkeypatch.setattr(main_window_mod.QTimer, "singleShot", lambda delay, callback: callbacks.append((delay, callback)))
    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted()
    quit_callbacks = [item for item in callbacks if item[1].__name__ == "quit"]
    assert len(quit_callbacks) == 1
    assert quit_callbacks[0][0] == 0
    window._really_quit = False


def test_minimize_hides_to_tray(window, app, monkeypatch):
    hidden = []
    window._tray = SimpleNamespace(
        hide=lambda: None,
        deleteLater=lambda: None,
        set_live_state=lambda *_args: None,
    )
    window._live_cfg = replace(window._live_cfg, minimize_to_tray=True)
    monkeypatch.setattr(window, "isMinimized", lambda: True)
    monkeypatch.setattr(window, "hide", lambda: hidden.append(True))

    window.changeEvent(QEvent(QEvent.WindowStateChange))
    app.processEvents()

    assert hidden == [True]


_TOOLTIP_WIDGET_TYPES = (
    QAbstractButton,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QAbstractSpinBox,
    QSlider,
    QProgressBar,
    QAbstractItemView,
)


def _control_name(widget: QWidget) -> tuple[str, str, str]:
    text = ""
    if isinstance(widget, QAbstractButton):
        text = widget.text()
    elif isinstance(widget, QComboBox):
        text = widget.currentText()
    elif isinstance(widget, QLineEdit):
        text = widget.placeholderText()
    return (widget.__class__.__name__, widget.objectName() or widget.__class__.__name__, text)


def _missing_tooltip_controls(root: QWidget) -> list[tuple[str, str, str]]:
    missing = []
    for widget in root.findChildren(QWidget):
        if not isinstance(widget, _TOOLTIP_WIDGET_TYPES):
            continue
        if widget.objectName().startswith("qt_"):
            continue
        if not (widget.toolTip() or "").strip():
            missing.append(_control_name(widget))
    return missing


def _card_is_in_page(panel, box_key: str, page_key: str) -> bool:
    return panel._section_pages[page_key].isAncestorOf(panel._boxes[box_key])


def test_window_builds(window):
    assert "Audion" in window.windowTitle()


def test_topbar_controls_are_compact(window):
    assert not hasattr(window, "btn_modules")
    assert window.cmb_lang.maximumHeight() == 34
    assert window.cmb_theme.maximumHeight() == 34
    assert window.cmb_lang.maximumWidth() == 58
    assert window.cmb_theme.maximumWidth() == 136
    assert isinstance(window.cmb_lang, CenteredComboBox)
    assert isinstance(window.cmb_theme, CenteredComboBox)
    assert not window.cmb_lang.isEditable()
    assert not window.cmb_theme.isEditable()


def test_interactive_controls_have_tooltips(window):
    assert _missing_tooltip_controls(window) == []
    for tabs in window.findChildren(QTabWidget):
        for index in range(tabs.count()):
            assert tabs.tabToolTip(index)


def test_native_elision_is_limited_to_live_audio_device(window, app):
    assert isinstance(window.lbl_live_audio_device, ElidedLabel)
    assert isinstance(window.cmb_live_audio_device, ElidedComboBox)
    assert all(
        not isinstance(combo, ElidedComboBox)
        for combo in window.findChildren(QComboBox)
        if combo is not window.cmb_live_audio_device
    )
    assert all(
        not isinstance(label, ElidedLabel)
        for label in window.findChildren(QLabel)
        if label is not window.lbl_live_audio_device
    )
    full = "Очень длинное имя устройства для проверки многоточия"
    label = ElidedLabel(full)
    label.resize(90, 30)
    label.show()
    app.processEvents()
    assert label.text() == full
    assert QLabel.text(label).endswith("…")
    assert label.toolTip() == full

    combo = ElidedComboBox()
    combo.addItem(full)
    combo.resize(110, 34)
    combo.show()
    app.processEvents()
    assert not combo.grab().isNull()


def test_settings_spinboxes_use_large_inline_chevrons(window):
    panel = window.settings_panel
    assert isinstance(panel.spin_live_overlay_scale, InlineSpinBox)
    assert isinstance(panel.spin_min, InlineDoubleSpinBox)
    for spin in (panel.spin_live_overlay_scale, panel.spin_min):
        down = spin.findChild(QAbstractButton, "SpinStepDown")
        up = spin.findChild(QAbstractButton, "SpinStepUp")
        assert down is not None and up is not None
        assert down.width() >= 28 and up.width() >= 28


def test_tooltip_wakeup_delay_is_1500_ms(app):
    assert app.style().styleHint(QStyle.SH_ToolTip_WakeUpDelay) == TOOLTIP_WAKEUP_DELAY_MS
    assert TOOLTIP_WAKEUP_DELAY_MS == 1500


def test_settings_panel_uses_section_tabs(window):
    panel = window.settings_panel
    assert isinstance(panel._section_tabs, UnderlineTabBar)
    assert isinstance(panel._section_stack, CurrentPageStackedWidget)
    if current_edition(get_project_paths(), window._settings) == "live":
        expected = [
            "settings_section_live",
            "settings_section_file_ops",
            "settings_section_settings",
            "settings_section_modules",
        ]
    else:
        expected = [
            "settings_section_live",
            "settings_section_file_ops",
            "settings_section_settings",
            "settings_section_modules",
        ]
    assert list(panel._section_buttons) == expected
    for button in panel._section_buttons.values():
        assert button.property("variant") == "section-tab"
        assert button.sizePolicy().horizontalPolicy() == QSizePolicy.Expanding
    assert panel._section_buttons["settings_section_file_ops"].text() == "Файлы"
    for key in expected:
        assert panel._section_pages[key].layout().count() == 1

    panel._section_buttons["settings_section_settings"].click()
    assert panel._section_stack.currentWidget() is panel._section_pages["settings_section_settings"]
    panel._section_buttons["settings_section_file_ops"].click()
    assert panel._section_stack.currentWidget() is panel._section_pages["settings_section_file_ops"]
    assert "live_settings" in panel._boxes
    assert "live_openai" in panel._boxes
    assert "live_vulkan" in panel._boxes
    assert "file_vulkan" in panel._boxes
    assert "live_overlay" in panel._boxes
    assert "live_cleanup" in panel._boxes
    assert "app_behavior" in panel._boxes
    assert "generation_settings" in panel._boxes
    assert "export_formats" in panel._boxes
    assert _card_is_in_page(panel, "live_overlay", "settings_section_live")
    for box_key in ("queue", "generation_settings", "cleanup", "subtitles", "export_formats"):
        assert _card_is_in_page(panel, box_key, "settings_section_file_ops")
    for box_key in ("app_behavior", "connectors", "reset_app"):
        assert _card_is_in_page(panel, box_key, "settings_section_settings")
    assert not _card_is_in_page(panel, "reset_app", "settings_section_modules")
    assert hasattr(panel, "modules_panel")
    assert panel.modules_panel._rows
    assert panel.modules_panel.btn_mic_check.text() == window._tr.tr("mic_check_button")
    action_buttons = [
        panel.modules_panel.btn_mic_check,
        panel.modules_panel._openai_key_btn,
        panel.modules_panel._xai_key_btn,
        panel.modules_panel._elevenlabs_key_btn,
        panel.modules_panel._notion_key_btn,
        *(button for _status, button in panel.modules_panel._rows.values()),
    ]
    assert {button.width() for button in action_buttons} == {208}
    assert panel.modules_panel.log_view is None
    assert panel.btn_reset_app.text() == window._tr.tr("reset_app_button")
    assert panel.chk_show_tray.text() == window._tr.tr("show_tray")
    assert panel.lbl_local_hw_hint.objectName() == "LocalHardwareBadge"
    accurate_idx = panel.cmb_stt.findData("gpt-4o-transcribe")
    assert accurate_idx >= 0
    assert "gpt-4o-transcribe" in panel.cmb_stt.itemData(accurate_idx, Qt.ToolTipRole)
    assert set(panel._live_source_buttons) == {"api", "local"}
    assert set(panel._live_api_provider_buttons) == {"openai", "xai", "elevenlabs"}
    assert set(panel._live_api_mode_buttons) == {"batch", "realtime"}
    assert set(panel._live_local_engine_buttons) == {"gigaam", "whispercpp"}
    assert set(panel._file_local_engine_buttons) == {"gigaam", "whispercpp"}
    hw_text = panel.lbl_local_hw_hint.text()
    assert (
        hw_text == window._tr.tr("local_hw_initializing")
        or hw_text.startswith("GPU initializing...")
        or "GPU:" in hw_text
        or hw_text.startswith("GPU detection failed:")
    )
    if current_edition(get_project_paths(), window._settings) == "studio":
        assert "cuda_settings" in panel._boxes
    else:
        assert "cuda_settings" not in panel._boxes

    first = panel._section_tabs.keys()[0]
    panel._section_tabs.move_tab("settings_section_settings", first)
    assert panel._section_tabs.keys()[0] == "settings_section_settings"


def test_settings_stack_sizes_to_visible_section(window):
    panel = window.settings_panel
    for key in panel._section_tabs.keys():
        panel._section_buttons[key].click()
        assert panel._section_stack.currentWidget() is panel._section_pages[key]
        assert panel._section_stack.sizeHint().height() == panel._section_pages[key].sizeHint().height()
        assert panel._section_stack.minimumSizeHint().height() == panel._section_pages[key].minimumSizeHint().height()
        assert panel.sizeHint().height() == panel.layout().sizeHint().height()


def test_settings_scroll_does_not_keep_hidden_section_height(window, app):
    window.resize(2048, 1200)
    window.show()
    app.processEvents()
    scroll = window._right_panel
    panel = window.settings_panel
    for key in ("settings_section_live", "settings_section_settings"):
        panel._section_buttons[key].click()
        app.processEvents()
        app.processEvents()
        assert panel.sizeHint().height() <= scroll.viewport().height()
        assert scroll.verticalScrollBar().maximum() == 0


def test_settings_segment_toggles_fill_their_rows(window):
    panel = window.settings_panel
    buttons = [
        *panel._compute_buttons.values(),
        *panel._live_source_buttons.values(),
        *panel._live_api_provider_buttons.values(),
        *panel._live_api_mode_buttons.values(),
        *panel._live_local_engine_buttons.values(),
        *panel._file_local_engine_buttons.values(),
        *panel._cuda_profile_buttons.values(),
        panel.btn_plain,
        panel.btn_diar,
    ]
    for button in buttons:
        assert button.property("variant") == "segment"
        assert button.sizePolicy().horizontalPolicy() == QSizePolicy.Expanding


def test_workspace_navigation_and_round_header_controls_have_safe_geometry(window):
    assert window._default_workspace_order() == ["workspace_tab_live_log", "workspace_tab_queue"]
    workspace_buttons = list(window._workspace_tabs.buttons().values())
    assert all(
        button.sizePolicy().horizontalPolicy() == QSizePolicy.Expanding
        for button in workspace_buttons
    )
    assert all(window._workspace_tabs.layout().stretch(i) == 1 for i in range(2))
    margins = window.log_box.layout().contentsMargins()
    assert min(margins.left(), margins.top(), margins.right(), margins.bottom()) >= 10
    for button in (
        window.btn_live_record,
        window.btn_log_clear,
        window.btn_log_save,
        window.btn_log_expand,
    ):
        assert button.property("variant") == "header-icon"
        assert button.minimumWidth() == button.minimumHeight()
        assert button.maximumWidth() == button.maximumHeight()
        assert button.minimumWidth() >= 34
    head_margins = window.log_box._head.contentsMargins()
    assert min(
        head_margins.left(),
        head_margins.top(),
        head_margins.right(),
        head_margins.bottom(),
    ) >= 2
    for button in window._workspace_tabs.buttons().values():
        text_width = button.fontMetrics().horizontalAdvance(button.text())
        assert button.sizeHint().width() >= text_width + 20


def test_settings_panel_hides_inactive_backend_cards(window):
    panel = window.settings_panel
    panel._compute_buttons[MODE_VULKAN].click()
    assert panel._file_openai_card.isHidden()
    assert not panel._file_vulkan_card.isHidden()
    if current_edition(get_project_paths(), window._settings) == "studio":
        assert panel._file_cuda_card.isHidden()
        panel._compute_buttons[MODE_CUDA].click()
        assert panel._file_openai_card.isHidden()
        assert panel._file_vulkan_card.isHidden()
        assert not panel._file_cuda_card.isHidden()
    panel._compute_buttons["api"].click()
    assert not panel._file_openai_card.isHidden()
    assert panel._file_vulkan_card.isHidden()
    if panel._file_cuda_card is not None:
        assert panel._file_cuda_card.isHidden()


def test_live_engine_dims_inactive_backend_cards(window):
    panel = window.settings_panel
    panel._live_source_buttons["local"].click()
    assert not panel._live_openai_card.body().isEnabled()
    assert panel._live_vulkan_card.body().isEnabled()

    panel._live_source_buttons["api"].click()
    assert panel._live_openai_card.body().isEnabled()
    assert not panel._live_vulkan_card.body().isEnabled()


def test_tray_quick_live_profiles_update_settings(window, monkeypatch):
    class FakeLive:
        def __init__(self):
            self._state = LiveState.DISABLED
            self.configs = []

        def update_config(self, config):
            self.configs.append(config)

        def set_armed(self, on):
            self._state = LiveState.ARMED if on else LiveState.DISABLED

        def state(self):
            return self._state

        def shutdown(self):
            self._state = LiveState.DISABLED

    fake_live = FakeLive()
    window._live = fake_live
    window._tray = None
    monkeypatch.setattr(window, "_ensure_tray_icon", lambda: None)

    window._on_live_profile_requested("xai")

    assert window._settings["live"]["source"] == "api"
    assert window._settings["live"]["api"]["provider"] == "xai"
    assert window._settings["live"]["api"]["model"] == "grok-transcribe"
    assert fake_live.configs[-1].engine == "xai_realtime"
    assert fake_live.state() == LiveState.ARMED

    window._on_live_profile_requested("gigaam")

    assert window._settings["live"]["source"] == "local"
    assert window._settings["live"]["local"]["engine"] == "gigaam"
    assert window._settings["live"]["local"]["model"] == "gigaam-v3-e2e-ctc"
    assert fake_live.configs[-1].engine == "vulkan"
    assert fake_live.configs[-1].local_engine == "gigaam"
    assert fake_live.state() == LiveState.ARMED


def test_combo_wheel_is_ignored(window):
    panel = window.settings_panel
    assert panel.eventFilter(panel.cmb_transcription_language, QEvent(QEvent.Wheel)) is True


def test_queue_source_and_operation_rows_are_compact(window):
    assert window.lbl_add_sources.text() == window._tr.tr("add_sources")
    assert window.btn_add_files.property("variant") == "source-picker"
    assert window.btn_add_folder.property("variant") == "source-picker"
    assert window.btn_add_files.toolTip() == window._formats_text("add_files_tip")
    assert window.btn_add_folder.toolTip() == window._formats_text("add_folder_tip")
    assert window.btn_export.text() == window._tr.tr("tray_export")
    assert window.btn_open_in.text() == window._tr.tr("input_folder_button")
    assert window.btn_open_out.text() == window._tr.tr("output_folder_button")
    for button in (window.btn_add_files, window.btn_add_folder):
        assert button.minimumWidth() <= 40
        assert button.maximumWidth() <= 40
        assert button.maximumHeight() <= 34
    for button in (window.btn_export,):
        assert button.property("variant") == "toolbar-text"
        assert button.minimumWidth() == 152
        assert button.maximumWidth() > 10000
        assert button.maximumHeight() <= 34
        assert button.sizePolicy().horizontalPolicy() == QSizePolicy.Expanding
    operation_buttons = (
        window.btn_export,
        window.btn_run,
        window.btn_stop,
        window.btn_retry,
        window.btn_clear,
    )
    assert window.queue_controls_layout.count() == len(operation_buttons)
    assert [
        window.queue_controls_layout.itemAt(index).widget()
        for index in range(window.queue_controls_layout.count())
    ] == list(operation_buttons)
    assert window.queue_controls_layout.stretch(0) == 1
    for button in (window.btn_open_in, window.btn_open_out):
        assert button.property("variant") == "queue-folder"
        assert button.minimumWidth() == 100
        assert button.maximumWidth() > 10000
        assert button.sizePolicy().horizontalPolicy() == QSizePolicy.Expanding


def test_short_checkbox_groups_use_single_borderless_strips(window):
    panel = window.settings_panel
    groups = (
        (panel.chk_title, panel.chk_summary, panel.chk_tags, panel.chk_actions),
        (panel.chk_json, panel.chk_md, panel.chk_txt, panel.chk_srt, panel.chk_vtt),
        (panel.chk_skip, panel.chk_force, panel.chk_recursive, panel.chk_next_to),
    )
    for checks in groups:
        parents = {check.parentWidget() for check in checks}
        assert len(parents) == 1
        strip = parents.pop()
        assert strip.objectName() == "OptionStrip"
        assert strip.layout().count() == len(checks)


def test_default_splitter_leaves_room_for_single_line_subtitle_controls(window, app):
    window.show()
    app.processEvents()
    left_width, settings_width = window._main_splitter.sizes()
    assert settings_width >= 1050
    assert settings_width > left_width * 2
    for key in ("max_chars_per_line", "max_lines", "min_duration", "max_duration"):
        assert not window.settings_panel._labels[key].wordWrap()


def test_left_workspace_uses_underlined_tabs(window):
    assert isinstance(window._workspace_tabs, UnderlineTabBar)
    expected = ["workspace_tab_live_log", "workspace_tab_queue"]
    assert window._workspace_stack.currentWidget() is window.log_box
    assert window._workspace_tabs.keys() == expected
    for button in window._workspace_tabs.buttons().values():
        assert button.sizePolicy().horizontalPolicy() == QSizePolicy.Expanding
    assert [window._workspace_tabs.buttons()[key].text() for key in window._workspace_keys] == [
        window._tr.tr(key) for key in expected
    ]

    window._workspace_tabs.buttons()["workspace_tab_live_log"].click()
    assert window._workspace_stack.currentWidget() is window.log_box
    window._workspace_tabs.buttons()["workspace_tab_queue"].click()
    assert window._workspace_stack.currentWidget() is window.queue_box

    before = window._workspace_tabs.keys()
    window._workspace_tabs.move_tab(before[1], before[0])
    assert window._workspace_tabs.keys()[0] == before[1]


def test_export_menus_expose_tray_actions(window):
    assert window.btn_export.text() == window._tr.tr("tray_export")
    assert window.btn_log_export.text() == window._tr.tr("tray_export")
    assert window.btn_log_export.property("variant") == "header-text"
    assert window.btn_log_export.minimumWidth() == 152
    assert window.btn_log_export.maximumWidth() == 152
    assert window.btn_export.menu() is not None
    assert window.btn_log_export.menu() is not None

    expected = [
        ("export_live_notion", "live:notion"),
        ("export_live_obsidian", "live:obsidian"),
        ("export_transcript_notion", "file:notion"),
        ("export_transcript_obsidian", "file:obsidian"),
    ]
    queue_actions = [a for a in window.btn_export.menu().actions() if not a.isSeparator()]
    log_actions = [a for a in window.btn_log_export.menu().actions() if not a.isSeparator()]
    expected_connectors = [(window._tr.tr(key), data) for key, data in expected]
    expected_recordings = [
        (window._tr.tr("recorder_export_m4a"), None),
        (window._tr.tr("recorder_export_wav"), None),
    ]
    assert [(a.text(), a.data()) for a in queue_actions] == expected_connectors + expected_recordings
    assert [(a.text(), a.data()) for a in log_actions] == expected_connectors + expected_recordings


def test_queue_supported_formats_hint(window):
    tooltip = window.lbl_formats.toolTip()
    assert window.lbl_formats.text() == window._tr.tr("supported_formats_short")
    assert "MP3" in tooltip
    assert "M2TS" in tooltip
    assert "*.mp3" in window._media_file_filter()
    assert "*.m2ts" in window._media_file_filter()


def test_add_paths_warns_about_explicit_unsupported_files(window, tmp_path):
    bad = tmp_path / "strange.xyz"
    bad.write_text("not media", encoding="utf-8")

    window._add_paths([str(bad)])

    assert window.queue_model.rowCount() == 0
    assert window._tr.tr("unsupported_files_skipped").split("{count}")[0] in window.log_view.toPlainText()


def test_live_record_button_reflects_live_state(window):
    class FakeLive:
        def state(self):
            return LiveState.ARMED

        def shutdown(self):
            pass

    window._live = FakeLive()
    window._sync_live_record_button()
    assert window.btn_live_record.isEnabled()
    assert window.btn_live_record.isChecked()

    window._sync_live_record_button(LiveState.DISABLED)
    assert window.btn_live_record.isEnabled()
    assert not window.btn_live_record.isChecked()


def test_live_audio_badge_and_selector_are_session_only(window):
    if window._live:
        window._live.shutdown()

    class FakeLive:
        def __init__(self):
            self.preferences = []

        def state(self):
            return LiveState.ARMED

        def set_input_device_preference(self, preference):
            self.preferences.append(preference)
            return True

        def shutdown(self):
            pass

    fake = FakeLive()
    window._live = fake
    window._live_audio_candidates = (
        InputDeviceCandidate(0, "Internal Microphone", "Windows WASAPI"),
        InputDeviceCandidate(1, "USB Headset Microphone", "Windows WASAPI"),
    )
    window._populate_live_audio_devices()

    assert window.log_box.layout().itemAt(0).widget() is window.live_audio_bar
    audio_row = window.live_audio_bar.layout()
    assert audio_row.itemAt(0).widget() is window.lbl_live_audio_device
    assert audio_row.itemAt(audio_row.count() - 1).widget() is window.cmb_live_audio_device
    assert audio_row.stretch(0) == audio_row.stretch(1) == 1

    window.cmb_live_audio_device.setCurrentIndex(2)
    window._on_live_input_device_changed("Windows WASAPI / USB Headset Microphone")

    assert fake.preferences[-1] == ("Windows WASAPI", "USB Headset Microphone")
    assert "USB Headset" in window.lbl_live_audio_device.toolTip()
    assert "input_device" not in window._collect_settings()["live"]


def test_missing_live_dependencies_trigger_automatic_install(window, monkeypatch):
    if window._live:
        window._live.shutdown()
    window._live = None
    if window._overlay:
        window._overlay.hide()
    window._overlay = None
    requested = []
    status = SimpleNamespace(
        ready=False,
        missing=("sounddevice", "websockets"),
        detail="sounddevice, websockets",
    )
    monkeypatch.setattr(main_window_mod, "check_live_dependencies", lambda: status)
    monkeypatch.setattr(window, "_start_live_deps_install", requested.append)

    window._setup_tray()

    assert requested == [status]
    assert window._live is None


def test_mic_activation_rechecks_and_repairs_live_dependencies(window, monkeypatch):
    requested = []
    status = SimpleNamespace(
        ready=False,
        missing=("sounddevice",),
        detail="sounddevice",
    )
    monkeypatch.setattr(main_window_mod, "check_live_dependencies", lambda: status)
    monkeypatch.setattr(window, "_start_live_deps_install", requested.append)

    window._on_live_notice("live_capture_failed")

    assert requested == [status]


def test_all_themes_render(window):
    paths = get_project_paths()
    for info in theme_mod.list_themes(paths):
        qss = theme_mod.build_stylesheet(info)
        assert len(qss) > 200
        assert "QComboBox::down-arrow" in qss
        assert "QProgressBar#InstallProgress" in qss
        assert "QToolTip" in qss
        assert "rgb(23, 33, 43)" in qss
        window.apply_theme(info.name)  # must not raise
    assert (paths.system_core / "ui" / "assets" / "combo_down.svg").exists()


def test_shell_boundaries_and_scroll_chrome(window):
    qss = QApplication.instance().styleSheet()
    assert window._main_splitter.objectName() == "MainSplitter"
    assert window._right_panel.objectName() == "SettingsScroll"
    assert window.status_bar.isSizeGripEnabled() is False
    assert "QFrame#ShellRule" in qss
    assert "QScrollArea#SettingsScroll QScrollBar:vertical" in qss
    assert "QSplitter#MainSplitter::handle:horizontal" in qss
    assert 'QPushButton[variant="segment"]' in qss
    assert "margin: 2px;" in qss
    left = window.findChild(QWidget, "LeftWorkspace")
    assert left is not None
    assert left.layout().contentsMargins().left() >= 6


def test_language_toggle(window):
    # Switch to every available language and back; retranslation must not raise.
    for i in range(window.cmb_lang.count()):
        window.cmb_lang.setCurrentIndex(i)
    window.cmb_lang.setCurrentIndex(0)


def test_app_language_is_separate_from_transcription_language(window):
    trans_idx = window.settings_panel.cmb_transcription_language.findData("auto")
    window.settings_panel.cmb_transcription_language.setCurrentIndex(trans_idx)
    app_idx = window.cmb_lang.findData("en")
    window.cmb_lang.setCurrentIndex(app_idx)

    vals = window._collect_settings()
    assert vals["ui_language"] == "en"
    assert vals["language"] == "auto"


def test_term_dictionary_is_shared_and_visible_outside_cleanup(window):
    panel = window.settings_panel
    panel.txt_live_hotwords.setText("ИАС УГРТ, ФГИС ТП, API")

    assert panel.txt_hotwords.text() == "ИАС УГРТ, ФГИС ТП, API"
    assert not panel.txt_live_hotwords.isHidden()
    assert not panel.txt_hotwords.isHidden()
    values = panel.values()
    assert values["term_dictionary"] == [
        "ИАС УГРТ",
        "ФГИС ТП",
        "API",
    ]
    assert "hotwords" not in values["postprocessing"]["cleanup"]


def test_live_hotkey_autostarts_and_local_models_are_locked_resident(window):
    panel = window.settings_panel
    assert not hasattr(panel, "chk_live_autostart")
    assert not hasattr(panel, "chk_live_prewarm")
    assert not hasattr(panel, "spin_live_idle_unload")
    live = panel.values()["live"]
    assert "autostart" not in live
    assert live["prewarm_local"] is True
    assert live["local"]["idle_unload_seconds"] == 0


def test_queue_status_updates(window):
    files = [Path("a.mp3"), Path("sub/b.wav")]
    window.queue_model.set_files(files)
    assert window.queue_model.rowCount() == 2
    window.queue_model.update_status("a.mp3", "processing")
    window.queue_model.update_status("a.mp3", "completed")
    assert window.queue_model._rows[0]["status"] == "completed"


def test_queue_checkboxes_override_incidental_row_selection(window):
    files = [Path("a.mp3"), Path("b.wav")]
    window.queue_model.set_files(files)
    window.queue_view.selectRow(0)

    assert window.queue_model.columnCount() == 3
    assert window.queue_model.setData(
        window.queue_model.index(1, 0), Qt.Checked, Qt.CheckStateRole
    )
    assert window.queue_model.checked_files() == [files[1]]
    assert window._selected_queue_items() == [(1, files[1])]


def test_queue_view_has_rounded_viewport_mask(window):
    window.queue_view.resize(240, 180)
    window.queue_view._apply_rounded_viewport_mask()
    assert not window.queue_view.mask().isEmpty()
    assert window.queue_view.viewport().mask().isEmpty()


def test_trash_deletes_only_selected_input_file(window, tmp_path, monkeypatch):
    test_paths = replace(
        window._paths,
        input=tmp_path / "input",
        workspace=tmp_path / "workspace",
    )
    test_paths.input.mkdir(parents=True)
    source = test_paths.input / "Audion_Recording_test.wav"
    source.write_bytes(b"wav")
    window._paths = test_paths
    window.queue_model.set_files([source])
    window.queue_view.selectRow(0)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    window._on_clear()

    assert not source.exists()
    assert window.queue_model.is_empty()


def test_temp_cleanup_keeps_source_and_queue_row(window, tmp_path):
    test_paths = replace(
        window._paths,
        input=tmp_path / "input",
        workspace=tmp_path / "workspace",
    )
    test_paths.input.mkdir(parents=True)
    source = test_paths.input / "meeting.wav"
    source.write_bytes(b"wav")
    work_dir = test_paths.workspace / source.stem / "chunks"
    work_dir.mkdir(parents=True)
    (work_dir / "chunk_0000.wav").write_bytes(b"chunk")
    window._paths = test_paths
    window.queue_model.set_files([source])
    window.queue_view.selectRow(0)

    window._cleanup_selected_temp()

    assert source.exists()
    assert not (test_paths.workspace / source.stem).exists()
    assert window.queue_model.files() == [source]
    assert window.queue_model._rows[0]["status"] == "pending"


def test_log_view_has_rounded_viewport_mask(window):
    window.log_view.resize(240, 120)
    window.log_view._apply_rounded_viewport_mask()
    assert not window.log_view.viewport().mask().isEmpty()
    assert isinstance(window.log_view, RoundedPlainTextEdit)
    assert hasattr(window.log_view, "paths_dropped")
    assert window.log_view.focusPolicy() == Qt.ClickFocus


def test_installer_output_is_routed_to_left_activity_log(window):
    window._workspace_tabs.set_current_key("workspace_tab_queue")
    window._workspace_stack.setCurrentWidget(window.queue_box)

    window.settings_panel.modules_panel._log("test installer line")

    assert window._workspace_stack.currentWidget() is window.log_box
    assert "test installer line" in window.log_view.toPlainText()


def test_icon_buttons_have_accessible_names(window):
    for button in (
        window.btn_add_files,
        window.btn_add_folder,
        window.btn_live_record,
        window.btn_log_export,
        window.btn_log_clear,
        window.btn_log_save,
        window.btn_log_expand,
    ):
        assert button.accessibleName()


def test_overlay_quick_menu_switches_recording_mode(window, monkeypatch):
    from system_core.live import focus

    menus = []
    monkeypatch.setattr(focus, "get_foreground_window", lambda: 321)
    monkeypatch.setattr(main_window_mod.QMenu, "popup", lambda menu, _pos: menus.append(menu))

    window._show_overlay_context_menu(QPoint(20, 20))

    assert len(menus) == 1
    menu = menus[0]
    labels = [action.text() for action in menu.actions() if action.text()]
    assert labels[:2] == [
        window._tr.tr("dictation_history_title"),
        window._tr.tr("dictation_history_paste_last"),
    ]
    assert menu.objectName() == "OverlayQuickMenu"
    assert menu.windowOpacity() == pytest.approx(0.85, abs=1 / 255)
    assert window._dictation_target_hwnd == 321
    assert window._tr.tr("overlay_menu_live_mode") in labels
    assert window._tr.tr("overlay_menu_file_mode") in labels
    assert window._tr.tr("open_input") in labels
    assert window._tr.tr("open_output") in labels
    file_mode = next(
        action for action in menu.actions()
        if action.text() == window._tr.tr("overlay_menu_file_mode")
    )
    file_mode.trigger()
    assert window._workspace_stack.currentWidget() is window.queue_box


def test_dictation_history_cards_copy_delete_and_paste(window, app):
    entries = tuple(
        DictationEntry(str(index), f"dictation {index}", "2026-07-18T10:00:00+00:00")
        for index in range(OVERLAY_HISTORY_LIMIT + 5)
    )
    dialog = DictationHistoryDialog(window._tr, entries, parent=window)
    pasted = []
    copied = []
    deleted = []
    dialog.paste_requested.connect(pasted.append)
    dialog.copy_requested.connect(copied.append)
    dialog.delete_requested.connect(deleted.append)
    dialog.show()
    app.processEvents()
    try:
        previews = [
            label for label in dialog.findChildren(QLabel)
            if label.objectName() == "DictationHistoryPreview"
        ]
        buttons = dialog.findChildren(QPushButton)
        copy_buttons = [
            button for button in buttons
            if button.accessibleName() == window._tr.tr("dictation_history_copy")
        ]
        delete_buttons = [
            button for button in buttons
            if button.accessibleName() == window._tr.tr("dictation_history_delete")
        ]

        assert len(previews) == OVERLAY_HISTORY_LIMIT
        assert len(copy_buttons) == OVERLAY_HISTORY_LIMIT
        assert len(delete_buttons) == OVERLAY_HISTORY_LIMIT
        margins = dialog._items.contentsMargins()
        assert margins.left() == margins.right()
        card = previews[0].parentWidget()
        card_right_gap = dialog._content.width() - card.geometry().right() - 1
        assert card.geometry().left() == card_right_gap
        assert copy_buttons[0].size().width() == 34
        assert delete_buttons[0].size().height() == 34
        assert previews[0].height() == previews[0].fontMetrics().lineSpacing() * 2 + 6

        copy_buttons[0].click()
        delete_buttons[0].click()
        QTest.mouseClick(previews[0], Qt.LeftButton)
        assert copied == ["dictation 0"]
        assert deleted == ["0"]
        assert pasted == ["dictation 0"]
    finally:
        dialog.close()


def test_dictation_history_fades_only_overflowing_previews(window, app):
    short_text = "Short dictation."
    long_text = " ".join(["Long dictation preview"] * 40)
    entries = (
        DictationEntry("short", short_text, "2026-07-18T10:00:00+00:00"),
        DictationEntry("long", long_text, "2026-07-18T10:00:00+00:00"),
    )
    dialog = DictationHistoryDialog(window._tr, entries, parent=window)
    dialog.show()
    app.processEvents()
    try:
        previews = {
            label.text(): label
            for label in dialog.findChildren(QLabel)
            if label.objectName() == "DictationHistoryPreview"
        }
        assert previews[short_text].property("fadeActive") is False
        assert previews[long_text].property("fadeActive") is True
        assert previews[long_text].graphicsEffect() is not None
    finally:
        dialog.close()


def test_full_dictation_history_is_scrollable(window, app):
    entries = tuple(
        DictationEntry(str(index), f"dictation {index}", "2026-07-18T10:00:00+00:00")
        for index in range(35)
    )
    dialog = DictationHistoryDialog(
        window._tr,
        entries,
        parent=window,
        limit=HISTORY_CACHE_LIMIT,
        title_key="dictation_history_all_title",
    )
    dialog.show()
    app.processEvents()
    try:
        previews = [
            label for label in dialog.findChildren(QLabel)
            if label.objectName() == "DictationHistoryPreview"
        ]
        assert len(previews) == len(entries)
        assert dialog._count.text() == f"{len(entries)} / {HISTORY_CACHE_LIMIT}"
        assert dialog._scroll.verticalScrollBar().maximum() > 0
    finally:
        dialog.close()


def test_past_dictation_restores_captured_target(window, app, monkeypatch):
    from system_core.live import focus

    calls = []
    monkeypatch.setattr(
        focus,
        "paste_text",
        lambda text, method, hwnd: calls.append((text, method, hwnd)),
    )
    window._dictation_target_hwnd = 987

    window._paste_dictation("reused dictation")
    app.processEvents()

    assert calls == [("reused dictation", window._live_cfg.paste_method, 987)]


def test_log_expand_uses_left_column(window):
    window._toggle_log_expanded()
    try:
        assert window._workspace_stack.currentWidget() is window.log_box
        assert window._workspace_tabs.isHidden()
        assert window._right_panel.isHidden()
        assert window.log_box.maximumHeight() > 1_000_000
    finally:
        window._toggle_log_expanded()
    assert not window._workspace_tabs.isHidden()
    assert not window._right_panel.isHidden()


def test_log_markdown_export(window, tmp_path):
    window.log_view.setPlainText("line one\n```")

    saved = window._write_log_markdown(tmp_path / "audion-log")
    body = saved.read_text(encoding="utf-8")

    assert saved.name == "audion-log.md"
    assert body.startswith("# Audion Voice AI")
    assert "````text\nline one\n```\n````" in body


def test_settings_roundtrip(window):
    window.settings_panel.set_model_lists(
        ["gpt-4o-transcribe-diarize", "whisper-1"],
        ["gpt-5.4-mini", "gpt-4o"],
        "fallback",
    )
    window.settings_panel.btn_diar.setChecked(True)
    window.settings_panel._live_source_buttons["api"].setChecked(True)
    window.settings_panel._live_api_provider_buttons["openai"].setChecked(True)
    window.settings_panel._live_api_mode_buttons["batch"].setChecked(True)
    fast_profile_idx = window.settings_panel.cmb_stt.findData("gpt-4o-mini-transcribe")
    assert fast_profile_idx >= 0
    window.settings_panel.cmb_stt.setCurrentIndex(fast_profile_idx)
    window.settings_panel._set_local_engine(window.settings_panel._live_local_engine_buttons, "whispercpp")
    window.settings_panel._sync_live_local_controls()
    window.settings_panel._set_local_engine(window.settings_panel._file_local_engine_buttons, "whispercpp")
    window.settings_panel._sync_file_local_controls()
    live_local_idx = window.settings_panel.cmb_live_local_model.findData("small")
    file_local_idx = window.settings_panel.cmb_file_vulkan_model.findData("large-v2")
    file_backend_idx = window.settings_panel.cmb_file_vulkan_backend.findData("cpu")
    assert live_local_idx >= 0
    assert file_local_idx >= 0
    assert file_backend_idx >= 0
    window.settings_panel.cmb_live_local_model.setCurrentIndex(live_local_idx)
    window.settings_panel.cmb_file_vulkan_model.setCurrentIndex(file_local_idx)
    window.settings_panel.cmb_file_vulkan_backend.setCurrentIndex(file_backend_idx)
    window.settings_panel.chk_live_overlay.setChecked(False)
    window.settings_panel.chk_show_tray.setChecked(False)
    window.settings_panel.spin_live_overlay_scale.setValue(115)
    window.settings_panel.spin_live_safety_timeout.setValue(20)
    window.settings_panel._live_cleanup_box.setChecked(True)
    window.settings_panel.cmb_live_cleanup_model.setCurrentText("gpt-4o")
    window.settings_panel.spin_live_cleanup_sentences.setValue(9)
    if hasattr(window.settings_panel, "cmb_cuda_model"):
        window.settings_panel.cmb_cuda_model.setCurrentIndex(
            window.settings_panel.cmb_cuda_model.findData("turbo")
        )
    vals = window.settings_panel.values()
    for key in ("compute_mode", "exports", "subtitles", "pipeline", "postprocessing", "vulkan"):
        assert key in vals
    assert "cleanup" in vals["postprocessing"]
    assert vals["transcription"]["diarize"] is True
    assert vals["live"]["engine"] in {"batch", "realtime", "vulkan"}
    assert vals["stt"]["model"] == "gpt-4o-mini-transcribe"
    assert vals["live"]["model"] == "gpt-4o-mini-transcribe"
    assert vals["live"]["source"] == "api"
    assert vals["live"]["api"]["provider"] == "openai"
    assert vals["live"]["local"]["engine"] == "whispercpp"
    assert vals["live"]["local"]["model"] == "small"
    assert vals["live"]["minimize_to_tray"] is False
    assert vals["vulkan"]["engine"] == "whispercpp"
    assert vals["vulkan"]["model"] == "large-v2"
    assert vals["vulkan"]["backend"] == "cpu"
    assert vals["live"]["show_overlay"] is False
    assert vals["live"]["overlay_scale_percent"] == 115
    assert "overlay_height" not in vals["live"]
    assert vals["live"]["safety_timeout_minutes"] == 20
    assert vals["live"]["cleanup"]["enabled"] is True
    assert vals["live"]["cleanup"]["model"] == "gpt-4o"
    assert vals["live"]["cleanup"]["sentence_threshold"] == 9
    if hasattr(window.settings_panel, "cmb_cuda_model"):
        assert vals["local"]["model"] == "turbo"
        assert vals["local"]["device"] == "cuda"
        assert vals["local"]["compute_type"] == "float16"


def test_plain_large_v3_is_not_exposed_in_model_lists(window):
    panel = window.settings_panel
    panel.set_model_lists(
        ["large-v2", "large-v3", "large-v3-turbo", "whisper-1"],
        ["gpt-4o"],
        "api",
    )
    panel._set_local_engine(panel._live_local_engine_buttons, "whispercpp")
    panel._sync_live_local_controls()
    panel._set_local_engine(panel._file_local_engine_buttons, "whispercpp")
    panel._sync_file_local_controls()

    for combo in (panel.cmb_live_local_model, panel.cmb_file_vulkan_model):
        values = _combo_values(combo)
        assert "large-v3" not in values
        assert "ggml-large-v3.bin" not in values
        assert "turbo" in values

    if hasattr(panel, "cmb_cuda_model"):
        values = _combo_values(panel.cmb_cuda_model)
        assert "large-v3" not in values
        assert "turbo" in values


def test_live_edition_compute_toggles(window):
    panel = window.settings_panel
    paths = get_project_paths()
    expected = {"api", "vulkan", "cuda"} if current_edition(paths, window._settings) == "studio" else {"api", "vulkan"}
    assert set(panel._compute_buttons) == expected

    panel._live_source_buttons["api"].setChecked(True)
    panel._live_api_provider_buttons["openai"].setChecked(True)
    panel._live_api_mode_buttons["batch"].setChecked(True)
    panel._compute_buttons["vulkan"].setChecked(True)
    vals = panel.values()

    assert vals["compute_mode"] == "vulkan"
    assert vals["live"]["engine"] == "batch"

    panel._live_source_buttons["local"].setChecked(True)
    vals = panel.values()
    assert vals["live"]["engine"] == "vulkan"


def test_settings_save_now_persists_ui_choices(window, tmp_path, monkeypatch):
    from system_core.ui.settings_io import save_settings as save_settings_to_disk

    isolated_paths = get_project_paths(tmp_path)
    monkeypatch.setattr(
        main_window_mod,
        "save_settings",
        lambda _paths, settings: save_settings_to_disk(isolated_paths, settings),
    )
    idx = window.cmb_theme.findData("graphite")
    if idx >= 0:
        window.cmb_theme.setCurrentIndex(idx)
    window.settings_panel.chk_txt.setChecked(True)
    window.settings_panel.txt_context.setPlainText("persist me")

    window._save_settings_now(force=True)

    app_cfg = load_yaml_or_json(isolated_paths.config / "app_settings.yaml")
    assert app_cfg["ui_theme"] == window._theme.name
    assert app_cfg["exports"]["txt"] is True
    assert app_cfg["context"] == "persist me"
    assert app_cfg["term_dictionary"]
    assert "hotwords" not in app_cfg["postprocessing"]["cleanup"]
    assert app_cfg["live"]["layout_routing"] == {
        "enabled": True,
        "ru_model": "gigaam-v3-e2e-ctc",
        "en_model": "turbo",
    }


def test_queue_state_roundtrip(window):
    files = [Path(r"C:\input\a.wav"), Path(r"C:\input\b.mp3")]
    window.queue_model.set_files(files)

    window._save_queue_state_now(force=True)
    window.queue_model.clear()
    window._restore_queue_state()

    assert [str(p) for p in window.queue_model.files()] == [str(p) for p in files]
