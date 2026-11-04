"""Picks NFL: junta datos ESPN + modelo y filtra contra la BD."""
import logging

from value_bot.analysis.market import rank_picks
from value_bot.analysis.nfl_model import analyze_nfl_game, is_pickable_week, team_rating
from value_bot.config import MAX_PICKS_SPORT, MIN_CONFIDENCE, VALUE_THRESHOLD
from value_bot.sports.nfl import get_league_average, get_nfl_games, get_team_scores

from value_bot.db import already_sent_bet
from value_bot.store import store

log = logging.getLogger(__name__)

# Sin calibración por historial: los factores de db.get_calibration_factors()
# salen de fútbol/NBA y la probabilidad NFL ya está anclada al mercado.


def _rating(team_id: str | None, league_avg: float) -> dict:
    if not team_id:
        return team_rating([], league_avg, current_season=False)
    team = get_team_scores(team_id)
    return team_rating(team.scores, league_avg, current_season=team.current,
                       previous=team.previous)


def _has_data(rating: dict) -> bool:
    """Sin juegos (ni de la temporada anterior) el rating es la media de liga: el modelo
    solo vería la localía y empujaría underdogs locales con value ficticio."""
    return bool(rating["games"] or rating["previous_games"])


def get_nfl_picks() -> list[dict]:
    scheduled = get_nfl_games()
    store.games_analyzed["nfl"] = len(scheduled)
    games = [g for g in scheduled if is_pickable_week(g)]
    if len(games) < len(scheduled):
        log.info("NFL: %d juegos de semanas 1–2 sin picks (ratings con datos del año anterior)",
                 len(scheduled) - len(games))
    if not games:
        log.info("NFL picks: 0 (sin juegos elegibles)")
        return []

    avg = get_league_average()
    log.info("NFL media de liga: %.1f pts/equipo", avg)

    all_picks = []
    for game in games:
        home_rating = _rating(game["home_id"], avg)
        away_rating = _rating(game["away_id"], avg)
        if not (_has_data(home_rating) and _has_data(away_rating)):
            log.info("NFL %s vs %s: sin marcadores de ambos equipos → sin picks", game["home"], game["away"])
            continue
        for pick in analyze_nfl_game(game, home_rating, away_rating, avg,
                                     min_conf=MIN_CONFIDENCE, value_threshold=VALUE_THRESHOLD):
            if already_sent_bet(game.get("event_id"), pick["market"], sport="nfl"):
                continue
            all_picks.append({**game, **pick, "sport": "nfl", "flag": "🏈", "league": "NFL"})

    # Value bets primero, luego por confianza
    result = rank_picks(all_picks, VALUE_THRESHOLD)[:MAX_PICKS_SPORT]
    log.info("NFL picks: %d (de %d juegos)", len(result), len(games))
    return result
