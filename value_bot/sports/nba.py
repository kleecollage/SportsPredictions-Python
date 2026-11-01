"""NBA data collection and statistics."""
import asyncio
import functools
import re
import time
import logging
from datetime import datetime, timedelta

import requests

from value_bot.config import (
    ABB_BASE, NBA_API_TIMEOUT, NBA_LEAGUE_ID, NBA_MIN_GAMES,
    DAYS_AHEAD, api_sports_get, nba_season, nba_api_season,
)
from value_bot.store import store
from value_bot.scrapers.espn import espn_nba_games, espn_parse_nba, scrape_bbc_sport_nba
from value_bot.scrapers.flashscore import scrape_flashscore_nba

try:
    from nba_api.stats.static import teams as nba_static_teams
    from nba_api.stats.endpoints import (
        leaguedashteamstats,
        teamgamelog,
        teamdashboardbygeneralsplits,
        leaguegamefinder,
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
    if not NBA_PPG_RANGE[0] <= ppg <= NBA_PPG_RANGE[1]:
        log.warning("nba_api: ppg %.1f fuera de rango (%s) — se descarta", ppg, row.get("TEAM_NAME", "?"))
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


def nba_get_team_form(team_name):
    """TeamGameLog — últimos juegos. Returns {form, ppg, win_rate, ...}"""
    if team_name in store.nba_team_form_cache:
        return store.nba_team_form_cache[team_name]
    tid = _nba_find_team_id(team_name)
    if NBA_API_OK and not tid:
        log.warning("nba_api: equipo no encontrado: '%s'", team_name)
        store.nba_team_form_cache[team_name] = {}
        return {}
    if not _nba_api_available():
        return {}
    for season, stype in _nba_stat_sources():
        if not _nba_api_available():
            break
        try:
            time.sleep(0.6)
            df = teamgamelog.TeamGameLog(
                team_id=tid, season=season, season_type_all_star=stype, timeout=NBA_API_TIMEOUT,
            ).get_data_frames()[0]
        except Exception as exc:
            _on_nba_api_error(exc, f"TeamGameLog {team_name} {season} {stype}")
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
    LeagueDash del propio equipo (prioridad de _nba_stat_sources) + forma reciente.
    Sin LeagueDash → {}: la forma sola no da puntos recibidos y no se inventan.
    """
    tid = _nba_find_team_id(team_name)
    if tid is None:
        return {}
    found = next(((season, stats[tid]) for season, stype in _nba_stat_sources()
                  if tid in (stats := nba_get_league_stats(season, stype))), None)
    if not found:
        return {}
    season, ls = found
    form = nba_get_team_form(team_name)
    return {
        "ppg":        ls["ppg"],
        "opp_ppg":    ls["opp_ppg"],
        "win_rate":   ls["win_pct"],
        "played":     ls["gp"],
        "pm":         ls["pm"],
        "form":       form.get("form", ""),
        "form_pts":   form.get("form_pts", 0),
        "recent_pts": form.get("recent_pts", []),
        "source":     "nba_api",
        "season":     season,
    }


def get_nba_team_stats_apisports(team_id):
    data = api_sports_get(ABB_BASE, "statistics", {
        "team": team_id, "league": NBA_LEAGUE_ID, "season": nba_season(),
    })
    resp = data.get("response", {})
    if not resp:
        return {}

    def safe_avg(f):
        if isinstance(f, dict):
            a = f.get("average", {})
            return float(a.get("all", 0) or 0) if isinstance(a, dict) else float(a or 0)
        return float(f or 0)

    def safe_tot(f):
        if isinstance(f, dict):
            t = f.get("all", {})
            return int(t.get("total", 0) or 0) if isinstance(t, dict) else int(t or 0)
        return int(f or 0)

    played_raw = resp.get("games", {}).get("played", {})
    played = played_raw.get("all", 0) if isinstance(played_raw, dict) else int(played_raw or 0)
    if played < 3:
        return {}

    pts_for     = safe_avg(resp.get("points", {}).get("for",     {}))
    pts_against = safe_avg(resp.get("points", {}).get("against", {}))
    wins        = safe_tot(resp.get("wins", {}))
    form_str    = resp.get("form", "") or ""
    recent      = form_str[-5:] if len(form_str) >= 5 else form_str
    form_pts    = sum(3 if c == "W" else 0 for c in recent)

    return {
        "ppg": pts_for, "opp_ppg": pts_against,
        "win_rate": round(wins / max(played, 1) * 100, 1),
        "played": played, "pm": round(pts_for - pts_against, 1),
        "form": recent, "form_pts": form_pts,
        "recent_pts": [], "source": "api-sports",
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


def nba_get_h2h_stats(home_team: str, away_team: str, n_games: int = 10) -> dict:
    """
    LeagueGameFinder → últimos N partidos entre estos dos equipos en Playoffs.
    Solo se llama cuando el partido es de Playoffs/Finals.

    Retorna:
        {
            total_games: int,
            avg_total:   float,   # promedio de puntos totales en esos partidos
            over_215_pct: float,  # % de partidos con total > 215
            over_220_pct: float,  # % de partidos con total > 220
            home_win_pct: float,  # % ganados por el equipo local en H2H
        }
    O {} si hay menos de 3 partidos H2H disponibles.
    """
    cache_key = (home_team, away_team)
    if cache_key in store.nba_h2h_cache:
        return store.nba_h2h_cache[cache_key]

    empty: dict = {}
    home_tid = _nba_find_team_id(home_team)
    away_tid = _nba_find_team_id(away_team)
    if not (_nba_api_available() and home_tid and away_tid):
        return empty

    try:
        time.sleep(0.8)
        # Buscar partidos del equipo local donde el oponente sea el equipo visitante
        finder = leaguegamefinder.LeagueGameFinder(
            team_id_nullable=home_tid,
            vs_team_id_nullable=away_tid,
            season_type_nullable="Playoffs",
            timeout=NBA_API_TIMEOUT,
        )
        df = finder.get_data_frames()[0]

        if df.empty or len(df) < 3:
            # Fallback: temporada regular si no hay suficientes Playoffs H2H
            time.sleep(0.8)
            finder_rs = leaguegamefinder.LeagueGameFinder(
                team_id_nullable=home_tid,
                vs_team_id_nullable=away_tid,
                season_type_nullable="Regular Season",
                timeout=NBA_API_TIMEOUT,
            )
            df = finder_rs.get_data_frames()[0]

        if df.empty or len(df) < 3:
            store.nba_h2h_cache[cache_key] = empty
            return empty

        df = df.head(n_games)

        # PTS = puntos del equipo local en ese partido
        # PLUS_MINUS = margen → opp_pts = PTS - PLUS_MINUS
        pts_home   = df["PTS"].astype(float)
        pts_away   = pts_home - df["PLUS_MINUS"].astype(float)
        totals     = pts_home + pts_away
        home_wins  = (df["WL"] == "W").sum()

        avg_total    = round(float(totals.mean()), 1)
        over_215_pct = round(float((totals > 215).mean() * 100), 1)
        over_220_pct = round(float((totals > 220).mean() * 100), 1)
        home_win_pct = round(float(home_wins / len(df) * 100), 1)

        result = {
            "total_games":  len(df),
            "avg_total":    avg_total,
            "over_215_pct": over_215_pct,
            "over_220_pct": over_220_pct,
            "home_win_pct": home_win_pct,
        }
        store.nba_h2h_cache[cache_key] = result
        log.info("H2H %s vs %s (%d partidos): avg_total=%.1f over215=%.0f%% home_wr=%.0f%%",
                 home_team, away_team, len(df), avg_total, over_215_pct, home_win_pct)
        return result

    except Exception as exc:
        _on_nba_api_error(exc, f"H2H {home_team} vs {away_team}")
        store.nba_h2h_cache[cache_key] = empty
        return empty


def get_best_nba_stats(game):
    """
    nba_api → api-sports → {}
    Retorna (h_stats, a_stats, splits, h2h) donde:
      splits = {"home": {ppg, opp_ppg, ...}, "away": {ppg, opp_ppg, ...}}
               o {} si no están disponibles
      h2h    = {avg_total, over_215_pct, over_220_pct, home_win_pct}
               o {} si el partido no es Playoffs o no hay datos
    """
    h_stats = nba_get_team_full_stats(game["home"])
    a_stats = nba_get_team_full_stats(game["away"])
    if not h_stats and game.get("home_id"):
        h_stats = get_nba_team_stats_apisports(game["home_id"])
    if not a_stats and game.get("away_id"):
        a_stats = get_nba_team_stats_apisports(game["away_id"])

    # Splits home/away: solo si nba_api devolvió stats base y son de su misma temporada
    # (al inicio de temporada los splits caen a la anterior: no deben pisar datos actuales)
    splits: dict = {}
    if h_stats and h_stats.get("source", "").startswith("nba_api"):
        h_split = nba_get_home_away_splits(game["home"], "home", season=h_stats.get("season"))
        a_split = nba_get_home_away_splits(game["away"], "road", season=(a_stats or {}).get("season"))
        if (h_split and a_split and h_split.get("season") == h_stats.get("season")
                and a_split.get("season") == a_stats.get("season")):
            splits = {"home": h_split, "away": a_split}

    # H2H: solo en Playoffs/Finals para no gastar requests en season regular
    is_playoffs = game.get("is_finals") or _is_nba_playoffs_month()
    h2h = nba_get_h2h_stats(game["home"], game["away"]) if is_playoffs else {}

    return h_stats, a_stats, splits, h2h


def _flashscore_nba_games() -> list:
    """Abre Chromium solo cuando ESPN, api-sports y BBC no dieron juegos (corre en un hilo sin loop)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # sin loop en este hilo: asyncio.run es seguro
    else:
        log.warning("Flashscore NBA: llamado dentro de un event loop activo → se omite")
        return []
    try:
        return asyncio.run(scrape_flashscore_nba())
    except Exception as exc:
        log.warning("Flashscore NBA: %s", exc)
        return []


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


def _api_sports_games(date_str: str) -> list[dict]:
    """Fuente 1: api-sports basketball, solo juegos sin empezar (status NS)."""
    data = api_sports_get(ABB_BASE, "games", {
        "league": NBA_LEAGUE_ID, "season": nba_season(), "date": date_str,
    })
    games = []
    for g in data.get("response", [])[:8]:
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
        parsed = espn_parse_nba(ev)
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


def _bbc_games(date_str: str) -> list[dict]:
    """Fuente 3: BBC Sport (sin hora)."""
    return [_game(date_str, g["home"], g["away"], "bbc_scrape") for g in scrape_bbc_sport_nba()]


_FLASHSCORE_TODAY_TIME = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def _flashscore_games(date_str: str) -> list[dict]:
    """
    Fuente 4: Flashscore vía Playwright. Solo items con hora de hoy (HH:MM): la página
    mezcla resultados y próximos días, y sin hora no se puede saber cuál es cuál.
    """
    items = store.pw_nba_games or _flashscore_nba_games()
    games = [_game(date_str, g["home"], g["away"], "flashscore_pw", kickoff=g["time"])
             for g in items if _FLASHSCORE_TODAY_TIME.match(str(g.get("time", "")).strip())]
    if len(games) < len(items):
        log.info("Flashscore NBA: %d de %d items sin hora de hoy → descartados",
                 len(items) - len(games), len(items))
    return games


def get_nba_games():
    """Juegos NBA del primer día (hoy … hoy + DAYS_AHEAD) con datos: api-sports + ESPN, luego BBC, luego Flashscore."""
    today = datetime.now()
    games: list[dict] = []
    for day_offset in range(DAYS_AHEAD + 1):
        date_str = (today + timedelta(days=day_offset)).strftime("%Y-%m-%d")
        games = _with_espn(_api_sports_games(date_str), date_str)
        if games:
            break

    today_str = today.strftime("%Y-%m-%d")
    if not games:
        log.info("NBA APIs sin resultado, BBC scraping...")
        games = _bbc_games(today_str)
    if not games:
        games = _flashscore_games(today_str)
        if games:
            log.info("NBA Fuente 4: Playwright Flashscore (%d partidos)", len(games))

    log.info("NBA games: %d", len(games))
    return games
