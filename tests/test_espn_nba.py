"""Scoreboard ESPN NBA: nulls, respuestas raras, local/visitante y próximos partidos."""
import copy
from datetime import datetime

import pytest

from value_bot import main
from value_bot.scrapers import espn
from value_bot.sports import nba
from value_bot.store import store

EVENT = {"id": "401", "date": "2026-11-02T00:00Z", "competitions": [{
    "status": {"type": {"name": "STATUS_SCHEDULED"}},
    "notes": [{"headline": "NBA Finals - Game 1"}],
    "venue": {"fullName": "Spectrum Center"},
    "competitors": [
        {"homeAway": "home", "team": {"displayName": "Charlotte Hornets"}, "records": [{"summary": "3-2"}]},
        {"homeAway": "away", "team": {"displayName": "Philadelphia 76ers"}, "records": [{"summary": "2-3"}]},
    ],
}]}


def _event(mutate):
    ev = copy.deepcopy(EVENT)
    mutate(ev["competitions"][0])
    return ev


def _comp(i):
    return lambda comp: comp["competitors"][i]


# ── espn_parse_nba ────────────────────────────────────────────────

def test_well_formed_event_is_parsed():
    parsed = espn.espn_parse_nba(EVENT)
    assert (parsed["home"], parsed["away"]) == ("Charlotte Hornets", "Philadelphia 76ers")
    assert (parsed["home_record"], parsed["venue"], parsed["is_finals"]) == ("3-2", "Spectrum Center", True)


@pytest.mark.parametrize("mutate, field, expected", [
    (lambda c: c.update(venue=None),                         "venue",          ""),
    (lambda c: c.update(notes=[None]),                       "series_summary", ""),
    (lambda c: c.update(notes=None),                         "series_summary", ""),
    (lambda c: c["competitors"][0].update(records=[None]),   "home_record",    "?"),
    (lambda c: c["competitors"][0].update(records=None),     "home_record",    "?"),
])
def test_null_optional_fields_fall_back_to_defaults(mutate, field, expected):
    parsed = espn.espn_parse_nba(_event(mutate))
    assert parsed["home"] == "Charlotte Hornets"
    assert parsed[field] == expected


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(status=None),                              # sin estado: no se sabe si empezó
    lambda c: c["competitors"][0].update(team=None),
    lambda c: c["competitors"][0]["team"].pop("displayName"),     # antes "?" vs ...
    lambda c: c["competitors"][0].pop("homeAway"),                # antes competitors[0] = local
    lambda c: c["competitors"][1].update(homeAway="home"),        # dos locales
    lambda c: c.update(competitors=None),
    lambda c: c["competitors"].__setitem__(0, None),
])
def test_event_without_reliable_teams_or_status_is_skipped(mutate):
    assert espn.espn_parse_nba(_event(mutate)) is None


@pytest.mark.parametrize("mutate", [
    lambda c: c["competitors"][0].pop("homeAway"),
    lambda c: c["competitors"][0].update(homeAway="Home"),        # ESPN cambia el valor
    lambda c: c["competitors"][0]["team"].pop("displayName"),
])
def test_scheduled_event_without_clear_teams_is_warned(mutate, caplog):
    # Si ESPN cambia el esquema, todos los juegos se saltarían en silencio (= "día sin NBA")
    assert espn.espn_parse_nba(_event(mutate)) is None
    assert "sin local/visitante claro" in caplog.text and "401" in caplog.text


def test_game_already_started_is_skipped_without_warning(caplog):
    live = _event(lambda c: c["status"]["type"].update(name="STATUS_IN_PROGRESS"))
    assert espn.espn_parse_nba(live) is None
    assert espn.espn_parse_nba(_event(lambda c: c.update(status=None))) is None
    assert "sin local/visitante" not in caplog.text


def test_reversed_order_without_home_away_is_not_swapped():
    def strip_and_reverse(comp):
        for c in comp["competitors"]:
            c.pop("homeAway")
        comp["competitors"].reverse()
    assert espn.espn_parse_nba(_event(strip_and_reverse)) is None


def test_non_dict_event_is_skipped():
    assert espn.espn_parse_nba(None) is None
    assert espn.espn_parse_nba({"competitions": [None]}) is None


# ── espn_nba_games ────────────────────────────────────────────────

@pytest.mark.parametrize("resp", [[], {"events": None}, {"events": {}}, {}, None])
def test_unexpected_scoreboard_shape_means_no_events(monkeypatch, caplog, resp):
    monkeypatch.setattr(espn, "http_get", lambda *a, **k: resp)
    assert espn.espn_nba_games("2026-11-01") == []
    assert "scoreboard NBA sin respuesta" in caplog.text


def test_day_without_games_is_not_a_warning(monkeypatch, caplog):
    monkeypatch.setattr(espn, "http_get", lambda *a, **k: {"events": []})
    assert espn.espn_nba_games("2026-11-01") == []
    assert "sin respuesta" not in caplog.text


# ── Próximos partidos ─────────────────────────────────────────────

@pytest.fixture
def scoreboard(monkeypatch):
    """Juegos por fecha ET; registra las fechas consultadas."""
    asked = []
    by_day = {"2026-11-01": [EVENT], "2026-11-02": [EVENT], "2026-11-03": [EVENT]}

    def fake(date_str):
        asked.append(date_str)
        return by_day.get(date_str, [])
    monkeypatch.setattr(espn, "espn_nba_games", fake)
    monkeypatch.setattr(espn, "now_et", lambda: datetime(2026, 11, 1, 9, 0, tzinfo=espn.ESPN_TZ))
    monkeypatch.setattr(store, "nba_slate_date", None)
    return asked


def test_upcoming_starts_the_day_after_today_in_eastern_time(scoreboard):
    upcoming = espn.get_upcoming_nba_events()
    assert scoreboard == ["2026-11-02"] and upcoming[0]["date"] == "2026-11-02"


def test_upcoming_starts_after_the_day_the_picks_used(scoreboard, monkeypatch):
    # Hoy sin NBA → los picks salieron de mañana: los próximos no deben repetirlos
    monkeypatch.setattr(store, "nba_slate_date", "2026-11-02")
    upcoming = espn.get_upcoming_nba_events()
    assert scoreboard[0] == "2026-11-03" and upcoming[0]["date"] == "2026-11-03"


def test_upcoming_is_capped(scoreboard, monkeypatch):
    monkeypatch.setattr(espn, "espn_nba_games", lambda d: [EVENT] * 10)
    assert len(espn.get_upcoming_nba_events()) == espn.UPCOMING_MAX


def test_clear_run_caches_resets_the_slate_date():
    store.nba_slate_date = "2026-11-02"
    store.clear_run_caches()
    assert store.nba_slate_date is None


# ── main: la sección de próximos nunca tumba el mensaje ───────────

def test_upcoming_failure_does_not_stop_the_daily_message(monkeypatch, caplog):
    def boom():
        raise RuntimeError("ESPN raro")
    monkeypatch.setattr(main, "get_upcoming_nba_events", boom)
    monkeypatch.setitem(main.SPORTS_ENABLED, "nba", True)
    assert main._upcoming() == []
    assert "Próximos partidos" in caplog.text


# ── nba.get_nba_games ─────────────────────────────────────────────

def test_one_unreadable_espn_event_does_not_drop_the_slate(monkeypatch):
    def parse(ev):
        if ev == "roto":
            raise ValueError("forma rara")
        return espn.espn_parse_nba(ev)
    monkeypatch.setattr(nba, "espn_parse_nba", parse)
    monkeypatch.setattr(nba, "espn_nba_games", lambda d: ["roto", EVENT])
    games = nba._with_espn([], "2026-11-01")
    assert [g["home"] for g in games] == ["Charlotte Hornets"]


def test_get_nba_games_records_the_slate_day(monkeypatch):
    monkeypatch.setattr(nba, "now_et", lambda: datetime(2026, 11, 1, 9, 0, tzinfo=espn.ESPN_TZ))
    monkeypatch.setattr(nba, "_api_sports_games", lambda d: [])
    monkeypatch.setattr(nba, "espn_nba_games", lambda d: [EVENT] if d == "2026-11-02" else [])
    monkeypatch.setattr(store, "nba_slate_date", None)
    assert nba.get_nba_games() and store.nba_slate_date == "2026-11-02"
