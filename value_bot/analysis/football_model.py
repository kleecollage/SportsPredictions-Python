"""
Modelo de fútbol (Poisson) para goles, ambos marcan, 1X2, empate y doble chance.

1. Goles esperados (XG) de cada equipo = media de sus goles a favor y los que recibe el rival.
2. Over/Under 2.5 y ambos marcan salen de Poisson sobre esos XG; el estilo de la liga
   (LEAGUE_AVERAGES) decide si se busca Over (ofensiva) o Under (defensiva).
3. Fuerza = % de victorias + puntos de forma × 2.5 (+10 al local); la diferencia decide
   qué resultado (1X2) es candidato: victoria (≥ 24), doble chance (12–23) y empate (≤ 10).
4. 1X2 y empate se anclan al mercado como NBA/NFL: probabilidad = FOOTBALL_MODEL_WEIGHT ×
   modelo + resto × mercado sin vig de los tres resultados (cuota real de The Odds API), con
   EV entre 0 y FOOTBALL_MAX_PICK_VALUE, cuota 1.30–3.50, ≥ FOOTBALL_MIN_SIDE_GAMES partidos
   por equipo y un solo resultado por partido. Sin los tres precios no hay pick de resultado.
   El peso viene de NBA/NFL (backtest de fútbol pendiente). El resto (goles, ambos marcan)
   lleva cuota estimada a partir de la probabilidad del modelo.

Funciones puras: no hacen requests ni leen la BD (la cuota real llega como argumento).
"""
import math
from typing import NamedTuple

from value_bot.analysis.market import best_value, no_vig, sources_txt
from value_bot.config import (
    LEAGUE_AVERAGES, LEAGUE_AVG_DEFAULT, MIN_CONFIDENCE, VALUE_THRESHOLD, edge, est_odds,
)

FOOTBALL_MIN_ODDS   = 1.30   # cuotas menores no dejan margen apostable
FOOTBALL_MAX_ODDS   = 3.50   # en underdogs largos el error del modelo se multiplica por la cuota
FOOTBALL_MODEL_WEIGHT = 0.15  # peso del modelo frente al mercado sin vig (el de NBA/NFL)
FOOTBALL_MAX_PICK_VALUE = 0.10  # más edge contra el mercado suele ser info que el modelo no tiene
FOOTBALL_MIN_SIDE_GAMES = 6  # 3 partidos de fase de liga (100 % de victorias) no dicen nada
MAX_PICKS_MATCH     = 3
HOME_STRENGTH_BONUS = 10     # ventaja de local en la escala de fuerza
FORM_PTS_WEIGHT     = 2.5    # puntos de forma (últimos 5) → fuerza
WIN_DIFF            = 24     # diff de fuerza mínima para 1X2
DOUBLE_CHANCE_DIFF  = 12     # 12 ≤ diff < 24 → doble chance
DRAW_MAX_DIFF       = 10     # empate solo entre equipos muy igualados
DEFAULT_DRAW_PCT    = 25
OVER_STYLES = ("attacking", "balanced", "technical")
DRAW_STYLES = ("defensive", "technical", "balanced")


class Match(NamedTuple):
    """Partido con las stats de ambos equipos y las proyecciones Poisson."""
    fix: dict
    hs: dict
    as_: dict
    league: dict          # fila de LEAGUE_AVERAGES (o el default)
    real: dict            # cuotas 1X2 reales (home_odds, draw_odds, away_odds); {} sin ellas
    exp_h: float
    exp_a: float
    diff: float           # fuerza local − fuerza visitante

    @property
    def lam(self) -> float:
        return self.exp_h + self.exp_a

    @property
    def over25(self) -> float:
        return poisson_over(self.lam, 2)

    @property
    def btts(self) -> float:
        return poisson_btts(self.exp_h, self.exp_a)

    @property
    def style(self) -> str:
        return self.league.get("style", "balanced")

    @property
    def league_name(self) -> str:
        return self.fix.get("league", "") or "Liga"

    @property
    def market_probs(self) -> dict | None:
        """Probabilidades sin vig por clave de cuota; None sin los tres precios válidos."""
        keys = ("home_odds", "draw_odds", "away_odds")
        prices = [self.real.get(k) for k in keys]
        if not all(isinstance(p, (int, float)) and p > 1 for p in prices):
            return None
        return dict(zip(keys, no_vig(*prices)))

    @property
    def enough_games(self) -> bool:
        return min(self.hs.get("played", 0), self.as_.get("played", 0)) >= FOOTBALL_MIN_SIDE_GAMES


# ── Poisson ───────────────────────────────────────────────────────

def poisson_p(lam: float, k: int) -> float:
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def poisson_over(lam: float, threshold: int) -> float:
    p_under_eq = sum(poisson_p(lam, k) for k in range(threshold + 1))
    return round(max(0.0, min(1.0, 1.0 - p_under_eq)), 4)


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


def _pick(pick_type: str, prob: float, conf: int, reasoning: str, data_quality: str = "high",
          odds: float | None = None, real_odds: float | None = None) -> dict:
    """Pick con cuota estimada (de la prob); con cuota real, esa cuota y su value."""
    pick = {"type": pick_type, "confidence": conf, "our_prob": prob,
            "est_odds": odds if odds is not None else est_odds(prob), "value": None,
            "reasoning": reasoning, "data_quality": data_quality}
    if not real_odds:
        return pick
    return {**pick, "est_odds": real_odds, "value": edge(prob, real_odds), "real_odds_source": True}


def _result_pick(m: Match, pick_type: str, model_p: float, odds_key: str, reasoning: str,
                 data_quality: str) -> list[dict]:
    """
    Pick de resultado (1, X o 2) anclado al mercado: mezcla el modelo con la prob. sin vig
    y paga la cuota real. [] sin mercado completo, con pocos partidos o fuera de cuota / EV.
    """
    market = m.market_probs
    if market is None or not m.enough_games:
        return []
    odds = m.real[odds_key]
    prob = round(FOOTBALL_MODEL_WEIGHT * model_p + (1 - FOOTBALL_MODEL_WEIGHT) * market[odds_key], 3)
    value = edge(prob, odds)
    if not (FOOTBALL_MIN_ODDS <= odds <= FOOTBALL_MAX_ODDS and 0 <= value <= FOOTBALL_MAX_PICK_VALUE):
        return []
    return [_pick(pick_type, prob, int(prob * 100), sources_txt(model_p, market[odds_key]) + reasoning,
                  data_quality=data_quality, real_odds=odds)]


def _best_result(picks: list[dict]) -> list[dict]:
    """1, X y 2 son excluyentes: queda el de más value."""
    return [best_value(*picks)] if picks else []


def build_match(fix: dict, hs: dict, as_: dict, real: dict | None) -> Match:
    exp_h = (hs.get("goals_for_pg", 0) + as_.get("goals_against_pg", 0)) / 2.0
    exp_a = (as_.get("goals_for_pg", 0) + hs.get("goals_against_pg", 0)) / 2.0
    diff = _strength(hs) + HOME_STRENGTH_BONUS - _strength(as_)
    league = LEAGUE_AVERAGES.get(fix.get("league", ""), LEAGUE_AVG_DEFAULT)
    return Match(fix, hs, as_, league, real or {}, exp_h, exp_a, diff)


# ── Mercados ──────────────────────────────────────────────────────

def _goal_total_picks(m: Match) -> list[dict]:
    """Over 2.5 en ligas ofensivas/equilibradas; Under 2.5 (u Over) en ligas defensivas."""
    home, away = m.fix["home"], m.fix["away"]
    over, under = m.over25, 1.0 - m.over25
    xg = f"XG local {m.exp_h:.2f} + XG visitante {m.exp_a:.2f} = {m.lam:.2f} esperados. "
    if m.style in OVER_STYLES:
        if over < 0.50:
            return []
        return [_pick("Over 2.5 Goles", round(over, 3), min(80, int(over * 100 * 0.97)),
                      xg + f"Poisson P(≥3 goles)={over*100:.0f}%. "
                      f"{home} anota {m.hs.get('goals_for_pg', 0):.1f}/j y concede "
                      f"{m.hs.get('goals_against_pg', 0):.1f}/j.", odds=est_odds(over))]
    if under >= 0.52:
        return [_pick("Under 2.5 Goles", round(under, 3), min(78, int(under * 100 * 0.97)),
                      xg + f"Poisson P(≤2 goles)={under*100:.0f}%. "
                      f"Liga defensiva: {home} concede {m.hs.get('goals_against_pg', 0):.1f}/j, "
                      f"{away} {m.as_.get('goals_against_pg', 0):.1f}/j.", odds=est_odds(under))]
    if over >= 0.52:
        return [_pick("Over 2.5 Goles", round(over, 3), min(75, int(over * 100 * 0.95)),
                      f"XG={m.lam:.2f}. Pese al estilo defensivo de {m.league_name}, "
                      f"Poisson indica {over*100:.0f}% de probabilidad de Over 2.5.",
                      odds=est_odds(over))]
    return []


def _btts_pick(m: Match, picks: list[dict]) -> list[dict]:
    """Ambos marcan si Poisson da ≥ 56 % y no hay un Under 2.5 (se contradicen)."""
    btts = m.btts
    if btts < 0.56 or any(p["type"] == "Under 2.5 Goles" for p in picks):
        return []
    home, away = m.fix["home"], m.fix["away"]
    return [_pick("Ambos Marcan (BTTS)", round(btts, 3), min(78, int(btts * 100 * 0.96)),
                  f"P(local marca)={(1-math.exp(-m.exp_h))*100:.0f}%, "
                  f"P(visitante marca)={(1-math.exp(-m.exp_a))*100:.0f}%. "
                  f"{home} anota {m.hs.get('goals_for_pg', 0):.1f}/j; "
                  f"{away} {m.as_.get('goals_for_pg', 0):.1f}/j.", odds=est_odds(btts))]


def _win_prob(diff: float, scale: float, floor: float, cap: float) -> float:
    raw_p = 1.0 / (1.0 + math.exp(-abs(diff) / 28.0))
    return min(cap, max(floor, round(0.50 + (raw_p - 0.50) * scale, 3)))


def _winner_pick(m: Match) -> list[dict]:
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
    return []


def _draw_pick(m: Match) -> list[dict]:
    """Empate entre equipos igualados (tasa de empates + 6 pts, techo 62 %), anclado al mercado."""
    if abs(m.diff) > DRAW_MAX_DIFF or m.style not in DRAW_STYLES:
        return []
    league_draw = m.league.get("draw_pct", DEFAULT_DRAW_PCT)
    h_draw, a_draw = m.hs.get("draw_rate", league_draw), m.as_.get("draw_rate", league_draw)
    da = (h_draw + a_draw) / 2.0
    if da < 23 and league_draw < 26:
        return []
    model_p = round(min(0.62, max(da, league_draw) / 100.0 + 0.06), 3)
    if model_p < 0.62:
        return []
    home, away = m.fix["home"], m.fix["away"]
    return _result_pick(m, "Empate (X)", model_p, "draw_odds",
                        f"Equipos muy igualados (diff fuerza {abs(m.diff):.0f}). "
                        f"Tasa empate: {home} {m.hs.get('draw_rate', DEFAULT_DRAW_PCT):.0f}%, "
                        f"{away} {m.as_.get('draw_rate', DEFAULT_DRAW_PCT):.0f}%. "
                        f"{m.league_name} promedio {league_draw}% empates.", "medium")


def _double_chance_pick(m: Match) -> list[dict]:
    """1X / X2 con ventaja moderada; cuota estimada con descuento (no hay cuota real)."""
    if not DOUBLE_CHANCE_DIFF <= abs(m.diff) < WIN_DIFF:
        return []
    raw_p = 1.0 / (1.0 + math.exp(-abs(m.diff) / 22.0))
    home, away = m.fix["home"], m.fix["away"]
    if m.diff > 0:
        pick_type = f"Doble Chance {home} (1X)"
        prob = round(min(0.78, 0.62 + (raw_p - 0.5) * 0.55), 3)
        reasoning = (f"{home}: {m.hs.get('win_rate', 0):.0f}% victorias, {_form_txt(m.hs)}. "
                     f"Ventaja local de {m.diff:.0f} pts → 1X más seguro que 1 directo.")
    else:
        pick_type = f"Doble Chance {away} (X2)"
        prob = round(min(0.76, 0.60 + (raw_p - 0.5) * 0.52), 3)
        reasoning = (f"{away}: {m.as_.get('win_rate', 0):.0f}% victorias, {_form_txt(m.as_)}. "
                     f"Ventaja visitante {abs(m.diff):.0f} pts — X2 cubre victoria o empate.")
    # descuento sobre cuota, no sobre prob
    return [_pick(pick_type, prob, int(prob * 100), reasoning, data_quality="medium",
                  odds=round(est_odds(prob) * 0.72, 2))]


def _over15_pick(m: Match) -> list[dict]:
    """Over 1.5 conservador solo si ningún otro mercado dio pick y el XG total es ≥ 2."""
    if m.lam < 2.0:
        return []
    prob = round(min(0.75, 0.54 + (m.lam - 2.0) * 0.08), 3)
    return [_pick("Over 1.5 Goles", prob, int(prob * 100),
                  f"XG total esperado: {m.lam:.2f} goles "
                  f"({m.fix['home']} {m.exp_h:.2f} + {m.fix['away']} {m.exp_a:.2f}). "
                  f"Pick conservador — {m.over25*100:.0f}% no supera umbral Over 2.5.",
                  data_quality="medium")]


def is_pickable(pick: dict, min_conf: int = MIN_CONFIDENCE, value_threshold: float = VALUE_THRESHOLD) -> bool:
    """Confianza mínima, o value bet con cuota real (como NBA/NFL)."""
    is_value_bet = pick.get("value") is not None and pick["value"] >= value_threshold
    return pick["confidence"] >= min_conf or is_value_bet


def analyze_football_match(fix: dict, hs: dict, as_: dict, real: dict | None = None,
                           min_conf: int = MIN_CONFIDENCE,
                           value_threshold: float = VALUE_THRESHOLD) -> list[dict]:
    """Hasta MAX_PICKS_MATCH picks del partido; `hs`/`as_` deben traer stats de ambos equipos."""
    m = build_match(fix, hs, as_, real)
    picks = _goal_total_picks(m)
    picks = [*picks, *_btts_pick(m, picks), *_best_result([*_winner_pick(m), *_draw_pick(m)]),
             *_double_chance_pick(m)]
    picks = picks or _over15_pick(m)
    picks = [p for p in picks
             if is_pickable(p, min_conf, value_threshold) and p.get("est_odds", 0) > FOOTBALL_MIN_ODDS]
    picks.sort(key=lambda x: (x["confidence"], x.get("value") or -99), reverse=True)
    return picks[:MAX_PICKS_MATCH]


def apply_calibration(pick: dict, factors: dict) -> dict:
    """
    Nuevo pick con confidence y our_prob ajustados al historial real del modelo.
    Con cuota real se recalcula el value; con cuota estimada, la cuota (sale de la prob).
    """
    if not factors:
        return pick
    bucket = (pick["confidence"] // 5) * 5
    factor = factors.get(bucket, 1.0)
    if factor == 1.0:
        return pick
    new_conf = min(95, max(55, int(pick["confidence"] * factor)))
    new_prob = round(min(0.95, (pick.get("our_prob") or pick["confidence"] / 100) * factor), 3)
    if pick.get("real_odds_source"):
        priced = {"value": edge(new_prob, pick["est_odds"])}
    else:
        priced = {"est_odds": est_odds(new_prob)}
    return {**pick, "confidence": new_conf, "our_prob": new_prob, **priced}
