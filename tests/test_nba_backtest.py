"""Backtest NBA: juegos finales, stats sin lookahead, replay como producción y reporte (sin red)."""
from datetime import datetime, timedelta, timezone

import pytest

from value_bot.analysis import nba_model
from value_bot.tools import backtest_report as report
from value_bot.tools import nba_backtest as bt

SEASON = "2025-26"
START = datetime(2025, 12, 1, tzinfo=timezone.utc)
LINES = {"home_ml": 2.1, "away_ml": 1.769, "home_spread": 2.5, "home_spread_odds": 1.87,
         "away_spread_odds": 1.952, "total": 221.5, "over_odds": 1.87, "under_odds": 1.952}


def _g(day, home_id, away_id, hs, as_, event_id=None):
    return {"event_id": event_id or f"{day}-{home_id}-{away_id}", "date": START + timedelta(days=day),
            "home_id": home_id, "away_id": away_id, "home": f"Team {home_id}", "away": f"Team {away_id}",
            "home_score": hs, "away_score": as_}


def _history(n=12):
    """Equipo 1 local en días pares y visitante en impares; siempre anota 115 y recibe 105."""
    games = []
    for day in range(n):
        if day % 2 == 0:
            games.append(_g(day, "1", "9", 115, 105))
        else:
            games.append(_g(day, "9", "1", 105, 115))
        games.append(_g(day, "2", "8", 108, 112) if day % 2 == 0 else _g(day, "8", "2", 112, 108))
    return games


# ── Scoreboard → juegos finales ───────────────────────────────────

def _event(status="STATUS_FINAL", season_type=2):
    return {"id": "401", "date": "2026-01-15T00:30Z", "season": {"type": season_type},
            "competitions": [{"status": {"type": {"name": status}}, "competitors": [
                {"homeAway": "home", "score": "112", "team": {"id": "5", "displayName": "Cleveland Cavaliers"}},
                {"homeAway": "away", "score": "104", "team": {"id": "20", "displayName": "Philadelphia 76ers"}},
            ]}]}


def test_parse_final_games_keeps_only_finished_regular_season():
    board = {"events": [_event(), _event(status="STATUS_SCHEDULED"), _event(season_type=3)]}
    games = bt.parse_final_games(board)
    assert len(games) == 1
    g = games[0]
    assert (g["home"], g["away"], g["home_score"], g["away_score"]) == (
        "Cleveland Cavaliers", "Philadelphia 76ers", 112.0, 104.0)
    assert (g["home_id"], g["away_id"], g["event_id"]) == ("5", "20", "401")
    assert g["date"] == datetime(2026, 1, 15, 0, 30, tzinfo=timezone.utc)


def test_parse_final_games_skips_malformed_events():
    broken = _event()
    broken["competitions"][0]["competitors"][0]["score"] = None
    assert bt.parse_final_games({"events": [broken, {"id": "x"}]}) == []


# ── Stats sin lookahead ───────────────────────────────────────────

def test_team_stats_use_only_games_before_kickoff():
    games = _history(6) + [_g(30, "1", "9", 150, 90)]  # el juego evaluado no cuenta
    stats = bt.team_stats_before(games, "1", START + timedelta(days=30))
    assert stats == {"ppg": 115.0, "opp_ppg": 105.0, "played": 6}


def test_team_stats_by_location_and_minimum_sample():
    games = _history(12)
    kickoff = START + timedelta(days=40)
    assert bt.team_stats_before(games, "1", kickoff, location="home")["played"] == 6
    assert bt.team_stats_before(games, "1", kickoff, location="away")["played"] == 6
    assert bt.team_stats_before(_history(4), "1", kickoff) is None  # < NBA_MIN_GAMES


# ── Replay ────────────────────────────────────────────────────────

def test_replay_skips_game_when_a_team_lacks_sample():
    target = _g(40, "1", "2", 110, 100)
    assert bt.replay_game(target, LINES, _history(4), SEASON) is None


def test_replay_matches_production_model_and_grades_picks(monkeypatch):
    monkeypatch.setattr(nba_model, "NBA_MAX_PICK_VALUE", 9.0)
    games = _history(12)
    target = _g(40, "1", "2", 120, 100)
    row = bt.replay_game(target, LINES, games, SEASON)

    h = {**bt.team_stats_before(games, "1", target["date"]), "season": SEASON}
    a = {**bt.team_stats_before(games, "2", target["date"]), "season": SEASON}
    splits = {"home": bt.team_stats_before(games, "1", target["date"], "home"),
              "away": bt.team_stats_before(games, "2", target["date"], "away")}
    expected = nba_model.analyze_nba_game({**target, "lines": LINES}, h, a, splits, SEASON,
                                          min_conf=62, value_threshold=0.04)
    assert [p["type"] for p in row["picks"]] == [p["type"] for p in expected]
    assert all(p["result"] in ("win", "loss", "void") for p in row["picks"])
    assert row["dq"] == "high" and row["splits"] is True


def test_replay_row_has_probabilities_and_outcomes_for_every_market():
    target = _g(40, "1", "2", 120, 100)
    row = bt.replay_game(target, LINES, _history(12), SEASON)
    assert row["ml"]["home_won"] is True
    assert row["spread"]["home_covered"] is True   # 20 + 2.5 > 0
    assert row["total"]["over"] is False           # 220 < 221.5
    for market in ("ml", "spread", "total"):
        assert 0 < row[market]["model"] < 1 and 0 < row[market]["market"] < 1
    assert row["actual_margin"] == 20 and row["actual_total"] == 220
    assert row["week"] == "2026-01"  # periodo del reporte = mes


def test_short_sample_gives_medium_quality_and_no_splits():
    games = _history(6)
    row = bt.replay_game(_g(40, "1", "2", 120, 100), LINES, games, SEASON)
    assert row["dq"] == "medium"
    assert row["splits"] is False  # 3 juegos de local/visita < NBA_MIN_GAMES


# ── Reporte ───────────────────────────────────────────────────────

def test_report_labels_period_and_shows_residual_sd():
    row = bt.replay_game(_g(40, "1", "2", 120, 100), LINES, _history(12), SEASON)
    text = report.render_report([row], period="mes", spread_gaps=bt.SPREAD_GAP_BUCKETS,
                                total_gaps=bt.TOTAL_GAP_BUCKETS)
    assert "Por mes:" in text or "PICKS" in text
    assert "SD residual margen" in text and "SD residual total" in text


def test_residual_sd():
    assert report.residual_sd([3.0, -3.0, 3.0, -3.0]) == pytest.approx(3.0)
