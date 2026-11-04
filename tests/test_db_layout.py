"""db partido por responsabilidad: una ruta de BD, pandas solo en los reportes, API pública igual."""
import importlib
import inspect
from pathlib import Path

import pytest

from value_bot import db

MODULES = ("connection", "schema", "picks", "parlays", "results", "reports", "backup")
PUBLIC = ("init_db", "save_picks", "already_sent_for_game", "already_sent_for_event", "already_sent_bet",
          "save_parlays", "get_pending_parlays", "save_parlay_result", "save_result", "get_unresolved_picks",
          "get_parlay_summary", "get_roi_summary", "get_model_calibration", "get_calibration_factors",
          "get_recent_picks", "load_picks_df")


@pytest.mark.parametrize("name", MODULES)
def test_db_is_a_package_of_small_modules(name):
    module = importlib.import_module(f"value_bot.db.{name}")
    assert len(inspect.getsource(module).splitlines()) < 400


def test_public_api_is_still_importable_from_db():
    assert set(PUBLIC) <= set(db.__all__)
    assert all(callable(getattr(db, name)) for name in PUBLIC)


def test_db_path_lives_only_in_connection():
    # Una sola ruta que parchear: el paquete no la reexporta (una copia quedaría vieja)
    assert not hasattr(db, "DB_PATH")
    # La BD de la raíz del repo: otra ruta crearía una vacía sin dedup ni historial
    assert db.connection.DB_PATH.resolve() == (Path(__file__).parent.parent / "picks_history.db").resolve()


@pytest.mark.parametrize("name", MODULES)
def test_only_connection_opens_sqlite(name):
    source = inspect.getsource(importlib.import_module(f"value_bot.db.{name}"))
    assert ("sqlite3.connect(" in source) == (name == "connection"), name


def test_only_reports_import_pandas():
    for name in MODULES:
        source = inspect.getsource(importlib.import_module(f"value_bot.db.{name}"))
        assert ("import pandas" in source) == (name == "reports"), name


def test_patching_the_connection_path_redirects_every_module(tmp_path, monkeypatch):
    path: Path = tmp_path / "picks.db"
    monkeypatch.setattr(db.connection, "DB_PATH", path)
    db.init_db()
    db.save_picks("2026-11-03", [{"sport": "nfl", "home": "A", "away": "B", "type": "Victoria A"}])
    (pick,) = db.get_unresolved_picks(("nfl",))
    db.save_result(pick["id"], "win", "1-0", 1.0)
    with db.connection.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM results").fetchone() == (1,)
