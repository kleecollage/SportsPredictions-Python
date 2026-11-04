"""Singleton DataStore — caches de los deportes que aún no pasaron a RunContext (NFL y NBA ya están allí)."""


class DataStore:
    def __init__(self):
        # Playwright Capa 4 (fútbol) — populado por run_playwright_scrapers()
        self.pw_football_fixtures: list = []

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
