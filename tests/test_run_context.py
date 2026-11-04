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


def test_store_sport_that_fails_still_reports_its_count(monkeypatch):
    # Igual que NFL, que escribe ctx.games_analyzed antes de poder fallar
    def failing():
        main.store.games_analyzed["nba"] = 5
        raise RuntimeError("nba_api raro")

    main.store.clear_run_caches()
    ctx = RunContext()
    with pytest.raises(RuntimeError):
        main._from_store(failing, "nba")(ctx)
    assert ctx.games_analyzed == {"nba": 5}
    main.store.clear_run_caches()


def test_context_fields_cannot_be_replaced():
    ctx = RunContext()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.nfl = None
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.nfl.team_ids = ["9"]
