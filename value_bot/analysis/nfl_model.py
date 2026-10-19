"""
Modelo de probabilidades NFL.

1. Rating por equipo: puntos a favor / en contra por juego, regresados a la
   media de la liga con NFL_PRIOR_GAMES juegos "fantasma" (con 2-3 juegos
   jugados un 45-7 no debe dominar la proyección).
2. Proyección aditiva: puntos = media + ataque propio + defensa rival ± HFA/2.
3. Margen ~ Normal discretizada con masa extra en números clave (3, 7, 10, 14)
   → P(cubrir spread); total ~ Normal → P(Over); P(ganar) con la Normal.
4. La probabilidad final mezcla modelo con la probabilidad sin vig del mercado
   (DraftKings vía ESPN). El mercado NFL es muy eficiente, así que el modelo
   solo pesa NFL_MODEL_WEIGHT (menos con datos flojos); sin cuota real no hay pick.
   Los totales están apagados (NFL_TOTALS_ENABLED) y las semanas 1–2 no se apuestan:
   ver el backtest en value_bot/tools/nfl_backtest.py.
5. En cada mercado se apuesta el lado de mayor EV (puede ser el underdog) y por
   partido sale a lo sumo una apuesta de lado (moneyline o spread) más el total.
   Underdogs más largos que NFL_MAX_ODDS y EV > NFL_MAX_PICK_VALUE se descartan:
   el error del modelo se multiplica por la cuota y un desacuerdo grande con el
   mercado suele ser información que el modelo no tiene (QB, lesiones).

Limitación conocida: los ratings no se ajustan por la fuerza del rival (un 3-0
contra rivales débiles infla el ataque); el peso del mercado lo amortigua.

Funciones puras: no hacen requests ni leen la BD.
"""
import math
from collections.abc import Sequence

NFL_HFA           = 1.5    # ventaja de local moderna (~1.5 pts, era ~3 antes de 2020)
NFL_MARGIN_SD     = 13.5   # desviación estándar histórica del margen final
NFL_TOTAL_SD      = 13.0   # desviación estándar histórica del total de puntos
NFL_PRIOR_GAMES   = 3      # juegos de media de liga añadidos al rating
# Backtest 2026 (91 juegos, semanas 1–6, cierre DraftKings): el peso del modelo que
# minimiza el Brier en ML y spread fue 0.15; en totales, 0 (el lado del modelo acertó 37 %).
NFL_MODEL_WEIGHT  = 0.15   # peso del modelo frente al mercado sin vig
NFL_MODEL_WEIGHT_MEDIUM_DQ = 0.10  # con muestra corta o de la temporada anterior
NFL_TOTALS_ENABLED = False  # el modelo de totales resta frente al mercado; se evalúa pero no se apuesta
NFL_FIRST_PICK_WEEK = 3     # semanas 1–2 de temporada regular: ratings con datos del año anterior (3-8, ROI −52 %)
REGULAR_SEASON_TYPE = 2     # seasontype ESPN; la postemporada (3) reinicia la numeración de semanas
NFL_MAX_PROB      = 0.85   # techo: en la NFL nada es más seguro que esto
NFL_MIN_ODDS      = 1.30   # cuotas menores no dejan margen apostable
NFL_MAX_ODDS      = 3.50   # +250: en underdogs más largos el error del modelo se multiplica por la cuota
NFL_HIGH_DQ_GAMES = 4      # juegos mínimos por equipo para data_quality "high"
NFL_PREV_SEASON_WEIGHT = 0.5  # peso de cada juego de la temporada anterior
NFL_MIN_PICK_VALUE     = 0.0  # ningún pick sale con EV negativo, aunque tenga confianza alta
NFL_MAX_PICK_VALUE     = 0.15  # EV mayor en un mercado tan eficiente = info que el modelo no tiene

# Multiplicador de la masa de |margen| = k en la Normal(sd 13.5) discretizada. Aprox.
# histórica NFL: ~15 % de los juegos se deciden por 3, ~9 % por 7, ~5.5 % por 10,
# ~4.5 % por 14, contra ~5.8 / 5.2 / 4.5 / 3.5 % que da la Normal cerca de margen 0.
# Los empates finales (tras tiempo extra) son ~0.3 % contra ~3 % de la Normal.
# La masa extra sale de los márgenes vecinos (NFL_KEY_NEIGHBOURS), así la media y
# las colas de la Normal no se mueven.
NFL_KEY_MARGIN_WEIGHTS = {0: 0.1, 3: 2.6, 7: 1.75, 10: 1.2, 14: 1.3}
NFL_KEY_NEIGHBOURS = (-2, -1, 1, 2)  # ningún vecino es otro número clave
NFL_PMF_SPAN_SD = 6  # la distribución del margen se corta a ±6 SD
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
    → {pf, pa, games, previous_games, current}: games cuenta solo la temporada
    actual. current=False si la muestra incluye la anterior.
    """
    counts = {"games": len(scores), "previous_games": len(previous), "current": current_season}
    if not scores and not previous:
        return {"pf": league_avg, "pa": league_avg, **counts}
    weight = len(scores) + previous_weight * len(previous) + prior_games
    prior = league_avg * prior_games

    def weighted(i: int) -> float:
        own = sum(s[i] for s in scores) + previous_weight * sum(s[i] for s in previous)
        return (own + prior) / weight

    return {"pf": weighted(0), "pa": weighted(1), **counts}


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


def _outcomes(expected: float, threshold: float, sd: float) -> tuple[float, float, float]:
    """
    (P(x > threshold), P(x == threshold), P(x < threshold)) para un resultado
    entero x ~ Normal(expected, sd). Líneas .5 no tienen push; las enteras
    usan corrección de continuidad (±0.5) para la masa del empate.
    """
    if not float(threshold).is_integer():
        below = _normal_cdf((threshold - expected) / sd)
        return 1 - below, 0.0, below
    upper = _normal_cdf((threshold + 0.5 - expected) / sd)
    lower = _normal_cdf((threshold - 0.5 - expected) / sd)
    return 1 - upper, upper - lower, lower


def _reweight_key(pmf: dict[int, float], key: int, weight: float) -> dict[int, float]:
    """Nueva pmf con la masa de `key` × weight; la diferencia sale de sus vecinos."""
    neighbours = {key + d for d in NFL_KEY_NEIGHBOURS} & pmf.keys()
    pool = sum(pmf[k] for k in neighbours)
    if key not in pmf or pool <= 0:
        return pmf
    extra = (weight - 1) * pmf[key]

    def adjusted(k: int, mass: float) -> float:
        if k == key:
            return mass + extra
        return mass - extra * mass / pool if k in neighbours else mass

    return {k: adjusted(k, mass) for k, mass in pmf.items()}


def _margin_pmf(expected: float, sd: float = NFL_MARGIN_SD,
                key_weights: dict[int, float] = NFL_KEY_MARGIN_WEIGHTS) -> dict[int, float]:
    """P(margen = k) para k entero: Normal discretizada con números clave (±k), suma 1."""
    span = math.ceil(NFL_PMF_SPAN_SD * sd)
    pmf = {
        k: _normal_cdf((k + 0.5 - expected) / sd) - _normal_cdf((k - 0.5 - expected) / sd)
        for k in range(math.floor(expected) - span, math.ceil(expected) + span + 1)
    }
    for key, weight in key_weights.items():
        for signed_key in {key, -key}:
            pmf = _reweight_key(pmf, signed_key, weight)
    return pmf


def spread_outcomes(margin: float, home_line: float,
                    sd: float = NFL_MARGIN_SD) -> tuple[float, float, float]:
    """
    (local cubre, push, local no cubre). home_line negativa = local favorito.
    Usa la distribución con números clave, así que también líneas .5 que cruzan
    el 3 o el 7 (−2.5 vs −3.5) reflejan la masa histórica de esos márgenes.
    """
    threshold = -home_line
    pmf = _margin_pmf(margin, sd)
    win = sum(mass for k, mass in pmf.items() if k > threshold)
    push = sum(mass for k, mass in pmf.items() if k == threshold)
    return win, push, 1 - win - push


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


def _model_weight(data_quality: str) -> float:
    """Con datos flojos el modelo se acerca menos al mercado."""
    return NFL_MODEL_WEIGHT if data_quality == "high" else NFL_MODEL_WEIGHT_MEDIUM_DQ


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


def _best_value(*picks: dict) -> dict:
    """De los dos lados de un mercado, el de mayor EV (no el más probable)."""
    return max(picks, key=lambda p: p["value"])


def _moneyline_pick(game, margin, market, proj_txt, dq):
    home_odds, away_odds = market.get("home_ml"), market.get("away_ml")
    if not (home_odds and away_odds):
        return None
    p_home = blend(win_prob(margin), no_vig_pair(home_odds, away_odds)[0], _model_weight(dq))

    def side_pick(side: str, prob: float, odds: float) -> dict:
        return _build_pick("moneyline", side, f"Victoria {side}", prob, odds,
                           f"{proj_txt}. {side} gana en {_pct(prob)} de escenarios.", dq)

    return _best_value(side_pick(game["home"], p_home, home_odds),
                       side_pick(game["away"], 1 - p_home, away_odds))


def _spread_pick(game, margin, market, proj_txt, dq):
    line = market.get("home_spread")
    h_odds, a_odds = market.get("home_spread_odds"), market.get("away_spread_odds")
    if line is None or not (h_odds and a_odds):
        return None
    home_cover, push, home_miss = spread_outcomes(margin, line)
    model_p = home_cover / (home_cover + home_miss)
    p_home = blend(model_p, no_vig_pair(h_odds, a_odds)[0], _model_weight(dq))

    def side_pick(side: str, side_line: float, prob: float, odds: float) -> dict:
        return _build_pick("spread", side, f"Hándicap {side} {_fmt_line(side_line)}", prob, odds,
                           f"{proj_txt}. Mercado {game['home']} {_fmt_line(line)} "
                           f"→ {side} cubre en {_pct(prob)}{_push_txt(push)}.", dq, push)

    return _best_value(side_pick(game["home"], line, p_home, h_odds),
                       side_pick(game["away"], -line, 1 - p_home, a_odds))


def _total_pick(game, total, market, proj_txt, dq):
    line = market.get("total")
    o_odds, u_odds = market.get("over_odds"), market.get("under_odds")
    if line is None or not (o_odds and u_odds):
        return None
    p_over = blend(over_prob(total, line), no_vig_pair(o_odds, u_odds)[0], _model_weight(dq))
    _, push, _ = total_outcomes(total, line)

    def side_pick(label: str, prob: float, odds: float) -> dict:
        return _build_pick("total", label, f"{label} {line:.1f} Puntos", prob, odds,
                           f"{proj_txt}. Total proyectado {total:.1f} vs línea {line:.1f}"
                           f"{_push_txt(push)}.", dq, push)

    return _best_value(side_pick("Over", p_over, o_odds), side_pick("Under", 1 - p_over, u_odds))


def is_pickable_week(game: dict) -> bool:
    """False en las primeras semanas de temporada regular; sin dato de semana no se bloquea."""
    week = game.get("week")
    if week is None or game.get("season_type") != REGULAR_SEASON_TYPE:
        return True
    return week >= NFL_FIRST_PICK_WEEK


def _qualifies(pick: dict, min_conf: int, value_threshold: float) -> bool:
    """
    Cuota entre NFL_MIN_ODDS y NFL_MAX_ODDS y EV entre NFL_MIN_PICK_VALUE y
    NFL_MAX_PICK_VALUE (un edge mayor es sospechoso); luego confianza alta o edge suficiente.
    """
    if not NFL_MIN_ODDS < pick["est_odds"] <= NFL_MAX_ODDS:
        return False
    if not NFL_MIN_PICK_VALUE <= pick["value"] <= NFL_MAX_PICK_VALUE:
        return False
    return pick["confidence"] >= min_conf or pick["value"] >= value_threshold


def _drop_correlated(picks: list[dict]) -> list[dict]:
    """
    Una sola apuesta de lado por partido, la de más value: moneyline y spread del
    mismo equipo son casi la misma apuesta, y de equipos opuestos son una cobertura.
    """
    side_bets = [p for p in picks if p["market"] in SIDE_MARKETS]
    if not side_bets:
        return picks
    best = _best_value(*side_bets)
    return [p for p in picks if p["market"] not in SIDE_MARKETS or p is best]


def _data_quality(home_rating: dict, away_rating: dict) -> str:
    """"high" solo con muestra suficiente de la temporada en curso (la cuota real ya es obligatoria)."""
    min_games = min(home_rating["games"], away_rating["games"])
    both_current = home_rating.get("current", True) and away_rating.get("current", True)
    return "high" if both_current and min_games >= NFL_HIGH_DQ_GAMES else "medium"


def analyze_nfl_game(game: dict, home_rating: dict, away_rating: dict, league_avg: float,
                     min_conf: int = 62, value_threshold: float = 0.04) -> list[dict]:
    """
    Picks de un partido, solo de mercados con cuota real en game["lines"]: a lo
    sumo una apuesta de lado (moneyline|spread) y un total (si NFL_TOTALS_ENABLED). Entra si su EV ≥ 0 y
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
        _total_pick(game, total, market, proj_txt, dq) if NFL_TOTALS_ENABLED else None,
    ]
    picks = _drop_correlated([p for p in candidates if p and _qualifies(p, min_conf, value_threshold)])
    return sorted(picks, key=lambda p: p["confidence"], reverse=True)
