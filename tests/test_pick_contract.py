"""Contrato del pick: campos que Telegram, save_picks, parlays y el resolver leen sin .get()."""
import logging

import pytest

from value_bot.analysis import football_picks, nba_picks, nfl_picks
from value_bot.analysis.pick import REQUIRED_FIELDS, keep_valid, missing_fields
from value_bot.sports.nfl import TeamScores


def _complete(**overrides):
    pick = {"sport": "nba", "league": "NBA", "home": "H", "away": "A", "type": "Victoria H",
            "confidence": 60, "est_odds": 1.9, "our_prob": 0.6, "value": 0.05,
            "reasoning": "r", "data_quality": "high", "real_odds_source": True}
    return {**pick, **overrides}


# ── missing_fields / keep_valid ───────────────────────────────────

def test_complete_pick_has_no_missing_fields():
    assert missing_fields(_complete()) == ()


def test_reports_each_missing_field_in_contract_order():
    pick = {k: v for k, v in _complete().items() if k not in ("reasoning", "est_odds")}
    assert missing_fields(pick) == ("est_odds", "reasoning")


def test_none_counts_as_missing():
    assert missing_fields(_complete(our_prob=None)) == ("our_prob",)


def test_pick_without_real_odds_breaks_the_contract():
    assert missing_fields(_complete(real_odds_source=False)) == ("real_odds_source",)


def test_zero_value_is_present():
    assert missing_fields(_complete(value=0.0)) == ()


def test_keep_valid_drops_and_logs_broken_picks(caplog):
    broken = {k: v for k, v in _complete(type="Over 211.5 Puntos").items() if k != "reasoning"}
    with caplog.at_level(logging.ERROR):
        kept = keep_valid([_complete(), broken], "nba")
    assert kept == [_complete()]
    assert "Over 211.5 Puntos" in caplog.text and "reasoning" in caplog.text


def test_keep_valid_does_not_mutate_input():
    picks = [_complete(), {"sport": "nba"}]
    keep_valid(picks, "nba")
    assert len(picks) == 2


# ── Lo que sale de cada deporte cumple el contrato ────────────────

def _assert_contract(picks):
    assert picks, "el escenario debe producir al menos un pick"
    for pick in picks:
        assert missing_fields(pick) == (), pick["type"]


def test_nfl_picks_meet_the_contract(monkeypatch):
    game = {"home": "Kansas City Chiefs", "away": "Las Vegas Raiders", "home_id": "12", "away_id": "13",
            "event_id": "evt-1", "kickoff": "2026-11-08T18:00Z",
            "lines": {"provider": "Draft Kings", "home_ml": 1.40, "away_ml": 3.10,
                      "home_spread": -6.5, "home_spread_odds": 1.91, "away_spread_odds": 1.91,
                      "total": 44.5, "over_odds": 1.91, "under_odds": 1.91}}
    scores = {"12": [(30.0, 14.0)] * 6, "13": [(14.0, 30.0)] * 6}
    monkeypatch.setattr(nfl_picks, "get_nfl_games", lambda: [game])
    monkeypatch.setattr(nfl_picks, "get_league_average", lambda: 22.0)
    monkeypatch.setattr(nfl_picks, "get_team_scores", lambda tid: TeamScores(scores[tid], current=True))
    monkeypatch.setattr(nfl_picks, "already_sent_bet", lambda *a, **k: False)
    _assert_contract(nfl_picks.get_nfl_picks())


def test_nba_picks_meet_the_contract(monkeypatch):
    game = {"home": "Charlotte Hornets", "away": "Philadelphia 76ers", "event_id": "401",
            "kickoff": "2026-11-03T23:00Z",
            "lines": {"home_ml": 2.1, "away_ml": 1.769, "home_spread": 2.5, "home_spread_odds": 1.87,
                      "away_spread_odds": 1.952, "total": 211.5, "over_odds": 1.87, "under_odds": 1.952}}
    strong = {"ppg": 124, "opp_ppg": 104, "season": nba_picks.nba_api_season(), "played": 20}
    weak = {"ppg": 104, "opp_ppg": 124, "season": nba_picks.nba_api_season(), "played": 20}
    monkeypatch.setattr(nba_picks, "get_nba_games", lambda: [game])
    monkeypatch.setattr(nba_picks, "get_best_nba_stats", lambda g: (strong, weak, {}))
    monkeypatch.setattr(nba_picks, "already_sent_bet", lambda *a, **k: False)
    monkeypatch.setattr(nba_picks, "nba_get_team_form", lambda name: {})
    _assert_contract(nba_picks.get_nba_picks())


def test_football_picks_meet_the_contract(monkeypatch):
    fix = {"sport": "football", "league": "Bundesliga", "flag": "🇩🇪", "home": "Home FC", "away": "Away FC",
           "date": "2026-11-08", "kickoff": "2026-11-08T14:30:00Z", "event_id": "fd:1", "odds_key": "k"}
    stats = {"home": {"goals_for_pg": 1.5, "goals_against_pg": 1.5, "win_rate": 10, "draw_rate": 25,
                      "form": "LLLLL", "form_pts": 0, "played": 10},
             "away": {"goals_for_pg": 1.5, "goals_against_pg": 1.5, "win_rate": 80, "draw_rate": 25,
                      "form": "WWWWW", "form_pts": 15, "played": 10}}
    monkeypatch.setattr(football_picks, "collect_football_fixtures", lambda: [fix])
    monkeypatch.setattr(football_picks, "build_football_stats", lambda f: {})
    monkeypatch.setattr(football_picks, "side_stats", lambda s, f, side: stats[side])
    monkeypatch.setattr(football_picks, "get_real_odds",
                        lambda *a, **k: {"home_odds": 4.0, "draw_odds": 3.6, "away_odds": 2.0})
    monkeypatch.setattr(football_picks, "already_sent_for_game", lambda *a, **k: False)
    _assert_contract(football_picks.get_football_picks())


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
def test_every_required_field_is_checked(field):
    assert missing_fields({k: v for k, v in _complete().items() if k != field}) == (field,)


def test_non_dict_items_are_dropped_without_losing_the_valid_picks(caplog):
    with caplog.at_level(logging.ERROR):
        assert keep_valid([_complete(), "roto", None], "nba") == [_complete()]
    assert caplog.text.count("descartado") == 2
