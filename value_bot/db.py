"""SQLite persistence — historial de picks, resultados, ROI y calibración del modelo."""
import json
import logging
import sqlite3
from pathlib import Path

from value_bot.config import ESPN_NBA_URL, http_get

try:
    import pandas as pd
    _PANDAS_OK = True
except ImportError:
    _PANDAS_OK = False

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

def already_sent_for_event(event_id: str, market: str) -> bool:
    """
    True si ya se envió un pick NFL de ese mercado para ese partido, en cualquier
    corrida anterior. El runner no corre todos los días, así que la deduplicación
    no puede depender de run_date; y usa el mercado (no pick_type) porque la
    línea se mueve entre días ("Under 53.5" → "Under 52.5").
    """
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("""
            SELECT 1 FROM picks
            WHERE sport = 'nfl'
              AND json_extract(payload, '$.event_id') = ?
              AND json_extract(payload, '$.market')   = ?
            LIMIT 1
        """, (event_id, market)).fetchone()
    return row is not None


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

def load_picks_df(days: int = 90):
    """
    Carga picks + results en un DataFrame (pandas) listo para análisis.
    Requiere pandas instalado. Si no está disponible retorna None.
    Retorna DataFrame vacío si las tablas aún no existen (primera ejecución).
    """
    if not _PANDAS_OK:
        log.warning("pandas no disponible — load_picks_df retorna None")
        return None
    try:
        with sqlite3.connect(DB_PATH) as conn:
            df = pd.read_sql("""
                SELECT p.*, r.outcome, r.profit_units
                FROM picks p
                LEFT JOIN results r ON r.pick_id = p.id
                WHERE p.run_date >= date('now', ?)
            """, conn, params=(f"-{days} days",))
        return df
    except Exception as exc:
        log.debug("load_picks_df: %s (tablas aún no creadas?)", exc)
        return pd.DataFrame()


def get_roi_summary(days: int = 30) -> dict:
    """
    Métricas de rendimiento de los últimos N días.
    Solo picks con outcome resuelto (win/loss).
    Usa pandas si disponible, SQL puro como fallback.
    """
    if _PANDAS_OK:
        df = load_picks_df(days)
        if df is None or df.empty or "outcome" not in df.columns:
            return {"error": "sin datos resueltos"}
        df = df[df["outcome"].isin(["win", "loss"])]
        if df.empty:
            return {"error": "sin datos resueltos"}

        total  = len(df)
        wins   = (df["outcome"] == "win").sum()
        profit = df["profit_units"].fillna(0).sum()

        by_sport_df = (
            df.groupby("sport")
              .agg(
                  total=("outcome", "count"),
                  wins=("outcome", lambda x: (x == "win").sum()),
                  profit=("profit_units", "sum"),
              )
              .assign(
                  win_rate=lambda d: (d["wins"] / d["total"] * 100).round(1),
                  profit=lambda d: d["profit"].round(2),
              )
        )
        by_sport = by_sport_df.to_dict("index")

        by_quality_df = (
            df.groupby("data_quality")
              .agg(
                  total=("outcome", "count"),
                  wins=("outcome", lambda x: (x == "win").sum()),
              )
              .assign(win_rate=lambda d: (d["wins"] / d["total"] * 100).round(1))
        )
        by_quality = by_quality_df.to_dict("index")

    else:
        # Fallback SQL puro
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute("""
                SELECT p.sport, p.data_quality, r.outcome, r.profit_units
                FROM picks p
                JOIN results r ON r.pick_id = p.id
                WHERE p.run_date >= date('now', ?)
                  AND r.outcome IN ('win', 'loss')
            """, (f"-{days} days",)).fetchall()

        if not rows:
            return {"error": "sin datos resueltos"}

        total  = len(rows)
        wins   = sum(1 for r in rows if r[2] == "win")
        profit = sum(r[3] or 0 for r in rows)
        by_sport, by_quality = {}, {}
        for sport, dq, outcome, profit_u in rows:
            by_sport.setdefault(sport, {"total": 0, "wins": 0, "profit": 0.0})
            by_sport[sport]["total"]  += 1
            by_sport[sport]["profit"] += profit_u or 0
            if outcome == "win":
                by_sport[sport]["wins"] += 1
            by_quality.setdefault(dq, {"total": 0, "wins": 0})
            by_quality[dq]["total"] += 1
            if outcome == "win":
                by_quality[dq]["wins"] += 1
        for sd in by_sport.values():
            t = sd["total"]
            sd["win_rate"] = round(sd["wins"] / t * 100, 1) if t else 0.0
            sd["profit"]   = round(sd["profit"], 2)

    return {
        "period_days":  days,
        "total_picks":  int(total),
        "win_rate":     round(float(wins) / float(total) * 100, 1),
        "total_profit": round(float(profit), 2),
        "roi_pct":      round(float(profit) / float(total) * 100, 2),
        "by_sport":     by_sport,
        "by_quality":   by_quality,
    }


def get_model_calibration(min_picks: int = 10) -> list[dict]:
    """
    Compara our_prob predicha vs win_rate real agrupada por bucket de confianza.
    Delta negativo = modelo sobreestima; positivo = modelo subestima.
    """
    if _PANDAS_OK:
        df = load_picks_df(90)
        if df is None or df.empty or "outcome" not in df.columns:
            return []
        df = df[df["outcome"].isin(["win", "loss"])].copy()
        if df.empty:
            return []
        df["bucket"] = (df["confidence"] // 5) * 5
        grouped = (
            df.groupby("bucket")
              .agg(total=("outcome", "count"),
                   wins=("outcome", lambda x: (x == "win").sum()))
        )
        result = []
        for bucket, row in grouped.iterrows():
            if row["total"] < min_picks:
                continue
            actual_wr = round(row["wins"] / row["total"] * 100, 1)
            result.append({
                "confidence_bucket": f"{bucket}-{bucket+4}%",
                "predicted_prob":    int(bucket) + 2,
                "actual_win_rate":   actual_wr,
                "delta":             round(actual_wr - (int(bucket) + 2), 1),
                "total_picks":       int(row["total"]),
            })
        return result

    # Fallback SQL puro
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


def get_calibration_factors(min_picks_per_bucket: int = 15) -> dict:
    """
    Retorna {conf_bucket: factor} donde factor = actual_win_rate / predicted.
    Solo buckets con >= min_picks_per_bucket picks resueltos.
    Ejemplo: {65: 0.88, 70: 0.92, 75: 1.03}
    Con pocos datos (<50 picks resueltos) retorna {} para no ajustar.
    """
    if _PANDAS_OK:
        df = load_picks_df(90)
        if df is None or df.empty or "outcome" not in df.columns:
            return {}
        df = df[df["outcome"].isin(["win", "loss"])].copy()
        if len(df) < 50:
            log.debug("get_calibration_factors: solo %d picks resueltos — sin ajuste", len(df))
            return {}
        df["bucket"] = (df["confidence"] // 5) * 5
        grouped = df.groupby("bucket").agg(
            total=("outcome", "count"),
            actual_wr=("outcome", lambda x: (x == "win").mean() * 100),
        )
        factors = {}
        for bucket, row in grouped.iterrows():
            if row["total"] >= min_picks_per_bucket:
                predicted = int(bucket) + 2
                factors[int(bucket)] = round(float(row["actual_wr"]) / predicted, 3)
        return factors

    # Fallback SQL
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT
                CAST(p.confidence / 5 AS INT) * 5 AS bucket,
                COUNT(*) AS total,
                AVG(CASE WHEN r.outcome='win' THEN 100.0 ELSE 0.0 END) AS actual_wr
            FROM picks p
            JOIN results r ON r.pick_id = p.id
            WHERE r.outcome IN ('win','loss')
            GROUP BY bucket
        """).fetchall()
        total_count = conn.execute(
            "SELECT COUNT(*) FROM picks p JOIN results r ON r.pick_id=p.id WHERE r.outcome IN ('win','loss')"
        ).fetchone()[0]

    if total_count < 50:
        return {}
    factors = {}
    for bucket, total, actual_wr in rows:
        if total >= min_picks_per_bucket:
            predicted = int(bucket) + 2
            factors[int(bucket)] = round(float(actual_wr) / predicted, 3)
    return factors


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
