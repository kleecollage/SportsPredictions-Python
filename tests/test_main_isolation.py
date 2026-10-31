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
