"""Picks enviados: guardado y deduplicación entre corridas."""
import json
import logging

from value_bot.analysis.market import SIDE_MARKETS
from value_bot.db.connection import connect

log = logging.getLogger(__name__)


def save_picks(run_date: str, picks: list) -> int:
    """
    Guarda picks evitando duplicados exactos (run_date + home + away + pick_type).
    Retorna número de picks efectivamente insertados.
    """
    if not picks:
        return 0

    inserted = 0
    with connect() as conn:
        for p in picks:
            exists = conn.execute("""
                SELECT 1 FROM picks
                WHERE run_date=? AND home=? AND away=? AND pick_type=?
                LIMIT 1
            """, (run_date, p.get("home", ""), p.get("away", ""), p.get("type", ""))
            ).fetchone()

            if exists:
                continue

            conn.execute("""
                INSERT INTO picks
                  (run_date, sport, league, home, away, pick_type,
                   confidence, est_odds, our_prob, data_quality, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_date,
                p.get("sport", ""),
                p.get("league", ""),
                p.get("home", ""),
                p.get("away", ""),
                p.get("type", ""),
                p.get("confidence"),
                p.get("est_odds"),
                p.get("our_prob"),
                p.get("data_quality", ""),
                json.dumps({
                    k: v for k, v in p.items()
                    if k not in ("home", "away", "sport", "league", "type",
                                 "confidence", "est_odds", "our_prob", "data_quality")
                }),
            ))
            inserted += 1

        conn.commit()

    log.info("DB: %d picks insertados (%d duplicados saltados) para %s",
             inserted, len(picks) - inserted, run_date)
    return inserted


def already_sent_for_game(sport: str, home: str, away: str, game_date: str,
                          pick_type: str | None = None) -> bool:
    """
    True si este pick (partido + tipo; sin tipo, cualquier pick del partido) ya se envió
    para el juego de game_date en cualquier corrida: el runner es intermitente y una
    corrida puede adelantar los juegos de otro día, así que no se compara contra run_date.
    """
    with connect() as conn:
        row = conn.execute("""
            SELECT 1 FROM picks
            WHERE sport = ? AND home = ? AND away = ? AND (? IS NULL OR pick_type = ?)
              AND json_extract(payload, '$.date') = ?
            LIMIT 1
        """, (sport, home, away, pick_type, pick_type, game_date)).fetchone()
    return row is not None


def already_sent_for_event(event_id: str, market: str, sport: str = "nfl") -> bool:
    """
    True si ya se envió un pick de ese deporte y mercado para ese partido, en cualquier
    corrida anterior. El runner no corre todos los días, así que la deduplicación
    no puede depender de run_date; y usa el mercado (no pick_type) porque la
    línea se mueve entre días ("Under 53.5" → "Under 52.5").
    """
    with connect() as conn:
        row = conn.execute("""
            SELECT 1 FROM picks
            WHERE sport = ?
              AND json_extract(payload, '$.event_id') = ?
              AND json_extract(payload, '$.market')   = ?
            LIMIT 1
        """, (sport, event_id, market)).fetchone()
    return row is not None


def already_sent_bet(event_id: str | None, market: str, sport: str) -> bool:
    """
    already_sent_for_event con una sola apuesta de lado por partido (NFL y NBA): un
    moneyline o spread previo, de cualquier equipo, bloquea los dos mercados de lado.
    Sin event_id no hay con qué deduplicar.
    """
    if not event_id:
        return False
    markets = SIDE_MARKETS if market in SIDE_MARKETS else (market,)
    return any(already_sent_for_event(event_id, m, sport=sport) for m in markets)
