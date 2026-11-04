"""
Contrato del pick que sale de cada deporte: los campos que Telegram, save_picks, parlays y
el resolver leen. Un pick sigue siendo un dict ({**juego, **pick}); esto fija qué no puede faltar.
"""
import logging
from typing import TypedDict

log = logging.getLogger(__name__)


class Pick(TypedDict, total=False):
    sport: str               # clave de sports_registry ("nba", "nfl", "football")
    league: str
    home: str
    away: str
    type: str                # texto del pick, el mismo que liquida el resolver
    confidence: int          # P(ganar | no push) en %
    est_odds: float          # cuota decimal real del mercado
    our_prob: float          # confidence / 100 sin redondear
    value: float             # EV con push
    reasoning: str
    data_quality: str        # "high" | "medium" | "low"
    real_odds_source: bool   # siempre True: sin cuota real no hay pick
    # Opcionales según deporte
    market: str              # moneyline | spread | total (NFL/NBA)
    side: str
    event_id: str | None     # ESPN o "fd:<id>"
    kickoff: str
    date: str
    push_prob: float
    flag: str
    lines: dict


REQUIRED_FIELDS = ("sport", "league", "home", "away", "type", "confidence", "est_odds", "our_prob",
                   "value", "reasoning", "data_quality", "real_odds_source")


def missing_fields(pick: object) -> tuple[str, ...]:
    """Campos obligatorios ausentes o None; real_odds_source además tiene que ser verdadero."""
    if not isinstance(pick, dict):
        return REQUIRED_FIELDS
    missing = tuple(f for f in REQUIRED_FIELDS if pick.get(f) is None)
    if "real_odds_source" not in missing and not pick["real_odds_source"]:
        missing += ("real_odds_source",)
    return missing


def keep_valid(picks: list, sport: str) -> list[Pick]:
    """Los picks que cumplen el contrato; los demás se loguean (error de código) y no salen."""
    valid = []
    for pick in picks:
        if missing := missing_fields(pick):
            log.error("Pick %s descartado (%s) por campos faltantes: %s",
                      sport, pick.get("type", "?") if isinstance(pick, dict) else type(pick).__name__,
                      ", ".join(missing))
            continue
        valid.append(pick)
    return valid
