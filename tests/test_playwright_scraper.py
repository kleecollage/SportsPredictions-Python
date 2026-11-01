"""Utilidades Playwright compartidas: parser de Flashscore, browser y aviso de cookies."""
import asyncio
import logging

import pytest
from bs4 import BeautifulSoup

from value_bot.scrapers import playwright_scraper as pw
from value_bot.scrapers.playwright_scraper import COOKIE_SELECTORS, _parse_flashscore_item


def _item(time_txt: str | None, home: str = "A", away: str = "B"):
    time_html = "" if time_txt is None else f'<div class="event__time">{time_txt}</div>'
    html = (f'<div class="event__match">{time_html}'
            f'<div class="event__participant--home">{home}</div>'
            f'<div class="event__participant--away">{away}</div></div>')
    return BeautifulSoup(html, "html.parser").select_one(".event__match")


# ── Parser: solo partidos con hora de inicio ──────────────────────

@pytest.mark.parametrize("time_txt", ["20:00", "9:30", " 18:45 "])
def test_upcoming_match_with_kickoff_time_is_parsed(time_txt):
    assert _parse_flashscore_item(_item(time_txt)) == {"home": "A", "away": "B", "time": time_txt.strip()}


@pytest.mark.parametrize("time_txt", [
    None, "", "Finished", "After Extra Time", "After Penalties", "Abandoned", "Awarded",
    "Postponed", "Cancelled", "45'", "HT", "Half Time", "1st Half", "FT", "01.11. 20:00",
])
def test_finished_live_or_timeless_items_are_dropped(time_txt):
    # La página mezcla resultados, en vivo y otros días; sin HH:MM no es un partido de hoy por jugar
    assert _parse_flashscore_item(_item(time_txt)) is None


def test_same_or_missing_team_names_are_dropped():
    assert _parse_flashscore_item(_item("20:00", home="A", away="A")) is None
    assert _parse_flashscore_item(_item("20:00", home="", away="B")) is None


# ── Browser ───────────────────────────────────────────────────────

class _FakeContext:
    async def add_init_script(self, _script): pass
    async def route(self, _pattern, _handler): pass


class _FakeChromium:
    def __init__(self):
        self.args = None

    async def launch(self, headless, args):
        self.args = args
        browser = type("B", (), {})()

        async def new_context(**_kw): return _FakeContext()
        browser.new_context = new_context
        return browser


def test_browser_keeps_chromium_sandbox():
    chromium = _FakeChromium()
    asyncio.run(pw._build_stealth_browser(type("P", (), {"chromium": chromium})()))
    assert "--no-sandbox" not in chromium.args


def test_bs4_ok_requires_the_lxml_parser(monkeypatch):
    real = pw.find_spec
    monkeypatch.setattr(pw, "find_spec", lambda name: None if name == "lxml" else real(name))
    assert pw._has_modules("bs4", "lxml") is False
    assert pw._has_modules("bs4") is True


# ── Aviso de cookies ──────────────────────────────────────────────

class _FakePage:
    def __init__(self, fail: bool):
        self.fail = fail
        self.clicks = []

    async def click(self, selector, timeout):
        self.clicks.append((selector, timeout))
        if self.fail:
            raise TimeoutError("sin aviso")


def test_cookie_banner_is_clicked_with_one_combined_wait(monkeypatch):
    monkeypatch.setattr(pw, "_human_delay", lambda *a: asyncio.sleep(0))
    page = _FakePage(fail=False)
    asyncio.run(pw._accept_cookies(page))
    assert page.clicks == [(", ".join(COOKIE_SELECTORS), pw.COOKIE_TIMEOUT_MS)]
    assert not any("id*=" in sel for sel in COOKIE_SELECTORS)  # nada de botones "accept" genéricos


def test_missing_cookie_banner_is_logged_not_raised(caplog):
    page = _FakePage(fail=True)
    with caplog.at_level(logging.DEBUG, logger=pw.log.name):
        asyncio.run(pw._accept_cookies(page))
    assert len(page.clicks) == 1
    assert "cookies" in caplog.text
