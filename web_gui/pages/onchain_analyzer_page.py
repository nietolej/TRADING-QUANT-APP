import os
import time
import requests
import pandas as pd
import plotly.graph_objects as from_plotly
from plotly.subplots import make_subplots
from nicegui import ui, background_tasks
from datetime import datetime, timezone, timedelta
from data_layer.storage import SessionLocal, OnChainMetric
from data_layer.onchain_flows import BlockExplorerClient
from data_layer.onchain_data import OnChainDataManager
from data_layer.market_data import MarketDataManager
from data_layer.unified_dataset import build_unified_daily, get_unified_wide_df
import asyncio
import concurrent.futures


def _glassnode_key_configured() -> bool:
    key = os.getenv("GLASSNODE_API_KEY")
    return bool(key) and key != "tu_clave_glassnode_aqui"

# Métricas soportadas por símbolo: la UI solo debe ofrecer combinaciones válidas, ya que
# antes un mismo dropdown mezclaba métricas de stablecoins (Mint/Burn, vía Etherscan) con
# métricas de CryptoQuant (solo BTC/ETH) y una combinación inválida (ej. BTC + Mint) caía
# silenciosamente en el proveedor equivocado, devolviendo "0 nuevos registros" sin explicar
# por qué.
CRYPTOQUANT_METRICS = [
    'exchange_netflow', 'exchange_inflow', 'exchange_outflow', 'exchange_reserve',
    'miner_reserve', 'miner_netflow', 'puell_multiple', 'mvrv', 'nvt_golden_cross',
    'sopr', 'active_addresses', 'funding_rates', 'open_interest',
    'estimated_leverage_ratio', 'taker_buy_sell_ratio', 'nupl', 'stock_to_flow'
]
STABLECOIN_METRICS = [
    'Mint', 'Burn', 'Exchange_Inflow', 'Exchange_Outflow', 'Exchange_Netflow',
    'Exchange_Reserve', 'Total_Supply'
]

# Métricas de flujo (aditivas): representan una cantidad que ocurrió durante el período
# (transferencias, depósitos/retiros), así que sumarlas dentro del día y rellenar huecos
# con 0 es correcto. El resto de métricas del módulo son niveles/ratios (MVRV, SOPR,
# reservas, funding rate, open interest, active addresses...) — sumar varias lecturas del
# mismo día o rellenar un hueco con 0 falsea el valor (ej. un MVRV de 0 en un día sin dato
# se ve idéntico a un MVRV real de 0). Ver auditoría 2026-09-17.
FLOW_METRICS = {
    'mint', 'burn', 'exchange_inflow', 'exchange_outflow', 'exchange_netflow',
}

# Subconjunto de CRYPTOQUANT_METRICS que Binance también publica gratis y sin API key
# (endpoints públicos de Futures) — ver data_sources/binance_public_provider.py. El resto
# de métricas de CryptoQuant (mvrv, sopr, puell_multiple, exchange_netflow, etc.) son
# cálculos propietarios sin equivalente público gratuito.
FREE_BINANCE_METRICS = {'funding_rates', 'open_interest', 'taker_buy_sell_ratio'}

# Métricas que Glassnode también publica. CORRECCIÓN (2026-09-16): a diferencia de lo que
# se asumió inicialmente, el API de Glassnode NO tiene tier gratuito — su propia
# documentación confirma que hasta el "Light API" (14 días de historia, resolución diaria,
# 50 llamadas/día) exige el plan de pago "Advanced" (~USD 49-99/mes). Se deja como
# alternativa a CryptoQuant únicamente porque puede resultar más barata según el caso, NO
# porque sea gratuita — ambas requieren pagar. exchange_reserve de BTC es la única de este
# grupo con alternativa 100% gratuita (ver blockchain_info_provider.py).
GLASSNODE_METRICS = {
    'sopr', 'puell_multiple', 'mvrv', 'nupl', 'active_addresses',
    'exchange_netflow', 'exchange_inflow', 'exchange_outflow', 'exchange_reserve',
    'miner_reserve'
}

METRICS_BY_SYMBOL = {
    'BTC': CRYPTOQUANT_METRICS + ['Total_Supply'],
    'ETH': list(CRYPTOQUANT_METRICS),
    'USDT': list(STABLECOIN_METRICS),
    'USDC': list(STABLECOIN_METRICS),
    # Vista combinada: mismas métricas que un stablecoin individual, pero agregando
    # USDT + USDC (ej. Total_Supply combinado = oferta total de ambos stablecoins).
    'USDT+USDC': list(STABLECOIN_METRICS),
}


_availability_cache = {}
_AVAILABILITY_TTL_SECONDS = 3600  # revalidar cada hora, no en cada render del selector


def _cryptoquant_indicator_access_ok() -> bool:
    """CryptoQuant expone `market-data` (OHLCV) en cualquier plan, pero MVRV/SOPR/Puell
    Multiple/exchange-flows/miner-flows/etc viven bajo endpoints 'market-indicator',
    'network-indicator', 'exchange-flows' y 'miner-flows' que devuelven 403 Forbidden si
    el plan de la cuenta no los incluye (confirmado en auditoría 2026-09-18: la misma key
    responde 200 en market-data/price-ohlcv y 403 en TODOS los indicadores/flows). En vez
    de asumir estáticamente que están disponibles, se hace una sola llamada barata
    (limit=1) y se cachea el resultado para reflejar cambios de suscripción sin reiniciar
    la app."""
    cached = _availability_cache.get('cryptoquant_indicators')
    if cached and (time.time() - cached[1]) < _AVAILABILITY_TTL_SECONDS:
        return cached[0]

    key = os.getenv("CRYPTOQUANT_API_KEY", "").strip()
    ok = False
    if key and key != "tu_clave_api_aqui":
        try:
            r = requests.get(
                "https://api.cryptoquant.com/v1/btc/market-indicator/mvrv",
                params={"window": "day", "limit": 1},
                headers={"Authorization": f"Bearer {key}"},
                timeout=5,
            )
            ok = r.status_code == 200
        except Exception:
            ok = False

    _availability_cache['cryptoquant_indicators'] = (ok, time.time())
    return ok


def _cryptoquant_market_data_access_ok() -> bool:
    """funding_rates/open_interest/taker_buy_sell_ratio viven bajo 'market-data', que es un
    tier DISTINTO de 'market-indicator'/'network-indicator'/'exchange-flows'/'miner-flows'
    (ver _cryptoquant_indicator_access_ok): un plan sin acceso a indicadores puede sí tener
    acceso a market-data (confirmado en auditoría 2026-09-18: el plan actual devuelve 200
    en funding-rates/open-interest/taker-buy-sell-stats con el parámetro 'exchange', pero
    403 en mvrv/sopr/exchange-flows/etc). Se prueba por separado para no ocultar estas 3
    métricas solo porque las 14 "premium" no están disponibles."""
    cached = _availability_cache.get('cryptoquant_market_data')
    if cached and (time.time() - cached[1]) < _AVAILABILITY_TTL_SECONDS:
        return cached[0]

    key = os.getenv("CRYPTOQUANT_API_KEY", "").strip()
    ok = False
    if key and key != "tu_clave_api_aqui":
        try:
            r = requests.get(
                "https://api.cryptoquant.com/v1/btc/market-data/funding-rates",
                params={"window": "day", "exchange": "binance", "limit": 1},
                headers={"Authorization": f"Bearer {key}"},
                timeout=5,
            )
            ok = r.status_code == 200
        except Exception:
            ok = False

    _availability_cache['cryptoquant_market_data'] = (ok, time.time())
    return ok


def _binance_public_reachable() -> bool:
    """Los endpoints públicos de Binance Futures (funding rate, open interest, taker
    ratio) pueden estar geobloqueados (HTTP 451 'restricted location') según la IP/región
    desde donde corre el servidor (confirmado en auditoría 2026-09-18). Se cachea el
    resultado de una llamada mínima para no repetir la prueba en cada render del selector."""
    cached = _availability_cache.get('binance_public')
    if cached and (time.time() - cached[1]) < _AVAILABILITY_TTL_SECONDS:
        return cached[0]

    ok = False
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/fundingRate",
            params={"symbol": "BTCUSDT", "limit": 1},
            timeout=5,
        )
        ok = r.status_code == 200
    except Exception:
        ok = False

    _availability_cache['binance_public'] = (ok, time.time())
    return ok


def _available_metrics_for(symbol: str) -> list:
    """Filtra METRICS_BY_SYMBOL a solo las métricas que la app puede descargar AHORA MISMO
    con la configuración/red actuales, para que el selector no ofrezca combinaciones que
    siempre van a fallar (CryptoQuant sin acceso a indicadores, Binance Futures
    geobloqueado, Glassnode sin key). Las de stablecoins (Etherscan/DefiLlama) no dependen
    de ningún plan de pago, así que siempre están disponibles."""
    all_metrics = METRICS_BY_SYMBOL.get(symbol, [])
    if symbol not in ('BTC', 'ETH'):
        return list(all_metrics)

    cq_ok = _cryptoquant_indicator_access_ok()
    cq_market_data_ok = _cryptoquant_market_data_access_ok()
    glassnode_ok = _glassnode_key_configured()
    binance_ok = _binance_public_reachable()

    available = []
    for m in all_metrics:
        if m == 'Total_Supply' or (m == 'exchange_reserve' and symbol == 'BTC'):
            # BTC vía blockchain.info: balance/oferta real, gratis y sin key, siempre disponible.
            available.append(m)
        elif m in FREE_BINANCE_METRICS:
            if binance_ok or cq_market_data_ok:
                available.append(m)
        elif cq_ok or (m in GLASSNODE_METRICS and glassnode_ok):
            available.append(m)
    return available


def _symbols_for(selection: str) -> list:
    """'USDT+USDC' -> ['USDT', 'USDC']; cualquier otro símbolo -> [símbolo]."""
    return selection.split('+') if '+' in selection else [selection]


def _market_symbol_for(selection: str) -> str:
    """Par de mercado usado como referencia de precio para el símbolo/selección elegida."""
    symbols = _symbols_for(selection)
    if len(symbols) > 1 or symbols[0] == 'USDT':
        # USDT/USDT no es un par válido, y para la vista combinada no hay un único par
        # representativo, así que se usa BTC/USDT como referencia general de mercado.
        return 'BTC/USDT'
    return f"{symbols[0]}/USDT"

# Métricas cuya profundidad histórica real está topada por la fuente gratuita usada (no
# por esta app) — ver auditoría 2026-09-17. cap_days=0 significa "sin histórico en
# absoluto, solo el valor actual en cada sincronización". Se usa para avisar al usuario
# en vez de dejar que un rango de días pedido parezca engañosamente completo.
METRIC_HISTORY_CAVEATS = {
    'exchange_reserve': (0, (
        'Esta fuente solo expone el balance ACTUAL de la(s) wallet(s) rastreada(s), no '
        'histórico: cada sincronización agrega un único punto "ahora". La serie mostrada '
        'solo cubre desde que empezaste a sincronizar, no los {days} días pedidos.'
    )),
    'open_interest': (30, (
        'Binance Futures solo expone los últimos 30 días en este endpoint. Pediste {days} '
        'días; los anteriores a hace 30 días no existen en la fuente.'
    )),
    'taker_buy_sell_ratio': (30, (
        'Binance Futures solo expone los últimos 30 días en este endpoint. Pediste {days} '
        'días; los anteriores a hace 30 días no existen en la fuente.'
    )),
}


def _history_caveat(metric_suffix: str, days: int) -> str:
    """Devuelve un aviso de límite histórico para la métrica/rango pedido, o '' si no aplica."""
    entry = METRIC_HISTORY_CAVEATS.get(metric_suffix.lower())
    if not entry:
        return ''
    cap_days, template = entry
    if days <= cap_days:
        return ''
    return template.format(days=days)

def fetch_data_async(symbol, days, metric=None):
    """
    Función síncrona que envuelve las llamadas pesadas de APIs para correr en threadpool.
    """
    start_date = datetime.now(timezone.utc) - timedelta(days=days)

    valid_metrics_lower = {m.lower() for m in METRICS_BY_SYMBOL.get(symbol, [])}
    metric_lower = metric.lower() if metric else ""
    if metric_lower not in valid_metrics_lower:
        raise ValueError(
            f"La métrica '{metric}' no aplica para el símbolo '{symbol}'. "
            f"Métricas válidas para {symbol}: {', '.join(METRICS_BY_SYMBOL.get(symbol, []))}."
        )

    records_saved = 0

    if symbol in ['BTC', 'ETH']:
        db = SessionLocal()
        mgr = OnChainDataManager(db)
        try:
            mapped_metric = metric_lower
            if mapped_metric == 'total_supply' and symbol == 'BTC':
                # Oferta circulante real de BTC (no market cap) desde el bloque génesis,
                # vía el endpoint público de Charts de blockchain.info — reemplaza a
                # CoinGecko, que topaba el histórico a 365 días (ver auditoría 2026-09-17).
                provider = 'blockchain_info'
            elif mapped_metric == 'exchange_reserve' and symbol == 'BTC':
                # Sin costo ni API key en absoluto: balance real de una wallet de Binance
                # públicamente verificada (ver blockchain_info_provider.py). Preferida sobre
                # Glassnode/CryptoQuant, que para esta métrica exigen suscripción de pago.
                provider = 'blockchain_info'
            elif mapped_metric in FREE_BINANCE_METRICS and _binance_public_reachable():
                # Sin costo ni API key: usa los endpoints públicos de Binance Futures en
                # vez de CryptoQuant para las métricas que Binance sí publica gratis. Si
                # Binance Futures está geobloqueado (HTTP 451 según la IP/región del
                # servidor, ver auditoría 2026-09-18) se cae a CryptoQuant más abajo en vez
                # de fallar directo: funding_rates/open_interest/taker_buy_sell_ratio SÍ
                # están disponibles ahí (endpoint 'market-data', un tier distinto al de los
                # indicadores premium que si devuelven 403 con el plan actual).
                provider = 'binance_public'
            elif mapped_metric in GLASSNODE_METRICS and _glassnode_key_configured():
                # NOTA: Glassnode NO es gratis (se verificó el 2026-09-16 en su propia
                # documentación) — el API mínimo (Light API) exige plan "Advanced" de pago.
                # Se usa solo si GLASSNODE_API_KEY está configurada (cuenta de pago propia);
                # de lo contrario cae a CryptoQuant, que cubre el mismo conjunto de métricas
                # (ver GLASSNODE_METRICS ⊆ CRYPTOQUANT_METRICS) y sí puede estar ya pagado.
                # Antes esto se enrutaba SIEMPRE a Glassnode sin verificar la key, así que
                # cualquier cuenta con solo CryptoQuant configurado (el caso común) fallaba
                # en 9 de las 17 métricas de BTC/ETH con "API Key de Glassnode no configurada",
                # aunque CryptoQuant ya tenía cobertura y credenciales válidas para todas ellas.
                provider = 'glassnode'
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
            
    # 'USDT+USDC' es la vista combinada: se sincroniza cada stablecoin por separado con
    # el mismo cliente, sumando los registros nuevos de ambos.
    stablecoin_symbols = [s for s in _symbols_for(symbol) if s in ('USDT', 'USDC')]
    if stablecoin_symbols:
        # BlockExplorer (Etherscan) metrics para Stablecoins: Mint/Burn, Inflow/Outflow,
        # Netflow (derivado de los dos anteriores) y Reserve (balance real actual de las
        # wallets rastreadas) — todas vía datos on-chain reales, gratis.
        #
        # Se llama SOLO al fetch que corresponde a la métrica pedida, no a las 4 juntas:
        # antes, elegir "Total_Supply" (una sola llamada rápida a DefiLlama) igual
        # disparaba fetch_exchange_flows contra las wallets calientes de Binance
        # (altísimo volumen de transferencias, paginación de Etherscan con sleep de
        # 0.3-0.5s por página) y podía tardar varios minutos o parecer colgado — ver
        # auditoría 2026-09-17.
        client = BlockExplorerClient()
        for sym in stablecoin_symbols:
            if metric_lower in ('mint', 'burn'):
                records_saved += client.fetch_stablecoin_supply(sym, start_date)
            elif metric_lower in ('exchange_inflow', 'exchange_outflow', 'exchange_netflow'):
                records_saved += client.fetch_exchange_flows(sym, start_date)  # incluye Netflow derivado
            elif metric_lower == 'exchange_reserve':
                records_saved += client.fetch_exchange_reserve(sym)
            elif metric_lower == 'total_supply':
                records_saved += client.fetch_total_supply(sym, start_date)

    # 2. Asegurar que tenemos precios históricos en DB
    market_symbol = _market_symbol_for(symbol)

    market_mgr = MarketDataManager()
    market_mgr.update_historical_data(market_symbol, '1d', start_date)

    # 3. Reconstruir la base de datos unificada diaria (todas las métricas, todos los
    # símbolos, precio y labels de dirección futura) con lo recién descargado. Se hace
    # aquí (ya corriendo en threadpool) para que la "Tabla Diaria Completa" del módulo
    # siempre refleje la última sincronización sin un paso manual aparte.
    build_unified_daily()

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
                options=['BTC', 'ETH', 'USDT', 'USDC', 'USDT+USDC'],
                value='BTC',
                label='Activo / Moneda'
            ).classes('w-48')

            days_input = ui.number(
                label='Días de Histórico',
                value=30,
                min=1,
                max=3650
            ).classes('w-32')

            # Las opciones de métrica se filtran según el símbolo elegido (ver
            # _on_symbol_change más abajo) Y según qué proveedores están realmente
            # disponibles ahora mismo (_available_metrics_for): antes el dropdown ofrecía
            # métricas de CryptoQuant/Binance que siempre fallaban (403 por plan sin
            # acceso a indicadores, o 451 por geobloqueo), dejando al usuario descubrirlo
            # recién al sincronizar. Ver auditoría 2026-09-18.
            initial_metrics = _available_metrics_for('BTC')
            metric_select = ui.select(
                options=initial_metrics,
                value=initial_metrics[0] if initial_metrics else None,
                label='Métrica On-Chain'
            ).classes('w-64')

            no_metrics_label = ui.label(
                '⚠️ Sin métricas disponibles para este activo (revisa el plan de CryptoQuant, '
                'la key de Glassnode o el bloqueo geográfico de Binance en "Conectar APIs").'
            ).classes('text-xs text-amber-400')
            no_metrics_label.set_visibility(False)

            def _on_symbol_change():
                valid_metrics = _available_metrics_for(symbol_select.value)
                metric_select.options = valid_metrics
                metric_select.value = valid_metrics[0] if valid_metrics else None
                metric_select.set_visibility(bool(valid_metrics))
                no_metrics_label.set_visibility(not valid_metrics)
                fetch_btn.set_enabled(bool(valid_metrics))
                plot_btn.set_enabled(bool(valid_metrics))
                metric_select.update()

            symbol_select.on_value_change(lambda e: _on_symbol_change())

            fetch_btn = ui.button('Sincronizar APIs', icon='sync').classes('bg-amber-500 text-slate-900 font-bold')
            plot_btn = ui.button('Graficar Datos', icon='insights').classes('bg-slate-700 text-white font-bold')
            # Reconstruye la "Tabla Diaria Completa" (data_layer/unified_dataset.py) desde
            # lo que YA está en la BD, sin volver a llamar a ninguna API — útil la primera
            # vez que se usa esta tabla, ya que "Sincronizar APIs" la reconstruye
            # automáticamente pero solo cubre el símbolo/métrica elegidos en el momento.
            rebuild_btn = ui.button('Reconstruir Base Unificada', icon='dataset').classes('bg-slate-700 text-white font-bold')

            loading_spinner = ui.spinner('dots', size='lg', color='amber').classes('ml-4')
            loading_spinner.set_visibility(False)

            # Aplicar el estado inicial (BTC) ahora que fetch_btn/plot_btn ya existen.
            no_metrics_label.set_visibility(not initial_metrics)
            fetch_btn.set_enabled(bool(initial_metrics))
            plot_btn.set_enabled(bool(initial_metrics))

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

            caveat = _history_caveat(metric, days)
            if caveat:
                ui.notify(caveat, type='warning', multi_line=True, timeout=10000, close_button=True)

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

        async def on_rebuild_click():
            rebuild_btn.disable()
            loading_spinner.set_visibility(True)
            ui.notify("Reconstruyendo base de datos unificada diaria...", type='info')
            try:
                loop = asyncio.get_running_loop()
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    rows = await loop.run_in_executor(pool, build_unified_daily)
                ui.notify(f"Base unificada reconstruida: {rows} filas (fecha x símbolo x métrica).", type='positive')
                await on_plot_click()
            except Exception as e:
                ui.notify(f"Error reconstruyendo la base unificada: {e}", type='negative')
            finally:
                rebuild_btn.enable()
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
            
            symbols = _symbols_for(symbol)

            # Consultar Datos On-Chain
            db = SessionLocal()
            try:
                mapped_metric = metric_suffix.lower()

                query = db.query(OnChainMetric).filter(
                    OnChainMetric.metric_name.in_([metric_name, metric_suffix, mapped_metric]),
                    OnChainMetric.symbol.in_(symbols),
                    OnChainMetric.timestamp >= start_date.replace(tzinfo=None)
                ).order_by(OnChainMetric.timestamp.asc())

                df_onchain = pd.read_sql(query.statement, db.bind)
            finally:
                # La conexion ya no se necesita mas alla de este punto (df_onchain ya esta
                # materializado en memoria), se cierra aqui para no dejarla abierta si algo
                # falla mas adelante en el renderizado del grafico/tabla.
                db.close()

            if df_onchain.empty:
                with chart_container:
                    ui.label('No hay datos on-chain para graficar. Ejecuta "Sincronizar APIs".').classes('text-red-400')
                return
                
            df_onchain['timestamp'] = pd.to_datetime(df_onchain['timestamp']).dt.tz_localize('UTC')
            df_onchain.set_index('timestamp', inplace=True)

            # Agrupar diario para mejor visualización. Solo las métricas de flujo (ver
            # FLOW_METRICS) son aditivas; Total_Supply/market_cap y el resto (niveles y
            # ratios: MVRV, SOPR, reservas, funding rate...) deben tomar el último valor
            # del día, no sumarlo.
            is_flow = mapped_metric in FLOW_METRICS

            # Alinear fechas para asegurar un eje X continuo (evita barras invisibles en Plotly)
            date_range = pd.date_range(
                start=start_date.date(),
                end=datetime.now(timezone.utc).date(),
                freq='1D'
            )

            # Se agrega primero DENTRO de cada símbolo (sum para flujos, last para
            # niveles) y recién después se combinan los símbolos sumando sus series
            # diarias. Es necesario para la vista 'USDT+USDC': un `.resample().last()`
            # sobre las filas de ambos símbolos mezcladas tomaría el último valor
            # cronológico de CUALQUIERA de los dos (no la suma de ambos), dando un
            # "Total_Supply combinado" incorrecto. Con un solo símbolo (caso normal),
            # este camino da exactamente el mismo resultado que antes.
            per_symbol_series = {}
            for sym in symbols:
                sub = df_onchain.loc[df_onchain['symbol'] == sym, ['value']]
                if sub.empty:
                    continue
                sub_daily = sub.resample('1D').sum() if is_flow else sub.resample('1D').last()
                sub_daily.index = sub_daily.index.tz_localize(None).normalize()
                if is_flow:
                    sub_daily = sub_daily.reindex(date_range, fill_value=0)
                else:
                    sub_daily = sub_daily.reindex(date_range).ffill().dropna()
                per_symbol_series[sym] = sub_daily['value']

            combined = pd.concat(list(per_symbol_series.values()), axis=1)
            # min_count=1: si NINGÚN símbolo tiene dato todavía para un día (ej. antes del
            # lanzamiento de ambos), el resultado queda NaN (se descarta más abajo) en vez
            # de mostrar 0 como si fuera un valor real conocido.
            combined_value = combined.sum(axis=1, skipna=True, min_count=1)
            if is_flow:
                combined_value = combined_value.fillna(0)
            else:
                combined_value = combined_value.dropna()
            df_onchain_daily = combined_value.to_frame('value')

            # Consultar Precio de Mercado para Eje Secundario
            market_symbol = _market_symbol_for(symbol)
            market_mgr = MarketDataManager()
            df_market = market_mgr.get_data(market_symbol, '1d', start_date)
            
            # Crear Gráfico Plotly con Subplots (Eje Secundario)
            fig = make_subplots(specs=[[{"secondary_y": True}]])

            # Añadir Serie On-Chain (Barras o Línea según la métrica)
            is_supply = ('Total_Supply' in metric_name or 'market_cap' in mapped_metric)
            bar_color = '#10b981' if 'inflow' in metric_name.lower() or 'mint' in metric_name.lower() else '#ef4444'

            # Vista combinada (USDT+USDC): además del total, se grafica cada símbolo por
            # separado para poder comparar el aporte de cada uno al combinado.
            is_combo = len(symbols) > 1
            combined_name = f'Combinado ({"+".join(symbols)})_{metric_suffix}' if is_combo else metric_name
            SYMBOL_COLORS = {'USDT': '#26a17b', 'USDC': '#2775ca'}  # colores de marca de cada stablecoin

            if is_supply:
                if is_combo:
                    for sym, series in per_symbol_series.items():
                        fig.add_trace(
                            from_plotly.Scatter(
                                x=series.index.astype(str).tolist(),
                                y=series.tolist(),
                                name=f'{sym}_{metric_suffix}',
                                line=dict(color=SYMBOL_COLORS.get(sym, '#94a3b8'), width=1.5, dash='dot'),
                            ),
                            secondary_y=False,
                        )
                fig.add_trace(
                    from_plotly.Scatter(
                        x=df_onchain_daily.index.astype(str).tolist(),
                        y=df_onchain_daily['value'].tolist(),
                        name=combined_name,
                        line=dict(color='#e2e8f0' if is_combo else '#3b82f6', width=3)
                    ),
                    secondary_y=False,
                )
            else:
                if is_combo:
                    for sym, series in per_symbol_series.items():
                        fig.add_trace(
                            from_plotly.Bar(
                                x=series.index.astype(str).tolist(),
                                y=series.tolist(),
                                name=f'{sym}_{metric_suffix}',
                                marker_color=SYMBOL_COLORS.get(sym, '#94a3b8'),
                                opacity=0.6,
                            ),
                            secondary_y=False,
                        )
                fig.add_trace(
                    from_plotly.Bar(
                        x=df_onchain_daily.index.astype(str).tolist(),
                        y=df_onchain_daily['value'].tolist(),
                        name=combined_name,
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
                # En la vista combinada, USDT/USDC individuales quedan muy por debajo del
                # total (ej. ~73B y ~183B vs. un combinado de ~257B): el rango del eje Y
                # debe cubrir también esas series individuales, no solo la combinada, o
                # sus líneas punteadas quedan fuera de vista (por debajo del borde
                # inferior del gráfico).
                series_for_range = [df_onchain_daily['value']] + (list(per_symbol_series.values()) if is_combo else [])
                min_y = min(s.min() for s in series_for_range)
                max_y = max(s.max() for s in series_for_range)
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
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                barmode='group' if (is_combo and not is_supply) else 'overlay',
            )

            with chart_container:
                ui.plotly(fig).classes('w-full h-[500px]')
                
            # Renderizar Leyenda de Información
            with info_container:
                ui.label('Ficha Técnica de la Métrica').classes('text-lg font-bold text-slate-300 mb-2')

                plot_caveat = _history_caveat(metric_suffix, days)
                if plot_caveat:
                    with ui.row().classes('items-start gap-2 w-full bg-amber-950/40 border border-amber-800 rounded-lg px-3 py-2 mb-3'):
                        ui.icon('warning', color='amber').classes('mt-0.5')
                        ui.label(plot_caveat).classes('text-sm text-amber-300 whitespace-normal flex-1')

                descriptions = {
                    'Mint': 'Creación de nuevos tokens (expansión monetaria). Muestra la entrada de dinero fiat al ecosistema. Generalmente es una señal Alcista (Bullish).',
                    'Burn': 'Destrucción de tokens (contracción monetaria). Representa retiros de liquidez del mercado hacia cuentas bancarias tradicionales. Suele ser una señal Bajista (Bearish).',
                    'Exchange_Inflow': 'Depósitos desde billeteras privadas hacia Exchanges. Un Inflow masivo de stablecoins representa "poder de compra" (municiones) listo para dispararse (Bullish).',
                    'Exchange_Outflow': 'Retiros desde Exchanges hacia billeteras frías. En el caso de stablecoins, indica una reducción en la liquidez inmediata para comprar activos (Bearish).',
                    'Exchange_Netflow': 'Inflow menos Outflow diario. Positivo = más depósitos que retiros (poder de compra acumulándose, Bullish); negativo = más retiros que depósitos (Bearish).',
                    'Exchange_Reserve': 'Balance actual real (on-chain) de la stablecoin en las wallets de exchange rastreadas. Sube = más liquidez disponible para comprar; baja = liquidez saliendo del exchange.',
                    'Total_Supply': 'Oferta total circulante real del activo (BTC: emitido on-chain según el calendario de minería; Stablecoins: circulante en todo el mercado cripto global). Representa la masa monetaria total (Liquidez Global).',
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
                        # Se toma del propio dato descargado (columna `source`) en vez de
                        # adivinarlo por el nombre de la métrica: antes siempre decía
                        # "Etherscan" para cualquier métrica que no fuera Total_Supply, lo
                        # cual era incorrecto para todo lo que viniera de CryptoQuant,
                        # Glassnode o Binance.
                        if 'source' in df_onchain.columns and not df_onchain['source'].empty:
                            source_text = df_onchain['source'].mode().iloc[0]
                        else:
                            source_text = 'Desconocida'
                        ui.label(source_text)
                
            # Renderizar Tabla Diaria Completa desde la base de datos unificada
            # (data_layer/unified_dataset.py): una fila por día, una columna por cada
            # combinación símbolo+métrica ya consolidada (todas las monedas sincronizadas,
            # más precio de mercado y labels de dirección futura) — a diferencia de la
            # tabla de abajo, que muestra solo los registros crudos de la métrica
            # seleccionada en el dropdown. Se lee de la tabla ya construida en vez de
            # recalcular el pivot en cada render: "Sincronizar APIs" y "Reconstruir Base
            # Unificada" son quienes la actualizan.
            df_unified_wide = get_unified_wide_df(start_date=start_date)

            with table_container:
                if not df_unified_wide.empty:
                    ui.label('Tabla Diaria Completa (Base Unificada: Todos los Símbolos, Métricas, Precio y Labels)').classes('text-lg font-bold text-slate-300 mb-2 mt-4')
                    df_daily_full = df_unified_wide.sort_index(ascending=False).round(6)
                    df_daily_full = df_daily_full.reset_index().rename(columns={'date': 'Fecha'})
                    df_daily_full['Fecha'] = pd.to_datetime(df_daily_full['Fecha']).dt.strftime('%d/%m/%Y')

                    full_columns = [{'name': 'Fecha', 'label': 'Fecha', 'field': 'Fecha', 'align': 'left', 'sortable': True}]
                    for col in df_daily_full.columns:
                        if col == 'Fecha':
                            continue
                        full_columns.append({'name': col, 'label': col, 'field': col, 'align': 'right', 'sortable': True})

                    ui.table(
                        columns=full_columns,
                        rows=df_daily_full.to_dict('records'),
                        row_key='Fecha',
                        pagination=20
                    ).classes('w-full mb-6 overflow-x-auto')
                else:
                    ui.label(
                        'La base de datos unificada aún no tiene datos para este rango. '
                        'Presiona "Reconstruir Base Unificada" (o "Sincronizar APIs").'
                    ).classes('text-sm text-amber-400 mb-4')

            # Renderizar Tabla de los últimos 100 registros crudos
            df_table = df_onchain.reset_index().sort_values('timestamp', ascending=False).head(100)
            df_table['timestamp'] = df_table['timestamp'].dt.strftime('%d/%m/%y %H:%M:%S')
            
            columns = [
                {'name': 'timestamp', 'label': 'Fecha', 'field': 'timestamp', 'align': 'left'},
                {'name': 'metric_name', 'label': 'Métrica', 'field': 'metric_name', 'align': 'left'},
                {'name': 'value', 'label': 'Valor (Tokens)', 'field': 'value', 'align': 'right'},
                {'name': 'source', 'label': 'Fuente', 'field': 'source', 'align': 'center'},
            ]
            # En la vista combinada (USDT+USDC) las filas mezclan ambos símbolos, así que
            # se agrega la columna para poder distinguirlas.
            if len(symbols) > 1:
                columns.insert(1, {'name': 'symbol', 'label': 'Símbolo', 'field': 'symbol', 'align': 'left'})
            
            with table_container:
                ui.label('Últimos Registros Crudos').classes('text-lg font-bold text-slate-300 mb-2 mt-4')
                ui.table(
                    columns=columns, 
                    rows=df_table.to_dict('records'), 
                    row_key='id'
                ).classes('w-full')

        fetch_btn.on_click(on_fetch_click)
        plot_btn.on_click(on_plot_click)
        rebuild_btn.on_click(on_rebuild_click)
        
        # Carga inicial vacía
        with chart_container:
            ui.label('Selecciona una métrica y presiona "Graficar Datos" para visualizar los flujos históricos.').classes('text-slate-500 m-auto text-center w-full')
