"""Estado NBA de una corrida (parte de RunContext): nunca se reutiliza entre corridas."""
import threading
from dataclasses import dataclass, field


@dataclass(frozen=True)
class NbaRun:
    league: dict = field(default_factory=dict)    # (season, season_type) → {team_id: línea por juego}
    form: dict = field(default_factory=dict)      # equipo → forma reciente (solo para Telegram)
    splits: dict = field(default_factory=dict)    # (equipo, home|road, season) → split
    # Tras un fallo de red no se insiste en la corrida (Event: bandera mutable en un dataclass frozen)
    api_down: threading.Event = field(default_factory=threading.Event)
    form_down: threading.Event = field(default_factory=threading.Event)   # solo corta la forma
    slate_dates: list[str] = field(default_factory=list)   # días ET de los juegos analizados
