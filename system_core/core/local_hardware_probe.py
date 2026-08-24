"""Child-process entry point for isolated local hardware detection."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from system_core.core.local_hardware import detect_local_hardware  # noqa: E402
from system_core.core.paths import get_project_paths  # noqa: E402


def _write(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=True), flush=True)


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else PROJECT_ROOT

    def progress(step: int, total: int, stage_key: str) -> None:
        _write(
            {
                "type": "progress",
                "step": int(step),
                "total": int(total),
                "stage": str(stage_key),
            }
        )

    try:
        profile = detect_local_hardware(get_project_paths(root), progress=progress)
    except Exception as exc:
        _write({"type": "error", "message": f"{exc.__class__.__name__}: {exc}"})
        return 1
    _write({"type": "profile", "profile": asdict(profile)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
