"""Hidden subprocess runner for external tools (ffmpeg / ffprobe).

Adapted from the Audion family `core/jobs.py`: hides child console windows on
Windows, forces UTF-8 for Python children, decodes mixed-encoding tool output
without mojibake, streams lines to a callback, and supports cooperative cancel.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import locale
import os
import subprocess

LogCallback = Callable[[str], None]
CancelCallback = Callable[[], bool]


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def hidden_subprocess_startupinfo() -> "subprocess.STARTUPINFO | None":
    if os.name != "nt" or not hasattr(subprocess, "STARTUPINFO"):
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return startupinfo


def hidden_subprocess_creationflags() -> int:
    if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return int(subprocess.CREATE_NO_WINDOW)
    return 0


def _windows_codepage(name: str) -> str | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        value = int(getattr(ctypes.windll.kernel32, name)())
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    return f"cp{value}" if value > 0 else None


def _fallback_encodings() -> list[str]:
    candidates = [
        _windows_codepage("GetOEMCP"),
        "cp866",
        locale.getpreferredencoding(False),
        _windows_codepage("GetACP"),
        "cp1251",
    ]
    result: list[str] = []
    for encoding in candidates:
        if encoding and encoding.lower() not in {item.lower() for item in result}:
            result.append(encoding)
    return result


def _score(text: str) -> int:
    score = 0
    for char in text:
        code = ord(char)
        if char in "\r\n\t":
            score += 1
        elif 32 <= code < 127:
            score += 1
        elif "А" <= char <= "я" or char in "Ёё":
            score += 8
        elif code == 0xFFFD:
            score -= 100
        elif code == 0:
            score -= 80
        elif code < 32:
            score -= 25
    return score


def decode_process_bytes(data: bytes) -> str:
    """Decode one chunk of tool output, picking the best-scoring encoding."""
    if not data:
        return ""
    candidates: list[str] = []

    def add(encoding: str, *, errors: str = "strict") -> None:
        try:
            text = data.decode(encoding, errors=errors)
        except (LookupError, UnicodeDecodeError):
            return
        if text not in candidates:
            candidates.append(text)

    if data.startswith(b"\xef\xbb\xbf"):
        add("utf-8-sig")
    add("utf-8")
    for encoding in _fallback_encodings():
        add(encoding)
    if not candidates:
        add("utf-8", errors="replace")
    return max(candidates, key=_score)


def decoded_lines(raw: bytes) -> list[str]:
    text = decode_process_bytes(raw).replace("\r\n", "\n").replace("\r", "\n")
    return [line.rstrip() for line in text.split("\n") if line.strip()]


def run_process(
    command: list[str],
    *,
    cwd: Path | None = None,
    log: LogCallback | None = None,
    cancel: CancelCallback | None = None,
    check: bool = True,
) -> ProcessResult:
    """Run a child process hidden, streaming stdout/stderr lines to `log`."""
    if not command:
        raise ValueError("Command is empty.")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    process = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        startupinfo=hidden_subprocess_startupinfo(),
        creationflags=hidden_subprocess_creationflags(),
    )

    lines: list[str] = []
    assert process.stdout is not None
    for raw_line in process.stdout:
        if cancel and cancel():
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            raise RuntimeError("Operation cancelled by user.")
        for line in decoded_lines(raw_line):
            lines.append(line)
            if log:
                log(line)

    exit_code = process.wait()
    if check and exit_code != 0:
        raise RuntimeError(f"Command failed (exit {exit_code}): {command[0]}")
    return ProcessResult(exit_code=exit_code, lines=tuple(lines))


def run_capture(command: list[str], *, cwd: Path | None = None) -> ProcessResult:
    """Run a process to completion without raising on non-zero exit."""
    return run_process(command, cwd=cwd, check=False)
