"""Playwright stealth browser utilities shared by all scrapers."""
import asyncio
import random
import logging

from value_bot.config import USER_AGENTS, STEALTH_SCRIPT, PLAYWRIGHT_TIMEOUT

try:
    from bs4 import BeautifulSoup
    BS4_OK = True
except ImportError:
    BS4_OK = False

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False

log = logging.getLogger(__name__)


async def _human_delay(min_ms: int = 800, max_ms: int = 2500) -> None:
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def _build_stealth_browser(playwright):
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ],
    )
    ua = random.choice(USER_AGENTS)
    context = await browser.new_context(
        user_agent=ua,
        viewport={"width": random.randint(1280, 1920), "height": random.randint(800, 1080)},
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    await context.add_init_script(STEALTH_SCRIPT)

    async def _block_heavy(route):
        if route.request.resource_type in ("image", "font", "media", "stylesheet"):
            await route.abort()
        else:
            await route.continue_()

    await context.route("**/*", _block_heavy)
    return browser, context


async def _accept_cookies(page) -> None:
    for sel in (
        "#onetrust-accept-btn-handler",
        "button[id*='accept']",
        ".cookieConsent__Button",
        "button.fc-cta-consent",
    ):
        try:
            await page.click(sel, timeout=2000)
            await _human_delay(400, 800)
            return
        except Exception:
            pass


async def _infinite_scroll(page, iters: int = 3) -> None:
    prev = 0
    for _ in range(iters):
        curr = await page.evaluate("document.body.scrollHeight")
        if curr == prev:
            break
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await _human_delay(900, 1800)
        prev = curr


def _parse_flashscore_item(item) -> dict | None:
    """Parse a .event__match div from Flashscore HTML."""
    time_el = item.select_one(".event__time")
    home_el = (item.select_one(".event__participant--home") or
               item.select_one(".event__homeParticipant") or
               item.select_one("[class*='homeParticipant']"))
    away_el = (item.select_one(".event__participant--away") or
               item.select_one(".event__awayParticipant") or
               item.select_one("[class*='awayParticipant']"))
    if not (home_el and away_el):
        return None
    time_txt = time_el.get_text(strip=True) if time_el else ""
    if any(x in time_txt.upper() for x in ("FT", "AET", "PEN", "CANC", "POSTP", "ABD", "WO")):
        return None
    home = home_el.get_text(strip=True)
    away = away_el.get_text(strip=True)
    if not home or not away or home == away:
        return None
    return {"home": home, "away": away, "time": time_txt}
