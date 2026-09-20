"""
Cliente de Telemetría y Abstracción para el Trading Daemon Core.
Permite a la Web GUI (NiceGUI/FastAPI) interactuar de forma transparente con el Daemon
de ejecución independiente en http://127.0.0.1:8001.

Proporciona detección automática de estado (Online / Offline) y fallback seguro
a modo embebido/local para garantizar disponibilidad en cualquier escenario.
"""

import os
import time
import logging
import threading
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
                self._cached_binance_client = BinanceTestnetClient(use_testnet=self.use_testnet, bot_id=self.bot_id)
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


class DaemonOfflineError(RuntimeError):
    """El Trading Daemon (puerto 8001) no está en ejecución: no hay dónde operar los bots."""


OFFLINE_HINT = "El Trading Daemon está apagado. Inícialo con start_bot_daemon.bat (o start_all.bat)."

_EMPTY_PORTFOLIO_SUMMARY = {
    "total_bots": 0, "running_bots": 0, "stopped_bots": 0, "error_bots": 0,
    "balances_by_currency": {}, "pnl_by_currency": {},
    "balance_display": "0.00 USDT", "pnl_display": "+0.00 USDT (+0.00%)",
    "total_initial_balance": 0.0, "total_current_balance": 0.0, "total_pnl": 0.0, "total_pnl_pct": 0.0,
    "total_trades": 0, "active_positions": 0, "win_rate": 0.0,
}


class DaemonClient:
    """
    Cliente para comunicarse con el Trading Daemon.

    Los bots viven SOLO en el daemon. Antes, si el daemon no respondía, el servidor web creaba un
    BotManager propio y reanudaba los bots dentro de su proceso: cada guardado de código (hot
    reload) los reiniciaba, un cuelgue de la interfaz los congelaba y, al levantar el daemon
    después, dos procesos operaban los mismos bots. Ahora, sin daemon, no hay bots que mostrar ni
    operar y cada acción de control informa claramente que el daemon está apagado.
    """

    # Cada cuánto el hilo de vigilancia consulta /health. Los llamadores nunca hacen esa consulta
    # ellos mismos: en Windows conectar a un puerto local cerrado tarda ~0.5 s y bloqueaba el
    # event loop en cada llamada.
    HEALTH_POLL_S = 2.0

    def __init__(self, base_url: str = DEFAULT_DAEMON_URL, timeout_seconds: float = 0.5):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds
        self._last_online_check = 0.0
        self._is_online_cache = False
        self._consecutive_failures = 0
        # Requerir varios fallos consecutivos antes de considerar el daemon offline, para no
        # "perder de vista" los bots por un hipo momentáneo de una sola petición.
        self._failures_before_offline = 3
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_lock = threading.Lock()

    # ── Estado online/offline ───────────────────────────────────────────────

    def _probe_health(self) -> bool:
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
            # si no, se mantiene el último estado conocido (probable hipo transitorio)
        self._last_online_check = time.time()
        return self._is_online_cache

    def _monitor_loop(self):
        while True:
            try:
                self._probe_health()
            except Exception:
                logger.exception("Error en el hilo de vigilancia del daemon")
            time.sleep(self.HEALTH_POLL_S)

    def _ensure_monitor(self):
        if self._monitor_thread is not None:
            return
        with self._monitor_lock:
            if self._monitor_thread is None:
                # La primera comprobación es síncrona para tener un estado correcto desde ya.
                self._probe_health()
                self._monitor_thread = threading.Thread(target=self._monitor_loop, name="daemon-health", daemon=True)
                self._monitor_thread.start()

    def is_daemon_online(self, force_refresh: bool = False) -> bool:
        """Estado del daemon según el último sondeo del hilo de vigilancia (sin red en la llamada)."""
        if force_refresh:
            return self._probe_health()
        self._ensure_monitor()
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
            logger.warning("Error al consultar status del daemon: %s", e)
            return {"status": "error", "detail": str(e)}

    # ── Lecturas (sin daemon: vacías) ───────────────────────────────────────

    def get_all_bots(self) -> List[Any]:
        """Lista de bots (BotProxy). Con el daemon apagado no hay bots: lista vacía."""
        if self.is_daemon_online():
            try:
                res = requests.get(f"{self.base_url}/api/bots", timeout=1.0)
                if res.status_code == 200:
                    return [BotProxy(b, self) for b in res.json()]
            except Exception as e:
                logger.warning("Fallo al obtener bots del daemon: %s", e)
        return []

    def get_bot(self, bot_id: str) -> Optional[Any]:
        """Obtiene un bot específico vía Daemon (None si no existe o el daemon está apagado)."""
        if not bot_id:
            return None
        if self.is_daemon_online():
            try:
                res = requests.get(f"{self.base_url}/api/bots/{bot_id}", timeout=1.0)
                if res.status_code == 200:
                    return BotProxy(res.json(), self)
            except Exception as e:
                logger.warning("Fallo al obtener el bot %s del daemon: %s", bot_id, e)
        return None

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Resumen agregado de la cartera (ceros si el daemon está apagado)."""
        if self.is_daemon_online():
            try:
                res = requests.get(f"{self.base_url}/api/portfolio_summary", timeout=1.0)
                if res.status_code == 200:
                    return res.json()
            except Exception as e:
                logger.warning("Fallo al obtener el resumen de cartera: %s", e)
        return dict(_EMPTY_PORTFOLIO_SUMMARY)

    def get_unexecuted_orders(self, bot_id: str = "all") -> List[Dict[str, Any]]:
        """Órdenes no ejecutadas / rechazadas (vacío si el daemon está apagado)."""
        if self.is_daemon_online():
            try:
                res = requests.get(f"{self.base_url}/api/unexecuted_orders?bot_id={bot_id}", timeout=1.0)
                if res.status_code == 200:
                    return res.json()
            except Exception as e:
                logger.warning("Fallo al obtener órdenes no ejecutadas: %s", e)
        return []

    # ── Acciones de control (sin daemon: error claro, jamás un bot local) ───

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
        """Crea un bot en el daemon. Lanza DaemonOfflineError si el daemon está apagado."""
        if not self.is_daemon_online():
            raise DaemonOfflineError(OFFLINE_HINT)
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
            raise RuntimeError(f"El daemon rechazó la creación del bot (HTTP {res.status_code}): {res.text[:200]}")
        except requests.RequestException as e:
            logger.error("Error creando bot en daemon: %s", e)
            raise RuntimeError(f"No se pudo crear el bot en el daemon: {e}") from e

    def _post(self, path: str, what: str, timeout: float = 5.0, json_body: Optional[dict] = None) -> bool:
        if not self.is_daemon_online():
            logger.warning("%s: %s", what, OFFLINE_HINT)
            return False
        try:
            res = requests.post(f"{self.base_url}{path}", json=json_body, timeout=timeout)
            return res.status_code == 200
        except Exception as e:
            logger.error("Error en '%s' (daemon online pero la petición falló): %s", what, e)
            return False

    def delete_bot(self, bot_id: str) -> bool:
        """Elimina un bot."""
        if not self.is_daemon_online():
            logger.warning("delete_bot: %s", OFFLINE_HINT)
            return False
        try:
            res = requests.delete(f"{self.base_url}/api/bots/{bot_id}", timeout=2.0)
            return res.status_code == 200
        except Exception as e:
            logger.error("Error eliminando bot en daemon: %s", e)
            return False

    def start_bot(self, bot_id: str) -> bool:
        """Inicia un bot en el daemon."""
        return self._post(f"/api/bots/{bot_id}/start", "start_bot")

    def stop_bot(self, bot_id: str) -> bool:
        """Detiene un bot en el daemon."""
        return self._post(f"/api/bots/{bot_id}/stop", "stop_bot")

    def reset_bot(self, bot_id: str, new_initial_balance: Optional[float] = None) -> bool:
        """Resetea un bot."""
        return self._post(f"/api/bots/{bot_id}/reset", "reset_bot", timeout=3.0,
                          json_body={"new_initial_balance": new_initial_balance})

    def update_bot_config(self, bot_id: str, **kwargs) -> bool:
        """Actualiza la configuración de un bot en el daemon."""
        if not self.is_daemon_online():
            logger.warning("update_bot_config: %s", OFFLINE_HINT)
            return False
        try:
            res = requests.patch(f"{self.base_url}/api/bots/{bot_id}", json=kwargs, timeout=3.0)
            return res.status_code == 200
        except Exception as e:
            logger.error("Error actualizando config en daemon (online pero la petición falló): %s", e)
            return False

    def start_all(self) -> bool:
        """Inicia todos los bots."""
        return self._post("/api/bots/start_all", "start_all")

    def stop_all(self) -> bool:
        """Detiene todos los bots."""
        return self._post("/api/bots/stop_all", "stop_all")

    def clear_unexecuted_orders(self, bot_id: Optional[str] = None) -> bool:
        """Limpia el registro de órdenes no ejecutadas."""
        return self._post("/api/clear_unexecuted_orders", "clear_unexecuted_orders", timeout=2.0,
                          json_body={"bot_id": bot_id})

    def emergency_kill(self) -> Dict[str, Any]:
        """Activa el Kill Switch de emergencia."""
        from execution_engine.security_manager import set_real_trading_enabled
        from notifications.telegram_bot import TelegramNotifier

        if self.is_daemon_online():
            try:
                res = requests.post(f"{self.base_url}/api/emergency_kill", timeout=5.0)
                if res.status_code == 200:
                    return res.json()
                logger.error("Kill switch: daemon online devolvió status %s en emergency_kill", res.status_code)
            except Exception as e:
                # CRÍTICO: el daemon está online (con los bots operando) pero la petición de kill
                # switch falló. Reportar el fallo real para que el usuario reintente o mate el proceso.
                logger.error("Kill switch: falló la petición al daemon (SIGUE ONLINE): %s", e)
            return {
                "status": "error",
                "message": (
                    "El Trading Daemon está online pero no respondió al kill switch. "
                    "Los bots reales pueden seguir operando. Reintenta o detén el proceso "
                    "del daemon manualmente."
                ),
            }

        # Sin daemon no hay bots operando en ningún proceso: solo se cierra el candado de trading real.
        set_real_trading_enabled(False)
        TelegramNotifier().send_alert(
            title="🚨 KILL SWITCH ACTIVADO (DAEMON APAGADO)",
            details={"Origen": "Web GUI", "Acción": "Candado de trading real cerrado; no había daemon con bots"}
        )
        return {"status": "emergency_killed", "message": "Daemon apagado: candado de trading real cerrado"}


# Instancia singleton para uso en toda la aplicación web
daemon_client = DaemonClient()
