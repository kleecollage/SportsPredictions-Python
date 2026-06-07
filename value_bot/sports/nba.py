"""NBA data collection and statistics."""
import time
import logging
from datetime import datetime, timedelta

from value_bot.config import (
    ABB_BASE, NBA_LEAGUE_ID, NBA_SEASON, NBA_API_SEASON,
    DAYS_AHEAD, api_sports_get,
)
from value_bot.scrapers.espn import espn_nba_games, espn_parse_nba, scrape_bbc_sport_nba

try:
    from nba_api.stats.static import teams as nba_static_teams
    from nba_api.stats.endpoints import leaguedashteamstats, teamgamelog
    NBA_API_OK = True
except ImportError:
    NBA_API_OK = False

log = logging.getLogger(__name__)

if not NBA_API_OK:
    log.warning("nba_api no disponible — usando api-sports + ESPN como fuente NBA")

_nba_league_cache: dict = {}
_nba_team_form_cache: dict = {}


def _is_nba_playoffs_month():
    return datetime.now().month in (4, 5, 6)


def _nba_find_team_id(name):
    if not NBA_API_OK:
        return None
    nl = name.lower()
    all_teams = nba_static_teams.get_teams()
    for t in all_teams:
        if t["full_name"].lower() == nl or t["nickname"].lower() == nl:
            return t["id"]
    for t in all_teams:
        fn = t["full_name"].lower()
        nn = t["nickname"].lower()
        ci = t["city"].lower()
        if nl in fn or nn in nl or ci in nl or any(w in nl for w in fn.split()):
            return t["id"]
    return None


def nba_get_league_stats(season_type="Playoffs"):
    """LeagueDashTeamStats with cache. Returns {team_name: {ppg, opp_ppg, win_pct, gp, pm}}"""
    if season_type in _nba_league_cache:
        return _nba_league_cache[season_type]
    if not NBA_API_OK:
        return {}
    try:
        time.sleep(0.8)
        try:
            ls = leaguedashteamstats.LeagueDashTeamStats(
                season=NBA_API_SEASON,
                season_type_all_star=season_type,
                per_mode_simple="PerGame",
            )
        except TypeError:
            ls = leaguedashteamstats.LeagueDashTeamStats(
                season=NBA_API_SEASON,
                season_type_all_star=season_type,
            )
        df = ls.get_data_frames()[0]
        if df.empty:
            _nba_league_cache[season_type] = {}
            return {}
        result = {}
        for _, row in df.iterrows():
            gp = int(row.get("GP", 0) or 0)
            if gp == 0:
                continue
            w       = int(row.get("W", 0) or 0)
            pts_raw = float(row.get("PTS", 0) or 0)
            pm_raw  = float(row.get("PLUS_MINUS", 0) or 0)
            if pts_raw > 500:
                pts = round(pts_raw / gp, 1)
                pm  = round(pm_raw / gp, 1)
            else:
                pts = pts_raw
                pm  = pm_raw
            result[row["TEAM_NAME"]] = {
                "ppg":     pts,
                "opp_ppg": round(pts - pm, 1),
                "win_pct": round(w / max(gp, 1) * 100, 1),
                "gp":      gp,
                "pm":      pm,
            }
        _nba_league_cache[season_type] = result
        log.info("nba_api %s stats: %d equipos", season_type, len(result))
        return result
    except Exception as e:
        log.warning("nba_api LeagueDash (%s): %s", season_type, e)
        _nba_league_cache[season_type] = {}
        return {}


def nba_get_team_form(team_name):
    """TeamGameLog — últimos juegos. Returns {form, ppg, win_rate, ...}"""
    if team_name in _nba_team_form_cache:
        return _nba_team_form_cache[team_name]
    if not NBA_API_OK:
        _nba_team_form_cache[team_name] = {}
        return {}
    tid = _nba_find_team_id(team_name)
    if not tid:
        log.warning("nba_api: equipo no encontrado: '%s'", team_name)
        _nba_team_form_cache[team_name] = {}
        return {}
    for stype in ("Playoffs", "Regular Season"):
        try:
            time.sleep(0.6)
            tgl = teamgamelog.TeamGameLog(
                team_id=tid, season=NBA_API_SEASON, season_type_all_star=stype
            )
            df = tgl.get_data_frames()[0]
            if df.empty or len(df) < 2:
                continue
            r5   = df.head(5)
            form = "".join(r5["WL"].tolist())
            res  = {
                "form":       form,
                "form_pts":   sum(3 if c == "W" else 0 for c in form),
                "ppg":        round(float(df.head(10)["PTS"].mean()), 1),
                "recent_pts": [int(x) for x in r5["PTS"].tolist()],
                "win_rate":   round(float((df["WL"] == "W").mean() * 100), 1),
                "played":     len(df),
                "stype":      stype,
            }
            _nba_team_form_cache[team_name] = res
            return res
        except Exception as e:
            log.warning("nba_api TeamGameLog (%s, %s): %s", team_name, stype, e)
    _nba_team_form_cache[team_name] = {}
    return {}


def nba_get_team_full_stats(team_name):
    """Combines LeagueDash + GameLog. Priority: Playoffs > Regular Season."""
    ls = {}
    for stype in ("Playoffs", "Regular Season"):
        candidates = nba_get_league_stats(stype)
        if not candidates:
            continue
        ls = candidates.get(team_name, {})
        if not ls:
            tl = team_name.lower()
            for k, v in candidates.items():
                if tl in k.lower() or k.lower() in tl or any(w in tl for w in k.lower().split()):
                    ls = v
                    break
        if ls:
            break

    form = nba_get_team_form(team_name)

    if ls:
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
        }
    elif form:
        return {
            "ppg":        form["ppg"],
            "opp_ppg":    115.0,
            "win_rate":   form["win_rate"],
            "played":     form["played"],
            "pm":         0.0,
            "form":       form["form"],
            "form_pts":   form["form_pts"],
            "recent_pts": form.get("recent_pts", []),
            "source":     "nba_api_partial",
        }
    return {}


def get_nba_team_stats_apisports(team_id):
    data = api_sports_get(ABB_BASE, "statistics", {
        "team": team_id, "league": NBA_LEAGUE_ID, "season": NBA_SEASON,
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


def get_best_nba_stats(game):
    """nba_api → api-sports → {}"""
    h_stats = nba_get_team_full_stats(game["home"])
    a_stats = nba_get_team_full_stats(game["away"])
    if not h_stats and game.get("home_id"):
        h_stats = get_nba_team_stats_apisports(game["home_id"])
    if not a_stats and game.get("away_id"):
        a_stats = get_nba_team_stats_apisports(game["away_id"])
    return h_stats, a_stats


def get_nba_games():
    """Collect NBA games for today + DAYS_AHEAD from api-sports + ESPN + BBC + Playwright."""
    from value_bot.scrapers.flashscore import _pw_nba_games

    today = datetime.now()
    games = []

    for day_offset in range(DAYS_AHEAD + 1):
        date_str = (today + timedelta(days=day_offset)).strftime("%Y-%m-%d")

        # Source 1: api-sports basketball
        data = api_sports_get(ABB_BASE, "games", {
            "league": NBA_LEAGUE_ID, "season": NBA_SEASON, "date": date_str,
        })
        for g in data.get("response", [])[:8]:
            teams  = g.get("teams", {})
            status = g.get("status", {}).get("short", "")
            if status in ("FT", "AOT", "CANC"):
                continue
            home = teams.get("home", {})
            away = teams.get("away", {})
            if home.get("id") and away.get("id"):
                games.append({
                    "sport": "nba", "date": date_str,
                    "kickoff": g.get("date", ""),
                    "home": home.get("name", "?"), "home_id": home.get("id"),
                    "away": away.get("name", "?"), "away_id": away.get("id"),
                    "is_finals": _is_nba_playoffs_month(),
                    "series_summary": "", "home_record": "", "away_record": "",
                    "venue": "", "source": "api-sports",
                })

        # Source 2: ESPN Hidden API
        for ev in espn_nba_games(date_str):
            parsed = espn_parse_nba(ev)
            if not parsed:
                continue
            existing = next(
                (g for g in games
                 if parsed["home"][:5].lower() in g["home"].lower() or
                    parsed["away"][:5].lower() in g["away"].lower()),
                None,
            )
            if existing:
                existing["is_finals"]      = parsed["is_finals"]
                existing["series_summary"] = parsed["series_summary"]
                existing["home_record"]    = parsed.get("home_record", "")
                existing["away_record"]    = parsed.get("away_record", "")
                existing["venue"]          = parsed.get("venue", "")
                if not existing.get("kickoff"):
                    existing["kickoff"] = parsed["kickoff"]
            else:
                games.append({
                    "sport": "nba", "date": date_str,
                    "kickoff": parsed["kickoff"],
                    "home": parsed["home"], "home_id": None,
                    "away": parsed["away"], "away_id": None,
                    "home_record": parsed["home_record"],
                    "away_record": parsed["away_record"],
                    "is_finals": parsed["is_finals"],
                    "series_summary": parsed["series_summary"],
                    "venue": parsed.get("venue", ""),
                    "source": "espn",
                })

        if games:
            break

    # Source 3: BBC scraping
    if not games:
        log.info("NBA APIs sin resultado, BBC scraping...")
        for g in scrape_bbc_sport_nba():
            games.append({
                "sport": "nba", "date": today.strftime("%Y-%m-%d"),
                "kickoff": "", "home": g["home"], "home_id": None,
                "away": g["away"], "away_id": None,
                "home_record": "", "away_record": "",
                "is_finals": _is_nba_playoffs_month(),
                "series_summary": "", "venue": "", "source": "bbc_scrape",
            })

    # Source 4: Playwright Flashscore (Capa 4)
    if not games and _pw_nba_games:
        log.info("NBA Fuente 4: Playwright Flashscore (%d partidos)", len(_pw_nba_games))
        today_str = today.strftime("%Y-%m-%d")
        for g in _pw_nba_games:
            games.append({
                "sport": "nba",
                "date": today_str,
                "kickoff": g.get("time", ""),
                "home": g["home"],
                "home_id": None,
                "away": g["away"],
                "away_id": None,
                "home_record": "",
                "away_record": "",
                "is_finals": _is_nba_playoffs_month(),
                "series_summary": "",
                "venue": "",
                "source": "flashscore_pw",
            })

    log.info("NBA games: %d", len(games))
    return games
