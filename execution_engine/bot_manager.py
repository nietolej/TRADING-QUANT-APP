import os
import json
import atexit
import time
import logging
import threading
from typing import Dict, List, Optional, Any
from .paper_trader import PaperTrader

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
# TQA_BOTS_STATE_FILE permite ejecutar un daemon aislado (pruebas) sin tocar el estado real de los bots.
BOTS_STATE_FILE = os.getenv("TQA_BOTS_STATE_FILE") or os.path.join(DATA_DIR, "bots_state.json")


class BotManager:
    """
    Gestor centralizado de múltiples instancias de bots de trading en vivo (Paper Trading).
    Permite crear, arrancar, detener, persistir en disco y monitorear múltiples bots concurrentemente.
    """

    # Agrupa ráfagas de cambios de estado en una sola escritura a disco.
    SAVE_DEBOUNCE_S = 0.5

    def __init__(self, persistence_file: str = BOTS_STATE_FILE, auto_start_running_bots: Optional[bool] = None):
        self._bots: Dict[str, PaperTrader] = {}
        # Protege SOLO el diccionario de bots y se mantiene por instantes: nunca se toma otro lock
        # (el de un bot, el de disco) mientras se sostiene, para que no pueda haber interbloqueos.
        self._lock = threading.RLock()
        self._io_lock = threading.Lock()  # serializa las escrituras del archivo de estado
        self._dirty = threading.Event()
        self._stop_writer = threading.Event()
        self._writer_thread: Optional[threading.Thread] = None
        self._unexec_cache: tuple = (None, [])  # (mtime del archivo global, contenido)
        self.persistence_file = persistence_file
        os.makedirs(os.path.dirname(self.persistence_file), exist_ok=True)

        # Solo el proceso del daemon reanuda bots al arrancar: si otro proceso (servidor web,
        # scripts de prueba) importara este módulo y los reanudara, habría dos procesos operando
        # los mismos bots (órdenes duplicadas) y cada guardado de código reiniciaría los bots.
        if auto_start_running_bots is None:
            auto_start_running_bots = os.getenv("TQA_PROCESS_ROLE", "") == "daemon"
            if not auto_start_running_bots:
                logger.warning(
                    "BotManager creado fuera del daemon (TQA_PROCESS_ROLE!=daemon): los bots marcados como "
                    "corriendo NO se reanudan en este proceso. Los bots viven en el Trading Daemon (puerto 8001)."
                )
        self._start_writer()
        atexit.register(self._flush_on_exit)
        # Cargar bots guardados previamente en disco
        self.load_state_from_disk(auto_start_running_bots=auto_start_running_bots)

    # ── Persistencia ────────────────────────────────────────────────────────

    def _start_writer(self):
        self._writer_thread = threading.Thread(target=self._writer_loop, name="bot-state-writer", daemon=True)
        self._writer_thread.start()

    def _writer_loop(self):
        while not self._stop_writer.is_set():
            self._dirty.wait()
            if self._stop_writer.is_set():
                break
            time.sleep(self.SAVE_DEBOUNCE_S)
            self._dirty.clear()
            try:
                self._persist()
            except Exception:
                logger.exception("Error en el hilo de persistencia del estado de bots")

    def _flush_on_exit(self):
        self._stop_writer.set()
        self._dirty.set()
        try:
            self._persist()
        except Exception:
            logger.exception("No se pudo guardar el estado de bots al salir")

    def _on_bot_state_changed(self, bot: PaperTrader):
        """Callback de cada PaperTrader al cambiar su estado. Se invoca con el lock del propio
        bot tomado, así que NO puede tocar ningún otro lock: solo marca el estado como sucio y
        el hilo de persistencia guarda fuera de cualquier lock de bot."""
        self._dirty.set()

    def save_state_to_disk(self):
        """Guarda ya (síncrono) el estado de todos los bots. Solo para llamadores que no
        sostienen el lock de un bot (endpoints del daemon, acciones de gestión)."""
        self._persist()

    def _persist(self):
        """Serializa todos los bots y reemplaza el archivo de estado de forma atómica."""
        with self._lock:
            bots = list(self._bots.items())  # copia instantánea; el lock se suelta ya
        try:
            # Cada to_dict() toma únicamente el lock de su bot, sin ningún otro lock en la mano.
            bots_payload = {bot_id: bot.to_dict() for bot_id, bot in bots}
            with self._io_lock:
                temp_file = f"{self.persistence_file}.tmp"
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(bots_payload, f, indent=2, ensure_ascii=False)
                os.replace(temp_file, self.persistence_file)  # reemplazo atómico
            logger.debug("Estado de %d bots guardado en %s", len(bots_payload), self.persistence_file)
        except Exception:
            logger.exception("Error al guardar estado de los bots en disco")

    def load_state_from_disk(self, auto_start_running_bots: bool = True):
        """Carga y restaura las instancias de bots desde el archivo JSON persistente."""
        to_resume: List[PaperTrader] = []
        with self._lock:
            if not os.path.exists(self.persistence_file):
                return

            try:
                with open(self.persistence_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if not isinstance(data, dict):
                    return

                for bot_id, bot_dict in data.items():
                    if bot_id in self._bots:
                        continue  # Ya existe en memoria

                    yaml_path = bot_dict.get("strategy_yaml_path", "")
                    if not os.path.exists(yaml_path):
                        # Intentar ruta relativa a config/strategies/
                        fname = os.path.basename(yaml_path)
                        fallback = os.path.join(os.path.dirname(DATA_DIR), "config", "strategies", fname)
                        if os.path.exists(fallback):
                            yaml_path = fallback
                        else:
                            continue

                    bot = PaperTrader(
                        strategy_yaml_path=yaml_path,
                        initial_balance=float(bot_dict.get("initial_balance", 1.0)),
                        currency=bot_dict.get("currency", "BTC"),
                        custom_parameters=bot_dict.get("custom_parameters", {}),
                        use_testnet=bool(bot_dict.get("use_testnet", False)),
                        custom_timeframe=bot_dict.get("timeframe"),
                        custom_symbol=bot_dict.get("symbol"),
                        bot_id=bot_id,
                        name=bot_dict.get("name"),
                        save_callback=self._on_bot_state_changed,
                    )
                    bot.restore_from_dict(bot_dict)
                    self._bots[bot_id] = bot
                    logger.info("Bot restaurado desde disco: %s (ID: %s, Estado: %s)", bot.name, bot_id, bot.status)

                    # Si estaba operando al momento del guardado, se reanuda (fuera del lock, abajo)
                    if auto_start_running_bots and bot_dict.get("is_running") is True:
                        to_resume.append(bot)

            except Exception:
                logger.exception("Error al cargar estado de bots desde disco")

        # bot.start() descarga histórico por red: no debe hacerse con el lock del manager tomado.
        for bot in to_resume:
            logger.info("Reanudando ejecución automática del bot %s tras reinicio/recarga...", bot.name)
            try:
                bot.start(reset_started_at=False)
            except Exception:
                logger.exception("No se pudo reanudar el bot %s", bot.name)

    def create_bot(
        self,
        strategy_yaml_path: str,
        name: Optional[str] = None,
        symbol: Optional[str] = "BTC/USDT",
        timeframe: Optional[str] = None,
        initial_balance: float = 1.0,
        currency: str = "BTC",
        use_testnet: bool = False,
        custom_parameters: Optional[dict] = None,
        update_callback: Optional[Any] = None,
        bot_id: Optional[str] = None,
    ) -> PaperTrader:
        """Crea y registra una nueva instancia de bot."""
        with self._lock:
            bot = PaperTrader(
                strategy_yaml_path=strategy_yaml_path,
                initial_balance=initial_balance,
                currency=currency,
                update_callback=update_callback,
                custom_parameters=custom_parameters,
                use_testnet=use_testnet,
                custom_timeframe=timeframe,
                custom_symbol=symbol,
                bot_id=bot_id,
                name=name,
                save_callback=self._on_bot_state_changed,
            )
            self._bots[bot.bot_id] = bot
        self.save_state_to_disk()
        logger.info("Bot creado y registrado: %s (ID: %s)", bot.name, bot.bot_id)
        return bot

    def get_bot(self, bot_id: str) -> Optional[PaperTrader]:
        """Obtiene un bot por su ID."""
        with self._lock:
            return self._bots.get(bot_id)

    def get_all_bots(self) -> List[PaperTrader]:
        """Devuelve la lista de todos los bots registrados."""
        with self._lock:
            return list(self._bots.values())

    def start_bot(self, bot_id: str):
        """Inicia un bot específico."""
        bot = self.get_bot(bot_id)
        if bot and not bot.is_running:
            bot.start()
            self.save_state_to_disk()

    def stop_bot(self, bot_id: str):
        """Detiene un bot específico."""
        bot = self.get_bot(bot_id)
        if bot and bot.is_running:
            bot.stop()
            self.save_state_to_disk()

    def delete_bot(self, bot_id: str) -> bool:
        """Detiene y elimina un bot del gestor."""
        with self._lock:
            bot = self._bots.pop(bot_id, None)
        if not bot:
            return False
        if bot.is_running:
            bot.stop()
        self.save_state_to_disk()
        logger.info("Bot eliminado: %s (ID: %s)", bot.name, bot_id)
        return True

    def start_all(self):
        """Inicia todos los bots registrados que estén detenidos."""
        for bot in self.get_all_bots():
            if not bot.is_running:
                bot.start()
        self.save_state_to_disk()

    def stop_all(self):
        """Detiene todos los bots que estén corriendo."""
        for bot in self.get_all_bots():
            if bot.is_running:
                bot.stop()
        self.save_state_to_disk()

    def get_portfolio_summary(self) -> dict:
        """Calcula el resumen agregado de todos los bots activos agrupando por moneda."""
        with self._lock:
            bots = list(self._bots.values())
            total_bots = len(bots)
            running_bots = sum(1 for b in bots if b.is_running)
            stopped_bots = sum(1 for b in bots if not b.is_running and b.status != "ERROR")
            error_bots = sum(1 for b in bots if b.status == "ERROR")

            balances_by_currency: dict[str, float] = {}
            initial_balances_by_currency: dict[str, float] = {}
            pnl_by_currency: dict[str, float] = {}

            total_trades = sum(b.stats.get("total_trades", 0) for b in bots)
            total_wins = sum(b.stats.get("wins", 0) for b in bots)
            active_positions = sum(1 for b in bots if b.position is not None)

            for b in bots:
                curr = (b.currency or "USDT").upper()
                balances_by_currency[curr] = balances_by_currency.get(curr, 0.0) + b.current_balance
                initial_balances_by_currency[curr] = initial_balances_by_currency.get(curr, 0.0) + b.initial_balance
                pnl_by_currency[curr] = pnl_by_currency.get(curr, 0.0) + b.stats.get("total_pnl", 0.0)

            # Generar string formateado de balance y PnL por divisa
            if not balances_by_currency:
                balance_display = "0.00 USDT"
                pnl_display = "+0.00 USDT (+0.00%)"
                total_current_balance = 0.0
                total_initial_balance = 0.0
                total_pnl = 0.0
                pnl_pct = 0.0
            else:
                bal_parts = []
                pnl_parts = []
                for curr, bal in balances_by_currency.items():
                    dec = 4 if curr in ["BTC", "ETH", "SOL"] else 2
                    bal_parts.append(f"{bal:,.{dec}f} {curr}")
                    
                    pnl_val = pnl_by_currency.get(curr, 0.0)
                    init_bal = initial_balances_by_currency.get(curr, 0.0)
                    pnl_curr_pct = (pnl_val / init_bal * 100.0) if init_bal > 0 else 0.0
                    sign = "+" if pnl_val >= 0 else ""
                    pnl_parts.append(f"{sign}{pnl_val:.{dec}f} {curr} ({sign}{pnl_curr_pct:.2f}%)")

                balance_display = " | ".join(bal_parts)
                pnl_display = " | ".join(pnl_parts)
                total_current_balance = sum(balances_by_currency.values())
                total_initial_balance = sum(initial_balances_by_currency.values())
                total_pnl = sum(pnl_by_currency.values())
                pnl_pct = (total_pnl / total_initial_balance * 100.0) if total_initial_balance > 0 else 0.0

            win_rate = (total_wins / total_trades * 100.0) if total_trades > 0 else 0.0

            return {
                "total_bots": total_bots,
                "running_bots": running_bots,
                "stopped_bots": stopped_bots,
                "error_bots": error_bots,
                "balances_by_currency": balances_by_currency,
                "pnl_by_currency": pnl_by_currency,
                "balance_display": balance_display,
                "pnl_display": pnl_display,
                "total_initial_balance": total_initial_balance,
                "total_current_balance": total_current_balance,
                "total_pnl": total_pnl,
                "total_pnl_pct": pnl_pct,
                "total_trades": total_trades,
                "active_positions": active_positions,
                "win_rate": win_rate,
            }

    def _read_global_unexecuted(self) -> List[dict]:
        """Histórico global de órdenes no ejecutadas, con caché por fecha de modificación: el
        monitor en vivo lo pide cada 1.5 s y el archivo pesa cientos de KB."""
        audit_path = os.path.join(DATA_DIR, "unexecuted_orders_history.json")
        try:
            mtime = os.path.getmtime(audit_path)
        except OSError:
            return []
        cached_mtime, cached = self._unexec_cache
        if cached_mtime == mtime:
            return cached
        try:
            with open(audit_path, "r", encoding="utf-8") as f:
                hist = json.load(f)
        except Exception:
            logger.warning("No se pudo leer %s (¿archivo corrupto?)", audit_path, exc_info=True)
            return cached if cached_mtime is not None else []
        hist = hist if isinstance(hist, list) else []
        self._unexec_cache = (mtime, hist)
        return hist

    def get_unexecuted_orders(self, bot_id: Optional[str] = None) -> List[dict]:
        """
        Retorna el historial de órdenes no ejecutadas / rechazadas.
        Si se especifica bot_id, retorna las del bot seleccionado.
        Si es None o 'all', retorna las de toda la cartera agregadas y ordenadas cronológicamente (más recientes primero).
        """
        with self._lock:
            if bot_id and bot_id != "all":
                bot = self._bots.get(bot_id)
                if bot and hasattr(bot, 'unexecuted_orders'):
                    return list(reversed(bot.unexecuted_orders))
                return []
            per_bot = [list(b.unexecuted_orders) for b in self._bots.values() if hasattr(b, 'unexecuted_orders')]

        # Cartera completa: agregar de todos los bots + archivo global (leído fuera del lock)
        all_orders: List[dict] = []
        seen_ids = set()
        for orders in per_bot:
            for ord_item in orders:
                oid = ord_item.get('order_id')
                if oid and oid not in seen_ids:
                    seen_ids.add(oid)
                    all_orders.append(ord_item)
        for ord_item in self._read_global_unexecuted():
            oid = ord_item.get('order_id')
            if oid and oid not in seen_ids:
                seen_ids.add(oid)
                all_orders.append(ord_item)

        all_orders.sort(key=lambda x: str(x.get('timestamp', '')), reverse=True)
        return all_orders

    def clear_unexecuted_orders(self, bot_id: Optional[str] = None):
        """Limpia el historial de órdenes no ejecutadas."""
        with self._lock:
            if bot_id and bot_id != "all":
                bot = self._bots.get(bot_id)
                if bot and hasattr(bot, 'unexecuted_orders'):
                    bot.unexecuted_orders.clear()
                    bot._save_state()
            else:
                for b in self._bots.values():
                    if hasattr(b, 'unexecuted_orders'):
                        b.unexecuted_orders.clear()
                        b._save_state()
                audit_path = os.path.join(DATA_DIR, "unexecuted_orders_history.json")
                if os.path.exists(audit_path):
                    try:
                        with open(audit_path, "w", encoding="utf-8") as f:
                            json.dump([], f)
                    except Exception:
                        logger.warning("No se pudo vaciar el histórico global de órdenes no ejecutadas", exc_info=True)


# Instancia singleton compartida en toda la aplicación
bot_manager = BotManager()
