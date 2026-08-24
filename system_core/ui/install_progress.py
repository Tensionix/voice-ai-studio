"""Parse installer output into progress-bar friendly values."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
import time


_SIZE = {
    "B": 1.0,
    "KB": 1024.0,
    "MB": 1024.0**2,
    "GB": 1024.0**3,
    "TB": 1024.0**4,
}

_PROGRESS_RE = re.compile(
    r"(?P<done>\d+(?:[.,]\d+)?)\s*(?P<done_unit>[KMGT]?B)?\s*/\s*"
    r"(?P<total>\d+(?:[.,]\d+)?)\s*(?P<total_unit>[KMGT]?B)"
    r"(?:\s*\((?P<pct>\d+(?:[.,]\d+)?)%\))?"
    r"(?:\s*@?\s*(?P<speed>\d+(?:[.,]\d+)?)\s*(?P<speed_unit>[KMGT]?B)/s)?"
    r"(?:\s+(?P<eta>\d+:\d{2}(?::\d{2})?))?",
    re.IGNORECASE,
)
_PIP_RAW_RE = re.compile(r"^Progress\s+(?P<done>\d+)\s+of\s+(?P<total>\d+)", re.IGNORECASE)
_PIP_DOWNLOAD_RE = re.compile(r"^\s*(?:Downloading|Using cached)\s+(?P<name>\S+)", re.IGNORECASE)
_AUDION_STEP_RE = re.compile(
    r"^\[audion-step\]\s*(?P<done>\d+)\s*/\s*(?P<total>\d+)\s*(?P<label>.*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class InstallProgress:
    percent: float
    done_text: str
    total_text: str
    speed_text: str = ""
    eta_text: str = ""
    label: str = ""


def _unit(unit: str | None) -> str:
    return (unit or "B").upper()


def _bytes(value: str, unit: str | None) -> float:
    return float(value.replace(",", ".")) * _SIZE.get(_unit(unit), 1.0)


def _eta(seconds: float) -> str:
    if seconds < 0 or seconds == float("inf"):
        return ""
    whole = int(seconds + 0.5)
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _format_size(value: float) -> str:
    for unit in ("TB", "GB", "MB", "KB"):
        scale = _SIZE[unit]
        if value >= scale:
            amount = value / scale
            digits = 2 if unit in {"GB", "TB"} else 1
            return f"{amount:.{digits}f} {unit}"
    return f"{int(value)} B"


def parse_install_progress(line: str) -> InstallProgress | None:
    text = (line or "").strip()
    step_match = _AUDION_STEP_RE.match(text)
    if step_match:
        done = float(step_match.group("done"))
        total = float(step_match.group("total"))
        if total <= 0:
            return None
        return InstallProgress(
            percent=max(0.0, min(100.0, (done / total) * 100.0)),
            done_text=str(int(done)),
            total_text=str(int(total)),
            label=(step_match.group("label") or "").strip(),
        )

    match = _PROGRESS_RE.search(text)
    if not match:
        return None

    total_unit = _unit(match.group("total_unit"))
    done_unit = _unit(match.group("done_unit") or total_unit)
    done = _bytes(match.group("done"), done_unit)
    total = _bytes(match.group("total"), total_unit)
    if total <= 0:
        return None

    percent = float(match.group("pct").replace(",", ".")) if match.group("pct") else ((done / total) * 100.0)
    speed_text = ""
    eta_text = match.group("eta") or ""
    if match.group("speed"):
        speed_unit = _unit(match.group("speed_unit"))
        speed_text = f"{match.group('speed')} {speed_unit}/s"
        speed = _bytes(match.group("speed"), speed_unit)
        if not eta_text and speed > 0:
            eta_text = _eta((total - done) / speed)

    label = ""
    prefix = text[: match.start()].strip(" :-")
    if prefix and len(prefix) <= 48:
        label = prefix

    return InstallProgress(
        percent=max(0.0, min(100.0, percent)),
        done_text=f"{match.group('done')} {done_unit}",
        total_text=f"{match.group('total')} {total_unit}",
        speed_text=speed_text,
        eta_text=eta_text,
        label=label,
    )


def is_pip_raw_progress_line(line: str) -> bool:
    return bool(_PIP_RAW_RE.match((line or "").strip()))


class InstallProgressTracker:
    """Stateful progress parser for installers and pip's raw progress output."""

    def __init__(self, clock: Callable[[], float] | None = None):
        self._clock = clock or time.monotonic
        self._label = "pip download"
        self._last_done: float | None = None
        self._last_total: float | None = None
        self._last_time: float | None = None

    def update(self, line: str) -> InstallProgress | None:
        text = (line or "").strip()
        label_match = _PIP_DOWNLOAD_RE.match(text)
        if label_match:
            self._label = label_match.group("name")

        raw_match = _PIP_RAW_RE.match(text)
        if raw_match:
            return self._parse_raw(raw_match)

        parsed = parse_install_progress(text)
        if parsed is not None:
            return parsed
        return None

    def _parse_raw(self, match: re.Match[str]) -> InstallProgress | None:
        done = float(match.group("done"))
        total = float(match.group("total"))
        if total <= 0:
            return None

        now = self._clock()
        speed_text = ""
        eta_text = ""
        same_download = (
            self._last_done is not None
            and self._last_total == total
            and done >= self._last_done
            and self._last_time is not None
        )
        if same_download:
            elapsed = max(now - self._last_time, 0.001)
            speed = max((done - self._last_done) / elapsed, 0.0)
            if speed > 0:
                speed_text = f"{_format_size(speed)}/s"
                eta_text = _eta((total - done) / speed)

        self._last_done = done
        self._last_total = total
        self._last_time = now

        return InstallProgress(
            percent=max(0.0, min(100.0, (done / total) * 100.0)),
            done_text=_format_size(done),
            total_text=_format_size(total),
            speed_text=speed_text,
            eta_text=eta_text,
            label=self._label,
        )
