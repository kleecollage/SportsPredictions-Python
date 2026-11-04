"""Esquema: tablas, índices y arreglos de BDs anteriores (idempotente)."""
import logging
import sqlite3

from value_bot.db import connection

log = logging.getLogger(__name__)


def init_db():
    with connection.connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS picks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date     TEXT NOT NULL,
                sport        TEXT NOT NULL,
                league       TEXT,
                home         TEXT NOT NULL,
                away         TEXT NOT NULL,
                pick_type    TEXT NOT NULL,
                confidence   INTEGER,
                est_odds     REAL,
                our_prob     REAL,
                data_quality TEXT,
                payload      TEXT,
                created_at   TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS results (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                pick_id      INTEGER NOT NULL REFERENCES picks(id),
                outcome      TEXT CHECK(outcome IN ('win','loss','void','pending'))
                             DEFAULT 'pending',
                actual_score TEXT,
                profit_units REAL,
                resolved_at  TEXT,
                source       TEXT
            )
        """)
        _create_indexes(conn)
        _dedupe_results(conn)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_results_pick_id
            ON results (pick_id)
        """)
        _create_parlays_table(conn)
        conn.commit()
    log.info("DB inicializada: %s", connection.DB_PATH)


def _create_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_picks_date_game ON picks (run_date, home, away)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_results_outcome ON results (outcome)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_picks_sport_league ON picks (sport, league, run_date)")


def _create_parlays_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS parlays (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date       TEXT NOT NULL,
            label          TEXT,
            leg_ids        TEXT NOT NULL,   -- JSON [picks.id, ...] ordenado
            combined_odds  REAL,
            combined_prob  REAL,
            combined_value REAL,
            outcome        TEXT CHECK(outcome IN ('win','loss','void','pending'))
                           DEFAULT 'pending',
            profit_units   REAL,
            resolved_at    TEXT,
            created_at     TEXT DEFAULT (datetime('now')),
            UNIQUE (run_date, leg_ids)
        )
    """)


def _dedupe_results(conn: sqlite3.Connection) -> None:
    """
    Deja un solo resultado por pick antes de crear el índice UNIQUE: el resuelto
    (no 'pending') más reciente. BDs anteriores no tenían la restricción.
    """
    removed = conn.execute("""
        DELETE FROM results WHERE id NOT IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY pick_id ORDER BY outcome = 'pending', id DESC
                ) AS rn
                FROM results
            ) WHERE rn = 1
        )
    """).rowcount
    if removed:
        log.warning("DB: %d resultados duplicados eliminados (pick_id repetido)", removed)
