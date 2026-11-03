"""Fútbol: calibración, sin stats sin picks y dedup por partido y fecha."""
import sqlite3

import pytest

from value_bot import db
from value_bot.analysis import football_model
from value_bot.analysis import football_picks as picks_mod
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
    out = football_model.apply_calibration(pick, {70: 0.9})
    assert out["est_odds"] == 1.6
    assert out["value"] == pytest.approx(out["our_prob"] * 1.6 - 1, abs=0.0001)
    assert pick["confidence"] == 70  # no muta el original


def test_calibration_without_real_odds_recomputes_estimated_odds():
    pick = {"confidence": 70, "our_prob": 0.70, "est_odds": est_odds(0.70), "value": None}
    out = football_model.apply_calibration(pick, {70: 0.9})
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
    monkeypatch.setattr(picks_mod, "get_calibration_factors", lambda *a, **k: {})
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


# ── Picks anclados al mercado y un partido por corrida ────────────

def _patch_picks(monkeypatch, fixtures, picks_by_home, factors=None):
    monkeypatch.setattr(picks_mod, "collect_football_fixtures", lambda: fixtures)
    monkeypatch.setattr(picks_mod, "build_football_stats", lambda f: {})
    monkeypatch.setattr(picks_mod, "get_calibration_factors", lambda *a, **k: factors or {})
    monkeypatch.setattr(picks_mod, "analyze_football_match", lambda fix, h, a: picks_by_home[fix["home"]])


VALUE_BET = {"type": "Victoria Away 0", "confidence": 52, "our_prob": 0.524, "est_odds": 2.0,
             "value": 0.048, "data_quality": "high", "real_odds_source": True}


def test_value_bet_below_confidence_floor_survives(temp_db, monkeypatch):
    _patch_picks(monkeypatch, [_fix(0)], {"Home 0": [VALUE_BET]})
    assert [p["type"] for p in picks_mod.get_football_picks()] == ["Victoria Away 0"]


def test_market_anchored_pick_is_not_recalibrated(temp_db, monkeypatch):
    _patch_picks(monkeypatch, [_fix(0)], {"Home 0": [VALUE_BET]}, factors={50: 0.5})
    (pick,) = picks_mod.get_football_picks()
    assert (pick["our_prob"], pick["value"]) == (0.524, 0.048)


def test_match_with_a_pick_from_an_earlier_run_gets_no_new_pick_type(temp_db, monkeypatch):
    fixtures = [_fix(0)]
    btts = {"type": "Ambos Marcan (BTTS)", "confidence": 70, "our_prob": 0.7, "est_odds": 1.4,
            "value": None, "data_quality": "high"}
    _patch_picks(monkeypatch, fixtures, {"Home 0": [btts]})
    db.save_picks("2026-10-30", [{**fixtures[0], "type": "Over 2.5 Goles", "confidence": 70}])
    assert picks_mod.get_football_picks() == []
