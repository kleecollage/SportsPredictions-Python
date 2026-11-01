"""ESPN Hidden API para juegos NBA (con líneas DraftKings)."""
import logging
from datetime import datetime, timedelta

from value_bot.config import ESPN_NBA_URL, http_get
from value_bot.scrapers.espn_lines import parse_lines


log = logging.getLogger(__name__)


def espn_nba_games(date_str):
    data = http_get(
        ESPN_NBA_URL,
        params={"dates": date_str.replace("-", "")},
        skip_codes=(404,),
    )
    return data.get("events", [])


def _is_nba_finals(headline: str) -> bool:
    """Solo las Finales de la NBA; "East Finals" o "Conference Finals" no lo son."""
    return "nba finals" in (headline or "").lower()


def espn_parse_nba(event):
    comps = event.get("competitions", [])
    if not comps:
        return None
    comp = comps[0]
    # Solo juegos sin empezar: en vivo ESPN quita las líneas y el modelo es pre-partido
    if comp.get("status", {}).get("type", {}).get("name", "") != "STATUS_SCHEDULED":
        return None
    competitors = comp.get("competitors", [])
    if len(competitors) < 2:
        return None
    home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
    away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

    def record(c):
        recs = c.get("records", [])
        return recs[0].get("summary", "?") if recs else "?"

    notes      = comp.get("notes", [])
    series_txt = notes[0].get("headline", "") if notes else ""
    is_finals  = _is_nba_finals(series_txt)

    return {
        "event_id":       str(event.get("id") or ""),
        "lines":          parse_lines(comp),
        "home":           home.get("team", {}).get("displayName", "?"),
        "away":           away.get("team", {}).get("displayName", "?"),
        "home_record":    record(home),
        "away_record":    record(away),
        "kickoff":        event.get("date", ""),
        "is_finals":      is_finals,
        "series_summary": series_txt,
        "venue":          comp.get("venue", {}).get("fullName", ""),
    }


def get_upcoming_nba_events(days=5):
    """Next NBA games (1-5 days ahead) for the Upcoming section."""
    upcoming = []
    today = datetime.now()
    for d in range(1, days + 1):
        ds = (today + timedelta(days=d)).strftime("%Y-%m-%d")
        for ev in espn_nba_games(ds):
            p = espn_parse_nba(ev)
            if p:
                upcoming.append({**p, "date": ds})
        if upcoming:
            break
    return upcoming[:3]
