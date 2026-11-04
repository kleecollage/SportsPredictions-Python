"""Resultados de picks: los escribe el resolver y lee los pendientes."""
import json

from value_bot.db.connection import connect


def save_result(pick_id: int, outcome: str, actual_score: str | None,
                profit_units: float | None, source: str = "espn_auto") -> None:
    """Inserta o actualiza el resultado de un pick (uno por pick_id)."""
    with connect() as conn:
        conn.execute("""
            INSERT INTO results (pick_id, outcome, actual_score, profit_units, resolved_at, source)
            VALUES (?, ?, ?, ?, datetime('now'), ?)
            ON CONFLICT(pick_id) DO UPDATE SET
                outcome=excluded.outcome,
                actual_score=excluded.actual_score,
                profit_units=excluded.profit_units,
                resolved_at=excluded.resolved_at,
                source=excluded.source
        """, (pick_id, outcome, actual_score, profit_units, source))
        conn.commit()


def get_unresolved_picks(sports: tuple[str, ...]) -> list[dict]:
    """
    Picks sin resultado (o 'pending') con el payload JSON ya decodificado.
    No filtra por fecha: quien resuelve decide con el kickoff del payload.
    """
    marks = ",".join("?" * len(sports))
    with connect() as conn:
        rows = conn.execute(f"""
            SELECT p.id, p.sport, p.run_date, p.home, p.away, p.pick_type, p.est_odds, p.payload
            FROM picks p
            LEFT JOIN results r ON r.pick_id = p.id
            WHERE p.sport IN ({marks})
              AND (r.id IS NULL OR r.outcome = 'pending')
            ORDER BY p.id
        """, sports).fetchall()

    cols = ("id", "sport", "run_date", "home", "away", "pick_type", "est_odds")
    return [{**dict(zip(cols, r[:7])), "payload": _load_payload(r[7])} for r in rows]


def _load_payload(raw: str | None) -> dict:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
