from nicegui import ui
from data_layer.storage import SessionLocal, OHLCV, OnChainMetric
from data_layer.market_data import MarketDataManager
from sqlalchemy import func
from datetime import datetime, timezone, timedelta

def render_market_analyzer():
    with ui.column().classes('w-full q-pa-md'):
        ui.label('Market Analyzer & Data Catalog').classes('text-2xl font-bold text-primary q-mb-md')
        
        columns = [
            {'name': 'symbol', 'label': 'Symbol', 'field': 'symbol', 'sortable': True},
            {'name': 'type', 'label': 'Type', 'field': 'type', 'sortable': True},
            {'name': 'metric', 'label': 'Timeframe/Metric', 'field': 'metric', 'sortable': True},
            {'name': 'start', 'label': 'Start Date', 'field': 'start'},
            {'name': 'end', 'label': 'End Date', 'field': 'end'},
        ]
        
        table = ui.table(columns=columns, rows=[], row_key='name').classes('w-full')
        
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
                    'start': str(row.start) if row.start else 'N/A',
                    'end': str(row.end) if row.end else 'N/A'
                })
                
            # OnChain data
            onchain_query = db.query(
                OnChainMetric.metric_name, 
                OnChainMetric.symbol, 
                func.min(OnChainMetric.timestamp).label('start'),
                func.max(OnChainMetric.timestamp).label('end')
            ).group_by(OnChainMetric.metric_name, OnChainMetric.symbol)
            
            for row in onchain_query:
                rows.append({
                    'symbol': row.symbol,
                    'type': 'On-Chain',
                    'metric': row.metric_name,
                    'start': str(row.start) if row.start else 'N/A',
                    'end': str(row.end) if row.end else 'N/A'
                })
                
            table.rows = rows
            table.update()
            db.close()
            ui.notify('Data refreshed!', type='info')

        # Dialog for Bulk Download
        with ui.dialog() as download_dialog, ui.card().classes('w-[500px] q-pa-md'):
            ui.label('Bulk Downloader').classes('text-xl font-bold q-mb-md')
            
            source_combo = ui.select(['binance', 'yahoo'], label='Source', value='binance').classes('w-full mt-2')
            symbols_input = ui.input(label='Symbols (comma separated)', placeholder='BTC/USDT, ETH/USDT').classes('w-full mt-2')
            tf_combo = ui.select(['1m', '5m', '15m', '1h', '4h', '1d'], label='Timeframe', value='4h').classes('w-full mt-2')
            
            with ui.row().classes('w-full gap-4 mt-2'):
                start_date_input = ui.input(label='Start (YYYY-MM-DD)', value=(datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')).classes('flex-1')
                end_date_input = ui.input(label='End (YYYY-MM-DD)', value=datetime.now().strftime('%Y-%m-%d')).classes('flex-1')
            
            log_container = ui.log(max_lines=20).classes('w-full h-40 mt-4 bg-gray-900 text-green-400')
            
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

        with ui.row().classes('w-full justify-between items-center q-mt-md'):
            ui.button('Refresh Data', on_click=load_data, icon='refresh').classes('bg-blue-500 text-white')
            ui.button('Bulk Downloader', on_click=download_dialog.open).classes('bg-green-500 text-white')
            
        # Initial load
        load_data()
