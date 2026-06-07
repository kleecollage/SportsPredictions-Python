"""ESPN Hidden API, BBC Sport scraping, and ESPN Playwright fallback."""
import logging
import requests
from datetime import datetime, timedelta

from value_bot.config import ESPN_NBA_URL, http_get, PLAYWRIGHT_ENABLED
from value_bot.scrapers.playwright_scraper import PLAYWRIGHT_OK, _build_stealth_browser, _human_delay

try:
    from bs4 import BeautifulSoup
    BS4_OK = True
except ImportError:
    BS4_OK = False

log = logging.getLogger(__name__)


def espn_nba_games(date_str):
    data = http_get(
        ESPN_NBA_URL,
        params={"dates": date_str.replace("-", "")},
        skip_codes=(404,),
    )
    return data.get("events", [])


def espn_parse_nba(event):
    comps = event.get("competitions", [])
    if not comps:
        return None
    comp = comps[0]
    status = comp.get("status", {}).get("type", {}).get("name", "")
    if status in ("STATUS_FINAL", "STATUS_CANCELED", "STATUS_POSTPONED"):
        return None
    competitors = comp.get("competitors", [])
    if len(competitors) < 2:
        return None
    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

    def record(c):
        recs = c.get("records", [])
        return recs[0].get("summary", "?") if recs else "?"

    notes      = comp.get("notes", [])
    series_txt = notes[0].get("headline", "") if notes else ""
    is_finals  = "Finals" in series_txt or "final" in series_txt.lower()

    return {
        "home":           home.get("team", {}).get("displayName", "?"),
        "away":           away.get("team", {}).get("displayName", "?"),
        "home_record":    record(home),
        "away_record":    record(away),
        "kickoff":        event.get("date", ""),
        "is_finals":      is_finals,
        "series_summary": series_txt,
        "venue":          comp.get("venue", {}).get("fullName", ""),
    }


def get_upcoming_nba_events(days=5):
    """Next NBA games (1-5 days ahead) for the Upcoming section."""
    upcoming = []
    today = datetime.now()
    for d in range(1, days + 1):
        ds = (today + timedelta(days=d)).strftime("%Y-%m-%d")
        for ev in espn_nba_games(ds):
            p = espn_parse_nba(ev)
            if p:
                upcoming.append({**p, "date": ds})
        if upcoming:
            break
    return upcoming[:3]


def scrape_bbc_sport_nba():
    if not BS4_OK:
        return []
    try:
        r = requests.get(
            "https://www.bbc.com/sport/basketball/nba",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        games = []
        for fix in soup.find_all(attrs={"data-testid": "fixture"})[:6]:
            teams = fix.find_all(attrs={"data-testid": "team-name"})
            if len(teams) >= 2:
                games.append({
                    "home": teams[0].get_text(strip=True),
                    "away": teams[1].get_text(strip=True),
                    "kickoff": "",
                })
        log.info("BBC scrape NBA: %d", len(games))
        return games
    except Exception as exc:
        log.warning("BBC scrape error: %s", exc)
        return []


async def scrape_espn_nba_pw() -> dict:
    """ESPN fallback via Playwright with XHR interception."""
    if not (PLAYWRIGHT_OK and PLAYWRIGHT_ENABLED):
        return {}
    from playwright.async_api import async_playwright
    captured: dict = {}
    try:
        async with async_playwright() as p:
            browser, context = await _build_stealth_browser(p)
            page = await context.new_page()

            async def _capture(response):
                if ("site.api.espn.com" in response.url and
                        "nba" in response.url.lower()):
                    try:
                        data = await response.json()
                        if data.get("events"):
                            captured.update(data)
                    except Exception:
                        pass

            page.on("response", _capture)
            try:
                from value_bot.config import PLAYWRIGHT_TIMEOUT
                await page.goto(
                    "https://www.espn.com/nba/scoreboard",
                    timeout=PLAYWRIGHT_TIMEOUT,
                    wait_until="domcontentloaded",
                )
                await _human_delay(4000, 7000)
            finally:
                await page.close()
            await browser.close()
    except Exception as exc:
        log.warning("scrape_espn_nba_pw: %s", exc)

    if captured:
        log.info("ESPN NBA playwright: %d eventos interceptados",
                 len(captured.get("events", [])))
    return captured
