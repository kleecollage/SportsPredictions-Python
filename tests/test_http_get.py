"""http_get: una request por intento; solo 429/5xx se reintentan (cada llamada gasta cuota)."""
import pytest
import requests

from value_bot import http_client


class FakeResponse:
    def __init__(self, status: int, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.fixture
def fake_get(monkeypatch):
    """Cola de respuestas; cuenta cuántas requests salen a la red."""
    calls = []

    def install(*responses):
        queue = list(responses)

        def get(url, headers=None, params=None, timeout=None):
            calls.append(url)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr(http_client.requests, "get", get)
        if http_client.TENACITY_OK:
            monkeypatch.setattr(http_client._fetch_json.retry, "sleep", lambda _: None)
        return calls

    return install


def test_ok_makes_a_single_request(fake_get):
    calls = fake_get(FakeResponse(200, {"events": []}))

    assert http_client.http_get("https://x.test/a") == {"events": []}
    assert len(calls) == 1


def test_any_2xx_is_read_as_json(fake_get):
    calls = fake_get(FakeResponse(202, {"accepted": 1}))

    assert http_client.http_get("https://x.test/a") == {"accepted": 1}
    assert len(calls) == 1


def test_skip_code_returns_empty_without_retry(fake_get):
    calls = fake_get(FakeResponse(404))

    assert http_client.http_get("https://x.test/a") == {}
    assert len(calls) == 1


@pytest.mark.skipif(not http_client.TENACITY_OK, reason="sin tenacity no hay reintentos")
def test_rate_limit_then_server_error_then_ok_uses_three_requests(fake_get):
    calls = fake_get(FakeResponse(429), FakeResponse(503), FakeResponse(200, {"ok": 1}))

    assert http_client.http_get("https://x.test/a") == {"ok": 1}
    assert len(calls) == 3


@pytest.mark.skipif(not http_client.TENACITY_OK, reason="sin tenacity no hay reintentos")
def test_persistent_server_error_gives_up_after_three_requests(fake_get):
    calls = fake_get(*(FakeResponse(500) for _ in range(5)))

    assert http_client.http_get("https://x.test/a") == {}
    assert len(calls) == 3


def test_other_client_error_is_not_retried(fake_get):
    calls = fake_get(FakeResponse(400), FakeResponse(200, {"never": 1}))

    assert http_client.http_get("https://x.test/a") == {}
    assert len(calls) == 1


def test_network_error_returns_empty_without_retry(fake_get):
    calls = fake_get(requests.exceptions.ConnectionError("down"), FakeResponse(200, {"never": 1}))

    assert http_client.http_get("https://x.test/a") == {}
    assert len(calls) == 1


def test_invalid_json_returns_empty_and_logs_it(fake_get, caplog):
    calls = fake_get(FakeResponse(200, requests.exceptions.JSONDecodeError("not json", "", 0)))

    assert http_client.http_get("https://x.test/a") == {}
    assert len(calls) == 1
    assert "JSON inválido" in caplog.text


def test_validate_env_fails_without_telegram(monkeypatch):
    from value_bot.config import validate_env

    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    with pytest.raises(EnvironmentError, match="TELEGRAM_CHAT_ID"):
        validate_env()
