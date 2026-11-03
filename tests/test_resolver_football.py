"""Resolución de picks de fútbol con marcadores de football-data.org (90 minutos)."""
from datetime import datetime, timezone

import pytest

from value_bot import db, resolver
from value_bot.resolver import FinalScore, evaluate_pick


def _match(match_id, status="FINISHED", home=2, away=1, duration="REGULAR", regular=None,
           home_name="Arsenal FC", away_name="Chelsea FC"):
    score = {"duration": duration, "fullTime": {"home": home, "away": away}}
    if regular:
        score["regularTime"] = {"home": regular[0], "away": regular[1]}
    return {"id": match_id, "status": status, "homeTeam": {"name": home_name},
            "awayTeam": {"name": away_name}, "score": score}


# ── Parsing (puro) ────────────────────────────────────────────────

def test_finished_matches_become_final_scores_with_fd_ids():
    finals, unplayed = resolver.parse_fd_results({"matches": [_match(7), _match(8, status="TIMED")]})
    assert finals == [FinalScore("fd:7", "Arsenal FC", "Chelsea FC", 2, 1, (), ())] and unplayed == []


def test_extra_time_matches_settle_on_regular_time():
    # Los mercados se liquidan a los 90': un 1-1 que se gana en prórroga es empate
    data = {"matches": [_match(7, home=2, away=1, duration="EXTRA_TIME", regular=(1, 1))]}
    (final,), _ = resolver.parse_fd_results(data)
    assert (final.home_score, final.away_score) == (1, 1)


def test_extra_time_without_regular_score_is_not_resolved():
    data = {"matches": [_match(7, duration="PENALTY_SHOOTOUT")]}
    assert resolver.parse_fd_results(data) == ([], [])


@pytest.mark.parametrize("status, label", [("POSTPONED", "pospuesto"), ("CANCELLED", "cancelado")])
def test_postponed_and_cancelled_matches_are_unplayed(status, label):
    _, (game,) = resolver.parse_fd_results({"matches": [_match(9, status=status)]})
    assert (game.event_id, game.status) == ("fd:9", label)


@pytest.mark.parametrize("data", [None, {}, {"matches": None}, {"matches": [None, {"id": None}]},
                                  {"matches": [{**_match(7), "score": None}]}])
def test_odd_responses_give_nothing(data):
    assert resolver.parse_fd_results(data) == ([], [])


def test_windows_never_exceed_the_fd_range():
    dates = ["2026-11-01", "2026-11-05", "2026-11-10", "2026-11-11", "2026-12-01"]
    assert resolver.fd_windows(dates) == [("2026-11-01", "2026-11-10"), ("2026-11-11", "2026-11-11"),
                                          ("2026-12-01", "2026-12-01")]


# ── Mercados de fútbol ────────────────────────────────────────────

DRAW = FinalScore("fd:1", "Arsenal FC", "Chelsea FC", 1, 1, (), ())
NIL = FinalScore("fd:1", "Arsenal FC", "Chelsea FC", 2, 0, (), ())


@pytest.mark.parametrize("pick_type, score, outcome", [
    ("Empate (X)", DRAW, "win"), ("Empate (X)", NIL, "loss"),
    ("Ambos Marcan (BTTS)", DRAW, "win"), ("Ambos Marcan (BTTS)", NIL, "loss"),
    ("Over 2.5 Goles", NIL, "loss"), ("Under 3.0 Goles", DRAW, "win"), ("Over 2.0 Goles", NIL, "void"),
    ("Victoria Arsenal FC", NIL, "win"),
])
def test_football_markets(pick_type, score, outcome):
    assert evaluate_pick(pick_type, score, draw_loses=True) == outcome


def test_a_draw_loses_a_football_win_bet_but_pushes_a_moneyline():
    assert evaluate_pick("Victoria Arsenal FC", DRAW, draw_loses=True) == "loss"
    assert evaluate_pick("Victoria Arsenal FC", DRAW) == "void"


# ── Resolución con la BD ──────────────────────────────────────────

@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "picks.db")
    db.init_db()


def _save(pick_type, event_id="fd:7", kickoff="2026-11-01T15:00:00Z", source=None):
    pick = {**({"source": source} if source else {}), "sport": "football", "league": "Premier League", "home": "Arsenal FC", "away": "Chelsea FC",
            "type": pick_type, "confidence": 55, "est_odds": 2.0, "our_prob": 0.55, "data_quality": "high",
            "kickoff": kickoff, **({"event_id": event_id} if event_id else {})}
    db.save_picks("2026-11-01", [pick])


@pytest.fixture
def fd_api(monkeypatch):
    calls = []

    def fake(path, params=None):
        calls.append((path, params))
        return {"matches": [_match(7, home=1, away=1)]}
    monkeypatch.setattr(resolver, "fd_get", fake)
    monkeypatch.setattr(resolver, "fd_enabled", lambda: True)
    monkeypatch.setattr(resolver, "http_get", lambda *a, **k: pytest.fail("ESPN no se consulta"))
    return calls


NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def test_football_picks_are_resolved_with_one_fd_call_per_window(temp_db, fd_api):
    _save("Empate (X)")
    _save("Victoria Arsenal FC")
    assert resolver.resolve_pending_picks(now=NOW) == 2
    assert fd_api == [("/matches", {"dateFrom": "2026-11-01", "dateTo": "2026-11-01"})]
    assert db.get_unresolved_picks(("football",)) == []


def test_football_pick_without_fd_id_is_not_queried(temp_db, fd_api):
    _save("Empate (X)", event_id=None)
    assert resolver.resolve_pending_picks(now=NOW) == 0 and fd_api == []


def test_football_data_pick_from_before_the_ids_is_matched_by_names(temp_db, fd_api):
    # Los picks del 2-nov-2026 salieron de football-data sin event_id
    _save("Empate (X)", event_id=None, source="football-data")
    assert resolver.resolve_pending_picks(now=NOW) == 1 and len(fd_api) == 1


def test_football_pick_not_started_is_not_queried(temp_db, fd_api):
    _save("Empate (X)", kickoff="2026-11-03T15:00:00Z")
    assert resolver.resolve_pending_picks(now=NOW) == 0 and fd_api == []


def test_without_fd_token_football_stays_pending(temp_db, fd_api, monkeypatch):
    monkeypatch.setattr(resolver, "fd_enabled", lambda: False)
    _save("Empate (X)")
    assert resolver.resolve_pending_picks(now=NOW) == 0 and fd_api == []


def test_fd_without_response_keeps_picks_pending(temp_db, fd_api, monkeypatch, caplog):
    monkeypatch.setattr(resolver, "fd_get", lambda path, params=None: {})
    _save("Empate (X)")
    assert resolver.resolve_pending_picks(now=NOW) == 0
    assert "football-data" in caplog.text and len(db.get_unresolved_picks(("football",))) == 1


def test_log_counts_the_football_data_windows(temp_db, fd_api, caplog):
    _save("Empate (X)")
    _save("Victoria Arsenal FC", kickoff="2026-10-01T15:00:00Z")
    with caplog.at_level("INFO", logger=resolver.__name__):
        resolver.resolve_pending_picks(now=NOW)
    assert "2 ventanas football-data" in caplog.text and "0 fechas ESPN" in caplog.text
