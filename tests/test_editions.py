from __future__ import annotations

from system_core.core.editions import (
    MODE_API,
    MODE_CUDA,
    MODE_VULKAN,
    current_edition,
    display_compute_mode,
    visible_compute_modes,
)
from system_core.core.paths import get_project_paths


def test_live_folder_exposes_openai_and_vulkan_modes():
    paths = get_project_paths()
    assert current_edition(paths, {"edition": "live"}) == "live"
    assert visible_compute_modes(paths, {"edition": "live"}) == [MODE_API, MODE_VULKAN]


def test_studio_exposes_cuda_mode():
    paths = get_project_paths()
    assert visible_compute_modes(paths, {"edition": "studio"}) == [MODE_API, MODE_VULKAN, MODE_CUDA]


def test_legacy_modes_display_as_new_product_modes():
    assert display_compute_mode("cpu") == MODE_VULKAN
    assert display_compute_mode("gpu") == MODE_CUDA
    assert display_compute_mode("openai") == MODE_API
