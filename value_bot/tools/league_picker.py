"""
Selector de ligas por teclado (curses) para gastar créditos de The Odds API en BTTS.

↑/↓ (o k/j) mover · espacio marcar · a todas/ninguna · Enter confirmar · q/Esc salir sin gastar.
Cada liga muestra cuántos partidos pasarían el árbol antes del gasto (= créditos máximos) frente
a los créditos restantes. El estado (`PickerState`) es puro y se testea sin terminal.
"""
import curses
import logging
import sys
from typing import NamedTuple

from value_bot.analysis.football_btts import Decision

log = logging.getLogger(__name__)

KEYS_UP = (curses.KEY_UP, ord("k"))
KEYS_DOWN = (curses.KEY_DOWN, ord("j"))
KEYS_CONFIRM = (curses.KEY_ENTER, 10, 13)
KEYS_QUIT = (ord("q"), 27)


class LeagueOption(NamedTuple):
    league: str
    candidates: int       # partidos que pedirían cuota (1 crédito cada uno)


class PickerState(NamedTuple):
    options: tuple[LeagueOption, ...]
    cursor: int = 0
    selected: frozenset[str] = frozenset()

    def move(self, delta: int) -> "PickerState":
        if not self.options:
            return self
        return self._replace(cursor=(self.cursor + delta) % len(self.options))

    def toggle(self) -> "PickerState":
        if not self.options:
            return self
        league = self.options[self.cursor].league
        return self._replace(selected=self.selected ^ {league})

    def toggle_all(self) -> "PickerState":
        every = frozenset(o.league for o in self.options)
        return self._replace(selected=frozenset() if self.selected == every else every)

    @property
    def cost(self) -> int:
        return sum(o.candidates for o in self.options if o.league in self.selected)


def handle_key(state: PickerState, key: int) -> tuple[PickerState, bool | None]:
    """(nuevo estado, terminado): True = confirmar, False = salir sin gastar, None = seguir."""
    if key in KEYS_UP:
        return state.move(-1), None
    if key in KEYS_DOWN:
        return state.move(1), None
    if key == ord(" "):
        return state.toggle(), None
    if key == ord("a"):
        return state.toggle_all(), None
    if key in KEYS_CONFIRM:
        return state, True
    if key in KEYS_QUIT:
        return state, False
    return state, None


def render_lines(state: PickerState, credits_left: int | None) -> list[str]:
    left = "?" if credits_left is None else str(credits_left)
    lines = ["BTTS bajo demanda — ligas donde gastar créditos de The Odds API", ""]
    for i, opt in enumerate(state.options):
        mark = "x" if opt.league in state.selected else " "
        arrow = ">" if i == state.cursor else " "
        lines.append(f"{arrow} [{mark}] {opt.league:<24} hasta {opt.candidates} créditos")
    lines += ["", f"Seleccionado: hasta {state.cost} créditos · restantes: {left}",
              "↑/↓ mover · espacio marcar · a todas · Enter confirmar · q salir"]
    return lines


def _curses_loop(screen, state: PickerState, credits_left: int | None) -> frozenset[str]:
    curses.curs_set(0)
    while True:
        screen.erase()
        height, width = screen.getmaxyx()
        for row, line in enumerate(render_lines(state, credits_left)[:height - 1]):
            screen.addnstr(row, 0, line, width - 1)
        screen.refresh()
        state, done = handle_key(state, screen.getch())
        if done is not None:
            return state.selected if done else frozenset()


def pick_leagues(options: list[LeagueOption], credits_left: int | None) -> frozenset[str]:
    """Ligas elegidas en la terminal; ninguna si no hay terminal interactiva o no hay opciones."""
    options = [o for o in options if o.candidates > 0]
    if not options:
        print("BTTS: ningún partido pasa el árbol antes del gasto; no hay nada que elegir.")
        return frozenset()
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        log.warning("BTTS: sin terminal interactiva → no se elige ninguna liga")
        return frozenset()
    return curses.wrapper(_curses_loop, PickerState(tuple(options)), credits_left)


def render_decisions(decisions: list[Decision]) -> str:
    """Árbol de cada partido evaluado, agrupado por liga."""
    out = []
    for league in dict.fromkeys(d.league for d in decisions):
        out.append(f"⚽ {league}")
        for d in (d for d in decisions if d.league == league):
            out.append(f"  {d.match}")
            out += [f"    {'└' if i == len(d.path) - 1 else '├'}─ {step}" for i, step in enumerate(d.path)]
    return "\n".join(out)
