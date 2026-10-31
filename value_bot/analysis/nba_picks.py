"""Picks NBA: junta juegos + stats + líneas ESPN con el modelo y filtra contra la BD."""
import logging

from value_bot.analysis.market import SIDE_MARKETS
from value_bot.analysis.nba_model import analyze_nba_game as analyze_nba_market
from value_bot.config import MAX_PICKS_SPORT, MIN_CONFIDENCE, VALUE_THRESHOLD, nba_api_season
from value_bot.db import already_sent_for_event
from value_bot.sports.nba import get_best_nba_stats, get_nba_games

log = logging.getLogger(__name__)

# Sin calibración por historial: la probabilidad ya está anclada al mercado y la cuota es real.


def analyze_nba_game(game: dict) -> list[dict]:
    """Picks de un juego; sin líneas de mercado ni stats de ambos equipos no hay picks."""
    if not game.get("lines"):
        log.info("NBA %s vs %s: sin líneas de mercado (ESPN) → sin picks", game["home"], game["away"])
        return []
    h_stats, a_stats, splits, _h2h = get_best_nba_stats(game)
    if not (h_stats and a_stats):
        log.info("NBA %s vs %s: sin stats de ambos equipos → sin picks", game["home"], game["away"])
        return []
    return analyze_nba_market(game, h_stats, a_stats, splits, nba_api_season(),
                              min_conf=MIN_CONFIDENCE, value_threshold=VALUE_THRESHOLD)


def _already_sent(event_id: str | None, pick: dict) -> bool:
    """
    Un pick por partido y mercado aunque el juego aparezca en varias corridas, y
    una sola apuesta de lado (moneyline o spread, de cualquier equipo) por partido.
    """
    if not event_id:
        return False
    markets = SIDE_MARKETS if pick["market"] in SIDE_MARKETS else (pick["market"],)
    return any(already_sent_for_event(event_id, market, sport="nba") for market in markets)


def get_nba_picks() -> list[dict]:
    all_picks = []
    for game in get_nba_games():
        for pick in analyze_nba_game(game):
            if _already_sent(game.get("event_id"), pick):
                log.info("NBA dedup BD: %s ya enviado para %s vs %s", pick["market"], game["home"], game["away"])
                continue
            all_picks.append({
                **game, **pick,
                "sport":  "nba",
                "flag":   "🏆" if game.get("is_finals") else "🏀",
                "league": "NBA Finals" if game.get("is_finals") else "NBA",
            })

    # Value bets primero, luego por confianza
    all_picks.sort(key=lambda p: ((p.get("value") or 0) >= VALUE_THRESHOLD, p["confidence"]),
                   reverse=True)
    result = all_picks[:MAX_PICKS_SPORT]
    log.info("NBA picks: %d", len(result))
    return result
