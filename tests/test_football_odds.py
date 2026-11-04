"""The Odds API para fútbol: 1X2 y totales del mismo bookmaker, línea de consenso y créditos."""
import pytest

from value_bot.sports import football_odds as fo


def _book(key, h2h=None, totals=None):
    markets = []
    if h2h:
        markets.append({"key": "h2h", "outcomes": [{"name": n, "price": p} for n, p in h2h]})
    if totals:
        markets.append({"key": "totals", "outcomes": [{"name": n, "price": p, "point": pt} for n, p, pt in totals]})
    return {"key": key, "markets": markets}


H2H = [("Galatasaray", 2.6), ("VfB Stuttgart", 2.5), ("Draw", 3.6)]


def _game(*books, home="Galatasaray", away="VfB Stuttgart", event_id="ev1", commence=None):
    return {"id": event_id, "home_team": home, "away_team": away, "bookmakers": list(books),
            **({"commence_time": commence} if commence else {})}


# ── Totales ───────────────────────────────────────────────────────

def test_totals_use_the_consensus_line_and_one_bookmaker():
    game = _game(
        _book("a", totals=[("Over", 1.8, 2.5), ("Under", 2.0, 2.5)]),
        _book("b", totals=[("Over", 2.0, 3.5), ("Under", 1.9, 3.5)]),
        _book("c", totals=[("Over", 2.1, 3.5), ("Under", 1.8, 3.5)]),
    )
    assert fo.parse_totals(game) == {"total_line": 3.5, "over_odds": 2.0, "under_odds": 1.9}


def test_totals_never_mix_bookmakers():
    # b tiene solo Over en 3.5 y c solo Under: ninguno trae los dos lados
    game = _game(
        _book("b", totals=[("Over", 2.0, 3.5)]),
        _book("c", totals=[("Under", 1.8, 3.5)]),
    )
    assert fo.parse_totals(game) == {}


@pytest.mark.parametrize("line", [2.25, 2.75])
def test_quarter_lines_are_not_a_market(line):
    game = _game(_book("a", totals=[("Over", 1.9, line), ("Under", 1.9, line)]))
    assert fo.parse_totals(game) == {}


@pytest.mark.parametrize("price", [None, "x", 1.0, float("nan")])
def test_unusable_prices_are_skipped(price):
    game = _game(_book("a", totals=[("Over", price, 2.5), ("Under", 1.9, 2.5)]))
    assert fo.parse_totals(game) == {}


def test_h2h_is_read_by_market_key_not_position():
    game = _game(_book("a", totals=[("Over", 1.8, 2.5), ("Under", 2.0, 2.5)], h2h=H2H))
    assert fo.parse_h2h(game) == {"home_odds": 2.6, "away_odds": 2.5, "draw_odds": 3.6}


def test_h2h_in_a_derby_gives_each_team_its_own_price():
    game = _game(_book("a", h2h=[("Manchester City", 1.8), ("Manchester United", 4.5), ("Draw", 3.9)]),
                 home="Manchester City", away="Manchester United")
    assert fo.parse_h2h(game) == {"home_odds": 1.8, "away_odds": 4.5, "draw_odds": 3.9}


def test_h2h_needs_all_three_prices_from_one_bookmaker():
    game = _game(_book("a", h2h=H2H[:2]), _book("b", h2h=[("Galatasaray", 2.7), ("VfB Stuttgart", 2.4),
                                                          ("Draw", 3.5)]))
    assert fo.parse_h2h(game) == {"home_odds": 2.7, "away_odds": 2.4, "draw_odds": 3.5}
    assert fo.parse_h2h(_game(_book("a", h2h=H2H[:2]))) == {}


@pytest.mark.parametrize("home, away", [(None, "VfB Stuttgart"), ("Galatasaray", "Galatasaray")])
def test_h2h_without_two_distinct_teams_is_not_a_market(home, away):
    book = _book("a", h2h=[(home, 2.6), (away, 2.5), ("Draw", 3.6)])
    assert fo.parse_h2h(_game(book, home=home, away=away)) == {}


# ── Nombres football-data ↔ The Odds API ─────────────────────────

@pytest.mark.parametrize("fd_name, odds_name", [
    ("FC Bayern München", "Bayern Munich"),
    ("FC Barcelona", "Barcelona"),
    ("FC Internazionale Milano", "Inter Milan"),
    ("SSC Napoli", "Napoli"),
    ("AFC Bournemouth", "Bournemouth"),
    ("Club Atlético de Madrid", "Atlético Madrid"),
    ("Brighton & Hove Albion FC", "Brighton and Hove Albion"),
    ("1. FC Union Berlin", "Union Berlin"),
    ("Paris Saint-Germain FC", "Paris Saint Germain"),
    ("Galatasaray SK", "Galatasaray"),
    ("Olympique Lyonnais", "Lyon"),
    ("Stade Rennais FC 1901", "Rennes"),
    ("Stade Brestois 29", "Brest"),
    ("SK Slavia Praha", "Slavia Prague"),
    ("Sporting Clube de Portugal", "Sporting Lisbon"),
])
def test_football_data_names_match_the_odds_api(fd_name, odds_name):
    assert fo.same_team(fd_name, odds_name)


@pytest.mark.parametrize("fd_name, odds_name", [
    ("Manchester City FC", "Manchester United"),
    ("AC Milan", "Inter Milan"),
    ("Real Madrid CF", "Real Sociedad"),
    ("FC", "FC Porto"),
])
def test_different_teams_do_not_match(fd_name, odds_name):
    assert not fo.same_team(fd_name, odds_name)


PSG_LENS = _game(home="Paris Saint Germain", away="Lens", event_id="psg", commence="2026-11-08T20:00:00Z")
PFC_LENS = _game(home="Paris FC", away="Lens", event_id="pfc", commence="2026-11-02T20:00:00Z")


def test_events_on_another_day_are_not_the_fixture():
    assert fo._find_game([PSG_LENS, PFC_LENS], "Paris FC", "RC Lens", "2026-11-02")["id"] == "pfc"
    assert fo._find_game([PSG_LENS], "Paris FC", "RC Lens", "2026-11-02") is None


def test_event_day_is_the_et_day():
    late = _game(home="Paris FC", away="Lens", commence="2026-11-03T02:00:00Z")   # 21:00 ET del 2-nov
    assert fo._find_game([late], "Paris FC", "RC Lens", "2026-11-02") is late


def test_exact_names_win_over_a_partial_match():
    same_day = {**PSG_LENS, "commence_time": "2026-11-02T15:00:00Z"}
    assert fo._find_game([same_day, PFC_LENS], "Paris FC", "RC Lens", "2026-11-02")["id"] == "pfc"


def test_two_partial_matches_are_ambiguous(caplog):
    a = _game(home="Paris Saint Germain", away="Lens", event_id="a")
    b = _game(home="Paris 13 Atletico", away="Lens", event_id="b")
    assert fo._find_game([a, b], "Paris", "RC Lens") is None and "ambiguo" in caplog.text


# ── get_real_odds ─────────────────────────────────────────────────

@pytest.fixture
def odds_api(monkeypatch):
    calls = []

    def fake(path, params):
        calls.append(params["markets"])
        game = _game(_book("a", h2h=H2H, totals=[("Over", 1.8, 2.5), ("Under", 2.0, 2.5)]))
        return [game], 300
    monkeypatch.setattr(fo, "THE_ODDS_API_KEY", "key-de-prueba")
    monkeypatch.setattr(fo, "FOOTBALL_TOTALS_ENABLED", True)
    monkeypatch.setattr(fo, "_odds_get", fake)
    return calls


def test_real_odds_bring_result_totals_and_event_id(odds_api, ctx):
    odds = fo.get_real_odds(ctx, "soccer_uefa_champs_league", "Galatasaray SK", "VfB Stuttgart")
    assert odds == {"home_odds": 2.6, "away_odds": 2.5, "draw_odds": 3.6, "total_line": 2.5,
                    "over_odds": 1.8, "under_odds": 2.0, "odds_event_id": "ev1"}
    assert odds_api == ["h2h,totals"] and ctx.football.credits.left == 300


def test_with_totals_off_only_results_are_requested(odds_api, monkeypatch, ctx):
    monkeypatch.setattr(fo, "FOOTBALL_TOTALS_ENABLED", False)
    fo.get_real_odds(ctx, "soccer_epl", "Galatasaray", "VfB Stuttgart")
    assert odds_api == ["h2h"]


def test_one_request_per_league_per_run(odds_api, ctx):
    for _ in range(3):
        fo.get_real_odds(ctx, "soccer_epl", "Galatasaray", "VfB Stuttgart")
    assert len(odds_api) == 1


def test_low_credits_only_ask_for_results(odds_api, caplog, ctx):
    ctx.football.credits.left = fo.ODDS_CREDITS_RESERVE - 1
    fo.get_real_odds(ctx, "soccer_epl", "Galatasaray", "VfB Stuttgart")
    assert odds_api == ["h2h"] and "créditos" in caplog.text


def test_real_odds_skip_an_event_from_another_day(odds_api, ctx):
    assert fo.get_real_odds(ctx, "soccer_epl", "Galatasaray", "VfB Stuttgart", date="2026-11-02") is not None
    ctx.football.odds["soccer_epl"] = [{**ctx.football.odds["soccer_epl"][0], "commence_time": "2026-11-09T19:00:00Z"}]
    assert fo.get_real_odds(ctx, "soccer_epl", "Galatasaray", "VfB Stuttgart", date="2026-11-02") is None


def test_unknown_match_has_no_odds(odds_api, ctx):
    assert fo.get_real_odds(ctx, "soccer_epl", "Real Madrid", "Barcelona") is None


def test_fixture_without_event_warns_once_per_run(odds_api, caplog, ctx):
    for _ in range(2):
        fo.get_real_odds(ctx, "soccer_epl", "Real Madrid CF", "FC Barcelona")
    warnings = [r for r in caplog.records if "sin evento" in r.getMessage()]
    assert len(warnings) == 1 and "Real Madrid CF" in warnings[0].getMessage()


class _Resp:
    def __init__(self, status=200, remaining="250", body=None):
        self.status_code, self.headers, self._body = status, {"x-requests-remaining": remaining}, body

    def json(self):
        return self._body


@pytest.mark.parametrize("remaining, credits", [("250", 250), ("249.0", 249), ("n/a", None), ("", None)])
def test_credits_header_never_breaks_the_request(monkeypatch, remaining, credits):
    monkeypatch.setattr(fo.requests, "get", lambda *a, **kw: _Resp(remaining=remaining, body=[]))
    assert fo._odds_get("/sports/soccer_epl/odds/", {}) == ([], credits)


def test_without_key_or_sport_no_request(odds_api, monkeypatch, ctx):
    monkeypatch.setattr(fo, "THE_ODDS_API_KEY", "")
    assert fo.get_real_odds(ctx, "soccer_epl", "Galatasaray", "VfB Stuttgart") is None
    assert fo.get_real_odds(ctx, "", "Galatasaray", "VfB Stuttgart") is None
    assert odds_api == []


def test_unexpected_response_means_no_odds(monkeypatch, ctx):
    monkeypatch.setattr(fo, "THE_ODDS_API_KEY", "key-de-prueba")
    monkeypatch.setattr(fo, "_odds_get", lambda path, params: ({"message": "error"}, None))
    assert fo.get_real_odds(ctx, "soccer_epl", "Galatasaray", "VfB Stuttgart") is None


# ── BTTS por evento ───────────────────────────────────────────────

def _btts_book(key, yes, no):
    return {"key": key, "markets": [{"key": "btts", "outcomes": [{"name": "Yes", "price": yes},
                                                                 {"name": "No", "price": no}]}]}


def test_btts_comes_from_one_bookmaker_with_both_prices():
    game = _game(_btts_book("a", None, 1.9), _btts_book("b", 2.1, 1.7))
    assert fo.parse_btts(game) == (2.1, 1.7)
    assert fo.parse_btts(_game(_btts_book("a", "x", 1.9))) is None and fo.parse_btts([]) is None


def test_btts_odds_ask_for_one_event_and_track_credits(monkeypatch, ctx):
    calls = []

    def fake(path, params):
        calls.append((path, params["markets"]))
        return _game(_btts_book("a", 2.0, 1.8)), 120
    monkeypatch.setattr(fo, "THE_ODDS_API_KEY", "key-de-prueba")
    monkeypatch.setattr(fo, "_odds_get", fake)
    assert fo.get_btts_odds(ctx, "soccer_epl", "ev1") == (2.0, 1.8)
    assert calls == [("/sports/soccer_epl/events/ev1/odds", "btts")] and ctx.football.credits.left == 120
    assert fo.get_btts_odds(ctx, "soccer_epl", "") is None and len(calls) == 1
