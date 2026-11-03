"""
Backtest del modelo de fútbol contra cuotas de cierre de football-data.co.uk.

    python -m value_bot.tools.football_backtest --season 2526 --leagues E0,SP1,D1,I1,F1 [--json f]

Por liga baja el CSV de la temporada (resultados + cierre de Pinnacle `PSC*`/`PC>2.5`, o la media
del mercado `AvgC*` si falta). Cada partido se re-corre con ratings hechos solo con los partidos
anteriores de esa liga (`football_ratings.team_ratings`) y las mismas reglas de producción
(`football_model.analyze_football_match`); los picks se liquidan con el resolver.

Reporta picks/ROI (IC 90 %), y para el Over 2.5: Brier del modelo (XG multiplicativo con splits y
aditivo) contra el mercado sin vig y la mezcla, y el peso del modelo que minimiza el Brier
(fija `FOOTBALL_TOTALS_WEIGHT`). Limitaciones: solo línea 2.5 (la del CSV), sin BTTS ni Champions
(el CSV no los trae) y el bot apuesta a las 9:00 ET, no al cierre (el backtest es optimista).
"""
import argparse
import csv
import io
import json
import logging
import sys
from datetime import datetime

import requests

from value_bot.analysis.football_model import (
    FOOTBALL_MIN_SIDE_GAMES, FOOTBALL_TOTALS_WEIGHT, analyze_football_match, expected_goals, total_outcomes,
)
from value_bot.analysis.football_ratings import team_ratings
from value_bot.analysis.market import no_vig_pair
from value_bot.resolver import FinalScore, evaluate_pick
from value_bot.tools.backtest_report import bootstrap_roi, pick_summary, probability_metrics

log = logging.getLogger(__name__)

CSV_URL = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"
CSV_TIMEOUT = 30
LEAGUE_NAMES = {"E0": "Premier League", "SP1": "LaLiga", "D1": "Bundesliga", "I1": "Serie A", "F1": "Ligue 1"}
TOTAL_LINE = 2.5
# (over, under) y (1, X, 2) de cierre: Pinnacle primero, media del mercado si falta
TOTAL_COLUMNS = (("PC>2.5", "PC<2.5"), ("AvgC>2.5", "AvgC<2.5"))
RESULT_COLUMNS = (("PSCH", "PSCD", "PSCA"), ("AvgCH", "AvgCD", "AvgCA"))


# ── CSV (puro) ────────────────────────────────────────────────────

def _price(row: dict, key: str) -> float | None:
    try:
        value = float(row.get(key) or "")
    except ValueError:
        return None
    return value if value > 1 else None


def _prices(row: dict, options: tuple[tuple[str, ...], ...]) -> tuple[float, ...] | None:
    """Primer juego de columnas con todos los precios válidos."""
    for keys in options:
        prices = tuple(_price(row, k) for k in keys)
        if None not in prices:
            return prices
    return None


def _iso_date(raw: str) -> str | None:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_csv(text: str, league: str) -> list[dict]:
    """Partidos jugados con fecha, marcador y cierre de totales 2.5, en orden cronológico."""
    games = []
    for row in csv.DictReader(io.StringIO(text)):
        day = _iso_date(row.get("Date") or "")
        totals = _prices(row, TOTAL_COLUMNS)
        try:
            hg, ag = int(row.get("FTHG") or ""), int(row.get("FTAG") or "")
        except ValueError:
            continue
        if not (day and totals and row.get("HomeTeam") and row.get("AwayTeam")):
            continue
        result = _prices(row, RESULT_COLUMNS)
        games.append({"date": day, "league": league, "home": row["HomeTeam"], "away": row["AwayTeam"],
                      "home_goals": hg, "away_goals": ag, "over_odds": totals[0], "under_odds": totals[1],
                      **(dict(zip(("home_odds", "draw_odds", "away_odds"), result)) if result else {})})
    return sorted(games, key=lambda g: g["date"])


def as_fd_match(game: dict) -> dict:
    """Partido del CSV con la forma de /matches de football-data (para team_ratings)."""
    return {"utcDate": game["date"], "status": "FINISHED",
            "homeTeam": {"name": game["home"]}, "awayTeam": {"name": game["away"]},
            "score": {"duration": "REGULAR", "fullTime": {"home": game["home_goals"], "away": game["away_goals"]}}}


# ── Replay (puro) ─────────────────────────────────────────────────

def _additive_xg(hs: dict, as_: dict) -> tuple[float, float]:
    plain = ("goals_for_pg", "goals_against_pg")
    return expected_goals({k: hs[k] for k in plain}, {k: as_[k] for k in plain})


def replay_game(game: dict, earlier: list[dict]) -> dict | None:
    """Re-corre el modelo con los partidos de la liga anteriores a la fecha del juego."""
    ratings = team_ratings([as_fd_match(g) for g in earlier if g["date"] < game["date"]])
    hs, as_ = ratings.get(game["home"]), ratings.get(game["away"])
    if not (hs and as_) or min(hs["played"], as_["played"]) < FOOTBALL_MIN_SIDE_GAMES:
        return None
    real = {k: game[k] for k in ("home_odds", "draw_odds", "away_odds", "over_odds", "under_odds") if k in game}
    fix = {"home": game["home"], "away": game["away"], "league": LEAGUE_NAMES.get(game["league"], game["league"])}
    picks = analyze_football_match(fix, hs, as_, {**real, "total_line": TOTAL_LINE}, totals=True)
    score = FinalScore("", game["home"], game["away"], game["home_goals"], game["away_goals"], (), ())

    market = no_vig_pair(game["over_odds"], game["under_odds"])[0]
    model = total_outcomes(sum(expected_goals(hs, as_)), TOTAL_LINE)[0]
    additive = total_outcomes(sum(_additive_xg(hs, as_)), TOTAL_LINE)[0]
    over = game["home_goals"] + game["away_goals"] > TOTAL_LINE
    return {
        "date": game["date"], "league": game["league"], "game": f"{game['home']} - {game['away']}",
        "score": f"{game['home_goals']}-{game['away_goals']}",
        "total": {"model": model, "market": market, "over": over,
                  "blend": FOOTBALL_TOTALS_WEIGHT * model + (1 - FOOTBALL_TOTALS_WEIGHT) * market},
        "total_additive": {"model": additive, "market": market, "over": over,
                           "blend": FOOTBALL_TOTALS_WEIGHT * additive + (1 - FOOTBALL_TOTALS_WEIGHT) * market},
        "picks": [{**{k: p[k] for k in ("type", "confidence", "est_odds", "value")},
                   "market": "total" if "Goles" in p["type"] else "resultado",
                   "result": evaluate_pick(p["type"], score, draw_loses=True)} for p in picks],
    }


def replay_league(games: list[dict]) -> list[dict]:
    return [row for row in (replay_game(g, games) for g in games) if row]


# ── Reporte (puro) ────────────────────────────────────────────────

def _summary_line(label: str, picks: list[dict]) -> str:
    s = pick_summary(picks)
    if not s["n"]:
        return f"  {label:<22} n=  0"
    return (f"  {label:<22} n={s['n']:>3}  {s['wins']:>3}-{s['losses']:<3}  u={s['units']:+6.2f}  "
            f"ROI={s['roi']:+6.1%}  EV prometido={s['promised_ev']:+5.1%}")


def render_report(rows: list[dict]) -> str:
    if not rows:
        return "Backtest fútbol: sin partidos con muestra y cierre."
    picks = [p for r in rows for p in r["picks"]]
    out = [f"=== PICKS ({len(rows)} partidos con ≥ {FOOTBALL_MIN_SIDE_GAMES} previos por equipo) ===",
           _summary_line("TOTAL", picks)]
    if picks:
        lo, hi = bootstrap_roi(picks)
        out.append(f"  IC 90 % ROI (bootstrap): {lo:+.1%} … {hi:+.1%}")
        out += [_summary_line(mk, [p for p in picks if p["market"] == mk]) for mk in ("total", "resultado")]
        out += [_summary_line(lg, [p for r in rows if r["league"] == lg for p in r["picks"]])
                for lg in sorted({r["league"] for r in rows})]
    out += ["", f"=== OVER {TOTAL_LINE} — MODELO vs CIERRE (todas las predicciones) ==="]
    for key, label in (("total", "XG casa/fuera"), ("total_additive", "XG aditivo")):
        m = probability_metrics([r[key] for r in rows], "over")
        b = m["brier"]
        out.append(f"  {label:<14} n={m['n']}  Brier modelo {b['model']:.4f} | mercado {b['market']:.4f} | "
                   f"mezcla {b['blend']:.4f}   lado del modelo {m['model_side_hits'] / m['n']:.1%}   "
                   f"peso óptimo {m['optimal_model_weight']:.2f}")
    return "\n".join(out)


# ── Red ───────────────────────────────────────────────────────────

def fetch_league(season: str, league: str) -> list[dict]:
    """CSV de football-data.co.uk; [] si no responde."""
    try:
        resp = requests.get(CSV_URL.format(season=season, league=league), timeout=CSV_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        log.warning("football-data.co.uk %s %s: %s", season, league, type(exc).__name__)
        return []
    return parse_csv(resp.content.decode("utf-8-sig", errors="replace"), league)


def run(season: str, leagues: list[str]) -> list[dict]:
    return [row for league in leagues for row in replay_league(fetch_league(season, league))]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Backtest del modelo de fútbol contra cuotas de cierre")
    parser.add_argument("--season", default="2526", help="temporada en formato football-data.co.uk (2526)")
    parser.add_argument("--leagues", default=",".join(LEAGUE_NAMES), help="códigos separados por coma")
    parser.add_argument("--json", default=None, help="guardar el detalle por partido en este archivo")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    rows = run(args.season, [lg.strip() for lg in args.leagues.split(",") if lg.strip()])
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1, ensure_ascii=False)
    print(render_report(rows))


if __name__ == "__main__":
    main()
