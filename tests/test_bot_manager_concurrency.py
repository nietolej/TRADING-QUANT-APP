"""
Regresión del interbloqueo (ABBA) entre el lock del BotManager y el lock de cada bot.

Antes: un bot, con SU lock tomado, guardaba el estado (pedía el lock del manager), mientras el
manager, con SU lock tomado, serializaba a todos los bots (pedía el lock de cada bot). Dos bots
haciéndolo a la vez se bloqueaban mutuamente y congelaban el servidor. Ahora el guardado lo hace
un hilo aparte sin ningún otro lock en la mano.
"""
import json
import os
import threading
import time

import pytest

import execution_engine.bot_manager as bm


class FakeBot:
    """Imita lo relevante de PaperTrader: un lock propio y un to_dict() que lo toma."""

    def __init__(self, bot_id):
        self.bot_id = bot_id
        self.name = bot_id
        self.is_running = True
        self._lock = threading.RLock()
        self.unexecuted_orders = []

    def to_dict(self):
        with self._lock:
            return {"bot_id": self.bot_id, "name": self.name, "is_running": self.is_running}


def _make_manager(tmp_path):
    return bm.BotManager(persistence_file=str(tmp_path / "bots_state.json"), auto_start_running_bots=False)


def _hammer(manager, bots, seconds):
    """Cada bot mantiene su lock y notifica cambio de estado en bucle (como _on_new_kline);
    en paralelo, el hilo principal guarda y lista bots como hacen la API y la interfaz."""
    stop = time.monotonic() + seconds
    errors = []

    def bot_thread(bot):
        try:
            while time.monotonic() < stop:
                with bot._lock:
                    manager._on_bot_state_changed(bot)
                    time.sleep(0.001)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=bot_thread, args=(b,), daemon=True) for b in bots]
    for t in threads:
        t.start()
    while time.monotonic() < stop:
        manager.save_state_to_disk()
        manager.get_all_bots()
        time.sleep(0.002)
    for t in threads:
        t.join(timeout=5)
    return threads, errors


def test_no_deadlock_with_two_bots_saving_concurrently(tmp_path):
    manager = _make_manager(tmp_path)
    bots = [FakeBot("bot_a"), FakeBot("bot_b")]
    for b in bots:
        manager._bots[b.bot_id] = b

    result = {}

    def run():
        result["threads"], result["errors"] = _hammer(manager, bots, seconds=2.0)

    runner = threading.Thread(target=run, daemon=True)
    runner.start()
    runner.join(timeout=15)
    assert not runner.is_alive(), "INTERBLOQUEO: el guardado concurrente de dos bots no terminó"
    assert not result["errors"]
    assert all(not t.is_alive() for t in result["threads"])


def test_state_file_is_valid_json_after_concurrent_saves(tmp_path):
    manager = _make_manager(tmp_path)
    for name in ("bot_a", "bot_b", "bot_c"):
        manager._bots[name] = FakeBot(name)
    threads = [threading.Thread(target=manager.save_state_to_disk) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    with open(tmp_path / "bots_state.json", encoding="utf-8") as f:
        data = json.load(f)
    assert set(data) == {"bot_a", "bot_b", "bot_c"}
    assert not os.path.exists(str(tmp_path / "bots_state.json") + ".tmp")


def test_state_change_notification_does_not_block(tmp_path):
    """El callback que invoca el bot con su lock tomado solo debe marcar 'sucio', sin trabajo pesado."""
    manager = _make_manager(tmp_path)
    bot = FakeBot("bot_a")
    manager._bots[bot.bot_id] = bot
    t0 = time.perf_counter()
    with bot._lock:
        for _ in range(1000):
            manager._on_bot_state_changed(bot)
    assert time.perf_counter() - t0 < 0.5


def test_writer_thread_persists_dirty_state(tmp_path):
    manager = _make_manager(tmp_path)
    bot = FakeBot("bot_a")
    manager._bots[bot.bot_id] = bot
    manager._on_bot_state_changed(bot)
    deadline = time.monotonic() + 5
    path = tmp_path / "bots_state.json"
    while time.monotonic() < deadline and not path.exists():
        time.sleep(0.05)
    assert path.exists(), "el hilo de persistencia no escribió el estado"
    assert json.loads(path.read_text(encoding="utf-8"))["bot_a"]["name"] == "bot_a"


def test_manager_outside_daemon_does_not_resume_bots(tmp_path, monkeypatch):
    """Fuera del proceso daemon nunca se reanudan bots (evita bots duplicados y reinicios por hot-reload)."""
    monkeypatch.delenv("TQA_PROCESS_ROLE", raising=False)
    started = []

    class Spy(bm.PaperTrader):
        def start(self, *a, **k):
            started.append(self.name)

    monkeypatch.setattr(bm, "PaperTrader", Spy)
    state = {
        "bot_x": {
            "strategy_yaml_path": os.path.join("config", "strategies", "ema_long.yaml"),
            "initial_balance": 100.0, "currency": "USDT", "custom_parameters": {"FAST": 1, "LOW": 10, "SL": 1, "TP": 2},
            "use_testnet": True, "timeframe": "1m", "symbol": "BTC/USDT", "name": "X", "is_running": True,
        }
    }
    (tmp_path / "bots_state.json").write_text(json.dumps(state), encoding="utf-8")
    bm.BotManager(persistence_file=str(tmp_path / "bots_state.json"))
    assert started == []
