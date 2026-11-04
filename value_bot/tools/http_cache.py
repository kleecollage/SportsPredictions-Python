"""
Caché en disco para los backtests: ESPN no cambia un juego terminado, así que repetir un
backtest no tiene por qué volver a pedir cientos de summaries. Solo se guarda lo que el que
llama marca como inmutable (`cacheable`) y nunca una respuesta vacía (fallo de red o 4xx).
La producción no la usa: necesita datos frescos.
"""
import hashlib
import json
import logging
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from value_bot.config import BACKTEST_CACHE_DIR
from value_bot.http_client import http_get

log = logging.getLogger(__name__)

SETTLED_AFTER_DAYS = 2   # un scoreboard de anteayer ya no tiene juegos en curso ni líneas por cerrar


def is_settled_day(day: str, today: date) -> bool:
    """Scoreboard AAAAMMDD que ya no cambia (`today` en hora ET, como los días de ESPN)."""
    return datetime.strptime(day, "%Y%m%d").date() <= today - timedelta(days=SETTLED_AFTER_DAYS)


def _path(url: str, params: dict | None) -> Path:
    key = json.dumps([url, sorted((params or {}).items())], default=str)
    return BACKTEST_CACHE_DIR / f"{hashlib.sha256(key.encode()).hexdigest()}.json"


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("Caché de backtest ilegible (%s), se vuelve a pedir: %s", path.name, exc)
        return None


def _write(path: Path, data) -> None:
    """Escritura atómica: los backtests piden en paralelo y un archivo a medias sería basura."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    except OSError as exc:
        log.warning("No se pudo guardar en la caché de backtest: %s", exc)
        return
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("No se pudo guardar en la caché de backtest: %s", exc)
        Path(tmp).unlink(missing_ok=True)


def cached_get(url: str, params: dict | None = None, *, cacheable: bool, keep: tuple[str, ...] | None = None):
    """
    http_get que lee/escribe la caché solo si la respuesta es inmutable. Con `keep` se guardan
    (y se devuelven, haya caché o no) solo esas claves: un summary ESPN pesa ~600 KB y el
    backtest solo usa `pickcenter` (~5 KB).
    """
    if not cacheable:
        return _only(http_get(url, params=params), keep)
    path = _path(url, params)
    data = _read(path)
    if data is not None:
        return _only(data, keep)
    data = _only(http_get(url, params=params), keep)
    if data:
        _write(path, data)
    return data


def _only(data, keep: tuple[str, ...] | None):
    if keep is None or not isinstance(data, dict):
        return data
    return {k: data[k] for k in keep if k in data}
