"""Dimers NFL hub (https://www.dimers.com/nfl-hub): predicciones y best bets.

Capa 1: la API JSON que consume la propia página (levy-edge.statsinsider.com.au),
        sin key. Primero /round/options para saber la semana vigente y luego
        /round/matches con las best bets de los bookmakers.
Capa 2: Playwright sobre /nfl-hub leyendo el estado SSR de Angular
        (<script id="ng-state">), que trae la misma respuesta de /round/matches.

Los parsers son puros (sin red) para poder testearlos.
"""
import json
import logging
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from value_bot.config import PLAYWRIGHT_ENABLED, PLAYWRIGHT_TIMEOUT, USER_AGENTS, http_get
from value_bot.scrapers.playwright_scraper import PLAYWRIGHT_OK, _build_stealth_browser, _human_delay

log = logging.getLogger(__name__)

DIMERS_HUB_URL = "https://www.dimers.com/nfl-hub"
DIMERS_API_BASE = "https://levy-edge.statsinsider.com.au"
DIMERS_BOOKMAKERS = (
    "fanduel,betmgm,william_hill,draftkings,bet_365,bet_rivers_in,fanatics,"
    "espnbet,thescore,hard_rock,kalshi,novig,polymarket"
)
DIMERS_HEADERS = {
    "User-Agent": USER_AGENTS[0],
    "Origin": "https://www.dimers.com",
    "Referer": "https://www.dimers.com/",
    "Accept": "application/json",
}

# Tipo de apuesta Dimers → mercado del bot, con los lados válidos de cada uno
_MARKETS = {
    "h2h": ("moneyline", {"home", "away"}),
    "line": ("spread", {"home", "away"}),
    "total": ("total", {"over", "under"}),
}
_FINISHED_STATUSES = {"complete", "completed", "final", "closed", "cancelled", "postponed"}
_NG_STATE_RE = re.compile(r'<script id="ng-state" type="application/json">(.*?)</script>', re.S)
_ET = ZoneInfo("America/New_York")


# ── Parsers puros ─────────────────────────────────────────────────

def _obj(value) -> dict:
    return value if isinstance(value, dict) else {}


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_utc(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def pick_current_round(options, today: date) -> tuple[int, int] | None:
    """(season, semana) de la primera ronda activa que no ha terminado.

    `Round_Date` es el último día de la semana (el martes tras el MNF).
    """
    candidates = []
    for opt in options if isinstance(options, list) else []:
        opt = _obj(opt)
        if not opt.get("Active"):
            continue
        try:
            end = date.fromisoformat(str(opt.get("Round_Date")))
            season, week = int(opt["Year"]), int(opt["Round_Number"])
        except (KeyError, TypeError, ValueError):
            continue
        if end >= today:
            candidates.append((end, season, week))
    if not candidates:
        return None
    _, season, week = min(candidates)
    return season, week


def _team_name(team: dict) -> str:
    return " ".join(p for p in (team.get("Market"), team.get("Nickname")) if p).strip()


def _parse_bet(raw) -> dict | None:
    raw = _obj(raw)
    market, sides = _MARKETS.get(raw.get("type"), (None, set()))
    side = raw.get("bet")
    odds = _float(raw.get("odds"))
    win_prob = _float(raw.get("winProb"))
    if market is None or side not in sides or odds is None or odds <= 1 or win_prob is None:
        return None
    line = raw.get("markValue") if market == "total" else raw.get("lineValue")
    edge_pp = _float(raw.get("edge"))
    return {
        "market": market,
        "side": side,
        "line": _float(line) if market != "moneyline" else None,
        "odds": odds,
        "win_prob": win_prob,
        "market_prob": _float(raw.get("marketProb")),
        "prob_edge": edge_pp / 100 if edge_pp is not None else None,  # Dimers da puntos %
        "value": win_prob * odds - 1,
        "bookmaker": raw.get("bookmaker", ""),
        "is_best": bool(raw.get("isBest")),
    }


def _is_finished(live: dict) -> bool:
    statuses = {str(live.get(k, "")).lower() for k in ("matchStatus", "status")}
    return bool(statuses & _FINISHED_STATUSES)


def parse_dimers_match(raw, now: datetime) -> dict | None:
    """Partido de /round/matches → dict normalizado; None si ya empezó o viene roto."""
    raw = _obj(raw)
    match = _obj(raw.get("MatchData"))
    home_team, away_team = _obj(match.get("HomeTeam")), _obj(match.get("AwayTeam"))
    kickoff = _parse_utc(match.get("Date"))
    home, away = _team_name(home_team), _team_name(away_team)
    if not (home and away and kickoff and match.get("SIMatchID")):
        return None
    if kickoff <= now or _is_finished(_obj(raw.get("LiveData"))):
        return None

    pre = _obj(raw.get("PreData"))
    pred_home, pred_away = _float(pre.get("PredHomeScore")), _float(pre.get("PredAwayScore"))
    bets = raw.get("aggregatedBestBets")
    return {
        "match_id": match["SIMatchID"],
        "season": match.get("Season"),
        "week": match.get("RoundNumber"),
        "home": home,
        "away": away,
        "home_abv": home_team.get("Abv", ""),
        "away_abv": away_team.get("Abv", ""),
        "kickoff": match["Date"],
        "venue": match.get("Venue", ""),
        "home_win_prob": _float(pre.get("PythagHome")),
        "away_win_prob": _float(pre.get("PythagAway")),
        "pred_home_score": pred_home,
        "pred_away_score": pred_away,
        "pred_margin": _float(pre.get("PredMargin")),
        "pred_total": pred_home + pred_away if None not in (pred_home, pred_away) else None,
        "over_prob": _float(pre.get("OverWinPct")),
        "under_prob": _float(pre.get("UnderWinPct")),
        "updated_at": pre.get("LastUpdated", ""),
        "best_bets": [b for b in map(_parse_bet, bets if isinstance(bets, list) else []) if b],
        "source": "dimers",
    }


def extract_matches_from_ng_state(html: str) -> list:
    """Respuesta cruda de /round/matches embebida en el HTML SSR de /nfl-hub."""
    found = _NG_STATE_RE.search(html or "")
    if not found:
        return []
    try:
        state = json.loads(found.group(1))
    except json.JSONDecodeError:
        log.warning("Dimers: ng-state no es JSON válido")
        return []
    for key, entry in _obj(state).items():
        if "/round/matches" in key:
            body = _obj(entry).get("body")
            return body if isinstance(body, list) else []
    return []


def _parse_all(raw_matches: list, now: datetime) -> list:
    games = []
    for raw in raw_matches:
        try:
            game = parse_dimers_match(raw, now)
        except Exception:
            log.exception("Dimers: partido mal formado, se salta")
            continue
        if game:
            games.append(game)
    return games


# ── Capa 1: API JSON ──────────────────────────────────────────────

def get_dimers_nfl_predictions(now: datetime | None = None) -> list:
    """Partidos NFL pendientes de la semana vigente con predicción y best bets."""
    now = now or datetime.now(timezone.utc)
    options = http_get(f"{DIMERS_API_BASE}/round/options",
                       headers=DIMERS_HEADERS, params={"Sport": "NFL"})
    current = pick_current_round(options, now.astimezone(_ET).date())
    if current is None:
        log.info("Dimers NFL: sin semana activa")
        return []
    season, week = current
    raw_matches = http_get(
        f"{DIMERS_API_BASE}/round/matches",
        headers=DIMERS_HEADERS,
        params={
            "Sport": "NFL", "Round": week, "Season": season,
            "strip": "true", "best_bets": "true", "bookmakers": DIMERS_BOOKMAKERS,
        },
    )
    if not isinstance(raw_matches, list):
        log.warning("Dimers NFL: respuesta inesperada de /round/matches")
        return []
    games = _parse_all(raw_matches, now)
    log.info("Dimers NFL semana %d/%d: %d partidos pendientes", week, season, len(games))
    return games


# ── Capa 2: Playwright (fallback) ─────────────────────────────────

async def scrape_dimers_nfl_pw(now: datetime | None = None) -> list:
    """Fallback: carga /nfl-hub y lee los partidos del estado SSR de Angular."""
    if not (PLAYWRIGHT_OK and PLAYWRIGHT_ENABLED):
        return []
    from playwright.async_api import async_playwright
    html = ""
    try:
        async with async_playwright() as p:
            browser, context = await _build_stealth_browser(p)
            try:
                page = await context.new_page()
                await page.goto(DIMERS_HUB_URL, timeout=PLAYWRIGHT_TIMEOUT,
                                wait_until="domcontentloaded")
                await _human_delay(1500, 3000)
                html = await page.content()
            finally:
                await browser.close()
    except Exception as exc:
        log.warning("scrape_dimers_nfl_pw: %s", exc)
        return []
    games = _parse_all(extract_matches_from_ng_state(html), now or datetime.now(timezone.utc))
    log.info("Dimers NFL playwright: %d partidos pendientes", len(games))
    return games


async def fetch_dimers_nfl(now: datetime | None = None) -> list:
    """API primero; Playwright solo si la API no devolvió nada."""
    import asyncio
    games = await asyncio.to_thread(get_dimers_nfl_predictions, now)
    return games or await scrape_dimers_nfl_pw(now)


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    for g in asyncio.run(fetch_dimers_nfl()):
        log.info("%s @ %s  %s  home %.0f%%  pred %.1f-%.1f",
                 g["away"], g["home"], g["kickoff"], (g["home_win_prob"] or 0) * 100,
                 g["pred_away_score"] or 0, g["pred_home_score"] or 0)
        for b in g["best_bets"]:
            log.info("   %-9s %-5s %6s  @%.2f  p=%.3f  EV=%+.3f  %s",
                     b["market"], b["side"], b["line"], b["odds"], b["win_prob"],
                     b["value"], b["bookmaker"])
