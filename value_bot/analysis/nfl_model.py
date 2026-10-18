"""
Modelo de probabilidades NFL.

1. Rating por equipo: puntos a favor / en contra por juego, regresados a la
   media de la liga con NFL_PRIOR_GAMES juegos "fantasma" (con 2-3 juegos
   jugados un 45-7 no debe dominar la proyección).
2. Proyección aditiva: puntos = media + ataque propio + defensa rival ± HFA/2.
3. Margen y total ~ Normal(proyección, SD histórica NFL) → P(ganar),
   P(cubrir spread), P(Over).
4. La probabilidad final mezcla modelo con la probabilidad sin vig del mercado
   (DraftKings vía ESPN). El mercado NFL es muy eficiente, así que el modelo
   solo pesa NFL_MODEL_WEIGHT; sin cuota real no se genera pick.

Funciones puras: no hacen requests ni leen la BD.
"""
import math
from collections.abc import Sequence

NFL_HFA           = 1.5    # ventaja de local moderna (~1.5 pts, era ~3 antes de 2020)
NFL_MARGIN_SD     = 13.5   # desviación estándar histórica del margen final
NFL_TOTAL_SD      = 13.0   # desviación estándar histórica del total de puntos
NFL_PRIOR_GAMES   = 3      # juegos de media de liga añadidos al rating
NFL_MODEL_WEIGHT  = 0.4    # peso del modelo frente al mercado sin vig
NFL_MAX_PROB      = 0.85   # techo: en la NFL nada es más seguro que esto
NFL_MIN_ODDS      = 1.30   # cuotas menores no dejan margen apostable
NFL_MAX_PICKS     = 3
NFL_HIGH_DQ_GAMES = 4      # juegos mínimos por equipo para data_quality "high"
NFL_PREV_SEASON_WEIGHT = 0.5  # peso de cada juego de la temporada anterior
NFL_MIN_PICK_VALUE     = 0.0  # ningún pick sale con EV negativo, aunque tenga confianza alta

# Masa de "margen exacto = k" frente a la Normal(sd 13.5). Aprox. histórica NFL:
# ~15 % de los juegos se deciden por 3, ~9 % por 7, ~5.5 % por 10, ~4.5 % por 14,
# contra ~5.8 / 5.2 / 4.5 / 3.5 % que da la Normal cerca de margen 0. Los empates
# finales (tras tiempo extra) son ~0.3 % contra ~3 % de la Normal.
NFL_KEY_MARGIN_WEIGHTS = {0: 0.1, 3: 2.6, 7: 1.75, 10: 1.2, 14: 1.3}
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


# ── Ratings y proyección ──────────────────────────────────────────

def team_rating(scores: list[tuple[float, float]], league_avg: float,
                prior_games: int = NFL_PRIOR_GAMES, current_season: bool = True,
                previous: Sequence[tuple[float, float]] = (),
                previous_weight: float = NFL_PREV_SEASON_WEIGHT) -> dict:
    """
    scores = [(pts_a_favor, pts_en_contra), ...] de la temporada actual;
    previous = los de la anterior, que pesan previous_weight cada uno.
    → {pf, pa, games, current}. current=False si la muestra incluye la anterior.
    """
    games = len(scores) + len(previous)
    if games == 0:
        return {"pf": league_avg, "pa": league_avg, "games": 0, "current": current_season}
    weight = len(scores) + previous_weight * len(previous) + prior_games
    prior = league_avg * prior_games

    def weighted(i: int) -> float:
        own = sum(s[i] for s in scores) + previous_weight * sum(s[i] for s in previous)
        return (own + prior) / weight

    return {"pf": weighted(0), "pa": weighted(1), "games": games, "current": current_season}


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


def _outcomes(expected: float, threshold: float, sd: float,
              key_weights: dict[int, float] | None = None) -> tuple[float, float, float]:
    """
    (P(x > threshold), P(x == threshold), P(x < threshold)) para un resultado
    entero x ~ Normal(expected, sd). Líneas .5 no tienen push; las enteras
    usan corrección de continuidad (±0.5) para la masa del empate. Con
    key_weights, el push en números clave se escala y ganar/perder conservan
    su proporción (P(cubrir | no push) no cambia).
    """
    if not float(threshold).is_integer():
        below = _normal_cdf((threshold - expected) / sd)
        return 1 - below, 0.0, below
    upper = _normal_cdf((threshold + 0.5 - expected) / sd)
    lower = _normal_cdf((threshold - 0.5 - expected) / sd)
    win, push, lose = 1 - upper, upper - lower, lower
    key_weight = (key_weights or {}).get(abs(int(threshold)), 1.0)
    if key_weight == 1.0:
        return win, push, lose
    push = min(push * key_weight, 0.5)
    return (1 - push) * win / (win + lose), push, (1 - push) * lose / (win + lose)


def spread_outcomes(margin: float, home_line: float,
                    sd: float = NFL_MARGIN_SD) -> tuple[float, float, float]:
    """(local cubre, push, local no cubre). home_line negativa = local favorito."""
    return _outcomes(margin, -home_line, sd, NFL_KEY_MARGIN_WEIGHTS)


def total_outcomes(total: float, line: float,
                   sd: float = NFL_TOTAL_SD) -> tuple[float, float, float]:
    """(Over, push, Under)."""
    return _outcomes(total, line, sd)


def cover_prob(margin: float, home_line: float, sd: float = NFL_MARGIN_SD) -> float:
    """P(local cubre | no hay push)."""
    win, _, lose = spread_outcomes(margin, home_line, sd)
    return win / (win + lose)


def over_prob(total: float, line: float, sd: float = NFL_TOTAL_SD) -> float:
    """P(Over | no hay push)."""
    over, _, under = total_outcomes(total, line, sd)
    return over / (over + under)


def blend(model_p: float, market_p: float, model_weight: float = NFL_MODEL_WEIGHT) -> float:
    return model_weight * model_p + (1 - model_weight) * market_p


# ── Picks ─────────────────────────────────────────────────────────

def _push_txt(push: float) -> str:
    return f" (push {push:.0%})" if push >= 0.005 else ""


def _pct(prob: float) -> str:
    """Mismo número que la barra de confianza (truncado y con techo)."""
    return f"{int(min(prob, NFL_MAX_PROB) * 100)}%"


def _fmt_line(line: float) -> str:
    return "PK" if line == 0 else f"{line:+.1f}"


def _expected_value(prob: float, odds: float, push: float) -> float:
    """
    EV por unidad. prob = P(ganar | no push); con push se devuelve la apuesta.
    Sin push equivale a config.edge().
    """
    return round(prob * (1 - push) * odds + push - 1, 4)


def _build_pick(market_name: str, side: str, pick_type: str, prob: float, odds: float,
                reasoning: str, data_quality: str, push: float = 0.0) -> dict:
    """Pick con cuota real; "side" (equipo u Over/Under) sirve para detectar correlación."""
    prob = min(prob, NFL_MAX_PROB)
    return {
        "market":           market_name,
        "side":             side,
        "type":             pick_type,
        "confidence":       int(prob * 100),
        "our_prob":         round(prob, 3),
        "push_prob":        round(push, 3),
        "est_odds":         odds,
        "value":            _expected_value(prob, odds, push),
        "reasoning":        reasoning,
        "data_quality":     data_quality,
        "real_odds_source": True,
    }


def _moneyline_pick(game, margin, market, proj_txt, dq):
    home_odds, away_odds = market.get("home_ml"), market.get("away_ml")
    if not (home_odds and away_odds):
        return None
    p_home = blend(win_prob(margin), no_vig_pair(home_odds, away_odds)[0])
    is_home = p_home >= 0.5
    side = game["home"] if is_home else game["away"]
    prob = p_home if is_home else 1 - p_home
    return _build_pick("moneyline", side, f"Victoria {side}", prob,
                       home_odds if is_home else away_odds,
                       f"{proj_txt}. {side} gana en {_pct(prob)} de escenarios.", dq)


def _spread_pick(game, margin, market, proj_txt, dq):
    line = market.get("home_spread")
    h_odds, a_odds = market.get("home_spread_odds"), market.get("away_spread_odds")
    if line is None or not (h_odds and a_odds):
        return None
    p_home = blend(cover_prob(margin, line), no_vig_pair(h_odds, a_odds)[0])
    is_home = p_home >= 0.5
    side = game["home"] if is_home else game["away"]
    side_line = line if is_home else -line
    prob = p_home if is_home else 1 - p_home
    _, push, _ = spread_outcomes(margin, line)
    return _build_pick("spread", side, f"Hándicap {side} {_fmt_line(side_line)}", prob,
                       h_odds if is_home else a_odds,
                       f"{proj_txt}. Mercado {game['home']} {_fmt_line(line)} "
                       f"→ {side} cubre en {_pct(prob)}{_push_txt(push)}.", dq, push)


def _total_pick(game, total, market, proj_txt, dq):
    line = market.get("total")
    o_odds, u_odds = market.get("over_odds"), market.get("under_odds")
    if line is None or not (o_odds and u_odds):
        return None
    p_over = blend(over_prob(total, line), no_vig_pair(o_odds, u_odds)[0])
    is_over = p_over >= 0.5
    prob = p_over if is_over else 1 - p_over
    label = "Over" if is_over else "Under"
    _, push, _ = total_outcomes(total, line)
    return _build_pick("total", label, f"{label} {line:.1f} Puntos", prob,
                       o_odds if is_over else u_odds,
                       f"{proj_txt}. Total proyectado {total:.1f} vs línea {line:.1f}"
                       f"{_push_txt(push)}.", dq, push)


def _qualifies(pick: dict, min_conf: int, value_threshold: float) -> bool:
    """Cuota apostable y EV ≥ 0; luego confianza alta o edge suficiente."""
    if pick["est_odds"] <= NFL_MIN_ODDS or pick["value"] < NFL_MIN_PICK_VALUE:
        return False
    return pick["confidence"] >= min_conf or pick["value"] >= value_threshold


def _drop_correlated(picks: list[dict]) -> list[dict]:
    """Moneyline y spread del mismo equipo son casi la misma apuesta: queda el de más value."""
    side_bets = [p for p in picks if p["market"] in SIDE_MARKETS]
    by_side: dict[str, dict] = {}
    for pick in side_bets:
        best = by_side.get(pick["side"])
        if best is None or pick["value"] > best["value"]:
            by_side[pick["side"]] = pick
    keep = {id(p) for p in by_side.values()}
    return [p for p in picks if p["market"] not in SIDE_MARKETS or id(p) in keep]


def _data_quality(home_rating: dict, away_rating: dict) -> str:
    """"high" solo con muestra suficiente de la temporada en curso (la cuota real ya es obligatoria)."""
    min_games = min(home_rating["games"], away_rating["games"])
    both_current = home_rating.get("current", True) and away_rating.get("current", True)
    return "high" if both_current and min_games >= NFL_HIGH_DQ_GAMES else "medium"


def analyze_nfl_game(game: dict, home_rating: dict, away_rating: dict, league_avg: float,
                     min_conf: int = 62, value_threshold: float = 0.04) -> list[dict]:
    """
    Picks de un partido, solo de mercados con cuota real en game["lines"].
    Cada pick lleva "market" = moneyline|spread|total. Entra si su EV ≥ 0 y
    supera min_conf o tiene edge ≥ value_threshold. Sede neutral = sin HFA.
    """
    neutral = bool(game.get("neutral_site"))
    exp_h, exp_a = project_game(home_rating, away_rating, league_avg, hfa=0.0 if neutral else NFL_HFA)
    margin, total = exp_h - exp_a, exp_h + exp_a
    market = game.get("lines") or {}
    dq = _data_quality(home_rating, away_rating)
    proj_txt = (f"Proyección {game['home']} {exp_h:.1f} – {exp_a:.1f} {game['away']} "
                f"(margen {margin:+.1f}{', sede neutral' if neutral else ''})")

    candidates = [
        _moneyline_pick(game, margin, market, proj_txt, dq),
        _spread_pick(game, margin, market, proj_txt, dq),
        _total_pick(game, total, market, proj_txt, dq),
    ]
    picks = _drop_correlated([p for p in candidates if p and _qualifies(p, min_conf, value_threshold)])
    picks.sort(key=lambda p: p["confidence"], reverse=True)
    return picks[:NFL_MAX_PICKS]
