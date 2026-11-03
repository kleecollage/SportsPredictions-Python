"""main: un deporte que falla no tumba a los demás (code review M1)."""
import asyncio

from value_bot import main


def test_failing_sport_returns_empty_and_others_still_run(monkeypatch):
    def boom():
        raise KeyError("TEAM_ID")

    monkeypatch.setattr(main, "SPORTS_ENABLED", {"football": False, "nba": True, "nfl": True})
    monkeypatch.setattr(main, "PICK_SOURCES",
                        {"football": lambda: ["no debe correr"], "nba": boom, "nfl": lambda: [{"pick": 1}]})
    picks = asyncio.run(main._collect_picks())
    assert picks == {"football": [], "nba": [], "nfl": [{"pick": 1}]}


def _flashscore_runs(monkeypatch, football_on, flashscore_on):
    ran = []

    async def fake_scrapers():
        ran.append(True)
    monkeypatch.setattr(main, "run_playwright_scrapers", fake_scrapers)
    monkeypatch.setattr(main, "SPORTS_ENABLED", {"football": football_on, "nba": True, "nfl": True})
    monkeypatch.setattr(main, "FLASHSCORE_ENABLED", flashscore_on)
    asyncio.run(main._football_scrapers())
    return bool(ran)


def test_flashscore_is_skipped_while_disabled(monkeypatch):
    # Selector de hora roto y sin stats de api-football (plan Free): Chromium no aportaba nada
    assert _flashscore_runs(monkeypatch, football_on=True, flashscore_on=False) is False


def test_flashscore_runs_only_with_football_and_the_flag(monkeypatch):
    assert _flashscore_runs(monkeypatch, football_on=True, flashscore_on=True) is True
    assert _flashscore_runs(monkeypatch, football_on=False, flashscore_on=True) is False
