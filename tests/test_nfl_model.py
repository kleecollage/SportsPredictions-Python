import pytest

from value_bot.analysis.nfl_model import (
    NFL_HFA,
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


# ── Conversión de cuotas ──────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("-110", 1.909), ("+140", 2.4), ("140", 2.4), ("-200", 1.5), ("EVEN", 2.0),
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
    assert rating == {"pf": LEAGUE_AVG, "pa": LEAGUE_AVG, "games": 0, "current": True}


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
    assert cover_prob(margin=3, home_line=-3) == pytest.approx(0.5)
    assert cover_prob(margin=7, home_line=-3) > 0.5
    assert cover_prob(margin=0, home_line=-3) < 0.5


def test_over_prob_is_half_when_projection_equals_line():
    assert over_prob(total=47, line=47) == pytest.approx(0.5)
    assert over_prob(total=50, line=47) > 0.5 > over_prob(total=44, line=47)


def test_blend_weights_model_and_market():
    assert blend(0.70, 0.50, model_weight=0.4) == pytest.approx(0.58)


# ── Generación de picks ───────────────────────────────────────────

def test_analyze_with_market_picks_home_moneyline_with_real_odds():
    picks = analyze_nfl_game(_game(FULL_MARKET), _rating(30, 17), _rating(16, 28), league_avg=LEAGUE_AVG)
    ml = next(p for p in picks if p["market"] == "moneyline")
    assert ml["type"] == "Victoria Kansas City Chiefs"
    assert ml["est_odds"] == 1.40
    assert ml["real_odds_source"] is True
    assert ml["value"] == pytest.approx(ml["our_prob"] * 1.40 - 1, abs=0.001)


def test_analyze_without_market_only_returns_model_moneyline():
    # Ventaja moderada (~6.5 pts): un favorito enorme quedaría bajo la cuota mínima
    picks = analyze_nfl_game(_game(), _rating(24, 21), _rating(21, 23), league_avg=LEAGUE_AVG)
    assert [p["market"] for p in picks] == ["moneyline"]
    assert picks[0]["data_quality"] == "medium"
    assert picks[0].get("real_odds_source") is not True


def test_analyze_even_matchup_without_value_returns_no_picks():
    even_market = {**FULL_MARKET, "home_ml": 1.91, "away_ml": 1.91, "home_spread": -1.5, "total": 45.5}
    avg = _rating(LEAGUE_AVG, LEAGUE_AVG)
    picks = analyze_nfl_game(_game(even_market), avg, avg, league_avg=LEAGUE_AVG)
    assert picks == []


def test_analyze_includes_value_pick_below_min_confidence():
    # El modelo proyecta 50 pts y el mercado pone 41.5: el Over no llega a 62%
    # de confianza tras mezclar con el mercado, pero sí tiene edge positivo.
    market = {**FULL_MARKET, "total": 41.5, "over_odds": 2.05, "under_odds": 1.80}
    picks = analyze_nfl_game(_game(market), _rating(24, 23), _rating(23, 24), league_avg=LEAGUE_AVG)
    over = next(p for p in picks if p["market"] == "total")
    assert over["type"].startswith("Over 41.5")
    assert over["confidence"] < 62
    assert over["value"] >= 0.04


def test_analyze_returns_at_most_three_picks_sorted_by_confidence():
    picks = analyze_nfl_game(_game(FULL_MARKET), _rating(32, 14), _rating(14, 32), league_avg=LEAGUE_AVG)
    assert 1 <= len(picks) <= 3
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
    win, push, lose = spread_outcomes(margin=3, home_line=-3)
    assert 0.02 < push < 0.05
    assert win == pytest.approx(lose)
    assert win + push + lose == pytest.approx(1.0)


def test_total_outcomes_integer_line_has_push_mass():
    over, push, under = total_outcomes(total=47, line=47)
    assert push > 0
    assert over == pytest.approx(under)


def test_spread_pick_value_accounts_for_push_refund():
    market = {**FULL_MARKET, "home_spread": -3.0, "home_spread_odds": 2.10, "away_spread_odds": 1.75}
    picks = analyze_nfl_game(_game(market), _rating(27, 20), _rating(20, 27), league_avg=LEAGUE_AVG)
    spread = next(p for p in picks if p["market"] == "spread")
    _, push, _ = spread_outcomes(margin=14 + 1.5, home_line=-3.0)
    expected = spread["our_prob"] * (1 - push) * 2.10 + push - 1
    assert spread["push_prob"] == pytest.approx(push, abs=0.001)
    assert spread["value"] == pytest.approx(expected, abs=0.002)


def test_pick_em_line_is_shown_as_pk():
    market = {**FULL_MARKET, "home_spread": 0.0, "home_spread_odds": 2.20, "away_spread_odds": 1.70}
    picks = analyze_nfl_game(_game(market), _rating(27, 20), _rating(20, 27), league_avg=LEAGUE_AVG)
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
