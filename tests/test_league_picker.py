"""Selector de ligas: estado puro, teclas y árbol impreso (sin curses)."""
import curses

from value_bot.analysis.football_btts import Decision
from value_bot.tools import league_picker as lp

OPTIONS = (lp.LeagueOption("Premier League", 5), lp.LeagueOption("LaLiga", 3), lp.LeagueOption("Serie A", 0))


def test_cursor_wraps_and_toggle_marks_the_league_under_it():
    state = lp.PickerState(OPTIONS).move(-1)
    assert state.cursor == 2
    state = state.move(1).move(1).toggle()
    assert state.selected == {"LaLiga"} and state.cost == 3
    assert state.toggle().selected == frozenset()


def test_toggle_all_selects_everything_then_nothing():
    state = lp.PickerState(OPTIONS).toggle_all()
    assert state.cost == 8 and len(state.selected) == 3
    assert state.toggle_all().selected == frozenset()


def test_state_is_immutable():
    state = lp.PickerState(OPTIONS)
    state.toggle()
    assert state.selected == frozenset() and state.cursor == 0


def test_keys():
    state = lp.PickerState(OPTIONS)
    moved, done = lp.handle_key(state, curses.KEY_DOWN)
    assert (moved.cursor, done) == (1, None)
    marked, _ = lp.handle_key(moved, ord(" "))
    assert lp.handle_key(marked, 10) == (marked, True)
    assert lp.handle_key(marked, ord("q")) == (marked, False)
    assert lp.handle_key(marked, ord("z")) == (marked, None)


def test_empty_picker_does_not_crash():
    state = lp.PickerState(())
    assert state.move(1) == state and state.toggle() == state and state.cost == 0


def test_render_shows_cost_against_credits():
    lines = lp.render_lines(lp.PickerState(OPTIONS).toggle(), 120)
    assert "> [x] Premier League" in lines[2] and "hasta 5 créditos · restantes: 120" in lines[-2]
    assert "restantes: ?" in lp.render_lines(lp.PickerState(OPTIONS), None)[-2]


def test_without_a_terminal_nothing_is_chosen(monkeypatch):
    monkeypatch.setattr(lp.sys.stdin, "isatty", lambda: False, raising=False)
    assert lp.pick_leagues(list(OPTIONS), 100) == frozenset()
    assert lp.pick_leagues([lp.LeagueOption("Serie A", 0)], 100) == frozenset()


def test_decision_tree_is_printed_per_league():
    decisions = [Decision("A vs B", "LaLiga", ("liga seleccionada", "pedir cuota (1 crédito)")),
                 Decision("C vs D", "LaLiga", ("liga no seleccionada → skip",))]
    assert lp.render_decisions(decisions).splitlines() == [
        "⚽ LaLiga", "  A vs B", "    ├─ liga seleccionada", "    └─ pedir cuota (1 crédito)",
        "  C vs D", "    └─ liga no seleccionada → skip"]
