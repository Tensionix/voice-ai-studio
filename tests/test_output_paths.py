from pathlib import Path

from system_core.core.paths import ProjectPaths
from system_core.pipeline.orchestrator import outputs_exist, sidecar_paths


def _paths(root: Path) -> ProjectPaths:
    return ProjectPaths(
        root=root,
        input=root / "input",
        output=root / "output",
        logs=root / "logs",
        report=root / "report",
        workspace=root / "workspace",
        config=root / "config",
        release=root / "release",
        models=root / "models",
        tools=root / "Tools",
        runtime=root / "runtime",
        system_core=root / "system_core",
    )


def test_sidecars_default_next_to_source(tmp_path: Path):
    source = tmp_path / "media" / "meeting.wav"
    source.parent.mkdir()
    settings = {"exports": {"json": True, "markdown": True}}

    resolved = sidecar_paths(source, settings, _paths(tmp_path))

    assert resolved["json"] == source.parent / "meeting.transcript.json"
    assert resolved["markdown"] == source.parent / "meeting.md"


def test_sidecars_can_go_to_output_folder(tmp_path: Path):
    source = tmp_path / "media" / "meeting.wav"
    source.parent.mkdir()
    paths = _paths(tmp_path)
    settings = {
        "pipeline": {"save_next_to_source": False},
        "exports": {"json": True, "markdown": True},
    }

    resolved = sidecar_paths(source, settings, paths)

    assert resolved["json"] == paths.output / "meeting.transcript.json"
    assert resolved["markdown"] == paths.output / "meeting.md"


def test_outputs_exist_checks_selected_output_folder(tmp_path: Path):
    source = tmp_path / "media" / "meeting.wav"
    source.parent.mkdir()
    paths = _paths(tmp_path)
    paths.output.mkdir()
    settings = {
        "pipeline": {"save_next_to_source": False},
        "exports": {"json": True, "markdown": True, "srt": False, "vtt": False, "txt": False},
    }

    (paths.output / "meeting.transcript.json").write_text("{}", encoding="utf-8")
    assert outputs_exist(source, settings, paths) is False

    (paths.output / "meeting.md").write_text("# Meeting\n", encoding="utf-8")
    assert outputs_exist(source, settings, paths) is True
