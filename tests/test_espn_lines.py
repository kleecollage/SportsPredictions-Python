"""Líneas ESPN: los dos lados de cada mercado salen del mismo snapshot (close u open)."""
import pytest

from value_bot.scrapers.espn_lines import obj, parse_lines, spread_line, to_float


def _comp(ml=None, spread=None, total=None, **extra):
    odds = {"provider": {"displayName": "DK"}, "moneyline": ml or {}, "pointSpread": spread or {},
            "total": total or {}, **extra}
    return {"odds": [odds]}


# ── Un snapshot por mercado ───────────────────────────────────────

def test_spread_sides_never_mix_close_and_open():
    # Visitante sin cuota en close → antes tomaba la de open (+3.5) contra la línea close (−2.5)
    lines = parse_lines(_comp(spread={
        "home": {"close": {"line": "-2.5", "odds": "-110"}, "open": {"line": "-3.5", "odds": "-105"}},
        "away": {"close": {"line": "+2.5", "odds": None}, "open": {"line": "+3.5", "odds": "-115"}},
    }))
    assert lines["home_spread"] == -3.5
    assert (lines["home_spread_odds"], lines["away_spread_odds"]) == pytest.approx((1.952, 1.87), abs=0.001)


def test_spread_without_a_complete_snapshot_is_dropped():
    # Línea de close y cuota de open no son el mismo mercado
    lines = parse_lines(_comp(spread={
        "home": {"close": {"line": "+2.5"}, "open": {"odds": "-110"}},
        "away": {"close": {"line": "-2.5"}, "open": {"odds": "-110"}},
    }))
    assert not {"home_spread", "home_spread_odds", "away_spread_odds"} & lines.keys()


def test_spread_with_inconsistent_away_line_is_skipped():
    lines = parse_lines(_comp(spread={
        "home": {"close": {"line": "-2.5", "odds": "-110"}},
        "away": {"close": {"line": "+3.5", "odds": "-110"}},
    }))
    assert "home_spread" not in lines


def test_pick_em_spread_on_both_sides_is_consistent():
    lines = parse_lines(_comp(spread={
        "home": {"close": {"line": "PK", "odds": "-110"}},
        "away": {"close": {"line": "PK", "odds": "-110"}},
    }))
    assert lines["home_spread"] == 0.0


def test_total_sides_never_mix_close_and_open():
    lines = parse_lines(_comp(total={
        "over": {"close": {"line": "o220.5", "odds": None}, "open": {"line": "o218.5", "odds": "-110"}},
        "under": {"close": {"line": "u220.5", "odds": "-150"}, "open": {"line": "u218.5", "odds": "-110"}},
    }))
    assert (lines["total"], lines["over_odds"], lines["under_odds"]) == pytest.approx((218.5, 1.909, 1.909),
                                                                                      abs=0.001)


def test_total_with_inconsistent_under_line_is_skipped():
    lines = parse_lines(_comp(total={
        "over": {"close": {"line": "o220.5", "odds": "-110"}},
        "under": {"close": {"line": "u218.5", "odds": "-110"}},
    }))
    assert not {"total", "over_odds", "under_odds"} & lines.keys()


def test_over_under_fallback_only_goes_with_closing_odds():
    no_line = {"over": {"close": {"odds": "-110"}}, "under": {"close": {"odds": "-110"}}}
    assert parse_lines(_comp(total=no_line, overUnder=221.5))["total"] == 221.5
    open_only = {"over": {"open": {"odds": "-110"}}, "under": {"open": {"odds": "-110"}}}
    assert "total" not in parse_lines(_comp(total=open_only, overUnder=221.5))   # overUnder es el actual


def test_moneyline_sides_never_mix_close_and_open():
    lines = parse_lines(_comp(ml={
        "home": {"close": {"odds": "-200"}, "open": {"odds": "-150"}},
        "away": {"close": {"odds": None}, "open": {"odds": "+130"}},
    }))
    assert (lines["home_ml"], lines["away_ml"]) == pytest.approx((1.667, 2.3), abs=0.001)


def test_one_sided_moneyline_is_not_a_market():
    lines = parse_lines(_comp(ml={"home": None, "away": {"close": {"odds": "-112"}}}))
    assert not {"home_ml", "away_ml"} & lines.keys()


# ── Un close que no sirve no tapa a un open completo ──────────────

def test_total_falls_back_to_open_when_close_has_no_line():
    lines = parse_lines(_comp(total={
        "over": {"close": {"odds": "-110"}, "open": {"line": "o218.5", "odds": "-110"}},
        "under": {"close": {"odds": "-110"}, "open": {"line": "u218.5", "odds": "-105"}},
    }))
    assert (lines["total"], lines["over_odds"], lines["under_odds"]) == pytest.approx((218.5, 1.909, 1.952),
                                                                                      abs=0.001)


def test_moneyline_falls_back_to_open_when_close_odds_are_unusable():
    lines = parse_lines(_comp(ml={
        "home": {"close": {"odds": "OFF"}, "open": {"odds": "-150"}},
        "away": {"close": {"odds": "+130"}, "open": {"odds": "+120"}},
    }))
    assert (lines["home_ml"], lines["away_ml"]) == pytest.approx((1.667, 2.2), abs=0.001)


def test_moneyline_with_no_usable_snapshot_leaves_no_side():
    lines = parse_lines(_comp(ml={"home": {"close": {"odds": "OFF"}}, "away": {"close": {"odds": "+130"}}}))
    assert not {"home_ml", "away_ml"} & lines.keys()


def test_spread_falls_back_to_open_when_close_line_is_not_a_number():
    lines = parse_lines(_comp(spread={
        "home": {"close": {"line": "NaN", "odds": "-110"}, "open": {"line": "-3", "odds": "-110"}},
        "away": {"close": {"odds": "-110"}, "open": {"line": "+3", "odds": "-110"}},
    }))
    assert lines["home_spread"] == -3.0


def test_inconsistent_close_does_not_fall_back_to_open():
    # Líneas que no cuadran = datos raros de ESPN, no un snapshot incompleto
    lines = parse_lines(_comp(spread={
        "home": {"close": {"line": "-2.5", "odds": "-110"}, "open": {"line": "-3", "odds": "-110"}},
        "away": {"close": {"line": "+3.5", "odds": "-110"}, "open": {"line": "+3", "odds": "-110"}},
    }))
    assert "home_spread" not in lines


# ── Formas raras y números inválidos ──────────────────────────────

@pytest.mark.parametrize("odds", [{"x": 1}, "roto", None, [], [None]])
def test_unexpected_odds_shape_means_no_lines(odds):
    assert parse_lines({"odds": odds}) == {}


@pytest.mark.parametrize("raw", ["NaN", "inf", "-inf", float("nan")])
def test_non_finite_numbers_are_rejected(raw):
    assert to_float(raw) is None and spread_line(raw) is None


def test_non_finite_total_line_is_not_a_market():
    lines = parse_lines(_comp(total={"over": {"close": {"line": "NaN", "odds": "-110"}},
                                     "under": {"close": {"odds": "-110"}}}))
    assert "total" not in lines


def test_obj_returns_dict_or_empty():
    assert obj({"a": {"b": 1}}, "a") == {"b": 1}
    assert obj({"a": None}, "a") == {} and obj(None, "a") == {}
