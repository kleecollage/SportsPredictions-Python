"""NFL: partidos, líneas de mercado y marcadores vía ESPN Hidden API (sin key)."""
import logging
from datetime import datetime, timedelta

from value_bot.analysis.nfl_model import american_to_decimal
from value_bot.config import (
    DAYS_AHEAD, ESPN_NFL_SCHEDULE_URL, ESPN_NFL_URL, NFL_MIN_GAMES,
    http_get, nfl_season,
)
from value_bot.store import store

log = logging.getLogger(__name__)

NFL_LEAGUE_AVG_DEFAULT = 22.5  # puntos por equipo y juego, temporadas recientes
MIN_GAMES_FOR_LEAGUE_AVG = 10
REGULAR_SEASON = 2             # seasontype ESPN


# ── Parsing (puro) ────────────────────────────────────────────────

def _to_float(raw) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        raw = raw.get("value", raw.get("displayValue"))
    try:
        return float(str(raw).lstrip("ou"))
    except (TypeError, ValueError):
        return None


def _latest(side: dict) -> dict:
    """Línea de cierre si existe; si no, la de apertura."""
    return side.get("close") or side.get("open") or {}


def _parse_lines(comp: dict) -> dict:
    odds_list = comp.get("odds") or []
    if not odds_list:
        return {}
    odds = odds_list[0]
    ml = odds.get("moneyline", {})
    spread = odds.get("pointSpread", {})
    total = odds.get("total", {})

    home_spread = _latest(spread.get("home", {}))
    away_spread = _latest(spread.get("away", {}))
    over, under = _latest(total.get("over", {})), _latest(total.get("under", {}))

    market = {
        "provider":         odds.get("provider", {}).get("displayName", ""),
        "home_ml":          american_to_decimal(_latest(ml.get("home", {})).get("odds")),
        "away_ml":          american_to_decimal(_latest(ml.get("away", {})).get("odds")),
        "home_spread":      _to_float(home_spread.get("line")),
        "home_spread_odds": american_to_decimal(home_spread.get("odds")),
        "away_spread_odds": american_to_decimal(away_spread.get("odds")),
        "total":            _to_float(over.get("line")) or _to_float(odds.get("overUnder")),
        "over_odds":        american_to_decimal(over.get("odds")),
        "under_odds":       american_to_decimal(under.get("odds")),
    }
    return {k: v for k, v in market.items() if v not in (None, "")}


def parse_espn_nfl_event(event: dict) -> dict | None:
    """Evento del scoreboard ESPN → juego. None si no está programado."""
    comps = event.get("competitions") or []
    if not comps:
        return None
    comp = comps[0]
    if comp.get("status", {}).get("type", {}).get("name") != "STATUS_SCHEDULED":
        return None
    competitors = comp.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not (home and away):
        return None

    def record(c):
        recs = c.get("records") or []
        return recs[0].get("summary", "") if recs else ""

    kickoff = event.get("date", "")
    return {
        "sport":       "nfl",
        "date":        kickoff[:10],
        "kickoff":     kickoff,
        "home":        home.get("team", {}).get("displayName", "?"),
        "home_id":     home.get("team", {}).get("id"),
        "away":        away.get("team", {}).get("displayName", "?"),
        "away_id":     away.get("team", {}).get("id"),
        "home_record": record(home),
        "away_record": record(away),
        "venue":       comp.get("venue", {}).get("fullName", ""),
        "lines":       _parse_lines(comp),
        "source":      "espn",
    }


def parse_team_scores(schedule: dict, team_id: str) -> list[tuple[float, float]] | None:
    """
    Calendario ESPN de un equipo → [(pts_a_favor, pts_en_contra)] de juegos terminados.
    None si la respuesta no trae calendario (request fallido), para no confundirlo
    con "aún no jugó".
    """
    if "events" not in schedule:
        return None
    scores = []
    for event in schedule.get("events") or []:
        comp = (event.get("competitions") or [{}])[0]
        if comp.get("status", {}).get("type", {}).get("name") != "STATUS_FINAL":
            continue
        competitors = comp.get("competitors") or []
        own = next((c for c in competitors if str(c.get("team", {}).get("id")) == str(team_id)), None)
        opp = next((c for c in competitors if c is not own), None)
        if not (own and opp):
            continue
        pf, pa = _to_float(own.get("score")), _to_float(opp.get("score"))
        if pf is not None and pa is not None:
            scores.append((pf, pa))
    return scores


# ── Recolección (red) ─────────────────────────────────────────────

def get_nfl_games() -> list[dict]:
    """Juegos programados de hoy a hoy + DAYS_AHEAD (ESPN no acepta rangos en NFL)."""
    today = datetime.now()
    games = []
    for offset in range(DAYS_AHEAD + 1):
        date_str = (today + timedelta(days=offset)).strftime("%Y%m%d")
        data = http_get(ESPN_NFL_URL, params={"dates": date_str})
        for event in data.get("events", []):
            game = parse_espn_nfl_event(event)
            if game:
                games.append(game)
    with_market = sum(1 for g in games if g["lines"])
    log.info("NFL games: %d (%d con líneas de mercado)", len(games), with_market)
    return games


def _fetch_scores(team_id: str, season: int) -> list[tuple[float, float]] | None:
    data = http_get(
        ESPN_NFL_SCHEDULE_URL.format(team_id=team_id),
        params={"season": season, "seasontype": REGULAR_SEASON},
    )
    return parse_team_scores(data, team_id)


def get_team_scores(team_id: str) -> list[tuple[float, float]]:
    """Marcadores de la temporada actual; la anterior si aún no hay NFL_MIN_GAMES."""
    if team_id in store.nfl_scores_cache:
        return store.nfl_scores_cache[team_id]
    scores = _fetch_scores(team_id, nfl_season())
    if scores is None:
        # Fallo de red: rating = media de liga, sin cachear para poder reintentar
        log.warning("NFL team %s: no se pudo leer el calendario → media de liga", team_id)
        return []
    if len(scores) < NFL_MIN_GAMES:
        previous = nfl_season(offset=-1)
        scores = _fetch_scores(team_id, previous) or []
        log.info("NFL team %s: sin muestra en temporada actual → usando %d", team_id, previous)
    store.nfl_scores_cache[team_id] = scores
    return scores


def league_average(score_lists: list[list[tuple[float, float]]]) -> float:
    """Puntos por equipo y juego entre los equipos consultados."""
    points = [pf for scores in score_lists for pf, _ in scores]
    if len(points) < MIN_GAMES_FOR_LEAGUE_AVG:
        return NFL_LEAGUE_AVG_DEFAULT
    return sum(points) / len(points)
