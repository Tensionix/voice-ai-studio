"""Export connectors: push a finished transcript to Notion / Obsidian.

Connectors read the canonical `TranscriptDoc`, are opt-in (the `connectors:`
settings block), and isolated (one failure never breaks the pipeline). Entry
point for callers is `registry.export_doc`.
"""

from .base import Connector, ExportResult
from .registry import any_enabled, build_connectors, export_doc

__all__ = [
    "Connector",
    "ExportResult",
    "any_enabled",
    "build_connectors",
    "export_doc",
]
