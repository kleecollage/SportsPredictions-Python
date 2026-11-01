"""Playwright stealth browser utilities shared by all scrapers."""
import asyncio
import logging
import random
import re
from importlib.util import find_spec

from value_bot.config import USER_AGENTS, STEALTH_SCRIPT


def _has_modules(*names: str) -> bool:
    return all(find_spec(name) is not None for name in names)


BS4_OK = _has_modules("bs4", "lxml")       # los scrapers parsean con BeautifulSoup(html, "lxml")
PLAYWRIGHT_OK = _has_modules("playwright")

log = logging.getLogger(__name__)


async def _human_delay(min_ms: int = 800, max_ms: int = 2500) -> None:
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def _build_stealth_browser(playwright):
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
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


COOKIE_SELECTORS = (
    "#onetrust-accept-btn-handler",
    ".cookieConsent__Button",
    "button.fc-cta-consent",
)
COOKIE_TIMEOUT_MS = 2000
_KICKOFF_RE = re.compile(r"\d{1,2}:\d{2}")


async def _accept_cookies(page) -> None:
    """Un solo intento contra todos los avisos conocidos; sin aviso no es un error."""
    try:
        await page.click(", ".join(COOKIE_SELECTORS), timeout=COOKIE_TIMEOUT_MS)
        await _human_delay(400, 800)
    except Exception as exc:
        log.debug("Sin aviso de cookies que aceptar: %s", exc)


def _parse_flashscore_item(item) -> dict | None:
    """Parse a .event__match div from Flashscore HTML; None si no es un partido por jugar."""
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
    # La página mezcla resultados, en vivo y otros días: solo HH:MM es un partido por jugar
    if not _KICKOFF_RE.fullmatch(time_txt):
        return None
    home = home_el.get_text(strip=True)
    away = away_el.get_text(strip=True)
    if not home or not away or home == away:
        return None
    return {"home": home, "away": away, "time": time_txt}
