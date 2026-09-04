"""Theme loader: config/ui_colors.yaml tokens -> a Qt stylesheet (iteration 2).

The token names in the catalog map 1:1 to the family convention, so the same
`color-*` tokens drive every Audion app's stylesheet. We keep the QSS template
here (UI code) and the colors in config (editable without touching code).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.config import load_yaml_or_json
from ..core.paths import ProjectPaths


@dataclass
class ThemeInfo:
    name: str
    label: str
    label_ru: str
    mode: str
    tokens: dict[str, str]


def _load_catalog(paths: ProjectPaths) -> dict[str, Any]:
    return load_yaml_or_json(paths.config / "ui_colors.yaml")


def list_themes(paths: ProjectPaths) -> list[ThemeInfo]:
    data = _load_catalog(paths)
    themes = data.get("themes", {}) or {}
    out: list[ThemeInfo] = []
    for name, spec in themes.items():
        if not isinstance(spec, dict):
            continue
        out.append(
            ThemeInfo(
                name=str(name),
                label=str(spec.get("label", name)),
                label_ru=str(spec.get("label_ru", spec.get("label", name))),
                mode=str(spec.get("mode", "dark")),
                tokens={str(k): str(v) for k, v in (spec.get("tokens", {}) or {}).items()},
            )
        )
    return out


def default_theme_name(paths: ProjectPaths) -> str:
    data = _load_catalog(paths)
    name = str(data.get("default_theme", ""))
    themes = data.get("themes", {}) or {}
    if name in themes:
        return name
    return next(iter(themes), "dark_blue")


def get_theme(paths: ProjectPaths, name: str | None) -> ThemeInfo:
    themes = {t.name: t for t in list_themes(paths)}
    if name and name in themes:
        return themes[name]
    return themes.get(default_theme_name(paths)) or next(iter(themes.values()))


def build_stylesheet(theme: ThemeInfo) -> str:
    """Render a Qt stylesheet from a theme's tokens."""
    t = theme.tokens

    def c(token: str, fallback: str = "#000000") -> str:
        return t.get(token, fallback)

    bg = c("color-background-primary", "#0F1622")
    bg2 = c("color-background-secondary", "#16202E")
    bg3 = c("color-background-tertiary", "#1F2C3D")
    work_bg = c("color-background-work", bg)
    control_bg = c("color-background-control", bg)
    action_bg = c("color-background-action", c("color-accent-primary", "#378ADD"))
    fg = c("color-text-primary", "#E6EDF5")
    fg2 = c("color-text-secondary", "#9FB0C3")
    fg3 = c("color-text-tertiary", "#6B7C90")
    log_fg = c("color-text-log", fg2)
    control_fg = c("color-text-control", log_fg)
    border = c("color-border-primary", "#2A3A4F")
    border2 = c("color-border-secondary", "#22303F")
    control_border = c("color-border-control", border)
    work_border = c("color-border-work", border2)
    accent = c("color-accent-primary", "#378ADD")
    accent3 = c("color-accent-tertiary", "#85B7EB")
    ok = c("color-status-success", "#1D9E75")
    err = c("color-status-error", "#D85A30")
    combo_arrow = (Path(__file__).resolve().parent / "assets" / "combo_down.svg").as_posix()
    spin_up_arrow = (Path(__file__).resolve().parent / "assets" / "spin_up.svg").as_posix()
    check_icon = (Path(__file__).resolve().parent / "assets" / "check.svg").as_posix()

    return f"""
    QWidget {{
        background-color: {bg};
        color: {fg};
        font-family: "Segoe UI", "Arial", "Tahoma", sans-serif;
        font-size: 13px;
    }}
    QMainWindow, QDialog {{ background-color: {bg}; }}
    QLabel {{ background: transparent; color: {fg}; }}
    QLabel[role="muted"] {{ color: {fg2}; }}
    QLabel[role="heading"] {{ color: {fg}; font-size: 14px; font-weight: 600; }}
    QLabel#LocalHardwareBadge, QLabel#RunSummary {{
        background-color: {control_bg};
        color: {control_fg};
        border: 1px solid {action_bg};
        border-radius: 9px;
        padding: 7px 10px;
    }}
    QLabel#RunSummary {{
        margin-top: 2px;
        margin-bottom: 2px;
    }}
    QLabel#LiveAudioDeviceBadge {{
        background-color: {control_bg};
        color: {control_fg};
        border: 1px solid {action_bg};
        border-radius: 16px;
        padding: 0 12px;
        font-size: 12px;
        font-weight: 600;
    }}
    QComboBox#LiveAudioDeviceSelect {{
        background-color: {bg3};
        color: {control_fg};
        border: 1px solid {action_bg};
        border-radius: 16px;
        padding: 0 28px 0 12px;
        min-height: 32px;
        max-height: 32px;
        font-size: 12px;
        font-weight: 600;
    }}
    QComboBox#LiveAudioDeviceSelect:hover {{
        border-color: {accent3};
    }}
    QComboBox#LiveAudioDeviceSelect::drop-down {{
        border: none;
        subcontrol-origin: padding;
        subcontrol-position: right center;
        width: 22px;
        right: 5px;
    }}

    QGroupBox {{
        border: 1px solid {border2};
        border-radius: 8px;
        margin-top: 22px;
        padding: 16px 12px 12px 12px;
        background-color: {bg2};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 12px;
        top: 2px;
        padding: 2px 8px;
        color: {fg2};
        font-weight: 600;
        /* opaque chip so the group border never crosses the title text */
        background-color: {bg};
    }}
    QGroupBox::indicator {{ width: 16px; height: 16px; }}

    /* Flat "card" sections (settings panel) — header label instead of a
       border-title, so nothing ever crosses the text. */
    QFrame#Card {{
        background-color: {bg2};
        border: 1px solid {border2};
        border-radius: 12px;
    }}
    /* First-run download prompt rows: same card, gentler corners. */
    QFrame#SetupPromptRow {{
        background-color: {bg2};
        border: 1px solid {border2};
        border-radius: 6px;
    }}
    QFrame#SetupPromptRow QWidget {{ background: transparent; }}
    QFrame#Card QWidget, QWidget#CardBody, QWidget#InlineWrap, QWidget#SettingsSectionPage {{
        background: transparent;
    }}
    QLabel[role="card-title"], QCheckBox[role="card-title"] {{
        color: {fg};
        font-size: 13px;
        font-weight: 600;
    }}
    QLabel[role="card-title"] {{ background: transparent; }}
    QFrame#CardRule {{
        background-color: {border2};
        border: none;
    }}
    QFrame#WorkSurface {{
        background-color: {work_bg};
        border: 1px solid {work_border};
        border-radius: 12px;
    }}
    QWidget#OptionStrip {{
        background: transparent;
        border: none;
    }}
    QLabel[variant="chip"] {{
        background-color: {control_bg};
        color: {fg2};
        border: none;
        border-radius: 8px;
        padding: 0 10px;
    }}
    QMenu#OverlayQuickMenu {{
        background-color: rgb(23, 33, 43);
        color: {fg};
        border: 1px solid {border};
        border-radius: 12px;
        padding: 6px;
    }}
    QMenu#OverlayQuickMenu::item {{
        background: transparent;
        border-radius: 7px;
        padding: 7px 28px 7px 10px;
        margin: 2px;
    }}
    QMenu#OverlayQuickMenu::item:selected {{ background-color: {bg3}; }}
    QMenu#OverlayQuickMenu::item:disabled {{ color: {fg3}; }}
    QMenu#OverlayQuickMenu::separator {{
        background-color: {border2};
        height: 1px;
        margin: 5px 8px;
    }}
    QDialog#DictationHistoryDialog {{ background-color: {bg}; }}
    QWidget#DictationHistoryContent {{ background: transparent; }}
    QFrame#DictationHistoryItem {{
        background-color: {control_bg};
        border: none;
        border-radius: 10px;
    }}
    QFrame#DictationHistoryItem:hover {{ background-color: {bg3}; }}
    QLabel#DictationHistoryPreview {{
        background: transparent;
        color: {control_fg};
        font-size: 13px;
    }}
    QLabel#DictationHistoryTime {{ background: transparent; color: {fg3}; }}

    QPushButton {{
        background-color: {bg3};
        color: {fg};
        border: 1px solid {border};
        border-radius: 8px;
        padding: 5px 10px;
        min-height: 24px;
    }}
    QPushButton:hover {{ border-color: {accent3}; }}
    QPushButton:pressed {{ background-color: {bg2}; }}
    QPushButton:disabled {{ color: {fg3}; border-color: {border2}; }}
    QPushButton:focus, QComboBox:focus, QLineEdit:focus,
    QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{
        border: 2px solid {accent3};
    }}

    QPushButton[accent="primary"] {{
        background-color: {action_bg};
        color: {fg};
        border: 1px solid {action_bg};
        font-weight: 600;
    }}
    QPushButton[accent="primary"]:hover {{ background-color: {accent}; border-color: {accent3}; }}
    QPushButton[accent="primary"]:disabled {{ background-color: {bg3}; color: {fg3}; border-color: {border2}; }}

    QPushButton[accent="danger"] {{ border-color: {err}; }}
    QPushButton[accent="danger"]:disabled {{ border-color: {border2}; }}

    QPushButton[variant="toolbar"] {{
        background-color: {action_bg};
        border: 1px solid {action_bg};
        border-radius: 16px;
        padding: 0;
        min-width: 34px;
        max-width: 40px;
        min-height: 32px;
        max-height: 32px;
    }}
    QPushButton[variant="toolbar"]:hover {{ background-color: {accent}; border-color: {accent3}; }}
    QPushButton[variant="toolbar"]:pressed {{ background-color: {accent}; border-color: {accent3}; }}
    QPushButton[variant="source-picker"] {{
        background-color: {action_bg};
        border: 1px solid {action_bg};
        border-radius: 17px;
        padding: 0;
    }}
    QPushButton[variant="source-picker"]:hover {{
        background-color: {accent};
        border-color: {accent3};
    }}
    QPushButton[variant="source-picker"]:pressed {{
        background-color: {accent};
        border-color: {accent3};
    }}
    QPushButton[variant="history-action"] {{
        background-color: {action_bg};
        border: 1px solid {action_bg};
        border-radius: 17px;
        padding: 0;
        min-width: 32px;
        max-width: 32px;
        min-height: 32px;
        max-height: 32px;
    }}
    QPushButton[variant="history-action"]:hover {{
        background-color: {accent};
        border-color: {accent3};
    }}
    QPushButton[variant="history-action"][accent="danger"] {{
        background-color: {bg3};
        border-color: {err};
    }}
    QPushButton[variant="topbar"] {{
        padding: 0;
        min-width: 30px;
        max-width: 30px;
        min-height: 28px;
        max-height: 28px;
        border-radius: 8px;
    }}
    QPushButton[variant="topbar-text"] {{
        background-color: {bg3};
        border: 1px solid {action_bg};
        padding: 0 10px;
        text-align: center;
        min-height: 28px;
        max-height: 28px;
        border-radius: 8px;
        font-weight: 500;
    }}
    QPushButton[variant="toolbar-text"] {{
        background-color: {bg3};
        border: 1px solid {action_bg};
        padding: 0 24px;
        text-align: center;
        min-width: 132px;
        max-width: 152px;
        min-height: 32px;
        max-height: 32px;
        font-weight: 600;
    }}
    QPushButton[variant="toolbar-text"]::menu-indicator {{
        subcontrol-origin: padding;
        subcontrol-position: right center;
        right: 8px;
        width: 10px;
    }}
    QPushButton[variant="queue-folder"] {{
        background-color: {bg3};
        border: 1px solid {action_bg};
        padding: 0 10px;
        text-align: center;
        min-height: 32px;
        max-height: 32px;
        font-weight: 600;
    }}
    QPushButton[variant="header-text"] {{
        background-color: {bg3};
        border: 1px solid {action_bg};
        border-radius: 17px;
        padding: 0 24px;
        text-align: center;
        min-width: 132px;
        max-width: 152px;
        min-height: 34px;
        max-height: 34px;
        font-weight: 600;
    }}
    QPushButton[variant="header-text"]:hover {{
        background-color: {accent};
        border-color: {accent3};
    }}
    QPushButton[variant="header-text"]::menu-indicator {{
        subcontrol-origin: padding;
        subcontrol-position: right center;
        right: 7px;
        width: 10px;
    }}
    QPushButton[variant="header-icon"] {{
        background-color: {action_bg};
        border: 1px solid {action_bg};
        border-radius: 17px;
        padding: 0;
        min-width: 34px;
        max-width: 34px;
        min-height: 34px;
        max-height: 34px;
    }}
    QPushButton[variant="header-icon"]:hover {{
        background-color: {accent};
        border-color: {accent3};
    }}
    QPushButton[variant="header-icon"][accent="danger"] {{
        background-color: {bg3};
        border-color: {err};
    }}
    QPushButton[variant="header-icon"][accent="danger"]:hover {{
        background-color: {bg2};
        border-color: {err};
    }}
    QPushButton[variant="header-icon"][accent="danger"]:checked {{
        background-color: {err};
        border-color: {err};
    }}

    QPushButton[variant="segment"] {{
        background-color: {bg};
        color: {fg2};
        border: 1px solid {border};
        border-radius: 9px;
        /* Paint-safe inset for fractional Windows DPI scaling. */
        margin: 2px;
        padding: 5px 10px;
        min-height: 28px;
        font-weight: 500;
    }}
    QPushButton[variant="segment"]:hover {{
        color: {fg};
        border-color: {accent3};
    }}
    QPushButton[variant="segment"]:checked {{
        background-color: {bg3};
        color: {fg};
        border: 1px solid {action_bg};
        font-weight: 600;
    }}
    QPushButton[variant="section-tab"] {{
        background-color: transparent;
        color: {fg2};
        border: none;
        border-bottom: 2px solid transparent;
        border-radius: 0;
        padding: 2px 11px 7px 11px;
        min-height: 32px;
        font-weight: 500;
    }}
    QPushButton[variant="section-tab"]:hover {{
        color: {fg};
        border-bottom-color: {accent3};
    }}
    QPushButton[variant="section-tab"]:checked {{
        background-color: transparent;
        color: {fg};
        border: none;
        border-bottom: 2px solid {action_bg};
        font-weight: 600;
    }}
    QPushButton[variant="section-tab"]:focus {{
        color: {fg};
        border: none;
        border-bottom: 2px solid {accent3};
    }}

    /* Generic checkable buttons stay restrained; segmented toggles have their
       own stronger selector above. */
    QPushButton:checked {{
        background-color: {bg3};
        color: {fg};
        border: 1px solid {action_bg};
        font-weight: 600;
    }}

    QComboBox, QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox {{
        background-color: {control_bg};
        color: {control_fg};
        border: 1px solid {control_border};
        border-radius: 9px;
        font-size: 13px;
        font-weight: 400;
        padding: 6px 10px;
        min-height: 24px;
        selection-background-color: {accent};
        selection-color: #FFFFFF;
    }}
    QComboBox[variant="topbar"] {{
        padding: 2px 22px 2px 8px;
        min-height: 28px;
        max-height: 28px;
        border-radius: 8px;
        text-align: center;
        font-weight: 500;
    }}
    QComboBox[variant="topbar"]::drop-down {{
        border: none;
        subcontrol-origin: padding;
        subcontrol-position: right center;
        width: 18px;
        right: 4px;
    }}
    QComboBox:hover, QLineEdit:hover {{ border-color: {accent3}; }}
    QFrame#Card QComboBox,
    QFrame#Card QLineEdit,
    QFrame#Card QPlainTextEdit,
    QFrame#Card QTextEdit,
    QFrame#Card QSpinBox,
    QFrame#Card QDoubleSpinBox {{
        background-color: {control_bg};
        color: {control_fg};
    }}
    QComboBox QAbstractItemView {{
        background-color: {control_bg};
        color: {control_fg};
        border: 1px solid {control_border};
        font-family: "Segoe UI", "Arial", "Tahoma", sans-serif;
        font-size: 13px;
        font-weight: 400;
        selection-background-color: {bg3};
        selection-color: {control_fg};
        outline: 0;
    }}
    QComboBox QAbstractItemView::item {{
        min-height: 28px;
        padding: 5px 10px;
        font-size: 13px;
        font-weight: 400;
    }}
    QComboBox::drop-down {{
        border: none;
        subcontrol-origin: padding;
        subcontrol-position: right center;
        width: 22px;
        right: 6px;
    }}
    QComboBox::down-arrow {{
        image: url("{combo_arrow}");
        width: 12px;
        height: 12px;
    }}
    QComboBox::down-arrow:on {{
        top: 1px;
    }}

    QSpinBox[inlineButtons="true"], QDoubleSpinBox[inlineButtons="true"] {{
        padding-right: 76px;
    }}
    QToolButton#SpinStepDown, QToolButton#SpinStepUp {{
        background-color: transparent;
        border: none;
        border-radius: 7px;
        padding: 7px;
    }}
    QToolButton#SpinStepDown {{ image: url("{combo_arrow}"); }}
    QToolButton#SpinStepUp {{ image: url("{spin_up_arrow}"); }}
    QToolButton#SpinStepDown:hover, QToolButton#SpinStepUp:hover {{
        background-color: {bg3};
    }}
    QToolButton#SpinStepDown:pressed, QToolButton#SpinStepUp:pressed {{
        background-color: {action_bg};
    }}

    QCheckBox {{
        background-color: {control_bg};
        color: {fg};
        border: none;
        border-radius: 8px;
        padding: 4px 9px;
        spacing: 6px;
    }}
    QFrame#Card QCheckBox {{ background-color: {control_bg}; }}
    QRadioButton {{ background: transparent; color: {fg}; spacing: 6px; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 18px; height: 18px;
        border: 1px solid {control_border};
        border-radius: 5px;
        background: {control_bg};
    }}
    QRadioButton::indicator {{ border-radius: 8px; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
        background: {accent3};
        border-color: {accent3};
    }}
    QCheckBox::indicator:checked {{ image: url("{check_icon}"); }}
    QCheckBox::indicator:focus, QRadioButton::indicator:focus {{
        border: 2px solid {accent3};
    }}

    QTableView, QTreeView, QListView {{
        background-color: {bg2};
        alternate-background-color: {bg};
        color: {fg};
        border: 1px solid {border2};
        border-radius: 12px;
        gridline-color: {border2};
        selection-background-color: {accent};
        selection-color: #FFFFFF;
        outline: 0;
    }}
    QHeaderView::section {{
        background-color: {bg3};
        color: {fg2};
        border: none;
        border-right: 1px solid {border2};
        padding: 5px 8px;
        font-weight: 600;
    }}
    QTableView#WorkTable {{
        background-color: {work_bg};
        alternate-background-color: {work_bg};
        border: none;
        border-radius: 10px;
        gridline-color: transparent;
    }}
    QTableView#WorkTable::item {{
        border: none;
        padding: 0 7px;
    }}
    QTableView#WorkTable::item:selected {{
        background-color: {bg3};
        color: {fg};
    }}
    QTableView#WorkTable QHeaderView::section {{
        border: none;
        border-bottom: 1px solid {border2};
        padding: 6px 8px;
    }}
    QTableView#WorkTable QWidget {{
        background-color: transparent;
    }}

    QPlainTextEdit#LogView {{
        background-color: {work_bg};
        color: {log_fg};
        border: none;
        border-radius: 10px;
        font-family: "Cascadia Mono", "Consolas", "Courier New", monospace;
        font-size: 12px;
        padding: 8px;
    }}
    QProgressBar#InstallProgress {{
        background-color: {control_bg};
        border: 1px solid {control_border};
        border-radius: 7px;
        min-height: 12px;
        max-height: 12px;
        text-align: center;
    }}
    QProgressBar#InstallProgress::chunk {{
        background-color: {action_bg};
        border-radius: 6px;
    }}
    QScrollArea {{ border: none; background: transparent; }}
    /* Scroll bars: a plain dark pill on a transparent track. The Windows
       style paints a dither pattern on add-page/sub-page and around the
       handle unless every sub-control is styled explicitly. */
    QScrollBar:vertical {{
        background: transparent;
        border: none;
        width: 12px;
        margin: 0 2px 0 2px;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        border: none;
        height: 12px;
        margin: 2px 0 2px 0;
    }}
    QScrollBar::handle:vertical {{
        background: {bg3};
        border: none;
        border-radius: 4px;
        min-height: 32px;
    }}
    QScrollBar::handle:horizontal {{
        background: {bg3};
        border: none;
        border-radius: 4px;
        min-width: 32px;
    }}
    QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: {border}; }}
    QScrollBar::handle:vertical:pressed, QScrollBar::handle:horizontal:pressed {{ background: {fg3}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        height: 0;
        width: 0;
        border: none;
        background: none;
    }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; border: none; }}
    QScrollArea#SettingsScroll QScrollBar:vertical {{
        background: transparent;
        /* Same vertical span as the splitter line and the cards. */
        margin: 58px 2px 0 2px;
    }}
    QScrollArea#SettingsScroll > QWidget#qt_scrollarea_corner {{
        background: transparent;
        border: none;
    }}

    QTabWidget::pane {{ border: 1px solid {border2}; border-radius: 8px; top: -1px; }}
    QTabBar::tab {{
        background: {bg2};
        color: {fg2};
        padding: 7px 14px;
        border: 1px solid {border2};
        border-bottom: none;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
    }}
    QTabBar::tab:selected {{ background: {bg3}; color: {fg}; }}

    QStatusBar {{ background: {bg2}; color: {fg2}; }}
    QFrame#ShellRule {{ background: {border2}; border: none; }}
    QToolTip {{
        background-color: rgb(23, 33, 43);
        color: {fg};
        border: 1px solid {border};
        border-radius: 6px;
        padding: 6px 8px;
    }}
    QSplitter::handle {{ background: {border2}; }}
    QSplitter#MainSplitter::handle:horizontal {{
        background: transparent;
        border-left: 1px solid {border2};
        /* Span exactly the cards beside it: they start 56 px below the
           splitter top (tab bar + spacing) and end flush with its bottom. */
        margin: 56px 5px 0 5px;
    }}
    """.strip()


def status_color(theme: ThemeInfo, status: str) -> str:
    mapping = {
        "completed": "color-status-success",
        "processing": "color-accent-primary",
        "failed": "color-status-error",
        "skipped": "color-text-tertiary",
        "pending": "color-text-secondary",
    }
    token = mapping.get(status, "color-text-secondary")
    return theme.tokens.get(token, "#9FB0C3")
