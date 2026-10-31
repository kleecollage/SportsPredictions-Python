"""Pick generation for NBA and Football."""
import math
import logging
from collections import defaultdict
from datetime import datetime

from value_bot.analysis.market import SIDE_MARKETS
from value_bot.analysis.nba_model import analyze_nba_game as analyze_nba_market
from value_bot.config import (
    MIN_CONFIDENCE, MAX_PICKS_SPORT, VALUE_THRESHOLD,
    LEAGUE_AVERAGES, LEAGUE_AVG_DEFAULT, est_odds, edge, nba_api_season,
)
from value_bot.sports.nba import get_nba_games, get_best_nba_stats
from value_bot.sports.football import collect_football_fixtures, build_football_stats, get_real_odds

log = logging.getLogger(__name__)

try:
    from value_bot.db import already_sent_today as _already_sent, get_calibration_factors as _get_cal
    from value_bot.db import already_sent_for_event as _already_sent_for_event
    _DB_DEDUP = True
except ImportError:
    _DB_DEDUP = False
    def _already_sent(*_): return False
    def _already_sent_for_event(*_, **__): return False
    def _get_cal(*_): return {}


def _apply_calibration(pick: dict, factors: dict) -> dict:
    """Ajusta confidence y our_prob según historial real del modelo."""
    if not factors:
        return pick
    bucket = (pick["confidence"] // 5) * 5
    factor = factors.get(bucket, 1.0)
    if factor == 1.0:
        return pick
    new_conf = min(95, max(55, int(pick["confidence"] * factor)))
    new_prob = round(min(0.95, (pick.get("our_prob") or pick["confidence"] / 100) * factor), 3)
    pick = {**pick, "confidence": new_conf, "our_prob": new_prob}
    return pick


def _form_emoji(form):
    return "".join("🟢" if c == "W" else "🔴" for c in form)


# ── Poisson helpers ───────────────────────────────────────────────

def _form_txt(stats: dict) -> str:
    """'forma WWDLW (10 pts)' o 'sin forma reciente' si la fuente no la trae."""
    form = stats.get("form") or ""
    if not form:
        return "sin forma reciente"
    return f"forma {form} ({stats.get('form_pts', 0)} pts)"


def _poisson_p(lam: float, k: int) -> float:
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _poisson_over(lam: float, threshold: int) -> float:
    p_under_eq = sum(_poisson_p(lam, k) for k in range(threshold + 1))
    return round(max(0.0, min(1.0, 1.0 - p_under_eq)), 4)


def _poisson_btts(exp_h: float, exp_a: float) -> float:
    p_home_scores = 1.0 - math.exp(-max(exp_h, 0.05))
    p_away_scores = 1.0 - math.exp(-max(exp_a, 0.05))
    return round(p_home_scores * p_away_scores, 4)


# ── NBA picks ─────────────────────────────────────────────────────
# Solo contra líneas reales de ESPN (nba_model). Sin calibración por historial:
# la probabilidad ya está anclada al mercado y la cuota es real.

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


def _nba_already_sent(event_id: str | None, pick: dict) -> bool:
    """Un pick por partido y mercado entre corridas; una sola apuesta de lado por partido."""
    if not (_DB_DEDUP and event_id):
        return False
    markets = SIDE_MARKETS if pick["market"] in SIDE_MARKETS else (pick["market"],)
    return any(_already_sent_for_event(event_id, market, sport="nba") for market in markets)


def get_nba_picks() -> list[dict]:
    all_picks = []
    for game in get_nba_games():
        for pick in analyze_nba_game(game):
            if _nba_already_sent(game.get("event_id"), pick):
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


# ── Football picks ────────────────────────────────────────────────

def analyze_football_match(fix, hs, as_):
    """v4.6 football pick analysis with real Poisson probabilities."""
    league  = fix.get("league", "")
    la      = LEAGUE_AVERAGES.get(league, LEAGUE_AVG_DEFAULT)
    style   = la.get("style", "balanced")
    lg_name = league or "Liga"

    if not hs or not as_:
        o25  = la["over25_pct"]
        u25  = la["under25_pct"]
        btts = la["btts_pct"]
        draw = la["draw_pct"]
        lam  = la["avg_goals"]
        league_picks = []

        # Sin stats individuales: usar tasa histórica con descuento del 10%
        # (confianza conservadora — coherente con data_quality "medium", cap 68%)
        def _hist_conf(pct):
            return min(68, int(pct * 0.90))

        if style == "attacking":
            conf = _hist_conf(o25)
            if conf >= MIN_CONFIDENCE:
                league_picks.append({
                    "type": "Over 2.5 Goles",
                    "confidence": conf,
                    "our_prob": round(o25 / 100, 3),
                    "est_odds": est_odds(o25 / 100),
                    "value": None,
                    "reasoning": (f"{fix['home']} vs {fix['away']} | "
                                  f"{lg_name}: {o25}% Over 2.5 históricamente "
                                  f"(media {lam:.2f} goles/j). Liga ofensiva, sin stats individuales."),
                    "data_quality": "medium",
                })
            conf_b = _hist_conf(btts)
            if conf_b >= MIN_CONFIDENCE:
                league_picks.append({
                    "type": "Ambos Marcan (BTTS)",
                    "confidence": conf_b,
                    "our_prob": round(btts / 100, 3),
                    "est_odds": est_odds(btts / 100),
                    "value": None,
                    "reasoning": (f"{fix['home']} vs {fix['away']} | "
                                  f"{lg_name}: {btts}% BTTS hist. "
                                  f"Liga ofensiva — ambos equipos tienen vocación goleadora."),
                    "data_quality": "medium",
                })

        elif style == "defensive":
            conf = _hist_conf(u25)
            if conf >= MIN_CONFIDENCE:
                league_picks.append({
                    "type": "Under 2.5 Goles",
                    "confidence": conf,
                    "our_prob": round(u25 / 100, 3),
                    "est_odds": est_odds(u25 / 100),
                    "value": None,
                    "reasoning": (f"{fix['home']} vs {fix['away']} | "
                                  f"{lg_name}: {u25}% Under 2.5 hist. "
                                  f"(media {lam:.2f} goles/j). Partido táctico esperado."),
                    "data_quality": "medium",
                })
            conf_x = _hist_conf(draw)
            if conf_x >= MIN_CONFIDENCE:
                league_picks.append({
                    "type": "Empate (X)",
                    "confidence": conf_x,
                    "our_prob": round(draw / 100, 3),
                    "est_odds": est_odds(draw / 100),
                    "value": None,
                    "reasoning": (f"{fix['home']} vs {fix['away']} | "
                                  f"{lg_name}: {draw}% empates hist. "
                                  f"Equipos de nivel similar en liga táctica."),
                    "data_quality": "medium",
                })

        elif style == "technical":
            conf = _hist_conf(o25)
            if conf >= MIN_CONFIDENCE:
                league_picks.append({
                    "type": "Over 2.5 Goles",
                    "confidence": conf,
                    "our_prob": round(o25 / 100, 3),
                    "est_odds": est_odds(o25 / 100),
                    "value": None,
                    "reasoning": (f"{lg_name}: {o25}% histórico Over 2.5, media {lam:.2f} goles/j. "
                                  f"Liga técnica — partidos abiertos son comunes."),
                    "data_quality": "medium",
                })
            hw = la["home_win_pct"]
            dc_prob = round(min(0.68, (hw + draw) / 100), 3)
            conf_dc = _hist_conf(hw + draw)
            if conf_dc >= MIN_CONFIDENCE:
                league_picks.append({
                    "type": "Doble Chance Local (1X)",
                    "confidence": conf_dc,
                    "our_prob": dc_prob,
                    "est_odds": est_odds(dc_prob * 0.72),
                    "value": None,
                    "reasoning": (f"{lg_name}: {hw}% victorias local + {draw}% empates histórico. "
                                  f"1X cubre ambos resultados favorables al local."),
                    "data_quality": "medium",
                })

        else:  # balanced
            conf = _hist_conf(o25)
            if conf >= MIN_CONFIDENCE:
                league_picks.append({
                    "type": "Over 2.5 Goles",
                    "confidence": conf,
                    "our_prob": round(o25 / 100, 3),
                    "est_odds": est_odds(o25 / 100),
                    "value": None,
                    "reasoning": (f"{lg_name}: {o25}% histórico Over 2.5 "
                                  f"(media {lam:.2f} goles/j). Liga equilibrada."),
                    "data_quality": "medium",
                })
            conf_b = _hist_conf(btts)
            if conf_b >= MIN_CONFIDENCE:
                league_picks.append({
                    "type": "Ambos Marcan (BTTS)",
                    "confidence": conf_b,
                    "our_prob": round(btts / 100, 3),
                    "est_odds": est_odds(btts / 100),
                    "value": None,
                    "reasoning": (f"{lg_name}: {btts}% de partidos con BTTS históricamente. "
                                  f"Liga equilibrada con presión ofensiva de ambos lados."),
                    "data_quality": "medium",
                })

        league_picks = [p for p in league_picks if p["confidence"] >= MIN_CONFIDENCE]
        if not league_picks:
            league_picks.append({
                "type": "Over 1.5 Goles",
                "confidence": 63,
                "our_prob": 0.68,
                "est_odds": est_odds(0.68),
                "value": None,
                "reasoning": (f"{lg_name}: {lam:.2f} goles/j promedio — pick conservador "
                              f"sin stats individuales disponibles."),
                "data_quality": "low",
            })
        return league_picks[:3]

    # With individual stats: real Poisson
    gf_h = hs.get("goals_for_pg", 0)
    ga_h = hs.get("goals_against_pg", 0)
    gf_a = as_.get("goals_for_pg", 0)
    ga_a = as_.get("goals_against_pg", 0)

    exp_h   = (gf_h + ga_a) / 2.0
    exp_a   = (gf_a + ga_h) / 2.0
    lam_tot = exp_h + exp_a

    over25_prob  = _poisson_over(lam_tot, 2)
    under25_prob = 1.0 - over25_prob
    btts_prob    = _poisson_btts(exp_h, exp_a)

    hs_str = hs.get("win_rate", 33) + hs.get("form_pts", 7) * 2.5 + 10
    as_str = as_.get("win_rate", 33) + as_.get("form_pts", 7) * 2.5
    diff   = hs_str - as_str

    real  = get_real_odds(fix.get("odds_key", ""), fix["home"], fix["away"])
    picks = []

    if style in ("attacking", "balanced", "technical"):
        if over25_prob >= 0.50:
            conf = min(80, int(over25_prob * 100 * 0.97))
            picks.append({
                "type": "Over 2.5 Goles",
                "confidence": conf,
                "our_prob": round(over25_prob, 3),
                "est_odds": est_odds(over25_prob),
                "value": None,
                "reasoning": (f"XG local {exp_h:.2f} + XG visitante {exp_a:.2f} = {lam_tot:.2f} esperados. "
                              f"Poisson P(≥3 goles)={over25_prob*100:.0f}%. "
                              f"{fix['home']} anota {gf_h:.1f}/j y concede {ga_h:.1f}/j."),
                "data_quality": "high",
            })
    else:  # defensive
        if under25_prob >= 0.52:
            conf = min(78, int(under25_prob * 100 * 0.97))
            picks.append({
                "type": "Under 2.5 Goles",
                "confidence": conf,
                "our_prob": round(under25_prob, 3),
                "est_odds": est_odds(under25_prob),
                "value": None,
                "reasoning": (f"XG local {exp_h:.2f} + XG visitante {exp_a:.2f} = {lam_tot:.2f} esperados. "
                              f"Poisson P(≤2 goles)={under25_prob*100:.0f}%. "
                              f"Liga defensiva: {fix['home']} concede {ga_h:.1f}/j, "
                              f"{fix['away']} {ga_a:.1f}/j."),
                "data_quality": "high",
            })
        if over25_prob >= 0.52 and len(picks) == 0:
            conf = min(75, int(over25_prob * 100 * 0.95))
            picks.append({
                "type": "Over 2.5 Goles",
                "confidence": conf,
                "our_prob": round(over25_prob, 3),
                "est_odds": est_odds(over25_prob),
                "value": None,
                "reasoning": (f"XG={lam_tot:.2f}. Pese al estilo defensivo de {lg_name}, "
                              f"Poisson indica {over25_prob*100:.0f}% de probabilidad de Over 2.5."),
                "data_quality": "high",
            })

    if btts_prob >= 0.56:
        has_under = any(p["type"] == "Under 2.5 Goles" for p in picks)
        if not has_under:
            conf = min(78, int(btts_prob * 100 * 0.96))
            picks.append({
                "type": "Ambos Marcan (BTTS)",
                "confidence": conf,
                "our_prob": round(btts_prob, 3),
                "est_odds": est_odds(btts_prob),
                "value": None,
                "reasoning": (f"P(local marca)={(1-math.exp(-exp_h))*100:.0f}%, "
                              f"P(visitante marca)={(1-math.exp(-exp_a))*100:.0f}%. "
                              f"{fix['home']} anota {gf_h:.1f}/j; {fix['away']} {gf_a:.1f}/j."),
                "data_quality": "high",
            })

    if diff >= 24:
        raw_p = 1.0 / (1.0 + math.exp(-diff / 28.0))
        prob  = round(0.50 + (raw_p - 0.50) * 0.72, 3)
        prob  = min(0.76, max(0.55, prob))
        p = {
            "type": f"Victoria {fix['home']}",
            "confidence": int(prob * 100),
            "our_prob": prob,
            "est_odds": est_odds(prob),
            "value": None,
            "reasoning": (f"{fix['home']}: {hs.get('win_rate', 0):.0f}% victorias, "
                          f"{_form_txt(hs)}. "
                          f"Ventaja sobre {fix['away']}: {diff:.0f} pts fuerza."),
            "data_quality": "high",
        }
        if real and real.get("home_odds"):
            p["est_odds"] = real["home_odds"]
            p["value"]    = edge(prob, real["home_odds"])
            p["real_odds_source"] = True
        picks.append(p)
    elif diff <= -24:
        raw_p = 1.0 / (1.0 + math.exp(-abs(diff) / 28.0))
        prob  = round(0.50 + (raw_p - 0.50) * 0.68, 3)
        prob  = min(0.74, max(0.54, prob))
        p = {
            "type": f"Victoria {fix['away']}",
            "confidence": int(prob * 100),
            "our_prob": prob,
            "est_odds": est_odds(prob),
            "value": None,
            "reasoning": (f"{fix['away']}: {as_.get('win_rate', 0):.0f}% victorias, "
                          f"{_form_txt(as_)}. "
                          f"Ventaja visitante: {abs(diff):.0f} pts — inusual pero estadísticamente clara."),
            "data_quality": "high",
        }
        if real and real.get("away_odds"):
            p["est_odds"] = real["away_odds"]
            p["value"]    = edge(prob, real["away_odds"])
            p["real_odds_source"] = True
        picks.append(p)

    if abs(diff) <= 10 and style in ("defensive", "technical", "balanced"):
        da = (hs.get("draw_rate", la.get("draw_pct", 25))
              + as_.get("draw_rate", la.get("draw_pct", 25))) / 2.0
        league_draw = la.get("draw_pct", 25)
        if da >= 23 or league_draw >= 26:
            base_draw = max(da, league_draw) / 100.0
            prob = round(min(0.62, base_draw + 0.06), 3)
            if prob >= 0.62:
                p = {
                    "type": "Empate (X)",
                    "confidence": int(prob * 100),
                    "our_prob": prob,
                    "est_odds": est_odds(prob),
                    "value": None,
                    "reasoning": (f"Equipos muy igualados (diff fuerza {abs(diff):.0f}). "
                                  f"Tasa empate: {fix['home']} {hs.get('draw_rate', 25):.0f}%, "
                                  f"{fix['away']} {as_.get('draw_rate', 25):.0f}%. "
                                  f"{lg_name} promedio {league_draw}% empates."),
                    "data_quality": "medium",
                }
                if real and real.get("draw_odds"):
                    p["est_odds"] = real["draw_odds"]
                    p["value"]    = edge(prob, real["draw_odds"])
                    p["real_odds_source"] = True
                picks.append(p)

    if 12 <= abs(diff) < 24:
        if diff > 0:
            dc_type = f"Doble Chance {fix['home']} (1X)"
            raw_p   = 1.0 / (1.0 + math.exp(-diff / 22.0))
            dc_prob = round(min(0.78, 0.62 + (raw_p - 0.5) * 0.55), 3)
            dc_ctx  = (f"{fix['home']}: {hs.get('win_rate', 0):.0f}% victorias, "
                       f"{_form_txt(hs)}. "
                       f"Ventaja local de {diff:.0f} pts → 1X más seguro que 1 directo.")
        else:
            dc_type = f"Doble Chance {fix['away']} (X2)"
            raw_p   = 1.0 / (1.0 + math.exp(-abs(diff) / 22.0))
            dc_prob = round(min(0.76, 0.60 + (raw_p - 0.5) * 0.52), 3)
            dc_ctx  = (f"{fix['away']}: {as_.get('win_rate', 0):.0f}% victorias, "
                       f"{_form_txt(as_)}. "
                       f"Ventaja visitante {abs(diff):.0f} pts — X2 cubre victoria o empate.")
        picks.append({
            "type": dc_type,
            "confidence": int(dc_prob * 100),
            "our_prob": dc_prob,
            "est_odds": round(est_odds(dc_prob) * 0.72, 2),  # descuento sobre cuota, no sobre prob
            "value": None,
            "reasoning": dc_ctx,
            "data_quality": "medium",
        })

    if not picks and lam_tot >= 2.0:
        prob = round(min(0.75, 0.54 + (lam_tot - 2.0) * 0.08), 3)
        picks.append({
            "type": "Over 1.5 Goles",
            "confidence": int(prob * 100),
            "our_prob": prob,
            "est_odds": est_odds(prob),
            "value": None,
            "reasoning": (f"XG total esperado: {lam_tot:.2f} goles "
                          f"({fix['home']} {exp_h:.2f} + {fix['away']} {exp_a:.2f}). "
                          f"Pick conservador — {over25_prob*100:.0f}% no supera umbral Over 2.5."),
            "data_quality": "medium",
        })

    MIN_ODDS = 1.30
    picks = [
        p for p in picks
        if p["confidence"] >= MIN_CONFIDENCE and p.get("est_odds", 0) > MIN_ODDS
    ]
    picks.sort(key=lambda x: (x["confidence"], x.get("value") or -99), reverse=True)
    return picks[:3]


def get_football_picks():
    """v4.6: football picks with intelligent deduplication."""
    fixtures    = collect_football_fixtures()
    stats       = build_football_stats(fixtures)
    all_picks   = []
    cal_factors = _get_cal() if _DB_DEDUP else {}
    for fix in fixtures:
        analysis = analyze_football_match(fix, stats.get(fix["home"]), stats.get(fix["away"]))
        for pick in analysis:
            pick = _apply_calibration(pick, cal_factors)
            if pick["confidence"] >= MIN_CONFIDENCE:
                all_picks.append({**fix, **pick})

    all_picks.sort(
        key=lambda x: (
            1 if (x.get("value") and x["value"] >= VALUE_THRESHOLD) else 0,
            x["confidence"],
        ),
        reverse=True,
    )

    lg_type_count: dict = defaultdict(int)
    deduped = []
    for p in all_picks:
        key = (p.get("league", ""), p.get("type", ""))
        if lg_type_count[key] < 2:
            deduped.append(p)
            lg_type_count[key] += 1

    deduped = deduped[:MAX_PICKS_SPORT]

    # Dedup contra BD: filtrar picks ya enviados hoy
    if _DB_DEDUP:
        today = datetime.now().strftime("%Y-%m-%d")
        before = len(deduped)
        deduped = [
            p for p in deduped
            if not _already_sent(p["home"], p["away"], p["type"], today)
        ]
        if before != len(deduped):
            log.info("Football dedup BD: %d picks ya enviados hoy filtrados", before - len(deduped))

    log.info("Football picks: %d (de %d fixtures, %d candidatos)", len(deduped), len(fixtures), len(all_picks))
    return deduped
