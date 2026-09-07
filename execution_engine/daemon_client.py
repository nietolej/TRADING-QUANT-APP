"""
Cliente de Telemetría y Abstracción para el Trading Daemon Core.
Permite a la Web GUI (NiceGUI/FastAPI) interactuar de forma transparente con el Daemon
de ejecución independiente en http://127.0.0.1:8001.

Proporciona detección automática de estado (Online / Offline) y fallback seguro
a modo embebido/local para garantizar disponibilidad en cualquier escenario.
"""

import os
import logging
import requests
from typing import Optional, Dict, Any, List
from datetime import datetime

logger = logging.getLogger("DaemonClient")

DEFAULT_DAEMON_URL = os.getenv("BOT_DAEMON_URL", "http://127.0.0.1:8001")


class PositionProxy:
    def __init__(self, data: Optional[Dict[str, Any]]):
        if not data:
            self.side = None
            self.entry_price = 0.0
            self.quantity = 0.0
            self.entry_timestamp = None
            self.sl_price = None
            self.tp_price = None
        else:
            self.side = data.get("side")
            self.entry_price = float(data.get("entry_price", 0.0))
            self.quantity = float(data.get("quantity", 0.0))
            self.entry_timestamp = data.get("entry_timestamp")
            self.sl_price = float(data["sl_price"]) if data.get("sl_price") is not None else None
            self.tp_price = float(data["tp_price"]) if data.get("tp_price") is not None else None


class BotProxy:
    """
    Proxy que emula la interfaz de PaperTrader para la Web GUI.
    Delega las acciones operativas al Trading Daemon mediante peticiones HTTP internas.
    """

    def __init__(self, data: Dict[str, Any], client: 'DaemonClient'):
        self._data = data
        self._daemon_client = client

        self.bot_id: str = data.get("bot_id", "")
        self.name: str = data.get("name", "")
        self.strategy_yaml_path: str = data.get("strategy_yaml_path", "")
        self.symbol: str = data.get("symbol", "BTC/USDT")
        self.timeframe: str = data.get("timeframe", "1h")
        self.initial_balance: float = float(data.get("initial_balance", 1.0))
        self.current_balance: float = float(data.get("current_balance", self.initial_balance))
        self.currency: str = data.get("currency", "USDT")
        self.use_testnet: bool = bool(data.get("use_testnet", False))
        self.status: str = data.get("status", "STOPPED")
        self.status_message: str = data.get("status_message", "Detenido")
        self.is_running: bool = bool(data.get("is_running", False))
        self.started_at = data.get("started_at")
        self.custom_parameters: Dict[str, Any] = data.get("custom_parameters", {})
        self.order_types: Dict[str, Any] = data.get("order_types", {})
        self.trade_history: List[Dict[str, Any]] = data.get("trade_history", [])
        self.log_lines: List[str] = data.get("log_lines", [])
        self.unexecuted_orders: List[Dict[str, Any]] = data.get("unexecuted_orders", [])
        self.stats: Dict[str, Any] = data.get("stats", {})
        
        self.strategy_name: str = os.path.splitext(os.path.basename(self.strategy_yaml_path))[0] if self.strategy_yaml_path else self.name

        pos_raw = data.get("position")
        self.position: Optional[PositionProxy] = PositionProxy(pos_raw) if pos_raw else None
        self.current_bid: float = float(self.position.entry_price) if self.position else 0.0
        self._cached_binance_client = None

    @property
    def _client(self):
        """Provee acceso transparente al cliente de Binance cuando es necesario para cierres manuales."""
        if self._cached_binance_client is None:
            try:
                from execution_engine.binance_client import BinanceTestnetClient
                self._cached_binance_client = BinanceTestnetClient(use_testnet=self.use_testnet)
            except Exception as e:
                logger.warning("No se pudo instanciar BinanceTestnetClient en BotProxy: %s", e)
        return self._cached_binance_client

    def start(self) -> bool:
        ok = self._daemon_client.start_bot(self.bot_id)
        if ok:
            self.is_running = True
            self.status = "RUNNING"
            self.status_message = "Corriendo (Daemon)"
        return ok

    def stop(self) -> bool:
        ok = self._daemon_client.stop_bot(self.bot_id)
        if ok:
            self.is_running = False
            self.status = "STOPPED"
            self.status_message = "Detenido"
        return ok

    def reset(self, new_initial_balance: Optional[float] = None) -> bool:
        ok = self._daemon_client.reset_bot(self.bot_id, new_initial_balance)
        if ok and new_initial_balance is not None:
            self.initial_balance = new_initial_balance
            self.current_balance = new_initial_balance
        return ok

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
    ) -> bool:
        return self._daemon_client.update_bot_config(
            self.bot_id,
            name=name,
            symbol=symbol,
            timeframe=timeframe,
            initial_balance=initial_balance,
            currency=currency,
            use_testnet=use_testnet,
            custom_parameters=custom_parameters,
            strategy_yaml_path=strategy_yaml_path,
            order_types=order_types
        )


class DaemonClient:
    """Cliente unificado para comunicación inter-proceso con el Trading Daemon."""

    def __init__(self, base_url: str = DEFAULT_DAEMON_URL, timeout_seconds: float = 0.5):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds
        self._last_online_check = 0.0
        self._is_online_cache = False
        self._cache_ttl = 1.0  # Cachear estado 1 segundo para evitar saturar sockets locales
        self._consecutive_failures = 0
        # Requerir varios fallos consecutivos antes de considerar el daemon offline y caer al
        # modo embebido, para no "perder de vista" bots por un hipo de red momentaneo de una
        # sola peticion (el daemon vuelve a marcarse online de inmediato en cuanto responde).
        self._failures_before_offline = 3

    def is_daemon_online(self, force_refresh: bool = False) -> bool:
        """Verifica si el Trading Daemon responde en el puerto local."""
        import time
        now = time.time()
        if not force_refresh and (now - self._last_online_check) < self._cache_ttl:
            return self._is_online_cache

        try:
            res = requests.get(f"{self.base_url}/health", timeout=self.timeout)
            reachable = (res.status_code == 200 and res.json().get("status") == "online")
        except Exception:
            reachable = False

        if reachable:
            self._consecutive_failures = 0
            self._is_online_cache = True
        else:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._failures_before_offline:
                self._is_online_cache = False
            # si no, se mantiene el ultimo estado conocido (probable hipo transitorio)

        self._last_online_check = now
        return self._is_online_cache

    def get_system_status(self) -> Dict[str, Any]:
        """Obtiene métricas de CPU, memoria y estado global del Daemon."""
        if not self.is_daemon_online():
            return {
                "status": "offline",
                "message": "Trading Daemon no está en ejecución",
                "pid": None,
                "uptime_seconds": 0
            }
        try:
            res = requests.get(f"{self.base_url}/api/status", timeout=self.timeout)
            return res.json()
        except Exception as e:
            logger.debug("Error al consultar status del daemon: %s", e)
            return {"status": "error", "detail": str(e)}

    def get_all_bots(self) -> List[Any]:
        """
        Retorna la lista de bots.
        Si el daemon está online, retorna instancias de BotProxy.
        Si está offline, recurre a bot_manager local (modo embebido).
        """
        if self.is_daemon_online():
            try:
                res = requests.get(f"{self.base_url}/api/bots", timeout=1.0)
                if res.status_code == 200:
                    bots_data = res.json()
                    return [BotProxy(b, self) for b in bots_data]
            except Exception as e:
                logger.warning("Fallo al obtener bots del daemon, usando fallback local: %s", e)

        from execution_engine.bot_manager import bot_manager
        return bot_manager.get_all_bots()

    def get_bot(self, bot_id: str) -> Optional[Any]:
        """Obtiene un bot específico vía Daemon o fallback."""
        if not bot_id:
            return None

        if self.is_daemon_online():
            try:
                res = requests.get(f"{self.base_url}/api/bots/{bot_id}", timeout=1.0)
                if res.status_code == 200:
                    return BotProxy(res.json(), self)
            except Exception:
                pass

        from execution_engine.bot_manager import bot_manager
        return bot_manager.get_bot(bot_id)

    def create_bot(
        self,
        strategy_yaml_path: str,
        initial_balance: float = 1.0,
        currency: str = "BTC",
        custom_parameters: Optional[dict] = None,
        use_testnet: bool = False,
        timeframe: Optional[str] = None,
        symbol: Optional[str] = None,
        name: Optional[str] = None,
        auto_start: bool = False
    ) -> Any:
        """Crea un bot en el daemon o en modo local.
        Nombres de parametro (symbol/timeframe) alineados con BotManager.create_bot local,
        que es como lo llaman todos los call sites reales (live_monitor_page.py, etc.)."""
        if self.is_daemon_online():
            payload = {
                "strategy_yaml_path": strategy_yaml_path,
                "initial_balance": initial_balance,
                "currency": currency,
                "custom_parameters": custom_parameters or {},
                "use_testnet": use_testnet,
                "custom_timeframe": timeframe,
                "custom_symbol": symbol,
                "name": name,
                "auto_start": auto_start
            }
            try:
                res = requests.post(f"{self.base_url}/api/bots", json=payload, timeout=3.0)
                if res.status_code == 200:
                    return BotProxy(res.json(), self)
            except Exception as e:
                logger.error("Error creando bot en daemon: %s", e)

        from execution_engine.bot_manager import bot_manager
        new_bot = bot_manager.create_bot(
            strategy_yaml_path=strategy_yaml_path,
            initial_balance=initial_balance,
            currency=currency,
            custom_parameters=custom_parameters,
            use_testnet=use_testnet,
            timeframe=timeframe,
            symbol=symbol,
            name=name,
        )
        if auto_start and new_bot and not new_bot.is_running:
            new_bot.start()
            bot_manager.save_state_to_disk()
        return new_bot

    def delete_bot(self, bot_id: str) -> bool:
        """Elimina un bot."""
        if self.is_daemon_online():
            try:
                res = requests.delete(f"{self.base_url}/api/bots/{bot_id}", timeout=2.0)
                if res.status_code == 200:
                    return True
            except Exception as e:
                logger.error("Error eliminando bot en daemon: %s", e)

        from execution_engine.bot_manager import bot_manager
        return bot_manager.delete_bot(bot_id)

    def start_bot(self, bot_id: str) -> bool:
        """Inicia un bot en el daemon."""
        if self.is_daemon_online():
            try:
                res = requests.post(f"{self.base_url}/api/bots/{bot_id}/start", timeout=5.0)
                return res.status_code == 200
            except Exception as e:
                logger.error("Error arrancando bot en daemon: %s", e)

        from execution_engine.bot_manager import bot_manager
        bot = bot_manager.get_bot(bot_id)
        if bot and not bot.is_running:
            bot.start()
            bot_manager.save_state_to_disk()
            return True
        return False

    def stop_bot(self, bot_id: str) -> bool:
        """Detiene un bot en el daemon."""
        if self.is_daemon_online():
            try:
                res = requests.post(f"{self.base_url}/api/bots/{bot_id}/stop", timeout=5.0)
                return res.status_code == 200
            except Exception as e:
                logger.error("Error deteniendo bot en daemon: %s", e)

        from execution_engine.bot_manager import bot_manager
        bot = bot_manager.get_bot(bot_id)
        if bot and bot.is_running:
            bot.stop()
            bot_manager.save_state_to_disk()
            return True
        return False

    def reset_bot(self, bot_id: str, new_initial_balance: Optional[float] = None) -> bool:
        """Resetea un bot."""
        if self.is_daemon_online():
            try:
                res = requests.post(
                    f"{self.base_url}/api/bots/{bot_id}/reset",
                    json={"new_initial_balance": new_initial_balance},
                    timeout=3.0
                )
                return res.status_code == 200
            except Exception as e:
                logger.error("Error reseteando bot en daemon: %s", e)

        from execution_engine.bot_manager import bot_manager
        bot = bot_manager.get_bot(bot_id)
        if bot:
            bot.reset(new_initial_balance=new_initial_balance)
            bot_manager.save_state_to_disk()
            return True
        return False

    def update_bot_config(self, bot_id: str, **kwargs) -> bool:
        """Actualiza la configuración de un bot en el daemon."""
        if self.is_daemon_online():
            try:
                res = requests.patch(f"{self.base_url}/api/bots/{bot_id}", json=kwargs, timeout=3.0)
                if res.status_code == 200:
                    return True
            except Exception as e:
                logger.error("Error actualizando config en daemon: %s", e)

        from execution_engine.bot_manager import bot_manager
        bot = bot_manager.get_bot(bot_id)
        if bot:
            bot.update_configuration(**kwargs)
            bot_manager.save_state_to_disk()
            return True
        return False

    def start_all(self) -> bool:
        """Inicia todos los bots."""
        if self.is_daemon_online():
            try:
                res = requests.post(f"{self.base_url}/api/bots/start_all", timeout=5.0)
                return res.status_code == 200
            except Exception:
                pass

        from execution_engine.bot_manager import bot_manager
        bot_manager.start_all()
        return True

    def stop_all(self) -> bool:
        """Detiene todos los bots."""
        if self.is_daemon_online():
            try:
                res = requests.post(f"{self.base_url}/api/bots/stop_all", timeout=5.0)
                return res.status_code == 200
            except Exception:
                pass

        from execution_engine.bot_manager import bot_manager
        bot_manager.stop_all()
        return True

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Obtiene el resumen de portafolio."""
        if self.is_daemon_online():
            try:
                res = requests.get(f"{self.base_url}/api/portfolio_summary", timeout=1.0)
                if res.status_code == 200:
                    return res.json()
            except Exception:
                pass

        from execution_engine.bot_manager import bot_manager
        return bot_manager.get_portfolio_summary()

    def get_unexecuted_orders(self, bot_id: str = "all") -> List[Dict[str, Any]]:
        """Obtiene órdenes pendientes/no ejecutadas."""
        if self.is_daemon_online():
            try:
                res = requests.get(f"{self.base_url}/api/unexecuted_orders?bot_id={bot_id}", timeout=1.0)
                if res.status_code == 200:
                    return res.json()
            except Exception:
                pass

        from execution_engine.bot_manager import bot_manager
        return bot_manager.get_unexecuted_orders(bot_id=bot_id)

    def clear_unexecuted_orders(self, bot_id: Optional[str] = None) -> bool:
        """Limpia el registro de órdenes no ejecutadas."""
        if self.is_daemon_online():
            try:
                res = requests.post(f"{self.base_url}/api/clear_unexecuted_orders", json={"bot_id": bot_id}, timeout=2.0)
                return res.status_code == 200
            except Exception:
                pass

        from execution_engine.bot_manager import bot_manager
        bot_manager.clear_unexecuted_orders(bot_id=bot_id)
        return True

    def emergency_kill(self) -> Dict[str, Any]:
        """Activa el Kill Switch de emergencia."""
        if self.is_daemon_online():
            try:
                res = requests.post(f"{self.base_url}/api/emergency_kill", timeout=5.0)
                if res.status_code == 200:
                    return res.json()
            except Exception:
                pass

        from execution_engine.bot_manager import bot_manager
        from execution_engine.security_manager import set_real_trading_enabled
        from notifications.telegram_bot import TelegramNotifier

        bot_manager.stop_all()
        set_real_trading_enabled(False)
        TelegramNotifier().send_alert(
            title="🚨 KILL SWITCH ACTIVADO (LOCAL)",
            details={"Origen": "Web GUI Local", "Acción": "Todos los bots detenidos"}
        )
        return {"status": "emergency_killed", "message": "Kill switch local ejecutado"}


# Instancia singleton para uso en toda la aplicación web
daemon_client = DaemonClient()
