"""Piezas de backtest comunes a NFL y NBA: líneas de cierre ESPN y calificación (puro)."""
from datetime import datetime

from value_bot.analysis.market import american_to_decimal

FULL_MARKET_KEYS = ("home_ml", "away_ml", "home_spread", "home_spread_odds", "away_spread_odds",
                    "total", "over_odds", "under_odds")


def parse_dt(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def parse_pickcenter(summary: dict) -> dict:
    """summary.pickcenter de ESPN → mercado con el formato de game["lines"] (perspectiva local)."""
    pc = (summary.get("pickcenter") or [{}])[0]
    home, away = pc.get("homeTeamOdds") or {}, pc.get("awayTeamOdds") or {}
    market = {
        "provider":         (pc.get("provider") or {}).get("name", ""),
        "home_ml":          american_to_decimal(home.get("moneyLine")),
        "away_ml":          american_to_decimal(away.get("moneyLine")),
        "home_spread":      pc.get("spread"),  # −3 = local favorito por 3
        "home_spread_odds": american_to_decimal(home.get("spreadOdds")),
        "away_spread_odds": american_to_decimal(away.get("spreadOdds")),
        "total":            pc.get("overUnder"),
        "over_odds":        american_to_decimal(pc.get("overOdds")),
        "under_odds":       american_to_decimal(pc.get("underOdds")),
    }
    return {k: v for k, v in market.items() if v not in (None, "")}


def has_full_market(lines: dict) -> bool:
    return all(lines.get(k) is not None for k in FULL_MARKET_KEYS)


def grade(pick: dict, game: dict, home_score: float, away_score: float) -> str:
    """win | loss | void de un pick contra el marcador final."""
    margin, total, lines = home_score - away_score, home_score + away_score, game["lines"]
    if pick["market"] == "total":
        diff, backs_positive = total - lines["total"], pick["side"] == "Over"
    else:
        line = lines["home_spread"] if pick["market"] == "spread" else 0
        diff, backs_positive = margin + line, pick["side"] == game["home"]
    if diff == 0:
        return "void"
    return "win" if (diff > 0) == backs_positive else "loss"


def outcome(diff: float) -> bool | None:
    return None if diff == 0 else diff > 0
