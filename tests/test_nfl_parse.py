import pytest

from value_bot.sports import nfl
from value_bot.sports.nfl import parse_espn_nfl_event, parse_team_scores


def _competitor(side, team_id, name, record="3-2"):
    return {
        "homeAway": side,
        "team": {"id": team_id, "displayName": name},
        "records": [{"summary": record}],
    }


def _event(status="STATUS_SCHEDULED", odds=None):
    return {
        "date": "2026-10-19T00:20Z",
        "competitions": [{
            "status": {"type": {"name": status}},
            "venue": {"fullName": "Lambeau Field"},
            "competitors": [
                _competitor("home", "9", "Green Bay Packers", "3-2"),
                _competitor("away", "6", "Dallas Cowboys", "4-1"),
            ],
            "odds": odds if odds is not None else [{
                "provider": {"displayName": "Draft Kings"},
                "overUnder": 53.5,
                "moneyline": {"home": {"close": {"odds": "-108"}}, "away": {"close": {"odds": "-112"}}},
                "pointSpread": {
                    "home": {"close": {"line": "+1.5", "odds": "-122"}},
                    "away": {"close": {"line": "-1.5", "odds": "+102"}},
                },
                "total": {
                    "over": {"close": {"line": "o53.5", "odds": "-112"}},
                    "under": {"close": {"line": "u53.5", "odds": "-108"}},
                },
            }],
        }],
    }


def test_parse_scheduled_event_with_market():
    game = parse_espn_nfl_event(_event())
    assert game["home"] == "Green Bay Packers" and game["home_id"] == "9"
    assert game["away"] == "Dallas Cowboys" and game["away_id"] == "6"
    assert game["home_record"] == "3-2" and game["venue"] == "Lambeau Field"
    m = game["lines"]
    assert m["provider"] == "Draft Kings"
    assert m["home_spread"] == 1.5
    assert m["total"] == 53.5
    assert m["home_ml"] == pytest.approx(1.926, abs=0.001)
    assert m["away_spread_odds"] == pytest.approx(2.02, abs=0.001)
    assert m["over_odds"] == pytest.approx(1.893, abs=0.001)


@pytest.mark.parametrize("status", ["STATUS_FINAL", "STATUS_IN_PROGRESS", "STATUS_HALFTIME"])
def test_parse_skips_games_not_scheduled(status):
    assert parse_espn_nfl_event(_event(status=status)) is None


def test_parse_event_without_odds_has_empty_market():
    game = parse_espn_nfl_event(_event(odds=[]))
    assert game["lines"] == {}


def test_parse_team_scores_only_completed_games_from_team_perspective():
    schedule = {"events": [
        {"competitions": [{"status": {"type": {"name": "STATUS_FINAL"}}, "competitors": [
            {"homeAway": "home", "team": {"id": "12"}, "score": {"value": 31.0}},
            {"homeAway": "away", "team": {"id": "7"}, "score": {"value": 10.0}},
        ]}]},
        {"competitions": [{"status": {"type": {"name": "STATUS_FINAL"}}, "competitors": [
            {"homeAway": "home", "team": {"id": "15"}, "score": "10"},
            {"homeAway": "away", "team": {"id": "12"}, "score": "24"},
        ]}]},
        {"competitions": [{"status": {"type": {"name": "STATUS_SCHEDULED"}}, "competitors": [
            {"homeAway": "home", "team": {"id": "12"}},
            {"homeAway": "away", "team": {"id": "3"}},
        ]}]},
    ]}
    assert parse_team_scores(schedule, "12") == [(31.0, 10.0), (24.0, 10.0)]


def test_parse_team_scores_returns_none_when_request_failed():
    assert parse_team_scores({}, "12") is None


def _patch_fetch(monkeypatch, by_season):
    nfl.store.nfl_scores_cache.clear()
    monkeypatch.setattr(nfl, "nfl_season", lambda offset=0: 2026 + offset)
    monkeypatch.setattr(nfl, "_fetch_scores", lambda team_id, season: by_season[season])


def test_get_team_scores_falls_back_to_previous_season_without_sample(monkeypatch):
    _patch_fetch(monkeypatch, {2026: [(20.0, 17.0)], 2025: [(24.0, 21.0)] * 17})
    assert len(nfl.get_team_scores("12")) == 17


def test_get_team_scores_does_not_fall_back_on_network_failure(monkeypatch):
    _patch_fetch(monkeypatch, {2026: None, 2025: [(24.0, 21.0)] * 17})
    assert nfl.get_team_scores("12") == []
