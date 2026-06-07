"""Flashscore scrapers (Capa 4) and run_playwright_scrapers orchestrator."""
import asyncio
import logging
from datetime import datetime

from value_bot.config import PLAYWRIGHT_ENABLED, PLAYWRIGHT_TIMEOUT
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

# Global caches populated by run_playwright_scrapers()
_pw_nba_games: list = []
_pw_football_fixtures: list = []


async def scrape_flashscore_nba() -> list:
    """Capa 4 NBA: Flashscore via Playwright stealth."""
    if not (PLAYWRIGHT_OK and PLAYWRIGHT_ENABLED and BS4_OK):
        return []
    from playwright.async_api import async_playwright
    url = "https://www.flashscore.com/basketball/usa/nba/"
    games = []
    try:
        async with async_playwright() as p:
            browser, context = await _build_stealth_browser(p)
            page = await context.new_page()
            try:
                await page.goto(url, timeout=PLAYWRIGHT_TIMEOUT, wait_until="domcontentloaded")
                await _human_delay(2500, 5000)
                await _accept_cookies(page)
                await _infinite_scroll(page, iters=3)
                html = await page.content()
            finally:
                await page.close()
            await browser.close()

        soup = BeautifulSoup(html, "lxml")
        for item in soup.select(".event__match"):
            m = _parse_flashscore_item(item)
            if m:
                games.append({**m, "source": "flashscore_pw"})
        log.info("Flashscore NBA: %d partidos", len(games))
    except Exception as exc:
        log.warning("scrape_flashscore_nba: %s", exc)
    return games


async def scrape_flashscore_football() -> list:
    """Capa 4 Fútbol: Flashscore via Playwright stealth."""
    if not (PLAYWRIGHT_OK and PLAYWRIGHT_ENABLED and BS4_OK):
        return []
    from playwright.async_api import async_playwright

    TARGETS = [
        ("Mundial 2026",      "🌍", "https://www.flashscore.com/football/world/world-cup-2026/"),
        ("MLS",               "🇺🇸", "https://www.flashscore.com/football/usa/mls/"),
        ("Brasileirao",       "🇧🇷", "https://www.flashscore.com/football/brazil/serie-a/"),
        ("Copa Libertadores", "🌎", "https://www.flashscore.com/football/south-america/copa-libertadores/"),
    ]

    all_fix = []
    today_str = datetime.now().strftime("%Y-%m-%d")

    try:
        async with async_playwright() as p:
            browser, context = await _build_stealth_browser(p)
            first = True
            for league_name, flag, url in TARGETS:
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
            await browser.close()
    except Exception as exc:
        log.warning("scrape_flashscore_football: %s", exc)

    log.info("Flashscore Football total: %d fixtures", len(all_fix))
    return all_fix


async def run_playwright_scrapers() -> None:
    """Runs NBA + Football Flashscore scrapers in parallel, populates global caches."""
    global _pw_nba_games, _pw_football_fixtures

    if not (PLAYWRIGHT_OK and PLAYWRIGHT_ENABLED):
        log.info("Playwright: no disponible/desactivado → Capa 4 saltada")
        return

    log.info("Playwright v4.3: iniciando scrapers en paralelo...")
    try:
        nba_res, football_res = await asyncio.gather(
            scrape_flashscore_nba(),
            scrape_flashscore_football(),
            return_exceptions=True,
        )

        if isinstance(nba_res, list):
            _pw_nba_games = nba_res
        else:
            log.warning("Playwright NBA falló: %s", nba_res)

        if isinstance(football_res, list):
            _pw_football_fixtures = football_res
        else:
            log.warning("Playwright Football falló: %s", football_res)

        log.info("Playwright completo → NBA:%d Fútbol:%d",
                 len(_pw_nba_games), len(_pw_football_fixtures))
    except Exception as exc:
        log.warning("run_playwright_scrapers: %s", exc)
