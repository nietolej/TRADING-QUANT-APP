import os
import re
import json
import logging
import asyncio
import random
import threading
import time
import weakref
import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import Optional, Callable, Tuple

from .binance_client import BinanceTestnetClient, format_binance_error
from .market_stream import stream_hub
from .trade_stats import compute_detailed_stats
from strategy_engine.base_strategy import BaseStrategy
from strategy_engine.conditions import ConditionEvaluator, state_exit_conditions, is_short_direction
from strategy_engine.risk_management import RiskManager
from data_layer.storage import SessionLocal, PaperTrade

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


class Position:
    def __init__(self, side: str, entry_price: float, quantity: float, timestamp):
        self.side = side          # 'long' or 'short'
        self.entry_price = entry_price
        self.quantity = quantity
        self.entry_timestamp = timestamp
        self.sl_price: Optional[float] = None
        self.tp_price: Optional[float] = None
        # Referencias {'kind': 'algo'|'order', 'id': int} a las órdenes SL/TP de ESTA posición en
        # Binance: permiten cancelar/consultar solo las propias aunque otros bots operen el símbolo.
        self.sl_ref: Optional[dict] = None
        self.tp_ref: Optional[dict] = None


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
        PaperTrader._ALL_BOTS[self.bot_id] = self
        self.name = name if name else f"{self.symbol} ({self.strategy_name})"
        self.currency = currency

        # Tipos de órdenes de ejecución (Por defecto: Señales a MARKET, SL a MARKET, TP a LIMIT).
        # El SL por defecto es STOP_MARKET: un STOP (stop-limit) con límite = precio del stop puede
        # quedar sin ejecutarse si el precio salta más allá, mientras el backtest asume que sale.
        exec_cfg = self.strategy.config.get("execution", {})
        self.order_types = {
            "entry": exec_cfg.get("entry_order_type", "MARKET").upper(),
            "exit": exec_cfg.get("exit_order_type", "MARKET").upper(),
            "stop_loss": exec_cfg.get("stop_loss_order_type", "MARKET").upper(),
            "take_profit": exec_cfg.get("take_profit_order_type", "LIMIT").upper(),
            # Cuándo se evalúan las señales de la estrategia: "intrabar" (en vivo) o "close" (al cierre de vela).
            "signal_mode": str(exec_cfg.get("signal_mode", "close")).lower(),
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
        self.started_at: Optional[str] = None
        self.unexecuted_orders: list[dict] = []
        self.log_lines: list[str] = []
        
        self.current_bid = 0.0
        self.current_ask = 0.0
        self.current_bid_qty = 0.0
        self.current_ask_qty = 0.0
        self.binance_position_info: Optional[dict] = None
        self._last_open_ts: float = 0.0
        self._zero_pos_reads: int = 0
        self._last_signal_candle = None   # última vela cerrada ya evaluada (modo 'close')

        self._client: Optional[BinanceTestnetClient] = None
        # RLock (no Lock simple) porque _save_state()/to_dict() puede invocarse desde
        # dentro de una sección ya protegida por este mismo lock (ver sync de posición
        # de Binance), y un Lock normal produciria un deadlock del hilo del bot.
        self._lock = threading.RLock()
        self._last_snapshot = None
        self._polling_thread: Optional[threading.Thread] = None
        self._sync_thread: Optional[threading.Thread] = None
        # Cada start() abre una nueva generación: los hilos de una anterior salen solos (evita bucles
        # duplicados si update_configuration hace stop()+start() y el hilo viejo aún no había despertado).
        self._run_generation: int = 0

        # Notificador de Telegram — opcional; no rompe si no está configurado
        try:
            from notifications.telegram_bot import TelegramNotifier
            self.telegram = TelegramNotifier()
        except Exception:
            self.telegram = None

    # ──────────────────────────────────────────────────────────────
    # Ciclo de vida
    # ──────────────────────────────────────────────────────────────

    # Tipos de Stop Loss que el backtester SÍ recalcula vela a vela (ver
    # backtest_engine/backtester.py y RiskManager.update_trailing_sl) pero que este motor
    # de ejecución en vivo/paper AÚN NO implementa: compute_sl_tp solo calcula el SL una
    # vez, al abrir la posición, y jamás se vuelve a mover mientras el trade sigue abierto.
    # PENDIENTE DE ANALIZAR/IMPLEMENTAR: aplicar update_trailing_sl en el loop de polling
    # de _poll_binance_klines y re-emitir la orden condicional SL en Binance cuando cambie.
    _LIVE_UNSUPPORTED_SL_TYPES = {
        "trailing_percent", "trailing", "break_even", "breakeven",
        "chandelier", "chandelier_exit",
    }

    @staticmethod
    def _fmt_level(price) -> str:
        return f"{price:.4f}" if price is not None else "sin orden"

    def _warn_if_sl_tp_disabled(self):
        """Avisa si el bot va a operar sin Stop Loss o sin Take Profit (configurado en 0 o
        ausente). Desde que el motor en vivo usa la misma regla que el backtest (0 = sin
        orden) ya no se aplica un 2% / 4% por defecto, así que esto se hace visible."""
        try:
            legs = self.strategy.risk_manager.disabled_legs()
        except Exception:
            logger.warning("[%s] No se pudo comprobar la configuración de SL/TP", self.name, exc_info=True)
            return
        if not legs:
            return
        if "SL" in legs:
            self._notify(
                f"⚠️ RIESGO: '{self.name}' opera SIN STOP LOSS (SL en 0 o sin configurar). "
                f"No se colocará orden de stop en Binance: la pérdida por operación no tiene límite "
                f"hasta la señal de salida.",
                is_alert=True
            )
        if "TP" in legs:
            self._notify(f"ℹ️ '{self.name}' opera sin Take Profit (TP en 0 o sin configurar).")

    SIZING_MODES = ("compounding", "fixed_fractional", "fixed_amount")

    def _sizing_config(self, initial_capital_quote: float) -> dict:
        """
        Tamaño de posición con los mismos modos que el Analizador de Estrategias (que ignora el
        sizing del YAML y usa el que elige el usuario). Antes el bot solo leía el del YAML, así
        que un backtest con "Riesgo fijo" o "Monto fijo" no correspondía a lo que operaba el bot.
        """
        mode = str(self.order_types.get("sizing", "compounding")).lower()
        if mode == "fixed_fractional":
            return {"method": "fixed_fractional", "risk_per_trade_pct": 1.0}
        if mode == "fixed_amount":
            return {"method": "fixed_amount", "value": float(initial_capital_quote)}
        return {"method": "compounding", "value": 100.0}

    def _live_unsupported_reason(self) -> Optional[str]:
        """
        Motivo por el que esta estrategia NO puede ejecutarse en vivo, o None. El motor en vivo
        evalúa las reglas entry/exit_conditions del YAML; las estrategias con clase propia
        (class_name) generan sus señales con código Python que solo usa el backtest, y sin
        reglas de entrada el bot arrancaba y nunca operaba, sin avisar.
        """
        class_name = self.strategy.config.get("class_name")
        if class_name:
            return (f"la estrategia usa la clase '{class_name}', cuyas señales solo existen en el "
                    f"backtest; el motor en vivo no puede reproducirlas.")
        rules = (self.strategy.config.get("entry_conditions") or {}).get("rules") or []
        if not rules:
            return "la estrategia no tiene reglas de entrada (entry_conditions) en su YAML."
        unsupported = sorted({r.get("type") for r in rules if r.get("type") == "onchain_threshold"})
        if unsupported:
            return "las reglas on-chain no tienen datos en vivo (solo existen en el backtest)."
        return None

    def _indicator_periods(self) -> list:
        periods = []
        for block in ("entry_conditions", "exit_conditions"):
            for rule in (self.strategy.config.get(block) or {}).get("rules", []) or []:
                for key in ("period1", "period2", "period", "fast_period", "slow_period"):
                    periods.append(rule.get(key))
                for key in ("indicator_1", "indicator_2"):
                    if isinstance(rule.get(key), dict):
                        periods.append(rule[key].get("period"))
        out = []
        for p in periods:
            try:
                out.append(int(float(p)))
            except (TypeError, ValueError):
                pass
        return out

    def _warn_if_warmup_short(self):
        """Avisa si algún indicador necesita más historia de la que el bot mantiene en memoria:
        sus valores (y por tanto sus cruces) no coincidirían con los del backtest."""
        try:
            longest = max(self._indicator_periods(), default=0)
        except Exception:
            logger.warning("[%s] No se pudieron leer los periodos de la estrategia", self.name, exc_info=True)
            return
        if longest > self.MAX_FAITHFUL_PERIOD:
            self._notify(
                f"⚠️ La estrategia usa un indicador de periodo {longest}: con {self.KLINES_WINDOW} velas de "
                f"historia en vivo sus valores pueden diferir de los del backtest (fiable hasta periodo "
                f"{self.MAX_FAITHFUL_PERIOD}).",
                is_alert=True
            )

    def _warn_if_dynamic_sl_unsupported_live(self):
        """Alerta si la estrategia usa un SL dinámico que solo se respeta en backtest.

        Riesgo: el Stop Loss se fija una sola vez al entrar y nunca se mueve, aunque el
        backtest con el que se validó la estrategia SÍ lo iba desplazando (protegiendo
        ganancias / moviendo a break-even). En vivo, el bot queda con más riesgo del que
        el backtest sugiere. Esto es una funcionalidad pendiente, no un valor por defecto.
        """
        try:
            raw_sl_type = str(
                self.strategy.risk_manager.sl_config.get("type", "")
            ).lower().strip().replace(" ", "_")
            is_dynamic_flagged = (
                raw_sl_type == "dynamic"
                and str(self.strategy.risk_manager.sl_config.get("dynamic_method", "")).lower() == "chandelier"
            )
            if raw_sl_type in self._LIVE_UNSUPPORTED_SL_TYPES or is_dynamic_flagged:
                self._notify(
                    f"⚠️ RIESGO: la estrategia usa Stop Loss tipo '{raw_sl_type}' "
                    f"(trailing/break-even/chandelier), que en este motor de ejecución en vivo "
                    f"NO se recalcula tras la entrada — el SL queda fijo en el nivel inicial "
                    f"durante todo el trade, a diferencia del backtest. El riesgo real puede ser "
                    f"mayor al esperado. [Pendiente de analizar/implementar]"
                )
                logger.warning(
                    "[%s] SL dinámico '%s' configurado pero no soportado por el motor en vivo "
                    "(pendiente de implementar trailing/break-even/chandelier en tiempo real).",
                    self.name, raw_sl_type,
                )
        except Exception as e:
            logger.debug("No se pudo evaluar el tipo de SL dinámico para alerta: %s", e)

    def start(self, reset_started_at: bool = True):
        """Descarga histórico de velas y conecta el polling de Binance."""
        if self.is_running:
            return

        unsupported = self._live_unsupported_reason()
        if unsupported:
            self.status = "ERROR"
            self.status_message = unsupported
            self._notify(f"⛔ No se puede iniciar '{self.name}': {unsupported}", is_alert=True)
            return

        self.status = "STARTING"
        self.status_message = "Iniciando..."
        self.is_running = True
        PaperTrader._ACTIVE_BOTS[self.bot_id] = self
        if reset_started_at or not self.started_at:
            self.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        self._notify(
            f"🚀 Iniciando Bot '{self.name}' | {self.symbol} {self.timeframe} | "
            f"Balance: {self.current_balance:,.2f} {self.currency}"
        )
        self._warn_if_dynamic_sl_unsupported_live()
        self._warn_if_sl_tp_disabled()
        self._warn_if_warmup_short()

        try:
            self._client = BinanceTestnetClient(use_testnet=self.use_testnet, bot_id=self.bot_id)
        except Exception as e:
            friendly_msg = format_binance_error(e)
            self._notify(f"❌ Error conectando a Binance: {friendly_msg}")
            self.status = "ERROR"
            self.status_message = friendly_msg
            self.is_running = False
            return

        # Warm-up: descargar las últimas KLINES_WINDOW velas
        self._notify("⏳ Descargando histórico para calentar indicadores...")
        try:
            binance_symbol = self.symbol.replace("/", "").upper()
            # futures_klines (no get_klines, que es Spot): si el historico de calentamiento viene
            # de Spot y el polling en vivo (mas abajo) ya usa Futures, se produce una discontinuidad
            # de precio entre el historico y las velas en vivo dentro del mismo klines_df, pudiendo
            # generar cruces de indicadores (EMA, etc.) falsos justo al arrancar el bot.
            raw_klines = self._client.client.futures_klines(
                symbol=binance_symbol, interval=self.timeframe, limit=self.KLINES_WINDOW
            )
        except Exception as e:
            friendly_msg = format_binance_error(e)
            self._notify(f"❌ Error descargando histórico: {friendly_msg}")
            self.status = "ERROR"
            self.status_message = friendly_msg
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
            friendly_msg = format_binance_error(e)
            self._notify(f"❌ Error procesando histórico: {friendly_msg}")
            self.status = "ERROR"
            self.status_message = friendly_msg
            self.is_running = False
            return

        self._last_signal_candle = None   # tras (re)iniciar, la última vela cerrada no se trata como señal nueva
        # Recuperar la posición propia (si el bot cayó con una abierta y no llegó a guardarla)
        try:
            self._recover_position_from_ledger()
        except Exception:
            logger.exception("[%s] No se pudo recuperar la posición desde el ledger", self.name)

        # Iniciar polling loop
        self._run_generation += 1
        generation = self._run_generation
        self._polling_thread = threading.Thread(
            target=self._polling_loop, args=(generation,), name=f"bot-market-{self.name}", daemon=True
        )
        self._sync_thread = threading.Thread(
            target=self._exchange_sync_loop, args=(generation,), name=f"bot-sync-{self.name}", daemon=True
        )
        self._polling_thread.start()
        self._sync_thread.start()

    # ──────────────────────────────────────────────────────────────
    # Hilos de fondo: datos de mercado (WebSocket + respaldo REST) y sincronización con el exchange
    # ──────────────────────────────────────────────────────────────

    # Velas en memoria (y de calentamiento al arrancar). Antes eran 300: una EMA de 200 conservaba
    # ~5% del valor inicial arbitrario y difería de la del backtest (calculada con todo el
    # histórico), y una SMA de más de 299 velas no llegaba a calcularse nunca. Máximo de Binance: 1500.
    KLINES_WINDOW = 1000
    # Periodo máximo de indicador que el calentamiento reproduce fielmente (una EMA necesita ~4x
    # su periodo para que el valor inicial deje de pesar).
    MAX_FAITHFUL_PERIOD = KLINES_WINDOW // 4

    USE_WEBSOCKET = True          # datos por WebSocket; con False (o si se corta) se usa el polling REST
    MIN_EVAL_INTERVAL_S = 0.25    # como mucho una evaluación de estrategia cada 250 ms
    REST_POLL_S = 2.0             # cadencia del respaldo REST cuando el WebSocket no está disponible
    WS_WARMUP_S = 3.0             # margen inicial para que el WebSocket conecte antes de usar REST
    EXCHANGE_SYNC_S = 4.0
    RATE_LIMIT_BACKOFF_BASE_S = 15.0   # espera base tras un error de límite de peticiones (-1003/-1015)
    RATE_LIMIT_BACKOFF_MAX_S = 180.0   # tope del backoff exponencial, para no esperar horas si persiste
    PROTECTION_CHECK_S = 30.0

    def _alive(self, generation: int) -> bool:
        """El hilo sigue vigente: el bot corre y no se reinició (start() tras stop()) mientras tanto."""
        return self.is_running and self._run_generation == generation

    def _sleep_while_alive(self, seconds: float, generation: int) -> None:
        end = time.monotonic() + seconds
        while self._alive(generation) and time.monotonic() < end:
            time.sleep(min(0.25, max(0.0, end - time.monotonic())))

    def _apply_book(self, bid: float, ask: float, bid_qty: float, ask_qty: float) -> None:
        with self._lock:
            self.current_bid, self.current_ask = bid, ask
            self.current_bid_qty, self.current_ask_qty = bid_qty, ask_qty

    def _poll_rest_once(self, binance_symbol: str) -> None:
        """Respaldo REST (una iteración): ticker + vela en curso de FUTURES."""
        # DEBE ser el endpoint de FUTURES: el de Spot devolvía el precio de Spot, no el de Futures
        # donde realmente opera el bot, desincronizando el precio de señal.
        ticker = self._client.client.futures_orderbook_ticker(symbol=binance_symbol)
        self._apply_book(
            float(ticker["bidPrice"]), float(ticker["askPrice"]), float(ticker["bidQty"]), float(ticker["askQty"])
        )
        raw_klines = self._client.client.futures_klines(symbol=binance_symbol, interval=self.timeframe, limit=2)
        if len(raw_klines) >= 2:
            # Cierre FINAL de la vela anterior: antes solo se procesaba la vela en curso y la
            # anterior quedaba con el último precio visto (hasta REST_POLL_S antes de su cierre).
            p = raw_klines[-2]
            self._apply_closed_kline({
                "timestamp": int(p[0]), "open": float(p[1]), "high": float(p[2]),
                "low": float(p[3]), "close": float(p[4]), "volume": float(p[5]),
            })
        if raw_klines:
            k = raw_klines[-1]
            self._on_new_kline({
                "timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
            })

    def _polling_loop(self, generation: int):
        """
        Hilo de mercado. Con el WebSocket sano el bot reacciona en milisegundos a cada cambio del
        libro/vela (antes: REST cada 2 s con ~0.5 s por llamada => precio con ~3 s de retraso). Si el
        flujo deja de llegar vuelve solo al polling REST y regresa al WebSocket cuando se recupera.
        """
        binance_symbol = self.symbol.replace("/", "").upper()
        sub = None
        if self.USE_WEBSOCKET:
            try:
                sub = stream_hub.subscribe(self.symbol, self.timeframe, self.use_testnet)
            except Exception:
                logger.exception("[%s] No se pudo abrir el WebSocket de mercado; se usará REST", self.name)
        mode = None
        last_eval = 0.0
        # Al arrancar se da unos segundos al WebSocket para que conecte antes de recurrir a REST.
        warmup_until = time.monotonic() + self.WS_WARMUP_S
        try:
            while self._alive(generation):
                used_stream = False
                try:
                    if sub is not None and sub.is_fresh():
                        used_stream = True
                        if mode != "ws":
                            mode = "ws"
                            logger.info("[%s] Datos de mercado por WebSocket (%s %s)", self.name, self.symbol, self.timeframe)
                        kline, book = sub.latest()
                        self._apply_book(book["bid"], book["ask"], book["bid_qty"], book["ask_qty"])
                        now = time.monotonic()
                        if sub.kline_changed() and (now - last_eval) >= self.MIN_EVAL_INTERVAL_S:
                            last_eval = now
                            closed = sub.latest_closed()
                            if closed:
                                self._apply_closed_kline(closed)
                            self._on_new_kline({k: kline[k] for k in ("timestamp", "open", "high", "low", "close", "volume")})
                    else:
                        if mode is None and sub is not None and time.monotonic() < warmup_until:
                            sub.wait_for_update(0.25)
                            continue
                        if mode != "rest":
                            if mode == "ws":
                                logger.warning("[%s] WebSocket sin datos recientes: polling REST de respaldo", self.name)
                                self._notify("⚠️ Flujo WebSocket interrumpido: usando polling REST de respaldo.")
                            mode = "rest"
                        self._poll_rest_once(binance_symbol)
                except Exception:
                    logger.exception("[%s] Error obteniendo datos de mercado", self.name)

                if used_stream:
                    sub.wait_for_update(0.25)
                else:
                    self._sleep_while_alive(self.REST_POLL_S, generation)
        finally:
            stream_hub.release(sub)

    def _exchange_sync_loop(self, generation: int):
        """Hilo de sincronización con Binance (posición propia, SL/TP). Va aparte del de mercado: las
        consultas REST de sincronización (~1.5 s) no deben retrasar la evaluación de la estrategia."""
        if not (self.use_testnet and self._client and self._client.api_key):
            return
        binance_symbol = self.symbol.replace("/", "").upper()
        next_protect = time.monotonic() + self.PROTECTION_CHECK_S
        while self._alive(generation):
            try:
                self._sync_with_exchange(binance_symbol)
                now = time.monotonic()
                if self.position and now >= next_protect:
                    next_protect = now + self.PROTECTION_CHECK_S
                    self._ensure_exchange_sl_tp()
            except Exception:
                logger.exception("[%s] Error en la sincronización con el exchange", self.name)
            wait = self.EXCHANGE_SYNC_S
            backoff = getattr(self, "_sync_backoff_until", 0.0) - time.monotonic()
            if backoff > wait:
                logger.warning("[%s] Límite de peticiones de Binance: sincronización en pausa %.0f s", self.name, backoff)
                wait = backoff
            self._sleep_while_alive(wait, generation)

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
        if self.is_running:
            self._warn_if_sl_tp_disabled()

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
                sl_price, tp_price = self.strategy.risk_manager.compute_sl_tp(self.klines_df, idx, self.position.side, strict=True)
                self.position.sl_price = sl_price
                self.position.tp_price = tp_price
            except Exception:
                logger.warning("[%s] No se pudo recalcular SL/TP tras cambiar la configuración", self.name, exc_info=True)

        if was_running and reconnect_needed:
            self.stop()
            self.start()

        self._save_state()
        self._notify(f"⚙️ Configuración actualizada para '{self.name}'. Parámetros: {self.custom_parameters} | Órdenes: {self.order_types}")

    def stop(self):
        """Detiene el bot, cancela órdenes pendientes en Binance y cierra las conexiones."""
        self.is_running = False
        if PaperTrader._ACTIVE_BOTS.get(self.bot_id) is self:
            PaperTrader._ACTIVE_BOTS.pop(self.bot_id, None)
        self.status = "STOPPED"
        self.status_message = "Detenido"
        if self._client:
            try:
                # Con posición abierta su SL/TP se conserva (detener el bot no debe dejarla desprotegida y
                # al reanudar se recupera). Sin posición se limpian solo las referencias propias sobrantes.
                if self.use_testnet and self.position is None:
                    self._cancel_own_protection(self.position)
                self._client.stop()
            except Exception:
                logger.warning("[%s] Error al limpiar/cerrar el cliente de Binance en stop()", self.name, exc_info=True)
        self._save_state()
        self._notify(
            f"🛑 Bot '{self.name}' detenido. Balance final: {self.current_balance:,.2f} {self.currency}"
        )

    def reset(self, new_initial_balance: Optional[float] = None):
        """Reinicia el bot a un estado limpio: detiene ejecución, borra historial de trades,
        estadísticas y posición, y restaura el balance. Usado por el endpoint /reset del
        daemon (bot_daemon.py) — antes no existía este método y esa llamada lanzaba
        AttributeError sin controlar, dejando el bot sin resetear."""
        if self.is_running:
            self.stop()

        if new_initial_balance is not None:
            self.initial_balance = new_initial_balance
        self.current_balance = self.initial_balance

        self.position = None
        self.binance_position_info = None
        self._had_open_binance_pos = False
        self.trade_history = []
        self.unexecuted_orders = []
        self.log_lines = []
        self.stats = {
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
        }
        self.session_id = datetime.now().strftime("%Y%m%d%H%M%S")
        self.started_at = None
        self.status = "STOPPED"
        self.status_message = "Detenido (reseteado)"

        self._save_state()
        self._notify(f"🔄 Bot '{self.name}' reseteado. Balance: {self.current_balance:,.2f} {self.currency}")

    def _save_state(self):
        """Notifica al gestor para persistir el estado del bot en disco."""
        if self.save_callback:
            try:
                self.save_callback(self)
            except Exception as e:
                logger.warning("Error guardando estado del bot %s: %s", self.name, e)

    # Máximo que to_dict() espera por el lock del bot antes de devolver la última foto conocida.
    SNAPSHOT_LOCK_TIMEOUT_S = 0.3

    def to_dict(self) -> dict:
        """
        Foto del estado del bot para la API/UI/persistencia. Mientras el hilo del bot sostiene su
        lock (evaluando la estrategia o esperando respuestas de Binance al enviar una orden, varios
        segundos) NO se espera indefinidamente: tras SNAPSHOT_LOCK_TIMEOUT_S se devuelve la última
        foto guardada, para que la interfaz y el daemon no se congelen detrás de una orden lenta.
        """
        if not self._lock.acquire(timeout=self.SNAPSHOT_LOCK_TIMEOUT_S):
            snapshot = self._last_snapshot
            if snapshot is not None:
                return snapshot
            self._lock.acquire()  # aún no hay foto previa: única vez que se espera sin límite
        try:
            data = self._to_dict_impl()
            self._last_snapshot = data
            return data
        finally:
            self._lock.release()

    def _to_dict_impl(self) -> dict:
        """Serializa el estado completo del bot a un diccionario JSON-friendly.

        Protegido por self._lock (RLock): sin esto, el hilo de polling puede estar
        mutando self.position/trade_history a mitad de camino (ver sync de posición
        de Binance) mientras save_state_to_disk() itera todos los bots, produciendo
        una foto de estado inconsistente/parcial persistida a disco.
        """
        with self._lock:
            pos_data = None
            if self.position:
                pos_data = {
                    "side": self.position.side,
                    "entry_price": float(self.position.entry_price),
                    "quantity": float(self.position.quantity),
                    "entry_timestamp": str(self.position.entry_timestamp),
                    "sl_price": float(self.position.sl_price) if self.position.sl_price is not None else None,
                    "tp_price": float(self.position.tp_price) if self.position.tp_price is not None else None,
                    "sl_ref": self.position.sl_ref,
                    "tp_ref": self.position.tp_ref,
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
                "started_at": self.started_at,
                "unexecuted_orders": self.unexecuted_orders[-100:],
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
        self.started_at = data.get("started_at")
        if not self.started_at:
            for line in reversed(data.get("log_lines", [])):
                if "Iniciando Bot" in line and line.startswith("["):
                    t_str = line[1:9]
                    self.started_at = f"{datetime.now().strftime('%Y-%m-%d')} {t_str}"
                    break
        self.custom_parameters = data.get("custom_parameters", {})
        self.order_types = data.get("order_types", self.order_types)
        self.stats = data.get("stats", self.stats)
        self.trade_history = data.get("trade_history", [])
        self.log_lines = data.get("log_lines", [])
        self.unexecuted_orders = data.get("unexecuted_orders", [])

        # Si no había historial de órdenes no ejecutadas explícito, extraer fallos históricos de log_lines
        if not self.unexecuted_orders:
            for line in self.log_lines:
                if "NO ejecutada en Binance" in line or "Fallo al ejecutar orden" in line:
                    t_match = line[1:9] if line.startswith("[") else datetime.now().strftime("%H:%M:%S")
                    reason_part = line.split("(", 1)[1].rstrip(")") if "(" in line else line
                    self.unexecuted_orders.append({
                        "order_id": f"unexec_hist_{len(self.unexecuted_orders)}",
                        "bot_id": self.bot_id,
                        "bot_name": self.name,
                        "symbol": self.symbol,
                        "timeframe": self.timeframe,
                        "strategy_name": self.strategy_name,
                        "network": "Testnet" if self.use_testnet else "Real (Mainnet)",
                        "timestamp": f"{datetime.now().strftime('%Y-%m-%d')} {t_match}",
                        "action": "ENTRY" if "Entrada" in line else "EXIT",
                        "side": "LONG" if "LONG" in line else ("SHORT" if "SHORT" in line else "BUY"),
                        "order_type": self.order_types.get("entry", "MARKET"),
                        "price": 0.0,
                        "quantity": 0.0,
                        "parameters": dict(self.custom_parameters or {}),
                        "parameters_summary": " | ".join(f"{k}: {v}" for k, v in (self.custom_parameters or {}).items()) or "Estándar",
                        "reason": reason_part,
                        "details": {"raw_log": line}
                    })

        # Restaurar posición abierta si existía
        pos_data = data.get("position")
        if pos_data:
            raw_ts = pos_data.get("entry_timestamp")
            # _save_state_to_disk serializa entry_timestamp con str(...); reparsearlo aquí
            # evita que quede como string suelto y rompa _to_naive_utc/_save_trade_to_db
            # más adelante cuando esta posición restaurada se cierre.
            entry_ts = pd.to_datetime(raw_ts, utc=True) if raw_ts else datetime.now(timezone.utc)
            pos = Position(
                side=pos_data.get("side", "long"),
                entry_price=float(pos_data.get("entry_price", 0.0)),
                quantity=float(pos_data.get("quantity", 0.0)),
                timestamp=entry_ts,
            )
            pos.sl_price = float(pos_data.get("sl_price")) if pos_data.get("sl_price") is not None else None
            pos.tp_price = float(pos_data.get("tp_price")) if pos_data.get("tp_price") is not None else None
            pos.sl_ref = pos_data.get("sl_ref")
            pos.tp_ref = pos_data.get("tp_ref")
            self.position = pos
        else:
            self.position = None

    # ──────────────────────────────────────────────────────────────
    # Callbacks del WebSocket
    # ──────────────────────────────────────────────────────────────

    def _upsert_kline(self, kline_data: dict) -> None:
        """Inserta o actualiza una vela en klines_df (llamar con self._lock tomado)."""
        ts = pd.to_datetime(kline_data["timestamp"], unit="ms", utc=True)
        new_row = pd.DataFrame(
            [{k: float(kline_data[k]) for k in ("open", "high", "low", "close", "volume")}],
            index=[ts],
        )
        # Si ya existe esa timestamp (vela duplicada), actualizar en lugar de concatenar
        if ts in self.klines_df.index:
            self.klines_df.loc[ts] = new_row.iloc[0]
        else:
            self.klines_df = pd.concat([self.klines_df, new_row]).sort_index()
        if len(self.klines_df) > self.KLINES_WINDOW:
            self.klines_df = self.klines_df.iloc[-self.KLINES_WINDOW:]

    def _apply_closed_kline(self, kline_data: dict) -> None:
        """Fija los valores FINALES de una vela ya cerrada, sin evaluar la estrategia. Se llama
        antes de procesar la vela en curso para que la señal de 'close' use el cierre real."""
        if not self.is_running:
            return
        ts = int(kline_data["timestamp"])
        if ts == getattr(self, "_last_closed_applied", None):
            return
        with self._lock:
            self._upsert_kline(kline_data)
        self._last_closed_applied = ts

    def _on_new_kline(self, kline_data: dict):
        """Callback invocado con cada actualización de la vela en curso."""
        if not self.is_running:
            return

        with self._lock:
            self._upsert_kline(kline_data)

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

    SIGNAL_MODES = ("intrabar", "close")

    @property
    def signal_mode(self) -> str:
        """'intrabar' (señales sobre la vela en curso, en vivo) o 'close' (solo al cierre de cada vela)."""
        mode = str(self.order_types.get("signal_mode", "close")).lower()
        return mode if mode in self.SIGNAL_MODES else "close"

    def _take_new_closed_candle(self):
        """
        Modo 'close': devuelve el DataFrame de velas CERRADAS si acaba de cerrarse una vela que aún no se
        evaluó (una sola vez por vela); en cualquier otro caso None. La vela en curso (última fila) queda
        fuera. Al arrancar se marca la última vela cerrada como ya vista: una señal que ocurrió antes de
        iniciar el bot no debe disparar una operación.
        """
        if len(self.klines_df) < 3:
            return None
        closed = self.klines_df.iloc[:-1]
        closed_ts = closed.index[-1]
        if self._last_signal_candle is None:
            self._last_signal_candle = closed_ts
            return None
        if closed_ts == self._last_signal_candle:
            return None
        self._last_signal_candle = closed_ts
        return closed

    def _evaluate_market(self):
        if self.klines_df.empty or len(self.klines_df) < 2:
            return

        current_price = float(self.klines_df["close"].iloc[-1])
        current_ts = self.klines_df.index[-1]
        # En modo 'close' las señales de la estrategia se evalúan solo con velas cerradas: un dip o un pico
        # momentáneo dentro de la vela ya no dispara una entrada/salida que la vela luego desmiente (en un
        # 60 % de las señales intravela, el cierre no las confirmaba). El SL/TP sigue vigilándose en vivo.
        close_mode = self.signal_mode == "close"
        signal_frame = self._take_new_closed_candle() if close_mode else self.klines_df

        if self.position:
            # ── Chequeo de SL / TP (siempre en vivo) ────────────
            action = self._check_sl_tp(current_price)
            if action:
                self._close_position(current_price, current_ts, action)
                return

            # ── Condición de salida (señal) ──────────────────────
            if signal_frame is not None:
                try:
                    exit_signal = ConditionEvaluator.evaluate_conditions(
                        signal_frame,
                        self.strategy.config.get("exit_conditions", {}),
                    )
                    if not exit_signal.empty and bool(exit_signal.iloc[-1]):
                        self._close_position(current_price, current_ts, "EXIT_SIGNAL")
                        return
                except Exception as exc:
                    logger.warning("Error evaluando exit_conditions: %s", exc)

            # ── Salida por estado (vela cerrada) ─────────────────
            # La regla de salida de la estrategia es un CRUCE (evento de un instante): si el
            # precio ya estaba del lado contrario al entrar, o el cruce ocurrió mientras la
            # posición no estaba registrada, ese evento nunca llega y la posición queda abierta
            # hasta el SL/TP. Se confirma también el ESTADO al cierre de cada vela.
            try:
                if self._exit_state_confirmed():
                    self._close_position(current_price, current_ts, "EXIT_SIGNAL")
                    return
            except Exception as exc:
                logger.warning("Error evaluando la salida por estado: %s", exc)

        elif signal_frame is not None:
            # ── Condición de entrada ─────────────────────────────
            try:
                entry_signal = ConditionEvaluator.evaluate_conditions(
                    signal_frame,
                    self.strategy.config.get("entry_conditions", {}),
                )
                if not entry_signal.empty and bool(entry_signal.iloc[-1]):
                    # Detectar dirección de la estrategia (Long o Short)
                    side = "short" if is_short_direction(self.strategy.config) else "long"
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

    def record_unexecuted_order(
        self,
        action: str,
        side: str,
        order_type: str,
        price: float,
        quantity: float,
        reason: str,
        details: Optional[dict] = None
    ) -> dict:
        """Registra de forma estructurada y persistente una orden no ejecutada/rechazada."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        active_params = self.custom_parameters or (self.strategy.parameters if self.strategy else {})
        params_snapshot = dict(active_params) if isinstance(active_params, dict) else {}

        record = {
            "order_id": f"unexec_{int(time.time() * 1000)}_{len(self.unexecuted_orders)}",
            "bot_id": self.bot_id,
            "bot_name": self.name,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "strategy_name": self.strategy_name,
            "network": "Testnet" if self.use_testnet else "Real (Mainnet)",
            "timestamp": now_str,
            "action": action.upper(),
            "side": side.upper(),
            "order_type": order_type.upper(),
            "price": float(price or 0.0),
            "quantity": float(quantity or 0.0),
            "parameters": params_snapshot,
            "parameters_summary": " | ".join(f"{k}: {v}" for k, v in params_snapshot.items()) if params_snapshot else "Estándar",
            "reason": str(reason),
            "details": details or {}
        }
        self.unexecuted_orders.append(record)
        self._append_to_global_unexecuted_log(record)
        self._save_state()
        return record

    def _append_to_global_unexecuted_log(self, record: dict):
        """Almacena la orden no ejecutada en un archivo de auditoría histórico global."""
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            audit_path = os.path.join(DATA_DIR, "unexecuted_orders_history.json")
            history = []
            if os.path.exists(audit_path):
                try:
                    with open(audit_path, "r", encoding="utf-8") as f:
                        history = json.load(f)
                    if not isinstance(history, list):
                        history = []
                except Exception:
                    history = []
            history.append(record)
            if len(history) > 1000:
                history = history[-1000:]
            with open(audit_path, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning("Error guardando orden no ejecutada en log global: %s", e)

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

        # Calcular SL y TP usando compute_sl_tp del RiskManager (antes del tamaño: el modo
        # "riesgo fijo" necesita la distancia al stop, igual que en el backtest).
        idx = len(self.klines_df) - 1
        try:
            # strict=True: SL/TP sin configurar o en 0 = sin esa orden, igual que en el backtest
            # (antes el motor en vivo aplicaba en silencio un 2% / 4%).
            sl_price, tp_price = risk_mgr.compute_sl_tp(self.klines_df, idx, side, strict=True)
        except Exception:
            sl_price = price * (0.98 if side == "long" else 1.02)
            tp_price = price * (1.04 if side == "long" else 0.96)

        # Saldo de la cuenta expresado en la divisa cotizada del par (ej. USDT)
        if is_base_currency:
            to_quote = price
        elif is_btc_account:
            # Colateral en BTC operando otro par (ej. ETH/USDT, SOL/USDT) con Multi-Assets
            to_quote = self._client.get_symbol_price("BTCUSDT") if self._client else 0.0
            if to_quote <= 0:
                to_quote = float(self.klines_df["close"].iloc[-1]) if "BTC" in self.symbol else 80000.0
        else:
            to_quote = 1.0
        sizer = RiskManager({**risk_mgr.config, "position_sizing": self._sizing_config(self.initial_balance * to_quote)})
        quantity = sizer.compute_position_size(self.current_balance * to_quote, price, sl_price)

        entry_type = self.order_types.get("entry", "MARKET").upper()

        if quantity <= 0:
            err_msg = f"Saldo insuficiente ({self.current_balance:.4f} {self.currency}) para abrir posición"
            self.record_unexecuted_order(
                action="ENTRY",
                side=side,
                order_type=entry_type,
                price=price,
                quantity=quantity,
                reason=err_msg,
                details={"balance": self.current_balance, "currency": self.currency}
            )
            self._notify(f"⚠️ {err_msg}.")
            return

        sl_type = self.order_types.get("stop_loss", "MARKET").upper()
        tp_type = self.order_types.get("take_profit", "LIMIT").upper()

        own_sl_ref = own_tp_ref = None

        # Envío y comprobación activa de ejecución en Binance Futures si está configurado
        if self.use_testnet:
            if not self._client:
                err_msg = "Modo Binance activo pero sin cliente conectado"
                self.record_unexecuted_order(
                    action="ENTRY",
                    side=side,
                    order_type=entry_type,
                    price=price,
                    quantity=quantity,
                    reason=err_msg,
                    details={"side": side, "symbol": self.symbol}
                )
                self._trigger_critical_order_alert(
                    err_msg,
                    {"error": "Cliente Binance no inicializado", "side": side, "symbol": self.symbol}
                )
                return

            # (Antes se cancelaban TODAS las órdenes abiertas del símbolo antes de abrir: con varios bots
            # eso borraba el SL/TP de los demás. Cada bot solo gestiona las referencias de sus órdenes.)

            # Tamaño mínimo por orden de Binance (cantidad × precio): con un saldo pequeño la orden sale por debajo y
            # Binance la rechaza (-4164). Se detecta antes de enviar, con un mensaje que dice qué corregir.
            min_notional = self._client.get_min_notional(self.symbol)
            if min_notional and quantity * price < min_notional:
                err_msg = (
                    f"Tamaño insuficiente: {quantity:.6f} × {price:.2f} = {quantity * price:.2f} {self.symbol.split('/')[-1]} "
                    f"< mínimo {min_notional:.0f} por orden de Binance. Sube el saldo del bot o su % de capital."
                )
                self.record_unexecuted_order(
                    action="ENTRY", side=side, order_type=entry_type, price=price, quantity=quantity, reason=err_msg,
                    details={"Cantidad": f"{quantity:.6f}", "Precio Señal": f"{price:.2f}", "Mínimo": f"{min_notional:.0f}"},
                )
                self._notify(f"⚠️ {err_msg}")
                return

            ext_order, err = self._client.place_futures_order(
                self.symbol, side, quantity, order_type=entry_type, price=price, verify_execution=True
            )

            # Validar si se ejecutó en el exchange
            if not ext_order or err:
                err_msg = str(err or "Orden rechazada o no completada por Binance")
                self.record_unexecuted_order(
                    action="ENTRY",
                    side=side,
                    order_type=entry_type,
                    price=price,
                    quantity=quantity,
                    reason=err_msg,
                    details={
                        "Tipo Orden": entry_type,
                        "Lado": side.upper(),
                        "Cantidad": f"{quantity:.4f}",
                        "Precio Señal": f"{price:.2f}",
                        "error": err_msg
                    }
                )
                self._trigger_critical_order_alert(
                    f"Orden de Entrada {side.upper()} NO ejecutada en Binance",
                    {
                        "Tipo Orden": entry_type,
                        "Lado": side.upper(),
                        "Cantidad": f"{quantity:.4f}",
                        "Precio Señal": f"{price:.2f}",
                        "error": err_msg
                    }
                )
                # Abortar apertura para evitar desincronización con el exchange
                return

            order_status = str(ext_order.get("status", "")).upper()
            if order_status not in ["FILLED", "PARTIALLY_FILLED", "NEW"]:
                err_msg = f"Estado no operable devuelto por exchange: {order_status}"
                self.record_unexecuted_order(
                    action="ENTRY",
                    side=side,
                    order_type=entry_type,
                    price=price,
                    quantity=quantity,
                    reason=err_msg,
                    details={
                        "ID Orden": ext_order.get("orderId"),
                        "Estado": order_status,
                        "error": err_msg
                    }
                )
                self._trigger_critical_order_alert(
                    f"Orden de Entrada {side.upper()} en estado inválido ({order_status})",
                    {
                        "ID Orden": ext_order.get("orderId"),
                        "Estado": order_status,
                        "error": err_msg
                    }
                )
                return

            # Sincronizar precio real de ejecución (avgPrice) si Binance lo reporta, validando
            # que sea razonable respecto al precio de la señal (protege contra un avgPrice
            # corrupto/glitch que inflaria o desinflaria el PNL de toda la operación).
            real_fill_price = float(ext_order.get('avgPrice', 0.0) or 0.0)
            if real_fill_price > 0:
                price_deviation = abs(real_fill_price - price) / price if price > 0 else 0.0
                if price_deviation <= 0.15:
                    # SL/TP se calcularon sobre el precio de la señal: se reanclan al precio real
                    # de ejecución para que las distancias configuradas se midan desde donde el
                    # bot realmente entró (con deslizamiento, la señal y el fill difieren).
                    sl_price, tp_price = self._anchor_sl_tp(sl_price, tp_price, price, real_fill_price)
                    price = real_fill_price
                else:
                    self._trigger_critical_order_alert(
                        "Discrepancia de precio de ejecución ignorada (posible glitch del exchange)",
                        {
                            "Precio de señal": f"{price:.4f}",
                            "avgPrice reportado por Binance": f"{real_fill_price:.4f}",
                            "Accion": "Se mantiene el precio de señal para no corromper el PNL calculado"
                        }
                    )

            # Sincronizar cantidad ejecutada real, pero SOLO si es coherente con lo solicitado.
            # Un MARKET order nunca puede ejecutar mas cantidad de la pedida: una respuesta con
            # executedQty muy superior (glitch de Testnet, orden residual, o bug de precision)
            # NO debe sobrescribir la cantidad interna, o el PNL/balance quedan corrompidos con
            # una posicion cientos de veces mas grande de la que realmente se pretendia abrir.
            real_qty = float(ext_order.get('executedQty', 0.0) or 0.0)
            if real_qty > 0:
                if real_qty <= quantity * 1.02:
                    quantity = real_qty
                else:
                    self._trigger_critical_order_alert(
                        "Discrepancia de cantidad ejecutada ignorada (posible glitch del exchange)",
                        {
                            "Cantidad solicitada": f"{quantity:.6f}",
                            "executedQty reportado por Binance": f"{real_qty:.6f}",
                            "Accion": "Se mantiene la cantidad solicitada para no corromper el balance interno"
                        }
                    )

            self._notify(
                f"⚡ ORDEN ENTRADA ({entry_type}) EJECUTADA en Binance | "
                f"Precio Fill: {price:.2f} | ID: {ext_order.get('orderId')} | Status: {order_status}"
            )

            # Colocar órdenes condicionales de TP y SL en Binance (Por defecto LIMIT)
            sl_tp_res = self._client.place_futures_sl_tp(
                self.symbol, side, quantity,
                sl_price=sl_price, tp_price=tp_price,
                sl_order_type=sl_type, tp_order_type=tp_type,
                reduce_only=False,  # protege SU cantidad aunque la posición neta sea de otro bot
            )
            own_sl_ref = self._client.order_ref(sl_tp_res.get("sl_order"))
            own_tp_ref = self._client.order_ref(sl_tp_res.get("tp_order"))
            if sl_tp_res.get("tp_order"):
                tp_ref = sl_tp_res['tp_order'].get('orderId') or sl_tp_res['tp_order'].get('algoId')
                self._notify(f"🎯 ORDEN TP ({tp_type}) COLOCADA @ {tp_price:.4f} | ID: {tp_ref}")
            if sl_tp_res.get("sl_order"):
                sl_ref = sl_tp_res['sl_order'].get('orderId') or sl_tp_res['sl_order'].get('algoId')
                self._notify(f"🛡️ ORDEN SL ({sl_type}) COLOCADA @ {sl_price:.4f} | ID: {sl_ref}")
            if sl_tp_res.get("errors"):
                # Esto no es informativo: si la orden condicional de SL o TP no quedo colocada
                # en el exchange, la posicion real queda "desnuda" (sin proteccion del lado del
                # servidor) y solo la vigila el loop interno del bot mientras el proceso siga
                # vivo. Un _notify de "Info" pasaba desapercibido; se escala a alerta critica.
                self._trigger_critical_order_alert(
                    "Fallo al colocar orden(es) condicional(es) SL/TP en Binance — posición sin protección en el exchange",
                    {
                        "Símbolo": self.symbol,
                        "Lado": side.upper(),
                        "SL colocado": bool(sl_tp_res.get("sl_order")),
                        "TP colocado": bool(sl_tp_res.get("tp_order")),
                        "Errores": ", ".join(sl_tp_res["errors"]),
                    }
                )

        # Abrir posición interna una vez confirmada la ejecución
        self.position = Position(side, price, quantity, ts)
        self.position.sl_price = sl_price
        self.position.tp_price = tp_price
        self.position.sl_ref = own_sl_ref
        self.position.tp_ref = own_tp_ref
        self._last_open_ts = time.time()

        self._notify(
            f"🟢 OPEN {side.upper()} [{entry_type}] | Precio: {price:.4f} | "
            f"SL ({sl_type}): {self._fmt_level(sl_price)} | TP ({tp_type}): {self._fmt_level(tp_price)} | "
            f"Qty: {quantity:.6f} {base_asset}"
        )
        self._save_state()

    # ──────────────────────────────────────────────────────────────
    # Posición PROPIA del bot (varios bots pueden operar el mismo símbolo)
    # ──────────────────────────────────────────────────────────────
    # Binance mantiene una única posición NETA por símbolo. Antes cada bot la trataba como suya:
    # adoptaba la de otro bot, la cerraba "por sincronización" y cancelaba en masa el SL/TP de los
    # demás. Ahora cada bot es dueño de SU posición: la deduce de SUS órdenes (entrada, SL y TP,
    # registradas con su bot_id) y solo cancela/repone las referencias de esas órdenes.

    # Bots en ejecución, por bot_id (compartido por todas las instancias del proceso). Permite saber si OTRO
    # bot opera el mismo símbolo en la misma red: en ese caso la posición NETA de Binance mezcla a ambos.
    _ACTIVE_BOTS: "dict[str, PaperTrader]" = {}
    # Todos los bots vivos del proceso (también los detenidos): su posición cuenta en la posición NETA de Binance.
    _ALL_BOTS: "weakref.WeakValueDictionary[str, PaperTrader]" = weakref.WeakValueDictionary()

    UNCERTAIN_CLOSE_TIMEOUT_S = 20.0   # cuánto se espera un estado incierto del SL/TP propio antes de decidir el cierre
    NET_READ_CONFIRM_DELAY_S = 0.6     # pausa entre las dos lecturas de posición que deben coincidir antes de cerrar

    def _others_signed_position(self) -> float:
        """Suma con signo (long +, short -) de las posiciones de los OTROS bots en el mismo símbolo y red."""
        me = self.symbol.replace("/", "").upper()
        total = 0.0
        for other in list(PaperTrader._ALL_BOTS.values()):
            if other is self or other.use_testnet != self.use_testnet or other.symbol.replace("/", "").upper() != me:
                continue
            pos = other.position
            if pos is not None:
                total += pos.quantity if pos.side == "long" else -pos.quantity
        return total

    def _exchange_position_verdict(self, pos: "Position") -> Tuple[str, Optional[float], float]:
        """
        Contrasta la posición de ESTE bot con la posición NETA real de Binance antes de enviar una orden de cierre.
        Devuelve (veredicto, neta, otros):
          OPEN       la neta = la de los otros bots + la de este: la posición existe y se puede cerrar.
          GONE       la neta = solo la de los otros bots: la posición de este bot ya no está en Binance (p. ej. su
                     SL/TP ya la cerró). Enviar otra orden de cierre abriría una posición contraria.
          AMBIGUOUS  ninguna de las dos: no se puede atribuir la posición; se cierra como mucho lo que hay.
          UNREADABLE no se pudo leer, o dos lecturas seguidas discreparon (Testnet devuelve a veces lecturas viejas
                     justo tras operar): no se decide nada y se reintenta.
        """
        tol = 1e-6
        mine = pos.quantity if pos.side == "long" else -pos.quantity
        others = self._others_signed_position()

        def read() -> Tuple[str, Optional[float]]:
            net = self._net_position_amount()
            if net is None:
                return "UNREADABLE", None
            if abs(net - (others + mine)) <= tol:
                return "OPEN", net
            if abs(net - others) <= tol:
                return "GONE", net
            return "AMBIGUOUS", net

        first, net_a = read()
        if first == "UNREADABLE":
            return "UNREADABLE", None, others
        time.sleep(self.NET_READ_CONFIRM_DELAY_S)
        second, net_b = read()
        if second != first or net_a != net_b:
            return "UNREADABLE", None, others
        return first, net_b, others

    def _shares_symbol(self) -> bool:
        """True si otro bot en ejecución opera el mismo símbolo y la misma red (posición neta compartida)."""
        me = self.symbol.replace("/", "").upper()
        return any(
            other is not self and other.is_running and other.use_testnet == self.use_testnet
            and other.symbol.replace("/", "").upper() == me
            for other in list(PaperTrader._ACTIVE_BOTS.values())
        )

    OPEN_SYNC_GRACE_S = 30.0          # tras abrir no se repone protección (las órdenes recién colocadas)
    OWN_SYNC_MIN_AGE_S = 5.0          # ni se resuelve el estado de las patas SL/TP
    ZERO_READS_TO_CONFIRM = 2         # lecturas seguidas de posición neta 0 para dar un cierre externo
    RECOVERY_LOOKBACK_DAYS = 7

    @staticmethod
    def _naive_utc(ts) -> datetime:
        stamp = pd.Timestamp(ts)
        stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
        return stamp.tz_localize(None).to_pydatetime()

    @staticmethod
    def _anchor_sl_tp(sl_price, tp_price, signal_price: float, entry_price: float):
        """
        Traslada SL/TP (calculados sobre `signal_price`) para que se midan desde `entry_price`,
        el precio de entrada real. Es un desplazamiento aditivo: exacto para SL/TP en puntos o
        ATR y con un error despreciable (fracción del % configurado) para SL/TP porcentuales.
        """
        if not signal_price or not entry_price or signal_price == entry_price:
            return sl_price, tp_price
        delta = entry_price - signal_price
        return (
            sl_price + delta if sl_price is not None else None,
            tp_price + delta if tp_price is not None else None,
        )

    def _net_position_amount(self) -> Optional[float]:
        """Cantidad NETA de la cuenta en el símbolo (None si no se pudo consultar)."""
        try:
            info = self._client.client.futures_position_information(symbol=self.symbol.replace("/", "").upper())
            return float(info[0].get("positionAmt", 0.0)) if info else 0.0
        except Exception as e:
            logger.warning("[%s] No se pudo consultar la posición neta: %s", self.name, e)
            return None

    def _refs_from_ledger(self, since: datetime) -> dict:
        """Últimas órdenes SL/TP registradas por ESTE bot en el ledger desde `since` (naive UTC)."""
        from reconciliation.models import AppOrderRecord

        out = {"sl": None, "tp": None}
        db = SessionLocal()
        try:
            rows = (
                db.query(AppOrderRecord)
                .filter(
                    AppOrderRecord.bot_id == self.bot_id,
                    AppOrderRecord.use_testnet == True,  # noqa: E712
                    AppOrderRecord.status == "SENT_OK",
                    AppOrderRecord.action.in_(("STOP_LOSS", "TAKE_PROFIT")),
                    AppOrderRecord.created_at >= since,
                )
                .order_by(AppOrderRecord.created_at.desc())
                .all()
            )
            for r in rows:
                leg = "sl" if r.action == "STOP_LOSS" else "tp"
                if out[leg] is None and r.binance_order_id:
                    oid = int(r.binance_order_id)
                    # Los ids de Algo Orders (condicionales) son enormes (>1e15); los de órdenes estándar no.
                    out[leg] = {"kind": "algo" if oid >= 10 ** 15 else "order", "id": oid}
        finally:
            db.close()
        return out

    def _attach_refs_from_ledger(self, pos: "Position") -> None:
        """Posiciones restauradas de un estado anterior no traen referencias: se recuperan del ledger."""
        try:
            refs = self._refs_from_ledger(self._naive_utc(pos.entry_timestamp) - timedelta(minutes=5))
        except Exception:
            logger.exception("[%s] No se pudieron leer las referencias SL/TP del ledger", self.name)
            return
        if pos.sl_ref is None:
            pos.sl_ref = refs["sl"]
        if pos.tp_ref is None:
            pos.tp_ref = refs["tp"]

    def _cancel_own_protection(self, pos: Optional["Position"] = None) -> None:
        """Cancela SOLO el SL/TP de este bot (nunca el de otros bots del mismo símbolo)."""
        pos = pos or self.position
        if pos is None or not (self.use_testnet and self._client and self._client.api_key):
            return
        if pos.sl_ref is None and pos.tp_ref is None:
            self._attach_refs_from_ledger(pos)
        refs = [r for r in (pos.sl_ref, pos.tp_ref) if r]
        if not refs:
            return
        ok, errors = self._client.cancel_order_refs(self.symbol, refs)
        if not ok:
            logger.warning("[%s] No se pudo cancelar el SL/TP propio: %s", self.name, "; ".join(errors))
        self._verify_no_orphan_protection(refs)

    def _verify_no_orphan_protection(self, refs: list) -> bool:
        """
        Comprueba en Binance que NINGUNA de las órdenes SL/TP propias sigue viva tras cerrar la posición: sin
        `reduceOnly`, Binance no cancela solo la pata sobrante y una huérfana abriría una posición nueva al
        dispararse. Reintenta la cancelación una vez y, si persiste, alerta como crítico.
        """
        mine = {(r["kind"], r["id"]) for r in refs if r}
        for attempt in range(2):
            open_refs = self._client.get_open_order_refs(self.symbol)
            if open_refs is None:
                return False  # no se pudo consultar: no se afirma nada
            leftover = mine & set(open_refs)
            if not leftover:
                return True
            if attempt == 0:
                self._client.cancel_order_refs(self.symbol, [{"kind": k, "id": i} for k, i in leftover])
        self._trigger_critical_order_alert(
            "SL/TP HUÉRFANO en Binance: siguen vivas órdenes de protección de una posición ya cerrada",
            {"Símbolo": self.symbol, "Órdenes": ", ".join(f"{k}:{i}" for k, i in sorted(leftover))},
        )
        return False

    def _resolve_own_exchange_close(self, pos: "Position"):
        """
        ¿Cerró el exchange la posición de ESTE bot ejecutando su SL o su TP?
        Devuelve ("FOUND", precio_medio) | ("NONE", None) si ninguna pata se ejecutó |
        ("UNKNOWN", None) si no se pudo determinar.
        """
        refs = [r for r in (pos.sl_ref, pos.tp_ref) if r]
        if not refs:
            return "UNKNOWN", None
        states = [self._client.get_order_ref_state(self.symbol, r) for r in refs]
        for st in states:
            if st["state"] == "FILLED" and st.get("avg_price"):
                return "FOUND", float(st["avg_price"])
        if all(st["state"] in ("CANCELED", "EXPIRED", "REJECTED") for st in states):
            return "NONE", None
        return "UNKNOWN", None

    def _own_exit_state(self, pos: "Position"):
        """
        Estado de las patas SL/TP de ESTE bot antes de cerrar con una orden propia. Devuelve
        ("FILLED", pata, precio_medio) si una ya se ejecutó, ("TRIGGERED"|"UNKNOWN", None, None) si no se puede
        afirmar que la posición siga abierta, y ("OPEN", None, None) si ninguna se ejecutó (o no hay referencias
        que consultar, caso en el que se procede como siempre).
        """
        if pos.sl_ref is None and pos.tp_ref is None:
            self._attach_refs_from_ledger(pos)
        legs = [(leg, getattr(pos, f"{leg}_ref")) for leg in ("sl", "tp") if getattr(pos, f"{leg}_ref")]
        if not legs:
            return "OPEN", None, None
        unknown = triggered = False
        for leg, ref in legs:
            state = self._client.get_order_ref_state(self.symbol, ref)
            if state["state"] == "FILLED" and state.get("avg_price"):
                return "FILLED", leg, float(state["avg_price"])
            triggered = triggered or state["state"] == "TRIGGERED"
            unknown = unknown or state["state"] == "UNKNOWN"
        if triggered:
            return "TRIGGERED", None, None
        return ("UNKNOWN", None, None) if unknown else ("OPEN", None, None)

    def _own_protection_alive(self, pos: "Position") -> bool:
        """
        True si Binance confirma que al menos una pata SL/TP de ESTE bot sigue viva (abierta y sin ejecutar).
        Es prueba directa de que su posición existe: si hubiera cerrado, el SL/TP propio se habría ejecutado.
        A diferencia de la posición NETA de la cuenta, no depende de lo que hagan los demás bots en el símbolo
        ni de exposición ajena a ellos (que la neta mezcla y hacía parecer 'cerrada' una posición abierta).
        """
        if pos.sl_ref is None and pos.tp_ref is None:
            self._attach_refs_from_ledger(pos)
        legs = [r for r in (pos.sl_ref, pos.tp_ref) if r]
        if not legs:
            return False
        open_refs = self._client.get_open_order_refs(self.symbol)
        if open_refs is None:
            return False
        for ref in legs:
            if (ref["kind"], ref["id"]) in open_refs and self._client.get_order_ref_state(self.symbol, ref).get("state") == "OPEN":
                return True
        return False

    def _sync_with_exchange(self, binance_symbol: str) -> None:
        """Sincronización periódica (~4 s) con Binance: posición neta (informativa) + posición propia."""
        try:
            pos_list = self._client.client.futures_position_information(symbol=binance_symbol)
            net_amt, mark_p = 0.0, 0.0
            if pos_list:
                p_info = pos_list[0]
                net_amt = float(p_info.get("positionAmt", 0.0))
                mark_p = float(p_info.get("markPrice", 0.0))
                entry_p = float(p_info.get("entryPrice", 0.0))
                info = None
                if net_amt != 0:
                    info = {
                        # Posición NETA de la cuenta en este símbolo (suma de todos los bots y de
                        # cualquier orden manual): es informativa, NO la posición de este bot.
                        "scope": "account_net",
                        "symbol": binance_symbol,
                        "amount": net_amt,
                        "abs_amount": abs(net_amt),
                        "entry_price": entry_p,
                        "break_even_price": float(p_info.get("breakEvenPrice", 0.0) or entry_p),
                        "mark_price": mark_p,
                        "unrealized_pnl": float(p_info.get("unRealizedProfit", 0.0)),
                        "initial_margin": float(p_info.get("isolatedMargin", 0.0) or p_info.get("initialMargin", 0.0)),
                        "leverage": int(p_info.get("leverage", 1)),
                        "margin_type": p_info.get("marginType", "cross"),
                    }
                with self._lock:
                    self.binance_position_info = info
            self._sync_own_position(net_amt, mark_p)
        except Exception as e_pos:
            # Límite de peticiones de Binance (-1003/-1015, por IP: en Testnet la IP puede ser compartida
            # por varios bots): insistir solo lo empeora. Backoff EXPONENCIAL con jitter (±20%), no fijo:
            # con varios bots en la misma IP, una espera fija los sincroniza y todos reintentan a la vez,
            # volviendo a chocar contra el límite en el mismo instante; el jitter los desincroniza, y el
            # exponencial da más margen si el límite persiste en vez de seguir golpeando cada 30s.
            if getattr(e_pos, "code", None) in (-1003, -1015):
                self._rate_limit_hits = getattr(self, "_rate_limit_hits", 0) + 1
                backoff_s = min(
                    self.RATE_LIMIT_BACKOFF_MAX_S,
                    self.RATE_LIMIT_BACKOFF_BASE_S * (2 ** (self._rate_limit_hits - 1)),
                )
                backoff_s *= 1.0 + random.uniform(-0.2, 0.2)
                self._sync_backoff_until = time.monotonic() + backoff_s
            # Un fallo aquí deja al bot sin saber el estado real de su posición: se cuenta y se avisa.
            self._sync_failure_count = getattr(self, "_sync_failure_count", 0) + 1
            logger.warning(
                "[%s] Error sync binance position (fallo consecutivo #%d): %s",
                self.name, self._sync_failure_count, e_pos, exc_info=self._sync_failure_count == 1,
            )
            if self._sync_failure_count == 5:
                self._notify(
                    f"⚠️ Desincronización persistente con Binance detectada "
                    f"({self._sync_failure_count} fallos consecutivos). "
                    f"El bot puede no reflejar la posición real del exchange."
                )
        else:
            self._sync_failure_count = 0
            self._rate_limit_hits = 0

    def _sync_own_position(self, net_amt: float, mark_p: float) -> None:
        """Resuelve el estado de la posición de ESTE bot a partir de SUS órdenes SL/TP."""
        pos = self.position
        if pos is None:
            self._zero_pos_reads = 0
            return
        if time.time() - getattr(self, "_last_open_ts", 0.0) < self.OWN_SYNC_MIN_AGE_S:
            return
        if pos.sl_ref is None and pos.tp_ref is None:
            self._attach_refs_from_ledger(pos)
        refs = [(leg, getattr(pos, f"{leg}_ref")) for leg in ("sl", "tp") if getattr(pos, f"{leg}_ref")]

        dead_legs = []
        if refs:
            open_refs = self._client.get_open_order_refs(self.symbol)
            if open_refs is None:
                return  # no se pudo consultar: no se decide nada con datos incompletos
            for leg, ref in refs:
                if (ref["kind"], ref["id"]) in open_refs:
                    continue
                state = self._client.get_order_ref_state(self.symbol, ref)
                if state["state"] == "FILLED":
                    price = float(state.get("avg_price") or 0.0) or (pos.sl_price if leg == "sl" else pos.tp_price) or mark_p
                    with self._lock:
                        if self.position is pos:
                            self._zero_pos_reads = 0
                            self._close_position(
                                price, datetime.now(timezone.utc), reason=leg.upper(), already_closed_on_exchange=True
                            )
                    return
                if state["state"] == "TRIGGERED":
                    return  # disparada pero aún sin ejecutar: se espera al siguiente ciclo
                if state["state"] in ("CANCELED", "EXPIRED", "REJECTED"):
                    dead_legs.append(leg)
                # UNKNOWN: incierto, no se toma ninguna decisión en este ciclo

        # Invariante: si la posición NETA de la cuenta es 0, ningún bot puede tener una posición abierta en
        # el símbolo. Se exigen 2 lecturas seguidas (Testnet a veces devuelve lecturas viejas justo tras
        # operar). Cubre el cierre manual en Binance y las posiciones "fantasma" heredadas de un estado
        # anterior (sin referencias SL/TP), que de otro modo nadie resolvería.
        if abs(net_amt) < 1e-9 and not self._shares_symbol():
            self._zero_pos_reads += 1
            if self._zero_pos_reads >= self.ZERO_READS_TO_CONFIRM:
                exit_p = mark_p if mark_p > 0 else pos.entry_price
                with self._lock:
                    if self.position is pos:
                        self._zero_pos_reads = 0
                        self._close_position(
                            exit_p, datetime.now(timezone.utc), reason="BINANCE_EXCHANGE_CLOSED",
                            already_closed_on_exchange=True,
                        )
            return

        self._zero_pos_reads = 0
        if not refs or (dead_legs and len(dead_legs) == len(refs)):
            self._ensure_exchange_sl_tp()   # posición viva pero sin protección conocida/viva: reponer

    def _ensure_exchange_sl_tp(self) -> None:
        """
        Repone en Binance las patas SL/TP de ESTE bot que no estén vivas. Antes de colocar nada
        comprueba que la posición siga existiendo (una orden reduceOnly sobre una posición que ya
        no existe, o que es de otro bot, sería un error).
        """
        pos = self.position
        if pos is None or not self.use_testnet or not self._client or not self._client.api_key:
            return
        if pos.sl_price is None and pos.tp_price is None:
            return
        if time.time() - getattr(self, "_last_open_ts", 0.0) < self.OPEN_SYNC_GRACE_S:
            return
        if pos.sl_ref is None and pos.tp_ref is None:
            self._attach_refs_from_ledger(pos)

        open_refs = self._client.get_open_order_refs(self.symbol)
        if open_refs is None:
            return
        missing = []
        for leg, price in (("sl", pos.sl_price), ("tp", pos.tp_price)):
            if price is None:
                continue
            ref = getattr(pos, f"{leg}_ref")
            if ref and (ref["kind"], ref["id"]) in open_refs:
                continue
            if ref:
                state = self._client.get_order_ref_state(self.symbol, ref)["state"]
                if state in ("FILLED", "TRIGGERED", "UNKNOWN"):
                    return  # la sincronización resuelve el cierre; o hay incertidumbre: no colocar nada
            missing.append(leg)
        if not missing:
            return

        with self._lock:
            if self.position is not pos:
                return  # se cerró (o cambió) mientras se consultaba el exchange
            self._place_missing_protection(pos, missing)

    def _net_backs_quantity(self, side: str, qty: float) -> Optional[bool]:
        """
        ¿La posición NETA real de Binance respalda, como mínimo, `qty` de este bot en `side`, una vez
        descontada la posición de los OTROS bots? None si no se pudo confirmar contra el exchange (el
        llamador no debe actuar a ciegas en ese caso).

        Se usa por partes iguales desde `_place_missing_protection` (¿reponer SL/TP?) y
        `_recover_position_from_ledger` (¿recrear una posición tras un reinicio?): una única
        implementación evita que las dos vuelvan a desincronizarse entre sí (incidente 2026-09-22, donde
        una comparaba con `_shares_symbol()` —que solo cuenta bots EN EJECUCIÓN— mientras calculaba con
        `_others_signed_position()` —que suma también los detenidos con una posición real en el
        exchange—, dejando a un bot compartiendo símbolo pero detenido fuera de la cuenta y enmascarando
        o inventando exposición).

        Se exige "al menos" `qty`, no una igualdad exacta: puede haber exposición extra no rastreada por
        ningún PaperTrader vivo (una orden manual, un bot ya eliminado) y eso es normal. Sin ningún otro
        bot, `_others_signed_position()` es 0 y esto equivale al chequeo exclusivo original (comparar
        directamente contra la neta).

        Nota: esta es una lectura ÚNICA de la neta, más ligera que `_exchange_position_verdict` (que hace
        doble lectura para descartar una lectura vieja de Testnet justo tras operar); se usa aquí porque
        ambos llamadores ya se ejecutan en el ciclo periódico de sincronización, que vuelve a intentarlo
        en la siguiente vuelta si esta lectura fue una casualidad.
        """
        net = self._net_position_amount()
        if net is None:
            return None  # no se pudo confirmar contra el exchange: no se decide nada a ciegas
        direction = 1.0 if side == "long" else -1.0
        remaining = (net - self._others_signed_position()) * direction
        return remaining >= qty - 1e-9

    def _place_missing_protection(self, pos: "Position", missing: list) -> None:
        """Coloca las patas SL/TP indicadas si la cuenta tiene una posición que las respalde. Con el lock del bot."""
        # Estas órdenes se colocan SIN reduceOnly (ver place_futures_sl_tp): si no hay posición real que
        # respalden quedan huérfanas y vivas en Binance para siempre (incidente 2026-09-22).
        backed = self._net_backs_quantity(pos.side, pos.quantity)
        if not backed:
            return  # None (no se pudo confirmar) o False (no está respaldada): no se coloca nada a ciegas

        sl_type = self.order_types.get("stop_loss", "MARKET").upper()
        tp_type = self.order_types.get("take_profit", "LIMIT").upper()
        self._notify(
            f"🛡️ La posición {pos.side.upper()} {pos.quantity:.6f} no tiene su "
            f"{' y '.join(l.upper() for l in missing)} en Binance. Reponiendo."
        )
        res = self._client.place_futures_sl_tp(
            self.symbol, pos.side, pos.quantity,
            sl_price=pos.sl_price if "sl" in missing else None,
            tp_price=pos.tp_price if "tp" in missing else None,
            sl_order_type=sl_type, tp_order_type=tp_type,
            reduce_only=False,
        )
        if res.get("sl_order"):
            pos.sl_ref = self._client.order_ref(res["sl_order"])
            self._notify(f"🛡️ ORDEN SL ({sl_type}) REPUESTA @ {pos.sl_price:.4f}")
        if res.get("tp_order"):
            pos.tp_ref = self._client.order_ref(res["tp_order"])
            self._notify(f"🎯 ORDEN TP ({tp_type}) REPUESTA @ {pos.tp_price:.4f}")
        if res.get("sl_order") or res.get("tp_order"):
            self._save_state()
        if res.get("errors") or res.get("error"):
            self._trigger_critical_order_alert(
                "No se pudo reponer el SL/TP en Binance — la posición sigue sin protección en el exchange",
                {
                    "Símbolo": self.symbol,
                    "Lado": pos.side.upper(),
                    "Errores": ", ".join(res.get("errors") or [str(res.get("error"))]),
                },
            )

    def _recover_position_from_ledger(self) -> None:
        """
        Recupera, tras un reinicio o una caída, la posición ABIERTA de este bot a partir de SUS
        órdenes en el ledger (sustituye a "adoptar la posición neta de la cuenta", que le robaba
        a un bot la posición de otro). Solo se recupera si hay una entrada sin cierre posterior,
        ninguna pata SL/TP ejecutada y una posición neta que la respalde.
        """
        if self.position is not None or not (self.use_testnet and self._client and self._client.api_key):
            return
        from reconciliation.models import AppOrderRecord

        since = datetime.utcnow() - timedelta(days=self.RECOVERY_LOOKBACK_DAYS)
        db = SessionLocal()
        try:
            base = db.query(AppOrderRecord).filter(
                AppOrderRecord.bot_id == self.bot_id,
                AppOrderRecord.use_testnet == True,  # noqa: E712
                AppOrderRecord.status == "SENT_OK",
                AppOrderRecord.created_at >= since,
            )
            last_open = base.filter(AppOrderRecord.action == "OPEN").order_by(AppOrderRecord.created_at.desc()).first()
            if last_open is None:
                return
            closed_after = base.filter(
                AppOrderRecord.action == "CLOSE", AppOrderRecord.created_at > last_open.created_at
            ).count()
            if closed_after:
                return
            open_time, open_side, open_order_id = last_open.created_at, last_open.side, last_open.binance_order_id
            qty = float(last_open.executed_qty or last_open.requested_qty or 0.0)
        finally:
            db.close()

        refs = self._refs_from_ledger(open_time - timedelta(minutes=1))
        for ref in (refs["sl"], refs["tp"]):
            if ref and self._client.get_order_ref_state(self.symbol, ref)["state"] in ("FILLED", "TRIGGERED"):
                return  # el exchange ya cerró esta posición con su SL/TP

        side = "long" if str(open_side).upper() == "BUY" else "short"
        if qty <= 0:
            return
        # Confirmación contra la posición NETA real de Binance, SIEMPRE (antes se omitía si el símbolo
        # es compartido, asumiendo que la neta "no dice nada de esta posición" — pero si la posición del
        # ledger nunca se cerró con una orden real (p. ej. un cierre resuelto solo internamente, sin
        # enviar nada a Binance porque el exchange ya estaba plano), el ledger sigue leyendo "abierta sin
        # cierre" en cada reinicio y esto la recreaba sin fin, sin comprobar nunca si de verdad existe
        # (incidente 2026-09-22: recreó una posición SHORT ya cerrada y le repuso SL/TP huérfanos otra vez).
        # Ver _net_backs_quantity para el porqué de usar SIEMPRE _others_signed_position() y nunca
        # _shares_symbol() aquí.
        backed = self._net_backs_quantity(side, qty)
        if not backed:
            return  # None (no se pudo confirmar) o False (no está respaldada): no se recupera a ciegas

        try:
            order = self._client.client.futures_get_order(
                symbol=self.symbol.replace("/", "").upper(), orderId=int(open_order_id)
            )
            entry_price = float(order.get("avgPrice") or 0.0)
        except Exception:
            entry_price = 0.0
        if entry_price <= 0:
            entry_price = float(self.klines_df["close"].iloc[-1]) if not self.klines_df.empty else 0.0
        if entry_price <= 0:
            return

        pos = Position(side, entry_price, qty, pd.Timestamp(open_time, tz="UTC"))
        try:
            idx = len(self.klines_df) - 1
            sl_p, tp_p = self.strategy.risk_manager.compute_sl_tp(self.klines_df, idx, side, strict=True)
            sl_p, tp_p = self._anchor_sl_tp(sl_p, tp_p, float(self.klines_df["close"].iloc[idx]), entry_price)
        except Exception:
            logger.exception("[%s] compute_sl_tp falló al recuperar la posición; se usa SL/TP de emergencia", self.name)
            sl_p = entry_price * (0.98 if side == "long" else 1.02)
            tp_p = entry_price * (1.04 if side == "long" else 0.96)
        pos.sl_price, pos.tp_price = sl_p, tp_p
        pos.sl_ref, pos.tp_ref = refs["sl"], refs["tp"]
        with self._lock:
            self.position = pos
        self._notify(
            f"♻️ Posición propia recuperada del ledger tras reinicio: {side.upper()} {qty:.6f} @ {entry_price:.4f}"
        )
        self._save_state()

    # Operadores de "cruce" (evento) -> su equivalente de "estado" para la salida por estado.

    @staticmethod
    def _timeframe_delta(timeframe: str) -> Optional[pd.Timedelta]:
        # Sensible a mayúsculas a propósito: en Binance "1m" es un minuto y "1M" un mes (no soportado).
        match = re.fullmatch(r"(\d+)([mhdw])", str(timeframe).strip())
        if not match:
            return None
        minutes = {"m": 1, "h": 60, "d": 1440, "w": 10080}[match.group(2)]
        return pd.Timedelta(minutes=int(match.group(1)) * minutes)

    def _exit_state_confirmed(self) -> bool:
        """
        True si, al CIERRE de la última vela completada tras la entrada, la condición de salida
        se cumple como ESTADO (p. ej. EMA rápida por debajo de la lenta) aunque el cruce en sí
        no se haya detectado como evento. Solo cuenta velas cerradas después de abrir la
        posición, para no salir por el estado previo a una entrada intra-vela.
        """
        pos = self.position
        if pos is None or len(self.klines_df) < 3:
            return False
        delta = self._timeframe_delta(self.timeframe)
        if delta is None:
            return False

        entry_ts = pd.Timestamp(pos.entry_timestamp)
        entry_ts = entry_ts.tz_localize("UTC") if entry_ts.tzinfo is None else entry_ts.tz_convert("UTC")
        last_closed_open = self.klines_df.index[-2]
        if last_closed_open + delta <= entry_ts:
            return False  # esa vela cerró antes de que se abriera la posición

        # Misma conversión cruce -> estado que aplica el backtest (conditions.apply_state_exit).
        exit_cfg = state_exit_conditions(self.strategy.config.get("exit_conditions", {}))
        if exit_cfg is None:
            return False

        signal = ConditionEvaluator.evaluate_conditions(self.klines_df.iloc[:-1], exit_cfg)
        return (not signal.empty) and bool(signal.iloc[-1])

    def manual_close_position(self) -> Tuple[bool, str]:
        """Cierra al mercado la posición PROPIA del bot (botón "Cerrar posición" de la interfaz)."""
        with self._lock:
            pos = self.position
            if pos is None:
                return False, "El bot no tiene una posición abierta."
            price = self.current_bid if pos.side == "long" else self.current_ask
            if not price or price <= 0:
                price = float(self.klines_df["close"].iloc[-1]) if not self.klines_df.empty else pos.entry_price
            self._close_position(price, datetime.now(timezone.utc), reason="MANUAL_BINANCE_CLOSE")
            closed = self.position is None
        if closed:
            return True, f"Posición {pos.side.upper()} cerrada."
        return False, "No se pudo cerrar la posición: revisa el log del bot (Binance pudo rechazar la orden)."

    def _find_real_exit_fill(self, pos: "Position"):
        """
        Busca en el historial de fills de Binance el cierre real de `pos`: los fills del lado
        contrario posteriores al último fill de entrada de esa posición.

        Devuelve (estado, precio): ("FOUND", precio medio ponderado) si hubo cierre real,
        ("NONE", None) si Binance no registra ningún cierre (la posición nunca existió allí) o
        ("ERROR", None) si no se pudo consultar (el llamador conserva el comportamiento previo).
        """
        try:
            entry_ts = pd.Timestamp(pos.entry_timestamp)
            entry_ts = entry_ts.tz_localize("UTC") if entry_ts.tzinfo is None else entry_ts.tz_convert("UTC")
            # Margen de 2 min: para posiciones propias entry_timestamp es la apertura de la vela,
            # hasta 1 min antes del fill de entrada real.
            start_ms = int(entry_ts.timestamp() * 1000) - 120_000
            fills = self._client.client.futures_account_trades(
                symbol=self.symbol.replace("/", "").upper(), startTime=start_ms, limit=1000
            )
            entry_side, exit_side = ("BUY", "SELL") if pos.side == "long" else ("SELL", "BUY")
            fills = sorted(fills, key=lambda f: f["time"])
            last_entry_idx = max((i for i, f in enumerate(fills) if f["side"] == entry_side), default=-1)
            closing = [f for f in fills[last_entry_idx + 1:] if f["side"] == exit_side]
            qty = sum(float(f["qty"]) for f in closing)
            if qty <= 0:
                return "NONE", None
            return "FOUND", sum(float(f["qty"]) * float(f["price"]) for f in closing) / qty
        except Exception as e:
            logger.warning("[%s] No se pudo consultar el fill de cierre real en Binance: %s", self.name, e)
            return "ERROR", None

    def _close_position(self, price: float, ts, reason: str, already_closed_on_exchange: bool = False):
        """Cierra la posición abierta, calcula PNL y persiste el trade, verificando ejecución en el exchange.

        already_closed_on_exchange=True: el propio exchange ya la cerró (SL/TP ejecutado, cierre externo);
        solo se contabiliza y se limpian las órdenes propias sobrantes, sin enviar otra orden de cierre."""
        pos = self.position
        if not pos:
            return

        base_asset = self.symbol.split("/")[0].upper() if "/" in self.symbol else "BTC"
        is_base_currency = (self.currency.upper() == base_asset)
        exit_type = self.order_types.get("exit", "MARKET").upper()

        # Cierre en Binance Futures si está configurado
        if self.use_testnet:
            if self._client:
                # Si el cierre no se originó directamente en el exchange, enviar orden de salida a Binance.
                # IMPORTANTE: ya NO se cancelan las órdenes SL/TP condicionales ANTES de intentar el
                # cierre (como se hacía antes): si el intento de cierre fallaba por una razón genuina
                # (red, rate limit, etc.) la posición quedaba desprotegida en el exchange sin que el
                # bot lo supiera. Ahora solo se cancelan una vez confirmado que la posición ya no existe.
                if reason != "BINANCE_EXCHANGE_CLOSED" and not already_closed_on_exchange:
                    # La orden de salida ya NO lleva reduceOnly (ver close_futures_position): si el SL/TP de este bot
                    # ya cerró la posición, enviarla abriría una posición contraria. Por eso se consulta antes el
                    # estado de SUS órdenes de protección.
                    exit_state, exit_leg, exit_px = self._own_exit_state(pos)
                    if exit_state == "FILLED":
                        self._notify(
                            f"ℹ️ El {exit_leg.upper()} de este bot ya cerró la posición en Binance a {exit_px:.2f}: "
                            f"no se envía otra orden de salida."
                        )
                        already_closed_on_exchange, price, reason = True, exit_px, exit_leg.upper()
                    elif exit_state in ("TRIGGERED", "UNKNOWN"):
                        # Disparada sin ejecutar aún, o estado no consultable: la posición sigue protegida, así que
                        # se espera. La espera es por TIEMPO (esta función se llama en cada tick de precio, varias
                        # veces por segundo: contar intentos la agotaba en un par de segundos y se cerraba a ciegas,
                        # duplicando la salida de un SL ya ejecutado). Vencido el plazo no se cierra "por defecto":
                        # se contrasta con la posición real de Binance más abajo.
                        now = time.monotonic()
                        since = getattr(self, "_close_uncertain_since", None)
                        if since is None:
                            self._close_uncertain_since = now
                            logger.warning(
                                "[%s] Estado %s del SL/TP propio al cerrar por %s: se espera hasta %.0f s antes de decidir.",
                                self.name, exit_state, reason, self.UNCERTAIN_CLOSE_TIMEOUT_S,
                            )
                            return
                        if now - since < self.UNCERTAIN_CLOSE_TIMEOUT_S:
                            return
                    self._close_uncertain_since = None

                # Última barrera antes de enviar una orden de cierre sin reduceOnly: contrastar con la posición
                # NETA real de Binance que la posición de este bot siga existiendo. Si ya no está (su SL/TP la
                # cerró, o un cierre externo), la orden de cierre abriría una posición contraria (incidente del
                # 21/09 08:50: SL ejecutado + cierre MARKET = short fantasma de 0.0011).
                #
                # Excepción: si el SL/TP propio sigue vivo en Binance, la posición existe con certeza y la neta
                # no tiene voto. Con dos bots espejo (uno sale mientras el otro entra) o con exposición ajena en
                # la cuenta, la neta puede coincidir por casualidad con "solo los otros bots" y el cierre se
                # omitía: el bot lo contabilizaba pero la posición seguía abierta en Binance (Test 2, 21/09).
                close_qty = pos.quantity
                if (reason != "BINANCE_EXCHANGE_CLOSED" and not already_closed_on_exchange
                        and not self._own_protection_alive(pos)):
                    verdict, net_amt, others = self._exchange_position_verdict(pos)
                    sign = 1.0 if pos.side == "long" else -1.0
                    if verdict == "UNREADABLE":
                        logger.warning(
                            "[%s] No se pudo confirmar la posición real en Binance antes de cerrar (%s): "
                            "se reintenta en el siguiente ciclo sin enviar nada.", self.name, reason,
                        )
                        return
                    available = (net_amt - others) * sign if net_amt is not None else 0.0
                    if verdict == "AMBIGUOUS" and available > 1e-6:
                        # La posición neta no cuadra con lo que se esperaba (p. ej. hay algo ajeno a los bots): como
                        # mucho se cierra lo que Binance tiene en el lado de este bot, nunca más (no invertir).
                        close_qty = min(pos.quantity, available)
                        logger.warning(
                            "[%s] Posición neta de Binance (%.6f) no cuadra con la esperada (%.6f): se cierra solo %.6f.",
                            self.name, net_amt, others + sign * pos.quantity, close_qty,
                        )
                        self._trigger_critical_order_alert(
                            "Posición neta de Binance distinta de la esperada al cerrar",
                            {"Neta en Binance": f"{net_amt:.6f}", "Esperada": f"{others + sign * pos.quantity:.6f}",
                             "Se cierra": f"{close_qty:.6f}"},
                        )
                    elif verdict in ("GONE", "AMBIGUOUS"):
                        # Sin posición de este bot en Binance: no se envía nada; se contabiliza el cierre real.
                        fill_status, real_exit_price = self._resolve_own_exchange_close(pos)
                        if fill_status != "FOUND":
                            fill_status, real_exit_price = self._find_real_exit_fill(pos)
                        if fill_status == "NONE":
                            self._cancel_own_protection(pos)
                            self._notify(
                                f"⚠️ Posición {pos.side.upper()} {pos.quantity:.6f} @ {pos.entry_price:.4f} descartada: "
                                f"Binance no la tiene y no hay ningún fill de cierre desde su apertura. "
                                f"No se registra ningún trade (no existió en el exchange)."
                            )
                            self.position = None
                            self._save_state()
                            return
                        if fill_status == "FOUND":
                            price = real_exit_price
                        self._notify(
                            f"ℹ️ La posición de este bot ya no está en Binance (neta {net_amt:.6f}): no se envía "
                            f"otra orden de cierre. Cierre registrado a {price:.2f}."
                        )
                        already_closed_on_exchange, reason = True, "BINANCE_EXCHANGE_CLOSED"

                if reason != "BINANCE_EXCHANGE_CLOSED" and not already_closed_on_exchange:
                    close_order, err = self._client.close_futures_position(
                        self.symbol, pos.side, close_qty, order_type=exit_type, price=price, verify_execution=True,
                        reduce_only=False,
                    )

                    # Un rechazo "ReduceOnly Order is rejected" (-2022) significa que Binance ya NO
                    # tiene posición que reducir: casi siempre porque el SL/TP ya la cerró en el
                    # exchange momentos antes de que este intento de EXIT_SIGNAL llegara (carrera
                    # entre la sincronización de posición del polling loop y la evaluación de la
                    # estrategia). En ese caso Binance esta diciendo la verdad: no hay nada que cerrar,
                    # así que es correcto proceder a cerrar internamente (con el mejor precio
                    # disponible) para no dejar al bot "creyendo" para siempre que tiene una posición
                    # que ya no existe. Cualquier OTRO error (red, margen, etc.) SI debe abortar sin
                    # tocar el estado interno, porque no hay confirmación de que la posición se cerró.
                    reduce_only_rejected = bool(err) and ("-2022" in str(err) or "ReduceOnly" in str(err))

                    if close_order and not err:
                        # Sincronizar precio real de salida (avgPrice) si Binance lo reporta,
                        # validando que sea razonable respecto al precio de la señal de salida
                        # (mismo sanity check que en la apertura, protege el PNL final del trade).
                        real_fill_price = float(close_order.get('avgPrice', 0.0) or 0.0)
                        if real_fill_price > 0:
                            price_deviation = abs(real_fill_price - price) / price if price > 0 else 0.0
                            if price_deviation <= 0.15:
                                price = real_fill_price
                            else:
                                self._trigger_critical_order_alert(
                                    "Discrepancia de precio de cierre ignorada (posible glitch del exchange)",
                                    {
                                        "Precio de señal de salida": f"{price:.4f}",
                                        "avgPrice reportado por Binance": f"{real_fill_price:.4f}",
                                        "Accion": "Se mantiene el precio de señal para no corromper el PNL final del trade"
                                    }
                                )
                        self._notify(
                            f"⚡ CIERRE ({exit_type}) EJECUTADO en Binance | "
                            f"Precio Fill: {price:.2f} | ID: {close_order.get('orderId')} | Status: {close_order.get('status')}"
                        )
                        closed_qty = float(close_order.get('executedQty', 0.0) or 0.0)
                        if closed_qty > 0 and abs(closed_qty - pos.quantity) > pos.quantity * 0.01:
                            self._trigger_critical_order_alert(
                                "El cierre ejecutó una cantidad distinta a la de la posición del bot",
                                {"Cantidad de la posición": f"{pos.quantity:.6f}", "Cantidad ejecutada": f"{closed_qty:.6f}"},
                            )
                        self._cancel_own_protection(pos)
                    elif reduce_only_rejected:
                        # Antes se cerraba internamente al precio de la señal SIN verificar que la
                        # posición hubiera existido: una posición adoptada que ya no estaba en
                        # Binance (ej. la de un test ejecutado en el mismo símbolo) se registraba
                        # como un trade con PNL inventado. Ahora se busca el cierre real en el
                        # historial de fills de Binance.
                        # Primero por las órdenes PROPIAS (SL/TP de este bot); si no concluyen, por el
                        # historial de fills de la cuenta.
                        fill_status, real_exit_price = self._resolve_own_exchange_close(pos)
                        if fill_status != "FOUND":
                            fill_status, real_exit_price = self._find_real_exit_fill(pos)
                        self._cancel_own_protection(pos)
                        if fill_status == "NONE":
                            self._notify(
                                f"⚠️ Posición {pos.side.upper()} {pos.quantity:.6f} @ {pos.entry_price:.4f} descartada: "
                                f"Binance no la tiene y no hay ningún fill de cierre desde su apertura. "
                                f"No se registra ningún trade (no existió en el exchange)."
                            )
                            self.position = None
                            self._save_state()
                            return
                        if fill_status == "FOUND":
                            price = real_exit_price
                            self._notify(
                                f"ℹ️ Binance ya había cerrado la posición (ReduceOnly rechazado). "
                                f"Se registra el cierre al precio real del fill: {price:.2f}."
                            )
                        else:
                            self._notify(
                                "ℹ️ Binance reporta que la posición ya no existe (ReduceOnly rechazado) y no se pudo "
                                "consultar el fill de cierre. Sincronizando estado interno como cerrada al precio de la señal."
                            )
                    else:
                        err_msg = str(err or "Orden de cierre rechazada o no confirmada por Binance")
                        self.record_unexecuted_order(
                            action="EXIT",
                            side="SELL" if pos.side == "long" else "BUY",
                            order_type=exit_type,
                            price=price,
                            quantity=pos.quantity,
                            reason=err_msg,
                            details={
                                "Lado Cierre": "SELL" if pos.side == "long" else "BUY",
                                "Cantidad": f"{pos.quantity:.4f}",
                                "Razón": reason,
                                "error": err_msg
                            }
                        )
                        self._trigger_critical_order_alert(
                            f"Fallo al ejecutar orden de CIERRE ({exit_type}) en Binance",
                            {
                                "Lado Cierre": "SELL" if pos.side == "long" else "BUY",
                                "Cantidad": f"{pos.quantity:.4f}",
                                "Razón": reason,
                                "error": err_msg
                            }
                        )
                        # Aborta SIN tocar self.position/balance/trade_history: la posicion sigue
                        # protegida por su SL/TP (nunca se cancelaron), y se reintentara en el
                        # siguiente ciclo de evaluacion de la estrategia.
                        return
                else:
                    # Cerrada ya en el exchange (SL/TP ejecutado, liquidación, cierre manual): solo
                    # queda limpiar las órdenes condicionales PROPIAS que hayan quedado vivas.
                    self._cancel_own_protection(pos)
            else:
                err_msg = "Modo Binance activo pero sin cliente conectado al cerrar"
                self.record_unexecuted_order(
                    action="EXIT",
                    side="SELL" if pos.side == "long" else "BUY",
                    order_type=exit_type,
                    price=price,
                    quantity=pos.quantity,
                    reason=err_msg,
                    details={"error": "Cliente Binance no inicializado", "symbol": self.symbol}
                )
                self._trigger_critical_order_alert(
                    err_msg,
                    {"error": "Cliente Binance no inicializado", "symbol": self.symbol}
                )
                # Sin cliente no hay forma de confirmar el cierre real: abortar sin fabricar el trade.
                return

        # PNL en cotizada (ej. USDT)
        if pos.side == "long":
            raw_pnl_quote = (price - pos.entry_price) * pos.quantity
            pnl_pct = ((price - pos.entry_price) / pos.entry_price) * 100.0
        else:
            raw_pnl_quote = (pos.entry_price - price) * pos.quantity
            pnl_pct = ((pos.entry_price - price) / pos.entry_price) * 100.0

        # Comisión estimada. Binance Futures USDⓈ-M (VIP 0, incluida Testnet) cobra 0.05% taker /
        # 0.02% maker por lado. El valor anterior (0.1% + 0.1% = 0.2% round-trip) DUPLICABA la
        # comisión real de un round-trip taker-taker (0.05%+0.05%=0.1%), sesgando sistemáticamente
        # el PNL mostrado a peor de lo que realmente seria en la cuenta. Se usa 0.05% por lado
        # (worst-case taker, ya que entry/exit por defecto son MARKET) como estimación conservadora
        # pero realista.
        fee_quote = pos.quantity * price * 0.001
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

        # ── Guardarraíl 3: Circuit Breaker de Pérdida Diaria (solo cuentas reales) ──
        if not self.use_testnet:
            today = datetime.now().date()

            def _exit_date(raw_exit_time):
                # exit_time llega como datetime en trades recien cerrados, pero como str
                # tras restaurar el estado desde disco (to_dict lo serializa con str()).
                # Sin este fallback, todos los trades pre-reinicio quedaban fuera del
                # calculo y el circuit breaker de perdida diaria podia no dispararse.
                if isinstance(raw_exit_time, datetime):
                    return raw_exit_time.date()
                if isinstance(raw_exit_time, str):
                    try:
                        return datetime.fromisoformat(raw_exit_time).date()
                    except ValueError:
                        return None
                return None

            daily_pnl = sum(
                t.get("pnl", 0.0) for t in self.trade_history
                if _exit_date(t.get("exit_time")) == today
            )
            daily_pnl_pct = (daily_pnl / self.initial_balance * 100.0) if self.initial_balance > 0 else 0.0
            from execution_engine.security_manager import check_circuit_breaker
            breaker_tripped, cb_msg = check_circuit_breaker(daily_pnl_pct, bot_name=self.name)
            if breaker_tripped:
                self.stop()
                if cb_msg:
                    self._notify(cb_msg)
                    self._trigger_critical_order_alert(
                        "Circuit Breaker de Pérdida Diaria Activado",
                        {
                            "Bot": self.name,
                            "PnL Diario": f"{daily_pnl_pct:.2f}%",
                            "Acción": "Bot detenido y candado de trading real cerrado"
                        }
                    )

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
        return compute_detailed_stats(self.trade_history, self.initial_balance)

    # ──────────────────────────────────────────────────────────────
    # Persistencia
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _to_naive_utc(ts):
        """Normaliza entry_time/exit_time a un datetime naive UTC antes de guardarlos.

        PaperTrade.entry_time/exit_time son columnas `DateTime` de SQLAlchemy sobre
        SQLite, que no acepta datetimes con tzinfo y lanza
        `TypeError: SQLite DateTime type only accepts Python datetime and date objects
        as input` al hacer commit. Dos orígenes distintos llegan aquí:
        - `pd.to_datetime(..., utc=True)` en caliente (tz-aware) durante una sesión viva.
        - Un string tz-aware (ej. "2026-09-17 20:04:00+00:00") cuando la posición se
          restauró desde el estado persistido en disco, ya que `_save_state_to_disk`
          serializa `entry_timestamp` con `str(...)` y `restore_from_dict` lo recarga
          tal cual, sin volver a parsearlo a datetime.
        Se usa pd.to_datetime para cubrir ambos casos con una sola conversión.
        """
        if ts is None:
            return None
        parsed = pd.to_datetime(ts, utc=True)
        return parsed.to_pydatetime().astimezone(timezone.utc).replace(tzinfo=None)

    def _save_trade_to_db(self, trade: dict):
        db = SessionLocal()
        try:
            db_trade = PaperTrade(
                session_id=self.session_id,
                symbol=self.symbol,
                strategy_name=self.strategy.config.get("strategy_name", "Unknown"),
                side=trade["side"],
                entry_time=self._to_naive_utc(trade["entry_time"]),
                exit_time=self._to_naive_utc(trade["exit_time"]),
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
                    logger.warning("[%s] No se pudo enviar el mensaje a Telegram", self.name, exc_info=True)

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
