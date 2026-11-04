"""Ruta de la BD y conexión: el único lugar que parchear en tests (`db.connection.DB_PATH`)."""
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from value_bot.config import REPO_ROOT

DB_PATH = REPO_ROOT / "picks_history.db"


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """
    Conexión a DB_PATH leída en cada llamada (los tests la redirigen); `path` solo para el
    respaldo. Commit al salir (rollback si hubo error) y **siempre** se cierra: el `with` de
    sqlite3 solo hace commit y dejaba los archivos abiertos.
    """
    conn = sqlite3.connect(path or DB_PATH)
    try:
        with conn:
            yield conn
    finally:
        conn.close()
