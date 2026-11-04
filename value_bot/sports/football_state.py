"""Estado de fútbol de una corrida (parte de RunContext): nunca se reutiliza entre corridas."""
import threading
from dataclasses import dataclass, field


@dataclass
class OddsCredits:
    """Último x-requests-remaining de The Odds API (None = desconocido). Caja mutable dentro del frozen."""
    left: int | None = None


@dataclass(frozen=True)
class FootballRun:
    # api-football: el plan no da acceso (p. ej. Free sin la temporada actual) → no insistir en la corrida
    afl_blocked: threading.Event = field(default_factory=threading.Event)
    team_ids: dict = field(default_factory=dict)              # (nombre, liga, temporada) → id api-football
    flashscore_fixtures: list = field(default_factory=list)   # Capa 4 (apagada por FLASHSCORE_ENABLED)
    # Fixtures y stats de get_football_picks (los reemplaza en cada llamada), para BTTS bajo demanda
    fixtures: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    # The Odds API: una llamada por liga y corrida
    odds: dict = field(default_factory=dict)                  # sport_key → eventos con cuotas
    odds_unmatched: set = field(default_factory=set)          # (liga, local, visitante) ya avisados
    credits: OddsCredits = field(default_factory=OddsCredits)
