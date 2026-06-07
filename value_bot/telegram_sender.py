"""Telegram message formatting and sending."""
import asyncio
import logging
from datetime import datetime

from telegram import Bot
from telegram.constants import ParseMode

from value_bot.config import BOT_TOKEN, CHAT_ID, VALUE_THRESHOLD
from value_bot.sports.nba import _nba_team_form_cache
from value_bot.analysis.picks import _form_emoji

log = logging.getLogger(__name__)

DAYS_ES   = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MONTHS_ES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
             "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
SPORT_EMO = {"football": "⚽", "nba": "🏀"}


def bar(pct, n=8):
    f = round(pct / 100 * n)
    return "█" * f + "░" * (n - f)


def fmt_ko(iso):
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%d/%m %H:%M UTC")
    except Exception:
        return ""


def q_icon(dq):
    return {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(dq or "low", "⚪")


def conf_stars(conf):
    if conf >= 75:
        return "⭐⭐⭐"
    if conf >= 68:
        return "⭐⭐"
    return "⭐"


def vbadge(v):
    return f' 💎 <b>VALUE +{v:.0%}</b>' if v and v >= VALUE_THRESHOLD else ""


def _group_picks_by_game(picks):
    groups = {}
    for p in picks:
        key = (p.get("sport", "?"), p.get("home", "?"), p.get("away", "?"))
        if key not in groups:
            groups[key] = {"game_info": p, "picks": []}
        groups[key]["picks"].append(p)
    return list(groups.values())


def _game_block(group):
    gi     = group["game_info"]
    gpicks = group["picks"]
    lines  = []

    ko    = fmt_ko(gi.get("kickoff", ""))
    ko_s  = f"  📅 {ko}" if ko else ""
    venue = gi.get("venue", "")
    sport = gi.get("sport", "?")
    flag  = gi.get("flag", SPORT_EMO.get(sport, "🎯"))

    lines.append(f"<b>📋 {gi['home']} vs {gi['away']}</b>")
    lines.append(f"   {flag} {gi.get('league', '?')}{ko_s}")
    if venue:
        lines.append(f"   🏟 {venue}")

    if sport == "nba":
        h_rec = gi.get("home_record", "")
        a_rec = gi.get("away_record", "")
        h_form_data = _nba_team_form_cache.get(gi["home"], {})
        a_form_data = _nba_team_form_cache.get(gi["away"], {})
        if h_form_data or a_form_data:
            h_info = (f"{h_form_data.get('ppg','?')}pts "
                      f"{_form_emoji(h_form_data.get('form',''))}"
                      if h_form_data else (h_rec or "—"))
            a_info = (f"{a_form_data.get('ppg','?')}pts "
                      f"{_form_emoji(a_form_data.get('form',''))}"
                      if a_form_data else (a_rec or "—"))
            lines.append(f"   📊 <code>{gi['home'].split()[-1]}: {h_info} | "
                         f"{gi['away'].split()[-1]}: {a_info}</code>")
        elif h_rec or a_rec:
            lines.append(f"   📊 <code>{gi['home'].split()[-1]}: {h_rec or '—'} | "
                         f"{gi['away'].split()[-1]}: {a_rec or '—'}</code>")

    if gi.get("series_summary"):
        lines.append(f"   🏆 <i>{gi['series_summary']}</i>")

    lines.append("")

    for idx, p in enumerate(gpicks, 1):
        icon = "✅" if idx == 1 else "🎯"
        ol   = "Cuota real" if p.get("real_odds_source") else "Cuota est."
        lines.append(
            f"   {icon} {conf_stars(p['confidence'])} <b>{p['type']}</b>"
            f"  ·  {ol}: <code>{p.get('est_odds','?')}</code>{vbadge(p.get('value'))}"
        )
        lines.append(
            f"   📊 {bar(p['confidence'])} <b>{p['confidence']}%</b> {q_icon(p.get('data_quality','low'))}"
        )
        lines.append(f"   💡 <i>{p['reasoning']}</i>")
        if idx < len(gpicks):
            lines.append("")

    lines.append("─────────────────────")
    return lines


def build_message(fp, np, parlays, upcoming=None):
    now = datetime.now()
    ds  = f"{DAYS_ES[now.weekday()]}, {now.day} de {MONTHS_ES[now.month-1]} de {now.year}"

    lines = [
        "🏆 <b>VALUE BOT PRO v4.6</b>",
        f"📅 {ds}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    if not fp and not np:
        lines += [
            "⚠️ <b>Sin partidos disponibles hoy</b>",
            "",
            "No se encontraron fixtures en las próximas 72h.",
            "<i>El bot verificará automáticamente mañana.</i>",
        ]
    else:
        total = len(fp) + len(np)
        lines.append(f"🎯 <b>{total} picks generados hoy</b>")
        lines.append("")

        if np:
            finals_picks  = [p for p in np if p.get("is_finals")]
            regular_picks = [p for p in np if not p.get("is_finals")]

            if finals_picks:
                lines += [
                    "━━━━━━━━━━━━━━━━━━━━━━",
                    "🏆 <b>NBA FINALS 2026</b>",
                    "━━━━━━━━━━━━━━━━━━━━━━",
                    "",
                ]
                for group in _group_picks_by_game(finals_picks):
                    lines.extend(_game_block(group))
                lines.append("")

            if regular_picks:
                groups = _group_picks_by_game(regular_picks)
                lines += [
                    "━━━━━━━━━━━━━━━━━━━━━━",
                    f"🏀 <b>NBA</b> ({len(groups)} juego{'s' if len(groups)>1 else ''}  ·  {len(regular_picks)} picks)",
                    "━━━━━━━━━━━━━━━━━━━━━━",
                    "",
                ]
                for g in groups:
                    lines.extend(_game_block(g))
                lines.append("")

        if fp:
            vcount = sum(1 for p in fp if p.get("value") and p["value"] >= VALUE_THRESHOLD)
            vsub   = f"  ·  {vcount} 💎 value{'s' if vcount>1 else ''}" if vcount else ""
            groups = _group_picks_by_game(fp)
            lines += [
                "━━━━━━━━━━━━━━━━━━━━━━",
                f"⚽ <b>FÚTBOL</b> ({len(groups)} partido{'s' if len(groups)>1 else ''}  ·  {len(fp)} picks{vsub})",
                "━━━━━━━━━━━━━━━━━━━━━━",
                "",
            ]
            for g in groups:
                lines.extend(_game_block(g))
            lines.append("")

        if parlays:
            lines += [
                "━━━━━━━━━━━━━━━━━━━━━━",
                "🎰 <b>PARLAYS RECOMENDADOS</b>",
                "━━━━━━━━━━━━━━━━━━━━━━",
                "",
            ]
            for parlay in parlays:
                lines.append(f"<b>{parlay['label']}</b>")
                lines.append(
                    f"   Cuota combinada: <code>{parlay['combined_odds']}x</code>  ·  "
                    f"Prob. conjunta: <b>~{parlay['combined_prob']:.1f}%</b>"
                )
                for leg in parlay["legs"]:
                    em = SPORT_EMO.get(leg.get("sport", ""), "🎯")
                    lines.append(
                        f"   {em} {leg['home']} vs {leg['away']} "
                        f"→ <b>{leg['type']}</b> ({leg['confidence']}%)"
                    )
                lines.append("")

    if upcoming:
        lines += [
            "━━━━━━━━━━━━━━━━━━━━━━",
            "📅 <b>PRÓXIMOS PARTIDOS</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "",
        ]
        for u in upcoming:
            ko   = fmt_ko(u.get("kickoff", ""))
            ko_s = f"  ·  {ko}" if ko else f"  ·  {u.get('date','')}"
            icon = "🏆" if u.get("is_finals") else "🏀"
            series = f" <i>({u['series_summary']})</i>" if u.get("series_summary") else ""
            lines.append(f"   {icon} <b>{u['home']} vs {u['away']}</b>{series}")
            lines.append(f"   {ko_s}")
            lines.append("")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "⚠️ <i>Solo análisis informativo. Juega con responsabilidad.</i>",
        "🤖 <i>Value Bot Pro v4.6  ·  Multi-Deporte + Playwright</i>",
    ]
    return "\n".join(lines)


async def send_telegram(msg):
    bot = Bot(token=BOT_TOKEN)
    for i in range(0, len(msg), 4000):
        await bot.send_message(
            chat_id=CHAT_ID, text=msg[i:i+4000], parse_mode=ParseMode.HTML
        )
        if i + 4000 < len(msg):
            await asyncio.sleep(0.5)
