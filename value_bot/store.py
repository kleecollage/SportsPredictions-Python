"""Singleton DataStore — caches de los deportes que aún no pasaron a RunContext (NFL ya está allí)."""


class DataStore:
    def __init__(self):
        # Playwright Capa 4 (fútbol) — populado por run_playwright_scrapers()
        self.pw_football_fixtures: list = []

        # NBA API caches
        self.nba_league_cache: dict = {}
        self.nba_team_form_cache: dict = {}
        self.nba_splits_cache: dict = {}    # (team_name, location, season) → splits
        self.nba_api_down: bool = False     # tras un fallo de red no se insiste en la corrida
        self.nba_form_down: bool = False    # idem solo para la forma (dato de mensaje, no del modelo)
        self.nba_slate_date: str | None = None  # día ET de los juegos NBA analizados (próximos = después)

        # The Odds API cache
        self.odds_cache: dict = {}
        self.odds_credits_left: int | None = None   # x-requests-remaining de la última respuesta
        self.odds_unmatched: set = set()   # (liga, local, visitante) sin evento ya avisados

        # api-football: el plan no da acceso (p. ej. Free sin la temporada actual) → no insistir
        self.afl_blocked: bool = False

        # Juegos analizados por deporte en la corrida (para el mensaje sin picks)
        self.games_analyzed: dict = {}

        # Fútbol de la corrida, para el paso BTTS bajo demanda (main --btts)
        self.football_fixtures: list = []
        self.football_stats: dict = {}

    def clear_run_caches(self):
        """Limpiar al inicio de cada run para evitar datos stale entre ejecuciones."""
        self.nba_league_cache.clear()
        self.nba_team_form_cache.clear()
        self.nba_splits_cache.clear()
        self.nba_api_down = False
        self.nba_form_down = False
        self.nba_slate_date = None
        self.pw_football_fixtures = []
        self.games_analyzed = {}
        self.football_fixtures = []
        self.football_stats = {}
        self.afl_blocked = False
        # odds_cache se puede reutilizar dentro del mismo run pero no entre días
        self.odds_cache.clear()
        self.odds_credits_left = None
        self.odds_unmatched = set()


store = DataStore()
