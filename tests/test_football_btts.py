"""Árbol de decisión de BTTS: cada rama y el pick anclado al mercado."""
import pytest

from value_bot import db
from value_bot import main as main_mod
from value_bot.analysis import football_btts as fb
from value_bot.analysis import football_picks as fp
from value_bot.run_context import RunContext

FIX = {"sport": "football", "home": "Home FC", "away": "Away FC", "league": "Premier League"}


def _stats(gf=1.5, ga=1.3, played=10):
    return {"goals_for_pg": gf, "goals_against_pg": ga, "played": played}


def _gate(hs=None, as_=None, **kw):
    event_id = kw.pop("event_id", "ev1")
    args = {"selected": {"Premier League"}, "sent_before": False, "find_event": lambda: event_id,
            "credits_left": 200, "reserve": 60, **kw}
    return fb.btts_gate(FIX, hs or _stats(), as_ or _stats(), **args)


def test_full_path_asks_for_the_odds():
    gate = _gate()
    assert gate.spend and not gate.stop and gate.path[-1].startswith("pedir cuota")
    assert gate.path[:2] == ("liga seleccionada", "stats de ambos")


@pytest.mark.parametrize("kw, last", [
    ({"selected": set()}, "liga no seleccionada → skip"),
    ({"as_": _stats(played=3)}, "sin 6 partidos de ambos → skip"),
    ({"sent_before": True}, "el partido ya tiene pick → skip"),
    ({"event_id": None}, "sin evento en The Odds API → skip"),
])
def test_each_skip_branch_spends_nothing(kw, last):
    gate = _gate(**kw)
    assert gate.path[-1] == last and not gate.spend and gate.pick is None


def test_event_is_only_looked_up_after_stats_and_dedup():
    def boom():
        raise AssertionError("no se busca el evento")
    for kw in ({"selected": set()}, {"as_": _stats(played=3)}, {"sent_before": True}):
        fb.btts_gate(FIX, kw.get("hs", _stats()), kw.get("as_", _stats()), find_event=boom,
                     selected=kw.get("selected", {"Premier League"}), sent_before=kw.get("sent_before", False),
                     credits_left=200, reserve=60)


@pytest.mark.parametrize("gf, ga", [(0.4, 0.4), (3.5, 3.5)])
def test_implausible_model_probability_does_not_spend(gf, ga):
    gate = _gate(_stats(gf, ga), _stats(gf, ga))
    assert not gate.spend and "no se gasta" in gate.path[-1]


def test_low_credits_stop_the_run():
    gate = _gate(credits_left=60)
    assert gate.stop and not gate.spend and "stop" in gate.path[-1]


def test_unknown_credits_do_not_block():
    assert _gate(credits_left=None).spend


def test_value_bet_is_blended_with_the_no_vig_market():
    gate = _gate(_stats(1.8, 1.6), _stats(1.8, 1.6))      # XG 1.7 + 1.7 → modelo 67 %
    decision = fb.btts_evaluate(gate, FIX, (2.5, 1.5), min_conf=62, value_threshold=0.04)
    market = (1 / 2.5) / (1 / 2.5 + 1 / 1.5)
    prob = round(0.15 * gate.model_p + 0.85 * market, 3)
    assert decision.pick["type"] == "Ambos Marcan (BTTS)" and decision.pick["our_prob"] == prob
    assert decision.pick["est_odds"] == 2.5 and decision.path[-1].endswith("→ pick")


def test_fair_market_is_discarded_with_the_reason():
    decision = fb.btts_evaluate(_gate(), FIX, (1.75, 2.05), min_conf=62, value_threshold=0.04)
    assert decision.pick is None and "descartado" in decision.path[-1]


def test_missing_odds_are_discarded():
    decision = fb.btts_evaluate(_gate(), FIX, None, min_conf=62, value_threshold=0.04)
    assert decision.pick is None and decision.path[-1] == "sin cuota BTTS → descartado"


# ── Orquestación (football_picks.get_btts_picks / main --btts) ────

STRONG = _stats(1.8, 1.6)


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "picks.db")
    db.init_db()
    ctx = RunContext()
    ctx.football.fixtures.extend([
        {**FIX, "date": "2026-11-08", "odds_key": "soccer_epl"},
        {"sport": "football", "home": "X", "away": "Y", "league": "LaLiga", "date": "2026-11-08", "odds_key": "soccer_spain_la_liga"},
    ])
    ctx.football.stats.update({("Premier League", "Home FC"): STRONG, ("Premier League", "Away FC"): STRONG,
                               ("LaLiga", "X"): STRONG, ("LaLiga", "Y"): STRONG})
    ctx.football.credits.left = 200
    asked = []
    monkeypatch.setattr(fp, "get_real_odds", lambda c, key, h, a, date=None: {"odds_event_id": f"ev-{h}"})
    monkeypatch.setattr(fp, "get_btts_odds", lambda c, key, ev: asked.append(ev) or (2.5, 1.5))
    return ctx, asked


def test_only_the_chosen_leagues_spend_credits(run):
    ctx, asked = run
    offered = {}
    picks, decisions = fp.get_btts_picks(ctx, [], lambda counts, credits: offered.update(counts) or {"Premier League"})
    assert offered == {"Premier League": 1, "LaLiga": 1}
    assert asked == ["ev-Home FC"] and [p["type"] for p in picks] == ["Ambos Marcan (BTTS)"]
    assert [d.match for d in decisions] == ["Home FC vs Away FC"]


def test_match_with_a_pick_in_this_run_is_not_queried(run):
    ctx, asked = run
    current = [{"home": "Home FC", "away": "Away FC"}]
    picks, decisions = fp.get_btts_picks(ctx, current, lambda counts, credits: {"Premier League"})
    assert asked == [] and picks == [] and decisions[0].path[-1] == "el partido ya tiene pick → skip"


def test_league_without_stats_never_asks_for_odds(run, monkeypatch):
    ctx, _ = run
    monkeypatch.setattr(fp, "get_real_odds", lambda *a: pytest.fail("cuesta créditos"))
    ctx.football.stats.clear()
    picks, decisions = fp.get_btts_picks(ctx, [], lambda counts, credits: {"Premier League"})
    assert picks == [] and decisions[0].path[-1].startswith("sin 6 partidos")


def test_btts_picks_respect_the_per_sport_cap(run, monkeypatch):
    ctx, asked = run
    monkeypatch.setattr(fp, "MAX_PICKS_SPORT", 1)
    current = [{"home": "P", "away": "Q", "league": "Serie A", "type": "Victoria P"}]
    picks, decisions = fp.get_btts_picks(ctx, current, lambda counts, credits: {"Premier League", "LaLiga"})
    assert picks == [] and asked == [] and "tope" in decisions[0].path[-1]


def test_btts_picks_respect_the_per_league_type_cap(run, monkeypatch):
    ctx, asked = run
    monkeypatch.setattr(fp, "MAX_PICKS_LEAGUE_TYPE", 1)
    ctx.football.fixtures[:] = [{**FIX, "date": "2026-11-08", "odds_key": "soccer_epl"},
                                {**FIX, "home": "Away FC", "away": "Home FC", "date": "2026-11-09",
                                 "odds_key": "soccer_epl"}]
    picks, decisions = fp.get_btts_picks(ctx, [], lambda counts, credits: {"Premier League"})
    assert len(picks) == 1 and len(asked) == 1 and "tope" in decisions[1].path[-1]


def test_running_out_of_credits_stops(run):
    ctx, asked = run
    ctx.football.credits.left = 60
    picks, decisions = fp.get_btts_picks(ctx, [], lambda counts, credits: {"Premier League", "LaLiga"})
    assert asked == [] and len(decisions) == 1 and decisions[0].stop


def test_daily_run_never_opens_the_btts_step(run, monkeypatch):
    ctx, _ = run
    monkeypatch.setattr(main_mod, "get_btts_picks", lambda *a: pytest.fail("no debe gastar créditos"))
    assert main_mod._btts_picks(ctx, [], []) == []


def test_btts_flag_runs_the_step_and_prints_the_tree(run, monkeypatch, capsys):
    ctx, _ = run
    monkeypatch.setattr("value_bot.sports_registry.SPORTS_ENABLED", {"football": True})
    monkeypatch.setattr(main_mod, "pick_leagues", lambda options, credits: frozenset({"LaLiga"}))
    picks = main_mod._btts_picks(ctx, [], ["--btts"])
    assert [p["home"] for p in picks] == ["X"] and "X vs Y" in capsys.readouterr().out
