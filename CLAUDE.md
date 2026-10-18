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
| 🏀 NBA | ⛔ desactivado | nba_api → api-sports → ESPN → BBC → Playwright Flashscore |

Sin deportes activos, `main()` sale sin scrapear ni enviar (y sin resolver resultados). Playwright solo corre si fútbol o NBA están activos.

## Temporadas (calculadas por fecha en cada llamada, `config.py`)
- `nba_season()` / `nba_api_season()`: arranca en octubre. Fuera de abril–junio se usan stats de **temporada regular**, nunca Playoffs viejos; si un equipo tiene < `NBA_MIN_GAMES` (5) se usa la regular anterior.
- `afl_season(info)`: ligas europeas/Liga MX por año de inicio (julio); `calendar_year: True` para MLS, Brasileirão, Libertadores, Sudamericana, Argentina; `season` explícita solo para torneos puntuales.
- `nfl_season()`: arranca en septiembre (enero–febrero = temporada anterior).
- No volver a hardcodear temporadas: en oct-2026 eso hizo que el bot analizara Playoffs 2025-26.

## NFL (`sports/nfl.py`, `analysis/nfl_model.py`, `analysis/nfl_picks.py`)
- Rating por equipo: pts a favor/en contra por juego regresados a la media con `NFL_PRIOR_GAMES`=3. Con < `NFL_MIN_GAMES` (2) en la temporada actual se suman los de la anterior y el rating queda `current=False`.
- Media de liga: los **32 equipos** (endpoint `/teams`), no solo los del día (sesgaba totales a Under).
- Proyección aditiva + HFA 1.5; margen ~ Normal(sd 13.5), total ~ Normal(sd 13.0).
- Probabilidad final = 0.4 modelo + 0.6 mercado sin vig. Techo 85 %, cuota mínima 1.30.
- Líneas enteras: push modelado; confianza = P(ganar | no push); value = `p·(1−push)·cuota + push − 1`. "PK"/"EVEN" = línea 0.
- Un pick entra si confianza ≥ `MIN_CONFIDENCE` (62) o value ≥ 0.04 con p ≥ 0.5 → casi todos los NFL son value bets 💎 de 54–58 %.
- `data_quality` "high" solo con líneas reales, ≥ 4 juegos y ambos ratings de la temporada actual.
- **Dedup entre corridas**: un pick por (`event_id` ESPN, `market` moneyline|spread|total), en cualquier corrida previa (`db.already_sent_for_event`). No usar `pick_type` (cambia con la línea) ni ventanas de tiempo (el runner es intermitente).
- Quirks de ESPN: ver skill global `learned/espn-hidden-api-nfl` (rango de fechas → 400, días en hora ET, cuotas open/close, score string vs objeto).

## Fútbol (lógica Poisson, `analysis/picks.py`)
- Over/Under 2.5 y BTTS con Poisson sobre XG `(gf_propio + ga_rival)/2`; Under solo en ligas defensivas; BTTS no se combina con Under.
- 1X2 con diff de fuerza ≥ 24 (logística), Doble Chance 12–23, Empate con diff ≤ 10 y draw_pct ≥ 26 %.
- Máx 3 picks/partido; value bet = edge ≥ 0.04 con cuota real (The Odds API).
- football-data.org free: solo PL, PD, BL1, SA, FL1, CL, WC, BSA, PPL (MLS/Liga MX → 403); 10 req/min (sleep 7 s). api-football: header `x-apisports-key`, 100 req/día.

## NBA
- Over/Under por proyección con splits home/away; Moneyline con diff ≥ 15; Spread con diff ≥ 18; H2H solo en Playoffs/Finals.

## Parlays (`analysis/parlays.py`)
Piernas con conf ≥ 68 (fútbol) / ≥ 62 (NBA); doble ≥ 38 %, triple ≥ 27 %; ligas distintas. Picks NFL de value (< 68 %) no entran.

## Base de datos (`picks_history.db`, versionada en git)
- `picks`: una fila por pick enviado; `payload` JSON con el resto de campos (`event_id`, `market`, `kickoff`, `lines`, `push_prob`…).
- `results`: la llena `resolver.resolve_pending_picks()` **al inicio de cada `main()` y del reporte semanal**: todos los picks pendientes NBA/NFL cuyo juego ya empezó (hasta 180 días), consultando ESPN por la fecha del juego. Push/empate → `void`. Fútbol aún no se resuelve.
- `results.pick_id` tiene índice UNIQUE (requisito del `ON CONFLICT`; sin él el INSERT fallaba y la tabla quedó vacía meses).
- El ROI del mensaje, el reporte semanal y la calibración (`get_calibration_factors`, solo fútbol/NBA) dependen de `results`.

## Telegram
Parse mode **HTML** (no MarkdownV2). Mensajes > 3900 chars se parten.

## Convenciones
- Commits `tipo: descripción` (feat, fix, chore, test…); sin push salvo que se pida.
- TDD con pytest para lógica nueva; módulos de modelo puros (sin red ni BD) para poder testearlos.
- Archivos < 800 líneas (`analysis/picks.py` ya va en ~750: separar antes de crecerlo).

## Pendientes conocidos
- Resolución de picks de fútbol.
- Mundial 2026 sigue en ligas de respaldo y Flashscore aunque ya terminó (gasta cuota de api-football si entra el fallback).
- `picks_history.db` cambia en cada corrida → aparece modificado en git.
