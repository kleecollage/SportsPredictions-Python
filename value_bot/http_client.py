"""GET JSON con reintentos solo en 429/5xx: cada request gasta cuota (api-football, Odds)."""
import logging
import time

import requests

from value_bot.config import FD_BASE, FD_HEADS, SPORTS_HEADS

try:
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    TENACITY_OK = True
except ImportError:
    TENACITY_OK = False

log = logging.getLogger(__name__)


class _RetryableStatus(requests.exceptions.RequestException):
    """429 o 5xx: lo único que vale la pena reintentar (cada request gasta cuota)."""


_EMPTY = object()   # código definitivo: {} sin reintentar


def _fetch_json(url: str, headers: dict, params: dict, timeout: float, skip_codes: tuple[int, ...]):
    """Una sola request por intento; tenacity reintenta solo _RetryableStatus."""
    r = requests.get(url, headers=headers, params=params, timeout=timeout)
    if r.status_code in skip_codes:
        log.warning("HTTP %d (definitivo): %s", r.status_code, url[:80])
        return _EMPTY
    if r.status_code == 429 or r.status_code >= 500:
        raise _RetryableStatus(f"HTTP {r.status_code}: {url[:60]}")
    if not 200 <= r.status_code < 300:
        log.warning("HTTP %d sin reintento: %s", r.status_code, url[:80])
        return _EMPTY
    return r.json()


if TENACITY_OK:
    _fetch_json = retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(_RetryableStatus),
        reraise=True,
    )(_fetch_json)


def http_get(url: str, headers: dict | None = None, params: dict | None = None,
             skip_codes: tuple[int, ...] = (403, 404), timeout: float = 12):
    """JSON de la URL o {} si falla; errores de red no se reintentan (como antes)."""
    try:
        data = _fetch_json(url, headers or {}, params or {}, timeout, skip_codes)
        return {} if data is _EMPTY else data
    except ValueError as exc:   # antes que RequestException: el JSONDecodeError de requests es ambos
        log.warning("HTTP JSON inválido [%s]: %s", url[:60], exc)
        return {}
    except requests.exceptions.RequestException as exc:
        log.warning("HTTP definitivamente falló [%s]: %s", url[:60], exc)
        return {}
    except Exception as exc:
        log.warning("HTTP error inesperado [%s]: %s", url[:60], exc)
        return {}


def api_sports_get(base, endpoint, params):
    return http_get(f"{base}/{endpoint}", headers=SPORTS_HEADS, params=params)


def fd_get(path, params=None):
    result = http_get(f"{FD_BASE}{path}", headers=FD_HEADS, params=params)
    if result:  # solo esperar si hubo respuesta real (respetar rate limit)
        time.sleep(7)
    return result
