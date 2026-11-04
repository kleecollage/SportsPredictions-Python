import os
import logging
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


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
ESPN_NFL_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_NFL_SCHEDULE_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team_id}/schedule"
ESPN_NFL_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"

REPO_ROOT = Path(__file__).resolve().parent.parent   # raíz del repo: BD y caché de backtests

# ── Respaldo de picks_history.db ──────────────────────────────────
# La BD no está en git: cada corrida deja una copia en iCloud Drive (si existe) y se guardan
# las últimas DB_BACKUP_KEEP. DB_BACKUP_DIR en el entorno la redirige (los tests lo hacen).
_ICLOUD_DRIVE = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
DB_BACKUP_DIR = Path(os.getenv("DB_BACKUP_DIR") or _ICLOUD_DRIVE / "ValueBot")
DB_BACKUP_KEEP = 14

# ── Caché HTTP de los backtests ───────────────────────────────────
# Respuestas de ESPN que ya no cambian (juegos terminados), en .cache/ del repo (fuera de git).
# BACKTEST_CACHE_DIR en el entorno la redirige (los tests lo hacen); borrar la carpeta la vacía.
BACKTEST_CACHE_DIR = Path(os.getenv("BACKTEST_CACHE_DIR") or REPO_ROOT / ".cache" / "backtest_http")

# ── Sports toggles ────────────────────────────────────────────────
SPORTS_ENABLED = {
    "football": True,
    "nba":      True,
    "nfl":      True,
}

# run_date (YYYY-MM-DD) desde la que corre el modelo vigente de cada deporte.
# El rendimiento del mensaje solo cuenta picks desde esa fecha: los de modelos
# anteriores miden algo que ya no está en producción. Actualizar al recalibrar.
MODEL_SINCE = {
    "nfl": "2026-10-19",  # recalibración por backtest (peso 0.15, totales apagados)
    "nba": "2026-10-31",  # picks solo contra líneas ESPN, lado por EV (nba_model)
    "football": "2026-11-02",  # reactivado tras el refactor (Premier, LaLiga, Champions)
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
# Capa 4 fútbol (Flashscore): apagada. El selector de hora viene vacío (nov-2026) y sus partidos
# necesitan stats de api-football, que el plan Free no da para la temporada actual.
FLASHSCORE_ENABLED = False
PLAYWRIGHT_TIMEOUT = 30_000  # ms

# ── Temporadas ────────────────────────────────────────────────────
# Se calculan en cada llamada (no al importar): ningún import viejo ni proceso largo
# se queda con la temporada anterior.
NBA_SEASON_START_MONTH  = 10   # la NBA arranca en octubre
EURO_SEASON_START_MONTH = 7    # ligas europeas / Liga MX arrancan en julio-agosto
NBA_MIN_GAMES           = 5    # menos juegos = muestra no fiable → temporada anterior
NBA_API_TIMEOUT         = 10   # s por request a stats.nba.com (el default de nba_api es 30)
NFL_SEASON_START_MONTH  = 9    # la temporada regular NFL arranca en septiembre
NFL_MIN_GAMES           = 2    # el rating NFL ya regresa a la media; 2 juegos bastan


def nba_season_start_year(offset: int = 0) -> int:
    now = datetime.now()
    start = now.year if now.month >= NBA_SEASON_START_MONTH else now.year - 1
    return start + offset


def nba_season(offset: int = 0) -> str:
    """Formato api-sports basketball: '2026-2027'."""
    start = nba_season_start_year(offset)
    return f"{start}-{start + 1}"


def nba_api_season(offset: int = 0) -> str:
    """Formato nba_api: '2026-27'."""
    start = nba_season_start_year(offset)
    return f"{start}-{str(start + 1)[-2:]}"


def nfl_season(offset: int = 0) -> int:
    """Año de inicio de la temporada NFL (enero-febrero = playoffs del año anterior)."""
    now = datetime.now()
    start = now.year if now.month >= NFL_SEASON_START_MONTH else now.year - 1
    return start + offset


def afl_season(info: dict) -> int:
    """Temporada api-football de una liga: explícita si la fija, si no por fecha."""
    if info.get("season"):
        return info["season"]
    now = datetime.now()
    if info.get("calendar_year"):
        return now.year
    return now.year if now.month >= EURO_SEASON_START_MONTH else now.year - 1


# ── NBA API ───────────────────────────────────────────────────────
NBA_LEAGUE_ID  = "12"

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
    # max_fixtures: jornada completa (football-data trae todos los partidos y la tabla en 2 llamadas)
    "Premier League":   {"fd": "PL",  "afl": 39,  "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "odds": "soccer_epl", "max_fixtures": 10},
    "LaLiga":           {"fd": "PD",  "afl": 140, "flag": "🇪🇸", "odds": "soccer_spain_la_liga", "max_fixtures": 10},
    "Bundesliga":       {"fd": "BL1", "afl": 78,  "flag": "🇩🇪", "odds": "soccer_germany_bundesliga"},
    "Serie A":          {"fd": "SA",  "afl": 135, "flag": "🇮🇹", "odds": "soccer_italy_serie_a"},
    "Ligue 1":          {"fd": "FL1", "afl": 61,  "flag": "🇫🇷", "odds": "soccer_france_ligue_one"},
    "Champions League": {"fd": "CL",  "afl": 2,   "flag": "🏆", "odds": "soccer_uefa_champs_league", "max_fixtures": 18},
    # Sin "fd": el plan free de football-data.org no incluye la Europa League → solo api-football,
    # cuyo plan Free solo da temporadas 2022-2024: hoy no trae partidos (warning en el log)
    "Europa League":    {"afl": 3,   "flag": "🇪🇺", "odds": "soccer_uefa_europa_league"},
}

# season explícita solo para torneos puntuales; calendar_year = temporada = año natural
FALLBACK_LEAGUES_FOOTBALL = {
    "Mundial 2026":      {"afl": 1,   "season": 2026, "flag": "🌍", "odds": "soccer_fifa_world_cup"},
    "MLS":               {"afl": 253, "calendar_year": True, "flag": "🇺🇸", "odds": "soccer_usa_mls"},
    "Copa Libertadores": {"afl": 13,  "calendar_year": True, "flag": "🌎", "odds": None},
    "Copa Sudamericana": {"afl": 11,  "calendar_year": True, "flag": "🌎", "odds": None},
    "Brasileirao":       {"afl": 71,  "calendar_year": True, "flag": "🇧🇷", "odds": "soccer_brazil_campeonato"},
    "Liga Argentina":    {"afl": 128, "calendar_year": True, "flag": "🇦🇷", "odds": None},
    "Liga MX":           {"afl": 262, "flag": "🇲🇽", "odds": None},
}

# style: "attacking" | "defensive" | "technical" | "balanced"
LEAGUE_AVERAGES: dict = {
    "Premier League":    {"avg_goals": 2.82, "btts_pct": 55, "home_win_pct": 46, "over25_pct": 57, "under25_pct": 43, "draw_pct": 24, "style": "balanced"},
    "LaLiga":            {"avg_goals": 2.57, "btts_pct": 52, "home_win_pct": 46, "over25_pct": 51, "under25_pct": 49, "draw_pct": 27, "style": "technical"},
    "Bundesliga":        {"avg_goals": 3.18, "btts_pct": 61, "home_win_pct": 45, "over25_pct": 65, "under25_pct": 35, "draw_pct": 22, "style": "attacking"},
    "Serie A":           {"avg_goals": 2.48, "btts_pct": 49, "home_win_pct": 44, "over25_pct": 47, "under25_pct": 53, "draw_pct": 28, "style": "defensive"},
    "Ligue 1":           {"avg_goals": 2.68, "btts_pct": 52, "home_win_pct": 46, "over25_pct": 52, "under25_pct": 48, "draw_pct": 25, "style": "balanced"},
    "Champions League":  {"avg_goals": 2.91, "btts_pct": 57, "home_win_pct": 43, "over25_pct": 58, "under25_pct": 42, "draw_pct": 25, "style": "attacking"},
    # Aproximados (el modelo solo usa style y draw_pct)
    "Europa League":     {"avg_goals": 2.80, "btts_pct": 53, "home_win_pct": 45, "over25_pct": 54, "under25_pct": 46, "draw_pct": 24, "style": "balanced"},
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
# httpx loguea la URL completa en INFO, y la URL de Telegram incluye el bot token
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)


# ── Validación de entorno ─────────────────────────────────────────

_OPTIONAL_TOKENS = {
    "FOOTBALL_DATA_TOKEN": "football-data.org (Capa 1 fútbol)",
    "API_SPORTS_KEY":      "api-sports/api-football (Capa 2)",
    "THE_ODDS_API_KEY":    "The Odds API (value bets)",
}


def validate_env() -> None:
    """Lo llaman los entry points que envían (main, runner), no el import: los tests y
    backtests importan config sin Telegram."""
    required = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise EnvironmentError(f"Variables de entorno requeridas faltantes: {missing}")
    for var, desc in _OPTIONAL_TOKENS.items():
        if not os.getenv(var):
            log.warning("Token opcional no configurado: %s → %s desactivado", var, desc)


# ── Fuentes ───────────────────────────────────────────────────────

def fd_enabled() -> bool:
    return bool(FOOTBALL_DATA_TOKEN) and FOOTBALL_DATA_TOKEN != "YOUR_FOOTBALL_DATA_TOKEN"
