"""Backtest NFL: parseo de cierre, replay sin lookahead, calificación y métricas (sin red)."""
from datetime import datetime, timezone

import pytest

from value_bot.tools import backtest_report as report
from value_bot.tools import nfl_backtest as bt

KICKOFF = "2026-10-18T17:00Z"
PICKCENTER = {"pickcenter": [{
    "provider": {"name": "DraftKings"}, "details": "SEA -3", "overUnder": 40.5, "spread": 3.0,
    "overOdds": -115.0, "underOdds": -105.0,
    "awayTeamOdds": {"moneyLine": -155, "spreadOdds": 100.0},
    "homeTeamOdds": {"moneyLine": 130, "spreadOdds": -120.0},
}]}


def _dt(raw):
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _event(home_score="20", away_score="17", week=7):
    return {
        "id": "1", "date": KICKOFF, "week": {"number": week}, "season": {"type": 2},
        "competitions": [{"neutralSite": False, "competitors": [
            {"homeAway": "home", "score": home_score, "team": {"id": "7", "displayName": "Denver Broncos"}},
            {"homeAway": "away", "score": away_score, "team": {"id": "26", "displayName": "Seattle Seahawks"}},
        ]}],
    }


LINES = bt.parse_pickcenter(PICKCENTER)


# ── Parseo del cierre ─────────────────────────────────────────────

def test_parse_pickcenter_uses_home_perspective_and_decimal_odds():
    assert LINES["home_spread"] == 3.0  # SEA −3 de visitante = local +3
    assert LINES["home_ml"] == pytest.approx(2.30)
    assert LINES["away_ml"] == pytest.approx(1.645, abs=0.001)
    assert LINES["total"] == 40.5
    assert LINES["over_odds"] == pytest.approx(1.87, abs=0.001)
    assert bt.has_full_market(LINES)


def test_parse_pickcenter_without_data_is_empty():
    assert bt.parse_pickcenter({}) == {}
    assert not bt.has_full_market({})


# ── Calificación ──────────────────────────────────────────────────

GAME = {"home": "Denver Broncos", "away": "Seattle Seahawks", "lines": LINES}


@pytest.mark.parametrize("pick, home, away, expected", [
    ({"market": "moneyline", "side": "Denver Broncos"}, 20, 17, "win"),
    ({"market": "moneyline", "side": "Seattle Seahawks"}, 20, 17, "loss"),
    ({"market": "moneyline", "side": "Seattle Seahawks"}, 20, 20, "void"),
    # Spread: local +3
    ({"market": "spread", "side": "Seattle Seahawks"}, 20, 18, "loss"),  # local gana por 2
    ({"market": "spread", "side": "Seattle Seahawks"}, 14, 24, "win"),   # local pierde por 10
    ({"market": "spread", "side": "Denver Broncos"}, 17, 20, "void"),    # local pierde por 3
    ({"market": "total", "side": "Over"}, 24, 17, "win"),
    ({"market": "total", "side": "Under"}, 24, 17, "loss"),
])
def test_grade_pick(pick, home, away, expected):
    assert bt.grade(pick, GAME, home, away) == expected


# ── Replay sin lookahead ──────────────────────────────────────────

def _history(before_kickoff, after_kickoff=()):
    """Mismo historial para los 2 equipos: juegos antes y después del kickoff."""
    rows = [(_dt("2026-10-05T17:00Z"), pf, pa) for pf, pa in before_kickoff]
    rows += [(_dt("2026-10-25T17:00Z"), pf, pa) for pf, pa in after_kickoff]
    return {"7": rows, "26": rows}


def test_replay_ignores_games_after_kickoff():
    clean = bt.replay_game(_event(), LINES, _history([(24, 20)] * 4), {}, ["7", "26"])
    leaky = bt.replay_game(_event(), LINES, _history([(24, 20)] * 4, [(60, 0)] * 4), {}, ["7", "26"])
    assert clean["home_rating"] == leaky["home_rating"]
    assert clean["league_avg"] == leaky["league_avg"]


def test_replay_uses_previous_season_when_current_sample_is_short():
    prev = {"7": [(_dt("2025-12-01T17:00Z"), 30, 10)] * 10, "26": []}
    row = bt.replay_game(_event(), LINES, _history([(24, 20)]), prev, ["7", "26"])
    assert row["home_rating"]["current"] is False
    assert row["home_rating"]["previous_games"] == 10


def test_replay_applies_production_week_filter():
    row = bt.replay_game(_event(week=1), LINES, _history([(24, 20)] * 4), {}, ["7", "26"])
    assert row["pickable"] is False and row["picks"] == []


def test_replay_records_model_and_market_probabilities_for_every_market():
    row = bt.replay_game(_event(), LINES, _history([(24, 20)] * 4), {}, ["7", "26"])
    for market in ("ml", "spread", "total"):
        assert 0 < row[market]["model"] < 1 and 0 < row[market]["market"] < 1
    assert row["ml"]["home_won"] is True
    assert row["total"]["over"] is False  # 37 < 40.5


# ── Métricas ──────────────────────────────────────────────────────

def test_units_counts_profit_loss_and_void():
    assert report.units({"result": "win", "est_odds": 1.91}) == pytest.approx(0.91)
    assert report.units({"result": "loss", "est_odds": 1.91}) == -1
    assert report.units({"result": "void", "est_odds": 1.91}) == 0


def test_pick_summary():
    picks = [{"result": "win", "est_odds": 2.0, "value": 0.05, "confidence": 55},
             {"result": "loss", "est_odds": 2.0, "value": 0.07, "confidence": 55}]
    s = report.pick_summary(picks)
    assert (s["n"], s["wins"], s["losses"]) == (2, 1, 1)
    assert s["units"] == pytest.approx(0.0)
    assert s["promised_ev"] == pytest.approx(0.06)
    assert s["expected_wins"] == pytest.approx(1.1)


def test_brier_and_optimal_weight_prefer_the_better_source():
    # El mercado acierta siempre con 0.7; el modelo dice lo contrario
    data = [{"model": 0.3, "market": 0.7, "blend": 0.6, "y": True}] * 10
    m = report.probability_metrics(data, outcome="y")
    assert m["brier"]["market"] < m["brier"]["model"]
    assert m["optimal_model_weight"] == 0.0
    assert m["model_side_hits"] == 0
