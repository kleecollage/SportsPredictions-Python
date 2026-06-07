"""Flashscore scrapers (Capa 4) y run_playwright_scrapers con browser compartido."""
import asyncio
import logging
from datetime import datetime

from value_bot.config import PLAYWRIGHT_ENABLED, PLAYWRIGHT_TIMEOUT
from value_bot.store import store
from value_bot.scrapers.playwright_scraper import (
    BS4_OK, PLAYWRIGHT_OK,
    _build_stealth_browser, _human_delay, _accept_cookies,
    _infinite_scroll, _parse_flashscore_item,
)

try:
    from bs4 import BeautifulSoup
except ImportError:
    pass

log = logging.getLogger(__name__)

_FOOTBALL_TARGETS = [
    ("Mundial 2026",      "🌍", "https://www.flashscore.com/football/world/world-cup-2026/"),
    ("MLS",               "🇺🇸", "https://www.flashscore.com/football/usa/mls/"),
    ("Brasileirao",       "🇧🇷", "https://www.flashscore.com/football/brazil/serie-a/"),
    ("Copa Libertadores", "🌎", "https://www.flashscore.com/football/south-america/copa-libertadores/"),
]


async def _scrape_nba_with_context(context) -> list:
    """NBA scraping usando un context Playwright ya creado."""
    url = "https://www.flashscore.com/basketball/usa/nba/"
    games = []
    page = await context.new_page()
    try:
        await page.goto(url, timeout=PLAYWRIGHT_TIMEOUT, wait_until="domcontentloaded")
        await _human_delay(2500, 5000)
        await _accept_cookies(page)
        await _infinite_scroll(page, iters=3)
        html = await page.content()
    finally:
        await page.close()

    soup = BeautifulSoup(html, "lxml")
    for item in soup.select(".event__match"):
        m = _parse_flashscore_item(item)
        if m:
            games.append({**m, "source": "flashscore_pw"})
    log.info("Flashscore NBA: %d partidos", len(games))
    return games


async def _scrape_football_with_context(context) -> list:
    """Football scraping usando un context Playwright ya creado."""
    all_fix = []
    today_str = datetime.now().strftime("%Y-%m-%d")
    first = True

    for league_name, flag, url in _FOOTBALL_TARGETS:
        page = await context.new_page()
        try:
            await page.goto(url, timeout=PLAYWRIGHT_TIMEOUT, wait_until="domcontentloaded")
            await _human_delay(1800, 4000)
            if first:
                await _accept_cookies(page)
                first = False
            for _ in range(2):
                await page.evaluate("window.scrollBy(0, 1500)")
                await _human_delay(700, 1300)
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            count = 0
            for item in soup.select(".event__match"):
                m = _parse_flashscore_item(item)
                if not m:
                    continue
                all_fix.append({
                    "sport": "football",
                    "league": league_name,
                    "flag": flag,
                    "fd_code": None,
                    "afl_lid": None,
                    "afl_season": 2026,
                    "odds_key": None,
                    "date": today_str,
                    "kickoff": "",
                    "home": m["home"],
                    "away": m["away"],
                    "home_id_fd": None,
                    "away_id_fd": None,
                    "home_id_afl": None,
                    "away_id_afl": None,
                    "source": "flashscore_pw",
                })
                count += 1
            log.info("Flashscore %s: %d fixtures", league_name, count)
        except Exception as exc:
            log.warning("Flashscore %s: %s", league_name, exc)
        finally:
            await page.close()
        await _human_delay(800, 2000)

    log.info("Flashscore Football total: %d fixtures", len(all_fix))
    return all_fix


async def scrape_flashscore_nba() -> list:
    """API pública — abre su propio browser. Usar solo fuera de run_playwright_scrapers."""
    if not (PLAYWRIGHT_OK and PLAYWRIGHT_ENABLED and BS4_OK):
        return []
    from playwright.async_api import async_playwright
    try:
        async with async_playwright() as p:
            browser, context = await _build_stealth_browser(p)
            try:
                return await _scrape_nba_with_context(context)
            finally:
                await browser.close()
    except Exception as exc:
        log.warning("scrape_flashscore_nba standalone: %s", exc)
        return []


async def scrape_flashscore_football() -> list:
    """API pública — abre su propio browser. Usar solo fuera de run_playwright_scrapers."""
    if not (PLAYWRIGHT_OK and PLAYWRIGHT_ENABLED and BS4_OK):
        return []
    from playwright.async_api import async_playwright
    try:
        async with async_playwright() as p:
            browser, context = await _build_stealth_browser(p)
            try:
                return await _scrape_football_with_context(context)
            finally:
                await browser.close()
    except Exception as exc:
        log.warning("scrape_flashscore_football standalone: %s", exc)
        return []


async def run_playwright_scrapers() -> None:
    """
    Ejecuta NBA + Football con un único browser compartido en paralelo.
    Popula store.pw_nba_games y store.pw_football_fixtures.
    """
    if not (PLAYWRIGHT_OK and PLAYWRIGHT_ENABLED):
        log.info("Playwright: no disponible/desactivado → Capa 4 saltada")
        return

    if not BS4_OK:
        log.warning("Playwright: bs4/lxml no instalado → Capa 4 saltada")
        return

    log.info("Playwright: iniciando scrapers con browser compartido...")
    from playwright.async_api import async_playwright
    try:
        async with async_playwright() as p:
            browser, context = await _build_stealth_browser(p)
            try:
                nba_res, football_res = await asyncio.gather(
                    _scrape_nba_with_context(context),
                    _scrape_football_with_context(context),
                    return_exceptions=True,
                )
            finally:
                await browser.close()

        if isinstance(nba_res, list):
            store.pw_nba_games = nba_res
        else:
            log.warning("Playwright NBA falló: %s", nba_res)

        if isinstance(football_res, list):
            store.pw_football_fixtures = football_res
        else:
            log.warning("Playwright Football falló: %s", football_res)

        log.info("Playwright completo → NBA:%d Fútbol:%d",
                 len(store.pw_nba_games), len(store.pw_football_fixtures))
    except Exception as exc:
        log.warning("run_playwright_scrapers: %s", exc)
