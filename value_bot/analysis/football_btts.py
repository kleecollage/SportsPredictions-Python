"""
"Ambos Marcan" (BTTS) bajo demanda con un árbol de decisión por partido (puro).

The Odds API solo da BTTS por evento y cada consulta cuesta BTTS_CREDIT_COST créditos, así que
antes de gastar se recorre el árbol y cada partido deja su ruta (`Decision.path`):

    liga seleccionada? ─no→ skip
    stats de ambos (≥ FOOTBALL_MIN_SIDE_GAMES)? ─no→ skip
    ya hay pick del partido (esta corrida o previas)? ─sí→ skip
    evento en The Odds API? ─no→ skip
    P_modelo(BTTS) fuera de BTTS_MODEL_RANGE? ─sí→ skip (sin edge plausible, no se gasta)
    créditos tras la consulta ≥ reserva? ─no→ stop
    → pedir cuota → mezcla con no-vig sí/no → cuota 1.30–3.50, EV 0–10 % → pick | descartado

Solo se apuesta el "sí" (lo que liquida el resolver). Sin backtest (el CSV de cierre no trae
BTTS) y con el mismo XG que perdió contra el mercado en los totales: el peso del modelo es el
de NBA/NFL y el runner diario nunca gasta créditos en esto.
"""
from collections.abc import Callable
from typing import NamedTuple

from value_bot.analysis.football_model import (
    FOOTBALL_MIN_SIDE_GAMES, expected_goals, is_pickable, market_pick, poisson_btts,
)
from value_bot.analysis.market import no_vig_pair

BTTS_TYPE = "Ambos Marcan (BTTS)"
BTTS_CREDIT_COST = 1
BTTS_MODEL_RANGE = (0.45, 0.75)   # fuera de esto el modelo no tiene nada creíble que aportar
FOOTBALL_BTTS_WEIGHT = 0.15       # sin validar: no hay cierre histórico de BTTS


class Decision(NamedTuple):
    match: str
    league: str
    path: tuple[str, ...]
    spend: bool = False           # hay que pedir (o se pidió) la cuota
    stop: bool = False            # sin créditos: no seguir con más partidos
    pick: dict | None = None
    model_p: float | None = None


def _label(fix: dict) -> str:
    return f"{fix['home']} vs {fix['away']}"


def _enough(stats: dict | None) -> bool:
    return bool(stats) and stats.get("played", 0) >= FOOTBALL_MIN_SIDE_GAMES


def btts_gate(fix: dict, hs: dict | None, as_: dict | None, *, selected: set[str] | frozenset[str],
              sent_before: bool, find_event: Callable[[], str | None], credits_left: int | None,
              reserve: int) -> Decision:
    """
    Ramas previas al gasto; `spend` = vale la pena pedir la cuota de este partido. `find_event`
    (id del evento en The Odds API) solo se llama si el partido pasa stats y dedup: buscarlo
    puede costar la llamada de cuotas de la liga.
    """
    d = Decision(_label(fix), fix.get("league", ""), ())

    def step(text: str, **changes) -> Decision:
        return d._replace(path=(*d.path, text), **changes)

    if d.league not in selected:
        return step("liga no seleccionada → skip")
    d = step("liga seleccionada")
    if not (_enough(hs) and _enough(as_)):
        return step(f"sin {FOOTBALL_MIN_SIDE_GAMES} partidos de ambos → skip")
    d = step("stats de ambos")
    if sent_before:
        return step("el partido ya tiene pick → skip")
    if not find_event():
        return step("sin evento en The Odds API → skip")
    model_p = poisson_btts(*expected_goals(hs, as_))
    lo, hi = BTTS_MODEL_RANGE
    d = d._replace(model_p=model_p)
    if not lo <= model_p <= hi:
        return step(f"modelo {model_p:.0%} fuera de {lo:.0%}–{hi:.0%} → skip (no se gasta)")
    d = step(f"modelo {model_p:.0%}")
    if credits_left is not None and credits_left - BTTS_CREDIT_COST < reserve:
        return step(f"{credits_left} créditos (reserva {reserve}) → stop", stop=True)
    return step(f"pedir cuota ({BTTS_CREDIT_COST} crédito)", spend=True)


def btts_evaluate(gate: Decision, fix: dict, odds: tuple[float, float] | None,
                  min_conf: int, value_threshold: float) -> Decision:
    """Con la cuota (sí, no) pedida: pick anclado al mercado o descartado."""
    if odds is None:
        return gate._replace(path=(*gate.path, "sin cuota BTTS → descartado"))
    yes, no = odds
    market_p = no_vig_pair(yes, no)[0]
    pick = market_pick(BTTS_TYPE, gate.model_p, market_p, yes,
                       f"P(ambos marcan) con XG {fix['home']} y {fix['away']}.", FOOTBALL_BTTS_WEIGHT)
    if pick is None or not is_pickable(pick, min_conf, value_threshold):
        why = "fuera de cuota o EV" if pick is None else f"EV {pick['value']:+.1%} y {pick['confidence']}%"
        return gate._replace(path=(*gate.path, f"cuota {yes:.2f} · mercado {market_p:.0%} → descartado ({why})"))
    return gate._replace(path=(*gate.path, f"cuota {yes:.2f} · EV {pick['value']:+.1%} → pick"), pick=pick)
