#!/usr/bin/env python3
"""Value Bot Pro v4.6 — entry point."""
import asyncio
import logging
from datetime import datetime

from value_bot.config import SPORTS_ENABLED
from value_bot.scrapers.playwright_scraper import PLAYWRIGHT_OK
from value_bot.scrapers.flashscore import run_playwright_scrapers
from value_bot.analysis.picks import get_football_picks, get_nba_picks
from value_bot.analysis.parlays import build_parlays
from value_bot.scrapers.espn import get_upcoming_nba_events
from value_bot.telegram_sender import build_message, send_telegram
from value_bot.store import store
from value_bot.db import init_db, save_picks

try:
    from value_bot.sports.nba import NBA_API_OK as _NBA_API_OK
except ImportError:
    _NBA_API_OK = False

log = logging.getLogger(__name__)


async def main():
    log.info("═══ Value Bot Pro v4.6 · Multi-Deporte + Playwright ═══")
    log.info("Deportes activos: %s", [k for k, v in SPORTS_ENABLED.items() if v])
    log.info("nba_api disponible: %s | playwright: %s", _NBA_API_OK, PLAYWRIGHT_OK)

    # Limpiar caches del run anterior para garantizar datos frescos
    store.clear_run_caches()

    # Inicializar DB de historial
    init_db()

    # Capa 4: Playwright scrapers antes de generar picks
    await run_playwright_scrapers()

    async def _empty():
        return []

    loop = asyncio.get_event_loop()
    football_picks, nba_picks = await asyncio.gather(
        loop.run_in_executor(None, get_football_picks) if SPORTS_ENABLED.get("football") else _empty(),
        loop.run_in_executor(None, get_nba_picks)      if SPORTS_ENABLED.get("nba")      else _empty(),
    )

    all_picks = football_picks + nba_picks
    parlays   = build_parlays(all_picks)
    upcoming  = get_upcoming_nba_events() if SPORTS_ENABLED.get("nba") else []
    message   = build_message(football_picks, nba_picks, parlays, upcoming)

    await send_telegram(message)

    # Persistir picks en SQLite para backtesting / deduplicación futura
    run_date = datetime.now().strftime("%Y-%m-%d")
    save_picks(run_date, all_picks)

    log.info("═══ ✓ Enviado | ⚽%d 🏀%d | Parlays:%d ═══",
             len(football_picks), len(nba_picks), len(parlays))


if __name__ == "__main__":
    asyncio.run(main())
