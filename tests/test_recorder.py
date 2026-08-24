from __future__ import annotations

from pathlib import Path
import re
import struct
import wave

from system_core.core.paths import get_project_paths
from system_core.media import recorder as recorder_module
from system_core.media.recorder import FileRecorder, export_recording, find_recordings


class _FakeCapture:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.selected_device = "Windows WASAPI / Test microphone"
        self._sink = None

    def set_frame_sink(self, sink, *, flush_existing=False):
        self._sink = sink
        return b""

    def start(self):
        frame = struct.pack("<h", 1200) * 960
        for _ in range(10):
            self._sink(frame)

    def stop(self):
        pass


class _ManualCapture(_FakeCapture):
    def start(self):
        pass

    def emit(self, count: int = 1):
        frame = struct.pack("<h", 1200) * 960
        for _ in range(count):
            self._sink(frame)


def test_default_recording_path_uses_audion_template_in_input(tmp_path: Path):
    paths = get_project_paths(tmp_path)
    recorder = FileRecorder(paths, capture_factory=_FakeCapture)

    target = recorder._next_destination()

    assert target.parent == paths.input
    assert re.fullmatch(
        r"Audion_Recording_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.wav",
        target.name,
    )


def test_recording_export_scan_accepts_only_audion_recording_templates(tmp_path: Path):
    paths = get_project_paths(tmp_path)
    paths.input.mkdir(parents=True, exist_ok=True)
    paths.output.mkdir(parents=True, exist_ok=True)
    accepted_wav = paths.input / "Audion_Recording_2026-07-17_15-42-08.wav"
    accepted_m4a = paths.output / "Audion_Recording_meeting.m4a"
    accepted_wav.touch()
    accepted_m4a.touch()
    (paths.input / "meeting.wav").touch()
    (paths.output / "Audion_Recording_meeting.mp3").touch()

    assert set(find_recordings(paths)) == {accepted_wav.resolve(), accepted_m4a.resolve()}


def test_file_recorder_streams_pcm_to_wav(tmp_path: Path):
    target = tmp_path / "meeting.wav"
    recorder = FileRecorder(
        get_project_paths(tmp_path),
        capture_factory=_FakeCapture,
    )

    recorder.start(target)
    recorder.request_stop()
    result = recorder.wait(timeout=5.0)

    assert result is not None
    assert result.path == target
    assert result.device.endswith("Test microphone")
    with wave.open(str(target), "rb") as wav:
        assert wav.getframerate() == 48_000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getnframes() == 9_600


def test_file_recorder_cancel_discards_recording(tmp_path: Path):
    target = tmp_path / "cancelled.wav"
    recorder = FileRecorder(
        get_project_paths(tmp_path),
        capture_factory=_FakeCapture,
    )

    recorder.start(target)
    recorder.request_stop(cancel=True)

    assert recorder.wait(timeout=5.0) is None
    assert not target.exists()
    assert not list(tmp_path.glob(".*.recording.wav"))


def test_file_recorder_pause_keeps_device_open_and_omits_paused_frames(tmp_path: Path):
    target = tmp_path / "paused.wav"
    capture = _ManualCapture()
    recorder = FileRecorder(
        get_project_paths(tmp_path),
        capture_factory=lambda **_kwargs: capture,
    )

    recorder.start(target)
    capture.emit(3)
    assert recorder.set_paused(True)
    assert recorder.is_paused
    capture.emit(4)
    assert recorder.set_paused(False)
    capture.emit(2)
    recorder.request_stop()
    result = recorder.wait(timeout=5.0)

    assert result is not None
    assert result.duration_seconds == 0.1
    with wave.open(str(target), "rb") as wav:
        assert wav.getnframes() == 4_800


def test_m4a_export_is_aac_lc_128k_mono(tmp_path: Path, monkeypatch):
    paths = get_project_paths(tmp_path)
    source = tmp_path / "Audion_Recording_source.wav"
    destination = tmp_path / "Audion_Recording_source.m4a"
    source.write_bytes(b"test wav payload")
    commands: list[list[str]] = []

    def fake_run(command, *, check=True):
        assert check is True
        commands.append(command)
        Path(command[-1]).write_bytes(b"test m4a payload")

    monkeypatch.setattr(recorder_module, "run_process", fake_run)

    assert export_recording(paths, source, destination) == destination.resolve()
    command = commands[0]
    assert command[command.index("-profile:a") + 1] == "aac_low"
    assert command[command.index("-b:a") + 1] == "128k"
    assert command[command.index("-ac") + 1] == "1"
    assert command[command.index("-ar") + 1] == "48000"
    assert destination.read_bytes() == b"test m4a payload"
