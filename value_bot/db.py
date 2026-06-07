"""SQLite persistence — historial de picks para backtesting y deduplicación."""
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "picks_history.db"

log = logging.getLogger(__name__)


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS picks (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date  TEXT NOT NULL,
                sport     TEXT NOT NULL,
                league    TEXT,
                home      TEXT NOT NULL,
                away      TEXT NOT NULL,
                pick_type TEXT NOT NULL,
                confidence INTEGER,
                est_odds  REAL,
                our_prob  REAL,
                data_quality TEXT,
                payload   TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_picks_date_game
            ON picks (run_date, home, away)
        """)
        conn.commit()
    log.info("DB inicializada: %s", DB_PATH)


def save_picks(run_date: str, picks: list):
    """Guarda lista de picks del run actual."""
    if not picks:
        return
    rows = []
    for p in picks:
        rows.append((
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
            json.dumps({k: v for k, v in p.items()
                        if k not in ("home", "away", "sport", "league", "type",
                                     "confidence", "est_odds", "our_prob", "data_quality")}),
        ))
    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany("""
            INSERT INTO picks
              (run_date, sport, league, home, away, pick_type, confidence,
               est_odds, our_prob, data_quality, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)
        conn.commit()
    log.info("DB: %d picks guardados para %s", len(rows), run_date)


def already_sent_today(home: str, away: str, run_date: str) -> bool:
    """True si ya se envió un pick para este partido hoy."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM picks WHERE run_date=? AND home=? AND away=? LIMIT 1",
            (run_date, home, away),
        ).fetchone()
    return row is not None


def get_recent_picks(days: int = 7) -> list[dict]:
    """Retorna los picks de los últimos N días para análisis de ROI."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT run_date, sport, league, home, away, pick_type,
                   confidence, est_odds, our_prob, data_quality, created_at
            FROM picks
            WHERE run_date >= date('now', ?)
            ORDER BY run_date DESC, confidence DESC
        """, (f"-{days} days",)).fetchall()
    cols = ["run_date", "sport", "league", "home", "away", "pick_type",
            "confidence", "est_odds", "our_prob", "data_quality", "created_at"]
    return [dict(zip(cols, r)) for r in rows]
