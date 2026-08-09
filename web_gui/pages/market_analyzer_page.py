from nicegui import ui, run
from data_layer.storage import SessionLocal, OHLCV, OnChainMetric
from data_layer.market_data import MarketDataManager
from sqlalchemy import func
from datetime import datetime, timezone, timedelta
import pandas as pd
import logging
import asyncio
from web_gui.components.cards import glass_card

from data_layer.export_utils import export_df_to_ninjatrader8

logger = logging.getLogger(__name__)

FALLBACK_SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT',
    'ADA/USDT', 'DOGE/USDT', 'AVAX/USDT', 'DOT/USDT', 'LINK/USDT',
    'BNB/BTC', 'ETH/BTC', 'LTC/BTC'
]

def _load_binance_symbols_sync() -> list:
    """Carga los símbolos de Binance de forma síncrona (se llama en un hilo separado)."""
    try:
        from data_layer.market_data import get_binance_exchange
        exc = get_binance_exchange()
        if not hasattr(exc, 'markets') or not exc.markets:
            exc.load_markets()
        raw_symbols = list(exc.markets.keys()) if hasattr(exc, 'markets') and exc.markets else []
        if raw_symbols:
            usdt_pairs = sorted([s for s in raw_symbols if s.endswith('/USDT')])
            other_pairs = sorted([s for s in raw_symbols if not s.endswith('/USDT')])
            return usdt_pairs + other_pairs
        return FALLBACK_SYMBOLS
    except Exception as e:
        logger.warning(f"No se pudieron cargar símbolos de Binance: {e}")
        return FALLBACK_SYMBOLS

def render_market_analyzer():
    with ui.column().classes('w-full q-pa-md max-w-[1600px] mx-auto'):
        # Hero Header
        with glass_card(title="Catálogo de Mercado e Historial de Precios", icon="storage"):
            pass # Title is handled by glass_card

        # ----------------- Main Data Catalog Table -----------------
        columns = [
            {'name': 'symbol', 'label': 'Par (Symbol)', 'field': 'symbol', 'sortable': True, 'classes': 'text-cyan-400 font-bold'},
            {'name': 'type', 'label': 'Tipo', 'field': 'type', 'sortable': True},
            {'name': 'metric', 'label': 'Temporalidad / Métrica', 'field': 'metric', 'sortable': True},
            {'name': 'source', 'label': 'Fuente', 'field': 'source', 'sortable': True},
            {'name': 'start', 'label': 'Fecha Inicial (UTC)', 'field': 'start'},
            {'name': 'end', 'label': 'Fecha Final (UTC)', 'field': 'end'},
        ]
        
        # Table with dark mode styling
        table = ui.table(columns=columns, rows=[], row_key='name').classes('w-full bg-slate-900 text-slate-200 border-none shadow-none mt-4')
        # Tailwind classes on NiceGUI tables don't automatically style everything, but bg-slate-900 makes the background dark.
        
        with ui.row().classes('w-full justify-between items-center q-mt-md gap-4'):
            ui.button('Actualizar Datos', on_click=lambda: load_data(), icon='refresh').props('rounded').classes('bg-slate-700 hover:bg-slate-600 text-white')
            ui.button('Bulk Downloader', on_click=lambda: download_dialog.open(), icon='download').props('rounded').classes('bg-cyan-600 hover:bg-cyan-500 text-white shadow-lg shadow-cyan-500/50')

        # ----------------- Data Viewer UI Elements -----------------
        with glass_card(title="Query & View Historical Data", icon="search"):
            with ui.row().classes('w-full gap-4 items-end mb-4 flex-wrap'):
                viewer_symbol_select = ui.select([], label='Symbol', value=None).classes('flex-1 min-w-[180px]')
                viewer_tf_select = ui.select([], label='Timeframe', value=None).classes('flex-1 min-w-[120px]')
                viewer_start_input = ui.input(label='Start (YYYY-MM-DD)', value=(datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')).classes('flex-1 min-w-[150px]')
                viewer_end_input = ui.input(label='End (YYYY-MM-DD)', value=datetime.now().strftime('%Y-%m-%d')).classes('flex-1 min-w-[150px]')
                ui.button('Cargar Data', on_click=lambda: load_viewer_data(), icon='search').props('rounded').classes('bg-cyan-600 hover:bg-cyan-500 text-white font-bold h-12 shadow-xl transition-all shadow-cyan-500/20 px-6')
                ui.button('Exportar NinjaTrader 8', on_click=lambda: open_nt8_dialog(), icon='file_download').props('rounded').classes('bg-emerald-600 hover:bg-emerald-500 text-white font-bold h-12 shadow-xl transition-all shadow-emerald-500/20 px-6')

            viewer_columns = [
                {'name': 'timestamp', 'label': 'Timestamp', 'field': 'timestamp', 'sortable': True},
                {'name': 'open', 'label': 'Open', 'field': 'open'},
                {'name': 'high', 'label': 'High', 'field': 'high'},
                {'name': 'low', 'label': 'Low', 'field': 'low'},
                {'name': 'close', 'label': 'Close', 'field': 'close'},
                {'name': 'volume', 'label': 'Volume', 'field': 'volume'},
            ]
            
            ui.label('Price Chart').classes('text-lg font-bold text-slate-300 mt-4 tracking-wide')
            viewer_chart = ui.echart({
                'backgroundColor': 'transparent',
                'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'cross'}},
                'xAxis': {'type': 'category', 'data': []},
                'yAxis': {'scale': True, 'splitLine': {'lineStyle': {'color': '#334155'}}}, # slate-700
                'series': [{'name': 'Price', 'type': 'candlestick', 'data': [], 'itemStyle': {'color': '#10b981', 'color0': '#ef4444', 'borderColor': '#10b981', 'borderColor0': '#ef4444'}}],
            }).classes('w-full h-96 border border-slate-700 rounded-lg p-2 bg-slate-900 mt-2')

            viewer_table = ui.table(columns=viewer_columns, rows=[], row_key='timestamp').classes('w-full mt-4 bg-slate-900 text-slate-200 border-none')

        def update_viewer_tfs(e):
            val = getattr(e, 'value', e)
            if not val or not isinstance(val, str):
                viewer_tf_select.options = []
                viewer_tf_select.value = None
                viewer_tf_select.update()
                return
            if val.startswith('On-Chain:'):
                viewer_tf_select.options = ['1d']
                viewer_tf_select.value = '1d'
                viewer_tf_select.update()
                return
                
            symbol = val.replace('Market: ', '')
            db = SessionLocal()
            tfs = [r[0] for r in db.query(OHLCV.timeframe).filter(OHLCV.symbol == symbol).distinct().all()]
            db.close()
            viewer_tf_select.options = tfs
            viewer_tf_select.value = tfs[0] if tfs else None
            viewer_tf_select.update()
            
        viewer_symbol_select.on_value_change(update_viewer_tfs)

        def load_viewer_data():
            if not viewer_symbol_select.value or not viewer_tf_select.value:
                ui.notify('Please select both Symbol and Timeframe', type='warning')
                return
                
            try:
                start_dt = datetime.strptime(viewer_start_input.value, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                end_dt = datetime.strptime(viewer_end_input.value, '%Y-%m-%d').replace(hour=23, minute=59, tzinfo=timezone.utc)
            except Exception:
                ui.notify('Invalid dates. Use YYYY-MM-DD', type='negative')
                return
                
            db = SessionLocal()
            
            is_market = viewer_symbol_select.value.startswith('Market:')
            if is_market:
                symbol = viewer_symbol_select.value.replace('Market: ', '')
                mgr = MarketDataManager(db)
                df = mgr.get_data(symbol, viewer_tf_select.value, start_dt, end_dt)
            else:
                parts = viewer_symbol_select.value.replace('On-Chain: ', '').split(' - ')
                symbol = parts[0]
                metric_name = parts[1]
                
                query = db.query(OnChainMetric).filter(
                    OnChainMetric.symbol == symbol,
                    OnChainMetric.metric_name == metric_name,
                    OnChainMetric.timestamp >= start_dt.replace(tzinfo=None),
                    OnChainMetric.timestamp <= end_dt.replace(tzinfo=None)
                ).order_by(OnChainMetric.timestamp.asc())
                
                df = pd.read_sql(query.statement, db.bind)
                if not df.empty:
                    df.set_index('timestamp', inplace=True)
                    if df.index.tz is None:
                        df.index = pd.to_datetime(df.index).tz_localize('UTC')
                    else:
                        df.index = pd.to_datetime(df.index).tz_convert('UTC')
                        
            db.close()
            
            if df.empty:
                ui.notify('No data found for the selected query', type='info')
                viewer_table.rows = []
                viewer_table.update()
                viewer_chart.options['xAxis']['data'] = []
                viewer_chart.options['series'][0]['data'] = []
                viewer_chart.update()
                return
                
            if len(df) > 5000:
                ui.notify('Showing first 5000 candles/points of query (limit reached)', type='warning')
                df = df.head(5000)
                
            df_reset = df.reset_index()
            rows = []
            
            if is_market:
                viewer_table._props['columns'] = [
                    {'name': 'timestamp', 'label': 'Timestamp', 'field': 'timestamp', 'sortable': True},
                    {'name': 'open', 'label': 'Open', 'field': 'open'},
                    {'name': 'high', 'label': 'High', 'field': 'high'},
                    {'name': 'low', 'label': 'Low', 'field': 'low'},
                    {'name': 'close', 'label': 'Close', 'field': 'close'},
                    {'name': 'volume', 'label': 'Volume', 'field': 'volume'},
                ]
                for _, r in df_reset.iterrows():
                    rows.append({
                        'timestamp': str(r['timestamp'])[:19],
                        'open': f"{r['open']:.4f}",
                        'high': f"{r['high']:.4f}",
                        'low': f"{r['low']:.4f}",
                        'close': f"{r['close']:.4f}",
                        'volume': f"{r['volume']:.2f}",
                    })
                viewer_chart.options['series'][0] = {'name': 'Price', 'type': 'candlestick', 'data': df[['open', 'close', 'low', 'high']].values.tolist()}
            else:
                viewer_table._props['columns'] = [
                    {'name': 'timestamp', 'label': 'Timestamp', 'field': 'timestamp', 'sortable': True},
                    {'name': 'value', 'label': metric_name, 'field': 'value'},
                ]
                for _, r in df_reset.iterrows():
                    rows.append({
                        'timestamp': str(r['timestamp'])[:19],
                        'value': f"{r['value']:.4f}",
                    })
                viewer_chart.options['series'][0] = {'name': metric_name, 'type': 'line', 'data': df['value'].values.tolist(), 'showSymbol': False}
                
            viewer_table.rows = rows
            viewer_table.update()
            
            viewer_chart.options['xAxis']['data'] = df.index.strftime('%Y-%m-%d %H:%M').tolist()
            viewer_chart.update()
            
            ui.notify(f"Loaded {len(rows)} records!", type='positive')

        def load_data():
            db = SessionLocal()
            rows = []
            
            # Market data
            market_query = db.query(
                OHLCV.symbol, 
                OHLCV.timeframe, 
                func.min(OHLCV.timestamp).label('start'),
                func.max(OHLCV.timestamp).label('end')
            ).group_by(OHLCV.symbol, OHLCV.timeframe)
            
            for row in market_query:
                rows.append({
                    'symbol': row.symbol,
                    'type': 'Market (OHLCV)',
                    'metric': row.timeframe,
                    'source': 'Binance / CCXT',
                    'start': str(row.start)[:10] if row.start else 'N/A',
                    'end': str(row.end)[:10] if row.end else 'N/A'
                })
                
            # OnChain data
            onchain_query = db.query(
                OnChainMetric.metric_name, 
                OnChainMetric.symbol, 
                OnChainMetric.source,
                func.min(OnChainMetric.timestamp).label('start'),
                func.max(OnChainMetric.timestamp).label('end')
            ).group_by(OnChainMetric.metric_name, OnChainMetric.symbol, OnChainMetric.source)
            
            for row in onchain_query:
                rows.append({
                    'symbol': row.symbol,
                    'type': 'On-Chain',
                    'metric': row.metric_name,
                    'source': row.source if row.source else 'N/A',
                    'start': str(row.start)[:10] if row.start else 'N/A',
                    'end': str(row.end)[:10] if row.end else 'N/A'
                })
                
            table.rows = rows
            table.update()
            
            # Update distinct symbols for the data viewer dropdown
            market_symbols = [f"Market: {r[0]}" for r in db.query(OHLCV.symbol).distinct().all()]
            onchain_symbols = [f"On-Chain: {r.symbol} - {r.metric_name}" for r in db.query(OnChainMetric.symbol, OnChainMetric.metric_name).distinct().all()]
            all_symbols = market_symbols + onchain_symbols
            
            viewer_symbol_select.options = all_symbols
            if all_symbols:
                if not viewer_symbol_select.value or viewer_symbol_select.value not in all_symbols:
                    viewer_symbol_select.value = all_symbols[0]
            else:
                viewer_symbol_select.value = None
            viewer_symbol_select.update()
            
            # Manually trigger timeframe update to guarantee options are populated on load/refresh
            update_viewer_tfs(viewer_symbol_select.value)
            
            db.close()
            ui.notify('Data refreshed!', type='info')

        # ----------------- Dialog for Bulk Download -----------------
        with ui.dialog() as download_dialog, ui.card().classes('w-[800px] max-w-4xl q-pa-md'):
            ui.label('Bulk Downloader').classes('text-xl font-bold q-mb-md')
            
            source_combo = ui.select(['binance', 'yahoo', 'coingecko', 'defillama', 'cryptoquant'], label='Source', value='binance').classes('w-full mt-2')
            
            ui.label('For Market: Select/Enter symbols. For On-Chain: Select metrics from dropdown.').classes('text-xs text-gray-500 mt-2 mb-1')
            
            onchain_metrics_map = {
                'coingecko': ['btc_market_cap', 'btc_volume'],
                'defillama': ['stablecoin_market_cap', 'usdt_market_cap', 'usdc_market_cap'],
                'cryptoquant': [
                    'exchange_netflow', 'exchange_inflow', 'exchange_outflow', 'exchange_reserve',
                    'miner_reserve', 'miner_netflow', 'puell_multiple', 'mvrv', 'nvt_golden_cross', 
                    'sopr', 'active_addresses', 'funding_rates', 'open_interest', 
                    'estimated_leverage_ratio', 'taker_buy_sell_ratio', 'nupl', 'stock_to_flow'
                ]
            }
            
            # Símbolos de Binance: se cargan de forma LAZY cuando el usuario abre el diálogo
            # para no bloquear el event loop de asyncio durante el startup.
            binance_select = ui.select(options=FALLBACK_SYMBOLS, label='Binance Symbols (cargando...)', multiple=True, with_input=True).classes('w-full mt-2').props('use-chips clearable')
            _binance_symbols_loaded = [False]

            async def _lazy_load_binance_symbols():
                """Carga los símbolos de Binance en background al abrir el diálogo."""
                if _binance_symbols_loaded[0]:
                    return
                binance_select.label = 'Binance Symbols (cargando...)'
                binance_select.update()
                try:
                    symbols = await run.io_bound(_load_binance_symbols_sync)
                    binance_select.options = symbols
                    binance_select.label = 'Binance Symbols'
                    _binance_symbols_loaded[0] = True
                    binance_select.update()
                except Exception as e:
                    logger.warning(f"Error cargando símbolos: {e}")
                    binance_select.label = 'Binance Symbols (fallback)'
                    binance_select.update()

            download_dialog.on('show', lambda _: asyncio.ensure_future(_lazy_load_binance_symbols()))

            
            yahoo_input = ui.input(label='Yahoo Symbols (comma separated)', placeholder='AAPL, TSLA, BTC-USD').classes('w-full mt-2')
            yahoo_input.set_visibility(False)
            
            onchain_select = ui.select(options=[], label='On-Chain Metrics', multiple=True).classes('w-full mt-2').props('use-chips clearable')
            onchain_select.set_visibility(False)
            
            tf_combo = ui.select(['1m', '5m', '15m', '1h', '4h', '1d'], label='Timeframe (Only for Market Data)', value='4h').classes('w-full mt-2')
            
            def on_source_change(e):
                val = getattr(e, 'value', e)
                if val == 'binance':
                    binance_select.set_visibility(True)
                    yahoo_input.set_visibility(False)
                    onchain_select.set_visibility(False)
                    tf_combo.set_visibility(True)
                elif val == 'yahoo':
                    binance_select.set_visibility(False)
                    yahoo_input.set_visibility(True)
                    onchain_select.set_visibility(False)
                    tf_combo.set_visibility(True)
                else:
                    binance_select.set_visibility(False)
                    yahoo_input.set_visibility(False)
                    onchain_select.options = onchain_metrics_map.get(val, [])
                    onchain_select.value = []
                    onchain_select.update()
                    onchain_select.set_visibility(True)
                    tf_combo.set_visibility(False)
                    
            source_combo.on_value_change(on_source_change)
            
            with ui.row().classes('w-full gap-4 mt-2'):
                start_date_input = ui.input(label='Start (YYYY-MM-DD)', value=(datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')).classes('flex-1')
                end_date_input = ui.input(label='End (YYYY-MM-DD)', value=datetime.now().strftime('%Y-%m-%d')).classes('flex-1')
            
            log_container = ui.log(max_lines=100).classes('w-full h-80 mt-4 bg-gray-900 text-green-400 font-mono text-sm')
            
            def run_download():
                is_market = source_combo.value in ['binance', 'yahoo']
                
                if source_combo.value == 'binance':
                    if not binance_select.value:
                        ui.notify('Please select at least one symbol', type='warning')
                        return
                    symbols = binance_select.value
                elif source_combo.value == 'yahoo':
                    if not yahoo_input.value:
                        ui.notify('Please enter symbols', type='warning')
                        return
                    symbols = [s.strip().upper() for s in yahoo_input.value.split(',')]
                else:
                    if not onchain_select.value:
                        ui.notify('Please select at least one metric', type='warning')
                        return
                    symbols = onchain_select.value
                
                try:
                    start_dt = datetime.strptime(start_date_input.value, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                    end_dt = datetime.strptime(end_date_input.value, '%Y-%m-%d').replace(hour=23, minute=59, tzinfo=timezone.utc)
                except Exception:
                    ui.notify('Invalid dates. Use YYYY-MM-DD', type='negative')
                    return
                
                log_container.push(f"Starting download for {len(symbols)} items...")
                
                # Para evitar bloquear la UI, se debería usar run_in_executor, pero por simplicidad se hace síncrono
                db = SessionLocal()
                
                if source_combo.value in ['binance', 'yahoo']:
                    mgr = MarketDataManager(db)
                    for sym in symbols:
                        sym = sym.upper()
                        log_container.push(f"Downloading Market Data {sym}...")
                        try:
                            mgr.update_historical_data(
                                sym, tf_combo.value, start_dt, end_dt,
                                progress_callback=lambda msg: log_container.push(msg),
                                source=source_combo.value
                            )
                        except Exception as e:
                            log_container.push(f"ERROR {sym}: {e}")
                else:
                    from data_layer.onchain_data import OnChainDataManager
                    mgr = OnChainDataManager(db)
                    for metric in symbols:
                        metric = metric.lower()
                        log_container.push(f"Downloading On-Chain Data {metric}...")
                        
                        inferred_symbol = "GLOBAL"
                        if "btc" in metric: inferred_symbol = "BTC"
                        elif "eth" in metric: inferred_symbol = "ETH"
                        elif "usdt" in metric: inferred_symbol = "USDT"
                        elif "usdc" in metric: inferred_symbol = "USDC"
                        elif source_combo.value == 'cryptoquant': inferred_symbol = "BTC"
                        
                        try:
                            count = mgr.update_historical_data(
                                metric_name=metric,
                                symbol=inferred_symbol,
                                start_date=start_dt,
                                provider_name=source_combo.value
                            )
                            if count and count > 0:
                                log_container.push(f"Successfully fetched {count} new records for {metric}")
                            else:
                                log_container.push(f"No new records found for {metric} (Empty or already up-to-date)")
                        except Exception as e:
                            log_container.push(f"ERROR {metric}: {e}")
                            
                db.close()
                log_container.push("=== Download Complete! ===")
                load_data() # refresh table
            
            ui.button('Start Download', on_click=run_download).classes('w-full mt-4 bg-green-600 text-white font-bold')
            ui.button('Close', on_click=download_dialog.close).classes('w-full mt-2')

        # ----------------- Dialog for NinjaTrader 8 Export -----------------
        with ui.dialog() as nt8_dialog, ui.card().classes('w-[650px] max-w-3xl q-pa-md bg-slate-900 text-white border border-slate-700 rounded-xl shadow-2xl'):
            ui.label('Exportar Datos para NinjaTrader 8 (.txt)').classes('text-xl font-bold text-cyan-400 mb-1')
            ui.label('Genera un archivo de datos históricos listo para importar en NinjaTrader 8 (Tools ➔ Historical Data ➔ Import).').classes('text-xs text-slate-400 mb-4')

            ui.label('Modo de Marca de Tiempo (Timestamps)').classes('text-xs font-bold text-slate-300 uppercase tracking-wider mt-2')
            nt8_ts_mode = ui.radio(
                {
                    'end_of_bar': 'Fin de la barra (End of Bar - Estándar NinjaTrader 8)',
                    'start_of_bar': 'Inicio de la barra (Start of Bar - Hora original Exchange)'
                },
                value='end_of_bar'
            ).classes('text-slate-200 text-sm my-1')

            with ui.row().classes('w-full gap-4 mt-3'):
                nt8_delimiter = ui.select({';': 'Punto y coma (;)', ',': 'Coma (,)'}, label='Delimitador de columnas', value=';').classes('w-48')
                nt8_date_fmt = ui.select({
                    'auto': 'Automático (Diario: YYYYMMDD, Intradía: YYYYMMDD HHMMSS)',
                    'daily_only': 'Solamente Fecha YYYYMMDD (Ej: 20200101;7165.72;7238.14;...)',
                    'single_field': 'YYYYMMDD HHMMSS (Fecha y Hora campo único)',
                    'split_field': 'YYYYMMDD;HHMMSS (Fecha y Hora campos separados)'
                }, label='Formato de Fecha / Hora', value='auto').classes('flex-1')

            def do_nt8_export():
                if not viewer_symbol_select.value or not viewer_tf_select.value:
                    ui.notify('Por favor selecciona primero un Símbolo y Timeframe', type='warning')
                    return
                try:
                    start_dt = datetime.strptime(viewer_start_input.value, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                    end_dt = datetime.strptime(viewer_end_input.value, '%Y-%m-%d').replace(hour=23, minute=59, tzinfo=timezone.utc)
                except Exception:
                    ui.notify('Fechas inválidas. Usa YYYY-MM-DD', type='negative')
                    return

                db = SessionLocal()
                is_market = viewer_symbol_select.value.startswith('Market:')
                if is_market:
                    symbol = viewer_symbol_select.value.replace('Market: ', '')
                    mgr = MarketDataManager(db)
                    df = mgr.get_data(symbol, viewer_tf_select.value, start_dt, end_dt)
                else:
                    parts = viewer_symbol_select.value.replace('On-Chain: ', '').split(' - ')
                    symbol = parts[0]
                    metric_name = parts[1]
                    query = db.query(OnChainMetric).filter(
                        OnChainMetric.symbol == symbol,
                        OnChainMetric.metric_name == metric_name,
                        OnChainMetric.timestamp >= start_dt.replace(tzinfo=None),
                        OnChainMetric.timestamp <= end_dt.replace(tzinfo=None)
                    ).order_by(OnChainMetric.timestamp.asc())
                    df = pd.read_sql(query.statement, db.bind)

                db.close()

                if df is None or df.empty:
                    ui.notify('No se encontraron datos almacenados para la consulta seleccionada', type='warning')
                    return

                tf_val = viewer_tf_select.value
                ts_mode = nt8_ts_mode.value
                delim = nt8_delimiter.value

                txt_content = export_df_to_ninjatrader8(
                    df=df,
                    timeframe=tf_val,
                    timestamp_mode=ts_mode,
                    delimiter=delim,
                    date_format_mode=nt8_date_fmt.value,
                    volume_as_int=True
                )

                clean_sym = symbol.replace('/', '').replace(':', '_').replace(' ', '_')
                filename = f"{clean_sym}_{tf_val}_NinjaTrader8.txt"

                ui.download(bytes(txt_content, 'utf-8'), filename=filename)
                ui.notify(f"¡Exportación exitosa! Se descargó {filename} ({len(df)} barras)", type='positive')
                nt8_dialog.close()

            with ui.row().classes('w-full gap-4 mt-6'):
                ui.button('Descargar Archivo NinjaTrader 8 (.txt)', on_click=do_nt8_export, icon='file_download').classes('flex-1 bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-3 shadow-lg')
                ui.button('Cancelar', on_click=nt8_dialog.close).classes('bg-slate-700 hover:bg-slate-600 text-white px-6')

        def open_nt8_dialog():
            if not viewer_symbol_select.value or not viewer_tf_select.value:
                ui.notify('Selecciona primero un Símbolo y Timeframe en el panel', type='warning')
                return
            nt8_dialog.open()
            
        # Initial load
        load_data()
