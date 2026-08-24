"""Headless tests for Live dictation (iteration 3): config, the controller flow,
the batch transcriber, the hotkey chord logic, and the tray menu.

Engine pieces (OS hook, mic, network) are injected as fakes so the flow is
exercised deterministically without touching real hardware. Skips when PySide6
isn't installed; uses offscreen Qt.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading

import pytest

# Mirror test_gui_smoke: make a project-local .devlibs importable if present.
_DEVLIBS = Path(__file__).resolve().parents[1] / ".devlibs"
if _DEVLIBS.exists():
    import sys

    sys.path.insert(0, str(_DEVLIBS))

pytest.importorskip("PySide6", reason="GUI deps not installed (requirements_full.in)")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QSize, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from system_core.core.models import Segment  # noqa: E402
from system_core.core.paths import get_project_paths  # noqa: E402
from system_core.live import LiveConfig, LiveController, LiveState  # noqa: E402
from system_core.live.hotkey import HotkeyListener, parse_chord, parse_hotkey  # noqa: E402
from system_core.live.transcriber import BatchLiveTranscriber, write_wav_pcm16  # noqa: E402
from system_core.settings import DEFAULT_SETTINGS  # noqa: E402
from system_core.providers.realtime_provider import (  # noqa: E402
    _COMPLETED,
    _DELTA,
    _FAILED,
    RealtimeLiveTranscriber,
)
from system_core.live.tray import VoiceTray  # noqa: E402
from system_core.providers.base import (  # noqa: E402
    LiveChunkResult,
    LiveOptions,
    TranscriptResult,
)
from system_core.ui.i18n import Translator  # noqa: E402
from system_core.ui.icons import app_icon  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_foreground_window_calls_win32_api(monkeypatch):
    from system_core.live import focus

    class FakeCall:
        restype = None

        def __call__(self):
            return 4242

    class FakeCtypes:
        windll = type("Windll", (), {"user32": type(
            "User32", (), {"GetForegroundWindow": FakeCall()}
        )()})()

    monkeypatch.setattr(focus.sys, "platform", "win32")
    monkeypatch.setattr(focus, "ctypes", FakeCtypes)

    assert focus.get_foreground_window() == 4242


# --- fakes -------------------------------------------------------------------
class FakeHotkey:
    def __init__(self):
        self.installed = False
        self.closed = False

    def install(self):
        self.installed = True

    def close(self):
        self.closed = True


class FakeCapture:
    def __init__(self, data: bytes = b"\x00\x01" * 16):
        self._data = data
        self.started = False

    def start(self):
        self.started = True

    def stop(self) -> bytes:
        self.started = False
        return self._data

    def close(self):
        pass


class FakeTranscriber:
    def __init__(self, text: str = "hello world"):
        self._text = text
        self.fed = b""
        self.options = None

    def start(self, options):
        self.fed = b""
        self.options = options

    def feed(self, pcm):
        self.fed += pcm
        return None

    def finish(self):
        return LiveChunkResult(self._text, True)

    def close(self):
        pass


class FakeStreamingTranscriber:
    """A streaming-style transcriber: frames arrive live and produce partials;
    finish() flushes a trailing segment via on_segment (like server-VAD)."""

    streaming = True
    on_partial = None
    on_segment = None

    def __init__(self, tail: str = ""):
        self._tail = tail          # segment emitted on finish() (the flushed tail)
        self.fed = b""
        self.finished = False

    def start(self, options):
        self.fed = b""
        self.finished = False

    def feed(self, pcm):
        self.fed += pcm
        if pcm and self.on_partial:
            self.on_partial(f"partial:{len(self.fed)}")
        return None

    def finish(self):
        self.finished = True
        if self._tail and self.on_segment:
            self.on_segment(self._tail)   # trailing sentence flushed on stop
        return LiveChunkResult(self._tail, True)

    def close(self):
        pass


class FakeStreamingCapture:
    """Streams frames via on_frame; stop() returns nothing (already streamed)."""

    def __init__(self):
        self.on_frame = None
        self.started = False

    def start(self):
        self.started = True

    def stop(self) -> bytes:
        self.started = False
        return b""

    def close(self):
        pass


class FakePrebufferStreamingCapture(FakeStreamingCapture):
    """Mimics AudioCapture after the mic starts before Realtime is ready."""

    def __init__(self, prebuffer: bytes):
        super().__init__()
        self._prebuffer = prebuffer

    def set_frame_sink(self, sink, *, flush_existing=False):
        self.on_frame = sink
        if not flush_existing:
            return b""
        data, self._prebuffer = self._prebuffer, b""
        return data


class FakeRealtimeConn:
    """Minimal stand-in for an OpenAI Realtime connection.

    Server-VAD is simulated: deltas and the completed/failed event are all queued
    up-front (as if the server transcribed while audio streamed). We never expose
    a commit() call — the provider must not manually commit."""

    import queue as _queue

    def __init__(self, deltas=(), final="", error=None):
        self._q = FakeRealtimeConn._queue.Queue()
        for d in deltas:
            self._q.put({"type": _DELTA, "delta": d})
        if error is not None:
            self._q.put({"type": _FAILED, "error": error})
        else:
            self._q.put({"type": _COMPLETED, "transcript": final})
        self.appended: list[str] = []
        self.committed = False
        self.closed = False

    class _Buf:
        def __init__(self, outer):
            self._o = outer

        def append(self, audio):
            self._o.appended.append(audio)

        def commit(self):  # must NOT be called (server-VAD owns commits)
            self._o.committed = True

    @property
    def input_audio_buffer(self):
        return FakeRealtimeConn._Buf(self)

    def recv(self):
        return self._q.get()  # blocks until an event (or None on close)

    def close(self):
        self.closed = True
        self._q.put(None)


class FakeManualCommitRealtimeConn:
    import queue as _queue

    def __init__(self, final="привет"):
        self._q = FakeManualCommitRealtimeConn._queue.Queue()
        self._final = final
        self.appended: list[str] = []
        self.committed = False
        self.closed = False

    class _Buf:
        def __init__(self, outer):
            self._o = outer

        def append(self, audio):
            self._o.appended.append(audio)

        def commit(self):
            self._o.committed = True
            self._o._q.put({"type": _COMPLETED, "transcript": self._o._final})

    @property
    def input_audio_buffer(self):
        return FakeManualCommitRealtimeConn._Buf(self)

    def recv(self):
        return self._q.get()

    def close(self):
        self.closed = True
        self._q.put(None)


# --- config ------------------------------------------------------------------
def test_live_config_defaults():
    cfg = LiveConfig.from_settings({})
    assert cfg.enabled is True
    assert cfg.mode == "toggle"
    assert cfg.paste_method == "clipboard"
    assert cfg.hotkey == "ralt+f12"
    assert cfg.source == "api"
    assert cfg.api_provider == "openai"
    assert cfg.api_mode == "batch"
    assert cfg.engine == "batch"
    assert cfg.model == "gpt-4o-mini-transcribe"
    assert cfg.local_engine == "gigaam"
    assert cfg.local_model == "gigaam-v3-e2e-ctc"
    assert cfg.overlay_scale_percent == 70
    assert cfg.overlay_height == 36  # compatibility view for legacy callers
    assert cfg.local_backend == "cpu"
    assert cfg.safety_timeout_minutes == 15
    assert cfg.language == "layout"
    assert cfg.layout_local_routing is True
    assert cfg.layout_ru_model == "gigaam-v3-e2e-ctc"
    assert cfg.layout_en_model == "turbo"
    assert cfg.cleanup_enabled is False
    assert cfg.cleanup_sentence_threshold == 12


def test_live_config_uses_existing_dictionary_and_explicit_language():
    cfg = LiveConfig.from_settings(
        {
            "ui_language": "ru",
            "language": "auto",
            "live": {"language": "ru"},
            "term_dictionary": ["ИАС УГРТ", "API", "YAML", "api"],
        }
    )

    assert cfg.language == "ru"
    assert cfg.keyterms == ("ИАС УГРТ", "API", "YAML")


def test_live_config_reads_legacy_cleanup_dictionary():
    cfg = LiveConfig.from_settings(
        {"postprocessing": {"cleanup": {"hotwords": ["LegacyTerm"]}}}
    )

    assert cfg.keyterms == ("LegacyTerm",)


def test_live_config_defaults_whispercpp_for_en_layout():
    cfg = LiveConfig.from_settings({"ui_language": "en"})
    assert cfg.local_engine == "whispercpp"
    assert cfg.local_model == "turbo"


def test_clean_build_defaults_to_free_layout_routing():
    cfg = LiveConfig.from_settings(DEFAULT_SETTINGS)

    assert cfg.overlay_scale_percent == 70
    assert cfg.local_backend == "cpu"
    assert cfg.source == "local"
    assert cfg.language == "layout"
    assert cfg.layout_local_routing is True
    assert cfg.layout_ru_model == "gigaam-v3-e2e-ctc"
    assert cfg.layout_en_model == "turbo"


def test_live_config_routes_xai_and_elevenlabs_api():
    xai = LiveConfig.from_settings(
        {"live": {"source": "api", "api": {"provider": "xai", "model": "grok-transcribe"}}}
    )
    eleven = LiveConfig.from_settings(
        {"live": {"source": "api", "api": {"provider": "elevenlabs", "model": "scribe_v2_realtime"}}}
    )

    assert xai.engine == "xai_realtime"
    assert xai.model == "grok-transcribe"
    assert eleven.engine == "elevenlabs_realtime"
    assert eleven.model == "scribe_v2_realtime"


def test_live_config_routes_openai_to_fixed_models():
    batch = LiveConfig.from_settings(
        {"live": {"source": "api", "api": {"provider": "openai", "mode": "batch", "model": "whisper-1"}}}
    )
    realtime = LiveConfig.from_settings(
        {"live": {"source": "api", "api": {"provider": "openai", "mode": "realtime", "model": "gpt-4o-transcribe"}}}
    )

    assert batch.engine == "batch"
    assert batch.model == "gpt-4o-mini-transcribe"
    assert realtime.engine == "realtime"
    assert realtime.model == "gpt-realtime-whisper"


def test_live_config_keeps_fixed_realtime_models_from_legacy_openai_model():
    cfg = LiveConfig.from_settings(
        {
            "live": {
                "source": "api",
                "model": "gpt-4o-mini-transcribe",
                "api": {"provider": "xai", "models": {"xai": "grok-transcribe"}},
            }
        }
    )

    assert cfg.engine == "xai_realtime"
    assert cfg.model == "grok-transcribe"


def test_live_config_overrides_and_validation():
    cfg = LiveConfig.from_settings(
        {
            "live": {
                "enabled": False,
                "mode": "toggle",
                "paste_method": "bogus",
                "hotkey": "f9",
                "engine": "vulkan",
                "overlay_height": 36,
                "safety_timeout_minutes": 25,
                "local": {"engine": "whispercpp", "model": "large-v2", "backend": "cpu", "idle_unload_seconds": 30},
                "cleanup": {
                    "enabled": True,
                    "model": "gpt-4o",
                    "prompt_id": "custom.live",
                    "sentence_threshold": 8,
                },
            }
        }
    )
    assert cfg.enabled is False
    assert cfg.mode == "toggle"
    assert cfg.paste_method == "clipboard"  # invalid value falls back to default
    assert cfg.hotkey == "f9"
    assert cfg.engine == "vulkan"
    assert cfg.source == "local"
    assert cfg.local_engine == "whispercpp"
    assert cfg.local_model == "large-v2"
    assert cfg.local_backend == "cpu"
    assert cfg.local_idle_unload_seconds == 0
    assert cfg.overlay_scale_percent == 100
    assert cfg.safety_timeout_minutes == 25
    assert cfg.cleanup_enabled is True
    assert cfg.cleanup_model == "gpt-4o"
    assert cfg.cleanup_prompt_id == "custom.live"
    assert cfg.cleanup_sentence_threshold == 8


def test_live_config_migrates_and_normalizes_overlay_scale():
    legacy = LiveConfig.from_settings(
        {"live": {"overlay_scale_percent": 100, "overlay_height": 64}}
    )
    explicit = LiveConfig.from_settings({"live": {"overlay_scale_percent": 128}})
    minimum = LiveConfig.from_settings({"live": {"overlay_scale_percent": 20}})

    assert legacy.overlay_scale_percent == 125
    assert explicit.overlay_scale_percent == 130
    assert minimum.overlay_scale_percent == 70


# --- controller --------------------------------------------------------------
def _controller(app, **kw):
    paths = get_project_paths()
    kw.setdefault("hotkey_factory", lambda: FakeHotkey())
    kw.setdefault("synchronous", True)
    return LiveController(paths, LiveConfig.from_settings({}), **kw)


def test_controller_passes_dominant_language_and_dictionary(app):
    cfg = LiveConfig.from_settings(
        {
            "live": {"language": "ru"},
            "term_dictionary": ["PowerShell", "ИАС УГРТ"],
        }
    )
    ctrl = LiveController(
        get_project_paths(),
        cfg,
        hotkey_factory=lambda: FakeHotkey(),
        synchronous=True,
    )

    options = ctrl._live_options()

    assert options.language == "ru"
    assert options.keyterms == ("PowerShell", "ИАС УГРТ")
    assert options.context == "PowerShell, ИАС УГРТ"


def test_controller_resolves_target_window_layout_language(app, monkeypatch):
    from system_core.live import focus

    cfg = LiveConfig.from_settings({"live": {"language": "layout"}})
    ctrl = LiveController(
        get_project_paths(),
        cfg,
        hotkey_factory=lambda: FakeHotkey(),
        synchronous=True,
    )
    ctrl._target_hwnd = 123
    monkeypatch.setattr(focus, "get_window_language", lambda hwnd: "ru" if hwnd == 123 else "auto")

    assert ctrl._live_options().language == "ru"


def test_controller_routes_local_engine_by_target_layout(app, monkeypatch):
    from system_core.live import focus

    cfg = LiveConfig.from_settings(
        {
            "live": {
                "source": "local",
                "language": "layout",
                "local": {
                    "engine": "gigaam",
                    "model": "gigaam-v3-e2e-ctc",
                    "backend": "auto",
                },
                "layout_routing": {
                    "enabled": True,
                    "ru_model": "gigaam-v3-e2e-ctc",
                    "en_model": "turbo",
                },
            }
        }
    )
    language = ["ru"]
    created: list[tuple[str, str]] = []
    options = []

    class _RoutedTranscriber(FakeTranscriber):
        def start(self, live_options):
            options.append(live_options)
            super().start(live_options)

    def transcriber_factory():
        created.append((ctrl._session_local_engine, ctrl._session_local_model))
        return _RoutedTranscriber("тест")

    monkeypatch.setattr(focus, "get_foreground_window", lambda: 123)
    monkeypatch.setattr(focus, "get_window_language", lambda hwnd: language[0])
    ctrl = LiveController(
        get_project_paths(),
        cfg,
        hotkey_factory=lambda: FakeHotkey(),
        capture_factory=lambda: FakeCapture(),
        transcriber_factory=transcriber_factory,
        paster=lambda text: None,
        synchronous=True,
    )

    ctrl.set_armed(True)
    ctrl.begin_utterance()
    ctrl.end_utterance()
    language[0] = "en"
    ctrl.begin_utterance()
    ctrl.end_utterance()

    assert created == [
        ("gigaam", "gigaam-v3-e2e-ctc"),
        ("whispercpp", "turbo"),
    ]
    assert [(item.language, item.model) for item in options] == [
        ("ru", "gigaam-v3-e2e-ctc"),
        ("en", "turbo"),
    ]
    ctrl.shutdown()


def test_layout_does_not_override_a_saved_api_source(app, monkeypatch):
    from system_core.live import focus

    cfg = LiveConfig.from_settings(
        {
            "live": {
                "source": "api",
                "language": "layout",
                "api": {"provider": "xai", "mode": "realtime"},
                "local": {"engine": "whispercpp", "model": "turbo"},
            }
        }
    )
    monkeypatch.setattr(focus, "get_window_language", lambda hwnd: "ru")
    ctrl = LiveController(
        get_project_paths(),
        cfg,
        hotkey_factory=lambda: FakeHotkey(),
        synchronous=True,
    )

    ctrl._resolve_session_route()

    assert ctrl._session_language == "ru"
    assert ctrl._session_engine == "xai_realtime"
    assert ctrl._session_local_engine == "whispercpp"
    assert ctrl._session_local_model == "turbo"


def test_controller_arm_disarm(app):
    made: list[FakeHotkey] = []

    def factory():
        hk = FakeHotkey()
        made.append(hk)
        return hk

    ctrl = _controller(app, hotkey_factory=factory)
    seen: list[LiveState] = []
    ctrl.state_changed.connect(seen.append)

    assert ctrl.toggle() is True
    assert ctrl.state() == LiveState.ARMED
    assert made[0].installed is True

    assert ctrl.toggle() is False
    assert ctrl.state() == LiveState.DISABLED
    assert made[0].closed is True

    # Re-arm must work — disabling Live is not a one-way trip.
    assert ctrl.toggle() is True
    assert ctrl.state() == LiveState.ARMED
    assert len(made) == 2 and made[1].installed is True

    assert seen == [LiveState.ARMED, LiveState.DISABLED, LiveState.ARMED]


def test_controller_engine_switch_prewarms_and_frees(app):
    class _VulkanFake:
        streaming = False

        def __init__(self):
            self.warmed = 0
            self.closed = False

        def warm(self):
            self.warmed += 1

        def start(self, options):
            pass

        def feed(self, pcm):
            return None

        def finish(self):
            return LiveChunkResult("", True)

        def close(self):
            self.closed = True

    tr = _VulkanFake()
    ctrl = _controller(app, transcriber_factory=lambda: tr)

    ctrl.set_armed(True)            # default engine=batch -> no pre-warm
    assert tr.warmed == 0

    ctrl.update_config(
        LiveConfig.from_settings({"live": {"engine": "vulkan", "prewarm_local": True}})
    )
    assert tr.warmed == 1           # switched into Vulkan while armed -> warm

    ctrl.update_config(LiveConfig.from_settings({"live": {"engine": "batch"}}))
    assert tr.closed is True        # switched away -> resident model freed

    ctrl.shutdown()


def test_controller_local_model_is_always_prewarmed(app):
    class _VulkanFake:
        streaming = False

        def __init__(self):
            self.warmed = 0

        def warm(self):
            self.warmed += 1

        def close(self):
            pass

    tr = _VulkanFake()
    ctrl = _controller(app, transcriber_factory=lambda: tr)
    ctrl.update_config(LiveConfig.from_settings({"live": {"engine": "vulkan"}}))
    ctrl.set_armed(True)
    assert tr.warmed == 1
    ctrl.shutdown()


def test_live_config_locks_local_models_resident():
    migrated = LiveConfig.from_settings({"live": {"autostart": True}})
    explicit = LiveConfig.from_settings(
        {"live": {"autostart": True, "prewarm_local": False}}
    )
    assert migrated.prewarm_local is True
    assert explicit.prewarm_local is True
    assert explicit.local_idle_unload_seconds == 0


def test_controller_vulkan_setting_change_restarts_resident_transcriber(app):
    class _VulkanFake:
        streaming = False

        def __init__(self):
            self.warmed = 0
            self.closed = False

        def warm(self):
            self.warmed += 1

        def start(self, options):
            pass

        def feed(self, pcm):
            return None

        def finish(self):
            return LiveChunkResult("", True)

        def close(self):
            self.closed = True

    made: list[_VulkanFake] = []

    def factory():
        transcriber = _VulkanFake()
        made.append(transcriber)
        return transcriber

    ctrl = LiveController(
        get_project_paths(),
        LiveConfig.from_settings(
            {
                "live": {
                    "engine": "vulkan",
                    "prewarm_local": True,
                    "local": {"engine": "whispercpp", "model": "turbo"},
                }
            }
        ),
        hotkey_factory=lambda: FakeHotkey(),
        transcriber_factory=factory,
        synchronous=True,
    )

    ctrl.set_armed(True)
    assert len(made) == 1
    assert made[0].warmed == 1
    switches: list[str] = []
    ctrl.model_switch_finished.connect(lambda _signature: switches.append("ready"))

    ctrl.update_config(
        LiveConfig.from_settings(
            {
                "live": {
                    "engine": "vulkan",
                    "prewarm_local": True,
                    "local": {
                        "engine": "whispercpp",
                        "model": "large-v2",
                        "backend": "cpu",
                        "idle_unload_seconds": 5,
                    },
                }
            }
        )
    )

    assert made[0].closed is True
    assert len(made) == 2
    assert made[1].warmed == 1
    assert switches == ["ready"]

    ctrl.shutdown()


def test_default_controller_keeps_layout_models_resident_until_replaced_or_shutdown(app, monkeypatch):
    class _ResidentFake:
        streaming = False

        def __init__(self, engine, model):
            self.engine = engine
            self.model = model
            self.warmed = 0
            self.closed = False

        def warm(self):
            self.warmed += 1

        def close(self):
            self.closed = True

    cfg = LiveConfig.from_settings(
        {
            "live": {
                "enabled": True,
                "source": "local",
                "language": "layout",
                "local": {"engine": "gigaam", "model": "gigaam-v3-e2e-ctc", "backend": "auto"},
                "layout_routing": {
                    "enabled": True,
                    "ru_model": "gigaam-v3-e2e-ctc",
                    "en_model": "turbo",
                },
            }
        }
    )
    ctrl = LiveController(
        get_project_paths(),
        cfg,
        hotkey_factory=lambda: FakeHotkey(),
        synchronous=True,
    )
    made = []

    def create(engine, model):
        item = _ResidentFake(engine, model)
        made.append(item)
        return item

    monkeypatch.setattr(ctrl, "_create_local_transcriber", create)
    monkeypatch.setattr(ctrl, "_prewarm", ctrl._reconcile_resident_transcribers)

    ctrl._prewarm()
    assert {(item.engine, item.model) for item in made} == {
        ("gigaam", "gigaam-v3-e2e-ctc"),
        ("whispercpp", "turbo"),
    }
    assert all(item.warmed == 1 and not item.closed for item in made)

    ctrl._state = LiveState.ARMED
    ctrl.set_armed(False)
    assert all(not item.closed for item in made)

    ctrl.update_config(
        LiveConfig.from_settings(
            {
                "live": {
                    "enabled": True,
                    "source": "local",
                    "language": "en",
                    "local": {
                        "engine": "whispercpp",
                        "model": "large-v2",
                        "backend": "auto",
                    },
                }
            }
        )
    )
    assert all(item.closed for item in made[:2])
    replacement = made[-1]
    assert (replacement.engine, replacement.model) == ("whispercpp", "large-v2")
    assert replacement.warmed == 1 and not replacement.closed

    ctrl.shutdown()
    assert replacement.closed


def test_controller_shutdown_waits_for_resident_warm_before_close(app, monkeypatch):
    warm_started = threading.Event()
    release_warm = threading.Event()
    closed_after_warm: list[bool] = []

    class _SlowResident:
        streaming = False

        def warm(self):
            warm_started.set()
            assert release_warm.wait(2.0)

        def close(self):
            closed_after_warm.append(release_warm.is_set())

    cfg = LiveConfig.from_settings(
        {
            "live": {
                "source": "local",
                "language": "ru",
                "local": {"engine": "gigaam", "model": "gigaam-v3-e2e-ctc"},
            }
        }
    )
    ctrl = LiveController(
        get_project_paths(),
        cfg,
        hotkey_factory=lambda: FakeHotkey(),
        synchronous=True,
    )
    monkeypatch.setattr(ctrl, "_create_local_transcriber", lambda *_args: _SlowResident())

    ctrl._prewarm()
    assert warm_started.wait(1.0)
    threading.Timer(0.05, release_warm.set).start()
    ctrl.shutdown()

    assert closed_after_warm == [True]


def test_controller_arm_failure(app):
    def boom():
        raise RuntimeError("no hook here")

    ctrl = _controller(app, hotkey_factory=boom)
    notices: list[str] = []
    errors: list[str] = []
    ctrl.notice.connect(notices.append)
    ctrl.error.connect(errors.append)

    assert ctrl.toggle() is False
    assert ctrl.state() == LiveState.DISABLED
    assert notices == ["live_hotkey_failed"]
    assert errors and "no hook" in errors[0]


def test_controller_utterance_flow(app):
    pasted: list[str] = []
    committed: list[str] = []
    capture = FakeCapture()
    transcriber = FakeTranscriber("привет мир")
    ctrl = _controller(
        app,
        capture_factory=lambda: capture,
        transcriber_factory=lambda: transcriber,
        paster=pasted.append,
    )
    ctrl.text_committed.connect(committed.append)

    ctrl.set_armed(True)
    ctrl.begin_utterance()
    assert ctrl.state() == LiveState.LISTENING
    assert capture.started is True

    ctrl.end_utterance()  # synchronous: transcribe + paste inline
    assert ctrl.state() == LiveState.ARMED
    assert pasted == ["привет мир"]
    assert committed == ["привет мир"]
    assert transcriber.fed == capture._data

    ctrl.shutdown()
    assert ctrl.state() == LiveState.DISABLED


def test_controller_cancel_discards_batch_result(app):
    pasted: list[str] = []
    capture = FakeCapture()
    transcriber = FakeTranscriber("не вставлять")
    ctrl = _controller(
        app,
        capture_factory=lambda: capture,
        transcriber_factory=lambda: transcriber,
        paster=pasted.append,
    )
    notices: list[str] = []
    ctrl.notice.connect(notices.append)
    ctrl.set_armed(True)
    ctrl.begin_utterance()
    session_id = ctrl._session_id

    ctrl.cancel_utterance()
    ctrl._transcribed.emit((session_id, "запоздалый текст"))

    assert ctrl.state() == LiveState.ARMED
    assert pasted == []
    assert "live_cancelled" in notices


def test_controller_streaming_flow(app):
    pasted: list[str] = []
    committed: list[str] = []
    partials: list[str] = []
    capture = FakeStreamingCapture()
    transcriber = FakeStreamingTranscriber("привет мир")  # flushed as a tail segment
    ctrl = _controller(
        app,
        capture_factory=lambda: capture,
        transcriber_factory=lambda: transcriber,
        paster=pasted.append,
    )
    ctrl.text_committed.connect(committed.append)
    ctrl.partial.connect(partials.append)

    ctrl.set_armed(True)
    ctrl.begin_utterance()
    assert ctrl.state() == LiveState.LISTENING
    assert ctrl._paste_segments is False  # realtime stays buffered until Stop
    # Streaming engine: frames are routed straight to the transcriber, live.
    assert callable(capture.on_frame)
    capture.on_frame(b"\x00\x01" * 10)  # simulate a mic frame
    assert partials and partials[-1].startswith("partial:")

    ctrl.end_utterance()  # synchronous: flush tail (pasted via on_segment) + re-arm
    assert transcriber.finished is True
    assert ctrl.state() == LiveState.ARMED
    assert pasted == ["привет мир"]       # pasted once when Stop finalizes
    assert committed == ["привет мир"]


def test_toggle_mode_ignores_hotkey_release_until_stop(app):
    transcriber = FakeStreamingTranscriber("готово")
    ctrl = _controller(
        app,
        capture_factory=lambda: FakeStreamingCapture(),
        transcriber_factory=lambda: transcriber,
        paster=lambda _text: None,
    )
    ctrl.set_armed(True)
    ctrl._handle_press()
    assert ctrl.state() == LiveState.LISTENING

    ctrl._handle_release()
    assert ctrl.state() == LiveState.LISTENING

    ctrl.stop_utterance()
    assert ctrl.state() == LiveState.ARMED
    assert transcriber.finished is True


def test_controller_safety_timeout_finishes_active_session(app):
    notices: list[str] = []
    transcriber = FakeStreamingTranscriber("защитный финал")
    ctrl = _controller(
        app,
        capture_factory=lambda: FakeStreamingCapture(),
        transcriber_factory=lambda: transcriber,
        paster=lambda _text: None,
    )
    ctrl.notice.connect(notices.append)
    ctrl.set_armed(True)
    ctrl.begin_utterance()
    assert ctrl._safety_timer.isActive()

    ctrl._on_safety_timeout()

    assert "live_safety_timeout_notice" in notices
    assert ctrl.state() == LiveState.ARMED
    assert transcriber.finished is True


def test_controller_streaming_flushes_startup_prebuffer(app):
    prebuffer = b"\x01\x02" * 64
    capture = FakePrebufferStreamingCapture(prebuffer)
    transcriber = FakeStreamingTranscriber("")
    ctrl = _controller(
        app,
        capture_factory=lambda: capture,
        transcriber_factory=lambda: transcriber,
        paster=lambda _text: None,
    )

    ctrl.set_armed(True)
    ctrl.begin_utterance()

    assert ctrl.state() == LiveState.LISTENING
    assert callable(capture.on_frame)
    assert transcriber.fed == prebuffer


# --- hands-free toggle (right Alt + Win) ------------------------------------
def test_hands_free_toggle_pastes_segments_and_stops(app):
    pasted: list[str] = []
    committed: list[str] = []
    notices: list[str] = []
    tr = FakeStreamingTranscriber("хвост")   # tail flushed on stop
    ctrl = _controller(
        app,
        capture_factory=lambda: FakeStreamingCapture(),
        transcriber_factory=lambda: tr,
        paster=pasted.append,
    )
    ctrl.notice.connect(notices.append)
    ctrl.text_committed.connect(committed.append)
    ctrl.set_armed(True)

    # toggle ON (right Alt + Win) -> hands-free continuous session
    ctrl._toggle_latch()
    assert ctrl._latched is True
    assert ctrl.state() == LiveState.LISTENING
    assert "live_latched_on" in notices

    # Sentences remain buffered while the session is active.
    tr.on_segment("первое предложение")
    tr.on_segment("второе предложение")
    assert pasted == []
    assert committed == []

    # a push-to-talk press while latched is ignored (no new session)
    ctrl._handle_press()
    assert ctrl.state() == LiveState.LISTENING

    # toggle OFF -> flush tail (pasted) + finalize
    ctrl._toggle_latch()
    assert ctrl._latched is False
    assert "live_latched_off" in notices
    assert ctrl.state() == LiveState.ARMED
    assert pasted == ["первое предложение второе предложение хвост"]
    assert committed == ["первое предложение второе предложение хвост"]


# --- realtime streaming transcriber -----------------------------------------
def test_realtime_transcriber_streams_and_finalizes(app):
    partials: list[str] = []
    conn = FakeRealtimeConn(deltas=["при", "вет"], final="привет мир")
    rt = RealtimeLiveTranscriber(
        get_project_paths(),
        connection_factory=lambda opts: conn,
        on_partial=partials.append,
        final_timeout=2.0,
    )
    rt.start(LiveOptions(sample_rate=16000))
    rt.feed(b"\x00\x01" * 100)
    assert conn.appended  # base64 PCM was streamed to the connection

    result = rt.finish()
    assert conn.committed is False  # server-VAD owns commits; we never manual-commit
    assert result.is_final is True
    assert result.text == "привет мир"
    assert conn.closed is True
    assert partials and partials[-1] == "привет мир"  # partials grew to the final


def test_realtime_whisper_uses_manual_commit(app):
    conn = FakeManualCommitRealtimeConn(final="привет")
    rt = RealtimeLiveTranscriber(
        get_project_paths(),
        connection_factory=lambda opts: conn,
        model="gpt-realtime-whisper",
        final_timeout=2.0,
    )
    rt.start(LiveOptions(sample_rate=24000))
    rt.feed(b"\x00\x01" * 100)

    result = rt.finish()

    assert conn.committed is True
    assert result.text == "привет"


def test_realtime_transcriber_error(app):
    conn = FakeRealtimeConn(error="boom")
    rt = RealtimeLiveTranscriber(
        get_project_paths(),
        connection_factory=lambda opts: conn,
        final_timeout=2.0,
    )
    rt.start(LiveOptions())
    with pytest.raises(RuntimeError, match="boom"):
        rt.finish()
    assert conn.closed is True


# --- batch transcriber -------------------------------------------------------
class _FakeProvider:
    def __init__(self):
        self.seen_path = None

    def transcribe(self, path, options):
        self.seen_path = path
        assert Path(path).exists()  # the WAV must exist during the call
        return TranscriptResult(
            segments=[
                Segment(index=0, start=0.0, end=1.0, text="hi"),
                Segment(index=1, start=1.0, end=2.0, text="there"),
            ]
        )


def test_batch_transcriber(app):
    paths = get_project_paths()
    provider = _FakeProvider()
    bt = BatchLiveTranscriber(paths, provider=provider)
    bt.start(LiveOptions(sample_rate=16000))
    bt.feed(b"\x01\x02" * 200)
    result = bt.finish()
    assert result.text == "hi there"
    # temp WAV cleaned up afterwards (no system/workspace litter).
    assert not (paths.workspace / "live_utterance.wav").exists()


def test_batch_transcriber_empty(app):
    paths = get_project_paths()
    bt = BatchLiveTranscriber(paths, provider=_FakeProvider())
    bt.start(LiveOptions())
    assert bt.finish().text == ""  # no audio -> no provider call, empty text


def test_write_wav_roundtrip(tmp_path):
    import wave

    pcm = b"\x10\x20" * 50
    wav = tmp_path / "x.wav"
    write_wav_pcm16(wav, pcm, 16000)
    with wave.open(str(wav), "rb") as h:
        assert h.getnchannels() == 1
        assert h.getsampwidth() == 2
        assert h.getframerate() == 16000
        assert h.readframes(h.getnframes()) == pcm


# --- hotkey chord logic ------------------------------------------------------
def test_parse_hotkey():
    assert parse_hotkey("alt+win") == {"alt", "win"}
    assert parse_hotkey("ralt+f12") == {"ralt", "f12"}
    assert parse_hotkey("") == {"ralt", "f12"}           # safe fallback
    assert parse_hotkey("ctrl+shift") == {"ctrl", "shift"}
    assert parse_hotkey("bogus") == {"ralt", "f12"}       # unknown -> fallback
    # right Alt is its own group, distinct from left Alt
    assert parse_hotkey("ralt+win") == {"ralt", "win"}


def test_parse_chord_no_fallback():
    assert parse_chord("ralt+win") == {"ralt", "win"}
    assert parse_chord("") == set()                       # optional -> empty, no fallback
    assert parse_chord("bogus") == set()


def test_hotkey_toggle_chord_is_separate_from_ptt():
    events: list[str] = []
    hk = HotkeyListener(
        "alt+win",
        lambda: events.append("press"),
        lambda: events.append("release"),
        toggle_spec="ralt+win",
        on_toggle=lambda: events.append("toggle"),
    )
    # Right Alt + Win fires the toggle once, NOT push-to-talk (disjoint groups).
    assert hk._handle("ralt", True, False) is False
    assert hk._handle("win", True, False) is False
    assert events == ["toggle"]
    # releasing + pressing again toggles a second time (not a one-shot for life)
    hk._handle("win", False, True)
    hk._handle("ralt", False, True)
    assert hk._handle("ralt", True, False) is False
    assert hk._handle("win", True, False) is False
    assert events == ["toggle", "toggle"]
    # Left Alt + Win still drives push-to-talk, no toggle.
    hk._handle("win", False, True); hk._handle("ralt", False, True)
    assert hk._handle("alt", True, False) is False
    assert hk._handle("win", True, False) is False
    assert events == ["toggle", "toggle", "press"]


def test_hotkey_right_alt_f12_has_no_windows_key():
    events: list[str] = []
    hk = HotkeyListener(
        "ralt+f12",
        lambda: events.append("press"),
        lambda: events.append("release"),
    )
    assert hk._handle("ralt", True, False) is False
    assert hk._handle("f12", True, False) is False
    assert events == ["press"]
    assert hk._handle("f12", False, True) is False
    assert events == ["press", "release"]


def test_hotkey_chord_sequence():
    fired: list[str] = []
    hk = HotkeyListener("alt+win", lambda: fired.append("down"), lambda: fired.append("up"))

    # Observe-only: _handle never suppresses (always False), it just tracks the
    # chord to fire on_press/on_release. Keys always pass through to the OS.
    # lone Alt down (partial chord): no fire yet
    assert hk._handle("alt", True, False) is False
    assert fired == []
    # Win down completes the chord: on_press
    assert hk._handle("win", True, False) is False
    assert fired == ["down"]
    # auto-repeat of a chord key while active: no extra fire
    assert hk._handle("win", True, False) is False
    assert fired == ["down"]
    # a non-chord key is never touched
    assert hk._handle(None, True, False) is False
    # release Win: chord breaks -> on_release
    assert hk._handle("win", False, True) is False
    assert fired == ["down", "up"]
    # release the still-held Alt: no second on_release
    assert hk._handle("alt", False, True) is False
    assert fired == ["down", "up"]
    # re-arm the chord: fires again (not a one-shot)
    assert hk._handle("alt", True, False) is False
    assert hk._handle("win", True, False) is False
    assert fired == ["down", "up", "down"]


# --- overlay -----------------------------------------------------------------
def test_overlay_states(app):
    from system_core.live.overlay import LiveOverlay

    tr = Translator.load(get_project_paths(), "ru")
    ov = LiveOverlay(tr, {"color-background-secondary": "#16202E"}, scale_percent=100)
    assert ov.height() == 52
    ov.show_idle()
    assert ov.isVisible()
    stable_width = ov.size().width()
    assert stable_width >= 420            # reserve the final controller geometry
    assert ov.size().height() == 52
    assert not ov.mask().isEmpty()         # only the central 120x12 idle target is interactive
    assert (ov.mask().boundingRect().width(), ov.mask().boundingRect().height()) == (120, 12)
    assert ov.mask().boundingRect().center() == ov.rect().center()
    assert ov._record.isHidden()
    ov._show_ready()
    assert ov.size().width() == stable_width
    assert ov.size().height() == 52
    assert ov.mask().isEmpty()             # the whole wide ready capsule is interactive
    assert ov._record.isVisible()
    assert ov._drag_handle.isHidden()
    assert ov._ready_drag_handle.isVisible()
    assert ov._record.geometry() == ov._panel.rect()
    assert ov._record.rect().center() == ov._panel.rect().center()
    assert ov._ready_drag_handle.geometry().left() == 10
    assert ov._ready_drag_handle.geometry().center().x() < ov._record.rect().center().x()
    assert ov._panel.childAt(ov._ready_drag_handle.geometry().center()) is ov._ready_drag_handle
    ready_center = ov.frameGeometry().center()
    ov.set_scale_percent(130)
    assert ov.scale_percent == 130
    assert ov.height() == 68
    assert ov._drag_handle.size() == QSize(31, 49)
    assert ov._cloud.width() == 73
    assert ov._elapsed.width() == 117
    assert ov._record.iconSize() == QSize(42, 42)
    ov.show_listening()
    assert ov.frameGeometry().center() == ready_center
    assert ov.size().width() >= round(420 * 1.3)
    assert ov.height() == 68
    assert ov._text.text() == tr.tr("overlay_listening")
    assert ov._elapsed_timer.isActive()
    assert ov._blink_timer.isActive()
    assert ov._stop.isEnabled()
    assert ov._stop.width() == ov._stop.height()
    assert ov._drag_handle.cursor().shape() == Qt.SizeAllCursor
    ov.set_audio_level(0.5)
    assert ov._meter.text() == "▃▇▅▃"
    ov.set_transport_state("receiving")
    assert ov._cloud.text() == "☁ ↕"
    ov.set_partial("привет")
    ov._flush_pending()
    assert ov._text.text() == "привет"
    ov.show_final("привет мир")
    assert ov._text.text() == "привет мир"
    assert ov._hide_timer.isActive()  # final lingers, then auto-hides
    assert not ov._elapsed_timer.isActive()
    assert not ov._blink_timer.isActive()
    assert ov._stop.isHidden()
    ov.dismiss_if_not_showing_final()
    assert ov.isVisible()  # final linger is not cut short by the ARMED state
    ov.hide()
    ov.deleteLater()


def test_overlay_dismisses_stale_transcribing_state(app):
    from system_core.live.overlay import LiveOverlay

    tr = Translator.load(get_project_paths(), "ru")
    ov = LiveOverlay(tr, {}, scale_percent=70)
    ov.show_listening()
    ov.show_transcribing()
    assert ov.isVisible()

    ov.dismiss_if_not_showing_final()

    assert ov.isVisible()
    assert ov._compact is True
    assert ov.height() == 36
    assert ov.width() >= round(420 * 0.7)
    ov.deleteLater()


def test_overlay_file_recorder_has_only_recording_controls(app):
    from system_core.live.overlay import LiveOverlay

    tr = Translator.load(get_project_paths(), "ru")
    ov = LiveOverlay(tr, {}, scale_percent=75)

    ov.show_file_recording()

    assert ov.isVisible()
    assert ov._text.text() == tr.tr("recorder_recording")
    assert ov._elapsed_timer.isActive()
    assert ov._drag_handle.isVisible()
    assert ov._cancel.isVisible()
    assert ov._elapsed.isVisible()
    assert ov._pause.isVisible()
    assert ov._stop.isVisible()
    assert ov._meter.isHidden()
    assert ov._dot.isHidden()
    assert ov._cloud.isHidden()

    paused: list[bool] = []
    ov.pause_requested.connect(paused.append)
    ov._pause.click()
    assert paused == [True]
    ov.set_file_recording_paused(True)
    assert ov._file_recording_paused is True
    assert ov._text.text() == tr.tr("recorder_paused")
    assert not ov._elapsed_timer.isActive()
    ov._pause.click()
    assert paused == [True, False]
    ov.set_file_recording_paused(False)
    assert ov._elapsed_timer.isActive()

    ov.show_file_recording_finalizing()
    assert ov._text.text() == tr.tr("recorder_finalizing")
    assert not ov._elapsed_timer.isActive()
    assert ov._cancel.isHidden()
    assert ov._pause.isHidden()
    assert not ov._stop.isEnabled()
    ov.show_file_recording()
    ov.show_file_recording_finalizing(cancel=True)
    assert ov._text.text() == tr.tr("recorder_cancelling")
    ov.hide()
    ov.deleteLater()


def test_overlay_fades_out_and_back_in_for_local_model_switch(app):
    from system_core.live.overlay import LiveOverlay

    tr = Translator.load(get_project_paths(), "ru")
    ov = LiveOverlay(tr, {}, scale_percent=75)
    completed: list[str] = []
    ov.show_idle()

    ov.fade_out_for_model_switch(lambda: completed.append("hidden"))
    QTest.qWait(240)

    assert completed == ["hidden"]
    assert ov.windowOpacity() <= 0.01
    assert ov.testAttribute(Qt.WA_TransparentForMouseEvents)

    ov.fade_in_after_model_switch()
    QTest.qWait(300)

    assert ov.windowOpacity() >= 0.99
    assert not ov.testAttribute(Qt.WA_TransparentForMouseEvents)
    ov.hide()
    ov.deleteLater()


def test_overlay_scales_compact_idle_target(app):
    from system_core.live.overlay import LiveOverlay

    tr = Translator.load(get_project_paths(), "ru")
    ov = LiveOverlay(tr, {}, scale_percent=130)
    ov.show_idle()
    assert ov.height() == 68
    assert (ov.mask().boundingRect().width(), ov.mask().boundingRect().height()) == (156, 16)

    center = ov.frameGeometry().center()
    ov.set_scale_percent(70)
    assert ov.frameGeometry().center() == center
    assert ov.height() == 36
    assert (ov.mask().boundingRect().width(), ov.mask().boundingRect().height()) == (84, 8)
    ov.deleteLater()


def test_overlay_controls_emit_without_focus(app):
    from system_core.live.overlay import LiveOverlay

    tr = Translator.load(get_project_paths(), "ru")
    ov = LiveOverlay(tr, {}, scale_percent=100)
    events: list[str] = []
    ov.record_requested.connect(lambda: events.append("record"))
    ov.stop_requested.connect(lambda: events.append("stop"))
    ov.cancel_requested.connect(lambda: events.append("cancel"))
    ov.context_menu_requested.connect(lambda point: events.append(("menu", point)))
    ov.set_record_action("recorder_start")
    assert ov._record.text() == tr.tr("recorder_start")
    assert not ov._record.icon().isNull()
    ov.show_idle()
    ov._show_ready()
    QTest.mouseClick(ov._record, Qt.LeftButton, pos=ov._record.rect().center())
    QTest.qWait(100)
    assert ov._record.isVisible()
    assert 0.0 < ov._record_opacity.opacity() < 1.0
    QTest.qWait(160)
    ov.show_listening()
    ov._stop.click()
    ov._cancel.click()
    ov.show_idle()
    ov._show_ready()
    ov._request_context_menu(ov.mapToGlobal(ov.rect().center()))

    assert events[:3] == ["record", "stop", "cancel"]
    assert events[3][0] == "menu"
    assert not ov._stop.isEnabled()
    assert ov.focusPolicy() == Qt.NoFocus
    assert not ov.toolTip()
    assert all(not widget.toolTip() for widget in ov.findChildren(QWidget))
    ov.hide()
    ov.deleteLater()


def test_ready_overlay_drag_handle_moves_without_recording(app):
    from system_core.live.overlay import LiveOverlay

    tr = Translator.load(get_project_paths(), "ru")
    ov = LiveOverlay(tr, {}, scale_percent=75)
    recorded = []
    ov.record_requested.connect(lambda: recorded.append(True))
    ov.show_idle()
    ov._show_ready()
    app.processEvents()

    start = ov.pos()
    center = ov._ready_drag_handle.rect().center()
    QTest.mousePress(ov._ready_drag_handle, Qt.LeftButton, pos=center)
    QTest.mouseMove(ov._ready_drag_handle, center + QPoint(24, 12))
    QTest.mouseRelease(
        ov._ready_drag_handle,
        Qt.LeftButton,
        pos=center + QPoint(24, 12),
    )

    assert ov.pos() != start
    assert ov._user_positioned
    assert recorded == []
    ov.hide()
    ov.deleteLater()


def test_xai_short_audio_drops_full_keyterm_echo_but_keeps_real_recitation():
    from system_core.live.controller import _strip_implausible_keyterm_echo

    terms = ("ИАС УГРТ", "ФГИС ТП", "КИПРР", "ГП", "ПТП", "ПЗЗ", "ЗОУИТ")
    echoed = "Раз, два, три. " + ", ".join(terms) + "."

    assert _strip_implausible_keyterm_echo(echoed, terms, 1.8) == "Раз, два, три"
    assert _strip_implausible_keyterm_echo(echoed, terms, 12.0) == echoed


# --- tray --------------------------------------------------------------------
def test_tray_menu_and_signals(app):
    tr = Translator.load(get_project_paths(), "ru")
    tray = VoiceTray(app_icon(), tr)

    labels = [a.text() for a in tray.contextMenu().actions() if a.text()]
    assert len(labels) == 9  # show / live / quick API/local / history / export / save / output / quit
    assert tr.tr("tray_live_xai") in labels
    assert tr.tr("tray_live_gigaam") in labels

    fired = {"show": 0, "history": 0, "menu": 0, "save_log": 0}
    profiles: list[str] = []
    exported: list[tuple[str, str]] = []
    recording_exports: list[str] = []
    tray.show_window.connect(lambda: fired.__setitem__("show", fired["show"] + 1))
    tray.show_dictation_history.connect(
        lambda: fired.__setitem__("history", fired["history"] + 1)
    )
    tray.menu_opened.connect(lambda: fired.__setitem__("menu", fired["menu"] + 1))
    tray.save_log.connect(lambda: fired.__setitem__("save_log", fired["save_log"] + 1))
    tray.live_profile_requested.connect(profiles.append)
    tray.export_requested.connect(lambda src, dst: exported.append((src, dst)))
    tray.recording_export_requested.connect(recording_exports.append)
    tray._act_show.trigger()
    tray._act_history.trigger()
    tray.contextMenu().aboutToShow.emit()
    tray._act_live_xai.trigger()
    tray._act_live_gigaam.trigger()
    tray._act_save_log.trigger()
    tray._act_live_notion.trigger()
    tray._act_file_obsidian.trigger()
    tray._act_recording_m4a.trigger()
    tray._act_recording_wav.trigger()
    assert fired["show"] == 1
    assert fired["history"] == 1
    assert fired["menu"] == 1
    assert fired["save_log"] == 1
    assert profiles == ["xai", "gigaam"]
    assert exported == [("live", "notion"), ("file", "obsidian")]
    assert recording_exports == ["m4a", "wav"]
    assert tray._act_save_log.text() == tr.tr("save_log_md")
    assert tray._act_history.text() == tr.tr("dictation_history_all_title")
    assert tray._sub_recording.title() == tr.tr("recorder_export")
    assert not tray.toolTip()


def test_tray_live_state_check(app):
    tr = Translator.load(get_project_paths(), "ru")
    tray = VoiceTray(app_icon(), tr)
    assert tray._act_live.isCheckable()
    assert tray._act_live.text() == tr.tr("tray_live")  # constant label
    tray.set_live_state(LiveState.DISABLED)
    assert tray._act_live.isChecked() is False
    tray.set_live_state(LiveState.ARMED)
    assert tray._act_live.isChecked() is True
    tray.set_live_state(LiveState.LISTENING)  # still armed
    assert tray._act_live.isChecked() is True


