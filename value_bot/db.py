"""SQLite persistence — historial de picks, resultados, ROI y calibración del modelo."""
import json
import logging
import sqlite3
from pathlib import Path

from value_bot.config import ESPN_NBA_URL, http_get

DB_PATH = Path(__file__).parent.parent / "picks_history.db"

log = logging.getLogger(__name__)


# ── Schema ────────────────────────────────────────────────────────

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
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
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_picks_date_game
            ON picks (run_date, home, away)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_results_outcome
            ON results (outcome)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_picks_sport_league
            ON picks (sport, league, run_date)
        """)
        conn.commit()
    log.info("DB inicializada: %s", DB_PATH)


# ── Escritura ─────────────────────────────────────────────────────

def save_picks(run_date: str, picks: list) -> int:
    """
    Guarda picks evitando duplicados exactos (run_date + home + away + pick_type).
    Retorna número de picks efectivamente insertados.
    """
    if not picks:
        return 0

    inserted = 0
    with sqlite3.connect(DB_PATH) as conn:
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


def already_sent_today(home: str, away: str, pick_type: str, run_date: str) -> bool:
    """True si ya se envió este pick exacto (partido + tipo) hoy."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM picks WHERE run_date=? AND home=? AND away=? AND pick_type=? LIMIT 1",
            (run_date, home, away, pick_type),
        ).fetchone()
    return row is not None


# ── Resolución automática de resultados ──────────────────────────

def _evaluate_pick(pick_type: str, scores: dict, home: str, away: str) -> str:
    """Evalúa si un pick ganó, perdió o queda pending según el marcador real."""
    pt    = pick_type.lower()
    total = scores["total"]
    h_sc  = scores["home_score"]
    a_sc  = scores["away_score"]

    if "over" in pt:
        try:
            line = float(pt.split("over")[1].strip().split()[0])
            return "win" if total > line else "loss"
        except (ValueError, IndexError):
            return "pending"

    if "under" in pt:
        try:
            line = float(pt.split("under")[1].strip().split()[0])
            return "win" if total < line else "loss"
        except (ValueError, IndexError):
            return "pending"

    if "victoria" in pt or "win" in pt:
        winner = home if h_sc > a_sc else away
        return "win" if winner.lower() in pt else "loss"

    if "hándicap" in pt or "handicap" in pt:
        try:
            spread = float(pt.split("-")[1].split()[0])
            return "win" if (h_sc - a_sc) > spread else "loss"
        except (ValueError, IndexError):
            return "pending"

    return "pending"


def resolve_nba_picks_auto(run_date: str) -> int:
    """
    Resuelve picks de NBA del run_date consultando ESPN API.
    Solo actúa sobre partidos con status STATUS_FINAL.
    Retorna número de picks resueltos.
    """
    with sqlite3.connect(DB_PATH) as conn:
        pending = conn.execute("""
            SELECT p.id, p.home, p.away, p.pick_type, p.est_odds
            FROM picks p
            LEFT JOIN results r ON r.pick_id = p.id
            WHERE p.run_date = ?
              AND p.sport    = 'nba'
              AND (r.id IS NULL OR r.outcome = 'pending')
        """, (run_date,)).fetchall()

    if not pending:
        return 0

    espn_data = http_get(
        ESPN_NBA_URL,
        params={"dates": run_date.replace("-", "")},
    )

    scores = {}
    for event in espn_data.get("events", []):
        comps = event.get("competitions", [])
        if not comps:
            continue
        comp   = comps[0]
        status = comp.get("status", {}).get("type", {}).get("name", "")
        if status != "STATUS_FINAL":
            continue
        competitors = comp.get("competitors", [])
        home_c = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away_c = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not (home_c and away_c):
            continue
        h_name  = home_c["team"]["displayName"]
        a_name  = away_c["team"]["displayName"]
        h_score = int(home_c.get("score", 0))
        a_score = int(away_c.get("score", 0))
        scores[(h_name[:8].lower(), a_name[:8].lower())] = {
            "home_score": h_score,
            "away_score": a_score,
            "total":      h_score + a_score,
        }

    resolved = 0
    with sqlite3.connect(DB_PATH) as conn:
        for pick_id, home, away, pick_type, est_odds in pending:
            key = (home[:8].lower(), away[:8].lower())
            sc  = scores.get(key)
            if not sc:
                continue

            outcome = _evaluate_pick(pick_type, sc, home, away)
            if outcome == "pending":
                continue

            profit = round(est_odds - 1, 4) if outcome == "win" else -1.0
            conn.execute("""
                INSERT INTO results (pick_id, outcome, actual_score, profit_units, resolved_at, source)
                VALUES (?, ?, ?, ?, datetime('now'), 'api_auto')
                ON CONFLICT(pick_id) DO UPDATE SET
                    outcome=excluded.outcome,
                    actual_score=excluded.actual_score,
                    profit_units=excluded.profit_units,
                    resolved_at=excluded.resolved_at
            """, (pick_id, outcome, f"{sc['home_score']}-{sc['away_score']}", profit))
            resolved += 1

        conn.commit()

    log.info("DB: %d picks NBA resueltos automáticamente para %s", resolved, run_date)
    return resolved


# ── Análisis y ROI ────────────────────────────────────────────────

def get_roi_summary(days: int = 30) -> dict:
    """
    Métricas de rendimiento de los últimos N días.
    Solo picks con outcome resuelto (win/loss).
    """
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT
                p.sport,
                p.league,
                p.data_quality,
                r.outcome,
                r.profit_units,
                p.confidence,
                p.est_odds
            FROM picks p
            JOIN results r ON r.pick_id = p.id
            WHERE p.run_date >= date('now', ?)
              AND r.outcome IN ('win', 'loss')
        """, (f"-{days} days",)).fetchall()

    if not rows:
        return {"error": "sin datos resueltos"}

    total  = len(rows)
    wins   = sum(1 for r in rows if r[3] == "win")
    profit = sum(r[4] for r in rows if r[4] is not None)

    by_sport   = {}
    by_quality = {}

    for row in rows:
        sport, league, dq, outcome, profit_u, conf, odds = row

        if sport not in by_sport:
            by_sport[sport] = {"total": 0, "wins": 0, "profit": 0.0}
        by_sport[sport]["total"]  += 1
        by_sport[sport]["profit"] += profit_u or 0
        if outcome == "win":
            by_sport[sport]["wins"] += 1

        if dq not in by_quality:
            by_quality[dq] = {"total": 0, "wins": 0}
        by_quality[dq]["total"] += 1
        if outcome == "win":
            by_quality[dq]["wins"] += 1

    # Añadir win_rate calculado por deporte
    for sport_data in by_sport.values():
        t = sport_data["total"]
        sport_data["win_rate"] = round(sport_data["wins"] / t * 100, 1) if t else 0.0
        sport_data["profit"]   = round(sport_data["profit"], 2)

    return {
        "period_days":  days,
        "total_picks":  total,
        "win_rate":     round(wins / total * 100, 1),
        "total_profit": round(profit, 2),
        "roi_pct":      round(profit / total * 100, 2),
        "by_sport":     by_sport,
        "by_quality":   by_quality,
    }


def get_model_calibration(min_picks: int = 10) -> list[dict]:
    """
    Compara our_prob predicha vs win_rate real agrupada por bucket de confianza.
    Delta negativo = modelo sobreestima; positivo = modelo subestima.
    """
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT
                CAST(p.confidence / 5 AS INT) * 5  AS conf_bucket,
                COUNT(*)                            AS total,
                SUM(CASE WHEN r.outcome='win' THEN 1 ELSE 0 END) AS wins
            FROM picks p
            JOIN results r ON r.pick_id = p.id
            WHERE r.outcome IN ('win','loss')
            GROUP BY conf_bucket
            HAVING total >= ?
            ORDER BY conf_bucket
        """, (min_picks,)).fetchall()

    result = []
    for bucket, total, wins in rows:
        actual_wr = round(wins / total * 100, 1)
        result.append({
            "confidence_bucket": f"{bucket}-{bucket+4}%",
            "predicted_prob":    bucket + 2,
            "actual_win_rate":   actual_wr,
            "delta":             round(actual_wr - (bucket + 2), 1),
            "total_picks":       total,
        })
    return result


def get_recent_picks(days: int = 7) -> list[dict]:
    """Picks de los últimos N días con su resultado si está disponible."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT p.run_date, p.sport, p.league, p.home, p.away, p.pick_type,
                   p.confidence, p.est_odds, p.our_prob, p.data_quality,
                   p.created_at, r.outcome, r.profit_units
            FROM picks p
            LEFT JOIN results r ON r.pick_id = p.id
            WHERE p.run_date >= date('now', ?)
            ORDER BY p.run_date DESC, p.confidence DESC
        """, (f"-{days} days",)).fetchall()

    cols = ["run_date", "sport", "league", "home", "away", "pick_type",
            "confidence", "est_odds", "our_prob", "data_quality",
            "created_at", "outcome", "profit_units"]
    return [dict(zip(cols, r)) for r in rows]
