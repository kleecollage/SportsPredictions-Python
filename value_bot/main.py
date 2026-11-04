#!/usr/bin/env python3
"""Value Bot Pro v4.6 — entry point."""
import asyncio
import logging
import sys
from collections.abc import Callable
from datetime import datetime

from value_bot.config import FLASHSCORE_ENABLED, MODEL_SINCE, validate_env
from value_bot.scrapers.playwright_scraper import PLAYWRIGHT_OK
from value_bot.scrapers.flashscore import run_playwright_scrapers
from value_bot.analysis.football_picks import get_btts_picks, get_football_picks
from value_bot.analysis.nba_picks import get_nba_picks
from value_bot.analysis.nfl_picks import get_nfl_picks
from value_bot.analysis.parlays import build_parlays
from value_bot.analysis.pick import Pick, keep_valid
from value_bot.scrapers.espn import get_upcoming_nba_events
from value_bot.telegram_sender import build_message, build_weekly_report, send_telegram
from value_bot.sports_registry import SPORTS_BY_KEY, active_sports, sport_keys
from value_bot.run_context import RunContext
from value_bot.store import store
from value_bot.db import (
    get_model_calibration, get_parlay_summary, get_recent_picks, get_roi_summary, init_db, save_parlays, save_picks,
)
from value_bot.resolver import resolve_parlays, resolve_pending_picks
from value_bot.tools.league_picker import LeagueOption, pick_leagues, render_decisions

try:
    from value_bot.sports.nba import NBA_API_OK as _NBA_API_OK
except ImportError:
    _NBA_API_OK = False

log = logging.getLogger(__name__)


def _from_store(get_picks: Callable[[], list[Pick]], sport: str) -> Callable[[RunContext], list[Pick]]:
    """
    Deportes que aún usan `store`: se corren igual y su conteo se copia al contexto, también
    si fallan (como NFL, que escribe ctx.games_analyzed antes de poder fallar).
    """
    def run(ctx: RunContext) -> list[Pick]:
        try:
            return get_picks()
        finally:
            if sport in store.games_analyzed:
                ctx.games_analyzed[sport] = store.games_analyzed[sport]
    return run


# Cada fuente recibe el RunContext de la corrida (fútbol todavía vía store)
PICK_SOURCES = {"football": _from_store(get_football_picks, "football"),
                "nba": get_nba_picks,
                "nfl": get_nfl_picks}
BTTS_FLAG = "--btts"   # BTTS gasta créditos por partido: solo a mano, nunca desde el scheduler


def _safe_picks(sport: str, ctx: RunContext) -> list[Pick]:
    """Picks de un deporte que cumplen el contrato; si revienta se loguea y se envían los demás."""
    try:
        return keep_valid(PICK_SOURCES[sport](ctx), sport)
    except Exception:
        log.exception("Picks %s fallaron → se envía el mensaje sin ese deporte", sport)
        return []


def _choose_btts_leagues(counts: dict[str, int], credits_left: int | None) -> frozenset[str]:
    options = [LeagueOption(league, n) for league, n in counts.items()]
    return pick_leagues(options, credits_left)


def _btts_picks(football_picks: list[dict], argv: list[str]) -> list[dict]:
    """BTTS bajo demanda (`--btts`): selector de ligas en la terminal y árbol por partido."""
    if BTTS_FLAG not in argv or "football" not in active_sports():
        return []
    try:
        picks, decisions = get_btts_picks(football_picks, _choose_btts_leagues)
    except Exception:
        log.exception("BTTS bajo demanda falló → el mensaje sale sin BTTS")
        return []
    if decisions:
        print(render_decisions(decisions))
    return keep_valid(picks, "football")


def _upcoming(ctx: RunContext) -> list:
    """Sección informativa "Próximos partidos": si falla, el mensaje sale sin ella."""
    if "nba" not in active_sports():
        return []
    try:
        return get_upcoming_nba_events(after=max(ctx.nba.slate_dates, default=None))
    except Exception:
        log.exception("Próximos partidos NBA fallaron → mensaje sin esa sección")
        return []


async def _collect_picks(ctx: RunContext) -> dict[str, list[Pick]]:
    """Picks de los deportes activos en paralelo; {deporte: [...]} con [] para los apagados."""
    loop = asyncio.get_running_loop()
    active = active_sports()
    results = await asyncio.gather(*(loop.run_in_executor(None, _safe_picks, s, ctx) for s in active))
    return {sport: [] for sport in sport_keys()} | dict(zip(active, results))


def _current_scope() -> dict:
    """Filtro de rendimiento: deportes activos y picks de su modelo vigente (MODEL_SINCE)."""
    return {"sports": active_sports(), "model_since": MODEL_SINCE}


def _current_performance() -> dict:
    """ROI de 7 días del modelo vigente."""
    return get_roi_summary(days=7, **_current_scope())


async def _football_scrapers():
    """Capa 4 fútbol: Playwright Flashscore antes de los picks, solo con FLASHSCORE_ENABLED."""
    if "football" in active_sports() and FLASHSCORE_ENABLED:
        await run_playwright_scrapers()


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


async def main(argv: list[str] | None = None) -> None:
    """`argv` con BTTS_FLAG abre el selector de BTTS (por defecto nunca: el scheduler no lo pasa)."""
    log.info("═══ Value Bot Pro v4.6 · Multi-Deporte + Playwright ═══")
    active = active_sports()
    log.info("Deportes activos: %s", active)
    if not active:
        log.info("Sin deportes activos en SPORTS_ENABLED → no se scrapea ni se envía mensaje")
        return
    log.info("nba_api disponible: %s | playwright: %s", _NBA_API_OK, PLAYWRIGHT_OK)

    # Limpiar caches del run anterior para garantizar datos frescos
    store.clear_run_caches()

    # Inicializar DB de historial y resolver juegos ya terminados (para el ROI del mensaje)
    init_db()
    _resolve_results()

    await _football_scrapers()

    ctx = RunContext()
    picks = await _collect_picks(ctx)
    picks = {**picks, "football": picks["football"] + _btts_picks(picks["football"], argv or [])}

    all_picks = [pick for sport in sport_keys() for pick in picks[sport]]
    parlays   = build_parlays(all_picks)
    upcoming  = _upcoming(ctx)
    perf      = _current_performance()
    message   = build_message(picks, parlays, upcoming, perf=perf, analyzed=dict(ctx.games_analyzed),
                              nba_form=ctx.nba.form)

    await send_telegram(message)

    run_date = datetime.now().strftime("%Y-%m-%d")

    # Persistir picks del día y los parlays (ligados a esos picks)
    inserted = save_picks(run_date, all_picks)
    _save_parlays(run_date, parlays)

    per_sport = " ".join(f"{SPORTS_BY_KEY[sport].emoji}{len(picks[sport])}" for sport in sport_keys())
    log.info("═══ ✓ Enviado | %s | Parlays:%d | DB:%d picks ═══", per_sport, len(parlays), inserted)


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
    validate_env()
    if "--weekly" in sys.argv:
        asyncio.run(weekly_report())
    else:
        asyncio.run(main(sys.argv[1:]))
