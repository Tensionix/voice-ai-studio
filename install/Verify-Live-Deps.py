"""Standalone verification used by Install-Live-Deps.cmd."""

from __future__ import annotations

import importlib.metadata


def main() -> int:
    try:
        import sounddevice as sd
        import websockets  # noqa: F401

        devices = tuple(sd.query_devices())
        host_apis = tuple(sd.query_hostapis())
    except Exception as exc:
        print(f"[ERROR] Live dependency verification failed: {exc.__class__.__name__}: {exc}")
        return 1

    input_count = sum(
        1 for device in devices
        if isinstance(device, dict) and int(device.get("max_input_channels") or 0) > 0
    )
    print(f"sounddevice {importlib.metadata.version('sounddevice')}: OK")
    print(f"websockets {importlib.metadata.version('websockets')}: OK")
    print(f"PortAudio host APIs: {len(host_apis)}; input endpoints: {input_count}")
    if input_count == 0:
        print("[WARN] Dependencies are installed, but Windows currently reports no input endpoints.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
