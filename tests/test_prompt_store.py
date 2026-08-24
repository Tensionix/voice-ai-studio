from pathlib import Path

from system_core.core.paths import ProjectPaths
from system_core.core.prompt_store import ROLE_CLEANUP, PromptStore


def _paths(tmp: Path) -> ProjectPaths:
    return ProjectPaths(
        root=tmp, input=tmp, output=tmp, logs=tmp, report=tmp, workspace=tmp,
        config=tmp, release=tmp, models=tmp, tools=tmp, runtime=tmp, system_core=tmp,
    )


def test_builtins_present_pinned_and_undeletable(tmp_path: Path):
    store = PromptStore.load(_paths(tmp_path))
    builtins = [p for p in store.list(ROLE_CLEANUP) if p.builtin]
    assert builtins, "expected at least one built-in cleanup prompt"
    assert "ПРИОРИТЕТ №1" in builtins[0].text
    assert "НЕ ПЕРЕФРАЗИРУЙ" in builtins[0].text
    b = builtins[0]
    assert b.pinned and not b.deletable
    try:
        store.delete_user(b.id)
        assert False, "built-in should not be deletable"
    except PermissionError:
        pass
    # set_pinned is a no-op for built-ins (permanent pin)
    store.set_pinned(b.id, False)
    assert store.get(b.id).pinned is True


def test_user_crud_pin_delete(tmp_path: Path):
    store = PromptStore.load(_paths(tmp_path))
    e = store.add_user(ROLE_CLEANUP, "My cleanup", "do the thing")
    assert store.get(e.id).text == "do the thing"
    store.update_user(e.id, text="do it better")
    assert store.get(e.id).text == "do it better"
    store.set_pinned(e.id, True)
    assert store.get(e.id).pinned is True
    store.delete_user(e.id)
    assert store.get(e.id) is None


def test_persistence_roundtrip(tmp_path: Path):
    p = _paths(tmp_path)
    store = PromptStore.load(p)
    e = store.add_user(ROLE_CLEANUP, "Persisted", "text here", pinned=True)
    store.set_active(ROLE_CLEANUP, e.id)
    # reload from disk
    store2 = PromptStore.load(p)
    assert store2.get(e.id) is not None
    assert store2.active(ROLE_CLEANUP).id == e.id


def test_active_defaults_to_builtin(tmp_path: Path):
    store = PromptStore.load(_paths(tmp_path))
    active = store.active(ROLE_CLEANUP)
    assert active.builtin is True


def test_mru_touch_orders_unpinned(tmp_path: Path):
    store = PromptStore.load(_paths(tmp_path))
    a = store.add_user(ROLE_CLEANUP, "A", "a")
    b = store.add_user(ROLE_CLEANUP, "B", "b")
    store.touch(b.id)  # b used more recently
    unpinned = [p for p in store.list(ROLE_CLEANUP) if not p.builtin and not p.pinned]
    assert unpinned[0].id == b.id
