import os
import json
import logging
import threading
from typing import Dict, List, Optional, Any
from .paper_trader import PaperTrader

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
BOTS_STATE_FILE = os.path.join(DATA_DIR, "bots_state.json")


class BotManager:
    """
    Gestor centralizado de múltiples instancias de bots de trading en vivo (Paper Trading).
    Permite crear, arrancar, detener, persistir en disco y monitorear múltiples bots concurrentemente.
    """

    def __init__(self, persistence_file: str = BOTS_STATE_FILE):
        self._bots: Dict[str, PaperTrader] = {}
        self._lock = threading.RLock()
        self.persistence_file = persistence_file
        os.makedirs(os.path.dirname(self.persistence_file), exist_ok=True)
        # Cargar bots guardados previamente en disco
        self.load_state_from_disk(auto_start_running_bots=True)

    def _on_bot_state_changed(self, bot: PaperTrader):
        """Callback invocado por cualquier PaperTrader cuando cambia su estado."""
        self.save_state_to_disk()

    def save_state_to_disk(self):
        """Guarda de forma atómica el estado de todos los bots en archivo JSON."""
        with self._lock:
            try:
                bots_payload = {}
                for bot_id, bot in self._bots.items():
                    bots_payload[bot_id] = bot.to_dict()

                temp_file = f"{self.persistence_file}.tmp"
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(bots_payload, f, indent=2, ensure_ascii=False)
                
                # Reemplazo atómico para evitar corrupción
                if os.path.exists(self.persistence_file):
                    os.replace(temp_file, self.persistence_file)
                else:
                    os.rename(temp_file, self.persistence_file)
                logger.debug("Estado de %d bots guardado exitosamente en %s", len(bots_payload), self.persistence_file)
            except Exception as e:
                logger.error("Error al guardar estado de los bots en disco: %s", e)

    def load_state_from_disk(self, auto_start_running_bots: bool = True):
        """Carga y restaura las instancias de bots desde el archivo JSON persistente."""
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

                    # Si estaba operando al momento del guardado, reanudar
                    if auto_start_running_bots and bot_dict.get("is_running") is True:
                        logger.info("Reanudando ejecución automática del bot %s tras reinicio/recarga...", bot.name)
                        bot.start()

            except Exception as e:
                logger.error("Error al cargar estado de bots desde disco: %s", e)

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
            bot = self._bots.get(bot_id)
            if bot:
                if bot.is_running:
                    bot.stop()
                del self._bots[bot_id]
                self.save_state_to_disk()
                logger.info("Bot eliminado: %s (ID: %s)", bot.name, bot_id)
                return True
            return False

    def start_all(self):
        """Inicia todos los bots registrados que estén detenidos."""
        with self._lock:
            for bot in self._bots.values():
                if not bot.is_running:
                    bot.start()
            self.save_state_to_disk()

    def stop_all(self):
        """Detiene todos los bots que estén corriendo."""
        with self._lock:
            for bot in self._bots.values():
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


# Instancia singleton compartida en toda la aplicación
bot_manager = BotManager()
