import pandas as pd
import numpy as np
from datetime import datetime
import uuid
from strategy_engine.base_strategy import BaseStrategy
from .metrics import calculate_metrics, calculate_equity_curve_metrics
import json

class Backtester:
    def __init__(self, strategy: BaseStrategy, initial_capital: float = 10000.0, commission_pct: float = 0.1, slippage_pct: float = 0.05):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct / 100.0
        self.slippage_pct = slippage_pct / 100.0
        self.use_vectorbt = True  # Flag to use vectorized backtesting by default

    def run_vectorized(self, df: pd.DataFrame) -> dict:
        """
        Ejecuta el backtest utilizando vectorbt para máxima velocidad.
        Ideal para Grid Search y ML.
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
        
        # Mapeo de métricas a nuestro formato
        metrics = portfolio.stats()
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
            "cagr": metrics.get("CAGR [%]", 0) / 100,
            "max_drawdown_pct": metrics.get("Max Drawdown [%]", 0),
            "percent_profitable": metrics.get("Win Rate [%]", 0),
            "profit_factor": metrics.get("Profit Factor", 0),
            "total_trades": metrics.get("Total Trades", 0),
            "average_trade_net_profit": metrics.get("Avg Winning Trade [%]", 0)
        }
        return run_results

        
    def run(self, df: pd.DataFrame) -> dict:
        """
        Punto de entrada general.
        Intenta vectorbt si no hay SL/TP activos; cae a iterativo si falla.
        """
        sl_type = self.strategy.risk_manager.sl_config.get("type", "none").lower()
        tp_type = self.strategy.risk_manager.tp_config.get("type", "none").lower()
        has_sl = sl_type not in ["none", ""]
        has_tp = tp_type not in ["none", ""]
        
        # Con SL/TP activos siempre usamos modo iterativo (más preciso)
        if has_sl or has_tp:
            return self.run_iterative(df)
        
        # Sin SL/TP intentamos vectorbt; si falla, caemos a iterativo
        if self.use_vectorbt:
            try:
                return self.run_vectorized(df)
            except Exception as vbt_err:
                import warnings
                warnings.warn(f"vectorbt falló ({vbt_err}), usando backtester iterativo.")
            
        return self.run_iterative(df)
        

    def run_iterative(self, df: pd.DataFrame) -> dict:
        """
        Ejecuta el backtest sobre el DataFrame (modo iterativo legacy). 
        Asume que df contiene 'open', 'high', 'low', 'close', y variables on-chain si son necesarias.
        """
        if df.empty:
            raise ValueError("El DataFrame de datos históricos está vacío.")
            
        # Generar señales (vectorizado)
        df = self.strategy.generate_signals(df)
        
        # Simulación de operaciones iterativa (event-driven ligero)
        # para manejar con precisión SL/TP dinámicos y sizing
        
        capital = self.initial_capital
        position = 0 # cantidad de activo
        entry_price = 0
        sl_price = 0
        tp_price = 0
        
        trades = []
        equity = []
        
        for i in range(len(df)):
            row = df.iloc[i]
            timestamp = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else i
            
            # Registrar equity
            current_value = capital + (position * row['close'])
            equity.append({'timestamp': timestamp, 'equity': current_value})
            
            # Revisar condiciones de salida o SL/TP si estamos en posición
            if position > 0:
                # Actualizar Trailing Stop Loss (incluye chandelier)
                current_atr = row.get('ATR', None)
                sl_price = self.strategy.risk_manager.update_trailing_sl(
                    current_sl=sl_price, 
                    current_price=row['close'], 
                    current_high=row['high'], 
                    current_low=row['low'], 
                    current_atr=current_atr, 
                    side="long"
                )
                
                exit_reason = None
                exit_p = row['close']
                
                if sl_price is not None and row['low'] <= sl_price:
                    exit_reason = "SL"
                    exit_p = sl_price
                elif tp_price is not None and tp_price > 0 and row['high'] >= tp_price:
                    exit_reason = "TP"
                    exit_p = tp_price
                elif row.get('exit_long', False):
                    exit_reason = "Signal"
                    exit_p = row['close']
                    
                if exit_reason:
                    # Slippage & comisiones
                    exit_p = exit_p * (1 - self.slippage_pct)
                    revenue = position * exit_p
                    commission = revenue * self.commission_pct
                    
                    pnl = revenue - (position * entry_price) - commission
                    capital += revenue - commission
                    
                    trades.append({
                        'entry_time': current_trade['entry_time'],
                        'exit_time': timestamp,
                        'side': 'long',
                        'entry_price': entry_price,
                        'exit_price': exit_p,
                        'quantity': position,
                        'pnl': pnl,
                        'exit_reason': exit_reason,
                        'portfolio_value': capital
                    })
                    position = 0
            
            elif position < 0:
                # Actualizar Trailing Stop Loss (Inverso: SL baja cuando precio baja)
                current_atr = row.get('ATR', None)
                sl_price = self.strategy.risk_manager.update_trailing_sl(
                    current_sl=sl_price, 
                    current_price=row['close'], 
                    current_high=row['high'], 
                    current_low=row['low'], 
                    current_atr=current_atr, 
                    side="short"
                )
                
                exit_reason = None
                exit_p = row['close']
                
                if sl_price is not None and sl_price > 0 and row['high'] >= sl_price:
                    exit_reason = "SL"
                    exit_p = sl_price
                elif tp_price is not None and tp_price > 0 and row['low'] <= tp_price:
                    exit_reason = "TP"
                    exit_p = tp_price
                elif row.get('exit_short', False):
                    exit_reason = "Signal"
                    exit_p = row['close']
                    
                if exit_reason:
                    # Slippage & comisiones (compramos más caro al salir de un short)
                    exit_p = exit_p * (1 + self.slippage_pct)
                    cost = abs(position) * exit_p
                    commission = cost * self.commission_pct
                    
                    revenue = abs(position) * entry_price
                    pnl = revenue - cost - commission
                    capital -= cost + commission
                    
                    trades.append({
                        'entry_time': current_trade['entry_time'],
                        'exit_time': timestamp,
                        'side': 'short',
                        'entry_price': entry_price,
                        'exit_price': exit_p,
                        'quantity': abs(position),
                        'pnl': pnl,
                        'exit_reason': exit_reason,
                        'portfolio_value': capital
                    })
                    position = 0
            
            # Revisar condiciones de entrada si NO estamos en posición
            if position == 0:
                if row.get('entry_long', False):
                    # Calcular SL/TP
                    sl, tp = self.strategy.risk_manager.compute_sl_tp(df, i, side="long")
                    qty = self.strategy.risk_manager.compute_position_size(capital, row['close'], sl)
                    
                    # Asumir entrada
                    entry_p = row['close'] * (1 + self.slippage_pct)
                    cost = qty * entry_p
                    commission = cost * self.commission_pct
                    
                    if cost + commission > capital:
                        max_cost_allowed = capital / (1 + self.commission_pct)
                        qty = max_cost_allowed / entry_p
                        cost = qty * entry_p
                        commission = cost * self.commission_pct
                        
                    if qty > 0.00001: # Tamaño mínimo de operación
                        capital -= (cost + commission)
                        position = qty
                        entry_price = entry_p
                        sl_price = sl
                        tp_price = tp
                        current_trade = {'entry_time': timestamp}
                        
                elif row.get('entry_short', False):
                    sl, tp = self.strategy.risk_manager.compute_sl_tp(df, i, side="short")
                    qty = self.strategy.risk_manager.compute_position_size(capital, row['close'], sl)
                    
                    entry_p = row['close'] * (1 - self.slippage_pct)
                    revenue = qty * entry_p
                    commission = revenue * self.commission_pct
                    
                    # En short vendemos y obtenemos revenue, pero retenemos capital como margen.
                    # Asumiremos apalancamiento 1x: capital no aumenta hasta cerrar.
                    # Mismo control de tamaño que en long
                    if revenue + commission > capital:
                        max_rev_allowed = capital / (1 + self.commission_pct)
                        qty = max_rev_allowed / entry_p
                        revenue = qty * entry_p
                        commission = revenue * self.commission_pct
                        
                    if qty > 0.00001:
                        capital += revenue - commission
                        position = -qty
                        entry_price = entry_p
                        sl_price = sl
                        tp_price = tp
                        current_trade = {'entry_time': timestamp}

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
            "raw_data": df
        }
        
        trade_metrics = calculate_metrics(trades_df, self.initial_capital)
        eq_metrics = calculate_equity_curve_metrics(equity_df['equity'])
        
        # Combinar métricas
        run_results.update(trade_metrics)
        run_results.update(eq_metrics)
        
        return run_results
