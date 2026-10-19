"""Métricas y reporte en texto del backtest NFL (puro: recibe las filas de nfl_backtest)."""
import math
import random

OUTCOME_KEYS = {"ml": "home_won", "spread": "home_covered", "total": "over"}
BOOTSTRAP_SAMPLES = 10_000
WEIGHT_GRID = [w / 20 for w in range(21)]  # 0.00, 0.05 … 1.00
VALUE_BUCKETS = ((0.0, 0.04), (0.04, 0.08), (0.08, 0.12), (0.12, 0.151))
SPREAD_GAP_BUCKETS = ((0, 1.5), (1.5, 3), (3, 5), (5, 99))
TOTAL_GAP_BUCKETS = ((0, 2), (2, 4), (4, 7), (7, 99))


# ── Picks ─────────────────────────────────────────────────────────

def units(pick: dict) -> float:
    return {"win": pick["est_odds"] - 1, "loss": -1.0}.get(pick["result"], 0.0)


def pick_summary(picks: list[dict]) -> dict:
    n = len(picks)
    total_units = sum(units(p) for p in picks)
    return {
        "n": n,
        "wins": sum(p["result"] == "win" for p in picks),
        "losses": sum(p["result"] == "loss" for p in picks),
        "units": total_units,
        "roi": total_units / n if n else 0.0,
        "promised_ev": sum(p["value"] for p in picks) / n if n else 0.0,
        "expected_wins": sum(p["confidence"] / 100 for p in picks),
    }


def bootstrap_roi(picks: list[dict], samples: int = BOOTSTRAP_SAMPLES, seed: int = 1) -> tuple[float, float]:
    """Intervalo de confianza del 90 % del ROI remuestreando picks."""
    rng = random.Random(seed)
    pnl = [units(p) for p in picks]
    sims = sorted(sum(rng.choice(pnl) for _ in pnl) / len(pnl) for _ in range(samples))
    return sims[int(0.05 * samples)], sims[int(0.95 * samples)]


# ── Modelo vs mercado ─────────────────────────────────────────────

def probability_metrics(data: list[dict], outcome: str) -> dict:
    """Brier y logloss de modelo/mercado/mezcla, aciertos del lado del modelo y peso óptimo."""
    data = [d for d in data if d[outcome] is not None]
    ys = [float(d[outcome]) for d in data]

    def brier(probs):
        return sum((p - y) ** 2 for p, y in zip(probs, ys)) / len(ys)

    def logloss(probs):
        return -sum(math.log(p if y else 1 - p) for p, y in zip(probs, ys)) / len(ys)

    sources = {src: [d[src] for d in data] for src in ("model", "market", "blend")}
    best_weight = min(WEIGHT_GRID, key=lambda w: brier(
        [w * d["model"] + (1 - w) * d["market"] for d in data]))
    return {
        "n": len(data),
        "brier": {src: brier(p) for src, p in sources.items()},
        "logloss": {src: logloss(p) for src, p in sources.items()},
        "model_side_hits": sum((d["model"] > 0.5) == bool(y) for d, y in zip(data, ys)),
        "optimal_model_weight": best_weight,
    }


def _mae(values: list[float]) -> float:
    return sum(abs(v) for v in values) / len(values)


def _side_hits_by_gap(rows, market, outcome, model_key, market_key, buckets):
    lines = []
    for lo, hi in buckets:
        sel = [r for r in rows if lo <= abs(r[model_key] - r[market_key]) < hi and r[market][outcome] is not None]
        if sel:
            hits = sum((r[market]["model"] > 0.5) == r[market][outcome] for r in sel)
            lines.append(f"  |Δ| {lo}–{hi} pts: {hits}/{len(sel)} ({hits / len(sel):.0%})")
    return lines


# ── Render ────────────────────────────────────────────────────────

def _summary_line(label: str, picks: list[dict]) -> str:
    s = pick_summary(picks)
    if not s["n"]:
        return f"  {label:<28} n=  0"
    return (f"  {label:<28} n={s['n']:>3}  {s['wins']:>2}-{s['losses']:<2}  u={s['units']:+6.2f}  "
            f"ROI={s['roi']:+6.1%}  EV prometido={s['promised_ev']:+5.1%}  "
            f"victorias esperadas={s['expected_wins']:5.1f}")


def _picks_section(rows: list[dict]) -> list[str]:
    picks = [{**p, "week": r["week"], "dq": r["dq"]} for r in rows for p in r["picks"]]
    out = [f"=== PICKS ({len(rows)} juegos, {sum(r['pickable'] for r in rows)} elegibles) ===",
           _summary_line("TOTAL", picks)]
    if not picks:
        return out
    lo, hi = bootstrap_roi(picks)
    out.append(f"  IC 90 % ROI (bootstrap): {lo:+.1%} … {hi:+.1%}")
    out.append("Por mercado:")
    out += [_summary_line(mk, [p for p in picks if p["market"] == mk]) for mk in ("spread", "moneyline", "total")]
    out.append("Lados por cuota:")
    sides = [p for p in picks if p["market"] != "total"]
    out.append(_summary_line("cuota < 2.0", [p for p in sides if p["est_odds"] < 2.0]))
    out.append(_summary_line("cuota ≥ 2.0", [p for p in sides if p["est_odds"] >= 2.0]))
    out.append("Por value prometido:")
    out += [_summary_line(f"value {lo:.2f}–{hi:.2f}", [p for p in picks if lo <= p["value"] < hi])
            for lo, hi in VALUE_BUCKETS]
    out.append("Por data_quality:")
    out += [_summary_line(dq, [p for p in picks if p["dq"] == dq]) for dq in ("high", "medium")]
    out.append("Por semana:")
    out += [_summary_line(f"semana {wk}", [p for p in picks if p["week"] == wk])
            for wk in sorted({p["week"] for p in picks if p["week"] is not None})]
    return out


def _market_section(rows: list[dict]) -> list[str]:
    out = ["", "=== MODELO vs MERCADO (todas las predicciones, sin filtro) ==="]
    for market, outcome in OUTCOME_KEYS.items():
        m = probability_metrics([r[market] for r in rows], outcome)
        b, ll = m["brier"], m["logloss"]
        out.append(f"{market:<7} n={m['n']}  Brier modelo {b['model']:.4f} | mercado {b['market']:.4f} | "
                   f"mezcla {b['blend']:.4f}   logloss {ll['model']:.3f}/{ll['market']:.3f}/{ll['blend']:.3f}   "
                   f"lado del modelo {m['model_side_hits']}/{m['n']} ({m['model_side_hits'] / m['n']:.1%})   "
                   f"peso óptimo {m['optimal_model_weight']:.2f}")
    out += [
        "",
        "MAE margen: modelo %.2f  mercado %.2f" % (
            _mae([-r["model_spread"] - r["actual_margin"] for r in rows]),
            _mae([-r["market_spread"] - r["actual_margin"] for r in rows])),
        "MAE total : modelo %.2f  mercado %.2f" % (
            _mae([r["model_total"] - r["actual_total"] for r in rows]),
            _mae([r["market_total"] - r["actual_total"] for r in rows])),
        "ATS del lado del modelo según desacuerdo con el spread de mercado:",
        *_side_hits_by_gap(rows, "spread", "home_covered", "model_spread", "market_spread", SPREAD_GAP_BUCKETS),
        "Over/Under del lado del modelo según desacuerdo con el total de mercado:",
        *_side_hits_by_gap(rows, "total", "over", "model_total", "market_total", TOTAL_GAP_BUCKETS),
    ]
    return out


def render_report(rows: list[dict]) -> str:
    if not rows:
        return "Backtest NFL: sin juegos con líneas de cierre en el rango."
    return "\n".join(_picks_section(rows) + _market_section(rows))
