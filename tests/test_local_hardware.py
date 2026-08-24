from system_core.core import local_hardware as hw
from system_core.core.paths import get_project_paths


def test_local_hardware_detection_reports_progress(tmp_path, monkeypatch):
    paths = get_project_paths(tmp_path)
    events: list[tuple[int, int, str]] = []

    monkeypatch.setattr(hw.shutil, "which", lambda _name: None)
    monkeypatch.setattr(hw.platform, "system", lambda: "Linux")
    monkeypatch.setattr(hw, "_has", lambda _module: False)
    monkeypatch.setattr(hw, "_onnx_providers", lambda: ())
    monkeypatch.setattr(hw, "vulkan_pack_installed", lambda _paths: False)
    monkeypatch.setattr(hw, "whispercpp_vulkan_backend_present", lambda _paths: False)

    profile = hw.detect_local_hardware(paths, progress=lambda step, total, key: events.append((step, total, key)))

    assert profile.recommended_stack == "cpu"
    assert events == [
        (1, 8, "local_hw_stage_start"),
        (2, 8, "local_hw_stage_nvidia"),
        (3, 8, "local_hw_stage_windows_gpu"),
        (4, 8, "local_hw_stage_onnx_runtime"),
        (5, 8, "local_hw_stage_onnx_providers"),
        (6, 8, "local_hw_stage_gigaam_runtime"),
        (7, 8, "local_hw_stage_whispercpp"),
        (8, 8, "local_hw_stage_recommendation"),
    ]


def test_local_hardware_prefers_directml_for_windows_gpu_onnx():
    profile = hw.LocalHardwareProfile(
        gpu_names=("Intel Iris Xe Graphics",),
        has_nvidia=False,
        has_intel=True,
        has_amd=False,
        has_cuda_runtime=False,
        has_onnxruntime=True,
        onnx_providers=("DmlExecutionProvider", "CPUExecutionProvider"),
        has_gigaam_runtime=True,
        has_whispercpp=False,
        has_whispercpp_vulkan_backend=False,
    )

    assert profile.recommended_stack == "onnx_directml"


def test_local_hardware_prefers_cuda_for_nvidia_onnx():
    profile = hw.LocalHardwareProfile(
        gpu_names=("NVIDIA GeForce RTX 5070",),
        has_nvidia=True,
        has_intel=False,
        has_amd=False,
        has_cuda_runtime=True,
        has_onnxruntime=True,
        onnx_providers=("CUDAExecutionProvider", "CPUExecutionProvider"),
        has_gigaam_runtime=True,
        has_whispercpp=True,
        has_whispercpp_vulkan_backend=False,
    )

    assert profile.recommended_stack == "onnx_cuda"


def test_local_hardware_distinguishes_whispercpp_cpu_fallback_from_vulkan():
    cpu_pack = hw.LocalHardwareProfile(
        gpu_names=("Intel Iris Xe Graphics",),
        has_nvidia=False,
        has_intel=True,
        has_amd=False,
        has_cuda_runtime=False,
        has_onnxruntime=False,
        onnx_providers=(),
        has_gigaam_runtime=False,
        has_whispercpp=True,
        has_whispercpp_vulkan_backend=False,
    )
    vulkan_pack = hw.LocalHardwareProfile(
        gpu_names=("Intel Iris Xe Graphics",),
        has_nvidia=False,
        has_intel=True,
        has_amd=False,
        has_cuda_runtime=False,
        has_onnxruntime=False,
        onnx_providers=(),
        has_gigaam_runtime=False,
        has_whispercpp=True,
        has_whispercpp_vulkan_backend=True,
    )

    assert cpu_pack.recommended_stack == "whispercpp_cpu"
    assert vulkan_pack.recommended_stack == "whispercpp_vulkan"
