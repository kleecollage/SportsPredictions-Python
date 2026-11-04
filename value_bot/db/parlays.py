"""Parlays enviados ligados a sus piernas y su liquidación."""
import json
import logging
import sqlite3

from value_bot.db.connection import connect

log = logging.getLogger(__name__)


def _pick_id(conn: sqlite3.Connection, run_date: str, leg: dict) -> int | None:
    """id del pick guardado por save_picks (misma clave: run_date + partido + tipo)."""
    row = conn.execute("""
        SELECT id FROM picks WHERE run_date=? AND home=? AND away=? AND pick_type=?
        ORDER BY id LIMIT 1
    """, (run_date, leg.get("home", ""), leg.get("away", ""), leg.get("type", ""))).fetchone()
    return row[0] if row else None


def save_parlays(run_date: str, parlays: list[dict]) -> int:
    """
    Guarda los parlays enviados ligados a los picks de sus piernas (llamar después de
    save_picks). Un parlay con alguna pierna sin guardar se salta. Retorna los insertados.
    """
    inserted = 0
    with connect() as conn:
        for parlay in parlays:
            ids = [_pick_id(conn, run_date, leg) for leg in parlay["legs"]]
            if None in ids:
                log.warning("DB: parlay %s con piernas sin guardar → no se registra", parlay.get("label"))
                continue
            inserted += conn.execute("""
                INSERT OR IGNORE INTO parlays
                  (run_date, label, leg_ids, combined_odds, combined_prob, combined_value)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (run_date, parlay.get("label"), json.dumps(sorted(ids)), parlay.get("combined_odds"),
                  parlay.get("combined_prob"), parlay.get("combined_value"))).rowcount
        conn.commit()
    return inserted


def get_pending_parlays() -> list[dict]:
    """
    [{id, run_date, legs: [{pick_id, outcome, est_odds}]}] de parlays sin resolver.
    outcome es None sin resultado, y 'void' si el pick de la pierna ya no existe.
    """
    with connect() as conn:
        rows = conn.execute("""
            SELECT pl.id, pl.run_date, j.value, p.id, p.est_odds, r.outcome
            FROM parlays pl
            JOIN json_each(pl.leg_ids) j
            LEFT JOIN picks p   ON p.id = j.value
            LEFT JOIN results r ON r.pick_id = j.value
            WHERE pl.outcome = 'pending'
            ORDER BY pl.id, j.key
        """).fetchall()
    parlays: dict[int, dict] = {}
    for parlay_id, run_date, leg_id, pick_id, est_odds, outcome in rows:
        if pick_id is None:
            log.warning("DB: parlay %d sin el pick %s → esa pierna cuenta como void", parlay_id, leg_id)
            outcome = "void"
        parlay = parlays.setdefault(parlay_id, {"id": parlay_id, "run_date": run_date, "legs": []})
        parlay["legs"].append({"pick_id": leg_id, "outcome": outcome, "est_odds": est_odds})
    return list(parlays.values())


def save_parlay_result(parlay_id: int, outcome: str, profit_units: float) -> None:
    with connect() as conn:
        conn.execute("""
            UPDATE parlays SET outcome = ?, profit_units = ?, resolved_at = datetime('now')
            WHERE id = ?
        """, (outcome, profit_units, parlay_id))
        conn.commit()
