"""
Los bots viven SOLO en el daemon: sin él la web no ejecuta nada por su cuenta y nunca se bloquea
esperando una conexión que ya sabe que fallará.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from execution_engine import daemon_client as dc

DEAD_URL = "http://127.0.0.1:59998"


@pytest.fixture
def offline_client():
    client = dc.DaemonClient(base_url=DEAD_URL)
    client._failures_before_offline = 1
    client.is_daemon_online()      # primera comprobación (única síncrona)
    return client


def test_offline_status_is_cached_and_calls_are_instant(offline_client):
    assert offline_client.is_daemon_online() is False
    t0 = time.perf_counter()
    for _ in range(500):
        offline_client.is_daemon_online()
        offline_client.get_all_bots()
        offline_client.get_portfolio_summary()
    assert time.perf_counter() - t0 < 0.5, "con el daemon apagado ninguna llamada debe esperar red"


def test_reads_are_empty_when_offline(offline_client):
    assert offline_client.get_all_bots() == []
    assert offline_client.get_bot("bot_x") is None
    assert offline_client.get_unexecuted_orders() == []
    summary = offline_client.get_portfolio_summary()
    assert summary["total_bots"] == 0 and summary["win_rate"] == 0.0


def test_actions_fail_clearly_and_never_create_local_bots(offline_client):
    with pytest.raises(dc.DaemonOfflineError):
        offline_client.create_bot("config/strategies/ema_long.yaml")
    assert offline_client.start_bot("bot_x") is False
    assert offline_client.stop_bot("bot_x") is False
    assert offline_client.start_all() is False
    assert offline_client.delete_bot("bot_x") is False
    assert offline_client.update_bot_config("bot_x", name="n") is False
    # El cliente ya no tiene ningún BotManager local al que recurrir.
    assert not hasattr(dc, "bot_manager")


def test_emergency_kill_offline_only_locks_real_trading(offline_client, monkeypatch):
    calls = []
    import execution_engine.security_manager as sm
    import notifications.telegram_bot as tg
    monkeypatch.setattr(sm, "set_real_trading_enabled", lambda v: calls.append(("lock", v)))

    class FakeNotifier:
        def send_alert(self, *a, **k):
            calls.append(("alert",))

    monkeypatch.setattr(tg, "TelegramNotifier", FakeNotifier)
    result = offline_client.emergency_kill()
    assert result["status"] == "emergency_killed"
    assert calls == [("lock", False), ("alert",)]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/health":
            body = {"status": "online"}
        elif self.path == "/api/bots":
            body = [{"bot_id": "bot_1", "name": "Bot 1", "symbol": "BTC/USDT", "is_running": True, "status": "RUNNING"}]
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_online_lists_bots_and_detects_shutdown_via_monitor_thread():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = dc.DaemonClient(base_url=f"http://127.0.0.1:{server.server_port}")
    client.HEALTH_POLL_S = 0.2
    client._failures_before_offline = 1
    try:
        assert client.is_daemon_online() is True
        bots = client.get_all_bots()
        assert [b.bot_id for b in bots] == ["bot_1"] and bots[0].is_running is True
        server.shutdown()
        server.server_close()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and client.is_daemon_online():
            time.sleep(0.05)      # is_daemon_online() no hace red: solo lee lo que dejó el hilo de vigilancia
        assert client.is_daemon_online() is False, "el hilo de vigilancia debe detectar la caída del daemon"
        assert client.get_all_bots() == []
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
