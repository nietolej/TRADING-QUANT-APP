import pandas as pd
import numpy as np
from datetime import datetime
import uuid
import json
from strategy_engine.base_strategy import BaseStrategy
from .metrics import calculate_metrics, calculate_equity_curve_metrics


class _SpotBook:
    """
    Cuenta spot de una sola posición con la MISMA semántica de ejecución que
    Backtester.run_iterative (modo spot_cash): SL/TP anclados al precio de ejecución,
    SL prioritario sobre TP, relleno al Open si la vela abre más allá del nivel, trailing
    actualizado solo después de comprobar la salida y PnL neto de ambas comisiones.
    Antes este motor tenía su propia copia de esa lógica con varias diferencias (entrada al
    close de la señal, trailing movido con el high de la misma vela que luego disparaba el
    stop, PnL sin la comisión de entrada y sin cierre de la posición al final del periodo),
    así que un backtest con el filtro activo no era comparable con uno sin él.
    """

    def __init__(self, capital, commission_pct, slippage_pct, risk_manager):
        self.capital = capital
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct
        self.rm = risk_manager
        self.position = 0.0
        self.entry_price = 0.0
        self.sl_price = None
        self.tp_price = None
        self.trade = {}
        self.trades = []

    def open(self, side, entry_p, sl, tp, ts) -> bool:
        qty = self.rm.compute_position_size(self.capital, entry_p, sl)
        qty = min(qty, self.capital / (entry_p * (1 + self.commission_pct)))
        notional = qty * entry_p
        commission = notional * self.commission_pct
        if qty <= 1e-6 or self.capital < (notional + commission) - 1e-6:
            return False
        if side == 'long':
            self.capital -= notional + commission
        else:
            self.capital += notional - commission
        self.position = qty if side == 'long' else -qty
        self.entry_price = entry_p
        self.sl_price = sl
        self.tp_price = tp
        self.trade = {'entry_time': ts, 'side': side, 'quantity': qty, 'entry_commission': commission}
        return True

    def exit_signal(self, row, open_p):
        """(motivo, precio bruto de salida) si esta vela cierra la posición, o (None, None)."""
        close_p = float(row['close'])
        if self.position > 0:
            if self.sl_price is not None and row['low'] <= self.sl_price:
                return "SL", min(self.sl_price, open_p)
            if self.tp_price is not None and self.tp_price > 0 and row['high'] >= self.tp_price:
                return "TP", max(self.tp_price, open_p)
            if row.get('exit_long', False):
                return "Signal", close_p
        elif self.position < 0:
            if self.sl_price is not None and self.sl_price > 0 and row['high'] >= self.sl_price:
                return "SL", max(self.sl_price, open_p)
            if self.tp_price is not None and self.tp_price > 0 and row['low'] <= self.tp_price:
                return "TP", min(self.tp_price, open_p)
            if row.get('exit_short', False):
                return "Signal", close_p
        return None, None

    def close(self, exit_raw, reason, ts) -> dict:
        qty = abs(self.position)
        side = 'long' if self.position > 0 else 'short'
        if side == 'long':
            exit_p = exit_raw * (1 - self.slippage_pct)
            gross = qty * (exit_p - self.entry_price)
            self.capital += qty * exit_p - qty * exit_p * self.commission_pct
        else:
            exit_p = exit_raw * (1 + self.slippage_pct)
            gross = qty * (self.entry_price - exit_p)
            self.capital -= qty * exit_p + qty * exit_p * self.commission_pct
        pnl = gross - qty * exit_p * self.commission_pct - self.trade.get('entry_commission', 0.0)
        tr = {
            'entry_time': self.trade.get('entry_time', ts),
            'exit_time': ts,
            'side': side,
            'entry_price': self.entry_price,
            'exit_price': exit_p,
            'quantity': qty,
            'pnl': pnl,
            'exit_reason': reason,
            'portfolio_value': self.capital
        }
        self.trades.append(tr)
        self.position = 0.0
        return tr

    def trail(self, row, close_p):
        self.sl_price = self.rm.update_trailing_sl(
            current_sl=self.sl_price, current_price=close_p,
            current_high=row['high'], current_low=row['low'],
            current_atr=row.get('ATR', None),
            side="long" if self.position > 0 else "short",
            entry_price=self.entry_price
        )

    def value(self, close_p):
        return self.capital + self.position * close_p


class EquityCurveBacktester:
    """
    Motor de backtest especializado en "Equity Curve Trading".
    Ejecuta dos simulaciones en paralelo:
    1. Virtual: Ejecuta la estrategia normalmente y rastrea el Drawdown (basado en PnL cerrado).
    2. Real: Ejecuta operaciones solo cuando el DD Virtual alcanza un umbral inicial,
             y deja de tomar nuevas operaciones cuando el DD Virtual se recupera.
    """
    def __init__(self, strategy: BaseStrategy, initial_capital: float = 10000.0, commission_pct: float = 0.1,
                 slippage_pct: float = 0.05, entry_on_next_open: bool = True):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct / 100.0
        self.slippage_pct = slippage_pct / 100.0
        # Igual que Backtester: por defecto entra al Open de la vela siguiente a la señal.
        self.entry_on_next_open = entry_on_next_open

    def run(self, df: pd.DataFrame) -> dict:
        if df.empty:
            raise ValueError("El DataFrame de datos históricos está vacío.")

        # 1. Extraer configuración de Equity Curve
        ec_config = self.strategy.config.get("equity_curve_management", {})

        # DD Filter
        dd_enabled = ec_config.get("dd_enabled", True)
        if "dd_enabled" not in ec_config and "enabled" in ec_config:
            dd_enabled = ec_config.get("enabled", False)

        start_dd_val = ec_config.get("start_trading_at_dd_pct")
        start_dd_pct = (float(start_dd_val) if start_dd_val is not None else 30.0) / 100.0

        stop_dd_val = ec_config.get("stop_trading_at_dd_pct")
        stop_dd_gain_pct = float(stop_dd_val) if stop_dd_val is not None else 0.0

        # CL Filter
        cl_enabled = ec_config.get("cl_enabled", False)
        cl_start = ec_config.get("cl_start", 3)
        cl_stop = ec_config.get("cl_stop", 0)

        # 2. Generar señales (vectorizado)
        df = self.strategy.generate_signals(df)
        rm = self.strategy.risk_manager

        # 3. Cuentas Virtual y Real
        virtual = _SpotBook(self.initial_capital, self.commission_pct, self.slippage_pct, rm)
        real = _SpotBook(self.initial_capital, self.commission_pct, self.slippage_pct, rm)
        v_equity_curve = []
        r_equity_curve = []

        v_high_watermark = self.initial_capital
        v_consecutive_losers = 0
        is_dd_trading_active = False
        is_cl_trading_active = False
        is_real_trading_active = False # Bandera de control
        cl_activation_v_capital = None
        dd_activation_v_capital = None
        pending_entry_side = None

        def _enter(side, entry_p, sl, tp, ts):
            # El usuario indicó: "DEBE ESPERAR LA PROXIMA SEÑAL PARA EMPEZAR A OPERAR".
            # La cuenta real solo abre junto con una entrada NUEVA de la virtual.
            if virtual.open(side, entry_p, sl, tp, ts) and is_real_trading_active and real.position == 0:
                real.open(side, entry_p, sl, tp, ts)

        for i in range(len(df)):
            row = df.iloc[i]
            timestamp = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else i
            close_p = float(row['close'])
            open_p = float(row.get('open', close_p))

            # FASE 0: entrada pendiente al Open (SL/TP con datos hasta la vela de la señal)
            if virtual.position == 0 and pending_entry_side is not None:
                side = pending_entry_side
                pending_entry_side = None
                entry_p = open_p * (1 + self.slippage_pct) if side == 'long' else open_p * (1 - self.slippage_pct)
                sl, tp = rm.compute_sl_tp(df, max(i - 1, 0), side=side, entry_price=entry_p, strict=True)
                _enter(side, entry_p, sl, tp, timestamp)

            # FASE 1: SALIDAS (VIRTUAL Y REAL)
            if virtual.position != 0:
                reason, exit_raw = virtual.exit_signal(row, open_p)
                if reason:
                    tr = virtual.close(exit_raw, reason, timestamp)
                    tr['real_status_marker'] = ''
                    pnl = tr['pnl']
                    v_capital = virtual.capital

                    # ACTUALIZAR REGLAS DE EQUITY CURVE AL CERRAR TRADE VIRTUAL
                    if pnl < 0:
                        v_consecutive_losers += 1
                    else:
                        v_consecutive_losers = 0

                    if v_capital > v_high_watermark:
                        v_high_watermark = v_capital
                    v_current_dd = (v_high_watermark - v_capital) / v_high_watermark if v_high_watermark > 0 else 0

                    # Activar o Desactivar Trading Real
                    if dd_enabled:
                        if not is_dd_trading_active and v_current_dd >= start_dd_pct:
                            is_dd_trading_active = True
                            dd_activation_v_capital = v_capital
                        elif is_dd_trading_active and dd_activation_v_capital is not None:
                            gain_pct = ((v_capital - dd_activation_v_capital) / dd_activation_v_capital) * 100
                            if gain_pct >= stop_dd_gain_pct:
                                is_dd_trading_active = False

                    if cl_enabled:
                        if not is_cl_trading_active and v_consecutive_losers >= cl_start:
                            is_cl_trading_active = True
                            cl_activation_v_capital = v_capital
                        elif is_cl_trading_active and cl_activation_v_capital is not None:
                            gain_pct = ((v_capital - cl_activation_v_capital) / cl_activation_v_capital) * 100
                            if gain_pct >= cl_stop:
                                is_cl_trading_active = False

                    old_real_active = is_real_trading_active
                    is_real_trading_active = bool((dd_enabled and is_dd_trading_active) or
                                                  (cl_enabled and is_cl_trading_active))
                    if not old_real_active and is_real_trading_active:
                        tr['real_status_marker'] = 'START'
                    elif old_real_active and not is_real_trading_active:
                        tr['real_status_marker'] = 'STOP'
                    tr['is_real_trade'] = old_real_active
                else:
                    virtual.trail(row, close_p)

            if real.position != 0:
                reason, exit_raw = real.exit_signal(row, open_p)
                if reason:
                    real.close(exit_raw, reason, timestamp)
                else:
                    real.trail(row, close_p)

            # FASE 2: SEÑALES DE ENTRADA
            if virtual.position == 0 and pending_entry_side is None:
                side = 'long' if row.get('entry_long', False) else ('short' if row.get('entry_short', False) else None)
                if side:
                    if self.entry_on_next_open:
                        pending_entry_side = side
                    else:
                        entry_p = close_p * (1 + self.slippage_pct) if side == 'long' else close_p * (1 - self.slippage_pct)
                        sl, tp = rm.compute_sl_tp(df, i, side=side, entry_price=entry_p, strict=True)
                        _enter(side, entry_p, sl, tp, timestamp)

            # REGISTRO DE CURVAS DE CAPITAL (MARK-TO-MARKET AL CIERRE DE LA VELA)
            v_equity_curve.append({'timestamp': timestamp, 'equity': virtual.value(close_p)})
            r_equity_curve.append({'timestamp': timestamp, 'equity': real.value(close_p)})

        # Cierre forzoso al final del periodo (igual que Backtester)
        for book, curve in ((virtual, v_equity_curve), (real, r_equity_curve)):
            if book.position != 0:
                tr = book.close(close_p, 'EOD_CLOSE', timestamp)
                if book is virtual:
                    tr['real_status_marker'] = ''
                    tr['is_real_trade'] = is_real_trading_active
                curve[-1]['equity'] = book.value(close_p)

        # --- Empaquetar Resultados Finales (Real) y Virtual ---
        r_trades_df = pd.DataFrame(real.trades)
        r_equity_df = pd.DataFrame(r_equity_curve).set_index('timestamp')

        v_trades_df = pd.DataFrame(virtual.trades)
        v_equity_df = pd.DataFrame(v_equity_curve).set_index('timestamp')

        run_results = {
            "run_id": str(uuid.uuid4()),
            "strategy_name": f"{self.strategy.name} (Equity Curve Mode)",
            "config_snapshot": json.dumps(self.strategy.config),
            "symbol": self.strategy.symbol,
            "timeframe": self.strategy.timeframe,
            "start_date": df.index[0] if isinstance(df.index, pd.DatetimeIndex) else None,
            "end_date": df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else None,
            "created_at": datetime.now(),

            # Resultados de la Estrategia Real
            "trades": r_trades_df,
            "equity_curve": r_equity_df,
            "final_equity": float(r_equity_df['equity'].iloc[-1]) if not r_equity_df.empty else self.initial_capital,

            # Incluimos los resultados Virtuales por si la UI desea dibujarlos en fondo
            "virtual_trades": v_trades_df,
            "virtual_equity_curve": v_equity_df,

            "raw_data": df
        }

        # Métricas de la Estrategia Real
        trade_metrics = calculate_metrics(r_trades_df, self.initial_capital)
        eq_metrics = calculate_equity_curve_metrics(r_equity_df['equity'], timeframe=self.strategy.timeframe)

        run_results.update(trade_metrics)
        run_results.update(eq_metrics)

        def _calc_fees(tdf):
            if tdf.empty: return 0.0, 0.0
            tot_comm = ((tdf['entry_price'] + tdf['exit_price']) * tdf['quantity'] * self.commission_pct).fillna(0).sum()
            longs = tdf[tdf['side'].str.lower() == 'long']
            shorts = tdf[tdf['side'].str.lower() == 'short']
            slip_pct = self.slippage_pct
            tot_slip = 0.0
            if not longs.empty:
                tot_slip += ((longs['entry_price'] * (slip_pct / (1 + slip_pct)) +
                              longs['exit_price'] * (slip_pct / (1 - slip_pct))) * longs['quantity']).fillna(0).sum()
            if not shorts.empty:
                tot_slip += ((shorts['entry_price'] * (slip_pct / (1 - slip_pct)) +
                              shorts['exit_price'] * (slip_pct / (1 + slip_pct))) * shorts['quantity']).fillna(0).sum()
            return tot_comm, tot_slip

        r_tot_comm, r_tot_slip = _calc_fees(r_trades_df)
        v_tot_comm, v_tot_slip = _calc_fees(v_trades_df)

        run_results['real_total_commission'] = r_tot_comm
        run_results['real_total_slippage'] = r_tot_slip
        run_results['virtual_total_commission'] = v_tot_comm
        run_results['virtual_total_slippage'] = v_tot_slip

        run_results['real_commission_pct_cap'] = (r_tot_comm / self.initial_capital * 100) if self.initial_capital > 0 else 0
        run_results['real_slippage_pct_cap'] = (r_tot_slip / self.initial_capital * 100) if self.initial_capital > 0 else 0
        run_results['virtual_commission_pct_cap'] = (v_tot_comm / self.initial_capital * 100) if self.initial_capital > 0 else 0
        run_results['virtual_slippage_pct_cap'] = (v_tot_slip / self.initial_capital * 100) if self.initial_capital > 0 else 0

        # Guardar configuración para UI
        run_results['commission_pct_cfg'] = self.commission_pct * 100
        run_results['slippage_pct_cfg'] = self.slippage_pct * 100

        return run_results
