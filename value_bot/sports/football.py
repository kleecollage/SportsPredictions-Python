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


def build_football_stats(fixtures):
    stats = {}
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

    missing = set()
    for f in fixtures:
        for side in ("home", "away"):
            nm = f[side]
            if nm not in stats:
                missing.add((nm, f.get(f"{side}_id_afl"), f.get("afl_lid"), f.get("afl_season")))

    for tname, tid, lid, season in missing:
        if not (tid and lid and season):
            continue
        resp = api_sports_get(AFL_BASE, "teams/statistics",
                              {"team": tid, "league": lid, "season": season}).get("response", {})
        if not resp:
            continue
        pi     = resp.get("fixtures", {}).get("played", {})
        played = pi.get("total", 0) if isinstance(pi, dict) else int(pi or 0)
        if played < 3:
            continue
        gf = resp.get("goals", {}).get("for",     {}).get("total", {}).get("total", 0)
        ga = resp.get("goals", {}).get("against", {}).get("total", {}).get("total", 0)
        w  = resp.get("fixtures", {}).get("wins",  {}).get("total", 0)
        d  = resp.get("fixtures", {}).get("draws", {}).get("total", 0)
        fs = resp.get("form", "") or ""
        rec = fs[-5:] if len(fs) >= 5 else fs
        fp  = sum(3 if c == "W" else (1 if c == "D" else 0) for c in rec)
        stats[tname] = {
            "played": played,
            "goals_for_pg":     round(gf / max(played, 1), 2),
            "goals_against_pg": round(ga / max(played, 1), 2),
            "win_rate":  round(w / max(played, 1) * 100, 1),
            "draw_rate": round(d / max(played, 1) * 100, 1),
            "form": rec, "form_pts": fp,
        }

    log.info("Football stats: %d equipos", len(stats))
    return stats
