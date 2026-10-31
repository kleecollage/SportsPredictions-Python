"""
Modelo de probabilidades NBA contra las líneas reales (DraftKings vía ESPN).

1. Proyección: puntos de cada equipo = media de su ataque y la defensa rival.
   Con splits casa/visita de ambos se usan esos (ya traen la ventaja de local);
   si no, promedios de temporada ± NBA_HFA/2.
2. Margen ~ Normal(NBA_MARGIN_SD) → P(ganar), P(cubrir); total ~ Normal(NBA_TOTAL_SD).
3. La probabilidad final mezcla modelo con la probabilidad sin vig del mercado;
   el modelo solo pesa NBA_MODEL_WEIGHT (menos con datos flojos). Sin cuota real
   no hay pick: las líneas ya no se inventan a partir de la proyección.
4. En cada mercado se apuesta el lado de mayor EV y por partido sale a lo sumo
   una apuesta de lado (moneyline o spread) más el total.

Pesos y topes son los conservadores de NFL, con un techo de EV más bajo (el modelo
no ve lesiones, descansos ni back-to-backs, que el mercado sí). Backtest 2025-26
contra cierres (value_bot/tools/nba_backtest.py): peso óptimo del modelo 0.00 en los
tres mercados y ROI −13.8 % en 107 picks; el modelo no aporta frente al mercado.

Funciones puras: no hacen requests ni leen la BD.
"""
from value_bot.analysis.market import (
    best_value, drop_correlated, expected_value, fmt_line, no_vig_pair, normal_cdf,
    outcomes, push_txt, sources_txt,
)

NBA_HFA            = 2.0    # ventaja de local reciente (~2 pts)
NBA_MARGIN_SD      = 12.5   # desviación estándar del margen final frente a la proyección
NBA_TOTAL_SD       = 18.0   # desviación estándar del total de puntos
NBA_MODEL_WEIGHT   = 0.15   # peso del modelo frente al mercado sin vig
NBA_MODEL_WEIGHT_MEDIUM_DQ = 0.10  # stats de la temporada anterior o muestra corta
NBA_TOTALS_ENABLED = True
NBA_MAX_PROB       = 0.85
NBA_MIN_ODDS       = 1.30   # cuotas menores no dejan margen apostable
NBA_MAX_ODDS       = 3.50   # en underdogs más largos el error del modelo se multiplica por la cuota
NBA_MIN_PICK_VALUE = 0.0    # ningún pick con EV negativo
NBA_MAX_PICK_VALUE = 0.10   # EV mayor = el mercado sabe algo (bajas, descanso) que el modelo no
NBA_HIGH_DQ_GAMES  = 10     # juegos mínimos de la temporada en curso para data_quality "high"


# ── Proyección ────────────────────────────────────────────────────

def project_nba(h_stats: dict, a_stats: dict, splits: dict) -> tuple[float, float, bool]:
    """(puntos local, puntos visitante, si se usaron splits casa/visita)."""
    h_split, a_split = splits.get("home") or {}, splits.get("away") or {}
    using_splits = bool(h_split and a_split)
    home, away = (h_split, a_split) if using_splits else (h_stats, a_stats)
    exp_h = (home["ppg"] + away["opp_ppg"]) / 2
    exp_a = (away["ppg"] + home["opp_ppg"]) / 2
    if not using_splits:
        exp_h, exp_a = exp_h + NBA_HFA / 2, exp_a - NBA_HFA / 2
    return exp_h, exp_a, using_splits


def data_quality(h_stats: dict, a_stats: dict, current_season: str) -> str:
    """"high" solo con ambos equipos en la temporada en curso y muestra suficiente."""
    def solid(stats: dict) -> bool:
        return stats.get("season") == current_season and stats.get("played", 0) >= NBA_HIGH_DQ_GAMES
    return "high" if solid(h_stats) and solid(a_stats) else "medium"


def model_weight(dq: str) -> float:
    return NBA_MODEL_WEIGHT if dq == "high" else NBA_MODEL_WEIGHT_MEDIUM_DQ


# ── Probabilidades del modelo ─────────────────────────────────────

def win_prob(margin: float) -> float:
    """P(local gana) dado el margen proyectado (local − visitante)."""
    return normal_cdf(margin / NBA_MARGIN_SD)


def spread_push(margin: float, home_line: float) -> float:
    return outcomes(margin, -home_line, NBA_MARGIN_SD)[1]


def cover_prob(margin: float, home_line: float) -> float:
    """P(local cubre | no push); home_line negativa = local favorito."""
    cover, _, miss = outcomes(margin, -home_line, NBA_MARGIN_SD)
    return cover / (cover + miss)


def total_push(total: float, line: float) -> float:
    return outcomes(total, line, NBA_TOTAL_SD)[1]


def over_prob(total: float, line: float) -> float:
    """P(Over | no push)."""
    over, _, under = outcomes(total, line, NBA_TOTAL_SD)
    return over / (over + under)


# ── Picks ─────────────────────────────────────────────────────────

def _pct(prob: float) -> str:
    """Mismo número que la barra de confianza (truncado y con techo)."""
    return f"{int(min(prob, NBA_MAX_PROB) * 100)}%"


def _two_sides(model_p: float, market_p: float, dq: str) -> tuple[tuple, tuple]:
    """(modelo, mercado, mezcla) del primer lado y del contrario."""
    weight = model_weight(dq)
    final = weight * model_p + (1 - weight) * market_p
    return (model_p, market_p, final), (1 - model_p, 1 - market_p, 1 - final)


def _build_pick(market_name: str, side: str, pick_type: str, probs: tuple, odds: float,
                reasoning: str, dq: str, push: float = 0.0) -> dict:
    model_p, market_p, prob = probs
    prob = min(prob, NBA_MAX_PROB)
    return {
        "market":           market_name,
        "side":             side,
        "type":             pick_type,
        "confidence":       int(prob * 100),
        "our_prob":         round(prob, 3),
        "push_prob":        round(push, 3),
        "est_odds":         odds,
        "value":            expected_value(prob, odds, push),
        "reasoning":        f"{reasoning} {sources_txt(model_p, market_p)}{_pct(prob)}{push_txt(push)}.",
        "data_quality":     dq,
        "real_odds_source": True,
    }


def _moneyline_pick(game, margin, market, proj_txt, dq):
    home_odds, away_odds = market.get("home_ml"), market.get("away_ml")
    if not (home_odds and away_odds):
        return None
    home_p, away_p = _two_sides(win_prob(margin), no_vig_pair(home_odds, away_odds)[0], dq)
    return best_value(
        _build_pick("moneyline", game["home"], f"Victoria {game['home']}", home_p, home_odds,
                    f"{proj_txt}. {game['home']} gana:", dq),
        _build_pick("moneyline", game["away"], f"Victoria {game['away']}", away_p, away_odds,
                    f"{proj_txt}. {game['away']} gana:", dq),
    )


def _spread_pick(game, margin, market, proj_txt, dq):
    line = market.get("home_spread")
    h_odds, a_odds = market.get("home_spread_odds"), market.get("away_spread_odds")
    if line is None or not (h_odds and a_odds):
        return None
    push = spread_push(margin, line)
    home_p, away_p = _two_sides(cover_prob(margin, line), no_vig_pair(h_odds, a_odds)[0], dq)
    txt = f"{proj_txt}. Mercado {game['home']} {fmt_line(line)}."

    def side_pick(side: str, side_line: float, probs: tuple, odds: float) -> dict:
        return _build_pick("spread", side, f"Hándicap {side} {fmt_line(side_line)}", probs, odds,
                           f"{txt} {side} cubre:", dq, push)

    return best_value(side_pick(game["home"], line, home_p, h_odds),
                      side_pick(game["away"], -line, away_p, a_odds))


def _total_pick(game, total, market, proj_txt, dq):
    line = market.get("total")
    o_odds, u_odds = market.get("over_odds"), market.get("under_odds")
    if line is None or not (o_odds and u_odds):
        return None
    push = total_push(total, line)
    over_p, under_p = _two_sides(over_prob(total, line), no_vig_pair(o_odds, u_odds)[0], dq)
    txt = f"{proj_txt}. Total proyectado {total:.1f} vs línea {line:.1f}."

    def side_pick(label: str, probs: tuple, odds: float) -> dict:
        return _build_pick("total", label, f"{label} {line:.1f} Puntos", probs, odds,
                           f"{txt} {label}:", dq, push)

    return best_value(side_pick("Over", over_p, o_odds), side_pick("Under", under_p, u_odds))


def _qualifies(pick: dict, min_conf: int, value_threshold: float) -> bool:
    """Cuota y EV dentro de los topes; luego confianza alta o edge suficiente."""
    if not NBA_MIN_ODDS < pick["est_odds"] <= NBA_MAX_ODDS:
        return False
    if not NBA_MIN_PICK_VALUE <= pick["value"] <= NBA_MAX_PICK_VALUE:
        return False
    return pick["confidence"] >= min_conf or pick["value"] >= value_threshold


def analyze_nba_game(game: dict, h_stats: dict, a_stats: dict, splits: dict, current_season: str,
                     min_conf: int = 62, value_threshold: float = 0.04) -> list[dict]:
    """
    Picks de un partido solo de mercados con cuota real en game["lines"]: a lo
    sumo una apuesta de lado y un total. Entra si su EV está entre los topes y
    supera min_conf o tiene edge ≥ value_threshold.
    """
    market = game.get("lines") or {}
    if not market:
        return []
    exp_h, exp_a, using_splits = project_nba(h_stats, a_stats, splits)
    margin, total = exp_h - exp_a, exp_h + exp_a
    dq = data_quality(h_stats, a_stats, current_season)
    series = game.get("series_summary")
    proj_txt = (f"Proyección {game['home']} {exp_h:.1f} – {exp_a:.1f} {game['away']} "
                f"(margen {margin:+.1f}{', splits casa/visita' if using_splits else ''})"
                f"{f' [{series}]' if series else ''}")

    candidates = [
        _moneyline_pick(game, margin, market, proj_txt, dq),
        _spread_pick(game, margin, market, proj_txt, dq),
        _total_pick(game, total, market, proj_txt, dq) if NBA_TOTALS_ENABLED else None,
    ]
    picks = drop_correlated([p for p in candidates if p and _qualifies(p, min_conf, value_threshold)])
    return sorted(picks, key=lambda p: p["confidence"], reverse=True)
