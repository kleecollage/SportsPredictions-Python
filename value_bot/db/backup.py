"""Respaldo de picks_history.db (fuera de git): copia consistente por corrida, nunca tumba la corrida."""
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from value_bot.config import DB_BACKUP_DIR, DB_BACKUP_KEEP
from value_bot.db.connection import connect

log = logging.getLogger(__name__)

BACKUP_GLOB = "picks_history-*.db"


def backup_db(dest: Path | None = None, keep: int = DB_BACKUP_KEEP) -> Path | None:
    """
    Copia la BD a `dest` (DB_BACKUP_DIR) con la API de backup de sqlite (consistente aunque
    haya escrituras) y deja las `keep` (≥ 1) copias más nuevas. Los picks nunca se borran: si la
    copia tiene menos que la anterior (BD perdida y recreada vacía) se avisa y no se rota, para
    no empujar fuera las buenas. Sin la carpeta padre (sin iCloud Drive) o ante cualquier error
    se avisa y devuelve None: un respaldo fallido no frena el envío.
    """
    dest = dest or DB_BACKUP_DIR
    if not dest.parent.is_dir():
        log.warning("Respaldo de la BD saltado: no existe %s", dest.parent)
        return None
    target = dest / f"picks_history-{datetime.now():%Y%m%d-%H%M%S}.db"
    try:
        dest.mkdir(exist_ok=True)
        previous = _newest(dest)
        with connect() as src, connect(target) as dst:
            src.backup(dst)
        if _shrunk(previous, target):
            log.error("Respaldo de la BD con menos picks que %s → no se borran copias viejas "
                      "(¿se perdió picks_history.db? restaurar desde %s)", previous.name, dest)
        else:
            _prune(dest, max(keep, 1))
    except (OSError, sqlite3.Error) as exc:
        log.warning("Respaldo de la BD falló (%s): %s", target, exc)
        return None
    log.info("Respaldo de la BD: %s", target)
    return target


def _newest(dest: Path) -> Path | None:
    copies = sorted(dest.glob(BACKUP_GLOB))   # el nombre lleva la fecha
    return copies[-1] if copies else None


def _pick_count(path: Path) -> int | None:
    """Picks de una copia; None si no es una BD legible con la tabla picks."""
    try:
        with connect(path) as conn:
            return conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0]
    except sqlite3.Error:
        return None


def _shrunk(previous: Path | None, target: Path) -> bool:
    before = _pick_count(previous) if previous else None
    return before is not None and (_pick_count(target) or 0) < before


def _prune(dest: Path, keep: int) -> None:
    """Borra las copias más viejas más allá de `keep`."""
    for old in sorted(dest.glob(BACKUP_GLOB))[:-keep]:
        old.unlink()
