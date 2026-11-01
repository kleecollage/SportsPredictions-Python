"""Parlays: piernas con cuota real y value, EV compuesto, sin solape, guardado y resolución."""
import sqlite3

import pytest

from value_bot import db, resolver
from value_bot.analysis import parlays as pl


def _leg(i, conf=70, value=0.05, odds=1.6, sport="nba", league="NBA", **extra):
    return {"sport": sport, "league": league, "home": f"H{i}", "away": f"A{i}", "event_id": f"e{i}",
            "type": f"Victoria H{i}", "market": "moneyline", "confidence": conf, "our_prob": conf / 100,
            "est_odds": odds, "value": value, "real_odds_source": True, "data_quality": "high",
            "reasoning": "Modelo.", **extra}


# ── Piernas elegibles ─────────────────────────────────────────────

def test_nba_legs_of_the_same_league_can_be_combined():
    parlays = pl.build_parlays([_leg(i, conf=64) for i in range(2)])
    assert len(parlays) == 1 and len(parlays[0]["legs"]) == 2


@pytest.mark.parametrize("change", [
    {"real_odds_source": False}, {"est_odds": None}, {"our_prob": None},
    {"value": None}, {"value": 0.01},          # sin value suficiente
    {"confidence": 60, "our_prob": 0.60},      # NBA/NFL < MIN_CONF_PARLAY
])
def test_leg_without_real_odds_value_or_confidence_is_not_eligible(change):
    assert not pl.is_eligible_leg({**_leg(0), **change})


def test_football_legs_are_left_out_until_football_is_resolved():
    assert not pl.is_eligible_leg(_leg(0, conf=80, sport="football", league="PL"))


def test_no_invented_odds_or_probability():
    legs = [{k: v for k, v in _leg(i).items() if k not in ("est_odds", "our_prob")} for i in range(2)]
    assert pl.build_parlays(legs) == []


# ── Estadísticas: EV compuesto y push ─────────────────────────────

def test_parlay_stats_compound_value_and_discount_push():
    legs = [_leg(0, odds=2.0, value=0.10, conf=55), _leg(1, odds=1.5, value=0.05, conf=70, push_prob=0.1)]
    stats = pl.parlay_stats(legs)
    assert stats["combined_odds"] == 3.0
    assert stats["combined_value"] == pytest.approx(1.10 * 1.05 - 1, abs=1e-4)
    assert stats["combined_prob"] == pytest.approx(0.55 * 0.70 * 0.9 * 100, abs=0.1)


# ── Selección ─────────────────────────────────────────────────────

def test_two_picks_of_the_same_game_never_share_a_parlay():
    same = [_leg(0), _leg(0, type="Over 211.5 Puntos", market="total")]
    assert pl.build_parlays(same) == []


def test_picks_the_combination_with_more_value():
    legs = [_leg(0, conf=75, value=0.04), _leg(1, conf=74, value=0.04), _leg(2, conf=64, value=0.09),
            _leg(3, conf=64, value=0.08)]
    double = pl.build_parlays(legs)[0]
    assert {leg["home"] for leg in double["legs"]} == {"H2", "H3"}


def test_combination_under_min_probability_is_skipped():
    assert len(pl.build_parlays([_leg(0, conf=62), _leg(1, conf=62)])) == 1  # 0.62² = 0.384
    assert pl.build_parlays([_leg(0, conf=62, push_prob=0.1), _leg(1, conf=62)]) == []


def test_triple_does_not_reuse_the_double_legs():
    result = pl.build_parlays([_leg(i) for i in range(5)])
    assert [len(p["legs"]) for p in result] == [2, 3]
    double, triple = ({leg["home"] for leg in p["legs"]} for p in result)
    assert not double & triple


def test_triple_needs_three_legs_left_after_the_double():
    assert [len(p["legs"]) for p in pl.build_parlays([_leg(i) for i in range(4)])] == [2]


def test_mixed_sport_label():
    legs = [_leg(0), _leg(1, sport="nfl", league="NFL")]
    assert "MIXTO" in pl.build_parlays(legs)[0]["label"]


# ── Liquidación pura ──────────────────────────────────────────────

@pytest.mark.parametrize("legs, expected", [
    ([("win", 2.0), ("win", 1.5)], ("win", 2.0)),
    ([("win", 2.0), ("loss", 1.5)], ("loss", -1.0)),
    ([("loss", 2.0), (None, 1.5)], ("loss", -1.0)),       # una perdida basta
    ([("win", 2.0), (None, 1.5)], None),                  # falta una pierna
    ([("win", 2.0), ("pending", 1.5)], None),
    ([("win", 2.0), ("void", 1.5)], ("win", 1.0)),        # el push se cae del parlay
    ([("void", 2.0), ("void", 1.5)], ("void", 0.0)),
])
def test_settle_parlay(legs, expected):
    assert pl.settle_parlay([{"outcome": o, "est_odds": odds} for o, odds in legs]) == expected


# ── Persistencia y resolución ─────────────────────────────────────

@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "picks.db")
    db.init_db()


def _saved_parlay(run_date="2026-10-31"):
    legs = [_leg(i, odds=2.0) for i in range(2)]
    db.save_picks(run_date, legs)
    parlay = pl.build_parlays(legs)
    assert db.save_parlays(run_date, parlay) == 1
    return {(p["home"]): p["id"] for p in db.get_unresolved_picks(("nba",))}


def test_saved_parlay_is_linked_to_its_leg_picks(temp_db):
    ids = _saved_parlay()
    [pending] = db.get_pending_parlays()
    assert sorted(leg["pick_id"] for leg in pending["legs"]) == sorted(ids.values())
    assert all(leg["outcome"] is None and leg["est_odds"] == 2.0 for leg in pending["legs"])


def test_same_parlay_is_not_saved_twice_on_the_same_day(temp_db):
    _saved_parlay()
    legs = [_leg(i, odds=2.0) for i in range(2)]
    assert db.save_parlays("2026-10-31", pl.build_parlays(legs)) == 0


def test_parlay_whose_legs_were_not_saved_is_skipped(temp_db):
    assert db.save_parlays("2026-10-31", pl.build_parlays([_leg(i) for i in range(2)])) == 0


def test_resolve_parlays_uses_leg_results(temp_db):
    ids = _saved_parlay()
    db.save_result(ids["H0"], "win", "1-0", 1.0)
    assert resolver.resolve_parlays() == 0                 # falta la otra pierna
    db.save_result(ids["H1"], "win", "1-0", 1.0)
    assert resolver.resolve_parlays() == 1
    assert db.get_pending_parlays() == []
    with sqlite3.connect(db.DB_PATH) as conn:
        assert conn.execute("SELECT outcome, profit_units FROM parlays").fetchone() == ("win", 3.0)


def test_parlay_summary_counts_resolved_parlays(temp_db):
    ids = _saved_parlay()
    db.save_result(ids["H0"], "loss", "0-1", -1.0)
    resolver.resolve_parlays()
    assert db.get_parlay_summary(days=7) == {"period_days": 7, "total": 1, "wins": 0, "profit": -1.0}


def test_parlay_summary_only_counts_active_sports_and_current_model(temp_db):
    ids = _saved_parlay("2026-10-31")
    db.save_result(ids["H0"], "loss", "0-1", -1.0)
    resolver.resolve_parlays()
    assert db.get_parlay_summary(days=7, sports=("nfl",)) == {}
    assert db.get_parlay_summary(days=7, sports=("nba",), model_since={"nba": "2026-11-01"}) == {}
    assert db.get_parlay_summary(days=7, sports=("nba",), model_since={"nba": "2026-10-31"})["total"] == 1


def test_parlay_with_a_missing_leg_pick_counts_that_leg_as_void(temp_db):
    ids = _saved_parlay()
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute("DELETE FROM picks WHERE id = ?", (ids["H1"],))
    db.save_result(ids["H0"], "win", "1-0", 1.0)
    assert resolver.resolve_parlays() == 1
    with sqlite3.connect(db.DB_PATH) as conn:
        assert conn.execute("SELECT outcome, profit_units FROM parlays").fetchone() == ("win", 1.0)


def test_parlay_still_open_after_the_lookback_is_voided(temp_db):
    from datetime import datetime, timezone
    _saved_parlay("2026-01-01")
    assert resolver.resolve_parlays(now=datetime(2026, 3, 1, tzinfo=timezone.utc)) == 0
    assert resolver.resolve_parlays(now=datetime(2026, 7, 1, tzinfo=timezone.utc)) == 1
    with sqlite3.connect(db.DB_PATH) as conn:
        assert conn.execute("SELECT outcome, profit_units FROM parlays").fetchone() == ("void", 0.0)


# ── main: guardar después de los picks, resolver aislado ───────────

def test_main_saves_parlays_linked_to_the_saved_picks(temp_db, monkeypatch):
    import asyncio
    from value_bot import main

    async def no_send(_msg):
        return None
    legs = [_leg(i) for i in range(2)]
    monkeypatch.setattr(main, "SPORTS_ENABLED", {"football": False, "nba": True, "nfl": False})
    monkeypatch.setattr(main, "PICK_SOURCES", {"football": list, "nba": lambda: legs, "nfl": list})
    monkeypatch.setattr(main, "resolve_pending_picks", lambda: 0)
    monkeypatch.setattr(main, "get_upcoming_nba_events", lambda: [])
    monkeypatch.setattr(main, "send_telegram", no_send)
    asyncio.run(main.main())

    [pending] = db.get_pending_parlays()
    saved = {p["id"] for p in db.get_unresolved_picks(("nba",))}
    assert {leg["pick_id"] for leg in pending["legs"]} == saved


def test_resolving_parlays_runs_even_if_picks_fail_and_never_raises(monkeypatch):
    from value_bot import main
    calls = []

    def boom():
        calls.append("picks")
        raise RuntimeError("ESPN caído")

    def parlays_boom():
        calls.append("parlays")
        raise RuntimeError("BD")
    monkeypatch.setattr(main, "resolve_pending_picks", boom)
    monkeypatch.setattr(main, "resolve_parlays", parlays_boom)
    main._resolve_results()
    assert calls == ["picks", "parlays"]


# ── Telegram ──────────────────────────────────────────────────────

def test_message_shows_parlay_ev_and_compounding_warning():
    from value_bot import telegram_sender as ts
    parlays = pl.build_parlays([_leg(i) for i in range(2)])
    msg = ts.build_message([], [_leg(i) for i in range(2)], parlays)
    assert "EV modelo: <b>+10.2%</b>" in msg
    assert "multiplica" in msg


def test_weekly_report_shows_parlay_results():
    from value_bot import telegram_sender as ts
    msg = ts.build_weekly_report({"error": "x"}, [], [],
                                 parlays={"period_days": 14, "total": 3, "wins": 1, "profit": -0.4})
    assert "Parlays (resueltos en 14 días)" in msg and "1-2" in msg and "-0.40u" in msg
    assert "Parlays" not in ts.build_weekly_report({"error": "x"}, [], [], parlays={})


def test_failing_to_save_parlays_does_not_break_the_run(temp_db, monkeypatch, caplog):
    import asyncio
    from value_bot import main

    async def no_send(_msg):
        return None

    def boom(*_a):
        raise sqlite3.OperationalError("database is locked")
    legs = [_leg(i) for i in range(2)]
    monkeypatch.setattr(main, "SPORTS_ENABLED", {"football": False, "nba": True, "nfl": False})
    monkeypatch.setattr(main, "PICK_SOURCES", {"football": list, "nba": lambda: legs, "nfl": list})
    monkeypatch.setattr(main, "resolve_pending_picks", lambda: 0)
    monkeypatch.setattr(main, "get_upcoming_nba_events", lambda: [])
    monkeypatch.setattr(main, "send_telegram", no_send)
    monkeypatch.setattr(main, "save_parlays", boom)
    asyncio.run(main.main())
    assert len(db.get_unresolved_picks(("nba",))) == 2
    assert "Guardar parlays falló" in caplog.text
