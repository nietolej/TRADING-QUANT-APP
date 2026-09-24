"""Regresiones del punto 5 de la auditoría: filtro de curva de capital, ranking del
optimizador, walk-forward y análisis de robustez."""
import pandas as pd
import pytest

from backtest_engine.backtester import Backtester
from backtest_engine.equity_curve_backtester import EquityCurveBacktester
from backtest_engine.optimizer import _fold_efficiency, rank_key
from backtest_engine.robustness_analyzer import analyze_robustness
from tests.test_backtest_accuracy import _df, _strategy


# ── Filtro de curva de capital ────────────────────────────────────────────

def _ec_strategy(entry_bars, exit_bars=(), sl=None, start_dd=0.0):
    s = _strategy(entry_bars, exit_bars, sl=sl)
    s.config["equity_curve_management"] = {"dd_enabled": True, "start_trading_at_dd_pct": start_dd,
                                           "stop_trading_at_dd_pct": 1000.0}
    return s


def test_equity_curve_backtester_matches_main_engine_for_virtual_book():
    rows = [[100, 100, 100, 100], [100, 101, 99.5, 100], [100, 100, 60, 60], [60, 61, 59, 60],
            [60, 60, 60, 60], [60, 70, 60, 66], [66, 67, 65, 66]]
    df = _df(rows)
    main = Backtester(_strategy({0, 4}, sl=1.0), initial_capital=1000, commission_pct=0.1, slippage_pct=0.05).run(df)
    ec = EquityCurveBacktester(_ec_strategy({0, 4}, sl=1.0), initial_capital=1000,
                               commission_pct=0.1, slippage_pct=0.05).run(df)
    v = ec["virtual_trades"]
    m = main["trades"]
    assert list(v["exit_reason"]) == list(m["exit_reason"])
    assert list(v["entry_time"]) == list(m["entry_time"])  # entrada al Open siguiente
    assert v["pnl"].sum() == pytest.approx(m["pnl"].sum())
    assert ec["virtual_equity_curve"]["equity"].iloc[-1] == pytest.approx(main["final_equity"])


def test_equity_curve_backtester_trailing_not_triggered_by_same_bar():
    # Trailing 1%: la vela 2 sube a 120 y cae a 110. Antes el stop se movía a 118.8 con el
    # high de esa vela y el low de la MISMA vela lo disparaba.
    df = _df([[100, 100, 100, 100], [100, 101, 99.5, 100], [100, 120, 110, 112], [112, 113, 111.5, 112]])
    s = _ec_strategy({0})
    s.config["risk_management"]["stop_loss"] = {"type": "trailing_percent", "value": 1.0}
    from strategy_engine.risk_management import RiskManager
    s.risk_manager = RiskManager(s.config["risk_management"])
    ec = EquityCurveBacktester(s, initial_capital=1000, commission_pct=0, slippage_pct=0).run(df)
    v = ec["virtual_trades"]
    assert v["exit_time"].iloc[0] == df.index[3]
    # El stop (118.8) quedó por encima del Open de la vela 3 (112): se ejecuta al Open.
    assert v["exit_price"].iloc[0] == pytest.approx(112.0)


def test_equity_curve_backtester_closes_at_end_and_reports_final_equity():
    df = _df([[100, 100, 100, 100], [100, 101, 99, 100], [100, 101, 99, 105]])
    ec = EquityCurveBacktester(_ec_strategy({0}), initial_capital=1000, commission_pct=0, slippage_pct=0).run(df)
    assert ec["virtual_trades"]["exit_reason"].iloc[-1] == "EOD_CLOSE"
    assert "final_equity" in ec


# ── Calentamiento (trade_start) ───────────────────────────────────────────

def test_trade_start_skips_trades_and_metrics_before_it():
    df = _df([[100, 100, 100, 100]] * 3 + [[100, 101, 99, 100]] * 4)
    r = Backtester(_strategy({0, 4}), initial_capital=1000, commission_pct=0, slippage_pct=0,
                   trade_start=df.index[3]).run(df)
    assert r["equity_curve"].index[0] == df.index[3]
    assert (r["trades"]["entry_time"] >= df.index[3]).all()
    assert len(r["trades"]) == 1


# ── Ranking del optimizador ───────────────────────────────────────────────

def test_rank_key_puts_zero_trade_combos_last_for_drawdown():
    no_trades = {"max_drawdown_pct": 0.0, "total_trades": 0}
    trading = {"max_drawdown_pct": -25.0, "total_trades": 40}
    ranked = sorted([no_trades, trading], key=lambda r: rank_key(r, "max_drawdown_pct"), reverse=True)
    assert ranked[0] is trading


# ── Walk-forward ──────────────────────────────────────────────────────────

def test_fold_efficiency_does_not_reward_losing_both_samples():
    assert _fold_efficiency("sharpe_ratio", -0.5, -0.8) == 0.0
    assert _fold_efficiency("sharpe_ratio", 1.0, 0.7) == pytest.approx(0.7)
    assert _fold_efficiency("sharpe_ratio", 0.01, 5.0) == pytest.approx(2.0)  # recortado


def test_fold_efficiency_drawdown_metric():
    assert _fold_efficiency("max_drawdown_pct", -10.0, -20.0) == pytest.approx(0.5)
    assert _fold_efficiency("max_drawdown_pct", -10.0, -5.0) == pytest.approx(2.0)


# ── Robustez ──────────────────────────────────────────────────────────────

def _res(a, b, dd, trades=20, pnl=10.0):
    return {"params": {"A": a, "B": b}, "sharpe_ratio": 1.0, "cagr": 5.0, "max_drawdown_pct": dd,
            "net_pnl": pnl, "profit_factor": 1.5, "profit_factor_reliable": True,
            "percent_profitable": 50.0, "total_trades": trades}


def test_robustness_ignores_zero_trade_combo_as_best_for_drawdown():
    ranges = {"A": {"min": 1, "max": 3, "step": 1}, "B": {"min": 1, "max": 3, "step": 1}}
    results = [_res(a, b, -20.0) for a in (1, 2, 3) for b in (1, 2, 3) if (a, b) != (3, 3)]
    results.append(_res(3, 3, 0.0, trades=0, pnl=0.0))
    rob = analyze_robustness(results, ranges, "max_drawdown_pct")
    assert rob["best_peak"]["params"] != {"A": 3, "B": 3}
    assert rob["best_robust"]["params"] != {"A": 3, "B": 3}


def test_robustness_coverage_penalty_prefers_interior_with_negative_scores():
    ranges = {"A": {"min": 1, "max": 3, "step": 1}, "B": {"min": 1, "max": 3, "step": 1}}
    results = [_res(a, b, -20.0) for a in (1, 2, 3) for b in (1, 2, 3)]
    rob = analyze_robustness(results, ranges, "max_drawdown_pct")
    # Todas iguales: debe ganar el centro (vecindad completa), no una esquina.
    assert rob["best_robust"]["params"] == {"A": 2, "B": 2}
