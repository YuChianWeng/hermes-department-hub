"""The dashboard's board adapter: real kanban.db reads, and strictly read-only."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from department_lib.kanban_cli import BoardError
from department_lib.kanban_db_source import KanbanDbBoard


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def board_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path / "kanban-home"))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    # This pytest process itself runs inside a dispatcher worker, whose env
    # carries the delegated-child fence marker. The fence (correctly) refuses
    # to create/migrate a board on a descendant's behalf; the fixture needs a
    # throwaway board under tmp_path, so unfence *this isolated test process*
    # only. The adapter under test still opens the file strictly read-only.
    monkeypatch.delenv("HERMES_DELEGATED_CHILD_CONTEXT", raising=False)
    agent_root = Path(os.path.expanduser(os.environ.get("HERMES_AGENT_ROOT", "~/.hermes/hermes-agent")))
    if str(agent_root) not in sys.path:
        sys.path.insert(0, str(agent_root))
    from hermes_cli import kanban_db
    # ``kanban_db`` re-exports the split connection helpers only partially;
    # ``connect`` lives in ``kanban_db_connect`` (verified against the live
    # Hermes checkout, 2026-11-05).
    from hermes_cli.kanban_db_connect import connect
    kanban_db.init_db()
    conn = connect()
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='tasks'").fetchone()
    yield kanban_db, conn
    conn.close()


def test_adapter_reads_status_title_and_events(board_env, tmp_path):
    kb, conn = board_env
    task_id = kb.create_task(conn, title="Ship the widget", assignee="worker-a")
    kb.block_task(conn, task_id, reason="needs the operator API key", kind="needs_input")

    board = KanbanDbBoard()
    snap = board.get(task_id)
    assert snap.status == "blocked"
    assert snap.assignee == "worker-a"
    assert board.titles([task_id]) == {task_id: "Ship the widget"}
    assert any(e.kind == "blocked" for e in snap.events)
    blocked = [e for e in snap.events if e.kind == "blocked"][-1]
    assert "operator API key" in str(blocked.payload.get("reason") or "")


def test_adapter_never_writes_the_board(board_env):
    kb, conn = board_env
    task_id = kb.create_task(conn, title="Read only", assignee="worker-a")
    db_path = Path(kb.kanban_db_path())
    conn.close()
    before = sha256(db_path)
    board = KanbanDbBoard()
    for _ in range(3):
        board.get(task_id)
        board.titles([task_id])
        board.health()
    assert sha256(db_path) == before


def test_adapter_reports_missing_board_not_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path / "nothing-here"))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    board = KanbanDbBoard()
    with pytest.raises(BoardError) as err:
        board.get("t_missing")
    assert err.value.kind in ("unavailable", "not_found")
    assert board.health()["available"] is False


def test_adapter_missing_task_is_not_found(board_env):
    kb, conn = board_env
    kb.create_task(conn, title="Something", assignee="worker-a")
    board = KanbanDbBoard()
    with pytest.raises(BoardError) as err:
        board.get("t_does_not_exist")
    assert err.value.kind == "not_found"


def test_adapter_rejects_a_non_kanban_sqlite_file(tmp_path):
    broken = tmp_path / "not-a-board.db"
    conn = sqlite3.connect(broken)
    conn.execute("CREATE TABLE unrelated (x)")
    conn.commit()
    conn.close()
    board = KanbanDbBoard(db_path=broken)
    with pytest.raises(BoardError) as err:
        board.get("t1")
    assert err.value.kind == "unavailable"
    assert "schema" in str(err.value)
