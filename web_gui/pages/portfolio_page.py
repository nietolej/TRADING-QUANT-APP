import os
import json
import asyncio
import glob
from datetime import datetime, timezone
import pandas as pd
import numpy as np
from nicegui import ui, run

from strategy_engine.base_strategy import BaseStrategy
from strategy_engine.risk_management import RiskManager
from backtest_engine.backtester import Backtester
from backtest_engine.metrics import calculate_metrics, calculate_equity_curve_metrics
from data_layer.market_data import MarketDataManager, normalize_timeframe
from data_layer.storage import SessionLocal, BacktestRun, OHLCV
from data_layer.export_utils import format_dt_display, format_date_display, parse_flexible_date


def _get_currency_symbol(curr_str: str) -> str:
    """Devuelve el símbolo corto de moneda para formatear números."""
    c = curr_str.upper().strip()
    if 'BTC' in c:
        return '₿'
    elif 'ETH' in c:
        return 'Ξ'
    elif 'SOL' in c:
        return 'SOL '
    elif 'AVAX' in c:
        return 'AVAX '
    elif 'BNB' in c:
        return 'BNB '
    elif 'EUR' in c:
        return '€'
    elif 'USDT' in c or 'USD' in c or 'USDC' in c:
        return '$'
    return f"{c} "


def _sync_run_portfolio_backtest(
    portfolio_items,
    total_capital,
    start_dt,
    end_dt,
    comm_pct,
    slip_pct,
    capital_currency='BTC (Satoshis / Base)',
    hold_benchmark='btc',
    account_mode='spot_cash'
):
    """
    Ejecuta el backtest combinado de múltiples estrategias con normalización precisa
    multi-divisa (USDT, BTC, ETH, etc.), genera la curva de Benchmark HOLD y extrae
    series de precios alineadas para medición dinámica.
    """
    db = SessionLocal()
    try:
        market_mgr = MarketDataManager(db)
        
        # 1. Obtener y alinear datos de mercado para cada estrategia
        strat_data_dict = {}
        all_unique_assets = set(['USDT', 'BTC', 'ETH'])

        # Extraer activo de capital
        cap_curr_clean = capital_currency.split(' ')[0].strip().upper()
        all_unique_assets.add(cap_curr_clean)

        for idx, item in enumerate(portfolio_items):
            symbol = item['symbol']
            timeframe = item['timeframe']
            s_parts = symbol.split('/')
            if len(s_parts) == 2:
                all_unique_assets.add(s_parts[0].strip().upper())
                all_unique_assets.add(s_parts[1].strip().upper())

            df = market_mgr.get_data(symbol, timeframe, start_dt, end_dt)
            if df.empty:
                market_mgr.update_historical_data(symbol, timeframe, start_dt, end_dt)
                df = market_mgr.get_data(symbol, timeframe, start_dt, end_dt)
            if not df.empty:
                strat_data_dict[idx] = df

        if not strat_data_dict:
            return {'error': "No se pudieron obtener datos históricos para las estrategias configuradas en el rango de fechas."}

        # 2. Descargar series de precios contra USDT para todos los activos únicos involucrados
        price_series_df_dict = {}
        for asset in all_unique_assets:
            if asset in ['USDT', 'USD', 'FDUSD', 'USDC']:
                continue
            df_p = market_mgr.get_data(f"{asset}/USDT", '1d', start_dt, end_dt)
            if df_p.empty:
                market_mgr.update_historical_data(f"{asset}/USDT", '1d', start_dt, end_dt)
                df_p = market_mgr.get_data(f"{asset}/USDT", '1d', start_dt, end_dt)
            if not df_p.empty:
                price_series_df_dict[asset] = df_p['close']

        # Encontrar rango de fechas común
        # Usar el índice de la primera estrategia con datos como referencia base
        first_df = list(strat_data_dict.values())[0]
        common_index = first_df.index
        for df_s in strat_data_dict.values():
            common_index = common_index.union(df_s.index)
        common_index = common_index.sort_values()

        # Construir tabla alineada de precios contra USDT
        price_matrix = pd.DataFrame(index=common_index)
        price_matrix['USDT'] = 1.0
        price_matrix['USD'] = 1.0
        price_matrix['USDC'] = 1.0
        price_matrix['FDUSD'] = 1.0

        for asset, s_close in price_series_df_dict.items():
            price_matrix[asset] = s_close.reindex(common_index).ffill().bfill()

        # Si no hay precio de un activo, fallback a 1.0
        for asset in all_unique_assets:
            if asset not in price_matrix.columns:
                price_matrix[asset] = 1.0
            else:
                price_matrix[asset] = price_matrix[asset].ffill().bfill().fillna(1.0)

        # 3. Determinar Capital Total Inicial en USDT (Referencia Universal)
        p_cap_t0 = float(price_matrix[cap_curr_clean].iloc[0]) if cap_curr_clean in price_matrix.columns else 1.0
        total_capital_usdt = float(total_capital) * p_cap_t0

        # 4. Simulación de cada estrategia individual
        equity_series_dict_usdt = {}
        all_trades = []
        strategy_breakdown = []

        for idx, item in enumerate(portfolio_items):
            if idx not in strat_data_dict:
                continue

            strategy_path = item['strategy_path']
            symbol = item['symbol']
            timeframe = item['timeframe']
            weight_pct = float(item.get('weight_pct', 0.0))
            if weight_pct <= 0:
                continue

            s_parts = symbol.split('/')
            b_asset = s_parts[0].strip().upper() if len(s_parts) == 2 else 'BASE'
            q_asset = s_parts[1].strip().upper() if len(s_parts) == 2 else 'QUOTE'

            # Capital asignado a esta estrategia en USDT
            strat_cap_usdt = total_capital_usdt * (weight_pct / 100.0)

            # Precio del activo Quote contra USDT en t0
            p_quote_t0 = float(price_matrix[q_asset].iloc[0]) if q_asset in price_matrix.columns else 1.0
            if p_quote_t0 <= 0:
                p_quote_t0 = 1.0

            # Capital con el que corre el backtest (en la moneda quote nativa del par)
            strat_cap_native = strat_cap_usdt / p_quote_t0

            df = strat_data_dict[idx]

            custom_params = item.get('custom_params', {})
            strategy = BaseStrategy(strategy_path, custom_parameters=custom_params)
            strategy.symbol = symbol
            strategy.timeframe = timeframe

            if 'risk_management' not in strategy.config:
                strategy.config['risk_management'] = {}
            strategy.config['risk_management']['position_sizing'] = {'method': 'compounding', 'value': 100.0}
            strategy.risk_manager = RiskManager(strategy.config['risk_management'])

            backtester = Backtester(
                strategy,
                initial_capital=strat_cap_native,
                commission_pct=comm_pct,
                slippage_pct=slip_pct,
                account_mode=account_mode
            )
            results = backtester.run(df)

            eq_curve = results.get('equity_curve')
            name_label = f"#{idx+1}: {os.path.basename(strategy_path)} ({symbol} {timeframe})"
            
            if eq_curve is not None and not eq_curve.empty:
                if 'timestamp' in eq_curve.columns:
                    eq_curve = eq_curve.set_index('timestamp')
                eq_curve.index = pd.to_datetime(eq_curve.index)
                
                # Serie nativa en quote asset
                s_native = eq_curve['equity'].reindex(common_index).ffill().bfill()
                
                # Convertir a USDT multiplicando por el precio de la divisa Quote en cada timestamp
                p_q_series = price_matrix[q_asset]
                s_usdt = s_native * p_q_series
                equity_series_dict_usdt[name_label] = s_usdt

            trades = results.get('trades')
            if trades is not None and not trades.empty:
                trades_copy = trades.copy()
                trades_copy['strategy'] = name_label
                all_trades.append(trades_copy)

            strat_cagr = results.get('cagr', 0.0)
            strat_max_dd = results.get('max_drawdown_pct', 0.0)
            strat_final_eq_native = results.get('final_equity', strat_cap_native)
            p_quote_end = float(price_matrix[q_asset].iloc[-1]) if q_asset in price_matrix.columns else 1.0
            strat_final_eq_usdt = strat_final_eq_native * p_quote_end
            strat_pnl_pct = ((strat_final_eq_usdt - strat_cap_usdt) / strat_cap_usdt * 100.0) if strat_cap_usdt > 0 else 0.0

            strategy_breakdown.append({
                'name': name_label,
                'weight_pct': weight_pct,
                'allocated_cap': strat_cap_usdt,
                'final_cap': strat_final_eq_usdt,
                'pnl_pct': strat_pnl_pct,
                'cagr': strat_cagr,
                'max_dd': strat_max_dd,
                'trades_count': len(trades) if trades is not None else 0
            })

        if not equity_series_dict_usdt:
            return {'error': "No se pudieron obtener datos históricos o generar equidad para las estrategias del portafolio."}

        combined_df = pd.DataFrame(equity_series_dict_usdt).ffill().bfill()
        portfolio_equity = combined_df.sum(axis=1)

        port_eq_metrics = calculate_equity_curve_metrics(portfolio_equity)

        # 5. Construcción de la Curva Benchmark HOLD de Referencia en USDT
        if hold_benchmark == 'btc':
            p_btc_series = price_matrix['BTC']
            p_btc_t0 = float(p_btc_series.iloc[0])
            hold_equity = (p_btc_series / p_btc_t0) * total_capital_usdt if p_btc_t0 > 0 else pd.Series(total_capital_usdt, index=common_index)
        elif hold_benchmark == 'eth':
            p_eth_series = price_matrix['ETH']
            p_eth_t0 = float(p_eth_series.iloc[0])
            hold_equity = (p_eth_series / p_eth_t0) * total_capital_usdt if p_eth_t0 > 0 else pd.Series(total_capital_usdt, index=common_index)
        elif hold_benchmark == 'weighted':
            hold_series_dict = {}
            for idx, item in enumerate(portfolio_items):
                symbol = item['symbol']
                s_parts = symbol.split('/')
                b_asset = s_parts[0].strip().upper() if len(s_parts) == 2 else 'BTC'
                weight_pct = float(item.get('weight_pct', 0.0))
                if weight_pct <= 0:
                    continue
                alloc_usdt_item = total_capital_usdt * (weight_pct / 100.0)
                p_b_series = price_matrix[b_asset] if b_asset in price_matrix.columns else price_matrix['BTC']
                p_b_t0 = float(p_b_series.iloc[0])
                if p_b_t0 > 0:
                    hold_series_dict[f"hold_{idx}"] = (p_b_series / p_b_t0) * alloc_usdt_item
            if hold_series_dict:
                hold_equity = pd.DataFrame(hold_series_dict).ffill().bfill().sum(axis=1)
            else:
                hold_equity = pd.Series(total_capital_usdt, index=common_index)
        elif hold_benchmark == 'first' and portfolio_items:
            first_sym = portfolio_items[0]['symbol']
            first_base = first_sym.split('/')[0].strip().upper()
            p_first_series = price_matrix[first_base] if first_base in price_matrix.columns else price_matrix['BTC']
            p_first_t0 = float(p_first_series.iloc[0])
            hold_equity = (p_first_series / p_first_t0) * total_capital_usdt if p_first_t0 > 0 else pd.Series(total_capital_usdt, index=common_index)
        else: # cash
            hold_equity = pd.Series(total_capital_usdt, index=common_index)

        hold_equity = hold_equity.reindex(common_index).ffill().bfill()
        hold_final_equity = float(hold_equity.iloc[-1]) if not hold_equity.empty else total_capital_usdt
        hold_pnl_pct = ((hold_final_equity - total_capital_usdt) / total_capital_usdt * 100.0) if total_capital_usdt > 0 else 0.0
        hold_metrics = calculate_equity_curve_metrics(hold_equity)
        hold_cagr = hold_metrics.get('cagr', 0.0)
        hold_max_dd = hold_metrics.get('max_drawdown_pct', 0.0)

        # Drawdown del Hold (%)
        hold_roll_max = hold_equity.cummax()
        hold_drawdown_series = (hold_equity - hold_roll_max) / hold_roll_max * 100.0

        # PnL y Alpha
        port_final_eq = float(portfolio_equity.iloc[-1]) if not portfolio_equity.empty else total_capital_usdt
        port_pnl_pct = ((port_final_eq - total_capital_usdt) / total_capital_usdt * 100.0) if total_capital_usdt > 0 else 0.0
        alpha_pct = port_pnl_pct - hold_pnl_pct

        combined_trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
        port_trade_metrics = calculate_metrics(combined_trades, total_capital_usdt) if not combined_trades.empty else {}

        # Drawdown serie (%)
        roll_max = portfolio_equity.cummax()
        drawdown_series = (portfolio_equity - roll_max) / roll_max * 100.0

        # Drawdown individual (%)
        individual_drawdowns = {}
        for col in combined_df.columns:
            s_series = combined_df[col]
            s_roll_max = s_series.cummax()
            s_dd = (s_series - s_roll_max) / s_roll_max * 100.0
            individual_drawdowns[col] = s_dd.round(2).tolist()

        dates_str = [format_date_display(d) for d in common_index]

        # 6. Serializar series de precios para medición multi-divisa en el frontend
        serialized_prices = {}
        for col in price_matrix.columns:
            serialized_prices[col] = price_matrix[col].round(6).tolist()

        total_trades_count = len(combined_trades)
        winning_trades_count = int(port_trade_metrics.get('winning_trades', 0))
        losing_trades_count = int(port_trade_metrics.get('losing_trades', 0))
        if winning_trades_count == 0 and losing_trades_count == 0 and not combined_trades.empty:
            pnl_col = 'pnl' if 'pnl' in combined_trades.columns else 'net_pnl' if 'net_pnl' in combined_trades.columns else None
            if pnl_col:
                winning_trades_count = int((combined_trades[pnl_col] > 0).sum())
                losing_trades_count = int((combined_trades[pnl_col] < 0).sum())

        win_rate_val = port_trade_metrics.get('percent_profitable', 0.0)
        if win_rate_val == 0.0 and total_trades_count > 0:
            win_rate_val = (winning_trades_count / total_trades_count) * 100.0

        formatted_trades = []
        if not combined_trades.empty:
            sort_col = None
            for candidate in ['entry_time', 'Entry Timestamp', 'timestamp', 'date', 'entry_date']:
                if candidate in combined_trades.columns:
                    sort_col = candidate
                    break

            if sort_col:
                try:
                    combined_trades[sort_col] = pd.to_datetime(combined_trades[sort_col])
                    combined_trades = combined_trades.sort_values(by=sort_col, ascending=True)
                except Exception:
                    pass

            for trade_idx, tr in combined_trades.iterrows():
                e_time = tr.get('entry_time') or tr.get('Entry Timestamp') or tr.get('timestamp') or "N/A"
                e_time_str = format_dt_display(e_time)

                x_time = tr.get('exit_time') or tr.get('Exit Timestamp') or "N/A"
                x_time_str = format_dt_display(x_time)

                pnl_val = float(tr.get('pnl', 0.0) or 0.0)
                entry_p = float(tr.get('entry_price', 0.0) or 0.0)
                exit_p = float(tr.get('exit_price', 0.0) or 0.0)
                side_val = str(tr.get('side', 'LONG')).upper()

                if 'pnl_pct' in tr and pd.notna(tr['pnl_pct']):
                    pnl_pct_val = float(tr['pnl_pct'])
                elif entry_p > 0:
                    if side_val == 'LONG':
                        pnl_pct_val = ((exit_p - entry_p) / entry_p) * 100.0
                    else:
                        pnl_pct_val = ((entry_p - exit_p) / entry_p) * 100.0
                else:
                    pnl_pct_val = 0.0

                pnl_pct_str = f"{'+' if pnl_pct_val > 0 else ''}{pnl_pct_val:.2f}%"

                formatted_trades.append({
                    'trade_id': trade_idx,
                    'entry_time': e_time_str,
                    'exit_time': x_time_str,
                    'strategy': str(tr.get('strategy', 'N/A')),
                    'side': side_val,
                    'entry_price': f"${entry_p:,.4f}" if entry_p > 0 else "$0.00",
                    'exit_price': f"${exit_p:,.4f}" if exit_p > 0 else "$0.00",
                    'pnl_raw': pnl_val,
                    'pnl_str': f"{'+' if pnl_val > 0 else ''}${pnl_val:,.2f}",
                    'pnl_pct_raw': pnl_pct_val,
                    'pnl_pct_str': pnl_pct_str,
                    'reason': str(tr.get('exit_reason', 'Señal'))
                })

        return {
            'portfolio_equity': portfolio_equity.round(2).tolist(),
            'individual_equity': {col: combined_df[col].round(2).tolist() for col in combined_df.columns},
            'hold_equity': hold_equity.round(2).tolist(),
            'drawdown_series': drawdown_series.round(2).tolist(),
            'individual_drawdowns': individual_drawdowns,
            'hold_drawdown_series': hold_drawdown_series.round(2).tolist(),
            'dates': dates_str,
            'price_series': serialized_prices,
            'cagr': port_eq_metrics.get('cagr', 0.0),
            'max_drawdown_pct': port_eq_metrics.get('max_drawdown_pct', 0.0),
            'sharpe_ratio': port_eq_metrics.get('sharpe_ratio', 0.0),
            'profit_factor': port_trade_metrics.get('profit_factor', 0.0),
            'total_trades': total_trades_count,
            'winning_trades': winning_trades_count,
            'losing_trades': losing_trades_count,
            'win_rate': win_rate_val,
            'chronological_trades': formatted_trades,
            'strategy_breakdown': strategy_breakdown,
            'initial_capital': total_capital,
            'initial_capital_usdt': total_capital_usdt,
            'final_equity': port_final_eq,
            'port_pnl_pct': port_pnl_pct,
            'hold_final_equity': hold_final_equity,
            'hold_pnl_pct': hold_pnl_pct,
            'hold_cagr': hold_cagr,
            'hold_max_dd': hold_max_dd,
            'alpha_pct': alpha_pct,
            'capital_currency': capital_currency,
            'hold_benchmark': hold_benchmark
        }
    finally:
        db.close()


def render_portfolio_page():
    """
    Renderiza la página independiente del Simulador de Portafolio Cuantitativo.
    """
    _current_dir = os.path.dirname(os.path.abspath(__file__))
    _base_dir = os.path.abspath(os.path.join(_current_dir, '..', '..'))
    _strategies_dir = os.path.join(_base_dir, 'config', 'strategies')

    def load_strategies_dict():
        found = {}
        if os.path.exists(_strategies_dir):
            for f in glob.glob(os.path.join(_strategies_dir, '*.yaml')):
                found[os.path.basename(f)] = os.path.abspath(f)
        return found

    strategies = load_strategies_dict()
    POPULAR_CRYPTO_SYMBOLS = [
        'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'AVAX/USDT', 'BNB/USDT', 'BNB/BTC', 'ETH/BTC',
        'XRP/USDT', 'ADA/USDT', 'DOGE/USDT', 'DOT/USDT', 'LINK/USDT',
        'NEAR/USDT', 'MATIC/USDT', 'SUI/USDT', 'APT/USDT', 'PEPE/USDT', 'SHIB/USDT',
        'LTC/USDT', 'TRX/USDT', 'ATOM/USDT', 'UNI/USDT', 'ICP/USDT', 'FIL/USDT',
        'XMR/USDT', 'ETC/USDT', 'BCH/USDT', 'INJ/USDT', 'TIA/USDT', 'RENDER/USDT',
        'FET/USDT', 'TAO/USDT', 'RUNE/USDT', 'FTM/USDT', 'KAS/USDT', 'AR/USDT',
        'STX/USDT', 'OP/USDT', 'ARB/USDT', 'IMX/USDT', 'GRT/USDT', 'GALA/USDT'
    ]

    def _get_initial_symbols():
        db = SessionLocal()
        try:
            db_symbols = [r[0] for r in db.query(OHLCV.symbol).distinct().all()]
        except Exception:
            db_symbols = []
        finally:
            db.close()
        return sorted(list(set(POPULAR_CRYPTO_SYMBOLS + db_symbols)))

    available_symbols = _get_initial_symbols()

    with ui.column().classes('w-full q-pa-md bg-[#0a0e17] text-slate-100 min-h-screen'):

        # ── Header Principal ──
        with ui.card().classes('w-full bg-slate-900/90 text-white rounded-2xl border border-slate-800 p-4 shadow-xl mb-4'):
            with ui.row().classes('items-center justify-between w-full'):
                with ui.row().classes('items-center gap-3'):
                    with ui.row().classes('items-center justify-center w-11 h-11 rounded-xl bg-emerald-500/15 border border-emerald-500/30 text-emerald-400'):
                        ui.icon('pie_chart', size='1.8rem')
                    with ui.column().classes('gap-0'):
                        ui.label('Simulador de Portafolio & Combinación de Estrategias').classes('text-xl font-black tracking-tight text-white leading-tight')
                        ui.label('Combina múltiples estrategias cuantitativas y activos con ponderaciones personalizadas, comparativa de HOLD y medición multi-divisa.').classes('text-slate-400 text-xs')
                
                with ui.row().classes('items-center gap-2'):
                    ui.badge('Multi-Estrategia & Multi-Divisa', color='emerald-8').classes('font-bold text-xs px-3 py-1')

        # ── Configuración Global del Portafolio ──
        with ui.card().classes('w-full p-5 mb-4 rounded-2xl border border-slate-800 bg-slate-900/80 shadow-xl backdrop-blur'):
            with ui.row().classes('items-center justify-between w-full mb-3 flex-wrap gap-2'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('tune', size='1.2rem').classes('text-amber-400')
                    ui.label('1. Configuración Global del Portafolio').classes('font-bold text-sm text-slate-200 uppercase tracking-wider')

                with ui.row().classes('items-center gap-2'):
                    ui.badge('Lógica Multi-Divisa y HOLD', color='slate-8').classes('text-[11px] text-slate-300 font-mono')

            # Fila 1: Capital + Moneda del Capital + Benchmark HOLD + Modo de Cuenta
            with ui.row().classes('w-full gap-4 items-end flex-wrap mb-3'):
                with ui.column().classes('flex-1 min-w-[180px] gap-0'):
                    lbl_cap_header = ui.label('Capital Total Inicial (₿ BTC)').classes('text-xs text-slate-400 mb-1')
                    port_capital_input = ui.number('Capital', value=1.0, step=0.1, min=0.0001).classes('w-full')

                with ui.column().classes('flex-1 min-w-[180px] gap-0'):
                    ui.label('Moneda del Capital (Activo Inicio)').classes('text-xs text-slate-400 mb-1')
                    port_currency_select = ui.select(
                        ['BTC (Satoshis / Base)', 'USDT (Dólares / Quote)', 'ETH', 'SOL', 'AVAX', 'USDC', 'BNB'],
                        value='BTC (Satoshis / Base)',
                        with_input=True,
                        new_value_mode='add-unique'
                    ).classes('w-full')

                    def _on_curr_change(e):
                        val = str(e.value or 'BTC')
                        curr_name = val.split(' ')[0].strip()
                        sym_sign = _get_currency_symbol(val)
                        lbl_cap_header.set_text(f"Capital Total Inicial ({sym_sign} {curr_name})")

                    port_currency_select.on_value_change(_on_curr_change)

                with ui.column().classes('flex-1 min-w-[220px] gap-0'):
                    ui.label('Benchmark HOLD de Referencia').classes('text-xs text-slate-400 mb-1')
                    port_hold_select = ui.select(
                        {
                            'btc': '₿ Hold 100% Bitcoin (BTC)',
                            'weighted': '🪙 Hold Ponderado del Portafolio',
                            'eth': '💎 Hold 100% Ethereum (ETH)',
                            'first': '🎯 Hold del Primer Activo del Portafolio',
                            'cash': '💵 100% Efectivo (Sin Hold / Quote)'
                        },
                        label='Comparar vs',
                        value='btc'
                    ).classes('w-full')

                with ui.column().classes('flex-1 min-w-[220px] gap-0'):
                    ui.label('Modo de Cuenta & Colateral').classes('text-xs text-slate-400 mb-1')
                    port_acct_mode_select = ui.select(
                        {
                            'spot_cash': '💵 Spot Efectivo (Salidas a Quote / USDT)',
                            'coin_margined_hold': '🪙 Hold Colateral + Trading (Coin-M)'
                        },
                        label='Operativa',
                        value='spot_cash'
                    ).classes('w-full')

            # Fila 2: Fechas + Comisiones + Slippage
            with ui.row().classes('w-full gap-4 items-end flex-wrap'):
                with ui.column().classes('flex-1 min-w-[170px] gap-0'):
                    ui.label('Fecha Inicio (DD/MM/AA)').classes('text-xs text-slate-400 mb-1')
                    port_start_input = ui.input('Inicio', value='01/01/17').classes('w-full')

                with ui.column().classes('flex-1 min-w-[170px] gap-0'):
                    ui.label('Fecha Fin (DD/MM/AA)').classes('text-xs text-slate-400 mb-1')
                    port_end_input = ui.input('Fin', value=format_date_display(datetime.now())).classes('w-full')

                with ui.column().classes('w-32 gap-0'):
                    ui.label('Comisión (%)').classes('text-xs text-slate-400 mb-1')
                    port_comm_input = ui.number('Comisión', value=0.1, step=0.01, min=0.0).classes('w-full')

                with ui.column().classes('w-32 gap-0'):
                    ui.label('Slippage (%)').classes('text-xs text-slate-400 mb-1')
                    port_slip_input = ui.number('Slippage', value=0.05, step=0.01, min=0.0).classes('w-full')

        # ── Estado de las Estrategias ──
        target_strat = 'ema_long.yaml' if 'ema_long.yaml' in strategies else (list(strategies.keys())[0] if strategies else '')
        first_strat = target_strat
        second_strat = target_strat

        first_sym = 'BTC/USDT' if 'BTC/USDT' in available_symbols else available_symbols[0]
        second_sym = 'ETH/USDT' if 'ETH/USDT' in available_symbols else (available_symbols[1] if len(available_symbols) > 1 else available_symbols[0])

        portfolio_state = {
            'items': [
                {'strategy': first_strat, 'symbol': first_sym, 'timeframe': '1d', 'weight_pct': 50.0},
                {'strategy': second_strat, 'symbol': second_sym, 'timeframe': '1d', 'weight_pct': 50.0}
            ]
        }

        port_items_container = ui.column().classes('w-full gap-3 mb-4')

        def get_strategy_default_params(strat_name):
            file_path = strategies.get(strat_name)
            if not file_path or not os.path.exists(file_path):
                return {}
            try:
                import yaml
                with open(file_path, 'r', encoding='utf-8') as f:
                    cfg = yaml.safe_load(f) or {}
                raw_params = cfg.get('parameters', {}) or {}
                params = {}
                for k, v in raw_params.items():
                    try:
                        params[k] = float(v) if '.' in str(v) else int(v)
                    except (ValueError, TypeError):
                        params[k] = v
                return params
            except Exception:
                return {}

        def fetch_saved_runs():
            db = SessionLocal()
            try:
                runs = db.query(BacktestRun).order_by(BacktestRun.created_at.desc()).all()
                opts = {}
                runs_map = {}
                for r in runs:
                    dt_s = format_dt_display(r.created_at) if r.created_at else ''
                    cagr_s = f"CAGR: {r.cagr:.1f}%" if r.cagr is not None else ""
                    lbl = f"[#{r.run_id[:8]}] {r.strategy_name} ({r.symbol} {r.timeframe}) {cagr_s} | {dt_s}"
                    opts[r.run_id] = lbl
                    runs_map[r.run_id] = r
                return opts, runs_map
            except Exception:
                return {}, {}
            finally:
                db.close()

        def refresh_portfolio_items_ui():
            port_items_container.clear()
            saved_opts, saved_map = fetch_saved_runs()

            with port_items_container:
                for i, item in enumerate(portfolio_state['items']):
                    if 'custom_params' not in item or not item['custom_params']:
                        item['custom_params'] = get_strategy_default_params(item['strategy']).copy()

                    with ui.card().classes('w-full p-4 rounded-2xl border border-slate-800 bg-slate-900/60 shadow-lg relative'):
                        
                        # 1. Preset Loader desde Historial
                        if saved_opts:
                            with ui.row().classes('w-full items-center gap-2 mb-2 bg-slate-950/60 p-2 rounded-xl border border-slate-800/80 flex-wrap'):
                                ui.icon('cloud_download', size='1rem').classes('text-sky-400')
                                ui.label('Cargar Configuración desde Backtest Guardado:').classes('text-xs text-slate-300 font-bold')
                                
                                def on_load_saved(e, idx=i):
                                    selected_run_id = e.value
                                    if not selected_run_id or selected_run_id not in saved_map:
                                        return
                                    run_obj = saved_map[selected_run_id]
                                    portfolio_state['items'][idx]['strategy'] = run_obj.strategy_name
                                    portfolio_state['items'][idx]['symbol'] = run_obj.symbol
                                    portfolio_state['items'][idx]['timeframe'] = run_obj.timeframe
                                    portfolio_state['items'][idx]['saved_run_id'] = selected_run_id
                                    
                                    try:
                                        if run_obj.parameters_used:
                                            p_dict = json.loads(run_obj.parameters_used) if isinstance(run_obj.parameters_used, str) else run_obj.parameters_used
                                            portfolio_state['items'][idx]['custom_params'] = p_dict
                                        else:
                                            portfolio_state['items'][idx]['custom_params'] = get_strategy_default_params(run_obj.strategy_name).copy()
                                    except Exception:
                                        portfolio_state['items'][idx]['custom_params'] = get_strategy_default_params(run_obj.strategy_name).copy()
                                    
                                    ui.notify(f"Estrategia #{idx+1} actualizada con los datos del backtest #{selected_run_id[:8]}", type='positive')
                                    refresh_portfolio_items_ui()

                                saved_sel = ui.select(
                                    saved_opts, 
                                    label='Seleccionar corrida guardada', 
                                    value=item.get('saved_run_id', None),
                                    on_change=lambda e, idx=i: on_load_saved(e, idx)
                                ).classes('flex-1 min-w-[280px]')

                        # 2. Configuración Principal de la Estrategia
                        with ui.row().classes('w-full gap-4 items-center mb-3 flex-wrap'):
                            with ui.row().classes('items-center gap-2 min-w-[130px]'):
                                ui.icon('memory', size='1.2rem').classes('text-emerald-400')
                                ui.label(f"Estrategia #{i+1}").classes('font-extrabold text-white text-base')

                            strat_sel = ui.select(list(strategies.keys()), label='Estrategia Base', value=item['strategy']).classes('flex-1 min-w-[220px]')
                            sym_sel = ui.select(available_symbols, label='Símbolo', value=item['symbol'], with_input=True, new_value_mode='add-unique').classes('w-44')
                            tf_sel = ui.select(['1m', '5m', '15m', '1h', '4h', '1d'], label='TF', value=item['timeframe']).classes('w-28')
                            weight_num = ui.number('Peso (%)', value=item['weight_pct'], min=0.0, max=100.0, step=5.0).classes('w-28')

                            def remove_item(idx_to_remove=i):
                                if len(portfolio_state['items']) <= 1:
                                    ui.notify("Debe existir al menos 1 estrategia en el portafolio", type="warning")
                                    return
                                portfolio_state['items'].pop(idx_to_remove)
                                refresh_portfolio_items_ui()

                            ui.button(icon='delete', on_click=lambda idx=i: remove_item(idx)).props('flat round color=negative size=md').tooltip('Eliminar esta estrategia del portafolio')

                        # 3. Resumen visual de parámetros actuales
                        with ui.row().classes('w-full gap-2 items-center bg-slate-950/40 p-2.5 rounded-xl border border-slate-800/60 flex-wrap'):
                            ui.label('⚙️ Parámetros activos:').classes('text-xs text-slate-400 font-bold')
                            for pk, pv in item.get('custom_params', {}).items():
                                with ui.row().classes('items-center gap-1 bg-slate-800/80 px-2 py-0.5 rounded border border-slate-700'):
                                    ui.label(f"{pk}:").classes('text-[11px] text-slate-400 font-mono')
                                    def mk_param_handler(param_key=pk, idx_item=i):
                                        def _h(e):
                                            try:
                                                v = float(e.value) if '.' in str(e.value) else int(e.value)
                                            except Exception:
                                                v = e.value
                                            portfolio_state['items'][idx_item]['custom_params'][param_key] = v
                                        return _h
                                    ui.number(value=pv, on_change=mk_param_handler(pk, i)).classes('w-16 text-xs').props('dense borderless')

                        # Handlers de cambios
                        def on_strat_change(e, idx):
                            new_s = e.value
                            if new_s:
                                portfolio_state['items'][idx]['strategy'] = new_s
                                portfolio_state['items'][idx]['saved_run_id'] = None
                                portfolio_state['items'][idx]['custom_params'] = get_strategy_default_params(new_s).copy()
                                refresh_portfolio_items_ui()

                        strat_sel.on_value_change(lambda e, idx=i: on_strat_change(e, idx))
                        sym_sel.on_value_change(lambda e, idx=i: portfolio_state['items'][idx].update({'symbol': e.value}))
                        tf_sel.on_value_change(lambda e, idx=i: portfolio_state['items'][idx].update({'timeframe': e.value}))
                        weight_num.on_value_change(lambda e, idx=i: portfolio_state['items'][idx].update({'weight_pct': float(e.value or 0.0)}))

        refresh_portfolio_items_ui()

        # Botones para agregar y balancear
        with ui.row().classes('w-full gap-3 mb-6 items-center flex-wrap'):
            def add_portfolio_item():
                default_strat = list(strategies.keys())[0] if strategies else ''
                portfolio_state['items'].append({
                    'strategy': default_strat,
                    'symbol': available_symbols[0],
                    'timeframe': '1d',
                    'weight_pct': 0.0,
                    'custom_params': get_strategy_default_params(default_strat).copy()
                })
                refresh_portfolio_items_ui()

            def rebalance_equal_weights():
                n = len(portfolio_state['items'])
                if n > 0:
                    eq_w = round(100.0 / n, 2)
                    for item in portfolio_state['items']:
                        item['weight_pct'] = eq_w
                    refresh_portfolio_items_ui()
                    ui.notify(f"Pesos distribuidos equitativamente ({eq_w}% por estrategia)", type="info")

            ui.button('➕ Agregar Otra Estrategia al Portafolio', on_click=add_portfolio_item).classes('bg-blue-600 hover:bg-blue-700 text-white font-bold py-2.5 px-5 rounded-xl shadow')
            ui.button('⚖️ Distribuir Capital Equitativamente (100%)', on_click=rebalance_equal_weights).classes('bg-slate-800 hover:bg-slate-700 text-slate-200 font-bold py-2.5 px-5 rounded-xl border border-slate-700 shadow')

        # Action Button
        btn_run_portfolio = ui.button('⚡ EJECUTAR SIMULACIÓN DE PORTAFOLIO COMBINADO').classes('w-full bg-emerald-600 hover:bg-emerald-500 text-white font-extrabold py-4 text-lg rounded-2xl shadow-2xl transition-all')

        # Results Section
        port_results_container = ui.column().classes('w-full mt-6 gap-4')

        async def run_portfolio_simulation():
            p_client = ui.context.client
            total_w = sum(float(item['weight_pct']) for item in portfolio_state['items'])
            if abs(total_w - 100.0) > 1.0:
                ui.notify(f"La suma de los pesos del portafolio debe ser 100% (actualmente es {total_w:.1f}%)", type="warning")

            btn_run_portfolio.disable()
            btn_run_portfolio.set_text("⏳ Calculando simulación de portafolio y comparativa HOLD...")
            ui.notify("⚡ Calculando simulación combinada del portafolio... Por favor espera", type="info", spinner=True, timeout=3.0)

            try:
                p_start = parse_flexible_date(port_start_input.value, default=datetime(2020, 1, 1, tzinfo=timezone.utc))
                p_end = parse_flexible_date(port_end_input.value, default=datetime.now(timezone.utc), is_end_of_day=True)
                tot_cap = float(port_capital_input.value or 10000.0)
                c_pct = float(port_comm_input.value or 0.1)
                s_pct = float(port_slip_input.value or 0.05)
                curr_selected = str(port_currency_select.value or 'USDT (Dólares / Quote)')
                hold_bench = str(port_hold_select.value or 'weighted')
                acct_m = str(port_acct_mode_select.value or 'spot_cash')

                items_for_sync = []
                for item in portfolio_state['items']:
                    s_name = item['strategy']
                    items_for_sync.append({
                        'strategy_path': strategies.get(s_name, s_name),
                        'symbol': item['symbol'],
                        'timeframe': normalize_timeframe(item['timeframe']),
                        'weight_pct': float(item['weight_pct']),
                        'custom_params': item.get('custom_params', {})
                    })

                res = await run.io_bound(
                    _sync_run_portfolio_backtest,
                    items_for_sync,
                    tot_cap,
                    p_start,
                    p_end,
                    c_pct,
                    s_pct,
                    curr_selected,
                    hold_bench,
                    acct_m
                )

                with p_client:
                    if 'error' in res:
                        ui.notify(res['error'], type="warning")
                        return

                    port_results_container.clear()
                    with port_results_container:

                        # ── Función para Calcular Métricas en cualquier Divisa ──
                        all_prices = res.get('price_series', {})
                        def compute_currency_metrics(asset_code):
                            p_arr = np.array(all_prices.get(asset_code, [1.0]*len(res['portfolio_equity'])), dtype=float)
                            p_arr[p_arr <= 0] = 1.0

                            port_eq_arr = np.array(res['portfolio_equity'], dtype=float) / p_arr
                            hold_eq_arr = np.array(res['hold_equity'], dtype=float) / p_arr

                            s_port = pd.Series(port_eq_arr, index=pd.to_datetime(res['dates'], format='%d/%m/%y', errors='coerce'))
                            s_hold = pd.Series(hold_eq_arr, index=pd.to_datetime(res['dates'], format='%d/%m/%y', errors='coerce'))

                            m_port = calculate_equity_curve_metrics(s_port)
                            m_hold = calculate_equity_curve_metrics(s_hold)

                            init_c = port_eq_arr[0]
                            final_c = port_eq_arr[-1]
                            pnl_p = ((final_c - init_c) / init_c * 100.0) if init_c > 0 else 0.0
                            hold_pnl = ((hold_eq_arr[-1] - hold_eq_arr[0]) / hold_eq_arr[0] * 100.0) if hold_eq_arr[0] > 0 else 0.0
                            alpha_v = pnl_p - hold_pnl

                            cagr_v = m_port.get('cagr', 0.0)
                            maxdd_v = m_port.get('max_drawdown_pct', 0.0)
                            sharpe_v = m_port.get('sharpe_ratio', 0.0)

                            hold_cagr_v = m_hold.get('cagr', 0.0)
                            hold_maxdd_v = m_hold.get('max_drawdown_pct', 0.0)

                            calmar_v = (cagr_v / abs(maxdd_v)) if maxdd_v != 0 else 0.0

                            return {
                                'asset': asset_code,
                                'symbol_sign': _get_currency_symbol(asset_code),
                                'init_cap': init_c,
                                'final_cap': final_c,
                                'pnl_pct': pnl_p,
                                'cagr': cagr_v,
                                'max_dd': maxdd_v,
                                'sharpe': sharpe_v,
                                'calmar': calmar_v,
                                'alpha': alpha_v,
                                'hold_final': hold_eq_arr[-1],
                                'hold_cagr': hold_cagr_v,
                                'hold_max_dd': hold_maxdd_v
                            }

                        # Calcular métricas para las 3 divisas principales (USDT, BTC, ETH)
                        metrics_3c = {
                            'USDT': compute_currency_metrics('USDT'),
                            'BTC': compute_currency_metrics('BTC'),
                            'ETH': compute_currency_metrics('ETH')
                        }

                        # ── 1. Tarjetas Multi-Divisa (3 Monedas Simultáneas: USDT, BTC, ETH) ──
                        with ui.card().classes('w-full bg-slate-900/90 border border-slate-800 p-5 rounded-2xl shadow-xl'):
                            with ui.row().classes('items-center justify-between w-full mb-3 flex-wrap gap-2'):
                                with ui.row().classes('items-center gap-3'):
                                    with ui.row().classes('w-10 h-10 rounded-xl bg-emerald-500/20 border border-emerald-500/40 items-center justify-center text-emerald-400'):
                                        ui.icon('analytics', size='1.5rem')
                                    with ui.column().classes('gap-0'):
                                        ui.label('Métricas Simultáneas Multi-Divisa (USDT, BTC, ETH)').classes('text-base font-bold text-white')
                                        ui.label('Evaluación cuantitativa del rendimiento y riesgo del portafolio expresado en cada divisa clave').classes('text-xs text-slate-400')
                                ui.badge('Visión Integral 360°', color='emerald-8').classes('text-xs font-bold px-3 py-1')

                            with ui.row().classes('w-full gap-4 flex-wrap'):
                                # Card USDT
                                m_usdt = metrics_3c['USDT']
                                with ui.card().classes('flex-1 min-w-[280px] bg-slate-950/80 p-4 rounded-xl border border-emerald-500/30 shadow-lg'):
                                    with ui.row().classes('items-center justify-between w-full mb-2'):
                                        with ui.row().classes('items-center gap-2'):
                                            ui.icon('attach_money', size='1.3rem').classes('text-emerald-400')
                                            ui.label('En Dólares (USDT)').classes('font-bold text-sm text-emerald-400')
                                        ui.badge(f"PnL: {m_usdt['pnl_pct']:+.1f}%", color='emerald-9').classes('text-xs font-bold')
                                    
                                    with ui.column().classes('w-full gap-1'):
                                        with ui.row().classes('justify-between items-center w-full'):
                                            ui.label('Capital Final:').classes('text-xs text-slate-400')
                                            ui.label(f"${m_usdt['final_cap']:,.2f}").classes('text-lg font-black text-white')
                                        with ui.row().classes('justify-between items-center w-full'):
                                            ui.label('CAGR Anualizado:').classes('text-xs text-slate-400')
                                            ui.label(f"{m_usdt['cagr']:+.2f}%").classes(f"text-sm font-bold {'text-emerald-400' if m_usdt['cagr']>=0 else 'text-rose-400'}")
                                        with ui.row().classes('justify-between items-center w-full'):
                                            ui.label('Max Drawdown:').classes('text-xs text-slate-400')
                                            ui.label(f"{m_usdt['max_dd']:.2f}%").classes('text-sm font-bold text-rose-400')
                                        with ui.row().classes('justify-between items-center w-full'):
                                            ui.label('Sharpe Ratio:').classes('text-xs text-slate-400')
                                            ui.label(f"{m_usdt['sharpe']:.2f}").classes('text-sm font-bold text-purple-400')
                                        with ui.row().classes('justify-between items-center w-full pt-1 border-t border-slate-800'):
                                            ui.label('Alpha vs HOLD:').classes('text-xs text-slate-400')
                                            ui.label(f"{m_usdt['alpha']:+.2f}%").classes(f"text-xs font-bold {'text-emerald-400' if m_usdt['alpha']>=0 else 'text-rose-400'}")

                                # Card BTC
                                m_btc = metrics_3c['BTC']
                                with ui.card().classes('flex-1 min-w-[280px] bg-slate-950/80 p-4 rounded-xl border border-amber-500/30 shadow-lg'):
                                    with ui.row().classes('items-center justify-between w-full mb-2'):
                                        with ui.row().classes('items-center gap-2'):
                                            ui.icon('currency_bitcoin', size='1.3rem').classes('text-amber-400')
                                            ui.label('En Bitcoin (BTC)').classes('font-bold text-sm text-amber-400')
                                        ui.badge(f"PnL: {m_btc['pnl_pct']:+.1f}%", color='amber-9').classes('text-xs font-bold')
                                    
                                    with ui.column().classes('w-full gap-1'):
                                        with ui.row().classes('justify-between items-center w-full'):
                                            ui.label('Capital Final:').classes('text-xs text-slate-400')
                                            ui.label(f"₿ {m_btc['final_cap']:,.4f}").classes('text-lg font-black text-white')
                                        with ui.row().classes('justify-between items-center w-full'):
                                            ui.label('CAGR Anualizado:').classes('text-xs text-slate-400')
                                            ui.label(f"{m_btc['cagr']:+.2f}%").classes(f"text-sm font-bold {'text-emerald-400' if m_btc['cagr']>=0 else 'text-rose-400'}")
                                        with ui.row().classes('justify-between items-center w-full'):
                                            ui.label('Max Drawdown:').classes('text-xs text-slate-400')
                                            ui.label(f"{m_btc['max_dd']:.2f}%").classes('text-sm font-bold text-rose-400')
                                        with ui.row().classes('justify-between items-center w-full'):
                                            ui.label('Sharpe Ratio:').classes('text-xs text-slate-400')
                                            ui.label(f"{m_btc['sharpe']:.2f}").classes('text-sm font-bold text-purple-400')
                                        with ui.row().classes('justify-between items-center w-full pt-1 border-t border-slate-800'):
                                            ui.label('Alpha vs BTC Hold:').classes('text-xs text-slate-400')
                                            ui.label(f"{m_btc['alpha']:+.2f}%").classes(f"text-xs font-bold {'text-emerald-400' if m_btc['alpha']>=0 else 'text-rose-400'}")

                                # Card ETH
                                m_eth = metrics_3c['ETH']
                                with ui.card().classes('flex-1 min-w-[280px] bg-slate-950/80 p-4 rounded-xl border border-sky-500/30 shadow-lg'):
                                    with ui.row().classes('items-center justify-between w-full mb-2'):
                                        with ui.row().classes('items-center gap-2'):
                                            ui.icon('diamond', size='1.3rem').classes('text-sky-400')
                                            ui.label('En Ethereum (ETH)').classes('font-bold text-sm text-sky-400')
                                        ui.badge(f"PnL: {m_eth['pnl_pct']:+.1f}%", color='sky-9').classes('text-xs font-bold')
                                    
                                    with ui.column().classes('w-full gap-1'):
                                        with ui.row().classes('justify-between items-center w-full'):
                                            ui.label('Capital Final:').classes('text-xs text-slate-400')
                                            ui.label(f"Ξ {m_eth['final_cap']:,.4f}").classes('text-lg font-black text-white')
                                        with ui.row().classes('justify-between items-center w-full'):
                                            ui.label('CAGR Anualizado:').classes('text-xs text-slate-400')
                                            ui.label(f"{m_eth['cagr']:+.2f}%").classes(f"text-sm font-bold {'text-emerald-400' if m_eth['cagr']>=0 else 'text-rose-400'}")
                                        with ui.row().classes('justify-between items-center w-full'):
                                            ui.label('Max Drawdown:').classes('text-xs text-slate-400')
                                            ui.label(f"{m_eth['max_dd']:.2f}%").classes('text-sm font-bold text-rose-400')
                                        with ui.row().classes('justify-between items-center w-full'):
                                            ui.label('Sharpe Ratio:').classes('text-xs text-slate-400')
                                            ui.label(f"{m_eth['sharpe']:.2f}").classes('text-sm font-bold text-purple-400')
                                        with ui.row().classes('justify-between items-center w-full pt-1 border-t border-slate-800'):
                                            ui.label('Alpha vs ETH Hold:').classes('text-xs text-slate-400')
                                            ui.label(f"{m_eth['alpha']:+.2f}%").classes(f"text-xs font-bold {'text-emerald-400' if m_eth['alpha']>=0 else 'text-rose-400'}")

                        # ── 2. Matriz Tabular Comparativa Detallada Multi-Divisa ──
                        with ui.card().classes('w-full bg-slate-900/90 border border-slate-800 p-4 rounded-2xl shadow-xl mt-3'):
                            ui.label('📋 Tabla Comparativa de Métricas por Divisa de Medición').classes('text-base font-bold text-white mb-2')
                            
                            # Construir columnas y filas de la matriz
                            matrix_columns = [
                                {'name': 'metric', 'label': 'Métrica Cuantitativa', 'field': 'metric', 'align': 'left'},
                                {'name': 'usdt', 'label': '💵 Medido en USDT ($)', 'field': 'usdt', 'align': 'right'},
                                {'name': 'btc', 'label': '🪙 Medido en BTC (₿)', 'field': 'btc', 'align': 'right'},
                                {'name': 'eth', 'label': '💎 Medido en ETH (Ξ)', 'field': 'eth', 'align': 'right'}
                            ]

                            # Añadir cualquier otro token nativo presente en la cartera (ej. AVAX, SOL)
                            extra_assets = [a for a in sorted(list(all_prices.keys())) if a not in ['USDT', 'USD', 'FDUSD', 'USDC', 'BTC', 'ETH']]
                            for ea in extra_assets:
                                metrics_3c[ea] = compute_currency_metrics(ea)
                                matrix_columns.append({
                                    'name': ea.lower(),
                                    'label': f"🪙 Medido en {ea}",
                                    'field': ea.lower(),
                                    'align': 'right'
                                })

                            def _fmt_row(key_val, formatter):
                                row_data = {'metric': key_val}
                                for k_cur, m_dict in metrics_3c.items():
                                    field_k = k_cur.lower() if k_cur not in ['USDT', 'BTC', 'ETH'] else k_cur.lower()
                                    row_data[field_k] = formatter(m_dict)
                                return row_data

                            matrix_rows = [
                                _fmt_row('Capital Inicial', lambda m: f"{m['symbol_sign']}{m['init_cap']:,.4f}" if m['symbol_sign'] != '$' else f"${m['init_cap']:,.2f}"),
                                _fmt_row('Capital Final', lambda m: f"{m['symbol_sign']}{m['final_cap']:,.4f}" if m['symbol_sign'] != '$' else f"${m['final_cap']:,.2f}"),
                                _fmt_row('Rendimiento Total (PnL %)', lambda m: f"{m['pnl_pct']:+.2f}%"),
                                _fmt_row('CAGR Anualizado (%)', lambda m: f"{m['cagr']:+.2f}%"),
                                _fmt_row('Max Drawdown (%)', lambda m: f"{m['max_dd']:.2f}%"),
                                _fmt_row('Alpha vs Benchmark HOLD (%)', lambda m: f"{m['alpha']:+.2f}%"),
                                _fmt_row('CAGR Benchmark HOLD (%)', lambda m: f"{m['hold_cagr']:+.2f}%"),
                                _fmt_row('Max Drawdown del HOLD (%)', lambda m: f"{m['hold_max_dd']:.2f}%"),
                                _fmt_row('Sharpe Ratio', lambda m: f"{m['sharpe']:.2f}"),
                                _fmt_row('Calmar Ratio (CAGR / MaxDD)', lambda m: f"{m['calmar']:.2f}"),
                                _fmt_row('Operaciones (Win Rate)', lambda m: f"{res['total_trades']} trades ({res['win_rate']:.1f}% win)")
                            ]

                            ui.table(columns=matrix_columns, rows=matrix_rows, row_key='metric').classes('w-full').props('dense flat')

                        # ── 3. Panel de Gráficos con Toggle de Moneda ──
                        measurement_options = {'USDT': '💵 USDT (Dólares / Quote)'}
                        for asset_name in sorted(list(all_prices.keys())):
                            if asset_name in ['USDT', 'USD', 'FDUSD', 'USDC']:
                                continue
                            if asset_name == 'BTC':
                                measurement_options['BTC'] = '🪙 BTC (Satoshis / Base)'
                            elif asset_name == 'ETH':
                                measurement_options['ETH'] = '💎 ETH (Ethereum)'
                            elif asset_name == 'AVAX':
                                measurement_options['AVAX'] = '🔺 AVAX (Avalanche)'
                            elif asset_name == 'SOL':
                                measurement_options['SOL'] = '☀️ SOL (Solana)'
                            else:
                                measurement_options[asset_name] = f"🪙 {asset_name} (Base)"

                        with ui.card().classes('w-full bg-slate-900/90 border border-slate-800 p-4 rounded-2xl shadow-lg mt-4'):
                            with ui.row().classes('w-full justify-between items-center flex-wrap gap-3'):
                                with ui.row().classes('items-center gap-3'):
                                    with ui.row().classes('w-10 h-10 rounded-xl bg-indigo-500/20 border border-indigo-500/40 items-center justify-center text-indigo-400'):
                                        ui.icon('currency_exchange', size='1.5rem')
                                    with ui.column().classes('gap-0'):
                                        ui.label('Moneda de Medición de Gráficos (Equity & Drawdown)').classes('text-base font-bold text-white')
                                        ui.label('Selecciona en qué divisa graficar las trayectorias de capital y riesgo').classes('text-xs text-slate-400')
                                
                                with ui.row().classes('items-center gap-2'):
                                    ui.label('Graficar en:').classes('text-xs font-bold text-slate-300')
                                    measure_toggle = ui.toggle(
                                        measurement_options,
                                        value='USDT'
                                    ).props('color=indigo-7 text-color=grey-4 toggle-color=indigo-6 toggle-text-color=white no-caps size=sm rounded')

                        # ── 2. Curva de Capital Combinada vs Curva Benchmark HOLD ──
                        palette_colors = ['#2563eb', '#9333ea', '#d97706', '#0891b2', '#e11d48', '#65a30d', '#0284c7']
                        
                        def build_equity_series(measured_asset='USDT'):
                            p_arr = np.array(all_prices.get(measured_asset, [1.0]*len(res['portfolio_equity'])), dtype=float)
                            p_arr[p_arr <= 0] = 1.0

                            series_list = []
                            # Curvas individuales
                            for idx, (k, v) in enumerate(res['individual_equity'].items()):
                                col_c = palette_colors[idx % len(palette_colors)]
                                ind_data = (np.array(v, dtype=float) / p_arr).round(4).tolist()
                                series_list.append({
                                    'name': k,
                                    'type': 'line',
                                    'data': ind_data,
                                    'smooth': True,
                                    'z': 3,
                                    'lineStyle': {'width': 1.8, 'type': 'solid', 'color': col_c},
                                    'showSymbol': False
                                })
                            
                            # Curva Benchmark HOLD
                            hold_data = (np.array(res['hold_equity'], dtype=float) / p_arr).round(4).tolist()
                            series_list.append({
                                'name': '💎 BENCHMARK HOLD (Referencia)',
                                'type': 'line',
                                'data': hold_data,
                                'smooth': True,
                                'z': 8,
                                'lineStyle': {'width': 2.5, 'type': 'dashed', 'color': '#38bdf8'},
                                'showSymbol': False
                            })

                            # Total Portafolio Combinado
                            port_data = (np.array(res['portfolio_equity'], dtype=float) / p_arr).round(4).tolist()
                            series_list.append({
                                'name': '★ PORTAFOLIO TOTAL COMBINADO',
                                'type': 'line',
                                'data': port_data,
                                'smooth': True,
                                'z': 10,
                                'lineStyle': {'width': 4, 'color': '#10b981'},
                                'areaStyle': {'opacity': 0.12, 'color': '#10b981'}
                            })
                            return series_list

                        def build_drawdown_series(measured_asset='USDT'):
                            p_arr = np.array(all_prices.get(measured_asset, [1.0]*len(res['portfolio_equity'])), dtype=float)
                            p_arr[p_arr <= 0] = 1.0

                            dd_list = []
                            # Drawdowns individuales
                            for idx, (k, v) in enumerate(res.get('individual_equity', {}).items()):
                                col_c = palette_colors[idx % len(palette_colors)]
                                ind_val = np.array(v, dtype=float) / p_arr
                                s_ind = pd.Series(ind_val)
                                s_ind_dd = ((s_ind - s_ind.cummax()) / s_ind.cummax() * 100.0).round(2).tolist()
                                dd_list.append({
                                    'name': f"DD {k}",
                                    'type': 'line',
                                    'data': s_ind_dd,
                                    'smooth': True,
                                    'z': 3,
                                    'lineStyle': {'width': 1.5, 'type': 'dashed', 'color': col_c},
                                    'showSymbol': False
                                })
                            
                            # Drawdown Benchmark HOLD
                            hold_val = np.array(res['hold_equity'], dtype=float) / p_arr
                            s_h = pd.Series(hold_val)
                            hold_dd = ((s_h - s_h.cummax()) / s_h.cummax() * 100.0).round(2).tolist()
                            dd_list.append({
                                'name': 'DD Benchmark HOLD (%)',
                                'type': 'line',
                                'data': hold_dd,
                                'smooth': True,
                                'z': 8,
                                'lineStyle': {'width': 2, 'type': 'dotted', 'color': '#38bdf8'},
                                'showSymbol': False
                            })

                            # Drawdown Total Combinado
                            port_val = np.array(res['portfolio_equity'], dtype=float) / p_arr
                            s_p = pd.Series(port_val)
                            port_dd = ((s_p - s_p.cummax()) / s_p.cummax() * 100.0).round(2).tolist()
                            dd_list.append({
                                'name': '★ DRAWDOWN TOTAL COMBINADO (%)',
                                'type': 'line',
                                'data': port_dd,
                                'smooth': True,
                                'z': 10,
                                'lineStyle': {'width': 3.5, 'color': '#f43f5e'},
                                'areaStyle': {'opacity': 0.15, 'color': '#f43f5e'}
                            })
                            return dd_list

                        with ui.card().classes('w-full bg-slate-900/80 p-4 rounded-2xl border border-slate-800 shadow-xl mt-4'):
                            with ui.row().classes('w-full justify-between items-center mb-2 flex-wrap'):
                                lbl_equity_chart_hdr = ui.label('📈 Curvas de Capital: Portafolio Activo vs Benchmark HOLD (en USDT)').classes('text-base font-bold text-slate-200')
                                badge_cur_indicator = ui.badge('Medición: USDT', color='indigo-8').classes('text-xs font-mono px-3 py-1')

                            port_chart_options = {
                                'title': {'text': 'Evolución de Capital ($ USDT) y Comparativa frente a Buy & Hold', 'left': 'center', 'textStyle': {'color': '#94a3b8', 'fontSize': 13}},
                                'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'cross'}, 'backgroundColor': '#0f172a', 'borderColor': '#334155', 'textStyle': {'color': '#f8fafc'}},
                                'legend': {'data': [s['name'] for s in build_equity_series('USDT')], 'bottom': 0, 'textStyle': {'color': '#94a3b8'}},
                                'xAxis': {'type': 'category', 'data': res['dates'], 'axisLine': {'lineStyle': {'color': '#334155'}}, 'axisLabel': {'color': '#94a3b8'}},
                                'yAxis': {'type': 'value', 'scale': True, 'splitLine': {'lineStyle': {'color': '#1e293b'}}, 'axisLabel': {'color': '#94a3b8'}},
                                'series': build_equity_series('USDT')
                            }
                            equity_echart = ui.echart(port_chart_options).classes('w-full h-84')

                        # ── 3. Drawdown Real Combinado vs Drawdown del HOLD ──
                        with ui.card().classes('w-full bg-slate-900/80 p-4 rounded-2xl border border-slate-800 shadow-xl mt-4'):
                            lbl_dd_chart_hdr = ui.label('📉 Caídas (Drawdown %) Individuales, Combinado y Benchmark HOLD (en USDT)').classes('text-base font-bold text-slate-200 mb-2')
                            dd_chart_options = {
                                'title': {'text': 'Comparativa de Riesgo de Caída (Drawdown Relativo % en USDT)', 'left': 'center', 'textStyle': {'color': '#94a3b8', 'fontSize': 13}},
                                'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'cross'}, 'backgroundColor': '#0f172a', 'borderColor': '#334155', 'textStyle': {'color': '#f8fafc'}},
                                'legend': {'data': [s['name'] for s in build_drawdown_series('USDT')], 'bottom': 0, 'textStyle': {'color': '#94a3b8'}},
                                'xAxis': {'type': 'category', 'data': res['dates'], 'axisLine': {'lineStyle': {'color': '#334155'}}, 'axisLabel': {'color': '#94a3b8'}},
                                'yAxis': {'type': 'value', 'max': 0, 'scale': True, 'splitLine': {'lineStyle': {'color': '#1e293b'}}, 'axisLabel': {'color': '#94a3b8', 'formatter': '{value}%'}},
                                'series': build_drawdown_series('USDT')
                            }
                            dd_echart = ui.echart(dd_chart_options).classes('w-full h-72')

                        # ── 4. Tabla Desglosada por Estrategia ──
                        with ui.card().classes('w-full bg-slate-900/80 p-4 rounded-2xl border border-slate-800 shadow-xl mt-4'):
                            ui.label('📊 Desglose de Rendimiento por Estrategia').classes('text-base font-bold text-slate-200 mb-2')
                            breakdown_cols = [
                                {'name': 'name', 'label': 'Estrategia / Activo', 'field': 'name', 'align': 'left'},
                                {'name': 'weight_pct', 'label': 'Peso (%)', 'field': 'weight_pct', 'align': 'center'},
                                {'name': 'allocated_cap', 'label': 'Cap. Asignado', 'field': 'allocated_cap', 'align': 'right'},
                                {'name': 'final_cap', 'label': 'Cap. Final', 'field': 'final_cap', 'align': 'right'},
                                {'name': 'pnl_pct', 'label': 'PnL %', 'field': 'pnl_pct', 'align': 'right'},
                                {'name': 'cagr', 'label': 'CAGR (%)', 'field': 'cagr', 'align': 'right'},
                                {'name': 'max_dd', 'label': 'Max DD (%)', 'field': 'max_dd', 'align': 'right'},
                                {'name': 'trades_count', 'label': 'Trades', 'field': 'trades_count', 'align': 'center'},
                            ]
                            def build_breakdown_rows(measured_asset='USDT'):
                                sym_sign = _get_currency_symbol(measured_asset)
                                p_arr = np.array(all_prices.get(measured_asset, [1.0]*len(res['portfolio_equity'])), dtype=float)
                                p_arr[p_arr <= 0] = 1.0
                                p_start = float(p_arr[0])
                                p_end = float(p_arr[-1])

                                rows = []
                                for row in res['strategy_breakdown']:
                                    alloc_m = row['allocated_cap'] / p_start
                                    final_m = row['final_cap'] / p_end
                                    pnl_m = ((final_m - alloc_m) / alloc_m * 100.0) if alloc_m > 0 else 0.0
                                    
                                    # Formato adaptativo de decimales según valor
                                    fmt = f"{sym_sign}{final_m:,.4f}" if sym_sign != '$' else f"${final_m:,.2f}"
                                    fmt_alloc = f"{sym_sign}{alloc_m:,.4f}" if sym_sign != '$' else f"${alloc_m:,.2f}"

                                    rows.append({
                                        'name': row['name'],
                                        'weight_pct': f"{row['weight_pct']:.1f}%",
                                        'allocated_cap': fmt_alloc,
                                        'final_cap': fmt,
                                        'pnl_pct': f"{'+' if pnl_m > 0 else ''}{pnl_m:.2f}%",
                                        'cagr': f"{row['cagr']:.2f}%",
                                        'max_dd': f"{row['max_dd']:.2f}%",
                                        'trades_count': row['trades_count']
                                    })
                                return rows

                            breakdown_table = ui.table(columns=breakdown_cols, rows=build_breakdown_rows('USDT'), row_key='name').classes('w-full').props('dense flat')

                        # ── 5. Registro Cronológico Consolidado de Operaciones ──
                        with ui.card().classes('w-full bg-slate-900/80 p-4 rounded-2xl border border-slate-800 shadow-xl mt-4'):
                            ui.label('📜 Registro Cronológico Consolidado de Operaciones').classes('text-base font-bold text-slate-200 mb-2')
                            trades_cols = [
                                {'name': 'trade_id', 'label': '#', 'field': 'trade_id', 'align': 'center'},
                                {'name': 'strategy', 'label': 'Estrategia', 'field': 'strategy', 'align': 'left'},
                                {'name': 'entry_time', 'label': 'Entrada (Fecha)', 'field': 'entry_time', 'align': 'left'},
                                {'name': 'exit_time', 'label': 'Salida (Fecha)', 'field': 'exit_time', 'align': 'left'},
                                {'name': 'side', 'label': 'Tipo', 'field': 'side', 'align': 'center'},
                                {'name': 'entry_price', 'label': 'P. Entrada', 'field': 'entry_price', 'align': 'right'},
                                {'name': 'exit_price', 'label': 'P. Salida', 'field': 'exit_price', 'align': 'right'},
                                {'name': 'pnl_str', 'label': 'PnL ($)', 'field': 'pnl_str', 'align': 'right'},
                                {'name': 'pnl_pct_str', 'label': 'PnL (%)', 'field': 'pnl_pct_str', 'align': 'right'},
                                {'name': 'reason', 'label': 'Motivo Salida', 'field': 'reason', 'align': 'center'},
                            ]
                            trades_table = ui.table(columns=trades_cols, rows=res['chronological_trades'], row_key='trade_id').classes('w-full').props('dense flat')
                            trades_table.add_slot('body-cell-side', '''
                                <q-td :props="props">
                                    <q-badge :color="props.value === 'LONG' ? 'positive' : 'negative'" class="px-2 py-0.5 font-bold">
                                        {{ props.value }}
                                    </q-badge>
                                </q-td>
                            ''')
                            trades_table.add_slot('body-cell-pnl_str', '''
                                <q-td :props="props">
                                    <span :class="props.row.pnl_raw > 0 ? 'text-emerald-400 font-bold' : (props.row.pnl_raw < 0 ? 'text-rose-400 font-bold' : 'text-slate-400')">
                                        {{ props.value }}
                                    </span>
                                </q-td>
                            ''')
                            trades_table.add_slot('body-cell-pnl_pct_str', '''
                                <q-td :props="props">
                                    <span :class="props.row.pnl_pct_raw > 0 ? 'text-emerald-400 font-bold' : (props.row.pnl_pct_raw < 0 ? 'text-rose-400 font-bold' : 'text-slate-400')">
                                        {{ props.value }}
                                    </span>
                                </q-td>
                            ''')

                        # ── Handler de Cambio Dinámico de Moneda de Medición ──
                        def _on_measure_toggle_change(e):
                            cur_asset = measure_toggle.value or 'USDT'
                            sym_sign = _get_currency_symbol(cur_asset)
                            p_arr = np.array(all_prices.get(cur_asset, [1.0]*len(res['portfolio_equity'])), dtype=float)
                            p_arr[p_arr <= 0] = 1.0

                            # 1. Recalcular equidad en divisa seleccionada
                            port_eq_arr = np.array(res['portfolio_equity'], dtype=float) / p_arr
                            hold_eq_arr = np.array(res['hold_equity'], dtype=float) / p_arr

                            s_port = pd.Series(port_eq_arr, index=pd.to_datetime(res['dates']))
                            s_hold = pd.Series(hold_eq_arr, index=pd.to_datetime(res['dates']))

                            # Recalcular métricas cuantitativas en la moneda elegida
                            m_port = calculate_equity_curve_metrics(s_port)
                            m_hold = calculate_equity_curve_metrics(s_hold)

                            cagr_v = m_port.get('cagr', 0.0)
                            maxdd_v = m_port.get('max_drawdown_pct', 0.0)
                            hold_cagr_v = m_hold.get('cagr', 0.0)
                            hold_maxdd_v = m_hold.get('max_drawdown_pct', 0.0)

                            pnl_v = ((port_eq_arr[-1] - port_eq_arr[0]) / port_eq_arr[0] * 100.0) if port_eq_arr[0] > 0 else 0.0
                            hold_pnl_v = ((hold_eq_arr[-1] - hold_eq_arr[0]) / hold_eq_arr[0] * 100.0) if hold_eq_arr[0] > 0 else 0.0
                            alpha_v = pnl_v - hold_pnl_v



                            # Actualizar Gráfico de Equity
                            new_eq_series = build_equity_series(cur_asset)
                            equity_echart.options['series'] = new_eq_series
                            equity_echart.options['title']['text'] = f"Evolución de Capital ({sym_sign} {cur_asset}) y Comparativa frente a Buy & Hold"
                            equity_echart.options['legend']['data'] = [s['name'] for s in new_eq_series]
                            lbl_equity_chart_hdr.set_text(f"📈 Curvas de Capital: Portafolio Activo vs Benchmark HOLD (en {cur_asset})")
                            badge_cur_indicator.set_text(f"Medición: {cur_asset}")
                            equity_echart.update()

                            # Actualizar Gráfico de Drawdown
                            new_dd_series = build_drawdown_series(cur_asset)
                            dd_echart.options['series'] = new_dd_series
                            dd_echart.options['title']['text'] = f"Comparativa de Riesgo de Caída (Drawdown Relativo % en {cur_asset})"
                            dd_echart.options['legend']['data'] = [s['name'] for s in new_dd_series]
                            lbl_dd_chart_hdr.set_text(f"📉 Caídas (Drawdown %) Individuales, Combinado y Benchmark HOLD (en {cur_asset})")
                            dd_echart.update()

                            # Actualizar Tabla Desglosada
                            breakdown_table.rows = build_breakdown_rows(cur_asset)
                            breakdown_table.columns[2]['label'] = f"Cap. Asignado ({sym_sign})"
                            breakdown_table.columns[3]['label'] = f"Cap. Final ({sym_sign})"
                            breakdown_table.update()

                        measure_toggle.on_value_change(_on_measure_toggle_change)

                        ui.notify(f"✅ Simulación del Portafolio completada exitosamente. Total Trades: {res['total_trades']}", type="positive")

            except Exception as sim_ex:
                ui.notify(f"Error en simulación: {sim_ex}", type="negative")
            finally:
                with p_client:
                    btn_run_portfolio.enable()
                    btn_run_portfolio.set_text("⚡ EJECUTAR SIMULACIÓN DE PORTAFOLIO COMBINADO")

        btn_run_portfolio.on_click(run_portfolio_simulation)

    def load_strategy_in_portfolio(strat_name, symbol=None, timeframe=None, custom_params=None):
        """Permite a otros módulos (como el catálogo o historial) enviar una estrategia al simulador."""
        if portfolio_state['items']:
            portfolio_state['items'][0]['strategy'] = strat_name
            if symbol:
                portfolio_state['items'][0]['symbol'] = symbol
            if timeframe:
                portfolio_state['items'][0]['timeframe'] = timeframe
            if custom_params:
                portfolio_state['items'][0]['custom_params'] = custom_params.copy()
            else:
                portfolio_state['items'][0]['custom_params'] = get_strategy_default_params(strat_name).copy()
            refresh_portfolio_items_ui()

    return {
        'load_strategy': load_strategy_in_portfolio,
        'refresh_items': refresh_portfolio_items_ui
    }
