"""Primitivas de mercado compartidas por los modelos NFL y NBA (puro: sin red ni BD)."""
import math

SIDE_MARKETS = ("moneyline", "spread")  # apuestas a un equipo, correlacionadas entre sí


# ── Cuotas ────────────────────────────────────────────────────────

def american_to_decimal(raw) -> float | None:
    """'-110' → 1.909, '+140' → 2.4, 'EVEN' → 2.0. None si no es válida."""
    if raw is None:
        return None
    text = str(raw).strip().upper()
    if text in ("EVEN", "EV"):
        return 2.0
    try:
        value = float(text)
    except ValueError:
        return None
    if value >= 100:
        return round(1 + value / 100, 3)
    if value <= -100:
        return round(1 + 100 / abs(value), 3)
    return None


def no_vig_pair(odds_a: float, odds_b: float) -> tuple[float, float]:
    """Probabilidades implícitas de dos lados quitando el margen de la casa."""
    imp_a, imp_b = 1 / odds_a, 1 / odds_b
    total = imp_a + imp_b
    return imp_a / total, imp_b / total


def expected_value(prob: float, odds: float, push: float) -> float:
    """
    EV por unidad. prob = P(ganar | no push); con push se devuelve la apuesta.
    Sin push equivale a config.edge().
    """
    return round(prob * (1 - push) * odds + push - 1, 4)


def best_value(*picks: dict) -> dict:
    """De los dos lados de un mercado, el de mayor EV (no el más probable)."""
    return max(picks, key=lambda p: p["value"])


def drop_correlated(picks: list[dict]) -> list[dict]:
    """
    Una sola apuesta de lado por partido, la de más value: moneyline y spread del
    mismo equipo son casi la misma apuesta, y de equipos opuestos son una cobertura.
    """
    side_bets = [p for p in picks if p["market"] in SIDE_MARKETS]
    if not side_bets:
        return picks
    best = best_value(*side_bets)
    return [p for p in picks if p["market"] not in SIDE_MARKETS or p is best]


# ── Probabilidades ────────────────────────────────────────────────

def normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def outcomes(expected: float, threshold: float, sd: float) -> tuple[float, float, float]:
    """
    (P(x > threshold), P(x == threshold), P(x < threshold)) para un resultado
    entero x ~ Normal(expected, sd). Líneas .5 no tienen push; las enteras
    usan corrección de continuidad (±0.5) para la masa del empate.
    """
    if not float(threshold).is_integer():
        below = normal_cdf((threshold - expected) / sd)
        return 1 - below, 0.0, below
    upper = normal_cdf((threshold + 0.5 - expected) / sd)
    lower = normal_cdf((threshold - 0.5 - expected) / sd)
    return 1 - upper, upper - lower, lower


# ── Texto ─────────────────────────────────────────────────────────

def push_txt(push: float) -> str:
    return f" (push {push:.0%})" if push >= 0.005 else ""


def fmt_line(line: float) -> str:
    return "PK" if line == 0 else f"{line:+.1f}"


def sources_txt(model_p: float, market_p: float) -> str:
    """'Modelo 55% · mercado 34% → ': de dónde sale la probabilidad final (la mezcla)."""
    return f"Modelo {int(model_p * 100)}% · mercado {int(market_p * 100)}% → "
