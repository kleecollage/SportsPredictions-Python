"""rank_picks: value bets primero, luego por confianza (orden común de NFL, NBA y fútbol)."""
from value_bot.analysis.market import rank_picks

THRESHOLD = 0.04


def _p(name, confidence, value):
    return {"type": name, "confidence": confidence, "value": value}


def test_value_bets_come_before_higher_confidence_picks():
    picks = [_p("conf", 70, 0.01), _p("value", 55, 0.05)]
    assert [p["type"] for p in rank_picks(picks, THRESHOLD)] == ["value", "conf"]


def test_ties_on_value_flag_are_ordered_by_confidence():
    picks = [_p("low", 56, 0.06), _p("high", 58, 0.05), _p("mid", 64, None)]
    assert [p["type"] for p in rank_picks(picks, THRESHOLD)] == ["high", "low", "mid"]


def test_missing_value_counts_as_no_value_bet():
    picks = [{"type": "none", "confidence": 80}, _p("value", 55, 0.04)]
    assert [p["type"] for p in rank_picks(picks, THRESHOLD)] == ["value", "none"]


def test_does_not_mutate_input():
    picks = [_p("a", 50, 0.0), _p("b", 60, 0.0)]
    rank_picks(picks, THRESHOLD)
    assert [p["type"] for p in picks] == ["a", "b"]
