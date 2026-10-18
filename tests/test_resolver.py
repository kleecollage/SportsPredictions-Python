"""Resolución de picks NBA/NFL contra marcadores finales de ESPN."""
import sqlite3
from datetime import datetime, timezone

import pytest

from value_bot import db, resolver
from value_bot.config import ESPN_NBA_URL, ESPN_NFL_URL
from value_bot.resolver import FinalScore, evaluate_pick

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def _score(home="New York Knicks", away="Philadelphia 76ers", hs=110, as_=100,
           home_periods=(30, 25, 30, 25), away_periods=(20, 30, 25, 25), event_id="1"):
    return FinalScore(event_id, home, away, hs, as_, tuple(home_periods), tuple(away_periods))


def _eval(pick_type, **kw):
    return evaluate_pick(pick_type, _score(**kw))


# ── evaluate_pick (puro) ──────────────────────────────────────────

@pytest.mark.parametrize("pick_type,expected", [
    ("Over 205.5 Puntos", "win"),
    ("Over 215.8 Puntos", "loss"),
    ("Under 211.1 Puntos", "win"),
    ("Under 205.5 Puntos", "loss"),
    ("Over 210.0 Puntos", "void"),
    ("Under 210.0 Puntos", "void"),
    ("Over 1.5 Goles", "win"),
])
def test_game_totals(pick_type, expected):
    assert _eval(pick_type) == expected


@pytest.mark.parametrize("pick_type,expected", [
    ("Victoria New York Knicks", "win"),
    ("Victoria New York Knicks (Local)", "win"),
    ("Victoria Philadelphia 76ers", "loss"),
])
def test_moneyline(pick_type, expected):
    assert _eval(pick_type) == expected


def test_moneyline_tie_is_void():
    assert _eval("Victoria New York Knicks", hs=20, as_=20) == "void"


def test_moneyline_unknown_team_is_unresolved():
    assert _eval("Victoria Boston Celtics") is None


@pytest.mark.parametrize("pick_type,hs,as_,expected", [
    ("Hándicap New York Knicks -6.5", 110, 100, "win"),
    ("Hándicap New York Knicks -6.5", 105, 100, "loss"),
    # el favorito visitante usa SU margen, no el del local
    ("Hándicap Philadelphia 76ers -3.5", 100, 105, "win"),
    ("Hándicap Philadelphia 76ers -3.5", 100, 102, "loss"),
    ("Hándicap Philadelphia 76ers +3.5", 102, 100, "win"),
    ("Hándicap Philadelphia 76ers +3.5", 104, 100, "loss"),
    ("Hándicap New York Knicks -7.0", 107, 100, "void"),
    ("Hándicap New York Knicks PK", 100, 100, "void"),
    ("Hándicap New York Knicks PK", 101, 100, "win"),
])
def test_spread(pick_type, hs, as_, expected):
    assert _eval(pick_type, hs=hs, as_=as_) == expected


def test_first_half_uses_first_two_periods():
    # 1ª mitad = 30+25+20+30 = 105; el total del partido (210) no cuenta
    assert _eval("1ª Mitad Over 101.4") == "win"
    assert _eval("1ª Mitad Over 106.9") == "loss"


def test_first_half_without_period_data_is_unresolved():
    assert _eval("1ª Mitad Over 101.4", home_periods=(), away_periods=()) is None


def test_team_total_uses_that_teams_score():
    assert _eval("Total Knicks Over 105.0") == "win"     # Knicks 110
    assert _eval("Total 76ers Over 105.0") == "loss"     # 76ers 100, total 210 no cuenta


@pytest.mark.parametrize("pick_type", [
    "Ganador Serie: New York Knicks", "Ambos Marcan (BTTS)", "Algo raro",
])
def test_unsupported_markets_are_unresolved(pick_type):
    assert _eval(pick_type) is None


# ── resolve_pending_picks (BD temporal + ESPN simulado) ──────────

@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "picks.db")
    db.init_db()


@pytest.fixture
def espn(monkeypatch):
    """Scoreboards simulados por (url, fecha); registra las consultas."""
    boards, calls = {}, []

    def fake_get(url, params=None, **_):
        calls.append((url, params["dates"]))
        return {"events": boards.get((url, params["dates"]), [])}

    monkeypatch.setattr(resolver, "http_get", fake_get)
    return boards, calls


def _event(event_id, home, away, hs, as_, status="STATUS_FINAL", periods=None):
    hp, ap = periods or ([], [])
    return {"id": event_id, "competitions": [{
        "status": {"type": {"name": status}},
        "competitors": [
            {"homeAway": "home", "team": {"displayName": home}, "score": str(hs),
             "linescores": [{"value": v} for v in hp]},
            {"homeAway": "away", "team": {"displayName": away}, "score": str(as_),
             "linescores": [{"value": v} for v in ap]},
        ]}]}


def _nba(home, away, pick_type, kickoff="2026-10-21T01:30Z", est_odds=1.91):
    return {"sport": "nba", "league": "NBA", "home": home, "away": away, "type": pick_type,
            "confidence": 60, "est_odds": est_odds, "our_prob": 0.6, "data_quality": "medium",
            "kickoff": kickoff}


def _nfl(pick_type, market, event_id="401", home="Los Angeles Rams", away="Buffalo Bills",
         kickoff="2026-10-13T00:15Z", est_odds=1.91):
    return {"sport": "nfl", "league": "NFL", "home": home, "away": away, "type": pick_type,
            "market": market, "event_id": event_id, "push_prob": 0.05, "confidence": 56,
            "est_odds": est_odds, "our_prob": 0.56, "data_quality": "high", "kickoff": kickoff}


def _results():
    with sqlite3.connect(db.DB_PATH) as conn:
        return {pt: (o, s, p) for pt, o, s, p in conn.execute("""
            SELECT p.pick_type, r.outcome, r.actual_score, r.profit_units
            FROM results r JOIN picks p ON p.id = r.pick_id""")}


def test_resolves_by_game_date_in_eastern_time_not_run_date(temp_db, espn):
    boards, calls = espn
    # Pick guardado el 18; juego el 20 a las 21:30 ET (= 21 a las 01:30 UTC)
    db.save_picks("2026-10-18", [_nba("San Antonio Spurs", "Oklahoma City Thunder",
                                      "Hándicap Oklahoma City Thunder -3.5")])
    boards[(ESPN_NBA_URL, "20261020")] = [
        _event("9", "San Antonio Spurs", "Oklahoma City Thunder", 100, 110)]

    assert resolver.resolve_pending_picks(NOW) == 1

    assert calls == [(ESPN_NBA_URL, "20261020")]
    assert _results() == {"Hándicap Oklahoma City Thunder -3.5": ("win", "100-110", 0.91)}


def test_same_city_teams_do_not_collide(temp_db, espn):
    boards, _ = espn
    db.save_picks("2026-10-18", [_nba("Los Angeles Lakers", "Golden State Warriors",
                                      "Victoria Los Angeles Lakers")])
    boards[(ESPN_NBA_URL, "20261020")] = [
        _event("1", "Los Angeles Clippers", "Golden State Warriors", 120, 100),
        _event("2", "Los Angeles Lakers", "Golden State Warriors", 90, 100),
    ]

    resolver.resolve_pending_picks(NOW)

    assert _results()["Victoria Los Angeles Lakers"] == ("loss", "90-100", -1.0)


def test_first_half_pick_resolved_with_period_scores(temp_db, espn):
    boards, _ = espn
    db.save_picks("2026-10-18", [_nba("Detroit Pistons", "Boston Celtics",
                                      "1ª Mitad Over 101.4", kickoff="2026-10-20T23:00Z")])
    boards[(ESPN_NBA_URL, "20261020")] = [_event(
        "3", "Detroit Pistons", "Boston Celtics", 105, 100,
        periods=([25, 25, 30, 25], [25, 20, 30, 25]))]

    resolver.resolve_pending_picks(NOW)

    assert _results()["1ª Mitad Over 101.4"][0] == "loss"   # 1ª mitad = 95


def test_games_not_final_or_in_the_future_stay_pending(temp_db, espn):
    boards, calls = espn
    db.save_picks("2026-10-18", [
        _nba("Detroit Pistons", "Boston Celtics", "Over 200.5 Puntos",
             kickoff="2026-10-20T23:00Z"),
        _nba("New York Knicks", "Philadelphia 76ers", "Over 200.5 Puntos",
             kickoff="2026-10-25T23:00Z"),
    ])
    boards[(ESPN_NBA_URL, "20261020")] = [
        _event("3", "Detroit Pistons", "Boston Celtics", 50, 40, status="STATUS_IN_PROGRESS")]

    assert resolver.resolve_pending_picks(NOW) == 0

    assert _results() == {}
    assert calls == [(ESPN_NBA_URL, "20261020")]   # el juego futuro ni se consulta


def test_picks_without_iso_kickoff_are_skipped(temp_db, espn):
    _, calls = espn
    db.save_picks("2026-06-17", [_nba("San Antonio Spurs", "New York Knicks",
                                      "Over 220.5 Puntos", kickoff="05.06. 20:30")])

    assert resolver.resolve_pending_picks(NOW) == 0
    assert calls == []


def test_resolved_picks_are_not_queried_again(temp_db, espn):
    boards, calls = espn
    db.save_picks("2026-10-18", [_nba("Detroit Pistons", "Boston Celtics",
                                      "Over 200.5 Puntos", kickoff="2026-10-20T23:00Z")])
    boards[(ESPN_NBA_URL, "20261020")] = [_event("3", "Detroit Pistons", "Boston Celtics",
                                                  105, 100)]
    resolver.resolve_pending_picks(NOW)
    calls.clear()

    assert resolver.resolve_pending_picks(NOW) == 0
    assert calls == []


def test_one_request_per_sport_and_date(temp_db, espn):
    _, calls = espn
    db.save_picks("2026-10-18", [
        _nba("Detroit Pistons", "Boston Celtics", "Over 200.5 Puntos",
             kickoff="2026-10-20T23:00Z"),
        _nba("Detroit Pistons", "Boston Celtics", "Under 230.5 Puntos",
             kickoff="2026-10-20T23:00Z"),
    ])

    resolver.resolve_pending_picks(NOW)

    assert calls == [(ESPN_NBA_URL, "20261020")]


def test_nfl_matches_by_event_id_and_handles_push(temp_db, espn):
    boards, calls = espn
    db.save_picks("2026-10-11", [
        _nfl("Hándicap Buffalo Bills -3.0", "spread"),          # 37-34 → push
        _nfl("Victoria Buffalo Bills", "moneyline", est_odds=1.67),
        _nfl("Over 70.5 Puntos", "total"),
    ])
    boards[(ESPN_NFL_URL, "20261012")] = [
        # Mismo partido con otro nombre: manda el event_id
        _event("401", "LA Rams", "Buffalo Bills", 34, 37),
    ]

    assert resolver.resolve_pending_picks(NOW) == 3

    assert calls == [(ESPN_NFL_URL, "20261012")]
    assert _results() == {
        "Hándicap Buffalo Bills -3.0": ("void", "34-37", 0.0),
        "Victoria Buffalo Bills": ("win", "34-37", 0.67),
        "Over 70.5 Puntos": ("win", "34-37", 0.91),
    }


def test_nfl_other_event_with_same_teams_is_not_used(temp_db, espn):
    boards, _ = espn
    db.save_picks("2026-10-11", [_nfl("Victoria Buffalo Bills", "moneyline", event_id="401")])
    boards[(ESPN_NFL_URL, "20261012")] = [
        _event("999", "Los Angeles Rams", "Buffalo Bills", 34, 37)]

    assert resolver.resolve_pending_picks(NOW) == 0


def test_empty_espn_response_resolves_nothing(temp_db, espn):
    db.save_picks("2026-10-18", [_nba("Detroit Pistons", "Boston Celtics",
                                      "Over 200.5 Puntos", kickoff="2026-10-20T23:00Z")])

    assert resolver.resolve_pending_picks(NOW) == 0


def test_games_older_than_lookback_are_not_queried(temp_db, espn):
    _, calls = espn
    db.save_picks("2026-03-30", [_nba("New York Knicks", "San Antonio Spurs",
                                      "Over 222.6 Puntos", kickoff="2026-04-01T00:30Z")])

    resolver.resolve_pending_picks(NOW)

    assert calls == []
