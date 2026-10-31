"""
Backtest del modelo NBA contra las líneas de cierre de DraftKings.

    python -m value_bot.tools.nba_backtest [--season 2025] [--from 2025-12-01] [--to 2026-04-12]
                                           [--games N] [--json salida.json]

- Stats como en producción (ppg / opp_ppg por juego, splits casa/visita con ≥ NBA_MIN_GAMES)
  pero reconstruidas con los marcadores ESPN de la temporada regular, solo con juegos
  anteriores a cada partido (sin lookahead).
- Líneas: summary.pickcenter de ESPN (el scoreboard las borra al terminar). ESPN no trae
  cierre NBA de octubre–noviembre de 2025: esos juegos se cuentan como omitidos.
- Corre nba_model.analyze_nba_game igual que producción y guarda modelo / mercado / mezcla
  de los 3 mercados para medir si el modelo aporta algo y qué SD usar.
- Solo lee ESPN: no toca la BD ni manda nada a Telegram.

Diferencias con producción: equipos con < NBA_MIN_GAMES juegos se omiten (producción usaría
la temporada anterior), y el bot apuesta con la línea de las 9:00 ET, no la de cierre (la
referencia más difícil de batir: el backtest es conservador).
"""
import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from value_bot.analysis.market import no_vig_pair
from value_bot.analysis.nba_model import (
    analyze_nba_game, cover_prob, data_quality, model_weight, over_prob, project_nba, win_prob,
)
from value_bot.config import (
    ESPN_NBA_URL, MIN_CONFIDENCE, NBA_MIN_GAMES, VALUE_THRESHOLD, http_get, nba_season_start_year,
)
from value_bot.scrapers.espn_lines import obj, to_float
from value_bot.tools.backtest_common import grade, has_full_market, outcome, parse_dt, parse_pickcenter
from value_bot.tools.backtest_report import render_report

log = logging.getLogger(__name__)

ESPN_NBA_SUMMARY_URL = ESPN_NBA_URL.replace("/scoreboard", "/summary")
REGULAR_SEASON = 2
FETCH_WORKERS = 8
SEASON_FIRST_DAY = (10, 1)    # ningún juego de temporada regular antes de octubre
SEASON_LAST_DAY = (4, 30)     # ni después de abril
SPREAD_GAP_BUCKETS = ((0, 2), (2, 4), (4, 7), (7, 99))
TOTAL_GAP_BUCKETS = ((0, 3), (3, 6), (6, 10), (10, 99))


# ── Puro ──────────────────────────────────────────────────────────

def _final_game(event: dict) -> dict | None:
    comp = (event.get("competitions") or [{}])[0]
    if obj(obj(comp, "status"), "type").get("name") != "STATUS_FINAL":
        return None
    if obj(event, "season").get("type") != REGULAR_SEASON:
        return None
    sides = {c.get("homeAway"): c for c in comp.get("competitors") or []}
    home, away = sides.get("home"), sides.get("away")
    if not (home and away):
        return None
    hs, as_ = to_float(home.get("score")), to_float(away.get("score"))
    if hs is None or as_ is None:
        return None
    return {
        "event_id": str(event["id"]), "date": parse_dt(event["date"]),
        "home": obj(home, "team").get("displayName", "?"), "away": obj(away, "team").get("displayName", "?"),
        "home_id": str(obj(home, "team").get("id")), "away_id": str(obj(away, "team").get("id")),
        "home_score": hs, "away_score": as_,
    }


def parse_final_games(scoreboard: dict) -> list[dict]:
    """Juegos FINAL de temporada regular de un scoreboard; los mal formados se saltan."""
    games = []
    for event in scoreboard.get("events") or []:
        try:
            game = _final_game(event)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            log.warning("Backtest NBA: evento %s mal formado (%r), se salta", event.get("id"), exc)
            continue
        if game:
            games.append(game)
    return games


def team_stats_before(games: list[dict], team_id: str, kickoff: datetime,
                      location: str | None = None) -> dict | None:
    """
    {ppg, opp_ppg, played} del equipo con sus juegos anteriores al kickoff (location
    "home" | "away" para splits); None con menos de NBA_MIN_GAMES, como LeagueDash en producción.
    """
    scores = []
    for g in games:
        if g["date"] >= kickoff:
            continue
        if g["home_id"] == team_id and location in (None, "home"):
            scores.append((g["home_score"], g["away_score"]))
        elif g["away_id"] == team_id and location in (None, "away"):
            scores.append((g["away_score"], g["home_score"]))
    if len(scores) < NBA_MIN_GAMES:
        return None
    n = len(scores)
    return {"ppg": sum(s[0] for s in scores) / n, "opp_ppg": sum(s[1] for s in scores) / n, "played": n}


def _probabilities(lines: dict, margin: float, total: float, dq: str) -> dict:
    weight = model_weight(dq)
    pairs = {
        "ml":     (win_prob(margin), no_vig_pair(lines["home_ml"], lines["away_ml"])[0]),
        "spread": (cover_prob(margin, lines["home_spread"]),
                   no_vig_pair(lines["home_spread_odds"], lines["away_spread_odds"])[0]),
        "total":  (over_prob(total, lines["total"]), no_vig_pair(lines["over_odds"], lines["under_odds"])[0]),
    }
    return {k: {"model": m, "market": mk, "blend": weight * m + (1 - weight) * mk}
            for k, (m, mk) in pairs.items()}


def replay_game(game: dict, lines: dict, games: list[dict], season: str) -> dict | None:
    """Re-corre el modelo para un juego terminado con lo que se sabía antes del inicio."""
    kickoff = game["date"]
    h = team_stats_before(games, game["home_id"], kickoff)
    a = team_stats_before(games, game["away_id"], kickoff)
    if not (h and a):
        return None
    h, a = {**h, "season": season}, {**a, "season": season}
    h_split = team_stats_before(games, game["home_id"], kickoff, "home")
    a_split = team_stats_before(games, game["away_id"], kickoff, "away")
    splits = {"home": h_split, "away": a_split} if h_split and a_split else {}

    live = {**game, "lines": lines}
    picks = analyze_nba_game(live, h, a, splits, season, min_conf=MIN_CONFIDENCE,
                             value_threshold=VALUE_THRESHOLD)
    exp_h, exp_a, _ = project_nba(h, a, splits)
    dq = data_quality(h, a, season)
    hs, as_ = game["home_score"], game["away_score"]
    probs = _probabilities(lines, exp_h - exp_a, exp_h + exp_a, dq)
    probs["ml"]["home_won"] = outcome(hs - as_)
    probs["spread"]["home_covered"] = outcome(hs - as_ + lines["home_spread"])
    probs["total"]["over"] = outcome(hs + as_ - lines["total"])
    return {
        "week": kickoff.strftime("%Y-%m"), "date": kickoff.isoformat(),
        "game": f"{game['away']} @ {game['home']}", "score": f"{as_:.0f}-{hs:.0f}",
        "dq": dq, "splits": bool(splits), "pickable": True,
        "home_stats": h, "away_stats": a,
        "model_spread": round(exp_a - exp_h, 1), "market_spread": lines["home_spread"],
        "model_total": round(exp_h + exp_a, 1), "market_total": lines["total"],
        "actual_margin": hs - as_, "actual_total": hs + as_, "lines": lines, **probs,
        "picks": [{**{k: p[k] for k in ("market", "side", "type", "confidence", "est_odds", "value")},
                   "result": grade(p, live, hs, as_)} for p in picks],
    }


# ── Red ───────────────────────────────────────────────────────────

def _season_days(start_year: int, until: date) -> list[str]:
    first = date(start_year, *SEASON_FIRST_DAY)
    last = min(date(start_year + 1, *SEASON_LAST_DAY), until)
    return [(first + timedelta(days=d)).strftime("%Y%m%d") for d in range((last - first).days + 1)]


def _season_games(start_year: int, until: date) -> list[dict]:
    """Todos los juegos FINAL de temporada regular hasta `until` (un scoreboard por día)."""
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        boards = pool.map(lambda d: http_get(ESPN_NBA_URL, params={"dates": d}), _season_days(start_year, until))
        games = [g for board in boards for g in parse_final_games(board)]
    return sorted(games, key=lambda g: g["date"])


def run(start_year: int, date_from: date | None = None, date_to: date | None = None,
        max_games: int | None = None) -> tuple[list[dict], dict]:
    """(filas del reporte, conteos de juegos evaluados / omitidos)."""
    today = datetime.now(timezone.utc).date()
    date_to = min(date_to or today, today)
    games = _season_games(start_year, date_to)
    targets = [g for g in games if (date_from is None or g["date"].date() >= date_from)
               and g["date"].date() <= date_to]
    targets = [g for g in targets
               if team_stats_before(games, g["home_id"], g["date"])
               and team_stats_before(games, g["away_id"], g["date"])]
    if max_games:
        targets = targets[-max_games:]
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        summaries = pool.map(lambda g: http_get(ESPN_NBA_SUMMARY_URL, params={"event": g["event_id"]}), targets)
        lines_by_id = {g["event_id"]: parse_pickcenter(s) for g, s in zip(targets, summaries)}

    rows = []
    for game in targets:
        lines = lines_by_id[game["event_id"]]
        if has_full_market(lines):
            row = replay_game(game, lines, games, f"{start_year}-{str(start_year + 1)[-2:]}")
            if row:
                rows.append(row)
    counts = {"season_games": len(games), "with_sample": len(targets), "with_lines": len(rows)}
    return rows, counts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Backtest del modelo NBA contra líneas de cierre")
    parser.add_argument("--season", type=int, default=nba_season_start_year(-1),
                        help="año de inicio de la temporada (2025 = 2025-26)")
    parser.add_argument("--from", dest="date_from", type=date.fromisoformat, default=None)
    parser.add_argument("--to", dest="date_to", type=date.fromisoformat, default=None)
    parser.add_argument("--games", type=int, default=None, help="solo los últimos N juegos")
    parser.add_argument("--json", default=None, help="guardar el detalle por juego en este archivo")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    rows, counts = run(args.season, args.date_from, args.date_to, args.games)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1, default=str, ensure_ascii=False)
    print(f"Juegos de temporada regular: {counts['season_games']} · con ≥ {NBA_MIN_GAMES} juegos previos "
          f"por equipo: {counts['with_sample']} · con cierre completo: {counts['with_lines']}")
    print(render_report(rows, period="mes", spread_gaps=SPREAD_GAP_BUCKETS, total_gaps=TOTAL_GAP_BUCKETS))


if __name__ == "__main__":
    main()
