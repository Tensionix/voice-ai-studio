"""First-run download prompt: module selection, persistence and the install queue.

Pure-Python parts (recommendation, missing list, state file) run everywhere;
the dialog and main-window parts need PySide6 and use offscreen Qt. No test
launches a real installer.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from system_core.core.config import load_yaml_or_json
from system_core.core.editions import current_edition
from system_core.core.modules import (
    NOT_NEEDED,
    RECOMMENDED,
    list_modules,
    missing_recommended_modules,
    module_recommendation,
)
from system_core.core.paths import get_project_paths
from system_core.ui.state_io import (
    SETUP_PROMPT_INSTALL,
    SETUP_PROMPT_LATER,
    SETUP_PROMPT_NEVER,
    load_setup_prompt_answer,
    save_setup_prompt_answer,
)


def _nvidia_profile():
    return SimpleNamespace(has_nvidia=True, has_amd=False, has_intel=False)


def test_recommended_modules_have_download_estimates():
    paths = get_project_paths()
    for mod in list_modules(paths):
        if mod.key in {"gigaam", "vulkan", "whispercpp_models", "gpu"}:
            assert mod.download_mb > 0, mod.key


def test_gpu_stack_recommended_only_for_studio_nvidia():
    paths = get_project_paths()
    gpu = next((m for m in list_modules(paths) if m.key == "gpu"), None)
    if gpu is None:
        assert current_edition(paths) != "studio"
        return
    assert module_recommendation(gpu, paths, None) == NOT_NEEDED
    assert module_recommendation(gpu, paths, _nvidia_profile()) == RECOMMENDED


def test_missing_recommended_excludes_installed_and_restore_rows():
    paths = get_project_paths()
    missing = missing_recommended_modules(paths, None)
    keys = [m.key for m in missing]
    assert "restore_intel" not in keys and "restore_rtx" not in keys
    for mod in missing:
        assert not mod.is_installed(paths)
        assert module_recommendation(mod, paths, None) == RECOMMENDED
    # Catalog order is preserved (install order matters: packs before models).
    order = [m.key for m in list_modules(paths)]
    assert keys == [key for key in order if key in keys]


def test_setup_prompt_answer_round_trip(tmp_path):
    paths = get_project_paths(tmp_path)
    assert load_setup_prompt_answer(paths) == ""
    save_setup_prompt_answer(paths, SETUP_PROMPT_LATER)
    assert load_setup_prompt_answer(paths) == SETUP_PROMPT_LATER
    save_setup_prompt_answer(paths, SETUP_PROMPT_NEVER)
    assert load_setup_prompt_answer(paths) == SETUP_PROMPT_NEVER
    # Garbage never resolves to a valid answer.
    (paths.workspace / "ui_state.json").write_text('{"setup_prompt": {"answer": "??"}}', encoding="utf-8")
    assert load_setup_prompt_answer(paths) == ""


def test_setup_prompt_translation_keys_exist():
    paths = get_project_paths()
    strings = load_yaml_or_json(paths.config / "i18n.yaml").get("strings", {})
    for key in (
        "setup_prompt_title",
        "setup_prompt_intro",
        "setup_prompt_total",
        "setup_prompt_note",
        "setup_prompt_install",
        "setup_prompt_later",
        "setup_prompt_never",
        "setup_prompt_queue_start",
        "setup_prompt_queue_done",
        "setup_prompt_queue_partial",
    ):
        assert key in strings, key
        assert strings[key].get("ru") and strings[key].get("en"), key


# --- Qt ------------------------------------------------------------------------
_DEVLIBS = Path(__file__).resolve().parents[1] / ".devlibs"
if _DEVLIBS.exists():
    import sys

    sys.path.insert(0, str(_DEVLIBS))

if pytest.importorskip("PySide6", reason="GUI deps not installed"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication  # noqa: E402

    from system_core.ui.i18n import Translator  # noqa: E402
    from system_core.ui.main_window import MainWindow  # noqa: E402
    from system_core.ui.setup_prompt import SetupPromptDialog, format_download_size  # noqa: E402

    @pytest.fixture(scope="module")
    def app():
        instance = QApplication.instance() or QApplication([])
        yield instance

    def test_format_download_size_is_bilingual():
        assert format_download_size(1750, "ru") == "≈ 1,7 ГБ"
        assert format_download_size(1750, "en") == "≈ 1.7 GB"
        assert format_download_size(640, "ru") == "≈ 640 МБ"
        assert format_download_size(0, "en") == ""

    def test_dialog_lists_modules_and_totals(app):
        paths = get_project_paths()
        tr = Translator.load(paths, "ru")
        modules = [m for m in list_modules(paths) if m.key in {"gigaam", "vulkan"}]
        dlg = SetupPromptDialog(modules, tr)
        assert dlg.selected_keys() == ["gigaam", "vulkan"]
        total = sum(m.download_mb for m in modules)
        assert format_download_size(total, "ru") in dlg._total.text()
        dlg._checks["vulkan"].setChecked(False)
        assert dlg.selected_keys() == ["gigaam"]
        dlg._checks["gigaam"].setChecked(False)
        assert not dlg.btn_install.isEnabled()
        dlg._checks["gigaam"].setChecked(True)

        dlg.btn_install.click()
        assert dlg.answer == SETUP_PROMPT_INSTALL
        dlg.deleteLater()

    def test_dialog_later_and_never_answers(app):
        paths = get_project_paths()
        tr = Translator.load(paths, "en")
        modules = [m for m in list_modules(paths) if m.key == "gigaam"]
        dlg = SetupPromptDialog(modules, tr)
        dlg.btn_later.click()
        assert dlg.answer == SETUP_PROMPT_LATER
        dlg = SetupPromptDialog(modules, tr)
        dlg.btn_never.click()
        assert dlg.answer == SETUP_PROMPT_NEVER
        dlg.deleteLater()

    @pytest.fixture
    def window(app, monkeypatch):
        paths = get_project_paths()
        state_file = paths.workspace / "ui_state.json"
        snapshot = state_file.read_text(encoding="utf-8") if state_file.exists() else None
        try:
            state_file.unlink()
        except FileNotFoundError:
            pass
        monkeypatch.setenv("AUDION_SETUP_PROMPT", "1")
        win = MainWindow(paths)
        try:
            yield win
        finally:
            win.close()
            if snapshot is None:
                try:
                    state_file.unlink()
                except FileNotFoundError:
                    pass
            else:
                state_file.write_text(snapshot, encoding="utf-8")

    def test_main_window_shows_prompt_once_and_queues_install(window, monkeypatch):
        panel = window.settings_panel.modules_panel
        paths = window._paths
        missing = [m for m in list_modules(paths) if m.key == "gigaam"]
        monkeypatch.setattr(panel, "missing_recommended_modules", lambda: missing)
        opened: list[SetupPromptDialog] = []
        monkeypatch.setattr(SetupPromptDialog, "open", lambda self: opened.append(self))
        queued: list[list[str]] = []
        monkeypatch.setattr(panel, "install_queue", lambda keys: queued.append(list(keys)) or True)

        window._maybe_show_setup_prompt()
        assert len(opened) == 1
        # A second hardware-profile signal must not open a second prompt.
        window._maybe_show_setup_prompt()
        assert len(opened) == 1

        dialog = opened[0]
        dialog.btn_install.click()
        assert queued == [["gigaam"]]
        assert load_setup_prompt_answer(paths) == SETUP_PROMPT_INSTALL

    def test_main_window_respects_never(window, monkeypatch):
        panel = window.settings_panel.modules_panel
        paths = window._paths
        save_setup_prompt_answer(paths, SETUP_PROMPT_NEVER)
        missing = [m for m in list_modules(paths) if m.key == "gigaam"]
        monkeypatch.setattr(panel, "missing_recommended_modules", lambda: missing)
        opened: list[SetupPromptDialog] = []
        monkeypatch.setattr(SetupPromptDialog, "open", lambda self: opened.append(self))

        window._maybe_show_setup_prompt()
        assert opened == []

    def test_main_window_skips_prompt_when_nothing_missing(window, monkeypatch):
        panel = window.settings_panel.modules_panel
        monkeypatch.setattr(panel, "missing_recommended_modules", lambda: [])
        opened: list[SetupPromptDialog] = []
        monkeypatch.setattr(SetupPromptDialog, "open", lambda self: opened.append(self))

        window._maybe_show_setup_prompt()
        assert opened == []
        assert not window._setup_prompt_shown
