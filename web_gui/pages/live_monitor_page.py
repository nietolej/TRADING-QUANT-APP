from nicegui import ui, run
import asyncio
import os
import glob
import yaml
import logging
from datetime import datetime
import pandas as pd
import plotly.graph_objects as go
from typing import Optional, Dict, Any

from execution_engine.bot_manager import bot_manager, BotManager
from execution_engine.paper_trader import PaperTrader
from execution_engine.binance_client import BinanceTestnetClient

logger = logging.getLogger("LiveMonitorPage")


def _extract_base_currency(symbol: str) -> str:
    """Extrae la primera moneda (base asset) del par, ej. BTC/USDT -> BTC, ETH/USDT -> ETH."""
    if not symbol:
        return "BTC"
    clean = str(symbol).strip().upper()
    if "/" in clean:
        return clean.split("/")[0].strip()
    for quote in ["USDT", "BUSD", "USDC", "USD", "EUR"]:
        if clean.endswith(quote) and len(clean) > len(quote):
            return clean[:-len(quote)]
    return clean


def _format_compact_time(ts) -> str:
    """Formatea fecha/hora en el formato más compacto y limpio (DD/MM HH:mm:ss)."""
    if not ts:
        return "-"
    try:
        if isinstance(ts, (int, float)):
            dt = pd.to_datetime(ts, unit="ms", utc=True)
        else:
            dt = pd.to_datetime(ts, utc=True)
        return dt.strftime("%d/%m %H:%M:%S")
    except Exception:
        s = str(ts).replace("T", " ")
        return s[5:19] if len(s) >= 19 else s


class LiveMonitorPage:
    def __init__(self):
        self.strategies_dir = "config/strategies"
        self.selected_bot_id: Optional[str] = None
        self.timer = None
        self.is_active_page = False
        self.trades_view_mode = "selected"  # "selected" o "all"
        self.highlighted_trade: Optional[Dict[str, Any]] = None
        
        # State tracking to avoid unnecessary DOM re-renders
        self._rendered_bots_count = -1
        self._rendered_selected_bot_id = None
        self._rendered_inspector_status = None
        self._last_chart_kline_len = -1
        self._last_chart_bot_id = None
        self._last_trades_hash = None
        
        # Widget references for fast, lightweight in-place updates
        self.bot_card_widgets: Dict[str, Dict[str, Any]] = {}
        self.inspector_param_inputs: Dict[str, Any] = {}
        
        # Dialog references
        self.new_bot_dialog = None
        self.edit_bot_dialog = None
        self.new_bot_inputs = {}

        # Ensure at least 1 default bot exists if none exist
        self._ensure_default_bot()

    def set_page_active(self, is_active: bool):
        """Activa o desactiva las actualizaciones en vivo según si la página está visible."""
        self.is_active_page = is_active
        if is_active:
            self._last_chart_kline_len = -1
            self._last_trades_hash = None
            asyncio.create_task(self._sync_exchange_positions(show_notify=False))
            self._refresh_ui_elements(force_dom_rebuild=True)

    def _get_available_strategies(self) -> list[str]:
        if not os.path.exists(self.strategies_dir):
            return []
        files = glob.glob(f"{self.strategies_dir}/*.yaml")
        return sorted([os.path.basename(f) for f in files])

    def _load_strategy_params(self, strategy_filename: str) -> dict:
        path = os.path.join(self.strategies_dir, strategy_filename)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                return config.get('parameters', {}) or {}
        except Exception:
            return {}

    def _ensure_default_bot(self):
        """Si no hay bots registrados, crear uno inicial por defecto con saldo en la divisa base (1.0 BTC)."""
        bots = bot_manager.get_all_bots()
        has_api_key = bool(os.getenv("BINANCE_API_KEY", "").strip())
        
        if not bots:
            strategies = self._get_available_strategies()
            strat_file = strategies[0] if strategies else "ema_long.yaml"
            strat_path = os.path.join(self.strategies_dir, strat_file)
            
            if os.path.exists(strat_path):
                params = self._load_strategy_params(strat_file)
                default_bot = bot_manager.create_bot(
                    strategy_yaml_path=strat_path,
                    name="Bot 1 (BTC/USDT)",
                    symbol="BTC/USDT",
                    timeframe="1m",
                    initial_balance=1.0,
                    currency="BTC",
                    use_testnet=has_api_key,
                    custom_parameters=params,
                )
                self.selected_bot_id = default_bot.bot_id
        else:
            for b in bots:
                # Si hay API key de Binance pero el bot tenía testnet desactivado, activarlo
                if has_api_key and not b.use_testnet:
                    b.use_testnet = True
                    if b._client:
                        b._client.use_testnet = True
            if not self.selected_bot_id or not bot_manager.get_bot(self.selected_bot_id):
                self.selected_bot_id = bots[0].bot_id

    # ──────────────────────────────────────────────────────────────
    # Acciones de Control de Bots
    # ──────────────────────────────────────────────────────────────

    async def _start_bot_async(self, bot_id: str):
        bot = bot_manager.get_bot(bot_id)
        if not bot:
            return
        if bot.is_running:
            ui.notify(f"El bot '{bot.name}' ya está en ejecución.", type='warning')
            return

        bot.status = "STARTING"
        bot.status_message = "Iniciando..."
        ui.notify(f"Iniciando bot '{bot.name}'...", type='info')
        self._update_cards_data_in_place()

        await run.io_bound(bot.start)

        if bot.is_running:
            ui.notify(f"✅ Bot '{bot.name}' iniciado correctamente.", type='positive')
        else:
            ui.notify(f"❌ Error al iniciar '{bot.name}': {bot.status_message}", type='negative')
            
        # Forzar actualización del inspector
        self._rendered_inspector_status = None
        self._last_chart_kline_len = -1
        self._refresh_ui_elements(force_dom_rebuild=True)

    def _get_selected_bot(self) -> Optional[PaperTrader]:
        """Devuelve la instancia de PaperTrader del bot actualmente seleccionado."""
        if not self.selected_bot_id:
            return None
        return bot_manager.get_bot(self.selected_bot_id)

    async def _submit_manual_order(self):
        """Ejecuta una orden manual directa hacia Binance Futures y verifica su llenado."""
        sym = str(getattr(self, 'manual_symbol_input', None).value or '').strip().upper() if hasattr(self, 'manual_symbol_input') else 'BTC/USDT'
        side = str(getattr(self, 'manual_side_select', None).value or 'BUY').upper() if hasattr(self, 'manual_side_select') else 'BUY'
        o_type = str(getattr(self, 'manual_type_select', None).value or 'MARKET').upper() if hasattr(self, 'manual_type_select') else 'MARKET'
        qty = float(getattr(self, 'manual_qty_input', None).value or 0.0) if hasattr(self, 'manual_qty_input') else 0.001
        price = float(getattr(self, 'manual_price_input', None).value or 0.0) if (hasattr(self, 'manual_price_input') and o_type == 'LIMIT') else None

        if not sym or qty <= 0:
            ui.notify("Por favor especifica un símbolo y cantidad válida (>0)", type='warning')
            return

        bot = self._get_selected_bot()
        use_testnet = bot.use_testnet if bot else True
        client = BinanceTestnetClient(use_testnet=use_testnet)
        
        ui.notify(f"Enviando orden manual {side} {qty} {sym} [{o_type}] a Binance...", type='info')
        loop = asyncio.get_event_loop()
        order, err = await loop.run_in_executor(
            None,
            lambda: client.place_futures_order(
                symbol=sym,
                side="long" if side == "BUY" else "short",
                quantity=qty,
                order_type=o_type,
                price=price,
                verify_execution=True
            )
        )

        if order and not err:
            ui.notify(
                f"✅ Orden {side} {qty} {sym} EJECUTADA exitosamente en Binance | ID: {order.get('orderId')} | Status: {order.get('status')}",
                type='positive',
                close_button=True,
                duration=7000
            )
            if bot and bot.symbol.replace('/', '').upper() == sym.replace('/', '').upper():
                bot._notify(f"⚡ Orden manual ({side} {o_type}) completada en Binance | ID: {order.get('orderId')}")
            self._refresh_ui_elements(force_dom_rebuild=False)
        else:
            err_msg = err or (f"Estado no completado: {order.get('status')}" if order else "Desconocido")
            ui.notify(
                f"🚨 ALERTA: Orden manual {side} {sym} RECHAZADA en Binance: {err_msg}",
                type='negative',
                close_button=True,
                duration=10000
            )
            if bot:
                bot._trigger_critical_order_alert(
                    f"Orden Manual {side} RECHAZADA en Binance",
                    {"Símbolo": sym, "Cantidad": qty, "Tipo": o_type, "error": err_msg}
                )

    async def _trigger_deliberate_rejection_test(self):
        """Envía intencionalmente una orden inválida a Binance para comprobar el sistema de alertas críticas."""
        bot = self._get_selected_bot()
        use_testnet = bot.use_testnet if bot else True
        client = BinanceTestnetClient(use_testnet=use_testnet)

        ui.notify("🧪 Iniciando prueba de rechazo deliberado con símbolo inválido...", type='info')
        loop = asyncio.get_event_loop()
        order, err = await loop.run_in_executor(
            None,
            lambda: client.place_futures_order(
                symbol="INVALID_COIN/USDT",
                side="long",
                quantity=0.001,
                order_type="MARKET",
                verify_execution=True
            )
        )

        err_msg = err or "Rechazada por Binance"
        ui.notify(
            f"🚨 [TEST ALERTA] Binance rechazó la orden correctamente: {err_msg}",
            type='negative',
            close_button=True,
            duration=12000
        )
        if bot:
            bot._trigger_critical_order_alert(
                "Orden de Prueba RECHAZADA por Binance (Test)",
                {"Símbolo": "INVALID_COIN/USDT", "Tipo": "MARKET", "error": err_msg}
            )
        self._refresh_ui_elements(force_dom_rebuild=False)

    def _stop_bot(self, bot_id: str):
        bot = bot_manager.get_bot(bot_id)
        if bot and bot.is_running:
            bot.stop()
            ui.notify(f"🛑 Bot '{bot.name}' detenido.", type='info')
            self._rendered_inspector_status = None
            self._refresh_ui_elements(force_dom_rebuild=True)

    async def _close_selected_bot_binance_pos(self):
        """Cierra inmediatamente la posición en Binance Futures y sincroniza el bot local."""
        bot = self._get_selected_bot()
        if not bot or not bot._client:
            ui.notify("Bot no seleccionado o cliente no disponible.", type='warning')
            return
        
        info = getattr(bot, 'binance_position_info', None)
        amt = abs(float(info.get('amount', 0.0))) if info else (bot.position.quantity if bot.position else 0.0)
        side = "long" if (info and float(info.get('amount', 0.0)) > 0) else ("short" if (info and float(info.get('amount', 0.0)) < 0) else (bot.position.side if bot.position else "long"))
        
        if amt <= 0:
            ui.notify("No hay posición abierta en Binance para cerrar.", type='info')
            return
            
        ui.notify(f"Enviando orden de cierre a Binance Futures para {bot.symbol}...", type='info')
        order, err = bot._client.close_futures_position(bot.symbol, side, amt, order_type="MARKET", verify_execution=True)
        if order and not err:
            ui.notify(f"✅ Posición cerrada en Binance Futures | ID: {order.get('orderId')} | Status: {order.get('status', 'FILLED')}", type='positive')
            bot._client.cancel_all_open_orders(bot.symbol)
            bot.binance_position_info = None
            if bot.position:
                bot._close_position(float(order.get('avgPrice', bot.current_bid)), datetime.now(), reason="MANUAL_BINANCE_CLOSE")
            self._refresh_ui_elements(force_dom_rebuild=False)
        else:
            ui.notify(f"🚨 Error cerrando posición en Binance: {err}", type='negative', close_button=True, duration=8000)

    async def _clean_orphan_orders(self):
        """Cancela todas las órdenes condicionales pendientes en Binance para el símbolo del bot."""
        bot = self._get_selected_bot()
        sym = bot.symbol if bot else "BTC/USDT"
        use_t = bot.use_testnet if bot else True
        client = BinanceTestnetClient(use_testnet=use_t)
        loop = asyncio.get_event_loop()
        ok, err = await loop.run_in_executor(None, lambda: client.cancel_all_open_orders(sym))
        if ok:
            ui.notify(f"🧹 Órdenes pendientes de {sym} canceladas en Binance con éxito.", type='positive')
            self._refresh_ui_elements(force_dom_rebuild=False)
        else:
            ui.notify(f"⚠️ Error al cancelar órdenes: {err}", type='warning')

    async def _sync_exchange_positions(self, show_notify: bool = False):
        """Sincroniza el estado real de posiciones y órdenes con Binance Futures para todos los bots (incluso si están detenidos)."""
        if getattr(self, '_is_syncing_exchange', False):
            return
        self._is_syncing_exchange = True
        try:
            has_api_key = bool(os.getenv("BINANCE_API_KEY", "").strip())
            if not has_api_key:
                if show_notify:
                    ui.notify("API Key de Binance no configurada en .env", type='warning')
                return

            client = BinanceTestnetClient(use_testnet=True)
            loop = asyncio.get_event_loop()
            open_positions = await loop.run_in_executor(None, client.get_open_positions)

            # Mapear posiciones abiertas por símbolo Binance (ej: 'BTCUSDT')
            open_by_symbol = {
                str(p.get('symbol', '')).upper(): p 
                for p in (open_positions or []) 
                if float(p.get('positionAmt', 0.0)) != 0
            }

            bots = bot_manager.get_all_bots()
            changes_detected = False

            for b in bots:
                if not b.use_testnet:
                    continue
                binance_sym = b.symbol.replace('/', '').upper()
                p_info = open_by_symbol.get(binance_sym)

                if p_info:
                    amt = float(p_info.get('positionAmt', 0.0))
                    entry_p = float(p_info.get('entryPrice', 0.0))
                    unrealized_pnl = float(p_info.get('unRealizedProfit', 0.0))
                    mark_p = float(p_info.get('markPrice', 0.0))
                    be_p = float(p_info.get('breakEvenPrice', 0.0) or entry_p)
                    im = float(p_info.get('isolatedMargin', 0.0) or p_info.get('initialMargin', 0.0))
                    leverage = int(p_info.get('leverage', 1))

                    new_info = {
                        "symbol": binance_sym,
                        "amount": amt,
                        "abs_amount": abs(amt),
                        "entry_price": entry_p,
                        "break_even_price": be_p,
                        "mark_price": mark_p,
                        "unrealized_pnl": unrealized_pnl,
                        "initial_margin": im,
                        "leverage": leverage,
                        "margin_type": p_info.get('marginType', 'cross'),
                    }
                    if getattr(b, 'binance_position_info', None) != new_info:
                        b.binance_position_info = new_info
                        changes_detected = True

                    if b.position:
                        if entry_p > 0 and b.position.entry_price != entry_p:
                            b.position.entry_price = entry_p
                            changes_detected = True
                        if b.position.quantity != abs(amt):
                            b.position.quantity = abs(amt)
                            changes_detected = True
                else:
                    # No hay posición abierta en Binance para este símbolo
                    had_info = getattr(b, 'binance_position_info', None) is not None
                    had_pos = b.position is not None

                    if had_info or had_pos:
                        b.binance_position_info = None
                        if b.position:
                            exit_p = b.position.entry_price
                            if b.klines_df is not None and not b.klines_df.empty:
                                exit_p = float(b.klines_df['close'].iloc[-1])
                            b._close_position(exit_p, datetime.now(), reason="BINANCE_EXCHANGE_CLOSED")
                            changes_detected = True

                        # Limpiar órdenes condicionales huérfanas si existían
                        try:
                            if b._client:
                                await loop.run_in_executor(None, lambda: b._client.cancel_all_open_orders(b.symbol))
                        except Exception:
                            pass
                        changes_detected = True

            if changes_detected:
                self._last_trades_hash = None
                self._refresh_ui_elements(force_dom_rebuild=False)

            if show_notify:
                count = len(open_by_symbol)
                ui.notify(f"🔄 Sincronización completada con Binance: {count} posición(es) activa(s)", type='positive')

        except Exception as e:
            logger.warning(f"Error sincronizando posiciones con Binance: {e}")
            if show_notify:
                ui.notify(f"⚠️ Error al sincronizar con Binance: {e}", type='negative')
        finally:
            self._is_syncing_exchange = False

    async def _start_all_bots(self):
        bots = [b for b in bot_manager.get_all_bots() if not b.is_running]
        if not bots:
            ui.notify("Todos los bots ya están en ejecución.", type='info')
            return

        ui.notify(f"Iniciando {len(bots)} bot(s)...", type='info')
        for bot in bots:
            await run.io_bound(bot.start)
        ui.notify("Proceso de inicio finalizado.", type='positive')
        self._rendered_inspector_status = None
        self._refresh_ui_elements(force_dom_rebuild=True)

    def _stop_all_bots(self):
        running_bots = [b for b in bot_manager.get_all_bots() if b.is_running]
        if not running_bots:
            ui.notify("No hay bots en ejecución para detener.", type='info')
            return

        bot_manager.stop_all()
        ui.notify(f"Detenidos {len(running_bots)} bot(s).", type='info')
        self._rendered_inspector_status = None
        self._refresh_ui_elements(force_dom_rebuild=True)

    def _delete_bot(self, bot_id: str):
        bot = bot_manager.get_bot(bot_id)
        bot_name = bot.name if bot else bot_id
        success = bot_manager.delete_bot(bot_id)
        if success:
            ui.notify(f"Bot '{bot_name}' eliminado.", type='positive')
            bots = bot_manager.get_all_bots()
            if self.selected_bot_id == bot_id:
                self.selected_bot_id = bots[0].bot_id if bots else None
            self._update_bot_select_options()
            self._refresh_ui_elements(force_dom_rebuild=True)

    # ──────────────────────────────────────────────────────────────
    # Diálogo de Creación de Nuevo Bot
    # ──────────────────────────────────────────────────────────────

    def _open_new_bot_dialog(self):
        strategies = self._get_available_strategies()
        default_strat = strategies[0] if strategies else ""

        self.new_bot_inputs = {}

        if self.new_bot_dialog:
            self.new_bot_dialog.clear()
        else:
            self.new_bot_dialog = ui.dialog()

        with self.new_bot_dialog, ui.card().classes('bg-gray-800 text-white p-6 w-full max-w-xl rounded-xl border border-gray-700 shadow-2xl'):
            with ui.row().classes('w-full justify-between items-center mb-4'):
                ui.label('🤖 Configurar Nuevo Bot de Trading').classes('text-xl font-bold text-yellow-400')
                ui.button(icon='close', on_click=self.new_bot_dialog.close).props('flat round dense text-color=gray-400')

            name_input = ui.input(
                label='Nombre del Bot',
                value=f"Bot {len(bot_manager.get_all_bots()) + 1} (BTC/USDT)"
            ).classes('w-full mb-3')

            strat_select = ui.select(
                strategies,
                label='Estrategia Cuantitativa (YAML)',
                value=default_strat
            ).classes('w-full mb-3')

            with ui.row().classes('w-full gap-3 mb-3'):
                symbol_input = ui.input(label='Par / Símbolo', value='BTC/USDT').classes('flex-1')
                tf_select = ui.select(
                    ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w'],
                    value='1m',
                    label='Temporalidad'
                ).classes('w-36')

            with ui.row().classes('w-full gap-3 mb-3'):
                balance_input = ui.number(label='Saldo Inicial', value=1.0).classes('flex-1')
                currency_select = ui.select(['BTC', 'USDT', 'ETH', 'SOL', 'USD', 'BNB'], value='BTC', label='Moneda').classes('w-28')

            def on_new_symbol_change(e):
                sym = str(e.value or '').strip().upper()
                base = _extract_base_currency(sym)
                if base not in currency_select.options:
                    currency_select.options = list(dict.fromkeys(currency_select.options + [base]))
                currency_select.value = base

            symbol_input.on_value_change(on_new_symbol_change)

            def on_new_currency_change(e):
                curr = str(e.value or '').upper()
                if curr in ['BTC', 'ETH', 'SOL', 'BNB'] and (balance_input.value is None or balance_input.value >= 100):
                    balance_input.value = 1.0
                elif curr in ['USDT', 'USD', 'USDC'] and (balance_input.value is None or balance_input.value <= 10):
                    balance_input.value = 10000.0

            currency_select.on_value_change(on_new_currency_change)

            network_select = ui.select(
                ['Binance Real (Mainnet)', 'Binance Testnet'],
                label='Red de Datos',
                value='Binance Real (Mainnet)'
            ).classes('w-full mb-3')

            # Sección de Tipos de Órdenes de Ejecución
            with ui.card().classes('w-full p-3 bg-slate-900/90 border border-slate-700 rounded-lg mb-3'):
                with ui.row().classes('items-center justify-between w-full mb-2'):
                    ui.label('⚡ Tipos de Órdenes de Ejecución').classes('text-xs font-bold text-amber-400')
                    ui.badge('Por defecto: Señales Market / SL-TP Limit', color='slate-800').props('rounded').classes('text-[10px] text-slate-400')
                with ui.grid(columns=2).classes('w-full gap-2'):
                    new_entry_type = ui.select(
                        {'MARKET': '⚡ Entrada: MARKET', 'LIMIT': '🎯 Entrada: LIMIT'},
                        value='MARKET', label='Orden de Entrada'
                    ).classes('w-full text-xs')
                    new_exit_type = ui.select(
                        {'MARKET': '⚡ Salida: MARKET', 'LIMIT': '🎯 Salida: LIMIT'},
                        value='MARKET', label='Orden de Salida'
                    ).classes('w-full text-xs')
                    new_sl_type = ui.select(
                        {'LIMIT': '🛡️ Stop Loss: LIMIT (Predet.)', 'MARKET': '⚡ Stop Loss: MARKET'},
                        value='LIMIT', label='Orden de Stop Loss'
                    ).classes('w-full text-xs')
                    new_tp_type = ui.select(
                        {'LIMIT': '🎯 Take Profit: LIMIT (Predet.)', 'MARKET': '⚡ Take Profit: MARKET'},
                        value='LIMIT', label='Orden de Take Profit'
                    ).classes('w-full text-xs')

            # Contenedor de parámetros de estrategia
            ui.label('Parámetros de la Estrategia:').classes('text-sm font-semibold text-gray-300 mt-1 mb-1')
            params_container = ui.column().classes('w-full gap-2 p-3 bg-gray-900 rounded-lg border border-gray-700 mb-4')

            def render_dialog_params(strat_name):
                params_container.clear()
                self.new_bot_inputs.clear()
                params = self._load_strategy_params(strat_name)
                if not params:
                    with params_container:
                        ui.label('Esta estrategia no requiere parámetros adicionales.').classes('text-xs text-gray-400 italic')
                    return
                with params_container:
                    with ui.grid(columns=2).classes('w-full gap-2'):
                        for k, v in params.items():
                            if isinstance(v, (int, float)):
                                inp = ui.number(label=k, value=v).classes('w-full')
                            else:
                                inp = ui.input(label=k, value=str(v)).classes('w-full')
                            self.new_bot_inputs[k] = inp

            render_dialog_params(default_strat)
            strat_select.on_value_change(lambda e: render_dialog_params(e.value))

            async def create_and_close(start_immediately: bool = False):
                if not strat_select.value:
                    ui.notify('Selecciona una estrategia.', type='warning')
                    return
                
                strat_path = os.path.join(self.strategies_dir, strat_select.value)
                if not os.path.exists(strat_path):
                    ui.notify('Archivo de estrategia no encontrado.', type='negative')
                    return

                collected_params = {}
                for k, inp in self.new_bot_inputs.items():
                    val = inp.value
                    try:
                        if isinstance(val, str) and val.replace('.', '', 1).isdigit():
                            collected_params[k] = float(val) if '.' in val else int(val)
                        else:
                            collected_params[k] = val
                    except Exception:
                        collected_params[k] = val

                bot_name = name_input.value.strip() or f"Bot {len(bot_manager.get_all_bots()) + 1}"
                sym = symbol_input.value.strip().upper()
                tf = tf_select.value
                bal = float(balance_input.value) if balance_input.value else 10000.0
                curr = currency_select.value
                use_testnet = (network_select.value == 'Binance Testnet')

                new_bot = bot_manager.create_bot(
                    strategy_yaml_path=strat_path,
                    name=bot_name,
                    symbol=sym,
                    timeframe=tf,
                    initial_balance=bal,
                    currency=curr,
                    use_testnet=use_testnet,
                    custom_parameters=collected_params,
                )

                new_bot.order_types = {
                    'entry': new_entry_type.value,
                    'exit': new_exit_type.value,
                    'stop_loss': new_sl_type.value,
                    'take_profit': new_tp_type.value
                }
                new_bot._save_state()

                self.selected_bot_id = new_bot.bot_id
                self._update_bot_select_options()
                self._refresh_ui_elements(force_dom_rebuild=True)
                self.new_bot_dialog.close()
                ui.notify(f"✅ Bot '{bot_name}' creado con éxito.", type='positive')

                if start_immediately:
                    await self._start_bot_async(new_bot.bot_id)

            with ui.row().classes('w-full justify-end gap-3'):
                ui.button('Cancelar', on_click=self.new_bot_dialog.close).props('flat text-color=gray-400')
                ui.button('Crear Bot', on_click=lambda: create_and_close(False)).classes('bg-blue-600 hover:bg-blue-700 text-white font-semibold')
                ui.button('🚀 Crear e Iniciar', on_click=lambda: create_and_close(True)).classes('bg-green-600 hover:bg-green-700 text-white font-bold')

        self.new_bot_dialog.open()

    # ──────────────────────────────────────────────────────────────
    # Diálogo de Edición de Bot Existente
    # ──────────────────────────────────────────────────────────────

    def _open_edit_bot_dialog(self, bot_id: str):
        bot = bot_manager.get_bot(bot_id)
        if not bot:
            return

        strategies = self._get_available_strategies()
        current_strat_file = os.path.basename(bot.strategy_yaml_path) if bot.strategy_yaml_path else (strategies[0] if strategies else "")

        edit_param_inputs = {}

        if self.edit_bot_dialog:
            self.edit_bot_dialog.clear()
        else:
            self.edit_bot_dialog = ui.dialog()

        with self.edit_bot_dialog, ui.card().classes('bg-gray-800 text-white p-6 w-full max-w-xl rounded-xl border border-gray-700 shadow-2xl'):
            with ui.row().classes('w-full justify-between items-center mb-4'):
                with ui.column().classes('gap-0'):
                    ui.label(f'⚙️ Configuración del Bot: {bot.name}').classes('text-xl font-bold text-yellow-400')
                    status_sub = "🟢 Bot Corriendo (Cambios aplicados en caliente)" if bot.is_running else "🔴 Bot Detenido"
                    ui.label(status_sub).classes('text-xs text-green-400 font-semibold' if bot.is_running else 'text-xs text-gray-400')
                ui.button(icon='close', on_click=self.edit_bot_dialog.close).props('flat round dense text-color=gray-400')

            edit_name_input = ui.input(label='Nombre del Bot', value=bot.name).classes('w-full mb-3')

            edit_strat_select = ui.select(
                strategies,
                label='Estrategia Cuantitativa (YAML)',
                value=current_strat_file
            ).classes('w-full mb-3')

            with ui.row().classes('w-full gap-3 mb-3'):
                edit_symbol_input = ui.input(label='Par / Símbolo', value=bot.symbol or 'BTC/USDT').classes('flex-1')
                edit_tf_select = ui.select(
                    ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w'],
                    value=bot.timeframe,
                    label='Temporalidad'
                ).classes('w-36')

            current_base = bot.currency or _extract_base_currency(bot.symbol)
            curr_options = list(dict.fromkeys([current_base, 'BTC', 'USDT', 'ETH', 'SOL', 'USD', 'BNB']))

            with ui.row().classes('w-full gap-3 mb-3'):
                edit_balance_input = ui.number(label='Saldo Inicial', value=bot.initial_balance).classes('flex-1')
                edit_currency_select = ui.select(curr_options, value=current_base, label='Moneda').classes('w-28')

            def on_edit_symbol_change(e):
                sym = str(e.value or '').strip().upper()
                base = _extract_base_currency(sym)
                if base not in edit_currency_select.options:
                    edit_currency_select.options = list(dict.fromkeys(edit_currency_select.options + [base]))
                edit_currency_select.value = base

            edit_symbol_input.on_value_change(on_edit_symbol_change)

            def on_edit_currency_change(e):
                curr = str(e.value or '').upper()
                if curr in ['BTC', 'ETH', 'SOL', 'BNB'] and (edit_balance_input.value is None or edit_balance_input.value >= 100):
                    edit_balance_input.value = 1.0
                elif curr in ['USDT', 'USD', 'USDC'] and (edit_balance_input.value is None or edit_balance_input.value <= 10):
                    edit_balance_input.value = 10000.0

            edit_currency_select.on_value_change(on_edit_currency_change)

            edit_network_select = ui.select(
                ['Binance Real (Mainnet)', 'Binance Testnet'],
                label='Red de Datos',
                value='Binance Testnet' if bot.use_testnet else 'Binance Real (Mainnet)'
            ).classes('w-full mb-3')

            # Sección de Tipos de Órdenes de Ejecución
            bot_ord_types = getattr(bot, 'order_types', {}) or {}
            with ui.card().classes('w-full p-3 bg-slate-900/90 border border-slate-700 rounded-lg mb-3'):
                with ui.row().classes('items-center justify-between w-full mb-2'):
                    ui.label('⚡ Tipos de Órdenes de Ejecución').classes('text-xs font-bold text-amber-400')
                    ui.badge('Por defecto: Señales Market / SL-TP Limit', color='slate-800').props('rounded').classes('text-[10px] text-slate-400')
                with ui.grid(columns=2).classes('w-full gap-2'):
                    edit_entry_type = ui.select(
                        {'MARKET': '⚡ Entrada: MARKET', 'LIMIT': '🎯 Entrada: LIMIT'},
                        value=bot_ord_types.get('entry', 'MARKET'), label='Orden de Entrada'
                    ).classes('w-full text-xs')
                    edit_exit_type = ui.select(
                        {'MARKET': '⚡ Salida: MARKET', 'LIMIT': '🎯 Salida: LIMIT'},
                        value=bot_ord_types.get('exit', 'MARKET'), label='Orden de Salida'
                    ).classes('w-full text-xs')
                    edit_sl_type = ui.select(
                        {'LIMIT': '🛡️ Stop Loss: LIMIT (Predet.)', 'MARKET': '⚡ Stop Loss: MARKET'},
                        value=bot_ord_types.get('stop_loss', 'LIMIT'), label='Orden de Stop Loss'
                    ).classes('w-full text-xs')
                    edit_tp_type = ui.select(
                        {'LIMIT': '🎯 Take Profit: LIMIT (Predet.)', 'MARKET': '⚡ Take Profit: MARKET'},
                        value=bot_ord_types.get('take_profit', 'LIMIT'), label='Orden de Take Profit'
                    ).classes('w-full text-xs')

            ui.label('Parámetros de la Estrategia:').classes('text-sm font-semibold text-gray-300 mt-1 mb-1')
            edit_params_container = ui.column().classes('w-full gap-2 p-3 bg-gray-900 rounded-lg border border-gray-700 mb-4')

            def render_edit_params(strat_name):
                edit_params_container.clear()
                edit_param_inputs.clear()
                default_params = self._load_strategy_params(strat_name)
                current_p = bot.custom_parameters if (strat_name == current_strat_file and bot.custom_parameters) else default_params

                if not current_p:
                    with edit_params_container:
                        ui.label('Esta estrategia no requiere parámetros adicionales.').classes('text-xs text-gray-400 italic')
                    return

                with edit_params_container:
                    with ui.grid(columns=2).classes('w-full gap-2'):
                        for k, v in current_p.items():
                            if isinstance(v, (int, float)):
                                inp = ui.number(label=k, value=v).classes('w-full')
                            else:
                                inp = ui.input(label=k, value=str(v)).classes('w-full')
                            edit_param_inputs[k] = inp

            render_edit_params(current_strat_file)
            edit_strat_select.on_value_change(lambda e: render_edit_params(e.value))

            def save_changes():
                strat_path = os.path.join(self.strategies_dir, edit_strat_select.value)
                collected_params = {}
                for k, inp in edit_param_inputs.items():
                    val = inp.value
                    try:
                        if isinstance(val, str) and val.replace('.', '', 1).isdigit():
                            collected_params[k] = float(val) if '.' in val else int(val)
                        else:
                            collected_params[k] = val
                    except Exception:
                        collected_params[k] = val

                bot.update_configuration(
                    name=edit_name_input.value,
                    symbol=edit_symbol_input.value,
                    timeframe=edit_tf_select.value,
                    initial_balance=float(edit_balance_input.value) if edit_balance_input.value else bot.initial_balance,
                    currency=edit_currency_select.value,
                    use_testnet=(edit_network_select.value == 'Binance Testnet'),
                    custom_parameters=collected_params,
                    strategy_yaml_path=strat_path,
                    order_types={
                        'entry': edit_entry_type.value,
                        'exit': edit_exit_type.value,
                        'stop_loss': edit_sl_type.value,
                        'take_profit': edit_tp_type.value
                    }
                )

                self.edit_bot_dialog.close()
                self._update_bot_select_options()
                self._refresh_ui_elements(force_dom_rebuild=True)
                ui.notify(f"✅ Configuración del bot '{bot.name}' actualizada.", type='positive')

            with ui.row().classes('w-full justify-end gap-3'):
                ui.button('Cancelar', on_click=self.edit_bot_dialog.close).props('flat text-color=gray-400')
                ui.button('💾 Guardar Cambios', on_click=save_changes).classes('bg-yellow-500 hover:bg-yellow-600 text-black font-bold')

        self.edit_bot_dialog.open()

    def _open_test_binance_dialog(self):
        """Abre un diálogo interactivo para probar la conexión y ciclo completo de órdenes en Binance Testnet."""
        test_dialog = ui.dialog()
        with test_dialog, ui.card().classes('bg-gray-900 text-white p-6 w-full max-w-xl rounded-2xl border border-gray-700 shadow-2xl'):
            with ui.row().classes('w-full justify-between items-center mb-3 pb-2 border-b border-gray-800'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('speed', color='yellow-400', size='26px')
                    ui.label('Test de Conexión y Órdenes — Binance Testnet').classes('text-lg font-bold text-white')
                ui.button(icon='close', on_click=test_dialog.close).props('flat round dense text-color=gray-400')
            
            ui.label('Esta prueba verificará tus claves API en .env, consultará tu saldo en Testnet y enviará una orden de compra y cierre de 0.001 BTC en vivo.').classes('text-xs text-gray-400 mb-4')
            
            results_container = ui.column().classes('w-full items-center justify-center p-4 min-h-[160px] bg-gray-950 rounded-xl border border-gray-800')
            
            with results_container:
                ui.button('🚀 Iniciar Prueba en Vivo', icon='play_arrow', on_click=lambda: self._run_binance_diagnostic(results_container, test_dialog)).classes('bg-yellow-500 hover:bg-yellow-600 text-black font-bold px-6 py-2 rounded-xl shadow-lg')
        
        test_dialog.open()

    async def _run_binance_diagnostic(self, results_container, dialog):
        results_container.clear()
        with results_container:
            with ui.column().classes('items-center gap-3 py-4'):
                ui.spinner('dots', size='xl', color='yellow-500')
                ui.label('Conectando con Binance Futures Testnet y ejecutando órdenes de prueba...').classes('text-xs text-gray-300 italic text-center')
        
        import asyncio
        loop = asyncio.get_event_loop()
        client = BinanceTestnetClient(use_testnet=True)
        
        def do_test():
            return client.test_connection_and_orders(symbol="BTC/USDT")
            
        res = await loop.run_in_executor(None, do_test)
        
        results_container.clear()
        with results_container:
            if res.get("success"):
                ui.notify("✅ ¡Conexión y órdenes en Binance Testnet verificadas con éxito!", type='positive')
                with ui.card().classes('bg-green-950/40 border border-green-500/60 p-4 rounded-xl w-full gap-2 shadow-inner'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('check_circle', color='green-400', size='24px')
                        ui.label('¡Conexión y Ejecución 100% Operativa!').classes('text-base font-bold text-green-400')
                    
                    ui.separator().classes('bg-green-800/40 my-2')
                    
                    with ui.column().classes('gap-1 text-xs text-gray-200'):
                        ui.label('• 🔑 Credenciales API: Detectadas correctamente en .env').classes('text-gray-300')
                        ui.label(f"• 💰 Saldo en Testnet: {res.get('account_balance_usdt', 0.0):,.2f} USDT").classes('text-green-300 font-semibold')
                        
                        buy_o = res.get('buy_order', {})
                        ui.label(f"• 🟢 Orden de Compra: BUY 0.001 BTC | ID: {buy_o.get('orderId')} | Status: {buy_o.get('status')}").classes('text-blue-300')
                        
                        sell_o = res.get('sell_order', {})
                        ui.label(f"• 🔴 Orden de Cierre: SELL 0.001 BTC | ID: {sell_o.get('orderId')} | Status: {sell_o.get('status')}").classes('text-orange-300')
                    
                    with ui.row().classes('w-full justify-between items-center mt-3 pt-2 border-t border-green-800/40'):
                        ui.link('🔗 Ver en Binance Testnet (Trade History)', 'https://testnet.binancefuture.com/en/futures/BTCUSDT', new_tab=True).classes('text-xs text-yellow-400 font-bold underline')
                        with ui.row().classes('gap-2'):
                            ui.button('Repetir', icon='refresh', on_click=lambda: self._run_binance_diagnostic(results_container, dialog)).props('dense outline color=yellow-500').classes('text-xs text-yellow-400')
                            ui.button('Listo', on_click=dialog.close).classes('bg-gray-800 hover:bg-gray-700 text-xs text-white px-3 py-1 rounded')
            else:
                ui.notify(f"❌ Error en la prueba: {res.get('error')}", type='negative')
                with ui.card().classes('bg-red-950/40 border border-red-500/60 p-4 rounded-xl w-full gap-2 shadow-inner'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('error', color='red-400', size='24px')
                        ui.label('Fallo en la Prueba de Conexión').classes('text-base font-bold text-red-400')
                    
                    ui.label(f"Detalle: {res.get('error')}").classes('text-xs text-red-300 font-mono mt-1 whitespace-pre-wrap')
                    with ui.row().classes('w-full justify-between items-center mt-3 pt-2 border-t border-red-800/40'):
                        ui.button('Reintentar', icon='refresh', on_click=lambda: self._run_binance_diagnostic(results_container, dialog)).props('dense outline color=red-400').classes('text-xs text-red-400')
                        ui.button('Cerrar', on_click=dialog.close).classes('bg-gray-800 hover:bg-gray-700 text-xs text-white px-3 py-1 rounded')

    # ──────────────────────────────────────────────────────────────
    # Construcción de Gráfico Plotly
    # ──────────────────────────────────────────────────────────────

    def _build_empty_chart(self, title: str = 'Esperando datos de mercado...'):
        fig = go.Figure()
        fig.update_layout(
            template='plotly_dark',
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=20, r=20, t=30, b=20),
            title=title,
            xaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.05)'),
            yaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.05)'),
        )
        return fig

    def _build_chart(self, bot: PaperTrader, highlighted_trade: Optional[dict] = None):
        try:
            df = bot.klines_df
            trades = bot.trade_history
            position = bot.position

            if df is None or df.empty:
                return self._build_empty_chart(f"Esperando datos para {bot.name} ({bot.symbol})...")

            # Sanitizar DataFrame eliminando filas con datos incompletos
            df_clean = df.dropna(subset=['open', 'high', 'low', 'close'])
            if df_clean.empty:
                return self._build_empty_chart(f"Esperando datos para {bot.name} ({bot.symbol})...")

            fig = go.Figure()
            x_vals = [str(idx) for idx in df_clean.index]

            # Velas Japonesas con números flotantes puros (previene errores de serialización JSON / NaN)
            fig.add_trace(go.Candlestick(
                x=x_vals,
                open=[float(x) for x in df_clean['open']],
                high=[float(x) for x in df_clean['high']],
                low=[float(x) for x in df_clean['low']],
                close=[float(x) for x in df_clean['close']],
                name='Precio'
            ))

            # Indicadores dinámicos de la estrategia
            import ta
            try:
                if bot.strategy:
                    config = bot.strategy.config
                    params = bot.strategy.parameters or {}

                    def resolve_period(p):
                        if isinstance(p, str) and p in params:
                            return int(params[p])
                        try:
                            return int(float(p))
                        except Exception:
                            return 20

                    rules = []
                    for cond_type in ["entry_conditions", "exit_conditions"]:
                        conds = config.get(cond_type, {})
                        rules.extend(conds.get("rules", []))

                    added_indicators = set()
                    colors = ['#3b82f6', '#f59e0b', '#8b5cf6', '#ec4899', '#10b981']
                    color_idx = 0

                    for rule in rules:
                        rule_type = rule.get("type")
                        if rule_type == "ma_cross":
                            fast = resolve_period(rule.get("fast_period", 20))
                            slow = resolve_period(rule.get("slow_period", 50))
                            ma_type = rule.get("ma_type", "SMA")

                            for p, m_type in [(fast, ma_type), (slow, ma_type)]:
                                ind_name = f"{m_type}_{p}"
                                if ind_name not in added_indicators:
                                    series = ta.trend.sma_indicator(df_clean['close'], window=p) if m_type == "SMA" else ta.trend.ema_indicator(df_clean['close'], window=p)
                                    series_list = [None if (pd.isna(v) or v is None) else float(v) for v in series]
                                    c = colors[color_idx % len(colors)]
                                    color_idx += 1
                                    fig.add_trace(go.Scatter(x=x_vals, y=series_list, mode='lines', name=f'{m_type} {p}', line=dict(width=1.5, color=c)))
                                    added_indicators.add(ind_name)

                        elif rule_type == "technical_indicator":
                            ind1 = rule.get("indicator1", "EMA")
                            p1 = resolve_period(rule.get("period1", 20))
                            ind2 = rule.get("indicator2", "Price")
                            p2 = resolve_period(rule.get("period2", 50))

                            for ind, p in [(ind1, p1), (ind2, p2)]:
                                if ind in ["SMA", "EMA"]:
                                    ind_name = f"{ind}_{p}"
                                    if ind_name not in added_indicators:
                                        series = ta.trend.sma_indicator(df_clean['close'], window=p) if ind == "SMA" else ta.trend.ema_indicator(df_clean['close'], window=p)
                                        series_list = [None if (pd.isna(v) or v is None) else float(v) for v in series]
                                        c = colors[color_idx % len(colors)]
                                        color_idx += 1
                                        fig.add_trace(go.Scatter(x=x_vals, y=series_list, mode='lines', name=f'{ind} {p}', line=dict(width=1.5, color=c)))
                                        added_indicators.add(ind_name)
            except Exception:
                pass

            # Marcadores generales de Trades
            for t in trades:
                entry_time_str = str(t.get('entry_time', ''))
                exit_time_str = str(t['exit_time']) if t.get('exit_time') else None
                entry_p = float(t.get('entry_price', 0))
                exit_p = float(t.get('exit_price', 0)) if t.get('exit_price') else 0.0

                if t.get('side') == 'long':
                    if entry_time_str and entry_p > 0:
                        fig.add_annotation(x=entry_time_str, y=entry_p, text="▲ BUY", showarrow=True, arrowhead=1, arrowcolor="#10b981", font=dict(color="#10b981", size=10, weight="bold"))
                    if exit_time_str and exit_p > 0:
                        fig.add_annotation(x=exit_time_str, y=exit_p, text="▼ SELL", showarrow=True, arrowhead=1, arrowcolor="#ef4444", font=dict(color="#ef4444", size=10, weight="bold"))
                else:
                    if entry_time_str and entry_p > 0:
                        fig.add_annotation(x=entry_time_str, y=entry_p, text="▼ SELL", showarrow=True, arrowhead=1, arrowcolor="#ef4444", font=dict(color="#ef4444", size=10, weight="bold"))
                    if exit_time_str and exit_p > 0:
                        fig.add_annotation(x=exit_time_str, y=exit_p, text="▲ BUY", showarrow=True, arrowhead=1, arrowcolor="#10b981", font=dict(color="#10b981", size=10, weight="bold"))

            # Posición Abierta
            if position:
                entry_time_str = str(position.entry_timestamp)
                entry_p = float(position.entry_price)
                if position.side == 'long':
                    fig.add_annotation(x=entry_time_str, y=entry_p, text="▲ BUY (ABIERTA)", showarrow=True, arrowhead=1, arrowcolor="#22c55e", font=dict(color="#22c55e", size=12, weight="bold"))
                else:
                    fig.add_annotation(x=entry_time_str, y=entry_p, text="▼ SELL (ABIERTA)", showarrow=True, arrowhead=1, arrowcolor="#ef4444", font=dict(color="#ef4444", size=12, weight="bold"))

                if position.sl_price and not pd.isna(position.sl_price):
                    sl_p = float(position.sl_price)
                    fig.add_hline(y=sl_p, line_dash="dash", line_color="#ef4444", annotation_text=f"SL: {sl_p:.2f}", annotation_position="right", annotation_font_color="#ef4444")
                if position.tp_price and not pd.isna(position.tp_price):
                    tp_p = float(position.tp_price)
                    fig.add_hline(y=tp_p, line_dash="dash", line_color="#10b981", annotation_text=f"TP: {tp_p:.2f}", annotation_position="right", annotation_font_color="#10b981")

            # ──────────────────────────────────────────────────────────────
            # RESALTADO DE TRADE SELECCIONADO EN LA TABLA
            # ──────────────────────────────────────────────────────────────
            if highlighted_trade:
                def find_closest_idx(target_ts_str):
                    if not target_ts_str or not x_vals:
                        return None
                    if target_ts_str in x_vals:
                        return x_vals.index(target_ts_str)
                    try:
                        target_dt = pd.to_datetime(target_ts_str, utc=True)
                        dts = [pd.to_datetime(x, utc=True) for x in x_vals]
                        diffs = [abs((d - target_dt).total_seconds()) for d in dts]
                        min_idx = int(pd.Series(diffs).argmin())
                        return min_idx
                    except Exception:
                        return None

                h_entry_idx = find_closest_idx(str(highlighted_trade.get('entry_time', '')))
                h_exit_str = highlighted_trade.get('exit_time', '')
                h_exit_idx = find_closest_idx(str(h_exit_str)) if h_exit_str and h_exit_str != '-' else (len(x_vals) - 1)

                if h_entry_idx is not None:
                    h_entry_x = x_vals[h_entry_idx]
                    h_exit_x = x_vals[h_exit_idx] if h_exit_idx is not None else x_vals[-1]
                    
                    raw_side = str(highlighted_trade.get('raw_side') or highlighted_trade.get('side', '')).lower()
                    is_long = 'long' in raw_side
                    
                    try:
                        h_entry_p = float(highlighted_trade.get('entry_price', 0))
                    except Exception:
                        h_entry_p = 0.0

                    try:
                        h_exit_p = float(highlighted_trade.get('exit_price', 0)) if highlighted_trade.get('exit_price') not in [None, '', '-'] else None
                    except Exception:
                        h_exit_p = None

                    try:
                        h_sl_p = float(highlighted_trade.get('sl_price', 0)) if highlighted_trade.get('sl_price') not in [None, '', '-'] else None
                    except Exception:
                        h_sl_p = None

                    try:
                        h_tp_p = float(highlighted_trade.get('tp_price', 0)) if highlighted_trade.get('tp_price') not in [None, '', '-'] else None
                    except Exception:
                        h_tp_p = None

                    # Sombra vertical del periodo del trade enfocado
                    fig.add_vrect(
                        x0=h_entry_x,
                        x1=h_exit_x,
                        fillcolor="rgba(250, 204, 21, 0.22)",
                        line=dict(color="#facc15", width=2.5, dash="dash"),
                        annotation_text=f"🎯 Trade ({'LONG' if is_long else 'SHORT'})",
                        annotation_position="top left",
                        annotation_font=dict(color="#facc15", size=13, weight="bold")
                    )

                    # Badge enfocado de Entrada
                    if h_entry_p > 0:
                        fig.add_annotation(
                            x=h_entry_x,
                            y=h_entry_p,
                            text=f"<b>{'▲' if is_long else '▼'} ENTRADA {raw_side.upper()}</b><br>${h_entry_p:,.2f}",
                            showarrow=True,
                            arrowhead=2,
                            arrowsize=1.6,
                            arrowwidth=2,
                            arrowcolor="#facc15",
                            bgcolor="#1e1b4b",
                            bordercolor="#facc15",
                            borderwidth=2.5,
                            font=dict(color="#fde047", size=12)
                        )

                    # Badge enfocado de Salida
                    if h_exit_p and h_exit_p > 0:
                        fig.add_annotation(
                            x=h_exit_x,
                            y=h_exit_p,
                            text=f"<b>🏁 SALIDA ({highlighted_trade.get('reason', 'EXIT')})</b><br>${h_exit_p:,.2f}<br>{highlighted_trade.get('pnl', '')}",
                            showarrow=True,
                            arrowhead=2,
                            arrowsize=1.6,
                            arrowwidth=2,
                            arrowcolor="#f43f5e",
                            bgcolor="#1e1b4b",
                            bordercolor="#f43f5e",
                            borderwidth=2.5,
                            font=dict(color="#fda4af", size=12)
                        )

                    # Líneas SL y TP del trade enfocado
                    if h_sl_p and h_sl_p > 0:
                        fig.add_shape(
                            type="line",
                            x0=h_entry_x,
                            x1=h_exit_x,
                            y0=h_sl_p,
                            y1=h_sl_p,
                            line=dict(color="#ef4444", width=2.5, dash="dash")
                        )
                        fig.add_annotation(
                            x=h_exit_x,
                            y=h_sl_p,
                            text=f"🛑 SL: {h_sl_p:,.2f}",
                            showarrow=False,
                            xanchor="left",
                            font=dict(color="#ef4444", size=11, weight="bold"),
                            bgcolor="rgba(0,0,0,0.8)"
                        )

                    if h_tp_p and h_tp_p > 0:
                        fig.add_shape(
                            type="line",
                            x0=h_entry_x,
                            x1=h_exit_x,
                            y0=h_tp_p,
                            y1=h_tp_p,
                            line=dict(color="#10b981", width=2.5, dash="dash")
                        )
                        fig.add_annotation(
                            x=h_exit_x,
                            y=h_tp_p,
                            text=f"🎯 TP: {h_tp_p:,.2f}",
                            showarrow=False,
                            xanchor="left",
                            font=dict(color="#10b981", size=11, weight="bold"),
                            bgcolor="rgba(0,0,0,0.8)"
                        )

                    # Encuadrar el zoom automáticamente alrededor del trade
                    z_start = max(0, h_entry_idx - 20)
                    z_end = min(len(x_vals) - 1, (h_exit_idx if h_exit_idx is not None else len(x_vals) - 1) + 20)
                    fig.update_xaxes(range=[x_vals[z_start], x_vals[z_end]])

            # Línea de Precio Actual
            if not df_clean.empty:
                current_price = float(df_clean['close'].iloc[-1])
                fig.add_hline(
                    y=current_price,
                    line_dash="dot",
                    line_color="#f59e0b",
                    line_width=1.5,
                    annotation_text=f"{current_price:,.2f}",
                    annotation_position="right",
                    annotation_font_color="white",
                    annotation_bgcolor="#f59e0b"
                )

            fig.update_layout(
                template='plotly_dark',
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                margin=dict(l=30, r=50, t=30, b=30),
                xaxis_rangeslider_visible=False,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                uirevision=None if highlighted_trade else 'constant'
            )
            return fig
        except Exception:
            return self._build_empty_chart(f"Error al graficar {bot.name}")

    # ──────────────────────────────────────────────────────────────
    # Bucle de Actualización UI Optimizado (Zero Thrashing)
    # ──────────────────────────────────────────────────────────────

    def _ui_update_loop(self):
        if not getattr(self, 'is_active_page', False):
            return
        try:
            self._sync_counter = getattr(self, '_sync_counter', 0) + 1
            # Sincronizar activamente con Binance cada 2 ciclos (~3 segundos)
            if self._sync_counter % 2 == 0:
                asyncio.create_task(self._sync_exchange_positions(show_notify=False))

            self._refresh_ui_elements(force_dom_rebuild=False)
        except Exception:
            pass

    def _refresh_ui_elements(self, force_dom_rebuild: bool = False):
        bots = bot_manager.get_all_bots()

        # 1. Actualizar KPIs globales en sitio
        summary = bot_manager.get_portfolio_summary()
        if hasattr(self, 'kpi_bots_label'):
            self.kpi_bots_label.set_text(f"{summary['running_bots']} / {summary['total_bots']}")
        if hasattr(self, 'kpi_balance_label'):
            self.kpi_balance_label.set_text(summary.get('balance_display', '0.00 USDT'))
        if hasattr(self, 'kpi_pnl_label'):
            self.kpi_pnl_label.set_text(summary.get('pnl_display', '+0.00 USDT (+0.00%)'))
            pnl_val = summary.get('total_pnl', 0.0)
            self.kpi_pnl_label.classes('text-green-400' if pnl_val >= 0 else 'text-red-400', remove='text-green-400 text-red-400')
        if hasattr(self, 'kpi_positions_label'):
            self.kpi_positions_label.set_text(f"{summary['active_positions']} activas | WR: {summary['win_rate']:.1f}%")

        # 2. Renderizar / Actualizar Lista de Tarjetas de bots
        # Solo reconstruir el DOM completo si la cantidad de bots cambió o se forzó rebuild
        if force_dom_rebuild or len(bots) != self._rendered_bots_count:
            self._build_bots_grid_dom(bots)
        else:
            self._update_cards_data_in_place()

        # 3. Actualizar Inspector del bot seleccionado
        self._update_selected_bot_inspector()

    def _build_bots_grid_dom(self, bots: list[PaperTrader]):
        """Construye el DOM de las tarjetas de bots una sola vez cuando cambia la lista."""
        if not hasattr(self, 'bots_cards_container'):
            return

        self.bots_cards_container.clear()
        self.bot_card_widgets.clear()
        self._rendered_bots_count = len(bots)

        if not bots:
            with self.bots_cards_container:
                with ui.card().classes('w-full bg-gray-800 border border-dashed border-gray-700 p-8 text-center items-center'):
                    ui.icon('smart_toy', size='48px', color='gray-500').classes('mb-2')
                    ui.label('No hay bots creados actualmente.').classes('text-gray-400 text-lg mb-4')
                    ui.button('Crear Primer Bot', icon='add', on_click=self._open_new_bot_dialog).classes('bg-yellow-500 hover:bg-yellow-600 text-black font-bold')
            return

        with self.bots_cards_container:
            with ui.grid(columns=1 if len(bots) == 1 else 2).classes('w-full gap-4'):
                for b in bots:
                    is_selected = (b.bot_id == self.selected_bot_id)
                    card_border = 'border-yellow-500 ring-2 ring-yellow-500/50 shadow-yellow-500/10' if is_selected else 'border-gray-700 hover:border-yellow-500/50'
                    
                    with ui.card().classes(f'bg-gray-800 text-white p-4 rounded-xl border {card_border} transition-all duration-200 shadow-lg flex flex-col justify-between cursor-pointer') as card_el:
                        widgets = {'card': card_el}

                        # Header de la tarjeta con selector explícito
                        with ui.row().classes('w-full justify-between items-center mb-2'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('smart_toy', color='yellow-400', size='20px')
                                ui.label(b.name).classes('font-bold text-base text-white truncate max-w-[180px]')
                            
                            with ui.row().classes('items-center gap-1.5'):
                                sel_badge = ui.badge('🎯 EN INSPECCIÓN' if is_selected else '👆 CLIC PARA SELECCIONAR', color='yellow-900' if is_selected else 'slate-800').props('rounded').classes('text-[10px] font-bold text-yellow-300' if is_selected else 'text-[10px] text-gray-400')
                                widgets['sel_badge'] = sel_badge
                                status_badge = ui.badge(b.status, color='gray').props('rounded outline').classes('text-xs font-bold')
                                widgets['status_badge'] = status_badge

                        # Info técnica & Red
                        with ui.row().classes('w-full gap-2 items-center mb-2 text-xs text-gray-400'):
                            ui.badge(b.symbol, color='blue-900').props('rounded').classes('text-blue-300 font-mono')
                            ui.badge(b.timeframe, color='purple-900').props('rounded').classes('text-purple-300 font-mono')
                            ui.badge(b.strategy_name.upper(), color='indigo-900').props('rounded').classes('text-indigo-300 font-bold')
                            net_txt = 'Testnet' if b.use_testnet else 'Real (Mainnet)'
                            net_col = 'text-yellow-400' if b.use_testnet else 'text-green-400'
                            net_label = ui.label(f"🌐 {net_txt}").classes(f'text-[11px] {net_col}')
                            widgets['net_label'] = net_label

                        # PARÁMETROS DE LA ESTRATEGIA (Pills)
                        params_dict = b.custom_parameters or (b.strategy.parameters if b.strategy else {})
                        with ui.row().classes('w-full gap-1 items-center mb-3 flex-wrap p-1.5 bg-gray-950/60 rounded border border-gray-800 text-xs'):
                            ui.label('⚙️ Parámetros:').classes('text-[10px] font-semibold text-gray-400 mr-1')
                            if params_dict:
                                for pk, pv in params_dict.items():
                                    ui.badge(f"{pk}: {pv}", color='slate-800').props('rounded outline').classes('text-[11px] text-gray-300 px-1.5 py-0.5 border border-gray-700')
                            else:
                                ui.label('Estándar').classes('text-[11px] text-gray-500 italic')

                        # Métricas rápidas (4 columnas con Win Rate y Estadísticas)
                        with ui.grid(columns=4).classes('w-full gap-2 p-2 bg-gray-900/80 rounded-lg border border-gray-700/50 mb-3 text-center'):
                            with ui.column().classes('items-center gap-0'):
                                ui.label('Balance').classes('text-[10px] text-gray-400 uppercase')
                                bal_lbl = ui.label(f"{b.current_balance:,.2f} {b.currency}").classes('text-xs font-bold text-white')
                                widgets['bal_label'] = bal_lbl
                            with ui.column().classes('items-center gap-0'):
                                ui.label('Total PNL').classes('text-[10px] text-gray-400 uppercase')
                                pnl_lbl = ui.label("+0.00").classes('text-xs font-bold text-green-400')
                                widgets['pnl_label'] = pnl_lbl
                            with ui.column().classes('items-center gap-0'):
                                ui.label('Win Rate').classes('text-[10px] text-gray-400 uppercase')
                                wr_lbl = ui.label("0.0% (0)").classes('text-xs font-bold text-yellow-400')
                                widgets['wr_label'] = wr_lbl
                            with ui.column().classes('items-center gap-0'):
                                ui.label('Posición').classes('text-[10px] text-gray-400 uppercase')
                                pos_lbl = ui.label("NINGUNA").classes('text-xs font-bold text-gray-400')
                                widgets['pos_label'] = pos_lbl

                        # Barra de acciones
                        with ui.row().classes('w-full justify-between items-center mt-auto pt-2 border-t border-gray-700/50'):
                            with ui.row().classes('gap-1 items-center'):
                                if is_selected:
                                    ui.button('🎯 Monitoreando', color='yellow-500').props('flat dense').classes('text-xs text-yellow-400 font-bold bg-yellow-500/10 rounded px-2.5 py-1')
                                else:
                                    ui.button('👁 Visualizar', icon='visibility', on_click=lambda bot_id=b.bot_id: self._select_bot(bot_id)).props('dense').classes('bg-yellow-500 hover:bg-yellow-600 text-black font-bold text-xs px-2.5 py-1 rounded shadow')

                                ui.button('Config', icon='settings', on_click=lambda bot_id=b.bot_id: self._open_edit_bot_dialog(bot_id)).props('flat dense text-color=gray-300').classes('text-xs hover:text-yellow-400').tooltip('Editar configuración y parámetros')

                            with ui.row().classes('gap-1 items-center'):
                                action_btn = ui.button(
                                    'Detener' if b.is_running else 'Iniciar',
                                    icon='stop' if b.is_running else 'play_arrow',
                                    on_click=lambda bot_id=b.bot_id, is_r=b.is_running: self._stop_bot(bot_id) if is_r else self._start_bot_async(bot_id)
                                ).classes('bg-red-600 hover:bg-red-700' if b.is_running else 'bg-green-600 hover:bg-green-700').classes('text-white text-xs px-2 py-1')
                                widgets['action_btn'] = action_btn

                                ui.button(icon='delete', on_click=lambda bot_id=b.bot_id: self._delete_bot(bot_id)).props('flat round dense text-color=red-400').tooltip('Eliminar bot')

                        self.bot_card_widgets[b.bot_id] = widgets

        self._update_cards_data_in_place()

    def _update_cards_data_in_place(self):
        """Actualiza los valores de las tarjetas en sitio sin reconstruir el DOM (ultra rápido)."""
        bots = bot_manager.get_all_bots()
        for b in bots:
            w = self.bot_card_widgets.get(b.bot_id)
            if not w:
                continue

            # Actualizar status badge
            badge = w.get('status_badge')
            if badge:
                if b.status == "RUNNING":
                    badge.set_text("CORRIENDO")
                    badge.props('color=green')
                elif b.status == "STARTING":
                    badge.set_text("INICIANDO...")
                    badge.props('color=yellow')
                elif b.status == "ERROR":
                    badge.set_text("ERROR")
                    badge.props('color=red')
                else:
                    badge.set_text("DETENIDO")
                    badge.props('color=gray')

            # Balance
            is_base = (b.currency.upper() == b.symbol.split("/")[0].upper() if "/" in b.symbol else False)
            dec = 4 if is_base else 2
            bal_lbl = w.get('bal_label')
            if bal_lbl:
                bal_lbl.set_text(f"{b.current_balance:,.{dec}f} {b.currency}")

            # PNL
            pnl_lbl = w.get('pnl_label')
            if pnl_lbl:
                pnl_val = b.stats.get('total_pnl', 0.0)
                pnl_pct = (pnl_val / b.initial_balance * 100) if b.initial_balance > 0 else 0.0
                sign = "+" if pnl_val >= 0 else ""
                pnl_lbl.set_text(f"{sign}{pnl_val:.{dec}f} ({sign}{pnl_pct:.1f}%)")
                pnl_lbl.classes('text-green-400' if pnl_val >= 0 else 'text-red-400', remove='text-green-400 text-red-400')

            # Win Rate y Trades
            wr_lbl = w.get('wr_label')
            if wr_lbl:
                wr_val = b.stats.get('win_rate', 0.0)
                t_count = len(b.trade_history)
                w_count = b.stats.get('wins', 0)
                wr_lbl.set_text(f"{wr_val:.1f}% ({w_count}/{t_count})")

            # Posición
            pos_lbl = w.get('pos_label')
            if pos_lbl:
                pos_str = f"{b.position.side.upper()}" if b.position else "NINGUNA"
                pos_lbl.set_text(pos_str)
                pos_lbl.classes('text-blue-400' if b.position else 'text-gray-400', remove='text-blue-400 text-gray-400')

    def _select_bot(self, bot_id: str):
        if self.selected_bot_id != bot_id:
            self.selected_bot_id = bot_id
            self.highlighted_trade = None
            if hasattr(self, 'btn_clear_highlight'):
                self.btn_clear_highlight.set_visibility(False)
            self._rendered_selected_bot_id = None  # trigger re-render of inspector config
            self._last_chart_kline_len = -1
            self._update_bot_select_options()
            self._refresh_ui_elements(force_dom_rebuild=True)

    def _update_bot_select_options(self):
        if hasattr(self, 'bot_select'):
            bots = bot_manager.get_all_bots()
            options = {b.bot_id: f"{b.name} [{b.symbol}] ({'🟢' if b.is_running else '🔴'})" for b in bots}
            self.bot_select.options = options
            if self.selected_bot_id in options:
                self.bot_select.value = self.selected_bot_id
            elif bots:
                self.selected_bot_id = bots[0].bot_id
                self.bot_select.value = self.selected_bot_id
            else:
                self.selected_bot_id = None
                self.bot_select.value = None
            self.bot_select.update()

    def _on_bot_select_change(self, e):
        if e.value and e.value != self.selected_bot_id:
            self.selected_bot_id = e.value
            self.highlighted_trade = None
            if hasattr(self, 'btn_clear_highlight'):
                self.btn_clear_highlight.set_visibility(False)
            self._rendered_selected_bot_id = None
            self._last_chart_kline_len = -1
            self._refresh_ui_elements(force_dom_rebuild=True)

    def _set_trades_view_mode(self, mode: str):
        self.trades_view_mode = mode
        if hasattr(self, 'btn_view_selected') and hasattr(self, 'btn_view_all'):
            if mode == "selected":
                self.btn_view_selected.classes('bg-yellow-500 text-black font-bold', remove='flat text-gray-300 font-medium')
                self.btn_view_all.classes('flat text-gray-300 font-medium', remove='bg-yellow-500 text-black font-bold')
            else:
                self.btn_view_all.classes('bg-yellow-500 text-black font-bold', remove='flat text-gray-300 font-medium')
                self.btn_view_selected.classes('flat text-gray-300 font-medium', remove='bg-yellow-500 text-black font-bold')
        self._last_trades_hash = None
        self._update_selected_bot_inspector()

    def _clear_trade_highlight(self):
        """Quita el enfoque del trade en el gráfico y regresa a la vista en vivo."""
        self.highlighted_trade = None
        if hasattr(self, 'btn_clear_highlight'):
            self.btn_clear_highlight.set_visibility(False)
        bot = bot_manager.get_bot(self.selected_bot_id) if self.selected_bot_id else None
        if bot and hasattr(self, 'chart'):
            fig = self._build_chart(bot, highlighted_trade=None)
            self.chart.update_figure(fig)
            ui.notify("Gráfico reestablecido a la vista general en vivo.", type='info')

    def _on_trade_row_clicked(self, e):
        """Callback al hacer clic en cualquier fila de la tabla de trades para enfocar el gráfico."""
        data = e.args.get('data')
        if not data:
            return

        bot_id = data.get('bot_id')
        if bot_id and bot_id != self.selected_bot_id:
            self.selected_bot_id = bot_id
            self._rendered_selected_bot_id = None
            self._last_chart_kline_len = -1
            self._update_bot_select_options()

        self.highlighted_trade = data
        if hasattr(self, 'btn_clear_highlight'):
            self.btn_clear_highlight.set_visibility(True)

        bot = bot_manager.get_bot(self.selected_bot_id)
        if bot:
            if hasattr(self, 'chart'):
                fig = self._build_chart(bot, highlighted_trade=data)
                self.chart.update_figure(fig)
                self._last_chart_kline_len = len(bot.klines_df) if bot.klines_df is not None else 0
                self._last_chart_bot_id = bot.bot_id

            side_str = str(data.get('side', '')).strip()
            entry_str = data.get('entry_price', '')
            bot_name_str = data.get('bot_name', bot.name)
            ui.notify(
                f"🎯 Enfocando trade en gráfico: {bot_name_str} | {side_str} @ {entry_str}",
                type='info',
                position='top'
            )

    def _update_selected_bot_inspector(self):
        bot = bot_manager.get_bot(self.selected_bot_id) if self.selected_bot_id else None
        if not bot:
            if hasattr(self, 'inspector_status_label'):
                self.inspector_status_label.set_text("Estado: NINGÚN BOT SELECCIONADO")
            return

        # Actualizar botones del selector de bot en la cabecera del inspector
        if hasattr(self, 'inspector_bot_tabs_container'):
            all_b = bot_manager.get_all_bots()
            tabs_fingerprint = f"{len(all_b)}_{self.selected_bot_id}_{'_'.join(b.status for b in all_b)}"
            if getattr(self, '_last_tabs_fingerprint', None) != tabs_fingerprint:
                self.inspector_bot_tabs_container.clear()
                with self.inspector_bot_tabs_container:
                    for b in all_b:
                        is_curr = (b.bot_id == bot.bot_id)
                        st_icon = "🟢" if b.is_running else "🔴"
                        btn_cls = 'bg-yellow-500 text-black font-bold ring-2 ring-yellow-400 shadow-md scale-105' if is_curr else 'bg-gray-800 text-gray-300 hover:text-white hover:bg-gray-700'
                        ui.button(
                            f"{st_icon} {b.name} ({b.symbol})",
                            on_click=lambda bid=b.bot_id: self._select_bot(bid)
                        ).props('dense').classes(f'text-xs px-3 py-1 rounded-lg transition-all {btn_cls}')
                self._last_tabs_fingerprint = tabs_fingerprint

        # Actualizar títulos del inspector
        if hasattr(self, 'chart_title_label'):
            self.chart_title_label.set_text(f"📈 Gráfico Técnico en Vivo: {bot.name} [{bot.symbol} - {bot.timeframe}]")
        if hasattr(self, 'trades_title_label'):
            if self.trades_view_mode == "all":
                self.trades_title_label.set_text("📋 Historial de Trades (🌐 Cartera Completa — Todos los Bots)")
            else:
                self.trades_title_label.set_text(f"📋 Historial de Trades (🎯 {bot.name})")

        # 1. Métricas Cuantitativas Avanzadas del Bot
        is_base = (bot.currency.upper() == bot.symbol.split("/")[0].upper() if "/" in bot.symbol else False)
        dec = 4 if is_base else 2
        status_emoji = "🟢" if bot.is_running else ("⏳" if bot.status == "STARTING" else "🔴")
        
        stats = bot.get_detailed_stats() if hasattr(bot, 'get_detailed_stats') else bot.stats
        pnl_val = stats.get('total_pnl', 0.0)
        pnl_pct = stats.get('total_pnl_pct', 0.0)
        sign = "+" if pnl_val >= 0 else ""
        
        if hasattr(self, 'inspector_status_label'):
            self.inspector_status_label.set_text(f"{bot.status} {status_emoji}")
        if hasattr(self, 'inspector_balance_label'):
            self.inspector_balance_label.set_text(f"Saldo: {bot.current_balance:,.{dec}f} {bot.currency}")
        
        if hasattr(self, 'inspector_winrate_label'):
            self.inspector_winrate_label.set_text(f"{stats.get('win_rate', 0.0):.1f}% ({stats.get('wins', 0)}G / {stats.get('losses', 0)}P)")
        if hasattr(self, 'inspector_trades_label'):
            self.inspector_trades_label.set_text(f"Trades Completados: {stats.get('total_trades', 0)}")
        
        if hasattr(self, 'inspector_pnl_label'):
            self.inspector_pnl_label.set_text(f"{sign}{pnl_val:,.{dec}f} {bot.currency} ({sign}{pnl_pct:.2f}%)")
            self.inspector_pnl_label.classes('text-green-400' if pnl_val >= 0 else 'text-red-400', remove='text-green-400 text-red-400')
        if hasattr(self, 'inspector_pf_label'):
            pf = stats.get('profit_factor', 0.0)
            avg_t = stats.get('avg_trade_pnl', 0.0)
            self.inspector_pf_label.set_text(f"Profit Factor: {pf:.2f} | Prom: {avg_t:+.{dec}f}")

        if hasattr(self, 'inspector_pos_label'):
            if bot.position:
                pos = bot.position
                self.inspector_pos_label.set_text(f"{pos.side.upper()} @ {pos.entry_price:.4f}")
                self.inspector_pos_label.classes('text-green-400' if pos.side == 'long' else 'text-red-400', remove='text-blue-400 text-gray-400')
                if hasattr(self, 'inspector_sltp_label'):
                    sl_txt = f"{pos.sl_price:.4f}" if pos.sl_price else "-"
                    tp_txt = f"{pos.tp_price:.4f}" if pos.tp_price else "-"
                    self.inspector_sltp_label.set_text(f"SL: {sl_txt} | TP: {tp_txt} | Qty: {pos.quantity:.4f}")
            else:
                self.inspector_pos_label.set_text("NINGUNA")
                self.inspector_pos_label.classes('text-gray-400', remove='text-green-400 text-red-400')
                if hasattr(self, 'inspector_sltp_label'):
                    self.inspector_sltp_label.set_text("Esperando señal de entrada...")

        # 2. Panel de Configuración y Parámetros en el Inspector
        if hasattr(self, 'inspector_config_container'):
            if self._rendered_selected_bot_id != bot.bot_id or self._rendered_inspector_status != bot.is_running:
                self._render_inspector_config(bot)
                self._rendered_selected_bot_id = bot.bot_id
                self._rendered_inspector_status = bot.is_running

        # 3. Ticker Order Book
        if hasattr(self, 'bid_label'):
            bid = bot.current_bid
            ask = bot.current_ask
            spread = ask - bid if (ask and bid) else 0.0
            self.bid_label.set_text(f"{bid:,.2f}")
            self.ask_label.set_text(f"{ask:,.2f}")
            self.spread_label.set_text(f"{spread:,.2f}")
            if hasattr(self, 'bid_qty_label'):
                self.bid_qty_label.set_text(f"Vol: {bot.current_bid_qty:.3f}")
                self.ask_qty_label.set_text(f"Vol: {bot.current_ask_qty:.3f}")

        # 4. Gráfico en Vivo (solo actualizar si la página está activa y hay cambios)
        if hasattr(self, 'chart') and getattr(self, 'is_active_page', False):
            current_kline_len = len(bot.klines_df) if (bot.klines_df is not None and not bot.klines_df.empty) else 0
            if current_kline_len != self._last_chart_kline_len or self._last_chart_bot_id != bot.bot_id or bot.is_running:
                try:
                    fig = self._build_chart(bot, highlighted_trade=self.highlighted_trade)
                    self.chart.update_figure(fig)
                    self._last_chart_kline_len = current_kline_len
                    self._last_chart_bot_id = bot.bot_id
                except Exception:
                    pass

        # 5. Posiciones Abiertas en Binance Futures (Exchange Live Sync)
        if hasattr(self, 'binance_pos_grid'):
            b_rows = []
            target_bots = bot_manager.get_all_bots() if self.trades_view_mode == "all" else [bot]
            has_any_binance_pos = False

            for b in target_bots:
                b_info = getattr(b, 'binance_position_info', None)
                if b_info and b_info.get('amount') and float(b_info.get('amount', 0)) != 0:
                    has_any_binance_pos = True
                    amt = float(b_info.get('amount', 0))
                    side_icon = "📈 LONG" if amt > 0 else "📉 SHORT"
                    raw_upnl = float(b_info.get('unrealized_pnl', 0.0))
                    im = float(b_info.get('initial_margin', 0.0))
                    roi_pct = (raw_upnl / im * 100.0) if im > 0 else 0.0
                    m_type = str(b_info.get('margin_type', 'cross')).capitalize()
                    
                    b_rows.append({
                        'symbol_display': f"{b_info.get('symbol', b.symbol)} Perp {b_info.get('leverage', 1)}x",
                        'size_display': f"{side_icon} {abs(amt):.4f} {b.symbol.split('/')[0]}",
                        'entry_price': f"{b_info.get('entry_price', 0):,.2f}",
                        'break_even': f"{b_info.get('break_even_price', b_info.get('entry_price', 0)):,.2f}",
                        'mark_price': f"{b_info.get('mark_price', 0):,.2f}",
                        'margin_display': f"{im:.2f} USDT ({m_type})",
                        'pnl_display': f"{raw_upnl:+,.2f} USDT ({roi_pct:+.2f}%)",
                        'raw_pnl': raw_upnl,
                        'bot_name': b.name
                    })

            self.binance_pos_grid.options['rowData'] = b_rows
            self.binance_pos_grid.update()

            if hasattr(self, 'binance_pos_badge'):
                if has_any_binance_pos:
                    self.binance_pos_badge.set_text(f"🔴 Posición Activa en Exchange ({len(b_rows)})")
                    self.binance_pos_badge.classes('bg-green-950 text-green-400 font-bold', remove='bg-yellow-950 text-yellow-300 bg-gray-800 text-gray-400')
                else:
                    self.binance_pos_badge.set_text("⚪ Sin Posición en Exchange")
                    self.binance_pos_badge.classes('bg-gray-800 text-gray-400', remove='bg-green-950 text-green-400 bg-yellow-950 text-yellow-300')

            if hasattr(self, 'btn_close_binance_pos'):
                self.btn_close_binance_pos.set_visibility(has_any_binance_pos)

        # 6. Tabla de Trades (AG Grid) - Con Fecha/Hora en formato ultra compacto
        if hasattr(self, 'trades_grid'):
            rows = []
            target_bots = bot_manager.get_all_bots() if self.trades_view_mode == "all" else [bot]

            for b in target_bots:
                b_is_base = (b.currency.upper() == b.symbol.split("/")[0].upper() if "/" in b.symbol else False)
                b_dec = 4 if b_is_base else 2
                b_df = b.klines_df

                if b.position:
                    pos = b.position
                    current_price = b_df['close'].iloc[-1] if (b_df is not None and not b_df.empty) else pos.entry_price
                    if pos.side == 'long':
                        pnl_raw = (current_price - pos.entry_price) * pos.quantity
                        pnl_pct_trade = ((current_price - pos.entry_price) / pos.entry_price) * 100.0 if pos.entry_price else 0
                    else:
                        pnl_raw = (pos.entry_price - current_price) * pos.quantity
                        pnl_pct_trade = ((pos.entry_price - current_price) / pos.entry_price) * 100.0 if pos.entry_price else 0

                    pnl_display_val = (pnl_raw / current_price) if b_is_base else pnl_raw
                    rows.append({
                        'bot_id': b.bot_id,
                        'bot_name': b.name,
                        'time_compact': _format_compact_time(pos.entry_timestamp),
                        'side': f"📈 {pos.side.upper()}" if pos.side == 'long' else f"📉 {pos.side.upper()}",
                        'raw_side': pos.side,
                        'entry_time': str(pos.entry_timestamp),
                        'exit_time': '',
                        'entry_price': f"{pos.entry_price:.4f}",
                        'exit_price': "-",
                        'sl_price': f"{pos.sl_price:.4f}" if pos.sl_price else '-',
                        'tp_price': f"{pos.tp_price:.4f}" if pos.tp_price else '-',
                        'pnl': f"{pnl_display_val:+.{b_dec}f} {b.currency} ({pnl_pct_trade:+.2f}%)",
                        'reason': 'ABIERTA',
                        'is_open': True
                    })

                for t in reversed(b.trade_history):
                    pnl_val_t = t.get('pnl', 0.0)
                    pnl_pct_t = t.get('pnl_pct', 0.0)
                    side_str = str(t.get('side', '')).upper()
                    side_icon = "📈 " if side_str == "LONG" else "📉 "
                    trade_ts = t.get('exit_time') or t.get('entry_time')
                    rows.append({
                        'bot_id': b.bot_id,
                        'bot_name': b.name,
                        'time_compact': _format_compact_time(trade_ts),
                        'side': f"{side_icon}{side_str}",
                        'raw_side': t.get('side', ''),
                        'entry_time': str(t.get('entry_time', '')),
                        'exit_time': str(t.get('exit_time', '')),
                        'entry_price': f"{t.get('entry_price', 0):.4f}",
                        'exit_price': f"{t.get('exit_price', 0):.4f}",
                        'sl_price': f"{t.get('sl_price', 0):.4f}" if t.get('sl_price') else '-',
                        'tp_price': f"{t.get('tp_price', 0):.4f}" if t.get('tp_price') else '-',
                        'pnl': f"{pnl_val_t:+.{b_dec}f} {b.currency} ({pnl_pct_t:+.2f}%)",
                        'reason': t.get('reason', ''),
                        'is_open': False
                    })
            
            # Solo actualizar la tabla AG-Grid si cambió el bot seleccionado o la cantidad de trades
            trades_fingerprint = f"{self.trades_view_mode}_{bot.bot_id}_{len(rows)}_{sum(1 for b in target_bots if b.position is not None)}_{sum(len(b.trade_history) for b in target_bots)}"
            if getattr(self, '_last_trades_hash', None) != trades_fingerprint:
                self.trades_grid.options['rowData'] = rows
                self.trades_grid.update()
                self._last_trades_hash = trades_fingerprint

        # 6. Consola de logs
        if hasattr(self, 'log_label'):
            lines = bot.log_lines if bot.log_lines else ["Esperando eventos del bot..."]
            self.log_label.set_text('\n'.join(lines[-40:]))

    def _render_inspector_config(self, bot: PaperTrader):
        """Renderiza la tarjeta de configuración y parámetros del bot seleccionado (sin resetear inputs cada segundo)."""
        self.inspector_config_container.clear()
        params = bot.custom_parameters or (bot.strategy.parameters if bot.strategy else {})
        
        with self.inspector_config_container:
            # Metadata rápida
            with ui.row().classes('w-full justify-between items-center gap-2 mb-3 flex-wrap'):
                with ui.row().classes('gap-2 items-center flex-wrap'):
                    ui.badge(f"Estrategia: {os.path.basename(bot.strategy_yaml_path)}", color='indigo-900').props('rounded').classes('text-indigo-200 text-xs font-bold')
                    ui.badge(f"Par: {bot.symbol}", color='blue-900').props('rounded').classes('text-blue-200 text-xs font-mono')
                    ui.badge(f"TF: {bot.timeframe}", color='purple-900').props('rounded').classes('text-purple-200 text-xs font-mono')
                    
                    def toggle_net():
                        bot.use_testnet = not bot.use_testnet
                        if bot._client:
                            bot._client.use_testnet = bot.use_testnet
                        self._rendered_inspector_status = None
                        self._rendered_selected_bot_id = None
                        self._refresh_ui_elements(force_dom_rebuild=True)
                        ui.notify(f"🌐 Red cambiada a {'Binance Testnet (Ejecución Real)' if bot.use_testnet else 'Simulación Local'} para '{bot.name}'", type='positive' if bot.use_testnet else 'info')

                    net_title = "⚡ Binance Testnet (Activo)" if bot.use_testnet else "🔒 Simulación Local (Clic para activar Testnet)"
                    net_color = "bg-yellow-500 text-black font-bold" if bot.use_testnet else "bg-gray-800 text-gray-300 hover:text-white"
                    ui.button(net_title, icon='cloud_done' if bot.use_testnet else 'cloud_off', on_click=toggle_net).props('dense').classes(f'text-xs px-2.5 py-1 rounded-lg shadow {net_color}').tooltip('Alternar envío de órdenes a Binance Testnet')

                    if bot.is_running:
                        ui.badge("⚡ Hot-Reload Activo", color='green-900').props('rounded').classes('text-green-300 text-xs font-semibold')
                
                # Botón de edición completa
                ui.button('Editar Configuración Completa', icon='edit', on_click=lambda: self._open_edit_bot_dialog(bot.bot_id)).props('outline dense').classes('text-xs text-yellow-400')

            # Render de parámetros interactivos
            if not params:
                ui.label('Esta estrategia no tiene parámetros configurables.').classes('text-xs text-gray-400 italic')
                return

            self.inspector_param_inputs.clear()
            with ui.row().classes('w-full items-center justify-between mb-1'):
                ui.label('Parámetros de la estrategia (Editables en tiempo real):').classes('text-xs text-gray-300 font-semibold')
                if bot.is_running:
                    ui.label('🟢 Los cambios se aplicarán en la siguiente vela sin detener el bot').classes('text-[11px] text-green-400 italic')

            with ui.grid(columns=2 if len(params) <= 4 else 4).classes('w-full gap-2 mb-3'):
                for k, v in params.items():
                    if isinstance(v, (int, float)):
                        inp = ui.number(label=k, value=v).classes('w-full bg-gray-950/80 rounded')
                    else:
                        inp = ui.input(label=k, value=str(v)).classes('w-full bg-gray-950/80 rounded')
                    self.inspector_param_inputs[k] = inp

            def save_inspector_params():
                new_p = {}
                for k, inp in self.inspector_param_inputs.items():
                    val = inp.value
                    try:
                        if isinstance(val, str) and val.replace('.', '', 1).isdigit():
                            new_p[k] = float(val) if '.' in val else int(val)
                        else:
                            new_p[k] = val
                    except Exception:
                        new_p[k] = val
                bot.update_configuration(custom_parameters=new_p)
                self._rendered_inspector_status = None
                self._rendered_selected_bot_id = None
                self._refresh_ui_elements(force_dom_rebuild=True)
                ui.notify(f"✅ Parámetros actualizados para '{bot.name}' ({new_p}).", type='positive')

            with ui.row().classes('w-full justify-end'):
                btn_txt = '⚡ Aplicar Parámetros en Caliente' if bot.is_running else '💾 Guardar Parámetros'
                ui.button(btn_txt, icon='bolt' if bot.is_running else 'save', on_click=save_inspector_params).classes('bg-yellow-500 hover:bg-yellow-600 text-black font-bold text-xs py-1 px-3 rounded shadow')

    # ──────────────────────────────────────────────────────────────
    # Render Principal
    # ──────────────────────────────────────────────────────────────

    def render(self):
        # 1. Header principal
        with ui.row().classes('w-full justify-between items-center mb-6'):
            with ui.column().classes('gap-1'):
                ui.label('Live Monitor (Paper Trading Multi-Bot)').classes('text-3xl font-bold text-white tracking-tight')
                ui.label('Ejecución simultánea y monitoreo cuantitativo en tiempo real de múltiples estrategias').classes('text-sm text-gray-400')
            
            with ui.row().classes('gap-3 flex-wrap items-center'):
                ui.button('🧪 Test Binance', icon='verified', on_click=self._open_test_binance_dialog).props('outline color=yellow-500').classes('text-xs font-bold text-yellow-400 px-3 py-2 rounded-lg hover:bg-yellow-500/20 shadow').tooltip('Enviar orden de prueba en vivo a Binance Testnet')
                ui.button('➕ Crear Nuevo Bot', on_click=self._open_new_bot_dialog).classes('bg-yellow-500 hover:bg-yellow-600 text-black font-bold px-4 py-2 rounded-lg shadow-lg')
                ui.button('▶ Iniciar Todos', on_click=self._start_all_bots).classes('bg-green-600 hover:bg-green-700 text-white font-semibold px-4 py-2 rounded-lg shadow')
                ui.button('⏹ Detener Todos', on_click=self._stop_all_bots).classes('bg-red-600 hover:bg-red-700 text-white font-semibold px-4 py-2 rounded-lg shadow')

        # 2. Tarjetas Resumen Global (KPIs de Cartera)
        init_summary = bot_manager.get_portfolio_summary()
        with ui.grid(columns=4).classes('w-full gap-4 mb-6'):
            # KPI 1: Bots
            with ui.card().classes('bg-gray-800 p-4 rounded-xl border border-gray-700 shadow-md flex flex-col justify-between'):
                ui.label('BOTS ACTIVOS / TOTAL').classes('text-xs font-semibold text-gray-400 uppercase tracking-wider')
                self.kpi_bots_label = ui.label(f"{init_summary['running_bots']} / {init_summary['total_bots']}").classes('text-2xl font-bold text-white mt-1')
            
            # KPI 2: Balance Total
            with ui.card().classes('bg-gray-800 p-4 rounded-xl border border-gray-700 shadow-md flex flex-col justify-between'):
                ui.label('BALANCE TOTAL CARTERA').classes('text-xs font-semibold text-gray-400 uppercase tracking-wider')
                self.kpi_balance_label = ui.label(init_summary.get('balance_display', '0.00 USDT')).classes('text-2xl font-bold text-green-400 mt-1')

            # KPI 3: PNL Total
            with ui.card().classes('bg-gray-800 p-4 rounded-xl border border-gray-700 shadow-md flex flex-col justify-between'):
                ui.label('PNL TOTAL ACUMULADO').classes('text-xs font-semibold text-gray-400 uppercase tracking-wider')
                self.kpi_pnl_label = ui.label(init_summary.get('pnl_display', '+0.00 USDT (+0.00%)')).classes('text-2xl font-bold text-yellow-400 mt-1')

            # KPI 4: Posiciones
            with ui.card().classes('bg-gray-800 p-4 rounded-xl border border-gray-700 shadow-md flex flex-col justify-between'):
                ui.label('POSICIONES & WIN RATE').classes('text-xs font-semibold text-gray-400 uppercase tracking-wider')
                self.kpi_positions_label = ui.label(f"{init_summary['active_positions']} activas | WR: {init_summary['win_rate']:.1f}%").classes('text-2xl font-bold text-blue-400 mt-1')

        # 3. Sección: Lista de Bots Configurados
        with ui.column().classes('w-full mb-8'):
            with ui.row().classes('w-full justify-between items-center mb-3'):
                ui.label('📋 Mis Bots de Trading').classes('text-xl font-bold text-white')
                ui.label('Haz clic en "Monitorear" para enfocar su gráfico y órdenes, o en "Config" para ajustar parámetros').classes('text-xs text-gray-400 italic')
            
            self.bots_cards_container = ui.column().classes('w-full')
            self._build_bots_grid_dom(bot_manager.get_all_bots())

        # 4. Sección: Panel de Inspección y Monitoreo del Bot Seleccionado
        with ui.card().classes('bg-gray-800/90 border border-gray-700 p-6 rounded-2xl w-full mb-6 shadow-xl'):
            with ui.row().classes('w-full justify-between items-center mb-6 pb-4 border-b border-gray-700 flex-wrap gap-4'):
                with ui.column().classes('gap-1'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('query_stats', size='28px', color='yellow-400')
                        ui.label('Inspector de Bot en Vivo').classes('text-2xl font-bold text-white')
                    ui.label('Selecciona el bot a visualizar haciendo clic en su botón:').classes('text-xs text-gray-400')
                
                # Switcher interactivo de botones de bots
                with ui.row().classes('gap-3 items-center flex-wrap'):
                    self.inspector_bot_tabs_container = ui.row().classes('gap-2 items-center flex-wrap')
                    
                    bots = bot_manager.get_all_bots()
                    options = {b.bot_id: f"{b.name} [{b.symbol}] ({'🟢' if b.is_running else '🔴'})" for b in bots}
                    self.bot_select = ui.select(
                        options=options,
                        value=self.selected_bot_id,
                        on_change=self._on_bot_select_change,
                        label='Buscar Bot'
                    ).classes('w-56 bg-gray-950 text-white rounded')

            # TARJETA DE CONFIGURACIÓN Y PARÁMETROS DEL BOT SELECCIONADO
            with ui.card().classes('bg-gray-900/90 text-white p-4 w-full rounded-xl border border-gray-700 mb-6 shadow-md'):
                with ui.row().classes('items-center gap-2 mb-2'):
                    ui.icon('tune', color='yellow-400', size='20px')
                    ui.label('Configuración y Parámetros del Bot').classes('text-base font-bold text-yellow-400')
                self.inspector_config_container = ui.column().classes('w-full')

            # Fila de Métricas Cuantitativas en Vivo del Bot + Order Book
            with ui.row().classes('w-full gap-4 mb-6 flex-wrap lg:flex-nowrap'):
                with ui.grid(columns=4).classes('flex-1 gap-3'):
                    # Tarjeta 1: Estado y Saldo
                    with ui.card().classes('bg-gray-900 text-white p-3 rounded-xl border border-gray-700/80 flex flex-col justify-between shadow'):
                        ui.label('ESTADO & SALDO').classes('text-[10px] font-semibold text-gray-400 uppercase tracking-wider')
                        self.inspector_status_label = ui.label('DETENIDO 🔴').classes('text-base font-bold text-white mt-1')
                        self.inspector_balance_label = ui.label('10,000.00 USDT').classes('text-xs font-semibold text-green-400')

                    # Tarjeta 2: Win Rate y Operaciones
                    with ui.card().classes('bg-gray-900 text-white p-3 rounded-xl border border-gray-700/80 flex flex-col justify-between shadow'):
                        ui.label('WIN RATE & TRADES').classes('text-[10px] font-semibold text-gray-400 uppercase tracking-wider')
                        self.inspector_winrate_label = ui.label('0.0% (0/0)').classes('text-base font-bold text-yellow-400 mt-1')
                        self.inspector_trades_label = ui.label('Trades: 0').classes('text-xs text-gray-300')

                    # Tarjeta 3: PnL & Profit Factor
                    with ui.card().classes('bg-gray-900 text-white p-3 rounded-xl border border-gray-700/80 flex flex-col justify-between shadow'):
                        ui.label('TOTAL PNL & RENDIMIENTO').classes('text-[10px] font-semibold text-gray-400 uppercase tracking-wider')
                        self.inspector_pnl_label = ui.label('+0.00 (+0.00%)').classes('text-base font-bold text-green-400 mt-1')
                        self.inspector_pf_label = ui.label('Profit Factor: 0.00').classes('text-xs text-gray-300')

                    # Tarjeta 4: Posición Abierta
                    with ui.card().classes('bg-gray-900 text-white p-3 rounded-xl border border-gray-700/80 flex flex-col justify-between shadow'):
                        ui.label('POSICIÓN ABIERTA').classes('text-[10px] font-semibold text-gray-400 uppercase tracking-wider')
                        self.inspector_pos_label = ui.label('NINGUNA').classes('text-base font-bold text-blue-400 mt-1')
                        self.inspector_sltp_label = ui.label('SL: - | TP: -').classes('text-xs text-gray-400 truncate')

                # Orderbook Ticker
                with ui.card().classes('bg-gray-900 text-white p-3 w-72 rounded-xl border border-gray-700/80 flex flex-col justify-between shadow'):
                    ui.label('Order Book Ticker (Live)').classes('text-xs font-bold text-gray-300 text-center')
                    with ui.row().classes('w-full justify-between items-center my-auto'):
                        with ui.column().classes('items-center border border-red-500 rounded px-2 py-1 flex-1 mr-1'):
                            self.bid_label = ui.label('0.00').classes('text-red-500 font-bold text-sm')
                            with ui.row().classes('gap-1 items-center'):
                                ui.label('SELL').classes('text-red-500 text-[10px] font-bold')
                                self.bid_qty_label = ui.label('0.000').classes('text-red-300 text-[9px]')
                        
                        with ui.column().classes('items-center mx-0.5'):
                            ui.label('SPREAD').classes('text-[9px] text-gray-400')
                            self.spread_label = ui.label('0.00').classes('text-gray-300 font-bold text-xs')

                        with ui.column().classes('items-center border border-blue-500 rounded px-2 py-1 flex-1 ml-1'):
                            self.ask_label = ui.label('0.00').classes('text-blue-500 font-bold text-sm')
                            with ui.row().classes('gap-1 items-center'):
                                ui.label('BUY').classes('text-blue-500 text-[10px] font-bold')
                                self.ask_qty_label = ui.label('0.000').classes('text-blue-300 text-[9px]')

            # ── TARJETA: POSICIÓN EN BINANCE FUTURES (EXCHANGE LIVE SYNC) ──
            with ui.card().classes('bg-gray-900 p-4 w-full mb-6 rounded-xl border border-yellow-500/40 shadow-lg'):
                with ui.row().classes('w-full justify-between items-center mb-3 flex-wrap gap-2'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('bolt', color='yellow-400', size='22px')
                        ui.label('Posición Abierta en Binance Futures (Exchange Live Sync)').classes('text-base font-bold text-white')
                    with ui.row().classes('items-center gap-2'):
                        self.binance_pos_badge = ui.badge('Sincronizando...', color='yellow-950').props('rounded').classes('text-yellow-300 text-xs font-bold')
                        ui.button(
                            '🔄 Sincronizar Ahora',
                            icon='sync',
                            on_click=lambda: self._sync_exchange_positions(show_notify=True)
                        ).props('dense outline color=yellow-400').classes('text-xs text-yellow-400 hover:bg-yellow-500/20 font-bold px-2.5 py-1 rounded shadow').tooltip('Fuerza la consulta en vivo de posiciones a Binance Futures')
                        ui.button(
                            'Limpiar Órdenes Huérfanas',
                            icon='cleaning_services',
                            on_click=self._clean_orphan_orders
                        ).props('dense outline color=yellow-400').classes('text-xs text-yellow-400 hover:bg-yellow-500/20').tooltip('Cancela órdenes SL/TP pendientes sin posición abierta')
                        self.btn_close_binance_pos = ui.button(
                            'Cerrar en Binance', 
                            icon='cancel', 
                            on_click=self._close_selected_bot_binance_pos
                        ).props('dense outline color=red-400').classes('text-xs text-red-400 hover:bg-red-500/20')
                        self.btn_close_binance_pos.set_visibility(False)

                self.binance_pos_grid = ui.aggrid({
                    'defaultColDef': {'flex': 1, 'sortable': True, 'resizable': True},
                    'columnDefs': [
                        {'headerName': 'Contrato',         'field': 'symbol_display', 'maxWidth': 160, 'cellClass': 'font-bold text-white'},
                        {'headerName': 'Tamaño (Size)',    'field': 'size_display',   'maxWidth': 140, 'cellClass': 'font-mono text-yellow-300 font-bold'},
                        {'headerName': 'Precio Entrada',   'field': 'entry_price',    'maxWidth': 135, 'cellClass': 'font-mono text-gray-200'},
                        {'headerName': 'Break Even',       'field': 'break_even',     'maxWidth': 135, 'cellClass': 'font-mono text-gray-400'},
                        {'headerName': 'Precio Marca',     'field': 'mark_price',     'maxWidth': 135, 'cellClass': 'font-mono text-sky-400'},
                        {'headerName': 'Margen',           'field': 'margin_display', 'maxWidth': 150, 'cellClass': 'font-mono text-blue-300'},
                        {'headerName': 'PNL (ROI %)',      'field': 'pnl_display',    'maxWidth': 180, 'cellClass': 'font-mono font-bold'},
                        {'headerName': 'Bot Asociado',     'field': 'bot_name',       'maxWidth': 160, 'cellClass': 'text-gray-300 font-semibold'},
                    ],
                    'rowData': [],
                    'rowClassRules': {
                        'text-green-400 font-semibold': 'data.raw_pnl > 0',
                        'text-red-400 font-semibold':   'data.raw_pnl < 0',
                    }
                }).classes('h-28 text-white')

            # ── TARJETA: TERMINAL DE ÓRDENES MANUALES & TEST DE ALERTAS ──
            with ui.card().classes('bg-gray-900 p-4 w-full mb-6 rounded-xl border border-slate-700/80 shadow-xl'):
                with ui.row().classes('w-full justify-between items-center mb-3 flex-wrap gap-2'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('touch_app', color='yellow-400', size='22px')
                        ui.label('Terminal de Órdenes Manuales & Test de Alertas en Binance').classes('text-base font-bold text-white')
                    ui.label('Envía órdenes manuales al exchange o simula un rechazo para verificar las alertas 🚨').classes('text-xs text-gray-400 italic')

                with ui.row().classes('w-full gap-3 items-end flex-wrap bg-gray-950 p-3 rounded-lg border border-gray-800'):
                    self.manual_symbol_input = ui.input(label='Símbolo', value='BTC/USDT').classes('w-36')
                    self.manual_side_select = ui.select({'BUY': '🟢 BUY (Long)', 'SELL': '🔴 SELL (Short)'}, value='BUY', label='Lado').classes('w-36')
                    self.manual_type_select = ui.select({'MARKET': '⚡ MARKET', 'LIMIT': '🎯 LIMIT'}, value='MARKET', label='Tipo').classes('w-32')
                    self.manual_qty_input = ui.number(label='Cantidad', value=0.001, min=0.00001, step=0.001).classes('w-28')
                    self.manual_price_input = ui.number(label='Precio Limit', value=0.0).classes('w-32')
                    
                    ui.button('🚀 Enviar a Binance', icon='send', on_click=self._submit_manual_order).props('dense').classes('bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-bold px-4 py-2.5 rounded-lg shadow')
                    ui.button('🧪 Test: Rechazo Deliberado', icon='warning', on_click=self._trigger_deliberate_rejection_test).props('dense outline color=amber-400').classes('text-xs text-amber-400 hover:bg-amber-500/20 font-bold px-3 py-2.5 rounded-lg').tooltip('Envía una orden intencionalmente inválida a Binance para probar la alerta roja y Telegram')

            # Gráfico Plotly
            with ui.card().classes('bg-gray-900 p-4 w-full mb-6 rounded-xl border border-gray-700/80'):
                with ui.row().classes('w-full justify-between items-center mb-2'):
                    self.chart_title_label = ui.label('Gráfico Técnico en Vivo').classes('text-lg font-bold text-white')
                    self.btn_clear_highlight = ui.button('✖ Quitar Enfoque de Trade', on_click=self._clear_trade_highlight).props('dense outline color=yellow-500').classes('text-xs text-yellow-400 hover:bg-yellow-500/20')
                    self.btn_clear_highlight.set_visibility(False)
                self.chart = ui.plotly(self._build_empty_chart()).classes('w-full h-96')

            # Historial de Trades con Selector de Vista (Individual vs Cartera) y Fecha/Hora Compacta
            with ui.card().classes('bg-gray-900 p-4 w-full mb-6 rounded-xl border border-gray-700/80'):
                with ui.row().classes('w-full justify-between items-center mb-4 flex-wrap gap-2'):
                    with ui.column().classes('gap-0'):
                        self.trades_title_label = ui.label('Historial de Trades (Sesión)').classes('text-lg font-bold text-white')
                        ui.label('💡 Haz clic en cualquier fila para enfocarla y señalarla en la gráfica').classes('text-xs text-gray-400 italic')
                    
                    with ui.row().classes('gap-1 bg-gray-950 p-1 rounded-lg border border-gray-800'):
                        self.btn_view_selected = ui.button('🎯 Bot Seleccionado', on_click=lambda: self._set_trades_view_mode("selected")).props('dense').classes('bg-yellow-500 text-black text-xs font-bold px-3 py-1 rounded')
                        self.btn_view_all = ui.button('🌐 Cartera Completa (Todos)', on_click=lambda: self._set_trades_view_mode("all")).props('dense flat').classes('text-gray-300 text-xs font-medium px-3 py-1 rounded hover:text-white')

                self.trades_grid = ui.aggrid({
                    'defaultColDef': {'flex': 1, 'sortable': True, 'resizable': True},
                    'columnDefs': [
                        {'headerName': 'Bot',            'field': 'bot_name',     'maxWidth': 140},
                        {'headerName': 'Fecha/Hora',     'field': 'time_compact', 'maxWidth': 130, 'cellClass': 'font-mono text-xs text-gray-300 text-center'},
                        {'headerName': 'Lado',           'field': 'side',         'maxWidth': 105},
                        {'headerName': 'Entrada',        'field': 'entry_price',  'maxWidth': 115},
                        {'headerName': 'Salida',         'field': 'exit_price',   'maxWidth': 115},
                        {'headerName': 'SL',             'field': 'sl_price',     'maxWidth': 115},
                        {'headerName': 'TP',             'field': 'tp_price',     'maxWidth': 115},
                        {'headerName': 'PNL',            'field': 'pnl',          'maxWidth': 160},
                        {'headerName': 'Gatillo/Razón',  'field': 'reason'},
                    ],
                    'rowData': [],
                    'rowSelection': 'single',
                    'rowClassRules': {
                        'text-green-400 font-semibold cursor-pointer': 'parseFloat(data.pnl) > 0',
                        'text-red-400 font-semibold cursor-pointer':   'parseFloat(data.pnl) <= 0',
                    }
                }).classes('h-64 text-white')
                self.trades_grid.on('rowClicked', self._on_trade_row_clicked)

            # Consola de Logs del Bot Seleccionado
            with ui.card().classes('bg-black/90 text-green-400 p-4 w-full h-44 overflow-y-auto font-mono rounded-xl border border-gray-800 shadow-inner'):
                ui.label('Consola de Ejecución en Vivo:').classes('text-xs text-gray-400 mb-2')
                self.log_label = ui.label('Esperando inicio...').classes('whitespace-pre-wrap text-xs')

        # 5. Timer de actualización periódica (1.5 segundos para fluidez sin sobrecarga)
        self.timer = ui.timer(1.5, self._ui_update_loop)
        ui.context.client.on_disconnect(lambda: self.timer.deactivate() if self.timer else None)


def render_live_monitor_page():
    page = LiveMonitorPage()
    page.render()
    return page
