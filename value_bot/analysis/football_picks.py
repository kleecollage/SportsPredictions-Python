"""
Picks de fútbol: junta fixtures + stats + cuotas reales (1X2 y totales) con el modelo y filtra
contra la BD. Sin calibración por historial: los picks ya están anclados al mercado (como NBA/NFL).
"""
import logging
from collections import defaultdict
from collections.abc import Callable

from value_bot.analysis.football_btts import Decision, btts_evaluate, btts_gate
from value_bot.analysis.football_model import analyze_football_match as analyze_football_market
from value_bot.analysis.football_model import is_pickable
from value_bot.analysis.market import rank_picks
from value_bot.analysis.pick import Pick
from value_bot.config import MAX_PICKS_SPORT, MIN_CONFIDENCE, VALUE_THRESHOLD
from value_bot.db import already_sent_for_game
from value_bot.run_context import RunContext
from value_bot.sports.football import build_football_stats, collect_football_fixtures, side_stats
from value_bot.sports.football_odds import ODDS_CREDITS_RESERVE, get_btts_odds, get_real_odds

log = logging.getLogger(__name__)

MAX_PICKS_LEAGUE_TYPE = 2   # mismo tipo de pick en la misma liga


def analyze_football_match(ctx: RunContext, fix: dict, hs: dict | None, as_: dict | None) -> list[dict]:
    """Picks de un partido; sin stats de ambos equipos no hay picks (ni se piden cuotas)."""
    if not hs or not as_:
        # Sin stats de los dos equipos no hay proyección: no se inventan picks con promedios de liga.
        log.info("Fútbol %s vs %s: sin stats de ambos equipos → sin picks", fix["home"], fix["away"])
        return []
    real = get_real_odds(ctx, fix.get("odds_key", ""), fix["home"], fix["away"], date=fix.get("date") or None)
    return analyze_football_market(fix, hs, as_, real, min_conf=MIN_CONFIDENCE, value_threshold=VALUE_THRESHOLD)


def _already_sent(pick: dict) -> bool:
    """
    El partido (fecha del juego) ya recibió algún pick en una corrida previa: si los números
    cambian de un día a otro no se le suma otro mercado al mismo partido.
    """
    return already_sent_for_game("football", pick["home"], pick["away"], pick.get("date", ""))


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


def get_football_picks(ctx: RunContext) -> list[Pick]:
    fixtures = collect_football_fixtures(ctx)
    ctx.games_analyzed["football"] = len(fixtures)
    stats = build_football_stats(ctx, fixtures)
    ctx.football.fixtures[:] = fixtures   # reemplaza: BTTS ve solo los de esta llamada
    ctx.football.stats.clear()
    ctx.football.stats.update(stats)
    all_picks = []
    for fix in fixtures:
        for pick in analyze_football_match(ctx, fix, side_stats(stats, fix, "home"), side_stats(stats, fix, "away")):
            if is_pickable(pick, MIN_CONFIDENCE, VALUE_THRESHOLD):
                all_picks.append({**fix, **pick})

    # Dedup contra BD antes del tope: un pick ya enviado no debe quitarle el lugar a otro
    fresh = [p for p in all_picks if not _already_sent(p)]
    if len(fresh) != len(all_picks):
        log.info("Football dedup BD: %d picks ya enviados para esos partidos", len(all_picks) - len(fresh))

    picks = _cap_per_league_type(rank_picks(fresh, VALUE_THRESHOLD))[:MAX_PICKS_SPORT]
    log.info("Football picks: %d (de %d fixtures, %d candidatos)", len(picks), len(fixtures), len(all_picks))
    return picks


# ── BTTS bajo demanda (main --btts) ───────────────────────────────

LeagueChooser = Callable[[dict[str, int], int | None], set[str] | frozenset[str]]


def _btts_gate(ctx: RunContext, fix: dict, selected: set[str] | frozenset[str], taken: set[tuple[str, str]],
               credits_left: int | None) -> tuple[Decision, str | None]:
    """Árbol antes del gasto para un fixture de la corrida (las cuotas 1X2 ya están en caché)."""
    stats = ctx.football.stats
    hs, as_ = side_stats(stats, fix, "home"), side_stats(stats, fix, "away")
    found: list[str | None] = []

    def find_event() -> str | None:
        real = get_real_odds(ctx, fix.get("odds_key", ""), fix["home"], fix["away"], date=fix.get("date") or None) or {}
        found.append(real.get("odds_event_id"))
        return found[-1]

    sent = (fix["home"], fix["away"]) in taken or _already_sent(fix)
    gate = btts_gate(fix, hs, as_, selected=selected, sent_before=sent, find_event=find_event,
                     credits_left=credits_left, reserve=ODDS_CREDITS_RESERVE)
    return gate, (found[-1] if found else None)


def _btts_cap_reached(fix: dict, n_current: int, picks: list[dict]) -> str | None:
    """Motivo si otro BTTS superaría MAX_PICKS_SPORT o MAX_PICKS_LEAGUE_TYPE (antes de gastar)."""
    if n_current + len(picks) >= MAX_PICKS_SPORT:
        return f"tope de {MAX_PICKS_SPORT} picks de fútbol → skip"
    if sum(p.get("league") == fix.get("league") for p in picks) >= MAX_PICKS_LEAGUE_TYPE:
        return f"tope de {MAX_PICKS_LEAGUE_TYPE} BTTS en la liga → skip"
    return None


def get_btts_picks(ctx: RunContext, current: list[dict],
                   choose_leagues: LeagueChooser) -> tuple[list[dict], list[Decision]]:
    """
    BTTS de las ligas que elija `choose_leagues({liga: partidos que pedirían cuota}, créditos)`,
    con los fixtures y stats que get_football_picks dejó en ctx.football. Los partidos de
    `current` (picks de esta corrida) no se repiten y los topes (MAX_PICKS_SPORT con `current`,
    MAX_PICKS_LEAGUE_TYPE BTTS por liga) se revisan antes de gastar. Devuelve (picks, árbol de cada partido elegido).
    """
    fixtures = ctx.football.fixtures
    taken = {(p["home"], p["away"]) for p in current}
    leagues = {f.get("league", "") for f in fixtures}
    counts = {lg: sum(_btts_gate(ctx, f, leagues, taken, None)[0].spend for f in fixtures if f.get("league", "") == lg)
              for lg in dict.fromkeys(f.get("league", "") for f in fixtures)}
    selected = choose_leagues(counts, ctx.football.credits.left)
    picks, decisions = [], []
    for fix in (f for f in fixtures if f.get("league", "") in selected):
        if cap := _btts_cap_reached(fix, len(current), picks):
            decisions.append(Decision(f"{fix['home']} vs {fix['away']}", fix.get("league", ""), (cap,)))
            continue
        decision, event_id = _btts_gate(ctx, fix, selected, taken, ctx.football.credits.left)
        if decision.spend:
            odds = get_btts_odds(ctx, fix.get("odds_key", ""), event_id)
            decision = btts_evaluate(decision, fix, odds, MIN_CONFIDENCE, VALUE_THRESHOLD)
        decisions.append(decision)
        if decision.pick:
            picks.append({**fix, **decision.pick})
        if decision.stop:
            break
    log.info("BTTS bajo demanda: %d picks de %d partidos evaluados", len(picks), len(decisions))
    return picks, decisions
