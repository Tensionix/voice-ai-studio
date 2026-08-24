"""Live Dictation settings, parsed from the merged app settings (`live:` block).

Kept as a small typed view so the tray/controller don't reach into raw dicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core.terms import normalize_keyterms, shared_keyterms

VALID_MODES = ("push_to_talk", "toggle")
VALID_PASTE = ("clipboard", "type")
VALID_ENGINES = ("batch", "realtime", "vulkan", "xai_realtime", "elevenlabs_realtime")
VALID_SOURCES = ("api", "local")
VALID_API_PROVIDERS = ("openai", "xai", "elevenlabs")
VALID_API_MODES = ("batch", "realtime")
VALID_LOCAL_ENGINES = ("gigaam", "whispercpp")
VALID_LOCAL_BACKENDS = ("auto", "cuda", "cublas", "directml", "vulkan", "cpu")
VALID_LANGUAGES = ("layout", "auto", "ru", "en")

OVERLAY_SCALE_MIN = 70
OVERLAY_SCALE_MAX = 130
OVERLAY_SCALE_STEP = 5
OVERLAY_BASE_HEIGHT = 52

OPENAI_LIVE_BATCH_MODEL = "gpt-4o-mini-transcribe"
OPENAI_LIVE_REALTIME_MODEL = "gpt-realtime-whisper"
XAI_LIVE_REALTIME_MODEL = "grok-transcribe"
ELEVENLABS_LIVE_REALTIME_MODEL = "scribe_v2_realtime"

def fixed_live_api_model(provider: str, mode: str) -> str:
    provider = (provider or "openai").strip().lower()
    mode = (mode or "batch").strip().lower()
    if provider == "xai":
        return XAI_LIVE_REALTIME_MODEL
    if provider == "elevenlabs":
        return ELEVENLABS_LIVE_REALTIME_MODEL
    return OPENAI_LIVE_REALTIME_MODEL if mode == "realtime" else OPENAI_LIVE_BATCH_MODEL


@dataclass(frozen=True)
class LiveConfig:
    enabled: bool = True
    prewarm_local: bool = True        # locked: selected Live models stay resident
    minimize_to_tray: bool = True
    hotkey: str = "ralt+f12"          # safe start gesture; no Windows key
    toggle_hotkey: str = ""            # optional secondary toggle chord
    mode: str = "toggle"              # toggle = start gesture, Stop button ends
    paste_method: str = "clipboard"   # clipboard | type
    source: str = "api"               # api | local
    api_provider: str = "openai"      # openai | xai | elevenlabs
    api_mode: str = "batch"           # OpenAI only: batch | realtime
    engine: str = "batch"             # batch | realtime | vulkan
    show_overlay: bool = True
    overlay_scale_percent: int = 70
    safety_timeout_minutes: int = 15
    language: str = "layout"         # target window layout; terms may be multilingual
    keyterms: tuple[str, ...] = ()    # names, abbreviations, formats, technical vocabulary
    model: str = OPENAI_LIVE_BATCH_MODEL
    local_engine: str = "gigaam"       # gigaam | whispercpp
    local_model: str = "gigaam-v3-e2e-ctc"
    local_backend: str = "cpu"
    local_idle_unload_seconds: int = 0      # locked: unload only on model change/app exit
    layout_local_routing: bool = True
    layout_ru_model: str = "gigaam-v3-e2e-ctc"
    layout_en_model: str = "turbo"
    cleanup_enabled: bool = False
    cleanup_model: str = "gpt-5.4-mini"
    cleanup_prompt_id: str = "builtin.cleanup.default"
    cleanup_sentence_threshold: int = 12

    @property
    def overlay_height(self) -> int:
        """Legacy pixel view for callers migrating from pre-scale settings."""
        return round(OVERLAY_BASE_HEIGHT * self.overlay_scale_percent / 100)

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> "LiveConfig":
        live = settings.get("live") if isinstance(settings, dict) else None
        live = live if isinstance(live, dict) else {}
        api = live.get("api") if isinstance(live.get("api"), dict) else {}
        local = live.get("local") if isinstance(live.get("local"), dict) else {}
        layout_routing = (
            live.get("layout_routing")
            if isinstance(live.get("layout_routing"), dict)
            else {}
        )
        cleanup = live.get("cleanup") if isinstance(live.get("cleanup"), dict) else {}
        default_local_engine = "gigaam" if str(settings.get("ui_language", "ru")).lower() == "ru" else "whispercpp"
        default_local_model = "gigaam-v3-e2e-ctc" if default_local_engine == "gigaam" else "turbo"

        def _str(key: str, default: str, allowed: tuple[str, ...] | None = None) -> str:
            value = str(live.get(key, default) or default).strip()
            if allowed and value not in allowed:
                return default
            return value

        def _local_str(key: str, default: str, allowed: tuple[str, ...] | None = None) -> str:
            value = str(local.get(key, default) or default).strip()
            if allowed and value not in allowed:
                return default
            return value

        def _api_str(key: str, default: str, allowed: tuple[str, ...] | None = None) -> str:
            value = str(api.get(key, default) or default).strip()
            if allowed and value not in allowed:
                return default
            return value

        def _int(node: dict[str, Any], key: str, default: int, *, minimum: int = 0) -> int:
            try:
                return max(minimum, int(node.get(key, default) or 0))
            except (TypeError, ValueError):
                return default

        local_engine = _local_str("engine", default_local_engine, VALID_LOCAL_ENGINES)
        local_model = _local_str("model", default_local_model)
        if local_engine == "gigaam" and not local_model.startswith("gigaam-"):
            local_model = "gigaam-v3-e2e-ctc"
        elif local_engine == "whispercpp" and local_model.startswith("gigaam-"):
            local_model = "turbo"
        legacy_engine = _str("engine", "batch", VALID_ENGINES)
        source = _str("source", "local" if legacy_engine == "vulkan" else "api", VALID_SOURCES)
        api_provider = _api_str("provider", "openai", VALID_API_PROVIDERS)
        if legacy_engine.startswith("xai"):
            api_provider = "xai"
        elif legacy_engine.startswith("elevenlabs"):
            api_provider = "elevenlabs"
        api_mode = _api_str(
            "mode",
            legacy_engine if legacy_engine in VALID_API_MODES else "realtime",
            VALID_API_MODES,
        )
        if source == "local":
            engine = "vulkan"
        elif api_provider == "openai":
            engine = api_mode
        else:
            engine = f"{api_provider}_realtime"
        model = fixed_live_api_model(api_provider, api_mode)
        if "language" in live:
            language = _str("language", "layout", VALID_LANGUAGES)
        else:
            language = "layout"
        keyterms = normalize_keyterms(live.get("keyterms", shared_keyterms(settings)))

        # New builds persist a percentage.  Older builds stored only the bar
        # height, so translate that value once while retaining the old visual
        # size as closely as the five-percent scale steps allow.
        if "overlay_height" in live:
            try:
                legacy_height = max(
                    OVERLAY_BASE_HEIGHT,
                    float(live.get("overlay_height", OVERLAY_BASE_HEIGHT) or OVERLAY_BASE_HEIGHT),
                )
            except (TypeError, ValueError):
                legacy_height = float(OVERLAY_BASE_HEIGHT)
            raw_overlay_scale = legacy_height * 100.0 / OVERLAY_BASE_HEIGHT
        else:
            try:
                raw_overlay_scale = float(live.get("overlay_scale_percent", 70) or 70)
            except (TypeError, ValueError):
                raw_overlay_scale = 70.0
        overlay_scale_percent = int(
            round(raw_overlay_scale / OVERLAY_SCALE_STEP) * OVERLAY_SCALE_STEP
        )
        overlay_scale_percent = max(
            OVERLAY_SCALE_MIN,
            min(OVERLAY_SCALE_MAX, overlay_scale_percent),
        )

        return cls(
            enabled=bool(live.get("enabled", True)),
            # Resident Live models are policy, not a user preference.
            prewarm_local=True,
            minimize_to_tray=bool(live.get("minimize_to_tray", True)),
            hotkey=_str("hotkey", "ralt+f12"),
            toggle_hotkey=_str("toggle_hotkey", ""),
            mode=_str("mode", "toggle", VALID_MODES),
            paste_method=_str("paste_method", "clipboard", VALID_PASTE),
            source=source,
            api_provider=api_provider,
            api_mode=api_mode,
            engine=engine,
            show_overlay=bool(live.get("show_overlay", True)),
            overlay_scale_percent=overlay_scale_percent,
            safety_timeout_minutes=_int(live, "safety_timeout_minutes", 15, minimum=1),
            language=language,
            keyterms=keyterms,
            model=model,
            local_engine=local_engine,
            local_model=local_model,
            local_backend=_local_str("backend", "cpu", VALID_LOCAL_BACKENDS),
            local_idle_unload_seconds=0,
            layout_local_routing=bool(layout_routing.get("enabled", True)),
            layout_ru_model=str(
                layout_routing.get("ru_model", "gigaam-v3-e2e-ctc")
                or "gigaam-v3-e2e-ctc"
            ),
            layout_en_model=str(layout_routing.get("en_model", "turbo") or "turbo"),
            cleanup_enabled=bool(cleanup.get("enabled", False)),
            cleanup_model=str(cleanup.get("model", "gpt-5.4-mini") or "gpt-5.4-mini"),
            cleanup_prompt_id=str(cleanup.get("prompt_id", "builtin.cleanup.default") or "builtin.cleanup.default"),
            cleanup_sentence_threshold=_int(cleanup, "sentence_threshold", 12, minimum=1),
        )
