"""Runner de launchd: decide en hora ET qué toca con el historial de la BD (la Mac no siempre está prendida)."""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from value_bot import db, runner

ET = ZoneInfo("America/New_York")
MON = datetime(2026, 11, 2, tzinfo=ET)    # lunes
WED = datetime(2026, 11, 4, tzinfo=ET)


def at(day: datetime, hour: int, minute: int = 0) -> datetime:
    return day.replace(hour=hour, minute=minute)


# ── Puro: qué toca ────────────────────────────────────────────────

def test_daily_is_not_due_before_nine_et():
    assert not runner.is_due("daily", at(WED, 8, 59), [])


def test_daily_is_due_after_nine_et_with_no_run_today():
    assert runner.is_due("daily", at(WED, 9), [])
    assert runner.is_due("daily", at(WED, 22), [(at(WED - timedelta(days=1), 9), "ok")])


def test_daily_that_already_ran_today_is_not_due():
    assert not runner.is_due("daily", at(WED, 15), [(at(WED, 9, 10), "ok")])


def test_failed_attempts_are_retried_up_to_the_limit():
    failed = [(at(WED, 9 + i), "failed") for i in range(runner.MAX_ATTEMPTS - 1)]
    assert runner.is_due("daily", at(WED, 12), failed)
    failed.append((at(WED, 11, 30), "failed"))
    assert not runner.is_due("daily", at(WED, 12), failed)


def test_a_started_run_that_never_finished_counts_as_an_attempt():
    started = [(at(WED, 9 + i), "started") for i in range(runner.MAX_ATTEMPTS)]
    assert not runner.is_due("daily", at(WED, 13), started)


def test_weekly_is_due_from_monday_9_05_et_until_it_runs():
    assert not runner.is_due("weekly", at(MON, 9, 4), [])
    assert runner.is_due("weekly", at(MON, 9, 5), [])
    # Mac apagada lunes y martes: el miércoles se recupera el de esta semana
    assert runner.is_due("weekly", at(WED, 10), [(at(MON - timedelta(days=7), 9, 6), "ok")])
    assert not runner.is_due("weekly", at(WED, 10), [(at(MON, 18), "ok")])


def test_time_is_compared_in_et_whatever_the_mac_timezone():
    cst = ZoneInfo("America/Mexico_City")
    assert not runner.is_due("daily", datetime(2026, 11, 4, 7, 30, tzinfo=cst), [])   # 8:30 ET
    assert runner.is_due("daily", datetime(2026, 11, 4, 8, 0, tzinfo=cst), [])        # 9:00 ET


# ── Con BD: registra cada intento y corre lo que toca ─────────────

@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db.connection, "DB_PATH", tmp_path / "picks.db")
    db.init_db()


def _jobs(monkeypatch, fail=()):
    ran = []

    def job(kind):
        async def run():
            ran.append(kind)
            if kind in fail:
                raise RuntimeError(f"{kind} reventó")
        return run
    monkeypatch.setattr(runner, "JOBS", {"daily": job("daily"), "weekly": job("weekly")})
    return ran


def test_run_due_runs_each_due_job_once_and_records_it(temp_db, monkeypatch):
    ran = _jobs(monkeypatch)
    asyncio.run(runner.run_due(at(MON, 10)))
    asyncio.run(runner.run_due(at(MON, 11)))
    assert ran == ["daily", "weekly"]
    assert [status for _, status in db.get_runs("daily", since=at(MON, 0))] == ["ok"]


def test_a_failing_job_is_recorded_and_does_not_stop_the_other(temp_db, monkeypatch, caplog):
    ran = _jobs(monkeypatch, fail={"daily"})
    asyncio.run(runner.run_due(at(MON, 10)))
    assert ran == ["daily", "weekly"]
    assert [s for _, s in db.get_runs("daily", since=at(MON, 0))] == ["failed"]
    assert "daily reventó" in caplog.text


def test_nothing_due_runs_nothing(temp_db, monkeypatch):
    ran = _jobs(monkeypatch)
    db.finish_run(db.start_run("weekly", at(MON, 9, 10)), ok=True)
    asyncio.run(runner.run_due(at(WED, 8)))   # antes de las 9 y con el semanal ya enviado
    assert ran == []


def test_runs_are_kept_in_the_picks_db(temp_db):
    run_id = db.start_run("daily", at(WED, 9))
    db.finish_run(run_id, ok=True)
    (when, status), = db.get_runs("daily", since=at(WED, 0))
    assert status == "ok" and when == at(WED, 9)


# ── Instalación ───────────────────────────────────────────────────

def test_launchd_agent_runs_the_runner_periodically_and_at_load():
    import plistlib
    from pathlib import Path
    plist = plistlib.loads((Path(runner.__file__).parent / "runner.plist").read_bytes())
    assert plist["ProgramArguments"][-2:] == ["-m", "value_bot.runner"]
    assert plist["RunAtLoad"] is True and 0 < plist["StartInterval"] <= 1800
    assert "KeepAlive" not in plist


def test_apscheduler_is_gone():
    import importlib.util
    from pathlib import Path
    assert importlib.util.find_spec("value_bot.scheduler") is None
    reqs = (Path(__file__).parent.parent / "requirements.txt").read_text().lower()
    assert "apscheduler" not in reqs and "sqlalchemy" not in reqs


def test_a_run_by_hand_is_recorded_so_the_runner_does_not_repeat_it(temp_db, monkeypatch):
    from value_bot import main
    ran = []

    async def fake_main(argv=None):
        ran.append(("daily", argv))

    async def fake_weekly():
        ran.append(("weekly", None))
    monkeypatch.setattr(main, "main", fake_main)
    monkeypatch.setattr(main, "weekly_report", fake_weekly)
    main._run_by_hand(["--btts"])
    main._run_by_hand(["--weekly"])
    assert ran == [("daily", ["--btts"]), ("weekly", None)]
    now = datetime.now(ET)
    assert [s for _, s in db.get_runs("daily", since=now - timedelta(hours=1))] == ["ok"]
    assert [s for _, s in db.get_runs("weekly", since=now - timedelta(hours=1))] == ["ok"]


# ── Revisión: sin corridas duplicadas ─────────────────────────────

def test_a_run_in_progress_blocks_another_one():
    started = [(at(WED, 9, 55), "started")]
    assert not runner.is_due("daily", at(WED, 10), started)


def test_a_started_run_older_than_the_stale_limit_is_retried():
    stale = at(WED, 10) - timedelta(minutes=runner.RUN_STALE_MINUTES + 1)
    assert runner.is_due("daily", at(WED, 10), [(stale, "started")])


def test_a_run_by_hand_before_nine_et_counts_for_the_day():
    assert not runner.is_due("daily", at(WED, 9, 30), [(at(WED, 8), "ok")])
    assert runner.is_due("daily", at(WED, 9, 30), [(at(WED, 8) - timedelta(days=1), "ok")])


def test_a_weekly_by_hand_early_on_monday_counts_for_the_week():
    assert not runner.is_due("weekly", at(MON, 10), [(at(MON, 7), "ok")])


def test_failing_to_save_after_sending_does_not_fail_the_run(temp_db, monkeypatch, caplog):
    from value_bot import main

    async def no_send(_msg):
        return None

    def save_boom(*_a):
        raise RuntimeError("database is locked")
    monkeypatch.setattr("value_bot.sports_registry.SPORTS_ENABLED", {"football": False, "nba": False, "nfl": True})
    monkeypatch.setattr(main, "PICK_SOURCES", {"football": lambda c: [], "nba": lambda c: [], "nfl": lambda c: []})
    monkeypatch.setattr(main, "resolve_pending_picks", lambda: 0)
    monkeypatch.setattr(main, "send_telegram", no_send)
    monkeypatch.setattr(main, "save_picks", save_boom)
    asyncio.run(main.main())          # no sube: el mensaje ya salió
    assert "Guardar picks falló" in caplog.text


def test_runs_store_times_to_the_second(temp_db):
    db.start_run("daily", at(WED, 9).replace(second=7, microsecond=123456))
    with db.connection.connect() as conn:
        assert conn.execute("SELECT run_at FROM runs").fetchone() == ("2026-11-04T14:00:07+00:00",)


def test_a_failed_run_by_hand_exits_with_error(temp_db, monkeypatch):
    from value_bot import main

    async def boom(argv=None):
        raise RuntimeError("x")
    monkeypatch.setattr(main, "main", boom)
    with pytest.raises(SystemExit) as exit_info:
        main._run_by_hand([])
    assert exit_info.value.code == 1
