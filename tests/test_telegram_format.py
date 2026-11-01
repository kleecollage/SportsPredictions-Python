from value_bot import telegram_sender as ts


def _nfl_pick(**extra):
    return {
        "sport": "nfl", "league": "NFL", "flag": "🏈",
        "home": "Las Vegas Raiders", "away": "Los Angeles Rams",
        "kickoff": "2026-10-25T20:25Z", "venue": "Allegiant Stadium",
        "home_record": "3-3", "away_record": "2-4",
        "type": "Victoria Las Vegas Raiders", "confidence": 37, "est_odds": 2.8,
        "value": 0.048, "data_quality": "high", "real_odds_source": True,
        "reasoning": "Proyección.", **extra,
    }


def test_kickoff_is_shown_in_eastern_time():
    assert ts.fmt_ko("2026-10-25T20:25Z") == "25/10 16:25 ET"  # EDT, UTC−4


def test_kickoff_eastern_time_follows_daylight_saving_change():
    assert ts.fmt_ko("2026-11-08T18:00Z") == "08/11 13:00 ET"  # EST, UTC−5


def test_kickoff_invalid_or_missing_is_empty():
    assert ts.fmt_ko("") == "" and ts.fmt_ko("mañana") == ""


def test_plural_helper():
    assert ts.plural(1, "pick") == "1 pick"
    assert ts.plural(2, "pick") == "2 picks"
    assert ts.plural(0, "juego") == "0 juegos"


def test_single_nfl_pick_message_uses_singular():
    msg = ts.build_message([], [], [], nflp=[_nfl_pick()])
    assert "1 pick generado hoy" in msg
    assert "(1 juego  ·  1 pick  ·  1 💎 value)" in msg
    assert "picks" not in msg.replace("Picks", "")


def test_several_picks_message_uses_plural():
    picks = [_nfl_pick(), _nfl_pick(home="Kansas City Chiefs", away="Seattle Seahawks")]
    msg = ts.build_message([], [], [], nflp=picks)
    assert "2 picks generados hoy" in msg
    assert "(2 juegos  ·  2 picks  ·  2 💎 values)" in msg


def test_performance_block_says_it_covers_the_current_model_only():
    perf = {"total_picks": 6, "win_rate": 50.0, "roi_pct": -5.0, "total_profit": -0.3,
            "by_sport": {"nfl": {"win_rate": 50.0}}, "by_quality": {}}
    msg = ts.build_message([], [], [], perf=perf, nflp=[_nfl_pick()])
    assert "RENDIMIENTO (resueltos en 7 días · modelo actual)" in msg


# ── Robustez de fmt_ko (code review M1 / L1) ──────────────────────

def test_kickoff_non_string_does_not_break_the_message():
    assert ts.fmt_ko(1729450000) == "" and ts.fmt_ko(None) == ""
    msg = ts.build_message([], [], [], nflp=[_nfl_pick(kickoff=1729450000)])
    assert "Las Vegas Raiders" in msg


def test_kickoff_date_without_time_shows_only_the_day():
    assert ts.fmt_ko("2026-10-20") == "20/10"


# ── Rendimiento con muestra pequeña (code review L2) ──────────────

def _perf(n):
    """Misma forma que db.get_roi_summary()."""
    return {"period_days": 7, "total_picks": n, "win_rate": 100.0, "roi_pct": 80.0,
            "total_profit": 0.8 * n, "by_quality": {},
            "by_sport": {"nfl": {"total": n, "wins": n, "win_rate": 100.0, "profit": 0.8 * n}}}


def test_performance_block_shows_from_one_pick_with_small_sample_warning():
    msg = ts.build_message([], [], [], perf=_perf(1), nflp=[_nfl_pick()])
    assert "RENDIMIENTO" in msg and "Muestra pequeña" in msg


def test_performance_block_without_warning_once_sample_is_enough():
    msg = ts.build_message([], [], [], perf=_perf(5), nflp=[_nfl_pick()])
    assert "RENDIMIENTO" in msg and "Muestra pequeña" not in msg


def test_performance_block_hidden_without_resolved_picks():
    msg = ts.build_message([], [], [], perf={"error": "sin datos resueltos"}, nflp=[_nfl_pick()])
    assert "RENDIMIENTO" not in msg


# ── Reporte semanal: modelo actual (code review M2) ───────────────

def test_weekly_report_labels_summary_as_current_model():
    msg = ts.build_weekly_report(_perf(3), [], [])
    assert "Resumen general (resueltos en 7 días · modelo actual)" in msg


def test_weekly_report_without_current_model_data_says_so():
    msg = ts.build_weekly_report({"error": "sin datos resueltos"}, [], [])
    assert "modelo actual" in msg


def test_nba_finals_header_uses_the_current_year():
    from datetime import datetime
    finals = {**_nfl_pick(), "sport": "nba", "league": "NBA Finals", "flag": "🏆", "is_finals": True}
    msg = ts.build_message([], [finals], [])
    assert f"NBA FINALS {datetime.now().year}" in msg
