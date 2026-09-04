"""Background QThreads so the UI never blocks on the pipeline or the API.

`QueueWorker` runs the existing `run_queue` off the UI thread and relays log lines
and per-file status transitions through Qt signals (queued connections are
thread-safe). `ModelWorker` refreshes the dynamic model catalog without freezing
the model pickers.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from PySide6.QtCore import QThread, Signal

from ..core.jobs import hidden_subprocess_creationflags, hidden_subprocess_startupinfo
from ..core.local_hardware import LocalHardwareProfile
from ..core.paths import ProjectPaths
from ..pipeline.queue import run_queue
from ..providers import model_catalog
from .install_progress import InstallProgressTracker, is_pip_raw_progress_line


class QueueWorker(QThread):
    log = Signal(str)
    status = Signal(str, str)          # (source path, status)
    finished_summary = Signal(object)  # QueueSummary

    def __init__(self, paths: ProjectPaths, inputs: list[Path], settings: dict[str, Any], parent=None):
        super().__init__(parent)
        self._paths = paths
        self._inputs = inputs
        self._settings = settings
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:  # noqa: D401 - QThread entry point
        def _log(message: str) -> None:
            self.log.emit(message)

        def _status(path: Path, state: str) -> None:
            self.status.emit(str(path), state)

        try:
            summary = run_queue(
                self._paths,
                self._inputs,
                self._settings,
                log=_log,
                cancel=lambda: self._cancel,
                on_status=_status,
            )
        except Exception as exc:  # never let the thread die silently
            self.log.emit(f"FATAL: {exc.__class__.__name__}: {exc}")
            summary = None
        self.finished_summary.emit(summary)


class AudioExportWorker(QThread):
    """Copy a WAV or encode a messenger-friendly M4A off the GUI thread."""

    done = Signal(str)
    failed = Signal(str)

    def __init__(self, paths: ProjectPaths, source: Path, destination: Path, parent=None):
        super().__init__(parent)
        self._paths = paths
        self._source = source
        self._destination = destination

    def run(self) -> None:
        from ..media.recorder import export_recording

        try:
            path = export_recording(self._paths, self._source, self._destination)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit(str(path))


class InstallWorker(QThread):
    """Run an opt-in module installer (`install/*.cmd`) off the UI thread,
    streaming its output lines to the log and reporting the exit code."""

    log = Signal(str)
    progress = Signal(object)
    done = Signal(int)  # process exit code (-1 on launch failure)

    def __init__(self, paths: ProjectPaths, script_path, parent=None):
        super().__init__(parent)
        self._paths = paths
        self._script = script_path
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        from ..core.jobs import run_process
        tracker = InstallProgressTracker()

        def _log(line: str) -> None:
            progress = tracker.update(line)
            if progress is not None:
                self.progress.emit(progress)
            if not is_pip_raw_progress_line(line):
                self.log.emit(line)

        try:
            # Keep the script path as its own argument: a single "set ... &&
            # call ..." string gets its inner quotes escaped as \" by
            # subprocess, which cmd.exe does not understand, so any project
            # path with spaces failed with "is not recognized as a command".
            # AUDION_NO_PAUSE travels through the environment instead.
            result = run_process(
                ["cmd", "/c", "call", str(self._script)],
                cwd=self._paths.root,
                log=_log,
                cancel=lambda: self._cancel or self.isInterruptionRequested(),
                check=False,
                extra_env={"AUDION_NO_PAUSE": "1"},
            )
            self.done.emit(result.exit_code)
        except Exception as exc:
            self.log.emit(f"FATAL: {exc.__class__.__name__}: {exc}")
            self.done.emit(-1)


class MicrophoneCheckWorker(QThread):
    """Probe the microphone off the GUI thread."""

    done = Signal(object)

    def run(self) -> None:
        from ..live.mic_check import check_microphone

        self.done.emit(check_microphone())


class ModelWorker(QThread):
    """Fetch STT + chat model lists (optionally forcing an API refresh)."""

    done = Signal(dict)   # {"stt": (models, source), "chat": (models, source)}
    failed = Signal(str)

    def __init__(self, paths: ProjectPaths, *, refresh: bool, parent=None):
        super().__init__(parent)
        self._paths = paths
        self._refresh = refresh

    def run(self) -> None:
        try:
            stt = model_catalog.list_models(self._paths, model_catalog.ROLE_STT, refresh=self._refresh)
            chat = model_catalog.list_models(self._paths, model_catalog.ROLE_CHAT, refresh=self._refresh)
            self.done.emit(
                {
                    "stt": (stt.models, stt.source),
                    "chat": (chat.models, chat.source),
                }
            )
        except Exception as exc:
            self.failed.emit(f"{exc.__class__.__name__}: {exc}")


class LocalHardwareWorker(QThread):
    """Detect GPU/local STT stack without blocking settings paint."""

    progress = Signal(int, int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, paths: ProjectPaths, parent=None):
        super().__init__(parent)
        self._paths = paths
        self._process: subprocess.Popen | None = None

    def cancel(self) -> None:
        self.requestInterruption()
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def run(self) -> None:
        python = self._paths.runtime / ("python.exe" if os.name == "nt" else "bin/python")
        executable = python if python.exists() else Path(sys.executable)
        probe = self._paths.system_core / "core" / "local_hardware_probe.py"
        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        profile_data: dict | None = None
        error = ""
        try:
            if self.isInterruptionRequested():
                return
            process = subprocess.Popen(
                [str(executable), str(probe), str(self._paths.root)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                startupinfo=hidden_subprocess_startupinfo(),
                creationflags=hidden_subprocess_creationflags(),
            )
            self._process = process
            if self.isInterruptionRequested():
                self.cancel()
            assert process.stdout is not None
            for raw_line in process.stdout:
                if self.isInterruptionRequested():
                    self.cancel()
                    break
                try:
                    payload = json.loads(raw_line)
                except (TypeError, ValueError):
                    continue
                kind = payload.get("type")
                if kind == "progress":
                    self.progress.emit(
                        int(payload.get("step", 0)),
                        int(payload.get("total", 0)),
                        str(payload.get("stage", "")),
                    )
                elif kind == "profile" and isinstance(payload.get("profile"), dict):
                    profile_data = payload["profile"]
                elif kind == "error":
                    error = str(payload.get("message", "hardware detection failed"))
            code = process.wait()
            if self.isInterruptionRequested():
                return
            if code != 0 or profile_data is None:
                self.failed.emit(error or f"hardware detection exited with code {code}")
                return
            profile_data["gpu_names"] = tuple(profile_data.get("gpu_names", ()))
            profile_data["onnx_providers"] = tuple(profile_data.get("onnx_providers", ()))
            self.done.emit(LocalHardwareProfile(**profile_data))
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(f"{exc.__class__.__name__}: {exc}")
        finally:
            self._process = None
