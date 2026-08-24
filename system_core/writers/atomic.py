"""Atomic sidecar writes (TZ section 6): write .tmp then replace.

Never leave a half-written sidecar if the process dies mid-write.
"""

from __future__ import annotations

from pathlib import Path
import os


def write_text_atomic(dest: Path, content: str, *, encoding: str = "utf-8") -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    with tmp.open("w", encoding=encoding, newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, dest)  # atomic on the same filesystem
    return dest
