import pandas as pd
import numpy as np
from datetime import datetime
import uuid
from strategy_engine.base_strategy import BaseStrategy
from .metrics import calculate_metrics, calculate_equity_curve_metrics
import json

class Backtester:
    def __init__(
        self,
        strategy: BaseStrategy,
        initial_capital: float = 10000.0,
        commission_pct: float = 0.1,
        slippage_pct: float = 0.05,
        account_mode: str = "spot_cash",
        leverage: float = 1.0,
        initial_base_capital: float = None,
        entry_on_next_open: bool = True,
        trade_start=None
    ):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct / 100.0
        self.slippage_pct = slippage_pct / 100.0
        self.account_mode = account_mode  # "spot_cash" | "coin_margined_hold"
        self.leverage = float(leverage) if leverage else 1.0
        self.initial_base_capital = initial_base_capital
        self.entry_on_next_open = entry_on_next_open  # Si True (default, realista): entra al Open de la vela siguiente
        # run_vectorized (vectorbt) no soporta entry_on_next_open: siempre entra al close de la
        # misma vela que genero la señal (look-ahead). Solo se usa cuando el propio usuario
        # desactiva explicitamente el modo realista, aceptando ese sesgo a cambio de velocidad.
        self.use_vectorbt = (account_mode == "spot_cash") and not entry_on_next_open
        # Calentamiento de indicadores (walk-forward): las velas anteriores a trade_start solo
        # alimentan los indicadores; no se opera en ellas y quedan fuera de la equity curve y
        # de las métricas. Sin esto cada ventana arrancaba "en frío" (EMA/SMA en NaN al
        # inicio) y perdía parte de sus velas sin posibilidad de dar señal.
        self.trade_start = pd.Timestamp(trade_start) if trade_start is not None else None

    def run_vectorized(self, df: pd.DataFrame) -> dict:
        """
        Ejecuta el backtest utilizando vectorbt para máxima velocidad.
        Ideal para Grid Search y ML en modo spot cash.
        """
        import vectorbt as vbt
        
        if df.empty:
            raise ValueError("El DataFrame está vacío.")
            
        # Generar señales vectorizadas
        df = self.strategy.generate_signals(df)
        
        # Si el modelo usa Machine Learning, las señales vendrán pre-calculadas en df['ml_signal']
        entries = df.get('entry_long', pd.Series(False, index=df.index))
        exits = df.get('exit_long', pd.Series(False, index=df.index))
        
        # Construir portfolio usando vectorbt
        portfolio = vbt.Portfolio.from_signals(
            close=df['close'],
            entries=entries,
            exits=exits,
            init_cash=self.initial_capital,
            fees=self.commission_pct,
            slippage=self.slippage_pct
        )
        
        metrics = {}
        try:
            vbt_trades = portfolio.trades.records_readable
        except AttributeError:
            try:
                vbt_trades = portfolio.trades.records
            except Exception:
                vbt_trades = pd.DataFrame()

        trades_df = pd.DataFrame()
        if not vbt_trades.empty:
            trades_df['entry_time'] = vbt_trades['Entry Timestamp']
            trades_df['exit_time'] = vbt_trades['Exit Timestamp']
            trades_df['side'] = vbt_trades['Direction'].str.lower()
            trades_df['entry_price'] = vbt_trades['Avg Entry Price']
            trades_df['exit_price'] = vbt_trades['Avg Exit Price']
            trades_df['quantity'] = vbt_trades['Size']
            trades_df['pnl'] = vbt_trades['PnL']
            trades_df['exit_reason'] = "Signal"
            trades_df['portfolio_value'] = vbt_trades['PnL'].cumsum() + self.initial_capital
            
        start_p = float(df['open'].iloc[0]) if 'open' in df.columns else float(df['close'].iloc[0])
        init_base = self.initial_capital / start_p if start_p > 0 else 0.0

        run_results = {
            "run_id": str(uuid.uuid4()),
            "strategy_name": self.strategy.name,
            "config_snapshot": json.dumps(self.strategy.config),
            "symbol": self.strategy.symbol,
            "timeframe": self.strategy.timeframe,
            "start_date": df.index[0] if isinstance(df.index, pd.DatetimeIndex) else None,
            "end_date": df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else None,
            "created_at": datetime.now(),
            "trades": trades_df,
            "equity_curve": pd.DataFrame({"equity": portfolio.value()}),
            "raw_data": df,
            "account_mode": "spot_cash",
            "initial_base_capital": init_base,
            "final_equity": float(portfolio.value().iloc[-1]),
            "cagr": 0.0,
            "max_drawdown_pct": 0.0,
            "percent_profitable": 0.0,
            "profit_factor": 0.0,
            "total_trades": 0,
            "average_trade_net_profit": 0.0
        }
        return run_results

        
    def run(self, df: pd.DataFrame) -> dict:
        """
        Punto de entrada general.
        Intenta vectorbt si no hay SL/TP activos y es modo spot_cash; cae a iterativo si falla o si es coin_margined_hold.
        """
        sl_type = self.strategy.risk_manager.sl_config.get("type", "none").lower()
        tp_type = self.strategy.risk_manager.tp_config.get("type", "none").lower()
        has_sl = sl_type not in ["none", ""]
        has_tp = tp_type not in ["none", ""]
        
        # En modo Coin-Margined o con SL/TP activos siempre usamos modo iterativo (máxima precisión)
        if self.account_mode == "coin_margined_hold" or has_sl or has_tp or self.trade_start is not None:
            return self.run_iterative(df)
        
        # Sin SL/TP en spot cash intentamos vectorbt; si falla, caemos a iterativo
        if self.use_vectorbt:
            try:
                return self.run_vectorized(df)
            except Exception as vbt_err:
                import warnings
                warnings.warn(f"vectorbt falló ({vbt_err}), usando backtester iterativo.")
            
        return self.run_iterative(df)
        

    def run_iterative(self, df: pd.DataFrame) -> dict:
        """
        Ejecuta el backtest sobre el DataFrame con soporte completo para:
        1. Modo Spot Cash Tradicional (salidas a divisa Quote).
        2. Modo Coin-Margined (Hold permanente del activo Base como colateral + trading apalancado).
        """
        if df.empty:
            raise ValueError("El DataFrame de datos históricos está vacío.")
            
        # Generar señales (vectorizado)
        df = self.strategy.generate_signals(df)
        
        start_price = float(df['open'].iloc[0]) if len(df) > 0 and 'open' in df.columns else float(df['close'].iloc[0])
        is_coin_m = (self.account_mode == "coin_margined_hold")
        
        if is_coin_m:
            hold_balance = float(self.initial_base_capital) if self.initial_base_capital is not None else (self.initial_capital / start_price if start_price > 0 else 1.0)
            initial_hold_base = hold_balance
            initial_quote_cap = initial_hold_base * start_price
            capital = initial_quote_cap
        else:
            capital = self.initial_capital
            hold_balance = 0.0
            initial_hold_base = self.initial_capital / start_price if start_price > 0 else 0.0
            initial_quote_cap = self.initial_capital
        
        position = 0.0  # cantidad de activo en trade
        entry_price = 0.0
        sl_price = 0.0
        tp_price = 0.0
        liq_price = None   # Precio de liquidación forzosa (solo coin_margined_hold apalancado)
        entry_margin = 0.0  # Margen (en quote) comprometido en la posición apalancada actual
        current_trade = {}
        pending_entry_side = None   # 'long' | 'short' | None — señal pendiente para entrar al Open siguiente
        
        trades = []
        equity = []

        def _leverage_cap_and_liq(qty, entry_p, effective_cap, side):
            """Solo coin_margined_hold: antes no existia ningun tope de tamaño de posicion
            ni de riesgo de margen (a diferencia del modo spot, que si limita qty al capital
            disponible) — con leverage alto y una vela adversa, capital/hold_balance podian
            volverse negativos sin que el motor lo detectara como cuenta liquidada. Ahora:
            1) limita qty al notional maximo que el margen (effective_cap) soporta con el
               apalancamiento configurado, y
            2) calcula un precio de liquidacion isolated-margin aproximado (ignora fees y el
               buffer de margen de mantenimiento real del exchange, por lo que es conservador:
               en la practica un exchange real liquidaria un poco antes que este precio)."""
            if entry_p <= 0 or self.leverage <= 0:
                return qty, 0.0, None
            max_notional = effective_cap * self.leverage
            if qty * entry_p > max_notional:
                qty = max_notional / entry_p
            margin = (qty * entry_p) / self.leverage
            if side == 'long':
                liq_p = entry_p * (1.0 - 1.0 / self.leverage)
            else:
                liq_p = entry_p * (1.0 + 1.0 / self.leverage)
            return qty, margin, liq_p

        rm = self.strategy.risk_manager

        def _entry_qty(side, entry_p, sl):
            """Tamaño de la posición: (qty, margen, precio de liquidación)."""
            if is_coin_m:
                effective_cap = hold_balance * entry_p
                qty = rm.compute_position_size(effective_cap, entry_p, sl) * self.leverage
                return _leverage_cap_and_liq(qty, entry_p, effective_cap, side)
            qty = rm.compute_position_size(capital, entry_p, sl)
            max_qty = capital / (entry_p * (1 + self.commission_pct))
            return min(qty, max_qty), 0.0, None

        def _open(side, entry_p, sl, tp, ts):
            nonlocal position, entry_price, sl_price, tp_price, entry_margin, liq_price
            nonlocal capital, hold_balance, current_trade
            qty, margin, liq_p = _entry_qty(side, entry_p, sl)
            if qty <= 1e-6:
                return
            notional = qty * entry_p
            commission = notional * self.commission_pct
            if is_coin_m:
                if hold_balance <= 0:
                    return
                # Antes la comisión de entrada nunca se cobraba en coin_margined_hold.
                hold_balance -= commission / entry_p
                entry_margin = margin
                liq_price = liq_p
            elif capital < (notional + commission) - 1e-6:
                return
            elif side == 'long':
                capital -= notional + commission
            else:
                capital += notional - commission
            position = qty if side == 'long' else -qty
            entry_price = entry_p
            sl_price = sl
            tp_price = tp
            current_trade = {'entry_time': ts, 'side': side, 'quantity': qty, 'entry_commission': commission}

        def _close(exit_raw, reason, ts):
            nonlocal position, capital, hold_balance, liq_price, entry_margin
            qty = abs(position)
            side = 'long' if position > 0 else 'short'
            if side == 'long':
                exit_p = exit_raw * (1 - self.slippage_pct)
                gross = qty * (exit_p - entry_price)
            else:
                exit_p = exit_raw * (1 + self.slippage_pct)
                gross = qty * (entry_price - exit_p)
            exit_comm = qty * exit_p * self.commission_pct
            entry_comm = current_trade.get('entry_commission', 0.0)
            # El PnL del trade incluye AMBAS comisiones. Antes omitía la de entrada (ya
            # descontada del capital al abrir), así que net_profit/profit_factor/win_rate
            # no cuadraban con la equity: un trade que ganaba menos que la comisión de
            # entrada se contaba como ganador.
            pnl = gross - exit_comm - entry_comm

            if is_coin_m:
                # Perdida total del margen usado (isolated margin): no se puede perder mas
                # que el margen inicial de la posicion, ni tampoco menos.
                settled = -entry_margin if reason == "LIQUIDATION" else gross - exit_comm
                if reason == "LIQUIDATION":
                    pnl = -entry_margin - entry_comm
                hold_balance += settled / exit_p if exit_p > 0 else 0.0
                capital = hold_balance * exit_p
            elif side == 'long':
                capital += qty * exit_p - exit_comm
            else:
                capital -= qty * exit_p + exit_comm

            trades.append({
                'entry_time': current_trade.get('entry_time', ts),
                'exit_time': ts,
                'side': side,
                'entry_price': entry_price,
                'exit_price': exit_p,
                'quantity': qty,
                'pnl': pnl,
                'pnl_base': pnl / exit_p if exit_p > 0 else 0.0,
                'exit_reason': reason,
                'portfolio_value': capital,
                'hold_balance': hold_balance
            })
            position = 0.0
            liq_price = None
            entry_margin = 0.0

        def _mark(ts, close_p):
            if is_coin_m:
                if position > 0:
                    unrealized_pnl = position * (close_p - entry_price)
                elif position < 0:
                    unrealized_pnl = abs(position) * (entry_price - close_p)
                else:
                    unrealized_pnl = 0.0
                current_value = (hold_balance * close_p) + unrealized_pnl
                eq_base = hold_balance + (unrealized_pnl / close_p if close_p > 0 else 0.0)
            else:
                current_value = capital + (position * close_p)
                eq_base = current_value / close_p if close_p > 0 else 0.0
            return {
                'timestamp': ts,
                'equity': current_value,
                'equity_base': eq_base,
                'hold_balance': hold_balance,
                'benchmark_quote': initial_hold_base * close_p,
                'benchmark_base': initial_hold_base
            }

        for i in range(len(df)):
            row = df.iloc[i]
            timestamp = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else i
            close_p = float(row['close'])
            open_p = float(row.get('open', close_p))

            # ── 1. Entrada pendiente al Open de esta vela (modo entry_on_next_open) ──
            # Se ejecuta ANTES de revisar salidas para que el SL/TP rija ya en la propia
            # vela de entrada (antes se ignoraba todo el rango de esa vela). El SL/TP se
            # ancla al precio real de ejecución y solo usa datos hasta la vela de la señal
            # (i-1): al Open de la vela i su high/low/close todavía no existen.
            if position == 0 and pending_entry_side is not None:
                side = pending_entry_side
                pending_entry_side = None
                entry_p = open_p * (1 + self.slippage_pct) if side == 'long' else open_p * (1 - self.slippage_pct)
                sl, tp = rm.compute_sl_tp(df, max(i - 1, 0), side=side, entry_price=entry_p, strict=True)
                _open(side, entry_p, sl, tp, timestamp)

            # ── 2. Salidas: liquidación, SL, TP o señal ──
            # IMPORTANTE (orden): el SL/TP se comprueba con el nivel vigente desde el CIERRE
            # de la barra anterior, NUNCA con un trailing recalculado usando el high/low de
            # esta misma barra — el dato OHLC no dice si el high o el low ocurrió primero
            # dentro de la vela. El trailing se actualiza DESPUÉS, solo si no hubo salida,
            # para que rija recién en la barra siguiente. Si la vela abre más allá del SL
            # (hueco), la orden se ejecuta al Open, no al nivel teórico del stop.
            if position != 0:
                exit_reason = None
                exit_raw = close_p
                if position > 0:
                    # Liquidación forzosa (solo apalancado): tiene prioridad sobre SL/TP.
                    if is_coin_m and liq_price is not None and row['low'] <= liq_price:
                        exit_reason, exit_raw = "LIQUIDATION", liq_price
                    elif sl_price is not None and row['low'] <= sl_price:
                        exit_reason, exit_raw = "SL", min(sl_price, open_p)
                    elif tp_price is not None and tp_price > 0 and row['high'] >= tp_price:
                        exit_reason, exit_raw = "TP", max(tp_price, open_p)
                    elif row.get('exit_long', False):
                        exit_reason = "Signal"
                else:
                    if is_coin_m and liq_price is not None and row['high'] >= liq_price:
                        exit_reason, exit_raw = "LIQUIDATION", liq_price
                    elif sl_price is not None and sl_price > 0 and row['high'] >= sl_price:
                        exit_reason, exit_raw = "SL", max(sl_price, open_p)
                    elif tp_price is not None and tp_price > 0 and row['low'] <= tp_price:
                        exit_reason, exit_raw = "TP", min(tp_price, open_p)
                    elif row.get('exit_short', False):
                        exit_reason = "Signal"

                if exit_reason:
                    _close(exit_raw, exit_reason, timestamp)
                else:
                    sl_price = rm.update_trailing_sl(
                        current_sl=sl_price,
                        current_price=close_p,
                        current_high=row['high'],
                        current_low=row['low'],
                        current_atr=row.get('ATR', None),
                        side="long" if position > 0 else "short",
                        entry_price=entry_price
                    )

            # ── 3. Señales de entrada: se encolan para el Open siguiente o se ejecutan al close ──
            in_warmup = self.trade_start is not None and timestamp < self.trade_start
            if position == 0 and pending_entry_side is None and not in_warmup:
                side = 'long' if row.get('entry_long', False) else ('short' if row.get('entry_short', False) else None)
                if side:
                    if self.entry_on_next_open:
                        pending_entry_side = side
                    else:
                        entry_p = close_p * (1 + self.slippage_pct) if side == 'long' else close_p * (1 - self.slippage_pct)
                        sl, tp = rm.compute_sl_tp(df, i, side=side, entry_price=entry_p, strict=True)
                        _open(side, entry_p, sl, tp, timestamp)

            # ── 4. Equity al CIERRE de la vela, después de entradas y salidas ──
            # Antes se registraba al inicio de la iteración: el día en que saltaba un SL se
            # valoraba la posición al close como si siguiera abierta (inflaba el Max DD) y el
            # último punto no incluía el costo del cierre final.
            equity.append(_mark(timestamp, close_p))

        # Forzar el cierre de cualquier posición abierta al terminar el rango de datos.
        # Antes, una posición abierta al final del backtest sumaba su PnL no realizado a la
        # equity curve (afectando CAGR/Sharpe/drawdown) pero NUNCA aparecía como trade cerrado
        # (no afectaba total_trades/win_rate/profit_factor) — dos secciones del mismo reporte
        # contaban historias distintas del mismo backtest, y ese resultado dependía de un
        # corte de fechas arbitrario, no de una señal de salida real.
        if position != 0:
            _close(close_p, 'EOD_CLOSE', timestamp)
            equity[-1] = _mark(timestamp, close_p)

        # Calcular Métricas
        trades_df = pd.DataFrame(trades)
        equity_df = pd.DataFrame(equity).set_index('timestamp')
        if self.trade_start is not None:
            equity_df = equity_df[equity_df.index >= self.trade_start]
        
        run_results = {
            "run_id": str(uuid.uuid4()),
            "strategy_name": self.strategy.name,
            "config_snapshot": json.dumps(self.strategy.config),
            "symbol": self.strategy.symbol,
            "timeframe": self.strategy.timeframe,
            "start_date": df.index[0] if isinstance(df.index, pd.DatetimeIndex) else None,
            "end_date": df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else None,
            "created_at": datetime.now(),
            "trades": trades_df,
            "equity_curve": equity_df,
            "raw_data": df,
            "account_mode": self.account_mode,
            "leverage": self.leverage,
            "initial_base_capital": initial_hold_base,
            "final_base_capital": hold_balance if is_coin_m else (equity_df['equity_base'].iloc[-1] if not equity_df.empty else initial_hold_base),
            # El simulador de portafolio lee esta clave para "Cap. Final" / "PnL %" del
            # desglose; antes no existía y la tabla mostraba siempre el capital asignado.
            "final_equity": float(equity_df['equity'].iloc[-1]) if not equity_df.empty else initial_quote_cap
        }
        
        trade_metrics = calculate_metrics(trades_df, initial_quote_cap)
        eq_metrics = calculate_equity_curve_metrics(equity_df['equity'], timeframe=self.strategy.timeframe)
        
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

        tot_comm, tot_slip = _calc_fees(trades_df)
        
        run_results['real_total_commission'] = tot_comm
        run_results['real_total_slippage'] = tot_slip
        run_results['real_commission_pct_cap'] = (tot_comm / initial_quote_cap * 100) if initial_quote_cap > 0 else 0
        run_results['real_slippage_pct_cap'] = (tot_slip / initial_quote_cap * 100) if initial_quote_cap > 0 else 0
        
        run_results['commission_pct_cfg'] = self.commission_pct * 100
        run_results['slippage_pct_cfg'] = self.slippage_pct * 100

        # Combinar métricas
        run_results.update(trade_metrics)
        run_results.update(eq_metrics)
        
        return run_results
