"""NFL: partidos, líneas de mercado y marcadores vía ESPN Hidden API (sin key)."""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import NamedTuple
from zoneinfo import ZoneInfo

from value_bot.config import (
    DAYS_AHEAD, ESPN_NFL_SCHEDULE_URL, ESPN_NFL_TEAMS_URL, ESPN_NFL_URL, NFL_MIN_GAMES,
    http_get, nfl_season,
)
from value_bot.scrapers.espn_lines import obj as _obj, parse_lines as _parse_lines, to_float as _to_float
from value_bot.store import store

log = logging.getLogger(__name__)

NFL_LEAGUE_AVG_DEFAULT = 22.5  # puntos por equipo y juego, temporadas recientes
MIN_GAMES_FOR_LEAGUE_AVG = 10
REGULAR_SEASON = 2             # seasontype ESPN
POSTSEASON = 3
PICKABLE_SEASON_TYPES = {REGULAR_SEASON, POSTSEASON}  # sin pretemporada (1) ni offseason (4)
ESPN_TZ = ZoneInfo("America/New_York")  # ESPN agrupa la jornada NFL por día ET
SCHEDULE_FETCH_WORKERS = 8     # requests paralelos a ESPN para la media de liga


class TeamScores(NamedTuple):
    scores: list[tuple[float, float]]           # temporada actual
    current: bool  # False si incluye la temporada anterior o no hay datos
    previous: tuple[tuple[float, float], ...] = ()  # temporada anterior (pesa menos en el rating)


# ── Parsing (puro) ────────────────────────────────────────────────

def _status_name(comp: dict) -> str | None:
    return _obj(_obj(comp, "status"), "type").get("name")


def _et_date(kickoff: str) -> str:
    """Día del juego en hora ET (como ESPN y el resolver), no en UTC."""
    try:
        return datetime.fromisoformat(kickoff.replace("Z", "+00:00")).astimezone(ESPN_TZ).date().isoformat()
    except (AttributeError, ValueError):
        return str(kickoff)[:10]


def _record(competitor: dict) -> str:
    recs = competitor.get("records") or []
    first = recs[0] if recs and isinstance(recs[0], dict) else {}
    return first.get("summary") or ""


def parse_espn_nfl_event(event: dict, default_season_type: int | None = None) -> dict | None:
    """
    Evento del scoreboard ESPN → juego. None si no está programado o no es de
    temporada regular/postemporada (la pretemporada no se pronostica).
    """
    season_type = _obj(event, "season").get("type", default_season_type)
    if season_type not in PICKABLE_SEASON_TYPES:
        return None
    comps = event.get("competitions") or []
    if not comps:
        return None
    comp = comps[0]
    if _status_name(comp) != "STATUS_SCHEDULED":
        return None
    competitors = comp.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not (home and away):
        return None

    kickoff = event.get("date", "")
    home_team, away_team = _obj(home, "team"), _obj(away, "team")
    return {
        "sport":       "nfl",
        "season_type": season_type,
        "week":        _obj(event, "week").get("number"),
        "event_id":    str(event["id"]) if event.get("id") else None,
        "date":        _et_date(kickoff),
        "kickoff":     kickoff,
        "home":        home_team.get("displayName", "?"),
        "home_id":     home_team.get("id"),
        "away":        away_team.get("displayName", "?"),
        "away_id":     away_team.get("id"),
        "home_record": _record(home),
        "away_record": _record(away),
        "venue":       _obj(comp, "venue").get("fullName", ""),
        "neutral_site": bool(comp.get("neutralSite")),
        "lines":       _parse_lines(comp),
        "source":      "espn",
    }


def league_games(games: list[dict], team_ids: set[str]) -> list[dict]:
    """
    Solo juegos entre equipos de la liga (fuera el Pro Bowl AFC vs NFC).
    Sin lista de equipos (request fallido) no se filtra.
    """
    if not team_ids:
        return games
    return [g for g in games if str(g.get("home_id")) in team_ids and str(g.get("away_id")) in team_ids]


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
        if _status_name(comp) != "STATUS_FINAL":
            continue
        competitors = comp.get("competitors") or []
        own = next((c for c in competitors if str(_obj(c, "team").get("id")) == str(team_id)), None)
        opp = next((c for c in competitors if c is not own), None)
        if not (own and opp):
            continue
        pf, pa = _to_float(own.get("score")), _to_float(opp.get("score"))
        if pf is not None and pa is not None:
            scores.append((pf, pa))
    return scores


# ── Recolección (red) ─────────────────────────────────────────────

def espn_query_dates(now: datetime, days_ahead: int) -> list[str]:
    """Días YYYYMMDD a consultar, en hora del este (como ESPN arma la jornada)."""
    today_et = now.astimezone(ESPN_TZ).date()
    return [(today_et + timedelta(days=d)).strftime("%Y%m%d") for d in range(days_ahead + 1)]


def _parse_board(data: dict, date_str: str) -> list[dict]:
    """Eventos de un día; un evento mal formado se loguea y se salta sin tirar el resto."""
    board_season_type = _obj(data, "season").get("type")
    games = []
    for event in data.get("events") or []:
        try:
            game = parse_espn_nfl_event(event, default_season_type=board_season_type)
        except (AttributeError, TypeError, KeyError, IndexError, ValueError) as exc:
            log.warning("NFL %s: evento %s mal formado, se omite (%r)",
                        date_str, event.get("id") if isinstance(event, dict) else "?", exc)
            continue
        if game:
            games.append(game)
    return games


def get_nfl_games() -> list[dict]:
    """Juegos programados de hoy a hoy + DAYS_AHEAD (ESPN no acepta rangos en NFL)."""
    games = []
    for date_str in espn_query_dates(datetime.now(timezone.utc), DAYS_AHEAD):
        games.extend(_parse_board(http_get(ESPN_NFL_URL, params={"dates": date_str}), date_str))
    games = league_games(games, set(get_team_ids()))
    with_market = sum(1 for g in games if g["lines"])
    log.info("NFL games: %d (%d con líneas de mercado)", len(games), with_market)
    return games


def _fetch_scores(team_id: str, season: int) -> list[tuple[float, float]] | None:
    data = http_get(
        ESPN_NFL_SCHEDULE_URL.format(team_id=team_id),
        params={"season": season, "seasontype": REGULAR_SEASON},
    )
    return parse_team_scores(data, team_id)


def get_team_scores(team_id: str) -> TeamScores:
    """
    Marcadores de la temporada actual. Si aún no hay NFL_MIN_GAMES, se agregan
    los de la temporada anterior en `previous` (current=False) en vez de descartar
    los actuales.
    Caché solo dentro de la corrida (store se limpia en cada run); los fallos de
    red no se cachean para poder reintentar.
    """
    if team_id in store.nfl_scores_cache:
        return store.nfl_scores_cache[team_id]
    current = _fetch_scores(team_id, nfl_season())
    if current is None:
        log.warning("NFL team %s: no se pudo leer el calendario → media de liga", team_id)
        return TeamScores([], current=False)
    if len(current) >= NFL_MIN_GAMES:
        result = TeamScores(current, current=True)
        store.nfl_scores_cache[team_id] = result
        return result

    previous_season = nfl_season(offset=-1)
    previous = _fetch_scores(team_id, previous_season)
    if previous is None:
        log.warning("NFL team %s: no se pudo leer la temporada %d → solo %d juegos actuales",
                    team_id, previous_season, len(current))
        return TeamScores(current, current=False)
    log.info("NFL team %s: %d juegos en temporada actual → sumando %d juegos de %d",
             team_id, len(current), len(previous), previous_season)
    result = TeamScores(current, current=False, previous=tuple(previous))
    store.nfl_scores_cache[team_id] = result
    return result


def fetch_team_ids() -> list[str]:
    """IDs ESPN de los 32 equipos; [] si el request falla."""
    data = http_get(ESPN_NFL_TEAMS_URL)
    leagues = (data.get("sports") or [{}])[0].get("leagues") or [{}]
    return [str(t["team"]["id"]) for t in leagues[0].get("teams", []) if _obj(t, "team").get("id")]


def get_team_ids() -> list[str]:
    """fetch_team_ids cacheado dentro de la corrida (no se cachea un fallo)."""
    if not store.nfl_team_ids:
        store.nfl_team_ids = fetch_team_ids()
    return store.nfl_team_ids


def league_average(score_lists: list[list[tuple[float, float]]]) -> float:
    """Puntos por equipo y juego entre los equipos dados."""
    points = [pf for scores in score_lists for pf, _ in scores]
    if len(points) < MIN_GAMES_FOR_LEAGUE_AVG:
        return NFL_LEAGUE_AVG_DEFAULT
    return sum(points) / len(points)


def get_league_average() -> float:
    """
    Media de toda la liga (32 equipos), no solo de los que juegan hoy: un slate
    de equipos goleadores inflaría la media y bajaría todos los totales.
    Los calendarios se piden en paralelo (32–64 requests).
    """
    team_ids = get_team_ids()
    if not team_ids:
        log.warning("NFL: no se pudo leer la lista de equipos → media por defecto %.1f",
                    NFL_LEAGUE_AVG_DEFAULT)
        return NFL_LEAGUE_AVG_DEFAULT
    with ThreadPoolExecutor(max_workers=SCHEDULE_FETCH_WORKERS) as pool:
        teams = list(pool.map(get_team_scores, team_ids))
    return league_average([t.scores + list(t.previous) for t in teams])
