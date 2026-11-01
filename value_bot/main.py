#!/usr/bin/env python3
"""Value Bot Pro v4.6 — entry point."""
import asyncio
import logging
import sys
from datetime import datetime

from value_bot.config import MODEL_SINCE, SPORTS_ENABLED
from value_bot.scrapers.playwright_scraper import PLAYWRIGHT_OK
from value_bot.scrapers.flashscore import run_playwright_scrapers
from value_bot.analysis.picks import get_football_picks
from value_bot.analysis.nba_picks import get_nba_picks
from value_bot.analysis.nfl_picks import get_nfl_picks
from value_bot.analysis.parlays import build_parlays
from value_bot.scrapers.espn import get_upcoming_nba_events
from value_bot.telegram_sender import build_message, build_weekly_report, send_telegram
from value_bot.store import store
from value_bot.db import (
    get_model_calibration, get_parlay_summary, get_recent_picks, get_roi_summary, init_db, save_parlays, save_picks,
)
from value_bot.resolver import resolve_parlays, resolve_pending_picks

try:
    from value_bot.sports.nba import NBA_API_OK as _NBA_API_OK
except ImportError:
    _NBA_API_OK = False

log = logging.getLogger(__name__)


PICK_SOURCES = {"football": get_football_picks, "nba": get_nba_picks, "nfl": get_nfl_picks}


def _safe_picks(sport: str) -> list:
    """Picks de un deporte; si revienta se loguea y se envían los demás."""
    try:
        return PICK_SOURCES[sport]()
    except Exception:
        log.exception("Picks %s fallaron → se envía el mensaje sin ese deporte", sport)
        return []


async def _collect_picks() -> dict[str, list]:
    """Picks de los deportes activos en paralelo; {deporte: [...]} con [] para los apagados."""
    loop = asyncio.get_running_loop()
    active = [sport for sport in PICK_SOURCES if SPORTS_ENABLED.get(sport)]
    results = await asyncio.gather(*(loop.run_in_executor(None, _safe_picks, s) for s in active))
    return {sport: [] for sport in PICK_SOURCES} | dict(zip(active, results))


def _current_scope() -> dict:
    """Filtro de rendimiento: deportes activos y picks de su modelo vigente (MODEL_SINCE)."""
    return {"sports": [sport for sport, on in SPORTS_ENABLED.items() if on], "model_since": MODEL_SINCE}


def _current_performance() -> dict:
    """ROI de 7 días del modelo vigente."""
    return get_roi_summary(days=7, **_current_scope())


def _resolve_results():
    """Resuelve picks y luego parlays pendientes; un fallo aquí no debe frenar el envío."""
    try:
        resolve_pending_picks()
    except Exception:
        log.exception("Resolución de resultados falló")
    try:
        resolve_parlays()
    except Exception:
        log.exception("Resolución de parlays falló")


def _save_parlays(run_date: str, parlays: list) -> None:
    """El mensaje ya salió: un fallo al guardar parlays se loguea y la corrida sigue."""
    try:
        save_parlays(run_date, parlays)
    except Exception:
        log.exception("Guardar parlays falló")


async def main() -> None:
    log.info("═══ Value Bot Pro v4.6 · Multi-Deporte + Playwright ═══")
    active_sports = [k for k, v in SPORTS_ENABLED.items() if v]
    log.info("Deportes activos: %s", active_sports)
    if not active_sports:
        log.info("Sin deportes activos en SPORTS_ENABLED → no se scrapea ni se envía mensaje")
        return
    log.info("nba_api disponible: %s | playwright: %s", _NBA_API_OK, PLAYWRIGHT_OK)

    # Limpiar caches del run anterior para garantizar datos frescos
    store.clear_run_caches()

    # Inicializar DB de historial y resolver juegos ya terminados (para el ROI del mensaje)
    init_db()
    _resolve_results()

    # Capa 4 fútbol: Playwright antes de generar picks. La de NBA se abre bajo demanda
    # dentro de get_nba_games, solo si ESPN / api-sports / BBC no dieron juegos.
    if SPORTS_ENABLED.get("football"):
        await run_playwright_scrapers(nba=False, football=True)

    picks = await _collect_picks()
    football_picks, nba_picks, nfl_picks = picks["football"], picks["nba"], picks["nfl"]

    all_picks = football_picks + nba_picks + nfl_picks
    parlays   = build_parlays(all_picks)
    upcoming  = get_upcoming_nba_events() if SPORTS_ENABLED.get("nba") else []
    perf      = _current_performance()
    message   = build_message(football_picks, nba_picks, parlays, upcoming, perf=perf, nflp=nfl_picks,
                              analyzed=dict(store.games_analyzed))

    await send_telegram(message)

    run_date = datetime.now().strftime("%Y-%m-%d")

    # Persistir picks del día y los parlays (ligados a esos picks)
    inserted = save_picks(run_date, all_picks)
    _save_parlays(run_date, parlays)

    log.info("═══ ✓ Enviado | ⚽%d 🏀%d 🏈%d | Parlays:%d | DB:%d picks ═══",
             len(football_picks), len(nba_picks), len(nfl_picks), len(parlays), inserted)


async def weekly_report():
    """Envía reporte semanal completo. Diseñado para correr cada lunes."""
    log.info("═══ Value Bot Pro — Reporte Semanal ═══")
    init_db()
    _resolve_results()
    roi    = _current_performance()
    calib  = get_model_calibration(min_picks=5, **_current_scope())
    recent = get_recent_picks(days=7, **_current_scope())
    msg    = build_weekly_report(roi, calib, recent, parlays=get_parlay_summary(days=7, **_current_scope()))
    await send_telegram(msg)
    log.info("═══ ✓ Reporte semanal enviado ═══")


if __name__ == "__main__":
    if "--weekly" in sys.argv:
        asyncio.run(weekly_report())
    else:
        asyncio.run(main())
