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
    WebSocket de Binance (testnet por defecto) y evalúa las condiciones
    de entrada/salida de la estrategia en cada vela cerrada.
    """

    def __init__(
        self,
        strategy_yaml_path: str,
        initial_balance: float = 10_000.0,
        update_callback: Optional[Callable] = None,
        custom_parameters: Optional[dict] = None,
    ):
        self.strategy = BaseStrategy(strategy_yaml_path, custom_parameters=custom_parameters)
        self.update_callback = update_callback

        # Símbolo y timeframe tomados del YAML o con fallback sensato
        self.symbol = self.strategy.config.get("symbol", "BTC/USDT")
        self.timeframe = self.strategy.config.get("timeframe", "1h")

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
        self._client: Optional[BinanceTestnetClient] = None
        self._lock = threading.Lock()

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
        """Descarga histórico de velas y conecta el WebSocket."""
        self.is_running = True
        self._notify(
            f"🚀 Iniciando Paper Trading | {self.symbol} {self.timeframe} | "
            f"Balance: ${self.current_balance:,.2f}"
        )

        try:
            self._client = BinanceTestnetClient()
        except Exception as e:
            self._notify(f"❌ Error conectando a Binance Testnet: {e}")
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
            self._notify(f"❌ Error descargando histórico: {e}")
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

            self.klines_df = pd.DataFrame(records)
            if not self.klines_df.empty:
                self.klines_df.set_index("timestamp", inplace=True)

            self._notify(
                f"✅ Histórico descargado ({len(self.klines_df)} velas). "
                "Escuchando mercado en vivo..."
            )
        except Exception as e:
            logger.error("Exception processing klines: %s", e, exc_info=True)
            self._notify(f"❌ Error procesando histórico: {e}")
            self.is_running = False
            return

        # Iniciar polling loop
        self._polling_thread = threading.Thread(target=self._polling_loop, daemon=True)
        self._polling_thread.start()

    def _polling_loop(self):
        """Hilo de fondo que consulta el mercado cada 2 segundos."""
        binance_symbol = self.symbol.replace("/", "").upper()
        while self.is_running:
            try:
                # Limit=2 para obtener la vela actual (en curso)
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
            except Exception as e:
                logger.error(f"Error polling klines: {e}")
            
            time.sleep(2)

    def stop(self):
        """Detiene el bot y cierra las conexiones."""
        self.is_running = False
        if self._client:
            try:
                self._client.stop()
            except Exception:
                pass
        self._notify(
            f"🛑 Paper Trading detenido. Balance final: ${self.current_balance:,.2f}"
        )

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
                    self._open_position("long", current_price, current_ts)
            except Exception as exc:
                logger.warning("Error evaluando entry_conditions: %s", exc)

    def _check_sl_tp(self, price: float) -> Optional[str]:
        """Devuelve 'SL', 'TP' o None según el precio actual."""
        if not self.position:
            return None

        sl = self.position.sl_price
        tp = self.position.tp_price

        if sl is not None and price <= sl:
            return "SL"
        if tp is not None and price >= tp:
            return "TP"
        return None

    # ──────────────────────────────────────────────────────────────
    # Gestión de posiciones
    # ──────────────────────────────────────────────────────────────

    def _open_position(self, side: str, price: float, ts):
        """Abre una nueva posición y calcula SL/TP."""
        risk_mgr = self.strategy.risk_manager

        # Tamaño de posición (cantidad de activo base)
        quantity = risk_mgr.compute_position_size(self.current_balance, price)
        self.position = Position(side, price, quantity, ts)

        # Calcular SL y TP usando compute_sl_tp del RiskManager
        # Necesitamos el índice de la última vela
        idx = len(self.klines_df) - 1
        try:
            sl_price, tp_price = risk_mgr.compute_sl_tp(self.klines_df, idx, side)
        except Exception:
            # Fallback: 2% SL, 4% TP
            sl_price = price * (0.98 if side == "long" else 1.02)
            tp_price = price * (1.04 if side == "long" else 0.96)

        self.position.sl_price = sl_price
        self.position.tp_price = tp_price

        self._notify(
            f"🟢 OPEN {side.upper()} | Precio: {price:.4f} | "
            f"SL: {sl_price:.4f} | TP: {tp_price:.4f} | "
            f"Qty: {quantity:.6f}"
        )

    def _close_position(self, price: float, ts, reason: str):
        """Cierra la posición abierta, calcula PNL y persiste el trade."""
        pos = self.position

        # PNL bruto
        if pos.side == "long":
            pnl = (price - pos.entry_price) * pos.quantity
        else:
            pnl = (pos.entry_price - price) * pos.quantity

        # Comisión estimada (0.1% entrada + 0.1% salida)
        fee = pos.quantity * price * 0.002
        net_pnl = pnl - fee
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
            self.stats["wins"] / self.stats["total_trades"] * 100
        )

        # Persistir en BD
        self._save_trade_to_db(trade)

        emoji = "✅" if net_pnl > 0 else "❌"
        self._notify(
            f"🔴 CLOSE {pos.side.upper()} | Precio: {price:.4f} | "
            f"Razón: {reason} | PNL: {net_pnl:+.2f} {emoji} | "
            f"Balance: ${self.current_balance:,.2f}"
        )
        self.position = None

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

    def _notify(self, message: str):
        logger.info("[PaperTrader] %s", message)

        # Telegram (no-op si no está configurado)
        if self.telegram:
            try:
                self.telegram.send_message(f"<b>[PaperTrader]</b>\n{message}")
            except Exception:
                pass

        # Callback a la UI — debe ser thread-safe
        if self.update_callback:
            state = {
                "message": message,
                "balance": self.current_balance,
                "position": self.position,
                "trades": self.trade_history[:],   # copia para seguridad
                "stats": dict(self.stats),
                "klines": self.klines_df.copy() if not self.klines_df.empty else pd.DataFrame()
            }
            try:
                self.update_callback(state)
            except Exception as exc:
                logger.warning("Error en update_callback: %s", exc)
