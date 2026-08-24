"""Live Dictation Mode (iteration 3).

Tray-summoned, push-to-talk dictation that streams mic audio to a realtime STT
provider and pastes the result into the focused window. This package holds the
UI-adjacent pieces (tray, overlay, controller) and the OS glue (hotkey, focus);
the streaming STT itself plugs in behind `providers.base.LiveTranscriber`.

Landed: `config` (LiveConfig), `controller` (state machine + full lifecycle),
`tray` (QSystemTrayIcon), `hotkey` (WH_KEYBOARD_LL push-to-talk), `audio_capture`
(sounddevice), `transcriber` (BatchLiveTranscriber behind the LiveTranscriber
seam), `focus` (clipboard/Unicode auto-paste). Coming next: streaming Realtime-WS
transcriber (live partials) and an optional frameless overlay.
"""

from __future__ import annotations

from .config import LiveConfig
from .controller import LiveController, LiveState

__all__ = ["LiveConfig", "LiveController", "LiveState"]
