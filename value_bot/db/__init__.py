"""
SQLite persistence — historial de picks, resultados, ROI y calibración del modelo.
Un módulo por responsabilidad; aquí solo se reexporta la API pública (la ruta de la BD vive
en `db.connection.DB_PATH` y no se reexporta: una copia no vería el parche de los tests).
"""
from value_bot.db import connection
from value_bot.db.backup import backup_db
from value_bot.db.parlays import get_pending_parlays, save_parlay_result, save_parlays
from value_bot.db.picks import already_sent_bet, already_sent_for_event, already_sent_for_game, save_picks
from value_bot.db.reports import (
    CALIBRATION_LOOKBACK_DAYS, CALIBRATION_MIN_RESOLVED, get_calibration_factors, get_model_calibration,
    get_parlay_summary, get_recent_picks, get_roi_summary, load_picks_df,
)
from value_bot.db.results import get_unresolved_picks, save_result
from value_bot.db.runs import finish_run, get_runs, start_run
from value_bot.db.schema import init_db

__all__ = [
    "already_sent_bet", "already_sent_for_event", "already_sent_for_game", "backup_db",
    "CALIBRATION_LOOKBACK_DAYS", "CALIBRATION_MIN_RESOLVED", "connection", "finish_run",
    "get_calibration_factors", "get_model_calibration", "get_parlay_summary", "get_pending_parlays",
    "get_recent_picks", "get_roi_summary", "get_runs", "get_unresolved_picks", "init_db", "load_picks_df",
    "save_parlay_result", "save_parlays", "save_picks", "save_result", "start_run",
]
