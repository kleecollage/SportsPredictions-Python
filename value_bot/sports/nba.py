"""NBA: juegos (api-sports + ESPN, que trae las líneas) y stats por equipo (nba_api → api-sports)."""
import functools
import time
import logging
from datetime import datetime, timedelta

import requests

from value_bot.config import (
    ABB_BASE, NBA_API_TIMEOUT, NBA_LEAGUE_ID, NBA_MIN_GAMES,
    DAYS_AHEAD, nba_season, nba_api_season,
)
from value_bot.http_client import api_sports_get
from value_bot.store import store
from value_bot.scrapers.espn import espn_nba_games, espn_parse_nba, now_et

try:
    from nba_api.stats.static import teams as nba_static_teams
    from nba_api.stats.endpoints import (
        leaguedashteamstats,
        teamgamelog,
        teamdashboardbygeneralsplits,
    )
    NBA_API_OK = True
except ImportError:
    NBA_API_OK = False

log = logging.getLogger(__name__)

if not NBA_API_OK:
    log.warning("nba_api no disponible — usando api-sports + ESPN como fuente NBA")

NBA_PPG_RANGE = (85.0, 140.0)  # fuera de esto el dato viene mal (p. ej. totales en vez de promedio)
# Nombres que usan ESPN / api-sports / Flashscore y no coinciden con nba_api
_TEAM_ALIASES = {
    "la clippers": "los angeles clippers",
    "la lakers":   "los angeles lakers",
}


def _is_nba_playoffs_month():
    return datetime.now().month in (4, 5, 6)


def _nba_stat_sources() -> list[tuple[str, str]]:
    """(temporada nba_api, tipo) en orden de prioridad.

    En meses de Playoffs: Playoffs y luego regular de la temporada actual.
    Resto del año: regular actual y, si aún no hay muestra (inicio de
    temporada), la regular anterior. Nunca Playoffs viejos fuera de abril-junio.
    """
    current, previous = nba_api_season(), nba_api_season(offset=-1)
    if _is_nba_playoffs_month():
        return [(current, "Playoffs"), (current, "Regular Season")]
    return [(current, "Regular Season"), (previous, "Regular Season")]


# ── Nombres de equipo → ID nba_api ────────────────────────────────

def _norm(name) -> str:
    key = " ".join(str(name or "").lower().split())
    return _TEAM_ALIASES.get(key, key)


@functools.cache
def _team_index() -> dict[str, int]:
    """Nombre completo y apodo normalizados → ID (los apodos NBA son únicos)."""
    index = {}
    for team in nba_static_teams.get_teams():
        index[_norm(team["full_name"])] = team["id"]
        index.setdefault(_norm(team["nickname"]), team["id"])
    return index


def _nba_find_team_id(name) -> int | None:
    """ID nba_api solo con coincidencia exacta (nombre, apodo o alias): nunca por parecido."""
    if not NBA_API_OK:
        return None
    return _team_index().get(_norm(name))


def _team_key(name):
    """Clave para comparar equipos entre fuentes: ID si se conoce, si no el nombre normalizado."""
    return _nba_find_team_id(name) or _norm(name)


# ── nba_api: disponibilidad y lectura de filas ────────────────────

def _nba_api_available() -> bool:
    return NBA_API_OK and not store.nba_api_down


def _on_nba_api_error(exc: Exception, what: str) -> None:
    """Un fallo de red corta nba_api para el resto de la corrida (cada intento esperaría el timeout)."""
    if isinstance(exc, requests.exceptions.RequestException):
        store.nba_api_down = True
        log.warning("nba_api sin respuesta (%s): %s → se omite nba_api el resto de la corrida", what, exc)
    else:
        log.warning("nba_api %s: %s", what, exc)


_PARSE_ERRORS = (KeyError, IndexError, TypeError, ValueError, AttributeError)


def _per_game_line(row) -> dict | None:
    """Fila por juego de nba_api → {ppg, opp_ppg, win_pct, gp, pm}; None si la muestra no sirve."""
    gp = int(row.get("GP", 0) or 0)
    if gp < NBA_MIN_GAMES:
        return None
    ppg = float(row.get("PTS", 0) or 0)
    pm = float(row.get("PLUS_MINUS", 0) or 0)
    if not all(NBA_PPG_RANGE[0] <= pts <= NBA_PPG_RANGE[1] for pts in (ppg, ppg - pm)):
        log.warning("NBA: ppg %.1f / recibidos %.1f fuera de rango (%s) — se descarta",
                    ppg, ppg - pm, row.get("TEAM_NAME", "?"))
        return None
    return {
        "ppg":     round(ppg, 1),
        "opp_ppg": round(ppg - pm, 1),
        "win_pct": round(int(row.get("W", 0) or 0) / gp * 100, 1),
        "gp":      gp,
        "pm":      round(pm, 1),
    }


def nba_get_league_stats(season, season_type):
    """LeagueDashTeamStats por juego, cacheado. → {team_id: {ppg, opp_ppg, win_pct, gp, pm}}"""
    cache_key = (season, season_type)
    if cache_key in store.nba_league_cache:
        return store.nba_league_cache[cache_key]
    if not _nba_api_available():
        return {}
    try:
        time.sleep(0.8)
        df = leaguedashteamstats.LeagueDashTeamStats(
            season=season,
            season_type_all_star=season_type,
            per_mode_detailed="PerGame",
            timeout=NBA_API_TIMEOUT,
        ).get_data_frames()[0]
    except Exception as exc:
        _on_nba_api_error(exc, f"LeagueDash {season} {season_type}")
        store.nba_league_cache[cache_key] = {}
        return {}
    try:
        result = {int(row["TEAM_ID"]): line for _, row in df.iterrows()
                  if (line := _per_game_line(row))}
    except _PARSE_ERRORS as exc:
        log.warning("nba_api LeagueDash %s %s: respuesta inesperada (%r) — se ignora",
                    season, season_type, exc)
        result = {}
    store.nba_league_cache[cache_key] = result
    log.info("nba_api %s %s stats: %d equipos", season, season_type, len(result))
    return result


def _form_from_log(df, season: str, stype: str) -> dict:
    r5   = df.head(5)
    form = "".join(r5["WL"].tolist())
    return {
        "form":       form,
        "form_pts":   sum(3 if c == "W" else 0 for c in form),
        "ppg":        round(float(df.head(10)["PTS"].mean()), 1),
        "recent_pts": [int(x) for x in r5["PTS"].tolist()],
        "win_rate":   round(float((df["WL"] == "W").mean() * 100), 1),
        "played":     len(df),
        "stype":      stype,
        "season":     season,
    }


def _form_available() -> bool:
    return _nba_api_available() and not store.nba_form_down


def nba_get_team_form(team_name):
    """
    TeamGameLog — últimos juegos {form, ppg, ...}. Solo para mostrar en Telegram (el
    modelo no la usa; nba_picks la pide solo para los juegos con pick): un fallo de red
    corta la forma (store.nba_form_down), no nba_api, para no dejar sin splits ni
    LeagueDash al resto de los juegos.
    """
    if team_name in store.nba_team_form_cache:
        return store.nba_team_form_cache[team_name]
    tid = _nba_find_team_id(team_name)
    if NBA_API_OK and not tid:
        log.warning("nba_api: equipo no encontrado: '%s'", team_name)
        store.nba_team_form_cache[team_name] = {}
        return {}
    if not _form_available():
        return {}
    for season, stype in _nba_stat_sources():
        try:
            time.sleep(0.6)
            df = teamgamelog.TeamGameLog(
                team_id=tid, season=season, season_type_all_star=stype, timeout=NBA_API_TIMEOUT,
            ).get_data_frames()[0]
        except requests.exceptions.RequestException as exc:
            store.nba_form_down = True
            log.warning("nba_api TeamGameLog sin respuesta (%s): %s → sin forma el resto de la corrida",
                        team_name, exc)
            return {}
        except Exception as exc:
            log.warning("nba_api TeamGameLog %s %s %s: %s", team_name, season, stype, exc)
            continue
        if df.empty or len(df) < NBA_MIN_GAMES:
            continue
        try:
            res = _form_from_log(df, season, stype)
        except _PARSE_ERRORS as exc:
            log.warning("nba_api TeamGameLog %s: respuesta inesperada (%r) — se ignora", team_name, exc)
            continue
        store.nba_team_form_cache[team_name] = res
        return res
    store.nba_team_form_cache[team_name] = {}
    return {}


def nba_get_team_full_stats(team_name):
    """
    LeagueDash del propio equipo (prioridad de _nba_stat_sources). Sin LeagueDash → {}:
    no se inventan puntos recibidos.
    """
    tid = _nba_find_team_id(team_name)
    if tid is None:
        return {}
    found = next(((season, stats[tid]) for season, stype in _nba_stat_sources()
                  if tid in (stats := nba_get_league_stats(season, stype))), None)
    if not found:
        return {}
    season, ls = found
    return {
        "ppg":      ls["ppg"],
        "opp_ppg":  ls["opp_ppg"],
        "win_rate": ls["win_pct"],
        "played":   ls["gp"],
        "pm":       ls["pm"],
        "source":   "nba_api",
        "season":   season,
    }


def _apisports_value(node, *path) -> float | None:
    """node[path...] → float; None si falta algún nivel (api-sports cambia dict / número)."""
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
    return float(node) if node not in (None, "") else None


def get_nba_team_stats_apisports(team_id):
    """
    Respaldo cuando nba_api no tiene al equipo, con la misma validación que LeagueDash
    (_per_game_line: muestra mínima y puntos en rango). Respuesta con otra forma → {}.
    Sin "season": cuenta como data_quality "medium".
    """
    data = api_sports_get(ABB_BASE, "statistics", {
        "team": team_id, "league": NBA_LEAGUE_ID, "season": nba_season(),
    })
    resp = data.get("response") if isinstance(data, dict) else None
    if not isinstance(resp, dict) or not resp:
        return {}
    try:
        played = _apisports_value(resp, "games", "played", "all")
        ppg = _apisports_value(resp, "points", "for", "average", "all")
        opp = _apisports_value(resp, "points", "against", "average", "all")
        wins = _apisports_value(resp, "wins", "all", "total") or 0
    except _PARSE_ERRORS as exc:
        log.warning("api-sports stats NBA %s: respuesta inesperada (%r) — se ignora", team_id, exc)
        return {}
    if played is None or ppg is None or opp is None:
        return {}
    line = _per_game_line({"GP": played, "W": wins, "PTS": ppg, "PLUS_MINUS": ppg - opp,
                           "TEAM_NAME": f"api-sports {team_id}"})
    if not line:
        return {}
    return {
        "ppg": line["ppg"], "opp_ppg": line["opp_ppg"], "win_rate": line["win_pct"],
        "played": line["gp"], "pm": line["pm"], "source": "api-sports",
    }


def nba_get_home_away_splits(team_name: str, location: str, season: str | None = None) -> dict:
    """
    TeamDashboardByGeneralSplits → stats por juego home/away de un equipo.

    location: "home" | "road"; season: solo esa temporada (la de las stats base del
    equipo), así no se piden splits de otra temporada que luego se descartan.
    Retorna {ppg, opp_ppg, win_pct, gp, source, season} para ese split, o {} si falla.
    Cacheado en store.nba_splits_cache[(team_name, location, season)].
    """
    cache_key = (team_name, location, season)
    if cache_key in store.nba_splits_cache:
        return store.nba_splits_cache[cache_key]
    tid = _nba_find_team_id(team_name)
    if not (_nba_api_available() and tid):
        return {}

    row_label = "Home" if location == "home" else "Road"
    sources = [(s, t) for s, t in _nba_stat_sources() if season is None or s == season]
    for season, season_type in sources:
        if not _nba_api_available():
            break
        try:
            time.sleep(0.8)
            dash = teamdashboardbygeneralsplits.TeamDashboardByGeneralSplits(
                team_id=tid,
                season=season,
                season_type_all_star=season_type,
                per_mode_detailed="PerGame",
                timeout=NBA_API_TIMEOUT,
            )
            loc_df = dash.get_data_frames()[1]  # index 1 = LocationTeamDashboard (Home / Road)
        except Exception as exc:
            _on_nba_api_error(exc, f"splits {team_name} {location} {season_type}")
            continue
        try:
            rows = loc_df[loc_df["GROUP_VALUE"] == row_label] if not loc_df.empty else loc_df
            line = _per_game_line(rows.iloc[0]) if not rows.empty else None
        except _PARSE_ERRORS as exc:
            log.warning("nba_api splits %s: respuesta inesperada (%r) — se ignora", team_name, exc)
            continue
        if not line:
            continue
        result = {
            "ppg":     line["ppg"],
            "opp_ppg": line["opp_ppg"],
            "win_pct": line["win_pct"],
            "gp":      line["gp"],
            "source":  f"nba_api_splits_{season_type.replace(' ', '_').lower()}",
            "season":  season,
        }
        store.nba_splits_cache[cache_key] = result
        log.info("Splits %s (%s, %s %s): ppg=%.1f opp=%.1f",
                 team_name, location, season, season_type, result["ppg"], result["opp_ppg"])
        return result

    store.nba_splits_cache[cache_key] = {}
    return {}


def get_best_nba_stats(game):
    """
    (h_stats, a_stats, splits): nba_api → api-sports → {}.
    splits = {"home": {...}, "away": {...}} solo con ambos equipos de nba_api y de la
    misma temporada que sus stats base (al inicio de temporada los splits caen a la
    anterior y no deben pisar datos actuales); si no, {}.
    La forma reciente no entra: es dato del mensaje (nba_picks la pide solo para los picks).
    """
    h_stats = nba_get_team_full_stats(game["home"])
    a_stats = nba_get_team_full_stats(game["away"])
    if not h_stats and game.get("home_id"):
        h_stats = get_nba_team_stats_apisports(game["home_id"])
    if not a_stats and game.get("away_id"):
        a_stats = get_nba_team_stats_apisports(game["away_id"])

    splits: dict = {}
    if all((stats or {}).get("source") == "nba_api" for stats in (h_stats, a_stats)):
        h_split = nba_get_home_away_splits(game["home"], "home", season=h_stats["season"])
        a_split = nba_get_home_away_splits(game["away"], "road", season=a_stats["season"])
        if (h_split and a_split and h_split.get("season") == h_stats["season"]
                and a_split.get("season") == a_stats["season"]):
            splits = {"home": h_split, "away": a_split}
    return h_stats, a_stats, splits


def _same_matchup(a: dict, b: dict) -> bool:
    """Mismo partido en dos fuentes: coinciden local y visitante (por ID o nombre normalizado)."""
    return (_team_key(a["home"]) == _team_key(b["home"])
            and _team_key(a["away"]) == _team_key(b["away"]))


def _game(date_str: str, home: str, away: str, source: str, **extra) -> dict:
    """Juego NBA con todos los campos que esperan picks y el mensaje."""
    return {
        "sport": "nba", "date": date_str, "kickoff": "",
        "home": home, "home_id": None, "away": away, "away_id": None,
        "home_record": "", "away_record": "", "venue": "",
        "is_finals": False,  # solo ESPN dice si es la Final (series_summary)
        "series_summary": "", "event_id": "", "lines": {}, "source": source, **extra,
    }


API_SPORTS_NOT_STARTED = "NS"
API_SPORTS_TZ = "America/New_York"   # mismo día que ESPN (si no, UTC parte la noche)


def _api_sports_games(date_str: str) -> list[dict]:
    """Fuente 1: api-sports basketball, solo juegos sin empezar (status NS), por día ET."""
    data = api_sports_get(ABB_BASE, "games", {
        "league": NBA_LEAGUE_ID, "season": nba_season(), "date": date_str, "timezone": API_SPORTS_TZ,
    })
    games = []
    for g in data.get("response", []):
        if g.get("status", {}).get("short", "") != API_SPORTS_NOT_STARTED:
            continue
        home, away = g.get("teams", {}).get("home", {}), g.get("teams", {}).get("away", {})
        if home.get("id") and away.get("id"):
            games.append(_game(date_str, home.get("name", "?"), away.get("name", "?"), "api-sports",
                               kickoff=g.get("date", ""), home_id=home.get("id"), away_id=away.get("id")))
    return games


def _with_espn(games: list[dict], date_str: str) -> list[dict]:
    """
    Fuente 2: ESPN completa récords / sede / Finales / event_id / líneas DraftKings
    del mismo partido o agrega los que falten. Sin ESPN no hay líneas → sin picks.
    """
    result = list(games)
    for ev in espn_nba_games(date_str):
        try:
            parsed = espn_parse_nba(ev)
        except Exception as exc:   # un evento mal formado no tumba el slate
            event_id = ev.get("id") if isinstance(ev, dict) else "?"
            log.warning("ESPN NBA: evento %s ilegible (%s), se salta", event_id, exc)
            continue
        if not parsed:
            continue
        espn_fields = {
            "is_finals": parsed["is_finals"], "series_summary": parsed["series_summary"],
            "home_record": parsed.get("home_record", ""), "away_record": parsed.get("away_record", ""),
            "venue": parsed.get("venue", ""),
            "event_id": parsed.get("event_id", ""), "lines": parsed.get("lines", {}),
        }
        idx = next((i for i, g in enumerate(result) if _same_matchup(g, parsed)), None)
        if idx is None:
            result.append(_game(date_str, parsed["home"], parsed["away"], "espn",
                                kickoff=parsed["kickoff"], **espn_fields))
        else:
            current = result[idx]
            result[idx] = {**current, **espn_fields, "kickoff": current.get("kickoff") or parsed["kickoff"]}
    return result


def get_nba_games() -> list[dict]:
    """
    Juegos NBA del primer día (hoy … hoy + DAYS_AHEAD) con datos: api-sports + ESPN.
    Sin ESPN no hay líneas y sin líneas no hay picks, así que ya no se cae a BBC ni a
    Flashscore (solo inflaban el conteo de analizados y abrían Chromium).
    """
    today = now_et()   # el día de ESPN y de api-sports (timezone America/New_York)
    games: list[dict] = []
    for day_offset in range(DAYS_AHEAD + 1):
        date_str = (today + timedelta(days=day_offset)).strftime("%Y-%m-%d")
        games = _with_espn(_api_sports_games(date_str), date_str)
        if games:
            store.nba_slate_date = date_str
            break
    if not games:
        log.warning("NBA: ESPN y api-sports sin juegos programados en %d días", DAYS_AHEAD + 1)
    log.info("NBA games: %d (%d con líneas de mercado)", len(games), sum(1 for g in games if g.get("lines")))
    return games
