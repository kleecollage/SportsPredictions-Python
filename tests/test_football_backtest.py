"""Backtest de fútbol: CSV de football-data.co.uk, replay sin mirar el futuro y reporte."""
import pytest

from value_bot.tools import football_backtest as fb

HEADER = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,PSCH,PSCD,PSCA,AvgCH,AvgCD,AvgCA,PC>2.5,PC<2.5,AvgC>2.5,AvgC<2.5"


def _csv(*rows):
    return "\n".join([HEADER, *rows])


def test_csv_rows_use_pinnacle_closing_and_fall_back_to_the_average():
    text = _csv("E0,15/08/2025,Liverpool,Bournemouth,4,2,1.30,6.0,9.5,1.31,5.8,9.0,1.62,2.40,1.60,2.35",
                "E0,16/08/25,Villa,Newcastle,0,0,2.4,3.4,3.0,2.5,3.3,2.9,,,2.05,1.80")
    first, second = fb.parse_csv(text, "E0")
    assert (first["date"], first["home_goals"], first["over_odds"], first["home_odds"]) == ("2025-08-15", 4, 1.62, 1.30)
    assert (second["date"], second["over_odds"], second["under_odds"]) == ("2025-08-16", 2.05, 1.80)


@pytest.mark.parametrize("row", [
    "E0,,A,B,1,0,2,3,4,2,3,4,1.9,1.9,1.9,1.9",            # sin fecha
    "E0,15/08/2025,A,B,,,2,3,4,2,3,4,1.9,1.9,1.9,1.9",     # sin jugar
    "E0,15/08/2025,A,B,1,0,2,3,4,2,3,4,,,x,1.9",           # sin totales
])
def test_unusable_rows_are_skipped(row):
    assert fb.parse_csv(_csv(row), "E0") == []


def test_rows_without_result_odds_keep_the_totals():
    (game,) = fb.parse_csv(_csv("E0,15/08/2025,A,B,1,0,,,,,,,1.9,1.9,,"), "E0")
    assert "home_odds" not in game and game["over_odds"] == 1.9


def _season(n_rounds=8):
    """A y B se enfrentan en casa de cada uno, con goles; C y D, pocos."""
    games = []
    for i in range(n_rounds):
        day = f"2025-09-{i + 1:02d}"
        home, away = ("A", "C") if i % 2 == 0 else ("C", "A")
        games.append({"date": day, "league": "E0", "home": home, "away": away, "home_goals": 3,
                      "away_goals": 2, "over_odds": 1.9, "under_odds": 1.95})
        home, away = ("B", "D") if i % 2 == 0 else ("D", "B")
        games.append({"date": day, "league": "E0", "home": home, "away": away, "home_goals": 0,
                      "away_goals": 1, "over_odds": 2.1, "under_odds": 1.75})
    return games


def test_replay_needs_enough_earlier_games_and_never_sees_the_future():
    games = _season()
    rows = fb.replay_league(games)
    # 6 partidos previos por equipo → solo las 2 últimas fechas (4 partidos)
    assert len(rows) == 4 and {r["date"] for r in rows} == {"2025-09-07", "2025-09-08"}
    future = [{**g, "home_goals": 9} if g["date"] > "2025-09-07" else g for g in games]
    first = next(r for r in fb.replay_league(future) if r["date"] == "2025-09-07")
    assert first == rows[0]


def test_replay_rows_carry_total_probabilities_and_graded_picks():
    row = fb.replay_league(_season())[0]
    assert row["total"]["over"] is True and 0 < row["total"]["model"] < 1
    assert row["total"]["market"] == pytest.approx((1 / 1.9) / (1 / 1.9 + 1 / 1.95))
    assert all(p["result"] in ("win", "loss", "void") for p in row["picks"])


def test_report_without_rows_and_with_rows():
    assert "sin partidos" in fb.render_report([])
    report = fb.render_report(fb.replay_league(_season()))
    assert "OVER 2.5" in report and "peso óptimo" in report and "XG aditivo" in report
