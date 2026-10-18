"""Deduplicación de picks NFL entre corridas (HIGH): por event_id + mercado, no por día."""
import pytest

from value_bot import db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "picks.db")
    db.init_db()


def _pick(event_id="401", market="spread", pick_type="Hándicap San Francisco 49ers -7.5",
          sport="nfl", **extra):
    return {**extra, "sport": sport, "league": "NFL", "home": "San Francisco 49ers",
            "away": "Washington Commanders", "type": pick_type, "market": market,
            "event_id": event_id, "confidence": 56, "est_odds": 1.91, "our_prob": 0.56,
            "data_quality": "high"}


def _check(event_id="401", market="spread"):
    return db.already_sent_for_event(event_id, market)


def test_pick_sent_on_a_previous_day_blocks_same_event_and_market(temp_db):
    db.save_picks("2026-10-16", [_pick()])
    assert _check() is True


def test_line_movement_does_not_create_a_new_pick(temp_db):
    db.save_picks("2026-10-16", [_pick(pick_type="Hándicap San Francisco 49ers -7.5")])
    # El domingo la línea es -8: mismo partido y mercado → bloqueado
    assert _check(market="spread") is True


def test_other_market_of_same_event_is_allowed(temp_db):
    db.save_picks("2026-10-16", [_pick(market="spread")])
    assert _check(market="total") is False


def test_other_event_with_same_teams_is_allowed(temp_db):
    db.save_picks("2026-10-16", [_pick(event_id="401")])
    assert _check(event_id="999") is False


def test_picks_from_other_sports_do_not_interfere(temp_db):
    db.save_picks("2026-10-16", [_pick(sport="nba")])
    assert _check() is False


# ── Apuesta del mismo lado ya enviada en otro mercado (code review M1) ─

SF = "San Francisco 49ers"


def test_spread_sent_blocks_moneyline_of_same_team(temp_db):
    db.save_picks("2026-10-16", [_pick(side=SF)])
    assert db.already_sent_side_bet("401", SF) is True


def test_side_bet_on_other_team_is_allowed(temp_db):
    db.save_picks("2026-10-16", [_pick(side=SF)])
    assert db.already_sent_side_bet("401", "Washington Commanders") is False


@pytest.mark.parametrize("market, pick_type", [
    ("moneyline", "Victoria San Francisco 49ers"),
    ("spread", "Hándicap San Francisco 49ers -7.5"),
])
def test_legacy_rows_without_side_use_pick_type(temp_db, market, pick_type):
    db.save_picks("2026-10-16", [_pick(market=market, pick_type=pick_type)])
    assert db.already_sent_side_bet("401", SF) is True


def test_totals_and_other_events_do_not_count_as_side_bets(temp_db):
    db.save_picks("2026-10-16", [_pick(market="total", pick_type="Under 47.5 Puntos", side="Under"),
                                 _pick(event_id="999", side=SF)])
    assert db.already_sent_side_bet("401", SF) is False
