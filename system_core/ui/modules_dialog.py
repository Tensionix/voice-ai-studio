"""The "Install Voice AI modules" section.

`ModulesPanel` is the reusable widget: it lists the opt-in modules from
`core.modules` with live install status, runs the right `install/*.cmd` on demand
(output streamed into a log), and re-checks status when an install finishes. It is
embedded directly in the main window's settings panel (a visible tab, not
hidden behind a header button) and still wrapped by `ModulesDialog` for focused
tests or future modal reuse. Available once the portable runtime is up — which
it always is when the GUI runs.
"""

from __future__ import annotations

import importlib
import re

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.capabilities import STATE_MISSING, STATE_PARTIAL, STATE_READY, list_capabilities
from ..core.credentials import mask_api_key, read_api_key, write_api_key
from ..core.editions import EDITION_STUDIO, current_edition
from ..core.modules import (
    NOT_NEEDED,
    OPTIONAL,
    RECOMMENDED,
    ModuleInfo,
    list_modules,
    missing_recommended_modules,
    module_recommendation,
)
from ..core.paths import ProjectPaths
from .i18n import Translator
from .icons import icon
from .tooltips import seed_tooltips
from .workers import InstallWorker, LocalHardwareWorker, MicrophoneCheckWorker

_ICON_MUTED = "#9FB0C3"
_ACTION_BUTTON_WIDTH = 208


class ModulesPanel(QWidget):
    """Per-module rows (name + status + install button) plus streamed output.

    No heading or close button — those belong to whatever hosts the panel (a Card
    in the settings form, or `ModulesDialog`)."""

    log_line = Signal(str)
    module_installed = Signal(str, int)
    # Hardware detection finished (successfully or not): recommendations are
    # final from now on. Used by the first-run download prompt.
    profile_resolved = Signal()
    # A multi-module install queue ended: (installed ok, total queued).
    queue_finished = Signal(int, int)

    def __init__(
        self,
        paths: ProjectPaths,
        tr: Translator,
        parent=None,
        *,
        embedded_log: bool = False,
    ):
        super().__init__(parent)
        self._paths = paths
        self._tr = tr
        self._worker: InstallWorker | None = None
        self._mic_worker: MicrophoneCheckWorker | None = None
        self._profile_worker: LocalHardwareWorker | None = None
        self._profile = None
        self.profile_is_resolved = False
        self._queue: list[ModuleInfo] = []
        self._queue_total = 0
        self._queue_ok = 0
        self._rows: dict[str, tuple[QLabel, QPushButton]] = {}
        self._module_frames: dict[str, QFrame] = {}
        self._module_badges: dict[str, QLabel] = {}
        self._cap_rows: dict[str, tuple[QLabel, QLabel]] = {}
        self._cap_texts: dict[str, tuple[QLabel, QLabel]] = {}
        self._module_texts: dict[str, tuple[QLabel, QLabel]] = {}
        self._mic_check_status_key = "mic_check_idle"

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        root.addWidget(self._build_install_profile_card())

        self._progress_frame = self._build_progress_frame()
        root.addWidget(self._progress_frame)

        self.log_view: QPlainTextEdit | None = None
        if embedded_log:
            self.log_view = QPlainTextEdit()
            self.log_view.setObjectName("LogView")
            self.log_view.setReadOnly(True)
            self.log_view.setFocusPolicy(Qt.ClickFocus)
            self.log_view.setFixedHeight(112)
            self.log_view.setPlaceholderText(tr.tr("modules_log_hint"))
            root.addWidget(self.log_view)

        # Installation is shown in dependency order. Readiness checks and
        # credentials follow the installable stages, so first-run setup reads
        # from top to bottom instead of jumping between diagnosis and actions.
        for mod in list_modules(self._paths):
            root.addWidget(self._build_row(mod))

        root.addWidget(self._build_capability_matrix())
        root.addWidget(self._build_mic_check_row())

        # API keys used by cloud Live/File providers.
        root.addWidget(self._build_key_row("openai", "api_key_name", "api_key_desc", "api_key_prompt", "api_key_saved"))
        root.addWidget(self._build_key_row("xai", "xai_key_name", "xai_key_desc", "xai_key_prompt", "xai_key_saved"))
        root.addWidget(
            self._build_key_row(
                "elevenlabs",
                "elevenlabs_key_name",
                "elevenlabs_key_desc",
                "elevenlabs_key_prompt",
                "elevenlabs_key_saved",
            )
        )
        # Notion token — only needed for the optional Notion export connector.
        root.addWidget(self._build_notion_key_row())

        self._refresh_all()
        seed_tooltips(self)

    # --- recommended setup --------------------------------------------------
    def _build_install_profile_card(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")
        root = QVBoxLayout(frame)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(6)

        self._profile_title = QLabel(self._tr.tr("install_profile_title"))
        self._profile_title.setProperty("role", "card-title")
        root.addWidget(self._profile_title)

        self._profile_summary = QLabel("")
        self._profile_summary.setWordWrap(True)
        root.addWidget(self._profile_summary)

        self._profile_plan = QLabel("")
        self._profile_plan.setProperty("role", "muted")
        self._profile_plan.setWordWrap(True)
        root.addWidget(self._profile_plan)
        return frame

    def _clean_gpu_name(self, name: str) -> str:
        text = re.sub(r"\s*\(UUID:.*?\)\s*$", "", str(name).strip())
        text = re.sub(r"^GPU\s+\d+\s*:\s*", "", text, flags=re.IGNORECASE)
        return text.strip() or str(name).strip()

    def _gpu_display(self, profile) -> str:
        names = []
        for raw in getattr(profile, "gpu_names", ()) or ():
            name = self._clean_gpu_name(str(raw))
            if name and name not in names:
                names.append(name)
        if not names:
            return self._tr.tr("install_profile_gpu_none")
        return ", ".join(names[:2])

    def _profile_name_key(self, profile) -> str:
        edition = current_edition(self._paths)
        if edition == EDITION_STUDIO and getattr(profile, "has_nvidia", False):
            return "install_profile_studio_nvidia"
        if getattr(profile, "has_nvidia", False) or getattr(profile, "has_amd", False) or getattr(profile, "has_intel", False):
            return "install_profile_windows_gpu"
        return "install_profile_cpu"

    def _profile_plan_key(self, profile) -> str:
        edition = current_edition(self._paths)
        if edition == EDITION_STUDIO and getattr(profile, "has_nvidia", False):
            return "install_profile_plan_studio_nvidia"
        if getattr(profile, "has_nvidia", False) or getattr(profile, "has_amd", False) or getattr(profile, "has_intel", False):
            return "install_profile_plan_windows_gpu"
        return "install_profile_plan_cpu"

    def _refresh_install_profile(self) -> None:
        if self._profile is None:
            self._profile_summary.setText(self._tr.tr("install_profile_checking"))
            self._profile_plan.setText(self._tr.tr("install_profile_wait"))
            self._start_profile_worker()
            return
        gpu = self._gpu_display(self._profile)
        profile = self._tr.tr(self._profile_name_key(self._profile))
        self._profile_summary.setText(
            self._tr.tr("install_profile_summary")
            .replace("{gpu}", gpu)
            .replace("{profile}", profile)
        )
        self._profile_plan.setText(self._tr.tr(self._profile_plan_key(self._profile)))

    def _start_profile_worker(self) -> None:
        if self._profile_worker is not None and self._profile_worker.isRunning():
            return
        worker = LocalHardwareWorker(self._paths, self)
        worker.progress.connect(self._on_profile_progress)
        worker.done.connect(self._on_profile_ready)
        worker.failed.connect(self._on_profile_failed)
        worker.finished.connect(lambda: self._clear_profile_worker(worker))
        worker.finished.connect(worker.deleteLater)
        self._profile_worker = worker
        worker.start()

    def _clear_profile_worker(self, worker: LocalHardwareWorker) -> None:
        if self._profile_worker is worker:
            self._profile_worker = None

    def _on_profile_progress(self, _step: int, _total: int, stage_key: str) -> None:
        stage = self._tr.tr(stage_key)
        if stage == stage_key:
            stage = stage_key
        self._profile_summary.setText(
            self._tr.tr("install_profile_progress").replace("{stage}", stage)
        )

    def _on_profile_ready(self, profile) -> None:
        self._profile = profile
        self._refresh_install_profile()
        self._refresh_module_recommendations()
        self._mark_profile_resolved()

    def _on_profile_failed(self, message: str) -> None:
        self._profile_summary.setText(
            self._tr.tr("install_profile_failed").replace("{error}", message)
        )
        self._profile_plan.setText(self._tr.tr("install_profile_plan_safe"))
        self._refresh_module_recommendations()
        self._mark_profile_resolved()

    def _mark_profile_resolved(self) -> None:
        if self.profile_is_resolved:
            return
        self.profile_is_resolved = True
        self.profile_resolved.emit()

    def missing_recommended_modules(self) -> list[ModuleInfo]:
        """Recommended-for-this-computer modules that are still not installed."""
        return missing_recommended_modules(self._paths, self._profile)

    _RECOMMENDATION_KEYS = {
        RECOMMENDED: "mod_rec_recommended",
        OPTIONAL: "mod_rec_optional",
        NOT_NEEDED: "mod_rec_not_needed",
    }

    def _recommendation_key(self, mod: ModuleInfo) -> str:
        return self._RECOMMENDATION_KEYS[module_recommendation(mod, self._paths, self._profile)]

    def _recommendation_color(self, key: str) -> str:
        if key == "mod_rec_recommended":
            return "#70D69A"
        if key == "mod_rec_not_needed":
            return "#9FB0C3"
        return "#E5B65A"

    def _set_module_opacity(self, key: str, opacity: float) -> None:
        frame = self._module_frames.get(key)
        if frame is None:
            return
        effect = frame.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(frame)
            frame.setGraphicsEffect(effect)
        effect.setOpacity(opacity)

    def _refresh_module_recommendations(self) -> None:
        for mod in list_modules(self._paths):
            badge = self._module_badges.get(mod.key)
            if badge is None:
                continue
            key = self._recommendation_key(mod)
            badge.setText(self._tr.tr(key))
            badge.setStyleSheet(f"color: {self._recommendation_color(key)}; font-weight: 600;")
            if key == "mod_rec_recommended":
                self._set_module_opacity(mod.key, 1.0)
            elif key == "mod_rec_optional":
                self._set_module_opacity(mod.key, 0.86)
            else:
                self._set_module_opacity(mod.key, 0.55)

    # --- capability matrix --------------------------------------------------
    def _build_capability_matrix(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")
        root = QVBoxLayout(frame)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        title = QLabel(self._tr.tr("capabilities_title"))
        title.setProperty("role", "card-title")
        root.addWidget(title)

        for cap in list_capabilities(self._paths):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)

            texts = QVBoxLayout()
            texts.setSpacing(1)
            name = QLabel(self._tr.tr(cap.name_key))
            desc = QLabel(self._tr.tr(cap.desc_key))
            desc.setProperty("role", "muted")
            desc.setWordWrap(True)
            texts.addWidget(name)
            texts.addWidget(desc)
            row.addLayout(texts, 1)

            detail = QLabel("")
            detail.setProperty("role", "muted")
            detail.setWordWrap(True)
            row.addWidget(detail, 1)

            state = QLabel("")
            state.setMinimumWidth(96)
            state.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(state)
            root.addLayout(row)
            self._cap_texts[cap.key] = (name, desc)
            self._cap_rows[cap.key] = (detail, state)
        return frame

    def _build_mic_check_row(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")
        row = QHBoxLayout(frame)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(12)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        self._mic_check_name = QLabel(self._tr.tr("mic_check_title"))
        self._mic_check_name.setProperty("role", "card-title")
        self._mic_check_desc = QLabel(self._tr.tr("mic_check_desc"))
        self._mic_check_desc.setProperty("role", "muted")
        self._mic_check_desc.setWordWrap(True)
        texts.addWidget(self._mic_check_name)
        texts.addWidget(self._mic_check_desc)
        row.addLayout(texts, 1)

        self._mic_check_status = QLabel(self._tr.tr("mic_check_idle"))
        self._mic_check_status.setProperty("role", "muted")
        self._mic_check_status.setWordWrap(True)
        row.addWidget(self._mic_check_status, 1)

        self.btn_mic_check = QPushButton(self._tr.tr("mic_check_button"))
        self.btn_mic_check.setIcon(icon("mic", "#E6EDF5"))
        self.btn_mic_check.setFixedWidth(_ACTION_BUTTON_WIDTH)
        self.btn_mic_check.clicked.connect(self._run_mic_check)
        row.addWidget(self.btn_mic_check)
        return frame

    def _state_label(self, state: str) -> str:
        if state == STATE_READY:
            return self._tr.tr("cap_ready")
        if state == STATE_PARTIAL:
            return self._tr.tr("cap_partial")
        return self._tr.tr("cap_missing")

    def _state_color(self, state: str) -> str:
        if state == STATE_READY:
            return "#70D69A"
        if state == STATE_PARTIAL:
            return "#E5B65A"
        return "#E36B6B"

    def _cap_detail(self, cap) -> str:
        parts: list[str] = []
        for check in cap.checks:
            prefix = "✓" if check.ok else "○"
            parts.append(f"{prefix} {self._tr.tr(check.label_key)}")
        return " · ".join(parts)

    def _refresh_capabilities(self) -> None:
        for cap in list_capabilities(self._paths):
            if cap.key not in self._cap_rows:
                continue
            detail, state = self._cap_rows[cap.key]
            detail.setText(self._cap_detail(cap))
            state.setText(self._state_label(cap.state))
            state.setStyleSheet(f"color: {self._state_color(cap.state)}; font-weight: 600;")

    # --- API key row ---------------------------------------------------------
    def _build_key_row(
        self,
        provider: str,
        name_key: str,
        desc_key: str,
        prompt_key: str,
        saved_key: str,
    ) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")
        row = QHBoxLayout(frame)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(12)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        name = QLabel(self._tr.tr(name_key))
        name.setProperty("role", "card-title")
        desc = QLabel(self._tr.tr(desc_key))
        desc.setProperty("role", "muted")
        desc.setWordWrap(True)
        texts.addWidget(name)
        texts.addWidget(desc)
        row.addLayout(texts, 1)
        setattr(self, f"_{provider}_key_name", name)
        setattr(self, f"_{provider}_key_desc", desc)

        status = QLabel("")
        status.setProperty("role", "muted")
        row.addWidget(status)
        setattr(self, f"_{provider}_key_status", status)

        btn = QPushButton(self._tr.tr("api_key_enter"))
        btn.setIcon(icon("download", "#E6EDF5"))
        btn.setFixedWidth(_ACTION_BUTTON_WIDTH)
        btn.clicked.connect(lambda: self._enter_api_key(provider, name_key, prompt_key, saved_key))
        row.addWidget(btn)
        setattr(self, f"_{provider}_key_btn", btn)
        return frame

    def _refresh_key_status(self, provider: str) -> None:
        status = getattr(self, f"_{provider}_key_status")
        key = read_api_key(self._paths, provider)
        if key:
            status.setText(f'{self._tr.tr("api_key_set")} ({mask_api_key(key)})')
        else:
            status.setText(self._tr.tr("api_key_missing"))

    def _enter_api_key(self, provider: str, name_key: str, prompt_key: str, saved_key: str) -> None:
        text, ok = QInputDialog.getText(
            self,
            self._tr.tr(name_key),
            self._tr.tr(prompt_key),
            QLineEdit.Password,
            "",
        )
        if not ok:
            return
        key = text.strip()
        if not key:
            return
        write_api_key(self._paths, provider, key)
        self._log(self._tr.tr(saved_key))
        self._refresh_key_status(provider)

    # --- Notion key row ------------------------------------------------------
    def _build_notion_key_row(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")
        row = QHBoxLayout(frame)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(12)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        name = QLabel(self._tr.tr("notion_key_name"))
        name.setProperty("role", "card-title")
        desc = QLabel(self._tr.tr("notion_key_desc"))
        desc.setProperty("role", "muted")
        desc.setWordWrap(True)
        texts.addWidget(name)
        texts.addWidget(desc)
        row.addLayout(texts, 1)
        self._notion_key_name = name
        self._notion_key_desc = desc

        self._notion_key_status = QLabel("")
        self._notion_key_status.setProperty("role", "muted")
        row.addWidget(self._notion_key_status)

        btn = QPushButton(self._tr.tr("api_key_enter"))
        btn.setIcon(icon("download", "#E6EDF5"))
        btn.setFixedWidth(_ACTION_BUTTON_WIDTH)
        btn.clicked.connect(self._enter_notion_key)
        row.addWidget(btn)
        self._notion_key_btn = btn
        return frame

    def _refresh_notion_key_status(self) -> None:
        key = read_api_key(self._paths, "notion")
        if key:
            self._notion_key_status.setText(f'{self._tr.tr("api_key_set")} ({mask_api_key(key)})')
        else:
            self._notion_key_status.setText(self._tr.tr("api_key_missing"))

    def _enter_notion_key(self) -> None:
        text, ok = QInputDialog.getText(
            self,
            self._tr.tr("notion_key_name"),
            self._tr.tr("conn_notion_key_prompt"),
            QLineEdit.Password,
            "",
        )
        if not ok:
            return
        key = text.strip()
        if not key:
            return
        write_api_key(self._paths, "notion", key)
        self._log(self._tr.tr("notion_key_saved"))
        self._refresh_notion_key_status()

    # --- rows ----------------------------------------------------------------
    def _build_progress_frame(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")
        root = QVBoxLayout(frame)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        self._progress_title = QLabel(self._tr.tr("install_progress_idle"))
        self._progress_title.setProperty("role", "card-title")
        root.addWidget(self._progress_title)

        self._progress_bar = QProgressBar()
        self._progress_bar.setObjectName("InstallProgress")
        self._progress_bar.setRange(0, 1000)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        root.addWidget(self._progress_bar)

        self._progress_detail = QLabel("")
        self._progress_detail.setProperty("role", "muted")
        self._progress_detail.setWordWrap(True)
        root.addWidget(self._progress_detail)
        frame.setVisible(False)
        return frame

    def _build_row(self, mod: ModuleInfo) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")  # reuse the flat-card styling per module
        self._module_frames[mod.key] = frame
        row = QHBoxLayout(frame)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(12)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        name = QLabel(self._tr.tr(mod.name_key))
        name.setProperty("role", "card-title")
        desc = QLabel(self._tr.tr(mod.desc_key))
        desc.setProperty("role", "muted")
        desc.setWordWrap(True)
        texts.addWidget(name)
        texts.addWidget(desc)
        row.addLayout(texts, 1)
        self._module_texts[mod.key] = (name, desc)

        status = QLabel("")
        status.setProperty("role", "muted")
        row.addWidget(status)

        badge = QLabel("")
        badge.setMinimumWidth(116)
        badge.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(badge)
        self._module_badges[mod.key] = badge

        btn = QPushButton()
        btn.setIcon(icon("download", "#E6EDF5"))
        btn.setFixedWidth(_ACTION_BUTTON_WIDTH)
        btn.clicked.connect(lambda: self._install(mod))
        row.addWidget(btn)

        self._rows[mod.key] = (status, btn)
        return frame

    def retranslate(self, tr: Translator) -> None:
        self._tr = tr
        self._profile_title.setText(tr.tr("install_profile_title"))
        for cap in list_capabilities(self._paths):
            texts = self._cap_texts.get(cap.key)
            if texts is not None:
                name, desc = texts
                name.setText(tr.tr(cap.name_key))
                desc.setText(tr.tr(cap.desc_key))
        for provider, name_key, desc_key in (
            ("openai", "api_key_name", "api_key_desc"),
            ("xai", "xai_key_name", "xai_key_desc"),
            ("elevenlabs", "elevenlabs_key_name", "elevenlabs_key_desc"),
        ):
            getattr(self, f"_{provider}_key_name").setText(tr.tr(name_key))
            getattr(self, f"_{provider}_key_desc").setText(tr.tr(desc_key))
            getattr(self, f"_{provider}_key_btn").setText(tr.tr("api_key_enter"))
        self._notion_key_name.setText(tr.tr("notion_key_name"))
        self._notion_key_desc.setText(tr.tr("notion_key_desc"))
        self._notion_key_btn.setText(tr.tr("api_key_enter"))
        self._mic_check_name.setText(tr.tr("mic_check_title"))
        self._mic_check_desc.setText(tr.tr("mic_check_desc"))
        self.btn_mic_check.setText(tr.tr("mic_check_button"))
        if self._mic_check_status_key:
            self._mic_check_status.setText(tr.tr(self._mic_check_status_key))
        for mod in list_modules(self._paths):
            texts = self._module_texts.get(mod.key)
            if texts is not None:
                name, desc = texts
                name.setText(tr.tr(mod.name_key))
                desc.setText(tr.tr(mod.desc_key))
        if not self._progress_frame.isVisible():
            self._progress_title.setText(tr.tr("install_progress_idle"))
        if self.log_view is not None:
            self.log_view.setPlaceholderText(tr.tr("modules_log_hint"))
        self._refresh_all()
        seed_tooltips(self)

    def _refresh_all(self) -> None:
        # Pick up packages installed into the running runtime this session.
        importlib.invalidate_caches()
        self._refresh_install_profile()
        self._refresh_capabilities()
        self._refresh_module_recommendations()
        for provider in ("openai", "xai", "elevenlabs"):
            self._refresh_key_status(provider)
        self._refresh_notion_key_status()
        for mod in list_modules(self._paths):
            status, btn = self._rows[mod.key]
            installed = mod.is_installed(self._paths)
            status.setText(self._tr.tr("mod_installed" if installed else "mod_not_installed"))
            btn.setText(self._tr.tr(mod.reinstall_key if installed else mod.install_key))
        seed_tooltips(self)

    def is_busy(self) -> bool:
        return bool(
            (self._worker and self._worker.isRunning())
            or (self._mic_worker and self._mic_worker.isRunning())
        )

    def _set_busy(self, busy: bool) -> None:
        for _, btn in self._rows.values():
            btn.setEnabled(not busy)
        self.btn_mic_check.setEnabled(not busy)

    def _reset_progress(self, name: str) -> None:
        self._progress_frame.setVisible(True)
        self._progress_title.setText(self._tr.tr("install_progress_running").replace("{name}", name))
        self._progress_detail.setText(self._tr.tr("install_progress_waiting"))
        self._progress_bar.setValue(0)

    def _on_progress(self, progress) -> None:
        value = int(max(0.0, min(100.0, progress.percent)) * 10)
        self._progress_bar.setValue(value)
        label = f"{progress.label}: " if progress.label else ""
        pieces = [f"{label}{progress.done_text} / {progress.total_text}"]
        if progress.speed_text:
            pieces.append(progress.speed_text)
        if progress.eta_text:
            pieces.append(f"ETA {progress.eta_text}")
        self._progress_detail.setText(" · ".join(pieces))

    # --- install -------------------------------------------------------------
    def _install(self, mod: ModuleInfo) -> None:
        if self.is_busy():
            return
        script = mod.script_path(self._paths)
        if not script.exists():
            self._log(f"missing installer: {script}")
            return
        self._set_busy(True)
        name = self._tr.tr(mod.name_key)
        self._reset_progress(name)
        self._log("")
        self._log(self._tr.tr("mod_installing").replace("{name}", name))
        self._worker = InstallWorker(self._paths, script)
        self._worker.log.connect(self._log)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(lambda code, m=mod: self._on_done(m, code))
        self._worker.start()

    def _on_done(self, mod: ModuleInfo, code: int) -> None:
        name = self._tr.tr(mod.name_key)
        key = "mod_done_ok" if code == 0 else "mod_done_fail"
        self._log(self._tr.tr(key).replace("{name}", name).replace("{code}", str(code)))
        if code == 0:
            self._progress_bar.setValue(1000)
            self._progress_detail.setText(self._tr.tr("install_progress_done"))
        self._set_busy(False)
        self._refresh_all()
        self.module_installed.emit(mod.key, code)
        if self._queue_total:
            # Count by the real post-install check, not the exit code alone.
            if code == 0 and mod.is_installed(self._paths):
                self._queue_ok += 1
            if self._queue:
                # Let the finished worker unwind before the next one starts.
                QTimer.singleShot(0, self._run_next_queued)
            else:
                ok, total = self._queue_ok, self._queue_total
                self._queue_total = 0
                self._queue_ok = 0
                self.queue_finished.emit(ok, total)

    # --- install queue (first-run prompt) ----------------------------------
    def install_queue(self, keys: list[str]) -> bool:
        """Install several modules one after another, in catalog order.

        Returns False when nothing was queued (busy, or no known keys)."""
        if self.is_busy():
            return False
        wanted = set(keys)
        queue = [mod for mod in list_modules(self._paths) if mod.key in wanted]
        if not queue:
            return False
        self._queue = queue
        self._queue_total = len(queue)
        self._queue_ok = 0
        self._run_next_queued()
        return True

    def _run_next_queued(self) -> None:
        if not self._queue:
            return
        mod = self._queue.pop(0)
        self._install(mod)
        if self._worker is None or not self._worker.isRunning():
            # Installer missing or refused to start: move on to the next one.
            if self._queue:
                QTimer.singleShot(0, self._run_next_queued)
            else:
                ok, total = self._queue_ok, self._queue_total
                self._queue_total = 0
                self._queue_ok = 0
                self.queue_finished.emit(ok, total)

    # --- microphone check ---------------------------------------------------
    def _run_mic_check(self) -> None:
        if self.is_busy():
            return
        self._set_busy(True)
        self._mic_check_status_key = "mic_check_running"
        self._mic_check_status.setText(self._tr.tr("mic_check_running"))
        self._mic_check_status.setStyleSheet("")
        self._log("")
        self._log(self._tr.tr("mic_check_running"))
        worker = MicrophoneCheckWorker(self._paths)
        self._mic_worker = worker
        worker.done.connect(self._on_mic_check_done)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: setattr(self, "_mic_worker", None))
        worker.start()

    def _rate_detail(self, result) -> str:
        parts: list[str] = []
        for check in result.rate_checks:
            if check.ok:
                route = ""
                if check.input_sample_rate and check.input_sample_rate != check.sample_rate:
                    route = f"; input {check.input_sample_rate}→{check.sample_rate} Hz"
                host = f"; {check.host_api}" if check.host_api else ""
                channels = ""
                if check.input_channels > 1:
                    peaks = "/".join(str(value) for value in check.channel_peaks)
                    channels = f"; {check.input_channels} ch peak {peaks}"
                parts.append(
                    f"{check.sample_rate} Hz OK "
                    f"({check.bytes_captured} bytes{route}{channels}{host})"
                )
            else:
                parts.append(f"{check.sample_rate} Hz FAIL ({check.error})")
        return " · ".join(parts)

    def _on_mic_check_done(self, result) -> None:
        self._set_busy(False)
        self._mic_check_status_key = ""
        if not result.sounddevice_available:
            message = self._tr.tr("mic_check_missing_sounddevice")
            color = "#E36B6B"
        elif result.device_count == 0:
            message = self._tr.tr("mic_check_no_devices")
            color = "#E36B6B"
        else:
            device = result.default_input or result.input_devices[0]
            details = self._rate_detail(result)
            if result.ready:
                message = (
                    self._tr.tr("mic_check_ready")
                    .replace("{count}", str(result.device_count))
                    .replace("{device}", device)
                    .replace("{details}", details)
                )
                color = "#70D69A"
            elif result.partial:
                message = (
                    self._tr.tr("mic_check_partial")
                    .replace("{count}", str(result.device_count))
                    .replace("{device}", device)
                    .replace("{details}", details)
                )
                color = "#E5B65A"
            else:
                first_error = next((check.error for check in result.rate_checks if check.error), result.error)
                message = self._tr.tr("mic_check_failed").replace("{error}", first_error or result.error)
                color = "#E36B6B"
        self._mic_check_status.setText(message)
        self._mic_check_status.setStyleSheet(f"color: {color}; font-weight: 600;")
        self._log(message)

    def _log(self, line: str) -> None:
        if self.log_view is not None:
            self.log_view.appendPlainText(line)
        self.log_line.emit(line)


class ModulesDialog(QDialog):
    """Topbar shortcut: the same panel, framed as a modal with a Close button."""

    def __init__(self, paths: ProjectPaths, tr: Translator, parent=None):
        super().__init__(parent)
        self._tr = tr
        self.setWindowTitle(tr.tr("modules_title"))
        self.setMinimumWidth(600)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        head = QLabel(tr.tr("modules_title"))
        head.setProperty("role", "heading")
        root.addWidget(head)

        self._panel = ModulesPanel(paths, tr, embedded_log=True)
        root.addWidget(self._panel)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.btn_close = QPushButton(tr.tr("close"))
        self.btn_close.clicked.connect(self.reject)
        bottom.addWidget(self.btn_close)
        root.addLayout(bottom)
        seed_tooltips(self)

    # Exposed for tests (the rows live on the panel).
    @property
    def _rows(self):
        return self._panel._rows

    # Block closing mid-install (the worker would outlive the dialog).
    def reject(self) -> None:  # noqa: D401
        if self._panel.is_busy():
            return
        super().reject()
