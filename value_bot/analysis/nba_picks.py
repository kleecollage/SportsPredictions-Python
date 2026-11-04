"""Picks NBA: junta juegos + stats + líneas ESPN con el modelo y filtra contra la BD."""
import logging

from value_bot.analysis.market import rank_picks
from value_bot.analysis.pick import Pick
from value_bot.analysis.nba_model import analyze_nba_game as analyze_nba_market
from value_bot.config import MAX_PICKS_SPORT, MIN_CONFIDENCE, VALUE_THRESHOLD, nba_api_season
from value_bot.db import already_sent_bet
from value_bot.sports.nba import get_best_nba_stats, get_nba_games, nba_get_team_form
from value_bot.run_context import RunContext

log = logging.getLogger(__name__)

# Sin calibración por historial: la probabilidad ya está anclada al mercado y la cuota es real.


def analyze_nba_game(ctx: RunContext, game: dict) -> list[dict]:
    """Picks de un juego; sin líneas de mercado ni stats de ambos equipos no hay picks."""
    if not game.get("lines"):
        log.info("NBA %s vs %s: sin líneas de mercado (ESPN) → sin picks", game["home"], game["away"])
        return []
    h_stats, a_stats, splits = get_best_nba_stats(ctx, game)
    if not (h_stats and a_stats):
        log.info("NBA %s vs %s: sin stats de ambos equipos → sin picks", game["home"], game["away"])
        return []
    return analyze_nba_market(game, h_stats, a_stats, splits, nba_api_season(),
                              min_conf=MIN_CONFIDENCE, value_threshold=VALUE_THRESHOLD)


def get_nba_picks(ctx: RunContext) -> list[Pick]:
    games = get_nba_games(ctx)
    ctx.games_analyzed["nba"] = sum(1 for g in games if g.get("lines"))  # sin líneas no se analiza
    all_picks = []
    for game in games:
        for pick in analyze_nba_game(ctx, game):
            if already_sent_bet(game.get("event_id"), pick["market"], sport="nba"):
                log.info("NBA dedup BD: %s ya enviado para %s vs %s", pick["market"], game["home"], game["away"])
                continue
            all_picks.append({
                **game, **pick,
                "sport":  "nba",
                "flag":   "🏆" if game.get("is_finals") else "🏀",
                "league": "NBA Finals" if game.get("is_finals") else "NBA",
            })

    # Value bets primero, luego por confianza
    result = rank_picks(all_picks, VALUE_THRESHOLD)[:MAX_PICKS_SPORT]
    _prefetch_form(ctx, result)
    log.info("NBA picks: %d", len(result))
    return result


def _prefetch_form(ctx: RunContext, picks: list[dict]) -> None:
    """Forma reciente (ctx.nba.form) solo de los equipos que salen en el mensaje."""
    teams = dict.fromkeys(team for p in picks for team in (p["home"], p["away"]))
    for team in teams:
        nba_get_team_form(ctx, team)
