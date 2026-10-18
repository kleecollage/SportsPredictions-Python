import logging
import threading
from datetime import datetime, timezone

import pytest

from value_bot.sports import nfl
from value_bot.sports.nfl import TeamScores, espn_query_dates, parse_espn_nfl_event, parse_team_scores


def _competitor(side, team_id, name, record="3-2"):
    return {
        "homeAway": side,
        "team": {"id": team_id, "displayName": name},
        "records": [{"summary": record}],
    }


@pytest.fixture(autouse=True)
def _clean_store():
    nfl.store.clear_run_caches()
    yield
    nfl.store.clear_run_caches()


def _event(status="STATUS_SCHEDULED", odds=None, season_type=2):
    return {
        "id": "401772945",
        "date": "2026-10-19T00:20Z",
        "season": {"year": 2026, "type": season_type},
        "competitions": [{
            "status": {"type": {"name": status}},
            "venue": {"fullName": "Lambeau Field"},
            "competitors": [
                _competitor("home", "9", "Green Bay Packers", "3-2"),
                _competitor("away", "6", "Dallas Cowboys", "4-1"),
            ],
            "odds": odds if odds is not None else [{
                "provider": {"displayName": "Draft Kings"},
                "overUnder": 53.5,
                "moneyline": {"home": {"close": {"odds": "-108"}}, "away": {"close": {"odds": "-112"}}},
                "pointSpread": {
                    "home": {"close": {"line": "+1.5", "odds": "-122"}},
                    "away": {"close": {"line": "-1.5", "odds": "+102"}},
                },
                "total": {
                    "over": {"close": {"line": "o53.5", "odds": "-112"}},
                    "under": {"close": {"line": "u53.5", "odds": "-108"}},
                },
            }],
        }],
    }


def test_parse_scheduled_event_with_market():
    game = parse_espn_nfl_event(_event())
    assert game["home"] == "Green Bay Packers" and game["home_id"] == "9"
    assert game["away"] == "Dallas Cowboys" and game["away_id"] == "6"
    assert game["home_record"] == "3-2" and game["venue"] == "Lambeau Field"
    assert game["event_id"] == "401772945"
    m = game["lines"]
    assert m["provider"] == "Draft Kings"
    assert m["home_spread"] == 1.5
    assert m["total"] == 53.5
    assert m["home_ml"] == pytest.approx(1.926, abs=0.001)
    assert m["away_spread_odds"] == pytest.approx(2.02, abs=0.001)
    assert m["over_odds"] == pytest.approx(1.893, abs=0.001)


@pytest.mark.parametrize("status", ["STATUS_FINAL", "STATUS_IN_PROGRESS", "STATUS_HALFTIME"])
def test_parse_skips_games_not_scheduled(status):
    assert parse_espn_nfl_event(_event(status=status)) is None


def test_parse_event_without_odds_has_empty_market():
    game = parse_espn_nfl_event(_event(odds=[]))
    assert game["lines"] == {}


def test_parse_team_scores_only_completed_games_from_team_perspective():
    schedule = {"events": [
        {"competitions": [{"status": {"type": {"name": "STATUS_FINAL"}}, "competitors": [
            {"homeAway": "home", "team": {"id": "12"}, "score": {"value": 31.0}},
            {"homeAway": "away", "team": {"id": "7"}, "score": {"value": 10.0}},
        ]}]},
        {"competitions": [{"status": {"type": {"name": "STATUS_FINAL"}}, "competitors": [
            {"homeAway": "home", "team": {"id": "15"}, "score": "10"},
            {"homeAway": "away", "team": {"id": "12"}, "score": "24"},
        ]}]},
        {"competitions": [{"status": {"type": {"name": "STATUS_SCHEDULED"}}, "competitors": [
            {"homeAway": "home", "team": {"id": "12"}},
            {"homeAway": "away", "team": {"id": "3"}},
        ]}]},
    ]}
    assert parse_team_scores(schedule, "12") == [(31.0, 10.0), (24.0, 10.0)]


def test_parse_team_scores_returns_none_when_request_failed():
    assert parse_team_scores({}, "12") is None


def test_parse_pick_em_spread_as_zero():
    event = _event()
    spread = event["competitions"][0]["odds"][0]["pointSpread"]
    spread["home"]["close"]["line"] = "PK"
    spread["away"]["close"]["line"] = "PK"
    assert parse_espn_nfl_event(event)["lines"]["home_spread"] == 0.0


def _patch_fetch(monkeypatch, by_season):
    nfl.store.nfl_scores_cache.clear()
    monkeypatch.setattr(nfl, "nfl_season", lambda offset=0: 2026 + offset)
    monkeypatch.setattr(nfl, "_fetch_scores", lambda team_id, season: by_season[season])


def test_get_team_scores_uses_current_season_with_enough_sample(monkeypatch):
    _patch_fetch(monkeypatch, {2026: [(20.0, 17.0)] * 3, 2025: [(24.0, 21.0)] * 17})
    assert nfl.get_team_scores("12") == TeamScores([(20.0, 17.0)] * 3, current=True)


def test_get_team_scores_combines_seasons_without_sample(monkeypatch):
    _patch_fetch(monkeypatch, {2026: [(20.0, 17.0)], 2025: [(24.0, 21.0)] * 17})
    result = nfl.get_team_scores("12")
    assert result.current is False
    assert result.scores == [(20.0, 17.0)]
    assert result.previous == ((24.0, 21.0),) * 17


def test_get_team_scores_does_not_fall_back_on_network_failure(monkeypatch):
    _patch_fetch(monkeypatch, {2026: None, 2025: [(24.0, 21.0)] * 17})
    assert nfl.get_team_scores("12") == TeamScores([], current=False)


# ── Media de liga con los 32 equipos (#2) ─────────────────────────

def test_get_league_average_uses_every_team(monkeypatch):
    monkeypatch.setattr(nfl, "fetch_team_ids", lambda: ["1", "2", "3"])
    by_team = {
        "1": TeamScores([(30.0, 10.0)] * 4, True),
        "2": TeamScores([(20.0, 20.0)] * 4, True),
        "3": TeamScores([(10.0, 30.0)] * 4, True),
    }
    monkeypatch.setattr(nfl, "get_team_scores", lambda tid: by_team[tid])
    assert nfl.get_league_average() == pytest.approx(20.0)


def test_get_league_average_falls_back_to_default_when_teams_unavailable(monkeypatch):
    monkeypatch.setattr(nfl, "fetch_team_ids", lambda: [])
    assert nfl.get_league_average() == nfl.NFL_LEAGUE_AVG_DEFAULT


# ── Fechas ESPN en hora del este (#8) ─────────────────────────────

def test_espn_query_dates_use_us_eastern_day():
    # 02:00 UTC del 19-oct = 22:00 ET del 18-oct: la jornada ESPN sigue siendo el 18
    now = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
    assert espn_query_dates(now, days_ahead=2) == ["20261018", "20261019", "20261020"]


# ── Tipo de temporada: sin pretemporada ni Pro Bowl (review #1) ───

def test_parse_skips_preseason_event():
    assert parse_espn_nfl_event(_event(season_type=1)) is None


def test_parse_accepts_postseason_event():
    assert parse_espn_nfl_event(_event(season_type=3)) is not None


def test_parse_uses_board_season_type_when_event_lacks_it():
    event = _event()
    del event["season"]
    assert parse_espn_nfl_event(event, default_season_type=2) is not None
    assert parse_espn_nfl_event(event, default_season_type=1) is None


def test_parse_skips_event_with_unknown_season_type():
    event = _event()
    del event["season"]
    assert parse_espn_nfl_event(event) is None


def test_league_games_drops_teams_outside_the_league():
    pro_bowl = {"home_id": "31", "away_id": "32"}
    regular = {"home_id": "9", "away_id": "6"}
    assert nfl.league_games([pro_bowl, regular], {"9", "6"}) == [regular]


def test_league_games_keeps_everything_when_team_list_unavailable():
    games = [{"home_id": "31", "away_id": "32"}]
    assert nfl.league_games(games, set()) == games


# ── Robustez ante nulls y eventos mal formados (review #2) ────────

def test_parse_tolerates_null_nested_fields():
    event = _event()
    comp = event["competitions"][0]
    comp["venue"] = None
    comp["odds"][0]["provider"] = None
    comp["odds"][0]["moneyline"]["home"] = None
    comp["competitors"][0]["records"] = None
    game = parse_espn_nfl_event(event)
    assert game["venue"] == "" and game["home_record"] == ""
    assert "provider" not in game["lines"] and "home_ml" not in game["lines"]
    assert game["lines"]["away_ml"] == pytest.approx(1.893, abs=0.001)


def test_parse_null_status_is_not_scheduled():
    event = _event()
    event["competitions"][0]["status"] = None
    assert parse_espn_nfl_event(event) is None


def test_get_nfl_games_skips_malformed_event_and_keeps_the_rest(monkeypatch):
    broken = _event()
    broken["competitions"][0]["competitors"] = "garbage"
    boards = iter([{"events": [broken, _event()]}])
    monkeypatch.setattr(nfl, "http_get", lambda url, params=None: next(boards, {}))
    monkeypatch.setattr(nfl, "fetch_team_ids", lambda: ["9", "6"])
    games = nfl.get_nfl_games()
    assert [g["event_id"] for g in games] == ["401772945"]


# ── Temporada anterior fallida no se cachea (review #3 y #4) ──────

def test_get_team_scores_does_not_cache_when_previous_season_fails(monkeypatch, caplog):
    _patch_fetch(monkeypatch, {2026: [(20.0, 17.0)], 2025: None})
    with caplog.at_level(logging.WARNING, logger=nfl.log.name):
        result = nfl.get_team_scores("12")
    assert result == TeamScores([(20.0, 17.0)], current=False)
    assert "12" not in nfl.store.nfl_scores_cache
    assert "2025" in caplog.text


def test_get_team_scores_logs_games_added_not_season_year(monkeypatch, caplog):
    _patch_fetch(monkeypatch, {2026: [(20.0, 17.0)], 2025: [(24.0, 21.0)] * 17})
    with caplog.at_level(logging.INFO, logger=nfl.log.name):
        nfl.get_team_scores("12")
    assert "sumando 17 juegos de 2025" in caplog.text


# ── Snapshots de línea open/close (review #5) ─────────────────────

def test_parse_lines_uses_open_when_close_lacks_odds():
    event = _event()
    ml = event["competitions"][0]["odds"][0]["moneyline"]
    ml["home"] = {"close": {"odds": None}, "open": {"odds": "+140"}}
    assert parse_espn_nfl_event(event)["lines"]["home_ml"] == pytest.approx(2.4)


def test_parse_lines_keeps_line_and_odds_from_the_same_snapshot():
    event = _event()
    spread = event["competitions"][0]["odds"][0]["pointSpread"]
    spread["home"] = {"close": {"line": "+2.5"}, "open": {"line": "+1.5", "odds": "-110"}}
    lines = parse_espn_nfl_event(event)["lines"]
    assert lines["home_spread"] == 1.5
    assert lines["home_spread_odds"] == pytest.approx(1.909, abs=0.001)


def test_parse_lines_merges_fields_when_no_snapshot_is_complete():
    event = _event()
    spread = event["competitions"][0]["odds"][0]["pointSpread"]
    spread["home"] = {"close": {"line": "+2.5"}, "open": {"odds": "-110"}}
    lines = parse_espn_nfl_event(event)["lines"]
    assert lines["home_spread"] == 2.5
    assert lines["home_spread_odds"] == pytest.approx(1.909, abs=0.001)


# ── Prefijos o/u en mayúscula (review #6) ─────────────────────────

def test_parse_total_with_uppercase_prefix():
    event = _event()
    odds = event["competitions"][0]["odds"][0]
    del odds["overUnder"]
    odds["total"]["over"]["close"]["line"] = "O 47.5"
    assert parse_espn_nfl_event(event)["lines"]["total"] == 47.5


# ── Fecha del juego en hora del este (review #7) ──────────────────

def test_parse_date_is_eastern_day_for_night_games():
    # 00:20 UTC del 19-oct = 20:20 ET del 18-oct (Sunday Night Football)
    assert parse_espn_nfl_event(_event())["date"] == "2026-10-18"


# ── Media de liga en paralelo (review #8) ─────────────────────────

def test_get_league_average_fetches_teams_concurrently(monkeypatch):
    monkeypatch.setattr(nfl, "fetch_team_ids", lambda: ["1", "2"])
    barrier = threading.Barrier(2, timeout=2)

    def scores(tid):
        barrier.wait()  # en serie nunca llegan dos hilos → BrokenBarrierError
        return TeamScores([(21.0, 21.0)] * 6, True)

    monkeypatch.setattr(nfl, "get_team_scores", scores)
    assert nfl.get_league_average() == pytest.approx(21.0)


def test_team_ids_are_fetched_once_per_run(monkeypatch):
    calls = []
    monkeypatch.setattr(nfl, "fetch_team_ids", lambda: calls.append(1) or ["9", "6"])
    assert nfl.get_team_ids() == ["9", "6"]
    assert nfl.get_team_ids() == ["9", "6"]
    assert len(calls) == 1


# ── Sede neutral (review nfl_model #2) ────────────────────────────

def test_parse_neutral_site_flag():
    event = _event()
    assert parse_espn_nfl_event(event)["neutral_site"] is False
    event["competitions"][0]["neutralSite"] = True
    assert parse_espn_nfl_event(event)["neutral_site"] is True


def test_get_league_average_includes_previous_season_sample(monkeypatch):
    monkeypatch.setattr(nfl, "fetch_team_ids", lambda: ["1"])
    monkeypatch.setattr(nfl, "get_team_scores",
                        lambda tid: TeamScores([(30.0, 10.0)], False, ((20.0, 20.0),) * 9))
    assert nfl.get_league_average() == pytest.approx(21.0)
