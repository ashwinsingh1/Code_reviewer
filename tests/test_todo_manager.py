"""
Tests for todo_manager — no COM, no Outlook dependency required.
"""
import json
import pytest
from pathlib import Path

# Patch the TODO_FILE before importing todo_manager
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "code_reviewer"))


@pytest.fixture()
def todo_file(tmp_path, monkeypatch):
    """Redirect TODO_FILE to a temp file for each test."""
    import daily_agent.todo_manager as tm
    monkeypatch.setattr(tm, "TODO_FILE", tmp_path / "todos.json")
    return tmp_path / "todos.json"


def get_tm():
    import daily_agent.todo_manager as tm
    return tm


def test_empty_list(todo_file):
    tm = get_tm()
    assert tm.list_todos() == []


def test_add_returns_task(todo_file):
    tm = get_tm()
    task = tm.add_todo("Write unit tests", priority="high")
    assert task["id"] == 1
    assert task["title"] == "Write unit tests"
    assert task["priority"] == "high"
    assert task["status"] == "pending"
    assert task["due_date"] is None


def test_add_multiple_ids_increment(todo_file):
    tm = get_tm()
    t1 = tm.add_todo("Task one")
    t2 = tm.add_todo("Task two")
    assert t2["id"] == t1["id"] + 1


def test_list_todos_persists(todo_file):
    tm = get_tm()
    tm.add_todo("Persisted task")
    tasks = tm.list_todos()
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Persisted task"


def test_complete_todo(todo_file):
    tm = get_tm()
    task = tm.add_todo("Complete me")
    result = tm.complete_todo(task["id"])
    assert result is True
    done = tm.list_todos()[0]
    assert done["status"] == "done"
    assert "completed_at" in done


def test_complete_nonexistent(todo_file):
    tm = get_tm()
    assert tm.complete_todo(999) is False


def test_delete_todo(todo_file):
    tm = get_tm()
    task = tm.add_todo("Delete me")
    assert tm.delete_todo(task["id"]) is True
    assert tm.list_todos() == []


def test_delete_nonexistent(todo_file):
    tm = get_tm()
    assert tm.delete_todo(999) is False


def test_pending_todos_filters(todo_file):
    tm = get_tm()
    t1 = tm.add_todo("Pending task")
    t2 = tm.add_todo("Done task")
    tm.complete_todo(t2["id"])
    pending = tm.pending_todos()
    assert len(pending) == 1
    assert pending[0]["id"] == t1["id"]


def test_edit_todo_title(todo_file):
    tm = get_tm()
    task = tm.add_todo("Old title")
    assert tm.edit_todo(task["id"], title="New title") is True
    updated = tm.list_todos()[0]
    assert updated["title"] == "New title"


def test_edit_todo_priority(todo_file):
    tm = get_tm()
    task = tm.add_todo("Test task", priority="low")
    tm.edit_todo(task["id"], priority="high")
    assert tm.list_todos()[0]["priority"] == "high"


def test_edit_nonexistent(todo_file):
    tm = get_tm()
    assert tm.edit_todo(999, title="Ghost") is False


def test_concurrent_writes_no_data_loss(todo_file):
    """Ensure the threading lock prevents concurrent write races."""
    import threading
    import daily_agent.todo_manager as tm

    errors = []

    def _add(title):
        try:
            tm.add_todo(title)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_add, args=(f"Task {i}",)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Concurrent writes raised errors: {errors}"
    tasks = tm.list_todos()
    assert len(tasks) == 20, f"Expected 20 tasks, got {len(tasks)}"
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids)), "Duplicate IDs found — race condition detected"
