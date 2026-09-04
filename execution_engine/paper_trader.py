import os
import logging
import asyncio
import threading
import time
import pandas as pd
from datetime import datetime, timezone
from typing import Optional, Callable

from .binance_client import BinanceTestnetClient
from strategy_engine.base_strategy import BaseStrategy
from strategy_engine.conditions import ConditionEvaluator
from data_layer.storage import SessionLocal, PaperTrade

logger = logging.getLogger(__name__)


class Position:
    def __init__(self, side: str, entry_price: float, quantity: float, timestamp):
        self.side = side          # 'long' or 'short'
        self.entry_price = entry_price
        self.quantity = quantity
        self.entry_timestamp = timestamp
        self.sl_price: Optional[float] = None
        self.tp_price: Optional[float] = None


class PaperTrader:
    """
    Motor de Paper Trading en vivo.
    Descarga velas históricas para calentar indicadores, se conecta al
    WebSocket/REST de Binance y evalúa las condiciones
    de entrada/salida de la estrategia en cada vela cerrada.
    """

    def __init__(
        self,
        strategy_yaml_path: str,
        initial_balance: float = 1.0,
        currency: str = "BTC",
        update_callback: Optional[Callable] = None,
        custom_parameters: Optional[dict] = None,
        use_testnet: bool = False,
        custom_timeframe: Optional[str] = None,
        custom_symbol: Optional[str] = None,
        bot_id: Optional[str] = None,
        name: Optional[str] = None,
        save_callback: Optional[Callable] = None,
    ):
        self.strategy_yaml_path = strategy_yaml_path
        self.custom_parameters = custom_parameters or {}
        self.strategy = BaseStrategy(strategy_yaml_path, custom_parameters=self.custom_parameters)
        self.update_callback = update_callback
        self.save_callback = save_callback
        self.use_testnet = use_testnet

        # Símbolo y timeframe tomados del YAML o con fallback sensato, con override del usuario
        self.symbol = custom_symbol if custom_symbol else self.strategy.config.get("symbol", "BTC/USDT")
        self.timeframe = custom_timeframe if custom_timeframe else self.strategy.config.get("timeframe", "1h")
        self.strategy_name = self.strategy.config.get("strategy_name", os.path.splitext(os.path.basename(strategy_yaml_path))[0])

        self.bot_id = bot_id if bot_id else f"bot_{int(time.time() * 1000)}"
        self.name = name if name else f"{self.symbol} ({self.strategy_name})"
        self.currency = currency

        # Tipos de órdenes de ejecución (Por defecto: Señales a MARKET, SL/TP a LIMIT)
        exec_cfg = self.strategy.config.get("execution", {})
        self.order_types = {
            "entry": exec_cfg.get("entry_order_type", "MARKET").upper(),
            "exit": exec_cfg.get("exit_order_type", "MARKET").upper(),
            "stop_loss": exec_cfg.get("stop_loss_order_type", "LIMIT").upper(),
            "take_profit": exec_cfg.get("take_profit_order_type", "LIMIT").upper(),
        }

        self.initial_balance = initial_balance
        self.current_balance = initial_balance

        self.position: Optional[Position] = None
        self.trade_history: list = []
        self.session_id: str = datetime.now().strftime("%Y%m%d%H%M%S")

        self.stats = {
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
        }

        self.klines_df = pd.DataFrame()
        self.is_running = False
        self.status = "STOPPED"  # STOPPED, STARTING, RUNNING, ERROR
        self.status_message = "Detenido"
        self.log_lines: list[str] = []
        
        self.current_bid = 0.0
        self.current_ask = 0.0
        self.current_bid_qty = 0.0
        self.current_ask_qty = 0.0
        self.binance_position_info: Optional[dict] = None
        self._last_open_ts: float = 0.0

        self._client: Optional[BinanceTestnetClient] = None
        self._lock = threading.Lock()
        self._polling_thread: Optional[threading.Thread] = None

        # Notificador de Telegram — opcional; no rompe si no está configurado
        try:
            from notifications.telegram_bot import TelegramNotifier
            self.telegram = TelegramNotifier()
        except Exception:
            self.telegram = None

    # ──────────────────────────────────────────────────────────────
    # Ciclo de vida
    # ──────────────────────────────────────────────────────────────

    def start(self):
        """Descarga histórico de velas y conecta el polling de Binance."""
        if self.is_running:
            return

        self.status = "STARTING"
        self.status_message = "Iniciando..."
        self.is_running = True
        
        self._notify(
            f"🚀 Iniciando Bot '{self.name}' | {self.symbol} {self.timeframe} | "
            f"Balance: {self.current_balance:,.2f} {self.currency}"
        )

        try:
            self._client = BinanceTestnetClient(use_testnet=self.use_testnet)
        except Exception as e:
            err_msg = f"❌ Error conectando a Binance: {e}"
            self._notify(err_msg)
            self.status = "ERROR"
            self.status_message = str(e)
            self.is_running = False
            return

        # Warm-up: descargar últimas 300 velas
        self._notify("⏳ Descargando histórico para calentar indicadores...")
        try:
            binance_symbol = self.symbol.replace("/", "").upper()
            raw_klines = self._client.client.get_klines(
                symbol=binance_symbol, interval=self.timeframe, limit=300
            )
        except Exception as e:
            err_msg = f"❌ Error descargando histórico: {e}"
            self._notify(err_msg)
            self.status = "ERROR"
            self.status_message = str(e)
            self.is_running = False
            return

        try:
            records = []
            for k in raw_klines:
                records.append(
                    {
                        "timestamp": pd.to_datetime(k[0], unit="ms", utc=True),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                    }
                )

            with self._lock:
                self.klines_df = pd.DataFrame(records)
                if not self.klines_df.empty:
                    self.klines_df.set_index("timestamp", inplace=True)

            self.status = "RUNNING"
            self.status_message = "Operando en vivo"
            self._save_state()
            self._notify(
                f"✅ Histórico descargado ({len(self.klines_df)} velas). "
                "Escuchando mercado en vivo..."
            )
        except Exception as e:
            logger.error("Exception processing klines: %s", e, exc_info=True)
            self._notify(f"❌ Error procesando histórico: {e}")
            self.status = "ERROR"
            self.status_message = str(e)
            self.is_running = False
            return

        # Iniciar polling loop
        self._polling_thread = threading.Thread(target=self._polling_loop, daemon=True)
        self._polling_thread.start()

    def _polling_loop(self):
        """Hilo de fondo que consulta el mercado cada 2 segundos y sincroniza con Binance Futures."""
        binance_symbol = self.symbol.replace("/", "").upper()
        loop_counter = 0
        while self.is_running:
            loop_counter += 1
            try:
                # 1. Obtener bid/ask actual (Orderbook ticker)
                ticker = self._client.client.get_orderbook_ticker(symbol=binance_symbol)
                with self._lock:
                    self.current_bid = float(ticker['bidPrice'])
                    self.current_ask = float(ticker['askPrice'])
                    self.current_bid_qty = float(ticker['bidQty'])
                    self.current_ask_qty = float(ticker['askQty'])
                
                # 2. Limit=2 para obtener la vela actual (en curso)
                raw_klines = self._client.client.get_klines(
                    symbol=binance_symbol, interval=self.timeframe, limit=2
                )
                if raw_klines:
                    k = raw_klines[-1]
                    kline_data = {
                        "timestamp": int(k[0]),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                    }
                    self._on_new_kline(kline_data)

                # 3. Sincronización continua de la Posición en Binance Futures (cada 4 seg)
                if self.use_testnet and self._client and self._client.api_key and (loop_counter % 2 == 0):
                    try:
                        pos_list = self._client.client.futures_position_information(symbol=binance_symbol)
                        if pos_list:
                            p_info = pos_list[0]
                            pos_amt = float(p_info.get('positionAmt', 0.0))
                            entry_p = float(p_info.get('entryPrice', 0.0))
                            unrealized_pnl = float(p_info.get('unRealizedProfit', 0.0))
                            mark_p = float(p_info.get('markPrice', 0.0))
                            be_p = float(p_info.get('breakEvenPrice', 0.0) or entry_p)
                            im = float(p_info.get('isolatedMargin', 0.0) or p_info.get('initialMargin', 0.0))
                            leverage = int(p_info.get('leverage', 1))

                            with self._lock:
                                # Si hay posición abierta en Binance y el bot tiene posición interna, sincronizar
                                if pos_amt != 0:
                                    self.binance_position_info = {
                                        "symbol": binance_symbol,
                                        "amount": pos_amt,
                                        "abs_amount": abs(pos_amt),
                                        "entry_price": entry_p,
                                        "break_even_price": be_p,
                                        "mark_price": mark_p,
                                        "unrealized_pnl": unrealized_pnl,
                                        "initial_margin": im,
                                        "leverage": leverage,
                                        "margin_type": p_info.get('marginType', 'cross'),
                                    }
                                    self._had_open_binance_pos = True
                                    if self.position:
                                        if entry_p > 0:
                                            self.position.entry_price = entry_p
                                        self.position.quantity = abs(pos_amt)

                                # Si se cerró en Binance (ej. TP/SL, liquidación o cierre manual en la web de Binance)
                                elif pos_amt == 0:
                                    self.binance_position_info = None
                                    if self.position:
                                        exit_p = mark_p if mark_p > 0 else (self.current_bid if self.position.side == 'long' else self.current_ask)
                                        if exit_p <= 0:
                                            exit_p = self.position.entry_price
                                        self._close_position(exit_p, datetime.now(), reason="BINANCE_EXCHANGE_CLOSED")

                                    # Cancelar cualquier orden condicional huérfana restante si no hay posición abierta en el exchange
                                    if getattr(self, '_had_open_binance_pos', False) or (self.position is None and self.use_testnet and self._client):
                                        try:
                                            self._client.cancel_all_open_orders(binance_symbol)
                                            if getattr(self, '_had_open_binance_pos', False):
                                                self._notify("🧹 Posición cerrada en Binance. Órdenes condicionales (SL/TP) canceladas.")
                                        except Exception:
                                            pass
                                        self._had_open_binance_pos = False

                    except Exception as e_pos:
                        logger.debug(f"[{self.name}] Error sync binance position: {e_pos}")

            except Exception as e:
                logger.error(f"[{self.name}] Error polling klines: {e}")
            
            time.sleep(2)

    def update_configuration(
        self,
        name: Optional[str] = None,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        initial_balance: Optional[float] = None,
        currency: Optional[str] = None,
        use_testnet: Optional[bool] = None,
        custom_parameters: Optional[dict] = None,
        strategy_yaml_path: Optional[str] = None,
        order_types: Optional[dict] = None
    ):
        """Actualiza la configuración, parámetros y tipos de órdenes del bot (soporta Hot-Reload en caliente)."""
        reconnect_needed = False
        was_running = self.is_running
        
        if strategy_yaml_path and strategy_yaml_path != self.strategy_yaml_path:
            self.strategy_yaml_path = strategy_yaml_path
        if custom_parameters is not None:
            self.custom_parameters = custom_parameters
        if order_types is not None:
            self.order_types.update(order_types)
        
        self.strategy = BaseStrategy(self.strategy_yaml_path, custom_parameters=self.custom_parameters)
        self.strategy_name = self.strategy.config.get("strategy_name", os.path.splitext(os.path.basename(self.strategy_yaml_path))[0])
        
        if name is not None and name.strip():
            self.name = name.strip()
        if symbol is not None and symbol.strip() and symbol.strip().upper() != self.symbol:
            self.symbol = symbol.strip().upper()
            reconnect_needed = True
        if timeframe is not None and timeframe != self.timeframe:
            self.timeframe = timeframe
            reconnect_needed = True
        if currency is not None:
            self.currency = currency
        if use_testnet is not None and use_testnet != self.use_testnet:
            self.use_testnet = use_testnet
            reconnect_needed = True
        if initial_balance is not None:
            self.initial_balance = initial_balance
            if len(self.trade_history) == 0:
                self.current_balance = initial_balance

        # Si el bot tiene una posición abierta y se actualizaron parámetros, recalcular SL/TP dinámicamente
        if self.position and not self.klines_df.empty:
            idx = len(self.klines_df) - 1
            try:
                sl_price, tp_price = self.strategy.risk_manager.compute_sl_tp(self.klines_df, idx, self.position.side)
                self.position.sl_price = sl_price
                self.position.tp_price = tp_price
            except Exception:
                pass

        if was_running and reconnect_needed:
            self.stop()
            self.start()

        self._save_state()
        self._notify(f"⚙️ Configuración actualizada para '{self.name}'. Parámetros: {self.custom_parameters} | Órdenes: {self.order_types}")

    def stop(self):
        """Detiene el bot, cancela órdenes pendientes en Binance y cierra las conexiones."""
        self.is_running = False
        self.status = "STOPPED"
        self.status_message = "Detenido"
        if self._client:
            try:
                if self.use_testnet:
                    self._client.cancel_all_open_orders(self.symbol)
                self._client.stop()
            except Exception:
                pass
        self._save_state()
        self._notify(
            f"🛑 Bot '{self.name}' detenido. Balance final: {self.current_balance:,.2f} {self.currency}"
        )

    def _save_state(self):
        """Notifica al gestor para persistir el estado del bot en disco."""
        if self.save_callback:
            try:
                self.save_callback(self)
            except Exception as e:
                logger.warning("Error guardando estado del bot %s: %s", self.name, e)

    def to_dict(self) -> dict:
        """Serializa el estado completo del bot a un diccionario JSON-friendly."""
        pos_data = None
        if self.position:
            pos_data = {
                "side": self.position.side,
                "entry_price": float(self.position.entry_price),
                "quantity": float(self.position.quantity),
                "entry_timestamp": str(self.position.entry_timestamp),
                "sl_price": float(self.position.sl_price) if self.position.sl_price is not None else None,
                "tp_price": float(self.position.tp_price) if self.position.tp_price is not None else None,
            }

        # Serializar historial de trades asegurando tipos estándar
        trades_clean = []
        for t in self.trade_history:
            trades_clean.append({
                "entry_time": str(t.get("entry_time")),
                "exit_time": str(t.get("exit_time")),
                "side": t.get("side"),
                "entry_price": float(t.get("entry_price", 0.0)),
                "exit_price": float(t.get("exit_price", 0.0)),
                "sl_price": float(t.get("sl_price")) if t.get("sl_price") is not None else None,
                "tp_price": float(t.get("tp_price")) if t.get("tp_price") is not None else None,
                "quantity": float(t.get("quantity", 0.0)),
                "pnl": float(t.get("pnl", 0.0)),
                "pnl_pct": float(t.get("pnl_pct", 0.0)),
                "reason": t.get("reason"),
            })

        return {
            "bot_id": self.bot_id,
            "name": self.name,
            "strategy_yaml_path": self.strategy_yaml_path,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "initial_balance": float(self.initial_balance),
            "current_balance": float(self.current_balance),
            "currency": self.currency,
            "use_testnet": bool(self.use_testnet),
            "status": self.status,
            "status_message": self.status_message,
            "is_running": bool(self.is_running),
            "custom_parameters": self.custom_parameters,
            "order_types": self.order_types,
            "position": pos_data,
            "trade_history": trades_clean,
            "stats": self.stats,
            "log_lines": self.log_lines[-40:],
        }

    def restore_from_dict(self, data: dict):
        """Restaura el estado guardado del bot desde un diccionario."""
        self.initial_balance = float(data.get("initial_balance", self.initial_balance))
        self.current_balance = float(data.get("current_balance", self.current_balance))
        self.currency = data.get("currency", self.currency)
        self.use_testnet = bool(data.get("use_testnet", self.use_testnet))
        self.status = data.get("status", "STOPPED")
        self.status_message = data.get("status_message", "Detenido")
        self.custom_parameters = data.get("custom_parameters", {})
        self.order_types = data.get("order_types", self.order_types)
        self.stats = data.get("stats", self.stats)
        self.trade_history = data.get("trade_history", [])
        self.log_lines = data.get("log_lines", [])

        # Restaurar posición abierta si existía
        pos_data = data.get("position")
        if pos_data:
            pos = Position(
                side=pos_data.get("side", "long"),
                entry_price=float(pos_data.get("entry_price", 0.0)),
                quantity=float(pos_data.get("quantity", 0.0)),
                timestamp=pos_data.get("entry_timestamp"),
            )
            pos.sl_price = float(pos_data.get("sl_price")) if pos_data.get("sl_price") is not None else None
            pos.tp_price = float(pos_data.get("tp_price")) if pos_data.get("tp_price") is not None else None
            self.position = pos
        else:
            self.position = None

    # ──────────────────────────────────────────────────────────────
    # Callbacks del WebSocket
    # ──────────────────────────────────────────────────────────────

    def _on_new_kline(self, kline_data: dict):
        """Callback invocado por el WebSocket al cerrar cada vela."""
        if not self.is_running:
            return

        with self._lock:
            ts = pd.to_datetime(kline_data["timestamp"], unit="ms", utc=True)
            new_row = pd.DataFrame(
                [
                    {
                        "open": float(kline_data["open"]),
                        "high": float(kline_data["high"]),
                        "low": float(kline_data["low"]),
                        "close": float(kline_data["close"]),
                        "volume": float(kline_data["volume"]),
                    }
                ],
                index=[ts],
            )

            # Si ya existe esa timestamp (vela duplicada), actualizar en lugar de concatenar
            if ts in self.klines_df.index:
                self.klines_df.loc[ts] = new_row.iloc[0]
            else:
                self.klines_df = pd.concat([self.klines_df, new_row])

            # Mantener ventana de 300 velas en memoria
            if len(self.klines_df) > 300:
                self.klines_df = self.klines_df.iloc[-300:]

            try:
                self._evaluate_market()
            except Exception as exc:
                logger.error("Error en _evaluate_market: %s", exc, exc_info=True)
                self._notify(f"⚠️ Error evaluando mercado: {exc}")

            # Notificar siempre para actualizar el gráfico en vivo
            self._notify()

    # ──────────────────────────────────────────────────────────────
    # Lógica de mercado
    # ──────────────────────────────────────────────────────────────

    def _evaluate_market(self):
        if self.klines_df.empty or len(self.klines_df) < 2:
            return

        current_price = float(self.klines_df["close"].iloc[-1])
        current_ts = self.klines_df.index[-1]

        if self.position:
            # ── Chequeo de SL / TP ──────────────────────────────
            action = self._check_sl_tp(current_price)
            if action:
                self._close_position(current_price, current_ts, action)
                return

            # ── Condición de salida (señal) ──────────────────────
            try:
                exit_signal = ConditionEvaluator.evaluate_conditions(
                    self.klines_df,
                    self.strategy.config.get("exit_conditions", {}),
                )
                if not exit_signal.empty and bool(exit_signal.iloc[-1]):
                    self._close_position(current_price, current_ts, "EXIT_SIGNAL")
                    return
            except Exception as exc:
                logger.warning("Error evaluando exit_conditions: %s", exc)

        else:
            # ── Condición de entrada ─────────────────────────────
            try:
                entry_signal = ConditionEvaluator.evaluate_conditions(
                    self.klines_df,
                    self.strategy.config.get("entry_conditions", {}),
                )
                if not entry_signal.empty and bool(entry_signal.iloc[-1]):
                    # Detectar dirección de la estrategia (Long o Short)
                    trade_dir = str(self.strategy.config.get("trade_direction", "Long")).strip().lower()
                    side = "short" if "short" in trade_dir else "long"
                    self._open_position(side, current_price, current_ts)
            except Exception as exc:
                logger.warning("Error evaluando entry_conditions: %s", exc)

    def _check_sl_tp(self, price: float) -> Optional[str]:
        """Devuelve 'SL', 'TP' o None según el precio actual y el lado de la posición (Long o Short)."""
        if not self.position:
            return None

        sl = self.position.sl_price
        tp = self.position.tp_price

        if self.position.side == "long":
            if sl is not None and price <= sl:
                return "SL"
            if tp is not None and price >= tp:
                return "TP"
        else:  # SHORT
            if sl is not None and price >= sl:
                return "SL"
            if tp is not None and price <= tp:
                return "TP"
        return None

    def _trigger_critical_order_alert(self, title: str, details: Optional[dict] = None):
        """Genera una alerta crítica inmediata en logs, estado, Telegram y UI."""
        details = details or {}
        logger.error("[PaperTrader %s] 🚨 CRITICAL ALERT: %s | Detalles: %s", self.name, title, details)
        
        err_detail = details.get("error") or details.get("status") or ""
        alert_msg = f"🚨 {title}" + (f" ({err_detail})" if err_detail else "")
        self.status_message = alert_msg
        
        # Enviar notificación local y marcar como alerta
        self._notify(alert_msg, is_alert=True)

        # Telegram Alert enriquecida
        if self.telegram:
            try:
                alert_payload = {
                    "Bot": self.name,
                    "Símbolo": self.symbol,
                    "Timeframe": self.timeframe,
                    **details
                }
                self.telegram.send_alert(title, alert_payload, is_critical=True)
            except Exception as e:
                logger.warning("Error enviando alerta de Telegram: %s", e)

    # ──────────────────────────────────────────────────────────────
    # Gestión de posiciones
    # ──────────────────────────────────────────────────────────────

    def _open_position(self, side: str, price: float, ts):
        """Abre una nueva posición y calcula SL/TP, verificando ejecución en el exchange si aplica."""
        risk_mgr = self.strategy.risk_manager

        # Si la divisa de la cuenta es BTC o la base del par
        base_asset = self.symbol.split("/")[0].upper() if "/" in self.symbol else "BTC"
        is_base_currency = (self.currency.upper() == base_asset)
        is_btc_account = (self.currency.upper() == "BTC")

        method = str(risk_mgr.sizing_config.get("method", "compounding")).lower()
        pct = float(risk_mgr.sizing_config.get("value", 100.0)) / 100.0 if method in ["compounding", "percent_equity", "full_capital"] else 1.0

        if is_base_currency:
            quantity = self.current_balance * pct
        elif is_btc_account:
            # Colateral en BTC operando otro par (ej. ETH/USDT, SOL/USDT) con Multi-Assets
            btc_price = self._client.get_symbol_price("BTCUSDT") if self._client else 0.0
            if btc_price <= 0:
                btc_price = float(self.klines_df["close"].iloc[-1]) if "BTC" in self.symbol else 80000.0
            capital_in_quote = self.current_balance * btc_price
            quantity = risk_mgr.compute_position_size(capital_in_quote, price)
        else:
            quantity = risk_mgr.compute_position_size(self.current_balance, price)

        if quantity <= 0:
            self._notify(f"⚠️ Saldo insuficiente ({self.current_balance:.4f} {self.currency}) para abrir posición.")
            return

        # Calcular SL y TP usando compute_sl_tp del RiskManager
        idx = len(self.klines_df) - 1
        try:
            sl_price, tp_price = risk_mgr.compute_sl_tp(self.klines_df, idx, side)
        except Exception:
            sl_price = price * (0.98 if side == "long" else 1.02)
            tp_price = price * (1.04 if side == "long" else 0.96)

        entry_type = self.order_types.get("entry", "MARKET").upper()
        sl_type = self.order_types.get("stop_loss", "LIMIT").upper()
        tp_type = self.order_types.get("take_profit", "LIMIT").upper()

        # Envío y comprobación activa de ejecución en Binance Futures si está configurado
        if self.use_testnet:
            if not self._client:
                self._trigger_critical_order_alert(
                    "Modo Binance activo pero sin cliente conectado",
                    {"error": "Cliente Binance no inicializado", "side": side, "symbol": self.symbol}
                )
                return

            # Cancelar previamente cualquier orden residual antes de abrir una nueva
            self._client.cancel_all_open_orders(self.symbol)

            ext_order, err = self._client.place_futures_order(
                self.symbol, side, quantity, order_type=entry_type, price=price, verify_execution=True
            )

            # Validar si se ejecutó en el exchange
            if not ext_order or err:
                self._trigger_critical_order_alert(
                    f"Orden de Entrada {side.upper()} NO ejecutada en Binance",
                    {
                        "Tipo Orden": entry_type,
                        "Lado": side.upper(),
                        "Cantidad": f"{quantity:.4f}",
                        "Precio Señal": f"{price:.2f}",
                        "error": err or "Orden rechazada o no completada por Binance"
                    }
                )
                # Abortar apertura para evitar desincronización con el exchange
                return

            order_status = str(ext_order.get("status", "")).upper()
            if order_status not in ["FILLED", "PARTIALLY_FILLED", "NEW"]:
                self._trigger_critical_order_alert(
                    f"Orden de Entrada {side.upper()} en estado inválido ({order_status})",
                    {
                        "ID Orden": ext_order.get("orderId"),
                        "Estado": order_status,
                        "error": f"Estado no operable: {order_status}"
                    }
                )
                return

            # Sincronizar precio real de ejecución (avgPrice) si Binance lo reporta
            real_fill_price = float(ext_order.get('avgPrice', 0.0) or 0.0)
            if real_fill_price > 0:
                price = real_fill_price

            real_qty = float(ext_order.get('executedQty', 0.0) or 0.0)
            if real_qty > 0:
                quantity = real_qty

            self._notify(
                f"⚡ ORDEN ENTRADA ({entry_type}) EJECUTADA en Binance | "
                f"Precio Fill: {price:.2f} | ID: {ext_order.get('orderId')} | Status: {order_status}"
            )

            # Colocar órdenes condicionales de TP y SL en Binance (Por defecto LIMIT)
            sl_tp_res = self._client.place_futures_sl_tp(
                self.symbol, side, quantity,
                sl_price=sl_price, tp_price=tp_price,
                sl_order_type=sl_type, tp_order_type=tp_type
            )
            if sl_tp_res.get("tp_order"):
                self._notify(f"🎯 ORDEN TP ({tp_type}) COLOCADA @ {tp_price:.4f} | ID: {sl_tp_res['tp_order'].get('orderId')}")
            if sl_tp_res.get("sl_order"):
                self._notify(f"🛡️ ORDEN SL ({sl_type}) COLOCADA @ {sl_price:.4f} | ID: {sl_tp_res['sl_order'].get('orderId')}")
            if sl_tp_res.get("errors"):
                self._notify(f"⚠️ Binance SL/TP Info: {', '.join(sl_tp_res['errors'])}")

        # Abrir posición interna una vez confirmada la ejecución
        self.position = Position(side, price, quantity, ts)
        self.position.sl_price = sl_price
        self.position.tp_price = tp_price
        self._last_open_ts = time.time()

        self._notify(
            f"🟢 OPEN {side.upper()} [{entry_type}] | Precio: {price:.4f} | "
            f"SL ({sl_type}): {sl_price:.4f} | TP ({tp_type}): {tp_price:.4f} | "
            f"Qty: {quantity:.6f} {base_asset}"
        )
        self._save_state()

    def _close_position(self, price: float, ts, reason: str):
        """Cierra la posición abierta, calcula PNL y persiste el trade, verificando ejecución en el exchange."""
        pos = self.position
        if not pos:
            return

        base_asset = self.symbol.split("/")[0].upper() if "/" in self.symbol else "BTC"
        is_base_currency = (self.currency.upper() == base_asset)
        exit_type = self.order_types.get("exit", "MARKET").upper()

        # Cierre en Binance Futures si está configurado
        if self.use_testnet:
            if self._client:
                # 1. CANCELAR TODAS LAS ÓRDENES CONDICIONALES PENDIENTES PRIMERO (SL / TP)
                self._client.cancel_all_open_orders(self.symbol)

                # 2. Si el cierre no se originó directamente en el exchange, enviar orden de salida a Binance
                if reason != "BINANCE_EXCHANGE_CLOSED":
                    close_order, err = self._client.close_futures_position(
                        self.symbol, pos.side, pos.quantity, order_type=exit_type, price=price, verify_execution=True
                    )
                    if close_order and not err:
                        # Sincronizar precio real de salida (avgPrice) si Binance lo reporta
                        real_fill_price = float(close_order.get('avgPrice', 0.0) or 0.0)
                        if real_fill_price > 0:
                            price = real_fill_price

                        self._notify(
                            f"⚡ CIERRE ({exit_type}) EJECUTADO en Binance | "
                            f"Precio Fill: {price:.2f} | ID: {close_order.get('orderId')} | Status: {close_order.get('status')}"
                        )
                    else:
                        self._trigger_critical_order_alert(
                            f"Fallo al ejecutar orden de CIERRE ({exit_type}) en Binance",
                            {
                                "Lado Cierre": "SELL" if pos.side == "long" else "BUY",
                                "Cantidad": f"{pos.quantity:.4f}",
                                "Razón": reason,
                                "error": err or "Orden de cierre rechazada o no confirmada por Binance"
                            }
                        )

                # 3. Cancelación de seguridad posterior para asegurar 0 órdenes residuales
                self._client.cancel_all_open_orders(self.symbol)
            else:
                self._trigger_critical_order_alert(
                    "Modo Binance activo pero sin cliente conectado al cerrar",
                    {"error": "Cliente Binance no inicializado", "symbol": self.symbol}
                )

        # PNL en cotizada (ej. USDT)
        if pos.side == "long":
            raw_pnl_quote = (price - pos.entry_price) * pos.quantity
            pnl_pct = ((price - pos.entry_price) / pos.entry_price) * 100.0
        else:
            raw_pnl_quote = (pos.entry_price - price) * pos.quantity
            pnl_pct = ((pos.entry_price - price) / pos.entry_price) * 100.0

        # Comisión estimada (0.1% entrada + 0.1% salida)
        fee_quote = pos.quantity * price * 0.002
        net_pnl_quote = raw_pnl_quote - fee_quote

        # Si la cuenta está en divisa base (ej. BTC) o la cuenta general es en BTC
        if self.currency.upper() == "BTC":
            if base_asset == "BTC":
                net_pnl = net_pnl_quote / price
            else:
                btc_p = self._client.get_symbol_price("BTCUSDT") if self._client else 0.0
                if btc_p <= 0:
                    btc_p = 80000.0
                net_pnl = net_pnl_quote / btc_p
        elif is_base_currency:
            net_pnl = net_pnl_quote / price
        else:
            net_pnl = net_pnl_quote

        self.current_balance += net_pnl

        trade = {
            "entry_time": pos.entry_timestamp,
            "exit_time": ts,
            "side": pos.side,
            "entry_price": pos.entry_price,
            "exit_price": price,
            "sl_price": pos.sl_price,
            "tp_price": pos.tp_price,
            "quantity": pos.quantity,
            "pnl": net_pnl,
            "pnl_pct": pnl_pct,
            "reason": reason,
        }
        self.trade_history.append(trade)

        # Estadísticas de sesión
        self.stats["total_trades"] += 1
        self.stats["total_pnl"] += net_pnl
        if net_pnl > 0:
            self.stats["wins"] += 1
        else:
            self.stats["losses"] += 1
        self.stats["win_rate"] = (
            self.stats["wins"] / self.stats["total_trades"] * 100.0
        )

        # Persistir en BD
        self._save_trade_to_db(trade)

        emoji = "✅" if net_pnl > 0 else "❌"
        dec = 4 if is_base_currency else 2
        self._notify(
            f"🔴 CLOSE {pos.side.upper()} | Precio: {price:.4f} | "
            f"Razón: {reason} | PNL: {net_pnl:+.{dec}f} {self.currency} ({pnl_pct:+.2f}%) {emoji} | "
            f"Balance: {self.current_balance:,.{dec}f} {self.currency}"
        )
        self.position = None
        self._save_state()

    def get_detailed_stats(self) -> dict:
        """Calcula estadísticas cuantitativas avanzadas de los trades del bot."""
        total_trades = len(self.trade_history)
        if total_trades == 0:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "total_pnl_pct": 0.0,
                "profit_factor": 0.0,
                "avg_trade_pnl": 0.0,
                "best_trade": 0.0,
                "worst_trade": 0.0,
                "gross_profit": 0.0,
                "gross_loss": 0.0,
            }

        pnls = [t.get("pnl", 0.0) for t in self.trade_history]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total_pnl = sum(pnls)
        total_pnl_pct = (total_pnl / self.initial_balance * 100.0) if self.initial_balance > 0 else 0.0
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

        return {
            "total_trades": total_trades,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / total_trades * 100.0),
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "profit_factor": profit_factor,
            "avg_trade_pnl": total_pnl / total_trades,
            "best_trade": max(pnls) if pnls else 0.0,
            "worst_trade": min(pnls) if pnls else 0.0,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
        }

    # ──────────────────────────────────────────────────────────────
    # Persistencia
    # ──────────────────────────────────────────────────────────────

    def _save_trade_to_db(self, trade: dict):
        db = SessionLocal()
        try:
            db_trade = PaperTrade(
                session_id=self.session_id,
                symbol=self.symbol,
                strategy_name=self.strategy.config.get("strategy_name", "Unknown"),
                side=trade["side"],
                entry_time=trade["entry_time"],
                exit_time=trade["exit_time"],
                entry_price=trade["entry_price"],
                exit_price=trade["exit_price"],
                pnl=trade["pnl"],
                reason=trade["reason"],
            )
            db.add(db_trade)
            db.commit()
        except Exception as exc:
            logger.error("Error guardando trade en BD: %s", exc)
            db.rollback()
        finally:
            db.close()

    # ──────────────────────────────────────────────────────────────
    # Notificaciones
    # ──────────────────────────────────────────────────────────────

    def _notify(self, message: str = "", is_alert: bool = False):
        if message:
            if is_alert:
                logger.error("[PaperTrader %s] %s", self.name, message)
            else:
                logger.info("[PaperTrader %s] %s", self.name, message)

            timestamp_str = datetime.now().strftime("%H:%M:%S")
            self.log_lines.append(f"[{timestamp_str}] {message}")
            if len(self.log_lines) > 100:
                self.log_lines = self.log_lines[-100:]

            # Telegram (solo si no es alerta crítica manejada por _trigger_critical_order_alert)
            if self.telegram and not is_alert:
                try:
                    self.telegram.send_message(f"<b>[PaperTrader - {self.name}]</b>\n{message}")
                except Exception:
                    pass

        # Callback a la UI — debe ser thread-safe
        if self.update_callback:
            state = {
                "bot_id": self.bot_id,
                "name": self.name,
                "status": self.status,
                "status_message": self.status_message,
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "currency": self.currency,
                "initial_balance": self.initial_balance,
                "balance": self.current_balance,
                "position": self.position,
                "trades": self.trade_history[:],   # copia para seguridad
                "stats": dict(self.stats),
                "klines": self.klines_df.copy() if not self.klines_df.empty else pd.DataFrame(),
                "log_lines": self.log_lines[:],
                "current_bid": getattr(self, 'current_bid', 0.0),
                "current_ask": getattr(self, 'current_ask', 0.0),
                "current_bid_qty": getattr(self, 'current_bid_qty', 0.0),
                "current_ask_qty": getattr(self, 'current_ask_qty', 0.0),
                "is_alert": is_alert
            }
            if message:
                state["message"] = message
            if is_alert:
                state["alert"] = message
                
            try:
                self.update_callback(state)
            except Exception as exc:
                logger.warning("Error en update_callback: %s", exc)
