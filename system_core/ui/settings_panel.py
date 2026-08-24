"""The right-hand settings form. Binds the merged settings dict to widgets.

Reads initial values from the merged settings, writes edits straight back into a
held copy, and exposes `values()` for saving. Keeps references to translatable
labels so a language switch re-labels the whole form without a rebuild.

Layout: compact tabbed pages of "cards". Each card carries its title as a real
header label (with a thin rule below) instead of a QGroupBox border-title, which
sidesteps the title/border overlap entirely. Frequent settings stay visible;
rarely-touched subtitle/pipeline knobs live under a collapsible "Advanced".
"""

from __future__ import annotations

import copy
from typing import Any

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.credentials import mask_api_key, read_api_key, write_api_key
from ..core.editions import (
    EDITION_LIVE,
    EDITION_STUDIO,
    MODE_API,
    MODE_CUDA,
    MODE_VULKAN,
    compute_mode_label_key,
    current_edition,
    display_compute_mode,
    whispercpp_cuda_ready,
    whispercpp_runtime_ready,
    visible_compute_modes,
)
from ..core.model_assets import whispercpp_model_assets
from ..core.paths import ProjectPaths
from ..core.prompt_store import ROLE_CLEANUP, PromptStore
from ..core.terms import shared_keyterms
from ..live.config import LiveConfig, fixed_live_api_model
from .i18n import Translator
from .icons import icon
from .modules_dialog import ModulesPanel
from .prompt_dialog import PromptLibraryDialog
from .state_io import load_tab_order, save_tab_order
from .tooltips import seed_tooltips
from .widgets import (
    Card,
    CurrentPageStackedWidget,
    InlineDoubleSpinBox,
    InlineSpinBox,
    UnderlineTabBar,
)
from .workers import LocalHardwareWorker

_TRANSCRIPTION_LANGUAGES = ["auto", "ru", "en"]
_LIVE_LANGUAGES = ["layout", "auto", "ru", "en"]
_FILE_STT_PROVIDERS = [
    ("xai", "file_provider_xai"),
    ("openai", "file_provider_openai"),
    ("gemini", "file_provider_gemini"),
    ("gigachat", "file_provider_gigachat"),
    ("assemblyai", "file_provider_assemblyai"),
]
_FILE_STT_MODEL_KEYS = {
    "openai": "model",
    "xai": "xai_model",
    "gemini": "gemini_model",
    "gigachat": "gigachat_model",
}
_STATIC_FILE_STT_MODELS = {
    "xai": ["grok-transcribe"],
    "gemini": ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.5-pro"],
    "gigachat": ["GigaChat", "GigaChat-Pro", "GigaChat-Max"],
    "assemblyai": ["assemblyai"],
}
_OPENAI_FILE_PROFILES = [
    ("openai_profile_fast", "gpt-4o-mini-transcribe", "openai_profile_fast_tip"),
    ("openai_profile_accurate", "gpt-4o-transcribe", "openai_profile_accurate_tip"),
    ("openai_profile_diarize", "gpt-4o-transcribe-diarize", "openai_profile_diarize_tip"),
]
_LOCAL_ENGINES = [
    ("gigaam", "local_engine_gigaam"),
    ("whispercpp", "local_engine_whispercpp"),
]
_LIVE_SOURCES = [
    ("api", "live_source_api"),
    ("local", "live_source_local"),
]
_LIVE_API_PROVIDERS = [
    ("xai", "live_api_provider_xai"),
    ("openai", "live_api_provider_openai"),
    ("elevenlabs", "live_api_provider_elevenlabs"),
]
_OPENAI_LIVE_MODES = [
    ("batch", "live_api_mode_batch"),
    ("realtime", "live_api_mode_realtime"),
]
_GIGAAM_LIVE_MODELS = [
    ("local_model_gigaam_v3_ctc", "gigaam-v3-e2e-ctc"),
    ("local_model_gigaam_v3_rnnt", "gigaam-v3-e2e-rnnt"),
]
_GIGAAM_FILE_MODELS = [
    ("local_model_gigaam_v3_rnnt", "gigaam-v3-e2e-rnnt"),
    ("local_model_gigaam_v3_ctc", "gigaam-v3-e2e-ctc"),
]
_GIGAAM_BACKENDS = [
    ("local_backend_auto", "auto"),
    ("local_backend_cuda", "cuda"),
    ("local_backend_directml", "directml"),
    ("local_backend_cpu", "cpu"),
]
_WHISPERCPP_BACKENDS = [
    ("live_backend_auto", "auto"),
    ("live_backend_cpu", "cpu"),
]
_CUDA_PROFILES = [
    ("quality", "cuda_profile_quality"),
    ("speed", "cuda_profile_speed"),
]
_FORM_LABEL_WIDTH = 144
# Neutral icon tint that reads on both dark themes (close to text-secondary).
_ICON_MUTED = "#9FB0C3"


class SettingsPanel(QWidget):
    refresh_requested = Signal()
    reset_requested = Signal()
    changed = Signal()

    def __init__(self, paths: ProjectPaths, tr: Translator, settings: dict[str, Any],
                 prompt_store: PromptStore, parent=None):
        super().__init__(parent)
        self._paths = paths
        self._tr = tr
        self._settings = copy.deepcopy(settings)
        self._prompts = prompt_store
        self._labels: dict[str, QLabel] = {}      # i18n key -> label widget
        self._boxes: dict[str, Card] = {}          # i18n key -> card
        self._icon_muted = _ICON_MUTED
        self._model_source = ""
        self._openai_stt_models: list[str] = []
        self._active_file_stt_provider = "openai"
        self._local_hw_profile = None
        self._local_hw_progress: tuple[int, int, str] | None = None
        self._local_hw_worker: LocalHardwareWorker | None = None
        self._loading = True
        self._compute_modes = visible_compute_modes(paths, self._settings)
        self._compute_buttons: dict[str, QPushButton] = {}
        self._live_source_buttons: dict[str, QPushButton] = {}
        self._live_api_provider_buttons: dict[str, QPushButton] = {}
        self._live_api_mode_buttons: dict[str, QPushButton] = {}
        self._live_local_engine_buttons: dict[str, QPushButton] = {}
        self._file_local_engine_buttons: dict[str, QPushButton] = {}
        self._cuda_profile_buttons: dict[str, QPushButton] = {}
        self._section_buttons: dict[str, QPushButton] = {}
        self._hotword_edits: list[QLineEdit] = []
        self._scroll_viewport: QWidget | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 8)
        root.setSpacing(6)

        default_sections = self._default_section_order()
        self._section_keys = load_tab_order(paths, "settings_sections", default_sections)
        self._section_tabs = UnderlineTabBar(
            [(key, self._tr.tr(key)) for key in self._section_keys],
            self,
            fill_width=True,
        )
        self._section_tabs.currentChanged.connect(self._set_section)
        self._section_tabs.orderChanged.connect(self._save_section_order)
        self._section_buttons = self._section_tabs.buttons()
        root.addWidget(self._section_tabs)

        self._section_stack = CurrentPageStackedWidget()
        self._section_pages: dict[str, QWidget] = {}
        root.addWidget(self._section_stack, 1)

        live_page, live_left, _ = self._section_page(columns=1)
        live_left.addWidget(self._build_live_group())
        self._live_openai_card = self._build_live_openai_group()
        live_left.addWidget(self._live_openai_card)
        self._live_vulkan_card = self._build_live_vulkan_group()
        live_left.addWidget(self._live_vulkan_card)
        live_left.addWidget(self._build_live_overlay_group())
        live_left.addWidget(self._build_live_cleanup_group())
        live_left.addStretch(1)
        self._section_pages["settings_section_live"] = live_page
        self._section_stack.addWidget(live_page)

        file_page, file_left, _ = self._section_page(columns=1)
        file_left.addWidget(self._build_compute_group())
        self._file_openai_card = self._build_models_group()
        file_left.addWidget(self._file_openai_card)
        self._file_vulkan_card = self._build_file_vulkan_group()
        file_left.addWidget(self._file_vulkan_card)
        if current_edition(self._paths, self._settings) == EDITION_STUDIO:
            self._file_cuda_card = self._build_cuda_group()
            file_left.addWidget(self._file_cuda_card)
        else:
            self._file_cuda_card = None
        file_left.addWidget(self._build_pipeline_group())
        file_left.addWidget(self._build_postprocess_group())
        file_left.addWidget(self._build_cleanup_group())
        file_left.addWidget(self._build_subtitles_group())
        file_left.addWidget(self._build_exports_group())
        file_left.addStretch(1)
        self._section_pages["settings_section_file_ops"] = file_page
        self._section_stack.addWidget(file_page)

        settings_page, settings_left, _ = self._section_page(columns=1)
        settings_left.addWidget(self._build_app_behavior_group())
        settings_left.addWidget(self._build_connectors_group())
        settings_left.addWidget(self._build_reset_group())
        settings_left.addStretch(1)
        self._section_pages["settings_section_settings"] = settings_page
        self._section_stack.addWidget(settings_page)

        modules_page, modules_left, _ = self._section_page(columns=1)
        self.modules_panel = ModulesPanel(self._paths, self._tr, self)
        self.modules_panel.module_installed.connect(
            lambda _key, _rc: self._refresh_local_availability()
        )
        modules_left.addWidget(self.modules_panel)
        modules_left.addStretch(1)
        self._section_pages["settings_section_modules"] = modules_page
        self._section_stack.addWidget(modules_page)

        self._section_tabs.set_current_index(0)
        self._section_stack.setCurrentWidget(self._section_pages[self._section_keys[0]])
        root.addStretch(1)

        self._install_combo_wheel_guards()
        self._load_into_widgets()
        seed_tooltips(self)
        QTimer.singleShot(0, self._sync_scroll_area_size)

    def apply_theme_tokens(self, tokens: dict[str, str]) -> None:
        self._icon_muted = tokens.get(
            "color-icon-secondary",
            tokens.get("color-text-secondary", _ICON_MUTED),
        )
        self._apply_theme_icons()

    def sizeHint(self):  # noqa: N802
        layout = self.layout()
        return layout.sizeHint() if layout is not None else super().sizeHint()

    def minimumSizeHint(self):  # noqa: N802
        layout = self.layout()
        return layout.minimumSize() if layout is not None else super().minimumSizeHint()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_scroll_area_size)

    def event(self, event) -> bool:
        handled = super().event(event)
        if event.type() == QEvent.LayoutRequest:
            QTimer.singleShot(0, self._sync_scroll_area_size)
        return handled

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._scroll_viewport and event.type() == QEvent.Resize:
            QTimer.singleShot(0, self._sync_scroll_area_size)
        return super().eventFilter(watched, event)

    def _apply_theme_icons(self) -> None:
        if hasattr(self, "btn_refresh"):
            self.btn_refresh.setIcon(icon("refresh", self._icon_muted))

    # --- helpers -------------------------------------------------------------
    def _default_section_order(self) -> list[str]:
        if current_edition(self._paths, self._settings) == EDITION_LIVE:
            return [
                "settings_section_live",
                "settings_section_file_ops",
                "settings_section_settings",
                "settings_section_modules",
            ]
        return [
            "settings_section_live",
            "settings_section_file_ops",
            "settings_section_settings",
            "settings_section_modules",
        ]

    def _section_page(self, *, columns: int = 2):
        page = QWidget()
        page.setObjectName("SettingsSectionPage")
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        left = QVBoxLayout()
        left.setSpacing(8)
        layout.addLayout(left, 1)
        if columns == 1:
            return page, left, left
        right = QVBoxLayout()
        right.setSpacing(8)
        layout.addLayout(right, 1)
        return page, left, right

    def _set_section(self, index: int) -> None:
        keys = self._section_tabs.keys()
        if 0 <= index < len(keys):
            self._section_stack.setCurrentWidget(self._section_pages[keys[index]])
            layout = self.layout()
            if layout is not None:
                layout.invalidate()
            self.updateGeometry()
            self._sync_scroll_area_size()
            QTimer.singleShot(0, self._sync_scroll_area_size)

    def show_section(self, key: str) -> None:
        """Select a settings page from an external quick action."""
        keys = self._section_tabs.keys()
        if key not in keys:
            return
        index = keys.index(key)
        self._section_tabs.set_current_key(key)
        self._set_section(index)

    def _save_section_order(self, keys: list[str]) -> None:
        self._section_keys = list(keys)
        save_tab_order(self._paths, "settings_sections", self._section_keys)

    def _sync_scroll_area_size(self) -> None:
        scroll = self.parentWidget()
        while scroll is not None and not isinstance(scroll, QScrollArea):
            scroll = scroll.parentWidget()
        if scroll is None:
            return
        viewport = scroll.viewport()
        if self._scroll_viewport is not viewport:
            if self._scroll_viewport is not None:
                self._scroll_viewport.removeEventFilter(self)
            self._scroll_viewport = viewport
            viewport.installEventFilter(self)
        target_width = max(viewport.width(), self.minimumSizeHint().width())
        target_height = max(viewport.height(), self.sizeHint().height())
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        if self.width() != target_width or self.height() != target_height:
            self.resize(target_width, target_height)
        self.setMaximumHeight(target_height)
        bar = scroll.verticalScrollBar()
        if bar.value() > bar.maximum():
            bar.setValue(bar.maximum())

    def _install_combo_wheel_guards(self) -> None:
        for combo in self.findChildren(QComboBox):
            combo.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802
        if isinstance(watched, QComboBox) and event.type() == QEvent.Wheel:
            event.ignore()
            return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event):  # noqa: N802
        self.stop_background_tasks()
        super().closeEvent(event)

    def stop_background_tasks(self) -> None:
        worker = self._local_hw_worker
        if worker is not None and worker.isRunning():
            worker.cancel()
            # GPU providers are loaded only in the worker's child process, so
            # cancelling detection cannot corrupt the Qt process during exit.
            worker.wait()

    def _apply_compute_visibility(self) -> None:
        mode = self._checked_compute_mode()
        if hasattr(self, "_file_openai_card"):
            self._file_openai_card.setVisible(mode == MODE_API)
        if hasattr(self, "_file_vulkan_card"):
            self._file_vulkan_card.setVisible(mode == MODE_VULKAN)
        if self._file_cuda_card is not None:
            self._file_cuda_card.setVisible(mode == MODE_CUDA)

    def _set_card_dimmed(self, card: Card, dimmed: bool) -> None:
        effect = card.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(card)
            card.setGraphicsEffect(effect)
        effect.setOpacity(0.68 if dimmed else 1.0)
        card.body().setEnabled(not dimmed)

    def _apply_live_engine_visibility(self) -> None:
        local_active = self._checked_live_source() == "local"
        self._set_card_dimmed(self._live_openai_card, local_active)
        self._set_card_dimmed(self._live_vulkan_card, not local_active)
        if hasattr(self, "_live_api_mode_widget"):
            openai_active = self._checked_live_api_provider() == "openai"
            self._live_api_mode_widget.setVisible(openai_active)
            self._live_api_mode_label.setVisible(openai_active)

    def _group(self, key: str, *, checkable: bool = False) -> Card:
        card = Card(self._tr.tr(key), checkable=checkable)
        card.layout().setContentsMargins(10, 8, 10, 10)
        card.layout().setSpacing(7)
        self._boxes[key] = card
        return card

    def _label(self, key: str) -> QLabel:
        label = QLabel(self._tr.tr(key))
        label.setWordWrap(True)
        label.setMinimumWidth(_FORM_LABEL_WIDTH)
        label.setMaximumWidth(_FORM_LABEL_WIDTH)
        label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._labels[key] = label
        return label

    def _form(self, parent: QWidget) -> QFormLayout:
        """A form layout with labels vertically centered against their fields and
        consistent spacing — prevents label/field baseline mismatch."""
        form = QFormLayout(parent)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.setContentsMargins(0, 0, 0, 0)
        return form

    def _checks_grid(self, parent: QWidget, checks: list[QCheckBox], cols: int = 2) -> None:
        """Lay checkboxes out across a grid (not a single column) so they use the
        card's full width."""
        grid = QGridLayout(parent)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        for i, chk in enumerate(checks):
            chk.toggled.connect(self._emit_changed)
            grid.addWidget(chk, i // cols, i % cols)
        for col in range(cols):
            grid.setColumnStretch(col, 1)

    def _checks_strip(self, checks: list[QCheckBox]) -> QWidget:
        """Keep a short related option set on one calm, borderless row."""
        strip = QWidget()
        strip.setObjectName("OptionStrip")
        strip.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(strip)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        for check in checks:
            check.toggled.connect(self._emit_changed)
            check.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            row.addWidget(check, 0, Qt.AlignLeft | Qt.AlignVCenter)
        return strip

    def _editable_combo(self) -> QComboBox:
        """An editable model picker that won't widen its column to fit long model
        names — it elides/scrolls internally instead."""
        combo = QComboBox()
        combo.setEditable(True)
        combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(6)
        return combo

    def _segment_button(self, text: str) -> QPushButton:
        button = QPushButton(text)
        button.setCheckable(True)
        button.setProperty("variant", "segment")
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return button

    def _emit_changed(self, *_args) -> None:
        if not getattr(self, "_loading", False):
            self.changed.emit()

    def _checked_compute_mode(self) -> str:
        for mode, button in self._compute_buttons.items():
            if button.isChecked():
                return mode
        return self._compute_modes[0] if self._compute_modes else MODE_API

    def _file_provider_items(self) -> list[tuple[str, str]]:
        return [(self._tr.tr(label_key), provider) for provider, label_key in _FILE_STT_PROVIDERS]

    def _checked_file_provider(self) -> str:
        if not hasattr(self, "cmb_stt_provider"):
            return "openai"
        return str(self.cmb_stt_provider.currentData() or "openai")

    def _file_provider_from_settings(self) -> str:
        provider = str(self._g("stt", "provider", default="") or "").strip().lower()
        if not provider and bool(self._g("assemblyai", "enabled", default=False)):
            provider = "assemblyai"
        aliases = {"grok": "xai", "x.ai": "xai", "giga": "gigachat", "sber": "gigachat"}
        provider = aliases.get(provider, provider or "openai")
        known = {value for value, _ in _FILE_STT_PROVIDERS}
        return provider if provider in known else "openai"

    def _file_model_key(self, provider: str) -> str | None:
        return _FILE_STT_MODEL_KEYS.get(provider)

    def _file_model_value(self, provider: str) -> str:
        key = self._file_model_key(provider)
        if not key:
            return provider
        defaults = {
            "model": "gpt-4o-transcribe",
            "xai_model": "grok-transcribe",
            "gemini_model": "gemini-3.5-flash",
            "gigachat_model": "GigaChat",
        }
        return str(self._g("stt", key, default=defaults.get(key, "")) or defaults.get(key, ""))

    def _openai_file_profile_items(self) -> list[tuple[str, str, str]]:
        return [(self._tr.tr(label_key), model, self._tr.tr(tip_key)) for label_key, model, tip_key in _OPENAI_FILE_PROFILES]

    def _file_model_items(self, provider: str) -> list[str]:
        return list(_STATIC_FILE_STT_MODELS.get(provider, [self._file_model_value(provider)]))

    def _store_current_file_model(self) -> None:
        if not hasattr(self, "cmb_stt"):
            return
        provider = self._active_file_stt_provider or self._checked_file_provider()
        key = self._file_model_key(provider)
        if key:
            self._settings.setdefault("stt", {})[key] = str(
                self.cmb_stt.currentData() or self.cmb_stt.currentText()
            ).strip()

    def _sync_file_model_combo(self, provider: str | None = None) -> None:
        if not hasattr(self, "cmb_stt"):
            return
        provider = provider or self._checked_file_provider()
        self._active_file_stt_provider = provider
        if provider == "openai":
            self._set_data_combo_with_tooltips(
                self.cmb_stt,
                self._openai_file_profile_items(),
                self._file_model_value(provider),
            )
        else:
            self._set_combo(self.cmb_stt, self._file_model_items(provider), self._file_model_value(provider))
        fixed_model = provider in {"xai", "assemblyai"}
        self.cmb_stt.setEditable(provider not in {"openai", "xai", "assemblyai"})
        self.cmb_stt.setEnabled(not fixed_model)
        self.btn_refresh.setEnabled(provider == "openai")

    def _on_file_model_changed(self, _index: int) -> None:
        if self._checked_file_provider() == "openai":
            self.cmb_stt.setToolTip(self.cmb_stt.itemData(self.cmb_stt.currentIndex(), Qt.ToolTipRole) or "")
        self._emit_changed()

    def _refresh_local_hw_hint(self) -> None:
        if not hasattr(self, "lbl_local_hw_hint"):
            return
        if self._local_hw_profile is None:
            if self._local_hw_progress is None:
                self.lbl_local_hw_hint.setText(self._tr.tr("local_hw_initializing"))
            else:
                self._set_local_hw_progress_badge(*self._local_hw_progress)
            self._start_local_hw_worker()
            return
        self._set_local_hw_badge(self._local_hw_profile)

    def _start_local_hw_worker(self) -> None:
        if self._local_hw_worker is not None and self._local_hw_worker.isRunning():
            return
        worker = LocalHardwareWorker(self._paths, self)
        worker.progress.connect(self._on_local_hw_progress)
        worker.done.connect(self._on_local_hw_ready)
        worker.failed.connect(self._on_local_hw_failed)
        worker.finished.connect(lambda: self._clear_local_hw_worker(worker))
        worker.finished.connect(worker.deleteLater)
        self._local_hw_worker = worker
        worker.start()

    def _clear_local_hw_worker(self, worker: LocalHardwareWorker) -> None:
        if self._local_hw_worker is worker:
            self._local_hw_worker = None

    def _on_local_hw_progress(self, step: int, total: int, stage_key: str) -> None:
        self._local_hw_progress = (step, total, stage_key)
        self._set_local_hw_progress_badge(step, total, stage_key)

    def _on_local_hw_ready(self, profile) -> None:
        self._local_hw_profile = profile
        self._local_hw_progress = None
        self._set_local_hw_badge(profile)

    def _on_local_hw_failed(self, message: str) -> None:
        if hasattr(self, "lbl_local_hw_hint"):
            self.lbl_local_hw_hint.setText(self._tr.tr("local_hw_failed").format(error=message))

    def _set_local_hw_badge(self, profile) -> None:
        gpu = ", ".join(profile.gpu_names) if profile.gpu_names else self._tr.tr("local_hw_no_gpu")
        stack_key = f"local_stack_{profile.recommended_stack}"
        stack = self._tr.tr(stack_key)
        if stack == stack_key:
            stack = profile.recommended_stack
        self.lbl_local_hw_hint.setText(self._tr.tr("local_hw_badge").format(gpu=gpu, stack=stack))

    def _set_local_hw_progress_badge(self, step: int, total: int, stage_key: str) -> None:
        stage = self._tr.tr(stage_key)
        if stage == stage_key:
            stage = stage_key
        self.lbl_local_hw_hint.setText(
            self._tr.tr("local_hw_progress").format(step=step, total=total, stage=stage)
        )

    def _on_compute_toggled(self, _button_id: int, on: bool) -> None:
        if on:
            self._apply_compute_visibility()
            self._emit_changed()

    def _set_combo_item_enabled(self, combo: QComboBox, index: int, enabled: bool) -> None:
        model = combo.model()
        item = model.item(index) if hasattr(model, "item") else None
        if item is not None:
            item.setEnabled(enabled)

    def _refresh_whisper_model_items(self, combo: QComboBox) -> None:
        for index in range(combo.count()):
            model = str(combo.itemData(index) or combo.itemText(index)).strip()
            ready = whispercpp_runtime_ready(self._paths, model=model, require_server=True)
            self._set_combo_item_enabled(combo, index, ready)
            combo.setItemData(
                index,
                "" if ready else self._tr.tr("local_model_not_installed"),
                Qt.ToolTipRole,
            )

    def _refresh_local_availability(self) -> None:
        for buttons in (self._live_local_engine_buttons, self._file_local_engine_buttons):
            button = buttons.get("whispercpp")
            if button is not None:
                ready = whispercpp_runtime_ready(
                    self._paths, model="turbo", require_server=True
                )
                button.setEnabled(ready)
                button.setToolTip(
                    "" if ready else self._tr.tr("whispercpp_not_installed")
                )
        for name in ("cmb_live_local_model", "cmb_file_vulkan_model", "cmb_cuda_model"):
            combo = getattr(self, name, None)
            if isinstance(combo, QComboBox):
                self._refresh_whisper_model_items(combo)

        cuda_button = self._compute_buttons.get(MODE_CUDA)
        if cuda_button is not None:
            model = "large-v2"
            if hasattr(self, "cmb_cuda_model"):
                model = str(self.cmb_cuda_model.currentData() or self.cmb_cuda_model.currentText() or model)
            ready = whispercpp_cuda_ready(self._paths, model=model, require_server=True)
            cuda_button.setEnabled(ready)
            cuda_button.setToolTip(
                "" if ready else self._tr.tr("cuda_whispercpp_not_installed")
            )
            if cuda_button.isChecked() and not ready:
                api_button = self._compute_buttons.get(MODE_API)
                if api_button is not None:
                    api_button.setChecked(True)

    def _on_file_provider_changed(self, _index: int) -> None:
        if self._loading:
            return
        self._store_current_file_model()
        self._sync_file_model_combo(self._checked_file_provider())
        self._emit_changed()

    def _on_live_source_toggled(self, _button_id: int, on: bool) -> None:
        if on:
            self._apply_live_engine_visibility()
            self._emit_changed()

    def _on_live_api_provider_toggled(self, _button_id: int, on: bool) -> None:
        if on:
            self._apply_live_engine_visibility()
            self._emit_changed()

    def _on_live_api_mode_toggled(self, _button_id: int, on: bool) -> None:
        if on:
            self._emit_changed()

    def _checked_button(self, buttons: dict[str, QPushButton], default: str) -> str:
        for key, button in buttons.items():
            if button.isChecked():
                return key
        return default

    def _set_button(self, buttons: dict[str, QPushButton], key: str, default: str) -> None:
        chosen = key if key in buttons else default
        for item, button in buttons.items():
            button.blockSignals(True)
            button.setChecked(item == chosen)
            button.blockSignals(False)

    def _checked_live_source(self) -> str:
        return self._checked_button(self._live_source_buttons, "api")

    def _checked_live_api_provider(self) -> str:
        return self._checked_button(self._live_api_provider_buttons, "openai")

    def _checked_live_api_mode(self) -> str:
        return self._checked_button(self._live_api_mode_buttons, "batch")

    def _checked_live_engine(self) -> str:
        if self._checked_live_source() == "local":
            return MODE_VULKAN
        provider = self._checked_live_api_provider()
        if provider == "openai":
            return self._checked_live_api_mode()
        return f"{provider}_realtime"

    def _default_local_engine(self) -> str:
        language = str(self._settings.get("ui_language") or self._tr.language or "ru").lower()
        return "gigaam" if language == "ru" else "whispercpp"

    def _local_engine_from_settings(self, scope: str) -> str:
        node = self._g("live", "local", default={}) if scope == "live" else self._g("vulkan", default={})
        value = str(node.get("engine", "") if isinstance(node, dict) else "").strip().lower()
        aliases = {
            "giga": "gigaam",
            "giga-am": "gigaam",
            "giga_am": "gigaam",
            "whisper": "whispercpp",
            "whisper.cpp": "whispercpp",
            "vulkan": "whispercpp",
        }
        value = aliases.get(value, value)
        return value if value in {"gigaam", "whispercpp"} else self._default_local_engine()

    def _checked_local_engine(self, buttons: dict[str, QPushButton]) -> str:
        for engine, button in buttons.items():
            if button.isChecked():
                return engine
        return self._default_local_engine()

    def _set_local_engine(self, buttons: dict[str, QPushButton], engine: str) -> None:
        chosen = engine if engine in buttons else self._default_local_engine()
        for key, button in buttons.items():
            button.blockSignals(True)
            button.setChecked(key == chosen)
            button.blockSignals(False)

    def _local_engine_row(self, buttons: dict[str, QPushButton], group: QButtonGroup) -> QWidget:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        for idx, (engine, label_key) in enumerate(_LOCAL_ENGINES):
            button = self._segment_button(self._tr.tr(label_key))
            group.addButton(button, idx)
            buttons[engine] = button
            row.addWidget(button)
        return self._wrap(row)

    def _local_model_items(self, engine: str, scope: str) -> list[tuple[str, str]]:
        if engine == "gigaam":
            models = _GIGAAM_LIVE_MODELS if scope == "live" else _GIGAAM_FILE_MODELS
            return [(self._tr.tr(label_key), value) for label_key, value in models]
        return self._live_model_items()

    def _local_backend_items(self, engine: str) -> list[tuple[str, str]]:
        source = _GIGAAM_BACKENDS if engine == "gigaam" else _WHISPERCPP_BACKENDS
        return [(self._tr.tr(label_key), value) for label_key, value in source]

    def _local_default_model(self, engine: str, scope: str) -> str:
        if engine == "gigaam":
            return "gigaam-v3-e2e-ctc" if scope == "live" else "gigaam-v3-e2e-rnnt"
        return "turbo"

    def _coerce_local_model(self, engine: str, scope: str, model: str | None) -> str:
        value = str(model or "").strip()
        fallback = self._local_default_model(engine, scope)
        if engine == "gigaam":
            return value if value.startswith("gigaam-") else fallback
        if value.startswith("gigaam-"):
            return fallback
        if value.lower() in {"large-v3", "ggml-large-v3.bin"}:
            return fallback
        return value or fallback

    def _coerce_local_backend(self, engine: str, backend: str | None) -> str:
        value = str(backend or "auto").strip().lower()
        allowed = {data for _label, data in self._local_backend_items(engine)}
        return value if value in allowed else "auto"

    def _sync_live_local_controls(self, model: str | None = None, backend: str | None = None) -> None:
        engine = self._checked_local_engine(self._live_local_engine_buttons)
        self._set_data_combo(
            self.cmb_live_local_model,
            self._local_model_items(engine, "live"),
            self._coerce_local_model(engine, "live", model),
        )
        self._set_data_combo(
            self.cmb_live_local_backend,
            self._local_backend_items(engine),
            self._coerce_local_backend(engine, backend),
        )
        if engine == "whispercpp":
            self._refresh_whisper_model_items(self.cmb_live_local_model)

    def _sync_file_local_controls(self, model: str | None = None, backend: str | None = None) -> None:
        engine = self._checked_local_engine(self._file_local_engine_buttons)
        self._set_data_combo(
            self.cmb_file_vulkan_model,
            self._local_model_items(engine, "file"),
            self._coerce_local_model(engine, "file", model),
        )
        self._set_data_combo(
            self.cmb_file_vulkan_backend,
            self._local_backend_items(engine),
            self._coerce_local_backend(engine, backend),
        )
        if engine == "whispercpp":
            self._refresh_whisper_model_items(self.cmb_file_vulkan_model)

    def _on_live_local_engine_toggled(self, _button_id: int, on: bool) -> None:
        if on:
            self._sync_live_local_controls()
            self._emit_changed()

    def _on_file_local_engine_toggled(self, _button_id: int, on: bool) -> None:
        if on:
            self._sync_file_local_controls()
            self._emit_changed()

    # --- groups --------------------------------------------------------------
    def _build_compute_group(self) -> Card:
        card = self._group("compute_mode")
        form = self._form(card.body())

        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(6)
        self._compute_group = QButtonGroup(self)
        self._compute_group.setExclusive(True)
        self._compute_group.idToggled.connect(self._on_compute_toggled)
        for idx, mode in enumerate(self._compute_modes):
            button = self._segment_button(self._tr.tr(compute_mode_label_key(mode)))
            self._compute_group.addButton(button, idx)
            self._compute_buttons[mode] = button
            mode_row.addWidget(button)
        form.addRow(self._label("compute_mode"), self._wrap(mode_row))

        self.cmb_transcription_language = QComboBox()
        for lang in _LIVE_LANGUAGES:
            self.cmb_transcription_language.addItem(self._tr.tr(f"lang_{lang}"), lang)
        self.cmb_transcription_language.currentIndexChanged.connect(self._emit_changed)
        form.addRow(self._label("transcription_language"), self.cmb_transcription_language)

        self.txt_hotwords = self._new_hotwords_edit()
        form.addRow(self._label("hotwords"), self.txt_hotwords)
        return card

    def _build_live_group(self) -> Card:
        card = self._group("live_settings")
        layout = QVBoxLayout(card.body())
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        language_form = self._form(None)
        layout.addLayout(language_form)
        self.cmb_live_language = QComboBox()
        for lang in _TRANSCRIPTION_LANGUAGES:
            self.cmb_live_language.addItem(self._tr.tr(f"lang_{lang}"), lang)
        self.cmb_live_language.setToolTip(self._tr.tr("live_language_hint"))
        self.cmb_live_language.currentIndexChanged.connect(self._emit_changed)
        language_form.addRow(self._label("live_language"), self.cmb_live_language)

        self.txt_live_hotwords = self._new_hotwords_edit()
        language_form.addRow(self._label("hotwords"), self.txt_live_hotwords)

        source_line = QHBoxLayout()
        source_line.setContentsMargins(0, 0, 0, 0)
        source_line.setSpacing(8)
        source_line.addWidget(self._label("live_source"))
        source_row = QHBoxLayout()
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setSpacing(6)
        self._live_source_group = QButtonGroup(self)
        self._live_source_group.setExclusive(True)
        self._live_source_group.idToggled.connect(self._on_live_source_toggled)
        for idx, (source, key) in enumerate(_LIVE_SOURCES):
            button = self._segment_button(self._tr.tr(key))
            self._live_source_group.addButton(button, idx)
            self._live_source_buttons[source] = button
            source_row.addWidget(button)
        source_line.addLayout(source_row, 1)
        layout.addLayout(source_line)
        return card

    def _build_live_openai_group(self) -> Card:
        card = self._group("live_openai")
        form = self._form(card.body())

        provider_row = QHBoxLayout()
        provider_row.setContentsMargins(0, 0, 0, 0)
        provider_row.setSpacing(6)
        self._live_api_provider_group = QButtonGroup(self)
        self._live_api_provider_group.setExclusive(True)
        self._live_api_provider_group.idToggled.connect(self._on_live_api_provider_toggled)
        for idx, (provider, key) in enumerate(_LIVE_API_PROVIDERS):
            button = self._segment_button(self._tr.tr(key))
            self._live_api_provider_group.addButton(button, idx)
            self._live_api_provider_buttons[provider] = button
            provider_row.addWidget(button)
        form.addRow(self._label("live_api_provider"), self._wrap(provider_row))

        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(6)
        self._live_api_mode_group = QButtonGroup(self)
        self._live_api_mode_group.setExclusive(True)
        self._live_api_mode_group.idToggled.connect(self._on_live_api_mode_toggled)
        for idx, (mode, key) in enumerate(_OPENAI_LIVE_MODES):
            button = self._segment_button(self._tr.tr(key))
            self._live_api_mode_group.addButton(button, idx)
            self._live_api_mode_buttons[mode] = button
            mode_row.addWidget(button)
        self._live_api_mode_widget = self._wrap(mode_row)
        self._live_api_mode_label = self._label("live_api_mode")
        form.addRow(self._live_api_mode_label, self._live_api_mode_widget)

        return card

    def _live_model_items(self) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []
        for asset in whispercpp_model_assets():
            items.append((self._tr.tr(f"live_vulkan_model_{asset.key.replace('-', '_')}"), asset.key))
        return items

    def _build_live_vulkan_group(self) -> Card:
        card = self._group("live_vulkan")
        form = self._form(card.body())

        self._live_local_engine_group = QButtonGroup(self)
        self._live_local_engine_group.setExclusive(True)
        self._live_local_engine_group.idToggled.connect(self._on_live_local_engine_toggled)
        form.addRow(
            self._label("live_local_engine"),
            self._local_engine_row(self._live_local_engine_buttons, self._live_local_engine_group),
        )

        self.cmb_live_local_model = QComboBox()
        self.cmb_live_local_model.currentIndexChanged.connect(self._emit_changed)
        form.addRow(self._label("live_vulkan_model"), self.cmb_live_local_model)

        self.cmb_live_local_backend = QComboBox()
        self.cmb_live_local_backend.currentIndexChanged.connect(self._emit_changed)
        form.addRow(self._label("live_vulkan_backend"), self.cmb_live_local_backend)

        return card

    def _build_file_vulkan_group(self) -> Card:
        card = self._group("file_vulkan")
        layout = QVBoxLayout(card.body())
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.lbl_local_hw_hint = QLabel("")
        self.lbl_local_hw_hint.setObjectName("LocalHardwareBadge")
        self.lbl_local_hw_hint.setProperty("role", "local-hw-badge")
        self.lbl_local_hw_hint.setWordWrap(True)
        self.lbl_local_hw_hint.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.lbl_local_hw_hint)

        form = self._form(None)
        layout.addLayout(form)

        self._file_local_engine_group = QButtonGroup(self)
        self._file_local_engine_group.setExclusive(True)
        self._file_local_engine_group.idToggled.connect(self._on_file_local_engine_toggled)
        form.addRow(
            self._label("file_local_engine"),
            self._local_engine_row(self._file_local_engine_buttons, self._file_local_engine_group),
        )

        self.cmb_file_vulkan_model = QComboBox()
        self.cmb_file_vulkan_model.currentIndexChanged.connect(self._emit_changed)
        form.addRow(self._label("file_vulkan_model"), self.cmb_file_vulkan_model)

        self.cmb_file_vulkan_backend = QComboBox()
        self.cmb_file_vulkan_backend.currentIndexChanged.connect(self._emit_changed)
        form.addRow(self._label("file_vulkan_backend"), self.cmb_file_vulkan_backend)
        return card

    def _build_live_overlay_group(self) -> Card:
        card = self._group("live_overlay")
        form = self._form(card.body())

        self.chk_live_overlay = QCheckBox(self._tr.tr("live_overlay_enabled"))
        self.chk_live_overlay.toggled.connect(self._emit_changed)
        form.addRow("", self.chk_live_overlay)

        self.spin_live_overlay_scale = InlineSpinBox()
        self.spin_live_overlay_scale.setRange(70, 130)
        self.spin_live_overlay_scale.setSingleStep(5)
        self.spin_live_overlay_scale.setSuffix(" %")
        self.spin_live_overlay_scale.setToolTip(self._tr.tr("live_overlay_scale_hint"))
        self.spin_live_overlay_scale.valueChanged.connect(self._emit_changed)
        form.addRow(self._label("live_overlay_scale"), self.spin_live_overlay_scale)

        self.spin_live_safety_timeout = InlineSpinBox()
        self.spin_live_safety_timeout.setRange(1, 120)
        self.spin_live_safety_timeout.setSingleStep(5)
        self.spin_live_safety_timeout.setSuffix(f" {self._tr.tr('minutes_short')}")
        self.spin_live_safety_timeout.setToolTip(self._tr.tr("live_safety_timeout_hint"))
        self.spin_live_safety_timeout.valueChanged.connect(self._emit_changed)
        form.addRow(self._label("live_safety_timeout"), self.spin_live_safety_timeout)
        return card

    def _build_live_cleanup_group(self) -> Card:
        card = self._group("live_cleanup", checkable=True)
        card.toggled.connect(self._emit_changed)
        self._live_cleanup_box = card
        form = self._form(card.body())

        self.cmb_live_cleanup_model = self._editable_combo()
        self.cmb_live_cleanup_model.currentTextChanged.connect(self._emit_changed)
        form.addRow(self._label("live_cleanup_model"), self.cmb_live_cleanup_model)

        prompt_row = QHBoxLayout()
        self.lbl_live_active_prompt = QLabel("")
        self.lbl_live_active_prompt.setProperty("role", "muted")
        self.lbl_live_active_prompt.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.btn_live_prompt = QPushButton(self._tr.tr("live_cleanup_prompt"))
        self.btn_live_prompt.clicked.connect(self._open_live_prompt_library)
        prompt_row.addWidget(self.lbl_live_active_prompt, 1)
        prompt_row.addWidget(self.btn_live_prompt)
        form.addRow(self._label("live_cleanup_prompt"), self._wrap(prompt_row))

        self.spin_live_cleanup_sentences = InlineSpinBox()
        self.spin_live_cleanup_sentences.setRange(1, 100)
        self.spin_live_cleanup_sentences.setSingleStep(1)
        self.spin_live_cleanup_sentences.valueChanged.connect(self._emit_changed)
        form.addRow(self._label("live_cleanup_sentences"), self.spin_live_cleanup_sentences)
        return card

    def _build_connectors_group(self) -> Card:
        card = self._group("connectors")
        layout = QVBoxLayout(card.body())
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # --- Obsidian (filesystem) ------------------------------------------
        self.chk_obsidian = QCheckBox(self._tr.tr("conn_obsidian"))
        self.chk_obsidian.toggled.connect(self._emit_changed)
        layout.addWidget(self.chk_obsidian)

        ob_form = self._form(None)
        layout.addLayout(ob_form)
        self.txt_vault = QLineEdit()
        self.txt_vault.setPlaceholderText(self._tr.tr("conn_vault_hint"))
        self.txt_vault.textChanged.connect(self._emit_changed)
        self.btn_vault = QPushButton(self._tr.tr("conn_browse"))
        self.btn_vault.clicked.connect(self._choose_vault)
        vault_row = QHBoxLayout()
        vault_row.setContentsMargins(0, 0, 0, 0)
        vault_row.addWidget(self.txt_vault, 1)
        vault_row.addWidget(self.btn_vault)
        ob_form.addRow(self._label("conn_vault"), self._wrap(vault_row))
        self.txt_subfolder = QLineEdit()
        self.txt_subfolder.textChanged.connect(self._emit_changed)
        ob_form.addRow(self._label("conn_subfolder"), self.txt_subfolder)

        # --- Notion (API) ----------------------------------------------------
        self.chk_notion = QCheckBox(self._tr.tr("conn_notion"))
        self.chk_notion.toggled.connect(self._emit_changed)
        layout.addWidget(self.chk_notion)

        no_form = self._form(None)
        layout.addLayout(no_form)
        self.cmb_notion_parent_type = QComboBox()
        self.cmb_notion_parent_type.addItem(self._tr.tr("conn_parent_page"), "page")
        self.cmb_notion_parent_type.addItem(self._tr.tr("conn_parent_database"), "database")
        self.cmb_notion_parent_type.currentIndexChanged.connect(self._emit_changed)
        no_form.addRow(self._label("conn_parent_type"), self.cmb_notion_parent_type)
        self.txt_notion_parent = QLineEdit()
        self.txt_notion_parent.setPlaceholderText(self._tr.tr("conn_parent_hint"))
        self.txt_notion_parent.textChanged.connect(self._emit_changed)
        no_form.addRow(self._label("conn_parent_id"), self.txt_notion_parent)

        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        self.lbl_notion_key = QLabel("")
        self.lbl_notion_key.setProperty("role", "muted")
        self.lbl_notion_key.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.btn_notion_key = QPushButton(self._tr.tr("conn_notion_key"))
        self.btn_notion_key.clicked.connect(self._enter_notion_key)
        key_row.addWidget(self.lbl_notion_key, 1)
        key_row.addWidget(self.btn_notion_key)
        no_form.addRow(self._label("conn_notion_key"), self._wrap(key_row))
        return card

    def _choose_vault(self) -> None:
        start = self.txt_vault.text().strip() or ""
        chosen = QFileDialog.getExistingDirectory(self, self._tr.tr("conn_vault"), start)
        if chosen:
            self.txt_vault.setText(chosen)  # textChanged -> _emit_changed

    def _enter_notion_key(self) -> None:
        current = read_api_key(self._paths, "notion") or ""
        text, ok = QInputDialog.getText(
            self,
            self._tr.tr("conn_notion_key"),
            self._tr.tr("conn_notion_key_prompt"),
            QLineEdit.Password,
            current,
        )
        if ok:
            write_api_key(self._paths, "notion", text.strip())
            self._refresh_notion_key_label()

    def _refresh_notion_key_label(self) -> None:
        masked = mask_api_key(read_api_key(self._paths, "notion"))
        if masked:
            self.lbl_notion_key.setText(self._tr.tr("conn_key_set").format(key=masked))
        else:
            self.lbl_notion_key.setText(self._tr.tr("conn_key_unset"))

    def _build_models_group(self) -> Card:
        card = self._group("transcription_mode")
        outer = QVBoxLayout(card.body())
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(7)

        # Two-button fork: Transcription | Transcription + diarization.
        fork = QHBoxLayout()
        fork.setSpacing(6)
        fork.setContentsMargins(0, 0, 0, 0)
        self.btn_plain = QPushButton(self._tr.tr("btn_transcription"))
        self.btn_diar = QPushButton(self._tr.tr("btn_transcription_diar"))
        self.btn_plain.setToolTip(self._tr.tr("btn_transcription_tip"))
        self.btn_diar.setToolTip(self._tr.tr("btn_transcription_diar_tip"))
        for btn in (self.btn_plain, self.btn_diar):
            btn.setCheckable(True)
            btn.setProperty("variant", "segment")
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._fork_group = QButtonGroup(self)
        self._fork_group.setExclusive(True)
        self._fork_group.addButton(self.btn_plain, 0)
        self._fork_group.addButton(self.btn_diar, 1)
        self._fork_group.idToggled.connect(lambda _id, on: self._emit_changed() if on else None)
        fork.addWidget(self.btn_plain, 1)
        fork.addWidget(self.btn_diar, 1)
        outer.addLayout(fork)

        form = self._form(None)
        outer.addLayout(form)

        self.cmb_stt_provider = QComboBox()
        self._set_data_combo(self.cmb_stt_provider, self._file_provider_items(), "openai")
        self.cmb_stt_provider.currentIndexChanged.connect(self._on_file_provider_changed)
        form.addRow(self._label("file_stt_provider"), self.cmb_stt_provider)

        self.cmb_stt = self._editable_combo()
        self.cmb_stt.currentTextChanged.connect(self._emit_changed)
        self.cmb_stt.currentIndexChanged.connect(self._on_file_model_changed)
        form.addRow(self._label("stt_model"), self.cmb_stt)

        refresh_row = QHBoxLayout()
        self.btn_refresh = QPushButton(self._tr.tr("refresh_models"))
        self.btn_refresh.setIcon(icon("refresh", self._icon_muted))
        self.btn_refresh.clicked.connect(self.refresh_requested.emit)
        self.lbl_model_source = QLabel("")
        self.lbl_model_source.setProperty("role", "muted")
        self.lbl_model_source.setVisible(False)
        refresh_row.addWidget(self.btn_refresh)
        refresh_row.addStretch(1)
        form.addRow("", self._wrap(refresh_row))

        self.txt_context = QPlainTextEdit()
        self.txt_context.setFixedHeight(99)
        self.txt_context.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.txt_context.setPlaceholderText(self._tr.tr("context_hint"))
        self.txt_context.setToolTip(self._tr.tr("context_hint"))
        self.txt_context.textChanged.connect(self._emit_changed)
        form.addRow(self._label("context"), self.txt_context)
        return card

    def _build_postprocess_group(self) -> Card:
        card = self._group("generation_settings")
        layout = QVBoxLayout(card.body())
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        form = self._form(None)
        self.cmb_post = self._editable_combo()
        self.cmb_post.currentTextChanged.connect(self._emit_changed)
        form.addRow(self._label("postprocess_model"), self.cmb_post)
        layout.addLayout(form)

        self.chk_title = QCheckBox(self._tr.tr("pp_title"))
        self.chk_summary = QCheckBox(self._tr.tr("pp_summary"))
        self.chk_tags = QCheckBox(self._tr.tr("pp_tags"))
        self.chk_actions = QCheckBox(self._tr.tr("pp_action_items"))
        layout.addWidget(
            self._checks_strip(
                [self.chk_title, self.chk_summary, self.chk_tags, self.chk_actions]
            )
        )
        return card

    def _build_cleanup_group(self) -> Card:
        card = self._group("cleanup", checkable=True)
        card.toggled.connect(self._emit_changed)
        self._cleanup_box = card
        form = self._form(card.body())

        self.cmb_cleanup_model = self._editable_combo()
        self.cmb_cleanup_model.currentTextChanged.connect(self._emit_changed)
        form.addRow(self._label("cleanup_model"), self.cmb_cleanup_model)

        prompt_row = QHBoxLayout()
        self.lbl_active_prompt = QLabel("")
        self.lbl_active_prompt.setProperty("role", "muted")
        # Don't let the active-prompt name dictate the card's minimum width.
        self.lbl_active_prompt.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.btn_prompt = QPushButton(self._tr.tr("cleanup_prompt"))
        self.btn_prompt.clicked.connect(self._open_prompt_library)
        prompt_row.addWidget(self.lbl_active_prompt, 1)
        prompt_row.addWidget(self.btn_prompt)
        form.addRow(self._label("cleanup_prompt"), self._wrap(prompt_row))

        return card

    def _new_hotwords_edit(self) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(self._tr.tr("hotwords_hint"))
        edit.setToolTip(self._tr.tr("hotwords_hint"))
        edit.textChanged.connect(lambda text, source=edit: self._sync_hotword_edits(source, text))
        self._hotword_edits.append(edit)
        return edit

    def _sync_hotword_edits(self, source: QLineEdit, text: str) -> None:
        for edit in self._hotword_edits:
            if edit is source or edit.text() == text:
                continue
            edit.blockSignals(True)
            edit.setText(text)
            edit.blockSignals(False)
        self._emit_changed()

    def _build_exports_group(self) -> Card:
        card = self._group("export_formats")
        self.chk_json = QCheckBox(self._tr.tr("fmt_json"))
        self.chk_md = QCheckBox(self._tr.tr("fmt_markdown"))
        self.chk_txt = QCheckBox(self._tr.tr("fmt_txt"))
        self.chk_srt = QCheckBox(self._tr.tr("fmt_srt"))
        self.chk_vtt = QCheckBox(self._tr.tr("fmt_vtt"))
        layout = QVBoxLayout(card.body())
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(
            self._checks_strip(
                [self.chk_json, self.chk_md, self.chk_txt, self.chk_srt, self.chk_vtt]
            )
        )
        return card

    def _build_cuda_group(self) -> Card:
        card = self._group("cuda_settings")
        form = self._form(card.body())

        self.cmb_cuda_model = QComboBox()
        self._set_data_combo(
            self.cmb_cuda_model,
            [
                (self._tr.tr("live_vulkan_model_large_v2"), "large-v2"),
                (self._tr.tr("live_vulkan_model_turbo"), "turbo"),
            ],
            "large-v2",
        )
        self.cmb_cuda_model.currentIndexChanged.connect(
            lambda _index: self._refresh_local_availability()
        )
        self.cmb_cuda_model.currentIndexChanged.connect(self._emit_changed)
        form.addRow(self._label("cuda_model"), self.cmb_cuda_model)

        backend = QLabel(self._tr.tr("cuda_whispercpp_resident"))
        backend.setWordWrap(True)
        backend.setProperty("role", "muted")
        form.addRow(self._label("cuda_device"), backend)
        return card

    def _build_app_behavior_group(self) -> Card:
        card = self._group("app_behavior")
        layout = QVBoxLayout(card.body())
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.chk_show_tray = QCheckBox(self._tr.tr("show_tray"))
        self.chk_show_tray.toggled.connect(self._emit_changed)
        layout.addWidget(self.chk_show_tray)
        return card

    def _build_reset_group(self) -> Card:
        card = self._group("reset_app")
        row = QHBoxLayout(card.body())
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        desc = QLabel(self._tr.tr("reset_app_desc"))
        desc.setProperty("role", "muted")
        desc.setWordWrap(True)
        row.addWidget(desc, 1)
        self._labels["reset_app_desc"] = desc

        self.btn_reset_app = QPushButton(self._tr.tr("reset_app_button"))
        self.btn_reset_app.setProperty("accent", "danger")
        self.btn_reset_app.clicked.connect(self.reset_requested.emit)
        row.addWidget(self.btn_reset_app)
        return card

    def _build_subtitles_group(self) -> Card:
        card = self._group("subtitles")
        layout = QVBoxLayout(card.body())
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.chk_speakers = QCheckBox(self._tr.tr("include_speakers"))
        self.chk_speakers.toggled.connect(self._emit_changed)
        layout.addWidget(self.chk_speakers)

        fields = QGridLayout()
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setHorizontalSpacing(12)
        fields.setVerticalSpacing(8)
        layout.addLayout(fields)

        self.spin_chars = InlineSpinBox()
        self.spin_chars.setRange(10, 120)
        self.spin_chars.valueChanged.connect(self._emit_changed)

        self.spin_lines = InlineSpinBox()
        self.spin_lines.setRange(1, 4)
        self.spin_lines.valueChanged.connect(self._emit_changed)

        self.spin_min = InlineDoubleSpinBox()
        self.spin_min.setRange(0.1, 10.0)
        self.spin_min.setSingleStep(0.1)
        self.spin_min.valueChanged.connect(self._emit_changed)

        self.spin_max = InlineDoubleSpinBox()
        self.spin_max.setRange(1.0, 30.0)
        self.spin_max.setSingleStep(0.5)
        self.spin_max.valueChanged.connect(self._emit_changed)

        for field in (self.spin_chars, self.spin_lines, self.spin_min, self.spin_max):
            field.setMinimumWidth(128)
            field.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        pairs = [
            (self._label("max_chars_per_line"), self.spin_chars),
            (self._label("max_lines"), self.spin_lines),
            (self._label("min_duration"), self.spin_min),
            (self._label("max_duration"), self.spin_max),
        ]
        for col, (label, field) in enumerate(pairs):
            label.setWordWrap(False)
            label.setMinimumWidth(0)
            label.setMaximumWidth(16777215)
            label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            fields.addWidget(label, 0, col * 2)
            fields.addWidget(field, 0, col * 2 + 1)
            fields.setColumnStretch(col * 2 + 1, 1)
        return card

    def _build_pipeline_group(self) -> Card:
        card = self._group("queue")
        self.chk_skip = QCheckBox(self._tr.tr("skip_existing"))
        self.chk_force = QCheckBox(self._tr.tr("force_reprocess"))
        self.chk_recursive = QCheckBox(self._tr.tr("recursive"))
        self.chk_next_to = QCheckBox(self._tr.tr("save_next_to_source"))
        layout = QVBoxLayout(card.body())
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(
            self._checks_strip(
                [self.chk_skip, self.chk_force, self.chk_recursive, self.chk_next_to]
            )
        )
        # Force and skip are mutually informative: forcing implies not skipping.
        self.chk_force.toggled.connect(lambda on: self.chk_skip.setEnabled(not on))
        return card

    def _wrap(self, layout) -> QWidget:
        holder = QWidget()
        holder.setObjectName("InlineWrap")
        holder.setLayout(layout)
        return holder

    # --- load / collect ------------------------------------------------------
    def _g(self, *keys, default=None):
        node: Any = self._settings
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def _bool_value(self, value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if not text:
            return default
        return text not in {"0", "false", "no", "off", "disabled"}

    def _set_combo(self, combo: QComboBox, items: list[str], value: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(items)
        if value and value not in items:
            combo.insertItem(0, value)
        combo.setCurrentText(value or (items[0] if items else ""))
        combo.blockSignals(False)

    def _set_data_combo(self, combo: QComboBox, items: list[tuple[str, str]], value: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        for label, data in items:
            combo.addItem(label, data)
        idx = combo.findData(value)
        if idx < 0 and value:
            combo.addItem(value, value)
            idx = combo.count() - 1
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _set_data_combo_with_tooltips(self, combo: QComboBox, items: list[tuple[str, str, str]], value: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        for label, data, tooltip in items:
            combo.addItem(label, data)
            idx = combo.count() - 1
            combo.setItemData(idx, tooltip, Qt.ToolTipRole)
        idx = combo.findData(value)
        if idx < 0 and value:
            combo.addItem(value, value)
            idx = combo.count() - 1
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.setToolTip(combo.itemData(combo.currentIndex(), Qt.ToolTipRole) or "")
        combo.blockSignals(False)

    def _load_into_widgets(self) -> None:
        self._loading = True
        # compute / transcription language
        mode = display_compute_mode(self._g("compute_mode", default=MODE_API))
        if mode not in self._compute_buttons:
            mode = MODE_API
        for key, button in self._compute_buttons.items():
            button.blockSignals(True)
            button.setChecked(key == mode)
            button.blockSignals(False)
        lang = self._g("language", default="auto")
        lidx = self.cmb_transcription_language.findData(lang)
        self.cmb_transcription_language.setCurrentIndex(lidx if lidx >= 0 else 0)
        file_local_engine = self._local_engine_from_settings("file")
        self._set_local_engine(self._file_local_engine_buttons, file_local_engine)
        self._sync_file_local_controls(
            str(
                self._g(
                    "vulkan",
                    "model",
                    default=self._local_default_model(file_local_engine, "file"),
                )
                or self._local_default_model(file_local_engine, "file")
            ),
            str(self._g("vulkan", "backend", default="auto") or "auto"),
        )

        # live dictation
        parsed_live = LiveConfig.from_settings(self._settings)
        live_language_index = self.cmb_live_language.findData(parsed_live.language)
        self.cmb_live_language.setCurrentIndex(live_language_index if live_language_index >= 0 else 0)
        self.chk_show_tray.setChecked(bool(self._g("live", "minimize_to_tray", default=True)))
        live_engine = str(self._g("live", "engine", default="batch") or "batch").strip().lower()
        live_source = str(self._g("live", "source", default="") or "").strip().lower()
        if live_source not in {"api", "local"}:
            live_source = "local" if live_engine == MODE_VULKAN else "api"
        live_api = self._g("live", "api", default={}) or {}
        live_api_provider = str(
            live_api.get("provider", "") if isinstance(live_api, dict) else ""
        ).strip().lower()
        if live_api_provider not in {provider for provider, _ in _LIVE_API_PROVIDERS}:
            if live_engine.startswith("xai"):
                live_api_provider = "xai"
            elif live_engine.startswith("elevenlabs"):
                live_api_provider = "elevenlabs"
            else:
                live_api_provider = "openai"
        live_api_mode = str(live_api.get("mode", "") if isinstance(live_api, dict) else "").strip().lower()
        if live_api_mode not in {"batch", "realtime"}:
            live_api_mode = live_engine if live_engine in {"batch", "realtime"} else "realtime"
        self._set_button(self._live_source_buttons, live_source, "api")
        self._set_button(self._live_api_provider_buttons, live_api_provider, "openai")
        self._set_button(self._live_api_mode_buttons, live_api_mode, "batch")
        live_local_engine = self._local_engine_from_settings("live")
        self._set_local_engine(self._live_local_engine_buttons, live_local_engine)
        self._sync_live_local_controls(
            str(
                self._g(
                    "live",
                    "local",
                    "model",
                    default=self._local_default_model(live_local_engine, "live"),
                )
                or self._local_default_model(live_local_engine, "live")
            ),
            str(self._g("live", "local", "backend", default="auto") or "auto"),
        )
        self.chk_live_overlay.setChecked(bool(self._g("live", "show_overlay", default=True)))
        self.spin_live_overlay_scale.setValue(parsed_live.overlay_scale_percent)
        self.spin_live_safety_timeout.setValue(
            int(self._g("live", "safety_timeout_minutes", default=15))
        )
        live_cleanup = self._g("live", "cleanup", default={}) or {}
        self._live_cleanup_box.setChecked(bool(live_cleanup.get("enabled", False)) if isinstance(live_cleanup, dict) else False)
        self._set_combo(
            self.cmb_live_cleanup_model,
            [str(self._g("live", "cleanup", "model", default=self._g("postprocessing", "cleanup", "model", default="")) or "")],
            str(self._g("live", "cleanup", "model", default=self._g("postprocessing", "cleanup", "model", default="")) or ""),
        )
        self.spin_live_cleanup_sentences.setValue(
            int(self._g("live", "cleanup", "sentence_threshold", default=12))
        )
        self._refresh_live_active_prompt_label()

        # connectors
        self.chk_obsidian.setChecked(bool(self._g("connectors", "obsidian", "enabled", default=False)))
        self.txt_vault.setText(str(self._g("connectors", "obsidian", "vault_path", default="") or ""))
        self.txt_subfolder.setText(str(self._g("connectors", "obsidian", "subfolder", default="Transcripts") or ""))
        self.chk_notion.setChecked(bool(self._g("connectors", "notion", "enabled", default=False)))
        ptype = self._g("connectors", "notion", "parent_type", default="page")
        ptidx = self.cmb_notion_parent_type.findData("database" if str(ptype).startswith("data") else "page")
        self.cmb_notion_parent_type.setCurrentIndex(ptidx if ptidx >= 0 else 0)
        self.txt_notion_parent.setText(str(self._g("connectors", "notion", "parent_id", default="") or ""))
        self._refresh_notion_key_label()

        # fork
        diarize = bool(self._g("transcription", "diarize", default=False))
        (self.btn_diar if diarize else self.btn_plain).setChecked(True)

        # models (lists filled later by set_model_lists; seed with current value)
        file_provider = self._file_provider_from_settings()
        self._set_data_combo(self.cmb_stt_provider, self._file_provider_items(), file_provider)
        self._active_file_stt_provider = file_provider
        self._sync_file_model_combo(file_provider)
        self._set_combo(self.cmb_post, [self._g("postprocess", "model", default="")], self._g("postprocess", "model", default=""))
        self._set_combo(
            self.cmb_cleanup_model,
            [self._g("postprocessing", "cleanup", "model", default="")],
            self._g("postprocessing", "cleanup", "model", default=""),
        )

        self.txt_context.setPlainText(str(self._g("context", default="") or ""))

        # post-processing
        self.chk_title.setChecked(bool(self._g("postprocessing", "generate_title", default=True)))
        self.chk_summary.setChecked(bool(self._g("postprocessing", "generate_summary", default=True)))
        self.chk_tags.setChecked(bool(self._g("postprocessing", "generate_tags", default=True)))
        self.chk_actions.setChecked(bool(self._g("postprocessing", "generate_action_items", default=False)))

        # cleanup
        self._cleanup_box.setChecked(bool(self._g("postprocessing", "cleanup", "enabled", default=False)))
        self.txt_hotwords.setText(", ".join(shared_keyterms(self._settings)))
        self._refresh_active_prompt_label()

        # exports
        self.chk_json.setChecked(bool(self._g("exports", "json", default=True)))
        self.chk_md.setChecked(bool(self._g("exports", "markdown", default=True)))
        self.chk_txt.setChecked(bool(self._g("exports", "txt", default=False)))
        self.chk_srt.setChecked(bool(self._g("exports", "srt", default=True)))
        self.chk_vtt.setChecked(bool(self._g("exports", "vtt", default=True)))

        if hasattr(self, "cmb_cuda_model"):
            cuda_model = str(self._g("local", "model", default="large-v2") or "large-v2")
            if cuda_model.lower() == "large-v3":
                cuda_model = "large-v2"
            self._set_data_combo(
                self.cmb_cuda_model,
                [
                    (self._tr.tr("live_vulkan_model_large_v2"), "large-v2"),
                    (self._tr.tr("live_vulkan_model_turbo"), "turbo"),
                ],
                cuda_model,
            )

        # subtitles
        self.chk_speakers.setChecked(bool(self._g("subtitles", "include_speakers", default=False)))
        self.spin_chars.setValue(int(self._g("subtitles", "max_chars_per_line", default=42)))
        self.spin_lines.setValue(int(self._g("subtitles", "max_lines", default=2)))
        self.spin_min.setValue(float(self._g("subtitles", "min_duration", default=1.0)))
        self.spin_max.setValue(float(self._g("subtitles", "max_duration", default=7.0)))

        # pipeline
        self.chk_skip.setChecked(bool(self._g("pipeline", "skip_existing", default=True)))
        self.chk_force.setChecked(bool(self._g("pipeline", "force", default=False)))
        self.chk_recursive.setChecked(bool(self._g("pipeline", "recursive", default=False)))
        self.chk_next_to.setChecked(bool(self._g("pipeline", "save_next_to_source", default=True)))
        self.chk_skip.setEnabled(not self.chk_force.isChecked())
        self._apply_compute_visibility()
        self._apply_live_engine_visibility()
        self._refresh_local_availability()
        self._refresh_local_hw_hint()
        self._loading = False

    def _refresh_active_prompt_label(self) -> None:
        prompt_id = self._g("postprocessing", "cleanup", "prompt_id", default="")
        entry = self._prompts.get(prompt_id) if prompt_id else None
        if entry is None:
            try:
                entry = self._prompts.active(ROLE_CLEANUP)
            except RuntimeError:
                entry = None
        self.lbl_active_prompt.setText(entry.name if entry else "—")

    def _refresh_live_active_prompt_label(self) -> None:
        prompt_id = self._g("live", "cleanup", "prompt_id", default="")
        entry = self._prompts.get(prompt_id) if prompt_id else None
        if entry is None:
            try:
                entry = self._prompts.active(ROLE_CLEANUP)
            except RuntimeError:
                entry = None
        self.lbl_live_active_prompt.setText(entry.name if entry else "—")

    def set_model_lists(self, stt: list[str], chat: list[str], source: str) -> None:
        self._openai_stt_models = list(stt)
        if self._checked_file_provider() == "openai":
            self._sync_file_model_combo("openai")
        self._set_combo(self.cmb_post, chat, self.cmb_post.currentText())
        self._set_combo(self.cmb_cleanup_model, chat, self.cmb_cleanup_model.currentText())
        self._set_combo(self.cmb_live_cleanup_model, chat, self.cmb_live_cleanup_model.currentText())
        if hasattr(self, "cmb_cuda_model"):
            current_cuda_model = str(
                self.cmb_cuda_model.currentData() or self.cmb_cuda_model.currentText()
            ).strip()
            if current_cuda_model.lower() == "large-v3":
                current_cuda_model = "large-v2"
            self._set_data_combo(
                self.cmb_cuda_model,
                [
                    (self._tr.tr("live_vulkan_model_large_v2"), "large-v2"),
                    (self._tr.tr("live_vulkan_model_turbo"), "turbo"),
                ],
                current_cuda_model,
            )
            self._refresh_local_availability()
        self._model_source = source
        self._update_model_source_label()

    def _update_model_source_label(self) -> None:
        if not self._model_source:
            self.lbl_model_source.clear()
            self.btn_refresh.setToolTip(self._tr.tr("refresh_models"))
            return
        key = f"model_source_{self._model_source}"
        text = self._tr.tr(key)
        source = text if text != key else self._model_source
        self.lbl_model_source.setText(f"({source})")
        self.btn_refresh.setToolTip(f"{self._tr.tr('refresh_models')} ({source})")

    def _open_prompt_library(self) -> None:
        current = self._g("postprocessing", "cleanup", "prompt_id", default="")
        dialog = PromptLibraryDialog(self._prompts, self._tr, active_id=current, parent=self)
        if dialog.exec() and dialog.selected_prompt_id:
            self._settings.setdefault("postprocessing", {}).setdefault("cleanup", {})[
                "prompt_id"
            ] = dialog.selected_prompt_id
            self._refresh_active_prompt_label()
            self.changed.emit()

    def _open_live_prompt_library(self) -> None:
        current = self._g("live", "cleanup", "prompt_id", default="")
        dialog = PromptLibraryDialog(self._prompts, self._tr, active_id=current, parent=self)
        if dialog.exec() and dialog.selected_prompt_id:
            self._settings.setdefault("live", {}).setdefault("cleanup", {})[
                "prompt_id"
            ] = dialog.selected_prompt_id
            self._refresh_live_active_prompt_label()
            self.changed.emit()

    def values(self) -> dict[str, Any]:
        """Collect the current widget state back into a settings dict."""
        s = self._settings
        compute_mode = self._checked_compute_mode()
        s["compute_mode"] = compute_mode
        s["language"] = self.cmb_transcription_language.currentData()
        vulkan = s.setdefault("vulkan", {})
        vulkan["engine"] = self._checked_local_engine(self._file_local_engine_buttons)
        vulkan["model"] = self.cmb_file_vulkan_model.currentData() or self.cmb_file_vulkan_model.currentText().strip()
        vulkan["backend"] = self.cmb_file_vulkan_backend.currentData() or "auto"
        live = s.setdefault("live", {})
        live.pop("autostart", None)
        live.pop("keyterms", None)
        live["prewarm_local"] = True
        live["language"] = self.cmb_live_language.currentData()
        live["minimize_to_tray"] = self.chk_show_tray.isChecked()
        live["source"] = self._checked_live_source()
        live["engine"] = self._checked_live_engine()
        live_api_provider = self._checked_live_api_provider()
        live_api_model = fixed_live_api_model(live_api_provider, self._checked_live_api_mode())
        live_api = live.setdefault("api", {})
        live_api["provider"] = live_api_provider
        live_api["mode"] = self._checked_live_api_mode()
        live_api["model"] = live_api_model
        live_api.setdefault("models", {})[live_api_provider] = live_api_model
        live["model"] = live_api_model
        live["show_overlay"] = self.chk_live_overlay.isChecked()
        live.pop("overlay_height", None)
        live["overlay_scale_percent"] = self.spin_live_overlay_scale.value()
        live["safety_timeout_minutes"] = self.spin_live_safety_timeout.value()
        live_local = live.setdefault("local", {})
        live_local["engine"] = self._checked_local_engine(self._live_local_engine_buttons)
        live_local["model"] = self.cmb_live_local_model.currentData() or self.cmb_live_local_model.currentText().strip()
        live_local["backend"] = self.cmb_live_local_backend.currentData() or "auto"
        live_local["idle_unload_seconds"] = 0
        live_cleanup = live.setdefault("cleanup", {})
        live_cleanup["enabled"] = self._live_cleanup_box.isChecked()
        live_cleanup["model"] = self.cmb_live_cleanup_model.currentText().strip()
        live_cleanup["sentence_threshold"] = self.spin_live_cleanup_sentences.value()
        conn = s.setdefault("connectors", {})
        ob = conn.setdefault("obsidian", {})
        ob["enabled"] = self.chk_obsidian.isChecked()
        ob["vault_path"] = self.txt_vault.text().strip()
        ob["subfolder"] = self.txt_subfolder.text().strip()
        no = conn.setdefault("notion", {})
        no["enabled"] = self.chk_notion.isChecked()
        no["parent_type"] = self.cmb_notion_parent_type.currentData()
        no["parent_id"] = self.txt_notion_parent.text().strip()
        s["context"] = self.txt_context.toPlainText().strip()
        s.setdefault("transcription", {})["diarize"] = self.btn_diar.isChecked()
        self._store_current_file_model()
        file_provider = self._checked_file_provider()
        stt = s.setdefault("stt", {})
        stt["provider"] = file_provider
        model_key = self._file_model_key(file_provider)
        if model_key:
            stt[model_key] = str(self.cmb_stt.currentData() or self.cmb_stt.currentText()).strip()
        s.setdefault("postprocess", {})["model"] = self.cmb_post.currentText().strip()
        s.setdefault("assemblyai", {})["enabled"] = file_provider == "assemblyai"
        # diarization step (local/pyannote) follows the fork in non-API modes
        s.setdefault("diarization", {})["enabled"] = self.btn_diar.isChecked()

        pp = s.setdefault("postprocessing", {})
        pp["generate_title"] = self.chk_title.isChecked()
        pp["generate_summary"] = self.chk_summary.isChecked()
        pp["generate_tags"] = self.chk_tags.isChecked()
        pp["generate_action_items"] = self.chk_actions.isChecked()
        cleanup = pp.setdefault("cleanup", {})
        cleanup["enabled"] = self._cleanup_box.isChecked()
        cleanup["model"] = self.cmb_cleanup_model.currentText().strip()
        cleanup.pop("hotwords", None)
        s["term_dictionary"] = [
            word.strip() for word in self.txt_hotwords.text().split(",") if word.strip()
        ]

        s["exports"] = {
            "json": self.chk_json.isChecked(),
            "markdown": self.chk_md.isChecked(),
            "txt": self.chk_txt.isChecked(),
            "srt": self.chk_srt.isChecked(),
            "vtt": self.chk_vtt.isChecked(),
        }
        if hasattr(self, "cmb_cuda_model"):
            local = s.setdefault("local", {})
            local["model"] = str(
                self.cmb_cuda_model.currentData() or self.cmb_cuda_model.currentText()
            ).strip()
            # Kept for settings-file compatibility; CUDA file STT is now the
            # resident whisper.cpp/cuBLAS server and ignores CT2 profiles.
            local["device"] = "cuda"
            local["compute_type"] = "float16"
            local["batched"] = False
            local["batch_size"] = int(local.get("batch_size") or 16)
        sub = s.setdefault("subtitles", {})
        sub["include_speakers"] = self.chk_speakers.isChecked()
        sub["max_chars_per_line"] = self.spin_chars.value()
        sub["max_lines"] = self.spin_lines.value()
        sub["min_duration"] = round(self.spin_min.value(), 2)
        sub["max_duration"] = round(self.spin_max.value(), 2)

        pipe = s.setdefault("pipeline", {})
        pipe["skip_existing"] = self.chk_skip.isChecked()
        pipe["force"] = self.chk_force.isChecked()
        pipe["recursive"] = self.chk_recursive.isChecked()
        pipe["save_next_to_source"] = self.chk_next_to.isChecked()
        return copy.deepcopy(s)

    def retranslate(self, tr: Translator) -> None:
        self._tr = tr
        for key in self._section_keys:
            self._section_tabs.set_tab_text(key, tr.tr(key))
        for key, label in self._labels.items():
            label.setText(tr.tr(key))
        for key, box in self._boxes.items():
            box.setTitle(tr.tr(key))
        if hasattr(self, "modules_panel"):
            self.modules_panel.retranslate(tr)
        for mode, button in self._compute_buttons.items():
            button.setText(tr.tr(compute_mode_label_key(mode)))
        for i, lang in enumerate(_TRANSCRIPTION_LANGUAGES):
            self.cmb_transcription_language.setItemText(i, tr.tr(f"lang_{lang}"))
        for i, lang in enumerate(_LIVE_LANGUAGES):
            self.cmb_live_language.setItemText(i, tr.tr(f"lang_{lang}"))
        self.cmb_live_language.setToolTip(tr.tr("live_language_hint"))
        self.chk_show_tray.setText(tr.tr("show_tray"))
        for source, key in _LIVE_SOURCES:
            button = self._live_source_buttons.get(source)
            if button is not None:
                button.setText(tr.tr(key))
        for provider, key in _LIVE_API_PROVIDERS:
            button = self._live_api_provider_buttons.get(provider)
            if button is not None:
                button.setText(tr.tr(key))
        for mode, key in _OPENAI_LIVE_MODES:
            button = self._live_api_mode_buttons.get(mode)
            if button is not None:
                button.setText(tr.tr(key))
                button.setToolTip(tr.tr("live_realtime_hint"))
        self.spin_live_safety_timeout.setSuffix(f" {tr.tr('minutes_short')}")
        self.spin_live_safety_timeout.setToolTip(tr.tr("live_safety_timeout_hint"))
        for local_engine, label_key in _LOCAL_ENGINES:
            live_button = self._live_local_engine_buttons.get(local_engine)
            file_button = self._file_local_engine_buttons.get(local_engine)
            if live_button is not None:
                live_button.setText(tr.tr(label_key))
            if file_button is not None:
                file_button.setText(tr.tr(label_key))
        self._sync_live_local_controls(
            str(self.cmb_live_local_model.currentData() or self.cmb_live_local_model.currentText()),
            str(self.cmb_live_local_backend.currentData() or "auto"),
        )
        self._sync_file_local_controls(
            str(self.cmb_file_vulkan_model.currentData() or self.cmb_file_vulkan_model.currentText()),
            str(self.cmb_file_vulkan_backend.currentData() or "auto"),
        )
        self.chk_live_overlay.setText(tr.tr("live_overlay_enabled"))
        self.spin_live_overlay_scale.setToolTip(tr.tr("live_overlay_scale_hint"))
        self._refresh_local_hw_hint()
        self.btn_live_prompt.setText(tr.tr("live_cleanup_prompt"))
        self.chk_obsidian.setText(tr.tr("conn_obsidian"))
        self.chk_notion.setText(tr.tr("conn_notion"))
        self.btn_vault.setText(tr.tr("conn_browse"))
        self.txt_vault.setPlaceholderText(tr.tr("conn_vault_hint"))
        self.txt_notion_parent.setPlaceholderText(tr.tr("conn_parent_hint"))
        self.btn_notion_key.setText(tr.tr("conn_notion_key"))
        self.cmb_notion_parent_type.setItemText(0, tr.tr("conn_parent_page"))
        self.cmb_notion_parent_type.setItemText(1, tr.tr("conn_parent_database"))
        self._refresh_notion_key_label()
        self.btn_plain.setText(tr.tr("btn_transcription"))
        self.btn_diar.setText(tr.tr("btn_transcription_diar"))
        self.btn_plain.setToolTip(tr.tr("btn_transcription_tip"))
        self.btn_diar.setToolTip(tr.tr("btn_transcription_diar_tip"))
        provider = self._checked_file_provider()
        self._set_data_combo(self.cmb_stt_provider, self._file_provider_items(), provider)
        self._sync_file_model_combo(provider)
        self.btn_refresh.setText(tr.tr("refresh_models"))
        self.txt_context.setPlaceholderText(tr.tr("context_hint"))
        self.txt_context.setToolTip(tr.tr("context_hint"))
        for edit in self._hotword_edits:
            edit.setPlaceholderText(tr.tr("hotwords_hint"))
            edit.setToolTip(tr.tr("hotwords_hint"))
        self.btn_prompt.setText(tr.tr("cleanup_prompt"))
        self.chk_title.setText(tr.tr("pp_title"))
        self.chk_summary.setText(tr.tr("pp_summary"))
        self.chk_tags.setText(tr.tr("pp_tags"))
        self.chk_actions.setText(tr.tr("pp_action_items"))
        self.chk_json.setText(tr.tr("fmt_json"))
        self.chk_md.setText(tr.tr("fmt_markdown"))
        self.chk_txt.setText(tr.tr("fmt_txt"))
        self.chk_srt.setText(tr.tr("fmt_srt"))
        self.chk_vtt.setText(tr.tr("fmt_vtt"))
        if hasattr(self, "cmb_cuda_device"):
            self.cmb_cuda_device.setItemText(0, "CUDA")
            self.cmb_cuda_device.setItemText(1, "CPU")
        for profile, label_key in _CUDA_PROFILES:
            button = self._cuda_profile_buttons.get(profile)
            if button is not None:
                button.setText(tr.tr(label_key))
                button.setToolTip(tr.tr("cuda_profile_hint"))
        if hasattr(self, "btn_reset_app"):
            self.btn_reset_app.setText(tr.tr("reset_app_button"))
        self.chk_speakers.setText(tr.tr("include_speakers"))
        self.chk_skip.setText(tr.tr("skip_existing"))
        self.chk_force.setText(tr.tr("force_reprocess"))
        self.chk_recursive.setText(tr.tr("recursive"))
        self.chk_next_to.setText(tr.tr("save_next_to_source"))
        self._update_model_source_label()
        self._refresh_active_prompt_label()
        self._refresh_live_active_prompt_label()
        seed_tooltips(self)
