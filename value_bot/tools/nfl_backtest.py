"""
Backtest del modelo NFL contra las líneas de cierre de DraftKings.

    python -m value_bot.tools.nfl_backtest --days 45 [--games N] [--json salida.json]

- Ratings y media de liga solo con juegos FINAL anteriores a cada kickoff (sin lookahead).
- Líneas: summary.pickcenter de ESPN (el scoreboard las borra al terminar el juego).
- Corre analyze_nfl_game y el filtro de semanas tal como en producción, y además guarda
  la probabilidad del modelo, del mercado y de la mezcla en los 3 mercados (aunque los
  totales estén apagados) para medir si el modelo aporta algo frente al mercado.
- Solo lee ESPN: no toca la BD ni manda nada a Telegram.

Limitación: el bot apuesta con la línea de las 9:00 ET, no la de cierre; el cierre es la
referencia más difícil de batir, así que el backtest es conservador.
"""
import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from value_bot.analysis.nfl_model import (
    NFL_HFA, _data_quality, _model_weight, analyze_nfl_game, blend,
    cover_prob, is_pickable_week, no_vig_pair, over_prob, project_game, team_rating,
)
from value_bot.config import (
    ESPN_NFL_SCHEDULE_URL, ESPN_NFL_URL, MIN_CONFIDENCE, NFL_MIN_GAMES, VALUE_THRESHOLD,
    http_get, nfl_season,
)
from value_bot.sports.nfl import (
    PICKABLE_SEASON_TYPES, REGULAR_SEASON, SCHEDULE_FETCH_WORKERS, _status_name, _to_float,
    fetch_team_ids, league_average,
)
from value_bot.tools.backtest_common import grade, has_full_market, outcome, parse_dt, parse_pickcenter
from value_bot.tools.backtest_report import render_report

log = logging.getLogger(__name__)

ESPN_NFL_SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
DEFAULT_DAYS = 45  # ~temporada regular hasta mediados de octubre


# ── Puro ──────────────────────────────────────────────────────────

def _scores_before(history: dict, team_id: str, kickoff: datetime) -> list[tuple[float, float]]:
    return [(pf, pa) for played, pf, pa in history.get(team_id, []) if played < kickoff]


def _ratings(team_ids, home_id, away_id, current, previous, kickoff):
    """Media de liga y ratings como en producción, con el corte en el kickoff."""
    def sample(tid):
        cur = _scores_before(current, tid, kickoff)
        prev = [] if len(cur) >= NFL_MIN_GAMES else _scores_before(previous, tid, kickoff)
        return cur, prev

    league_avg = league_average([cur + prev for cur, prev in map(sample, team_ids)])

    def rating(tid):
        cur, prev = sample(tid)
        return team_rating(cur, league_avg, current_season=len(cur) >= NFL_MIN_GAMES, previous=prev)

    return league_avg, rating(home_id), rating(away_id)


def _probabilities(lines, home_rating, away_rating, league_avg, neutral):
    exp_h, exp_a = project_game(home_rating, away_rating, league_avg, hfa=0.0 if neutral else NFL_HFA)
    margin, total = exp_h - exp_a, exp_h + exp_a
    weight = _model_weight(_data_quality(home_rating, away_rating))
    pairs = {
        "ml":     (cover_prob(margin, 0), no_vig_pair(lines["home_ml"], lines["away_ml"])[0]),
        "spread": (cover_prob(margin, lines["home_spread"]),
                   no_vig_pair(lines["home_spread_odds"], lines["away_spread_odds"])[0]),
        "total":  (over_prob(total, lines["total"]), no_vig_pair(lines["over_odds"], lines["under_odds"])[0]),
    }
    probs = {k: {"model": m, "market": mk, "blend": blend(m, mk, weight)} for k, (m, mk) in pairs.items()}
    return probs, exp_h, exp_a


def replay_game(event: dict, lines: dict, current: dict, previous: dict, team_ids: list[str]) -> dict:
    """Re-corre el modelo para un juego terminado con lo que se sabía antes del kickoff."""
    comp = event["competitions"][0]
    home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
    away = next(c for c in comp["competitors"] if c["homeAway"] == "away")
    hs, as_ = float(home["score"]), float(away["score"])
    kickoff = parse_dt(event["date"])
    game = {
        "event_id": event["id"], "home": home["team"]["displayName"], "away": away["team"]["displayName"],
        "week": (event.get("week") or {}).get("number"), "season_type": (event.get("season") or {}).get("type"),
        "neutral_site": bool(comp.get("neutralSite")), "lines": lines,
    }
    league_avg, hr, ar = _ratings(team_ids, home["team"]["id"], away["team"]["id"], current, previous, kickoff)
    pickable = is_pickable_week(game)
    picks = analyze_nfl_game(game, hr, ar, league_avg, min_conf=MIN_CONFIDENCE,
                             value_threshold=VALUE_THRESHOLD) if pickable else []
    probs, exp_h, exp_a = _probabilities(lines, hr, ar, league_avg, game["neutral_site"])
    probs["ml"]["home_won"] = outcome(hs - as_)
    probs["spread"]["home_covered"] = outcome(hs - as_ + lines["home_spread"])
    probs["total"]["over"] = outcome(hs + as_ - lines["total"])
    return {
        "week": game["week"], "date": event["date"], "game": f"{game['away']} @ {game['home']}",
        "score": f"{as_:.0f}-{hs:.0f}", "league_avg": round(league_avg, 2), "dq": _data_quality(hr, ar),
        "home_rating": hr, "away_rating": ar, "pickable": pickable,
        "model_spread": round(exp_a - exp_h, 1), "market_spread": lines["home_spread"],
        "model_total": round(exp_h + exp_a, 1), "market_total": lines["total"],
        "actual_margin": hs - as_, "actual_total": hs + as_, "lines": lines, **probs,
        "picks": [{**{k: p[k] for k in ("market", "side", "type", "confidence", "est_odds", "value")},
                   "result": grade(p, game, hs, as_)} for p in picks],
    }


# ── Red ───────────────────────────────────────────────────────────

def _team_history(team_id: str, season: int) -> list[tuple[datetime, float, float]]:
    """Juegos FINAL de temporada regular con fecha: [(kickoff, pts_a_favor, pts_en_contra)]."""
    data = http_get(ESPN_NFL_SCHEDULE_URL.format(team_id=team_id),
                    params={"season": season, "seasontype": REGULAR_SEASON})
    out = []
    for event in data.get("events") or []:
        comp = (event.get("competitions") or [{}])[0]
        if _status_name(comp) != "STATUS_FINAL":
            continue
        competitors = comp.get("competitors") or []
        own = next((c for c in competitors if str((c.get("team") or {}).get("id")) == str(team_id)), None)
        opp = next((c for c in competitors if c is not own), None)
        pf, pa = (_to_float(own.get("score")), _to_float(opp.get("score"))) if own and opp else (None, None)
        if pf is not None and pa is not None:
            out.append((parse_dt(event["date"]), pf, pa))
    return out


def _finished_events(days: int) -> list[dict]:
    today = datetime.now(timezone.utc)
    events = []
    for offset in range(days, -1, -1):
        day = (today - timedelta(days=offset)).strftime("%Y%m%d")
        for event in http_get(ESPN_NFL_URL, params={"dates": day}).get("events") or []:
            comp = (event.get("competitions") or [{}])[0]
            if (_status_name(comp) == "STATUS_FINAL"
                    and (event.get("season") or {}).get("type") in PICKABLE_SEASON_TYPES):
                events.append(event)
    return sorted(events, key=lambda e: e["date"])


def run(days: int, max_games: int | None = None) -> list[dict]:
    events = _finished_events(days)
    if max_games:
        events = events[-max_games:]
    team_ids = fetch_team_ids()
    if not events or not team_ids:
        log.warning("Backtest: %d juegos, %d equipos → nada que evaluar", len(events), len(team_ids))
        return []
    season = nfl_season()
    with ThreadPoolExecutor(max_workers=SCHEDULE_FETCH_WORKERS) as pool:
        current = dict(zip(team_ids, pool.map(lambda t: _team_history(t, season), team_ids)))
        previous = dict(zip(team_ids, pool.map(lambda t: _team_history(t, season - 1), team_ids)))
        summaries = pool.map(lambda e: http_get(ESPN_NFL_SUMMARY_URL, params={"event": e["id"]}), events)
        lines_by_id = {e["id"]: parse_pickcenter(s) for e, s in zip(events, summaries)}

    rows = []
    for event in events:
        lines = lines_by_id[event["id"]]
        if not has_full_market(lines):
            log.warning("Backtest: %s sin líneas completas de cierre, se omite", event.get("shortName"))
            continue
        rows.append(replay_game(event, lines, current, previous, team_ids))
    return rows


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Backtest del modelo NFL contra líneas de cierre")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="días hacia atrás a consultar")
    parser.add_argument("--games", type=int, default=None, help="solo los últimos N juegos")
    parser.add_argument("--json", default=None, help="guardar el detalle por juego en este archivo")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    rows = run(args.days, args.games)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1, default=str, ensure_ascii=False)
    print(render_report(rows))


if __name__ == "__main__":
    main()
