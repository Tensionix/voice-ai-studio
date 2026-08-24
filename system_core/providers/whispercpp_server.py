"""Warm (resident) whisper.cpp server for low-latency live dictation.

Batch dictation spawns a fresh ``whisper-cli`` per utterance, which reloads the
GGML model (~1.5 GB for Turbo) into VRAM every time -- that is the multi-second
pause before text appears. This module keeps one ``whisper-server`` process alive
with the model resident, so each utterance only pays inference cost.

Robustness mirrors the locked fallback policy:
  * the ``auto`` backend tries the GPU build first; if the server dies on start
    or never becomes ready (no device / init failure / hang), it relaunches with
    ``-ng`` (CPU);
  * if even that fails, callers drop to the batch CLI provider.

Idle unload: when ``idle_unload_seconds > 0`` a watchdog terminates the resident
process after that many idle seconds to free VRAM (it is re-warmed on next use).
The default is ``0`` = keep warm; the model is always released on ``close()``
(Live disarm / app exit / compute-mode switch).

Networking stays on loopback (127.0.0.1) -- no traffic leaves the machine, and
no files are written outside the project.
"""

from __future__ import annotations

import atexit
import json
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from ..core.editions import find_whispercpp_server, resolve_whispercpp_model
from ..core.jobs import (
    hidden_subprocess_creationflags,
    hidden_subprocess_startupinfo,
)
from ..core.paths import ProjectPaths

DEFAULT_WHISPERCPP_MODEL = "turbo"

# Model load (cold disk -> VRAM) dominates the first warm; the readiness poll
# also doubles as the "alive but hangs" trigger from the fallback policy.
_READY_TIMEOUT = 90.0
_READY_POLL = 0.25
_INFER_TIMEOUT = 120.0


class WhisperCppServerError(RuntimeError):
    """The resident server could not be started or used; fall back to batch."""


def _free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _assign_kill_on_close_job(proc):
    """Tie ``proc`` to a Windows Job Object so the OS reaps it if this process
    dies for *any* reason -- including a crash or a Task-Manager force-kill that
    never runs ``close()``. Without this, the server would orphan and keep VRAM.

    Returns an opaque job handle to keep alive for the process lifetime, or None
    on non-Windows / failure (graceful ``close()`` still covers the normal exit).
    """
    import os

    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        class _BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.POINTER(wintypes.ULONG)),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class _EXT(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BASIC),
                ("IoInfo", _IO),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        _JobObjectExtendedLimitInformation = 9

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _EXT()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job, _JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info),
        ):
            kernel32.CloseHandle(job)
            return None
        if not kernel32.AssignProcessToJobObject(job, int(proc._handle)):
            kernel32.CloseHandle(job)
            return None
        return job
    except Exception:
        return None


def _close_job(job) -> None:
    if job is None:
        return
    try:
        import ctypes

        ctypes.windll.kernel32.CloseHandle(int(job))
    except Exception:
        pass


class WhisperCppServer:
    """Manage one resident whisper.cpp server process (lazy, thread-safe).

    Every engine seam is injectable so the lifecycle is testable without a real
    binary, network, or GPU:
      * ``launcher(command) -> process`` replaces the ``subprocess.Popen`` call;
      * ``http_get(url, timeout) -> status`` replaces the readiness probe;
      * ``http_post(url, wav_path, data, timeout) -> text`` replaces inference.
    """

    def __init__(
        self,
        paths: ProjectPaths,
        model: str | None = None,
        *,
        backend: str = "auto",
        idle_unload_seconds: int = 0,
        host: str = "127.0.0.1",
        binary: Optional[Path] = None,
        launcher=None,
        http_get=None,
        http_post=None,
    ) -> None:
        self.paths = paths
        self.model = model or DEFAULT_WHISPERCPP_MODEL
        self.backend = backend or "auto"
        self.idle_unload_seconds = max(0, int(idle_unload_seconds or 0))
        self.host = host
        self._binary = binary
        self._launcher = launcher
        self._http_get = http_get
        self._http_post = http_post

        self._lock = threading.RLock()
        self._proc = None
        self._job = None
        self._port = 0
        self._active_backend: Optional[str] = None
        self._last_used = 0.0
        self._watchdog: Optional[threading.Thread] = None
        self._stop_watchdog = threading.Event()

    # --- availability --------------------------------------------------------
    def available(self) -> bool:
        return self._resolve_binary() is not None and self._resolve_model() is not None

    def _resolve_binary(self) -> Optional[Path]:
        if self._binary is not None:
            return self._binary
        return find_whispercpp_server(self.paths)

    def _resolve_model(self) -> Optional[Path]:
        return resolve_whispercpp_model(self.paths, self.model)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self._port}"

    @property
    def active_backend(self) -> Optional[str]:
        return self._active_backend

    # --- lifecycle -----------------------------------------------------------
    def ensure_running(self, *, timeout: float = _READY_TIMEOUT) -> None:
        with self._lock:
            if self._is_alive():
                return
            binary = self._resolve_binary()
            model = self._resolve_model()
            if binary is None or model is None:
                raise WhisperCppServerError(
                    "whisper.cpp server binary or model not found."
                )
            # auto: GPU first, then CPU. Forced cpu: CPU only. Forced legacy GPU:
            # GPU only (no silent demotion), matching the batch provider policy.
            if self.backend == "cpu":
                order = ["cpu"]
            elif self.backend in {"vulkan", "cuda", "cublas"}:
                order = [self.backend]
            else:
                order = ["auto", "cpu"]
            errors: list[str] = []
            for backend in order:
                try:
                    self._launch(binary, model, backend, timeout)
                    self._active_backend = backend
                    self._start_watchdog()
                    return
                except Exception as exc:  # noqa: BLE001 - try the next backend
                    errors.append(f"{backend}: {exc}")
                    self._kill_proc()
            raise WhisperCppServerError(
                "whisper.cpp server failed to start (" + "; ".join(errors) + ")."
            )

    def _launch(self, binary: Path, model: Path, backend: str, timeout: float) -> None:
        self._port = _free_port(self.host)
        command = [
            str(binary),
            "-m", str(model),
            "--host", self.host,
            "--port", str(self._port),
        ]
        if backend == "cpu":
            command.append("-ng")
        if self._launcher is not None:
            self._proc = self._launcher(command)
        else:
            self.paths.workspace.mkdir(parents=True, exist_ok=True)
            self._proc = subprocess.Popen(
                command,
                cwd=str(self.paths.root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=hidden_subprocess_startupinfo(),
                creationflags=hidden_subprocess_creationflags(),
            )
            # OS-level safety net: if this process is force-killed or crashes
            # before close() runs, the job closing reaps the server too.
            self._job = _assign_kill_on_close_job(self._proc)
        self._wait_ready(timeout)
        self._touch()

    def _wait_ready(self, timeout: float) -> None:
        deadline = time.monotonic() + max(1.0, timeout)
        url = self.base_url + "/"
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                raise WhisperCppServerError(
                    f"server exited early (code {self._proc.returncode})."
                )
            if self._probe(url):
                return
            time.sleep(_READY_POLL)
        raise WhisperCppServerError("server did not become ready in time (hang).")

    def _probe(self, url: str) -> bool:
        try:
            if self._http_get is not None:
                code = self._http_get(url, 2.0)
            else:
                import httpx  # base dependency; imported lazily

                code = httpx.get(url, timeout=2.0).status_code
            return code is not None and int(code) < 500
        except Exception:
            return False

    def _is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # --- inference -----------------------------------------------------------
    def transcribe_wav(
        self,
        wav_path: Path,
        *,
        language: str | None = None,
        prompt: str | None = None,
        timeout: float = _INFER_TIMEOUT,
    ) -> str:
        with self._lock:
            self.ensure_running()
            url = self.base_url + "/inference"
            data = {"response_format": "json", "temperature": "0"}
            if language and language.lower() != "auto":
                data["language"] = language
            if prompt:
                data["prompt"] = prompt
            try:
                payload = self._post_payload(url, wav_path, data, timeout)
            except Exception as exc:  # server may have died mid-session
                # Drop the process so the next call relaunches, and signal the
                # caller (live transcriber) to fall back to batch for this turn.
                self._kill_proc()
                raise WhisperCppServerError(str(exc)) from exc
            self._touch()
            return str(payload.get("text", "") or "").strip()

    def transcribe_wav_verbose(
        self,
        wav_path: Path,
        *,
        language: str | None = None,
        prompt: str | None = None,
        timeout: float = _INFER_TIMEOUT,
    ) -> dict:
        """Return whisper.cpp's timestamped JSON while keeping the model warm."""
        with self._lock:
            self.ensure_running()
            url = self.base_url + "/inference"
            data = {"response_format": "verbose_json", "temperature": "0"}
            if language and language.lower() != "auto":
                data["language"] = language
            if prompt:
                data["prompt"] = prompt
            try:
                payload = self._post_payload(url, wav_path, data, timeout)
            except Exception as exc:
                self._kill_proc()
                raise WhisperCppServerError(str(exc)) from exc
            self._touch()
            return payload

    def _post(self, url: str, wav_path: Path, data: dict[str, str], timeout: float) -> str:
        payload = self._post_payload(url, wav_path, data, timeout)
        return str(payload.get("text", "") or "")

    def _post_payload(
        self, url: str, wav_path: Path, data: dict[str, str], timeout: float
    ) -> dict:
        if self._http_post is not None:
            raw = self._http_post(url, wav_path, data, timeout)
        else:
            import httpx  # base dependency; imported lazily

            with open(wav_path, "rb") as handle:
                files = {"file": (wav_path.name, handle, "audio/wav")}
                response = httpx.post(url, files=files, data=data, timeout=timeout)
            response.raise_for_status()
            try:
                raw = response.json()
            except Exception:
                raw = response.text or ""
        if isinstance(raw, dict):
            return raw
        try:
            payload = json.loads(str(raw))
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
        return {"text": str(raw or "")}

    # --- idle unload ---------------------------------------------------------
    def _touch(self) -> None:
        self._last_used = time.monotonic()

    def _start_watchdog(self) -> None:
        if self.idle_unload_seconds <= 0:
            return
        if self._watchdog is not None and self._watchdog.is_alive():
            return
        self._stop_watchdog.clear()
        self._watchdog = threading.Thread(
            target=self._watch, name="whispercpp-idle", daemon=True
        )
        self._watchdog.start()

    def _watch(self) -> None:
        tick = min(5.0, max(1.0, self.idle_unload_seconds / 4))
        while not self._stop_watchdog.wait(tick):
            with self._lock:
                if not self._is_alive():
                    return
                if time.monotonic() - self._last_used >= self.idle_unload_seconds:
                    self._kill_proc()
                    return

    # --- teardown ------------------------------------------------------------
    def _kill_proc(self) -> None:
        proc, self._proc = self._proc, None
        job, self._job = self._job, None
        self._active_backend = None
        if proc is not None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
            except Exception:
                pass
        _close_job(job)

    def close(self) -> None:
        # Not guarded by the big lock: disarm must not block behind a long
        # inference. Killing the process makes any in-flight POST raise, which
        # the caller already treats as a batch fallback.
        self._stop_watchdog.set()
        self._kill_proc()


_FILE_SERVER_CACHE: dict[tuple[str, str, str], WhisperCppServer] = {}
_FILE_SERVER_CACHE_LOCK = threading.RLock()


def cached_file_server(
    paths: ProjectPaths,
    model: str | None,
    backend: str,
) -> WhisperCppServer:
    """Keep exactly one file-transcription model resident for the app process."""
    key = (str(paths.root.resolve()), str(model or DEFAULT_WHISPERCPP_MODEL), str(backend or "auto"))
    with _FILE_SERVER_CACHE_LOCK:
        for old_key, old_server in list(_FILE_SERVER_CACHE.items()):
            if old_key != key:
                old_server.close()
                _FILE_SERVER_CACHE.pop(old_key, None)
        server = _FILE_SERVER_CACHE.get(key)
        if server is None:
            server = WhisperCppServer(paths, model, backend=backend, idle_unload_seconds=0)
            _FILE_SERVER_CACHE[key] = server
        return server


def close_cached_file_servers() -> None:
    with _FILE_SERVER_CACHE_LOCK:
        servers = list(_FILE_SERVER_CACHE.values())
        _FILE_SERVER_CACHE.clear()
    for server in servers:
        server.close()


atexit.register(close_cached_file_servers)
