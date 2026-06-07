import os
import time
import logging
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    TENACITY_OK = True
except ImportError:
    TENACITY_OK = False

# ── Telegram ──────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── football-data.org ─────────────────────────────────────────────
FOOTBALL_DATA_TOKEN = os.getenv("FOOTBALL_DATA_TOKEN", "")
FD_BASE  = "https://api.football-data.org/v4"
FD_HEADS = {"X-Auth-Token": FOOTBALL_DATA_TOKEN}

# ── api-sports / api-football ─────────────────────────────────────
API_SPORTS_KEY = os.getenv("API_SPORTS_KEY", "")
AFL_BASE       = "https://v3.football.api-sports.io"
ABB_BASE       = "https://v1.basketball.api-sports.io"
SPORTS_HEADS   = {"x-apisports-key": API_SPORTS_KEY}

# ── The Odds API ──────────────────────────────────────────────────
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY", "")
ODDS_BASE        = "https://api.the-odds-api.com/v4"

# ── ESPN ──────────────────────────────────────────────────────────
ESPN_NBA_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"

# ── Sports toggles ────────────────────────────────────────────────
SPORTS_ENABLED = {
    "football": True,
    "nba":      True,
}

# ── Thresholds ────────────────────────────────────────────────────
DAYS_AHEAD       = 3
MAX_PICKS_SPORT  = 6
MIN_CONFIDENCE   = 62
MIN_CONF_PARLAY  = 62
VALUE_THRESHOLD  = 0.04
MIN_FIXTURES_TOP = 3

# ── Playwright ────────────────────────────────────────────────────
PLAYWRIGHT_ENABLED = True
PLAYWRIGHT_TIMEOUT = 30_000  # ms

# ── NBA API ───────────────────────────────────────────────────────
NBA_LEAGUE_ID  = "12"
NBA_SEASON     = "2025-2026"
NBA_API_SEASON = "2025-26"

# ── Stealth browser ───────────────────────────────────────────────
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

# ── Football leagues ──────────────────────────────────────────────
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

LEAGUE_AVG_DEFAULT = {
    "avg_goals": 2.5, "btts_pct": 48, "home_win_pct": 44,
    "over25_pct": 47, "under25_pct": 53, "draw_pct": 26, "style": "balanced",
}

# ── Logging ───────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)


# ── Validación de entorno ─────────────────────────────────────────

def validate_env():
    required = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise EnvironmentError(f"Variables de entorno requeridas faltantes: {missing}")

validate_env()

_optional_tokens = {
    "FOOTBALL_DATA_TOKEN": "football-data.org (Capa 1 fútbol)",
    "API_SPORTS_KEY":      "api-sports/api-football (Capa 2)",
    "THE_ODDS_API_KEY":    "The Odds API (value bets)",
}
for _var, _desc in _optional_tokens.items():
    if not os.getenv(_var):
        log.warning("Token opcional no configurado: %s → %s desactivado", _var, _desc)


# ── HTTP helpers ──────────────────────────────────────────────────

def _http_get_raw(url, headers, params, timeout):
    """Inner request que tenacity puede reintentar con backoff exponencial."""
    r = requests.get(url, headers=headers, params=params, timeout=timeout)
    if r.status_code == 429:
        raise requests.exceptions.RequestException(f"rate-limit 429: {url[:60]}")
    r.raise_for_status()
    return r.json()


if TENACITY_OK:
    _http_get_raw = retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        reraise=True,
    )(_http_get_raw)


def http_get(url, headers=None, params=None, skip_codes=(403, 404), timeout=12):
    h = headers or {}
    p = params or {}
    try:
        # Verificar códigos definitivos antes de reintentar
        probe = requests.get(url, headers=h, params=p, timeout=timeout)
        if probe.status_code in skip_codes:
            log.warning("HTTP %d (definitivo): %s", probe.status_code, url[:80])
            return {}
        if probe.status_code not in (200, 201):
            # Delegar el reintento a tenacity para 429 y 5xx
            return _http_get_raw(url, h, p, timeout)
        return probe.json()
    except requests.exceptions.RequestException as exc:
        log.warning("HTTP definitivamente falló [%s]: %s", url[:60], exc)
        return {}
    except Exception as exc:
        log.warning("HTTP error inesperado [%s]: %s", url[:60], exc)
        return {}


def api_sports_get(base, endpoint, params):
    return http_get(f"{base}/{endpoint}", headers=SPORTS_HEADS, params=params)


def fd_get(path, params=None):
    result = http_get(f"{FD_BASE}{path}", headers=FD_HEADS, params=params)
    if result:  # solo esperar si hubo respuesta real (respetar rate limit)
        time.sleep(7)
    return result


# ── Odds math helpers ─────────────────────────────────────────────

def est_odds(prob):
    return round((1 / max(prob, 0.01)) * 0.95, 2)


def edge(prob, real_odds):
    return round(prob * real_odds - 1, 4)
