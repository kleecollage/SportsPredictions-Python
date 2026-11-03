"""Modelo de fútbol (puro): mercados por estilo de liga, cuotas reales y topes por partido."""
import itertools

import pytest

from value_bot.analysis import football_model as fm
from value_bot.config import est_odds

FIX = {"home": "Home FC", "away": "Away FC", "league": "Bundesliga"}


def _stats(gf=1.5, ga=1.5, win=40, draw=25, form="WDLWD", form_pts=8, played=10):
    return {"goals_for_pg": gf, "goals_against_pg": ga, "win_rate": win, "draw_rate": draw,
            "form": form, "form_pts": form_pts, "played": played}


def _types(picks):
    return [p["type"] for p in picks]


def _analyze(hs, as_, league="Bundesliga", real=None):
    return fm.analyze_football_match({**FIX, "league": league}, hs, as_, real)


# ── Goles ─────────────────────────────────────────────────────────

# Con cuota estimada 0.95/p, Over/Under/BTTS solo pasan conf ≥ 62 y cuota > 1.30 entre ~64 % y ~73 %
HIGH_XG = (_stats(gf=1.8, ga=1.7), _stats(gf=1.6, ga=1.8))     # XG 1.80 + 1.65


def test_attacking_league_with_high_xg_picks_over_not_under():
    picks = _analyze(*HIGH_XG)
    assert "Over 2.5 Goles" in _types(picks) and "Under 2.5 Goles" not in _types(picks)


def test_defensive_league_with_low_xg_picks_under():
    picks = _analyze(_stats(gf=1.0, ga=1.0), _stats(gf=1.0, ga=1.0), league="Serie A")   # XG 2.0
    under = next(p for p in picks if p["type"] == "Under 2.5 Goles")
    assert "Over 2.5 Goles" not in _types(picks)
    assert under["value"] is None and under["est_odds"] == est_odds(1 - fm.poisson_over(2.0, 2))


def test_very_likely_goal_markets_are_dropped_by_their_estimated_odds():
    # P(Over 2.5) ≈ 0.80 → cuota estimada 1.19 < 1.30
    assert "Over 2.5 Goles" not in _types(_analyze(_stats(gf=2.5, ga=1.5), _stats(gf=2.0, ga=1.8)))


def test_btts_is_not_added_next_to_an_under():
    m = fm.build_match(FIX, _stats(gf=2.5, ga=2.5), _stats(gf=2.5, ga=2.5), None)
    under = {"type": "Under 2.5 Goles"}
    assert fm._btts_pick(m, []) and fm._btts_pick(m, [under]) == []


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
    assert fm._best_result([a, b]) == [b] and fm._best_result([]) == []


# ── Mercados que hoy nunca salen ──────────────────────────────────

MODERATE_GAP = (_stats(win=40, form_pts=7), _stats(win=40, form_pts=2))   # diff de fuerza 22.5


def test_double_chance_estimated_odds_are_below_the_minimum():
    (dc,) = fm._double_chance_pick(fm.build_match(FIX, *MODERATE_GAP, None))
    assert dc["type"] == "Doble Chance Home FC (1X)" and dc["est_odds"] < fm.FOOTBALL_MIN_ODDS
    assert not any(t.startswith("Doble Chance") for t in _types(_analyze(*MODERATE_GAP)))


@pytest.mark.parametrize("league", ["Bundesliga", "Serie A", "LaLiga", "Otra liga"])
def test_over_15_never_survives_the_confidence_floor(league):
    # Para llegar al 62 % necesita XG ≥ 3.0, y ahí siempre sale antes un Over 2.5
    goals = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0)
    for gf_h, ga_h, gf_a, ga_a in itertools.product(goals, repeat=4):
        picks = _analyze(_stats(gf=gf_h, ga=ga_h), _stats(gf=gf_a, ga=ga_a), league=league)
        assert "Over 1.5 Goles" not in _types(picks)


# ── Topes y orden ─────────────────────────────────────────────────

def test_at_most_three_picks_sorted_by_confidence():
    hs, as_ = _stats(gf=1.8, ga=1.7, win=80, form_pts=15), _stats(gf=1.6, ga=1.8, win=10, form_pts=0)
    picks = _analyze(hs, as_, real={"home_odds": 2.1, "draw_odds": 3.5, "away_odds": 3.6})
    assert set(_types(picks)) == {"Over 2.5 Goles", "Ambos Marcan (BTTS)", "Victoria Home FC"}
    confidences = [p["confidence"] for p in picks]
    assert len(picks) == fm.MAX_PICKS_MATCH and confidences == sorted(confidences, reverse=True)
    assert all(p["est_odds"] > fm.FOOTBALL_MIN_ODDS for p in picks)
    assert all(p["confidence"] >= fm.MIN_CONFIDENCE or p["value"] >= fm.VALUE_THRESHOLD for p in picks)


def test_model_does_not_mutate_inputs():
    hs, as_, real = _stats(), _stats(), {"home_odds": 2.0}
    before = (dict(hs), dict(as_), dict(real))
    _analyze(hs, as_, real=real)
    assert (hs, as_, real) == before
