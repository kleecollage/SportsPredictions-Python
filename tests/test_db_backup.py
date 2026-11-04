"""Respaldo de picks_history.db fuera de git."""
import asyncio
import sqlite3
from pathlib import Path

import pytest

from value_bot import db
from value_bot.db import backup


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db.connection, "DB_PATH", tmp_path / "picks.db")
    db.init_db()
    db.save_picks("2026-11-03", [{"sport": "nfl", "home": "A", "away": "B", "type": "Victoria A"}])
    return tmp_path


def _picks_in(path: Path) -> int:
    with db.connection.connect(path) as conn:
        return conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0]


def test_backup_is_a_readable_copy_of_the_db(temp_db):
    dest = temp_db / "cloud" / "ValueBot"
    (temp_db / "cloud").mkdir()
    copy = backup.backup_db(dest)
    assert copy is not None and copy.parent == dest and _picks_in(copy) == 1


def test_backup_keeps_only_the_newest_copies(temp_db):
    dest = temp_db / "ValueBot"
    dest.mkdir()
    old = [dest / f"picks_history-2026010{i}-090000.db" for i in range(1, 6)]
    for path in old:
        path.write_bytes(b"")
    (dest / "notas.txt").write_text("no se toca")
    backup.backup_db(dest, keep=3)
    kept = sorted(p.name for p in dest.glob("picks_history-*.db"))
    assert len(kept) == 3 and old[-1].name in kept and old[0].name not in kept
    assert (dest / "notas.txt").exists()


def test_without_icloud_drive_the_backup_is_skipped(temp_db, caplog):
    dest = temp_db / "sin-icloud" / "ValueBot"
    assert backup.backup_db(dest) is None
    assert not dest.parent.exists() and "respaldo" in caplog.text.lower()


def test_a_failing_backup_never_breaks_the_run(temp_db, caplog):
    dest = temp_db / "ValueBot"
    dest.write_text("es un archivo, no una carpeta")
    assert backup.backup_db(dest) is None
    assert "respaldo" in caplog.text.lower()


def test_tests_never_back_up_to_the_real_icloud():
    from value_bot import config
    assert "CloudDocs" not in str(config.DB_BACKUP_DIR)


def test_main_backs_up_after_saving_the_picks(temp_db, monkeypatch):
    from value_bot import main
    order = []

    async def no_send(_msg):
        return None
    monkeypatch.setattr("value_bot.sports_registry.SPORTS_ENABLED", {"football": False, "nba": False, "nfl": True})
    monkeypatch.setattr(main, "PICK_SOURCES", {"football": lambda c: [], "nba": lambda c: [], "nfl": lambda c: []})
    monkeypatch.setattr(main, "resolve_pending_picks", lambda: 0)
    monkeypatch.setattr(main, "send_telegram", no_send)
    monkeypatch.setattr(main, "save_picks", lambda *a: order.append("save") or 0)
    monkeypatch.setattr(main, "backup_db", lambda *a, **k: order.append("backup"))
    asyncio.run(main.main())
    assert order == ["save", "backup"]


def test_weekly_report_also_backs_up(temp_db, monkeypatch):
    from value_bot import main
    calls = []

    async def no_send(_msg):
        return None
    monkeypatch.setattr(main, "resolve_pending_picks", lambda: 0)
    monkeypatch.setattr(main, "send_telegram", no_send)
    monkeypatch.setattr(main, "backup_db", lambda *a, **k: calls.append("backup"))
    asyncio.run(main.weekly_report())
    assert calls == ["backup"]


# ── Git ───────────────────────────────────────────────────────────

def test_local_dbs_are_ignored_by_git():
    gitignore = (Path(__file__).parent.parent / ".gitignore").read_text().splitlines()
    assert "picks_history.db" in gitignore


# ── Revisión: rotación segura, conexiones cerradas, respaldo aunque falle el envío ──

def _backup_with_picks(dest: Path, name: str, n: int) -> Path:
    path = dest / name
    with db.connection.connect(path) as conn:
        conn.execute("CREATE TABLE picks (id INTEGER)")
        conn.executemany("INSERT INTO picks VALUES (?)", [(i,) for i in range(n)])
    return path


def test_a_shrunk_db_is_copied_but_never_rotates_out_the_good_backups(temp_db, caplog):
    # BD local perdida/recreada vacía: sus copias no deben empujar fuera a las buenas
    dest = temp_db / "ValueBot"
    dest.mkdir()
    good = [_backup_with_picks(dest, f"picks_history-2026010{i}-090000.db", 5) for i in range(1, 4)]
    copy = backup.backup_db(dest, keep=2)
    assert copy is not None and all(p.exists() for p in good)
    assert any(r.levelname == "ERROR" and "menos picks" in r.getMessage() for r in caplog.records)


def test_a_growing_db_rotates_normally(temp_db):
    dest = temp_db / "ValueBot"
    dest.mkdir()
    old = [_backup_with_picks(dest, f"picks_history-2026010{i}-090000.db", 0) for i in range(1, 4)]
    backup.backup_db(dest, keep=2)
    assert not old[0].exists() and not old[1].exists() and old[2].exists()


@pytest.mark.parametrize("keep", [0, -1])
def test_keep_below_one_keeps_at_least_the_new_copy(temp_db, keep):
    dest = temp_db / "ValueBot"
    dest.mkdir()
    old = _backup_with_picks(dest, "picks_history-20260101-090000.db", 0)
    copy = backup.backup_db(dest, keep=keep)
    assert copy.exists() and not old.exists()


def test_connections_are_closed_after_use(temp_db):
    with db.connection.connect() as conn:
        conn.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_connect_commits_on_success_and_rolls_back_on_error(temp_db):
    with pytest.raises(RuntimeError):
        with db.connection.connect() as conn:
            conn.execute("DELETE FROM picks")
            raise RuntimeError("a mitad")
    assert _picks_in(db.connection.DB_PATH) == 1


def test_main_backs_up_even_if_sending_fails(temp_db, monkeypatch):
    from value_bot import main
    calls = []

    async def send_boom(_msg):
        raise RuntimeError("Telegram caído")
    monkeypatch.setattr("value_bot.sports_registry.SPORTS_ENABLED", {"football": False, "nba": False, "nfl": True})
    monkeypatch.setattr(main, "PICK_SOURCES", {"football": lambda c: [], "nba": lambda c: [], "nfl": lambda c: []})
    monkeypatch.setattr(main, "resolve_pending_picks", lambda: 0)
    monkeypatch.setattr(main, "send_telegram", send_boom)
    monkeypatch.setattr(main, "backup_db", lambda *a, **k: calls.append("backup"))
    with pytest.raises(RuntimeError):
        asyncio.run(main.main())
    with pytest.raises(RuntimeError):
        asyncio.run(main.weekly_report())
    assert calls == ["backup", "backup"]


def test_public_names_are_sorted():
    assert db.__all__ == sorted(db.__all__, key=str.lower)
