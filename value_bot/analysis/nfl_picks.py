"""Picks NFL: junta datos ESPN + modelo y filtra contra la BD."""
import logging

from value_bot.analysis.nfl_model import SIDE_MARKETS, analyze_nfl_game, team_rating
from value_bot.config import MAX_PICKS_SPORT, MIN_CONFIDENCE, VALUE_THRESHOLD
from value_bot.sports.nfl import get_league_average, get_nfl_games, get_team_scores

from value_bot.db import already_sent_for_event, already_sent_side_bet

log = logging.getLogger(__name__)

# Sin calibración por historial: los factores de db.get_calibration_factors()
# salen de fútbol/NBA y la probabilidad NFL ya está anclada al mercado.


def _rating(team_id: str | None, league_avg: float) -> dict:
    if not team_id:
        return team_rating([], league_avg, current_season=False)
    team = get_team_scores(team_id)
    return team_rating(team.scores, league_avg, current_season=team.current,
                       previous=team.previous)


def _already_sent(event_id: str | None, pick: dict) -> bool:
    """
    Un pick por partido y mercado aunque el juego aparezca en varias corridas, y
    sin repetir el mismo equipo en moneyline y spread (son casi la misma apuesta).
    """
    if not event_id:
        return False
    if already_sent_for_event(event_id, pick["market"]):
        return True
    return pick["market"] in SIDE_MARKETS and already_sent_side_bet(event_id, pick["side"])


def get_nfl_picks() -> list[dict]:
    games = get_nfl_games()
    if not games:
        log.info("NFL picks: 0 (sin juegos programados)")
        return []

    avg = get_league_average()
    log.info("NFL media de liga: %.1f pts/equipo", avg)

    all_picks = []
    for game in games:
        home_rating = _rating(game["home_id"], avg)
        away_rating = _rating(game["away_id"], avg)
        for pick in analyze_nfl_game(game, home_rating, away_rating, avg,
                                     min_conf=MIN_CONFIDENCE, value_threshold=VALUE_THRESHOLD):
            if _already_sent(game.get("event_id"), pick):
                continue
            all_picks.append({**game, **pick, "sport": "nfl", "flag": "🏈", "league": "NFL"})

    # Value bets primero, luego por confianza
    all_picks.sort(key=lambda p: ((p.get("value") or 0) >= VALUE_THRESHOLD, p["confidence"]),
                   reverse=True)
    result = all_picks[:MAX_PICKS_SPORT]
    log.info("NFL picks: %d (de %d juegos)", len(result), len(games))
    return result
