"""
Resolución automática de picks NBA/NFL con marcadores finales de ESPN.

El runner no corre todos los días, así que cada corrida resuelve todos los picks
pendientes cuyo juego ya empezó, consultando el scoreboard ESPN de la fecha del
juego (kickoff del payload, en hora del este), no la de la corrida.
"""
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from value_bot import db
from value_bot.config import ESPN_NBA_URL, ESPN_NFL_URL, http_get
from value_bot.sports.nfl import ESPN_TZ

log = logging.getLogger(__name__)

SCOREBOARD_URLS = {"nba": ESPN_NBA_URL, "nfl": ESPN_NFL_URL}
LOOKBACK_DAYS = 180       # juegos más viejos ya no se consultan
FIRST_HALF_PERIODS = 2    # cuartos 1 y 2

_NUM = r"(\d+(?:\.\d+)?)"
_FIRST_HALF = re.compile(rf"^1ª mitad (over|under) {_NUM}$")
_TEAM_TOTAL = re.compile(rf"^total (.+) (over|under) {_NUM}$")
_GAME_TOTAL = re.compile(rf"^(over|under) {_NUM}(?: |$)")
_SPREAD = re.compile(r"^h[aá]ndicap (.+) ([+-]\d+(?:\.\d+)?|pk)$")
_MONEYLINE = re.compile(r"^victoria (.+?)(?: \(local\))?$")


class FinalScore(NamedTuple):
    event_id: str
    home: str
    away: str
    home_score: int
    away_score: int
    home_periods: tuple[float, ...]
    away_periods: tuple[float, ...]

    @property
    def total(self) -> int:
        return self.home_score + self.away_score


# ── Evaluación (pura) ─────────────────────────────────────────────

def _sign(diff: float) -> str:
    diff = round(diff, 6)
    if diff > 0:
        return "win"
    return "loss" if diff < 0 else "void"


def _over_under(side: str, value: float, line: float) -> str:
    return _sign(value - line if side == "over" else line - value)


def _margin_for(score: FinalScore, team: str) -> int | None:
    """Margen final desde el punto de vista de `team` (nombre completo)."""
    if team == score.home.lower():
        return score.home_score - score.away_score
    if team == score.away.lower():
        return score.away_score - score.home_score
    return None


def _points_by_nickname(score: FinalScore, nickname: str) -> int | None:
    """Puntos del equipo cuyo nombre termina en `nickname` ("Knicks", "76ers")."""
    is_home = score.home.lower().split()[-1] == nickname
    is_away = score.away.lower().split()[-1] == nickname
    if is_home == is_away:
        return None
    return score.home_score if is_home else score.away_score


def _first_half_total(score: FinalScore) -> float | None:
    if min(len(score.home_periods), len(score.away_periods)) < FIRST_HALF_PERIODS:
        return None
    return (sum(score.home_periods[:FIRST_HALF_PERIODS])
            + sum(score.away_periods[:FIRST_HALF_PERIODS]))


def evaluate_pick(pick_type: str, score: FinalScore) -> str | None:
    """
    'win' | 'loss' | 'void' (push o empate en moneyline) según el marcador final.
    None si el mercado no se puede resolver con este marcador.
    """
    pt = " ".join(pick_type.lower().split())

    if m := _FIRST_HALF.match(pt):
        half = _first_half_total(score)
        return None if half is None else _over_under(m[1], half, float(m[2]))

    if m := _TEAM_TOTAL.match(pt):
        points = _points_by_nickname(score, m[1])
        return None if points is None else _over_under(m[2], points, float(m[3]))

    if m := _GAME_TOTAL.match(pt):
        return _over_under(m[1], score.total, float(m[2]))

    if m := _SPREAD.match(pt):
        margin = _margin_for(score, m[1])
        line = 0.0 if m[2] == "pk" else float(m[2])
        return None if margin is None else _sign(margin + line)

    if m := _MONEYLINE.match(pt):
        margin = _margin_for(score, m[1])
        return None if margin is None else _sign(margin)

    return None


def _profit(outcome: str, est_odds: float | None) -> float | None:
    if outcome == "void":
        return 0.0
    if outcome == "loss":
        return -1.0
    return round(est_odds - 1, 4) if est_odds else None


# ── ESPN (parsing puro) ──────────────────────────────────────────

def _periods(competitor: dict) -> tuple[float, ...]:
    values = []
    for line in competitor.get("linescores") or []:
        try:
            values.append(float(line.get("value")))
        except (TypeError, ValueError):
            return ()
    return tuple(values)


def parse_final_scores(scoreboard: dict) -> list[FinalScore]:
    """Scoreboard ESPN → marcadores de los juegos terminados."""
    finals = []
    for event in scoreboard.get("events") or []:
        comp = (event.get("competitions") or [{}])[0]
        if not comp.get("status", {}).get("type", {}).get("name", "").startswith("STATUS_FINAL"):
            continue
        competitors = comp.get("competitors") or []
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not (home and away):
            continue
        try:
            h_score, a_score = int(float(home["score"])), int(float(away["score"]))
        except (KeyError, TypeError, ValueError):
            continue
        finals.append(FinalScore(
            str(event.get("id", "")),
            home.get("team", {}).get("displayName", ""),
            away.get("team", {}).get("displayName", ""),
            h_score, a_score, _periods(home), _periods(away),
        ))
    return finals


def _find_game(pick: dict, games: list[FinalScore]) -> FinalScore | None:
    """Por event_id ESPN si el pick lo tiene (NFL); si no, por nombres completos."""
    event_id = pick["payload"].get("event_id")
    if event_id:
        return next((g for g in games if g.event_id == str(event_id)), None)
    home, away = pick["home"].lower(), pick["away"].lower()
    return next((g for g in games if g.home.lower() == home and g.away.lower() == away), None)


def _kickoff(payload: dict) -> datetime | None:
    """Kickoff ISO con zona horaria; None si falta o viene en otro formato (Flashscore)."""
    try:
        kickoff = datetime.fromisoformat(str(payload.get("kickoff", "")).replace("Z", "+00:00"))
    except ValueError:
        return None
    return kickoff if kickoff.tzinfo else None


# ── Resolución (BD + red) ─────────────────────────────────────────

def _resolve_one(pick: dict, games: list[FinalScore]) -> bool:
    game = _find_game(pick, games)
    if not game:
        return False
    outcome = evaluate_pick(pick["pick_type"], game)
    if outcome is None:
        return False
    db.save_result(pick["id"], outcome, f"{game.home_score}-{game.away_score}",
                   _profit(outcome, pick["est_odds"]))
    return True


def resolve_pending_picks(now: datetime | None = None) -> int:
    """
    Resuelve los picks NBA/NFL pendientes cuyo juego ya empezó (hasta
    LOOKBACK_DAYS atrás). Una consulta ESPN por deporte y fecha de juego.
    Juegos sin terminar o mercados no soportados quedan pendientes.
    Retorna el número de picks resueltos.
    """
    now = now or datetime.now(timezone.utc)
    oldest = now - timedelta(days=LOOKBACK_DAYS)

    by_board: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for pick in db.get_unresolved_picks(tuple(SCOREBOARD_URLS)):
        kickoff = _kickoff(pick["payload"])
        if kickoff is None or not oldest <= kickoff <= now:
            continue
        game_date = kickoff.astimezone(ESPN_TZ).strftime("%Y%m%d")
        by_board[(pick["sport"], game_date)].append(pick)

    resolved = 0
    for (sport, game_date), picks in by_board.items():
        games = parse_final_scores(http_get(SCOREBOARD_URLS[sport], params={"dates": game_date}))
        resolved += sum(_resolve_one(pick, games) for pick in picks)

    log.info("Resultados: %d picks resueltos (%d fechas consultadas)", resolved, len(by_board))
    return resolved
