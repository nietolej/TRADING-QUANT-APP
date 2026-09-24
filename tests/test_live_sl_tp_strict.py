"""El motor en vivo trata SL/TP en 0 o sin configurar igual que el backtest: sin esa orden
(antes aplicaba en silencio un 2% / 4%), y avisa al arrancar."""
import pandas as pd
import pytest

import execution_engine.paper_trader as pt
from strategy_engine.risk_management import RiskManager


@pytest.fixture(autouse=True)
def _fresh_bot_registry():
    pt.PaperTrader._ALL_BOTS.clear()
    yield
    pt.PaperTrader._ALL_BOTS.clear()


def _bot(params):
    bot = pt.PaperTrader(
        strategy_yaml_path="config/strategies/ema_long.yaml", initial_balance=100.0, currency="USDT",
        use_testnet=True, custom_timeframe="1m", custom_symbol="BTC/USDT", bot_id="bot_x", name="bot_x",
        custom_parameters=params,
    )
    bot._save_state = lambda: None
    bot.notes = []
    bot._notify = lambda msg="", is_alert=False: bot.notes.append((msg, is_alert))
    return bot


def test_disabled_legs():
    assert RiskManager({}).disabled_legs() == ["SL", "TP"]
    assert RiskManager({"stop_loss": {"type": "percentage", "value": 0},
                        "take_profit": {"type": "percentage", "value": 2}}).disabled_legs() == ["SL"]
    assert RiskManager({"stop_loss": {"type": "atr", "value": 2},
                        "take_profit": {"type": "percentage", "value": 3}}).disabled_legs() == []


def test_live_sl_zero_means_no_stop_and_warns():
    bot = _bot({"FAST": 1, "LOW": 10, "SL": 0, "TP": 1.0})
    bot.klines_df = pd.DataFrame({"open": [100.0], "high": [101.0], "low": [99.0], "close": [100.0]})
    sl, tp = bot.strategy.risk_manager.compute_sl_tp(bot.klines_df, 0, "long", strict=True)
    assert sl is None
    assert tp == pytest.approx(101.0)

    bot._warn_if_sl_tp_disabled()
    assert any("SIN STOP LOSS" in msg and is_alert for msg, is_alert in bot.notes)


def test_live_configured_sl_has_no_warning():
    bot = _bot({"FAST": 1, "LOW": 10, "SL": 0.5, "TP": 1.0})
    bot._warn_if_sl_tp_disabled()
    assert bot.notes == []


def test_open_notification_handles_missing_levels():
    assert pt.PaperTrader._fmt_level(None) == "sin orden"
    assert pt.PaperTrader._fmt_level(1.23456) == "1.2346"
