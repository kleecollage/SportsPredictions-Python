# Value Bot Pro — Contexto del Proyecto

## Rol
Analista Senior de Apuestas Multi-Deporte. Generar pronósticos + parlays diarios de alta probabilidad y enviarlos por Telegram.

## Script principal
`/Users/kleec/Documents/PredictsProyect/predictions_betting_bot.py`
Versión actual: **Value Bot Pro v4.5** (2144 líneas)

## Deportes soportados
| Deporte | Estado | Fuentes |
|---------|--------|---------|
| ⚽ Fútbol | ✅ Activo | football-data.org + api-football + Odds API + **Playwright Flashscore** |
| 🏀 NBA | ✅ Activo | api-sports basketball + ESPN Hidden API + BBC + **Playwright Flashscore** |
| ⚾ MLB | 🔜 Futuro | — |
| 🎾 Tenis | 🔜 Futuro | — |

---

## Arquitectura: Sistema Híbrido Multi-Fuente

### Fútbol — Capa 1: football-data.org (PRINCIPAL)
- Free tier cubre: PL, PD, BL1, SA, FL1, CL, WC, BSA, PPL
- Header: `X-Auth-Token`
- Rate limit: 10 req/min → sleep de 7s entre llamadas
- Token: registrarse en football-data.org/client/register
- **IMPORTANTE:** No intentar MLS, Liga MX, Copa Libertadores con esta API (no están en free tier → 403/404)

### Fútbol — Capa 2: api-football.com / api-sports.io (RESPALDO)
- Base URL: `https://v3.football.api-sports.io`
- Header: `x-apisports-key` (NO usar RapidAPI headers)
- Key actual: `4fb41fb14e8ab9295631b65e1af9a3e9`
- Dashboard: dashboard.api-sports.io
- Plan: FREE — 100 req/día, reset a las 00:00 UTC
- **BUG CONOCIDO RESUELTO:** `afl_current_season()` devuelve 2025 para junio 2026, pero ligas de fallback (Mundial, MLS, Brasileirao) necesitan `season=2026` explícito

### Fútbol — Capa 3: The Odds API (OPCIONAL)
- Base URL: `https://api.the-odds-api.com/v4`
- Plan free: 500 req/mes
- Registrarse en the-odds-api.com
- Cuando está activa: calcula edge real (value bets marcados con 💎)

### NBA — Capa 1: api-sports basketball
- Base URL: `https://v1.basketball.api-sports.io`
- Header: `x-apisports-key` (misma key que api-football)
- League ID: 12 | Season: "2025-2026"

### NBA — Capa 2: ESPN Hidden API
- URL: `site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard`
- Free, sin key, detecta NBA Finals automáticamente
- Detecta Finals: si `datetime.now().month == 6` + texto "Finals" en `series_summary`

### NBA — Capa 3: BeautifulSoup scraping BBC Sport
- Último recurso si ESPN y api-sports fallan

### Capa 4 (v4.3): Playwright Stealth — Flashscore
- `scrape_flashscore_nba()` → NBA games (`/basketball/usa/nba/`)
- `scrape_flashscore_football()` → Mundial 2026, MLS, Brasileirao, Copa Libertadores
- `scrape_espn_nba_pw()` → ESPN con interceptación XHR (respaldo)
- `run_playwright_scrapers()` → paralelo con `asyncio.gather()`, llamado desde `main()`
- Stealth: `navigator.webdriver=undefined`, UA rotation, `window.chrome` spoofing
- Request interception: bloquea `image/font/media/stylesheet` (+30% velocidad)
- Delays: `_human_delay(800ms–2500ms)` entre acciones
- Scroll infinito: `_infinite_scroll()` 3 iteraciones por página
- CSS selectors: `.event__match`, `.event__participant--home/away`, `.event__time`
- Fallback selectores: `[class*='homeParticipant']`, `[class*='awayParticipant']`
- **`PLAYWRIGHT_ENABLED = True`** → cambiar a False para deshabilitar
- Activa Football Capa 4 solo si `len(fixtures) < MIN_FIXTURES_TOP`
- Activa NBA Capa 4 solo si todas las fuentes anteriores devuelven 0 juegos

---

## Ligas TOP Fútbol (Capa 1 + 2)
| Liga | FD code | AFL ID | Temporada |
|------|---------|--------|-----------|
| Premier League | PL | 39 | 2025 |
| LaLiga | PD | 140 | 2025 |
| Bundesliga | BL1 | 78 | 2025 |
| Serie A | SA | 135 | 2025 |
| Ligue 1 | FL1 | 61 | 2025 |
| Champions League | CL | 2 | 2025 |

## Ligas FALLBACK Fútbol (Solo api-football con season explícita)
| Liga | AFL ID | Season |
|------|--------|--------|
| Mundial 2026 | 1 | 2026 |
| MLS | 253 | 2026 |
| Copa Libertadores | 13 | 2026 |
| Copa Sudamericana | 11 | 2026 |
| Brasileirao | 71 | 2026 |
| Liga Argentina | 128 | 2026 |
| Liga MX | 262 | 2025 |

---

## Telegram
- Bot Token: `8753205289:AAFXq9Z88KfTnPVP6NlsCVzcwW1HKuJZ8wI`
- Chat ID: `-1004227148957`
- Parse mode: **HTML** (no usar MarkdownV2 — demasiados problemas de escaping)

---

## Lógica de análisis

### Fútbol (v4.5 — lógica Poisson)
- **Over 2.5**: `_poisson_over(exp_h+exp_a, 2)` >= 0.50 → conf 50-80%; ligas attacking/balanced/technical
- **Under 2.5**: `1 - over25` >= 0.52 → conf 52-78%; ligas defensive (Serie A, Copa Lib, Brasileirao…)
- **BTTS**: `_poisson_btts(exp_h, exp_a)` >= 0.56 → conf 56-78%; no se combina con Under 2.5
- **1X2**: diff fuerza >= 24 pts → prob vía función logística `1/(1+e^(-diff/28))` → conf 55-76%
- **Double Chance**: diff 12–23 pts → conf 58-78%
- **Empate (X)**: diff <= 10 + liga draw_pct >= 26% → conf 62-66%
- **Value bet**: edge = (our_prob × real_odds) - 1 >= 0.04 → marcado con 💎
- **Max picks/partido**: 3 (ordenados por confianza, sin redundancias)
- **Expected Goals**: `exp_h = (gf_h + ga_a) / 2` — promedio entre ataque propio y defensa rival
- **Estilos de liga**: attacking (BL1, MLS, UCL), defensive (SA, CopLib, Brasileirao…), technical (PD), balanced (PL, FL1, MX, WC)

### NBA
- **Over/Under**: projected_total >= 222 → Over | <= 208 → Under
- **Moneyline**: diff de fuerza >= 20 (con +8 puntos de ventaja local)
- **NBA Finals**: siempre aparecen al tope con sección especial 🏆

### Parlays
- Mínimo conf 67%, máximo 3 piernas
- Cross-sport permitido → etiqueta automática "PARLAY MIXTO"

---

## Situación (Junio 2026)
- Ligas europeas en OFFSEASON → 0 fixtures top hasta agosto 2026
- Mundial 2026 inicia ~11 junio 2026 → fallback principal de fútbol
- NBA Finals activas en junio 2026 → sección destacada en mensaje

---

## Ejecución automática en Mac
```bash
# Cron — 9:00 AM diario
crontab -e
# Agregar:
0 9 * * * /usr/bin/python3 /Users/kleec/Documents/PredictsProyect/predictions_betting_bot.py >> /Users/kleec/Documents/PredictsProyect/bot.log 2>&1
```

---

## Bugs conocidos y resueltos
1. **import os faltante** (v4.5): el refactor `.env` de la sesión anterior omitió `import os` → script fallaba en línea 79 con `NameError`. Corregido en v4.5.
2. **Season bug**: `afl_current_season()` devolvía 2025 para ligas que necesitan 2026 → cada liga fallback tiene `season` explícito
2. **MLS/Liga MX en football-data.org** → 403 (no están en free tier) → solo se usan vía api-football
3. **Loop de reintentos en 403/404** → `http_get` no reintenta en errores definitivos (`skip_codes=(403,404)`)
4. **MarkdownV2 escaping** → cambiado a HTML parse mode en todo el bot
5. **RapidAPI headers** → cambiado a `x-apisports-key` directo en api-sports.io
6. **`per_mode_simple` kwarg error** (v4.3) → `try/except TypeError` en `nba_get_league_stats()` para compatibilidad con versiones antiguas de nba_api

---

## Historial de versiones
- v1.0 ⚽: Script básico con picks estáticos (siempre "Over 1.5 Goles" al 68%)
- v2.0 🏀: Reescritura para NBA/EuroLeague con api-sports basketball v1
- v3.0 ⚽: Sistema híbrido fútbol multi-fuente (football-data.org + api-football + Odds API)
- v3.1 ⚽: Fix bugs de temporada + fallback a api-football directo para ligas no en FD free tier
- **v1.0 Pro ⚽🏀**: Multi-deporte con ESPN Hidden API + BBC scraping + parlays cross-sport
- **v4.2 ⚽🏀**: nba_api integrado, 3-5 picks/juego, parlays automáticos 62%+, formato agrupado por partido, sección upcoming
- **v4.3 ⚽🏀**: Playwright stealth Capa 4 (Flashscore NBA+Fútbol), UA rotation, request interception, scroll infinito, fix nba_api per_mode_simple
- **v4.4 ⚽🏀**: BUGFIX NBA Over Points (normalization totales→per-game), sanity check 180-275pts, LEAGUE_AVERAGES fútbol, Double Chance picks, NBA Spread+TeamTotal, múltiples picks/partido fútbol, parlays same-sport
- **v4.5 ⚽🏀**: FIX CRÍTICO import os, probabilidades Poisson reales, Under 2.5 nuevo mercado, picks por estilo de liga, max 3 picks/partido, reasoning con XG, 1X2 logístico, Double Chance 12-23pts (ACTUAL)
