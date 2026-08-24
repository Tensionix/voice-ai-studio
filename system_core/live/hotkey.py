"""Global push-to-talk hotkey via a low-level keyboard hook (Windows).

`RegisterHotKey` only reports key-down; push-to-talk needs key-up too, so we use
a `WH_KEYBOARD_LL` hook (in-process, removed on exit). The hook fires
`on_press` when the full chord (default Right Alt+F12) becomes held and `on_release`
when it breaks.

**Observe-only — we never suppress a key.** The hook *watches* for the chord but
passes every key event straight through to Windows (`CallNextHookEx`). Alt and
Win keep behaving exactly as the user expects; we only listen. This is the whole
point of the redesign: an earlier version *swallowed* the chord keys to hide the
Start menu, but swallowing a key-up while its key-down had already leaked left the
OS thinking Win was held forever (every keystroke became Win+key). Not touching
the events at all makes a stuck modifier impossible.

The hook callback runs on the thread that installed it (the Qt GUI thread, whose
event loop pumps Windows messages). To stay well under the low-level-hook timeout
(~300 ms, past which Windows silently drops the hook), `on_press`/`on_release`
should do almost nothing — the controller wires them to *queued* signals so the
real work (mic start / transcribe) runs on the next event-loop turn, not here.

ctypes note: every WinAPI used here gets explicit argtypes/restype. On 64-bit
Python the defaults truncate handles/pointers to 32 bits, which makes
`GetModuleHandleW`/`SetWindowsHookExW` silently return a bad value — that's why
the hook "fails to install" without these declarations.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Callable, Optional

# --- virtual-key groups ------------------------------------------------------
# Left vs right Alt are kept separate so a second chord (right Alt + Win) can act
# as a distinct hands-free toggle, while plain (left) Alt + Win stays push-to-talk.
_GROUP_VKS: dict[str, set[int]] = {
    "win": {0x5B, 0x5C},               # L/R Win
    "alt": {0xA4, 0x12},               # Left Menu (+ generic)
    "ralt": {0xA5},                    # Right Menu (AltGr) — hands-free toggle
    "ctrl": {0xA2, 0xA3, 0x11},        # L/R Control + generic
    "shift": {0xA0, 0xA1, 0x10},       # L/R Shift + generic
}
_GROUP_VKS.update({f"f{number}": {0x6F + number} for number in range(1, 25)})

_WM_KEYDOWN, _WM_KEYUP, _WM_SYSKEYDOWN, _WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
_WH_KEYBOARD_LL = 13
_HC_ACTION = 0
_LLKHF_INJECTED = 0x10

# LRESULT / HHOOK are pointer-sized; declare them so ctypes doesn't truncate.
_LRESULT = ctypes.c_ssize_t
_HHOOK = ctypes.c_void_p
_ULONG_PTR = ctypes.c_size_t

# LRESULT CALLBACK proc(int nCode, WPARAM wParam, LPARAM lParam)
_HOOKPROC = ctypes.CFUNCTYPE(_LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

_win32 = None  # cached (user32, kernel32) with signatures applied


def _load_win32():
    """Load user32/kernel32 once with correct argtypes/restype (Windows only)."""
    global _win32
    if _win32 is not None:
        return _win32
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.SetWindowsHookExW.argtypes = (
        ctypes.c_int, _HOOKPROC, wintypes.HMODULE, wintypes.DWORD,
    )
    user32.SetWindowsHookExW.restype = _HHOOK

    user32.CallNextHookEx.argtypes = (
        _HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
    )
    user32.CallNextHookEx.restype = _LRESULT

    user32.UnhookWindowsHookEx.argtypes = (_HHOOK,)
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL

    kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    _win32 = (user32, kernel32)
    return _win32


def parse_hotkey(spec: str) -> set[str]:
    """Parse a chord specification. Unknown tokens are dropped; an invalid or
    empty primary chord falls back to the safe Right Alt+F12 default."""
    groups = {tok.strip().lower() for tok in (spec or "").split("+") if tok.strip()}
    groups = {g for g in groups if g in _GROUP_VKS}
    return groups or {"ralt", "f12"}


def parse_chord(spec: str) -> set[str]:
    """Like parse_hotkey but with no fallback — empty/unknown -> empty set
    (used for the optional toggle chord, which may be absent)."""
    groups = {tok.strip().lower() for tok in (spec or "").split("+") if tok.strip()}
    return {g for g in groups if g in _GROUP_VKS}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(_ULONG_PTR)),
    ]


class HotkeyListener:
    """A push-to-talk chord listener. Windows-only; `install()` raises elsewhere."""

    def __init__(
        self,
        spec: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        *,
        toggle_spec: str = "",
        on_toggle: Optional[Callable[[], None]] = None,
    ):
        self._required = parse_hotkey(spec)
        self._toggle_required = parse_chord(toggle_spec)
        self._on_press = on_press
        self._on_release = on_release
        self._on_toggle = on_toggle
        self._down: set[str] = set()
        self._active = False
        self._toggle_active = False
        self._hook = None
        self._proc = None  # keep a reference so the CFUNCTYPE isn't GC'd
        self._user32 = None

    # --- group lookup --------------------------------------------------------
    @staticmethod
    def _group_of(vk: int) -> Optional[str]:
        for group, vks in _GROUP_VKS.items():
            if vk in vks:
                return group
        return None

    # --- core decision (pure; always returns False — never suppress) ---------
    def _handle(self, group: Optional[str], is_down: bool, is_up: bool) -> bool:
        # Observe only. We track held chord keys to fire on_press/on_release for
        # the push-to-talk chord and on_toggle (once per activation) for the
        # optional toggle chord. We NEVER consume the event — always return False
        # so Windows sees every key normally (no swallowed key-up -> no stuck mod).
        # The two chords use disjoint groups (alt vs ralt), so only one matches.
        if group is None:
            return False

        if is_down:
            self._down.add(group)
            if (
                self._toggle_required
                and not self._toggle_active
                and self._toggle_required <= self._down
            ):
                self._toggle_active = True
                self._safe(self._on_toggle)
            if not self._active and self._required <= self._down:
                self._active = True
                self._safe(self._on_press)
        elif is_up:
            was_active = self._active
            self._down.discard(group)
            if self._toggle_active and not (self._toggle_required <= self._down):
                self._toggle_active = False
            if was_active and not (self._required <= self._down):
                self._active = False
                self._safe(self._on_release)

        return False  # always pass the key through to the OS

    @staticmethod
    def _safe(fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:
            pass

    # --- hook proc -----------------------------------------------------------
    def _low_level_proc(self, n_code, w_param, l_param):
        if n_code == _HC_ACTION:
            info = KBDLLHOOKSTRUCT.from_address(l_param)
            if not (info.flags & _LLKHF_INJECTED):  # ignore our own synthetic keys
                group = self._group_of(info.vkCode)
                is_down = w_param in (_WM_KEYDOWN, _WM_SYSKEYDOWN)
                is_up = w_param in (_WM_KEYUP, _WM_SYSKEYUP)
                if self._handle(group, is_down, is_up):
                    return 1  # suppress
        return self._user32.CallNextHookEx(None, n_code, w_param, l_param)

    # --- lifecycle -----------------------------------------------------------
    def install(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("global hotkey is only supported on Windows")
        if self._hook is not None:
            return
        user32, kernel32 = _load_win32()
        self._user32 = user32
        self._proc = _HOOKPROC(self._low_level_proc)
        h_module = kernel32.GetModuleHandleW(None)
        self._hook = user32.SetWindowsHookExW(_WH_KEYBOARD_LL, self._proc, h_module, 0)
        if not self._hook:
            err = ctypes.get_last_error()
            self._proc = None
            detail = ctypes.WinError(err).strerror if err else "unknown error"
            raise RuntimeError(f"SetWindowsHookExW failed (err {err}: {detail})")

    def close(self) -> None:
        if self._hook is not None and self._user32 is not None:
            try:
                self._user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
        self._hook = None
        self._proc = None
        self._user32 = None
        self._down.clear()
        self._active = False
        self._toggle_active = False
