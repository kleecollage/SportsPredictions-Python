"""
The Odds API para fútbol: 1X2 y totales (una llamada por liga y corrida) y BTTS por evento.

Cada llamada cuesta créditos (mercados × regiones; 500 al mes en el plan free): la respuesta
trae los restantes en `x-requests-remaining` y por debajo de ODDS_CREDITS_RESERVE solo se
pide el 1X2 (y solo 1X2 con los totales apagados: FOOTBALL_TOTALS_ENABLED). Los dos lados de un
mercado salen siempre del mismo bookmaker.

Los nombres de football-data ("FC Bayern München") y de The Odds API ("Bayern Munich") se
emparejan por palabras (`same_team`), no por prefijo: con prefijo los equipos "FC …" nunca tenían
cuota y, sin cuota real, no hay pick. Como "Paris FC" queda en {paris} ⊂ "Paris Saint Germain",
el evento además tiene que ser del día ET del fixture, gana la coincidencia exacta y dos parciales
son ambiguas (sin cuota antes que la de otro partido).
"""
import logging
import math
import re
import unicodedata
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from value_bot.analysis.football_model import FOOTBALL_TOTALS_ENABLED
from value_bot.config import ODDS_BASE, THE_ODDS_API_KEY
from value_bot.store import store

log = logging.getLogger(__name__)

ODDS_REGION = "eu"
ODDS_CREDITS_RESERVE = 60     # créditos que no se gastan en totales (quedan para el 1X2)
ODDS_TIMEOUT = 15
# Palabras que no distinguen equipos ("FC", "1.", "de") y traducciones de football-data → The Odds API
NAME_NOISE = frozenset({"fc", "afc", "cf", "sc", "ssc", "sk", "rc", "rcd", "club", "de", "and", "1"})
NAME_ALIASES = {"munchen": "munich", "internazionale": "inter", "milano": "milan", "praha": "prague",
                "lyonnais": "lyon", "rennais": "rennes", "brestois": "brest"}
TEAM_ALIASES = {"sporting clube de portugal": "sporting lisbon"}   # sin traducción palabra por palabra
ODDS_TZ = ZoneInfo("America/New_York")   # "date" del fixture = día ET (sports.football._et_date)


def _odds_get(path: str, params: dict) -> tuple[object, int | None]:
    """(json, créditos restantes) de The Odds API; ({}, None) si falla. Nunca loguea la key."""
    try:
        resp = requests.get(f"{ODDS_BASE}{path}", params={**params, "apiKey": THE_ODDS_API_KEY},
                            timeout=ODDS_TIMEOUT)
    except requests.exceptions.RequestException as exc:
        log.warning("The Odds API %s: %s", path, type(exc).__name__)
        return {}, None
    credits = _credits(resp.headers.get("x-requests-remaining"))
    if resp.status_code != 200:
        log.warning("The Odds API %s: HTTP %d", path, resp.status_code)
        return {}, credits
    try:
        return resp.json(), credits
    except ValueError:
        log.warning("The Odds API %s: respuesta no es JSON", path)
        return {}, credits


def _credits(raw: object) -> int | None:
    """Créditos restantes del header; None si falta o no es un número (no tumba la corrida)."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return int(value) if math.isfinite(value) else None


def _price(raw: object) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 1 else None


def _market(book: dict, key: str) -> list[dict]:
    """Outcomes del mercado `key` de un bookmaker (por clave, no por posición)."""
    for market in book.get("markets") or []:
        if isinstance(market, dict) and market.get("key") == key:
            return [o for o in market.get("outcomes") or [] if isinstance(o, dict)]
    return []


def _books(game: dict) -> list[dict]:
    return [b for b in game.get("bookmakers") or [] if isinstance(b, dict)]


def parse_h2h(game: dict) -> dict:
    """
    1X2 del primer bookmaker con los tres precios. Cada outcome se asigna por el nombre exacto
    del local/visitante del evento (en un derbi "Manchester …" el prefijo no distingue).
    """
    home, away = game.get("home_team"), game.get("away_team")
    if not (home and away) or home == away:
        return {}
    sides = {home: "home_odds", away: "away_odds", "Draw": "draw_odds"}
    for book in _books(game):
        result = {sides[o.get("name")]: price for o in _market(book, "h2h")
                  if o.get("name") in sides and (price := _price(o.get("price"))) is not None}
        if len(result) == len(sides):
            return result
    return {}


def _total_sides(book: dict) -> dict[float, dict]:
    """{línea: {"over": cuota, "under": cuota}} de un bookmaker; solo líneas .0 / .5 (sin cuartos)."""
    sides: dict[float, dict] = {}
    for o in _market(book, "totals"):
        side, price = (o.get("name") or "").lower(), _price(o.get("price"))
        try:
            line = float(o.get("point"))
        except (TypeError, ValueError):
            continue
        if side in ("over", "under") and price is not None and (line * 2).is_integer():
            sides.setdefault(line, {})[side] = price
    return {line: s for line, s in sides.items() if len(s) == 2}


def parse_totals(game: dict) -> dict:
    """
    Totales de la línea de consenso (la que más bookmakers ofrecen con los dos lados; empate →
    la más baja), Over y Under del mismo bookmaker. {} sin mercado utilizable.
    """
    by_book = [_total_sides(b) for b in _books(game)]
    counts = Counter(line for sides in by_book for line in sides)
    if not counts:
        return {}
    line = min(counts, key=lambda ln: (-counts[ln], ln))
    sides = next(s[line] for s in by_book if line in s)
    return {"total_line": line, "over_odds": sides["over"], "under_odds": sides["under"]}


def _name_words(name: str) -> frozenset[str]:
    """Palabras del nombre sin acentos, ruido ("FC", "de") ni diferencias de idioma."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    plain = " ".join(re.findall(r"[a-z0-9]+", plain.replace("&", " and ")))
    return frozenset(NAME_ALIASES.get(w, w) for w in TEAM_ALIASES.get(plain, plain).split() if w not in NAME_NOISE)


def same_team(a: str, b: str) -> bool:
    """Las palabras de un nombre contienen todas las del otro ("Galatasaray SK" ~ "Galatasaray")."""
    wa, wb = _name_words(a), _name_words(b)
    return bool(wa and wb) and (wa <= wb or wb <= wa)


def _event_day(game: dict) -> str | None:
    """Día ET de `commence_time`; None si falta o no es ISO con zona."""
    try:
        moment = datetime.fromisoformat(str(game.get("commence_time") or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(ODDS_TZ).date().isoformat() if moment.tzinfo else None


def _same_names(game: dict, home: str, away: str) -> bool:
    return (_name_words(home) == _name_words(game.get("home_team") or "")
            and _name_words(away) == _name_words(game.get("away_team") or ""))


def _find_game(games: list, home: str, away: str, date: str | None = None) -> dict | None:
    """
    Evento del fixture: del día ET `date` (si el evento trae hora), coincidencia exacta de
    palabras antes que parcial; con dos parciales y ninguna exacta, None (ambiguo).
    """
    candidates = [g for g in games if isinstance(g, dict)
                  and (date is None or _event_day(g) in (None, date))
                  and same_team(home, g.get("home_team") or "") and same_team(away, g.get("away_team") or "")]
    if exact := [g for g in candidates if _same_names(g, home, away)]:
        return exact[0]
    if len(candidates) > 1:
        log.warning("The Odds API: %s vs %s ambiguo (%s) → sin cuota", home, away,
                    ", ".join(f"{g.get('home_team')} vs {g.get('away_team')}" for g in candidates))
        return None
    return candidates[0] if candidates else None


def _warn_unmatched(sport_key: str, home: str, away: str, n_games: int) -> None:
    """Una vez por partido y corrida: sin cuota no hay pick y no debe parecer un día sin value."""
    if (sport_key, home, away) in store.odds_unmatched:
        return
    store.odds_unmatched.add((sport_key, home, away))
    log.warning("The Odds API %s: sin evento para %s vs %s (%d eventos en la liga) → sin cuota",
                sport_key, home, away, n_games)


def _league_games(sport_key: str) -> list:
    """Partidos con cuotas de la liga, una llamada por corrida (h2h + totales si hay créditos)."""
    if sport_key not in store.odds_cache:
        low = store.odds_credits_left is not None and store.odds_credits_left < ODDS_CREDITS_RESERVE
        if low and FOOTBALL_TOTALS_ENABLED:
            log.warning("The Odds API: %d créditos (< %d) → solo 1X2, sin totales",
                        store.odds_credits_left, ODDS_CREDITS_RESERVE)
        data, credits = _odds_get(f"/sports/{sport_key}/odds/",
                                  {"regions": ODDS_REGION,
                                   "markets": "h2h,totals" if FOOTBALL_TOTALS_ENABLED and not low else "h2h"})
        if credits is not None:
            store.odds_credits_left = credits
        store.odds_cache[sport_key] = data if isinstance(data, list) else []
    return store.odds_cache[sport_key]


def get_real_odds(sport_key: str, home: str, away: str, date: str | None = None) -> dict | None:
    """
    1X2 (home/draw/away_odds), totales (total_line, over/under_odds) e id de evento del partido
    del día ET `date` (fecha del fixture); None sin partido.
    """
    if not THE_ODDS_API_KEY or not sport_key:
        return None
    games = _league_games(sport_key)
    game = _find_game(games, home, away, date)
    if game is None:
        if games:
            _warn_unmatched(sport_key, home, away, len(games))
        return None
    odds = {**parse_h2h(game), **parse_totals(game)}
    return {**odds, "odds_event_id": game.get("id")} if odds else None


def parse_btts(game: object) -> tuple[float, float] | None:
    """(sí, no) de "Ambos Marcan" del primer bookmaker con los dos precios."""
    for book in _books(game) if isinstance(game, dict) else []:
        prices = {(o.get("name") or "").lower(): _price(o.get("price")) for o in _market(book, "btts")}
        if prices.get("yes") and prices.get("no"):
            return prices["yes"], prices["no"]
    return None


def get_btts_odds(sport_key: str, event_id: str) -> tuple[float, float] | None:
    """Cuota BTTS de un evento (cuesta créditos: solo bajo demanda, nunca en el runner diario)."""
    if not (THE_ODDS_API_KEY and sport_key and event_id):
        return None
    data, credits = _odds_get(f"/sports/{sport_key}/events/{event_id}/odds",
                              {"regions": ODDS_REGION, "markets": "btts"})
    if credits is not None:
        store.odds_credits_left = credits
    return parse_btts(data)
