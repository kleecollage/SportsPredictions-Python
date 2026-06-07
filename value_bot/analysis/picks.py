"""Pick generation for NBA and Football."""
import math
import logging
from collections import defaultdict

from value_bot.config import (
    MIN_CONFIDENCE, MAX_PICKS_SPORT, VALUE_THRESHOLD,
    LEAGUE_AVERAGES, LEAGUE_AVG_DEFAULT, est_odds, edge,
)
from value_bot.sports.nba import get_nba_games, get_best_nba_stats
from value_bot.sports.football import collect_football_fixtures, build_football_stats, get_real_odds

log = logging.getLogger(__name__)


def _form_emoji(form):
    return "".join("🟢" if c == "W" else "🔴" for c in form)


# ── Poisson helpers ───────────────────────────────────────────────

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

def analyze_nba_game(game):
    """Generates 3-5 picks per game."""
    h_stats, a_stats = get_best_nba_stats(game)
    picks = []

    is_finals  = game.get("is_finals", False)
    series_txt = game.get("series_summary", "")
    h_rec      = game.get("home_record", "?")
    a_rec      = game.get("away_record", "?")
    ctx        = (f" [{series_txt}]" if series_txt
                  else (" [NBA Finals]" if is_finals else ""))

    if h_stats and a_stats:
        h_ppg = h_stats["ppg"];     a_ppg = a_stats["ppg"]
        h_opp = h_stats["opp_ppg"]; a_opp = a_stats["opp_ppg"]
        h_form = h_stats.get("form", ""); a_form = a_stats.get("form", "")
        h_fe   = _form_emoji(h_form) if h_form else "—"
        a_fe   = _form_emoji(a_form) if a_form else "—"

        projected = (h_ppg + a_opp + a_ppg + h_opp) / 2
        if not (180.0 <= projected <= 275.0):
            log.warning("NBA línea fuera de rango: %.1f pts → usando default", projected)
            projected = 218.5 if is_finals else 221.0
        game_line = round(projected, 1)

        # Pick 1: Game total
        if projected >= 218:
            prob = min(0.80, 0.54 + (projected - 210) * 0.008)
            picks.append({
                "type": f"Over {game_line} Puntos",
                "confidence": int(prob * 100),
                "our_prob": round(prob, 3),
                "est_odds": est_odds(prob),
                "value": None,
                "reasoning": (f"{game['home']}: {h_ppg:.0f}pts {h_fe} | "
                              f"{game['away']}: {a_ppg:.0f}pts {a_fe} | "
                              f"Proyección ~{projected:.0f}pts{ctx}"),
                "data_quality": "high",
            })
        elif projected <= 210:
            prob = min(0.76, 0.54 + (210 - projected) * 0.007)
            picks.append({
                "type": f"Under {game_line} Puntos",
                "confidence": int(prob * 100),
                "our_prob": round(prob, 3),
                "est_odds": est_odds(prob),
                "value": None,
                "reasoning": (f"Defensas dominantes. {game['home']} concede {h_opp:.0f}/j, "
                              f"{game['away']} concede {a_opp:.0f}/j → ~{projected:.0f}pts{ctx}"),
                "data_quality": "high",
            })
        else:
            direction = "Over" if projected >= 214 else "Under"
            picks.append({
                "type": f"{direction} {game_line} Puntos",
                "confidence": 63, "our_prob": 0.63,
                "est_odds": est_odds(0.63), "value": None,
                "reasoning": (f"Proyección equilibrada ~{projected:.0f}pts. "
                              f"{game['home']} {h_ppg:.0f}/j vs {game['away']} {a_ppg:.0f}/j{ctx}"),
                "data_quality": "medium",
            })

        # Pick 2: Moneyline
        h_str = h_stats["win_rate"] + h_stats.get("form_pts", 0) * 2 + 8
        a_str = a_stats["win_rate"] + a_stats.get("form_pts", 0) * 2
        diff  = h_str - a_str

        if abs(diff) >= 15:
            if diff > 0:
                fav, fav_wr, fav_pm, fav_fe_s = game["home"], h_stats["win_rate"], h_stats.get("pm", 0), h_fe
            else:
                fav, fav_wr, fav_pm, fav_fe_s = game["away"], a_stats["win_rate"], a_stats.get("pm", 0), a_fe
            prob = min(0.78, 0.52 + abs(diff) * 0.007)
            picks.append({
                "type": f"Victoria {fav}",
                "confidence": int(prob * 100),
                "our_prob": round(prob, 3),
                "est_odds": est_odds(prob),
                "value": None,
                "reasoning": (f"{fav}: {fav_wr:.0f}% victorias {fav_fe_s}, "
                              f"margin {fav_pm:+.1f}pts/j{ctx}"),
                "data_quality": "high",
            })
        elif is_finals:
            picks.append({
                "type": f"Victoria {game['home']} (Local)",
                "confidence": 64, "our_prob": 0.64,
                "est_odds": est_odds(0.64), "value": None,
                "reasoning": (f"Partido parejo — ventaja histórica de local en Finals. "
                              f"{game['home']} {h_fe}{ctx}"),
                "data_quality": "medium",
            })

        # Pick 3: Spread
        if abs(diff) >= 18:
            fav_s  = game["home"] if diff > 0 else game["away"]
            spread = 6.5 if abs(diff) >= 28 else (5.0 if abs(diff) >= 22 else 3.5)
            sp_prob = min(0.70, 0.54 + abs(diff) * 0.006)
            picks.append({
                "type": f"Hándicap {fav_s} -{spread:.1f}",
                "confidence": int(sp_prob * 100),
                "our_prob": round(sp_prob, 3),
                "est_odds": 1.91,
                "value": None,
                "reasoning": (f"{fav_s} superior en {abs(diff):.0f}pts de fuerza estimada. "
                              f"Spread proyectado -{spread:.1f}pts{ctx}"),
                "data_quality": "medium",
            })

        # Pick 4: Team Total
        if h_ppg >= 112:
            tt_line = round(h_ppg * 0.95, 1)
            tt_prob = min(0.68, 0.54 + (h_ppg - 110) * 0.007)
            picks.append({
                "type": f"Total {game['home'].split()[-1]} Over {tt_line}",
                "confidence": int(tt_prob * 100),
                "our_prob": round(tt_prob, 3),
                "est_odds": est_odds(tt_prob),
                "value": None,
                "reasoning": (f"{game['home']} promedia {h_ppg:.0f}pts/j "
                              f"(forma {h_fe}). Team total estimado >{tt_line}{ctx}"),
                "data_quality": "medium",
            })

        # Pick 5: 1st Half Total
        half_line = round(game_line * 0.49, 1)
        half_prob = 0.65 if projected >= 218 else 0.63
        picks.append({
            "type": f"1ª Mitad Over {half_line}",
            "confidence": int(half_prob * 100),
            "our_prob": half_prob,
            "est_odds": est_odds(half_prob),
            "value": None,
            "reasoning": (f"Ritmo alto de salida esperado en Playoffs. "
                          f"~{half_line:.0f}pts proyectados en 1ª mitad{ctx}"),
            "data_quality": "medium",
        })

        # Bonus Finals: Series winner
        if is_finals and series_txt:
            h_last = game["home"].split()[-1].lower()
            a_last = game["away"].split()[-1].lower()
            h_leads = h_last + " lead" in series_txt.lower()
            a_leads = a_last + " lead" in series_txt.lower()
            if h_leads or a_leads:
                leader       = game["home"] if h_leads else game["away"]
                leader_stats = h_stats if h_leads else a_stats
                picks.append({
                    "type": f"Ganador Serie: {leader}",
                    "confidence": 67, "our_prob": 0.67,
                    "est_odds": est_odds(0.67), "value": None,
                    "reasoning": (f"{leader} lidera la serie. "
                                  f"{leader_stats['win_rate']:.0f}% victorias en Playoffs{ctx}"),
                    "data_quality": "medium",
                })

    else:
        base_line = 215.5
        picks.append({
            "type": f"Over {base_line} Puntos",
            "confidence": 63, "our_prob": 0.63,
            "est_odds": 1.87, "value": None,
            "reasoning": (f"{game['home']} ({h_rec}) vs {game['away']} ({a_rec}). "
                          f"Promedio NBA Playoffs ≥ 215pts{ctx}"),
            "data_quality": "low",
        })
        picks.append({
            "type": f"1ª Mitad Over {round(base_line * 0.49, 1)}",
            "confidence": 62, "our_prob": 0.62,
            "est_odds": 1.85, "value": None,
            "reasoning": (f"Ritmo elevado esperado en Playoffs. "
                          f"~{base_line * 0.49:.0f}pts en 1ª mitad{ctx}"),
            "data_quality": "low",
        })
        if is_finals:
            picks.append({
                "type": f"Victoria {game['home']}",
                "confidence": 63, "our_prob": 0.63,
                "est_odds": est_odds(0.63), "value": None,
                "reasoning": (f"Ventaja de local en NBA Finals histórica (~58%). "
                              f"{game['home']} ({h_rec}){ctx}"),
                "data_quality": "low",
            })

    picks = [p for p in picks if p["confidence"] >= MIN_CONFIDENCE]
    picks.sort(key=lambda x: x["confidence"], reverse=True)
    return picks


def get_nba_picks():
    games = get_nba_games()
    all_picks = []

    for game in games:
        game_picks = analyze_nba_game(game)
        for pd in game_picks:
            if pd["confidence"] >= MIN_CONFIDENCE:
                all_picks.append({
                    **game, **pd,
                    "sport":  "nba",
                    "flag":   "🏆" if game.get("is_finals") else "🏀",
                    "league": "NBA Finals" if game.get("is_finals") else "NBA",
                })

    all_picks.sort(
        key=lambda x: (1 if x.get("is_finals") else 0, x["confidence"]),
        reverse=True,
    )
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

        if style == "attacking":
            if o25 >= 52:
                conf = min(77, o25 + 10)
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
            if btts >= 55:
                conf_b = min(74, btts + 12)
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
            if u25 >= 52:
                conf = min(74, u25 + 10)
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
            if draw >= 26:
                conf_x = min(66, draw + 37)
                league_picks.append({
                    "type": "Empate (X)",
                    "confidence": conf_x,
                    "our_prob": round(draw / 100 + 0.08, 3),
                    "est_odds": est_odds(draw / 100 + 0.06),
                    "value": None,
                    "reasoning": (f"{fix['home']} vs {fix['away']} | "
                                  f"{lg_name}: {draw}% empates hist. "
                                  f"Equipos de nivel similar en liga táctica."),
                    "data_quality": "medium",
                })

        elif style == "technical":
            if o25 >= 50:
                conf = min(73, o25 + 12)
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
            if hw >= 44:
                dc_prob = round(min(0.68, (hw + draw) / 100), 3)
                conf_dc = min(68, hw + 22)
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
            if o25 >= 50:
                conf = min(75, o25 + 10)
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
            if btts >= 52:
                conf_b = min(72, btts + 12)
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
                "reasoning": (f"P(local marca)={1-math.exp(-exp_h)*100:.0f}%, "
                              f"P(visitante marca)={1-math.exp(-exp_a)*100:.0f}%. "
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
                          f"forma {hs.get('form', '?')} ({hs.get('form_pts', 0)} pts últimos 5). "
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
                          f"forma {as_.get('form', '?')} ({as_.get('form_pts', 0)} pts). "
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
                       f"forma {hs.get('form', '?')} ({hs.get('form_pts', 0)} pts). "
                       f"Ventaja local de {diff:.0f} pts → 1X más seguro que 1 directo.")
        else:
            dc_type = f"Doble Chance {fix['away']} (X2)"
            raw_p   = 1.0 / (1.0 + math.exp(-abs(diff) / 22.0))
            dc_prob = round(min(0.76, 0.60 + (raw_p - 0.5) * 0.52), 3)
            dc_ctx  = (f"{fix['away']}: {as_.get('win_rate', 0):.0f}% victorias, "
                       f"forma {as_.get('form', '?')} ({as_.get('form_pts', 0)} pts). "
                       f"Ventaja visitante {abs(diff):.0f} pts — X2 cubre victoria o empate.")
        picks.append({
            "type": dc_type,
            "confidence": int(dc_prob * 100),
            "our_prob": dc_prob,
            "est_odds": round(est_odds(dc_prob * 0.72), 2),
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

    picks = [p for p in picks if p["confidence"] >= MIN_CONFIDENCE]
    picks.sort(key=lambda x: (x["confidence"], x.get("value") or -99), reverse=True)
    return picks[:3]


def get_football_picks():
    """v4.6: football picks with intelligent deduplication."""
    fixtures  = collect_football_fixtures()
    stats     = build_football_stats(fixtures)
    all_picks = []
    for fix in fixtures:
        analysis = analyze_football_match(fix, stats.get(fix["home"]), stats.get(fix["away"]))
        for pick in analysis:
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

    result = deduped[:MAX_PICKS_SPORT]
    log.info("Football picks: %d (de %d fixtures, %d candidatos)", len(result), len(fixtures), len(all_picks))
    return result
