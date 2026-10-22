import pytest

from value_bot.analysis import nfl_model
from value_bot.analysis.nfl_model import (
    NFL_FIRST_PICK_WEEK,
    NFL_HFA,
    NFL_MAX_ODDS,
    NFL_MAX_PICK_VALUE,
    NFL_MODEL_WEIGHT,
    NFL_MODEL_WEIGHT_MEDIUM_DQ,
    _drop_correlated,
    _margin_pmf,
    NFL_PREV_SEASON_WEIGHT,
    american_to_decimal,
    analyze_nfl_game,
    blend,
    cover_prob,
    no_vig_pair,
    over_prob,
    project_game,
    spread_outcomes,
    team_rating,
    total_outcomes,
    is_pickable_week,
    win_prob,
)

LEAGUE_AVG = 22.0


def _rating(pf, pa, games=6, current=True):
    return {"pf": pf, "pa": pa, "games": games, "current": current}


def _game(market=None):
    return {
        "sport": "nfl", "home": "Kansas City Chiefs", "away": "Las Vegas Raiders",
        "home_record": "5-1", "away_record": "1-5", "kickoff": "2026-10-25T20:25Z",
        "lines": market or {},
    }


FULL_MARKET = {
    "provider": "Draft Kings",
    "home_ml": 1.40, "away_ml": 3.10,
    "home_spread": -6.5, "home_spread_odds": 1.91, "away_spread_odds": 1.91,
    "total": 44.5, "over_odds": 1.91, "under_odds": 1.91,
}


@pytest.fixture
def legacy_weights(monkeypatch):
    """
    Peso del modelo previo al backtest 2026 (0.4 / 0.25). Los escenarios marcados se
    armaron con él para que haya edge; prueban selección de lado, push, formato y
    filtros, no el peso (que tiene sus propios tests).
    """
    monkeypatch.setattr(nfl_model, "NFL_MODEL_WEIGHT", 0.4)
    monkeypatch.setattr(nfl_model, "NFL_MODEL_WEIGHT_MEDIUM_DQ", 0.25)


# ── Conversión de cuotas ──────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("-110", 1.909), ("+140", 2.4), ("140", 2.4), ("-200", 1.5), ("EVEN", 2.0), ("EV", 2.0),
])
def test_american_to_decimal_converts_valid_odds(raw, expected):
    assert american_to_decimal(raw) == pytest.approx(expected, abs=0.001)


@pytest.mark.parametrize("raw", [None, "", "abc", "0"])
def test_american_to_decimal_returns_none_for_invalid_odds(raw):
    assert american_to_decimal(raw) is None


def test_no_vig_pair_removes_bookmaker_margin():
    p1, p2 = no_vig_pair(1.909, 1.909)
    assert p1 == pytest.approx(0.5)
    assert p1 + p2 == pytest.approx(1.0)


def test_no_vig_pair_favours_shorter_odds():
    p_fav, p_dog = no_vig_pair(1.40, 3.10)
    assert p_fav > 0.65 and p_dog < 0.35
    assert p_fav + p_dog == pytest.approx(1.0)


# ── Ratings y proyección ──────────────────────────────────────────

def test_team_rating_regresses_toward_league_average():
    rating = team_rating([(30, 10), (20, 20)], league_avg=LEAGUE_AVG, prior_games=3)
    assert rating["pf"] == pytest.approx((50 + 3 * LEAGUE_AVG) / 5)
    assert rating["pa"] == pytest.approx((30 + 3 * LEAGUE_AVG) / 5)
    assert rating["games"] == 2


def test_team_rating_without_games_is_league_average():
    rating = team_rating([], league_avg=LEAGUE_AVG)
    assert rating == {"pf": LEAGUE_AVG, "pa": LEAGUE_AVG, "games": 0, "previous_games": 0,
                      "current": True}


def test_team_rating_flags_previous_season_data():
    rating = team_rating([(24, 20)], league_avg=LEAGUE_AVG, current_season=False)
    assert rating["current"] is False


def test_project_game_equal_teams_only_home_field_advantage():
    avg = _rating(LEAGUE_AVG, LEAGUE_AVG)
    exp_h, exp_a = project_game(avg, avg, league_avg=LEAGUE_AVG)
    assert exp_h - exp_a == pytest.approx(NFL_HFA)
    assert exp_h + exp_a == pytest.approx(2 * LEAGUE_AVG)


def test_project_game_strong_offense_vs_weak_defense_scores_more():
    strong = _rating(30, 15)
    weak = _rating(15, 30)
    exp_h, exp_a = project_game(strong, weak, league_avg=LEAGUE_AVG)
    assert exp_h > 30 and exp_a < 15


# ── Probabilidades ────────────────────────────────────────────────

def test_win_prob_is_half_for_zero_margin_and_increases():
    assert win_prob(0) == pytest.approx(0.5)
    assert win_prob(13.5) == pytest.approx(0.841, abs=0.002)
    assert win_prob(-3) < 0.5 < win_prob(3)


def test_cover_prob_is_half_when_margin_equals_line():
    assert cover_prob(margin=3, home_line=-3) == pytest.approx(0.5, abs=0.01)
    assert cover_prob(margin=7, home_line=-3) > 0.5
    assert cover_prob(margin=0, home_line=-3) < 0.5


def test_over_prob_is_half_when_projection_equals_line():
    assert over_prob(total=47, line=47) == pytest.approx(0.5)
    assert over_prob(total=50, line=47) > 0.5 > over_prob(total=44, line=47)


def test_blend_weights_model_and_market():
    assert blend(0.70, 0.50, model_weight=0.4) == pytest.approx(0.58)


# ── Generación de picks ───────────────────────────────────────────

def test_analyze_with_market_picks_home_moneyline_with_real_odds():
    # Sin spread: con él, el hándicap del mismo equipo desplazaría al ML (más value)
    no_spread = {k: v for k, v in FULL_MARKET.items() if "spread" not in k}
    picks = analyze_nfl_game(_game(no_spread), _rating(30, 17), _rating(16, 28), league_avg=LEAGUE_AVG)
    ml = next(p for p in picks if p["market"] == "moneyline")
    assert ml["type"] == "Victoria Kansas City Chiefs"
    assert ml["est_odds"] == 1.40
    assert ml["real_odds_source"] is True
    assert ml["value"] == pytest.approx(ml["our_prob"] * 1.40 - 1, abs=0.001)


def test_analyze_without_market_returns_no_picks():
    # Sin cuota real no hay contra qué medir el edge (review #4)
    picks = analyze_nfl_game(_game(), _rating(30, 17), _rating(16, 28), league_avg=LEAGUE_AVG)
    assert picks == []


def test_analyze_even_matchup_without_value_returns_no_picks():
    even_market = {**FULL_MARKET, "home_ml": 1.91, "away_ml": 1.91, "home_spread": -1.5, "total": 45.5}
    avg = _rating(LEAGUE_AVG, LEAGUE_AVG)
    picks = analyze_nfl_game(_game(even_market), avg, avg, league_avg=LEAGUE_AVG)
    assert picks == []


@pytest.mark.usefixtures("legacy_weights")
def test_analyze_includes_value_pick_below_min_confidence(totals_enabled):
    # El modelo proyecta 50 pts y el mercado pone 44.5: el Over no llega a 62%
    # de confianza tras mezclar con el mercado, pero sí tiene edge positivo.
    market = {**FULL_MARKET, "total": 44.5, "over_odds": 2.05, "under_odds": 1.80}
    picks = analyze_nfl_game(_game(market), _rating(24, 23), _rating(23, 24), league_avg=LEAGUE_AVG)
    over = next(p for p in picks if p["market"] == "total")
    assert over["type"].startswith("Over 44.5")
    assert over["confidence"] < 62
    assert over["value"] >= 0.04


def test_analyze_returns_at_most_one_side_bet_and_one_total_sorted_by_confidence():
    picks = analyze_nfl_game(_game(FULL_MARKET), _rating(32, 14), _rating(14, 32), league_avg=LEAGUE_AVG)
    assert 1 <= len(picks) <= 2
    confs = [p["confidence"] for p in picks]
    assert confs == sorted(confs, reverse=True)


def test_pick_market_key_does_not_clash_with_game_lines():
    game = _game(FULL_MARKET)
    picks = analyze_nfl_game(game, _rating(30, 17), _rating(16, 28), league_avg=LEAGUE_AVG)
    merged = {**game, **picks[0]}
    assert merged["lines"] == FULL_MARKET


# ── Push en líneas enteras (#5) ───────────────────────────────────

def test_spread_outcomes_half_point_line_has_no_push():
    win, push, lose = spread_outcomes(margin=3, home_line=-3.5)
    assert push == 0
    assert win + lose == pytest.approx(1.0)


def test_spread_outcomes_integer_line_has_push_mass():
    # El 4 cede masa al 3 (número clave): queda por debajo de la Normal (~2.9 %)
    win, push, lose = spread_outcomes(margin=4, home_line=-4)
    assert 0.015 < push < 0.03
    assert win == pytest.approx(lose, abs=0.04)
    assert win + push + lose == pytest.approx(1.0)


def test_total_outcomes_integer_line_has_push_mass():
    over, push, under = total_outcomes(total=47, line=47)
    assert push > 0
    assert over == pytest.approx(under)


@pytest.mark.usefixtures("legacy_weights")
def test_spread_pick_value_accounts_for_push_refund():
    market = {**FULL_MARKET, "home_spread": -3.0, "home_spread_odds": 2.10, "away_spread_odds": 1.75}
    picks = analyze_nfl_game(_game(market), _rating(24, 21), _rating(21, 24), league_avg=LEAGUE_AVG)
    spread = next(p for p in picks if p["market"] == "spread")
    _, push, _ = spread_outcomes(margin=6 + 1.5, home_line=-3.0)
    expected = spread["our_prob"] * (1 - push) * 2.10 + push - 1
    assert spread["push_prob"] == pytest.approx(push, abs=0.001)
    assert spread["value"] == pytest.approx(expected, abs=0.002)


@pytest.mark.usefixtures("legacy_weights")
def test_pick_em_line_is_shown_as_pk():
    market = {**FULL_MARKET, "home_spread": 0.0, "home_spread_odds": 2.20, "away_spread_odds": 1.70}
    picks = analyze_nfl_game(_game(market), _rating(23, 22), _rating(22, 23), league_avg=LEAGUE_AVG)
    spread = next(p for p in picks if p["market"] == "spread")
    assert spread["type"] == "Hándicap Kansas City Chiefs PK"


# ── data_quality (#4) ─────────────────────────────────────────────

def test_data_quality_high_only_with_market_and_current_season():
    picks = analyze_nfl_game(_game(FULL_MARKET), _rating(30, 17), _rating(16, 28), league_avg=LEAGUE_AVG)
    assert all(p["data_quality"] == "high" for p in picks)


def test_data_quality_medium_when_ratings_come_from_previous_season():
    picks = analyze_nfl_game(_game(FULL_MARKET), _rating(30, 17, games=17, current=False),
                             _rating(16, 28), league_avg=LEAGUE_AVG)
    assert picks and all(p["data_quality"] == "medium" for p in picks)


# ── Review nfl_model: EV, sede neutral, números clave, correlación ─

def test_confidence_pick_with_negative_value_is_rejected():
    # Mercado: favorito −250 (70 % sin vig); modelo: equipos parejos.
    # La mezcla da ~63 % al favorito pero EV ≈ −12 %: el favorito no debe salir (review #1).
    market = {"home_ml": american_to_decimal("-250"), "away_ml": american_to_decimal("+210")}
    even = _rating(LEAGUE_AVG, LEAGUE_AVG)
    picks = analyze_nfl_game(_game(market), even, even, league_avg=LEAGUE_AVG)
    assert all(p["side"] != "Kansas City Chiefs" and p["value"] >= 0 for p in picks)


def test_every_pick_has_real_odds_and_non_negative_value():
    picks = analyze_nfl_game(_game(FULL_MARKET), _rating(27, 20), _rating(20, 27), league_avg=LEAGUE_AVG)
    assert picks
    assert all(p["real_odds_source"] is True and p["value"] >= 0 for p in picks)


@pytest.mark.usefixtures("legacy_weights")
def test_neutral_site_removes_home_field_advantage():
    even = _rating(LEAGUE_AVG, LEAGUE_AVG)
    market = {**FULL_MARKET, "home_ml": 1.80, "away_ml": 2.05}
    home = analyze_nfl_game(_game(market), even, even, league_avg=LEAGUE_AVG)
    neutral = analyze_nfl_game({**_game(market), "neutral_site": True}, even, even, league_avg=LEAGUE_AVG)
    assert any("margen +1.5" in p["reasoning"] for p in home)
    assert all("margen +0.0" in p["reasoning"] and "sede neutral" in p["reasoning"] for p in neutral)


def test_spread_push_on_key_number_three_reflects_nfl_history():
    _, push, _ = spread_outcomes(margin=3, home_line=-3)
    assert 0.07 < push < 0.10


def test_spread_push_on_key_number_seven_is_above_normal():
    _, push_seven, _ = spread_outcomes(margin=7, home_line=-7)
    _, push_eight, _ = spread_outcomes(margin=8, home_line=-8)
    assert push_seven > 1.5 * push_eight


def test_key_number_distribution_sums_to_one_and_stays_near_even():
    win, push, lose = spread_outcomes(margin=5, home_line=-3)
    assert win + push + lose == pytest.approx(1.0)
    assert cover_prob(margin=3, home_line=-3) == pytest.approx(0.5, abs=0.01)


def test_key_numbers_do_not_shift_projected_margin():
    # La masa extra sale de los vecinos: la media del margen no se corre hacia 0
    pmf = _margin_pmf(8.0)
    assert sum(pmf.values()) == pytest.approx(1.0)
    assert sum(k * mass for k, mass in pmf.items()) == pytest.approx(8.0, abs=0.1)
    assert min(pmf.values()) >= 0


def test_total_push_ignores_spread_key_numbers():
    # Un total entero no usa los pesos de margen (3, 7…), solo la Normal
    _, push, _ = total_outcomes(total=47, line=47)
    assert 0.02 < push < 0.04


def test_spread_line_without_odds_gives_no_spread_pick():
    market = {k: v for k, v in FULL_MARKET.items() if k not in ("home_spread_odds", "away_spread_odds")}
    picks = analyze_nfl_game(_game(market), _rating(32, 14), _rating(14, 32), league_avg=LEAGUE_AVG)
    assert "spread" not in [p["market"] for p in picks]


def test_provider_only_market_gives_no_picks():
    picks = analyze_nfl_game(_game({"provider": "Draft Kings"}), _rating(32, 14), _rating(14, 32),
                             league_avg=LEAGUE_AVG)
    assert picks == []


@pytest.mark.usefixtures("legacy_weights")
def test_moneyline_and_spread_on_same_team_keep_only_best_value():
    # Margen proyectado +13.5: ML a 1.40 (EV ~5 %) y −6.5 a 1.91 (EV ~11 %) califican ambos
    picks = analyze_nfl_game(_game(FULL_MARKET), _rating(25, 19), _rating(19, 25), league_avg=LEAGUE_AVG)
    kc = [p for p in picks if p["market"] in ("moneyline", "spread") and "Kansas City" in p["type"]]
    assert len(kc) == 1
    assert kc[0]["market"] == "spread"  # −6.5 a 1.91 tiene más value que ML a 1.40


def test_team_rating_weights_previous_season_less():
    rating = team_rating([(30, 10)], league_avg=LEAGUE_AVG, prior_games=3,
                         current_season=False, previous=[(10, 30)] * 4)
    w = NFL_PREV_SEASON_WEIGHT
    assert rating["pf"] == pytest.approx((30 + w * 40 + 3 * LEAGUE_AVG) / (1 + w * 4 + 3))
    assert rating["pa"] == pytest.approx((10 + w * 120 + 3 * LEAGUE_AVG) / (1 + w * 4 + 3))
    assert rating["games"] == 1 and rating["previous_games"] == 4
    assert rating["current"] is False


# ── Code review: PK sin empates, lados opuestos ───────────────────

def test_pick_em_push_reflects_rare_nfl_ties():
    # Un empate tras tiempo extra es ~0.3 % de los juegos, no el ~3 % de la Normal
    win, push, lose = spread_outcomes(margin=0, home_line=0)
    assert push < 0.01
    assert win == pytest.approx(lose)


def _side_pick(market, side, value):
    return {"market": market, "side": side, "value": value, "confidence": 60}


def test_drop_correlated_keeps_one_side_bet_even_on_opposite_teams():
    # ML de un equipo + spread del rival es una cobertura (apuesta a un middle), no dos picks
    picks = [_side_pick("moneyline", "Kansas City Chiefs", 0.05),
             _side_pick("spread", "Las Vegas Raiders", 0.06)]
    assert _drop_correlated(picks) == [picks[1]]


def test_drop_correlated_never_touches_totals():
    picks = [_side_pick("moneyline", "Kansas City Chiefs", 0.05),
             _side_pick("spread", "Kansas City Chiefs", 0.08),
             _side_pick("total", "Over", 0.01)]
    assert [p["market"] for p in _drop_correlated(picks)] == ["spread", "total"]


# ── Review nfl_model 2: lado por EV, números clave .5, peso por calidad ─

@pytest.mark.usefixtures("legacy_weights")
def test_moneyline_picks_underdog_when_value_is_on_underdog():
    # Mercado: local −200 / visitante +170; el modelo ve el juego casi parejo.
    # La mezcla aún da > 50 % al local, pero el EV positivo está en el visitante.
    market = {"home_ml": 1.50, "away_ml": 2.70}
    even = _rating(LEAGUE_AVG, LEAGUE_AVG)
    picks = analyze_nfl_game(_game(market), even, even, league_avg=LEAGUE_AVG)
    ml = next(p for p in picks if p["market"] == "moneyline")
    assert ml["side"] == "Las Vegas Raiders"
    assert ml["est_odds"] == 2.70
    assert ml["value"] >= 0.04


@pytest.mark.usefixtures("legacy_weights")
def test_spread_picks_side_with_better_ev_under_skewed_juice():
    # Juice desigual: el local tiene > 50 % mezclado pero cuota 1.67 → EV negativo
    market = {"home_spread": -3.5, "home_spread_odds": 1.67, "away_spread_odds": 2.25}
    even = _rating(LEAGUE_AVG, LEAGUE_AVG)
    picks = analyze_nfl_game(_game(market), even, even, league_avg=LEAGUE_AVG)
    spread = next(p for p in picks if p["market"] == "spread")
    assert spread["side"] == "Las Vegas Raiders"
    assert spread["type"] == "Hándicap Las Vegas Raiders +3.5"
    assert spread["est_odds"] == 2.25


def test_half_point_line_crossing_three_captures_key_number_mass():
    # −2.5 vs −3.5 difiere justo en "margen = 3" (~7.5 % por lado en la NFL; la Normal da ~3 %)
    gap = cover_prob(margin=3, home_line=-2.5) - cover_prob(margin=3, home_line=-3.5)
    assert gap > 0.06


def test_half_point_line_crossing_non_key_number_stays_small():
    gap = cover_prob(margin=5, home_line=-4.5) - cover_prob(margin=5, home_line=-5.5)
    assert gap < 0.03


@pytest.mark.usefixtures("legacy_weights")
def test_model_weight_is_lower_with_medium_data_quality(totals_enabled):
    # Proyección 50 vs línea 41.5 con cuotas parejas: el modelo se aleja del 50 % del mercado
    market = {"total": 41.5, "over_odds": 1.91, "under_odds": 1.91}
    current = analyze_nfl_game(_game(market), _rating(23.5, 23.5), _rating(23.5, 23.5), league_avg=LEAGUE_AVG)
    stale = analyze_nfl_game(_game(market), _rating(23.5, 23.5, current=False), _rating(23.5, 23.5),
                             league_avg=LEAGUE_AVG)
    model_p = over_prob(total=50, line=41.5)
    assert current[0]["our_prob"] == pytest.approx(blend(model_p, 0.5, nfl_model.NFL_MODEL_WEIGHT), abs=0.001)
    assert stale[0]["our_prob"] == pytest.approx(blend(model_p, 0.5, nfl_model.NFL_MODEL_WEIGHT_MEDIUM_DQ),
                                                 abs=0.001)


# ── Longshots y techo de edge (code review HIGH) ──────────────────
# El error del modelo se multiplica por la cuota: con ratings parejos frente a un
# mercado muy desigual, el underdog largo salía con EV de +50 % o más.

@pytest.mark.parametrize("dog_odds, fav_odds", [(5.0, 1.22), (8.0, 1.11)])
def test_long_underdog_is_never_picked(dog_odds, fav_odds):
    market = {"home_ml": fav_odds, "away_ml": dog_odds}
    even = _rating(LEAGUE_AVG, LEAGUE_AVG)
    assert analyze_nfl_game(_game(market), even, even, league_avg=LEAGUE_AVG) == []


@pytest.mark.usefixtures("legacy_weights")
def test_underdog_within_max_odds_and_moderate_edge_is_picked():
    market = {"home_ml": 1.40, "away_ml": 3.10}
    even = _rating(LEAGUE_AVG, LEAGUE_AVG)
    ml = analyze_nfl_game(_game(market), even, even, league_avg=LEAGUE_AVG)[0]
    assert ml["side"] == "Las Vegas Raiders"
    assert ml["est_odds"] <= NFL_MAX_ODDS
    assert 0.04 <= ml["value"] <= NFL_MAX_PICK_VALUE


@pytest.mark.usefixtures("legacy_weights")
def test_suspicious_edge_is_rejected_even_at_short_odds(totals_enabled):
    # Proyección 56 vs línea 41.5: el modelo ve un Over "seguro" a 1.91 (EV > 20 %).
    # Un desacuerdo así con el mercado NFL indica información que el modelo no tiene.
    market = {"total": 41.5, "over_odds": 1.91, "under_odds": 1.91}
    high_scoring = _rating(26.5, 26.5)
    assert analyze_nfl_game(_game(market), high_scoring, high_scoring, league_avg=LEAGUE_AVG) == []


@pytest.mark.usefixtures("legacy_weights")
def test_underdog_beyond_max_odds_is_rejected_even_with_moderate_edge(monkeypatch):
    # +300 con EV ~12 %: dentro del techo de edge, solo lo frena NFL_MAX_ODDS
    market = {"home_ml": 1.25, "away_ml": 4.00}
    args = (_game(market), _rating(23, 21), _rating(21, 23))
    assert analyze_nfl_game(*args, league_avg=LEAGUE_AVG) == []

    monkeypatch.setattr(nfl_model, "NFL_MAX_ODDS", 99.0)
    ml = analyze_nfl_game(*args, league_avg=LEAGUE_AVG)[0]
    assert ml["side"] == "Las Vegas Raiders"
    assert 0.04 <= ml["value"] <= NFL_MAX_PICK_VALUE


@pytest.mark.usefixtures("legacy_weights")
def test_underdog_at_exactly_max_odds_is_allowed():
    market = {"home_ml": 1.32, "away_ml": NFL_MAX_ODDS}
    picks = analyze_nfl_game(_game(market), _rating(22.5, 21.5), _rating(21.5, 22.5),
                             league_avg=LEAGUE_AVG)
    assert [(p["side"], p["est_odds"]) for p in picks] == [("Las Vegas Raiders", NFL_MAX_ODDS)]


# ── Backtest temporada 2026 (91 juegos, cierre DraftKings) ────────
# Totales: el lado del modelo acertó 34/91 (37 %) y su Brier fue peor que el mercado.
# ML/spread: el peso del modelo que minimiza Brier fue 0.15, no 0.4.
# Semanas 1–2 (ratings con datos de la temporada anterior): 3-8, ROI −52 %.

@pytest.fixture
def totals_enabled(monkeypatch):
    monkeypatch.setattr(nfl_model, "NFL_TOTALS_ENABLED", True)


def test_totals_are_not_picked_while_disabled(monkeypatch):
    market = {"total": 41.5, "over_odds": 1.91, "under_odds": 1.91}
    high_scoring = _rating(26, 26)
    assert analyze_nfl_game(_game(market), high_scoring, high_scoring, league_avg=LEAGUE_AVG) == []

    monkeypatch.setattr(nfl_model, "NFL_TOTALS_ENABLED", True)
    picks = analyze_nfl_game(_game(market), high_scoring, high_scoring, league_avg=LEAGUE_AVG)
    assert [p["market"] for p in picks] == ["total"]


def test_model_weight_comes_from_backtest_optimum():
    assert NFL_MODEL_WEIGHT == pytest.approx(0.15)
    assert 0 < NFL_MODEL_WEIGHT_MEDIUM_DQ < NFL_MODEL_WEIGHT


def test_moneyline_probability_leans_mostly_on_market():
    # Modelo: local muy superior; mercado: parejo. La mezcla queda cerca del 50 %.
    market = {"home_ml": 1.91, "away_ml": 1.91}
    picks = analyze_nfl_game(_game(market), _rating(30, 16), _rating(16, 30), league_avg=LEAGUE_AVG)
    ml = next(p for p in picks if p["market"] == "moneyline")
    margin = 2 * (30 - 16) + NFL_HFA
    assert ml["our_prob"] == pytest.approx(blend(win_prob(margin), 0.5, 0.15), abs=0.001)


@pytest.mark.parametrize("week, season_type, expected", [
    (1, 2, False), (2, 2, False), (NFL_FIRST_PICK_WEEK, 2, True), (10, 2, True),
    (1, 3, True),        # wild card: la postemporada reinicia la numeración
    (None, None, True),  # sin dato de semana no se bloquea
])
def test_is_pickable_week_skips_first_regular_season_weeks(week, season_type, expected):
    assert is_pickable_week({"week": week, "season_type": season_type}) is expected


# ── Texto del razonamiento: modelo vs mercado ─────────────────────

RAIDERS_RAMS = {"home": "Las Vegas Raiders", "away": "Los Angeles Rams"}


def test_moneyline_reasoning_shows_model_market_and_blend():
    # Caso real 22-oct-2026: el modelo da a LV ganador (+1.8) pero el mercado lo tiene +180.
    market = {"home_ml": 2.8, "away_ml": 1.459}
    pick = nfl_model._moneyline_pick(RAIDERS_RAMS, 1.8, market, "Proyección", "high")
    assert pick["side"] == "Las Vegas Raiders"
    assert "Modelo 55% · mercado 34% → Las Vegas Raiders gana en 37%" in pick["reasoning"]


def test_spread_reasoning_shows_model_and_market_for_the_picked_side():
    market = {"home_spread": 3.5, "home_spread_odds": 1.952, "away_spread_odds": 1.87}
    pick = nfl_model._spread_pick(RAIDERS_RAMS, 1.8, market, "Proyección", "high")
    side_is_home = pick["side"] == "Las Vegas Raiders"
    model_p = cover_prob(1.8, 3.5) if side_is_home else 1 - cover_prob(1.8, 3.5)
    assert f"Modelo {int(model_p * 100)}% · mercado " in pick["reasoning"]
    assert f"{pick['side']} cubre en {pick['confidence']}%" in pick["reasoning"]


def test_total_reasoning_shows_model_and_market():
    market = {"total": 48.5, "over_odds": 1.926, "under_odds": 1.893}
    pick = nfl_model._total_pick(RAIDERS_RAMS, 51.0, market, "Proyección", "high")
    assert "Total proyectado 51.0 vs línea 48.5" in pick["reasoning"]
    assert "Modelo " in pick["reasoning"] and " · mercado " in pick["reasoning"]
