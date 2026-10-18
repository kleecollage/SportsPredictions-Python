"""Singleton DataStore — encapsula todos los caches mutables del bot."""


class DataStore:
    def __init__(self):
        # Playwright Capa 4 — populados por run_playwright_scrapers()
        self.pw_nba_games: list = []
        self.pw_football_fixtures: list = []

        # NBA API caches
        self.nba_league_cache: dict = {}
        self.nba_team_form_cache: dict = {}
        self.nba_splits_cache: dict = {}    # (team_name, location) → splits
        self.nba_h2h_cache: dict = {}       # (home, away) → h2h stats

        # NFL caches
        self.nfl_scores_cache: dict = {}    # team_id → [(pts_favor, pts_contra), ...]
        self.nfl_team_ids: list = []        # IDs ESPN de los 32 equipos

        # The Odds API cache
        self.odds_cache: dict = {}

    def clear_run_caches(self):
        """Limpiar al inicio de cada run para evitar datos stale entre ejecuciones."""
        self.nba_league_cache.clear()
        self.nba_team_form_cache.clear()
        self.nba_splits_cache.clear()
        self.nba_h2h_cache.clear()
        self.nfl_scores_cache.clear()
        self.nfl_team_ids = []
        self.pw_nba_games = []
        self.pw_football_fixtures = []
        # odds_cache se puede reutilizar dentro del mismo run pero no entre días
        self.odds_cache.clear()


store = DataStore()
