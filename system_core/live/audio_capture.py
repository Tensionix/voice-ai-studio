"""Microphone capture for Live dictation (iteration 3).

Push-to-talk records into an in-memory PCM16 mono buffer: `start()` on key-down,
`stop() -> bytes` on key-up. ``sounddevice`` is imported lazily, then the current
Windows recording/communications defaults are resolved on every ``start()``.
Nothing is written to disk here — the transcriber owns the temp WAV (in-project
workspace, never %TEMP%).
"""

from __future__ import annotations

import threading


def _downmix_active_channel_pcm16(chunk: bytes, channels: int) -> tuple[bytes, int]:
    """Adaptively mix active PCM16 input channels to mono.

    Professional interfaces often expose a mono microphone on only L or R; in
    that case averaging with the silent side would throw away 6 dB, so the
    active side passes unchanged.  When two inputs really contain signal, sum
    them and scale the block against the strongest source peak.  This preserves
    both speakers without clipping or boosting duplicated webcam stereo.
    """
    if channels <= 1 or not chunk:
        return chunk, 0
    try:
        samples = memoryview(chunk).cast("h")
    except (TypeError, ValueError):
        return chunk, 0
    frame_count = len(samples) // channels
    if frame_count <= 0 or frame_count * channels != len(samples):
        return chunk, 0
    energy = [0] * channels
    peaks = [0] * channels
    for channel in range(channels):
        absolute = [abs(int(value)) for value in samples[channel::channels]]
        energy[channel] = sum(absolute)
        peaks[channel] = max(absolute, default=0)
    dominant = max(range(channels), key=energy.__getitem__)
    strongest_energy = energy[dominant]
    active_channels = [
        channel
        for channel in range(channels)
        if energy[channel] >= max(1, strongest_energy * 0.12)
    ]
    if len(active_channels) <= 1:
        active = active_channels[0] if active_channels else dominant
        output = bytearray(frame_count * 2)
        mono = memoryview(output).cast("h")
        for frame in range(frame_count):
            mono[frame] = samples[frame * channels + active]
        return bytes(output), dominant

    mixed_values = [
        sum(int(samples[frame * channels + channel]) for channel in active_channels)
        for frame in range(frame_count)
    ]
    mixed_peak = max((abs(value) for value in mixed_values), default=0)
    source_peak = max((peaks[channel] for channel in active_channels), default=0)
    gain = min(1.0, source_peak / mixed_peak) if mixed_peak else 1.0
    output = bytearray(frame_count * 2)
    mono = memoryview(output).cast("h")
    for frame, value in enumerate(mixed_values):
        mono[frame] = max(-32768, min(32767, round(value * gain)))
    return bytes(output), dominant


class AudioCaptureError(RuntimeError):
    """Mic unavailable or sounddevice missing."""


class AudioCapture:
    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        block_ms: int = 20,
        *,
        preferred_device: tuple[str, str] | None = None,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.block_ms = block_ms
        # Session-only (host API, endpoint name). The GUI deliberately never
        # persists this value, so a new process always returns to Windows auto.
        self.preferred_device = preferred_device
        # When set, each PCM frame is pushed here live (streaming mode) instead of
        # buffered for a single read on stop() (batch mode). Set before start().
        self._on_frame = None
        self.on_level = None
        self._stream = None
        self._stream_sample_rate = sample_rate
        self._stream_channels = channels
        self._active_input_channel = 0
        self._device_index = -1
        self._device_name = ""
        self._host_api = ""
        self._ratecv_state = None
        self._ratecv = None
        self._frames: list[bytes] = []
        self._lock = threading.Lock()

    @property
    def selected_device(self) -> str:
        if self._host_api and self._device_name:
            return f"{self._host_api} / {self._device_name}"
        return self._device_name

    @property
    def selected_input_channel(self) -> int:
        """One-based channel currently supplying the mono STT stream."""
        return self._active_input_channel + 1

    @property
    def on_frame(self):
        with self._lock:
            return self._on_frame

    @on_frame.setter
    def on_frame(self, sink) -> None:
        self.set_frame_sink(sink, flush_existing=False)

    def set_frame_sink(self, sink, *, flush_existing: bool = False) -> bytes:
        """Switch the live frame sink and optionally drain frames captured so far.

        Realtime sessions can take noticeable time to open. During that window the
        mic is already recording into `_frames`; once the session is ready, the
        controller switches to live streaming and flushes that prebuffer.
        """
        with self._lock:
            self._on_frame = sink
            if not flush_existing:
                return b""
            data = b"".join(self._frames)
            self._frames = []
            return data

    # sounddevice RawInputStream callback (runs on its own audio thread).
    def _on_audio(self, indata, frames, time_info, status):  # noqa: ARG002
        chunk = bytes(indata)
        if chunk and self.channels == 1 and self._stream_channels > 1:
            chunk, self._active_input_channel = _downmix_active_channel_pcm16(
                chunk, self._stream_channels
            )
        if chunk and self._stream_sample_rate != self.sample_rate and self._ratecv:
            try:
                chunk, self._ratecv_state = self._ratecv(
                    chunk,
                    2,
                    self.channels,
                    self._stream_sample_rate,
                    self.sample_rate,
                    self._ratecv_state,
                )
            except Exception:
                return
        callback = self.on_level
        if callback and chunk:
            try:
                samples = memoryview(chunk).cast("h")
                peak = max((abs(value) for value in samples[::4]), default=0) / 32768.0
                callback(min(1.0, peak))
            except Exception:
                pass
        with self._lock:
            sink = self._on_frame
            if sink is None:
                self._frames.append(chunk)
                return
        try:
            sink(chunk)  # stream live; don't also buffer (saves memory)
        except Exception:
            pass

    def start(self) -> None:
        try:
            import sounddevice as sd  # type: ignore
        except Exception as exc:  # pragma: no cover - environment guard
            raise AudioCaptureError(
                "sounddevice is not installed — run install/Install-Live-Deps.cmd"
            ) from exc
        from .mic_check import (
            candidate_channel_counts,
            candidate_sample_rates,
            input_device_candidates,
            refresh_device_inventory,
        )

        try:
            refresh_device_inventory(sd)
            candidates = input_device_candidates(sd)
        except Exception as exc:
            raise AudioCaptureError(f"microphone device query failed: {exc}") from exc
        if not candidates:
            raise AudioCaptureError(
                "no microphone input devices found; check Windows microphone privacy "
                "and the default recording/communications device"
            )
        if self.preferred_device is not None:
            preferred_host, preferred_name = self.preferred_device
            candidates = tuple(
                candidate
                for candidate in candidates
                if candidate.host_api == preferred_host and candidate.name == preferred_name
            )
            if not candidates:
                label = (
                    f"{preferred_host} / {preferred_name}"
                    if preferred_host
                    else preferred_name
                )
                raise AudioCaptureError(
                    "selected microphone is no longer available after refreshing "
                    f"Windows audio devices: {label}"
                )

        with self._lock:
            self._frames = []
        self._ratecv_state = None
        self._ratecv = None
        failures: list[str] = []
        for candidate in candidates:
            for input_rate in candidate_sample_rates(candidate, self.sample_rate):
                for input_channels in candidate_channel_counts(candidate, self.channels):
                    for latency in ("low", "high"):
                        stream = None
                        try:
                            self._stream_sample_rate = input_rate
                            self._stream_channels = input_channels
                            self._active_input_channel = 0
                            self._device_index = candidate.index
                            self._device_name = candidate.name
                            self._host_api = candidate.host_api
                            self._ratecv = None
                            if input_rate != self.sample_rate:
                                # Python 3.12's built-in streaming rate converter keeps
                                # chunk boundaries continuous and avoids a heavy DSP dep.
                                import warnings

                                with warnings.catch_warnings():
                                    warnings.simplefilter("ignore", DeprecationWarning)
                                    import audioop

                                self._ratecv = audioop.ratecv
                            blocksize = max(1, int(input_rate * max(5, self.block_ms) / 1000))
                            stream = sd.RawInputStream(
                                device=candidate.index,
                                samplerate=input_rate,
                                channels=input_channels,
                                dtype="int16",
                                blocksize=blocksize,
                                latency=latency,
                                callback=self._on_audio,
                            )
                            stream.start()
                            self._stream = stream
                            return
                        except Exception as exc:
                            failures.append(
                                f"{candidate.label} @ {input_rate} Hz/"
                                f"{input_channels} ch/{latency}: {exc}"
                            )
                            if stream is not None:
                                try:
                                    stream.stop()
                                    stream.close()
                                except Exception:
                                    pass
                            with self._lock:
                                self._frames = []
                            self._ratecv_state = None
        self._stream = None
        detail = " | ".join(failures[:5]) or "no usable input endpoint"
        raise AudioCaptureError(
            "microphone unavailable after checking the Windows recording and "
            f"communications defaults: {detail}"
        )

    def stop(self) -> bytes:
        """Stop the stream and return the captured PCM16 bytes."""
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        with self._lock:
            data = b"".join(self._frames)
            self._frames = []
            self._on_frame = None
            self.on_level = None
            self._ratecv_state = None
        return data

    def close(self) -> None:
        self.stop()
