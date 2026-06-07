# SportsPredictions-Python

multi-sport betting prediction bot built with Claude Cowork. It automatically generates high-quality value bets and parlays for Football (MLS, European leagues, etc.) and NBA using a hybrid system: APIs + advanced web scraping with Playwright.

"""
🏆 Value Bot Pro v4.6 — Multi-Deporte + Playwright Stealth
══════════════════════════════════════════════════════════════════
CAMBIOS v4.3 vs v4.2:
  ✅ Playwright stealth scraping  → Capa 4 (Flashscore NBA + Fútbol)
  ✅ Anti-detección avanzada       → User-agent rotation, navigator.webdriver=undefined
  ✅ Delays human-like             → random 0.8-2.5s entre acciones
  ✅ Interceptación de requests    → bloqueo de imagen/font/media (velocidad)
  ✅ Scroll infinito               → carga fixtures adicionales
  ✅ Fix nba_api per_mode_simple   → try/except TypeError (compatibility)

CAMBIOS v4.6 vs v4.4:
  ✅ FIX CRITICO import os + dotenv block (faltaba → NameError en os.getenv)
  ✅ Probabilidades Poisson reales  → Over/Under y BTTS matemáticamente correctos
  ✅ Under 2.5 Goles               → nuevo mercado para ligas defensivas
  ✅ Picks por estilo de liga       → attacking/defensive/technical/balanced
  ✅ Máximo 3 picks/partido         → calidad > cantidad, sin redundancias
  ✅ Razonamientos con XG reales    → "XG local 1.42 + XG visitante 1.18 = 2.60"
  ✅ 1X2 vía función logística      → más suave que lineal, range 55-76%
  ✅ Double Chance umbral 12-23pts  → más preciso que el anterior 10-22
  ✅ LEAGUE_AVERAGES ampliado       → under25_pct, draw_pct, style nuevos campos
  ✅ BTTS no contradice Under 2.5   → picks coherentes entre sí

CAMBIOS v4.4 vs v4.3:
  ✅ BUGFIX NBA Over Points       → normalizar totales→per-game (era "Over 3726")
  ✅ Sanity check totales NBA     → rango realista 180–275 pts
  ✅ LEAGUE_AVERAGES fútbol       → picks con contexto de liga, no genéricos
  ✅ Double Chance picks          → fútbol: 1X / X2 con ventaja moderada
  ✅ NBA Spread pick              → Hándicap Asiático estimado
  ✅ Múltiples picks/partido      → fútbol = same as NBA (todos los picks)
  ✅ Parlays same-sport           → fútbol primero, luego NBA, mixto last resort

CAMBIOS v4.2 vs v1.0:
  ✅ nba_api integrado  → stats reales Playoffs/Finals (PPG, forma, margin)
  ✅ 3-5 picks por partido  (Over/Under + Moneyline + 1ª Mitad + Serie)
  ✅ Parlays automáticos  → pool usa conf >= 62% (antes: 67%)
  ✅ Picks agrupados por partido  en el mensaje
  ✅ Stats block por equipo  (PPG, WR%, forma últimos 5 en emojis)
  ✅ Sección "Próximos Partidos"  aunque no haya picks hoy
  ✅ Análisis profundo Finals  (mínimo 3-4 picks por juego)
  ✅ DAYS_AHEAD: 2 → 3  |  MAX_PICKS_SPORT: 4 → 6

FUENTES NBA (en orden de prioridad):

  1. nba_api  (LeagueDashTeamStats + TeamGameLog Playoffs/Regular) ← NUEVO
  2. api-sports basketball  (fallback stats)
  3. ESPN Hidden API  (games + Finals context — free, sin key)
  4. BeautifulSoup BBC  (último recurso)

FUENTES FÚTBOL:

  1. football-data.org         (fixtures + standings)
  2. api-football / api-sports  (fallback + stats individuales)
  3. The Odds API               (cuotas reales — opcional)
  4. Fallback ligas activas     (Mundial 2026, MLS, Brasileirao…)
  5. Playwright Flashscore      (Capa 4 ← NUEVO v4.3)

"""
