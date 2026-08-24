"""Microphone discovery and the short diagnostic used by Maintenance.

Windows exposes the same input through several PortAudio hosts.  In particular,
passing ``device=None`` can fail on Windows 10 even though the concrete default
recording endpoint works.  This module therefore resolves the Windows recording
and communications defaults, always opens an explicit device index, and keeps
same-device host fallbacks ahead of unrelated inputs such as Loopback.
"""

from __future__ import annotations

import ctypes
import sys
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class InputDeviceCandidate:
    index: int
    name: str
    host_api: str = ""
    default_sample_rate: int = 0
    max_input_channels: int = 1
    roles: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return f"{self.host_api} / {self.name}" if self.host_api else self.name


@dataclass(frozen=True)
class MicRateCheck:
    sample_rate: int
    ok: bool
    bytes_captured: int = 0
    error: str = ""
    statuses: tuple[str, ...] = ()
    device_index: int = -1
    device_name: str = ""
    host_api: str = ""
    input_sample_rate: int = 0
    input_channels: int = 1
    channel_peaks: tuple[int, ...] = ()


@dataclass(frozen=True)
class MicCheckResult:
    sounddevice_available: bool
    input_devices: tuple[str, ...] = ()
    default_input: str = ""
    rate_checks: tuple[MicRateCheck, ...] = ()
    error: str = ""

    @property
    def device_count(self) -> int:
        return len(self.input_devices)

    @property
    def ready(self) -> bool:
        return (
            self.sounddevice_available
            and bool(self.input_devices)
            and bool(self.rate_checks)
            and all(check.ok for check in self.rate_checks)
        )

    @property
    def partial(self) -> bool:
        return (
            self.sounddevice_available
            and bool(self.input_devices)
            and bool(self.rate_checks)
            and any(check.ok for check in self.rate_checks)
            and not self.ready
        )


def _device_name(device: object, index: int) -> str:
    if isinstance(device, dict):
        name = str(device.get("name") or "").strip()
        return name or f"Input #{index}"
    name = str(device).strip()
    return name or f"Input #{index}"


def _input_channels(device: object) -> int:
    if isinstance(device, dict):
        try:
            return int(device.get("max_input_channels") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _default_rate(device: object) -> int:
    if not isinstance(device, dict):
        return 0
    try:
        value = int(round(float(device.get("default_samplerate") or 0)))
    except (TypeError, ValueError):
        return 0
    return value if 8_000 <= value <= 384_000 else 0


def _default_input_index(sd) -> int:
    try:
        raw = sd.default.device
        try:
            # sounddevice uses a private _InputOutputPair object on Windows;
            # it is indexable but is not a tuple/list.
            index = raw[0]
        except (IndexError, KeyError, TypeError):
            index = raw
        return int(index) if index is not None and int(index) >= 0 else -1
    except Exception:
        return -1


def _host_apis(sd) -> tuple[object, ...]:
    try:
        return tuple(sd.query_hostapis())
    except Exception:
        return ()


def _host_name(device: object, host_apis: tuple[object, ...]) -> str:
    if not isinstance(device, dict):
        return ""
    try:
        api = host_apis[int(device.get("hostapi"))]
    except (IndexError, TypeError, ValueError):
        return ""
    if isinstance(api, dict):
        return str(api.get("name") or "").strip()
    return str(api).strip()


def _normalise_name(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _names_match(left: str, right: str) -> bool:
    a, b = _normalise_name(left), _normalise_name(right)
    if not a or not b:
        return False
    return a == b or (min(len(a), len(b)) >= 8 and (a in b or b in a))


# Minimal Core Audio COM declarations.  Keeping this local avoids adding pycaw
# or comtypes solely to discover the two Windows default capture roles.
class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, value: str):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", wintypes.DWORD)]


class _PROPVARIANT_VALUE(ctypes.Union):
    _fields_ = [("pwszVal", wintypes.LPWSTR), ("ptr", ctypes.c_void_p)]


class _PROPVARIANT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [
        ("vt", wintypes.USHORT),
        ("reserved1", wintypes.USHORT),
        ("reserved2", wintypes.USHORT),
        ("reserved3", wintypes.USHORT),
        ("value", _PROPVARIANT_VALUE),
    ]


def _com_method(pointer, index: int, restype, *argtypes):
    table = ctypes.cast(
        pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    ).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(table[index])


def _release_com(pointer) -> None:
    if pointer:
        try:
            _com_method(pointer, 2, wintypes.ULONG)(pointer)
        except Exception:
            pass


def windows_default_input_names() -> tuple[str, ...]:
    """Return Windows recording-default, then communications-default names."""
    if sys.platform != "win32":
        return ()
    ole32 = None
    enumerator = ctypes.c_void_p()
    initialized_here = False
    names: list[str] = []
    hresult = ctypes.c_long
    try:
        ole32 = ctypes.OleDLL("ole32")
        ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        ole32.CoInitializeEx.restype = hresult
        init_result = int(ole32.CoInitializeEx(None, 2))  # COINIT_APARTMENTTHREADED
        initialized_here = init_result in (0, 1)

        ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(_GUID),
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        ole32.CoCreateInstance.restype = hresult
        clsid = _GUID.parse("BCDE0395-E52F-467C-8E3D-C4579291692E")
        iid = _GUID.parse("A95664D2-9614-4F35-A746-DE8DB63617E6")
        if int(ole32.CoCreateInstance(
            ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(enumerator)
        )) < 0:
            return ()

        ole32.PropVariantClear.argtypes = [ctypes.POINTER(_PROPVARIANT)]
        ole32.PropVariantClear.restype = hresult
        friendly_name_key = _PROPERTYKEY(
            _GUID.parse("A45C254E-DF1C-4EFD-8020-67D146A850E0"), 14
        )
        get_default = _com_method(
            enumerator,
            4,
            hresult,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
        )
        # eConsole is the normal recording default; eCommunications may differ.
        for role in (0, 2):
            device = ctypes.c_void_p()
            store = ctypes.c_void_p()
            value = _PROPVARIANT()
            try:
                if int(get_default(enumerator, 1, role, ctypes.byref(device))) < 0:
                    continue
                open_store = _com_method(
                    device, 4, hresult, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)
                )
                if int(open_store(device, 0, ctypes.byref(store))) < 0:
                    continue
                get_value = _com_method(
                    store,
                    5,
                    hresult,
                    ctypes.POINTER(_PROPERTYKEY),
                    ctypes.POINTER(_PROPVARIANT),
                )
                if int(get_value(
                    store, ctypes.byref(friendly_name_key), ctypes.byref(value)
                )) >= 0 and value.vt == 31 and value.pwszVal:
                    name = str(value.pwszVal).strip()
                    if name and name not in names:
                        names.append(name)
            finally:
                try:
                    if value.vt:
                        ole32.PropVariantClear(ctypes.byref(value))
                except Exception:
                    pass
                _release_com(store)
                _release_com(device)
    except Exception:
        return tuple(names)
    finally:
        _release_com(enumerator)
        if ole32 is not None and initialized_here:
            try:
                ole32.CoUninitialize()
            except Exception:
                pass
    return tuple(names)


def refresh_device_inventory(sd) -> None:
    """Refresh PortAudio after USB/Bluetooth hot-plug, when supported."""
    terminate = getattr(sd, "_terminate", None)
    initialize = getattr(sd, "_initialize", None)
    if not callable(terminate) or not callable(initialize):
        return
    try:
        terminate()
        initialize()
    except Exception:
        # Device enumeration below still works with the existing inventory.
        try:
            initialize()
        except Exception:
            pass


def input_device_candidates(sd) -> tuple[InputDeviceCandidate, ...]:
    """Order explicit input devices by Windows defaults and safe fallbacks."""
    devices = tuple(sd.query_devices())
    host_apis = _host_apis(sd)
    all_candidates: list[InputDeviceCandidate] = []
    for index, device in enumerate(devices):
        if _input_channels(device) <= 0:
            continue
        all_candidates.append(
            InputDeviceCandidate(
                index=index,
                name=_device_name(device, index),
                host_api=_host_name(device, host_apis),
                default_sample_rate=_default_rate(device),
                max_input_channels=_input_channels(device),
            )
        )
    if not all_candidates:
        return ()

    by_index = {candidate.index: candidate for candidate in all_candidates}
    default_index = _default_input_index(sd)
    default_name = by_index.get(default_index, InputDeviceCandidate(-1, "")).name
    windows_defaults = windows_default_input_names()
    host_rank = {
        "windows wasapi": 0,
        "windows directsound": 1,
        "mme": 2,
        "windows wdm-ks": 3,
    }

    ordered: list[InputDeviceCandidate] = []
    roles_by_index: dict[int, list[str]] = {}

    def add(candidate: InputDeviceCandidate, role: str = "") -> None:
        if role:
            roles_by_index.setdefault(candidate.index, []).append(role)
        if all(existing.index != candidate.index for existing in ordered):
            ordered.append(candidate)

    # PortAudio's concrete default first.  Explicitly passing this index fixes a
    # Windows 10 MME failure observed when the same stream used device=None.
    if default_index in by_index:
        add(by_index[default_index], "recording-default")

    for position, endpoint_name in enumerate(windows_defaults):
        role = "recording-default" if position == 0 else "communications-default"
        matching = [c for c in all_candidates if _names_match(c.name, endpoint_name)]
        matching.sort(key=lambda c: host_rank.get(c.host_api.casefold(), 9))
        for candidate in matching:
            add(candidate, role)

    # The same physical endpoint can have slightly different/truncated names in
    # MME, DirectSound and WASAPI.  Keep these ahead of unrelated devices.
    if default_name:
        matching = [c for c in all_candidates if _names_match(c.name, default_name)]
        matching.sort(key=lambda c: host_rank.get(c.host_api.casefold(), 9))
        for candidate in matching:
            add(candidate, "recording-default-alternate-host")

    host_defaults: list[InputDeviceCandidate] = []
    for api in host_apis:
        if not isinstance(api, dict):
            continue
        try:
            index = int(api.get("default_input_device", -1))
        except (TypeError, ValueError):
            continue
        if index in by_index:
            host_defaults.append(by_index[index])
    host_defaults.sort(key=lambda c: host_rank.get(c.host_api.casefold(), 9))
    for candidate in host_defaults:
        add(candidate, "host-default")

    # Mapper/Primary devices follow Windows' active defaults and are preferable
    # to choosing an arbitrary Loopback or digital input.
    mapper_terms = ("sound mapper - input", "primary sound capture", "первичный драйвер записи")
    for candidate in all_candidates:
        if any(term in candidate.name.casefold() for term in mapper_terms):
            add(candidate, "windows-mapper")

    def remaining_rank(candidate: InputDeviceCandidate) -> tuple[int, int, int]:
        name = candidate.name.casefold()
        penalty = 0
        if any(term in name for term in ("loop-back", "loopback", "stereo mix", "стерео микшер")):
            penalty += 50
        if any(term in name for term in ("spdif", "adat")):
            penalty += 30
        preferred = 0 if any(term in name for term in ("microphone", "микрофон", " mic", "analogue")) else 10
        return penalty + preferred, host_rank.get(candidate.host_api.casefold(), 9), candidate.index

    for candidate in sorted(all_candidates, key=remaining_rank):
        add(candidate)

    return tuple(
        InputDeviceCandidate(
            index=candidate.index,
            name=candidate.name,
            host_api=candidate.host_api,
            default_sample_rate=candidate.default_sample_rate,
            max_input_channels=candidate.max_input_channels,
            roles=tuple(dict.fromkeys(roles_by_index.get(candidate.index, ()))),
        )
        for candidate in ordered
    )


def candidate_sample_rates(candidate: InputDeviceCandidate, requested_rate: int) -> tuple[int, ...]:
    """Requested STT rate first, then the endpoint's native/default rate."""
    rates = [int(requested_rate)]
    native = int(candidate.default_sample_rate or 0)
    if native > 0 and native not in rates:
        rates.append(native)
    return tuple(rates)


def candidate_channel_counts(
    candidate: InputDeviceCandidate,
    requested_channels: int,
) -> tuple[int, ...]:
    """Use both sides of a stereo input pair, with mono as a driver fallback."""
    requested = max(1, int(requested_channels))
    maximum = max(1, int(candidate.max_input_channels or 1))
    if requested == 1 and maximum >= 2:
        return (2, 1)
    return (min(requested, maximum),)


def _input_devices(sd) -> tuple[str, ...]:
    devices = sd.query_devices()
    return tuple(
        _device_name(device, index)
        for index, device in enumerate(devices)
        if _input_channels(device) > 0
    )


def _default_input_name(sd) -> str:
    try:
        devices = sd.query_devices()
        index = _default_input_index(sd)
        if index < 0:
            return ""
        device = devices[index]
        if _input_channels(device) <= 0:
            return ""
        return _device_name(device, index)
    except Exception:
        return ""


def _probe_rate(
    sd,
    sample_rate: int,
    *,
    candidates: tuple[InputDeviceCandidate, ...],
    duration_seconds: float,
    channels: int,
    block_ms: int,
) -> MicRateCheck:
    failures: list[str] = []
    for candidate in candidates:
        for input_rate in candidate_sample_rates(candidate, sample_rate):
            for input_channels in candidate_channel_counts(candidate, channels):
                captured = 0
                channel_peaks = [0] * input_channels
                statuses: list[str] = []

                def _on_audio(indata, frames, time_info, status):  # noqa: ARG001
                    nonlocal captured
                    if status:
                        statuses.append(str(status))
                    chunk = bytes(indata)
                    captured += len(chunk)
                    try:
                        samples = memoryview(chunk).cast("h")
                        for channel in range(input_channels):
                            peak = max(
                                (abs(value) for value in samples[channel::input_channels]),
                                default=0,
                            )
                            channel_peaks[channel] = max(channel_peaks[channel], peak)
                    except Exception:
                        pass

                stream = None
                try:
                    blocksize = max(1, int(input_rate * max(5, block_ms) / 1000))
                    stream = sd.RawInputStream(
                        device=candidate.index,
                        samplerate=input_rate,
                        channels=input_channels,
                        dtype="int16",
                        blocksize=blocksize,
                        latency="low",
                        callback=_on_audio,
                    )
                    stream.start()
                    time.sleep(max(0.1, duration_seconds))
                    if captured > 0:
                        return MicRateCheck(
                            sample_rate=sample_rate,
                            ok=True,
                            bytes_captured=captured,
                            statuses=tuple(statuses[:3]),
                            device_index=candidate.index,
                            device_name=candidate.name,
                            host_api=candidate.host_api,
                            input_sample_rate=input_rate,
                            input_channels=input_channels,
                            channel_peaks=tuple(channel_peaks),
                        )
                    failures.append(
                        f"{candidate.label} @ {input_rate} Hz/{input_channels} ch: no PCM frames"
                    )
                except Exception as exc:
                    failures.append(
                        f"{candidate.label} @ {input_rate} Hz/{input_channels} ch: {exc}"
                    )
                finally:
                    if stream is not None:
                        try:
                            stream.stop()
                            stream.close()
                        except Exception:
                            pass

    detail = " | ".join(failures[:4]) or "no usable input devices"
    return MicRateCheck(sample_rate=sample_rate, ok=False, error=detail)


def check_microphone(
    *,
    sample_rates: Iterable[int] = (16000, 24000),
    duration_seconds: float = 0.35,
    channels: int = 1,
    block_ms: int = 20,
) -> MicCheckResult:
    try:
        import sounddevice as sd  # type: ignore
    except Exception as exc:
        return MicCheckResult(False, error=f"sounddevice_missing: {exc}")

    try:
        refresh_device_inventory(sd)
        candidates = input_device_candidates(sd)
        devices = tuple(candidate.name for candidate in candidates)
    except Exception as exc:
        return MicCheckResult(True, error=f"query_devices_failed: {exc}")

    if not devices or not candidates:
        return MicCheckResult(
            True,
            input_devices=(),
            default_input=_default_input_name(sd),
            error="no_input_devices",
        )

    checks = tuple(
        _probe_rate(
            sd,
            int(rate),
            candidates=candidates,
            duration_seconds=duration_seconds,
            channels=channels,
            block_ms=block_ms,
        )
        for rate in sample_rates
    )
    error = "" if any(check.ok for check in checks) else "input_stream_failed"
    selected = next((check.device_name for check in checks if check.ok and check.device_name), "")
    return MicCheckResult(
        True,
        input_devices=devices,
        default_input=selected or _default_input_name(sd),
        rate_checks=checks,
        error=error,
    )


def _main() -> int:
    result = check_microphone()
    print(f"sounddevice: {'OK' if result.sounddevice_available else 'missing'}")
    print(f"input devices: {result.device_count}")
    if result.default_input:
        print(f"selected input: {result.default_input}")
    for name in result.input_devices:
        print(f"- {name}")
    for check in result.rate_checks:
        status = "OK" if check.ok else "failed"
        device = f", {check.host_api} / {check.device_name}" if check.device_name else ""
        conversion = (
            f", capture {check.input_sample_rate} -> STT {check.sample_rate} Hz"
            if check.input_sample_rate and check.input_sample_rate != check.sample_rate
            else ""
        )
        channels = (
            f", {check.input_channels} ch peaks={check.channel_peaks}"
            if check.input_channels > 1 else ""
        )
        suffix = f" ({check.error})" if check.error else ""
        print(
            f"{check.sample_rate} Hz: {status}, {check.bytes_captured} bytes"
            f"{device}{conversion}{channels}{suffix}"
        )
    if result.error:
        print(f"error: {result.error}")
    return 0 if result.ready else 2


if __name__ == "__main__":
    raise SystemExit(_main())
