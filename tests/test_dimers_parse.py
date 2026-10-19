import json
from datetime import date, datetime, timezone

import pytest

from value_bot.scrapers import dimers
from value_bot.scrapers.dimers import (
    extract_matches_from_ng_state,
    parse_dimers_match,
    pick_current_round,
)

NOW = datetime(2026, 10, 18, 13, 0, tzinfo=timezone.utc)


def _team(abv, market, nickname):
    return {"Abv": abv, "DisplayName": market, "Market": market, "Nickname": nickname}


def _match(date_str="2026-10-20T00:15:00Z", live=None, bets=None):
    return {
        "LiveData": live if live is not None else {"error": True},
        "MatchData": {
            "SIMatchID": "NFL_2026_6_SF_WAS",
            "Season": 2026,
            "RoundNumber": 6,
            "Date": date_str,
            "Venue": "Levi's Stadium",
            "HomeTeam": _team("SF", "San Francisco", "49ers"),
            "AwayTeam": _team("WAS", "Washington", "Commanders"),
        },
        "PreData": {
            "PythagHome": 0.759,
            "PythagAway": 0.241,
            "PredHomeScore": 29.7,
            "PredAwayScore": 21.36,
            "PredMargin": 8.33,
            "OverWinPct": 0.4547,
            "UnderWinPct": 0.5453,
            "LastUpdated": "2026-10-19T02:48:01Z",
        },
        "aggregatedBestBets": bets if bets is not None else [
            {"type": "h2h", "bet": "away", "odds": 4.65, "edge": 2.59,
             "winProb": 0.2409, "marketProb": 0.2151, "bookmaker": "novig", "isBest": False},
            {"type": "total", "bet": "under", "markValue": 51.5, "odds": 2.04, "edge": 3.38,
             "winProb": 0.524, "marketProb": 0.4902, "bookmaker": "novig", "isBest": False},
            {"type": "line", "bet": "home", "lineValue": -7.5, "odds": 2.02, "edge": 3.69,
             "winProb": 0.5319, "marketProb": 0.495, "bookmaker": "novig", "isBest": True},
        ],
    }


# ── pick_current_round ────────────────────────────────────────────

def test_pick_current_round_takes_first_active_round_not_finished():
    options = [
        {"Active": False, "Round_Date": "2025-09-09", "Round_Number": 1, "Year": 2025},
        {"Active": True, "Round_Date": "2026-10-13", "Round_Number": 5, "Year": 2026},
        {"Active": True, "Round_Date": "2026-10-20", "Round_Number": 6, "Year": 2026},
        {"Active": True, "Round_Date": "2026-10-27", "Round_Number": 7, "Year": 2026},
    ]
    assert pick_current_round(options, date(2026, 10, 18)) == (2026, 6)


def test_pick_current_round_includes_round_ending_today():
    options = [{"Active": True, "Round_Date": "2026-10-20", "Round_Number": 6, "Year": 2026}]
    assert pick_current_round(options, date(2026, 10, 20)) == (2026, 6)


def test_pick_current_round_none_when_no_active_future_round():
    options = [
        {"Active": False, "Round_Date": "2026-10-27", "Round_Number": 7, "Year": 2026},
        {"Active": True, "Round_Date": "2026-10-13", "Round_Number": 5, "Year": 2026},
        {"Active": True, "Round_Date": "bad-date", "Round_Number": 8, "Year": 2026},
    ]
    assert pick_current_round(options, date(2026, 10, 18)) is None


def test_pick_current_round_tolerates_garbage():
    assert pick_current_round(None, date(2026, 10, 18)) is None
    assert pick_current_round([None, "x", {}], date(2026, 10, 18)) is None


# ── parse_dimers_match ────────────────────────────────────────────

def test_parse_upcoming_match_basic_fields():
    game = parse_dimers_match(_match(), now=NOW)
    assert game["match_id"] == "NFL_2026_6_SF_WAS"
    assert game["season"] == 2026 and game["week"] == 6
    assert game["home"] == "San Francisco 49ers" and game["home_abv"] == "SF"
    assert game["away"] == "Washington Commanders" and game["away_abv"] == "WAS"
    assert game["kickoff"] == "2026-10-20T00:15:00Z"
    assert game["venue"] == "Levi's Stadium"
    assert game["home_win_prob"] == pytest.approx(0.759)
    assert game["away_win_prob"] == pytest.approx(0.241)
    assert game["pred_home_score"] == pytest.approx(29.7)
    assert game["pred_total"] == pytest.approx(51.06)
    assert game["over_prob"] == pytest.approx(0.4547)
    assert game["source"] == "dimers"


def test_parse_normalizes_best_bets_to_bot_markets():
    bets = {(b["market"], b["side"]): b for b in parse_dimers_match(_match(), now=NOW)["best_bets"]}
    assert set(bets) == {("moneyline", "away"), ("total", "under"), ("spread", "home")}

    spread = bets[("spread", "home")]
    assert spread["line"] == -7.5 and spread["odds"] == 2.02
    assert spread["win_prob"] == pytest.approx(0.5319)
    assert spread["prob_edge"] == pytest.approx(0.0369)
    assert spread["value"] == pytest.approx(0.5319 * 2.02 - 1)
    assert spread["bookmaker"] == "novig" and spread["is_best"] is True

    assert bets[("total", "under")]["line"] == 51.5
    assert bets[("moneyline", "away")]["line"] is None


def test_parse_skips_malformed_or_unknown_bets():
    bets = [
        {"type": "h2h", "bet": "home", "odds": "n/a", "winProb": 0.5},
        {"type": "props", "bet": "home", "odds": 2.0, "winProb": 0.5},
        {"type": "total", "bet": "sideways", "odds": 2.0, "winProb": 0.5, "markValue": 40},
        {"type": "line", "bet": "away", "odds": 1.0, "winProb": 0.5, "lineValue": 3},
        "garbage",
        {"type": "line", "bet": "away", "odds": 1.9, "winProb": 0.55, "lineValue": 3},
    ]
    parsed = parse_dimers_match(_match(bets=bets), now=NOW)["best_bets"]
    assert [(b["market"], b["side"], b["line"]) for b in parsed] == [("spread", "away", 3.0)]


def test_parse_skips_started_or_finished_games():
    assert parse_dimers_match(_match(date_str="2026-10-18T12:59:00Z"), now=NOW) is None
    finished = _match(live={"matchStatus": "complete", "status": "completed"})
    assert parse_dimers_match(finished, now=NOW) is None


def test_parse_returns_none_on_malformed_match():
    assert parse_dimers_match({}, now=NOW) is None
    assert parse_dimers_match({"MatchData": None}, now=NOW) is None
    no_teams = _match()
    no_teams["MatchData"]["HomeTeam"] = None
    assert parse_dimers_match(no_teams, now=NOW) is None
    bad_date = _match(date_str="tomorrow")
    assert parse_dimers_match(bad_date, now=NOW) is None


def test_parse_handles_missing_predictions():
    raw = _match(bets=[])
    raw["PreData"] = None
    raw["aggregatedBestBets"] = None
    game = parse_dimers_match(raw, now=NOW)
    assert game["home_win_prob"] is None and game["pred_total"] is None
    assert game["best_bets"] == []


# ── extract_matches_from_ng_state ─────────────────────────────────

def _html(state):
    return (
        '<html><body><script id="ng-state" type="application/json">'
        + json.dumps(state)
        + "</script></body></html>"
    )


def test_extract_matches_from_ng_state():
    state = {
        "isApp": False,
        "http_https://levy-edge.statsinsider.com.au/round/options?Sport=NFL": {"body": []},
        "http_https://levy-edge.statsinsider.com.au/round/matches?Sport=NFL&Round=6": {
            "body": [_match()], "status": 200,
        },
    }
    matches = extract_matches_from_ng_state(_html(state))
    assert len(matches) == 1 and matches[0]["MatchData"]["SIMatchID"] == "NFL_2026_6_SF_WAS"


def test_extract_matches_from_ng_state_missing_or_invalid():
    assert extract_matches_from_ng_state("<html></html>") == []
    bad = '<script id="ng-state" type="application/json">{not json</script>'
    assert extract_matches_from_ng_state(bad) == []
    assert extract_matches_from_ng_state(_html({"isApp": False})) == []


# ── get_dimers_nfl_predictions (red mockeada) ─────────────────────

def test_get_predictions_fetches_current_round(monkeypatch):
    calls = []
    options = [{"Active": True, "Round_Date": "2026-10-20", "Round_Number": 6, "Year": 2026}]

    def fake_get(url, params=None, **_):
        calls.append((url, params))
        return options if url.endswith("/round/options") else [_match(), {"bad": 1}]

    monkeypatch.setattr(dimers, "http_get", fake_get)
    games = dimers.get_dimers_nfl_predictions(now=NOW)

    assert [g["match_id"] for g in games] == ["NFL_2026_6_SF_WAS"]
    matches_params = calls[1][1]
    assert matches_params["Round"] == 6 and matches_params["Season"] == 2026
    assert matches_params["Sport"] == "NFL" and matches_params["best_bets"] == "true"


def test_get_predictions_empty_on_network_failure(monkeypatch):
    monkeypatch.setattr(dimers, "http_get", lambda *a, **k: {})
    assert dimers.get_dimers_nfl_predictions(now=NOW) == []
