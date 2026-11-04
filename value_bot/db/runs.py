"""Corridas del runner (diaria/semanal): cada intento con su estado, para recuperar las perdidas."""
from datetime import datetime, timezone

from value_bot.db.connection import connect


def _iso(when: datetime) -> str:
    """ISO UTC al segundo: mismo largo siempre, así la comparación de texto ordena bien."""
    return when.astimezone(timezone.utc).isoformat(timespec="seconds")


def start_run(kind: str, when: datetime) -> int:
    """Registra un intento ('started') antes de correrlo: uno que muere a mitad cuenta igual."""
    with connect() as conn:
        return conn.execute("INSERT INTO runs (kind, run_at, status) VALUES (?, ?, 'started')",
                            (kind, _iso(when))).lastrowid


def finish_run(run_id: int, ok: bool) -> None:
    with connect() as conn:
        conn.execute("UPDATE runs SET status = ? WHERE id = ?", ("ok" if ok else "failed", run_id))


def get_runs(kind: str, since: datetime) -> list[tuple[datetime, str]]:
    """[(momento del intento, estado)] de `kind` desde `since`, en orden."""
    with connect() as conn:
        rows = conn.execute("SELECT run_at, status FROM runs WHERE kind = ? AND run_at >= ? ORDER BY id",
                            (kind, _iso(since))).fetchall()
    return [(datetime.fromisoformat(run_at), status) for run_at, status in rows]
