"""Product edition and engine capability helpers.

The codebase is shared by the Live and Studio folders. The folder/config edition
selects which compute modes are visible in the UI; installed packs decide whether
those modes can actually run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .model_assets import whispercpp_model_asset, whispercpp_model_path
from .paths import ProjectPaths

EDITION_LIVE = "live"
EDITION_STUDIO = "studio"

MODE_API = "api"
MODE_VULKAN = "vulkan"
MODE_CUDA = "cuda"

LEGACY_MODE_CPU = "cpu"
LEGACY_MODE_GPU = "gpu"

_STUDIO_HINTS = ("studio", "pro", "plus", "cuda")
# Resident HTTP server (warm live path) vs one-shot CLI (batch path). They are
# searched separately: the batch provider needs a CLI binary, the warm server
# needs a server binary. CLI is listed first in the combined tuple so the
# generic "is a binary present" lookup prefers the one-shot tool.
_WHISPERCPP_SERVER_BINARIES = (
    "whisper-server.exe",
    "server.exe",
    "whisper-server",
    "server",
)
_WHISPERCPP_CLI_BINARIES = (
    "whisper-cli.exe",
    "main.exe",
    "whisper.exe",
    "whisper-cli",
    "main",
    "whisper",
)
_WHISPERCPP_BINARIES = _WHISPERCPP_CLI_BINARIES + _WHISPERCPP_SERVER_BINARIES

WHISPERCPP_MODEL_FILES = {
    "small": "ggml-small.bin",
    "turbo": "ggml-large-v3-turbo.bin",
    "large-v3-turbo": "ggml-large-v3-turbo.bin",
    "large-v2": "ggml-large-v2.bin",
}


def _setting(settings: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
    node: Any = settings or {}
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def current_edition(paths: ProjectPaths, settings: dict[str, Any] | None = None) -> str:
    value = str(_setting(settings, "edition", default="") or "").strip().lower()
    if value in {EDITION_LIVE, EDITION_STUDIO}:
        return value
    if value in {"pro", "plus", "cuda"}:
        return EDITION_STUDIO

    root_name = paths.root.name.lower()
    if any(hint in root_name for hint in _STUDIO_HINTS):
        return EDITION_STUDIO
    return EDITION_LIVE


def visible_compute_modes(paths: ProjectPaths, settings: dict[str, Any] | None = None) -> list[str]:
    if current_edition(paths, settings) == EDITION_STUDIO:
        return [MODE_API, MODE_VULKAN, MODE_CUDA]
    return [MODE_API, MODE_VULKAN]


def display_compute_mode(value: Any) -> str:
    mode = str(value or MODE_API).strip().lower()
    if mode in {"openai", "cloud"}:
        return MODE_API
    if mode in {LEGACY_MODE_CPU, "local", "whispercpp"}:
        return MODE_VULKAN
    if mode in {LEGACY_MODE_GPU, "nvidia"}:
        return MODE_CUDA
    if mode in {MODE_API, MODE_VULKAN, MODE_CUDA}:
        return mode
    return MODE_API


def compute_mode_label_key(value: Any) -> str:
    return f"mode_{display_compute_mode(value)}"


def whispercpp_root(paths: ProjectPaths) -> Path:
    return paths.tools / "whispercpp"


def find_whispercpp_binary(paths: ProjectPaths) -> Path | None:
    root = whispercpp_root(paths)
    if not root.exists():
        return None
    for name in _WHISPERCPP_BINARIES:
        direct = root / name
        if direct.exists():
            return direct
    for path in root.rglob("*"):
        if path.is_file() and path.name.lower() in _WHISPERCPP_BINARIES:
            return path
    return None


def find_whispercpp_cli(paths: ProjectPaths) -> Path | None:
    root = whispercpp_root(paths)
    if not root.exists():
        return None
    for name in _WHISPERCPP_CLI_BINARIES:
        direct = root / name
        if direct.exists():
            return direct
    cli_names = {name.lower() for name in _WHISPERCPP_CLI_BINARIES}
    for path in root.rglob("*"):
        if path.is_file() and path.name.lower() in cli_names:
            return path
    return None


def find_whispercpp_server(paths: ProjectPaths) -> Path | None:
    """Locate a resident whisper.cpp *server* binary (warm live path).

    Returns None when the installed pack ships only the one-shot CLI; callers
    then fall back to the batch CLI provider."""
    root = whispercpp_root(paths)
    if not root.exists():
        return None
    for name in _WHISPERCPP_SERVER_BINARIES:
        direct = root / name
        if direct.exists():
            return direct
    server_names = {name.lower() for name in _WHISPERCPP_SERVER_BINARIES}
    for path in root.rglob("*"):
        if path.is_file() and path.name.lower() in server_names:
            return path
    return None


def whispercpp_pack_kind(paths: ProjectPaths) -> str:
    root = whispercpp_root(paths)
    if not root.exists():
        return ""
    marker = root / "audion-whispercpp-pack.txt"
    if marker.exists():
        try:
            for line in marker.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().lower().startswith("kind="):
                    kind = line.split("=", 1)[1].strip().lower()
                    if kind in {"cuda", "cublas"}:
                        return "cublas"
                    if kind in {"cpu", "manual"}:
                        return kind
                    if kind == "vulkan":
                        return "manual"
        except Exception:
            pass

    names = [path.name.lower() for path in root.rglob("*") if path.is_file()]
    if any(("cublas" in name) or ("cuda" in name) for name in names):
        return "cublas"
    if any("vulkan" in name for name in names):
        return "manual"
    return "cpu" if find_whispercpp_binary(paths) is not None else ""


def whispercpp_cublas_backend_present(paths: ProjectPaths) -> bool:
    return whispercpp_pack_kind(paths) == "cublas"


def whispercpp_vulkan_backend_present(paths: ProjectPaths) -> bool:
    return whispercpp_pack_kind(paths) == "vulkan"


def whispercpp_model_file(model: str | None = None) -> str:
    key = str(model or "turbo").strip().lower()
    asset = whispercpp_model_asset(key)
    if asset is not None:
        return asset.filename
    return WHISPERCPP_MODEL_FILES.get(key, key)


def resolve_whispercpp_model(paths: ProjectPaths, model: str | None = None) -> Path | None:
    model_value = str(model or "turbo").strip()
    if model_value:
        candidate = Path(model_value)
        if candidate.exists():
            return candidate
        rel = paths.models / model_value
        if rel.exists():
            return rel

    mapped = whispercpp_model_path(paths, model_value or "turbo")
    return mapped if mapped.exists() else None


def vulkan_pack_installed(paths: ProjectPaths, *, model: str | None = "turbo") -> bool:
    return find_whispercpp_binary(paths) is not None and resolve_whispercpp_model(paths, model) is not None


def whispercpp_runtime_ready(
    paths: ProjectPaths,
    *,
    model: str | None = "turbo",
    backend: str = "auto",
    require_server: bool = False,
) -> bool:
    """Strict readiness used by GUI buttons, including pack payloads/manifest."""
    root = whispercpp_root(paths)
    marker = root / "audion-whispercpp-pack.txt"
    common_payloads = ("ggml.dll", "ggml-base.dll", "whisper.dll")
    if not marker.exists() or find_whispercpp_cli(paths) is None:
        return False
    if require_server and find_whispercpp_server(paths) is None:
        return False
    if resolve_whispercpp_model(paths, model) is None:
        return False
    if not all((root / name).exists() for name in common_payloads):
        return False

    requested = str(backend or "auto").strip().lower()
    kind = whispercpp_pack_kind(paths)
    if requested in {"cuda", "cublas"}:
        return kind == "cublas" and all(
            (root / name).exists()
            for name in ("ggml-cuda.dll", "cudart64_12.dll", "cublas64_12.dll")
        )
    if requested == "vulkan":
        return whispercpp_vulkan_backend_present(paths) and (root / "ggml-vulkan.dll").exists()
    if kind == "cublas":
        return (root / "ggml-cuda.dll").exists()
    return any(root.glob("ggml-cpu-*.dll"))


def whispercpp_cuda_ready(
    paths: ProjectPaths,
    *,
    model: str | None = "large-v2",
    require_server: bool = True,
) -> bool:
    return whispercpp_runtime_ready(
        paths, model=model, backend="cublas", require_server=require_server
    )
