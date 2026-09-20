"""El cliente de Binance se autorrepara ante -1021 (desfase de reloj) y no re-mide el reloj en cada instanciación."""
import json
from types import SimpleNamespace

import pytest
from binance.client import Client
from binance.exceptions import BinanceAPIException

import execution_engine.binance_client as bc


def _api_error(code: int, msg: str = "boom") -> BinanceAPIException:
    response = SimpleNamespace(status_code=400, text=json.dumps({"code": code, "msg": msg}))
    return BinanceAPIException(response, 400, response.text)


def _client() -> bc.ResilientClient:
    return bc.ResilientClient("k", "s", testnet=True, ping=False)


def test_retries_once_on_clock_skew_with_fresh_timestamp(monkeypatch):
    calls = []

    def fake_request(self, method, uri, signed, force_params=False, **kwargs):
        calls.append(dict(kwargs.get("data", {})))
        if len(calls) == 1:
            kwargs["data"]["timestamp"] = 111       # python-binance escribe el timestamp en kwargs
            raise _api_error(-1021, "Timestamp for this request was 1000ms ahead")
        return {"ok": True}

    monkeypatch.setattr(Client, "_request", fake_request)
    resyncs = []
    monkeypatch.setattr(bc, "_sync_timestamp_offset", lambda c, use_futures=True, force=False: resyncs.append(force))

    result = _client()._request("post", "order", True, data={"symbol": "BTCUSDT"})

    assert result == {"ok": True}
    assert resyncs == [True], "debe re-medir el reloj forzando la consulta"
    assert len(calls) == 2
    assert "timestamp" not in calls[1], "el reintento debe partir de los parámetros originales (timestamp nuevo)"


def test_does_not_retry_other_errors(monkeypatch):
    def fake_request(self, *a, **k):
        raise _api_error(-2022, "ReduceOnly Order is rejected")

    monkeypatch.setattr(Client, "_request", fake_request)
    with pytest.raises(BinanceAPIException) as exc:
        _client()._request("post", "order", True, data={})
    assert exc.value.code == -2022


def test_unsigned_requests_never_retry(monkeypatch):
    calls = []

    def fake_request(self, *a, **k):
        calls.append(1)
        raise _api_error(-1021)

    monkeypatch.setattr(Client, "_request", fake_request)
    with pytest.raises(BinanceAPIException):
        _client()._request("get", "ping", False)
    assert len(calls) == 1


def test_second_failure_is_propagated(monkeypatch):
    def fake_request(self, *a, **k):
        raise _api_error(-1021)

    monkeypatch.setattr(Client, "_request", fake_request)
    monkeypatch.setattr(bc, "_sync_timestamp_offset", lambda *a, **k: None)
    with pytest.raises(BinanceAPIException):
        _client()._request("post", "order", True, data={})


def test_time_offset_is_cached_between_clients(monkeypatch):
    bc._TIME_OFFSET_CACHE.clear()
    fetches = []

    class FakeClient:
        testnet = True
        timestamp_offset = 0

        def futures_time(self):
            fetches.append(1)
            return {"serverTime": 5_000 + 250}

    monkeypatch.setattr(bc.time, "time", lambda: 5.0)
    first, second = FakeClient(), FakeClient()
    bc._sync_timestamp_offset(first)
    bc._sync_timestamp_offset(second)
    assert len(fetches) == 1, "el segundo cliente debe reutilizar el offset medido"
    assert second.timestamp_offset == first.timestamp_offset == 250
    bc._sync_timestamp_offset(FakeClient(), force=True)
    assert len(fetches) == 2, "force=True siempre vuelve a medir"
