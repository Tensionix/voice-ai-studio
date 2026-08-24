"""Persistent Live-dictation clipboard behavior."""

from system_core.core.paths import get_project_paths
from system_core.live.history import DictationHistory, HISTORY_CACHE_LIMIT


def test_history_persists_cache_limit_and_preserves_paragraphs(tmp_path):
    paths = get_project_paths(tmp_path)
    history = DictationHistory.load(paths)
    multiline = "First paragraph.\n\nSecond paragraph."

    history.add(multiline)
    for index in range(HISTORY_CACHE_LIMIT + 4):
        history.add(f"dictation {index}")

    restored = DictationHistory.load(paths)
    assert len(restored.entries()) == HISTORY_CACHE_LIMIT
    assert restored.latest().text == f"dictation {HISTORY_CACHE_LIMIT + 3}"
    assert multiline not in {entry.text for entry in restored.entries()}

    restored.add(multiline)
    assert restored.latest().text == multiline


def test_history_delete_is_persistent(tmp_path):
    paths = get_project_paths(tmp_path)
    history = DictationHistory.load(paths)
    first = history.add("first")
    second = history.add("second")

    assert first is not None
    assert second is not None
    assert history.delete(first.entry_id)
    assert not history.delete("missing")

    restored = DictationHistory.load(paths)
    assert [entry.text for entry in restored.entries()] == ["second"]
