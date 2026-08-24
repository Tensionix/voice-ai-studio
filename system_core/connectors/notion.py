"""Notion connector — create a page from the transcript via the Notion REST API.

Reads the canonical doc, turns it into Notion blocks (summary, action items as
to-dos, transcript grouped by speaker) and POSTs a new page under a parent page
or database. Long transcripts are appended in batches (Notion caps a request at
100 blocks, and each text run at 2000 chars). Network access is via `httpx`
(a base dependency); the HTTP session is injectable so tests run offline.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from ..core.credentials import read_api_key
from ..core.models import Segment, TranscriptDoc
from ..core.paths import ProjectPaths
from ..providers._openai_common import is_timeout_or_transient, is_transport_error
from .base import Connector, ExportResult, doc_title

_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"
_MAX_BLOCKS = 100          # Notion: max children per request
_MAX_TEXT = 1900           # Notion: max 2000 chars per rich_text run (margin)
_MAX_RETRIES = 4


def _hms(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _rich_text(content: str) -> list[dict[str, Any]]:
    """Split content into <=2000-char runs (Notion's per-run limit)."""
    content = content or ""
    if not content:
        return []
    runs: list[dict[str, Any]] = []
    for i in range(0, len(content), _MAX_TEXT):
        runs.append({"type": "text", "text": {"content": content[i:i + _MAX_TEXT]}})
    return runs


def _para(text: str) -> dict[str, Any]:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": _rich_text(text)}}


def _heading(level: int, text: str) -> dict[str, Any]:
    key = f"heading_{min(max(level, 1), 3)}"
    return {"object": "block", "type": key, key: {"rich_text": _rich_text(text)}}


def _todo(text: str) -> dict[str, Any]:
    return {"object": "block", "type": "to_do",
            "to_do": {"rich_text": _rich_text(text), "checked": False}}


def _segments_blocks(segments: list[Segment]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    has_speakers = any(seg.speaker for seg in segments)
    current: Optional[str] = None
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        if has_speakers and seg.speaker and seg.speaker != current:
            blocks.append(_heading(3, seg.speaker))
            current = seg.speaker
        blocks.append(_para(f"[{_hms(seg.start)}] {text}"))
    return blocks


def _cleaned_blocks(markdown: str) -> list[dict[str, Any]]:
    """Light markdown -> blocks for the polished body: headings and paragraphs."""
    blocks: list[dict[str, Any]] = []
    for para in markdown.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if para.startswith("### "):
            blocks.append(_heading(3, para[4:].strip()))
        elif para.startswith("## "):
            blocks.append(_heading(2, para[3:].strip()))
        elif para.startswith("# "):
            blocks.append(_heading(2, para[2:].strip()))
        else:
            blocks.append(_para(" ".join(para.split("\n"))))
    return blocks


def doc_to_blocks(doc: TranscriptDoc) -> list[dict[str, Any]]:
    """Build the Notion block list mirroring the Markdown layout."""
    blocks: list[dict[str, Any]] = []
    md = doc.metadata
    if md.summary:
        blocks.append(_heading(2, "Summary"))
        blocks.append(_para(md.summary.strip()))
    if md.action_items:
        blocks.append(_heading(2, "Action Items"))
        blocks += [_todo(item) for item in md.action_items]
    blocks.append(_heading(2, "Transcript"))
    if doc.cleaned_markdown and doc.cleaned_markdown.strip():
        blocks += _cleaned_blocks(doc.cleaned_markdown.strip())
    else:
        blocks += _segments_blocks(doc.transcription.segments)
    return blocks


class NotionConnector(Connector):
    name = "notion"

    def __init__(self, paths: ProjectPaths, config: dict[str, Any], *, session: Any = None) -> None:
        super().__init__(paths, config)
        self._session = session  # injectable httpx-like client (tests pass a fake)

    # --- gating --------------------------------------------------------------
    def _token(self) -> Optional[str]:
        return read_api_key(self.paths, "notion")

    def _parent(self) -> tuple[str, str]:
        """(parent_type, parent_id): parent_type is 'page' or 'database'."""
        ptype = str(self.config.get("parent_type", "page")).strip().lower()
        ptype = "database" if ptype.startswith("data") else "page"
        return ptype, str(self.config.get("parent_id", "")).strip()

    def is_configured(self) -> tuple[bool, str]:
        if not self._token():
            return False, "Notion API key is not set (config/api_key_notion.txt)."
        _, pid = self._parent()
        if not pid:
            return False, "Notion parent page/database id is not set."
        return True, ""

    # --- HTTP ----------------------------------------------------------------
    def _client(self) -> Any:
        if self._session is not None:
            return self._session
        import httpx  # base dependency; imported lazily

        self._session = httpx.Client(timeout=30.0)
        return self._session

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Notion-Version": _VERSION,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Do one Notion call with retries. Returns parsed JSON or raises RuntimeError."""
        client = self._client()
        call = getattr(client, method.lower())
        last_err = ""
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = call(url, headers=self._headers(), json=payload)
                status = getattr(resp, "status_code", 0)
                if 200 <= status < 300:
                    return resp.json()
                # Parse Notion's error message when present.
                try:
                    body = resp.json()
                    last_err = body.get("message") or body.get("code") or resp.text
                except Exception:
                    last_err = getattr(resp, "text", f"HTTP {status}")
                retryable = status == 429 or 500 <= status < 600
                if retryable and attempt < _MAX_RETRIES:
                    time.sleep(min(30.0, 2.0 * attempt))
                    continue
                raise RuntimeError(f"Notion HTTP {status}: {last_err}")
            except RuntimeError:
                raise
            except Exception as exc:  # transport-level
                last_err = str(exc)
                if (is_transport_error(exc) or is_timeout_or_transient(last_err.lower())) and attempt < _MAX_RETRIES:
                    time.sleep(min(30.0, 2.0 * attempt))
                    continue
                raise RuntimeError(f"Notion request failed: {exc}") from exc
        raise RuntimeError(f"Notion request failed: {last_err}")

    # --- action --------------------------------------------------------------
    def export(self, doc: TranscriptDoc, *, source_path: Optional[Path] = None) -> ExportResult:
        ok, reason = self.is_configured()
        if not ok:
            return ExportResult(self.name, False, message=reason)

        ptype, pid = self._parent()
        title = doc_title(doc)
        blocks = doc_to_blocks(doc)

        parent = {"page_id": pid} if ptype == "page" else {"database_id": pid}
        payload = {
            "parent": parent,
            "properties": {"title": {"title": _rich_text(title)}},
            "children": blocks[:_MAX_BLOCKS],
        }
        try:
            page = self._request("POST", f"{_API}/pages", payload)
            page_id = page.get("id", "")
            # Append any remaining blocks past the 100-block create limit.
            rest = blocks[_MAX_BLOCKS:]
            for i in range(0, len(rest), _MAX_BLOCKS):
                self._request(
                    "PATCH",
                    f"{_API}/blocks/{page_id}/children",
                    {"children": rest[i:i + _MAX_BLOCKS]},
                )
        except Exception as exc:  # noqa: BLE001 — surfaced as result, never raised
            return ExportResult(self.name, False, message=str(exc))

        url = page.get("url", "") or page_id
        return ExportResult(self.name, True, target=url, message="Created Notion page")
