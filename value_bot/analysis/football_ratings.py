"""
Ratings de equipos de fútbol con los partidos terminados de football-data (puro).

Por equipo: promedios totales (goles a favor/en contra, % victorias y empates, forma de los
últimos FORM_GAMES) y, para el XG, ataque y defensa de local y de visitante como múltiplos de
la media de la competición, regresados a 1 con FOOTBALL_PRIOR_GAMES partidos de media (como
NFL_PRIOR_GAMES): con 2 partidos de local un 3-0 no convierte a nadie en el mejor ataque.
En el backtest el XG casa/fuera empata con el aditivo y ninguno le gana al cierre.
Goles a los 90' (sin prórroga), como se liquidan las apuestas.
"""
from collections import defaultdict
from typing import NamedTuple

from value_bot.scrapers.espn_lines import obj, to_float

FOOTBALL_PRIOR_GAMES = 30   # mejor Brier del backtest (4, 8, 15, 30); con menos los splits añaden ruido
MIN_RATED_GAMES = 3
FORM_GAMES = 5
FORM_POINTS = {"W": 3, "D": 1, "L": 0}


class Result(NamedTuple):
    kickoff: str
    home: str
    away: str
    home_goals: int
    away_goals: int


class _Game(NamedTuple):
    """Un partido desde el punto de vista de un equipo."""
    at_home: bool
    scored: int
    conceded: int

    @property
    def outcome(self) -> str:
        return "W" if self.scored > self.conceded else ("D" if self.scored == self.conceded else "L")


def fd_goals(score: object) -> tuple[int, int] | None:
    """Goles a los 90': `fullTime` en partidos normales, `regularTime` si hubo prórroga."""
    if not isinstance(score, dict):
        return None
    part = obj(score, "fullTime" if score.get("duration", "REGULAR") == "REGULAR" else "regularTime")
    home, away = to_float(part.get("home")), to_float(part.get("away"))
    return None if home is None or away is None else (int(home), int(away))


def parse_results(matches: object) -> list[Result]:
    """Partidos FINISHED con marcador y nombres, en orden cronológico."""
    results = []
    for m in matches if isinstance(matches, list) else []:
        if not isinstance(m, dict) or m.get("status") != "FINISHED":
            continue
        home, away = obj(m, "homeTeam").get("name"), obj(m, "awayTeam").get("name")
        goals = fd_goals(m.get("score"))
        if home and away and goals:
            results.append(Result(str(m.get("utcDate") or ""), home, away, *goals))
    return sorted(results, key=lambda r: r.kickoff)


def _regressed(goals: int, games: int, league_avg: float) -> float:
    """Goles por partido / media de la liga, con FOOTBALL_PRIOR_GAMES partidos de media."""
    if league_avg <= 0:
        return 1.0
    return round((goals + FOOTBALL_PRIOR_GAMES * league_avg) / (games + FOOTBALL_PRIOR_GAMES) / league_avg, 4)


def _team_stats(games: list[_Game], home_avg: float, away_avg: float) -> dict:
    played = len(games)
    home = [g for g in games if g.at_home]
    away = [g for g in games if not g.at_home]
    form = "".join(g.outcome for g in games[-FORM_GAMES:])
    outcomes = [g.outcome for g in games]
    return {
        "played": played,
        "goals_for_pg": round(sum(g.scored for g in games) / played, 2),
        "goals_against_pg": round(sum(g.conceded for g in games) / played, 2),
        "win_rate": round(outcomes.count("W") / played * 100, 1),
        "draw_rate": round(outcomes.count("D") / played * 100, 1),
        "form": form, "form_pts": sum(FORM_POINTS[c] for c in form),
        "home_attack": _regressed(sum(g.scored for g in home), len(home), home_avg),
        "home_defense": _regressed(sum(g.conceded for g in home), len(home), away_avg),
        "away_attack": _regressed(sum(g.scored for g in away), len(away), away_avg),
        "away_defense": _regressed(sum(g.conceded for g in away), len(away), home_avg),
        "league_home_gpg": round(home_avg, 3), "league_away_gpg": round(away_avg, 3),
    }


def team_ratings(matches: object) -> dict[str, dict]:
    """{equipo: stats} de los que tienen ≥ MIN_RATED_GAMES partidos terminados."""
    results = parse_results(matches)
    if not results:
        return {}
    home_avg = sum(r.home_goals for r in results) / len(results)
    away_avg = sum(r.away_goals for r in results) / len(results)
    games: dict[str, list[_Game]] = defaultdict(list)
    for r in results:
        games[r.home].append(_Game(True, r.home_goals, r.away_goals))
        games[r.away].append(_Game(False, r.away_goals, r.home_goals))
    return {team: _team_stats(g, home_avg, away_avg) for team, g in games.items() if len(g) >= MIN_RATED_GAMES}
