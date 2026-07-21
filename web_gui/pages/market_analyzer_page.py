from nicegui import ui
from data_layer.storage import SessionLocal, OHLCV, OnChainMetric
from data_layer.market_data import MarketDataManager
from sqlalchemy import func
from datetime import datetime, timezone, timedelta
import pandas as pd

def render_market_analyzer():
    with ui.column().classes('w-full q-pa-md'):
        ui.label('Market Analyzer & Data Catalog').classes('text-2xl font-bold text-primary q-mb-md')
        
        # ----------------- Main Data Catalog Table -----------------
        columns = [
            {'name': 'symbol', 'label': 'Par (Symbol)', 'field': 'symbol', 'sortable': True},
            {'name': 'type', 'label': 'Tipo', 'field': 'type', 'sortable': True},
            {'name': 'metric', 'label': 'Temporalidad / Métrica', 'field': 'metric', 'sortable': True},
            {'name': 'source', 'label': 'Fuente', 'field': 'source', 'sortable': True},
            {'name': 'start', 'label': 'Fecha Inicial', 'field': 'start'},
            {'name': 'end', 'label': 'Fecha Final', 'field': 'end'},
        ]
        
        table = ui.table(columns=columns, rows=[], row_key='name').classes('w-full')
        
        with ui.row().classes('w-full justify-between items-center q-mt-md'):
            ui.button('Refresh Data', on_click=lambda: load_data(), icon='refresh').classes('bg-blue-500 text-white')
            ui.button('Bulk Downloader', on_click=lambda: download_dialog.open()).classes('bg-green-500 text-white')

        # ----------------- Data Viewer UI Elements -----------------
        ui.separator().classes('my-6')
        ui.label('Query & View Historical Data').classes('text-xl font-bold text-primary q-mb-md')
        
        with ui.row().classes('w-full gap-4 items-center'):
            viewer_symbol_select = ui.select([], label='Symbol', value=None).classes('flex-1')
            viewer_tf_select = ui.select([], label='Timeframe', value=None).classes('flex-1')
            viewer_start_input = ui.input(label='Start (YYYY-MM-DD)', value=(datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')).classes('flex-1')
            viewer_end_input = ui.input(label='End (YYYY-MM-DD)', value=datetime.now().strftime('%Y-%m-%d')).classes('flex-1')
            ui.button('Load Data', on_click=lambda: load_viewer_data(), icon='search').classes('bg-blue-600 text-white font-bold h-14 mt-2 px-6')

        viewer_columns = [
            {'name': 'timestamp', 'label': 'Timestamp', 'field': 'timestamp', 'sortable': True},
            {'name': 'open', 'label': 'Open', 'field': 'open'},
            {'name': 'high', 'label': 'High', 'field': 'high'},
            {'name': 'low', 'label': 'Low', 'field': 'low'},
            {'name': 'close', 'label': 'Close', 'field': 'close'},
            {'name': 'volume', 'label': 'Volume', 'field': 'volume'},
        ]
        
        ui.label('Price Chart').classes('text-lg font-bold mt-4')
        viewer_chart = ui.echart({
            'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'cross'}},
            'xAxis': {'type': 'category', 'data': []},
            'yAxis': {'scale': True},
            'series': [{'name': 'Price', 'type': 'candlestick', 'data': []}],
        }).classes('w-full h-96 border rounded-lg p-2 bg-white mt-2')

        viewer_table = ui.table(columns=viewer_columns, rows=[], row_key='timestamp').classes('w-full mt-4')

        # ----------------- Logic -----------------
        def update_viewer_tfs(val):
            if not val:
                viewer_tf_select.options = []
                viewer_tf_select.value = None
                viewer_tf_select.update()
                return
            db = SessionLocal()
            tfs = [r[0] for r in db.query(OHLCV.timeframe).filter(OHLCV.symbol == val).distinct().all()]
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
            mgr = MarketDataManager(db)
            df = mgr.get_data(viewer_symbol_select.value, viewer_tf_select.value, start_dt, end_dt)
            db.close()
            
            if df.empty:
                ui.notify('No data found for the selected query', type='info')
                viewer_table.rows = []
                viewer_table.update()
                viewer_chart.options['xAxis']['data'] = []
                viewer_chart.options['series'][0]['data'] = []
                viewer_chart.update()
                return
                
            if len(df) > 1000:
                ui.notify('Showing first 1000 candles of query', type='info')
                df = df.head(1000)
                
            df_reset = df.reset_index()
            rows = []
            for _, r in df_reset.iterrows():
                rows.append({
                    'timestamp': str(r['timestamp'])[:19],
                    'open': f"{r['open']:.4f}",
                    'high': f"{r['high']:.4f}",
                    'low': f"{r['low']:.4f}",
                    'close': f"{r['close']:.4f}",
                    'volume': f"{r['volume']:.2f}",
                })
            
            viewer_table.rows = rows
            viewer_table.update()
            
            viewer_chart.options['xAxis']['data'] = df.index.strftime('%Y-%m-%d %H:%M').tolist()
            viewer_chart.options['series'][0]['data'] = df[['open', 'close', 'low', 'high']].values.tolist()
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
                    'start': str(row.start) if row.start else 'N/A',
                    'end': str(row.end) if row.end else 'N/A'
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
                    'start': str(row.start) if row.start else 'N/A',
                    'end': str(row.end) if row.end else 'N/A'
                })
                
            table.rows = rows
            table.update()
            
            # Update distinct symbols for the data viewer dropdown
            distinct_symbols = [r[0] for r in db.query(OHLCV.symbol).distinct().all()]
            viewer_symbol_select.options = distinct_symbols
            if distinct_symbols:
                if not viewer_symbol_select.value or viewer_symbol_select.value not in distinct_symbols:
                    viewer_symbol_select.value = distinct_symbols[0]
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
            
            source_combo = ui.select(['binance', 'yahoo'], label='Source', value='binance').classes('w-full mt-2')
            symbols_input = ui.input(label='Symbols (comma separated)', placeholder='BTC/USDT, ETH/USDT').classes('w-full mt-2')
            tf_combo = ui.select(['1m', '5m', '15m', '1h', '4h', '1d'], label='Timeframe', value='4h').classes('w-full mt-2')
            
            with ui.row().classes('w-full gap-4 mt-2'):
                start_date_input = ui.input(label='Start (YYYY-MM-DD)', value=(datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')).classes('flex-1')
                end_date_input = ui.input(label='End (YYYY-MM-DD)', value=datetime.now().strftime('%Y-%m-%d')).classes('flex-1')
            
            log_container = ui.log(max_lines=100).classes('w-full h-80 mt-4 bg-gray-900 text-green-400 font-mono text-sm')
            
            def run_download():
                if not symbols_input.value:
                    ui.notify('Please enter symbols', type='warning')
                    return
                symbols = [s.strip().upper() for s in symbols_input.value.split(',')]
                
                try:
                    start_dt = datetime.strptime(start_date_input.value, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                    end_dt = datetime.strptime(end_date_input.value, '%Y-%m-%d').replace(hour=23, minute=59, tzinfo=timezone.utc)
                except Exception:
                    ui.notify('Invalid dates. Use YYYY-MM-DD', type='negative')
                    return
                
                log_container.push(f"Starting download for {len(symbols)} symbols...")
                
                # Para evitar bloquear la UI, se debería usar run_in_executor, pero por simplicidad se hace síncrono
                db = SessionLocal()
                mgr = MarketDataManager(db)
                
                for sym in symbols:
                    log_container.push(f"Downloading {sym}...")
                    try:
                        mgr.update_historical_data(
                            sym, tf_combo.value, start_dt, end_dt,
                            progress_callback=lambda msg: log_container.push(msg),
                            source=source_combo.value
                        )
                    except Exception as e:
                        log_container.push(f"ERROR {sym}: {e}")
                        
                db.close()
                log_container.push("=== Download Complete! ===")
                load_data() # refresh table
            
            ui.button('Start Download', on_click=run_download).classes('w-full mt-4 bg-green-600 text-white font-bold')
            ui.button('Close', on_click=download_dialog.close).classes('w-full mt-2')
            
        # Initial load
        load_data()
