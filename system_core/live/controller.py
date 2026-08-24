"""Live dictation controller: the state machine the tray and the global hotkey drive.

Flow of one push-to-talk utterance:
    hotkey down  -> begin_utterance(): start mic capture + open transcriber
    hotkey up    -> end_utterance():   stop capture, transcribe (off the GUI
                    thread), then paste the text into the focused window.

State (disabled / armed / listening / transcribing / error) is broadcast so the
tray + overlay reflect it. Every engine piece (hotkey, capture, transcriber,
paster) is injectable so the flow is testable without a real mic/network/OS hook.
"""

from __future__ import annotations

import threading
import re
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from ..core.paths import ProjectPaths
from ..providers.base import LiveOptions, LiveRegionBlockedError
from .config import LiveConfig
from .transcriber import write_wav_pcm16


def _strip_implausible_keyterm_echo(
    text: str,
    keyterms: tuple[str, ...],
    audio_seconds: float,
) -> str:
    """Drop an impossible full-dictionary suffix produced by strong STT bias.

    xAI legitimately accepts repeated ``keyterm`` parameters, but on very short
    or near-silent input it can occasionally emit the complete supplied list
    after the real phrase.  Only remove an exact, long, in-order dictionary
    suffix when the captured duration is too short to have spoken it.  A genuine
    deliberate vocabulary test is retained because its recording is long enough.
    """
    value = " ".join(str(text or "").split())
    term_words = [
        match.group(0).casefold()
        for term in keyterms
        for match in re.finditer(r"\w+", str(term))
    ]
    matches = list(re.finditer(r"\w+", value))
    if len(term_words) < 6 or len(matches) < len(term_words):
        return value
    suffix = [match.group(0).casefold() for match in matches[-len(term_words):]]
    if suffix != term_words:
        return value
    # Even unusually fast acronym dictation needs roughly 280 ms per token.
    # Use only the dictionary suffix for the plausibility threshold, keeping the
    # rule conservative when a long real phrase precedes it.
    if max(0.0, float(audio_seconds or 0.0)) >= len(term_words) * 0.28:
        return value
    start = matches[-len(term_words)].start()
    return value[:start].rstrip(" \t,.;:—-")


class LiveState(str, Enum):
    DISABLED = "disabled"          # Live off; hotkey not listening
    ARMED = "armed"               # idle, waiting for the push-to-talk chord
    LISTENING = "listening"        # chord held, capturing mic audio
    TRANSCRIBING = "transcribing"  # chord released, awaiting final text
    ERROR = "error"


class LiveController(QObject):
    state_changed = Signal(object)   # LiveState
    text_committed = Signal(str)     # final transcript (after paste)
    partial = Signal(str)            # running partial transcript (streaming engine)
    audio_level = Signal(float)      # actual PCM peak, normalized 0..1
    input_device_changed = Signal(str)  # concrete endpoint opened for this utterance
    transport_state = Signal(str)    # connecting | ready | sending | receiving
    model_switch_finished = Signal(object) # completed transcriber signature
    notice = Signal(str)             # i18n key for the tray / log
    error = Signal(str)              # free-text problem for the log
    _transcribed = Signal(object)    # internal: (text:str | Exception) from worker
    _segment_ready = Signal(str)     # internal: a completed segment (latched paste)
    _latched_stopped = Signal(object)  # internal: session id flushed + closed
    # The hotkey hook fires these from inside the low-level keyboard callback;
    # they're delivered as *queued* signals so the gesture handlers (mic start,
    # focus capture) run on the next event-loop turn instead of blocking the hook
    # callback — which must return fast or Windows drops the hook.
    _press_requested = Signal()
    _release_requested = Signal()
    _toggle_requested = Signal()     # hands-free latch toggle (right Alt + Win)

    def __init__(
        self,
        paths: ProjectPaths,
        config: LiveConfig,
        parent: Optional[QObject] = None,
        *,
        hotkey_factory: Optional[Callable[[], object]] = None,
        capture_factory: Optional[Callable[[], object]] = None,
        transcriber_factory: Optional[Callable[[], object]] = None,
        paster: Optional[Callable[[str], None]] = None,
        synchronous: bool = False,
    ):
        super().__init__(parent)
        self._paths = paths
        self._config = config
        self._state = LiveState.DISABLED
        self._synchronous = synchronous

        self._hotkey_factory = hotkey_factory or self._default_hotkey
        self._capture_factory = capture_factory or self._default_capture
        self._uses_default_transcriber_factory = transcriber_factory is None
        self._transcriber_factory = transcriber_factory or self._default_transcriber
        self._paster = paster or (lambda text: self._default_paste(text))

        self._hotkey = None
        self._capture = None
        self._transcriber = None
        self._active_transcriber_signature: tuple[object, ...] | None = None
        self._resident_transcribers: dict[tuple[str, str, str], object] = {}
        self._resident_warming: set[tuple[str, str, str]] = set()
        self._resident_lock = threading.RLock()
        self._warm_threads: set[threading.Thread] = set()
        self._resident_generation = 0
        self._shutting_down = False
        self._custom_warmed_signature: tuple[object, ...] | None = None
        self._target_hwnd = None  # window focused when the utterance began
        self._input_device_preference: tuple[str, str] | None = None
        self._session_language = config.language
        self._session_engine = config.engine
        self._session_local_engine = config.local_engine
        self._session_local_model = config.local_model
        self._session_id = 0
        self._cancelled_sessions: set[int] = set()
        self._stream_segments: list[str] = []
        self._stream_lock = threading.Lock()
        self._transport_has_audio = False
        self._transport_has_text = False
        self._safety_timer = QTimer(self)
        self._safety_timer.setSingleShot(True)
        self._safety_timer.timeout.connect(self._on_safety_timeout)

        # Hands-free latch (toggled by the right Alt + Win chord).
        self._latched = False                 # hands-free continuous session active
        self._paste_segments = False          # paste each completed segment as it lands

        self._transcribed.connect(self._on_transcribed)
        self._segment_ready.connect(self._on_segment_ready)
        self._latched_stopped.connect(self._on_latched_stopped)
        # Hop the hotkey callback onto the event loop before doing real work.
        self._press_requested.connect(self._handle_press, Qt.QueuedConnection)
        self._release_requested.connect(self._handle_release, Qt.QueuedConnection)
        self._toggle_requested.connect(self._toggle_latch, Qt.QueuedConnection)

    # --- default engine factories (lazy, kept off the import path) -----------
    def _default_hotkey(self):
        from .hotkey import HotkeyListener

        return HotkeyListener(
            self._config.hotkey,
            self._press_requested.emit,
            self._release_requested.emit,
            toggle_spec=self._config.toggle_hotkey,
            on_toggle=self._toggle_requested.emit,
        )

    def _sample_rate(self) -> int:
        # OpenAI Realtime requires >= 24 kHz PCM16; other Live paths are fine at
        # 16 kHz. Capture and the session format must agree.
        return 24000 if self._session_engine == "realtime" else 16000

    def _default_capture(self):
        from .audio_capture import AudioCapture

        return AudioCapture(
            sample_rate=self._sample_rate(),
            channels=1,
            preferred_device=self._input_device_preference,
        )

    def _create_local_transcriber(self, engine: str, model: str):
        """Build a resident local Live engine for one explicit route."""
        if engine == "gigaam":
            from ..providers.gigaam_provider import GigaAMLiveTranscriber

            return GigaAMLiveTranscriber(
                self._paths,
                model=model,
                backend=self._config.local_backend,
            )
        from .whispercpp_live import WhisperCppLiveTranscriber

        return WhisperCppLiveTranscriber(
            self._paths,
            model=model,
            backend=self._config.local_backend,
            # Live models are intentionally resident. The controller closes the
            # process only when the selected route changes or the app exits.
            idle_unload_seconds=0,
        )

    def _default_transcriber(self):
        if self._session_engine == "realtime":
            from ..providers.realtime_provider import RealtimeLiveTranscriber

            return RealtimeLiveTranscriber(self._paths, model=self._config.model or None)
        if self._session_engine == "xai_realtime":
            from ..providers.cloud_realtime import XAIRealtimeLiveTranscriber

            return XAIRealtimeLiveTranscriber(self._paths, model=self._config.model or None)
        if self._session_engine == "elevenlabs_realtime":
            from ..providers.cloud_realtime import ElevenLabsRealtimeLiveTranscriber

            return ElevenLabsRealtimeLiveTranscriber(self._paths, model=self._config.model or None)
        if self._session_engine == "vulkan":
            return self._create_local_transcriber(
                self._session_local_engine,
                self._session_local_model,
            )

        from .transcriber import BatchLiveTranscriber

        return BatchLiveTranscriber(self._paths)

    def _default_paste(self, text: str) -> None:
        from .focus import paste_text

        paste_text(text, self._config.paste_method, self._target_hwnd)

    # --- state ---------------------------------------------------------------
    @property
    def config(self) -> LiveConfig:
        return self._config

    def state(self) -> LiveState:
        return self._state

    def is_armed(self) -> bool:
        return self._state != LiveState.DISABLED

    def _set_state(self, state: LiveState) -> None:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)

    def _live_options(self) -> LiveOptions:
        context = ", ".join(self._config.keyterms)
        language = self._session_language
        # Keep this helper useful on its own (diagnostics/tests call it before
        # begin_utterance).  Normal capture has already resolved the complete
        # route, so this is only a compatibility fallback.
        if language == "layout":
            language = self._target_layout_language()
        return LiveOptions(
            language=language,
            model=(
                self._session_local_model
                if self._session_engine == "vulkan"
                else self._config.model or None
            ),
            context=context,
            keyterms=self._config.keyterms,
            sample_rate=self._sample_rate(),
            vad_filter=True,
        )

    def _configured_session_route(self) -> None:
        self._session_language = self._config.language
        self._session_engine = self._config.engine
        self._session_local_engine = self._config.local_engine
        self._session_local_model = self._config.local_model

    def _target_layout_language(self) -> str:
        try:
            from .focus import get_window_language

            return get_window_language(self._target_hwnd)
        except Exception:
            return "auto"

    def _resolve_session_route(self) -> None:
        """Resolve target layout and choose the free local RU/EN engine."""
        language = self._config.language
        if language == "layout":
            language = self._target_layout_language()
        self._configured_session_route()
        self._session_language = language
        if self._config.source != "local" or not self._config.layout_local_routing:
            return
        if language == "ru":
            self._session_local_engine = "gigaam"
            self._session_local_model = self._config.layout_ru_model
        elif language == "en":
            self._session_local_engine = "whispercpp"
            self._session_local_model = self._config.layout_en_model

    def _session_transcriber_signature(self) -> tuple[object, ...]:
        return (
            self._session_engine,
            self._config.model,
            self._session_local_engine,
            self._session_local_model,
            self._config.local_backend,
            self._config.local_idle_unload_seconds,
        )

    def _resident_key(self, engine: str, model: str) -> tuple[str, str, str]:
        return (str(engine), str(model), str(self._config.local_backend))

    def _desired_resident_specs(self) -> tuple[tuple[str, str], ...]:
        if (
            self._shutting_down
            or
            not self._config.enabled
            or self._config.source != "local"
            or self._config.engine != "vulkan"
        ):
            return ()
        if self._config.language == "layout" and self._config.layout_local_routing:
            specs = (
                ("whispercpp", self._config.layout_en_model),
                ("gigaam", self._config.layout_ru_model),
            )
        else:
            specs = ((self._config.local_engine, self._config.local_model),)
        # Preserve order while avoiding duplicate engine/model pairs.
        return tuple(dict.fromkeys(specs))

    def _is_resident_transcriber(self, transcriber) -> bool:
        if transcriber is None:
            return False
        with self._resident_lock:
            return any(item is transcriber for item in self._resident_transcribers.values())

    def _ensure_resident_transcriber(self, engine: str, model: str):
        key = self._resident_key(engine, model)
        with self._resident_lock:
            transcriber = self._resident_transcribers.get(key)
            if transcriber is None:
                transcriber = self._create_local_transcriber(engine, model)
                self._resident_transcribers[key] = transcriber
        return key, transcriber

    def _warm_resident_transcriber(self, key, transcriber) -> None:
        warm = getattr(transcriber, "warm", None)
        if not callable(warm):
            return
        with self._resident_lock:
            if key in self._resident_warming:
                return
            self._resident_warming.add(key)
        try:
            warm()
        finally:
            with self._resident_lock:
                self._resident_warming.discard(key)

    def _reconcile_resident_transcribers(self, expected_generation: int | None = None) -> None:
        specs = self._desired_resident_specs()
        desired = {self._resident_key(engine, model) for engine, model in specs}
        with self._resident_lock:
            obsolete = [
                self._resident_transcribers.pop(key)
                for key in tuple(self._resident_transcribers)
                if key not in desired
            ]
            self._resident_warming.intersection_update(desired)
        for transcriber in dict.fromkeys(obsolete):
            try:
                transcriber.close()
            except Exception:
                pass
        for engine, model in specs:
            with self._resident_lock:
                if (
                    self._shutting_down
                    or expected_generation is not None
                    and expected_generation != self._resident_generation
                ):
                    return
            key, transcriber = self._ensure_resident_transcriber(engine, model)
            self._warm_resident_transcriber(key, transcriber)

    def _resident_transcriber_for_session(self):
        key, transcriber = self._ensure_resident_transcriber(
            self._session_local_engine,
            self._session_local_model,
        )
        self._warm_resident_transcriber(key, transcriber)
        return transcriber

    def _detach_transcriber(self) -> None:
        self._transcriber = None
        self._active_transcriber_signature = None

    def _close_resident_transcribers(self) -> None:
        with self._resident_lock:
            transcribers = list(dict.fromkeys(self._resident_transcribers.values()))
            self._resident_transcribers.clear()
            self._resident_warming.clear()
        for transcriber in transcribers:
            try:
                transcriber.close()
            except Exception:
                pass

    def _drop_transcriber(self) -> None:
        transcriber = self._transcriber
        if transcriber is not None:
            with self._resident_lock:
                for key, item in tuple(self._resident_transcribers.items()):
                    if item is transcriber:
                        self._resident_transcribers.pop(key, None)
                        self._resident_warming.discard(key)
            try:
                transcriber.close()
            except Exception:
                pass
        self._detach_transcriber()
        self._custom_warmed_signature = None

    # --- arming --------------------------------------------------------------
    def set_armed(self, on: bool) -> None:
        if on and self._state == LiveState.DISABLED:
            try:
                self._hotkey = self._hotkey_factory()
                install = getattr(self._hotkey, "install", None)
                if callable(install):
                    install()
            except Exception as exc:
                self._hotkey = None
                self.error.emit(str(exc))
                self.notice.emit("live_hotkey_failed")
                return
            self._set_state(LiveState.ARMED)
            self._prewarm()
        elif not on and self._state != LiveState.DISABLED:
            self._teardown_session()
            self._set_state(LiveState.DISABLED)

    def _prewarm(self) -> None:
        """Start loading selected local Live models and keep them resident."""
        if self._shutting_down:
            return
        signature = self._transcriber_signature(self._config)
        if self._uses_default_transcriber_factory:
            with self._resident_lock:
                self._resident_generation += 1
                generation = self._resident_generation

            def work() -> None:
                try:
                    self._reconcile_resident_transcribers(generation)
                except Exception as exc:
                    self.error.emit(str(exc))
                finally:
                    with self._resident_lock:
                        current = generation == self._resident_generation
                    if current and not self._shutting_down:
                        self.model_switch_finished.emit(signature)

            thread: threading.Thread

            def tracked_work() -> None:
                try:
                    work()
                finally:
                    with self._resident_lock:
                        self._warm_threads.discard(thread)

            thread = threading.Thread(
                target=tracked_work,
                name="live-model-warm",
                daemon=True,
            )
            with self._resident_lock:
                self._warm_threads.add(thread)
            thread.start()
            return
        if self._config.engine != "vulkan":
            self.model_switch_finished.emit(signature)
            return
        try:
            signature = self._session_transcriber_signature()
            if (
                self._transcriber is not None
                and self._custom_warmed_signature == signature
            ):
                return
            if self._transcriber is None:
                self._transcriber = self._transcriber_factory()
                self._active_transcriber_signature = signature
            warm = getattr(self._transcriber, "warm", None)
            if callable(warm):
                warm()
            self._custom_warmed_signature = signature
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.model_switch_finished.emit(signature)

    def config_requires_transcriber_reload(self, config: LiveConfig) -> bool:
        return self.transcriber_signature(config) != self.transcriber_signature()

    def transcriber_signature(self, config: LiveConfig | None = None) -> tuple[object, ...]:
        return self._transcriber_signature(config or self._config)

    def update_config(self, config: LiveConfig) -> None:
        """Apply a live settings change to the running controller. An engine
        switch drops the current transcriber -- freeing a resident local model
        when leaving local mode ("memory clean") -- and pre-warms when switching
        into local mode while armed ("switch -> warm"). An in-flight utterance is left
        alone; the change takes effect on the next one."""
        old = self._config
        self._config = config
        if (
            self._state == LiveState.LISTENING
            and config.safety_timeout_minutes != old.safety_timeout_minutes
        ):
            self._start_safety_timer()
        if self._transcriber_signature(config) == self._transcriber_signature(old):
            return
        if self._state not in (LiveState.ARMED, LiveState.DISABLED):
            return
        self._configured_session_route()
        if self._is_resident_transcriber(self._transcriber):
            self._detach_transcriber()
        else:
            self._drop_transcriber()
        if self._capture is not None:
            try:
                self._capture.close()
            except Exception:
                pass
            self._capture = None
        self._prewarm()

    def _transcriber_signature(self, config: LiveConfig) -> tuple[object, ...]:
        return (
            config.source,
            config.engine,
            config.model,
            config.api_provider,
            config.api_mode,
            config.local_engine,
            config.local_model,
            config.local_backend,
            config.local_idle_unload_seconds,
            config.layout_local_routing,
            config.layout_ru_model,
            config.layout_en_model,
        )

    def toggle(self) -> bool:
        self.set_armed(not self.is_armed())
        return self.is_armed()

    def set_input_device_preference(
        self,
        preference: tuple[str, str] | None,
    ) -> bool:
        """Set a non-persistent microphone override for the current process."""
        if self._state in (LiveState.LISTENING, LiveState.TRANSCRIBING):
            return False
        normalized = None
        if preference is not None:
            normalized = (str(preference[0] or ""), str(preference[1] or ""))
        if normalized == self._input_device_preference:
            return True
        self._input_device_preference = normalized
        # AudioCapture holds the endpoint it opened. Recreate it so the next
        # activation refreshes PortAudio and applies the new session choice.
        if self._capture is not None:
            try:
                self._capture.close()
            except Exception:
                pass
            self._capture = None
        return True

    # --- utterance lifecycle (driven by the hotkey) --------------------------
    def begin_utterance(self) -> None:
        if self._state not in (LiveState.ARMED, LiveState.ERROR):
            return
        self._paste_segments = False  # set once we know the engine (below)
        with self._stream_lock:
            self._stream_segments = []
        self._session_id += 1
        try:
            from .focus import get_foreground_window

            self._target_hwnd = get_foreground_window()
        except Exception:
            self._target_hwnd = None
        self._resolve_session_route()
        route_signature = self._session_transcriber_signature()
        if (
            self._transcriber is not None
            and self._active_transcriber_signature != route_signature
        ):
            if self._is_resident_transcriber(self._transcriber):
                self._detach_transcriber()
            else:
                self._drop_transcriber()
        # Start the mic first. Realtime/WebSocket setup can take long enough for a
        # fast speaker to lose the first word if we wait to open the microphone.
        # Streaming engines get the prebuffer flushed into them once ready.
        try:
            if self._capture is None:
                self._capture = self._capture_factory()
            self._capture.on_frame = None
            self._capture.on_level = self.audio_level.emit
            self._capture.start()
            selected_device = str(getattr(self._capture, "selected_device", "") or "").strip()
            if selected_device:
                self.input_device_changed.emit(selected_device)
        except Exception as exc:
            self.error.emit(str(exc))
            self.notice.emit("live_capture_failed")
            self._set_state(LiveState.ARMED)
            return
        self._set_state(LiveState.LISTENING)
        self._start_safety_timer()

        # Open the transcriber session after capture is live. A failure here is
        # NOT a mic problem — report it as a Live error and stop the prebuffering
        # mic session.
        try:
            if self._transcriber is None:
                if self._uses_default_transcriber_factory and self._session_engine == "vulkan":
                    self._transcriber = self._resident_transcriber_for_session()
                else:
                    self._transcriber = self._transcriber_factory()
                self._active_transcriber_signature = route_signature
            # Streaming engines fill the field incrementally (sentence by sentence,
            # for both push-to-talk and hands-free); batch pastes one blob at the end.
            # Streaming text stays buffered until Stop. This makes Cancel honest:
            # nothing has been pasted into the target application yet.
            self._paste_segments = False
            self._transcriber.on_partial = self._on_partial
            self._transcriber.on_segment = self._segment_ready.emit
            self._transport_has_audio = False
            self._transport_has_text = False
            if getattr(self._transcriber, "streaming", False):
                self.transport_state.emit("connecting")
            self._transcriber.start(self._live_options())
            if getattr(self._transcriber, "streaming", False):
                self.transport_state.emit("ready")
        except LiveRegionBlockedError as exc:
            try:
                if self._capture:
                    self._capture.stop()
            except Exception:
                pass
            try:
                if self._transcriber:
                    self._transcriber.close()
            except Exception:
                pass
            self._transcriber = None
            self._active_transcriber_signature = None
            self.error.emit(str(exc))
            self.notice.emit("live_region_blocked")
            self._set_state(LiveState.ARMED)
            return
        except Exception as exc:
            try:
                if self._capture:
                    self._capture.stop()
            except Exception:
                pass
            try:
                if self._transcriber:
                    self._transcriber.close()
            except Exception:
                pass
            self._transcriber = None
            self._active_transcriber_signature = None
            self.error.emit(str(exc))
            self.notice.emit("live_error")
            self._set_state(LiveState.ARMED)
            return

        # Streaming engines want PCM pushed live during capture. Flush anything
        # recorded while the Realtime session was opening, then stream subsequent
        # frames directly from the audio callback.
        if getattr(self._transcriber, "streaming", False):
            setter = getattr(self._capture, "set_frame_sink", None)
            if callable(setter):
                prebuffer = setter(self._feed_stream_frame, flush_existing=True)
            else:
                self._capture.on_frame = self._feed_stream_frame
                prebuffer = b""
            if prebuffer:
                self._feed_stream_frame(prebuffer)
        else:
            self._capture.on_frame = None

    def _on_partial(self, text: str) -> None:
        # Invoked from the transcriber's reader thread; the signal hops to the GUI
        # thread (queued) for whoever shows live partials.
        self.partial.emit(text)
        if text and not self._transport_has_text:
            self._transport_has_text = True
            self.transport_state.emit("receiving")

    def _feed_stream_frame(self, pcm: bytes) -> None:
        transcriber = self._transcriber
        if transcriber is None:
            return
        if pcm and not self._transport_has_audio:
            self._transport_has_audio = True
            self.transport_state.emit("sending")
        transcriber.feed(pcm)

    def end_utterance(self) -> None:
        """Finalize the current session (push-to-talk release, or hands-free stop)."""
        if self._state != LiveState.LISTENING:
            return
        self._safety_timer.stop()
        self.audio_level.emit(0.0)
        streaming = bool(getattr(self._transcriber, "streaming", False))
        session_id = self._session_id
        transcriber = self._transcriber
        try:
            pcm = self._capture.stop() if self._capture else b""
        except Exception as exc:
            self.error.emit(str(exc))
            self._set_state(LiveState.ARMED)
            return
        self._set_state(LiveState.TRANSCRIBING)
        if streaming:
            # Flush the tail, then paste the complete session exactly once.
            def work():
                try:
                    result = transcriber.finish()
                    final_text = str(result.text or "").strip()
                    with self._stream_lock:
                        buffered = " ".join(self._stream_segments).strip()
                    if buffered and final_text:
                        if final_text.casefold() == buffered.casefold():
                            complete = final_text
                        elif final_text.casefold().startswith(buffered.casefold()):
                            complete = final_text
                        elif buffered.casefold().endswith(final_text.casefold()):
                            complete = buffered
                        else:
                            complete = f"{buffered} {final_text}".strip()
                    else:
                        complete = final_text or buffered
                    if self._session_engine == "xai_realtime":
                        audio_seconds = len(pcm) / max(1, self._sample_rate() * 2)
                        complete = _strip_implausible_keyterm_echo(
                            complete,
                            self._config.keyterms,
                            audio_seconds,
                        )
                    self._transcribed.emit((session_id, complete))
                except Exception as exc:
                    if session_id not in self._cancelled_sessions:
                        recovery = self._preserve_failed_stream(pcm)
                        detail = str(exc)
                        if recovery is not None:
                            detail += f"; audio preserved: {recovery}"
                        self._transcribed.emit((session_id, RuntimeError(detail)))

            if self._synchronous:
                work()
            else:
                threading.Thread(target=work, daemon=True).start()
        else:
            # Batch engine: one request after release -> paste the whole blob.
            if self._synchronous:
                self._do_transcribe(pcm, session_id, transcriber)
            else:
                threading.Thread(
                    target=self._do_transcribe,
                    args=(pcm, session_id, transcriber),
                    daemon=True,
                ).start()

    def stop_utterance(self) -> None:
        """User-facing Stop: finish normally and keep the final transcript."""
        if self._latched:
            self._latched = False
            self.notice.emit("live_latched_off")
        self.end_utterance()

    def cancel_utterance(self) -> None:
        """Abort the active session and ignore any result already in flight."""
        if self._state not in (LiveState.LISTENING, LiveState.TRANSCRIBING):
            return
        self._safety_timer.stop()
        self.audio_level.emit(0.0)
        session_id = self._session_id
        self._cancelled_sessions.add(session_id)
        self._latched = False
        self._paste_segments = False
        with self._stream_lock:
            self._stream_segments = []
        capture, transcriber = self._capture, self._transcriber
        try:
            if capture is not None:
                capture.stop()
        except Exception:
            pass
        if transcriber is not None:
            try:
                transcriber.on_partial = None
                transcriber.on_segment = None
            except Exception:
                pass
            try:
                transcriber.close()
            except Exception:
                pass
        if self._transcriber is transcriber:
            self._transcriber = None
            self._active_transcriber_signature = None
        self.notice.emit("live_cancelled")
        self._set_state(LiveState.ARMED)

    def _start_safety_timer(self) -> None:
        minutes = max(1, int(self._config.safety_timeout_minutes))
        self._safety_timer.start(minutes * 60 * 1000)

    def _on_safety_timeout(self) -> None:
        if self._state != LiveState.LISTENING:
            return
        self.notice.emit("live_safety_timeout_notice")
        self.stop_utterance()

    def _preserve_failed_stream(self, pcm: bytes):
        """Keep failed realtime audio recoverable without risking duplicate paste."""
        if not pcm:
            return None
        try:
            directory = self._paths.workspace / "recovery" / "live"
            directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = directory / f"failed_realtime_{stamp}.wav"
            write_wav_pcm16(path, pcm, self._sample_rate())
            return path
        except Exception as exc:
            self.error.emit(f"Could not preserve failed realtime audio: {exc}")
            return None

    def _do_transcribe(self, pcm: bytes, session_id: int, transcriber) -> None:
        """Runs on a worker thread (or inline when synchronous). Emits a queued
        signal so the result is handled back on the GUI thread."""
        try:
            transcriber.feed(pcm)
            result = transcriber.finish()
            self._transcribed.emit((session_id, result.text or ""))
        except Exception as exc:  # network/mic/etc.
            self._transcribed.emit((session_id, exc))

    def _on_transcribed(self, payload) -> None:
        """GUI-thread slot: paste + broadcast, then re-arm."""
        session_id, payload = payload
        with self._stream_lock:
            self._stream_segments = []
        if session_id in self._cancelled_sessions:
            self._cancelled_sessions.discard(session_id)
            return
        if isinstance(payload, Exception):
            self.error.emit(str(payload))
            self.notice.emit("live_error")
        else:
            text = str(payload).strip()
            if text:
                try:
                    self._paster(text)
                except Exception as exc:
                    self.error.emit(str(exc))
                self.text_committed.emit(text)
            else:
                self.notice.emit("live_empty")  # heard nothing / silence
        # Back to idle (only if Live is still armed — disarm may have raced in).
        if self._state == LiveState.TRANSCRIBING:
            self._set_state(LiveState.ARMED)

    # --- hotkey gestures (hold = push-to-talk; right Alt+Win = hands-free) ----
    def _handle_press(self) -> None:
        if self._latched:
            return  # hands-free session owns the mic; ignore push-to-talk presses
        self.begin_utterance()

    def _handle_release(self) -> None:
        if self._latched:
            return  # releases don't stop a hands-free session (only the toggle does)
        if self._config.mode == "toggle":
            return  # continuous overlay session ends with Stop (or safety timeout)
        self.end_utterance()

    def _toggle_latch(self) -> None:
        """Right Alt + Win: start/stop a hands-free continuous session."""
        if not self._latched:
            self.begin_utterance()
            if self._state == LiveState.LISTENING:   # only latch if it actually started
                self._latched = True
                self.notice.emit("live_latched_on")
        else:
            self._latched = False
            self.notice.emit("live_latched_off")
            self.end_utterance()  # flush + finalize the continuous session

    def _on_segment_ready(self, text: str) -> None:
        """Buffer a locked streaming segment; Stop commits the complete text."""
        text = (text or "").strip()
        if not text:
            return
        with self._stream_lock:
            if not self._stream_segments or self._stream_segments[-1] != text:
                self._stream_segments.append(text)

    def _on_latched_stopped(self, session_id: int) -> None:
        if session_id in self._cancelled_sessions:
            self._cancelled_sessions.discard(session_id)
            return
        self._paste_segments = False
        if self._state == LiveState.TRANSCRIBING:
            self._set_state(LiveState.ARMED)

    # --- teardown ------------------------------------------------------------
    def _teardown_session(self, *, close_models: bool = False) -> None:
        self._safety_timer.stop()
        self._latched = False
        self._paste_segments = False
        with self._stream_lock:
            self._stream_segments = []
        active = self._transcriber
        resources = [self._capture, self._hotkey]
        if not self._is_resident_transcriber(active):
            resources.append(active)
        for resource in resources:
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        self._capture = None
        self._transcriber = None
        self._active_transcriber_signature = None
        self._hotkey = None
        if close_models:
            self._close_resident_transcribers()

    def shutdown(self) -> None:
        self._shutting_down = True
        with self._resident_lock:
            self._resident_generation += 1
            warm_threads = tuple(self._warm_threads)
        # A resident model may be inside ONNX Runtime while loading provider
        # DLLs. Closing that model or ending Python concurrently can fast-fail
        # Windows during DLL teardown. Let the bounded warm complete first.
        current = threading.current_thread()
        for thread in warm_threads:
            if thread is not current:
                thread.join()
        self._teardown_session(close_models=True)
        self._set_state(LiveState.DISABLED)
