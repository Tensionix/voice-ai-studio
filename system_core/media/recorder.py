"""Long-form microphone recording streamed to a PCM WAV on disk.

The Live capture path buffers short utterances in memory.  Meetings are the
opposite workload, so this recorder feeds the same hot-plug/default-device
capture into a bounded queue and writes every block immediately.  Three-hour
sessions therefore use constant RAM and leave an ordinary 48 kHz/16-bit mono
WAV for the file-transcription pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import queue
import shutil
import threading
import time
import wave

from ..core.paths import ProjectPaths
from ..core.jobs import run_process
from ..live.audio_capture import AudioCapture
from .ffmpeg_tools import ffmpeg_path


RECORDING_PATTERNS = ("Audion_Recording_*.wav", "Audion_Recording_*.m4a")


def find_recordings(paths: ProjectPaths) -> list[Path]:
    """Return only Audion recorder files from input/output, newest first."""
    recordings: dict[str, Path] = {}
    for directory in (paths.input, paths.output):
        for pattern in RECORDING_PATTERNS:
            for path in directory.glob(pattern):
                if path.is_file():
                    resolved = path.resolve()
                    recordings[str(resolved).casefold()] = resolved

    def modified(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    return sorted(recordings.values(), key=modified, reverse=True)


@dataclass(frozen=True)
class RecordingResult:
    path: Path
    duration_seconds: float
    bytes_written: int
    device: str
    sample_rate: int
    channels: int
    sample_width: int


class FileRecorder:
    """Record PCM16 mono to WAV without retaining the meeting in memory."""

    def __init__(
        self,
        paths: ProjectPaths,
        *,
        sample_rate: int = 48_000,
        preferred_device: tuple[str, str] | None = None,
        capture_factory=None,
        queue_seconds: int = 10,
    ) -> None:
        self.paths = paths
        self.sample_rate = int(sample_rate)
        self.channels = 1
        self.sample_width = 2
        self.preferred_device = preferred_device
        self._capture_factory = capture_factory or AudioCapture
        # AudioCapture sends 20 ms blocks.  A bounded ten-second cushion keeps
        # the PortAudio callback non-blocking even during a brief disk stall.
        self._chunks: queue.Queue[bytes | None] = queue.Queue(
            maxsize=max(50, int(queue_seconds * 50))
        )
        self._capture = None
        self._writer: threading.Thread | None = None
        self._writer_ready = threading.Event()
        self._writer_done = threading.Event()
        self._lock = threading.Lock()
        self._recording = False
        self._paused = False
        self._accept_frames = False
        self._finalizing = False
        self._cancelled = False
        self._error = ""
        self._started_at = 0.0
        self._stopped_at = 0.0
        self._pause_started_at = 0.0
        self._paused_seconds = 0.0
        self._bytes_written = 0
        self._dropped_bytes = 0
        self._device = ""
        self._destination: Path | None = None
        self._temporary: Path | None = None
        self._result: RecordingResult | None = None

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    @property
    def is_finalizing(self) -> bool:
        with self._lock:
            return self._finalizing

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    @property
    def is_done(self) -> bool:
        return self._writer_done.is_set()

    @property
    def error(self) -> str:
        with self._lock:
            return self._error

    @property
    def selected_device(self) -> str:
        with self._lock:
            return self._device

    @property
    def destination(self) -> Path | None:
        return self._destination

    @property
    def result(self) -> RecordingResult | None:
        with self._lock:
            return self._result

    @property
    def elapsed_seconds(self) -> float:
        with self._lock:
            started = self._started_at
            stopped = self._stopped_at
            pause_started = self._pause_started_at
            paused_seconds = self._paused_seconds
        if started <= 0:
            return 0.0
        now = stopped or time.monotonic()
        active_pause = max(0.0, now - pause_started) if pause_started > 0 else 0.0
        return max(0.0, now - started - paused_seconds - active_pause)

    def _next_destination(self) -> Path:
        directory = self.paths.input
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        base = directory / f"Audion_Recording_{stamp}.wav"
        if not base.exists():
            return base
        for index in range(2, 10_000):
            candidate = directory / f"Audion_Recording_{stamp}_{index}.wav"
            if not candidate.exists():
                return candidate
        raise RuntimeError("could not allocate a unique recording filename")

    def start(self, destination: Path | None = None) -> Path:
        with self._lock:
            if self._recording or self._finalizing:
                raise RuntimeError("a recording is already active")
        target = (destination or self._next_destination()).resolve()
        if target.suffix.lower() != ".wav":
            raise ValueError("the long-form recording backend writes WAV files")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(str(target))
        temporary = target.with_name(f".{target.stem}.recording.wav")
        temporary.unlink(missing_ok=True)

        self._destination = target
        self._temporary = temporary
        self._writer_ready.clear()
        self._writer_done.clear()
        self._cancelled = False
        self._error = ""
        self._bytes_written = 0
        self._dropped_bytes = 0
        self._result = None
        self._paused = False
        self._pause_started_at = 0.0
        self._paused_seconds = 0.0
        while not self._chunks.empty():
            try:
                self._chunks.get_nowait()
            except queue.Empty:
                break

        self._writer = threading.Thread(
            target=self._write_loop,
            name="audion-file-recorder",
            daemon=True,
        )
        self._writer.start()
        if not self._writer_ready.wait(timeout=5.0):
            raise RuntimeError("recording file writer did not start")
        if self.error:
            raise RuntimeError(self.error)

        capture = self._capture_factory(
            sample_rate=self.sample_rate,
            channels=self.channels,
            block_ms=20,
            preferred_device=self.preferred_device,
        )
        capture.set_frame_sink(self._enqueue, flush_existing=False)
        with self._lock:
            self._accept_frames = True
        try:
            capture.start()
        except Exception:
            with self._lock:
                self._accept_frames = False
            self._cancelled = True
            self._chunks.put(None)
            self._writer_done.wait(timeout=5.0)
            raise
        self._capture = capture
        with self._lock:
            self._device = capture.selected_device
            self._started_at = time.monotonic()
            self._stopped_at = 0.0
            self._recording = True
            self._paused = False
            self._finalizing = False
        return target

    def _enqueue(self, chunk: bytes) -> None:
        if not chunk:
            return
        with self._lock:
            if not self._accept_frames or self._paused:
                return
        try:
            self._chunks.put_nowait(bytes(chunk))
        except queue.Full:
            with self._lock:
                self._dropped_bytes += len(chunk)
                if not self._error:
                    self._error = "recording disk writer could not keep up with the microphone"

    def _write_loop(self) -> None:
        temporary = self._temporary
        if temporary is None:
            with self._lock:
                self._error = "recording destination was not initialized"
            self._writer_ready.set()
            self._writer_done.set()
            return
        try:
            with wave.open(str(temporary), "wb") as wav:
                wav.setnchannels(self.channels)
                wav.setsampwidth(self.sample_width)
                wav.setframerate(self.sample_rate)
                self._writer_ready.set()
                while True:
                    chunk = self._chunks.get()
                    if chunk is None:
                        break
                    wav.writeframesraw(chunk)
                    with self._lock:
                        self._bytes_written += len(chunk)
        except Exception as exc:
            with self._lock:
                if not self._error:
                    self._error = str(exc)
            self._writer_ready.set()
            # Drain until Stop can enqueue its sentinel without blocking.
            while True:
                try:
                    if self._chunks.get(timeout=0.1) is None:
                        break
                except queue.Empty:
                    if not self.is_recording:
                        break
        finally:
            self._finish_file()

    def _finish_file(self) -> None:
        target = self._destination
        temporary = self._temporary
        with self._lock:
            cancelled = self._cancelled
            error = self._error
            written = self._bytes_written
            bytes_per_second = self.sample_rate * self.channels * self.sample_width
            duration = written / bytes_per_second if bytes_per_second else 0.0
            device = self._device
        try:
            if cancelled or error or written <= 0 or target is None or temporary is None:
                if not cancelled and not error and written <= 0:
                    with self._lock:
                        self._error = "the microphone returned no audio frames"
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            else:
                temporary.replace(target)
                result = RecordingResult(
                    path=target,
                    duration_seconds=duration,
                    bytes_written=written,
                    device=device,
                    sample_rate=self.sample_rate,
                    channels=self.channels,
                    sample_width=self.sample_width,
                )
                with self._lock:
                    self._result = result
        except Exception as exc:
            with self._lock:
                self._error = str(exc)
        finally:
            with self._lock:
                self._recording = False
                self._paused = False
                self._accept_frames = False
                self._finalizing = False
            self._writer_done.set()

    def set_paused(self, paused: bool) -> bool:
        """Pause/resume frame admission while keeping the capture device open."""
        now = time.monotonic()
        with self._lock:
            if not self._recording or self._finalizing:
                return False
            paused = bool(paused)
            if paused == self._paused:
                return True
            if paused:
                self._paused = True
                self._pause_started_at = now
            else:
                if self._pause_started_at > 0:
                    self._paused_seconds += max(0.0, now - self._pause_started_at)
                self._pause_started_at = 0.0
                self._paused = False
        return True

    def request_stop(self, *, cancel: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            if not self._recording:
                return
            if self._paused and self._pause_started_at > 0:
                self._paused_seconds += max(0.0, now - self._pause_started_at)
            self._paused = False
            self._pause_started_at = 0.0
            self._accept_frames = False
            self._recording = False
            self._finalizing = True
            self._cancelled = bool(cancel)
            self._stopped_at = now
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.stop()
        try:
            self._chunks.put(None, timeout=3.0)
        except queue.Full:
            with self._lock:
                self._error = self._error or "recording writer did not stop"

    def wait(self, timeout: float | None = None) -> RecordingResult | None:
        self._writer_done.wait(timeout=timeout)
        return self.result

    def close(self, *, cancel: bool = False, timeout: float = 30.0) -> RecordingResult | None:
        if self.is_recording:
            self.request_stop(cancel=cancel)
        if self.is_finalizing:
            self.wait(timeout=timeout)
        return self.result


def export_recording(
    paths: ProjectPaths,
    source: Path,
    destination: Path,
    *,
    m4a_bitrate: str = "128k",
) -> Path:
    """Save a recording as WAV or high-quality mono M4A.

    M4A uses AAC-LC at 128 kbit/s mono: comfortably above speech transparency
    while remaining messenger-friendly (about 170 MB for three hours).
    """
    source = source.resolve()
    destination = destination.resolve()
    suffix = destination.suffix.lower()
    if suffix not in {".wav", ".m4a"}:
        raise ValueError("recording export must be WAV or M4A")
    if not source.exists():
        raise FileNotFoundError(str(source))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part")
    temporary.unlink(missing_ok=True)
    try:
        if suffix == ".wav" and source.suffix.lower() == ".wav":
            shutil.copy2(source, temporary)
        else:
            command = [
                ffmpeg_path(paths),
                "-hide_banner",
                "-loglevel", "error",
                "-nostdin",
                "-y",
                "-i", str(source),
                "-vn",
                "-ar", "48000",
                "-ac", "1",
            ]
            if suffix == ".m4a":
                command += [
                    "-c:a", "aac",
                    "-profile:a", "aac_low",
                    "-b:a", m4a_bitrate,
                    "-f", "ipod",
                ]
            else:
                command += ["-c:a", "pcm_s16le", "-f", "wav"]
            command.append(str(temporary))
            run_process(command, check=True)
        temporary.replace(destination)
        return destination
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
