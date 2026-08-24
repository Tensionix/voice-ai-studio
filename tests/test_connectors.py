import json
from pathlib import Path

from system_core.connectors import export_doc
from system_core.connectors.notion import NotionConnector, doc_to_blocks
from system_core.connectors.obsidian import ObsidianConnector
from system_core.core.models import (
    Metadata,
    Segment,
    SourceMeta,
    Transcription,
    TranscriptDoc,
)
from system_core.core.paths import get_project_paths


def _doc(n_segments: int = 2) -> TranscriptDoc:
    segs = []
    for i in range(n_segments):
        segs.append(
            Segment(
                index=i,
                start=float(i * 4),
                end=float(i * 4 + 3),
                speaker=f"Speaker {i % 2 + 1}",
                text=f"Реплика номер {i}.",
            )
        )
    return TranscriptDoc(
        source=SourceMeta(
            path="C:/media/meeting.mp4",
            filename="meeting.mp4",
            media_type="video",
            duration_seconds=120.0,
            created_at="2026-06-18T10:00:00",
        ),
        transcription=Transcription(language="ru", diarization=True, segments=segs),
        metadata=Metadata(
            title="Совещание команды",
            slug="soveshchanie-komandy",
            summary="Короткое резюме встречи.",
            tags=["meeting"],
            action_items=["Отправить протокол"],
        ),
    )


def _paths(tmp_path: Path):
    paths = get_project_paths(tmp_path)
    paths.config.mkdir(parents=True, exist_ok=True)
    return paths


# --- Obsidian ----------------------------------------------------------------

def test_obsidian_writes_note_into_vault_subfolder(tmp_path: Path):
    vault = tmp_path / "Vault"
    vault.mkdir()
    paths = _paths(tmp_path)
    conn = ObsidianConnector(paths, {"enabled": True, "vault_path": str(vault), "subfolder": "Transcripts"})
    result = conn.export(_doc())
    assert result.ok
    note = vault / "Transcripts" / "soveshchanie-komandy.md"
    assert note.exists()
    text = note.read_text(encoding="utf-8")
    assert "# Совещание команды" in text
    assert "## Transcript" in text


def test_obsidian_skips_when_vault_missing(tmp_path: Path):
    paths = _paths(tmp_path)
    conn = ObsidianConnector(paths, {"enabled": True, "vault_path": str(tmp_path / "nope")})
    result = conn.export(_doc())
    assert not result.ok
    assert "does not exist" in result.message


# --- Notion (fake HTTP) ------------------------------------------------------

class _Resp:
    def __init__(self, status: int, data: dict):
        self.status_code = status
        self._data = data
        self.text = json.dumps(data)

    def json(self) -> dict:
        return self._data


class FakeNotionSession:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    def post(self, url, headers=None, json=None):
        self.calls.append(("POST", url, json))
        return _Resp(200, {"id": "page-123", "url": "https://notion.so/page-123"})

    def patch(self, url, headers=None, json=None):
        self.calls.append(("PATCH", url, json))
        return _Resp(200, {})


def _with_notion_key(paths) -> None:
    (paths.config / "api_key_notion.txt").write_text("secret_key_abcd\n", encoding="utf-8")


def test_notion_creates_page_with_title_and_blocks(tmp_path: Path):
    paths = _paths(tmp_path)
    _with_notion_key(paths)
    session = FakeNotionSession()
    conn = NotionConnector(
        paths, {"enabled": True, "parent_type": "page", "parent_id": "parent-1"}, session=session
    )
    result = conn.export(_doc())
    assert result.ok
    assert result.target == "https://notion.so/page-123"
    method, url, payload = session.calls[0]
    assert method == "POST" and url.endswith("/pages")
    assert payload["parent"] == {"page_id": "parent-1"}
    title_runs = payload["properties"]["title"]["title"]
    assert title_runs[0]["text"]["content"] == "Совещание команды"
    types = [b["type"] for b in payload["children"]]
    assert "heading_2" in types and "to_do" in types


def test_notion_batches_blocks_over_100(tmp_path: Path):
    paths = _paths(tmp_path)
    _with_notion_key(paths)
    session = FakeNotionSession()
    conn = NotionConnector(paths, {"enabled": True, "parent_id": "p"}, session=session)
    result = conn.export(_doc(n_segments=160))
    assert result.ok
    methods = [c[0] for c in session.calls]
    assert "POST" in methods and "PATCH" in methods
    # First request never exceeds Notion's 100-block ceiling.
    assert len(session.calls[0][2]["children"]) <= 100


def test_notion_not_configured_without_key(tmp_path: Path):
    paths = _paths(tmp_path)
    conn = NotionConnector(paths, {"enabled": True, "parent_id": "p"}, session=FakeNotionSession())
    ok, reason = conn.is_configured()
    assert not ok and "key" in reason.lower()
    result = conn.export(_doc())
    assert not result.ok


def test_doc_to_blocks_splits_long_text(tmp_path: Path):
    doc = _doc(n_segments=1)
    doc.transcription.segments[0].text = "А" * 5000
    blocks = doc_to_blocks(doc)
    paras = [b for b in blocks if b["type"] == "paragraph"]
    # The transcript paragraph (the long one) must be split across runs.
    runs = max((p["paragraph"]["rich_text"] for p in paras), key=len)
    assert len(runs) >= 3  # 5000 chars / 1900 per run
    assert all(len(r["text"]["content"]) <= 2000 for r in runs)


# --- registry isolation ------------------------------------------------------

def test_export_doc_isolates_failures(tmp_path: Path):
    vault = tmp_path / "Vault"
    vault.mkdir()
    paths = _paths(tmp_path)  # no Notion key -> notion is skipped, not fatal
    settings = {
        "connectors": {
            "obsidian": {"enabled": True, "vault_path": str(vault)},
            "notion": {"enabled": True, "parent_id": "p"},
        }
    }
    results = export_doc(paths, settings, _doc(), log=lambda _m: None)
    by_name = {r.connector: r for r in results}
    assert by_name["obsidian"].ok
    assert not by_name["notion"].ok  # missing key, isolated
    assert (vault / "soveshchanie-komandy.md").exists()
