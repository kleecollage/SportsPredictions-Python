"""Tabla results: índice UNIQUE por pick_id (upsert) y lectura de picks sin resolver."""
import sqlite3

import pytest

from value_bot import db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "picks.db")
    db.init_db()


def _pick(home="Detroit Pistons", away="Boston Celtics", pick_type="Under 211.1 Puntos",
          sport="nba", **extra):
    return {"sport": sport, "league": "NBA", "home": home, "away": away, "type": pick_type,
            "confidence": 63, "est_odds": 1.51, "our_prob": 0.63, "data_quality": "medium",
            "kickoff": "2026-10-20T19:00Z", **extra}


def _results():
    with sqlite3.connect(db.DB_PATH) as conn:
        return conn.execute("SELECT pick_id, outcome, profit_units FROM results").fetchall()


def test_save_result_twice_updates_the_same_row(temp_db):
    db.save_picks("2026-10-18", [_pick()])
    pick_id = db.get_unresolved_picks(("nba",))[0]["id"]

    db.save_result(pick_id, "loss", "100-99", -1.0)
    db.save_result(pick_id, "win", "100-99", 0.51)

    assert _results() == [(pick_id, "win", 0.51)]


def test_init_db_removes_duplicate_results_before_creating_unique_index(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    with sqlite3.connect(path) as conn:  # esquema anterior, sin índice UNIQUE
        conn.execute("CREATE TABLE results (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "pick_id INTEGER NOT NULL, outcome TEXT, actual_score TEXT, "
                     "profit_units REAL, resolved_at TEXT, source TEXT)")
        conn.executemany("INSERT INTO results (pick_id, outcome) VALUES (?, ?)",
                         [(1, "pending"), (1, "win"), (2, "loss")])

    db.init_db()

    assert sorted((r[0], r[1]) for r in _results()) == [(1, "win"), (2, "loss")]
    db.save_result(1, "loss", "1-0", -1.0)  # el upsert ya no falla
    assert sorted((r[0], r[1]) for r in _results()) == [(1, "loss"), (2, "loss")]


def test_unresolved_picks_include_payload_and_skip_resolved(temp_db):
    db.save_picks("2026-10-18", [_pick(), _pick(pick_type="Over 215.8 Puntos")])
    first, second = db.get_unresolved_picks(("nba",))
    db.save_result(first["id"], "win", "110-100", 0.51)

    pending = db.get_unresolved_picks(("nba",))

    assert [p["id"] for p in pending] == [second["id"]]
    assert pending[0]["payload"]["kickoff"] == "2026-10-20T19:00Z"
    assert pending[0]["pick_type"] == "Over 215.8 Puntos"


def test_pending_outcome_counts_as_unresolved(temp_db):
    db.save_picks("2026-10-18", [_pick()])
    pick_id = db.get_unresolved_picks(("nba",))[0]["id"]
    db.save_result(pick_id, "pending", None, None)

    assert [p["id"] for p in db.get_unresolved_picks(("nba",))] == [pick_id]


def test_unresolved_picks_filter_by_sport(temp_db):
    db.save_picks("2026-10-18", [_pick(sport="football"), _pick(sport="nfl", pick_type="X")])

    assert [p["sport"] for p in db.get_unresolved_picks(("nba", "nfl"))] == ["nfl"]


# ── Rendimiento: solo deportes activos y modelo actual ────────────

def _resolved(run_date, sport, outcome, profit):
    db.save_picks(run_date, [_pick(sport=sport, home=f"{sport} {run_date} {outcome}")])
    pick_id = max(p["id"] for p in db.get_unresolved_picks((sport,)))
    db.save_result(pick_id, outcome, "1-0", profit)


@pytest.fixture(params=[True, False], ids=["pandas", "sql"])
def mixed_history(request, temp_db, monkeypatch):
    from datetime import date, timedelta
    monkeypatch.setattr(db, "_PANDAS_OK", request.param and db._PANDAS_OK)
    today, old = date.today(), date.today() - timedelta(days=3)
    _resolved(old.isoformat(), "nfl", "loss", -1.0)      # modelo NFL anterior
    _resolved(today.isoformat(), "nfl", "win", 1.8)      # modelo NFL actual
    _resolved(today.isoformat(), "nba", "loss", -1.0)    # deporte apagado
    return today.isoformat()


def test_roi_summary_without_filters_counts_everything(mixed_history):
    assert db.get_roi_summary(days=7)["total_picks"] == 3


def test_roi_summary_only_counts_active_sports_and_current_model(mixed_history):
    roi = db.get_roi_summary(days=7, sports=("nfl",), model_since={"nfl": mixed_history})
    assert roi["total_picks"] == 1 and roi["win_rate"] == 100.0
    assert roi["total_profit"] == 1.8 and set(roi["by_sport"]) == {"nfl"}


def test_roi_summary_without_current_model_picks_reports_no_data(mixed_history):
    roi = db.get_roi_summary(days=7, sports=("football",))
    assert roi.get("error")


# ── Reporte semanal: picks recientes y calibración del modelo actual (code review M2) ─

def test_recent_picks_only_from_active_sports_and_current_model(mixed_history):
    assert len(db.get_recent_picks(days=7)) == 3
    recent = db.get_recent_picks(days=7, sports=("nfl",), model_since={"nfl": mixed_history})
    assert [(r["sport"], r["outcome"]) for r in recent] == [("nfl", "win")]


def test_calibration_only_from_active_sports_and_current_model(mixed_history):
    assert sum(c["total_picks"] for c in db.get_model_calibration(min_picks=1)) == 3
    calib = db.get_model_calibration(min_picks=1, sports=("nfl",), model_since={"nfl": mixed_history})
    assert sum(c["total_picks"] for c in calib) == 1
