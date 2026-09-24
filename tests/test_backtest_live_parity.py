"""Paridad backtest ↔ bot en vivo: mismo mercado de datos, mismo cierre de vela, misma regla de
salida, mismo tamaño de posición y mismas restricciones de ejecución."""
import json

import pandas as pd
import pytest

import execution_engine.paper_trader as pt
from backtest_engine.backtester import Backtester
from data_layer import market_data as md
from execution_engine.market_stream import SymbolStream
from strategy_engine.base_strategy import BaseStrategy
from strategy_engine.conditions import apply_state_exit, state_exit_conditions
from tests.test_backtest_accuracy import _df


@pytest.fixture(autouse=True)
def _fresh_bot_registry():
    pt.PaperTrader._ALL_BOTS.clear()
    yield
    pt.PaperTrader._ALL_BOTS.clear()


def _bot(yaml="config/strategies/ema_long.yaml", params=None):
    bot = pt.PaperTrader(
        strategy_yaml_path=yaml, initial_balance=100.0, currency="USDT", use_testnet=True,
        custom_timeframe="1m", custom_symbol="BTC/USDT", bot_id="bot_p", name="bot_p",
        custom_parameters=params if params is not None else {"FAST": 1, "LOW": 10, "SL": 0.5, "TP": 1.0},
    )
    bot._save_state = lambda: None
    bot.notes = []
    bot._notify = lambda msg="", is_alert=False: bot.notes.append((msg, is_alert))
    return bot


# ── 1. Mercado de datos ───────────────────────────────────────────────────

def test_data_symbol_roundtrip():
    assert md.data_symbol("BTC/USDT", "spot") == "BTC/USDT"
    assert md.data_symbol("BTC/USDT", "futures_testnet") == "BTC/USDT@futures_testnet"
    assert md.split_data_symbol("BTC/USDT@futures") == ("BTC/USDT", "futures")
    assert md.split_data_symbol("BTC/USDT") == ("BTC/USDT", "spot")


def test_futures_klines_use_futures_endpoint(monkeypatch):
    calls = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return [[1790121600000, "1", "2", "0.5", "1.5", "10", 0, "0", 0, "0", "0", "0"]]

    def fake_get(url, params=None, timeout=None):
        calls["url"], calls["params"] = url, params
        return Resp()

    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    df = md.MarketDataManager._fetch_futures_klines("BTC/USDT@futures_testnet", "BTC/USDT", "futures_testnet", "1m", 0)
    assert calls["url"] == md.FUTURES_KLINES_URLS["futures_testnet"]
    assert calls["params"]["symbol"] == "BTCUSDT"
    assert df["symbol"].iloc[0] == "BTC/USDT@futures_testnet"
    assert df["close"].iloc[0] == 1.5


# ── 2. Cierre final de la vela ────────────────────────────────────────────

def test_stream_keeps_last_closed_kline():
    s = SymbolStream("BTC/USDT", "1m", True, urls=[])
    base = {"e": "kline", "k": {"t": 60_000, "o": "1", "h": "2", "l": "0.5", "c": "1.9", "v": "1", "x": True}}
    s._handle(json.dumps(base))
    s._handle(json.dumps({"e": "kline", "k": {**base["k"], "t": 120_000, "c": "3", "x": False}}))
    kline, _ = s.latest()
    assert kline["timestamp"] == 120_000
    assert s.latest_closed()["close"] == 1.9


def test_rest_fallback_applies_final_close_of_previous_candle():
    bot = _bot()
    bot.is_running = True
    bot._evaluate_market = lambda: None
    t0 = pd.Timestamp("2026-09-24 00:00", tz="UTC")
    bot.klines_df = pd.DataFrame({"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.2], "volume": [1.0]},
                                 index=[t0])
    ms = int(t0.timestamp() * 1000)

    class Client:
        def futures_orderbook_ticker(self, symbol):
            return {"bidPrice": "1", "askPrice": "1", "bidQty": "1", "askQty": "1"}

        def futures_klines(self, symbol, interval, limit):
            return [[ms, "100", "101", "99", "100.9", "5"], [ms + 60_000, "100.9", "101", "100.8", "100.95", "1"]]

    bot._client = type("C", (), {"client": Client()})()
    bot._poll_rest_once("BTCUSDT")
    assert bot.klines_df.loc[t0, "close"] == 100.9          # cierre final, no el último visto (100.2)
    assert len(bot.klines_df) == 2


# ── 3. Costos por mercado ─────────────────────────────────────────────────

def test_futures_costs_are_futures_fees():
    assert md.MARKET_COSTS["futures"]["commission_pct"] == 0.05
    assert md.MARKET_COSTS["spot"]["commission_pct"] == 0.1


# ── 4. Estrategias no ejecutables en vivo ─────────────────────────────────

def test_custom_class_strategy_cannot_start_live():
    bot = _bot("config/strategies/onchain_flow.yaml", params={})
    bot.start()
    assert bot.status == "ERROR"
    assert not bot.is_running
    assert any("No se puede iniciar" in m for m, _ in bot.notes)


def test_regular_strategy_is_supported_live():
    assert _bot()._live_unsupported_reason() is None


# ── 5. Salida por estado también en el backtest ───────────────────────────

def test_state_exit_matches_live_rule():
    cfg = {"rules": [{"type": "technical_indicator", "operator": "crosses_below"}], "logic": "OR"}
    assert state_exit_conditions(cfg)["rules"][0]["operator"] == "is_below"
    mixed = {"rules": [{"type": "technical_indicator", "operator": "crosses_below"},
                       {"type": "rsi_threshold", "condition": "above"}], "logic": "AND"}
    assert state_exit_conditions(mixed) is None


def test_backtest_exits_on_state_when_exit_indicators_differ():
    # Entrada: precio cruza sobre SMA3. Salida: precio cruza bajo SMA2. Tras entrar, el precio ya
    # está bajo la SMA2 sin un cruce nuevo: el bot sale por estado; el backtest también debe.
    cfg = {
        "strategy_name": "t", "timeframe": "1d", "trade_direction": "Long",
        "entry_conditions": {"logic": "AND", "rules": [{"type": "technical_indicator", "indicator1": "Price",
                             "operator": "crosses_above", "indicator2": "SMA", "period2": 3}]},
        "exit_conditions": {"logic": "OR", "rules": [{"type": "technical_indicator", "indicator1": "Price",
                            "operator": "crosses_below", "indicator2": "SMA", "period2": 2}]},
        "risk_management": {"position_sizing": {"method": "compounding", "value": 100.0}},
    }
    # Cruce sobre SMA3 en la vela 5 -> entrada al Open de la 6. En la 6 el precio (9) ya está bajo
    # la SMA2 (9.5) y también lo estaba en la 5: estado de salida sin cruce. El siguiente cruce
    # bajo la SMA2 recién ocurre en la vela 8.
    closes = [20, 20, 20, 5, 12, 10, 9, 11, 9, 9]
    df = _df([[c, c, c, c] for c in closes])
    s = BaseStrategy(json.loads(json.dumps(cfg)))
    signals = apply_state_exit(s.generate_signals(df), s.config)
    assert signals["exit_long"].sum() >= 1
    with_state = Backtester(BaseStrategy(json.loads(json.dumps(cfg))), initial_capital=1000,
                            commission_pct=0, slippage_pct=0).run(df)["trades"]
    without = Backtester(BaseStrategy(json.loads(json.dumps(cfg))), initial_capital=1000,
                         commission_pct=0, slippage_pct=0, exit_on_state=False).run(df)["trades"]
    assert with_state["entry_time"].iloc[0] == df.index[6] == without["entry_time"].iloc[0]
    assert with_state["exit_time"].iloc[0] == df.index[6]   # sale por estado, como el bot
    assert without["exit_time"].iloc[0] == df.index[8]      # solo con el evento de cruce


# ── 6. Calentamiento ──────────────────────────────────────────────────────

def test_warmup_window_and_warning():
    assert pt.PaperTrader.KLINES_WINDOW >= 1000
    bot = _bot(params={"FAST": 1, "LOW": 400, "SL": 0.5, "TP": 1.0})
    bot._warn_if_warmup_short()
    assert any("periodo 400" in m for m, _ in bot.notes)
    quiet = _bot()
    quiet._warn_if_warmup_short()
    assert quiet.notes == []


# ── 7. Tamaño de posición ─────────────────────────────────────────────────

def test_live_sizing_modes_match_analyzer():
    bot = _bot()
    assert bot._sizing_config(100.0) == {"method": "compounding", "value": 100.0}
    bot.order_types["sizing"] = "fixed_fractional"
    assert bot._sizing_config(100.0) == {"method": "fixed_fractional", "risk_per_trade_pct": 1.0}
    bot.order_types["sizing"] = "fixed_amount"
    assert bot._sizing_config(250.0) == {"method": "fixed_amount", "value": 250.0}


# ── 8. SL por defecto ─────────────────────────────────────────────────────

def test_new_bots_default_to_stop_market():
    assert _bot().order_types["stop_loss"] == "MARKET"
