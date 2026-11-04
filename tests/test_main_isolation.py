"""main: un deporte que falla no tumba a los demás (code review M1)."""
import asyncio

from value_bot import main
from value_bot.run_context import RunContext

NFL_PICK = {"sport": "nfl", "league": "NFL", "home": "H", "away": "A", "type": "Victoria H",
            "confidence": 56, "est_odds": 1.9, "our_prob": 0.56, "value": 0.05, "reasoning": "r",
            "data_quality": "high", "real_odds_source": True}


def test_failing_sport_returns_empty_and_others_still_run(monkeypatch):
    def boom(ctx):
        raise KeyError("TEAM_ID")

    monkeypatch.setattr("value_bot.sports_registry.SPORTS_ENABLED", {"football": False, "nba": True, "nfl": True})
    monkeypatch.setattr(main, "PICK_SOURCES",
                        {"football": lambda ctx: ["no debe correr"], "nba": boom, "nfl": lambda ctx: [NFL_PICK]})
    picks = asyncio.run(main._collect_picks(RunContext()))
    assert picks == {"football": [], "nba": [], "nfl": [NFL_PICK]}


def test_picks_breaking_the_contract_are_dropped(monkeypatch):
    broken = {k: v for k, v in NFL_PICK.items() if k != "reasoning"}
    monkeypatch.setattr("value_bot.sports_registry.SPORTS_ENABLED", {"football": False, "nba": False, "nfl": True})
    monkeypatch.setattr(main, "PICK_SOURCES", {**main.PICK_SOURCES, "nfl": lambda ctx: [NFL_PICK, broken]})
    assert asyncio.run(main._collect_picks(RunContext()))["nfl"] == [NFL_PICK]


def _flashscore_runs(monkeypatch, football_on, flashscore_on):
    ran = []

    async def fake_scrapers(ctx):
        ran.append(True)
    monkeypatch.setattr(main, "run_playwright_scrapers", fake_scrapers)
    monkeypatch.setattr("value_bot.sports_registry.SPORTS_ENABLED", {"football": football_on, "nba": True, "nfl": True})
    monkeypatch.setattr(main, "FLASHSCORE_ENABLED", flashscore_on)
    asyncio.run(main._football_scrapers(RunContext()))
    return bool(ran)


def test_flashscore_is_skipped_while_disabled(monkeypatch):
    # Selector de hora roto y sin stats de api-football (plan Free): Chromium no aportaba nada
    assert _flashscore_runs(monkeypatch, football_on=True, flashscore_on=False) is False


def test_flashscore_runs_only_with_football_and_the_flag(monkeypatch):
    assert _flashscore_runs(monkeypatch, football_on=True, flashscore_on=True) is True
    assert _flashscore_runs(monkeypatch, football_on=False, flashscore_on=True) is False
