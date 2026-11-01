"""Review nba.py: api-sports validado, forma solo para mostrar, sin H2H ni fuentes sin líneas."""
import logging

import pytest
import requests

from value_bot.analysis import nba_picks
from value_bot.sports import nba
from value_bot.store import store

pytestmark = pytest.mark.skipif(not nba.NBA_API_OK, reason="nba_api no instalado")


@pytest.fixture(autouse=True)
def _clean_store():
    store.clear_run_caches()
    yield
    store.clear_run_caches()


# ── H1: api-sports con la misma validación que nba_api ────────────

def _apisports(played=10, ppg="112.5", opp="110.0", wins=6):
    return {"response": {"games": {"played": {"all": played}},
                         "points": {"for": {"average": {"all": ppg}}, "against": {"average": {"all": opp}}},
                         "wins": {"all": {"total": wins}}}}


def test_valid_api_sports_stats(monkeypatch):
    monkeypatch.setattr(nba, "api_sports_get", lambda *a, **k: _apisports())
    stats = nba.get_nba_team_stats_apisports(1)
    assert stats["ppg"] == 112.5 and stats["opp_ppg"] == 110.0 and stats["played"] == 10
    assert stats["source"] == "api-sports" and "season" not in stats


@pytest.mark.parametrize("body", [
    _apisports(ppg="1125"),                    # totales o dato corrupto
    _apisports(opp="11.0"),
    _apisports(played=3),                      # < NBA_MIN_GAMES
    _apisports(played=None),
    {"response": {"games": None}},
    {"response": {"games": {"played": {"all": 10}}, "points": None}},
    {"response": [{"x": 1}]},
    {"response": "error"},
    {},
])
def test_corrupt_or_unexpected_api_sports_stats_are_dropped(monkeypatch, body):
    monkeypatch.setattr(nba, "api_sports_get", lambda *a, **k: body)
    assert nba.get_nba_team_stats_apisports(1) == {}


def test_per_game_line_rejects_impossible_points_allowed():
    assert nba._per_game_line({"GP": 10, "W": 5, "PTS": 110.0, "PLUS_MINUS": 60.0}) is None


# ── M1: sin H2H; la forma (solo para mostrar) no corta nba_api ────

def test_h2h_is_gone_and_best_stats_returns_three_values(monkeypatch):
    assert not hasattr(nba, "nba_get_h2h_stats")
    monkeypatch.setattr(nba, "nba_get_team_full_stats", lambda name: {})
    monkeypatch.setattr(nba, "nba_get_team_form", lambda name: {})
    assert nba.get_best_nba_stats({"home": "Atlanta Hawks", "away": "Boston Celtics"}) == ({}, {}, {})


def test_form_timeout_does_not_cut_model_data(monkeypatch):
    built = []

    class Log:
        def __init__(self, **kwargs):
            built.append(kwargs)
            raise requests.exceptions.ReadTimeout("gamelog colgado")
    monkeypatch.setattr(nba.teamgamelog, "TeamGameLog", Log)
    monkeypatch.setattr(nba.time, "sleep", lambda _: None)

    assert nba.nba_get_team_form("Boston Celtics") == {}
    assert store.nba_api_down is False and store.nba_form_down is True
    assert nba.nba_get_team_form("Chicago Bulls") == {} and len(built) == 1


def test_form_circuit_resets_each_run():
    store.nba_form_down = True
    store.clear_run_caches()
    assert store.nba_form_down is False


def test_best_stats_do_not_request_the_display_form(monkeypatch):
    base = {"ppg": 115.0, "opp_ppg": 112.0, "source": "nba_api", "season": "2026-27"}
    monkeypatch.setattr(nba, "nba_get_team_full_stats", lambda name: dict(base))
    monkeypatch.setattr(nba, "nba_get_home_away_splits", lambda *a, **k: {})

    def boom(_):
        raise AssertionError("la forma no es dato del modelo")
    monkeypatch.setattr(nba, "nba_get_team_form", boom)
    nba.get_best_nba_stats({"home": "Atlanta Hawks", "away": "Boston Celtics"})


def test_form_is_fetched_only_for_games_with_picks(monkeypatch):
    games = [{"home": f"H{i}", "away": f"A{i}", "event_id": f"e{i}", "lines": {"home_ml": 1.8}} for i in range(3)]
    asked = []
    monkeypatch.setattr(nba_picks, "get_nba_games", lambda: games)
    monkeypatch.setattr(nba_picks, "analyze_nba_game", lambda g: [{
        "market": "moneyline", "type": f"Victoria {g['home']}", "confidence": 64, "value": 0.05}]
        if g["home"] == "H1" else [])
    monkeypatch.setattr(nba_picks, "already_sent_for_event", lambda *a, **k: False)
    monkeypatch.setattr(nba_picks, "nba_get_team_form", lambda name: asked.append(name) or {})
    picks = nba_picks.get_nba_picks()
    assert [p["home"] for p in picks] == ["H1"] and asked == ["H1", "A1"]


def test_full_stats_do_not_request_the_form(monkeypatch):
    hawks = nba._nba_find_team_id("Atlanta Hawks")
    monkeypatch.setattr(nba, "_nba_stat_sources", lambda: [("2026-27", "Regular Season")])
    monkeypatch.setattr(nba, "nba_get_league_stats",
                        lambda s, t: {hawks: {"ppg": 115.0, "opp_ppg": 112.0, "win_pct": 60.0, "gp": 8, "pm": 3.0}})

    def boom(_):
        raise AssertionError("la forma no es dato del modelo")
    monkeypatch.setattr(nba, "nba_get_team_form", boom)
    assert nba.nba_get_team_full_stats("Atlanta Hawks")["ppg"] == 115.0


# ── L2: splits solo con ambos equipos de nba_api ──────────────────

def test_no_splits_requested_when_one_team_comes_from_api_sports(monkeypatch):
    asked = []
    stats = {"Atlanta Hawks": {"ppg": 115.0, "opp_ppg": 112.0, "source": "nba_api", "season": "2026-27"},
             "Boston Celtics": {}}
    monkeypatch.setattr(nba, "nba_get_team_full_stats", lambda name: dict(stats[name]))
    monkeypatch.setattr(nba, "get_nba_team_stats_apisports",
                        lambda tid: {"ppg": 112.0, "opp_ppg": 110.0, "played": 10, "source": "api-sports"})
    monkeypatch.setattr(nba, "nba_get_home_away_splits", lambda *a, **k: asked.append(a) or {})
    monkeypatch.setattr(nba, "nba_get_team_form", lambda name: {})
    _, away, splits = nba.get_best_nba_stats({"home": "Atlanta Hawks", "away": "Boston Celtics", "away_id": 2})
    assert away["source"] == "api-sports" and splits == {} and asked == []


# ── M2 / L1: fuentes de juegos ────────────────────────────────────

@pytest.fixture
def feeds(monkeypatch):
    feed = {"api": [], "espn": [], "params": []}

    def api(base, endpoint, params):
        feed["params"].append(params)
        return {"response": feed["api"]}
    monkeypatch.setattr(nba, "api_sports_get", api)
    monkeypatch.setattr(nba, "espn_nba_games", lambda d: feed["espn"])
    monkeypatch.setattr(nba, "espn_parse_nba", lambda ev: ev)
    return feed


def test_without_espn_or_api_sports_there_are_no_games_and_it_says_why(feeds, caplog):
    assert not hasattr(nba, "_bbc_games") and not hasattr(nba, "_flashscore_games")
    with caplog.at_level(logging.WARNING):
        assert nba.get_nba_games() == []
    assert "sin juegos" in caplog.text


def test_api_sports_asks_eastern_dates_and_keeps_every_game(feeds):
    feeds["api"] = [{"teams": {"home": {"id": i + 1, "name": f"Home {i}"}, "away": {"id": 100 + i, "name": f"Away {i}"}},
                     "status": {"short": "NS"}, "date": "2026-11-01T23:00:00-04:00"} for i in range(12)]
    assert len(nba.get_nba_games()) == 12
    assert feeds["params"][0]["timezone"] == "America/New_York"


def test_only_games_with_lines_count_as_analyzed(monkeypatch):
    games = [{"home": "A", "away": "B", "lines": {"home_ml": 1.8}}, {"home": "C", "away": "D", "lines": {}}]
    monkeypatch.setattr(nba_picks, "get_nba_games", lambda: games)
    monkeypatch.setattr(nba_picks, "analyze_nba_game", lambda g: [])
    nba_picks.get_nba_picks()
    assert store.games_analyzed == {"nba": 1}
