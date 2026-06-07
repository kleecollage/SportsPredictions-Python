"""Parlay builder — same-sport priority, diversity constraints."""
import logging
from value_bot.config import MIN_CONF_PARLAY

log = logging.getLogger(__name__)

MIN_CONF_P_FB = 68
MIN_PROB_2    = 0.38
MIN_PROB_3    = 0.27


def build_parlays(all_picks):
    """
    v4.6: selective parlays.
    - Min conf 68% for football legs
    - Max 1 pick per league in the same parlay
    - Double: combined prob >= 38%
    - Triple: combined prob >= 27%
    - Priority: diverse football > NBA > mixed high-confidence
    """
    hi_pool  = [p for p in all_picks if p["confidence"] >= MIN_CONF_P_FB]
    pool_nba = [p for p in all_picks if p.get("sport") == "nba" and p["confidence"] >= MIN_CONF_PARLAY]
    fp_hi    = [p for p in hi_pool if p.get("sport") == "football"]

    if len(hi_pool) + len(pool_nba) < 2:
        log.info("Parlays: pool insuficiente (%d picks hi + %d nba)", len(hi_pool), len(pool_nba))
        return []

    parlays = []

    def combined_stats(legs):
        o = pr = 1.0
        for leg in legs:
            o  *= leg.get("est_odds", 1.80)
            pr *= leg.get("our_prob", 0.65)
        return round(o, 2), round(pr * 100, 1)

    def is_multi_sport(legs):
        return len(set(l.get("sport", "?") for l in legs)) > 1

    def leagues_diverse(legs):
        lgs = [l.get("league", l.get("sport", "?")) for l in legs]
        return len(lgs) == len(set(lgs))

    def best_pair(picks_list, require_diff_game=True, require_div_league=True, min_prob=MIN_PROB_2):
        best = None
        best_score = -1
        for i in range(len(picks_list)):
            for j in range(i + 1, len(picks_list)):
                l1, l2 = picks_list[i], picks_list[j]
                same_game = (l1.get("home") == l2.get("home") and
                             l1.get("away") == l2.get("away"))
                if require_diff_game and same_game:
                    continue
                if require_div_league and not leagues_diverse([l1, l2]):
                    continue
                prob = l1.get("our_prob", 0.65) * l2.get("our_prob", 0.65)
                if prob < min_prob:
                    continue
                score = l1["confidence"] + l2["confidence"]
                if score > best_score:
                    best = [l1, l2]
                    best_score = score
        return best

    def best_triple(picks_list, require_div_league=True, min_prob=MIN_PROB_3):
        best = None
        best_score = -1
        for i in range(len(picks_list)):
            for j in range(i + 1, len(picks_list)):
                for k in range(j + 1, len(picks_list)):
                    legs  = [picks_list[i], picks_list[j], picks_list[k]]
                    games = [(l.get("home"), l.get("away")) for l in legs]
                    if len(set(games)) < len(games):
                        continue
                    if require_div_league and not leagues_diverse(legs):
                        continue
                    prob = 1.0
                    for l in legs:
                        prob *= l.get("our_prob", 0.65)
                    if prob < min_prob:
                        continue
                    score = sum(l["confidence"] for l in legs)
                    if score > best_score:
                        best = legs
                        best_score = score
        return best

    best_2 = (
        best_pair(fp_hi)
        or best_pair(fp_hi, require_div_league=False)
        or best_pair(pool_nba)
        or best_pair(hi_pool, min_prob=MIN_PROB_2 - 0.05)
        or best_pair(hi_pool, require_div_league=False, min_prob=MIN_PROB_2 - 0.08)
    )

    if best_2:
        co, cp = combined_stats(best_2)
        multi     = is_multi_sport(best_2)
        sport_tag = " MIXTO" if multi else ""
        label = (f"⚡ PARLAY DOBLE ⚽{sport_tag}"
                 if best_2[0].get("sport") == "football"
                 else f"⚡ PARLAY DOBLE 🏀{sport_tag}")
        parlays.append({"label": label, "legs": best_2,
                        "combined_odds": co, "combined_prob": cp})

    best_3 = (
        best_triple(fp_hi)
        or best_triple(fp_hi, require_div_league=False)
        or best_triple(pool_nba)
        or best_triple(hi_pool, require_div_league=False, min_prob=MIN_PROB_3 - 0.05)
    )

    if best_3:
        co, cp = combined_stats(best_3)
        sport_em = "⚽" if best_3[0].get("sport") == "football" else "🏀"
        parlays.append({"label": f"🔥 PARLAY TRIPLE {sport_em}",
                        "legs": best_3,
                        "combined_odds": co, "combined_prob": cp})

    log.info("Parlays: %d (⚽hi:%d 🏀pool:%d hi_total:%d)",
             len(parlays), len(fp_hi), len(pool_nba), len(hi_pool))
    return parlays
