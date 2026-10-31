"""nba.py: nombres → equipo, stats por juego, unión de fuentes, Finales y corte de nba_api."""
import asyncio

import pandas as pd
import pytest
import requests

from value_bot.analysis import picks as picks_mod
from value_bot.scrapers import espn, flashscore
from value_bot.sports import nba
from value_bot.store import store

pytestmark = pytest.mark.skipif(not nba.NBA_API_OK, reason="nba_api no instalado")


@pytest.fixture(autouse=True)
def _clean_store():
    store.clear_run_caches()
    yield
    store.clear_run_caches()


def _tid(name):
    return nba._nba_find_team_id(name)


# ── H1: nombre → ID sin fuzzy ─────────────────────────────────────

def test_every_nba_api_full_name_maps_to_its_own_team():
    from nba_api.stats.static import teams
    for team in teams.get_teams():
        assert _tid(team["full_name"]) == team["id"]


@pytest.mark.parametrize("alias, full_name", [
    ("LA Clippers", "Los Angeles Clippers"),
    ("LA Lakers", "Los Angeles Lakers"),
    ("Lakers", "Los Angeles Lakers"),
    ("  boston celtics ", "Boston Celtics"),
])
def test_aliases_and_nicknames_map_to_the_right_team(alias, full_name):
    assert _tid(alias) == _tid(full_name) is not None


@pytest.mark.parametrize("name, not_this", [
    ("Charlotte Hornets", "Brooklyn Nets"),        # "nets" ⊂ "hornets"
    ("New Orleans Pelicans", "New York Knicks"),   # comparten "new"
    ("Los Angeles Lakers", "LA Clippers"),         # "la" ⊂ "lakers"
    ("Atlanta Hawks", "LA Clippers"),              # "la" ⊂ "atlanta"
])
def test_similar_names_never_map_to_another_team(name, not_this):
    assert _tid(name) != _tid(not_this)


def test_unknown_team_is_none():
    assert _tid("Springfield Atoms") is None and _tid("") is None


# ── H2 / M4: LeagueDash por juego, rango válido y timeout ─────────

class _FakeEndpoint:
    calls: list = []
    frames: list = []
    error: Exception | None = None

    def __init__(self, **kwargs):
        type(self).calls.append(kwargs)
        if type(self).error:
            raise type(self).error

    def get_data_frames(self):
        return type(self).frames


@pytest.fixture
def fake_league(monkeypatch):
    class Fake(_FakeEndpoint):
        calls, frames, error = [], [], None
    monkeypatch.setattr(nba.leaguedashteamstats, "LeagueDashTeamStats", Fake)
    monkeypatch.setattr(nba.time, "sleep", lambda _: None)
    return Fake


def _league_df(rows):
    return pd.DataFrame(rows, columns=["TEAM_ID", "TEAM_NAME", "GP", "W", "PTS", "PLUS_MINUS"])


def test_league_stats_requests_per_game_and_keys_by_team_id(fake_league):
    hornets = _tid("Charlotte Hornets")
    fake_league.frames = [_league_df([(hornets, "Charlotte Hornets", 5, 2, 99.0, -3.0)])]
    stats = nba.nba_get_league_stats("2026-27", "Regular Season")
    assert fake_league.calls[0]["per_mode_detailed"] == "PerGame"
    assert fake_league.calls[0]["timeout"] == nba.NBA_API_TIMEOUT
    assert stats[hornets]["ppg"] == 99.0 and stats[hornets]["opp_ppg"] == 102.0
    assert stats[hornets]["win_pct"] == 40.0


def test_league_stats_drop_short_samples_and_impossible_values(fake_league):
    fake_league.frames = [_league_df([
        (1, "Few Games", 4, 4, 115.0, 9.0),
        (2, "Totals Leak", 5, 2, 495.0, -15.0),   # totales colados = ppg imposible
        (3, "Valid", 5, 3, 112.0, 1.0),
    ])]
    assert set(nba.nba_get_league_stats("2026-27", "Regular Season")) == {3}


def test_network_failure_opens_circuit_and_skips_later_nba_api_calls(fake_league, monkeypatch):
    fake_league.error = requests.exceptions.ReadTimeout("stats.nba.com colgado")
    assert nba.nba_get_league_stats("2026-27", "Regular Season") == {}
    assert store.nba_api_down is True

    calls_before = len(fake_league.calls)
    assert nba.nba_get_league_stats("2025-26", "Regular Season") == {}
    assert nba.nba_get_team_form("Boston Celtics") == {}
    assert nba.nba_get_home_away_splits("Boston Celtics", "home") == {}
    assert len(fake_league.calls) == calls_before


def test_circuit_resets_each_run():
    store.nba_api_down = True
    store.clear_run_caches()
    assert store.nba_api_down is False


# ── H1 / M3: stats completas del equipo correcto, sin inventar ────

def _stats(ppg):
    return {"ppg": ppg, "opp_ppg": ppg - 2, "win_pct": 60.0, "gp": 5, "pm": 2.0}


def test_team_missing_this_season_falls_back_to_its_own_previous_season(monkeypatch):
    pelicans, knicks = _tid("New Orleans Pelicans"), _tid("New York Knicks")
    seasons = {
        ("2026-27", "Regular Season"): {knicks: _stats(118.0)},     # Pelicans con < 5 juegos
        ("2025-26", "Regular Season"): {pelicans: _stats(109.0), knicks: _stats(117.0)},
    }
    monkeypatch.setattr(nba, "_nba_stat_sources", lambda: list(seasons))
    monkeypatch.setattr(nba, "nba_get_league_stats", lambda s, t: seasons[(s, t)])
    monkeypatch.setattr(nba, "nba_get_team_form", lambda _: {})
    assert nba.nba_get_team_full_stats("New Orleans Pelicans")["ppg"] == 109.0


def test_form_without_league_stats_is_not_enough(monkeypatch):
    monkeypatch.setattr(nba, "_nba_stat_sources", lambda: [("2026-27", "Regular Season")])
    monkeypatch.setattr(nba, "nba_get_league_stats", lambda s, t: {})
    monkeypatch.setattr(nba, "nba_get_team_form",
                        lambda _: {"form": "WWLWW", "ppg": 120.0, "win_rate": 80.0, "played": 5})
    assert nba.nba_get_team_full_stats("Boston Celtics") == {}


def test_game_without_stats_produces_no_picks(monkeypatch):
    monkeypatch.setattr(picks_mod, "get_best_nba_stats", lambda g: ({}, {}, {}, {}))
    game = {"home": "Boston Celtics", "away": "Chicago Bulls", "home_record": "2-2", "away_record": "1-3",
            "lines": {"home_ml": 1.5, "away_ml": 2.6}}
    assert picks_mod.analyze_nba_game(game) == []


# ── M1 / M2: unión de fuentes y marca de Finales ─────────────────

def _api_game(home, away, date="2026-10-30T00:00:00+00:00", ids=(101, 102)):
    return {"teams": {"home": {"id": ids[0], "name": home}, "away": {"id": ids[1], "name": away}},
            "status": {"short": "NS"}, "date": date}


def _espn(home, away, headline=""):
    return {"home": home, "away": away, "home_record": "3-1", "away_record": "1-3",
            "kickoff": "2026-10-30T02:00Z", "is_finals": espn._is_nba_finals(headline),
            "series_summary": headline, "venue": f"{home} Arena"}


@pytest.fixture
def sources(monkeypatch):
    feed = {"api": [], "espn": []}
    monkeypatch.setattr(nba, "api_sports_get", lambda *a, **k: {"response": feed["api"]})
    monkeypatch.setattr(nba, "espn_nba_games", lambda d: feed["espn"])
    monkeypatch.setattr(nba, "espn_parse_nba", lambda ev: ev)
    monkeypatch.setattr(nba, "scrape_bbc_sport_nba", lambda: [])
    return feed


def test_espn_merges_only_into_the_same_matchup(sources):
    sources["api"] = [_api_game("Los Angeles Clippers", "Utah Jazz")]
    sources["espn"] = [_espn("Los Angeles Lakers", "Phoenix Suns"), _espn("LA Clippers", "Utah Jazz")]
    games = {(g["home"], g["away"]): g for g in nba.get_nba_games()}

    assert set(games) == {("Los Angeles Clippers", "Utah Jazz"), ("Los Angeles Lakers", "Phoenix Suns")}
    clippers = games[("Los Angeles Clippers", "Utah Jazz")]
    assert clippers["venue"] == "LA Clippers Arena" and clippers["source"] == "api-sports"
    assert games[("Los Angeles Lakers", "Phoenix Suns")]["venue"] == "Los Angeles Lakers Arena"


def test_playoff_month_does_not_mark_every_game_as_finals(sources, monkeypatch):
    monkeypatch.setattr(nba, "_is_nba_playoffs_month", lambda: True)
    sources["api"] = [_api_game("Boston Celtics", "New York Knicks"), _api_game("Denver Nuggets", "Utah Jazz")]
    sources["espn"] = [_espn("Boston Celtics", "New York Knicks", "East Finals - Game 2")]
    assert not any(g["is_finals"] for g in nba.get_nba_games())


@pytest.mark.parametrize("headline, expected", [
    ("NBA Finals - Game 3", True),
    ("East Finals - Game 2", False),
    ("West Conference Finals - Game 5", False),
    ("East First Round - Game 1", False),
    ("", False),
])
def test_only_the_nba_finals_headline_counts_as_finals(headline, expected):
    assert espn._is_nba_finals(headline) is expected


# ── M6: Playwright solo para los deportes activos ─────────────────

def test_playwright_runs_only_enabled_sports(monkeypatch):
    ran = []

    class _Ctx:
        async def __aenter__(self): return object()
        async def __aexit__(self, *a): return False

    class _Browser:
        async def close(self): pass

    async def fake_build(_p): return _Browser(), object()
    async def fake_nba(_c): ran.append("nba"); return [{"home": "A", "away": "B"}]
    async def fake_football(_c): ran.append("football"); return []

    import playwright.async_api
    monkeypatch.setattr(playwright.async_api, "async_playwright", lambda: _Ctx())
    monkeypatch.setattr(flashscore, "PLAYWRIGHT_OK", True)
    monkeypatch.setattr(flashscore, "PLAYWRIGHT_ENABLED", True)
    monkeypatch.setattr(flashscore, "_build_stealth_browser", fake_build)
    monkeypatch.setattr(flashscore, "_scrape_nba_with_context", fake_nba)
    monkeypatch.setattr(flashscore, "_scrape_football_with_context", fake_football)

    asyncio.run(flashscore.run_playwright_scrapers(nba=True, football=False))
    assert ran == ["nba"] and store.pw_nba_games == [{"home": "A", "away": "B"}]


# ── H1: respuestas con esquema inesperado no tumban la corrida ────

def test_league_stats_with_unexpected_columns_returns_empty(fake_league):
    fake_league.frames = [pd.DataFrame([{"TEAM_NAME": "X", "GP": 5, "W": 3, "PTS": 110.0}])]
    assert nba.nba_get_league_stats("2026-27", "Regular Season") == {}
    assert store.nba_api_down is False  # no es un fallo de red


def test_team_form_with_unexpected_columns_returns_empty(monkeypatch):
    class Log(_FakeEndpoint):
        calls, frames, error = [], [pd.DataFrame([{"GAME_ID": i, "PTS": 110} for i in range(6)])], None
    monkeypatch.setattr(nba.teamgamelog, "TeamGameLog", Log)
    monkeypatch.setattr(nba.time, "sleep", lambda _: None)
    assert nba.nba_get_team_form("Boston Celtics") == {}


def test_splits_with_unexpected_columns_returns_empty(monkeypatch):
    class Dash(_FakeEndpoint):
        calls, frames, error = [], [pd.DataFrame(), pd.DataFrame([{"GP": 9, "PTS": 112.0}])], None
    monkeypatch.setattr(nba.teamdashboardbygeneralsplits, "TeamDashboardByGeneralSplits", Dash)
    monkeypatch.setattr(nba.time, "sleep", lambda _: None)
    assert nba.nba_get_home_away_splits("Boston Celtics", "home") == {}


# ── M2: splits solo si son de la misma temporada que las stats base ─

def _wire_best_stats(monkeypatch, base_season, split_season):
    base = {"ppg": 120.0, "opp_ppg": 115.0, "source": "nba_api", "season": base_season}
    split = {"ppg": 108.0, "opp_ppg": 110.0, "gp": 41, "season": split_season}
    monkeypatch.setattr(nba, "nba_get_team_full_stats", lambda name: dict(base))
    monkeypatch.setattr(nba, "nba_get_home_away_splits", lambda name, loc, season=None: dict(split))
    monkeypatch.setattr(nba, "_is_nba_playoffs_month", lambda: False)


def test_splits_from_previous_season_are_not_mixed_with_current_stats(monkeypatch):
    _wire_best_stats(monkeypatch, base_season="2026-27", split_season="2025-26")
    _, _, splits, _ = nba.get_best_nba_stats({"home": "Atlanta Hawks", "away": "Cleveland Cavaliers"})
    assert splits == {}


def test_splits_of_the_same_season_are_used(monkeypatch):
    _wire_best_stats(monkeypatch, base_season="2026-27", split_season="2026-27")
    _, _, splits, _ = nba.get_best_nba_stats({"home": "Atlanta Hawks", "away": "Cleveland Cavaliers"})
    assert splits["home"]["ppg"] == 108.0 and splits["away"]["ppg"] == 108.0


def test_full_stats_report_the_season_they_come_from(monkeypatch):
    hawks = _tid("Atlanta Hawks")
    seasons = {("2026-27", "Regular Season"): {}, ("2025-26", "Regular Season"): {hawks: _stats(117.0)}}
    monkeypatch.setattr(nba, "_nba_stat_sources", lambda: list(seasons))
    monkeypatch.setattr(nba, "nba_get_league_stats", lambda s, t: seasons[(s, t)])
    monkeypatch.setattr(nba, "nba_get_team_form", lambda _: {})
    assert nba.nba_get_team_full_stats("Atlanta Hawks")["season"] == "2025-26"


# ── L2: equipo desconocido se cachea (un solo aviso por corrida) ──

def test_unknown_team_form_is_cached(caplog):
    with caplog.at_level("WARNING"):
        nba.nba_get_team_form("Springfield Atoms")
        nba.nba_get_team_form("Springfield Atoms")
    assert sum("no encontrado" in r.message for r in caplog.records) == 1


# ── L3: Flashscore NBA solo cuando las otras fuentes no dan juegos ─

def test_flashscore_nba_runs_only_when_other_sources_are_empty(sources, monkeypatch):
    calls = []
    monkeypatch.setattr(nba, "_flashscore_nba_games",
                        lambda: calls.append(1) or [{"home": "Boston Celtics", "away": "Chicago Bulls", "time": "19:30"}])
    sources["espn"] = [_espn("Boston Celtics", "Chicago Bulls")]
    assert len(nba.get_nba_games()) == 1 and calls == []

    sources["espn"] = []
    games = nba.get_nba_games()
    assert calls and [g["source"] for g in games] == ["flashscore_pw"]



# ── Review 3 · H1: Flashscore sin hora = no es un juego de hoy ────

@pytest.mark.parametrize("time_txt, kept", [
    ("19:30", True), ("7:05", True),
    ("", False), ("30.10. 19:30", False), ("Final", False), ("FT", False), ("25:99", False),
])
def test_flashscore_items_need_a_valid_today_time(sources, monkeypatch, time_txt, kept):
    monkeypatch.setattr(nba, "_flashscore_nba_games",
                        lambda: [{"home": "Boston Celtics", "away": "Chicago Bulls", "time": time_txt}])
    games = nba.get_nba_games()
    assert (len(games) == 1) is kept


def test_flashscore_list_of_finished_and_future_games_produces_nothing(sources, monkeypatch):
    # Caso real 29-oct-2026: 34 items sin hora (resultados recientes + próximos días)
    junk = [{"home": "Miami Heat", "away": "Indiana Pacers", "time": ""},
            {"home": "Atlanta Hawks", "away": "Miami Heat", "time": ""}]
    monkeypatch.setattr(nba, "_flashscore_nba_games", lambda: junk)
    assert nba.get_nba_games() == []


# ── Review 3 · M1: splits solo de la temporada de las stats base ──

def test_splits_are_requested_only_for_the_base_season(monkeypatch):
    seen = []

    class Dash(_FakeEndpoint):
        calls, frames, error = [], [pd.DataFrame(), pd.DataFrame(
            [{"GROUP_VALUE": "Home", "GP": 40, "W": 25, "PTS": 114.0, "PLUS_MINUS": 3.0}])], None

        def __init__(self, **kwargs):
            seen.append(kwargs["season"])
            super().__init__(**kwargs)

    monkeypatch.setattr(nba.teamdashboardbygeneralsplits, "TeamDashboardByGeneralSplits", Dash)
    monkeypatch.setattr(nba.time, "sleep", lambda _: None)
    monkeypatch.setattr(nba, "_nba_stat_sources",
                        lambda: [("2026-27", "Regular Season"), ("2025-26", "Regular Season")])

    split = nba.nba_get_home_away_splits("Boston Celtics", "home", season="2025-26")
    assert seen == ["2025-26"] and split["season"] == "2025-26" and split["ppg"] == 114.0


def test_best_stats_ask_splits_for_each_team_base_season(monkeypatch):
    asked = []
    seasons = {"Atlanta Hawks": "2026-27", "Cleveland Cavaliers": "2025-26"}
    monkeypatch.setattr(nba, "nba_get_team_full_stats",
                        lambda name: {"ppg": 115.0, "opp_ppg": 112.0, "source": "nba_api", "season": seasons[name]})
    monkeypatch.setattr(nba, "nba_get_home_away_splits",
                        lambda name, loc, season=None: asked.append((name, season)) or {})
    monkeypatch.setattr(nba, "_is_nba_playoffs_month", lambda: False)
    nba.get_best_nba_stats({"home": "Atlanta Hawks", "away": "Cleveland Cavaliers"})
    assert asked == [("Atlanta Hawks", "2026-27"), ("Cleveland Cavaliers", "2025-26")]


# ── Review 3 · L2: sin warning de corrutina desde un loop activo ──

def test_flashscore_inside_running_loop_returns_empty_without_creating_coroutine(monkeypatch):
    created = []

    async def _empty():
        return []

    def fake_scrape():  # registra cuándo se CREA la corrutina, no cuándo corre
        created.append(1)
        return _empty()

    monkeypatch.setattr(nba, "scrape_flashscore_nba", fake_scrape)

    async def inside():
        return nba._flashscore_nba_games()

    assert asyncio.run(inside()) == [] and created == []
