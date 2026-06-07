"""Football fixture collection, stats building, and odds fetching."""
import logging
from datetime import datetime, timedelta

from value_bot.config import (
    FOOTBALL_DATA_TOKEN, AFL_BASE, DAYS_AHEAD, MIN_FIXTURES_TOP,
    THE_ODDS_API_KEY, ODDS_BASE,
    TOP_LEAGUES_FOOTBALL, FALLBACK_LEAGUES_FOOTBALL,
    fd_get, api_sports_get, http_get,
)
from value_bot.store import store

log = logging.getLogger(__name__)


# ── Odds ──────────────────────────────────────────────────────────

def get_real_odds(sport_key, home, away):
    if not THE_ODDS_API_KEY or not sport_key:
        return None
    if sport_key not in store.odds_cache:
        data = http_get(
            f"{ODDS_BASE}/sports/{sport_key}/odds/",
            params={"apiKey": THE_ODDS_API_KEY, "regions": "eu", "markets": "h2h"},
            skip_codes=(401, 404),
        )
        store.odds_cache[sport_key] = data if isinstance(data, list) else []
    hl, al = home.lower()[:7], away.lower()[:7]
    for g in store.odds_cache.get(sport_key, []):
        gh = (g.get("home_team") or "").lower()
        ga = (g.get("away_team") or "").lower()
        if hl in gh and al in ga:
            bookmakers = g.get("bookmakers", [])
            if not bookmakers:
                continue
            outcomes = bookmakers[0].get("markets", [{}])[0].get("outcomes", [])
            result = {}
            for o in outcomes:
                name  = (o.get("name") or "").lower()
                price = float(o.get("price", 0))
                if hl in name:
                    result["home_odds"] = price
                elif al in name:
                    result["away_odds"] = price
                elif "draw" in name:
                    result["draw_odds"] = price
            if len(result) >= 2:
                return result
    return None


# ── Fixtures ──────────────────────────────────────────────────────

def _parse_afl_fix(m, league, info, date):
    teams  = m.get("teams", {})
    status = m.get("fixture", {}).get("status", {}).get("short", "")
    if status in ("FT", "AET", "PEN", "CANC", "PST", "ABD"):
        return None
    home = teams.get("home", {})
    away = teams.get("away", {})
    if not home.get("id") or not away.get("id"):
        return None
    return {
        "sport": "football", "league": league, "flag": info["flag"],
        "fd_code": info.get("fd"), "afl_lid": info.get("afl"),
        "afl_season": info.get("afl_s") or info.get("season"),
        "odds_key": info.get("odds"),
        "date": date, "kickoff": m.get("fixture", {}).get("date", ""),
        "home": home.get("name", "?"), "home_id_afl": home.get("id"),
        "away": away.get("name", "?"), "away_id_afl": away.get("id"),
        "source": "api-football",
    }


def collect_football_fixtures():

    today     = datetime.now()
    date_from = today.strftime("%Y-%m-%d")
    date_to   = (today + timedelta(days=DAYS_AHEAD)).strftime("%Y-%m-%d")
    fixtures  = []

    for name, info in TOP_LEAGUES_FOOTBALL.items():
        found = False
        if FOOTBALL_DATA_TOKEN and FOOTBALL_DATA_TOKEN != "YOUR_FOOTBALL_DATA_TOKEN":
            data = fd_get(
                f"/competitions/{info['fd']}/matches",
                {"dateFrom": date_from, "dateTo": date_to, "status": "SCHEDULED"},
            )
            for m in data.get("matches", [])[:4]:
                h = m.get("homeTeam", {}); a = m.get("awayTeam", {})
                if h.get("id") and a.get("id"):
                    fixtures.append({
                        "sport": "football", "league": name, "flag": info["flag"],
                        "fd_code": info["fd"], "afl_lid": info["afl"],
                        "afl_season": info["afl_s"], "odds_key": info.get("odds"),
                        "date": (m.get("utcDate") or "")[:10],
                        "kickoff": m.get("utcDate", ""),
                        "home": h.get("name", "?"), "home_id_fd": h.get("id"),
                        "away": a.get("name", "?"), "away_id_fd": a.get("id"),
                        "source": "football-data",
                    })
                    found = True
        if not found:
            for d in range(DAYS_AHEAD + 1):
                ds = (today + timedelta(days=d)).strftime("%Y-%m-%d")
                raw = api_sports_get(AFL_BASE, "fixtures",
                                     {"league": info["afl"], "season": info["afl_s"], "date": ds})
                for m in raw.get("response", [])[:4]:
                    p = _parse_afl_fix(m, name, info, ds)
                    if p:
                        fixtures.append(p); found = True
                if found:
                    break

    if len(fixtures) < MIN_FIXTURES_TOP:
        log.info("Pocas ligas top (%d). Activando fallback...", len(fixtures))
        for name, info in FALLBACK_LEAGUES_FOOTBALL.items():
            found = False
            for d in range(DAYS_AHEAD + 1):
                if found:
                    break
                ds = (today + timedelta(days=d)).strftime("%Y-%m-%d")
                raw = api_sports_get(AFL_BASE, "fixtures",
                                     {"league": info["afl"], "season": info["season"], "date": ds})
                for m in raw.get("response", [])[:4]:
                    p = _parse_afl_fix(m, name, info, ds)
                    if p:
                        fixtures.append(p); found = True

    # Capa 4: Playwright Flashscore
    if len(fixtures) < MIN_FIXTURES_TOP and store.pw_football_fixtures:
        log.info("Football Capa 4: Playwright Flashscore (%d fixtures disponibles)",
                 len(store.pw_football_fixtures))
        existing_keys = {
            (f["home"].lower()[:6], f["away"].lower()[:6])
            for f in fixtures
        }
        added = 0
        for fix in store.pw_football_fixtures:
            key = (fix["home"].lower()[:6], fix["away"].lower()[:6])
            if key not in existing_keys:
                fixtures.append(fix)
                existing_keys.add(key)
                added += 1
        log.info("Playwright añadió %d fixtures de Flashscore", added)

    log.info("Football fixtures: %d", len(fixtures))
    return fixtures


# Cache nombre→id para no llamar a /teams?search= dos veces por el mismo equipo
_team_id_cache: dict = {}

# Mapa de nombre de liga → (afl_id, season) para Flashscore fixtures sin IDs
_LEAGUE_AFL_MAP: dict = {
    **{name: (info["afl"], info["afl_s"])   for name, info in TOP_LEAGUES_FOOTBALL.items()},
    **{name: (info["afl"], info["season"])  for name, info in FALLBACK_LEAGUES_FOOTBALL.items()},
}


def _resolve_team_id_by_name(team_name: str, league_id: int, season: int) -> int | None:
    """
    Busca el ID de un equipo en api-football usando su nombre.
    Primero intenta coincidencia exacta, luego por primera palabra del nombre.
    Resultados cacheados en _team_id_cache para evitar llamadas repetidas.
    """
    cache_key = (team_name, league_id, season)
    if cache_key in _team_id_cache:
        return _team_id_cache[cache_key]

    tl = team_name.lower()
    # Intentar dos términos de búsqueda: nombre completo y primera palabra
    for search_term in [team_name, team_name.split()[0]]:
        resp = api_sports_get(AFL_BASE, "teams", {
            "search": search_term,
            "league": league_id,
            "season": season,
        })
        teams = resp.get("response", [])
        if not teams:
            continue
        for t in teams:
            name = t.get("team", {}).get("name", "").lower()
            tid  = t.get("team", {}).get("id")
            if tid and (tl in name or name in tl or
                        any(w in name for w in tl.split() if len(w) > 3)):
                _team_id_cache[cache_key] = tid
                log.info("Resolved '%s' → team_id=%d (league=%d season=%d)",
                         team_name, tid, league_id, season)
                return tid

    _team_id_cache[cache_key] = None
    log.warning("No se pudo resolver team_id para '%s' (league=%d season=%d)",
                team_name, league_id, season)
    return None


def _fetch_team_stats(tid: int, lid: int, season: int) -> dict:
    """Llama a api-football /teams/statistics y parsea la respuesta."""
    resp = api_sports_get(AFL_BASE, "teams/statistics",
                          {"team": tid, "league": lid, "season": season}).get("response", {})
    if not resp:
        return {}
    pi     = resp.get("fixtures", {}).get("played", {})
    played = pi.get("total", 0) if isinstance(pi, dict) else int(pi or 0)
    if played < 3:
        return {}
    gf = resp.get("goals", {}).get("for",     {}).get("total", {}).get("total", 0)
    ga = resp.get("goals", {}).get("against", {}).get("total", {}).get("total", 0)
    w  = resp.get("fixtures", {}).get("wins",  {}).get("total", 0)
    d  = resp.get("fixtures", {}).get("draws", {}).get("total", 0)
    fs = resp.get("form", "") or ""
    rec = fs[-5:] if len(fs) >= 5 else fs
    fp  = sum(3 if c == "W" else (1 if c == "D" else 0) for c in rec)
    return {
        "played": played,
        "goals_for_pg":     round(gf / max(played, 1), 2),
        "goals_against_pg": round(ga / max(played, 1), 2),
        "win_rate":  round(w / max(played, 1) * 100, 1),
        "draw_rate": round(d / max(played, 1) * 100, 1),
        "form": rec, "form_pts": fp,
    }


def build_football_stats(fixtures):
    stats = {}

    # ── Paso 1: football-data.org standings (fixtures con fd_code) ────────────
    if FOOTBALL_DATA_TOKEN and FOOTBALL_DATA_TOKEN != "YOUR_FOOTBALL_DATA_TOKEN":
        for code in set(f["fd_code"] for f in fixtures if f.get("fd_code")):
            for section in fd_get(f"/competitions/{code}/standings").get("standings", []):
                if section.get("type") != "TOTAL":
                    continue
                for row in section.get("table", []):
                    name   = row.get("team", {}).get("name", "?")
                    played = row.get("playedGames", 0)
                    if played < 3:
                        continue
                    form_s = row.get("form", "") or ""
                    rec    = form_s[-5:] if len(form_s) >= 5 else form_s
                    fp     = sum(3 if c == "W" else (1 if c == "D" else 0) for c in rec)
                    stats[name] = {
                        "played": played,
                        "goals_for_pg":     round(row.get("goalsFor",     0) / max(played, 1), 2),
                        "goals_against_pg": round(row.get("goalsAgainst", 0) / max(played, 1), 2),
                        "win_rate":         round(row.get("won",  0) / max(played, 1) * 100, 1),
                        "draw_rate":        round(row.get("draw", 0) / max(played, 1) * 100, 1),
                        "form": rec, "form_pts": fp,
                    }

    # ── Paso 2: api-football con IDs conocidos (fixtures con afl_lid) ─────────
    missing_with_ids = set()
    for f in fixtures:
        for side in ("home", "away"):
            nm = f[side]
            if nm not in stats:
                missing_with_ids.add((
                    nm,
                    f.get(f"{side}_id_afl"),
                    f.get("afl_lid"),
                    f.get("afl_season"),
                ))

    for tname, tid, lid, season in missing_with_ids:
        if not (tid and lid and season):
            continue   # sin IDs → se resuelve en el Paso 3
        s = _fetch_team_stats(tid, lid, season)
        if s:
            stats[tname] = s

    # ── Paso 3: resolución por nombre para fixtures de Flashscore (sin IDs) ───
    # Flashscore fixtures tienen afl_lid=None pero sí tienen league name,
    # que mapeamos a (afl_id, season) via _LEAGUE_AFL_MAP para buscar en api-football
    still_missing = [
        (f[side], f.get("league", ""))
        for f in fixtures
        for side in ("home", "away")
        if f[side] not in stats and not f.get(f"{side}_id_afl")
    ]
    # Deduplicar (mismo equipo puede aparecer en varios fixtures)
    seen = set()
    for tname, league_name in still_missing:
        if tname in seen or tname in stats:
            continue
        seen.add(tname)

        league_meta = _LEAGUE_AFL_MAP.get(league_name)
        if not league_meta:
            log.debug("Sin mapeo AFL para liga '%s' (equipo: %s)", league_name, tname)
            continue
        lid, season = league_meta

        tid = _resolve_team_id_by_name(tname, lid, season)
        if not tid:
            continue

        s = _fetch_team_stats(tid, lid, season)
        if s:
            stats[tname] = s
            log.info("Stats resueltas por nombre: %s (liga %s)", tname, league_name)

    log.info("Football stats: %d equipos (%d fixtures)", len(stats), len(fixtures))
    return stats
