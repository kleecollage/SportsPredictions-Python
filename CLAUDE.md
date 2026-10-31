# Value Bot Pro — Contexto del Proyecto

## Rol
Analista senior de apuestas multi-deporte. Genera picks + parlays diarios y los envía por Telegram.

## Ubicación y ejecución
- Repo: `/Users/kleec/Documents/PronosticProject` — paquete `value_bot/` (los imports son `value_bot.*`; ejecutar siempre con `-m` desde la raíz).
- Entorno: `.venv` (Python 3.14). `pip install -r requirements-dev.txt` + `playwright install chromium`.
- Una corrida: `python -m value_bot.main` · reporte semanal: `python -m value_bot.main --weekly`
- Scheduler: `python -m value_bot.scheduler` (APScheduler, 9:00 y lunes 9:05 **America/New_York**; jobstore en `picks_history.db`). Servicio launchd: `value_bot/scheduler.plist` → `.venv/bin/python`. Logs útiles en `scheduler_error.log` (logging va a stderr).
- Tests: `.venv/bin/python -m pytest -q` (pytest; `tests/conftest.py` pone variables de Telegram falsas).
- **El runner NO corre todos los días** (Mac apagada). Nada puede depender de corridas diarias ni de estado en memoria entre corridas: lo persistente va a `picks_history.db`.

## Secretos
- Viven en `.env` (plantilla `.env.template`): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (obligatorios), `FOOTBALL_DATA_TOKEN`, `API_SPORTS_KEY`, `THE_ODDS_API_KEY` (opcionales).
- **Nunca** escribir valores de secretos en este archivo, en código ni en logs. httpx está en WARNING porque loguea la URL de Telegram con el token.
- Versiones anteriores de este archivo tenían token y key en claro (siguen en el historial de git) → rotarlos si no se ha hecho.

## Deportes (`SPORTS_ENABLED` en `config.py`)
| Deporte | Estado | Fuentes |
|---|---|---|
| 🏈 NFL | ✅ activo | ESPN hidden API (juegos, líneas DraftKings, marcadores) — sin key |
| ⚽ Fútbol | ⛔ desactivado | football-data.org → api-football → The Odds API → Playwright Flashscore |
| 🏀 NBA | ✅ activo (29-oct-2026, sin backtest) | nba_api → api-sports → ESPN → BBC → Playwright Flashscore |

Sin deportes activos, `main()` sale sin scrapear ni enviar (y sin resolver resultados). Playwright de fútbol corre antes de los picks si fútbol está activo; el de NBA (Flashscore) se abre bajo demanda en `get_nba_games` solo si ESPN, api-sports y BBC no dieron juegos. Cada deporte corre aislado (`main._collect_picks` / `_safe_picks`): si uno revienta se loguea y el mensaje sale con los demás.

## Temporadas (calculadas por fecha en cada llamada, `config.py`)
- `nba_season()` / `nba_api_season()`: arranca en octubre. Fuera de abril–junio se usan stats de **temporada regular**, nunca Playoffs viejos; si un equipo tiene < `NBA_MIN_GAMES` (5) se usa la regular anterior.
- `afl_season(info)`: ligas europeas/Liga MX por año de inicio (julio); `calendar_year: True` para MLS, Brasileirão, Libertadores, Sudamericana, Argentina; `season` explícita solo para torneos puntuales.
- `nfl_season()`: arranca en septiembre (enero–febrero = temporada anterior).
- No volver a hardcodear temporadas: en oct-2026 eso hizo que el bot analizara Playoffs 2025-26.

## NFL (`sports/nfl.py`, `analysis/nfl_model.py`, `analysis/nfl_picks.py`)
- **Juegos** (`get_nfl_games`): solo `season.type` 2 (regular) o 3 (postemporada) y ambos equipos entre los 32 → sin pretemporada ni Pro Bowl. Un evento mal formado se loguea y se salta (no tumba el slate). `date` en hora ET; `neutral_site` sale de `competitions[0].neutralSite`.
- Rating por equipo: pts a favor/en contra por juego regresados a la media con `NFL_PRIOR_GAMES`=3. Con < `NFL_MIN_GAMES` (2) en la temporada actual se agregan los de la anterior en `TeamScores.previous` (pesan `NFL_PREV_SEASON_WEIGHT`=0.5) y el rating queda `current=False`. Fallos de red no se cachean.
- Media de liga: los **32 equipos** (endpoint `/teams`, cacheado por corrida en `store.nfl_team_ids`), no solo los del día (sesgaba totales a Under). Calendarios en paralelo (`SCHEDULE_FETCH_WORKERS`=8).
- Proyección aditiva + HFA 1.5 (0 en sede neutral); margen ~ Normal(sd 13.5), total ~ Normal(sd 13.0).
- Rating sin ajuste por fuerza del rival (limitación conocida). `games` = juegos de la temporada actual; los de la anterior van en `previous_games`.
- Probabilidad final = 0.15 modelo + 0.85 mercado sin vig (0.10 / 0.90 con `data_quality` "medium"): peso que minimizó el Brier en el backtest 2026 (antes 0.4 → EV prometido +7.8 %, ROI real −12 % en 58 picks). En cada mercado se elige el lado de **mayor EV**, no el más probable (puede salir el underdog). Techo 85 %, cuota entre 1.30 y 3.50 (`NFL_MAX_ODDS`: en underdogs largos el error del modelo se multiplica por la cuota). **Sin cuota real del mercado no hay pick** (ni moneyline de modelo puro ni mercado 50/50 inventado).
- Líneas enteras: push modelado; confianza = P(ganar | no push); value = `p·(1−push)·cuota + push − 1`. "PK"/"EVEN"/"EV" = línea 0. Spreads con margen discreto (`_margin_pmf`): la masa de ±3, ±7, ±10, ±14 sube y la de 0 baja (`NFL_KEY_MARGIN_WEIGHTS`), tomándola de los vecinos ±1/±2 para no mover la media; así también las líneas .5 que cruzan un número clave (−2.5 vs −3.5) lo reflejan.
- **Totales apagados** (`NFL_TOTALS_ENABLED`): el lado del modelo acertó 37 % y su Brier fue peor que el mercado. Se siguen calculando en el backtest.
- **Sin picks en semanas 1–2** de temporada regular (`is_pickable_week`, `NFL_FIRST_PICK_WEEK`=3; postemporada no se filtra): ratings con datos del año anterior, 3-8 en 2026. `week`/`season_type` salen del evento ESPN; sin semana no se bloquea.
- Con estos pesos casi no salen picks NFL (2 en 60 juegos elegibles de 2026): el modelo no le gana al cierre. No bajar umbrales sin backtest.
- Un pick entra si 0 ≤ value ≤ 0.15 (`NFL_MIN_PICK_VALUE` / `NFL_MAX_PICK_VALUE`: un edge mayor contra el mercado NFL suele ser info que el modelo no tiene, p. ej. QB fuera) **y** (confianza ≥ `MIN_CONFIDENCE` (62) o value ≥ 0.04) → casi todos los NFL son value bets 💎 de 54–58 % (pueden salir underdogs de ~35–45 %).
- Una sola apuesta de lado (moneyline o spread, de cualquier equipo) por partido: el mismo equipo es la misma apuesta y equipos opuestos son una cobertura. Queda el de más value (`_drop_correlated`) → máx. 2 picks por partido (lado + total; hoy 1, con los totales apagados).
- `data_quality` "high" solo con ≥ 4 juegos y ambos ratings de la temporada actual (la cuota real ya es obligatoria).
- **Dedup entre corridas**: un pick por (`event_id` ESPN, `market` moneyline|spread|total) en cualquier corrida previa (`db.already_sent_for_event`), y si ya salió un moneyline o spread de ese partido no sale otro de ninguno de los dos (`nfl_picks._already_sent`). No usar ventanas de tiempo (el runner es intermitente).
- **Backtest**: `python -m value_bot.tools.nfl_backtest --days 45 [--games N] [--json f]` — líneas de cierre DraftKings de `summary.pickcenter` (el scoreboard las borra al terminar), ratings solo con juegos anteriores al kickoff, mismas reglas que producción; reporta picks por mercado/semana/value y Brier modelo vs mercado vs mezcla con peso óptimo. Solo lee ESPN. Conservador: el bot usa la línea de las 9:00 ET, no el cierre.
- Quirks de ESPN: ver skill global `learned/espn-hidden-api-nfl` (rango de fechas → 400, días en hora ET, cuotas open/close, score string vs objeto). Además: campos anidados pueden venir `null` (usar `_obj`), y de open/close se toma el snapshot que traiga línea y cuota juntas.

## Fútbol (lógica Poisson, `analysis/picks.py`)
- Over/Under 2.5 y BTTS con Poisson sobre XG `(gf_propio + ga_rival)/2`; Under solo en ligas defensivas; BTTS no se combina con Under.
- 1X2 con diff de fuerza ≥ 24 (logística), Doble Chance 12–23, Empate con diff ≤ 10 y draw_pct ≥ 26 %.
- Máx 3 picks/partido; value bet = edge ≥ 0.04 con cuota real (The Odds API).
- football-data.org free: solo PL, PD, BL1, SA, FL1, CL, WC, BSA, PPL (MLS/Liga MX → 403); 10 req/min (sleep 7 s). api-football: header `x-apisports-key`, 100 req/día.

## NBA (`sports/nba.py`, `analyze_nba_game` en `analysis/picks.py`)
- Over/Under por proyección con splits home/away; Moneyline con diff ≥ 15; Spread con diff ≥ 18; H2H solo en Playoffs/Finals.
- **Equipos por ID de nba_api**, solo con coincidencia exacta (nombre completo, apodo o `_TEAM_ALIASES`, p. ej. "LA Clippers"). Nunca por parecido: el fuzzy viejo daba a 11/30 equipos stats de otro ("la" ⊂ "Atlanta", "nets" ⊂ "Hornets"). LeagueDash se indexa por `TEAM_ID`.
- nba_api 1.11: el parámetro es `per_mode_detailed="PerGame"` (`per_mode_simple` no existe y devolvía totales). Filas con < `NBA_MIN_GAMES` o ppg fuera de 85–140 se descartan.
- Sin LeagueDash de un equipo (ni actual ni anterior) → sin stats → **sin picks** de ese juego (ya no se inventan `opp_ppg` 115 ni picks genéricos "Over 215.5").
- `NBA_API_TIMEOUT` = 10 s por request; el primer fallo de red pone `store.nba_api_down` y se deja de llamar a nba_api en esa corrida. Una respuesta con esquema inesperado (columna faltante) se loguea y se trata como sin datos (`_PARSE_ERRORS`), nunca sube.
- Splits home/away solo si son de la **misma temporada** que las stats base del equipo (`season` en ambos); al inicio de temporada los splits caen a la anterior y no deben pisar los promedios actuales. `nba_get_home_away_splits(..., season=)` solo pide esa temporada.
- Fuentes de juegos en funciones separadas (`_api_sports_games`, `_with_espn`, `_bbc_games`, `_flashscore_games`). Flashscore (fuente 4) solo acepta items con hora `HH:MM`: su página mezcla resultados y próximos días, y desde oct-2026 `.event__time` viene vacío (34/34 items sin hora) → hoy la fuente 4 no aporta juegos hasta reparar el selector.
- Unión de fuentes: un juego de ESPN se une a uno de api-sports solo si coinciden local **y** visitante (`_same_matchup`). `is_finals` solo sale del headline ESPN "NBA Finals" (`espn._is_nba_finals`), nunca por el mes.
- Dedup: mismo pick (local, visitante, tipo) para la misma fecha de juego (`payload.date`) en cualquier corrida (`db.already_sent_for_game`).
- Picks NBA sin cuota real (`est_odds` estimada, `value` None) y modelo sin backtest: tratar su ROI con cautela.

## Parlays (`analysis/parlays.py`)
Piernas con conf ≥ 68 (fútbol) / ≥ 62 (NBA); doble ≥ 38 %, triple ≥ 27 %; ligas distintas. Picks NFL de value (< 68 %) no entran.

## Base de datos (`picks_history.db`, versionada en git)
- `picks`: una fila por pick enviado; `payload` JSON con el resto de campos (`event_id`, `market`, `side`, `kickoff`, `lines`, `push_prob`, `neutral_site`…).
- `results`: la llena `resolver.resolve_pending_picks()` **al inicio de cada `main()` y del reporte semanal**: todos los picks pendientes NBA/NFL cuyo juego ya empezó (hasta 180 días), consultando ESPN por la fecha del juego. Push/empate → `void`. Fútbol aún no se resuelve.
- `results.pick_id` tiene índice UNIQUE (requisito del `ON CONFLICT`; sin él el INSERT fallaba y la tabla quedó vacía meses).
- El ROI del mensaje, el reporte semanal y la calibración (`get_calibration_factors`, solo fútbol/NBA) dependen de `results`.
- El rendimiento del mensaje diario y todo el reporte semanal (ROI, calibración, mejores/peores picks; `main._current_scope`) solo cuentan **deportes activos** y picks con `run_date` ≥ `MODEL_SINCE[deporte]` (`config.py`): al recalibrar un modelo, actualizar esa fecha. El bloque sale desde 1 pick resuelto, con aviso de muestra pequeña por debajo de `PERF_MIN_SAMPLE` (5).

## Telegram
Parse mode **HTML** (no MarkdownV2). Mensajes > 3900 chars se parten. Kickoffs en hora ET (`fmt_ko`); conteos con `plural()`. El razonamiento NFL muestra `Modelo X% · mercado Y% → Z%` (Z = mezcla = confianza).

## Convenciones
- Commits `tipo: descripción` (feat, fix, chore, test…); sin push salvo que se pida.
- TDD con pytest para lógica nueva; módulos de modelo puros (sin red ni BD) para poder testearlos.
- Archivos < 800 líneas (`analysis/picks.py` ya va en ~750: separar antes de crecerlo).

## Pendientes conocidos
- Resolución de picks de fútbol.
- Mundial 2026 sigue en ligas de respaldo y Flashscore aunque ya terminó (gasta cuota de api-football si entra el fallback).
- `picks_history.db` cambia en cada corrida → aparece modificado en git.
