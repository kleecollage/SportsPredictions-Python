"""
Modelo de fútbol (Poisson) contra el mercado: totales de goles y resultado (1X2).

1. Goles esperados (XG): con ratings de local/visitante de ambos (football_ratings) es
   media de goles del local en la liga × ataque de local × defensa de visitante del rival (y al
   revés); sin ellos, media de los goles a favor propios y los que recibe el rival.
2. Totales: P(goles > línea) con Poisson del XG total contra la **línea real** (The Odds API,
   cualquier línea .0/.5; push en las enteras). En cada partido se apuesta el lado de mayor EV.
   Apagados (FOOTBALL_TOTALS_ENABLED): en el backtest 2024-25 y 2025-26 (5 ligas, cierre de
   Pinnacle) el Brier del modelo fue peor que el del mercado y el peso óptimo, 0.00.
3. Resultado: fuerza = % de victorias + puntos de forma × 2.5 (+10 al local); la diferencia
   decide qué resultado es candidato: victoria (≥ 24) o empate (≤ 10, ligas no ofensivas).
4. Todo se ancla al mercado como NBA/NFL: probabilidad = peso × modelo + resto × mercado sin
   vig (FOOTBALL_TOTALS_WEIGHT / FOOTBALL_MODEL_WEIGHT), EV entre 0 y FOOTBALL_MAX_PICK_VALUE,
   cuota 1.30–3.50 y ≥ FOOTBALL_MIN_SIDE_GAMES partidos por equipo. Sin cuota real no hay pick:
   la confianza ya no sale de una cuota estimada a partir del propio modelo.
5. Por partido: a lo sumo un total y un resultado (1, X o 2 son excluyentes).

Funciones puras: no hacen requests ni leen la BD (las cuotas reales llegan como argumento).
"""
import math
from typing import NamedTuple

from value_bot.analysis.market import best_value, expected_value, no_vig, push_txt, sources_txt
from value_bot.config import LEAGUE_AVERAGES, LEAGUE_AVG_DEFAULT, MIN_CONFIDENCE, VALUE_THRESHOLD

FOOTBALL_MIN_ODDS   = 1.30   # cuotas menores no dejan margen apostable
FOOTBALL_MAX_ODDS   = 3.50   # en underdogs largos el error del modelo se multiplica por la cuota
FOOTBALL_MODEL_WEIGHT = 0.15  # peso del modelo en 1X2 frente al mercado sin vig (el de NBA/NFL)
FOOTBALL_TOTALS_WEIGHT = 0.15  # idem en totales (tools/football_backtest dio 0.00)
FOOTBALL_TOTALS_ENABLED = False  # el modelo de totales resta frente al mercado; se evalúa pero no se apuesta
FOOTBALL_MAX_PICK_VALUE = 0.10  # más edge contra el mercado suele ser info que el modelo no tiene
FOOTBALL_MIN_SIDE_GAMES = 6  # 3 partidos de fase de liga (100 % de victorias) no dicen nada
MAX_GOALS = 15               # cola de Poisson que se suma (P(> 15 goles) ≈ 0)
HOME_STRENGTH_BONUS = 10     # ventaja de local en la escala de fuerza
FORM_PTS_WEIGHT     = 2.5    # puntos de forma (últimos 5) → fuerza
WIN_DIFF            = 24     # diff de fuerza mínima para victoria
DRAW_MAX_DIFF       = 10     # empate solo entre equipos muy igualados
DEFAULT_DRAW_PCT    = 25
DRAW_STYLES = ("defensive", "technical", "balanced")
RESULT_KEYS = ("home_odds", "draw_odds", "away_odds")
VENUE_KEYS = ("home_attack", "home_defense", "away_attack", "away_defense", "league_home_gpg", "league_away_gpg")


class Match(NamedTuple):
    """Partido con las stats de ambos equipos, sus XG y las cuotas reales."""
    fix: dict
    hs: dict
    as_: dict
    league: dict          # fila de LEAGUE_AVERAGES (o el default)
    real: dict            # home/draw/away_odds, total_line, over/under_odds; {} sin ellas
    exp_h: float
    exp_a: float
    diff: float           # fuerza local − fuerza visitante

    @property
    def lam(self) -> float:
        return self.exp_h + self.exp_a

    @property
    def style(self) -> str:
        return self.league.get("style", "balanced")

    @property
    def league_name(self) -> str:
        return self.fix.get("league", "") or "Liga"

    @property
    def result_market(self) -> dict | None:
        """Probabilidades sin vig de 1, X y 2; None sin los tres precios válidos."""
        prices = [self.real.get(k) for k in RESULT_KEYS]
        return dict(zip(RESULT_KEYS, no_vig(*prices))) if all(map(_is_price, prices)) else None

    @property
    def enough_games(self) -> bool:
        return min(self.hs.get("played", 0), self.as_.get("played", 0)) >= FOOTBALL_MIN_SIDE_GAMES


def _is_price(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 1


# ── Poisson ───────────────────────────────────────────────────────

def poisson_p(lam: float, k: int) -> float:
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def total_outcomes(lam: float, line: float) -> tuple[float, float, float]:
    """(P(goles > línea), P(goles == línea), P(goles < línea)) con goles ~ Poisson(lam)."""
    probs = [poisson_p(lam, k) for k in range(MAX_GOALS + 1)]
    under = sum(p for k, p in enumerate(probs) if k < line)
    push = probs[int(line)] if float(line).is_integer() and line <= MAX_GOALS else 0.0
    return max(0.0, 1.0 - under - push), push, under


def poisson_btts(exp_h: float, exp_a: float) -> float:
    p_home_scores = 1.0 - math.exp(-max(exp_h, 0.05))
    p_away_scores = 1.0 - math.exp(-max(exp_a, 0.05))
    return round(p_home_scores * p_away_scores, 4)


# ── Helpers ───────────────────────────────────────────────────────

def _form_txt(stats: dict) -> str:
    """'forma WWDLW (10 pts)' o 'sin forma reciente' si la fuente no la trae."""
    form = stats.get("form") or ""
    if not form:
        return "sin forma reciente"
    return f"forma {form} ({stats.get('form_pts', 0)} pts)"


def _strength(stats: dict) -> float:
    return stats.get("win_rate", 33) + stats.get("form_pts", 7) * FORM_PTS_WEIGHT


def market_pick(pick_type: str, model_p: float, market_p: float, odds: float, reasoning: str,
                weight: float, push: float = 0.0, data_quality: str = "high") -> dict | None:
    """
    Pick anclado al mercado: prob = weight × modelo + resto × mercado sin vig (P de ganar sin
    push), EV con push y cuota real. None fuera de cuota 1.30–3.50 o de EV 0–FOOTBALL_MAX_PICK_VALUE.
    """
    prob = round(weight * model_p + (1 - weight) * market_p, 3)
    value = expected_value(prob, odds, push)
    if not (FOOTBALL_MIN_ODDS <= odds <= FOOTBALL_MAX_ODDS and 0 <= value <= FOOTBALL_MAX_PICK_VALUE):
        return None
    return {"type": pick_type, "confidence": int(prob * 100), "our_prob": prob, "est_odds": odds,
            "value": value, "push_prob": round(push, 4), "real_odds_source": True,
            "reasoning": sources_txt(model_p, market_p) + reasoning + push_txt(push),
            "data_quality": data_quality}


def _best(picks: list[dict | None]) -> list[dict]:
    """Lados excluyentes de un mercado: queda el de más value."""
    valid = [p for p in picks if p]
    return [best_value(*valid)] if valid else []


def expected_goals(hs: dict, as_: dict) -> tuple[float, float]:
    """XG (local, visitante): multiplicativo con splits de local/visitante, si no aditivo."""
    if all(k in s for s in (hs, as_) for k in VENUE_KEYS):
        return (hs["league_home_gpg"] * hs["home_attack"] * as_["away_defense"],
                hs["league_away_gpg"] * as_["away_attack"] * hs["home_defense"])
    return ((hs.get("goals_for_pg", 0) + as_.get("goals_against_pg", 0)) / 2.0,
            (as_.get("goals_for_pg", 0) + hs.get("goals_against_pg", 0)) / 2.0)


def build_match(fix: dict, hs: dict, as_: dict, real: dict | None) -> Match:
    exp_h, exp_a = expected_goals(hs, as_)
    diff = _strength(hs) + HOME_STRENGTH_BONUS - _strength(as_)
    league = LEAGUE_AVERAGES.get(fix.get("league", ""), LEAGUE_AVG_DEFAULT)
    return Match(fix, hs, as_, league, real or {}, exp_h, exp_a, diff)


# ── Totales ───────────────────────────────────────────────────────

def _total_picks(m: Match) -> list[dict]:
    """Over o Under de la línea real, el de más EV; [] sin línea y cuotas reales o pocos partidos."""
    line, over_odds, under_odds = (m.real.get(k) for k in ("total_line", "over_odds", "under_odds"))
    if not (isinstance(line, (int, float)) and _is_price(over_odds) and _is_price(under_odds)
            and m.enough_games):
        return []
    p_over, push, p_under = total_outcomes(m.lam, line)
    if push >= 1:
        return []
    model_over, model_under = p_over / (1 - push), p_under / (1 - push)
    market_over, market_under = no_vig(over_odds, under_odds)
    xg = (f"XG {m.fix['home']} {m.exp_h:.2f} + {m.fix['away']} {m.exp_a:.2f} = {m.lam:.2f} goles, "
          f"línea {line:.1f}")
    return _best([
        market_pick(f"Over {line:.1f} Goles", model_over, market_over, over_odds, xg,
                    FOOTBALL_TOTALS_WEIGHT, push),
        market_pick(f"Under {line:.1f} Goles", model_under, market_under, under_odds, xg,
                    FOOTBALL_TOTALS_WEIGHT, push),
    ])


# ── Resultado (1X2) ───────────────────────────────────────────────

def _result_pick(m: Match, pick_type: str, model_p: float, odds_key: str, reasoning: str,
                 data_quality: str) -> dict | None:
    market = m.result_market
    if market is None or not m.enough_games:
        return None
    return market_pick(pick_type, model_p, market[odds_key], m.real[odds_key], reasoning,
                       FOOTBALL_MODEL_WEIGHT, data_quality=data_quality)


def _win_prob(diff: float, scale: float, floor: float, cap: float) -> float:
    raw_p = 1.0 / (1.0 + math.exp(-abs(diff) / 28.0))
    return min(cap, max(floor, round(0.50 + (raw_p - 0.50) * scale, 3)))


def _winner_pick(m: Match) -> dict | None:
    """Victoria con diferencia de fuerza ≥ WIN_DIFF (logística), anclada al mercado."""
    home, away = m.fix["home"], m.fix["away"]
    if m.diff >= WIN_DIFF:
        return _result_pick(m, f"Victoria {home}", _win_prob(m.diff, 0.72, 0.55, 0.76), "home_odds",
                            f"{home}: {m.hs.get('win_rate', 0):.0f}% victorias, {_form_txt(m.hs)}. "
                            f"Ventaja sobre {away}: {m.diff:.0f} pts fuerza.", "high")
    if m.diff <= -WIN_DIFF:
        return _result_pick(m, f"Victoria {away}", _win_prob(m.diff, 0.68, 0.54, 0.74), "away_odds",
                            f"{away}: {m.as_.get('win_rate', 0):.0f}% victorias, {_form_txt(m.as_)}. "
                            f"Ventaja visitante: {abs(m.diff):.0f} pts fuerza.", "high")
    return None


def _draw_pick(m: Match) -> dict | None:
    """Empate entre equipos igualados (tasa de empates + 6 pts, techo 62 %), anclado al mercado."""
    if abs(m.diff) > DRAW_MAX_DIFF or m.style not in DRAW_STYLES:
        return None
    league_draw = m.league.get("draw_pct", DEFAULT_DRAW_PCT)
    h_draw, a_draw = m.hs.get("draw_rate", league_draw), m.as_.get("draw_rate", league_draw)
    da = (h_draw + a_draw) / 2.0
    if da < 23 and league_draw < 26:
        return None
    model_p = round(min(0.62, max(da, league_draw) / 100.0 + 0.06), 3)
    if model_p < 0.62:
        return None
    home, away = m.fix["home"], m.fix["away"]
    return _result_pick(m, "Empate (X)", model_p, "draw_odds",
                        f"Equipos muy igualados (diff fuerza {abs(m.diff):.0f}). "
                        f"Tasa empate: {home} {m.hs.get('draw_rate', DEFAULT_DRAW_PCT):.0f}%, "
                        f"{away} {m.as_.get('draw_rate', DEFAULT_DRAW_PCT):.0f}%. "
                        f"{m.league_name} promedio {league_draw}% empates.", "medium")


# ── Partido ───────────────────────────────────────────────────────

def is_pickable(pick: dict, min_conf: int = MIN_CONFIDENCE, value_threshold: float = VALUE_THRESHOLD) -> bool:
    """Confianza mínima, o value bet con cuota real (como NBA/NFL)."""
    is_value_bet = pick.get("value") is not None and pick["value"] >= value_threshold
    return pick["confidence"] >= min_conf or is_value_bet


def analyze_football_match(fix: dict, hs: dict, as_: dict, real: dict | None = None,
                           min_conf: int = MIN_CONFIDENCE, value_threshold: float = VALUE_THRESHOLD,
                           totals: bool | None = None) -> list[dict]:
    """
    A lo sumo un total y un resultado; `hs`/`as_` deben traer stats de ambos equipos.
    `totals`: apostar totales (None = FOOTBALL_TOTALS_ENABLED; el backtest los pide siempre).
    """
    m = build_match(fix, hs, as_, real)
    with_totals = FOOTBALL_TOTALS_ENABLED if totals is None else totals
    picks = [*(_total_picks(m) if with_totals else []), *_best([_winner_pick(m), _draw_pick(m)])]
    picks = [p for p in picks if is_pickable(p, min_conf, value_threshold)]
    return sorted(picks, key=lambda p: (p["value"], p["confidence"]), reverse=True)
