from __future__ import annotations

import sys
import struct
from types import SimpleNamespace

import pytest

from system_core.live.audio_capture import AudioCapture, _downmix_active_channel_pcm16


class _NativeRateStream:
    def __init__(self, callback, sample_rate: int):
        self._callback = callback
        self._sample_rate = sample_rate

    def start(self):
        frames = self._sample_rate // 20
        self._callback(b"\0" * frames * 2, frames, None, "")

    def stop(self):
        pass

    def close(self):
        pass


class _NativeRateWebcam:
    default = SimpleNamespace(device=(0, -1))

    def __init__(self, native_rate: int):
        self.native_rate = native_rate
        self.opened_rates: list[int] = []
        self.terminate_count = 0
        self.initialize_count = 0

    def _terminate(self):
        self.terminate_count += 1

    def _initialize(self):
        self.initialize_count += 1

    def query_devices(self):
        return [{
            "name": "USB Webcam Microphone",
            "max_input_channels": 1,
            "default_samplerate": float(self.native_rate),
        }]

    def query_hostapis(self):
        return ()

    def RawInputStream(self, **kwargs):  # noqa: N802
        rate = int(kwargs["samplerate"])
        self.opened_rates.append(rate)
        if rate != self.native_rate:
            raise RuntimeError("native rate required")
        assert kwargs["device"] == 0
        assert kwargs["channels"] == 1
        assert kwargs["dtype"] == "int16"
        return _NativeRateStream(kwargs["callback"], rate)


@pytest.mark.parametrize("native_rate", [44100, 48000])
def test_capture_uses_native_webcam_rate_and_resamples(monkeypatch, native_rate):
    sounddevice = _NativeRateWebcam(native_rate)
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)

    capture = AudioCapture(sample_rate=16000, channels=1)
    capture.start()
    pcm = capture.stop()

    assert native_rate in sounddevice.opened_rates
    assert capture.selected_device == "USB Webcam Microphone"
    assert 1500 <= len(pcm) <= 1700  # ~50 ms of mono PCM16 at 16 kHz


def test_capture_refreshes_portaudio_inventory_on_every_activation(monkeypatch):
    sounddevice = _NativeRateWebcam(48000)
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)
    capture = AudioCapture(sample_rate=16000, channels=1)

    capture.start()
    capture.stop()
    capture.start()
    capture.stop()

    assert sounddevice.terminate_count == 2
    assert sounddevice.initialize_count == 2


class _SelectableMicrophones(_NativeRateWebcam):
    default = SimpleNamespace(device=(0, -1))

    def query_devices(self):
        return [
            {
                "name": "Internal Microphone",
                "max_input_channels": 1,
                "default_samplerate": float(self.native_rate),
            },
            {
                "name": "USB Headset Microphone",
                "max_input_channels": 1,
                "default_samplerate": float(self.native_rate),
            },
        ]

    def RawInputStream(self, **kwargs):  # noqa: N802
        self.opened_rates.append(int(kwargs["samplerate"]))
        self.opened_device = int(kwargs["device"])
        return _NativeRateStream(kwargs["callback"], int(kwargs["samplerate"]))


def test_capture_honors_session_only_forced_microphone(monkeypatch):
    sounddevice = _SelectableMicrophones(48000)
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)
    capture = AudioCapture(
        sample_rate=16000,
        channels=1,
        preferred_device=("", "USB Headset Microphone"),
    )

    capture.start()
    capture.stop()

    assert sounddevice.opened_device == 1
    assert capture.selected_device == "USB Headset Microphone"


class _StereoStream:
    def __init__(self, callback, active_channel: int):
        self._callback = callback
        self._active_channel = active_channel

    def start(self):
        frame = [0, 0]
        frame[self._active_channel] = 2400
        pcm = struct.pack("<" + "h" * 320, *(frame * 160))
        self._callback(pcm, 160, None, "")

    def stop(self):
        pass

    def close(self):
        pass


class _StereoRealtek:
    default = SimpleNamespace(device=(0, -1))

    def __init__(self, active_channel: int):
        self.active_channel = active_channel
        self.opened_channels: list[int] = []

    def query_devices(self):
        return [{
            "name": "Microphone (Realtek Audio)",
            "max_input_channels": 2,
            "default_samplerate": 48000.0,
        }]

    def query_hostapis(self):
        return ()

    def RawInputStream(self, **kwargs):  # noqa: N802
        self.opened_channels.append(int(kwargs["channels"]))
        return _StereoStream(kwargs["callback"], self.active_channel)


@pytest.mark.parametrize("active_channel", [0, 1])
def test_stereo_input_selects_whichever_channel_has_signal(monkeypatch, active_channel):
    sounddevice = _StereoRealtek(active_channel)
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)

    capture = AudioCapture(sample_rate=16000, channels=1)
    capture.start()
    pcm = capture.stop()

    assert sounddevice.opened_channels[0] == 2
    assert capture.selected_input_channel == active_channel + 1
    samples = list(memoryview(pcm).cast("h"))
    assert samples and set(samples) == {2400}


def test_stereo_input_mixes_two_real_inputs_without_clipping():
    stereo = struct.pack("<hhhh", 1000, 2000, 3000, 1000)

    mono, dominant = _downmix_active_channel_pcm16(stereo, 2)

    assert dominant == 0
    assert list(memoryview(mono).cast("h")) == [2250, 3000]
