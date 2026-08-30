import pandas as pd
import plotly.graph_objects as from_plotly
from plotly.subplots import make_subplots
from nicegui import ui, background_tasks
from datetime import datetime, timezone, timedelta
from data_layer.storage import SessionLocal, OnChainMetric
from data_layer.onchain_flows import BlockExplorerClient
from data_layer.onchain_data import OnChainDataManager
from data_layer.market_data import MarketDataManager
import asyncio
import concurrent.futures

def fetch_data_async(symbol, days, metric=None):
    """
    Función síncrona que envuelve las llamadas pesadas de APIs para correr en threadpool.
    """
    start_date = datetime.now(timezone.utc) - timedelta(days=days)
    
    cryptoquant_metrics = [
        'exchange_netflow', 'exchange_inflow', 'exchange_outflow', 'exchange_reserve',
        'miner_reserve', 'miner_netflow', 'puell_multiple', 'mvrv', 'nvt_golden_cross', 
        'sopr', 'active_addresses', 'funding_rates', 'open_interest', 
        'estimated_leverage_ratio', 'taker_buy_sell_ratio', 'nupl', 'stock_to_flow'
    ]
    
    records_saved = 0
    metric_lower = metric.lower() if metric else ""
    
    if metric_lower in cryptoquant_metrics or symbol in ['BTC', 'ETH']:
        # Usar CryptoQuant provider
        db = SessionLocal()
        mgr = OnChainDataManager(db)
        try:
            mapped_metric = metric_lower
            if mapped_metric == 'total_supply':
                mapped_metric = 'btc_market_cap' if symbol == 'BTC' else 'stablecoin_market_cap'
                provider = 'coingecko' if symbol == 'BTC' else 'defillama'
            else:
                provider = 'cryptoquant'
                
            count = mgr.update_historical_data(
                metric_name=mapped_metric,
                symbol=symbol,
                start_date=start_date,
                provider_name=provider
            )
            records_saved = count if count else 0
        except Exception as e:
            print(f"Error fetching {metric}: {e}")
            raise e
        finally:
            db.close()
            
    if symbol in ['USDT', 'USDC']:
        # Legacy BlockExplorer metrics (para Stablecoins)
        client = BlockExplorerClient()
        mints_burns = client.fetch_stablecoin_supply(symbol, start_date)
        flows = client.fetch_exchange_flows(symbol, start_date)
        supply = client.fetch_total_supply(symbol, start_date)
        records_saved += (mints_burns + flows + supply)
    
    # 2. Asegurar que tenemos precios históricos en DB
    market_symbol = f"{symbol}/USDT" if symbol != "USDT" else "BTC/USDT"
    
    market_mgr = MarketDataManager()
    market_mgr.update_historical_data(market_symbol, '1d', start_date)
    
    return records_saved

def render_onchain_analyzer():
    """
    Renderiza el módulo visual de Análisis On-Chain.
    """
    with ui.column().classes('w-full h-full p-4 fade-in'):
        ui.label('Módulo de Análisis On-Chain').classes('text-2xl font-bold font-heading text-white mb-2')
        ui.label('Monitoreo de Mints, Burns y Flujos de Liquidez hacia Exchanges.').classes('text-sm text-slate-400 mb-6')

        # Contenedor Superior: Controles
        with ui.row().classes('w-full items-end gap-4 bg-obsidian p-4 rounded-xl border border-slate-800 mb-6'):
            symbol_select = ui.select(
                options=['BTC', 'ETH', 'USDT', 'USDC'],
                value='BTC',
                label='Activo / Moneda'
            ).classes('w-48')
            
            days_input = ui.number(
                label='Días de Histórico', 
                value=30, 
                min=1, 
                max=3650
            ).classes('w-32')
            
            metric_select = ui.select(
                options=[
                    'Mint', 'Burn', 'Exchange_Inflow', 'Exchange_Outflow', 'Total_Supply',
                    'exchange_netflow', 'exchange_reserve', 'miner_reserve', 'puell_multiple', 'mvrv', 'nupl'
                ],
                value='Exchange_Inflow',
                label='Métrica On-Chain'
            ).classes('w-64')

            fetch_btn = ui.button('Sincronizar APIs', icon='sync').classes('bg-amber-500 text-slate-900 font-bold')
            plot_btn = ui.button('Graficar Datos', icon='insights').classes('bg-slate-700 text-white font-bold')
            
            loading_spinner = ui.spinner('dots', size='lg', color='amber').classes('ml-4')
            loading_spinner.set_visibility(False)

        # Contenedor Medio: Gráfico
        chart_container = ui.column().classes('w-full bg-obsidian p-4 rounded-xl border border-slate-800 mb-6 min-h-[500px]')
        
        # Contenedor de Información (Leyenda)
        info_container = ui.column().classes('w-full bg-obsidian p-4 rounded-xl border border-slate-800 mb-6 hidden')
        
        # Contenedor Inferior: Tabla de Datos
        table_container = ui.column().classes('w-full')

        async def on_fetch_click():
            symbol = symbol_select.value
            days = int(days_input.value)
            metric = metric_select.value
            
            fetch_btn.disable()
            loading_spinner.set_visibility(True)
            ui.notify(f"Descargando datos on-chain para {symbol} (últimos {days} días)...", type='info')
            
            try:
                loop = asyncio.get_running_loop()
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    records_saved = await loop.run_in_executor(pool, fetch_data_async, symbol, days, metric)
                
                ui.notify(f"Sincronización completada. {records_saved} nuevos registros guardados.", type='positive')
                await on_plot_click()
            except Exception as e:
                ui.notify(f"Error en la sincronización: {e}", type='negative')
            finally:
                fetch_btn.enable()
                loading_spinner.set_visibility(False)

        async def on_plot_click():
            symbol = symbol_select.value
            metric_suffix = metric_select.value
            metric_name = f"{symbol}_{metric_suffix}"
            days = int(days_input.value)
            start_date = datetime.now(timezone.utc) - timedelta(days=days)
            
            
            chart_container.clear()
            info_container.clear()
            info_container.classes(remove='hidden')
            table_container.clear()
            
            # Consultar Datos On-Chain
            db = SessionLocal()
            
            mapped_metric = metric_suffix.lower()
            if mapped_metric == 'total_supply' and symbol == 'BTC':
                mapped_metric = 'btc_market_cap'
                
            query = db.query(OnChainMetric).filter(
                OnChainMetric.metric_name.in_([metric_name, metric_suffix, mapped_metric]),
                OnChainMetric.symbol == symbol,
                OnChainMetric.timestamp >= start_date.replace(tzinfo=None)
            ).order_by(OnChainMetric.timestamp.asc())
            
            df_onchain = pd.read_sql(query.statement, db.bind)
            
            if df_onchain.empty:
                with chart_container:
                    ui.label('No hay datos on-chain para graficar. Ejecuta "Sincronizar APIs".').classes('text-red-400')
                db.close()
                return
                
            df_onchain['timestamp'] = pd.to_datetime(df_onchain['timestamp']).dt.tz_localize('UTC')
            df_onchain.set_index('timestamp', inplace=True)
            
            # Agrupar diario para mejor visualización
            if 'Total_Supply' in metric_name or 'market_cap' in mapped_metric:
                df_onchain_daily = df_onchain[['value']].resample('1D').last()
            else:
                df_onchain_daily = df_onchain[['value']].resample('1D').sum().fillna(0)
            
            # Alinear fechas para asegurar un eje X continuo (evita barras invisibles en Plotly)
            date_range = pd.date_range(
                start=start_date.date(), 
                end=datetime.now(timezone.utc).date(), 
                freq='1D'
            )
            df_onchain_daily.index = df_onchain_daily.index.tz_localize(None).normalize()
            
            if 'Total_Supply' in metric_name or 'market_cap' in mapped_metric:
                df_onchain_daily = df_onchain_daily.reindex(date_range).ffill().dropna()
            else:
                df_onchain_daily = df_onchain_daily.reindex(date_range, fill_value=0)

            # Consultar Precio de Mercado para Eje Secundario
            market_symbol = f"{symbol}/USDT" if symbol != "USDT" else "BTC/USDT"
            market_mgr = MarketDataManager()
            df_market = market_mgr.get_data(market_symbol, '1d', start_date)
            
            # Crear Gráfico Plotly con Subplots (Eje Secundario)
            fig = make_subplots(specs=[[{"secondary_y": True}]])
            
            # Añadir Serie On-Chain (Barras o Línea según la métrica)
            is_supply = ('Total_Supply' in metric_name or 'market_cap' in mapped_metric)
            bar_color = '#10b981' if 'inflow' in metric_name.lower() or 'mint' in metric_name.lower() else '#ef4444'
            
            if is_supply:
                fig.add_trace(
                    from_plotly.Scatter(
                        x=df_onchain_daily.index.astype(str).tolist(), 
                        y=df_onchain_daily['value'].tolist(), 
                        name=metric_name, 
                        line=dict(color='#3b82f6', width=3)
                    ),
                    secondary_y=False,
                )
            else:
                fig.add_trace(
                    from_plotly.Bar(
                        x=df_onchain_daily.index.astype(str).tolist(), 
                        y=df_onchain_daily['value'].tolist(), 
                        name=metric_name, 
                        marker_color=bar_color,
                        opacity=0.8
                    ),
                    secondary_y=False,
                )
            
            # Añadir Serie Precio (Línea)
            if not df_market.empty:
                fig.add_trace(
                    from_plotly.Scatter(
                        x=df_market.index.astype(str).tolist(), 
                        y=df_market['close'].tolist(), 
                        name=f'Precio {market_symbol}', 
                        line=dict(color='#f59e0b', width=2)
                    ),
                    secondary_y=True,
                )
            
            # Calcular padding dinámico para Total_Supply para que la línea no toque el borde inferior
            yaxis_config = dict(showgrid=True, gridcolor='#1e293b', title='Volumen (Tokens)')
            if is_supply:
                min_y = df_onchain_daily['value'].min()
                max_y = df_onchain_daily['value'].max()
                if pd.notna(min_y) and pd.notna(max_y):
                    padding = (max_y - min_y) * 0.2 if min_y != max_y else min_y * 0.1
                    yaxis_config['range'] = [min_y - padding, max_y + padding]

            fig.update_layout(
                title=f'{metric_name} vs Precio de Mercado',
                paper_bgcolor='#0a0e17',
                plot_bgcolor='#0a0e17',
                font=dict(color='#cbd5e1'),
                xaxis=dict(showgrid=False, zeroline=False),
                yaxis=yaxis_config,
                yaxis2=dict(showgrid=False, title='Precio (USDT)'),
                margin=dict(l=40, r=40, t=60, b=40),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )

            with chart_container:
                ui.plotly(fig).classes('w-full h-[500px]')
                
            # Renderizar Leyenda de Información
            with info_container:
                ui.label('Ficha Técnica de la Métrica').classes('text-lg font-bold text-slate-300 mb-2')
                descriptions = {
                    'Mint': 'Creación de nuevos tokens (expansión monetaria). Muestra la entrada de dinero fiat al ecosistema. Generalmente es una señal Alcista (Bullish).',
                    'Burn': 'Destrucción de tokens (contracción monetaria). Representa retiros de liquidez del mercado hacia cuentas bancarias tradicionales. Suele ser una señal Bajista (Bearish).',
                    'Exchange_Inflow': 'Depósitos desde billeteras privadas hacia Exchanges. Un Inflow masivo de stablecoins representa "poder de compra" (municiones) listo para dispararse (Bullish).',
                    'Exchange_Outflow': 'Retiros desde Exchanges hacia billeteras frías. En el caso de stablecoins, indica una reducción en la liquidez inmediata para comprar activos (Bearish).',
                    'Total_Supply': 'Oferta Total Circulante de la Stablecoin en todo el mercado cripto global (incluye todas las redes). Representa la masa monetaria total (Liquidez Global).',
                    'exchange_netflow': 'Diferencia entre Inflow y Outflow en exchanges. Valores positivos indican más depósitos (Bearish para BTC), valores negativos indican más retiros (Bullish).',
                    'exchange_reserve': 'Cantidad total de monedas guardadas en las wallets de los exchanges. Si sube, hay mayor presión de venta. Si baja, los inversores están acumulando en wallets frías.',
                    'miner_reserve': 'Cantidad de monedas en las carteras de los mineros. Una caída indica que los mineros están vendiendo para cubrir gastos (Bearish).',
                    'puell_multiple': 'Ratio entre el valor de emisión diaria de BTC y su media móvil anual. Valores bajos indican zona de compra, valores altos zona de venta.',
                    'mvrv': 'Ratio entre el Market Cap y el Realized Cap. Ayuda a identificar si el precio está sobrevalorado (techo) o infravalorado (suelo).',
                    'nupl': 'Net Unrealized Profit/Loss. Mide el estado de ganancias/pérdidas no realizadas de toda la red. Ayuda a ver el sentimiento general.'
                }
                desc = descriptions.get(metric_suffix, '')
                
                with ui.row().classes('gap-8 w-full text-slate-400 text-sm'):
                    with ui.column().classes('gap-1 flex-1'):
                        ui.label('📖 Significado').classes('font-bold text-slate-300')
                        ui.label(desc).classes('whitespace-normal')
                    with ui.column().classes('gap-1'):
                        ui.label('📊 Escala (Eje Y)').classes('font-bold text-slate-300')
                        ui.label('Unidades nativas de Tokens (Escala Lineal)')
                    with ui.column().classes('gap-1'):
                        ui.label('📡 Fuente de Datos').classes('font-bold text-slate-300')
                        source_text = 'CoinGecko API (Global Market Cap)' if 'Total_Supply' in metric_suffix else 'Etherscan API (Ethereum Mainnet)'
                        ui.label(source_text)
                
            # Renderizar Tabla de los últimos 100 registros crudos
            df_table = df_onchain.reset_index().sort_values('timestamp', ascending=False).head(100)
            df_table['timestamp'] = df_table['timestamp'].dt.strftime('%d/%m/%y %H:%M:%S')
            
            columns = [
                {'name': 'timestamp', 'label': 'Fecha', 'field': 'timestamp', 'align': 'left'},
                {'name': 'metric_name', 'label': 'Métrica', 'field': 'metric_name', 'align': 'left'},
                {'name': 'value', 'label': 'Valor (Tokens)', 'field': 'value', 'align': 'right'},
                {'name': 'source', 'label': 'Fuente', 'field': 'source', 'align': 'center'},
            ]
            
            with table_container:
                ui.label('Últimos Registros Crudos').classes('text-lg font-bold text-slate-300 mb-2 mt-4')
                ui.table(
                    columns=columns, 
                    rows=df_table.to_dict('records'), 
                    row_key='id'
                ).classes('w-full')
                
            db.close()

        fetch_btn.on_click(on_fetch_click)
        plot_btn.on_click(on_plot_click)
        
        # Carga inicial vacía
        with chart_container:
            ui.label('Selecciona una métrica y presiona "Graficar Datos" para visualizar los flujos históricos.').classes('text-slate-500 m-auto text-center w-full')
