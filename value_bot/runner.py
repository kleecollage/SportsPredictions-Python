"""
Runner de launchd (`python -m value_bot.runner`, cada 15 min y al iniciar sesión): corre lo que
toque en hora ET y registra cada intento en la BD. La Mac no siempre está prendida: un día que
arranca tarde corre los picks al prender, y un lunes perdido manda el reporte en cuanto puede.

- Picks diarios: desde las 9:00 ET, una vez por día ET.
- Reporte semanal: desde el lunes 9:05 ET, una vez por semana (se recupera hasta el domingo).
- Un intento fallido se reintenta en la siguiente pasada, hasta MAX_ATTEMPTS por turno.
- Uno en curso ('started' de menos de RUN_STALE_MINUTES, p. ej. una corrida a mano) frena a los demás.
- Un 'ok' cuenta para todo el día (o la semana) ET: una corrida a mano a las 8:00 no se repite a las 9:00.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from value_bot.db import finish_run, get_runs, init_db, start_run

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
DAILY_AT = time(9, 0)
WEEKLY_AT = time(9, 5)
WEEKLY_WEEKDAY = 0          # lunes
MAX_ATTEMPTS = 3            # por turno: un error de red al prender no pierde el día
HISTORY_DAYS = 8            # alcanza para el turno semanal más viejo
RUN_STALE_MINUTES = 60      # un 'started' más viejo se toma como corrida muerta (se reintenta)


def _period_day(kind: str, now: datetime) -> date:
    """Día ET en que empieza el período de `kind`: hoy (diario) o el lunes de esta semana."""
    today = now.astimezone(ET).date()
    return today if kind == "daily" else today - timedelta(days=(today.weekday() - WEEKLY_WEEKDAY) % 7)


def slot_start(kind: str, now: datetime) -> datetime | None:
    """Desde cuándo toca `kind` en el período vigente, o None si todavía no es la hora."""
    start = datetime.combine(_period_day(kind, now), DAILY_AT if kind == "daily" else WEEKLY_AT, ET)
    return start if now.astimezone(ET) >= start else None


def is_due(kind: str, now: datetime, runs: list[tuple[datetime, str]]) -> bool:
    """
    Toca si ya es la hora, nada salió 'ok' en el período (desde la medianoche ET del día o
    del lunes, así cuenta una corrida a mano temprano), ninguna sigue en curso y quedan intentos.
    """
    if slot_start(kind, now) is None:
        return False
    period = datetime.combine(_period_day(kind, now), time(0), ET)
    in_period = [(when, status) for when, status in runs if when >= period]
    if any(status == "ok" for _, status in in_period):
        return False
    stale = timedelta(minutes=RUN_STALE_MINUTES)
    if any(status == "started" and now - when < stale for when, status in in_period):
        log.info("Runner: %s ya está corriendo → no se lanza otra", kind)
        return False
    return len(in_period) < MAX_ATTEMPTS


def _daily() -> Awaitable[None]:
    from value_bot.main import main   # import pesado solo si toca correr
    return main()


def _weekly() -> Awaitable[None]:
    from value_bot.main import weekly_report
    return weekly_report()


JOBS: dict[str, Callable[[], Awaitable[None]]] = {"daily": _daily, "weekly": _weekly}


async def run_recorded(kind: str, job: Callable[[], Awaitable[None]], now: datetime) -> bool:
    """Corre `job` registrando el intento; un fallo se loguea y no sube."""
    run_id = start_run(kind, now)
    try:
        await job()
    except Exception:
        log.exception("Runner: %s falló (se reintenta en la próxima pasada si quedan intentos)", kind)
        finish_run(run_id, ok=False)
        return False
    finish_run(run_id, ok=True)
    return True


async def run_due(now: datetime | None = None) -> None:
    """Corre cada trabajo que toque (diario antes que semanal)."""
    now = now or datetime.now(ET)
    init_db()
    for kind, job in JOBS.items():
        if is_due(kind, now, get_runs(kind, since=now - timedelta(days=HISTORY_DAYS))):
            log.info("Runner: toca %s", kind)
            await run_recorded(kind, job, now)


if __name__ == "__main__":
    from value_bot.config import validate_env
    validate_env()
    asyncio.run(run_due())
