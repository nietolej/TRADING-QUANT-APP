"""Regresiones de la auditoría del motor de backtest (ejecución, SL/TP, contabilidad, datos)."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backtest_engine.backtester import Backtester
from data_layer import market_data as md
from data_layer.storage import Base, OHLCV
from strategy_engine.base_strategy import BaseStrategy
from strategy_engine.risk_management import RiskManager


def _df(rows):
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="1D", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1.0)


def _strategy(entry_bars, exit_bars=(), sl=None, tp=None):
    """Estrategia cuyas señales se fijan a mano por índice de vela."""
    rm = {}
    if sl is not None:
        rm["stop_loss"] = {"type": "percentage", "value": sl}
    if tp is not None:
        rm["take_profit"] = {"type": "percentage", "value": tp}
    rm["position_sizing"] = {"method": "compounding", "value": 100.0}
    s = BaseStrategy({"strategy_name": "t", "timeframe": "1d", "risk_management": rm})

    def gen(df):
        df = df.copy()
        df["entry_long"] = [i in entry_bars for i in range(len(df))]
        df["exit_long"] = [i in exit_bars for i in range(len(df))]
        df["entry_short"] = False
        df["exit_short"] = False
        return df

    s.generate_signals = gen
    return s


# ── SL/TP ──────────────────────────────────────────────────────────────────

def test_sl_tp_anchored_to_fill_price_when_given():
    df = _df([[100, 101, 99, 100], [100, 110, 99, 108]])
    rm = RiskManager({"stop_loss": {"type": "percentage", "value": 1.0},
                      "take_profit": {"type": "percentage", "value": 10.0}})
    sl, tp = rm.compute_sl_tp(df, 0, "long", entry_price=100.0)
    assert sl == pytest.approx(99.0)
    assert tp == pytest.approx(110.0)


def test_zero_sl_tp_disables_only_when_requested():
    df = _df([[100, 101, 99, 100]])
    rm = RiskManager({"stop_loss": {"type": "percentage", "value": 0},
                      "take_profit": {"type": "percentage", "value": 0}})
    assert rm.compute_sl_tp(df, 0, "long", strict=True) == (None, None)
    # Comportamiento histórico (motor en vivo): 0 -> 2% SL / 4% TP
    sl, tp = rm.compute_sl_tp(df, 0, "long")
    assert sl == pytest.approx(98.0) and tp == pytest.approx(104.0)


def test_next_open_sl_uses_fill_not_future_close():
    # Señal en la vela 0 -> entrada al open de la vela 1 (100). La vela 1 cierra en 120:
    # antes el SL quedaba en 118.8 (por encima de la compra) y salía "por SL" ganando.
    df = _df([[100, 100, 100, 100], [100, 121, 100, 120], [120, 121, 105, 110], [110, 111, 109, 110]])
    r = Backtester(_strategy({0}, sl=1.0), initial_capital=1000, commission_pct=0, slippage_pct=0).run(df)
    t = r["trades"].iloc[0]
    assert t["entry_price"] == pytest.approx(100.0)
    assert t["exit_reason"] == "EOD_CLOSE"  # low nunca tocó 99


def test_sl_checked_on_entry_bar():
    df = _df([[100, 100, 100, 100], [100, 101, 95, 100], [100, 101, 99.5, 100]])
    r = Backtester(_strategy({0}, sl=1.0), initial_capital=1000, commission_pct=0, slippage_pct=0).run(df)
    t = r["trades"].iloc[0]
    assert t["exit_reason"] == "SL"
    assert t["exit_time"] == df.index[1]
    assert t["exit_price"] == pytest.approx(99.0)


def test_sl_gap_fills_at_open():
    df = _df([[100, 100, 100, 100], [100, 101, 99.5, 100], [90, 91, 88, 89]])
    r = Backtester(_strategy({0}, sl=1.0), initial_capital=1000, commission_pct=0, slippage_pct=0).run(df)
    t = r["trades"].iloc[0]
    assert t["exit_reason"] == "SL"
    assert t["exit_price"] == pytest.approx(90.0)


# ── Contabilidad ──────────────────────────────────────────────────────────

def test_trade_pnl_includes_both_commissions_and_final_equity():
    df = _df([[100, 100, 100, 100], [100, 101, 99, 100], [100, 101, 99, 100.05], [100, 101, 99, 100]])
    r = Backtester(_strategy({0}, exit_bars={2}), initial_capital=1000, commission_pct=0.1, slippage_pct=0).run(df)
    trades = r["trades"]
    # +0.05% bruto no cubre 0.2% de comisiones: el trade es perdedor
    assert trades["pnl"].iloc[0] < 0
    assert r["winning_trades"] == 0
    assert trades["pnl"].sum() == pytest.approx(r["final_equity"] - 1000, abs=1e-9)
    assert r["final_equity"] == pytest.approx(r["equity_curve"]["equity"].iloc[-1])


def test_equity_recorded_after_intrabar_exit():
    # El SL salta en la vela 2 (99) pero la vela cierra en 60: la equity de ese día debe
    # reflejar la salida a 99, no la posición valorada a 60.
    df = _df([[100, 100, 100, 100], [100, 101, 99.5, 100], [100, 100, 60, 60], [60, 61, 59, 60]])
    r = Backtester(_strategy({0}, sl=1.0), initial_capital=1000, commission_pct=0, slippage_pct=0).run(df)
    eq = r["equity_curve"]["equity"]
    assert eq.iloc[2] == pytest.approx(990.0)
    assert r["max_drawdown_pct"] == pytest.approx(-1.0)


def test_eod_close_is_reflected_in_last_equity_point():
    df = _df([[100, 100, 100, 100], [100, 101, 99, 100], [100, 101, 99, 110]])
    r = Backtester(_strategy({0}), initial_capital=1000, commission_pct=0.1, slippage_pct=0).run(df)
    assert r["trades"]["exit_reason"].iloc[-1] == "EOD_CLOSE"
    assert r["equity_curve"]["equity"].iloc[-1] == pytest.approx(r["trades"]["portfolio_value"].iloc[-1])


# ── Datos ─────────────────────────────────────────────────────────────────

def test_drop_unclosed_candles():
    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    df = pd.DataFrame({"timestamp": [now - timedelta(days=2), now - timedelta(days=1, hours=12), now - timedelta(hours=12)]})
    out = md.drop_unclosed_candles(df, "1d", now=now)
    assert len(out) == 2


def test_save_df_to_db_updates_existing_candle():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    mgr = md.MarketDataManager.__new__(md.MarketDataManager)
    mgr.db = db
    ts = datetime(2026, 9, 18, tzinfo=timezone.utc)
    row = dict(timestamp=ts, symbol="BTC/USDT", timeframe="1d", open=1.0, high=2.0, low=0.5, close=1.5, volume=3.0)
    mgr._save_df_to_db(pd.DataFrame([row]))
    mgr._save_df_to_db(pd.DataFrame([{**row, "high": 5.0, "close": 4.0, "volume": 30.0}]))
    stored = db.query(OHLCV).all()
    assert len(stored) == 1
    assert (stored[0].high, stored[0].close, stored[0].volume) == (5.0, 4.0, 30.0)


def test_missing_tp_config_means_no_tp_in_strict_mode():
    df = _df([[100, 101, 99, 100]])
    rm = RiskManager({"stop_loss": {"type": "percentage", "value": 1.0}})
    sl, tp = rm.compute_sl_tp(df, 0, "long", strict=True)
    assert sl == pytest.approx(99.0) and tp is None
