"""Lightweight local hardware detection for choosing local STT defaults."""

from __future__ import annotations

import importlib.util
import platform
import shutil
import subprocess
import ctypes
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

from .editions import (
    EDITION_STUDIO,
    MODE_API,
    MODE_CUDA,
    MODE_VULKAN,
    current_edition,
    vulkan_pack_installed,
    whispercpp_cublas_backend_present,
    whispercpp_vulkan_backend_present,
)
from .paths import ProjectPaths, get_project_paths

LocalHardwareProgress = Callable[[int, int, str], None]
_DETECTION_STAGES = (
    "local_hw_stage_start",
    "local_hw_stage_nvidia",
    "local_hw_stage_windows_gpu",
    "local_hw_stage_onnx_runtime",
    "local_hw_stage_onnx_providers",
    "local_hw_stage_gigaam_runtime",
    "local_hw_stage_whispercpp",
    "local_hw_stage_recommendation",
)


@dataclass(frozen=True)
class LocalHardwareProfile:
    gpu_names: tuple[str, ...]
    has_nvidia: bool
    has_intel: bool
    has_amd: bool
    has_cuda_runtime: bool
    has_onnxruntime: bool
    onnx_providers: tuple[str, ...]
    has_gigaam_runtime: bool
    has_whispercpp: bool
    has_whispercpp_vulkan_backend: bool
    has_whispercpp_cublas_backend: bool = False

    @property
    def recommended_stack(self) -> str:
        providers = set(self.onnx_providers)
        if self.has_nvidia and "CUDAExecutionProvider" in providers:
            return "onnx_cuda"
        if (self.has_amd or self.has_intel or self.has_nvidia) and "DmlExecutionProvider" in providers:
            return "onnx_directml"
        if self.has_onnxruntime and "CPUExecutionProvider" in providers:
            return "onnx_cpu"
        if self.has_nvidia and self.has_whispercpp_cublas_backend:
            return "whispercpp_cublas"
        if self.has_whispercpp_vulkan_backend:
            return "whispercpp_vulkan"
        if self.has_whispercpp:
            return "whispercpp_cpu"
        return "cpu"


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _run(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=2.5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return ""
    return "\n".join(part for part in (result.stdout, result.stderr) if part).strip()


def _emit(progress: LocalHardwareProgress | None, step: int, key: str) -> None:
    if progress is not None:
        progress(step, len(_DETECTION_STAGES), key)


def _video_controller_names(progress: LocalHardwareProgress | None = None) -> tuple[str, ...]:
    names: list[str] = []
    nvidia_smi = shutil.which("nvidia-smi")
    _emit(progress, 2, "local_hw_stage_nvidia")
    if nvidia_smi:
        out = _run([nvidia_smi, "-L"])
        for line in out.splitlines():
            if line.strip():
                names.append(line.strip())

    _emit(progress, 3, "local_hw_stage_windows_gpu")
    if platform.system().lower() == "windows":
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if ps:
            out = _run([
                ps,
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
            ])
            for line in out.splitlines():
                line = line.strip()
                if line and line not in names:
                    names.append(line)
    return tuple(names)


def _onnx_providers() -> tuple[str, ...]:
    try:
        import onnxruntime as ort  # type: ignore

        providers = []
        for item in ort.get_available_providers():
            provider = str(item)
            if provider in {"TensorrtExecutionProvider", "NvTensorRTRTXExecutionProvider", "NvTensorRtRtxExecutionProvider"}:
                continue
            if _onnx_provider_loadable(ort, provider):
                providers.append(provider)
        return tuple(providers)
    except Exception:
        return ()


def _preload_cuda_dlls(ort) -> None:
    preload = getattr(ort, "preload_dlls", None)
    if not callable(preload):
        return
    try:
        preload(cuda=True, cudnn=True, msvc=True)
    except Exception:
        pass


def _onnx_provider_loadable(ort, provider: str) -> bool:
    if platform.system().lower() != "windows":
        return True
    dll_names = {
        "CUDAExecutionProvider": "onnxruntime_providers_cuda.dll",
    }
    dll_name = dll_names.get(provider)
    if not dll_name:
        return True
    if provider == "CUDAExecutionProvider":
        _preload_cuda_dlls(ort)
    try:
        capi_dir = Path(ort.__file__).resolve().parent / "capi"
        ctypes.WinDLL(str(capi_dir / dll_name))
        return True
    except Exception:
        return False


def _build_profile(root: str, progress: LocalHardwareProgress | None = None) -> LocalHardwareProfile:
    _emit(progress, 1, "local_hw_stage_start")
    paths = get_project_paths(Path(root))
    names = _video_controller_names(progress)
    text = " ".join(names).lower()
    _emit(progress, 4, "local_hw_stage_onnx_runtime")
    has_onnxruntime = _has("onnxruntime")
    _emit(progress, 5, "local_hw_stage_onnx_providers")
    onnx_providers = _onnx_providers()
    _emit(progress, 6, "local_hw_stage_gigaam_runtime")
    has_gigaam_runtime = _has("onnx_asr") or _has("gigaam")
    _emit(progress, 7, "local_hw_stage_whispercpp")
    has_cuda_runtime = _has("torch") or whispercpp_cublas_backend_present(paths)
    has_whispercpp = vulkan_pack_installed(paths)
    has_whispercpp_vulkan_backend = whispercpp_vulkan_backend_present(paths)
    has_whispercpp_cublas_backend = whispercpp_cublas_backend_present(paths)
    _emit(progress, 8, "local_hw_stage_recommendation")
    return LocalHardwareProfile(
        gpu_names=names,
        has_nvidia=("nvidia" in text) or shutil.which("nvidia-smi") is not None,
        has_intel=("intel" in text) or ("iris" in text) or ("arc" in text),
        has_amd=("amd" in text) or ("radeon" in text),
        has_cuda_runtime=has_cuda_runtime,
        has_onnxruntime=has_onnxruntime,
        onnx_providers=onnx_providers,
        has_gigaam_runtime=has_gigaam_runtime,
        has_whispercpp=has_whispercpp,
        has_whispercpp_vulkan_backend=has_whispercpp_vulkan_backend,
        has_whispercpp_cublas_backend=has_whispercpp_cublas_backend,
    )


@lru_cache(maxsize=16)
def _cached_profile(root: str) -> LocalHardwareProfile:
    return _build_profile(root)


def detect_local_hardware(
    paths: ProjectPaths,
    progress: LocalHardwareProgress | None = None,
) -> LocalHardwareProfile:
    if progress is None:
        return _cached_profile(str(paths.root))
    return _build_profile(str(paths.root), progress)


def recommended_compute_mode(paths: ProjectPaths, settings: dict | None = None) -> str:
    """Pick a conservative file-engine default for `compute_mode: auto`."""
    profile = detect_local_hardware(paths)
    if profile.has_gigaam_runtime and profile.has_onnxruntime:
        return MODE_VULKAN
    if current_edition(paths, settings) == EDITION_STUDIO and profile.has_nvidia and profile.has_cuda_runtime:
        return MODE_CUDA
    if profile.has_whispercpp:
        return MODE_VULKAN
    return MODE_API


def recommended_gigaam_backend(paths: ProjectPaths) -> str:
    profile = detect_local_hardware(paths)
    stack = profile.recommended_stack
    if stack == "onnx_cuda":
        return "cuda"
    if stack == "onnx_directml":
        return "directml"
    return "cpu"


def recommended_whispercpp_backend(paths: ProjectPaths) -> str:
    profile = detect_local_hardware(paths)
    if profile.has_whispercpp_cublas_backend and profile.has_nvidia:
        return "cublas"
    return "cpu"
