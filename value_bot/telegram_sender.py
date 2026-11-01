"""Telegram message formatting and sending."""
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Bot
from telegram.constants import ParseMode

from value_bot.config import BOT_TOKEN, CHAT_ID, VALUE_THRESHOLD
from value_bot.store import store
from value_bot.analysis.picks import _form_emoji

log = logging.getLogger(__name__)

DAYS_ES   = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MONTHS_ES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
             "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
SPORT_EMO = {"football": "⚽", "nba": "🏀", "nfl": "🏈"}
KICKOFF_TZ = ZoneInfo("America/New_York")  # misma zona que el scheduler y las fechas ESPN
PERF_MIN_SAMPLE = 5  # con menos picks resueltos el rendimiento se muestra con aviso


def bar(pct, n=8):
    f = round(pct / 100 * n)
    return "█" * f + "░" * (n - f)


def fmt_ko(iso) -> str:
    """Kickoff ISO → '25/10 16:25 ET'; solo fecha → '25/10'; cualquier otra cosa → ''."""
    if not iso or not isinstance(iso, str):
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if len(iso) == len("YYYY-MM-DD"):
        return dt.strftime("%d/%m")  # sin hora: no inventar una medianoche
    if dt.tzinfo is None:
        return dt.strftime("%d/%m %H:%M")
    return dt.astimezone(KICKOFF_TZ).strftime("%d/%m %H:%M ET")


def plural(n: int, word: str) -> str:
    """'1 pick', '2 picks' (las palabras del mensaje forman el plural con -s)."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _section_counts(groups: list, picks: list, game_word: str, vcount: int = 0) -> str:
    """'1 juego  ·  1 pick  ·  1 💎 value' para el encabezado de cada deporte."""
    vsub = f"  ·  {vcount} 💎 value{'s' if vcount > 1 else ''}" if vcount else ""
    return f"{plural(len(groups), game_word)}  ·  {plural(len(picks), 'pick')}{vsub}"


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
        h_form_data = store.nba_team_form_cache.get(gi["home"], {})
        a_form_data = store.nba_team_form_cache.get(gi["away"], {})
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

    if sport == "nfl":
        h_rec = gi.get("home_record") or "—"
        a_rec = gi.get("away_record") or "—"
        lines.append(f"   📊 <code>{gi['home'].split()[-1]}: {h_rec} | "
                     f"{gi['away'].split()[-1]}: {a_rec}</code>")
        provider = (gi.get("lines") or {}).get("provider")
        if provider:
            lines.append(f"   💵 <i>Líneas: {provider}</i>")

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


def _performance_block(perf: dict) -> str:
    """Bloque HTML de rendimiento: picks resueltos en los últimos 7 días."""
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "📈 <b>RENDIMIENTO (resueltos en 7 días · modelo actual)</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"   🎯 Picks resueltos: <b>{perf['total_picks']}</b>  ·  Win rate: <b>{perf['win_rate']}%</b>",
        f"   💰 ROI: <b>{perf['roi_pct']:+.1f}%</b>  ·  Profit: <b>{perf['total_profit']:+.2f}u</b>",
    ]
    if perf["total_picks"] < PERF_MIN_SAMPLE:
        lines.append(f"   ⚠️ <i>Muestra pequeña ({plural(perf['total_picks'], 'pick')}): "
                     f"todavía no dice nada del modelo.</i>")
    by_sport = perf.get("by_sport", {})
    if by_sport:
        parts = []
        for sport, sd in by_sport.items():
            em = SPORT_EMO.get(sport, "🎯")
            parts.append(f"{em} {sd['win_rate']}% WR")
        lines.append("   " + "  |  ".join(parts))

    by_quality = perf.get("by_quality", {})
    q_parts = []
    for dq in ("high", "medium", "low"):
        if dq in by_quality:
            qd = by_quality[dq]
            icon = q_icon(dq)
            wr = qd.get("win_rate", round(qd.get("wins", 0) / max(qd.get("total", 1), 1) * 100, 1))
            q_parts.append(f"{icon} {dq.capitalize()}: {wr}%")
    if q_parts:
        lines.append("   " + "  |  ".join(q_parts))

    lines.append("")
    return "\n".join(lines)


def _no_picks_block(analyzed: dict | None) -> list[str]:
    """Sin picks: con juegos analizados dice cuántos; sin juegos, que no hubo partidos."""
    counts = {sport: n for sport, n in (analyzed or {}).items() if n}
    if not counts:
        return [
            "⚠️ <b>Sin partidos disponibles hoy</b>",
            "",
            "No se encontraron fixtures en las próximas 72h.",
            "<i>El bot verificará automáticamente mañana.</i>",
            "",
        ]
    noun = {"football": "partido"}
    parts = [f"{SPORT_EMO.get(sport, '🎯')} {plural(n, noun.get(sport, 'juego'))}" for sport, n in counts.items()]
    return [
        "🔍 <b>Sin picks hoy</b>",
        "",
        f"Analizados: {'  ·  '.join(parts)}",
        "<i>Ningún pick pasó los filtros de value contra el mercado.</i>",
        "",
    ]


SEP = "━━━━━━━━━━━━━━━━━━━━━━"


def _date_line() -> str:
    now = datetime.now()
    return f"📅 {DAYS_ES[now.weekday()]}, {now.day} de {MONTHS_ES[now.month-1]} de {now.year}"


def _value_count(picks: list) -> int:
    return sum(1 for p in picks if p.get("value") and p["value"] >= VALUE_THRESHOLD)


def _picks_section(header: str, picks: list, groups: list | None = None) -> list[str]:
    """Encabezado de sección + un bloque por partido."""
    lines = [SEP, header, SEP, ""]
    for group in groups if groups is not None else _group_picks_by_game(picks):
        lines.extend(_game_block(group))
    lines.append("")
    return lines


def _nba_sections(np: list) -> list[str]:
    finals  = [p for p in np if p.get("is_finals")]
    regular = [p for p in np if not p.get("is_finals")]
    lines = _picks_section(f"🏆 <b>NBA FINALS {datetime.now().year}</b>", finals) if finals else []
    if regular:
        groups = _group_picks_by_game(regular)
        lines += _picks_section(f"🏀 <b>NBA</b> ({_section_counts(groups, regular, 'juego')})", regular, groups)
    return lines


def _counted_section(emoji_title: str, picks: list, noun: str) -> list[str]:
    groups = _group_picks_by_game(picks)
    counts = _section_counts(groups, picks, noun, _value_count(picks))
    return _picks_section(f"{emoji_title} ({counts})", picks, groups)


def _picks_blocks(fp: list, np: list, nflp: list) -> list[str]:
    total = len(fp) + len(np) + len(nflp)
    generated = "generado" if total == 1 else "generados"
    lines = [f"🎯 <b>{plural(total, 'pick')} {generated} hoy</b>", ""]
    if np:
        lines += _nba_sections(np)
    if fp:
        lines += _counted_section("⚽ <b>FÚTBOL</b>", fp, "partido")
    if nflp:
        lines += _counted_section("🏈 <b>NFL</b>", nflp, "juego")
    return lines


def _parlays_block(parlays: list) -> list[str]:
    if not parlays:
        return []
    lines = [SEP, "🎰 <b>PARLAYS</b>", SEP,
             "<i>Un parlay multiplica el edge del modelo y también su error.</i>", ""]
    for parlay in parlays:
        lines.append(f"<b>{parlay['label']}</b>")
        lines.append(
            f"   Cuota combinada: <code>{parlay['combined_odds']}x</code>  ·  "
            f"Prob. conjunta: <b>~{parlay['combined_prob']:.1f}%</b>  ·  "
            f"EV modelo: <b>{parlay['combined_value']:+.1%}</b>"
        )
        for leg in parlay["legs"]:
            em = SPORT_EMO.get(leg.get("sport", ""), "🎯")
            lines.append(f"   {em} {leg['home']} vs {leg['away']} → <b>{leg['type']}</b> ({leg['confidence']}%)")
        lines.append("")
    return lines


def _upcoming_block(upcoming: list | None) -> list[str]:
    if not upcoming:
        return []
    lines = [SEP, "📅 <b>PRÓXIMOS PARTIDOS</b>", SEP, ""]
    for u in upcoming:
        ko   = fmt_ko(u.get("kickoff", ""))
        ko_s = f"  ·  {ko}" if ko else f"  ·  {u.get('date','')}"
        icon = "🏆" if u.get("is_finals") else "🏀"
        series = f" <i>({u['series_summary']})</i>" if u.get("series_summary") else ""
        lines += [f"   {icon} <b>{u['home']} vs {u['away']}</b>{series}", f"   {ko_s}", ""]
    return lines


def build_message(fp, np, parlays, upcoming=None, perf=None, nflp=None, analyzed=None):
    lines = ["🏆 <b>VALUE BOT PRO v4.6</b>", _date_line(), SEP, ""]
    nflp = nflp or []
    if not fp and not np and not nflp:
        lines += _no_picks_block(analyzed)
    else:
        lines += _picks_blocks(fp, np, nflp) + _parlays_block(parlays)
    lines += _upcoming_block(upcoming)

    # Bloque de rendimiento (solo si hay datos resueltos)
    if perf and perf.get("total_picks", 0) >= 1:
        lines.append(_performance_block(perf))

    lines += [
        SEP,
        "⚠️ <i>Solo análisis informativo. Juega con responsabilidad.</i>",
        "🤖 <i>Value Bot Pro v4.6  ·  Multi-Deporte + Playwright</i>",
    ]
    return "\n".join(lines)


def _weekly_summary_block(roi: dict) -> list[str]:
    if roi.get("error"):
        return [
            "⚠️ <i>Sin picks resueltos del modelo actual esta semana.</i>",
            "<i>El reporte se enriquecerá a medida que se acumulen resultados.</i>",
            "",
        ]
    lines = [
        f"🎯 <b>Resumen general (resueltos en {roi['period_days']} días · modelo actual)</b>",
        f"   Picks resueltos: <b>{roi['total_picks']}</b>",
        f"   Win rate: <b>{roi['win_rate']}%</b>",
        f"   ROI: <b>{roi['roi_pct']:+.1f}%</b>  ·  Profit: <b>{roi['total_profit']:+.2f}u</b>",
        "",
    ]
    by_sport = roi.get("by_sport", {})
    if by_sport:
        lines.append("📈 <b>Por deporte</b>")
        for sport, sd in by_sport.items():
            em = SPORT_EMO.get(sport, "🎯")
            lines.append(f"   {em} <b>{sport.upper()}</b>: "
                         f"{sd['win_rate']}% WR  ·  {sd['profit']:+.2f}u  ·  {plural(sd['total'], 'pick')}")
        lines.append("")
    by_quality = roi.get("by_quality", {})
    if by_quality:
        lines.append("🔍 <b>Por calidad de datos</b>")
        for dq in ("high", "medium", "low"):
            if dq in by_quality:
                qd = by_quality[dq]
                wr = qd.get("win_rate", round(qd.get("wins", 0) / max(qd.get("total", 1), 1) * 100, 1))
                lines.append(f"   {q_icon(dq)} {dq.capitalize()}: {wr}% WR  ·  {plural(qd['total'], 'pick')}")
        lines.append("")
    return lines


def _parlay_summary_lines(parlays: dict | None) -> list[str]:
    if not parlays:
        return []
    losses = parlays["total"] - parlays["wins"]
    return [
        f"🎰 <b>Parlays (resueltos en {parlays['period_days']} días)</b>: {parlays['wins']}-{losses}  ·  "
        f"{parlays['profit']:+.2f}u  ·  {plural(parlays['total'], 'parlay')}",
        "",
    ]


def _calibration_block(calib: list) -> list[str]:
    if not calib:
        return []
    lines = [SEP, "🧮 <b>Calibración del modelo</b>", "<code>Bucket     Predicho  Real      Delta</code>"]
    for c in calib:
        delta_sign = "+" if c["delta"] >= 0 else ""
        lines.append(
            f"<code>{c['confidence_bucket']:<10} "
            f"{c['predicted_prob']:>5}%    "
            f"{c['actual_win_rate']:>5}%    "
            f"{delta_sign}{c['delta']}%</code>"
        )
    lines.append("")
    return lines


def _recent_list(title: str, mark: str, picks: list) -> list[str]:
    if not picks:
        return []
    lines = [SEP, title]
    for r in picks:
        em = SPORT_EMO.get(r["sport"], "🎯")
        lines.append(f"   {em} {mark} <b>{r['pick_type']}</b> ({r['home']} vs {r['away']})  ·  {r['run_date']}")
    lines.append("")
    return lines


def _recent_block(recent: list) -> list[str]:
    best  = sorted([r for r in recent if r.get("outcome") == "win"],
                   key=lambda x: x.get("est_odds", 0) or 0, reverse=True)[:3]
    worst = [r for r in recent if r.get("outcome") == "loss"][-3:]
    return (_recent_list("🏆 <b>Mejores picks de la semana</b>", "✅", best)
            + _recent_list("📉 <b>Picks fallidos</b>", "❌", worst))


def build_weekly_report(roi: dict, calib: list, recent: list, parlays: dict | None = None) -> str:
    """Mensaje de reporte semanal completo para envío los lunes."""
    lines = ["📊 <b>VALUE BOT PRO — REPORTE SEMANAL</b>", _date_line(), SEP, ""]
    lines += (_weekly_summary_block(roi) + _parlay_summary_lines(parlays)
              + _calibration_block(calib) + _recent_block(recent or []))
    lines += [
        SEP,
        "⚠️ <i>Solo análisis informativo. Juega con responsabilidad.</i>",
        "🤖 <i>Value Bot Pro v4.6  ·  Reporte Semanal</i>",
    ]
    return "\n".join(lines)


def _split_html_message(msg: str, max_len: int = 3900) -> list:
    """Corta el mensaje en líneas completas para no partir tags HTML a la mitad."""
    chunks, current, size = [], [], 0
    for line in msg.split("\n"):
        line_len = len(line) + 1  # +1 por el \n
        if size + line_len > max_len and current:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


async def send_telegram(msg):
    bot = Bot(token=BOT_TOKEN)
    chunks = _split_html_message(msg)
    for idx, chunk in enumerate(chunks):
        await bot.send_message(
            chat_id=CHAT_ID, text=chunk, parse_mode=ParseMode.HTML
        )
        if idx < len(chunks) - 1:
            await asyncio.sleep(0.5)
