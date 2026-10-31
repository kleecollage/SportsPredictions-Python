"""Líneas de mercado del scoreboard ESPN (DraftKings), comunes a NFL y NBA (puro)."""
from value_bot.analysis.market import american_to_decimal

PICK_EM_LINES = {"PK", "PICK", "EVEN", "EV"}


def obj(data, key: str) -> dict:
    """data[key] como dict; {} si falta, es null o no es un objeto (ESPN manda nulls)."""
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else {}


def to_float(raw) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        raw = raw.get("value", raw.get("displayValue"))
    try:
        return float(str(raw).strip().lstrip("ouOU").strip())
    except (TypeError, ValueError):
        return None


def spread_line(raw) -> float | None:
    """Línea de spread; pick'em ('PK', 'EVEN') = 0.0."""
    if isinstance(raw, str) and raw.strip().upper() in PICK_EM_LINES:
        return 0.0
    return to_float(raw)


def latest(side: dict, *fields: str) -> dict:
    """
    Snapshot más reciente (close, luego open) que trae todos los `fields`, para no
    mezclar la línea de un momento con la cuota de otro. Si ninguno está completo,
    cada campo sale del snapshot más reciente que lo tenga.
    """
    snapshots = [s for s in (obj(side, "close"), obj(side, "open")) if s]
    complete = next((s for s in snapshots if all(s.get(f) is not None for f in fields)), None)
    if complete:
        return complete
    return {f: next((s[f] for s in snapshots if s.get(f) is not None), None) for f in fields}


def parse_lines(comp: dict) -> dict:
    odds_list = comp.get("odds") or []
    if not odds_list or not isinstance(odds_list[0], dict):
        return {}
    odds = odds_list[0]
    ml, spread, total = obj(odds, "moneyline"), obj(odds, "pointSpread"), obj(odds, "total")

    home_spread = latest(obj(spread, "home"), "line", "odds")
    away_spread = latest(obj(spread, "away"), "line", "odds")
    over = latest(obj(total, "over"), "line", "odds")
    under = latest(obj(total, "under"), "odds")

    market = {
        "provider":         obj(odds, "provider").get("displayName", ""),
        "home_ml":          american_to_decimal(latest(obj(ml, "home"), "odds").get("odds")),
        "away_ml":          american_to_decimal(latest(obj(ml, "away"), "odds").get("odds")),
        "home_spread":      spread_line(home_spread.get("line")),
        "home_spread_odds": american_to_decimal(home_spread.get("odds")),
        "away_spread_odds": american_to_decimal(away_spread.get("odds")),
        "total":            to_float(over.get("line")) or to_float(odds.get("overUnder")),
        "over_odds":        american_to_decimal(over.get("odds")),
        "under_odds":       american_to_decimal(under.get("odds")),
    }
    return {k: v for k, v in market.items() if v not in (None, "")}
