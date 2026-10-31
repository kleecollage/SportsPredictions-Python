"""Modelo NBA contra líneas reales de ESPN: proyección, mezcla con mercado, EV y dedup."""
import pytest

from value_bot import db
from value_bot.analysis import nba_model
from value_bot.analysis import nba_picks
from value_bot.analysis.market import no_vig_pair, normal_cdf
from value_bot.analysis.nba_model import (
    NBA_HFA, NBA_MARGIN_SD, NBA_MODEL_WEIGHT, NBA_MODEL_WEIGHT_MEDIUM_DQ,
    analyze_nba_game, data_quality, project_nba,
)
from value_bot.resolver import FinalScore, evaluate_pick
from value_bot.scrapers import espn

SEASON = "2026-27"
# Hornets (local) vs 76ers, DraftKings vía ESPN 31-oct-2026
LINES = {"home_ml": 2.1, "away_ml": 1.769, "home_spread": 2.5, "home_spread_odds": 1.87,
         "away_spread_odds": 1.952, "total": 211.5, "over_odds": 1.87, "under_odds": 1.952}
GAME = {"home": "Charlotte Hornets", "away": "Philadelphia 76ers", "event_id": "401", "lines": LINES}


def _stats(ppg, opp, season=SEASON, played=20):
    return {"ppg": ppg, "opp_ppg": opp, "season": season, "played": played}


def _analyze(h, a, splits=None, game=GAME, **kw):
    return analyze_nba_game(game, h, a, splits or {}, SEASON, **kw)


# ── Proyección ────────────────────────────────────────────────────

def test_projection_without_splits_adds_home_court():
    exp_h, exp_a, using = project_nba(_stats(110, 110), _stats(110, 110), {})
    assert (exp_h - exp_a, using) == (pytest.approx(NBA_HFA), False)


def test_projection_with_both_splits_uses_them_without_extra_home_court():
    splits = {"home": {"ppg": 116, "opp_ppg": 108}, "away": {"ppg": 106, "opp_ppg": 114}}
    exp_h, exp_a, using = project_nba(_stats(110, 110), _stats(110, 110), splits)
    assert using is True
    assert exp_h == pytest.approx((116 + 114) / 2)
    assert exp_a == pytest.approx((106 + 108) / 2)


def test_data_quality_is_medium_with_previous_season_or_short_sample():
    assert data_quality(_stats(110, 110), _stats(110, 110), SEASON) == "high"
    assert data_quality(_stats(110, 110, season="2025-26"), _stats(110, 110), SEASON) == "medium"
    assert data_quality(_stats(110, 110, played=6), _stats(110, 110), SEASON) == "medium"
    assert data_quality(_stats(110, 110), {"ppg": 110, "opp_ppg": 110}, SEASON) == "medium"


# ── Picks contra el mercado ───────────────────────────────────────

def test_no_market_lines_means_no_picks():
    assert _analyze(_stats(125, 100), _stats(100, 125), game={**GAME, "lines": {}}) == []


def test_moneyline_probability_blends_model_with_no_vig_market(monkeypatch):
    monkeypatch.setattr(nba_model, "NBA_MAX_PICK_VALUE", 9.0)
    monkeypatch.setattr(nba_model, "NBA_TOTALS_ENABLED", False)
    h, a = _stats(114, 110), _stats(110, 114)
    exp_h, exp_a, _ = project_nba(h, a, {})
    model_p = normal_cdf((exp_h - exp_a) / NBA_MARGIN_SD)
    market_p = no_vig_pair(LINES["home_ml"], LINES["away_ml"])[0]
    pick = nba_model._moneyline_pick(GAME, exp_h - exp_a, LINES, "Proyección", "high")
    assert pick["our_prob"] == pytest.approx(NBA_MODEL_WEIGHT * model_p + (1 - NBA_MODEL_WEIGHT) * market_p,
                                             abs=0.001)
    assert pick["est_odds"] == LINES["home_ml"] and pick["real_odds_source"] is True
    assert "Modelo" in pick["reasoning"] and "mercado" in pick["reasoning"]


def test_medium_data_quality_moves_less_from_the_market():
    h, a = _stats(114, 110, season="2025-26"), _stats(110, 114)
    exp_h, exp_a, _ = project_nba(h, a, {})
    model_p = normal_cdf((exp_h - exp_a) / NBA_MARGIN_SD)
    market_p = no_vig_pair(LINES["home_ml"], LINES["away_ml"])[0]
    pick = nba_model._moneyline_pick(GAME, exp_h - exp_a, LINES, "Proyección", "medium")
    expected = NBA_MODEL_WEIGHT_MEDIUM_DQ * model_p + (1 - NBA_MODEL_WEIGHT_MEDIUM_DQ) * market_p
    assert pick["our_prob"] == pytest.approx(expected, abs=0.001)


def test_side_is_chosen_by_expected_value_not_probability():
    # El modelo ve parejo un juego donde el visitante es favorito → el value está en el local
    pick = nba_model._moneyline_pick(GAME, 0.0, LINES, "Proyección", "high")
    assert pick["side"] == "Charlotte Hornets"
    assert pick["confidence"] < 50


def test_every_pick_has_real_odds_and_non_negative_value():
    for pick in _analyze(_stats(118, 104), _stats(104, 118)):
        assert pick["real_odds_source"] is True
        assert 0 <= pick["value"] <= nba_model.NBA_MAX_PICK_VALUE
        assert nba_model.NBA_MIN_ODDS < pick["est_odds"] <= nba_model.NBA_MAX_ODDS


def test_suspiciously_large_edge_is_dropped():
    # Un EV mayor al techo contra el mercado es información que el modelo no tiene
    pick = {"est_odds": 2.1, "confidence": 60, "value": nba_model.NBA_MAX_PICK_VALUE + 0.01}
    assert nba_model._qualifies(pick, min_conf=62, value_threshold=0.04) is False
    assert nba_model._qualifies({**pick, "value": 0.05}, min_conf=62, value_threshold=0.04) is True


def test_huge_model_disagreement_never_exceeds_the_value_cap():
    picks = _analyze(_stats(130, 100), _stats(100, 130), min_conf=0, value_threshold=0.0)
    assert all(p["value"] <= nba_model.NBA_MAX_PICK_VALUE for p in picks)


def test_at_most_one_side_bet_per_game(monkeypatch):
    monkeypatch.setattr(nba_model, "NBA_MAX_PICK_VALUE", 9.0)
    picks = _analyze(_stats(118, 104), _stats(104, 118), min_conf=0, value_threshold=0.0)
    assert sum(p["market"] in ("moneyline", "spread") for p in picks) == 1


def test_totals_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(nba_model, "NBA_MAX_PICK_VALUE", 9.0)
    monkeypatch.setattr(nba_model, "NBA_TOTALS_ENABLED", False)
    picks = _analyze(_stats(125, 120), _stats(125, 120), min_conf=0, value_threshold=0.0)
    assert all(p["market"] != "total" for p in picks)


@pytest.mark.parametrize("market", ["moneyline", "spread", "total"])
def test_pick_types_are_gradable_by_the_resolver(monkeypatch, market):
    monkeypatch.setattr(nba_model, "NBA_MAX_PICK_VALUE", 9.0)
    builder = {"moneyline": nba_model._moneyline_pick, "spread": nba_model._spread_pick}.get(market)
    pick = (builder(GAME, 3.0, LINES, "Proyección", "high") if builder
            else nba_model._total_pick(GAME, 220.0, LINES, "Proyección", "high"))
    score = FinalScore("401", GAME["home"], GAME["away"], 110, 104, (), ())
    assert evaluate_pick(pick["type"], score) in ("win", "loss", "void")


# ── ESPN trae event_id y líneas ───────────────────────────────────

def _espn_event():
    return {"id": "401", "date": "2026-10-31T23:00Z", "competitions": [{
        "status": {"type": {"name": "STATUS_SCHEDULED"}},
        "competitors": [
            {"homeAway": "home", "team": {"displayName": "Charlotte Hornets"}},
            {"homeAway": "away", "team": {"displayName": "Philadelphia 76ers"}},
        ],
        "odds": [{"moneyline": {"home": {"close": {"odds": "+110"}}, "away": {"close": {"odds": "-130"}}},
                  "total": {"over": {"close": {"line": "o211.5", "odds": "-115"}},
                            "under": {"close": {"odds": "-105"}}}}],
    }]}


def test_espn_nba_event_carries_event_id_and_lines():
    parsed = espn.espn_parse_nba(_espn_event())
    assert parsed["event_id"] == "401"
    assert parsed["lines"]["home_ml"] == pytest.approx(2.1)
    assert parsed["lines"]["total"] == 211.5


# ── picks.py: sin líneas no se piden stats; dedup por evento ─────

def test_game_without_lines_skips_stats_requests(monkeypatch):
    def boom(_):
        raise AssertionError("no debe pedir stats sin líneas")
    monkeypatch.setattr(nba_picks, "get_best_nba_stats", boom)
    assert nba_picks.analyze_nba_game({**GAME, "lines": {}}) == []


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "picks.db")
    db.init_db()


def _sent(market, event_id="401"):
    return {"sport": "nba", "league": "NBA", "home": GAME["home"], "away": GAME["away"],
            "type": "x", "market": market, "event_id": event_id, "confidence": 55,
            "est_odds": 2.1, "our_prob": 0.5, "data_quality": "high"}


def test_event_dedup_is_per_sport(temp_db):
    db.save_picks("2026-10-31", [_sent("moneyline")])
    assert db.already_sent_for_event("401", "moneyline", sport="nba") is True
    assert db.already_sent_for_event("401", "moneyline") is False  # NFL por defecto


def _patch_slate(monkeypatch, pick):
    monkeypatch.setattr(nba_picks, "get_nba_games", lambda: [GAME])
    monkeypatch.setattr(nba_picks, "get_best_nba_stats", lambda g: (_stats(1, 1), _stats(1, 1), {}, {}))
    monkeypatch.setattr(nba_picks, "analyze_nba_market", lambda *a, **k: [pick])


def test_side_bet_already_sent_blocks_the_other_side_market(temp_db, monkeypatch):
    db.save_picks("2026-10-30", [_sent("spread")])
    _patch_slate(monkeypatch, {"market": "moneyline", "side": GAME["home"], "type": "Victoria Charlotte Hornets",
                               "confidence": 55, "value": 0.05, "est_odds": 2.1})
    assert nba_picks.get_nba_picks() == []


def test_total_is_independent_of_side_bets(temp_db, monkeypatch):
    db.save_picks("2026-10-30", [_sent("spread")])
    _patch_slate(monkeypatch, {"market": "total", "side": "Over", "type": "Over 211.5 Puntos",
                               "confidence": 55, "value": 0.05, "est_odds": 1.87})
    picks = nba_picks.get_nba_picks()
    assert [p["type"] for p in picks] == ["Over 211.5 Puntos"]
    assert picks[0]["sport"] == "nba" and picks[0]["event_id"] == "401"
