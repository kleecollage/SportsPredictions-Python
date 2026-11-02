"""ESPN Hidden API para juegos NBA (con líneas DraftKings)."""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from value_bot.config import ESPN_NBA_URL, http_get
from value_bot.scrapers.espn_lines import obj, parse_lines
from value_bot.store import store


log = logging.getLogger(__name__)

ESPN_TZ = ZoneInfo("America/New_York")   # ESPN agrupa la jornada por día ET
UPCOMING_DAYS = 5
UPCOMING_MAX = 3


def now_et() -> datetime:
    return datetime.now(ESPN_TZ)


def espn_nba_games(date_str: str) -> list[dict]:
    """Eventos del scoreboard de ese día (AAAA-MM-DD); [] si ESPN no responde o cambia la forma."""
    data = http_get(
        ESPN_NBA_URL,
        params={"dates": date_str.replace("-", "")},
        skip_codes=(404,),
    )
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        log.warning("ESPN: scoreboard NBA sin respuesta para %s", date_str)
        return []
    return events


def _is_nba_finals(headline: str) -> bool:
    """Solo las Finales de la NBA; "East Finals" o "Conference Finals" no lo son."""
    return "nba finals" in (headline or "").lower()


def _first_obj(items: object) -> dict:
    """Primer elemento de una lista ESPN como dict; {} si falta o es null."""
    first = items[0] if isinstance(items, list) and items else None
    return first if isinstance(first, dict) else {}


def _sides(comp: dict) -> tuple[dict, dict] | None:
    """(local, visitante) solo si ESPN marca exactamente uno de cada uno; nunca por orden."""
    competitors = [c for c in (comp.get("competitors") or []) if isinstance(c, dict)]
    homes = [c for c in competitors if c.get("homeAway") == "home"]
    aways = [c for c in competitors if c.get("homeAway") == "away"]
    if len(homes) != 1 or len(aways) != 1:
        return None
    return homes[0], aways[0]


def _record(competitor: dict) -> str:
    return _first_obj(competitor.get("records")).get("summary") or "?"


def espn_parse_nba(event: object) -> dict | None:
    """Juego sin empezar con local, visitante y líneas; None si el evento no es confiable."""
    comp = _first_obj(event.get("competitions") if isinstance(event, dict) else None)
    # Solo juegos sin empezar: en vivo ESPN quita las líneas y el modelo es pre-partido
    if obj(obj(comp, "status"), "type").get("name") != "STATUS_SCHEDULED":
        return None
    sides = _sides(comp)
    home_name, away_name = (obj(side, "team").get("displayName") for side in sides or ({}, {}))
    if not (home_name and away_name):
        # Programado pero sin equipos claros = esquema ESPN cambiado, no un día sin NBA
        log.warning("ESPN NBA: evento %s sin local/visitante claro, se salta", event.get("id"))
        return None
    home, away = sides

    series_txt = _first_obj(comp.get("notes")).get("headline") or ""
    return {
        "event_id":       str(event.get("id") or ""),
        "lines":          parse_lines(comp),
        "home":           home_name,
        "away":           away_name,
        "home_record":    _record(home),
        "away_record":    _record(away),
        "kickoff":        event.get("date") or "",
        "is_finals":      _is_nba_finals(series_txt),
        "series_summary": series_txt,
        "venue":          obj(comp, "venue").get("fullName") or "",
    }


def get_upcoming_nba_events(days: int = UPCOMING_DAYS) -> list[dict]:
    """
    Próximos juegos NBA (primer día con juegos tras el del slate de picks, o tras hoy ET)
    para la sección "Próximos partidos"; así no repite los juegos de los picks.
    """
    start = (datetime.strptime(store.nba_slate_date, "%Y-%m-%d").date() if store.nba_slate_date
             else now_et().date())
    upcoming = []
    for d in range(1, days + 1):
        ds = (start + timedelta(days=d)).isoformat()
        upcoming = [{**p, "date": ds} for p in map(espn_parse_nba, espn_nba_games(ds)) if p]
        if upcoming:
            break
    return upcoming[:UPCOMING_MAX]
