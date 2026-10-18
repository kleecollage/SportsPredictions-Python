"""Orquestación NFL (#3): get_nfl_picks y su render en el mensaje de Telegram."""
import pytest

from value_bot.analysis import nfl_picks
from value_bot.sports.nfl import TeamScores
from value_bot.telegram_sender import build_message

LINES = {
    "provider": "Draft Kings",
    "home_ml": 1.40, "away_ml": 3.10,
    "home_spread": -6.5, "home_spread_odds": 1.91, "away_spread_odds": 1.91,
    "total": 44.5, "over_odds": 1.91, "under_odds": 1.91,
}


def _game(home, away, home_id, away_id):
    return {
        "sport": "nfl", "date": "2026-10-25", "kickoff": "2026-10-25T17:00Z",
        "home": home, "home_id": home_id, "away": away, "away_id": away_id,
        "home_record": "5-1", "away_record": "1-5", "venue": "", "lines": LINES,
        "source": "espn",
    }


def _pick(pick_type, confidence, value):
    return {"market": "moneyline", "type": pick_type, "confidence": confidence,
            "our_prob": confidence / 100, "est_odds": 1.9, "value": value,
            "reasoning": "r", "data_quality": "high"}


@pytest.fixture
def wired(monkeypatch):
    """Conecta get_nfl_picks con datos falsos y registra las llamadas al modelo."""
    games = [_game("Kansas City Chiefs", "Las Vegas Raiders", "12", "13"),
             _game("Green Bay Packers", "Dallas Cowboys", "9", "6")]
    calls = []
    picks_by_home = {
        "Kansas City Chiefs": [_pick("Victoria Kansas City Chiefs", 70, 0.01)],
        "Green Bay Packers": [_pick("Under 53.5 Puntos", 55, 0.07),
                              _pick("Victoria Green Bay Packers", 64, None)],
    }

    def fake_analyze(game, home_rating, away_rating, league_avg, **_):
        calls.append({"home": game["home"], "avg": league_avg,
                      "home_rating": home_rating, "away_rating": away_rating})
        return picks_by_home[game["home"]]

    monkeypatch.setattr(nfl_picks, "get_nfl_games", lambda: games)
    monkeypatch.setattr(nfl_picks, "get_league_average", lambda: 23.0)
    monkeypatch.setattr(nfl_picks, "get_team_scores",
                        lambda tid: TeamScores([(24.0, 20.0)] * 6, current=tid != "6"))
    monkeypatch.setattr(nfl_picks, "analyze_nfl_game", fake_analyze)
    monkeypatch.setattr(nfl_picks, "_already_sent", lambda *_: False)
    return calls


def test_uses_league_average_of_all_teams_not_slate(wired):
    nfl_picks.get_nfl_picks()
    assert {c["avg"] for c in wired} == {23.0}


def test_marks_ratings_from_previous_season(wired):
    nfl_picks.get_nfl_picks()
    gb = next(c for c in wired if c["home"] == "Green Bay Packers")
    assert gb["home_rating"]["current"] is True
    assert gb["away_rating"]["current"] is False


def test_tags_picks_with_sport_and_keeps_game_lines(wired):
    picks = nfl_picks.get_nfl_picks()
    assert all(p["sport"] == "nfl" and p["league"] == "NFL" and p["flag"] == "🏈" for p in picks)
    assert all(p["lines"] == LINES for p in picks)


def test_value_picks_first_then_confidence(wired):
    types = [p["type"] for p in nfl_picks.get_nfl_picks()]
    assert types == ["Under 53.5 Puntos", "Victoria Kansas City Chiefs", "Victoria Green Bay Packers"]


def test_skips_picks_already_sent(wired, monkeypatch):
    monkeypatch.setattr(nfl_picks, "_already_sent",
                        lambda home, away, pick_type, day: pick_type == "Under 53.5 Puntos")
    types = [p["type"] for p in nfl_picks.get_nfl_picks()]
    assert "Under 53.5 Puntos" not in types and len(types) == 2


def test_caps_number_of_picks(wired, monkeypatch):
    monkeypatch.setattr(nfl_picks, "MAX_PICKS_SPORT", 1)
    assert len(nfl_picks.get_nfl_picks()) == 1


def test_no_games_returns_empty_without_fetching_stats(monkeypatch):
    monkeypatch.setattr(nfl_picks, "get_nfl_games", lambda: [])
    monkeypatch.setattr(nfl_picks, "get_league_average",
                        lambda: pytest.fail("no debe pedir la media sin juegos"))
    assert nfl_picks.get_nfl_picks() == []


def test_message_renders_nfl_section(wired):
    picks = nfl_picks.get_nfl_picks()
    msg = build_message([], [], [], nflp=picks)
    assert "🏈 <b>NFL</b> (2 juegos  ·  3 picks  ·  1 💎 value)" in msg
    assert "Líneas: Draft Kings" in msg
    assert "Chiefs: 5-1 | Raiders: 1-5" in msg
