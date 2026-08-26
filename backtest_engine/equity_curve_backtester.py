import pandas as pd
import numpy as np
from datetime import datetime
import uuid
import json
from strategy_engine.base_strategy import BaseStrategy
from .metrics import calculate_metrics, calculate_equity_curve_metrics

class EquityCurveBacktester:
    """
    Motor de backtest especializado en "Equity Curve Trading".
    Ejecuta dos simulaciones en paralelo:
    1. Virtual: Ejecuta la estrategia normalmente y rastrea el Drawdown (basado en PnL cerrado).
    2. Real: Ejecuta operaciones solo cuando el DD Virtual alcanza un umbral inicial,
             y deja de tomar nuevas operaciones cuando el DD Virtual se recupera.
    """
    def __init__(self, strategy: BaseStrategy, initial_capital: float = 10000.0, commission_pct: float = 0.1, slippage_pct: float = 0.05):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct / 100.0
        self.slippage_pct = slippage_pct / 100.0

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
        
        # 3. Estado de Simulación Virtual
        v_capital = self.initial_capital
        v_position = 0
        v_entry_price = 0
        v_sl_price = 0
        v_tp_price = 0
        v_high_watermark = self.initial_capital
        v_current_dd = 0.0
        v_trades = []
        v_equity_curve = []
        v_current_trade = {}
        v_consecutive_losers = 0
        
        # 4. Estado de Simulación Real
        r_capital = self.initial_capital
        r_position = 0
        r_entry_price = 0
        r_sl_price = 0
        r_tp_price = 0
        r_trades = []
        r_equity_curve = []
        r_current_trade = {}
        
        is_dd_trading_active = False
        is_cl_trading_active = False
        is_real_trading_active = False # Bandera de control
        cl_activation_v_capital = None
        dd_activation_v_capital = None
        
        for i in range(len(df)):
            row = df.iloc[i]
            timestamp = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else i
            
            # ---------------------------------------------------------
            # FASE 1: GESTIÓN DE SALIDAS (VIRTUAL Y REAL)
            # ---------------------------------------------------------
            
            # --- Salida Virtual ---
            if v_position != 0:
                side = "long" if v_position > 0 else "short"
                current_atr = row.get('ATR', None)
                v_sl_price = self.strategy.risk_manager.update_trailing_sl(
                    current_sl=v_sl_price, current_price=row['close'], 
                    current_high=row['high'], current_low=row['low'], 
                    current_atr=current_atr, side=side, entry_price=v_entry_price
                )
                
                exit_reason = None
                exit_p = row['close']
                
                if side == "long":
                    if v_sl_price is not None and row['low'] <= v_sl_price:
                        exit_reason = "SL"; exit_p = v_sl_price
                    elif v_tp_price is not None and v_tp_price > 0 and row['high'] >= v_tp_price:
                        exit_reason = "TP"; exit_p = v_tp_price
                    elif row.get('exit_long', False):
                        exit_reason = "Signal"; exit_p = row['close']
                else:
                    if v_sl_price is not None and v_sl_price > 0 and row['high'] >= v_sl_price:
                        exit_reason = "SL"; exit_p = v_sl_price
                    elif v_tp_price is not None and v_tp_price > 0 and row['low'] <= v_tp_price:
                        exit_reason = "TP"; exit_p = v_tp_price
                    elif row.get('exit_short', False):
                        exit_reason = "Signal"; exit_p = row['close']
                        
                if exit_reason:
                    # Cerrar posición virtual
                    if side == "long":
                        exit_p = exit_p * (1 - self.slippage_pct)
                        revenue = v_position * exit_p
                        commission = revenue * self.commission_pct
                        pnl = revenue - (v_position * v_entry_price) - commission
                        v_capital += revenue - commission
                    else:
                        exit_p = exit_p * (1 + self.slippage_pct)
                        cost = abs(v_position) * exit_p
                        commission = cost * self.commission_pct
                        revenue = abs(v_position) * v_entry_price
                        pnl = revenue - cost - commission
                        v_capital -= cost + commission
                        
                    v_trades.append({
                        'entry_time': v_current_trade.get('entry_time', timestamp),
                        'exit_time': timestamp,
                        'side': side,
                        'entry_price': v_entry_price,
                        'exit_price': exit_p,
                        'quantity': abs(v_position),
                        'pnl': pnl,
                        'exit_reason': exit_reason,
                        'portfolio_value': v_capital,
                        'real_status_marker': '', # Se actualizará abajo
                        'is_real_trade': False # Se actualizará abajo
                    })
                    v_position = 0
                    
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
                    is_real_trading_active = False
                    if dd_enabled and is_dd_trading_active:
                        is_real_trading_active = True
                    if cl_enabled and is_cl_trading_active:
                        is_real_trading_active = True
                        
                    if not old_real_active and is_real_trading_active:
                        v_trades[-1]['real_status_marker'] = 'START'
                    elif old_real_active and not is_real_trading_active:
                        v_trades[-1]['real_status_marker'] = 'STOP'
                    
                    v_trades[-1]['is_real_trade'] = old_real_active

            # --- Salida Real ---
            if r_position != 0:
                side = "long" if r_position > 0 else "short"
                current_atr = row.get('ATR', None)
                r_sl_price = self.strategy.risk_manager.update_trailing_sl(
                    current_sl=r_sl_price, current_price=row['close'], 
                    current_high=row['high'], current_low=row['low'], 
                    current_atr=current_atr, side=side, entry_price=r_entry_price
                )
                
                exit_reason = None
                exit_p = row['close']
                
                if side == "long":
                    if r_sl_price is not None and row['low'] <= r_sl_price:
                        exit_reason = "SL"; exit_p = r_sl_price
                    elif r_tp_price is not None and r_tp_price > 0 and row['high'] >= r_tp_price:
                        exit_reason = "TP"; exit_p = r_tp_price
                    elif row.get('exit_long', False):
                        exit_reason = "Signal"; exit_p = row['close']
                else:
                    if r_sl_price is not None and r_sl_price > 0 and row['high'] >= r_sl_price:
                        exit_reason = "SL"; exit_p = r_sl_price
                    elif r_tp_price is not None and r_tp_price > 0 and row['low'] <= r_tp_price:
                        exit_reason = "TP"; exit_p = r_tp_price
                    elif row.get('exit_short', False):
                        exit_reason = "Signal"; exit_p = row['close']
                        
                if exit_reason:
                    if side == "long":
                        exit_p = exit_p * (1 - self.slippage_pct)
                        revenue = r_position * exit_p
                        commission = revenue * self.commission_pct
                        pnl = revenue - (r_position * r_entry_price) - commission
                        r_capital += revenue - commission
                    else:
                        exit_p = exit_p * (1 + self.slippage_pct)
                        cost = abs(r_position) * exit_p
                        commission = cost * self.commission_pct
                        revenue = abs(r_position) * r_entry_price
                        pnl = revenue - cost - commission
                        r_capital -= cost + commission
                        
                    r_trades.append({
                        'entry_time': r_current_trade.get('entry_time', timestamp),
                        'exit_time': timestamp,
                        'side': side,
                        'entry_price': r_entry_price,
                        'exit_price': exit_p,
                        'quantity': abs(r_position),
                        'pnl': pnl,
                        'exit_reason': exit_reason,
                        'portfolio_value': r_capital
                    })
                    r_position = 0


            # ---------------------------------------------------------
            # FASE 2: GESTIÓN DE ENTRADAS (VIRTUAL Y REAL)
            # ---------------------------------------------------------
            
            # --- Entrada Virtual y Real (Sincronizada) ---
            if v_position == 0:
                if row.get('entry_long', False) or row.get('entry_short', False):
                    side = "long" if row.get('entry_long', False) else "short"
                    sl, tp = self.strategy.risk_manager.compute_sl_tp(df, i, side=side)
                    
                    # 1. Simular Entrada Virtual
                    v_qty = self.strategy.risk_manager.compute_position_size(v_capital, row['close'], sl)
                    if side == "long":
                        entry_p = row['close'] * (1 + self.slippage_pct)
                        max_qty = v_capital / (entry_p * (1 + self.commission_pct))
                        v_qty = min(v_qty, max_qty)
                        cost = v_qty * entry_p
                        commission = cost * self.commission_pct
                        
                        if v_capital >= (cost + commission) - 1e-6 and v_qty > 1e-6:
                            v_position = v_qty
                            v_entry_price = entry_p
                            v_sl_price = sl; v_tp_price = tp
                            v_capital -= (cost + commission)
                            v_current_trade = {'entry_time': timestamp, 'side': side, 'quantity': v_qty}
                    else:
                        entry_p = row['close'] * (1 - self.slippage_pct)
                        max_qty = v_capital / (entry_p * (1 + self.commission_pct))
                        v_qty = min(v_qty, max_qty)
                        revenue = v_qty * entry_p
                        commission = revenue * self.commission_pct
                        
                        if v_capital >= (revenue + commission) - 1e-6 and v_qty > 1e-6:
                            v_capital += revenue - commission
                            v_position = -v_qty
                            v_entry_price = entry_p
                            v_sl_price = sl; v_tp_price = tp
                            v_current_trade = {'entry_time': timestamp, 'side': side, 'quantity': v_qty}

                    # 2. Simular Entrada Real (Sincronización de Señal)
                    # El usuario indicó: "DEBE ESPERAR LA PROXIMA SEÑAL PARA EMPEZAR A OPERAR"
                    # Ya que solo procesamos esta sección cuando v_position == 0, esto garantiza
                    # que es una *nueva* señal (virtual acaba de abrirla arriba).
                    if is_real_trading_active and r_position == 0:
                        r_qty = self.strategy.risk_manager.compute_position_size(r_capital, row['close'], sl)
                        
                        if side == "long":
                            entry_p = row['close'] * (1 + self.slippage_pct)
                            max_qty = r_capital / (entry_p * (1 + self.commission_pct))
                            r_qty = min(r_qty, max_qty)
                            cost = r_qty * entry_p
                            commission = cost * self.commission_pct
                            
                            if r_capital >= (cost + commission) - 1e-6 and r_qty > 1e-6:
                                r_position = r_qty
                                r_entry_price = entry_p
                                r_sl_price = sl; r_tp_price = tp
                                r_capital -= (cost + commission)
                                r_current_trade = {'entry_time': timestamp, 'side': side, 'quantity': r_qty}
                        else:
                            entry_p = row['close'] * (1 - self.slippage_pct)
                            max_qty = r_capital / (entry_p * (1 + self.commission_pct))
                            r_qty = min(r_qty, max_qty)
                            revenue = r_qty * entry_p
                            commission = revenue * self.commission_pct
                            
                            if r_capital >= (revenue + commission) - 1e-6 and r_qty > 1e-6:
                                r_capital += revenue - commission
                                r_position = -r_qty
                                r_entry_price = entry_p
                                r_sl_price = sl; r_tp_price = tp
                                r_current_trade = {'entry_time': timestamp, 'side': side, 'quantity': r_qty}

            # ---------------------------------------------------------
            # REGISTRO DE CURVAS DE CAPITAL (MARK-TO-MARKET FLOTANTE)
            # ---------------------------------------------------------
            # La fórmula v_capital + (v_position * row['close']) funciona correctamente tanto para LONG como para SHORT
            v_current_value = v_capital + (v_position * row['close'])
            v_equity_curve.append({'timestamp': timestamp, 'equity': v_current_value})
            
            r_current_value = r_capital + (r_position * row['close'])
            r_equity_curve.append({'timestamp': timestamp, 'equity': r_current_value})

        # --- Empaquetar Resultados Finales (Real) y Virtual ---
        r_trades_df = pd.DataFrame(r_trades)
        r_equity_df = pd.DataFrame(r_equity_curve).set_index('timestamp')
        
        v_trades_df = pd.DataFrame(v_trades)
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
            
            # Incluimos los resultados Virtuales por si la UI desea dibujarlos en fondo
            "virtual_trades": v_trades_df,
            "virtual_equity_curve": v_equity_df,
            
            "raw_data": df
        }
        
        # Métricas de la Estrategia Real
        trade_metrics = calculate_metrics(r_trades_df, self.initial_capital)
        eq_metrics = calculate_equity_curve_metrics(r_equity_df['equity'])
        
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
