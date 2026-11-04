"""Caché en disco de los backtests: solo respuestas que ya no cambian, nunca fallos."""
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from value_bot import config, db
from value_bot.tools import http_cache, nba_backtest, nfl_backtest

URL = "https://site.api.espn.com/x/summary"


@pytest.fixture
def calls(monkeypatch, tmp_path):
    """http_get falso que cuenta llamadas; la caché va a una carpeta del test."""
    net = SimpleNamespace(made=[], responses={})

    def fake_get(url, params=None):
        net.made.append((url, params))
        return net.responses.get(params.get("event"), {"pickcenter": [{"spread": -3}]})
    monkeypatch.setattr(http_cache, "http_get", fake_get)
    monkeypatch.setattr(http_cache, "BACKTEST_CACHE_DIR", tmp_path / "cache")
    return net


def test_second_call_reads_from_disk(calls):
    first = http_cache.cached_get(URL, {"event": "1"}, cacheable=True)
    second = http_cache.cached_get(URL, {"event": "1"}, cacheable=True)
    assert first == second == {"pickcenter": [{"spread": -3}]}
    assert len(calls.made) == 1


def test_params_are_part_of_the_key_in_any_order(calls):
    http_cache.cached_get(URL, {"event": "1", "lang": "en"}, cacheable=True)
    http_cache.cached_get(URL, {"lang": "en", "event": "1"}, cacheable=True)
    http_cache.cached_get(URL, {"event": "2"}, cacheable=True)
    assert len(calls.made) == 2


def test_not_cacheable_always_goes_to_the_network(calls):
    http_cache.cached_get(URL, {"event": "1"}, cacheable=False)
    http_cache.cached_get(URL, {"event": "1"}, cacheable=False)
    assert len(calls.made) == 2
    assert not (http_cache.BACKTEST_CACHE_DIR).exists()


def test_failed_response_is_not_cached(calls):
    calls.responses["1"] = {}
    http_cache.cached_get(URL, {"event": "1"}, cacheable=True)
    http_cache.cached_get(URL, {"event": "1"}, cacheable=True)
    assert len(calls.made) == 2


def test_corrupt_cache_file_is_refetched_and_rewritten(calls):
    http_cache.cached_get(URL, {"event": "1"}, cacheable=True)
    [path] = http_cache.BACKTEST_CACHE_DIR.glob("*.json")
    path.write_text("{roto", encoding="utf-8")
    assert http_cache.cached_get(URL, {"event": "1"}, cacheable=True) == {"pickcenter": [{"spread": -3}]}
    assert len(calls.made) == 2
    assert http_cache.cached_get(URL, {"event": "1"}, cacheable=True) and len(calls.made) == 2


def test_unwritable_cache_dir_still_returns_the_data(calls, monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(http_cache, "BACKTEST_CACHE_DIR", blocker / "cache")   # padre es un archivo
    assert http_cache.cached_get(URL, {"event": "1"}, cacheable=True) == {"pickcenter": [{"spread": -3}]}


def test_only_the_kept_keys_are_stored_and_returned(calls):
    calls.responses["1"] = {"pickcenter": [{"spread": -3}], "boxscore": {"x": "grande"}, "news": []}
    first = http_cache.cached_get(URL, {"event": "1"}, cacheable=True, keep=("pickcenter",))
    [path] = http_cache.BACKTEST_CACHE_DIR.glob("*.json")
    second = http_cache.cached_get(URL, {"event": "1"}, cacheable=True, keep=("pickcenter",))
    assert first == second == {"pickcenter": [{"spread": -3}]}
    assert "boxscore" not in path.read_text(encoding="utf-8")
    assert len(calls.made) == 1


def test_failed_write_leaves_no_temp_file(calls, monkeypatch):
    def boom(*_a):
        raise OSError("disco lleno")
    monkeypatch.setattr(http_cache.os, "replace", boom)
    assert http_cache.cached_get(URL, {"event": "1"}, cacheable=True) == {"pickcenter": [{"spread": -3}]}
    assert list(http_cache.BACKTEST_CACHE_DIR.iterdir()) == []


def test_repo_root_is_defined_once_for_the_db_and_the_cache():
    assert db.connection.DB_PATH == config.REPO_ROOT / "picks_history.db"
    assert ".cache" in (config.REPO_ROOT / ".gitignore").read_text(encoding="utf-8").split()


def test_cache_dir_defaults_to_the_gitignored_repo_folder():
    env = {k: v for k, v in os.environ.items() if k != "BACKTEST_CACHE_DIR"}
    out = subprocess.run([sys.executable, "-c", "from value_bot import config; print(config.BACKTEST_CACHE_DIR)"],
                         cwd=config.REPO_ROOT, env=env, capture_output=True, text=True, check=True).stdout
    assert out.strip() == str(config.REPO_ROOT / ".cache" / "backtest_http")


def test_tests_never_use_the_repo_cache():
    assert config.BACKTEST_CACHE_DIR != config.REPO_ROOT / ".cache" / "backtest_http"


@pytest.mark.parametrize("module", [nfl_backtest, nba_backtest])
def test_backtests_only_keep_the_pickcenter_of_summaries(module, monkeypatch):
    kept = []
    monkeypatch.setattr(module, "cached_get", lambda url, params, cacheable, keep=None: kept.append(keep) or {})
    if module is nfl_backtest:
        monkeypatch.setattr(module, "_finished_events", lambda days: [{"id": "1", "shortName": "A @ B"}])
        monkeypatch.setattr(module, "fetch_team_ids", lambda: ["1"])
        monkeypatch.setattr(module, "_team_history", lambda t, s: [])
        module.run(3)
    else:
        game = {"event_id": "1", "date": datetime(2026, 1, 5, tzinfo=timezone.utc),
                "home_id": "1", "away_id": "2"}
        monkeypatch.setattr(module, "_season_games", lambda start, until: [game])
        monkeypatch.setattr(module, "team_stats_before", lambda games, tid, when: True)
        module.run(2025, date_to=date(2026, 1, 10))
    assert kept == [("pickcenter",)]


# ── Qué se considera inmutable ────────────────────────────────────

@pytest.mark.parametrize("day, today, final", [
    ("20261101", date(2026, 11, 4), True),    # 3 días atrás
    ("20261102", date(2026, 11, 4), True),    # 2 días: juegos de la noche ya terminaron
    ("20261103", date(2026, 11, 4), False),   # ayer: puede haber juegos en curso o líneas por cerrar
    ("20261104", date(2026, 11, 4), False),
])
def test_only_scoreboards_of_settled_days_are_cached(day, today, final):
    assert http_cache.is_settled_day(day, today) is final


def test_nfl_backtest_caches_previous_season_schedules_but_not_the_current(monkeypatch):
    seen = []
    monkeypatch.setattr(nfl_backtest, "nfl_season", lambda: 2026)
    monkeypatch.setattr(nfl_backtest, "cached_get", lambda url, params, cacheable: seen.append(cacheable) or {})
    nfl_backtest._team_history("12", 2026)
    nfl_backtest._team_history("12", 2025)
    assert seen == [False, True]


def test_nba_backtest_scoreboards_use_the_settled_day_rule(monkeypatch):
    seen = []
    monkeypatch.setattr(nba_backtest, "cached_get", lambda url, params, cacheable: seen.append((params["dates"], cacheable)) or {})
    monkeypatch.setattr(nba_backtest, "_today_et", lambda: date(2026, 11, 4))
    monkeypatch.setattr(nba_backtest, "_season_days", lambda start, until: ["20261101", "20261103"])
    nba_backtest._season_games(2026, date(2026, 11, 4))
    assert sorted(seen) == [("20261101", True), ("20261103", False)]
