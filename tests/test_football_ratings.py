"""Ratings de fútbol desde partidos terminados: splits casa/fuera, forma y regresión a la media."""
import pytest

from value_bot.analysis import football_model as fm
from value_bot.analysis import football_ratings as fr


def _m(day, home, away, hg, ag, status="FINISHED", duration="REGULAR", regular=None):
    score = {"duration": duration, "fullTime": {"home": hg, "away": ag}}
    if regular:
        score["regularTime"] = {"home": regular[0], "away": regular[1]}
    return {"utcDate": f"2026-10-{day:02d}T15:00:00Z", "status": status, "score": score,
            "homeTeam": {"name": home}, "awayTeam": {"name": away}}


# A gana todo en casa 3-0; en la liga, los locales meten 2 y los visitantes 1 por partido
SEASON = [
    _m(1, "A", "B", 3, 0), _m(2, "C", "A", 1, 1), _m(3, "A", "C", 3, 0),
    _m(4, "B", "A", 2, 2), _m(5, "A", "B", 3, 0), _m(6, "C", "B", 0, 3),
]


def test_totals_form_and_rates_follow_the_matches():
    a = fr.team_ratings(SEASON)["A"]
    assert a["played"] == 5 and a["goals_for_pg"] == pytest.approx(12 / 5)
    assert a["goals_against_pg"] == pytest.approx(3 / 5)
    assert (a["win_rate"], a["draw_rate"]) == (60.0, 40.0)
    assert (a["form"], a["form_pts"]) == ("WDWDW", 11)


def test_form_keeps_the_last_five_in_date_order():
    matches = [_m(d, "A", "B", 1, 0) for d in range(1, 7)] + [_m(7, "B", "A", 1, 0)]
    a = fr.team_ratings(list(reversed(matches)))["A"]
    assert a["form"] == "WWWWL"


def test_home_and_away_strength_are_regressed_to_the_league_mean():
    ratings = fr.team_ratings(SEASON)
    home_avg = 12 / 6
    a = ratings["A"]
    # 9 goles en 3 partidos de local + FOOTBALL_PRIOR_GAMES partidos de media
    prior = fr.FOOTBALL_PRIOR_GAMES
    assert a["home_attack"] == pytest.approx((9 + prior * home_avg) / (3 + prior) / home_avg, abs=1e-4)
    assert a["league_home_gpg"] == pytest.approx(home_avg) and a["league_away_gpg"] == pytest.approx(1.0)
    assert 1 < a["home_attack"] < 9 / 3 / home_avg


def test_team_without_games_at_a_venue_sits_at_the_mean():
    matches = [_m(d, "A", "B", 2, 1) for d in range(1, 5)]
    b = fr.team_ratings(matches)["B"]
    assert (b["home_attack"], b["home_defense"]) == (1.0, 1.0)


def test_unfinished_and_extra_time_matches():
    matches = SEASON + [_m(7, "A", "B", 9, 0, status="SCHEDULED"),
                        _m(8, "B", "C", 3, 2, duration="EXTRA_TIME", regular=(2, 2))]
    ratings = fr.team_ratings(matches)
    assert ratings["A"]["played"] == 5
    assert ratings["B"]["form"].endswith("D")


def test_teams_with_too_few_games_are_left_out():
    assert fr.team_ratings(SEASON[:2]) == {}


@pytest.mark.parametrize("matches", [None, [], [None, {"status": "FINISHED"}], [{**SEASON[0], "score": None}]])
def test_odd_input_gives_no_ratings(matches):
    assert fr.team_ratings(matches) == {}


# ── XG del modelo ─────────────────────────────────────────────────

def test_model_uses_the_venue_ratings_when_both_teams_have_them():
    ratings = fr.team_ratings(SEASON)
    h, a = ratings["A"], ratings["B"]
    m = fm.build_match({"home": "A", "away": "B", "league": "Premier League"}, h, a, None)
    assert m.exp_h == pytest.approx(h["league_home_gpg"] * h["home_attack"] * a["away_defense"])
    assert m.exp_a == pytest.approx(h["league_away_gpg"] * a["away_attack"] * h["home_defense"])


def test_model_falls_back_to_the_averages_without_venue_ratings():
    hs = {"goals_for_pg": 2.0, "goals_against_pg": 1.0}
    as_ = {"goals_for_pg": 1.0, "goals_against_pg": 1.5}
    m = fm.build_match({"home": "A", "away": "B"}, hs, as_, None)
    assert (m.exp_h, m.exp_a) == (1.75, 1.0)


# ── build_football_stats ──────────────────────────────────────────

def _fd_stub(monkeypatch, by_code):
    from value_bot.sports import football
    calls = []
    monkeypatch.setattr(football, "fd_enabled", lambda: True)
    monkeypatch.setattr(football, "fd_get", lambda path, params=None: calls.append((path, params))
                        or {"matches": by_code[path.split("/")[2]]})
    return football, calls


def test_stats_come_from_one_finished_matches_call_per_competition(monkeypatch, ctx):
    football, calls = _fd_stub(monkeypatch, {"PL": SEASON})
    fixtures = [{"home": "A", "away": "B", "league": "Premier League", "fd_code": "PL"},
                {"home": "C", "away": "B", "league": "Premier League", "fd_code": "PL"}]
    stats = football.build_football_stats(ctx, fixtures)
    assert calls == [("/competitions/PL/matches", {"status": "FINISHED"})]
    assert football.side_stats(stats, fixtures[0], "home") == fr.team_ratings(SEASON)["A"]
    assert set(stats) == {("Premier League", t) for t in "ABC"}


def test_a_team_in_two_competitions_keeps_each_ones_stats(monkeypatch, ctx):
    # A juega Premier (5 partidos) y Champions (3): el partido de Champions no usa los de la Premier
    cl = [_m(1, "A", "X", 0, 1), _m(2, "X", "A", 2, 0), _m(3, "A", "X", 1, 1)]
    football, _ = _fd_stub(monkeypatch, {"PL": SEASON, "CL": cl})
    pl_fix = {"home": "A", "away": "B", "league": "Premier League", "fd_code": "PL"}
    cl_fix = {"home": "A", "away": "X", "league": "Champions League", "fd_code": "CL"}
    stats = football.build_football_stats(ctx, [cl_fix, pl_fix])
    assert football.side_stats(stats, pl_fix, "home")["played"] == 5
    assert football.side_stats(stats, cl_fix, "home") == fr.team_ratings(cl)["A"]
