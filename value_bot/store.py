"""Singleton DataStore — encapsula todos los caches mutables del bot."""


class DataStore:
    def __init__(self):
        # Playwright Capa 4 — populados por run_playwright_scrapers()
        self.pw_nba_games: list = []
        self.pw_football_fixtures: list = []

        # NBA API caches
        self.nba_league_cache: dict = {}
        self.nba_team_form_cache: dict = {}

        # The Odds API cache
        self.odds_cache: dict = {}

    def clear_run_caches(self):
        """Limpiar al inicio de cada run para evitar datos stale entre ejecuciones."""
        self.nba_league_cache.clear()
        self.nba_team_form_cache.clear()
        self.pw_nba_games = []
        self.pw_football_fixtures = []
        # odds_cache se puede reutilizar dentro del mismo run pero no entre días
        self.odds_cache.clear()


store = DataStore()
