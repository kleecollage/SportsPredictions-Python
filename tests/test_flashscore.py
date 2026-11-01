"""Capa 4 de fútbol: armado de fixtures de Flashscore y aislamiento por liga."""
import asyncio
import logging
from datetime import date

from value_bot.config import FALLBACK_LEAGUES_FOOTBALL, afl_season
from value_bot.scrapers import flashscore
from value_bot.telegram_sender import fmt_ko

TODAY = date(2026, 11, 1)
MLS = FALLBACK_LEAGUES_FOOTBALL["MLS"]


def _fix(league="MLS", time="20:30"):
    info = FALLBACK_LEAGUES_FOOTBALL[league]
    return flashscore._build_fixture(league, info, {"home": "Inter Miami", "away": "LA Galaxy", "time": time},
                                     TODAY)


def test_fixture_takes_odds_key_and_season_from_the_league():
    fix = _fix()
    assert fix["odds_key"] == MLS["odds"] == "soccer_usa_mls"   # antes None → nunca cuota real
    assert fix["afl_season"] == afl_season(MLS) and fix["flag"] == MLS["flag"]
    assert fix["source"] == "flashscore_pw" and fix["sport"] == "football"


def test_fixture_season_follows_the_calendar(monkeypatch):
    monkeypatch.setattr(flashscore, "afl_season", lambda info: 2027)
    assert _fix()["afl_season"] == 2027   # nada de temporada fija


def test_fixture_keeps_kickoff_in_eastern_time():
    fix = _fix(time="20:30")
    assert fix["date"] == "2026-11-01"
    assert fix["kickoff"] == "2026-11-01T20:30:00-05:00"
    assert fmt_ko(fix["kickoff"]) == "01/11 20:30 ET"


def test_finished_world_cup_is_no_longer_scraped():
    leagues = [name for name, _url in flashscore._FOOTBALL_TARGETS]
    assert "Mundial 2026" not in leagues
    assert all(name in FALLBACK_LEAGUES_FOOTBALL for name in leagues)


def test_unused_standalone_scraper_is_gone():
    assert not hasattr(flashscore, "scrape_flashscore_football")


# ── Scrape por liga ───────────────────────────────────────────────

_ROW = ('<div class="event__match"><div class="event__time">{t}</div>'
        '<div class="event__participant--home">{h}</div>'
        '<div class="event__participant--away">{a}</div></div>')


class _Page:
    def __init__(self, html):
        self.html = html

    async def goto(self, *_a, **_k): pass
    async def evaluate(self, _js): pass
    async def content(self): return self.html
    async def close(self): pass


class _Context:
    """Una página por liga; None = new_page falla en esa liga."""
    def __init__(self, pages):
        self.pages = list(pages)

    async def new_page(self):
        html = self.pages.pop(0)
        if html is None:
            raise RuntimeError("browser caído")
        return _Page(html)


def _no_wait(monkeypatch):
    async def _instant(*_a, **_k): pass
    monkeypatch.setattr(flashscore, "_human_delay", _instant)
    monkeypatch.setattr(flashscore, "_accept_cookies", _instant)


def test_a_failing_league_does_not_drop_the_others(monkeypatch):
    _no_wait(monkeypatch)
    ok = _ROW.format(t="20:00", h="Flamengo", a="Palmeiras")
    targets = flashscore._FOOTBALL_TARGETS
    pages = [ok] + [None] * (len(targets) - 1)
    fixtures = asyncio.run(flashscore._scrape_football_with_context(_Context(pages)))
    assert [(f["league"], f["home"]) for f in fixtures] == [(targets[0][0], "Flamengo")]


def test_items_without_kickoff_time_are_warned(monkeypatch, caplog):
    _no_wait(monkeypatch)
    no_time = _ROW.format(t="", h="Flamengo", a="Palmeiras")
    pages = [no_time] * len(flashscore._FOOTBALL_TARGETS)
    with caplog.at_level(logging.WARNING, logger=flashscore.log.name):
        fixtures = asyncio.run(flashscore._scrape_football_with_context(_Context(pages)))
    assert fixtures == []
    assert "sin hora" in caplog.text   # selector roto, no "liga sin partidos"


def test_league_with_only_results_is_not_reported_as_a_broken_selector(monkeypatch, caplog):
    _no_wait(monkeypatch)
    finished = _ROW.format(t="Finished", h="Flamengo", a="Palmeiras")
    pages = [finished] * len(flashscore._FOOTBALL_TARGETS)
    with caplog.at_level(logging.INFO, logger=flashscore.log.name):
        fixtures = asyncio.run(flashscore._scrape_football_with_context(_Context(pages)))
    assert fixtures == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]   # fuera de temporada ≠ selector roto
    assert "sin partidos por jugar" in caplog.text


def test_pause_only_between_leagues(monkeypatch):
    pauses = []

    async def _pause(*_a): pauses.append(1)
    async def _html(*_a, **_k): return ""
    monkeypatch.setattr(flashscore, "_human_delay", _pause)
    monkeypatch.setattr(flashscore, "_league_html", _html)
    asyncio.run(flashscore._scrape_football_with_context(object()))
    assert len(pauses) == len(flashscore._FOOTBALL_TARGETS) - 1
