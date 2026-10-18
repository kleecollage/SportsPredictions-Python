"""
Modelo de probabilidades NFL.

1. Rating por equipo: puntos a favor / en contra por juego, regresados a la
   media de la liga con NFL_PRIOR_GAMES juegos "fantasma" (con 2-3 juegos
   jugados un 45-7 no debe dominar la proyección).
2. Proyección aditiva: puntos = media + ataque propio + defensa rival ± HFA/2.
3. Margen y total ~ Normal(proyección, SD histórica NFL) → P(ganar),
   P(cubrir spread), P(Over).
4. Si ESPN trae líneas (DraftKings), la probabilidad final mezcla modelo con
   la probabilidad sin vig del mercado. El mercado NFL es muy eficiente, así
   que el modelo solo pesa NFL_MODEL_WEIGHT.

Funciones puras: no hacen requests ni leen la BD.
"""
import math

from value_bot.config import edge, est_odds

NFL_HFA           = 1.5    # ventaja de local moderna (~1.5 pts, era ~3 antes de 2020)
NFL_MARGIN_SD     = 13.5   # desviación estándar histórica del margen final
NFL_TOTAL_SD      = 13.0   # desviación estándar histórica del total de puntos
NFL_PRIOR_GAMES   = 3      # juegos de media de liga añadidos al rating
NFL_MODEL_WEIGHT  = 0.4    # peso del modelo frente al mercado sin vig
NFL_MAX_PROB      = 0.85   # techo: en la NFL nada es más seguro que esto
NFL_MIN_ODDS      = 1.30   # cuotas menores no dejan margen apostable
NFL_MAX_PICKS     = 3
NFL_HIGH_DQ_GAMES = 4      # juegos mínimos por equipo para data_quality "high"


# ── Cuotas ────────────────────────────────────────────────────────

def american_to_decimal(raw) -> float | None:
    """'-110' → 1.909, '+140' → 2.4, 'EVEN' → 2.0. None si no es válida."""
    if raw is None:
        return None
    text = str(raw).strip().upper()
    if text == "EVEN":
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


# ── Ratings y proyección ──────────────────────────────────────────

def team_rating(scores: list[tuple[float, float]], league_avg: float,
                prior_games: int = NFL_PRIOR_GAMES) -> dict:
    """scores = [(pts_a_favor, pts_en_contra), ...] → {pf, pa, games}."""
    n = len(scores)
    if n == 0:
        return {"pf": league_avg, "pa": league_avg, "games": 0}
    prior = league_avg * prior_games
    pf = (sum(s[0] for s in scores) + prior) / (n + prior_games)
    pa = (sum(s[1] for s in scores) + prior) / (n + prior_games)
    return {"pf": pf, "pa": pa, "games": n}


def project_game(home: dict, away: dict, league_avg: float,
                 hfa: float = NFL_HFA) -> tuple[float, float]:
    """Puntos esperados (local, visitante)."""
    exp_home = league_avg + (home["pf"] - league_avg) + (away["pa"] - league_avg) + hfa / 2
    exp_away = league_avg + (away["pf"] - league_avg) + (home["pa"] - league_avg) - hfa / 2
    return exp_home, exp_away


# ── Probabilidades ────────────────────────────────────────────────

def _normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def win_prob(margin: float, sd: float = NFL_MARGIN_SD) -> float:
    """P(local gana) dado el margen proyectado (local - visitante)."""
    return _normal_cdf(margin / sd)


def cover_prob(margin: float, home_line: float, sd: float = NFL_MARGIN_SD) -> float:
    """P(local cubre). home_line negativa = local favorito (ej. -3.5)."""
    return _normal_cdf((margin + home_line) / sd)


def over_prob(total: float, line: float, sd: float = NFL_TOTAL_SD) -> float:
    return 1 - _normal_cdf((line - total) / sd)


def blend(model_p: float, market_p: float, model_weight: float = NFL_MODEL_WEIGHT) -> float:
    return model_weight * model_p + (1 - model_weight) * market_p


# ── Picks ─────────────────────────────────────────────────────────

def _pct(prob: float) -> str:
    """Mismo número que la barra de confianza (truncado y con techo)."""
    return f"{int(min(prob, NFL_MAX_PROB) * 100)}%"


def _fmt_line(line: float) -> str:
    return f"{line:+.1f}"


def _build_pick(market_name: str, pick_type: str, prob: float, odds: float | None,
                reasoning: str, data_quality: str) -> dict:
    prob = min(prob, NFL_MAX_PROB)
    pick = {
        "market":       market_name,
        "type":         pick_type,
        "confidence":   int(prob * 100),
        "our_prob":     round(prob, 3),
        "est_odds":     odds if odds else est_odds(prob),
        "value":        edge(prob, odds) if odds else None,
        "reasoning":    reasoning,
        "data_quality": data_quality,
    }
    if odds:
        pick["real_odds_source"] = True
    return pick


def _moneyline_pick(game, margin, market, proj_txt, dq):
    p_home = win_prob(margin)
    home_odds, away_odds = market.get("home_ml"), market.get("away_ml")
    has_market = bool(home_odds and away_odds)
    if has_market:
        p_home = blend(p_home, no_vig_pair(home_odds, away_odds)[0])
    is_home = p_home >= 0.5
    side = game["home"] if is_home else game["away"]
    prob = p_home if is_home else 1 - p_home
    odds = (home_odds if is_home else away_odds) if has_market else None
    return _build_pick("moneyline", f"Victoria {side}", prob, odds,
                       f"{proj_txt}. {side} gana en {_pct(prob)} de escenarios.",
                       dq if has_market else "medium")


def _spread_pick(game, margin, market, proj_txt, dq):
    line = market.get("home_spread")
    if line is None:
        return None
    h_odds, a_odds = market.get("home_spread_odds"), market.get("away_spread_odds")
    p_home = cover_prob(margin, line)
    mkt_home = no_vig_pair(h_odds, a_odds)[0] if (h_odds and a_odds) else 0.5
    p_home = blend(p_home, mkt_home)
    is_home = p_home >= 0.5
    side = game["home"] if is_home else game["away"]
    side_line = line if is_home else -line
    prob = p_home if is_home else 1 - p_home
    odds = h_odds if is_home else a_odds
    return _build_pick("spread", f"Hándicap {side} {_fmt_line(side_line)}", prob, odds,
                       f"{proj_txt}. Mercado {game['home']} {_fmt_line(line)} "
                       f"→ {side} cubre en {_pct(prob)}.", dq)


def _total_pick(game, total, market, proj_txt, dq):
    line = market.get("total")
    if line is None:
        return None
    o_odds, u_odds = market.get("over_odds"), market.get("under_odds")
    p_over = over_prob(total, line)
    mkt_over = no_vig_pair(o_odds, u_odds)[0] if (o_odds and u_odds) else 0.5
    p_over = blend(p_over, mkt_over)
    is_over = p_over >= 0.5
    prob = p_over if is_over else 1 - p_over
    label = "Over" if is_over else "Under"
    odds = o_odds if is_over else u_odds
    return _build_pick("total", f"{label} {line:.1f} Puntos", prob, odds,
                       f"{proj_txt}. Total proyectado {total:.1f} vs línea {line:.1f}.", dq)


def _qualifies(pick: dict, min_conf: int, value_threshold: float) -> bool:
    if pick["est_odds"] <= NFL_MIN_ODDS:
        return False
    if pick["confidence"] >= min_conf:
        return True
    has_value = pick["value"] is not None and pick["value"] >= value_threshold
    return has_value and pick["our_prob"] >= 0.5


def analyze_nfl_game(game: dict, home_rating: dict, away_rating: dict, league_avg: float,
                     min_conf: int = 62, value_threshold: float = 0.04) -> list[dict]:
    """
    Picks de un partido: moneyline siempre; spread y total solo con líneas de
    mercado (game["lines"]). Cada pick lleva "market" = moneyline|spread|total.
    Entra un pick si supera min_conf o si tiene edge >= value_threshold.
    """
    exp_h, exp_a = project_game(home_rating, away_rating, league_avg)
    margin, total = exp_h - exp_a, exp_h + exp_a
    market = game.get("lines") or {}

    min_games = min(home_rating["games"], away_rating["games"])
    dq = "high" if (market and min_games >= NFL_HIGH_DQ_GAMES) else "medium"
    proj_txt = (f"Proyección {game['home']} {exp_h:.1f} – {exp_a:.1f} {game['away']} "
                f"(margen {margin:+.1f})")

    candidates = [
        _moneyline_pick(game, margin, market, proj_txt, dq),
        _spread_pick(game, margin, market, proj_txt, dq),
        _total_pick(game, total, market, proj_txt, dq),
    ]
    picks = [p for p in candidates if p and _qualifies(p, min_conf, value_threshold)]
    picks.sort(key=lambda p: p["confidence"], reverse=True)
    return picks[:NFL_MAX_PICKS]
