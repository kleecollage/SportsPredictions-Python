"""Caché NFL de una corrida (parte de RunContext): nunca se reutiliza entre corridas."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class NflRun:
    scores: dict = field(default_factory=dict)       # team_id → TeamScores (los fallos no se guardan)
    team_ids: list[str] = field(default_factory=list)  # IDs ESPN de los 32 equipos
