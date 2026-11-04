"""Ligas de fútbol: Premier, LaLiga, Champions y Europa League, con y sin football-data.org."""
import logging
from datetime import datetime

import pytest

from value_bot import config
from value_bot.sports import football

REQUIRED = {
    # liga: (código football-data o None, id api-football, clave The Odds API)
    "Premier League":   ("PL", 39, "soccer_epl"),
    "LaLiga":           ("PD", 140, "soccer_spain_la_liga"),
    "Champions League": ("CL", 2, "soccer_uefa_champs_league"),
    "Europa League":    (None, 3, "soccer_uefa_europa_league"),   # no está en el plan free de football-data
}


@pytest.mark.parametrize("league, ids", REQUIRED.items())
def test_required_leagues_are_top_leagues(league, ids):
    info = config.TOP_LEAGUES_FOOTBALL[league]
    assert (info.get("fd"), info["afl"], info["odds"]) == ids
    assert league in config.LEAGUE_AVERAGES


@pytest.mark.parametrize("league", REQUIRED)
def test_season_in_november_is_the_one_that_started_in_summer(monkeypatch, league):
    class Nov2026(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 2, 9, 0)
    monkeypatch.setattr(config, "datetime", Nov2026)
    assert config.afl_season(config.TOP_LEAGUES_FOOTBALL[league]) == 2026


def _fd_match(code):
    return {"homeTeam": {"id": 1, "name": f"{code} Home"}, "awayTeam": {"id": 2, "name": f"{code} Away"},
            "utcDate": "2026-11-05T20:00:00Z"}


def _afl_match(lid):
    return {"fixture": {"date": "2026-11-06T20:00:00+00:00", "status": {"short": "NS"}},
            "teams": {"home": {"id": 10 + lid, "name": f"AFL{lid} Home"}, "away": {"id": 20 + lid, "name": f"AFL{lid} Away"}}}


@pytest.fixture
def sources(monkeypatch):
    """football-data responde las ligas con código; api-football el resto. Registra las consultas."""
    calls = {"fd": [], "afl": []}

    def fd_get(path, params=None):
        calls["fd"].append(path)
        code = path.split("/")[2]
        return {"matches": [_fd_match(code)]}

    def api_sports_get(base, endpoint, params):
        calls["afl"].append(params["league"])
        return {"response": [_afl_match(params["league"])]}

    monkeypatch.setattr(football, "fd_enabled", lambda: True)
    monkeypatch.setattr(football, "fd_get", fd_get)
    monkeypatch.setattr(football, "api_sports_get", api_sports_get)
    return calls


def test_europa_league_comes_from_api_football_when_football_data_is_set(sources, ctx):
    fixtures = football.collect_football_fixtures(ctx)
    by_league = {f["league"]: f for f in fixtures}

    assert set(REQUIRED) <= set(by_league)
    europa = by_league["Europa League"]
    assert (europa["source"], europa["afl_lid"], europa["fd_code"]) == ("api-football", 3, None)
    assert europa["odds_key"] == "soccer_uefa_europa_league"
    assert all("None" not in path for path in sources["fd"])        # no se pide a football-data
    assert sources["afl"] == [3]                                     # el resto salió de football-data


def test_leagues_with_football_data_keep_its_fixtures(sources, ctx):
    by_league = {f["league"]: f for f in football.collect_football_fixtures(ctx)}
    for league in ("Premier League", "LaLiga", "Champions League"):
        fix = by_league[league]
        assert (fix["source"], fix["fd_code"]) == ("football-data", REQUIRED[league][0])


def test_without_football_data_token_all_leagues_use_api_football(sources, monkeypatch, ctx):
    monkeypatch.setattr(football, "fd_enabled", lambda: False)
    fixtures = football.collect_football_fixtures(ctx)
    assert sources["fd"] == []
    assert {f["league"] for f in fixtures} >= set(REQUIRED)


def test_fallback_leagues_still_run_when_top_leagues_are_empty(monkeypatch, ctx):
    asked = []

    def api_sports_get(base, endpoint, params):
        asked.append(params["league"])
        is_fallback = params["league"] == config.FALLBACK_LEAGUES_FOOTBALL["MLS"]["afl"]
        return {"response": [_afl_match(params["league"])] if is_fallback else []}

    monkeypatch.setattr(football, "fd_enabled", lambda: False)
    monkeypatch.setattr(football, "api_sports_get", api_sports_get)
    fixtures = football.collect_football_fixtures(ctx)
    assert [f["league"] for f in fixtures] == ["MLS"]
    assert set(info["afl"] for info in config.TOP_LEAGUES_FOOTBALL.values()) <= set(asked)


# ── Tope de partidos por liga ─────────────────────────────────────

FULL_ROUND = {"Premier League": 10, "LaLiga": 10, "Champions League": 18}


def _many_fd_matches(monkeypatch, n=25):
    def fd_get(path, params=None):
        code = path.split("/")[2]
        return {"matches": [{**_fd_match(code), "homeTeam": {"id": 100 + i, "name": f"{code} H{i}"},
                             "awayTeam": {"id": 200 + i, "name": f"{code} A{i}"}} for i in range(n)]}
    monkeypatch.setattr(football, "fd_enabled", lambda: True)
    monkeypatch.setattr(football, "fd_get", fd_get)
    monkeypatch.setattr(football, "api_sports_get", lambda *a, **k: {"response": []})


@pytest.mark.parametrize("league, cap", FULL_ROUND.items())
def test_premier_laliga_and_champions_take_a_full_round(monkeypatch, league, cap, ctx):
    _many_fd_matches(monkeypatch)
    fixtures = [f for f in football.collect_football_fixtures(ctx) if f["league"] == league]
    assert len(fixtures) == cap == config.TOP_LEAGUES_FOOTBALL[league]["max_fixtures"]


def test_other_leagues_keep_the_default_cap(monkeypatch, ctx):
    _many_fd_matches(monkeypatch)
    fixtures = [f for f in football.collect_football_fixtures(ctx) if f["league"] == "Bundesliga"]
    assert len(fixtures) == football.MAX_FIXTURES_LEAGUE == 4


# ── Plan de api-football sin acceso a la temporada ───────────────

PLAN_ERROR = {"errors": {"plan": "Free plans do not have access to this season"}, "response": []}


def test_api_football_plan_error_is_warned_once_and_stops_api_football(monkeypatch, caplog, ctx):
    calls = []
    monkeypatch.setattr(football, "fd_enabled", lambda: False)
    monkeypatch.setattr(football, "api_sports_get", lambda *a, **k: calls.append(a) or PLAN_ERROR)
    fixtures = football.collect_football_fixtures(ctx)
    stats = football.build_football_stats(ctx, [{"home": "A", "away": "B", "league": "LaLiga",
                                           "home_id_afl": 1, "away_id_afl": 2, "afl_lid": 140, "afl_season": 2026}])
    plan_warnings = [r for r in caplog.records if r.levelname == "WARNING" and "plan" in r.getMessage()]
    assert (fixtures, stats, len(calls), len(plan_warnings)) == ([], {}, 1, 1)
    assert ctx.football.afl_blocked.is_set()


# ── football-data: respuesta vacía ≠ sin respuesta ───────────────

def test_empty_football_data_round_does_not_ask_api_football(monkeypatch, ctx):
    asked = []
    monkeypatch.setattr(football, "fd_enabled", lambda: True)
    monkeypatch.setattr(football, "fd_get", lambda path, params=None: {"matches": []})
    monkeypatch.setattr(football, "api_sports_get", lambda b, e, params: asked.append(params["league"]) or {"response": []})
    football.collect_football_fixtures(ctx)
    with_fd = {info["afl"] for info in config.TOP_LEAGUES_FOOTBALL.values() if info.get("fd")}
    assert not with_fd & set(asked)


def test_football_data_without_answer_falls_back_to_api_football(monkeypatch, ctx):
    monkeypatch.setattr(football, "fd_enabled", lambda: True)
    monkeypatch.setattr(football, "fd_get", lambda path, params=None: {})
    monkeypatch.setattr(football, "api_sports_get", lambda b, e, params: {"response": [_afl_match(params["league"])]})
    leagues = {f["league"] for f in football.collect_football_fixtures(ctx)}
    assert "Premier League" in leagues


def test_football_data_fixture_date_is_the_eastern_day(monkeypatch, ctx):
    late = {**_fd_match("PL"), "utcDate": "2026-11-06T03:00:00Z"}   # 22:00 ET del día 5
    monkeypatch.setattr(football, "fd_enabled", lambda: True)
    monkeypatch.setattr(football, "fd_get", lambda path, params=None: {"matches": [late]})
    monkeypatch.setattr(football, "api_sports_get", lambda *a, **k: {"response": []})
    (fix, *_) = [f for f in football.collect_football_fixtures(ctx) if f["league"] == "Premier League"]
    assert (fix["date"], fix["kickoff"]) == ("2026-11-05", "2026-11-06T03:00:00Z")


# ── Límite diario, fallback inútil y fechas sin zona ──────────────

@pytest.mark.parametrize("errors", [{"requests": "You have reached the request limit for the day"},
                                    {"rateLimit": "Too many requests"}])
def test_request_limit_also_stops_api_football(monkeypatch, errors, ctx):
    calls = []
    monkeypatch.setattr(football, "fd_enabled", lambda: False)
    monkeypatch.setattr(football, "api_sports_get", lambda *a, **k: calls.append(a) or {"errors": errors})
    football.collect_football_fixtures(ctx)
    assert len(calls) == 1 and ctx.football.afl_blocked.is_set()


def test_no_fallback_attempt_when_api_football_is_blocked(monkeypatch, caplog, ctx):
    monkeypatch.setattr(football, "fd_enabled", lambda: False)
    monkeypatch.setattr(football, "api_sports_get", lambda *a, **k: PLAN_ERROR)
    with caplog.at_level(logging.INFO, logger=football.log.name):
        football.collect_football_fixtures(ctx)
    assert "Activando fallback" not in caplog.text and "Football fixtures: 0" in caplog.text


def test_naive_utc_time_is_read_as_utc():
    assert football._et_date("2026-11-06T03:00:00") == "2026-11-05"
