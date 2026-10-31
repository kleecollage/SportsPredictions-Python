"""Fútbol y calibración (review picks.py: M4, L3, L4)."""
import sqlite3

import pytest

from value_bot import db
from value_bot.analysis import picks as picks_mod
from value_bot.config import est_odds


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "picks.db")
    db.init_db()


def _resolved(sport, run_date, n, wins, confidence=70):
    picks = [{"sport": sport, "league": "L", "home": f"H{sport}{run_date}{i}", "away": "A", "type": "Over 2.5 Goles",
              "confidence": confidence, "est_odds": 1.8, "our_prob": confidence / 100, "data_quality": "high"}
             for i in range(n)]
    db.save_picks(run_date, picks)
    with sqlite3.connect(db.DB_PATH) as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM picks WHERE sport=? AND run_date=? ORDER BY id",
                                          (sport, run_date))]
    for i, pick_id in enumerate(ids):
        db.save_result(pick_id, "win" if i < wins else "loss", "", None)


# ── M4: calibración por deporte y modelo vigente ─────────────────

def test_calibration_ignores_other_sports(temp_db):
    _resolved("nfl", "2026-10-30", 60, 20)
    assert db.get_calibration_factors("football") == {}
    assert db.get_calibration_factors("nfl")[70] == pytest.approx(20 / 60 * 100 / 72, abs=0.001)


def test_calibration_ignores_picks_before_model_since(temp_db):
    _resolved("football", "2026-10-01", 60, 20)
    assert db.get_calibration_factors("football", model_since="2026-10-15") == {}
    assert 70 in db.get_calibration_factors("football", model_since="2026-09-01")


def test_calibration_with_real_odds_recomputes_value_not_odds():
    pick = {"confidence": 70, "our_prob": 0.70, "est_odds": 1.6, "value": 0.12, "real_odds_source": True}
    out = picks_mod._apply_calibration(pick, {70: 0.9})
    assert out["est_odds"] == 1.6
    assert out["value"] == pytest.approx(out["our_prob"] * 1.6 - 1, abs=0.0001)
    assert pick["confidence"] == 70  # no muta el original


def test_calibration_without_real_odds_recomputes_estimated_odds():
    pick = {"confidence": 70, "our_prob": 0.70, "est_odds": est_odds(0.70), "value": None}
    out = picks_mod._apply_calibration(pick, {70: 0.9})
    assert out["est_odds"] == est_odds(out["our_prob"])
    assert out["value"] is None


# ── L4: sin stats no hay picks; dedup por partido y fecha ─────────

def test_match_without_team_stats_produces_no_picks():
    fix = {"home": "A", "away": "B", "league": "Premier League"}
    assert picks_mod.analyze_football_match(fix, None, {"goals_for_pg": 1.5}) == []
    assert picks_mod.analyze_football_match(fix, {}, {}) == []


def _fix(i, date="2026-11-01"):
    return {"sport": "football", "league": f"Liga {i}", "home": f"Home {i}", "away": f"Away {i}", "date": date}


def _patch(monkeypatch, fixtures, conf_by_home):
    monkeypatch.setattr(picks_mod, "collect_football_fixtures", lambda: fixtures)
    monkeypatch.setattr(picks_mod, "build_football_stats", lambda f: {})
    monkeypatch.setattr(picks_mod, "_get_cal", lambda *a, **k: {})
    monkeypatch.setattr(picks_mod, "analyze_football_match", lambda fix, h, a: [{
        "type": "Over 2.5 Goles", "confidence": conf_by_home[fix["home"]], "our_prob": 0.7,
        "est_odds": 1.6, "value": None, "data_quality": "high"}])


def test_pick_sent_on_an_earlier_run_for_the_same_match_is_blocked(temp_db, monkeypatch):
    fixtures = [_fix(0)]
    _patch(monkeypatch, fixtures, {"Home 0": 70})
    db.save_picks("2026-10-30", [{**fixtures[0], "type": "Over 2.5 Goles", "confidence": 70}])
    assert picks_mod.get_football_picks() == []


def test_same_teams_on_another_date_are_not_blocked(temp_db, monkeypatch):
    fixtures = [_fix(0, date="2026-11-08")]
    _patch(monkeypatch, fixtures, {"Home 0": 70})
    db.save_picks("2026-10-30", [{**_fix(0), "type": "Over 2.5 Goles", "confidence": 70}])
    assert len(picks_mod.get_football_picks()) == 1


def test_db_dedup_runs_before_the_per_sport_cap(temp_db, monkeypatch):
    cap = picks_mod.MAX_PICKS_SPORT
    fixtures = [_fix(i) for i in range(cap + 2)]
    _patch(monkeypatch, fixtures, {f"Home {i}": 90 - i for i in range(cap + 2)})
    db.save_picks("2026-10-30", [{**fixtures[0], "type": "Over 2.5 Goles", "confidence": 90}])
    picks = picks_mod.get_football_picks()
    assert len(picks) == cap
    assert "Home 0" not in [p["home"] for p in picks]
