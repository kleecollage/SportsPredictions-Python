"""Picks de fútbol: junta fixtures + stats + cuotas 1X2 con el modelo y filtra contra la BD."""
import logging
from collections import defaultdict

from value_bot.analysis.football_model import analyze_football_match as analyze_football_market
from value_bot.analysis.football_model import apply_calibration
from value_bot.config import MAX_PICKS_SPORT, MIN_CONFIDENCE, MODEL_SINCE, VALUE_THRESHOLD
from value_bot.db import already_sent_for_game, get_calibration_factors
from value_bot.sports.football import build_football_stats, collect_football_fixtures, get_real_odds
from value_bot.store import store

log = logging.getLogger(__name__)

MAX_PICKS_LEAGUE_TYPE = 2   # mismo tipo de pick en la misma liga


def analyze_football_match(fix: dict, hs: dict | None, as_: dict | None) -> list[dict]:
    """Picks de un partido; sin stats de ambos equipos no hay picks (ni se piden cuotas)."""
    if not hs or not as_:
        # Sin stats de los dos equipos no hay proyección: no se inventan picks con promedios de liga.
        log.info("Fútbol %s vs %s: sin stats de ambos equipos → sin picks", fix["home"], fix["away"])
        return []
    real = get_real_odds(fix.get("odds_key", ""), fix["home"], fix["away"])
    return analyze_football_market(fix, hs, as_, real, min_conf=MIN_CONFIDENCE)


def _already_sent(pick: dict) -> bool:
    """Mismo pick para el mismo partido (fecha del juego) en cualquier corrida previa."""
    return already_sent_for_game("football", pick["home"], pick["away"], pick.get("date", ""), pick["type"])


def _cap_per_league_type(picks: list[dict]) -> list[dict]:
    """Máx. MAX_PICKS_LEAGUE_TYPE picks del mismo tipo por liga, en el orden recibido."""
    counts: dict = defaultdict(int)
    kept = []
    for p in picks:
        key = (p.get("league", ""), p.get("type", ""))
        if counts[key] < MAX_PICKS_LEAGUE_TYPE:
            kept.append(p)
            counts[key] += 1
    return kept


def get_football_picks() -> list[dict]:
    fixtures = collect_football_fixtures()
    store.games_analyzed["football"] = len(fixtures)
    stats = build_football_stats(fixtures)
    cal_factors = get_calibration_factors("football", MODEL_SINCE.get("football"))
    all_picks = []
    for fix in fixtures:
        for pick in analyze_football_match(fix, stats.get(fix["home"]), stats.get(fix["away"])):
            pick = apply_calibration(pick, cal_factors)
            if pick["confidence"] >= MIN_CONFIDENCE:
                all_picks.append({**fix, **pick})

    # Dedup contra BD antes del tope: un pick ya enviado no debe quitarle el lugar a otro
    fresh = [p for p in all_picks if not _already_sent(p)]
    if len(fresh) != len(all_picks):
        log.info("Football dedup BD: %d picks ya enviados para esos partidos", len(all_picks) - len(fresh))

    fresh.sort(key=lambda x: (1 if (x.get("value") and x["value"] >= VALUE_THRESHOLD) else 0,
                              x["confidence"]), reverse=True)
    picks = _cap_per_league_type(fresh)[:MAX_PICKS_SPORT]
    log.info("Football picks: %d (de %d fixtures, %d candidatos)", len(picks), len(fixtures), len(all_picks))
    return picks
