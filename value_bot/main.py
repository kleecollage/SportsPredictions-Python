#!/usr/bin/env python3
"""Value Bot Pro v4.6 — entry point."""
import asyncio
import logging
import sys
from datetime import datetime

from value_bot.config import SPORTS_ENABLED
from value_bot.scrapers.playwright_scraper import PLAYWRIGHT_OK
from value_bot.scrapers.flashscore import run_playwright_scrapers
from value_bot.analysis.picks import get_football_picks, get_nba_picks
from value_bot.analysis.nfl_picks import get_nfl_picks
from value_bot.analysis.parlays import build_parlays
from value_bot.scrapers.espn import get_upcoming_nba_events
from value_bot.telegram_sender import build_message, build_weekly_report, send_telegram
from value_bot.store import store
from value_bot.db import init_db, save_picks, resolve_nba_picks_auto, get_roi_summary, get_model_calibration, get_recent_picks

try:
    from value_bot.sports.nba import NBA_API_OK as _NBA_API_OK
except ImportError:
    _NBA_API_OK = False

log = logging.getLogger(__name__)


async def main():
    log.info("═══ Value Bot Pro v4.6 · Multi-Deporte + Playwright ═══")
    active_sports = [k for k, v in SPORTS_ENABLED.items() if v]
    log.info("Deportes activos: %s", active_sports)
    if not active_sports:
        log.info("Sin deportes activos en SPORTS_ENABLED → no se scrapea ni se envía mensaje")
        return
    log.info("nba_api disponible: %s | playwright: %s", _NBA_API_OK, PLAYWRIGHT_OK)

    # Limpiar caches del run anterior para garantizar datos frescos
    store.clear_run_caches()

    # Inicializar DB de historial
    init_db()

    # Capa 4: Playwright scrapers antes de generar picks (solo cubre fútbol/NBA)
    if SPORTS_ENABLED.get("football") or SPORTS_ENABLED.get("nba"):
        await run_playwright_scrapers()

    async def _empty():
        return []

    loop = asyncio.get_event_loop()
    football_picks, nba_picks, nfl_picks = await asyncio.gather(
        loop.run_in_executor(None, get_football_picks) if SPORTS_ENABLED.get("football") else _empty(),
        loop.run_in_executor(None, get_nba_picks)      if SPORTS_ENABLED.get("nba")      else _empty(),
        loop.run_in_executor(None, get_nfl_picks)      if SPORTS_ENABLED.get("nfl")      else _empty(),
    )

    all_picks = football_picks + nba_picks + nfl_picks
    parlays   = build_parlays(all_picks)
    upcoming  = get_upcoming_nba_events() if SPORTS_ENABLED.get("nba") else []
    perf      = get_roi_summary(days=7)
    message   = build_message(football_picks, nba_picks, parlays, upcoming, perf=perf, nflp=nfl_picks)

    await send_telegram(message)

    run_date = datetime.now().strftime("%Y-%m-%d")

    # Persistir picks del día
    inserted = save_picks(run_date, all_picks)

    # Resolver automáticamente picks de NBA del día anterior via ESPN
    from datetime import timedelta
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    resolved  = resolve_nba_picks_auto(yesterday)
    if resolved:
        log.info("Auto-resolved %d NBA picks de %s", resolved, yesterday)

    log.info("═══ ✓ Enviado | ⚽%d 🏀%d 🏈%d | Parlays:%d | DB:%d picks ═══",
             len(football_picks), len(nba_picks), len(nfl_picks), len(parlays), inserted)


async def weekly_report():
    """Envía reporte semanal completo. Diseñado para correr cada lunes."""
    log.info("═══ Value Bot Pro — Reporte Semanal ═══")
    init_db()
    roi    = get_roi_summary(days=7)
    calib  = get_model_calibration(min_picks=5)
    recent = get_recent_picks(days=7)
    msg    = build_weekly_report(roi, calib, recent)
    await send_telegram(msg)
    log.info("═══ ✓ Reporte semanal enviado ═══")


if __name__ == "__main__":
    if "--weekly" in sys.argv:
        asyncio.run(weekly_report())
    else:
        asyncio.run(main())
