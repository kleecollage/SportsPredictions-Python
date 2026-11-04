"""
Estado de una corrida, creado por main() y pasado explícitamente a cada deporte: lo que vive
aquí no sobrevive a la corrida ni hay que limpiarlo a mano. Cada deporte guarda lo suyo en su
<deporte>_state.py; ningún módulo tiene estado global entre corridas.
"""
from dataclasses import dataclass, field

from value_bot.sports.football_state import FootballRun
from value_bot.sports.nba_state import NbaRun
from value_bot.sports.nfl_state import NflRun


@dataclass(frozen=True)   # se mutan sus dicts/listas, nunca se reemplazan los campos
class RunContext:
    football: FootballRun = field(default_factory=FootballRun)
    nba: NbaRun = field(default_factory=NbaRun)
    nfl: NflRun = field(default_factory=NflRun)
    games_analyzed: dict[str, int] = field(default_factory=dict)   # deporte → juegos analizados
