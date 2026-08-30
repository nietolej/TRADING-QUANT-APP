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
        entry_on_next_open: bool = False
    ):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct / 100.0
        self.slippage_pct = slippage_pct / 100.0
        self.account_mode = account_mode  # "spot_cash" | "coin_margined_hold"
        self.leverage = float(leverage) if leverage else 1.0
        self.initial_base_capital = initial_base_capital
        self.entry_on_next_open = entry_on_next_open  # Si True: entra al Open de la vela siguiente (más realista)
        self.use_vectorbt = (account_mode == "spot_cash")  # Flag to use vectorized backtesting by default

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
        if self.account_mode == "coin_margined_hold" or has_sl or has_tp:
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
        current_trade = {}
        pending_entry_side = None   # 'long' | 'short' | None — señal pendiente para entrar al Open siguiente
        
        trades = []
        equity = []
        
        for i in range(len(df)):
            row = df.iloc[i]
            timestamp = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else i
            close_p = float(row['close'])
            
            # Registrar equity
            if is_coin_m:
                # Unrealized PnL de la posición adicional de trading
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
                
            equity.append({
                'timestamp': timestamp,
                'equity': current_value,
                'equity_base': eq_base,
                'hold_balance': hold_balance,
                'benchmark_quote': initial_hold_base * close_p,
                'benchmark_base': initial_hold_base
            })
            
            # Revisar condiciones de salida o SL/TP si estamos en posición
            if position > 0:
                # Actualizar Trailing Stop Loss (incluye chandelier y break-even)
                current_atr = row.get('ATR', None)
                sl_price = self.strategy.risk_manager.update_trailing_sl(
                    current_sl=sl_price, 
                    current_price=close_p, 
                    current_high=row['high'], 
                    current_low=row['low'], 
                    current_atr=current_atr, 
                    side="long",
                    entry_price=entry_price
                )
                
                exit_reason = None
                exit_p = close_p
                
                if sl_price is not None and row['low'] <= sl_price:
                    exit_reason = "SL"
                    exit_p = sl_price
                elif tp_price is not None and tp_price > 0 and row['high'] >= tp_price:
                    exit_reason = "TP"
                    exit_p = tp_price
                elif row.get('exit_long', False):
                    exit_reason = "Signal"
                    exit_p = close_p
                    
                if exit_reason:
                    # Slippage & comisiones
                    exit_p = exit_p * (1 - self.slippage_pct)
                    revenue = position * exit_p
                    commission = revenue * self.commission_pct
                    
                    pnl = revenue - (position * entry_price) - commission
                    
                    if is_coin_m:
                        pnl_base = pnl / exit_p if exit_p > 0 else 0.0
                        hold_balance += pnl_base
                        capital = hold_balance * exit_p
                    else:
                        capital += revenue - commission
                        pnl_base = pnl / exit_p if exit_p > 0 else 0.0
                    
                    trades.append({
                        'entry_time': current_trade.get('entry_time', timestamp),
                        'exit_time': timestamp,
                        'side': 'long',
                        'entry_price': entry_price,
                        'exit_price': exit_p,
                        'quantity': position,
                        'pnl': pnl,
                        'pnl_base': pnl_base,
                        'exit_reason': exit_reason,
                        'portfolio_value': capital,
                        'hold_balance': hold_balance
                    })
                    position = 0.0
            
            elif position < 0:
                # Actualizar Trailing Stop Loss (Inverso: SL baja cuando precio baja)
                current_atr = row.get('ATR', None)
                sl_price = self.strategy.risk_manager.update_trailing_sl(
                    current_sl=sl_price, 
                    current_price=close_p, 
                    current_high=row['high'], 
                    current_low=row['low'], 
                    current_atr=current_atr, 
                    side="short",
                    entry_price=entry_price
                )
                
                exit_reason = None
                exit_p = close_p
                
                if sl_price is not None and sl_price > 0 and row['high'] >= sl_price:
                    exit_reason = "SL"
                    exit_p = sl_price
                elif tp_price is not None and tp_price > 0 and row['low'] <= tp_price:
                    exit_reason = "TP"
                    exit_p = tp_price
                elif row.get('exit_short', False):
                    exit_reason = "Signal"
                    exit_p = close_p
                    
                if exit_reason:
                    # Slippage & comisiones
                    exit_p = exit_p * (1 + self.slippage_pct)
                    cost = abs(position) * exit_p
                    commission = cost * self.commission_pct
                    
                    revenue = abs(position) * entry_price
                    pnl = revenue - cost - commission
                    
                    if is_coin_m:
                        pnl_base = pnl / exit_p if exit_p > 0 else 0.0
                        hold_balance += pnl_base
                        capital = hold_balance * exit_p
                    else:
                        capital -= cost + commission
                        pnl_base = pnl / exit_p if exit_p > 0 else 0.0
                    
                    trades.append({
                        'entry_time': current_trade.get('entry_time', timestamp),
                        'exit_time': timestamp,
                        'side': 'short',
                        'entry_price': entry_price,
                        'exit_price': exit_p,
                        'quantity': abs(position),
                        'pnl': pnl,
                        'pnl_base': pnl_base,
                        'exit_reason': exit_reason,
                        'portfolio_value': capital,
                        'hold_balance': hold_balance
                    })
                    position = 0.0
            
            # ── Ejecución de entrada pendiente (modo entry_on_next_open) ──────────
            # Si la vela anterior generó señal, ahora ejecutamos al Open de esta vela
            if position == 0 and pending_entry_side is not None:
                open_p = float(row.get('open', close_p))
                side = pending_entry_side
                pending_entry_side = None

                sl, tp = self.strategy.risk_manager.compute_sl_tp(df, i, side=side)

                if side == 'long':
                    entry_p = open_p * (1 + self.slippage_pct)
                    if is_coin_m:
                        effective_cap = hold_balance * entry_p
                        qty = self.strategy.risk_manager.compute_position_size(effective_cap, entry_p, sl) * self.leverage
                    else:
                        qty = self.strategy.risk_manager.compute_position_size(capital, entry_p, sl)
                        max_qty = capital / (entry_p * (1 + self.commission_pct))
                        if qty > max_qty:
                            qty = max_qty
                    cost = qty * entry_p
                    commission = cost * self.commission_pct
                    if is_coin_m:
                        if hold_balance > 0 and qty > 1e-6:
                            position = qty; entry_price = entry_p; sl_price = sl; tp_price = tp
                            current_trade = {'entry_time': timestamp, 'side': 'long', 'quantity': qty}
                    else:
                        if capital >= (cost + commission) - 1e-6 and qty > 1e-6:
                            position = qty; entry_price = entry_p; sl_price = sl; tp_price = tp
                            capital -= (cost + commission)
                            current_trade = {'entry_time': timestamp, 'side': 'long', 'quantity': qty}

                elif side == 'short':
                    entry_p = open_p * (1 - self.slippage_pct)
                    if is_coin_m:
                        effective_cap = hold_balance * entry_p
                        qty = self.strategy.risk_manager.compute_position_size(effective_cap, entry_p, sl) * self.leverage
                    else:
                        qty = self.strategy.risk_manager.compute_position_size(capital, entry_p, sl)
                        max_qty = capital / (entry_p * (1 + self.commission_pct))
                        if qty > max_qty:
                            qty = max_qty
                    revenue = qty * entry_p
                    commission = revenue * self.commission_pct
                    if is_coin_m:
                        if hold_balance > 0 and qty > 1e-6:
                            position = -qty; entry_price = entry_p; sl_price = sl; tp_price = tp
                            current_trade = {'entry_time': timestamp, 'side': 'short', 'quantity': qty}
                    else:
                        if capital >= (revenue + commission) - 1e-6 and qty > 1e-6:
                            capital += revenue - commission
                            position = -qty; entry_price = entry_p; sl_price = sl; tp_price = tp
                            current_trade = {'entry_time': timestamp, 'side': 'short', 'quantity': qty}

            # ── Detectar señales de entrada para la próxima vela o ejecutar en esta ─
            if position == 0:
                if row.get('entry_long', False):
                    if self.entry_on_next_open:
                        # Encolar la entrada para ejecutar al Open de la siguiente vela
                        pending_entry_side = 'long'
                    else:
                        sl, tp = self.strategy.risk_manager.compute_sl_tp(df, i, side="long")
                        entry_p = close_p * (1 + self.slippage_pct)
                        
                        if is_coin_m:
                            effective_cap = hold_balance * entry_p
                            qty = self.strategy.risk_manager.compute_position_size(effective_cap, entry_p, sl) * self.leverage
                        else:
                            qty = self.strategy.risk_manager.compute_position_size(capital, entry_p, sl)
                            max_qty = capital / (entry_p * (1 + self.commission_pct))
                            if qty > max_qty:
                                qty = max_qty
                            
                        cost = qty * entry_p
                        commission = cost * self.commission_pct
                        
                        if is_coin_m:
                            if hold_balance > 0 and qty > 1e-6:
                                position = qty
                                entry_price = entry_p
                                sl_price = sl
                                tp_price = tp
                                current_trade = {
                                    'entry_time': timestamp,
                                    'side': 'long',
                                    'quantity': qty
                                }
                        else:
                            if capital >= (cost + commission) - 1e-6 and qty > 1e-6:
                                position = qty
                                entry_price = entry_p
                                sl_price = sl
                                tp_price = tp
                                capital -= (cost + commission)
                                current_trade = {
                                    'entry_time': timestamp,
                                    'side': 'long',
                                    'quantity': qty
                                }
                        
                elif row.get('entry_short', False):
                    if self.entry_on_next_open:
                        pending_entry_side = 'short'
                    else:
                        sl, tp = self.strategy.risk_manager.compute_sl_tp(df, i, side="short")
                        entry_p = close_p * (1 - self.slippage_pct)
                        
                        if is_coin_m:
                            effective_cap = hold_balance * entry_p
                            qty = self.strategy.risk_manager.compute_position_size(effective_cap, entry_p, sl) * self.leverage
                        else:
                            qty = self.strategy.risk_manager.compute_position_size(capital, entry_p, sl)
                            max_qty = capital / (entry_p * (1 + self.commission_pct))
                            if qty > max_qty:
                                qty = max_qty
                            
                        revenue = qty * entry_p
                        commission = revenue * self.commission_pct
                        
                        if is_coin_m:
                            if hold_balance > 0 and qty > 1e-6:
                                position = -qty
                                entry_price = entry_p
                                sl_price = sl
                                tp_price = tp
                                current_trade = {
                                    'entry_time': timestamp,
                                    'side': 'short',
                                    'quantity': qty
                                }
                        else:
                            if capital >= (revenue + commission) - 1e-6 and qty > 1e-6:
                                capital += revenue - commission
                                position = -qty
                                entry_price = entry_p
                                sl_price = sl
                                tp_price = tp
                                current_trade = {
                                    'entry_time': timestamp,
                                    'side': 'short',
                                    'quantity': qty
                                }

        # Calcular Métricas
        trades_df = pd.DataFrame(trades)
        equity_df = pd.DataFrame(equity).set_index('timestamp')
        
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
            "final_base_capital": hold_balance if is_coin_m else (equity_df['equity_base'].iloc[-1] if not equity_df.empty else initial_hold_base)
        }
        
        trade_metrics = calculate_metrics(trades_df, initial_quote_cap)
        eq_metrics = calculate_equity_curve_metrics(equity_df['equity'])
        
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
