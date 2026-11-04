"""RunContext: el estado de una corrida se pasa explícito y no sobrevive a la siguiente."""
import asyncio
import dataclasses

import pytest

from value_bot import main
from value_bot.run_context import RunContext
from value_bot.sports import nfl
from value_bot.sports.nfl import TeamScores


def test_each_context_starts_empty():
    first, second = RunContext(), RunContext()
    first.nfl.team_ids.append("9")
    first.games_analyzed["nfl"] = 3
    assert second.nfl.team_ids == [] and second.games_analyzed == {}


def test_nfl_cache_lives_in_the_context_not_across_runs(monkeypatch):
    fetched = []
    monkeypatch.setattr(nfl, "nfl_season", lambda offset=0: 2026 + offset)
    monkeypatch.setattr(nfl, "_fetch_scores", lambda tid, season: fetched.append(tid) or [(20.0, 17.0)] * 3)
    run = RunContext()
    nfl.get_team_scores(run, "12")
    nfl.get_team_scores(run, "12")
    nfl.get_team_scores(RunContext(), "12")   # corrida nueva: vuelve a pedir
    assert fetched == ["12", "12"]
    assert run.nfl.scores["12"] == TeamScores([(20.0, 17.0)] * 3, current=True)


def test_main_passes_one_context_to_every_sport_and_reads_its_counts(monkeypatch):
    seen = []

    def source(sport):
        def get(ctx):
            seen.append(ctx)
            ctx.games_analyzed[sport] = 2
            return []
        return get

    monkeypatch.setattr("value_bot.sports_registry.SPORTS_ENABLED", {"football": True, "nba": True, "nfl": True})
    monkeypatch.setattr(main, "PICK_SOURCES", {s: source(s) for s in ("football", "nba", "nfl")})
    ctx = RunContext()
    asyncio.run(main._collect_picks(ctx))
    assert len(seen) == 3 and all(c is ctx for c in seen)
    assert ctx.games_analyzed == {"football": 2, "nba": 2, "nfl": 2}


def test_football_that_fails_still_reports_its_count(monkeypatch):
    # Como NFL: el conteo se escribe antes de pedir stats, que es lo que puede fallar
    from value_bot.analysis import football_picks as fp
    monkeypatch.setattr(fp, "collect_football_fixtures", lambda c: [{"home": "A", "away": "B"}] * 4)
    monkeypatch.setattr(fp, "build_football_stats", lambda c, f: (_ for _ in ()).throw(RuntimeError("football-data raro")))
    ctx = RunContext()
    with pytest.raises(RuntimeError):
        fp.get_football_picks(ctx)
    assert ctx.games_analyzed == {"football": 4}


def test_context_fields_cannot_be_replaced():
    ctx = RunContext()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.nfl = None
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.nfl.team_ids = ["9"]


# ── NBA ───────────────────────────────────────────────────────────

def test_nba_state_starts_clean_in_each_run():
    first = RunContext()
    first.nba.api_down.set()
    first.nba.form["Boston Celtics"] = {"form": "WWWWW"}
    first.nba.slate_dates.append("2026-11-03")
    second = RunContext()
    assert not second.nba.api_down.is_set() and second.nba.form == {} and second.nba.slate_dates == []


def test_nba_network_failure_only_stops_nba_api_for_that_run(monkeypatch):
    import requests
    from value_bot.sports import nba

    def down(**_):
        raise requests.exceptions.ConnectTimeout("stats.nba.com")
    monkeypatch.setattr(nba, "NBA_API_OK", True)
    monkeypatch.setattr(nba.time, "sleep", lambda _: None)
    monkeypatch.setattr(nba.leaguedashteamstats, "LeagueDashTeamStats", down, raising=False)
    run = RunContext()
    assert nba.nba_get_league_stats(run, "2026-27", "Regular Season") == {}
    assert run.nba.api_down.is_set()
    assert not RunContext().nba.api_down.is_set()


def test_upcoming_starts_after_the_slate_in_the_context(monkeypatch):
    from value_bot import main
    asked = []
    monkeypatch.setattr("value_bot.sports_registry.SPORTS_ENABLED", {"nba": True})
    monkeypatch.setattr(main, "get_upcoming_nba_events", lambda after=None: asked.append(after) or [])
    ctx = RunContext()
    ctx.nba.slate_dates.append("2026-11-03")
    main._upcoming(ctx)
    assert asked == ["2026-11-03"]


def test_nba_form_from_the_context_reaches_the_message():
    from value_bot import telegram_sender as ts
    pick = {"sport": "nba", "league": "NBA", "home": "Boston Celtics", "away": "Chicago Bulls", "type": "Victoria Boston Celtics",
            "confidence": 60, "est_odds": 1.9, "value": 0.05, "reasoning": "r", "data_quality": "high",
            "real_odds_source": True}
    form = {"Boston Celtics": {"ppg": 118.2, "form": "WWLWW"}, "Chicago Bulls": {"ppg": 104.5, "form": "LLWLL"}}
    msg = ts.build_message({"nba": [pick]}, [], nba_form=form)
    assert "Celtics: 118.2pts" in msg and "Bulls: 104.5pts" in msg
    assert "118.2pts" not in ts.build_message({"nba": [pick]}, [])


# ── Fútbol ────────────────────────────────────────────────────────

def test_football_state_starts_clean_in_each_run():
    first = RunContext()
    first.football.afl_blocked.set()
    first.football.odds["soccer_epl"] = [{"id": "x"}]
    first.football.credits.left = 12
    first.football.team_ids[("Arsenal", 39, 2024)] = 42
    second = RunContext()
    assert not second.football.afl_blocked.is_set()
    assert second.football.odds == {} and second.football.credits.left is None and second.football.team_ids == {}


def test_odds_are_fetched_once_per_league_and_run(monkeypatch):
    from value_bot.sports import football_odds as fo
    calls = []
    monkeypatch.setattr(fo, "THE_ODDS_API_KEY", "k")
    monkeypatch.setattr(fo, "_odds_get", lambda path, params: calls.append(path) or ([], 150))
    run = RunContext()
    fo.get_real_odds(run, "soccer_epl", "A", "B")
    fo.get_real_odds(run, "soccer_epl", "C", "D")
    fo.get_real_odds(RunContext(), "soccer_epl", "A", "B")
    assert len(calls) == 2 and run.football.credits.left == 150


def test_store_module_is_gone():
    import importlib.util
    from value_bot import main
    assert importlib.util.find_spec("value_bot.store") is None
    assert not hasattr(main, "_from_store")


def test_team_id_lookup_is_cached_per_run_not_across_runs(monkeypatch):
    from value_bot.sports import football
    searches = []

    def api_sports_get(base, endpoint, params):
        searches.append(params["search"])
        return {"response": [{"team": {"id": 42, "name": "Arsenal"}}]}
    monkeypatch.setattr(football, "api_sports_get", api_sports_get)
    run = RunContext()
    assert football._resolve_team_id_by_name(run, "Arsenal", 39, 2024) == 42
    assert football._resolve_team_id_by_name(run, "Arsenal", 39, 2024) == 42
    assert len(searches) == 1
    football._resolve_team_id_by_name(RunContext(), "Arsenal", 39, 2024)
    assert len(searches) == 2


def test_football_picks_replace_the_btts_fixtures_instead_of_appending(monkeypatch):
    from value_bot.analysis import football_picks as fp
    fix = {"home": "A", "away": "B", "league": "Premier League"}
    monkeypatch.setattr(fp, "collect_football_fixtures", lambda c: [fix])
    monkeypatch.setattr(fp, "build_football_stats", lambda c, f: {})
    ctx = RunContext()
    ctx.football.stats[("Premier League", "Viejo")] = {}
    fp.get_football_picks(ctx)
    fp.get_football_picks(ctx)
    assert ctx.football.fixtures == [fix] and ctx.football.stats == {}
