from __future__ import annotations

import sys
import importlib.util
from pathlib import Path
from types import SimpleNamespace

_MODULE_PATH = Path(__file__).resolve().parents[1] / "system_core" / "live" / "mic_check.py"
_SPEC = importlib.util.spec_from_file_location("audion_mic_check_test", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MIC_CHECK = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MIC_CHECK
_SPEC.loader.exec_module(_MIC_CHECK)
check_microphone = _MIC_CHECK.check_microphone
input_device_candidates = _MIC_CHECK.input_device_candidates


class _FakeStream:
    def __init__(self, *, callback, fail: bool = False):
        self._callback = callback
        self._fail = fail

    def start(self):
        if self._fail:
            raise RuntimeError("blocked by test")
        self._callback(b"\0" * 320, 160, None, "")

    def stop(self):
        pass

    def close(self):
        pass


class _FakeSoundDevice:
    default = SimpleNamespace(device=(0, -1))

    def __init__(self, devices=None, fail_stream: bool = False):
        self._devices = devices if devices is not None else [
            {"name": "Internal Mic", "max_input_channels": 1},
            {"name": "Speakers", "max_input_channels": 0},
        ]
        self._fail_stream = fail_stream

    def query_devices(self):
        return self._devices

    def RawInputStream(self, **kwargs):  # noqa: N802 - mimics sounddevice API
        return _FakeStream(callback=kwargs["callback"], fail=self._fail_stream)


def test_microphone_check_ready(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", _FakeSoundDevice())

    result = check_microphone(sample_rates=(16000, 24000), duration_seconds=0.01)

    assert result.ready
    assert result.device_count == 1
    assert result.default_input == "Internal Mic"
    assert [check.sample_rate for check in result.rate_checks] == [16000, 24000]
    assert all(check.bytes_captured > 0 for check in result.rate_checks)


def test_microphone_check_no_input_devices(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", _FakeSoundDevice(devices=[
        {"name": "Speakers", "max_input_channels": 0},
    ]))

    result = check_microphone(duration_seconds=0.01)

    assert not result.ready
    assert result.device_count == 0
    assert result.error == "no_input_devices"


def test_microphone_check_stream_failure(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", _FakeSoundDevice(fail_stream=True))

    result = check_microphone(sample_rates=(16000,), duration_seconds=0.01)

    assert not result.ready
    assert not result.partial
    assert result.error == "input_stream_failed"
    assert "blocked by test" in result.rate_checks[0].error


def test_microphone_check_falls_back_from_broken_default(monkeypatch):
    class _FallbackSoundDevice(_FakeSoundDevice):
        def __init__(self):
            super().__init__(devices=[
                {"name": "Broken Default", "max_input_channels": 1},
                {"name": "Webcam Microphone", "max_input_channels": 1},
            ])

        def RawInputStream(self, **kwargs):  # noqa: N802
            return _FakeStream(callback=kwargs["callback"], fail=kwargs.get("device") == 0)

    monkeypatch.setattr(_MIC_CHECK, "windows_default_input_names", lambda: ())
    monkeypatch.setitem(sys.modules, "sounddevice", _FallbackSoundDevice())

    result = check_microphone(sample_rates=(16000,), duration_seconds=0.01)

    assert result.ready
    assert result.rate_checks[0].device_index == 1
    assert result.rate_checks[0].device_name == "Webcam Microphone"


def test_recording_default_precedes_separate_communications_default(monkeypatch):
    sounddevice = _FakeSoundDevice(devices=[
        {"name": "System Recording Mic", "max_input_channels": 1},
        {"name": "Webcam Communications Mic", "max_input_channels": 1},
        {"name": "Stereo Mix", "max_input_channels": 2},
    ])
    monkeypatch.setattr(
        _MIC_CHECK,
        "windows_default_input_names",
        lambda: ("System Recording Mic", "Webcam Communications Mic"),
    )

    candidates = input_device_candidates(sounddevice)

    assert [candidate.index for candidate in candidates] == [0, 1, 2]
    assert "recording-default" in candidates[0].roles
    assert "communications-default" in candidates[1].roles
