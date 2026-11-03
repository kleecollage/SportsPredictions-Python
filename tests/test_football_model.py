"""Modelo de fútbol (puro): totales y 1X2 anclados al mercado, un pick por mercado y partido."""
import pytest

from value_bot.analysis import football_model as fm

FIX = {"home": "Home FC", "away": "Away FC", "league": "Bundesliga"}


def _stats(gf=1.5, ga=1.5, win=40, draw=25, form="WDLWD", form_pts=8, played=10):
    return {"goals_for_pg": gf, "goals_against_pg": ga, "win_rate": win, "draw_rate": draw,
            "form": form, "form_pts": form_pts, "played": played}


def _types(picks):
    return [p["type"] for p in picks]


def _analyze(hs, as_, league="Bundesliga", real=None):
    return fm.analyze_football_match({**FIX, "league": league}, hs, as_, real)


# ── Totales: línea real y mercado sin vig ────────────────────────

HIGH_XG = (_stats(gf=2.2, ga=1.8), _stats(gf=2.0, ga=2.0))     # XG 2.10 + 1.90 = 4.0
LOW_XG = (_stats(gf=0.9, ga=0.9), _stats(gf=0.9, ga=0.9))      # XG 1.8


def _totals(line, over, under):
    return {"total_line": line, "over_odds": over, "under_odds": under}


def _total(hs, as_, real):
    return fm._total_picks(fm.build_match(FIX, hs, as_, real))


def test_total_outcomes_split_push_only_on_integer_lines():
    over, push, under = fm.total_outcomes(2.0, 2.5)
    assert push == 0 and over + under == pytest.approx(1)
    over, push, under = fm.total_outcomes(2.0, 3.0)
    assert push == pytest.approx(fm.poisson_p(2.0, 3)) and over + push + under == pytest.approx(1)


def test_over_blends_model_with_no_vig_market_at_the_real_line():
    (pick,) = _total(*HIGH_XG, _totals(3.5, 3.0, 1.40))
    model = fm.total_outcomes(4.0, 3.5)[0]
    market = (1 / 3.0) / (1 / 3.0 + 1 / 1.40)
    blended = round(0.15 * model + 0.85 * market, 3)
    assert pick["type"] == "Over 3.5 Goles" and (pick["est_odds"], pick["real_odds_source"]) == (3.0, True)
    assert (pick["our_prob"], pick["confidence"]) == (blended, int(blended * 100))
    assert pick["value"] == pytest.approx(blended * 3.0 - 1, abs=1e-4)
    assert pick["reasoning"].startswith("Modelo 56% · mercado 31% → ")


def test_side_with_more_ev_wins_even_if_less_likely():
    (pick,) = _total(*LOW_XG, _totals(2.5, 1.75, 2.1))
    assert pick["type"] == "Under 2.5 Goles" and pick["confidence"] < 50 and pick["value"] > 0


def test_integer_line_prices_the_push():
    (pick,) = _total(_stats(gf=1.5, ga=1.5), _stats(gf=1.5, ga=1.5), _totals(3.0, 1.6, 2.45))
    push = fm.poisson_p(3.0, 3)
    assert pick["type"] == "Under 3.0 Goles" and pick["push_prob"] == pytest.approx(push, abs=1e-4)
    assert pick["value"] == pytest.approx(pick["our_prob"] * (1 - push) * 2.45 + push - 1, abs=1e-3)


@pytest.mark.parametrize("real", [None, {"home_odds": 2.0}, _totals(2.5, None, 1.9), _totals(None, 1.9, 1.9)])
def test_without_real_totals_there_is_no_total_pick(real):
    assert _total(*LOW_XG, real) == []


def test_total_needs_enough_games_from_both_teams():
    hs, as_ = LOW_XG
    assert _total({**hs, "played": 3}, as_, _totals(2.5, 1.75, 2.1)) == []


def test_total_with_odds_above_the_maximum_is_dropped():
    assert _total(*HIGH_XG, _totals(3.5, 3.8, 1.25)) == []


def test_total_with_too_much_value_is_dropped():
    # Modelo 73 % Under 2.5 contra un mercado de 34 %: EV > FOOTBALL_MAX_PICK_VALUE
    assert _total(*LOW_XG, _totals(2.5, 1.45, 2.9)) == []


# ── 1X2 y empate: anclados al mercado sin vig ─────────────────────

STRONG, WEAK = _stats(win=80, form_pts=15), _stats(win=10, form_pts=0)
# Villarreal–PSG del 3-nov-2026: PSG 3-0-0 en Champions, modelo 74 %, mercado ~62 %
PSG_MARKET = {"home_odds": 5.5, "draw_odds": 4.4, "away_odds": 1.52}


def _side(picks):
    return [p for p in picks if p["type"].startswith(("Victoria", "Empate"))]


def test_away_favourite_priced_by_the_market_is_not_a_value_bet():
    # 0.15 × 74 % + 0.85 × 61.7 % = 63.5 % → EV −3.5 % a cuota 1.52
    assert _side(_analyze(WEAK, STRONG, real=PSG_MARKET)) == []


def test_side_pick_blends_model_with_no_vig_market_and_keeps_real_odds():
    market = {"home_odds": 4.0, "draw_odds": 3.6, "away_odds": 2.0}
    (pick,) = _side(_analyze(WEAK, STRONG, real=market))
    no_vig = (1 / 2.0) / (1 / 4.0 + 1 / 3.6 + 1 / 2.0)
    blended = round(0.15 * 0.74 + 0.85 * no_vig, 3)
    assert pick["type"] == "Victoria Away FC"
    assert (pick["our_prob"], pick["confidence"]) == (blended, int(blended * 100))
    assert (pick["est_odds"], pick["real_odds_source"]) == (2.0, True)
    assert pick["value"] == pytest.approx(blended * 2.0 - 1, abs=1e-4)
    assert pick["reasoning"].startswith("Modelo 74% · mercado 48% → ")


def test_value_bet_below_the_confidence_floor_is_kept():
    market = {"home_odds": 4.0, "draw_odds": 3.6, "away_odds": 2.0}
    (pick,) = _side(_analyze(WEAK, STRONG, real=market))
    assert pick["confidence"] < fm.MIN_CONFIDENCE and pick["value"] >= fm.VALUE_THRESHOLD


def test_side_pick_needs_the_three_market_prices():
    assert _side(_analyze(WEAK, STRONG, real={"away_odds": 2.0, "home_odds": 4.0})) == []
    assert _side(_analyze(WEAK, STRONG)) == []


@pytest.mark.parametrize("played", [(3, 10), (10, 3)])
def test_side_pick_needs_enough_games_from_both_teams(played):
    # 3 partidos de fase de liga: un 100 % de victorias no dice casi nada
    market = {"home_odds": 4.0, "draw_odds": 3.6, "away_odds": 2.0}
    hs, as_ = {**WEAK, "played": played[0]}, {**STRONG, "played": played[1]}
    assert _side(_analyze(hs, as_, real=market)) == []


def test_side_pick_with_too_much_value_is_dropped():
    # Un edge enorme contra el mercado suele ser información que el modelo no tiene
    market = {"home_odds": 8.0, "draw_odds": 5.0, "away_odds": 2.6}
    assert _side(_analyze(WEAK, STRONG, real=market)) == []


def test_draw_is_anchored_to_the_market_too():
    even = dict(win=30, form_pts=7, draw=60)
    market = {"home_odds": 2.6, "draw_odds": 3.0, "away_odds": 2.9}
    (draw,) = _side(_analyze(_stats(**even), _stats(**even), league="LaLiga", real=market))
    no_vig = (1 / 3.0) / (1 / 2.6 + 1 / 3.0 + 1 / 2.9)
    assert draw["type"] == "Empate (X)"
    assert draw["our_prob"] == round(0.15 * 0.62 + 0.85 * no_vig, 3) and draw["est_odds"] == 3.0
    usual = dict(win=30, form_pts=7, draw=30)
    assert _side(_analyze(_stats(**usual), _stats(**usual), league="LaLiga", real=market)) == []


def test_only_one_result_bet_per_match():
    a = {"type": "Victoria Home FC", "value": 0.05}
    b = {"type": "Empate (X)", "value": 0.07}
    assert fm._best([a, None, b]) == [b] and fm._best([None]) == []


# ── Partido ───────────────────────────────────────────────────────

def test_totals_are_off_by_default():
    real = _totals(2.5, 1.75, 2.1)
    assert fm.FOOTBALL_TOTALS_ENABLED is False
    assert _analyze(*LOW_XG, real=real) == [] and _total(*LOW_XG, real) != []


def test_match_gives_at_most_one_total_and_one_result_sorted_by_value(monkeypatch):
    monkeypatch.setattr(fm, "FOOTBALL_TOTALS_ENABLED", True)
    real = {**_totals(2.5, 1.75, 2.1), "home_odds": 4.0, "draw_odds": 3.6, "away_odds": 2.0}
    low = {"goals_for_pg": 0.9, "goals_against_pg": 0.9}
    picks = _analyze({**WEAK, **low}, {**STRONG, **low}, real=real)
    assert sorted(_types(picks)) == ["Under 2.5 Goles", "Victoria Away FC"]
    values = [p["value"] for p in picks]
    assert values == sorted(values, reverse=True)
    assert all(p["confidence"] >= fm.MIN_CONFIDENCE or p["value"] >= fm.VALUE_THRESHOLD for p in picks)


def test_model_does_not_mutate_inputs():
    hs, as_, real = _stats(), _stats(), {"home_odds": 2.0}
    before = (dict(hs), dict(as_), dict(real))
    _analyze(hs, as_, real=real)
    assert (hs, as_, real) == before
