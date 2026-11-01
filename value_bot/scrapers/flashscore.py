"""Flashscore scrapers de fútbol (Capa 4) y run_playwright_scrapers."""
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from value_bot.config import FALLBACK_LEAGUES_FOOTBALL, PLAYWRIGHT_ENABLED, PLAYWRIGHT_TIMEOUT, afl_season
from value_bot.store import store
from value_bot.scrapers.playwright_scraper import (
    BS4_OK, PLAYWRIGHT_OK,
    _build_stealth_browser, _human_delay, _accept_cookies,
    _parse_flashscore_item,
)

log = logging.getLogger(__name__)

FLASHSCORE_TZ = ZoneInfo("America/New_York")  # la del browser (_build_stealth_browser)

# Nombre = clave de FALLBACK_LEAGUES_FOOTBALL (bandera, cuota de The Odds API y temporada)
_FOOTBALL_TARGETS = [
    ("MLS",               "https://www.flashscore.com/football/usa/mls/"),
    ("Brasileirao",       "https://www.flashscore.com/football/brazil/serie-a/"),
    ("Copa Libertadores", "https://www.flashscore.com/football/south-america/copa-libertadores/"),
]


def _build_fixture(league: str, info: dict, match: dict, today: date) -> dict:
    """Fixture de la Capa 4 con la info de la liga; la hora HH:MM de Flashscore es de hoy en ET."""
    hour, minute = map(int, match["time"].split(":"))
    kickoff = datetime(today.year, today.month, today.day, hour, minute, tzinfo=FLASHSCORE_TZ)
    return {
        "sport": "football",
        "league": league,
        "flag": info.get("flag", ""),
        "fd_code": None,
        "afl_lid": None,
        "afl_season": afl_season(info),
        "odds_key": info.get("odds"),
        "date": today.isoformat(),
        "kickoff": kickoff.isoformat(),
        "home": match["home"],
        "away": match["away"],
        "home_id_fd": None,
        "away_id_fd": None,
        "home_id_afl": None,
        "away_id_afl": None,
        "source": "flashscore_pw",
    }


async def _league_html(context, url: str, accept_cookies: bool) -> str:
    page = await context.new_page()
    try:
        await page.goto(url, timeout=PLAYWRIGHT_TIMEOUT, wait_until="domcontentloaded")
        await _human_delay(1800, 4000)
        if accept_cookies:
            await _accept_cookies(page)
        for _ in range(2):
            await page.evaluate("window.scrollBy(0, 1500)")
            await _human_delay(700, 1300)
        return await page.content()
    finally:
        await page.close()


def _log_no_matches(league: str, items: list) -> None:
    """Sin estado en ningún item = selector roto; con estado (Finished, 45'…) = liga sin partidos hoy."""
    with_status = sum(1 for it in items
                      if (el := it.select_one(".event__time")) and el.get_text(strip=True))
    if with_status:
        log.info("Flashscore %s: %d items, sin partidos por jugar hoy (resultados/en vivo/otros días)",
                 league, len(items))
    else:
        log.warning("Flashscore %s: %d items, todos sin hora (¿selector .event__time roto?)",
                    league, len(items))


def _parse_league(html: str, league: str, today: date) -> list:
    from bs4 import BeautifulSoup
    items = BeautifulSoup(html, "lxml").select(".event__match")
    matches = [m for m in map(_parse_flashscore_item, items) if m]
    if items and not matches:
        _log_no_matches(league, items)
    info = FALLBACK_LEAGUES_FOOTBALL[league]
    return [_build_fixture(league, info, m, today) for m in matches]


async def _scrape_football_with_context(context) -> list:
    """Football scraping usando un context Playwright ya creado; una liga que falla no tumba las demás."""
    all_fix = []
    today = datetime.now(FLASHSCORE_TZ).date()

    for i, (league, url) in enumerate(_FOOTBALL_TARGETS):
        if i:
            await _human_delay(800, 2000)   # pausa solo entre ligas
        try:
            html = await _league_html(context, url, accept_cookies=(i == 0))
            fixtures = _parse_league(html, league, today)
            all_fix.extend(fixtures)
            log.info("Flashscore %s: %d fixtures", league, len(fixtures))
        except Exception as exc:
            log.warning("Flashscore %s: %s", league, exc)

    log.info("Flashscore Football total: %d fixtures", len(all_fix))
    return all_fix


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
