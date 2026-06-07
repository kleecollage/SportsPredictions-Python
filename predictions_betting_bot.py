#!/usr/bin/env python3
"""
🏆 Value Bot Pro v4.5 — Multi-Deporte + Playwright Stealth
══════════════════════════════════════════════════════════════════
CAMBIOS v4.3 vs v4.2:
  ✅ Playwright stealth scraping  → Capa 4 (Flashscore NBA + Fútbol)
  ✅ Anti-detección avanzada       → User-agent rotation, navigator.webdriver=undefined
  ✅ Delays human-like             → random 0.8-2.5s entre acciones
  ✅ Interceptación de requests    → bloqueo de imagen/font/media (velocidad)
  ✅ Scroll infinito               → carga fixtures adicionales
  ✅ Fix nba_api per_mode_simple   → try/except TypeError (compatibility)

CAMBIOS v4.5 vs v4.4:
  ✅ FIX CRITICO import os + dotenv block (faltaba → NameError en os.getenv)
  ✅ Probabilidades Poisson reales  → Over/Under y BTTS matemáticamente correctos
  ✅ Under 2.5 Goles               → nuevo mercado para ligas defensivas
  ✅ Picks por estilo de liga       → attacking/defensive/technical/balanced
  ✅ Máximo 3 picks/partido         → calidad > cantidad, sin redundancias
  ✅ Razonamientos con XG reales    → "XG local 1.42 + XG visitante 1.18 = 2.60"
  ✅ 1X2 vía función logística      → más suave que lineal, range 55-76%
  ✅ Double Chance umbral 12-23pts  → más preciso que el anterior 10-22
  ✅ LEAGUE_AVERAGES ampliado       → under25_pct, draw_pct, style nuevos campos
  ✅ BTTS no contradice Under 2.5   → picks coherentes entre sí

CAMBIOS v4.4 vs v4.3:
  ✅ BUGFIX NBA Over Points       → normalizar totales→per-game (era "Over 3726")
  ✅ Sanity check totales NBA     → rango realista 180–275 pts
  ✅ LEAGUE_AVERAGES fútbol       → picks con contexto de liga, no genéricos
  ✅ Double Chance picks          → fútbol: 1X / X2 con ventaja moderada
  ✅ NBA Spread pick              → Hándicap Asiático estimado
  ✅ Múltiples picks/partido      → fútbol = same as NBA (todos los picks)
  ✅ Parlays same-sport           → fútbol primero, luego NBA, mixto last resort

CAMBIOS v4.2 vs v1.0:
  ✅ nba_api integrado  → stats reales Playoffs/Finals (PPG, forma, margin)
  ✅ 3-5 picks por partido  (Over/Under + Moneyline + 1ª Mitad + Serie)
  ✅ Parlays automáticos  → pool usa conf >= 62% (antes: 67%)
  ✅ Picks agrupados por partido  en el mensaje
  ✅ Stats block por equipo  (PPG, WR%, forma últimos 5 en emojis)
  ✅ Sección "Próximos Partidos"  aunque no haya picks hoy
  ✅ Análisis profundo Finals  (mínimo 3-4 picks por juego)
  ✅ DAYS_AHEAD: 2 → 3  |  MAX_PICKS_SPORT: 4 → 6

FUENTES NBA (en orden de prioridad):
  1. nba_api  (LeagueDashTeamStats + TeamGameLog Playoffs/Regular) ← NUEVO
  2. api-sports basketball  (fallback stats)
  3. ESPN Hidden API  (games + Finals context — free, sin key)
  4. BeautifulSoup BBC  (último recurso)

FUENTES FÚTBOL:
  1. football-data.org         (fixtures + standings)
  2. api-football / api-sports  (fallback + stats individuales)
  3. The Odds API               (cuotas reales — opcional)
  4. Fallback ligas activas     (Mundial 2026, MLS, Brasileirao…)
  5. Playwright Flashscore      (Capa 4 ← NUEVO v4.3)
"""

import os
import math

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv no instalado — usar variables de entorno del sistema

import requests
import asyncio
import logging
import time
import random
from datetime import datetime, timedelta
from telegram import Bot
from telegram.constants import ParseMode

try:
    from bs4 import BeautifulSoup
    BS4_OK = True
except ImportError:
    BS4_OK = False

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False

try:
    from nba_api.stats.static import teams as nba_static_teams
    from nba_api.stats.endpoints import leaguedashteamstats, teamgamelog
    NBA_API_OK = True
except ImportError:
    NBA_API_OK = False


# ══════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

FOOTBALL_DATA_TOKEN = os.getenv("FOOTBALL_DATA_TOKEN", "")
FD_BASE  = "https://api.football-data.org/v4"
FD_HEADS = {"X-Auth-Token": FOOTBALL_DATA_TOKEN}

API_SPORTS_KEY = os.getenv("API_SPORTS_KEY", "")
AFL_BASE = "https://v3.football.api-sports.io"
ABB_BASE = "https://v1.basketball.api-sports.io"
SPORTS_HEADS = {"x-apisports-key": API_SPORTS_KEY}

THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY", "")
ODDS_BASE = "https://api.the-odds-api.com/v4"

ESPN_NBA_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"

SPORTS_ENABLED = {
    "football": True,
    "nba":      True,
}

DAYS_AHEAD       = 3
MAX_PICKS_SPORT  = 6
MIN_CONFIDENCE   = 62
MIN_CONF_PARLAY  = 62
VALUE_THRESHOLD  = 0.04
MIN_FIXTURES_TOP = 3

# ── Playwright v4.3 ──
PLAYWRIGHT_ENABLED = True
PLAYWRIGHT_TIMEOUT = 30_000  # ms por página

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
]

STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'plugins',   {get: () => [1,2,3,4,5]});
    Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en','es']});
    Object.defineProperty(navigator, 'platform',  {get: () => 'MacIntel'});
    window.chrome = {runtime:{}, loadTimes:()=>{}, csi:()=>{}, app:{}};
"""

# Caches globales Playwright
_pw_nba_games: list = []
_pw_football_fixtures: list = []

NBA_LEAGUE_ID  = "12"
NBA_SEASON     = "2025-2026"
NBA_API_SEASON = "2025-26"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)

if not NBA_API_OK:
    log.warning("nba_api no disponible — usando api-sports + ESPN como fuente NBA")


# ══════════════════════════════════════════════════════════════════
#  COMPETICIONES DE FÚTBOL
# ══════════════════════════════════════════════════════════════════

TOP_LEAGUES_FOOTBALL = {
    "Premier League":   {"fd": "PL",  "afl": 39,  "afl_s": 2025, "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "odds": "soccer_epl"},
    "LaLiga":           {"fd": "PD",  "afl": 140, "afl_s": 2025, "flag": "🇪🇸", "odds": "soccer_spain_la_liga"},
    "Bundesliga":       {"fd": "BL1", "afl": 78,  "afl_s": 2025, "flag": "🇩🇪", "odds": "soccer_germany_bundesliga"},
    "Serie A":          {"fd": "SA",  "afl": 135, "afl_s": 2025, "flag": "🇮🇹", "odds": "soccer_italy_serie_a"},
    "Ligue 1":          {"fd": "FL1", "afl": 61,  "afl_s": 2025, "flag": "🇫🇷", "odds": "soccer_france_ligue_one"},
    "Champions League": {"fd": "CL",  "afl": 2,   "afl_s": 2025, "flag": "🏆", "odds": "soccer_uefa_champs_league"},
}

FALLBACK_LEAGUES_FOOTBALL = {
    "Mundial 2026":      {"afl": 1,   "season": 2026, "flag": "🌍", "odds": "soccer_fifa_world_cup"},
    "MLS":               {"afl": 253, "season": 2026, "flag": "🇺🇸", "odds": "soccer_usa_mls"},
    "Copa Libertadores": {"afl": 13,  "season": 2026, "flag": "🌎", "odds": None},
    "Copa Sudamericana": {"afl": 11,  "season": 2026, "flag": "🌎", "odds": None},
    "Brasileirao":       {"afl": 71,  "season": 2026, "flag": "🇧🇷", "odds": "soccer_brazil_campeonato"},
    "Liga Argentina":    {"afl": 128, "season": 2026, "flag": "🇦🇷", "odds": None},
    "Liga MX":           {"afl": 262, "season": 2025, "flag": "🇲🇽", "odds": None},
}

# Promedios de liga — v4.5 (+ under25_pct, draw_pct, style para picks inteligentes)
# Campos:  avg_goals  btts_pct  home_win_pct  over25_pct  under25_pct  draw_pct  style
# style: "attacking" | "defensive" | "technical" | "balanced"
LEAGUE_AVERAGES: dict = {
    "Premier League":    {"avg_goals": 2.82, "btts_pct": 55, "home_win_pct": 46, "over25_pct": 57, "under25_pct": 43, "draw_pct": 24, "style": "balanced"},
    "LaLiga":            {"avg_goals": 2.57, "btts_pct": 52, "home_win_pct": 46, "over25_pct": 51, "under25_pct": 49, "draw_pct": 27, "style": "technical"},
    "Bundesliga":        {"avg_goals": 3.18, "btts_pct": 61, "home_win_pct": 45, "over25_pct": 65, "under25_pct": 35, "draw_pct": 22, "style": "attacking"},
    "Serie A":           {"avg_goals": 2.48, "btts_pct": 49, "home_win_pct": 44, "over25_pct": 47, "under25_pct": 53, "draw_pct": 28, "style": "defensive"},
    "Ligue 1":           {"avg_goals": 2.68, "btts_pct": 52, "home_win_pct": 46, "over25_pct": 52, "under25_pct": 48, "draw_pct": 25, "style": "balanced"},
    "Champions League":  {"avg_goals": 2.91, "btts_pct": 57, "home_win_pct": 43, "over25_pct": 58, "under25_pct": 42, "draw_pct": 25, "style": "attacking"},
    "MLS":               {"avg_goals": 2.83, "btts_pct": 51, "home_win_pct": 46, "over25_pct": 55, "under25_pct": 45, "draw_pct": 23, "style": "attacking"},
    "Brasileirao":       {"avg_goals": 2.50, "btts_pct": 48, "home_win_pct": 48, "over25_pct": 47, "under25_pct": 53, "draw_pct": 25, "style": "defensive"},
    "Mundial 2026":      {"avg_goals": 2.55, "btts_pct": 50, "home_win_pct": 42, "over25_pct": 49, "under25_pct": 51, "draw_pct": 27, "style": "balanced"},
    "Copa Libertadores": {"avg_goals": 2.38, "btts_pct": 45, "home_win_pct": 45, "over25_pct": 42, "under25_pct": 58, "draw_pct": 26, "style": "defensive"},
    "Copa Sudamericana": {"avg_goals": 2.30, "btts_pct": 44, "home_win_pct": 45, "over25_pct": 39, "under25_pct": 61, "draw_pct": 27, "style": "defensive"},
    "Liga Argentina":    {"avg_goals": 2.48, "btts_pct": 48, "home_win_pct": 49, "over25_pct": 46, "under25_pct": 54, "draw_pct": 25, "style": "defensive"},
    "Liga MX":           {"avg_goals": 2.65, "btts_pct": 51, "home_win_pct": 47, "over25_pct": 52, "under25_pct": 48, "draw_pct": 24, "style": "balanced"},
}
_LEAGUE_AVG_DEFAULT = {
    "avg_goals": 2.5, "btts_pct": 48, "home_win_pct": 44,
    "over25_pct": 47, "under25_pct": 53, "draw_pct": 26, "style": "balanced",
}


# ══════════════════════════════════════════════════════════════════
#  HTTP GENÉRICO
# ══════════════════════════════════════════════════════════════════

def http_get(url, headers=None, params=None, skip_codes=(403, 404), timeout=12):
    for attempt in range(2):
        try:
            r = requests.get(url, headers=headers or {}, params=params or {}, timeout=timeout)
            if r.status_code in skip_codes:
                log.warning("HTTP %d (definitivo): %s", r.status_code, url[:80])
                return {}
            if r.status_code == 429:
                wait = 15 * (attempt + 1)
                log.warning("Rate limit 429. Esperando %ds...", wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError:
            return {}
        except Exception as exc:
            log.warning("HTTP error [%s]: %s", url[:60], exc)
            if attempt == 0:
                time.sleep(2)
    return {}


def api_sports_get(base, endpoint, params):
    return http_get(f"{base}/{endpoint}", headers=SPORTS_HEADS, params=params)


def fd_get(path, params=None):
    result = http_get(f"{FD_BASE}{path}", headers=FD_HEADS, params=params)
    time.sleep(7)
    return result


# ══════════════════════════════════════════════════════════════════
#  NBA_API — Estadísticas reales Playoffs / Finals
# ══════════════════════════════════════════════════════════════════

_nba_league_cache = {}
_nba_team_form_cache = {}


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
    """LeagueDashTeamStats con caché. Retorna {team_name: {ppg, opp_ppg, win_pct, gp, pm}}"""
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
            # Versión antigua de nba_api no soporta per_mode_simple
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
            gp  = int(row.get("GP", 0) or 0)
            if gp == 0:
                continue
            w       = int(row.get("W", 0) or 0)
            pts_raw = float(row.get("PTS", 0) or 0)
            pm_raw  = float(row.get("PLUS_MINUS", 0) or 0)
            # v4.4 FIX: si per_mode_simple falló → PTS es total de temporada → /GP
            # Detectar modo: per-game values son <500, totals son >500
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
    """TeamGameLog — últimos juegos (Playoffs → Regular). Retorna {form, ppg, win_rate, ...}"""
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
    """Combina LeagueDash + GameLog. Prioridad: Playoffs > Regular Season."""
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


# ══════════════════════════════════════════════════════════════════
#  ESPN HIDDEN API — Free, sin key
# ══════════════════════════════════════════════════════════════════

def espn_nba_games(date_str):
    data = http_get(
        ESPN_NBA_URL,
        params={"dates": date_str.replace("-", "")},
        skip_codes=(404,),
    )
    return data.get("events", [])


def espn_parse_nba(event):
    comps = event.get("competitions", [])
    if not comps:
        return None
    comp = comps[0]
    status = comp.get("status", {}).get("type", {}).get("name", "")
    if status in ("STATUS_FINAL", "STATUS_CANCELED", "STATUS_POSTPONED"):
        return None
    competitors = comp.get("competitors", [])
    if len(competitors) < 2:
        return None
    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

    def record(c):
        recs = c.get("records", [])
        return recs[0].get("summary", "?") if recs else "?"

    notes      = comp.get("notes", [])
    series_txt = notes[0].get("headline", "") if notes else ""
    is_finals  = "Finals" in series_txt or "final" in series_txt.lower()

    return {
        "home":           home.get("team", {}).get("displayName", "?"),
        "away":           away.get("team", {}).get("displayName", "?"),
        "home_record":    record(home),
        "away_record":    record(away),
        "kickoff":        event.get("date", ""),
        "is_finals":      is_finals,
        "series_summary": series_txt,
        "venue":          comp.get("venue", {}).get("fullName", ""),
    }


def get_upcoming_nba_events(days=5):
    """Próximos juegos NBA (1-5 días adelante) para sección Upcoming."""
    upcoming = []
    today = datetime.now()
    for d in range(1, days + 1):
        ds = (today + timedelta(days=d)).strftime("%Y-%m-%d")
        for ev in espn_nba_games(ds):
            p = espn_parse_nba(ev)
            if p:
                upcoming.append({**p, "date": ds})
        if upcoming:
            break
    return upcoming[:3]


# ══════════════════════════════════════════════════════════════════
#  SCRAPING FALLBACK — BeautifulSoup BBC Sport
# ══════════════════════════════════════════════════════════════════

def scrape_bbc_sport_nba():
    if not BS4_OK:
        return []
    try:
        r = requests.get(
            "https://www.bbc.com/sport/basketball/nba",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        games = []
        for fix in soup.find_all(attrs={"data-testid": "fixture"})[:6]:
            teams = fix.find_all(attrs={"data-testid": "team-name"})
            if len(teams) >= 2:
                games.append({
                    "home": teams[0].get_text(strip=True),
                    "away": teams[1].get_text(strip=True),
                    "kickoff": "",
                })
        log.info("BBC scrape NBA: %d", len(games))
        return games
    except Exception as exc:
        log.warning("BBC scrape error: %s", exc)
        return []


# ══════════════════════════════════════════════════════════════════
#  THE ODDS API — Cuotas reales (opcional)
# ══════════════════════════════════════════════════════════════════

_odds_cache = {}


def get_real_odds(sport_key, home, away):
    if not THE_ODDS_API_KEY or not sport_key:
        return None
    if sport_key not in _odds_cache:
        data = http_get(
            f"{ODDS_BASE}/sports/{sport_key}/odds/",
            params={"apiKey": THE_ODDS_API_KEY, "regions": "eu", "markets": "h2h"},
            skip_codes=(401, 404),
        )
        _odds_cache[sport_key] = data if isinstance(data, list) else []
    hl, al = home.lower()[:7], away.lower()[:7]
    for g in _odds_cache.get(sport_key, []):
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


def est_odds(prob):
    return round((1 / max(prob, 0.01)) * 0.95, 2)


def edge(prob, real_odds):
    return round(prob * real_odds - 1, 4)



# ══════════════════════════════════════════════════════════════════
#  PLAYWRIGHT v4.3 — Scraping Avanzado con Stealth (Capa 4)
# ══════════════════════════════════════════════════════════════════

async def _human_delay(min_ms: int = 800, max_ms: int = 2500) -> None:
    """Simula comportamiento humano con delay aleatorio."""
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def _build_stealth_browser(playwright):
    """Crea browser + context con anti-detección y bloqueo de recursos."""
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ],
    )
    ua = random.choice(USER_AGENTS)
    context = await browser.new_context(
        user_agent=ua,
        viewport={"width": random.randint(1280, 1920), "height": random.randint(800, 1080)},
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    await context.add_init_script(STEALTH_SCRIPT)

    # Interceptar y bloquear recursos pesados → +30% velocidad
    async def _block_heavy(route):
        if route.request.resource_type in ("image", "font", "media", "stylesheet"):
            await route.abort()
        else:
            await route.continue_()

    await context.route("**/*", _block_heavy)
    return browser, context


def _parse_flashscore_item(item) -> dict | None:
    """Parsea un <div class='event__match'> de Flashscore."""
    time_el = item.select_one(".event__time")
    home_el = (item.select_one(".event__participant--home") or
               item.select_one(".event__homeParticipant") or
               item.select_one("[class*='homeParticipant']"))
    away_el = (item.select_one(".event__participant--away") or
               item.select_one(".event__awayParticipant") or
               item.select_one("[class*='awayParticipant']"))
    if not (home_el and away_el):
        return None
    time_txt = time_el.get_text(strip=True) if time_el else ""
    # Omitir terminados / cancelados
    if any(x in time_txt.upper() for x in ("FT", "AET", "PEN", "CANC", "POSTP", "ABD", "WO")):
        return None
    home = home_el.get_text(strip=True)
    away = away_el.get_text(strip=True)
    if not home or not away or home == away:
        return None
    return {"home": home, "away": away, "time": time_txt}


async def _accept_cookies(page) -> None:
    """Acepta banner de cookies si aparece."""
    for sel in (
        "#onetrust-accept-btn-handler",
        "button[id*='accept']",
        ".cookieConsent__Button",
        "button.fc-cta-consent",
    ):
        try:
            await page.click(sel, timeout=2000)
            await _human_delay(400, 800)
            return
        except Exception:
            pass


async def _infinite_scroll(page, iters: int = 3) -> None:
    """Scroll progresivo para cargar contenido dinámico."""
    prev = 0
    for _ in range(iters):
        curr = await page.evaluate("document.body.scrollHeight")
        if curr == prev:
            break
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await _human_delay(900, 1800)
        prev = curr


async def scrape_flashscore_nba() -> list:
    """
    Capa 4 NBA: Flashscore via Playwright stealth.
    Returns [{home, away, time, source}]
    """
    if not (PLAYWRIGHT_OK and PLAYWRIGHT_ENABLED and BS4_OK):
        return []
    url = "https://www.flashscore.com/basketball/usa/nba/"
    games = []
    try:
        async with async_playwright() as p:
            browser, context = await _build_stealth_browser(p)
            page = await context.new_page()
            try:
                await page.goto(url, timeout=PLAYWRIGHT_TIMEOUT, wait_until="domcontentloaded")
                await _human_delay(2500, 5000)
                await _accept_cookies(page)
                await _infinite_scroll(page, iters=3)
                html = await page.content()
            finally:
                await page.close()
            await browser.close()

        soup = BeautifulSoup(html, "lxml")
        for item in soup.select(".event__match"):
            m = _parse_flashscore_item(item)
            if m:
                games.append({**m, "source": "flashscore_pw"})
        log.info("Flashscore NBA: %d partidos", len(games))
    except Exception as exc:
        log.warning("scrape_flashscore_nba: %s", exc)
    return games


async def scrape_flashscore_football() -> list:
    """
    Capa 4 Fútbol: Flashscore via Playwright stealth.
    Targets activos en Junio 2026: World Cup 2026, MLS.
    Returns lista de fixtures compatible con collect_football_fixtures().
    """
    if not (PLAYWRIGHT_OK and PLAYWRIGHT_ENABLED and BS4_OK):
        return []

    TARGETS = [
        ("Mundial 2026",  "🌍", "https://www.flashscore.com/football/world/world-cup-2026/"),
        ("MLS",           "🇺🇸", "https://www.flashscore.com/football/usa/mls/"),
        ("Brasileirao",   "🇧🇷", "https://www.flashscore.com/football/brazil/serie-a/"),
        ("Copa Libertadores", "🌎", "https://www.flashscore.com/football/south-america/copa-libertadores/"),
    ]

    all_fix = []
    today_str = datetime.now().strftime("%Y-%m-%d")

    try:
        async with async_playwright() as p:
            browser, context = await _build_stealth_browser(p)
            first = True
            for league_name, flag, url in TARGETS:
                page = await context.new_page()
                try:
                    await page.goto(url, timeout=PLAYWRIGHT_TIMEOUT, wait_until="domcontentloaded")
                    await _human_delay(1800, 4000)
                    if first:
                        await _accept_cookies(page)
                        first = False
                    for _ in range(2):
                        await page.evaluate("window.scrollBy(0, 1500)")
                        await _human_delay(700, 1300)
                    html = await page.content()
                    soup = BeautifulSoup(html, "lxml")
                    count = 0
                    for item in soup.select(".event__match"):
                        m = _parse_flashscore_item(item)
                        if not m:
                            continue
                        all_fix.append({
                            "sport": "football",
                            "league": league_name,
                            "flag": flag,
                            "fd_code": None,
                            "afl_lid": None,
                            "afl_season": 2026,
                            "odds_key": None,
                            "date": today_str,
                            "kickoff": "",
                            "home": m["home"],
                            "away": m["away"],
                            "home_id_fd": None,
                            "away_id_fd": None,
                            "home_id_afl": None,
                            "away_id_afl": None,
                            "source": "flashscore_pw",
                        })
                        count += 1
                    log.info("Flashscore %s: %d fixtures", league_name, count)
                except Exception as exc:
                    log.warning("Flashscore %s: %s", league_name, exc)
                finally:
                    await page.close()
                await _human_delay(800, 2000)  # Pausa humana entre páginas
            await browser.close()
    except Exception as exc:
        log.warning("scrape_flashscore_football: %s", exc)

    log.info("Flashscore Football total: %d fixtures", len(all_fix))
    return all_fix


async def scrape_espn_nba_pw() -> dict:
    """
    Respaldo ESPN via Playwright con interceptación XHR.
    Captura llamadas a site.api.espn.com automáticamente.
    Usar solo si ESPN HTTP directo falla.
    """
    if not (PLAYWRIGHT_OK and PLAYWRIGHT_ENABLED):
        return {}
    captured: dict = {}
    try:
        async with async_playwright() as p:
            browser, context = await _build_stealth_browser(p)
            page = await context.new_page()

            async def _capture(response):
                if ("site.api.espn.com" in response.url and
                        "nba" in response.url.lower()):
                    try:
                        data = await response.json()
                        if data.get("events"):
                            captured.update(data)
                    except Exception:
                        pass

            page.on("response", _capture)
            try:
                await page.goto(
                    "https://www.espn.com/nba/scoreboard",
                    timeout=PLAYWRIGHT_TIMEOUT,
                    wait_until="domcontentloaded",
                )
                await _human_delay(4000, 7000)
            finally:
                await page.close()
            await browser.close()
    except Exception as exc:
        log.warning("scrape_espn_nba_pw: %s", exc)

    if captured:
        log.info("ESPN NBA playwright: %d eventos interceptados",
                 len(captured.get("events", [])))
    return captured


async def run_playwright_scrapers() -> None:
    """
    v4.3: Ejecuta scrapers Playwright en paralelo, popula caches globales.
    Llamado desde main() antes de generar picks.
    """
    global _pw_nba_games, _pw_football_fixtures

    if not (PLAYWRIGHT_OK and PLAYWRIGHT_ENABLED):
        log.info("Playwright: no disponible/desactivado → Capa 4 saltada")
        return

    log.info("Playwright v4.3: iniciando scrapers en paralelo...")
    try:
        nba_res, football_res = await asyncio.gather(
            scrape_flashscore_nba(),
            scrape_flashscore_football(),
            return_exceptions=True,
        )

        if isinstance(nba_res, list):
            _pw_nba_games = nba_res
        else:
            log.warning("Playwright NBA falló: %s", nba_res)

        if isinstance(football_res, list):
            _pw_football_fixtures = football_res
        else:
            log.warning("Playwright Football falló: %s", football_res)

        log.info("Playwright completo → NBA:%d Fútbol:%d",
                 len(_pw_nba_games), len(_pw_football_fixtures))
    except Exception as exc:
        log.warning("run_playwright_scrapers: %s", exc)

# ══════════════════════════════════════════════════════════════════
#  MÓDULO NBA — Recopilación de juegos
# ══════════════════════════════════════════════════════════════════

def _is_nba_playoffs_month():
    return datetime.now().month in (4, 5, 6)


def get_nba_games():
    today = datetime.now()
    games = []

    for day_offset in range(DAYS_AHEAD + 1):
        date_str = (today + timedelta(days=day_offset)).strftime("%Y-%m-%d")

        # Fuente 1: api-sports basketball
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

        # Fuente 2: ESPN Hidden API
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

    # Fuente 3: BBC scraping
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

    # Fuente 4: Playwright Flashscore (v4.3 Capa 4)
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


# ══════════════════════════════════════════════════════════════════
#  MÓDULO NBA — Estadísticas api-sports (fallback)
# ══════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════
#  MÓDULO NBA — Análisis multi-pick v4.2
# ══════════════════════════════════════════════════════════════════

def _form_emoji(form):
    return "".join("🟢" if c == "W" else "🔴" for c in form)


def analyze_nba_game(game):
    """v4.2: Genera 3-5 picks por juego."""
    h_stats, a_stats = get_best_nba_stats(game)
    picks = []

    is_finals  = game.get("is_finals", False)
    series_txt = game.get("series_summary", "")
    h_rec      = game.get("home_record", "?")
    a_rec      = game.get("away_record", "?")
    ctx        = (f" [{series_txt}]" if series_txt
                  else (" [NBA Finals]" if is_finals else ""))

    # ── CON ESTADÍSTICAS ──────────────────────────────────────────
    if h_stats and a_stats:
        h_ppg = h_stats["ppg"];     a_ppg = a_stats["ppg"]
        h_opp = h_stats["opp_ppg"]; a_opp = a_stats["opp_ppg"]
        h_form = h_stats.get("form", ""); a_form = a_stats.get("form", "")
        h_fe   = _form_emoji(h_form) if h_form else "—"
        a_fe   = _form_emoji(a_form) if a_form else "—"

        projected = (h_ppg + a_opp + a_ppg + h_opp) / 2
        # v4.4: sanity check — línea NBA realista entre 180 y 275 pts
        if not (180.0 <= projected <= 275.0):
            log.warning("NBA línea fuera de rango: %.1f pts (h=%.1f a=%.1f gp=%d) → usando default",
                        projected, h_ppg, a_ppg, h_stats.get("gp", 0))
            projected = 218.5 if is_finals else 221.0
        game_line = round(projected, 1)

        # Pick 1: Total del partido
        if projected >= 218:
            prob = min(0.80, 0.54 + (projected - 210) * 0.008)
            picks.append({
                "type": f"Over {game_line} Puntos",
                "confidence": int(prob * 100),
                "our_prob": round(prob, 3),
                "est_odds": est_odds(prob),
                "value": None,
                "reasoning": (f"{game['home']}: {h_ppg:.0f}pts {h_fe} | "
                              f"{game['away']}: {a_ppg:.0f}pts {a_fe} | "
                              f"Proyección ~{projected:.0f}pts{ctx}"),
                "data_quality": "high",
            })
        elif projected <= 210:
            prob = min(0.76, 0.54 + (210 - projected) * 0.007)
            picks.append({
                "type": f"Under {game_line} Puntos",
                "confidence": int(prob * 100),
                "our_prob": round(prob, 3),
                "est_odds": est_odds(prob),
                "value": None,
                "reasoning": (f"Defensas dominantes. {game['home']} concede {h_opp:.0f}/j, "
                              f"{game['away']} concede {a_opp:.0f}/j → ~{projected:.0f}pts{ctx}"),
                "data_quality": "high",
            })
        else:
            direction = "Over" if projected >= 214 else "Under"
            picks.append({
                "type": f"{direction} {game_line} Puntos",
                "confidence": 63, "our_prob": 0.63,
                "est_odds": est_odds(0.63), "value": None,
                "reasoning": (f"Proyección equilibrada ~{projected:.0f}pts. "
                              f"{game['home']} {h_ppg:.0f}/j vs {game['away']} {a_ppg:.0f}/j{ctx}"),
                "data_quality": "medium",
            })

        # Pick 2: Moneyline
        h_str = h_stats["win_rate"] + h_stats.get("form_pts", 0) * 2 + 8
        a_str = a_stats["win_rate"] + a_stats.get("form_pts", 0) * 2
        diff  = h_str - a_str

        if abs(diff) >= 15:
            if diff > 0:
                fav, fav_wr, fav_pm, fav_fe_s = game["home"], h_stats["win_rate"], h_stats.get("pm", 0), h_fe
            else:
                fav, fav_wr, fav_pm, fav_fe_s = game["away"], a_stats["win_rate"], a_stats.get("pm", 0), a_fe
            prob = min(0.78, 0.52 + abs(diff) * 0.007)
            picks.append({
                "type": f"Victoria {fav}",
                "confidence": int(prob * 100),
                "our_prob": round(prob, 3),
                "est_odds": est_odds(prob),
                "value": None,
                "reasoning": (f"{fav}: {fav_wr:.0f}% victorias {fav_fe_s}, "
                              f"margin {fav_pm:+.1f}pts/j{ctx}"),
                "data_quality": "high",
            })
        elif is_finals:
            picks.append({
                "type": f"Victoria {game['home']} (Local)",
                "confidence": 64, "our_prob": 0.64,
                "est_odds": est_odds(0.64), "value": None,
                "reasoning": (f"Partido parejo — ventaja histórica de local en Finals. "
                              f"{game['home']} {h_fe}{ctx}"),
                "data_quality": "medium",
            })

        # Pick 3 (v4.4): Spread / Hándicap Asiático
        if abs(diff) >= 18:
            fav_s = game["home"] if diff > 0 else game["away"]
            spread = 6.5 if abs(diff) >= 28 else (5.0 if abs(diff) >= 22 else 3.5)
            sp_prob = min(0.70, 0.54 + abs(diff) * 0.006)
            picks.append({
                "type": f"Hándicap {fav_s} -{spread:.1f}",
                "confidence": int(sp_prob * 100),
                "our_prob": round(sp_prob, 3),
                "est_odds": 1.91,
                "value": None,
                "reasoning": (f"{fav_s} superior en {abs(diff):.0f}pts de fuerza estimada. "
                              f"Spread proyectado -{spread:.1f}pts{ctx}"),
                "data_quality": "medium",
            })

        # Pick 4 (v4.4): Team Total equipo local
        if h_ppg >= 112:
            tt_line = round(h_ppg * 0.95, 1)
            tt_prob = min(0.68, 0.54 + (h_ppg - 110) * 0.007)
            picks.append({
                "type": f"Total {game['home'].split()[-1]} Over {tt_line}",
                "confidence": int(tt_prob * 100),
                "our_prob": round(tt_prob, 3),
                "est_odds": est_odds(tt_prob),
                "value": None,
                "reasoning": (f"{game['home']} promedia {h_ppg:.0f}pts/j "
                              f"(forma {h_fe}). Team total estimado >{tt_line}{ctx}"),
                "data_quality": "medium",
            })

        # Pick 5: 1ª Mitad Total
        half_line = round(game_line * 0.49, 1)
        half_prob = 0.65 if projected >= 218 else 0.63
        picks.append({
            "type": f"1ª Mitad Over {half_line}",
            "confidence": int(half_prob * 100),
            "our_prob": half_prob,
            "est_odds": est_odds(half_prob),
            "value": None,
            "reasoning": (f"Ritmo alto de salida esperado en Playoffs. "
                          f"~{half_line:.0f}pts proyectados en 1ª mitad{ctx}"),
            "data_quality": "medium",
        })

        # Pick 4 (bonus Finals): Ganador de Serie
        if is_finals and series_txt:
            h_last = game["home"].split()[-1].lower()
            a_last = game["away"].split()[-1].lower()
            h_leads = h_last + " lead" in series_txt.lower()
            a_leads = a_last + " lead" in series_txt.lower()
            if h_leads or a_leads:
                leader       = game["home"] if h_leads else game["away"]
                leader_stats = h_stats if h_leads else a_stats
                picks.append({
                    "type": f"Ganador Serie: {leader}",
                    "confidence": 67, "our_prob": 0.67,
                    "est_odds": est_odds(0.67), "value": None,
                    "reasoning": (f"{leader} lidera la serie. "
                                  f"{leader_stats['win_rate']:.0f}% victorias en Playoffs{ctx}"),
                    "data_quality": "medium",
                })

    # ── SIN ESTADÍSTICAS ──────────────────────────────────────────
    else:
        base_line = 215.5

        picks.append({
            "type": f"Over {base_line} Puntos",
            "confidence": 63, "our_prob": 0.63,
            "est_odds": 1.87, "value": None,
            "reasoning": (f"{game['home']} ({h_rec}) vs {game['away']} ({a_rec}). "
                          f"Promedio NBA Playoffs ≥ 215pts{ctx}"),
            "data_quality": "low",
        })
        picks.append({
            "type": f"1ª Mitad Over {round(base_line * 0.49, 1)}",
            "confidence": 62, "our_prob": 0.62,
            "est_odds": 1.85, "value": None,
            "reasoning": (f"Ritmo elevado esperado en Playoffs. "
                          f"~{base_line * 0.49:.0f}pts en 1ª mitad{ctx}"),
            "data_quality": "low",
        })
        if is_finals:
            picks.append({
                "type": f"Victoria {game['home']}",
                "confidence": 63, "our_prob": 0.63,
                "est_odds": est_odds(0.63), "value": None,
                "reasoning": (f"Ventaja de local en NBA Finals histórica (~58%). "
                              f"{game['home']} ({h_rec}){ctx}"),
                "data_quality": "low",
            })

    picks = [p for p in picks if p["confidence"] >= MIN_CONFIDENCE]
    picks.sort(key=lambda x: x["confidence"], reverse=True)
    return picks


def get_nba_picks():
    """v4.2: retorna TODOS los picks válidos por partido."""
    games = get_nba_games()
    all_picks = []

    for game in games:
        game_picks = analyze_nba_game(game)
        for pd in game_picks:
            if pd["confidence"] >= MIN_CONFIDENCE:
                all_picks.append({
                    **game, **pd,
                    "sport":  "nba",
                    "flag":   "🏆" if game.get("is_finals") else "🏀",
                    "league": "NBA Finals" if game.get("is_finals") else "NBA",
                })

    all_picks.sort(
        key=lambda x: (1 if x.get("is_finals") else 0, x["confidence"]),
        reverse=True,
    )
    result = all_picks[:MAX_PICKS_SPORT]
    log.info("NBA picks: %d", len(result))
    return result


# ══════════════════════════════════════════════════════════════════
#  MÓDULO FÚTBOL
# ══════════════════════════════════════════════════════════════════

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

    # Capa 4 (v4.3): Playwright Flashscore — activar si pocas fixtures
    if len(fixtures) < MIN_FIXTURES_TOP and _pw_football_fixtures:
        log.info("Football Capa 4: Playwright Flashscore (%d fixtures disponibles)",
                 len(_pw_football_fixtures))
        existing_keys = {
            (f["home"].lower()[:6], f["away"].lower()[:6])
            for f in fixtures
        }
        added = 0
        for fix in _pw_football_fixtures:
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



# ── v4.5: Helpers probabilísticos Poisson ─────────────────────────────────────

def _poisson_p(lam: float, k: int) -> float:
    """P(X = k) para distribución de Poisson con parámetro lam."""
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _poisson_over(lam: float, threshold: int) -> float:
    """P(X > threshold) usando Poisson. Ej: _poisson_over(2.8, 2) = P(goles >= 3)."""
    p_under_eq = sum(_poisson_p(lam, k) for k in range(threshold + 1))
    return round(max(0.0, min(1.0, 1.0 - p_under_eq)), 4)


def _poisson_btts(exp_h: float, exp_a: float) -> float:
    """P(ambos equipos marcan >= 1 gol) vía Poisson independiente."""
    p_home_scores = 1.0 - math.exp(-max(exp_h, 0.05))
    p_away_scores = 1.0 - math.exp(-max(exp_a, 0.05))
    return round(p_home_scores * p_away_scores, 4)


def analyze_football_match(fix, hs, as_):
    """
    v4.5: Análisis de partido de fútbol con probabilidades Poisson reales.
    - Picks priorizados por estilo de liga (attacking / defensive / technical / balanced)
    - Over/Under 2.5 vía Poisson (P(goles>=3) y P(goles<=2))
    - BTTS vía Poisson independiente por equipo
    - Máximo 3 picks/partido, sin duplicados entre mercados similares
    - Reasoning específico con goles esperados y contexto de liga
    """
    league  = fix.get("league", "")
    la      = LEAGUE_AVERAGES.get(league, _LEAGUE_AVG_DEFAULT)
    style   = la.get("style", "balanced")
    lg_name = league or "Liga"

    # ── Sin stats individuales: picks por perfil de liga ──────────────────────
    if not hs or not as_:
        lam = la["avg_goals"]
        over25_p  = _poisson_over(lam, 2)
        under25_p = 1.0 - over25_p
        btts_base = la["btts_pct"] / 100.0
        league_picks = []

        if style == "attacking":
            # Bundesliga / MLS / UCL: Over 2.5 es pick principal
            if over25_p >= 0.50:
                league_picks.append({
                    "type": "Over 2.5 Goles",
                    "confidence": min(77, int(over25_p * 98)),
                    "our_prob": round(over25_p, 3),
                    "est_odds": est_odds(over25_p),
                    "value": None,
                    "reasoning": (f"{lg_name} ({style}): media {lam:.2f} goles/j — Poisson "
                                  f"estima {over25_p*100:.0f}% de partidos Over 2.5."),
                    "data_quality": "medium",
                })
            if btts_base >= 0.53 and la["btts_pct"] >= 54:
                prob_btts = min(0.74, btts_base + 0.01)
                league_picks.append({
                    "type": "Ambos Marcan (BTTS)",
                    "confidence": min(74, int(prob_btts * 100)),
                    "our_prob": round(prob_btts, 3),
                    "est_odds": est_odds(prob_btts),
                    "value": None,
                    "reasoning": (f"{lg_name}: {la['btts_pct']}% histórico de BTTS. "
                                  f"Liga ofensiva — ambos equipos suelen anotar."),
                    "data_quality": "medium",
                })

        elif style == "defensive":
            # Serie A / Copa Lib / Brasileirao: Under 2.5 o DC conservadora
            if under25_p >= 0.52:
                league_picks.append({
                    "type": "Under 2.5 Goles",
                    "confidence": min(74, int(under25_p * 98)),
                    "our_prob": round(under25_p, 3),
                    "est_odds": est_odds(under25_p),
                    "value": None,
                    "reasoning": (f"{lg_name} ({style}): media {lam:.2f} goles/j — Poisson "
                                  f"estima {under25_p*100:.0f}% de partidos Under 2.5."),
                    "data_quality": "medium",
                })
            if la["draw_pct"] >= 26:
                prob_x = min(0.66, la["draw_pct"] / 100.0 + 0.08)
                league_picks.append({
                    "type": "Empate (X)",
                    "confidence": min(66, int(prob_x * 100)),
                    "our_prob": round(prob_x, 3),
                    "est_odds": est_odds(prob_x),
                    "value": None,
                    "reasoning": (f"{lg_name}: {la['draw_pct']}% de partidos terminan en empate. "
                                  f"Liga táctica, resultado cerrado común."),
                    "data_quality": "medium",
                })

        elif style == "technical":
            # LaLiga: Over 2.5 si la media lo soporta, si no DC local
            if over25_p >= 0.50:
                league_picks.append({
                    "type": "Over 2.5 Goles",
                    "confidence": min(73, int(over25_p * 97)),
                    "our_prob": round(over25_p, 3),
                    "est_odds": est_odds(over25_p),
                    "value": None,
                    "reasoning": (f"{lg_name}: media {lam:.2f} goles/j, Over 2.5 en "
                                  f"{la['over25_pct']}% de partidos histórico."),
                    "data_quality": "medium",
                })
            prob_dc = min(0.68, la["home_win_pct"] / 100.0 + 0.05)
            league_picks.append({
                "type": "Doble Chance Local (1X)",
                "confidence": min(68, int(prob_dc * 100)),
                "our_prob": round(prob_dc, 3),
                "est_odds": est_odds(prob_dc * 0.72),
                "value": None,
                "reasoning": (f"{lg_name}: ventaja local histórica {la['home_win_pct']}%. "
                              f"1X cubre victoria y empate en partido parejo."),
                "data_quality": "medium",
            })

        else:  # balanced (PL, Ligue 1, MX, Mundial)
            if over25_p >= 0.49:
                league_picks.append({
                    "type": "Over 2.5 Goles",
                    "confidence": min(75, int(over25_p * 97)),
                    "our_prob": round(over25_p, 3),
                    "est_odds": est_odds(over25_p),
                    "value": None,
                    "reasoning": (f"{lg_name}: media {lam:.2f} goles/j — "
                                  f"{la['over25_pct']}% Over 2.5 histórico de liga."),
                    "data_quality": "medium",
                })
            if btts_base >= 0.51:
                prob_btts = min(0.72, btts_base + 0.01)
                league_picks.append({
                    "type": "Ambos Marcan (BTTS)",
                    "confidence": min(72, int(prob_btts * 100)),
                    "our_prob": round(prob_btts, 3),
                    "est_odds": est_odds(prob_btts),
                    "value": None,
                    "reasoning": (f"{lg_name}: {la['btts_pct']}% de partidos con BTTS. "
                                  f"Liga equilibrada — ambos equipos activos en ataque."),
                    "data_quality": "medium",
                })

        league_picks = [p for p in league_picks if p["confidence"] >= MIN_CONFIDENCE]
        if not league_picks:
            # Fallback universal: Over 1.5 (muy conservador, ~80% tasa histórica)
            league_picks.append({
                "type": "Over 1.5 Goles",
                "confidence": 63,
                "our_prob": 0.68,
                "est_odds": est_odds(0.68),
                "value": None,
                "reasoning": (f"{lg_name}: {lam:.2f} goles/j promedio — pick conservador "
                              f"sin stats individuales disponibles."),
                "data_quality": "low",
            })
        return league_picks[:3]

    # ── Con stats individuales: cálculos Poisson reales ───────────────────────
    gf_h = hs.get("goals_for_pg", 0)
    ga_h = hs.get("goals_against_pg", 0)
    gf_a = as_.get("goals_for_pg", 0)
    ga_a = as_.get("goals_against_pg", 0)

    # Expected goals (promedio entre ataque propio y defensa rival)
    exp_h   = (gf_h + ga_a) / 2.0   # goles esperados del equipo local
    exp_a   = (gf_a + ga_h) / 2.0   # goles esperados del visitante
    lam_tot = exp_h + exp_a          # total esperado del partido

    # Probabilidades Poisson
    over25_prob  = _poisson_over(lam_tot, 2)   # P(goles >= 3)
    under25_prob = 1.0 - over25_prob            # P(goles <= 2)
    btts_prob    = _poisson_btts(exp_h, exp_a)  # P(ambos marcan >= 1)

    # Fuerza relativa para 1X2 / Doble Chance
    hs_str = hs.get("win_rate", 33) + hs.get("form_pts", 7) * 2.5 + 10
    as_str = as_.get("win_rate", 33) + as_.get("form_pts", 7) * 2.5
    diff   = hs_str - as_str

    real   = get_real_odds(fix.get("odds_key", ""), fix["home"], fix["away"])
    picks  = []

    # ── Mercados de goles (Over / Under / BTTS) ───────────────────────────────
    # Prioridad según estilo de liga
    if style in ("attacking", "balanced", "technical"):
        # Intentar Over 2.5 primero
        if over25_prob >= 0.50:
            conf = min(80, int(over25_prob * 100 * 0.97))
            picks.append({
                "type": "Over 2.5 Goles",
                "confidence": conf,
                "our_prob": round(over25_prob, 3),
                "est_odds": est_odds(over25_prob),
                "value": None,
                "reasoning": (f"XG local {exp_h:.2f} + XG visitante {exp_a:.2f} = {lam_tot:.2f} esperados. "
                              f"Poisson P(≥3 goles)={over25_prob*100:.0f}%. "
                              f"{fix['home']} anota {gf_h:.1f}/j y concede {ga_h:.1f}/j."),
                "data_quality": "high",
            })
    else:  # defensive
        # Intentar Under 2.5 primero
        if under25_prob >= 0.52:
            conf = min(78, int(under25_prob * 100 * 0.97))
            picks.append({
                "type": "Under 2.5 Goles",
                "confidence": conf,
                "our_prob": round(under25_prob, 3),
                "est_odds": est_odds(under25_prob),
                "value": None,
                "reasoning": (f"XG local {exp_h:.2f} + XG visitante {exp_a:.2f} = {lam_tot:.2f} esperados. "
                              f"Poisson P(≤2 goles)={under25_prob*100:.0f}%. "
                              f"Liga defensiva: {fix['home']} concede {ga_h:.1f}/j, "
                              f"{fix['away']} {ga_a:.1f}/j."),
                "data_quality": "high",
            })
        # Añadir Over 2.5 en segundo plano si la probabilidad es razonable
        if over25_prob >= 0.52 and len(picks) == 0:
            conf = min(75, int(over25_prob * 100 * 0.95))
            picks.append({
                "type": "Over 2.5 Goles",
                "confidence": conf,
                "our_prob": round(over25_prob, 3),
                "est_odds": est_odds(over25_prob),
                "value": None,
                "reasoning": (f"XG={lam_tot:.2f}. Pese al estilo defensivo de {lg_name}, "
                              f"Poisson indica {over25_prob*100:.0f}% de probabilidad de Over 2.5."),
                "data_quality": "high",
            })

    # BTTS — solo si el mercado es significativo y no contradice el pick de goles
    if btts_prob >= 0.56:
        # No añadir BTTS si ya hay Under 2.5 (son contradictorios en partidos cerrados)
        has_under = any(p["type"] == "Under 2.5 Goles" for p in picks)
        if not has_under:
            conf = min(78, int(btts_prob * 100 * 0.96))
            picks.append({
                "type": "Ambos Marcan (BTTS)",
                "confidence": conf,
                "our_prob": round(btts_prob, 3),
                "est_odds": est_odds(btts_prob),
                "value": None,
                "reasoning": (f"P(local marca)={1-math.exp(-exp_h)*100:.0f}%, "
                              f"P(visitante marca)={1-math.exp(-exp_a)*100:.0f}%. "
                              f"{fix['home']} anota {gf_h:.1f}/j; {fix['away']} {gf_a:.1f}/j."),
                "data_quality": "high",
            })

    # ── 1X2 directo ───────────────────────────────────────────────────────────
    if diff >= 24:
        # Probabilidad via función logística (más suave que lineal)
        raw_p = 1.0 / (1.0 + math.exp(-diff / 28.0))
        prob  = round(0.50 + (raw_p - 0.50) * 0.72, 3)  # escala conservadora
        prob  = min(0.76, max(0.55, prob))
        p = {
            "type": f"Victoria {fix['home']}",
            "confidence": int(prob * 100),
            "our_prob": prob,
            "est_odds": est_odds(prob),
            "value": None,
            "reasoning": (f"{fix['home']}: {hs.get('win_rate', 0):.0f}% victorias, "
                          f"forma {hs.get('form', '?')} ({hs.get('form_pts', 0)} pts últimos 5). "
                          f"Ventaja sobre {fix['away']}: {diff:.0f} pts fuerza."),
            "data_quality": "high",
        }
        if real and real.get("home_odds"):
            p["est_odds"] = real["home_odds"]
            p["value"]    = edge(prob, real["home_odds"])
            p["real_odds_source"] = True
        picks.append(p)
    elif diff <= -24:
        raw_p = 1.0 / (1.0 + math.exp(-abs(diff) / 28.0))
        prob  = round(0.50 + (raw_p - 0.50) * 0.68, 3)
        prob  = min(0.74, max(0.54, prob))
        p = {
            "type": f"Victoria {fix['away']}",
            "confidence": int(prob * 100),
            "our_prob": prob,
            "est_odds": est_odds(prob),
            "value": None,
            "reasoning": (f"{fix['away']}: {as_.get('win_rate', 0):.0f}% victorias, "
                          f"forma {as_.get('form', '?')} ({as_.get('form_pts', 0)} pts). "
                          f"Ventaja visitante: {abs(diff):.0f} pts — inusual pero estadísticamente clara."),
            "data_quality": "high",
        }
        if real and real.get("away_odds"):
            p["est_odds"] = real["away_odds"]
            p["value"]    = edge(prob, real["away_odds"])
            p["real_odds_source"] = True
        picks.append(p)

    # ── Empate ────────────────────────────────────────────────────────────────
    # Activar si equipos parejos Y liga con tasa de empate alta (style=defensive/technical)
    if abs(diff) <= 10 and style in ("defensive", "technical", "balanced"):
        da = (hs.get("draw_rate", la.get("draw_pct", 25))
              + as_.get("draw_rate", la.get("draw_pct", 25))) / 2.0
        league_draw = la.get("draw_pct", 25)
        if da >= 23 or league_draw >= 26:
            base_draw = max(da, league_draw) / 100.0
            prob = round(min(0.62, base_draw + 0.06), 3)
            if prob >= 0.62:
                p = {
                    "type": "Empate (X)",
                    "confidence": int(prob * 100),
                    "our_prob": prob,
                    "est_odds": est_odds(prob),
                    "value": None,
                    "reasoning": (f"Equipos muy igualados (diff fuerza {abs(diff):.0f}). "
                                  f"Tasa empate: {fix['home']} {hs.get('draw_rate', 25):.0f}%, "
                                  f"{fix['away']} {as_.get('draw_rate', 25):.0f}%. "
                                  f"{lg_name} promedio {league_draw}% empates."),
                    "data_quality": "medium",
                }
                if real and real.get("draw_odds"):
                    p["est_odds"] = real["draw_odds"]
                    p["value"]    = edge(prob, real["draw_odds"])
                    p["real_odds_source"] = True
                picks.append(p)

    # ── Double Chance — ventaja moderada (12-23 pts diff) ─────────────────────
    if 12 <= abs(diff) < 24:
        if diff > 0:
            dc_type = f"Doble Chance {fix['home']} (1X)"
            raw_p   = 1.0 / (1.0 + math.exp(-diff / 22.0))
            dc_prob = round(min(0.78, 0.62 + (raw_p - 0.5) * 0.55), 3)
            dc_ctx  = (f"{fix['home']}: {hs.get('win_rate', 0):.0f}% victorias, "
                       f"forma {hs.get('form', '?')} ({hs.get('form_pts', 0)} pts). "
                       f"Ventaja local de {diff:.0f} pts → 1X más seguro que 1 directo.")
        else:
            dc_type = f"Doble Chance {fix['away']} (X2)"
            raw_p   = 1.0 / (1.0 + math.exp(-abs(diff) / 22.0))
            dc_prob = round(min(0.76, 0.60 + (raw_p - 0.5) * 0.52), 3)
            dc_ctx  = (f"{fix['away']}: {as_.get('win_rate', 0):.0f}% victorias, "
                       f"forma {as_.get('form', '?')} ({as_.get('form_pts', 0)} pts). "
                       f"Ventaja visitante {abs(diff):.0f} pts — X2 cubre victoria o empate.")
        picks.append({
            "type": dc_type,
            "confidence": int(dc_prob * 100),
            "our_prob": dc_prob,
            "est_odds": round(est_odds(dc_prob * 0.72), 2),
            "value": None,
            "reasoning": dc_ctx,
            "data_quality": "medium",
        })

    # ── Fallback: Over 1.5 si sin picks y promedio de goles mínimo ────────────
    if not picks and lam_tot >= 2.0:
        prob = round(min(0.75, 0.54 + (lam_tot - 2.0) * 0.08), 3)
        picks.append({
            "type": "Over 1.5 Goles",
            "confidence": int(prob * 100),
            "our_prob": prob,
            "est_odds": est_odds(prob),
            "value": None,
            "reasoning": (f"XG total esperado: {lam_tot:.2f} goles "
                          f"({fix['home']} {exp_h:.2f} + {fix['away']} {exp_a:.2f}). "
                          f"Pick conservador — {over25_prob*100:.0f}% no supera umbral Over 2.5."),
            "data_quality": "medium",
        })

    # Filtrar por confianza mínima, ordenar, limitar a 3
    picks = [p for p in picks if p["confidence"] >= MIN_CONFIDENCE]
    picks.sort(key=lambda x: (x["confidence"], x.get("value") or -99), reverse=True)
    return picks[:3]


def get_football_picks():
    """v4.4: múltiples picks por fixture (igual que NBA). Agrupados por partido."""
    fixtures = collect_football_fixtures()
    stats    = build_football_stats(fixtures)
    all_picks = []
    for fix in fixtures:
        analysis = analyze_football_match(fix, stats.get(fix["home"]), stats.get(fix["away"]))
        for pick in analysis:
            if pick["confidence"] >= MIN_CONFIDENCE:
                all_picks.append({**fix, **pick})
    all_picks.sort(
        key=lambda x: (
            1 if (x.get("value") and x["value"] >= VALUE_THRESHOLD) else 0,
            x["confidence"],
        ),
        reverse=True,
    )
    result = all_picks[:MAX_PICKS_SPORT]
    log.info("Football picks: %d (de %d fixtures)", len(result), len(fixtures))
    return result


# ══════════════════════════════════════════════════════════════════
#  PARLAY BUILDER v4.4 — Same-Sport Priority
# ══════════════════════════════════════════════════════════════════

def build_parlays(all_picks):
    """
    v4.4: Parlays inteligentes con preferencia same-sport.
    Prioridad: Fútbol > NBA > Mixto (solo si confianza muy alta).
    """
    pool    = [p for p in all_picks if p["confidence"] >= MIN_CONF_PARLAY]
    hi_pool = [p for p in all_picks if p["confidence"] >= 68]  # alta confianza
    if len(pool) < 2:
        log.info("Parlays: pool insuficiente (%d picks)", len(pool))
        return []

    parlays = []
    fp_pool  = [p for p in pool if p.get("sport") == "football"]
    nba_pool = [p for p in pool if p.get("sport") == "nba"]

    def combined_stats(legs):
        o = pr = 1.0
        for leg in legs:
            o  *= leg.get("est_odds", 1.80)
            pr *= leg.get("our_prob", 0.65)
        return round(o, 2), round(pr * 100, 1)

    def is_multi_sport(legs):
        return len(set(l.get("sport", "?") for l in legs)) > 1

    def best_pair(picks_list, same_sport_only=False, require_diff_game=True):
        """Encuentra la mejor combinación de 2 piernas."""
        best = None
        best_score = -1
        for i in range(len(picks_list)):
            for j in range(i + 1, len(picks_list)):
                l1, l2 = picks_list[i], picks_list[j]
                if same_sport_only and l1.get("sport") != l2.get("sport"):
                    continue
                same_game = (l1.get("home") == l2.get("home") and
                             l1.get("away") == l2.get("away"))
                if require_diff_game and same_game:
                    continue
                score = l1["confidence"] + l2["confidence"]
                if score > best_score:
                    best = [l1, l2]
                    best_score = score
        return best

    def best_triple(picks_list):
        """Encuentra la mejor combinación de 3 piernas mismo deporte."""
        best = None
        best_score = -1
        for i in range(len(picks_list)):
            for j in range(i + 1, len(picks_list)):
                for k in range(j + 1, len(picks_list)):
                    legs  = [picks_list[i], picks_list[j], picks_list[k]]
                    score = sum(l["confidence"] for l in legs)
                    if score > best_score:
                        best = legs
                        best_score = score
        return best

    # ── Parlay 2: mismo deporte primero ──────────────────────────────────────
    best_2 = (
        best_pair(fp_pool)                          # 1. Fútbol mismo deporte
        or best_pair(fp_pool, require_diff_game=False)
        or best_pair(nba_pool)                      # 2. NBA mismo deporte
        or best_pair(nba_pool, require_diff_game=False)
        or best_pair(hi_pool)                       # 3. Mixto, solo alta confianza
        or best_pair(pool)                          # 4. Cualquier combinación
        or (pool[:2] if len(pool) >= 2 else None)  # 5. Fallback
    )

    if best_2:
        co, cp = combined_stats(best_2)
        multi   = is_multi_sport(best_2)
        sport_tag = "" if not multi else " MIXTO"
        if best_2[0].get("sport") == "football":
            label = f"⚡ PARLAY DOBLE ⚽{sport_tag}"
        else:
            label = f"⚡ PARLAY DOBLE 🏀{sport_tag}"
        parlays.append({"label": label, "legs": best_2,
                        "combined_odds": co, "combined_prob": cp})

    # ── Parlay 3: mismo deporte, alta confianza ───────────────────────────────
    best_3 = (
        best_triple(fp_pool)        # Fútbol
        or best_triple(nba_pool)    # NBA
    )
    if best_3:
        co, cp = combined_stats(best_3)
        sport_em = "⚽" if best_3[0].get("sport") == "football" else "🏀"
        parlays.append({"label": f"🔥 PARLAY TRIPLE {sport_em}",
                        "legs": best_3,
                        "combined_odds": co, "combined_prob": cp})

    log.info("Parlays: %d (⚽pool:%d 🏀pool:%d)", len(parlays), len(fp_pool), len(nba_pool))
    return parlays


# ══════════════════════════════════════════════════════════════════
#  FORMATO TELEGRAM v4.4 — Picks agrupados por partido
# ══════════════════════════════════════════════════════════════════

DAYS_ES   = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MONTHS_ES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
             "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
SPORT_EMO = {"football": "⚽", "nba": "🏀"}


def bar(pct, n=8):
    f = round(pct / 100 * n)
    return "█" * f + "░" * (n - f)


def fmt_ko(iso):
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%d/%m %H:%M UTC")
    except Exception:
        return ""


def q_icon(dq):
    return {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(dq or "low", "⚪")


def conf_stars(conf):
    if conf >= 75:
        return "⭐⭐⭐"
    if conf >= 68:
        return "⭐⭐"
    return "⭐"


def vbadge(v):
    return f' 💎 <b>VALUE +{v:.0%}</b>' if v and v >= VALUE_THRESHOLD else ""


def _group_picks_by_game(picks):
    groups = {}
    for p in picks:
        key = (p.get("sport", "?"), p.get("home", "?"), p.get("away", "?"))
        if key not in groups:
            groups[key] = {"game_info": p, "picks": []}
        groups[key]["picks"].append(p)
    return list(groups.values())


def _game_block(group):
    gi     = group["game_info"]
    gpicks = group["picks"]
    lines  = []

    ko    = fmt_ko(gi.get("kickoff", ""))
    ko_s  = f"  📅 {ko}" if ko else ""
    venue = gi.get("venue", "")
    sport = gi.get("sport", "?")
    flag  = gi.get("flag", SPORT_EMO.get(sport, "🎯"))

    lines.append(f"<b>📋 {gi['home']} vs {gi['away']}</b>")
    lines.append(f"   {flag} {gi.get('league', '?')}{ko_s}")
    if venue:
        lines.append(f"   🏟 {venue}")

    # Stats block NBA
    if sport == "nba":
        h_rec = gi.get("home_record", "")
        a_rec = gi.get("away_record", "")
        h_form_data = _nba_team_form_cache.get(gi["home"], {})
        a_form_data = _nba_team_form_cache.get(gi["away"], {})
        if h_form_data or a_form_data:
            h_info = (f"{h_form_data.get('ppg','?')}pts "
                      f"{_form_emoji(h_form_data.get('form',''))}"
                      if h_form_data else (h_rec or "—"))
            a_info = (f"{a_form_data.get('ppg','?')}pts "
                      f"{_form_emoji(a_form_data.get('form',''))}"
                      if a_form_data else (a_rec or "—"))
            lines.append(f"   📊 <code>{gi['home'].split()[-1]}: {h_info} | "
                         f"{gi['away'].split()[-1]}: {a_info}</code>")
        elif h_rec or a_rec:
            lines.append(f"   📊 <code>{gi['home'].split()[-1]}: {h_rec or '—'} | "
                         f"{gi['away'].split()[-1]}: {a_rec or '—'}</code>")

    if gi.get("series_summary"):
        lines.append(f"   🏆 <i>{gi['series_summary']}</i>")

    lines.append("")

    for idx, p in enumerate(gpicks, 1):
        icon = "✅" if idx == 1 else "🎯"
        ol   = "Cuota real" if p.get("real_odds_source") else "Cuota est."
        lines.append(
            f"   {icon} {conf_stars(p['confidence'])} <b>{p['type']}</b>"
            f"  ·  {ol}: <code>{p.get('est_odds','?')}</code>{vbadge(p.get('value'))}"
        )
        lines.append(
            f"   📊 {bar(p['confidence'])} <b>{p['confidence']}%</b> {q_icon(p.get('data_quality','low'))}"
        )
        lines.append(f"   💡 <i>{p['reasoning']}</i>")
        if idx < len(gpicks):
            lines.append("")

    lines.append("─────────────────────")
    return lines


def build_message(fp, np, parlays, upcoming=None):
    now = datetime.now()
    ds  = f"{DAYS_ES[now.weekday()]}, {now.day} de {MONTHS_ES[now.month-1]} de {now.year}"

    lines = [
        "🏆 <b>VALUE BOT PRO v4.4</b>",
        f"📅 {ds}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    if not fp and not np:
        lines += [
            "⚠️ <b>Sin partidos disponibles hoy</b>",
            "",
            "No se encontraron fixtures en las próximas 72h.",
            "<i>El bot verificará automáticamente mañana.</i>",
        ]
    else:
        total = len(fp) + len(np)
        lines.append(f"🎯 <b>{total} picks generados hoy</b>")
        lines.append("")

        # NBA
        if np:
            finals_picks  = [p for p in np if p.get("is_finals")]
            regular_picks = [p for p in np if not p.get("is_finals")]

            if finals_picks:
                lines += [
                    "━━━━━━━━━━━━━━━━━━━━━━",
                    "🏆 <b>NBA FINALS 2026</b>",
                    "━━━━━━━━━━━━━━━━━━━━━━",
                    "",
                ]
                for group in _group_picks_by_game(finals_picks):
                    lines.extend(_game_block(group))
                lines.append("")

            if regular_picks:
                groups = _group_picks_by_game(regular_picks)
                lines += [
                    "━━━━━━━━━━━━━━━━━━━━━━",
                    f"🏀 <b>NBA</b> ({len(groups)} juego{'s' if len(groups)>1 else ''}  ·  {len(regular_picks)} picks)",
                    "━━━━━━━━━━━━━━━━━━━━━━",
                    "",
                ]
                for g in groups:
                    lines.extend(_game_block(g))
                lines.append("")

        # Fútbol
        if fp:
            vcount = sum(1 for p in fp if p.get("value") and p["value"] >= VALUE_THRESHOLD)
            vsub   = f"  ·  {vcount} 💎 value{'s' if vcount>1 else ''}" if vcount else ""
            groups = _group_picks_by_game(fp)
            lines += [
                "━━━━━━━━━━━━━━━━━━━━━━",
                f"⚽ <b>FÚTBOL</b> ({len(groups)} partido{'s' if len(groups)>1 else ''}  ·  {len(fp)} picks{vsub})",
                "━━━━━━━━━━━━━━━━━━━━━━",
                "",
            ]
            for g in groups:
                lines.extend(_game_block(g))
            lines.append("")

        # Parlays
        if parlays:
            lines += [
                "━━━━━━━━━━━━━━━━━━━━━━",
                "🎰 <b>PARLAYS RECOMENDADOS</b>",
                "━━━━━━━━━━━━━━━━━━━━━━",
                "",
            ]
            for parlay in parlays:
                lines.append(f"<b>{parlay['label']}</b>")
                lines.append(
                    f"   Cuota combinada: <code>{parlay['combined_odds']}x</code>  ·  "
                    f"Prob. conjunta: <b>~{parlay['combined_prob']:.1f}%</b>"
                )
                for leg in parlay["legs"]:
                    em = SPORT_EMO.get(leg.get("sport", ""), "🎯")
                    lines.append(
                        f"   {em} {leg['home']} vs {leg['away']} "
                        f"→ <b>{leg['type']}</b> ({leg['confidence']}%)"
                    )
                lines.append("")

    # Próximos partidos
    if upcoming:
        lines += [
            "━━━━━━━━━━━━━━━━━━━━━━",
            "📅 <b>PRÓXIMOS PARTIDOS</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "",
        ]
        for u in upcoming:
            ko   = fmt_ko(u.get("kickoff", ""))
            ko_s = f"  ·  {ko}" if ko else f"  ·  {u.get('date','')}"
            icon = "🏆" if u.get("is_finals") else "🏀"
            series = f" <i>({u['series_summary']})</i>" if u.get("series_summary") else ""
            lines.append(f"   {icon} <b>{u['home']} vs {u['away']}</b>{series}")
            lines.append(f"   {ko_s}")
            lines.append("")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "⚠️ <i>Solo análisis informativo. Juega con responsabilidad.</i>",
        "🤖 <i>Value Bot Pro v4.4  ·  Multi-Deporte + Playwright</i>",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════════════

async def send_telegram(msg):
    bot = Bot(token=BOT_TOKEN)
    for i in range(0, len(msg), 4000):
        await bot.send_message(
            chat_id=CHAT_ID, text=msg[i:i+4000], parse_mode=ParseMode.HTML
        )
        if i + 4000 < len(msg):
            await asyncio.sleep(0.5)


# ══════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════

async def main():
    log.info("═══ Value Bot Pro v4.4 · Multi-Deporte + Playwright ═══")
    log.info("Deportes activos: %s", [k for k, v in SPORTS_ENABLED.items() if v])
    log.info("nba_api disponible: %s | playwright: %s", NBA_API_OK, PLAYWRIGHT_OK)

    # v4.3: Playwright scrapers (Capa 4) — correr antes de picks
    await run_playwright_scrapers()

    football_picks = get_football_picks() if SPORTS_ENABLED.get("football") else []
    nba_picks      = get_nba_picks()      if SPORTS_ENABLED.get("nba")      else []

    all_picks = football_picks + nba_picks
    parlays   = build_parlays(all_picks)
    upcoming  = get_upcoming_nba_events() if SPORTS_ENABLED.get("nba") else []
    message   = build_message(football_picks, nba_picks, parlays, upcoming)

    await send_telegram(message)
    log.info("═══ ✓ Enviado | ⚽%d 🏀%d | Parlays:%d ═══",
             len(football_picks), len(nba_picks), len(parlays))


if __name__ == "__main__":
    asyncio.run(main())
