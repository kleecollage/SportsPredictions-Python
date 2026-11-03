"""
Resolución automática de picks NBA/NFL con marcadores finales de ESPN.

El runner no corre todos los días, así que cada corrida resuelve todos los picks
pendientes cuyo juego ya empezó, consultando el scoreboard ESPN de la fecha del
juego (kickoff del payload, en hora del este), no la de la corrida.
"""
import logging
import re
from collections import defaultdict
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from value_bot import db
from value_bot.analysis.parlays import settle_parlay
from value_bot.config import ESPN_NBA_URL, ESPN_NFL_URL, http_get
from value_bot.scrapers.espn_lines import first_obj, obj, to_float
from value_bot.sports.nfl import ESPN_TZ

log = logging.getLogger(__name__)

SCOREBOARD_URLS = {"nba": ESPN_NBA_URL, "nfl": ESPN_NFL_URL}
LOOKBACK_DAYS = 180       # juegos más viejos ya no se consultan
FIRST_HALF_PERIODS = 2    # cuartos 1 y 2
# Juegos que no se jugarán en esa fecha → void (como en la casa). Suspendido no: se reanuda.
UNPLAYED_STATUSES = {"STATUS_POSTPONED": "pospuesto", "STATUS_CANCELED": "cancelado"}
EXPIRED_SOURCE = "lookback"

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


class UnplayedGame(NamedTuple):
    event_id: str
    home: str
    away: str
    status: str      # "pospuesto" | "cancelado"


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
    """Puntos por periodo; () si falta alguno o no es un número (ESPN manda nulls)."""
    lines = competitor.get("linescores")
    values = [to_float(line) if isinstance(line, dict) else None
              for line in (lines if isinstance(lines, list) else [])]
    return () if None in values else tuple(values)


def _events(scoreboard: object) -> Iterator[tuple[str, str, dict, dict]]:
    """(id, estado, local, visitante) de cada evento con exactamente un local y un visitante."""
    events = scoreboard.get("events") if isinstance(scoreboard, dict) else None
    for event in events if isinstance(events, list) else []:
        comp = first_obj(event.get("competitions") if isinstance(event, dict) else None)
        competitors = comp.get("competitors")
        competitors = [c for c in competitors if isinstance(c, dict)] if isinstance(competitors, list) else []
        homes = [c for c in competitors if c.get("homeAway") == "home"]
        aways = [c for c in competitors if c.get("homeAway") == "away"]
        if len(homes) == 1 and len(aways) == 1:
            status = obj(obj(comp, "status"), "type").get("name") or ""
            yield str(event.get("id") or ""), status, homes[0], aways[0]


def _name(competitor: dict) -> str:
    return obj(competitor, "team").get("displayName") or ""


def parse_final_scores(scoreboard: object) -> list[FinalScore]:
    """Scoreboard ESPN → marcadores de los juegos terminados (tolera nulls y formas raras)."""
    finals = []
    for event_id, status, home, away in _events(scoreboard):
        h_score, a_score = to_float(home.get("score")), to_float(away.get("score"))
        if not status.startswith("STATUS_FINAL") or h_score is None or a_score is None:
            continue
        finals.append(FinalScore(event_id, _name(home), _name(away), int(h_score), int(a_score),
                                 _periods(home), _periods(away)))
    return finals


def parse_unplayed_games(scoreboard: object) -> list[UnplayedGame]:
    """Juegos pospuestos o cancelados del scoreboard."""
    return [UnplayedGame(event_id, _name(home), _name(away), UNPLAYED_STATUSES[status])
            for event_id, status, home, away in _events(scoreboard) if status in UNPLAYED_STATUSES]


def _find_game[G: (FinalScore, UnplayedGame)](pick: dict, games: list[G]) -> G | None:
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

def _evaluate(pick: dict, game: FinalScore) -> str | None:
    """
    El pick_type usa los nombres del pick (p. ej. "Los Angeles Clippers" de api-sports) y
    ESPN puede usar otros ("LA Clippers"). Con event_id el juego es ese mismo, así que sus
    lados llevan los nombres del pick; si no, o si así no se resuelve, los de ESPN.
    """
    views = [game._replace(home=pick["home"], away=pick["away"])] if pick["payload"].get("event_id") else []
    for view in (*views, game):
        if (outcome := evaluate_pick(pick["pick_type"], view)) is not None:
            return outcome
    return None


def _resolve_one(pick: dict, finals: list[FinalScore], unplayed: list[UnplayedGame]) -> bool:
    if game := _find_game(pick, finals):
        outcome = _evaluate(pick, game)
        if outcome is None:
            return False
        db.save_result(pick["id"], outcome, f"{game.home_score}-{game.away_score}",
                       _profit(outcome, pick["est_odds"]))
        return True
    if game := _find_game(pick, unplayed):
        log.info("Pick %d: juego %s → void", pick["id"], game.status)
        db.save_result(pick["id"], "void", game.status, 0.0)
        return True
    return False


def _safe_resolve(pick: dict, finals: list[FinalScore], unplayed: list[UnplayedGame]) -> bool:
    try:
        return _resolve_one(pick, finals, unplayed)
    except Exception:
        log.exception("Pick %d: no se pudo resolver, sigue pendiente", pick["id"])
        return False


def _resolve_board(sport: str, game_date: str, picks: list[dict]) -> int:
    """Picks de un deporte y fecha; un scoreboard raro no frena a los demás."""
    board = http_get(SCOREBOARD_URLS[sport], params={"dates": game_date})
    if not isinstance(board, dict) or not isinstance(board.get("events"), list):
        log.warning("ESPN: scoreboard %s %s sin respuesta, %d picks siguen pendientes",
                    sport.upper(), game_date, len(picks))
        return 0
    try:
        finals, unplayed = parse_final_scores(board), parse_unplayed_games(board)
    except Exception:
        log.exception("ESPN: scoreboard %s %s ilegible, sus picks siguen pendientes", sport.upper(), game_date)
        return 0
    return sum(_safe_resolve(pick, finals, unplayed) for pick in picks)


def _is_expired(pick: dict, kickoff: datetime | None, oldest: datetime) -> bool:
    """Juego (o, sin kickoff legible, corrida) anterior a LOOKBACK_DAYS: ya no se consulta."""
    if kickoff is not None:
        return kickoff < oldest
    return str(pick["run_date"] or "") < oldest.date().isoformat()


def _void_expired(pick: dict) -> int:
    log.warning("Pick %d sin resolver tras %d días → void", pick["id"], LOOKBACK_DAYS)
    db.save_result(pick["id"], "void", None, 0.0, source=EXPIRED_SOURCE)
    return 1


def resolve_pending_picks(now: datetime | None = None) -> int:
    """
    Resuelve los picks NBA/NFL pendientes cuyo juego ya empezó (hasta
    LOOKBACK_DAYS atrás). Una consulta ESPN por deporte y fecha de juego.
    Juegos sin terminar o mercados no soportados quedan pendientes; pospuestos,
    cancelados y picks más viejos que LOOKBACK_DAYS quedan void (como los parlays).
    Retorna el número de picks resueltos.
    """
    now = now or datetime.now(timezone.utc)
    oldest = now - timedelta(days=LOOKBACK_DAYS)

    resolved = 0
    by_board: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for pick in db.get_unresolved_picks(tuple(SCOREBOARD_URLS)):
        kickoff = _kickoff(pick["payload"])
        if _is_expired(pick, kickoff, oldest):
            resolved += _void_expired(pick)
        elif kickoff is not None and kickoff <= now:
            by_board[(pick["sport"], kickoff.astimezone(ESPN_TZ).strftime("%Y%m%d"))].append(pick)

    for (sport, game_date), picks in by_board.items():
        resolved += _resolve_board(sport, game_date, picks)

    log.info("Resultados: %d picks resueltos (%d fechas consultadas)", resolved, len(by_board))
    return resolved


def resolve_parlays(now: datetime | None = None) -> int:
    """
    Liquida los parlays pendientes con los resultados ya guardados de sus piernas.
    Uno que sigue abierto pasado LOOKBACK_DAYS (sus piernas ya no se consultan) queda void.
    """
    oldest = ((now or datetime.now(timezone.utc)) - timedelta(days=LOOKBACK_DAYS)).date().isoformat()
    resolved = 0
    for parlay in db.get_pending_parlays():
        settled = settle_parlay(parlay["legs"])
        if settled is None and parlay["run_date"] < oldest:
            log.warning("Parlay %d sin resolver tras %d días → void", parlay["id"], LOOKBACK_DAYS)
            settled = ("void", 0.0)
        if settled:
            db.save_parlay_result(parlay["id"], *settled)
            resolved += 1
    log.info("Parlays: %d resueltos", resolved)
    return resolved
