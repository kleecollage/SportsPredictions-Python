#!/usr/bin/env python3
"""
Value Bot Pro — APScheduler daemon.

Reemplaza el crontab con un proceso persistente que:
  - Corre el bot diariamente a las 9:00 AM
  - Corre el reporte semanal los lunes a las 9:05 AM
  - Persiste el estado de los jobs en el mismo SQLite de picks
  - Recupera ejecuciones perdidas (misfire_grace_time=3600)

Uso:
    python3 -m value_bot.scheduler

Para detener:
    Ctrl+C  o  kill <pid>

Para instalar como servicio launchd en Mac:
    Copiar value_bot/scheduler.plist a ~/Library/LaunchAgents/
    launchctl load ~/Library/LaunchAgents/com.valuebot.scheduler.plist
"""
import asyncio
import logging

from value_bot.config import validate_env
from value_bot.db import DB_PATH

log = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _build_scheduler():
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as exc:
        raise ImportError(
            "APScheduler no está instalado.\n"
            "Instalar con: pip install 'APScheduler>=3.10' 'SQLAlchemy>=2.0'"
        ) from exc

    jobstores = {
        "default": SQLAlchemyJobStore(url=f"sqlite:///{DB_PATH}")
    }
    scheduler = AsyncIOScheduler(
        jobstores=jobstores,
        timezone="America/New_York",
        job_defaults={
            "coalesce":           True,   # si hay varios misfires pendientes, solo corre uno
            "max_instances":      1,      # nunca dos instancias simultáneas del mismo job
            "misfire_grace_time": 3600,   # corre hasta 1h tarde si el sistema estuvo apagado
        },
    )
    return scheduler, CronTrigger


async def start_scheduler():
    """Inicia el scheduler y bloquea hasta Ctrl+C."""
    validate_env()

    # Importar las funciones de negocio aquí (evitar import circular en top-level)
    from value_bot.main import main, weekly_report

    scheduler, CronTrigger = _build_scheduler()

    scheduler.add_job(
        main,
        CronTrigger(hour=9, minute=0),
        id="daily_picks",
        replace_existing=True,
        name="Picks diarios 9AM",
    )
    log.info("Job registrado: daily_picks — todos los días 9:00 AM (America/New_York)")

    scheduler.add_job(
        weekly_report,
        CronTrigger(day_of_week="mon", hour=9, minute=5),
        id="weekly_report",
        replace_existing=True,
        name="Reporte semanal lunes 9:05AM",
    )
    log.info("Job registrado: weekly_report — lunes 9:05 AM (America/New_York)")

    scheduler.start()

    next_jobs = scheduler.get_jobs()
    for job in next_jobs:
        log.info("  → %s  |  próxima ejecución: %s", job.name, job.next_run_time)

    log.info("APScheduler activo. Ctrl+C para detener.")
    try:
        while True:
            await asyncio.sleep(3600)   # heartbeat cada hora
    except (KeyboardInterrupt, SystemExit):
        log.info("Apagando scheduler...")
        scheduler.shutdown(wait=False)
        log.info("Scheduler detenido.")


if __name__ == "__main__":
    asyncio.run(start_scheduler())
