"""Flashscore scrapers de fútbol (Capa 4) y run_playwright_scrapers."""
import logging
from datetime import datetime

from value_bot.config import PLAYWRIGHT_ENABLED, PLAYWRIGHT_TIMEOUT
from value_bot.store import store
from value_bot.scrapers.playwright_scraper import (
    BS4_OK, PLAYWRIGHT_OK,
    _build_stealth_browser, _human_delay, _accept_cookies,
    _parse_flashscore_item,
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
    """Scrapea los fixtures de fútbol de Flashscore y popula store.pw_football_fixtures."""
    if not (PLAYWRIGHT_OK and PLAYWRIGHT_ENABLED):
        log.info("Playwright: no disponible/desactivado → Capa 4 saltada")
        return

    if not BS4_OK:
        log.warning("Playwright: bs4/lxml no instalado → Capa 4 saltada")
        return

    log.info("Playwright: iniciando scraper de fútbol...")
    from playwright.async_api import async_playwright
    try:
        async with async_playwright() as p:
            browser, context = await _build_stealth_browser(p)
            try:
                store.pw_football_fixtures = await _scrape_football_with_context(context)
            finally:
                await browser.close()
        log.info("Playwright completo → Fútbol:%d", len(store.pw_football_fixtures))
    except Exception as exc:
        log.warning("run_playwright_scrapers: %s", exc)
