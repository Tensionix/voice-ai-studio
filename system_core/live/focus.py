"""Deliver dictated text into whatever window currently has focus (Windows).

Our tray + (future) overlay are designed not to steal focus, so the user's target
app stays foreground and we can paste straight into it. Two methods:
  - "clipboard" (default): put text on the clipboard, send Ctrl+V. Fast, robust,
    handles any unicode. Clobbers the clipboard (acceptable for a dictation tool).
  - "type": synthesize Unicode key events (SendInput). No clipboard touch, but
    slower and some apps swallow rapid synthetic input.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_INPUT_KEYBOARD = 1
_VK_CONTROL = 0x11
_VK_V = 0x56
# Modifiers we force-release before pasting (defensive): L/R Win, L/R Alt,
# L/R Ctrl, L/R Shift. Sending a key-up for a key that isn't down is a harmless
# no-op, but it guarantees a clean state so Ctrl+V can't become Win+Ctrl+V etc.
_VK_MODIFIERS = (0x5B, 0x5C, 0xA4, 0xA5, 0x12, 0xA2, 0xA3, 0x11, 0xA0, 0xA1, 0x10)


def set_clipboard_text(text: str) -> None:
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.clipboard().setText(text)
    except Exception:
        pass


def get_foreground_window():
    """HWND of the window the user is typing into (captured when dictation starts,
    so we can restore it before pasting). None off Windows / on failure."""
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        return user32.GetForegroundWindow()
    except Exception:
        return None


def get_window_language(hwnd) -> str:
    """Map the target window's current Windows keyboard layout to STT language."""
    if sys.platform != "win32" or not hwnd:
        return "auto"
    try:
        user32 = ctypes.windll.user32
        user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.c_void_p)
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        thread_id = user32.GetWindowThreadProcessId(hwnd, None)
        user32.GetKeyboardLayout.argtypes = (wintypes.DWORD,)
        user32.GetKeyboardLayout.restype = ctypes.c_void_p
        lang_id = int(user32.GetKeyboardLayout(thread_id) or 0) & 0xFFFF
        primary_language = lang_id & 0x03FF
        if primary_language == 0x19:  # LANG_RUSSIAN
            return "ru"
        if primary_language == 0x09:  # LANG_ENGLISH (all regional variants)
            return "en"
    except Exception:
        pass
    return "auto"


def _restore_foreground(hwnd) -> None:
    if not hwnd:
        return
    try:
        user32 = ctypes.windll.user32
        if user32.GetForegroundWindow() == hwnd:
            return  # target is already focused (our tray/overlay never stole it)
        user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def paste_text(text: str, method: str = "clipboard", hwnd=None) -> None:
    if not text:
        return
    if sys.platform != "win32":
        set_clipboard_text(text)  # best effort off Windows
        return
    _restore_foreground(hwnd)
    _release_modifiers()  # ensure no leaked Alt/Win/Ctrl/Shift corrupts the paste
    if method == "type":
        _send_unicode(text)
    else:
        set_clipboard_text(text)
        _send_ctrl_v()


# --- Windows SendInput plumbing ----------------------------------------------
# dwExtraInfo is ULONG_PTR (pointer-sized), not a 32-bit ULONG.
_ULONG_PTR = ctypes.c_size_t


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        # The union MUST carry the largest member (MOUSEINPUT) so sizeof(_INPUT)
        # equals the real Win32 INPUT (40 bytes on 64-bit). SendInput rejects any
        # struct whose cbSize != sizeof(INPUT) and inserts *nothing* — a too-small
        # keyboard-only union is why Ctrl+V silently never reached other apps.
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _key_input(vk: int, scan: int, flags: int) -> _INPUT:
    inp = _INPUT()
    inp.type = _INPUT_KEYBOARD
    inp.ki = _KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return inp


def _send(inputs: list[_INPUT]) -> int:
    if not inputs:
        return 0
    user32 = ctypes.windll.user32
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.c_void_p, ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    arr = (_INPUT * len(inputs))(*inputs)
    return user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))


def _release_modifiers() -> None:
    """Send key-up for every modifier so a leaked/held one can't poison the next
    keystroke (e.g. a stuck Win turning Ctrl+V into Win+Ctrl+V)."""
    _send([_key_input(vk, 0, _KEYEVENTF_KEYUP) for vk in _VK_MODIFIERS])


def _send_ctrl_v() -> None:
    _send([
        _key_input(_VK_CONTROL, 0, 0),
        _key_input(_VK_V, 0, 0),
        _key_input(_VK_V, 0, _KEYEVENTF_KEYUP),
        _key_input(_VK_CONTROL, 0, _KEYEVENTF_KEYUP),
    ])


def _send_unicode(text: str) -> None:
    inputs: list[_INPUT] = []
    for ch in text:
        code = ord(ch)
        inputs.append(_key_input(0, code, _KEYEVENTF_UNICODE))
        inputs.append(_key_input(0, code, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP))
    _send(inputs)
