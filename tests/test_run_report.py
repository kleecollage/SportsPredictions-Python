"""Corrida 31-oct-2026: mensaje sin picks, juegos NBA ya empezados y ventana de rendimiento."""
import sqlite3
from datetime import date, timedelta

import pytest

from value_bot import db
from value_bot import telegram_sender as ts
from value_bot.analysis import nba_picks
from value_bot.scrapers import espn
from value_bot.sports import nba
from value_bot.store import store


# ── Mensaje sin picks: distinguir "sin juegos" de "ningún pick con value" ─

def test_no_picks_with_analyzed_games_says_none_passed_the_filters():
    msg = ts.build_message([], [], [], analyzed={"nba": 4, "nfl": 13})
    assert "Sin partidos disponibles" not in msg and "No se encontraron fixtures" not in msg
    assert "🏀 4 juegos" in msg and "🏈 13 juegos" in msg
    assert "ningún pick" in msg.lower()


def test_no_picks_and_no_games_keeps_the_no_fixtures_message():
    for analyzed in (None, {}, {"nba": 0, "nfl": 0}):
        assert "Sin partidos disponibles" in ts.build_message([], [], [], analyzed=analyzed)


def test_analyzed_count_uses_singular():
    assert "🏈 1 juego" in ts.build_message([], [], [], analyzed={"nfl": 1})


def test_nba_picks_records_how_many_games_were_analyzed(monkeypatch):
    store.clear_run_caches()
    monkeypatch.setattr(nba_picks, "get_nba_games", lambda: [{"home": "A", "away": "B"}] * 3)
    monkeypatch.setattr(nba_picks, "analyze_nba_game", lambda g: [])
    nba_picks.get_nba_picks()
    assert store.games_analyzed == {"nba": 3}
    store.clear_run_caches()
    assert store.games_analyzed == {}


# ── NBA: solo juegos que no han empezado ──────────────────────────

def _espn_event(status):
    return {"id": "401", "date": "2026-10-31T22:00Z", "competitions": [{
        "status": {"type": {"name": status}},
        "competitors": [
            {"homeAway": "home", "team": {"displayName": "Charlotte Hornets"}},
            {"homeAway": "away", "team": {"displayName": "Philadelphia 76ers"}},
        ],
    }]}


@pytest.mark.parametrize("status, kept", [
    ("STATUS_SCHEDULED", True),
    ("STATUS_HALFTIME", False),
    ("STATUS_IN_PROGRESS", False),
    ("STATUS_END_PERIOD", False),
    ("STATUS_FINAL", False),
    ("STATUS_POSTPONED", False),
])
def test_espn_nba_only_scheduled_games(status, kept):
    assert (espn.espn_parse_nba(_espn_event(status)) is not None) is kept


@pytest.mark.parametrize("short, kept", [("NS", True), ("Q2", False), ("HT", False), ("FT", False),
                                         ("CANC", False), ("POST", False)])
def test_api_sports_nba_only_not_started_games(monkeypatch, short, kept):
    game = {"teams": {"home": {"id": 1, "name": "Charlotte Hornets"},
                      "away": {"id": 2, "name": "Philadelphia 76ers"}},
            "status": {"short": short}, "date": "2026-10-31T22:00:00+00:00"}
    monkeypatch.setattr(nba, "api_sports_get", lambda *a, **k: {"response": [game]})
    assert bool(nba._api_sports_games("2026-10-31")) is kept


# ── Rendimiento: ventana por fecha de resolución ──────────────────

@pytest.fixture(params=[True, False], ids=["pandas", "sql"])
def temp_db(request, tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "picks.db")
    monkeypatch.setattr(db, "_PANDAS_OK", request.param and db._PANDAS_OK)
    db.init_db()


def _resolved(run_date, outcome, profit, resolved_days_ago=0):
    db.save_picks(run_date, [{"sport": "nfl", "league": "NFL", "home": f"H {run_date} {outcome}", "away": "A",
                              "type": "Victoria H", "confidence": 55, "est_odds": 2.0, "our_prob": 0.55,
                              "data_quality": "high"}])
    pick_id = max(p["id"] for p in db.get_unresolved_picks(("nfl",)))
    db.save_result(pick_id, outcome, "1-0", profit)
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute("UPDATE results SET resolved_at = datetime('now', ?) WHERE pick_id = ?",
                     (f"-{resolved_days_ago} days", pick_id))


def test_pick_sent_long_ago_but_resolved_today_counts(temp_db):
    _resolved((date.today() - timedelta(days=9)).isoformat(), "loss", -1.0)
    roi = db.get_roi_summary(days=7, sports=("nfl",), model_since={"nfl": "2026-01-01"})
    assert roi["total_picks"] == 1 and roi["total_profit"] == -1.0


def test_pick_resolved_before_the_window_does_not_count(temp_db):
    _resolved((date.today() - timedelta(days=9)).isoformat(), "loss", -1.0, resolved_days_ago=8)
    assert db.get_roi_summary(days=7).get("error")


def test_model_since_still_filters_by_send_date(temp_db):
    _resolved((date.today() - timedelta(days=9)).isoformat(), "loss", -1.0)
    since = (date.today() - timedelta(days=5)).isoformat()
    assert db.get_roi_summary(days=7, sports=("nfl",), model_since={"nfl": since}).get("error")


def test_performance_labels_say_resolved():
    perf = {"period_days": 7, "total_picks": 6, "win_rate": 50.0, "roi_pct": -5.0, "total_profit": -0.3,
            "by_sport": {"nfl": {"total": 6, "wins": 3, "win_rate": 50.0, "profit": -0.3}}, "by_quality": {}}
    assert "RENDIMIENTO (resueltos en 7 días · modelo actual)" in ts.build_message([], [], [], perf=perf)
    assert "Resumen general (resueltos en 7 días · modelo actual)" in ts.build_weekly_report(perf, [], [])
