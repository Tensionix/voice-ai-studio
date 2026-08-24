"""Health-check entry point (mirrors the Audion family `main.py`)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR.parent))

from system_core.core.paths import ensure_project_dirs, get_project_paths  # noqa: E402


def detect_python_mode(root: Path) -> str:
    if (root / "runtime" / "python.exe").exists():
        return "portable-runtime"
    return "system-python"


def main() -> int:
    paths = get_project_paths()
    ensure_project_dirs(paths)

    ffmpeg_ok = (paths.tools / "ffmpeg" / "bin").exists()
    payload = {
        "project": "Audion Voice AI",
        "project_root": str(paths.root),
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "python_mode": detect_python_mode(paths.root),
        "folders": {"input": str(paths.input), "output": str(paths.output)},
        "openai_key": (paths.config / "api_key_openai.txt").exists(),
        "ffmpeg_portable": ffmpeg_ok,
        "message": "Audion Voice AI workspace ready (API mode).",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
