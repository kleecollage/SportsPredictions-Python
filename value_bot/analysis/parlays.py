"""
Parlays: combinaciones de picks de partidos distintos, solo con cuota real y value.

La cuota de un parlay es el producto de las cuotas, así que su EV es el producto de
(1 + EV) de cada pierna: compone el edge del modelo y también su error. Por eso cada
pierna exige cuota real y value ≥ VALUE_THRESHOLD, y se guardan y resuelven (tabla
parlays) para medir si rinden.
"""
import logging
from collections.abc import Sequence
from itertools import combinations
from math import prod

from value_bot.config import MIN_CONF_PARLAY, VALUE_THRESHOLD

log = logging.getLogger(__name__)

PARLAY_SPORTS = ("nba", "nfl")      # los que el resolver liquida (fútbol aún no)
MIN_PROB = {2: 0.38, 3: 0.27}       # prob. de acertar todas las piernas
LABELS = {2: "⚡ PARLAY DOBLE", 3: "🔥 PARLAY TRIPLE"}
SPORT_EMOJI = {"football": "⚽", "nba": "🏀", "nfl": "🏈"}


def is_eligible_leg(pick: dict) -> bool:
    """Deporte que se resuelve, cuota real, probabilidad, value y confianza mínimos."""
    if pick.get("sport") not in PARLAY_SPORTS:
        return False
    if not (pick.get("real_odds_source") and pick.get("est_odds") and pick.get("our_prob")):
        return False
    if (pick.get("value") or 0) < VALUE_THRESHOLD:
        return False
    return pick["confidence"] >= MIN_CONF_PARLAY


def _win_prob(leg: dict) -> float:
    """P(ganar) sin condicionar al push (our_prob es P(ganar | no push))."""
    return leg["our_prob"] * (1 - (leg.get("push_prob") or 0))


def parlay_stats(legs: Sequence[dict]) -> dict:
    """Cuota, prob. de acertar todas (%) y EV del parlay; un push se cae del parlay (×1)."""
    return {
        "combined_odds":  round(prod(leg["est_odds"] for leg in legs), 2),
        "combined_prob":  round(prod(_win_prob(leg) for leg in legs) * 100, 1),
        "combined_value": round(prod(1 + leg["value"] for leg in legs) - 1, 4),
    }


def _game_key(leg: dict) -> str | tuple:
    return leg.get("event_id") or (leg.get("home"), leg.get("away"))


def _distinct_games(legs: Sequence[dict]) -> bool:
    return len({_game_key(leg) for leg in legs}) == len(legs)


def _leagues_diverse(legs: Sequence[dict]) -> bool:
    return len({leg.get("league") for leg in legs}) == len(legs)


def _best_combo(pool: list[dict], size: int) -> list[dict] | None:
    """Combinación de partidos distintos con más EV (ligas distintas primero)."""
    candidates = [c for c in combinations(pool, size)
                  if _distinct_games(c) and prod(_win_prob(leg) for leg in c) >= MIN_PROB[size]]
    if not candidates:
        return None
    return list(max(candidates, key=lambda c: (_leagues_diverse(c), parlay_stats(c)["combined_value"])))


def _parlay(legs: list[dict]) -> dict:
    sports = {leg.get("sport") for leg in legs}
    tag = " MIXTO" if len(sports) > 1 else f" {SPORT_EMOJI.get(legs[0].get('sport'), '🎯')}"
    return {"label": f"{LABELS[len(legs)]}{tag}", "legs": legs, **parlay_stats(legs)}


def build_parlays(all_picks: list[dict]) -> list[dict]:
    """Un doble y un triple como máximo; el triple no repite piernas del doble."""
    pool = [p for p in all_picks if is_eligible_leg(p)]
    if len(pool) < 2:
        log.info("Parlays: pool insuficiente (%d piernas elegibles de %d picks)", len(pool), len(all_picks))
        return []

    double = _best_combo(pool, 2)
    used = {id(leg) for leg in double or []}
    triple = _best_combo([p for p in pool if id(p) not in used], 3)
    parlays = [_parlay(c) for c in (double, triple) if c]
    log.info("Parlays: %d (de %d piernas elegibles)", len(parlays), len(pool))
    return parlays


def settle_parlay(legs: list[dict]) -> tuple[str, float] | None:
    """
    (outcome, profit) con los resultados de las piernas [{outcome, est_odds}]; None si
    falta alguna. Una pierna perdida pierde el parlay; un push (void) se cae y paga ×1.
    """
    outcomes = [leg.get("outcome") for leg in legs]
    if "loss" in outcomes:
        return "loss", -1.0
    if any(o not in ("win", "void") for o in outcomes):
        return None
    winners = [leg["est_odds"] for leg in legs if leg["outcome"] == "win"]
    if not winners:
        return "void", 0.0
    return "win", round(prod(winners) - 1, 4)
