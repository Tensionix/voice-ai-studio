"""Tests for the Voice AI module catalog + the install dialog.

Catalog/status checks are pure Python (no Qt). The dialog test skips without
PySide6 and uses offscreen Qt; it only builds the dialog (never triggers an
actual install).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from system_core.core.config import load_yaml_or_json
from system_core.core.editions import current_edition
from system_core.core.modules import list_modules
from system_core.core.paths import get_project_paths
from system_core.core.capabilities import STATE_READY, list_capabilities, vulkan_capability


def _expected_module_keys(paths):
    if current_edition(paths) == "studio":
        return ["ffmpeg", "live", "wheel_cache", "gigaam", "vulkan", "whispercpp_models", "gpu", "restore_intel", "restore_rtx"]
    return ["ffmpeg", "live", "wheel_cache", "gigaam", "vulkan", "restore_intel", "restore_rtx"]


def test_catalog_shape():
    paths = get_project_paths()
    mods = list_modules(paths)
    keys = [m.key for m in mods]
    assert keys == _expected_module_keys(paths)
    # Each module points at an installer that actually ships in install/.
    for mod in mods:
        assert mod.script_path(paths).exists(), f"missing installer for {mod.key}"


def test_module_translation_keys_exist():
    paths = get_project_paths()
    strings = load_yaml_or_json(paths.config / "i18n.yaml").get("strings", {})
    for mod in list_modules(paths):
        assert mod.name_key in strings, f"missing title i18n key for {mod.key}: {mod.name_key}"
        assert mod.desc_key in strings, f"missing description i18n key for {mod.key}: {mod.desc_key}"


def test_is_installed_returns_bool():
    paths = get_project_paths()
    for mod in list_modules(paths):
        assert isinstance(mod.is_installed(paths), bool)  # never raises / always bool


def test_ffmpeg_status_matches_filesystem():
    paths = get_project_paths()
    ffmpeg = next(m for m in list_modules(paths) if m.key == "ffmpeg")
    present = (paths.tools / "ffmpeg" / "bin" / "ffmpeg.exe").exists()
    assert ffmpeg.is_installed(paths) == present


def test_capability_matrix_shape():
    paths = get_project_paths()
    caps = list_capabilities(paths)
    if current_edition(paths) == "studio":
        expected = ["openai", "ffmpeg", "live", "gigaam", "vulkan", "cuda"]
    else:
        expected = ["openai", "ffmpeg", "live", "gigaam", "vulkan"]
    assert [cap.key for cap in caps] == expected
    for cap in caps:
        assert cap.state in {"ready", "partial", "missing"}
        assert cap.checks


def test_whispercpp_ready_requires_manifest_server_and_payloads(tmp_path):
    paths = get_project_paths(tmp_path)
    (paths.tools / "whispercpp").mkdir(parents=True)
    (paths.models).mkdir(parents=True)
    (paths.tools / "whispercpp" / "whisper-cli.exe").write_text("", encoding="utf-8")
    (paths.models / "ggml-large-v3-turbo.bin").write_text("", encoding="utf-8")

    cap = vulkan_capability(paths)
    assert cap.state != STATE_READY

    root = paths.tools / "whispercpp"
    (root / "audion-whispercpp-pack.txt").write_text("kind=cpu\n", encoding="utf-8")
    for filename in ("whisper-server.exe", "ggml.dll", "ggml-base.dll", "whisper.dll", "ggml-cpu-test.dll"):
        (root / filename).write_text("", encoding="utf-8")
    cap = vulkan_capability(paths)
    assert cap.state == STATE_READY
    assert cap.missing_count == 0
    optional = [check for check in cap.checks if not check.required]
    assert optional
    assert any(not check.ok for check in optional)


# --- dialog (Qt) -------------------------------------------------------------
_DEVLIBS = Path(__file__).resolve().parents[1] / ".devlibs"
if _DEVLIBS.exists():
    import sys

    sys.path.insert(0, str(_DEVLIBS))

if pytest.importorskip("PySide6", reason="GUI deps not installed"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication  # noqa: E402

    from system_core.ui.i18n import Translator  # noqa: E402
    from system_core.ui.modules_dialog import ModulesDialog  # noqa: E402

    @pytest.fixture(scope="module")
    def app():
        instance = QApplication.instance() or QApplication([])
        yield instance

    def test_modules_dialog_builds(app):
        paths = get_project_paths()
        tr = Translator.load(paths, "ru")
        dlg = ModulesDialog(paths, tr)
        # One row per module, each with a status label + install button.
        assert list(dlg._rows.keys()) == _expected_module_keys(paths)
        if current_edition(paths) == "studio":
            expected_caps = {"openai", "ffmpeg", "live", "gigaam", "vulkan", "cuda"}
        else:
            expected_caps = {"openai", "ffmpeg", "live", "gigaam", "vulkan"}
        assert set(dlg._panel._cap_rows.keys()) == expected_caps
        assert dlg._panel.btn_mic_check.text() == tr.tr("mic_check_button")
        assert dlg._panel._mic_check_status.text() == tr.tr("mic_check_idle")
        for _key, (status, btn) in dlg._rows.items():
            assert status.text() in (tr.tr("mod_installed"), tr.tr("mod_not_installed"))
            assert btn.text() in (tr.tr("mod_install"), tr.tr("mod_reinstall"), tr.tr("mod_restore"))
        dlg.deleteLater()
