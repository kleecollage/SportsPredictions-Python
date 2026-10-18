"""Picks NFL: junta datos ESPN + modelo y filtra contra la BD."""
import logging
from datetime import datetime

from value_bot.analysis.nfl_model import analyze_nfl_game, team_rating
from value_bot.config import MAX_PICKS_SPORT, MIN_CONFIDENCE, VALUE_THRESHOLD
from value_bot.sports.nfl import get_nfl_games, get_team_scores, league_average

try:
    from value_bot.db import already_sent_today as _already_sent
except ImportError:
    def _already_sent(*_):
        return False

log = logging.getLogger(__name__)

# Sin calibración por historial: los factores de db.get_calibration_factors()
# salen de fútbol/NBA y la probabilidad NFL ya está anclada al mercado.


def get_nfl_picks() -> list[dict]:
    games = get_nfl_games()
    if not games:
        log.info("NFL picks: 0 (sin juegos programados)")
        return []

    team_ids = {tid for g in games for tid in (g["home_id"], g["away_id"]) if tid}
    scores = {tid: get_team_scores(tid) for tid in team_ids}
    avg = league_average(list(scores.values()))
    log.info("NFL media de liga: %.1f pts/equipo (%d equipos)", avg, len(scores))

    today = datetime.now().strftime("%Y-%m-%d")
    all_picks = []
    for game in games:
        home_rating = team_rating(scores.get(game["home_id"], []), avg)
        away_rating = team_rating(scores.get(game["away_id"], []), avg)
        for pick in analyze_nfl_game(game, home_rating, away_rating, avg,
                                     min_conf=MIN_CONFIDENCE, value_threshold=VALUE_THRESHOLD):
            if _already_sent(game["home"], game["away"], pick["type"], today):
                continue
            all_picks.append({**game, **pick, "sport": "nfl", "flag": "🏈", "league": "NFL"})

    # Value bets primero, luego por confianza
    all_picks.sort(key=lambda p: ((p.get("value") or 0) >= VALUE_THRESHOLD, p["confidence"]),
                   reverse=True)
    result = all_picks[:MAX_PICKS_SPORT]
    log.info("NFL picks: %d (de %d juegos)", len(result), len(games))
    return result
