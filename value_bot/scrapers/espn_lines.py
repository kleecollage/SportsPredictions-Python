"""Líneas de mercado del scoreboard ESPN (DraftKings), comunes a NFL y NBA (puro)."""
import math
from collections.abc import Callable

from value_bot.analysis.market import american_to_decimal

PICK_EM_LINES = {"PK", "PICK", "EVEN", "EV"}
SNAPSHOTS = ("close", "open")   # más reciente primero
CLOSE = "close"                 # el overUnder del evento es la línea actual (= close)


def obj(data: object, key: str) -> dict:
    """data[key] como dict; {} si falta, es null o no es un objeto (ESPN manda nulls)."""
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def first_obj(items: object) -> dict:
    """Primer elemento de una lista ESPN como dict; {} si falta, es null o no es lista."""
    first = items[0] if isinstance(items, list) and items else None
    return first if isinstance(first, dict) else {}


def to_float(raw: object) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        raw = raw.get("value", raw.get("displayValue"))
    try:
        value = float(str(raw).strip().lstrip("ouOU").strip())
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def spread_line(raw: object) -> float | None:
    """Línea de spread; pick'em ('PK', 'EVEN') = 0.0."""
    if isinstance(raw, str) and raw.strip().upper() in PICK_EM_LINES:
        return 0.0
    return to_float(raw)


def _first_snapshot(side_a: dict, side_b: dict, build: Callable[[str, dict, dict], dict | None]) -> dict:
    """
    Mercado del primer snapshot (close, luego open) en que los dos lados traen valores
    utilizables. Los dos salen siempre del mismo momento: mezclar la cuota de uno con la
    del otro inventa una probabilidad sin vig que ningún mercado tuvo. `build` devuelve
    None si al snapshot le falta algo (se prueba el siguiente) y {} si es inconsistente
    (datos raros de ESPN → sin ese mercado).
    """
    for name in SNAPSHOTS:
        market = build(name, obj(side_a, name), obj(side_b, name))
        if market is not None:
            return market
    return {}


def _odds_pair(snap_a: dict, snap_b: dict) -> tuple[float, float] | None:
    odds_a, odds_b = american_to_decimal(snap_a.get("odds")), american_to_decimal(snap_b.get("odds"))
    return (odds_a, odds_b) if odds_a and odds_b else None


def _moneyline_at(_name: str, home: dict, away: dict) -> dict | None:
    odds = _odds_pair(home, away)
    return {"home_ml": odds[0], "away_ml": odds[1]} if odds else None


def _spread_at(_name: str, home: dict, away: dict) -> dict | None:
    """Spread del local; si el visitante trae línea debe ser la opuesta."""
    line, odds = spread_line(home.get("line")), _odds_pair(home, away)
    if line is None or not odds:
        return None
    if away.get("line") is not None and spread_line(away["line"]) != -line:
        return {}
    return {"home_spread": line, "home_spread_odds": odds[0], "away_spread_odds": odds[1]}


def _total_at(name: str, over: dict, under: dict, over_under: object) -> dict | None:
    """Línea del over; el overUnder del evento solo como línea de las cuotas de close."""
    line = to_float(over.get("line"))
    if line is None and name == CLOSE:
        line = to_float(over_under)
    odds = _odds_pair(over, under)
    if line is None or not odds:
        return None
    if under.get("line") is not None and to_float(under["line"]) != line:
        return {}
    return {"total": line, "over_odds": odds[0], "under_odds": odds[1]}


def parse_lines(comp: dict) -> dict:
    odds = first_obj(comp.get("odds") if isinstance(comp, dict) else None)
    if not odds:
        return {}
    ml, spread, total = obj(odds, "moneyline"), obj(odds, "pointSpread"), obj(odds, "total")
    market = {
        "provider": obj(odds, "provider").get("displayName", ""),
        **_first_snapshot(obj(ml, "home"), obj(ml, "away"), _moneyline_at),
        **_first_snapshot(obj(spread, "home"), obj(spread, "away"), _spread_at),
        **_first_snapshot(obj(total, "over"), obj(total, "under"),
                          lambda name, over, under: _total_at(name, over, under, odds.get("overUnder"))),
    }
    return {k: v for k, v in market.items() if v not in (None, "")}
