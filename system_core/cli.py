"""Command-line entry point for the file pipeline (iteration 1 test harness).

Run from the project root:
    python -m system_core.cli "path\\to\\file_or_folder" [options]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python system_core/cli.py` as well as `python -m system_core.cli`.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR.parent))

from system_core.core.paths import ensure_project_dirs, get_project_paths  # noqa: E402
from system_core.providers import model_catalog  # noqa: E402
from system_core.settings import load_settings  # noqa: E402
from system_core.writers.json_writer import read_json  # noqa: E402


def _log(message: str) -> None:
    print(message, flush=True)


def _apply_overrides(settings: dict, args: argparse.Namespace) -> dict:
    if args.lang:
        settings["language"] = args.lang
    if args.compute_mode:
        settings["compute_mode"] = args.compute_mode
    if args.stt_model:
        settings.setdefault("stt", {})["model"] = args.stt_model
    if args.postprocess_model:
        settings.setdefault("postprocess", {})["model"] = args.postprocess_model
    if args.diarize:
        settings.setdefault("transcription", {})["diarize"] = True
    if args.assemblyai:
        settings.setdefault("assemblyai", {})["enabled"] = True
    pipeline = settings.setdefault("pipeline", {})
    if args.recursive:
        pipeline["recursive"] = True
    if args.force:
        pipeline["force"] = True
    if args.no_skip:
        pipeline["skip_existing"] = False
    if args.exports:
        wanted = {fmt.strip().lower() for fmt in args.exports.split(",") if fmt.strip()}
        settings["exports"] = {
            "json": "json" in wanted,
            "markdown": "markdown" in wanted or "md" in wanted,
            "srt": "srt" in wanted,
            "vtt": "vtt" in wanted,
            "txt": "txt" in wanted,
        }
    return settings


def cmd_list_models(args: argparse.Namespace) -> int:
    paths = get_project_paths()
    role = model_catalog.ROLE_STT if args.role == "stt" else model_catalog.ROLE_CHAT
    result = model_catalog.list_models(paths, role, refresh=args.refresh)
    print(f"# {args.role} models (source: {result.source})")
    for model in result.models:
        print(model)
    return 0


def cmd_regen(args: argparse.Namespace) -> int:
    from system_core.writers.markdown_writer import write_markdown
    from system_core.writers.srt_writer import write_srt
    from system_core.writers.vtt_writer import write_vtt
    from system_core.pipeline.orchestrator import _subtitle_settings

    paths = get_project_paths()
    settings = load_settings(paths)
    sub = _subtitle_settings(settings)
    for json_arg in args.inputs:
        json_path = Path(json_arg).resolve()
        doc = read_json(json_path)
        stem = json_path.name.replace(".transcript.json", "")
        base = json_path.parent / stem
        write_markdown(base.with_suffix(".md"), doc)
        write_srt(base.with_suffix(".srt"), doc.transcription.segments, sub)
        write_vtt(base.with_suffix(".vtt"), doc.transcription.segments, sub)
        _log(f"Regenerated MD/SRT/VTT from {json_path.name}")
    return 0


def cmd_rename_speakers(args: argparse.Namespace) -> int:
    from system_core.pipeline.speaker_rename import rename_speakers_in_file

    paths = get_project_paths()
    settings = load_settings(paths)

    mapping: dict[str, str] = {}
    for pair in args.mapping:
        if "=" not in pair:
            _log(f"Ignoring '{pair}' (expected OLD=NEW)")
            continue
        old, new = pair.split("=", 1)
        mapping[old.strip()] = new.strip()
    if not mapping:
        _log("No OLD=NEW pairs given.")
        return 2

    json_path = Path(args.input).resolve()
    written = rename_speakers_in_file(json_path, mapping, settings)
    for path in written:
        _log(f"Updated {path.name}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from system_core.connectors import export_doc

    paths = get_project_paths()
    settings = load_settings(paths)

    # Explicit flags force a subset; without flags, use the enabled connectors.
    only: set[str] | None = None
    forced = {name for name in ("notion", "obsidian") if getattr(args, name, False)}
    if forced:
        only = forced
    if args.vault:
        settings.setdefault("connectors", {}).setdefault("obsidian", {})["vault_path"] = args.vault
    if args.notion_parent:
        settings.setdefault("connectors", {}).setdefault("notion", {})["parent_id"] = args.notion_parent

    failures = 0
    for json_arg in args.inputs:
        json_path = Path(json_arg).resolve()
        doc = read_json(json_path)
        _log(f"Exporting {json_path.name}")
        results = export_doc(paths, settings, doc, source_path=json_path, only=only, log=_log)
        if not results:
            _log("  (no connectors enabled — use --notion/--obsidian or enable them in settings)")
        failures += sum(1 for r in results if not r.ok)
    return 1 if failures else 0


def cmd_process(args: argparse.Namespace) -> int:
    from system_core.pipeline.queue import run_queue

    paths = get_project_paths()
    ensure_project_dirs(paths)
    settings = _apply_overrides(load_settings(paths), args)

    inputs = [Path(item) for item in args.inputs]
    summary = run_queue(paths, inputs, settings, log=_log)

    if summary.failed:
        _log("\nFailed files:")
        for path, error in summary.failed:
            _log(f"  - {path}: {error}")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="audion-voice", description="Audion Voice AI pipeline")
    sub = parser.add_subparsers(dest="command")

    proc = sub.add_parser("process", help="Process files/folders (default)")
    _add_process_args(proc)

    models = sub.add_parser("list-models", help="List available models")
    models.add_argument("role", choices=["stt", "chat"], nargs="?", default="stt")
    models.add_argument("--refresh", action="store_true", help="Force refresh from API")
    models.set_defaults(func=cmd_list_models)

    regen = sub.add_parser("regen", help="Regenerate MD/SRT/VTT from existing .transcript.json")
    regen.add_argument("inputs", nargs="+")
    regen.set_defaults(func=cmd_regen)

    rename = sub.add_parser("rename", help="Rename speakers in a .transcript.json + regen sidecars")
    rename.add_argument("input", help="Path to a .transcript.json")
    rename.add_argument("mapping", nargs="+", help='OLD=NEW pairs, e.g. "Speaker 0=Иван"')
    rename.set_defaults(func=cmd_rename_speakers)

    export = sub.add_parser("export", help="Export existing .transcript.json to Notion/Obsidian")
    export.add_argument("inputs", nargs="+")
    export.add_argument("--notion", action="store_true", help="Force Notion export")
    export.add_argument("--obsidian", action="store_true", help="Force Obsidian export")
    export.add_argument("--vault", help="Override Obsidian vault path")
    export.add_argument("--notion-parent", dest="notion_parent", help="Override Notion parent id")
    export.set_defaults(func=cmd_export)

    return parser


def _add_process_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("inputs", nargs="+", help="Files or folders to process")
    parser.add_argument("--lang", help="Language code or 'auto'")
    parser.add_argument(
        "--compute-mode",
        choices=["api", "vulkan", "cuda", "cpu", "gpu"],
        dest="compute_mode",
    )
    parser.add_argument("--stt-model", dest="stt_model")
    parser.add_argument("--postprocess-model", dest="postprocess_model")
    parser.add_argument("--diarize", action="store_true", help="Request speaker labels")
    parser.add_argument("--assemblyai", action="store_true", help="Use AssemblyAI provider")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--force", action="store_true", help="Reprocess even if outputs exist")
    parser.add_argument("--no-skip", action="store_true", help="Do not skip existing outputs")
    parser.add_argument("--exports", help="Comma list: json,markdown,srt,vtt,txt")
    parser.set_defaults(func=cmd_process)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Default to the `process` subcommand when the first arg isn't a known command.
    known = {"process", "list-models", "regen", "export", "rename", "-h", "--help"}
    if argv and argv[0] not in known:
        argv = ["process", *argv]

    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
