"""SQLite persistence — historial de picks, resultados, ROI y calibración del modelo."""
import json
import logging
import sqlite3
from pathlib import Path

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
        _create_indexes(conn)
        _dedupe_results(conn)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_results_pick_id
            ON results (pick_id)
        """)
        _create_parlays_table(conn)
        conn.commit()
    log.info("DB inicializada: %s", DB_PATH)


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


def already_sent_for_game(sport: str, home: str, away: str, game_date: str, pick_type: str) -> bool:
    """
    True si este pick (partido + tipo) ya se envió para el juego de game_date en
    cualquier corrida: el runner es intermitente y una corrida puede adelantar
    los juegos de otro día, así que no se compara contra run_date.
    """
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("""
            SELECT 1 FROM picks
            WHERE sport = ? AND home = ? AND away = ? AND pick_type = ?
              AND json_extract(payload, '$.date') = ?
            LIMIT 1
        """, (sport, home, away, pick_type, game_date)).fetchone()
    return row is not None




def already_sent_for_event(event_id: str, market: str, sport: str = "nfl") -> bool:
    """
    True si ya se envió un pick de ese deporte y mercado para ese partido, en cualquier
    corrida anterior. El runner no corre todos los días, así que la deduplicación
    no puede depender de run_date; y usa el mercado (no pick_type) porque la
    línea se mueve entre días ("Under 53.5" → "Under 52.5").
    """
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("""
            SELECT 1 FROM picks
            WHERE sport = ?
              AND json_extract(payload, '$.event_id') = ?
              AND json_extract(payload, '$.market')   = ?
            LIMIT 1
        """, (sport, event_id, market)).fetchone()
    return row is not None


# ── Parlays ───────────────────────────────────────────────────────

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
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            UPDATE parlays SET outcome = ?, profit_units = ?, resolved_at = datetime('now')
            WHERE id = ?
        """, (outcome, profit_units, parlay_id))
        conn.commit()


def get_parlay_summary(days: int = 7, sports=None, model_since: dict | None = None) -> dict:
    """
    {period_days, total, wins, profit} de parlays ganados/perdidos resueltos en los últimos
    N días; {} sin datos. Un parlay cuenta si todas sus piernas están en el alcance
    (sports / model_since, como get_roi_summary).
    """
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT pl.id, pl.outcome, pl.profit_units, p.sport, p.run_date
            FROM parlays pl
            JOIN json_each(pl.leg_ids) j
            JOIN picks p ON p.id = j.value
            WHERE pl.outcome IN ('win', 'loss') AND date(pl.resolved_at) >= date('now', ?)
        """, (f"-{days} days",)).fetchall()
    parlays: dict[int, tuple] = {}
    out_of_scope: set[int] = set()
    for parlay_id, outcome, profit, sport, run_date in rows:
        parlays[parlay_id] = (outcome, profit)
        if not _in_scope(sport, run_date, sports, model_since):
            out_of_scope.add(parlay_id)
    kept = [v for k, v in parlays.items() if k not in out_of_scope]
    if not kept:
        return {}
    return {"period_days": days, "total": len(kept), "wins": sum(o == "win" for o, _ in kept),
            "profit": round(sum(p or 0 for _, p in kept), 2)}


# ── Resultados ────────────────────────────────────────────────────

def save_result(pick_id: int, outcome: str, actual_score: str | None,
                profit_units: float | None, source: str = "espn_auto") -> None:
    """Inserta o actualiza el resultado de un pick (uno por pick_id)."""
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(f"""
            SELECT p.id, p.sport, p.home, p.away, p.pick_type, p.est_odds, p.payload
            FROM picks p
            LEFT JOIN results r ON r.pick_id = p.id
            WHERE p.sport IN ({marks})
              AND (r.id IS NULL OR r.outcome = 'pending')
            ORDER BY p.id
        """, sports).fetchall()

    cols = ("id", "sport", "home", "away", "pick_type", "est_odds")
    return [{**dict(zip(cols, r[:6])), "payload": _load_payload(r[6])} for r in rows]


def _load_payload(raw: str | None) -> dict:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


# ── Análisis y ROI ────────────────────────────────────────────────

# Fecha que define la ventana de días: envío del pick o resolución (si no hay, el envío)
_WINDOW_DATE = {
    "run_date": "p.run_date",
    "resolved": "COALESCE(date(r.resolved_at), p.run_date)",
}


def load_picks_df(days: int = 90, window: str = "run_date"):
    """
    Carga picks + results en un DataFrame (pandas) listo para análisis.
    window: "run_date" (picks enviados en los últimos N días) o "resolved" (resueltos en ellos).
    Requiere pandas instalado. Si no está disponible retorna None.
    Retorna DataFrame vacío si las tablas aún no existen (primera ejecución).
    """
    if not _PANDAS_OK:
        log.warning("pandas no disponible — load_picks_df retorna None")
        return None
    try:
        with sqlite3.connect(DB_PATH) as conn:
            df = pd.read_sql(f"""
                SELECT p.*, r.outcome, r.profit_units
                FROM picks p
                LEFT JOIN results r ON r.pick_id = p.id
                WHERE {_WINDOW_DATE[window]} >= date('now', ?)
            """, conn, params=(f"-{days} days",))
        return df
    except Exception as exc:
        log.debug("load_picks_df: %s (tablas aún no creadas?)", exc)
        return pd.DataFrame()


def _in_scope(sport: str, run_date: str, sports, model_since: dict | None) -> bool:
    """Pick de un deporte pedido (None = todos) y del modelo vigente de ese deporte."""
    if sports is not None and sport not in sports:
        return False
    since = (model_since or {}).get(sport)
    return since is None or run_date >= since


def _scope_mask(df, sports, model_since: dict | None):
    """Máscara booleana de _in_scope sobre un DataFrame con columnas sport y run_date."""
    return pd.Series([_in_scope(sp, rd, sports, model_since)
                      for sp, rd in zip(df["sport"], df["run_date"])], index=df.index, dtype=bool)


def get_roi_summary(days: int = 30, sports=None, model_since: dict | None = None) -> dict:
    """
    Métricas de los picks resueltos en los últimos N días (por fecha de resolución: un
    pick de hace 2 semanas cuyo juego se resolvió hoy cuenta; el runner es intermitente).
    Solo outcome win/loss, de `sports` (None = todos) y con run_date ≥ model_since[sport]
    cuando el deporte tiene fecha de modelo.
    Usa pandas si disponible, SQL puro como fallback.
    """
    if _PANDAS_OK:
        df = load_picks_df(days, window="resolved")
        if df is None or df.empty or "outcome" not in df.columns:
            return {"error": "sin datos resueltos"}
        df = df[df["outcome"].isin(["win", "loss"]) & _scope_mask(df, sports, model_since)]
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
            rows = conn.execute(f"""
                SELECT p.sport, p.data_quality, r.outcome, r.profit_units, p.run_date
                FROM picks p
                JOIN results r ON r.pick_id = p.id
                WHERE {_WINDOW_DATE["resolved"]} >= date('now', ?)
                  AND r.outcome IN ('win', 'loss')
            """, (f"-{days} days",)).fetchall()
        rows = [r[:4] for r in rows if _in_scope(r[0], r[4], sports, model_since)]

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


def _calibration_row(bucket: int, total: int, wins: int) -> dict:
    actual_wr = round(wins / total * 100, 1)
    return {
        "confidence_bucket": f"{bucket}-{bucket+4}%",
        "predicted_prob":    bucket + 2,
        "actual_win_rate":   actual_wr,
        "delta":             round(actual_wr - (bucket + 2), 1),
        "total_picks":       total,
    }


def get_model_calibration(min_picks: int = 10, sports=None,
                          model_since: dict | None = None) -> list[dict]:
    """
    Compara our_prob predicha vs win_rate real agrupada por bucket de confianza.
    Delta negativo = modelo sobreestima; positivo = modelo subestima.
    sports / model_since: mismo filtro que get_roi_summary.
    """
    if _PANDAS_OK:
        df = load_picks_df(90)
        if df is None or df.empty or "outcome" not in df.columns:
            return []
        df = df[df["outcome"].isin(["win", "loss"]) & _scope_mask(df, sports, model_since)].copy()
        if df.empty:
            return []
        df["bucket"] = (df["confidence"] // 5) * 5
        grouped = (
            df.groupby("bucket")
              .agg(total=("outcome", "count"),
                   wins=("outcome", lambda x: (x == "win").sum()))
        )
        return [_calibration_row(int(bucket), int(row["total"]), int(row["wins"]))
                for bucket, row in grouped.iterrows() if row["total"] >= min_picks]

    # Fallback SQL puro
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT p.sport, p.run_date, p.confidence, r.outcome
            FROM picks p
            JOIN results r ON r.pick_id = p.id
            WHERE r.outcome IN ('win','loss')
        """).fetchall()

    buckets: dict[int, tuple[int, int]] = {}
    for sport, run_date, confidence, outcome in rows:
        if not _in_scope(sport, run_date, sports, model_since):
            continue
        bucket = int(confidence or 0) // 5 * 5
        total, wins = buckets.get(bucket, (0, 0))
        buckets = {**buckets, bucket: (total + 1, wins + (outcome == "win"))}
    return [_calibration_row(bucket, total, wins)
            for bucket, (total, wins) in sorted(buckets.items()) if total >= min_picks]


CALIBRATION_MIN_RESOLVED = 50   # con menos picks resueltos no se ajusta nada
CALIBRATION_LOOKBACK_DAYS = 90


def get_calibration_factors(sport: str, model_since: str | None = None,
                            min_picks_per_bucket: int = 15) -> dict:
    """
    {conf_bucket: factor} con factor = win rate real / predicho, solo con picks
    resueltos de `sport` y del modelo vigente (run_date ≥ model_since): mezclar
    deportes o modelos viejos calibraría un modelo con el historial de otro.
    Solo buckets con >= min_picks_per_bucket picks; {} con < CALIBRATION_MIN_RESOLVED.
    Ejemplo: {65: 0.88, 70: 0.92, 75: 1.03}
    """
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("""
            SELECT CAST(p.confidence / 5 AS INT) * 5 AS bucket,
                   COUNT(*) AS total,
                   AVG(CASE WHEN r.outcome = 'win' THEN 100.0 ELSE 0.0 END) AS actual_wr
            FROM picks p
            JOIN results r ON r.pick_id = p.id
            WHERE r.outcome IN ('win', 'loss')
              AND p.sport = ?
              AND p.run_date >= ?
              AND p.run_date >= date('now', ?)
            GROUP BY bucket
        """, (sport, model_since or "", f"-{CALIBRATION_LOOKBACK_DAYS} days")).fetchall()

    if sum(total for _, total, _ in rows) < CALIBRATION_MIN_RESOLVED:
        log.debug("get_calibration_factors(%s): pocos picks resueltos — sin ajuste", sport)
        return {}
    return {
        int(bucket): round(float(actual_wr) / (int(bucket) + 2), 3)
        for bucket, total, actual_wr in rows if total >= min_picks_per_bucket
    }


def get_recent_picks(days: int = 7, sports=None, model_since: dict | None = None) -> list[dict]:
    """Picks de los últimos N días con su resultado; sports / model_since como en get_roi_summary."""
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
    picks = [dict(zip(cols, r)) for r in rows]
    return [p for p in picks if _in_scope(p["sport"], p["run_date"], sports, model_since)]
