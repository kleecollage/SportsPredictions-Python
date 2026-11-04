"""Ruta de la BD y conexión: el único lugar que parchear en tests (`db.connection.DB_PATH`)."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "picks_history.db"


def connect() -> sqlite3.Connection:
    """Conexión a DB_PATH leída en cada llamada (los tests la redirigen a una BD temporal)."""
    return sqlite3.connect(DB_PATH)
