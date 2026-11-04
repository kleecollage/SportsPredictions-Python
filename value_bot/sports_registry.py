"""
Registro único de deportes: identidad y presentación de cada uno. Los toggles y las fechas de
modelo siguen en config (SPORTS_ENABLED, MODEL_SINCE) y las fuentes de picks en main.PICK_SOURCES;
tests/test_sports_registry.py exige que tengan exactamente estas claves.
"""
from dataclasses import dataclass

from value_bot.config import SPORTS_ENABLED


@dataclass(frozen=True)
class SportSpec:
    key: str          # valor de pick["sport"] y de la columna picks.sport
    emoji: str
    title: str        # encabezado de su sección en el mensaje
    noun: str         # "juego" | "partido" para los conteos
    parlay_ok: bool   # el resolver lo liquida con muestra → sus picks pueden ir en parlays


# Orden = orden de las secciones del mensaje
SPORTS = (
    SportSpec("nba", "🏀", "NBA", "juego", parlay_ok=True),
    SportSpec("football", "⚽", "FÚTBOL", "partido", parlay_ok=False),   # sin muestra resuelta aún
    SportSpec("nfl", "🏈", "NFL", "juego", parlay_ok=True),
)
SPORTS_BY_KEY = {s.key: s for s in SPORTS}


def sport_keys() -> list[str]:
    return [s.key for s in SPORTS]


def active_sports() -> list[str]:
    """Deportes encendidos en SPORTS_ENABLED, en el orden del registro."""
    return [s.key for s in SPORTS if SPORTS_ENABLED.get(s.key)]
