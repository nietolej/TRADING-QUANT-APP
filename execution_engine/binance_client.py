import os
import time
import copy
import math
import uuid
import logging
from typing import Dict, Any, Optional, List, Tuple
from dotenv import load_dotenv
from binance.client import Client
from binance.exceptions import BinanceAPIException

load_dotenv()
logger = logging.getLogger("BinanceClient")

import dotenv

from execution_engine.security_manager import (
    is_real_trading_enabled,
    validate_order_guardrails,
    load_security_config,
    save_security_config,
)


# El desfase de reloj es una propiedad de esta máquina, no de cada cliente: se reutiliza el
# último valor medido durante TIME_OFFSET_TTL_S en vez de gastar un viaje de red (~0.5 s) en
# CADA instanciación de BinanceTestnetClient (había 17 en las páginas y una por ciclo de conciliación).
TIME_OFFSET_TTL_S = 300.0
_TIME_OFFSET_CACHE: Dict[Tuple[bool, bool], Tuple[int, float]] = {}


def _sync_timestamp_offset(client: Client, use_futures: bool = True, force: bool = False) -> None:
    """
    Corrige el desfase de reloj local frente al servidor de Binance (Error -1021).

    Es muy común que el reloj del sistema (sobre todo en Windows) esté
    desincronizado por unos pocos segundos respecto al NTP de Binance. Sin esta
    corrección, TODAS las llamadas firmadas (cuenta, posiciones, precios,
    órdenes) fallan con "Timestamp for this request was ahead/behind of the
    server's time" y quedan silenciadas por los try/except del cliente,
    dando la falsa impresión de que la app "no sincroniza" con Binance.
    """
    key = (bool(use_futures), bool(getattr(client, "testnet", False)))
    cached = _TIME_OFFSET_CACHE.get(key)
    if not force and cached and (time.monotonic() - cached[1]) < TIME_OFFSET_TTL_S:
        client.timestamp_offset = cached[0]
        return
    try:
        server_time = (client.futures_time() if use_futures else client.get_server_time()).get("serverTime")
        if server_time:
            offset = int(server_time) - int(time.time() * 1000)
            client.timestamp_offset = offset
            _TIME_OFFSET_CACHE[key] = (offset, time.monotonic())
    except Exception as e:
        logger.warning("No se pudo sincronizar el offset de tiempo con Binance: %s", e)


class ResilientClient(Client):
    """
    Cliente de Binance que se autorrepara ante el desfase de reloj (error -1021).

    El offset se medía una sola vez al crear el cliente, pero los clientes de los bots viven días:
    al derivar el reloj, cada orden fallaba con "Timestamp ahead of the server's time" (173 de las
    257 órdenes no ejecutadas del 6/9). Ahora, ante un -1021 en una petición firmada, se vuelve a
    medir el offset y se reintenta UNA vez con timestamp nuevo.
    """

    def _request(self, method, uri: str, signed: bool, force_params: bool = False, **kwargs):
        pristine = copy.deepcopy(kwargs) if signed else None  # python-binance escribe timestamp/firma en kwargs
        try:
            return super()._request(method, uri, signed, force_params, **kwargs)
        except BinanceAPIException as e:
            if not signed or e.code != -1021:
                raise
            logger.warning("Desfase de reloj (-1021) en %s: resincronizando y reintentando una vez", uri)
            _sync_timestamp_offset(self, use_futures=True, force=True)
            return super()._request(method, uri, signed, force_params, **pristine)


def _fmt_ts(ms) -> str:
    """Formatea un timestamp en milisegundos de Binance como DD/MM HH:mm:ss."""
    if not ms:
        return "-"
    try:
        from datetime import datetime
        return datetime.fromtimestamp(int(ms) / 1000.0).strftime("%d/%m %H:%M:%S")
    except Exception:
        return str(ms)


def format_binance_error(e: Exception) -> str:
    """
    Traduce errores técnicos de la API de Binance (excepciones o texto crudo) a mensajes
    claros y accionables para el usuario, cubriendo los casos que más confusión generan
    (bloqueo geográfico, desfase de reloj, credenciales inválidas, timeouts de red).
    """
    status_code = getattr(e, "status_code", None)
    raw = str(e)

    if status_code == 451 or "restricted location" in raw or "b. Eligibility" in raw:
        return (
            "🌍 Binance bloqueó el acceso desde tu red (HTTP 451 - Ubicación restringida). "
            "Es un bloqueo geográfico/regulatorio aplicado por Binance sobre tu IP actual, "
            "no un error de la aplicación. Verifica si tu país/red tiene acceso a Binance "
            "Futures/Testnet, o prueba desde otra conexión."
        )
    if "Invalid JSON error message" in raw or "<!DOCTYPE html>" in raw or "www.binance.com" in raw:
        return (
            "🌍 Binance respondió con su página web (HTML) en vez de datos de la API. "
            "Normalmente pasa cuando la IP/red está bloqueada o redirigida por Binance "
            "(geobloqueo, firewall, proxy/VPN) antes de llegar al endpoint real. "
            "Verifica tu conexión/región o prueba desde otra red."
        )
    if "-2015" in raw:
        return "🔑 Clave API inválida, restricción de IP o faltan permisos de Futuros (Error -2015)."
    if "-1021" in raw:
        return "⏱️ Desincronización de reloj del sistema con el servidor de Binance (Error -1021)."
    if "-2014" in raw:
        return "🔑 Formato de API-key inválido (Error -2014)."
    if "Read timed out" in raw or "Connection" in raw:
        return f"🔌 No se pudo conectar con Binance (timeout o red no disponible): {raw}"
    return raw


# Prefijo con el que se etiqueta (newClientOrderId) toda orden enviada a Binance por esta
# app, para poder identificarla en el historial del exchange y distinguirla de órdenes
# manuales u otros bots que compartan la misma cuenta. Usado por el módulo separado
# reconciliation/ para detectar huérfanos (ver reconciliation/models.py).
APP_CLIENT_ORDER_PREFIX = "QTAPP_"


def _new_client_order_id() -> str:
    return f"{APP_CLIENT_ORDER_PREFIX}{uuid.uuid4().hex[:20]}"


def _log_order_to_ledger(**kwargs) -> None:
    """
    Registra en el ledger de reconciliación (módulo separado, ver reconciliation/order_ledger.py)
    el resultado de un intento de orden. Es "best-effort": el ledger es una capa de auditoría
    añadida, así que un fallo aquí (import roto, DB bloqueada, etc.) nunca debe interrumpir el
    envío de órdenes reales a Binance.
    """
    try:
        from reconciliation.order_ledger import log_app_order
        log_app_order(**kwargs)
    except Exception as e:
        logger.debug("No se pudo registrar la orden en el ledger de reconciliación: %s", e)


def get_binance_credentials() -> Dict[str, Any]:
    """Obtiene las credenciales actuales cargadas en variables de entorno sin fallbacks cruzados inseguros."""
    is_testnet = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
    
    testnet_k = os.getenv("BINANCE_TESTNET_API_KEY", "").strip()
    testnet_s = os.getenv("BINANCE_TESTNET_SECRET_KEY", "").strip()
    # Si no hay clave específica de testnet, verificar si BINANCE_API_KEY existe Y el entorno activo es testnet
    if not testnet_k and is_testnet and not os.getenv("BINANCE_REAL_API_KEY"):
        testnet_k = os.getenv("BINANCE_API_KEY", "").strip()
    if not testnet_s and is_testnet and not os.getenv("BINANCE_REAL_SECRET_KEY"):
        testnet_s = os.getenv("BINANCE_SECRET_KEY", "").strip()

    real_k = os.getenv("BINANCE_REAL_API_KEY", "").strip()
    real_s = os.getenv("BINANCE_REAL_SECRET_KEY", "").strip()
    # Para real, NUNCA usar BINANCE_API_KEY si BINANCE_TESTNET_API_KEY está configurada (evita mezclar credenciales)
    if not real_k and not is_testnet and not os.getenv("BINANCE_TESTNET_API_KEY"):
        real_k = os.getenv("BINANCE_API_KEY", "").strip()
    if not real_s and not is_testnet and not os.getenv("BINANCE_TESTNET_SECRET_KEY"):
        real_s = os.getenv("BINANCE_SECRET_KEY", "").strip()

    return {
        "testnet_api_key": testnet_k,
        "testnet_secret_key": testnet_s,
        "real_api_key": real_k,
        "real_secret_key": real_s,
        "default_network": "testnet" if is_testnet else "mainnet",
        "has_testnet": bool(testnet_k and testnet_s),
        "has_real": bool(real_k and real_s),
        "real_trading_enabled": is_real_trading_enabled()
    }

def save_binance_credentials(
    testnet_key: str = "",
    testnet_secret: str = "",
    real_key: str = "",
    real_secret: str = "",
    default_network: str = "testnet"
) -> Tuple[bool, Optional[str]]:
    """Guarda las claves API en el archivo .env y las actualiza inmediatamente en os.environ."""
    root_dir = os.path.dirname(os.path.dirname(__file__))
    env_path = os.path.join(root_dir, ".env")
    
    if not os.path.exists(env_path):
        try:
            with open(env_path, "w", encoding="utf-8") as f:
                f.write("# Archivo de entorno\n")
        except Exception as e:
            return False, f"No se pudo crear el archivo .env: {e}"

    try:
        t_key = testnet_key.strip()
        t_sec = testnet_secret.strip()
        r_key = real_key.strip()
        r_sec = real_secret.strip()

        # Guardar en .env y os.environ
        if t_key:
            dotenv.set_key(env_path, "BINANCE_TESTNET_API_KEY", t_key)
            os.environ["BINANCE_TESTNET_API_KEY"] = t_key
        if t_sec:
            dotenv.set_key(env_path, "BINANCE_TESTNET_SECRET_KEY", t_sec)
            os.environ["BINANCE_TESTNET_SECRET_KEY"] = t_sec

        if r_key:
            dotenv.set_key(env_path, "BINANCE_REAL_API_KEY", r_key)
            os.environ["BINANCE_REAL_API_KEY"] = r_key
        if r_sec:
            dotenv.set_key(env_path, "BINANCE_REAL_SECRET_KEY", r_sec)
            os.environ["BINANCE_REAL_SECRET_KEY"] = r_sec

        is_testnet = (default_network.lower() == "testnet")
        dotenv.set_key(env_path, "BINANCE_TESTNET", "true" if is_testnet else "false")
        os.environ["BINANCE_TESTNET"] = "true" if is_testnet else "false"

        # Mantener BINANCE_API_KEY y BINANCE_SECRET_KEY sincronizadas con la red activa
        active_key = t_key if is_testnet else r_key
        active_secret = t_sec if is_testnet else r_sec
        if active_key:
            dotenv.set_key(env_path, "BINANCE_API_KEY", active_key)
            os.environ["BINANCE_API_KEY"] = active_key
        if active_secret:
            dotenv.set_key(env_path, "BINANCE_SECRET_KEY", active_secret)
            os.environ["BINANCE_SECRET_KEY"] = active_secret

        return True, None
    except Exception as e:
        logger.error("Error al guardar credenciales en .env: %s", e)
        return False, f"Error al guardar credenciales en .env: {e}"

def verify_binance_credentials(
    use_testnet: bool, 
    api_key: Optional[str] = None, 
    api_secret: Optional[str] = None
) -> Dict[str, Any]:
    """Valida la conectividad, ping y autenticación de credenciales sin colocar órdenes."""
    t0 = time.time()
    network_label = "Binance Futures Testnet" if use_testnet else "Binance Real (Mainnet)"
    
    key = (api_key or "").strip()
    sec = (api_secret or "").strip()
    
    if not key or not sec:
        creds = get_binance_credentials()
        if use_testnet:
            key = creds["testnet_api_key"]
            sec = creds["testnet_secret_key"]
        else:
            key = creds["real_api_key"]
            sec = creds["real_secret_key"]

    if not key or not sec:
        return {
            "success": False,
            "network": network_label,
            "latency_ms": 0,
            "error": f"Claves no ingresadas. Por favor ingresa API Key y Secret Key para {network_label}."
        }

    try:
        client = ResilientClient(key, sec, testnet=use_testnet, ping=False, requests_params={'timeout': 8})
        if use_testnet:
            client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'
        else:
            client.FUTURES_URL = 'https://fapi.binance.com/fapi'
        _sync_timestamp_offset(client, use_futures=True)

        # 1. Ping
        client.futures_ping()
        latency = int((time.time() - t0) * 1000)

        # 2. Consultar cuenta de futuros para verificar permisos y saldo
        acc = client.futures_account()
        wallet_bal = float(acc.get('totalWalletBalance', 0.0))
        avail_bal = float(acc.get('availableBalance', 0.0))
        can_trade = bool(acc.get('canTrade', True))

        return {
            "success": True,
            "network": network_label,
            "latency_ms": latency,
            "wallet_balance": wallet_bal,
            "available_balance": avail_bal,
            "can_trade": can_trade,
            "error": None
        }
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        return {
            "success": False,
            "network": network_label,
            "latency_ms": latency,
            "error": format_binance_error(e)
        }

class BinanceTestnetClient:
    """Cliente unificado para interactuar con Binance (Futures Testnet y Real Mainnet)."""

    def __init__(self, use_testnet: bool = False, api_key: Optional[str] = None, api_secret: Optional[str] = None,
                 bot_id: Optional[str] = None):
        self.use_testnet = use_testnet
        # Bot al que se atribuyen las órdenes que este cliente registre en el ledger de
        # reconciliación (None = orden manual/de prueba, sin bot asociado).
        self.bot_id = bot_id
        if api_key is not None and api_secret is not None:
            self.api_key = api_key.strip()
            self.api_secret = api_secret.strip()
        elif use_testnet:
            self.api_key = os.getenv("BINANCE_TESTNET_API_KEY", "").strip()
            self.api_secret = os.getenv("BINANCE_TESTNET_SECRET_KEY", "").strip()
            if not self.api_key and not os.getenv("BINANCE_REAL_API_KEY"):
                self.api_key = os.getenv("BINANCE_API_KEY", "").strip()
            if not self.api_secret and not os.getenv("BINANCE_REAL_SECRET_KEY"):
                self.api_secret = os.getenv("BINANCE_SECRET_KEY", "").strip()
        else:
            self.api_key = os.getenv("BINANCE_REAL_API_KEY", "").strip()
            self.api_secret = os.getenv("BINANCE_REAL_SECRET_KEY", "").strip()
            if not self.api_key and not os.getenv("BINANCE_TESTNET_API_KEY"):
                self.api_key = os.getenv("BINANCE_API_KEY", "").strip()
            if not self.api_secret and not os.getenv("BINANCE_TESTNET_SECRET_KEY"):
                self.api_secret = os.getenv("BINANCE_SECRET_KEY", "").strip()

        # Inicialización segura de cliente Binance sin ping síncrono bloqueante
        self.client = None
        try:
            self.client = ResilientClient(
                self.api_key,
                self.api_secret,
                testnet=use_testnet,
                ping=False,
                requests_params={'timeout': 10}
            )
            if use_testnet:
                self.client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'
            else:
                self.client.FUTURES_URL = 'https://fapi.binance.com/fapi'
            _sync_timestamp_offset(self.client, use_futures=True)
        except Exception as e:
            logger.warning("No se pudo inicializar Binance Client (restricción geográfica o red): %s", e)
            self.client = None

    def get_historical_klines(self, symbol: str, interval: str, lookback_str: str):
        """Obtiene velas históricas para inicializar indicadores."""
        if not self.client:
            return []
        binance_symbol = symbol.replace("/", "").upper()
        return self.client.get_historical_klines(binance_symbol, interval, lookback_str)

    def verify_order_status(
        self,
        symbol: str,
        order_id: int,
        expected_statuses: Optional[List[str]] = None,
        max_attempts: int = 4,
        delay_seconds: float = 0.5
    ) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Consulta activamente el estado de una orden en Binance Futures para verificar
        si fue ejecutada (FILLED) o si falló/fue rechazada (REJECTED, CANCELED, EXPIRED, etc.).
        
        Returns:
            Tuple[is_success, order_dict, error_message]
        """
        if expected_statuses is None:
            expected_statuses = ["FILLED"]

        if not self.client:
            return False, None, "Cliente de Binance no disponible (restricción geográfica o credenciales ausentes)"

        binance_symbol = symbol.replace("/", "").upper()
        last_order = None

        for attempt in range(1, max_attempts + 1):
            try:
                order_info = self.client.futures_get_order(symbol=binance_symbol, orderId=order_id)
                last_order = order_info
                status = str(order_info.get("status", "")).upper()

                if status in expected_statuses:
                    logger.info("Orden %s verificada exitosamente en estado %s (Intento %d/%d)", order_id, status, attempt, max_attempts)
                    return True, order_info, None

                # Si está en estado terminal fallido
                if status in ["REJECTED", "CANCELED", "EXPIRED"]:
                    err = f"Orden {order_id} no se ejecutó en el exchange. Estado actual: {status}"
                    logger.warning(err)
                    return False, order_info, err

                # Si aún está NEW o PARTIALLY_FILLED y esperamos FILLED, esperar antes del siguiente intento
                if attempt < max_attempts:
                    time.sleep(delay_seconds)

            except Exception as e:
                logger.warning("Error consultando estado de orden %s (Intento %d/%d): %s", order_id, attempt, max_attempts, e)
                if attempt < max_attempts:
                    time.sleep(delay_seconds)
                else:
                    return False, last_order, f"Error consultando orden {order_id}: {e}"

        status = last_order.get("status", "UNKNOWN") if last_order else "UNKNOWN"
        err = f"La orden {order_id} no alcanzó el estado requerido {expected_statuses} tras {max_attempts} intentos. Estado final: {status}"
        logger.warning(err)
        return False, last_order, err

    def get_symbol_precisions(self, symbol: str) -> Tuple[int, int, float, float]:
        """
        Obtiene la precisión de cantidad, precio, cantidad mínima y el STEP SIZE real (tamaño
        del incremento válido, no solo la cantidad de decimales) para un par en Binance Futures.
        Consulta futures_exchange_info() con caché en memoria o utiliza estándares del exchange.
        """
        binance_symbol = symbol.replace("/", "").upper()
        if not hasattr(self, '_symbol_precision_cache'):
            self._symbol_precision_cache = {}
        if not hasattr(self, '_min_notional_cache'):
            self._min_notional_cache = {}

        if binance_symbol in self._symbol_precision_cache:
            return self._symbol_precision_cache[binance_symbol]

        # Intentar obtener de exchange_info de Binance
        qty_prec = 3
        price_prec = 2
        min_qty = 0.001
        step_size_val = 0.001

        if self.client:
            try:
                info = self.client.futures_exchange_info()
                for s in info.get("symbols", []):
                    sym_name = s.get("symbol")
                    if sym_name:
                        q_p = int(s.get("quantityPrecision", 3))
                        p_p = int(s.get("pricePrecision", 2))
                        m_q = 0.001
                        s_size = 10 ** (-q_p)
                        for f in s.get("filters", []):
                            if f.get("filterType") == "LOT_SIZE":
                                step_size = f.get("stepSize", "0.001")
                                s_size = float(step_size)
                                if "." in step_size:
                                    q_p = len(step_size.rstrip("0").split(".")[1])
                                else:
                                    q_p = 0
                                m_q = float(f.get("minQty", 0.001))
                            elif f.get("filterType") == "PRICE_FILTER":
                                tick_size = f.get("tickSize", "0.01")
                                if "." in tick_size:
                                    p_p = len(tick_size.rstrip("0").split(".")[1])
                            elif f.get("filterType") == "MIN_NOTIONAL":
                                self._min_notional_cache[sym_name] = float(f.get("notional") or f.get("minNotional") or 0.0)
                        self._symbol_precision_cache[sym_name] = (q_p, p_p, m_q, s_size)
                if binance_symbol in self._symbol_precision_cache:
                    return self._symbol_precision_cache[binance_symbol]
            except Exception as ex:
                logger.warning("No se pudo obtener exchange_info de Binance para precisión: %s", ex)

        # Fallbacks seguros por par común
        if "BTC" in binance_symbol:
            qty_prec, price_prec, min_qty = 3, 2, 0.001
        elif "ETH" in binance_symbol:
            qty_prec, price_prec, min_qty = 3, 2, 0.001
        elif any(k in binance_symbol for k in ["SOL", "BNB"]):
            qty_prec, price_prec, min_qty = 2, 2, 0.01
        elif any(k in binance_symbol for k in ["DOGE", "XRP", "ADA"]):
            qty_prec, price_prec, min_qty = 0, 4, 1.0
        else:
            qty_prec, price_prec, min_qty = 3, 2, 0.001
        step_size_val = min_qty if min_qty > 0 else 10 ** (-qty_prec)

        self._symbol_precision_cache[binance_symbol] = (qty_prec, price_prec, min_qty, step_size_val)
        return (qty_prec, price_prec, min_qty, step_size_val)

    def get_min_notional(self, symbol: str) -> float:
        """Valor mínimo por orden (cantidad × precio, en USDT) que exige Binance para el símbolo; 0.0 si se desconoce."""
        self.get_symbol_precisions(symbol)
        return float(getattr(self, '_min_notional_cache', {}).get(symbol.replace("/", "").upper(), 0.0))

    def format_quantity(self, symbol: str, quantity: float) -> float:
        """Ajusta la cantidad al STEP SIZE real del exchange (no solo redondea decimales):
        Binance rechaza (error LOT_SIZE) cualquier cantidad que no sea un múltiplo exacto del
        step size. Se redondea siempre HACIA ABAJO al múltiplo válido más cercano para no
        exceder el balance/margen disponible."""
        qty_prec, _, min_qty, step_size = self.get_symbol_precisions(symbol)
        quantity = float(quantity)
        if step_size and step_size > 0:
            steps = math.floor((quantity + 1e-9) / step_size)
            formatted = round(steps * step_size, max(qty_prec, 0))
        elif qty_prec == 0:
            formatted = float(int(quantity))
        else:
            formatted = round(quantity, qty_prec)
        return max(formatted, min_qty)

    def format_price(self, symbol: str, price: float) -> float:
        """Ajusta y redondea el precio a la precisión del exchange."""
        _, price_prec, _, _ = self.get_symbol_precisions(symbol)
        return round(float(price), price_prec)

    def _recover_order_after_ambiguous_error(self, binance_symbol: str, client_order_id: str, exc: Exception) -> Optional[dict]:
        """
        Ante un fallo AMBIGUO al enviar una orden (timeout de red, conexión perdida — no un
        rechazo explícito de Binance como -2019 margen insuficiente o -4164 notional mínimo),
        consulta a Binance por `origClientOrderId` antes de asumir que la orden nunca se creó.

        Un timeout puede significar que Binance procesó la orden pero la respuesta nunca llegó:
        sin esta comprobación, la app la marcaría como fallida y —si algo más tarde reintentara—
        podría enviar una segunda orden real duplicada. Con el `clientOrderId` original (nunca se
        reintenta con uno nuevo) se puede confirmar con certeza si existe o no.

        Devuelve la orden real si Binance la tiene, o None si de verdad no se creó (o no se pudo
        determinar, p. ej. porque la red también falla en esta consulta).
        """
        if isinstance(exc, BinanceAPIException):
            return None  # rechazo explícito de Binance: no es ambiguo, la orden no se creó
        if not self.client:
            return None
        try:
            existing = self.client.futures_get_order(symbol=binance_symbol, origClientOrderId=client_order_id)
            if existing and existing.get("orderId"):
                logger.warning(
                    "Orden %s recuperada tras un error ambiguo (%s): Binance SÍ la había creado "
                    "(ID: %s, estado: %s). Se trata como enviada, no como fallida.",
                    client_order_id, exc, existing.get("orderId"), existing.get("status"),
                )
                return existing
        except Exception as lookup_e:
            logger.warning(
                "No se pudo confirmar si la orden %s se creó tras un error ambiguo (%s): %s. "
                "Se asume no creada (comportamiento previo).", client_order_id, exc, lookup_e,
            )
        return None

    def _recover_algo_order_after_ambiguous_error(self, binance_symbol: str, client_algo_id: str, exc: Exception) -> Optional[dict]:
        """Misma idea que `_recover_order_after_ambiguous_error`, para las condicionales SL/TP
        (Algo Orders): no tienen un lookup directo por client id, así que se busca entre las
        abiertas del símbolo por `clientAlgoId`."""
        if isinstance(exc, BinanceAPIException):
            return None
        if not self.client:
            return None
        try:
            for algo in (self.client.futures_get_open_algo_orders(symbol=binance_symbol) or []):
                if algo.get("clientAlgoId") == client_algo_id:
                    logger.warning(
                        "Orden condicional %s recuperada tras un error ambiguo (%s): Binance SÍ la "
                        "había creado (algoId: %s). Se trata como enviada, no como fallida.",
                        client_algo_id, exc, algo.get("algoId"),
                    )
                    return algo
        except Exception as lookup_e:
            logger.warning(
                "No se pudo confirmar si la condicional %s se creó tras un error ambiguo (%s): %s. "
                "Se asume no creada (comportamiento previo).", client_algo_id, exc, lookup_e,
            )
        return None

    def place_futures_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        verify_execution: bool = True
    ) -> Tuple[Optional[dict], Optional[str]]:
        """
        Envía una orden (MARKET o LIMIT) a Binance Futures y verifica su ejecución en el exchange.
        Aplica obligatoriamente el Candado de Seguridad (Dual-Lock) y los Guardarraíles Cuantitativos.
        """
        binance_symbol = symbol.replace("/", "").upper()
        binance_side = "BUY" if side.lower() == "long" else "SELL"

        # ── CANDADO DE SEGURIDAD OPERATIVA (Primera línea de defensa) ──
        if not self.use_testnet and not is_real_trading_enabled():
            return None, (
                "⛔ BLOQUEO DE SEGURIDAD OPERATIVA: La cuenta Real está en MODO SOLO LECTURA. "
                "El candado de trading con dinero real está cerrado."
            )

        if not self.api_key or not self.api_secret:
            return None, "API Key o Secret no configuradas en .env"

        if quantity <= 0:
            return None, "Cantidad inválida (debe ser mayor a 0)"

        # ── GUARDARRAÍLES CUANTITATIVOS ──
        # Se consulta el apalancamiento REAL configurado en Binance para este simbolo (no se
        # asume 1x): un leverage=1 fijo hacia el guardarrail de "apalancamiento maximo" lo
        # dejaba inerte para siempre, sin importar el limite que el usuario configurara.
        ref_price = price if (price and price > 0) else self.get_symbol_price(binance_symbol)
        slippage_ref = self.get_reference_price(binance_symbol, binance_side, price, order_type)
        real_leverage = self.get_symbol_leverage(binance_symbol)
        avail_balance = self.get_available_balance("USDT")
        allowed, guardrail_err = validate_order_guardrails(
            symbol=binance_symbol,
            quantity=quantity,
            price=ref_price,
            leverage=real_leverage,
            use_testnet=self.use_testnet,
            available_balance_usd=avail_balance if avail_balance > 0 else None
        )
        if not allowed:
            logger.warning("ORDEN INTERCEPTADA POR SEGURIDAD [%s]: %s", binance_symbol, guardrail_err)
            return None, guardrail_err

        # Ajuste de precisión dinámico según especificación del par en Binance
        qty = self.format_quantity(binance_symbol, quantity)

        if not self.client:
            return None, "Cliente de Binance no disponible (restricción geográfica o red)"

        o_type = order_type.upper()
        client_order_id = _new_client_order_id()
        try:
            params = {
                "symbol": binance_symbol,
                "side": binance_side,
                "type": o_type,
                "quantity": qty,
                "newClientOrderId": client_order_id
            }
            if o_type == "LIMIT":
                if price is None or price <= 0:
                    return None, "Precio límite requerido para órdenes LIMIT"
                params["price"] = self.format_price(binance_symbol, price)
                params["timeInForce"] = "GTC"

            order = self.client.futures_create_order(**params)
            if not order.get("orderId"):
                # Ver auditoría 2026-09-18: python-binance no lanza excepción si Binance
                # responde 200 OK con cuerpo vacío (binance/client.py:_handle_response),
                # así que sin esta validación la app registraría SENT_OK para una orden
                # que Binance nunca llegó a crear.
                raise RuntimeError(f"Binance devolvió una respuesta vacía/sin orderId para la orden {o_type}: {order!r}")
            order_id = order.get("orderId")
            initial_status = str(order.get("status", "")).upper()
            logger.info("Orden %s (%s) creada en Binance Futures. ID: %s, Estado inicial: %s", o_type, binance_side, order_id, initial_status)
            _log_order_to_ledger(
                bot_id=self.bot_id,
                symbol=binance_symbol, side=binance_side, action="OPEN", order_type=o_type,
                requested_qty=qty, requested_price=price, reference_price=slippage_ref, use_testnet=self.use_testnet,
                status="SENT_OK", binance_order_id=order_id, client_order_id=client_order_id,
                exchange_status=initial_status, executed_qty=order.get("executedQty"),
                avg_price=order.get("avgPrice"),
            )

            if verify_execution and order_id:
                expected = ["FILLED"] if o_type == "MARKET" else ["NEW", "PARTIALLY_FILLED", "FILLED"]
                
                if initial_status not in expected:
                    ok, verified_order, v_err = self.verify_order_status(
                        symbol=binance_symbol, order_id=order_id, expected_statuses=expected, max_attempts=4, delay_seconds=0.5
                    )
                    if not ok:
                        return verified_order or order, v_err or f"Orden rechazada o fallida en Binance (Estado: {initial_status})"
                    return verified_order, None
                else:
                    return order, None

            return order, None
        except Exception as e:
            logger.error("Error enviando orden %s a Binance Futures: %s", o_type, e)
            recovered = self._recover_order_after_ambiguous_error(binance_symbol, client_order_id, e)
            if recovered:
                _log_order_to_ledger(
                    bot_id=self.bot_id,
                    symbol=binance_symbol, side=binance_side, action="OPEN", order_type=o_type,
                    requested_qty=qty, requested_price=price, reference_price=slippage_ref, use_testnet=self.use_testnet,
                    status="SENT_OK", binance_order_id=recovered.get("orderId"), client_order_id=client_order_id,
                    exchange_status=str(recovered.get("status") or "").upper(), executed_qty=recovered.get("executedQty"),
                    avg_price=recovered.get("avgPrice"),
                )
                return recovered, None
            _log_order_to_ledger(
                bot_id=self.bot_id,
                symbol=binance_symbol, side=binance_side, action="OPEN", order_type=o_type,
                requested_qty=qty, requested_price=price, reference_price=slippage_ref, use_testnet=self.use_testnet,
                status="SEND_FAILED", client_order_id=client_order_id, error=str(e),
            )
            return None, format_binance_error(e)

    def close_futures_position(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        verify_execution: bool = True,
        reduce_only: bool = True,
    ) -> Tuple[Optional[dict], Optional[str]]:
        """
        Cierra una posición en Binance Futures con orden contraria y verifica su ejecución en el exchange.
        Aplica el Candado de Seguridad de cuenta real.

        reduce_only=False: la orden se envía tal cual, por la cantidad EXACTA indicada. Los bots lo usan
        porque Binance (modo one-way) solo tiene una posición NETA por símbolo: con dos bots operando el
        mismo símbolo, un `reduceOnly` se recorta (o se rechaza con -2022) contra la posición neta y no
        contra la del bot, y su cierre no se ejecuta por la cantidad que le corresponde.
        """
        binance_symbol = symbol.replace("/", "").upper()
        # ── CANDADO DE SEGURIDAD OPERATIVA (Primera línea de defensa) ──
        if not self.use_testnet and not is_real_trading_enabled():
            return None, "⛔ BLOQUEO DE SEGURIDAD OPERATIVA: La cuenta Real está en MODO SOLO LECTURA."

        if not self.api_key or not self.api_secret:
            return None, "API Key o Secret no configuradas en .env"

        if quantity <= 0:
            return None, "Cantidad inválida (debe ser mayor a 0)"

        qty = self.format_quantity(binance_symbol, quantity)
        close_side = "SELL" if side.lower() == "long" else "BUY"
        # Precio de referencia para medir slippage en la reconciliación (idéntico criterio
        # que place_futures_order: usa el precio LIMIT pedido si existe, si no consulta el
        # último precio de mercado justo antes de enviar el cierre).
        close_ref = self.get_reference_price(binance_symbol, close_side, price, order_type)

        o_type = order_type.upper()
        client_order_id = _new_client_order_id()
        try:
            params = {
                "symbol": binance_symbol,
                "side": close_side,
                "type": o_type,
                "quantity": qty,
                "newClientOrderId": client_order_id
            }
            if reduce_only:
                params["reduceOnly"] = True
            if o_type == "LIMIT":
                if price is not None and price > 0:
                    params["price"] = self.format_price(binance_symbol, price)
                    params["timeInForce"] = "GTC"
                else:
                    params["type"] = "MARKET"

            if not self.client:
                return None, "Cliente de Binance no disponible"

            order = self.client.futures_create_order(**params)
            if not order.get("orderId"):
                # Ver auditoría 2026-09-18 (misma causa que en place_futures_order).
                raise RuntimeError(f"Binance devolvió una respuesta vacía/sin orderId para el cierre {params['type']}: {order!r}")
            order_id = order.get("orderId")
            initial_status = str(order.get("status", "")).upper()
            logger.info("Cierre de posición %s (%s) enviado a Binance Futures. ID: %s, Estado inicial: %s", o_type, close_side, order_id, initial_status)
            _log_order_to_ledger(
                bot_id=self.bot_id,
                symbol=binance_symbol, side=close_side, action="CLOSE", order_type=params["type"],
                requested_qty=qty, requested_price=price, reference_price=close_ref, use_testnet=self.use_testnet,
                status="SENT_OK", binance_order_id=order_id, client_order_id=client_order_id,
                exchange_status=initial_status, executed_qty=order.get("executedQty"),
                avg_price=order.get("avgPrice"),
            )

            if verify_execution and order_id:
                expected = ["FILLED"] if params["type"] == "MARKET" else ["NEW", "PARTIALLY_FILLED", "FILLED"]
                if initial_status not in expected:
                    ok, verified_order, v_err = self.verify_order_status(
                        symbol=binance_symbol, order_id=order_id, expected_statuses=expected, max_attempts=4, delay_seconds=0.5
                    )
                    if not ok:
                        return verified_order or order, v_err or f"Orden de cierre rechazada o no completada (Estado: {initial_status})"
                    return verified_order, None
                else:
                    return order, None

            return order, None
        except Exception as e:
            logger.error("Error cerrando posición en Binance Futures: %s", e)
            recovered = self._recover_order_after_ambiguous_error(binance_symbol, client_order_id, e)
            if recovered:
                _log_order_to_ledger(
                    bot_id=self.bot_id,
                    symbol=binance_symbol, side=close_side, action="CLOSE", order_type=o_type,
                    requested_qty=qty, requested_price=price, reference_price=close_ref, use_testnet=self.use_testnet,
                    status="SENT_OK", binance_order_id=recovered.get("orderId"), client_order_id=client_order_id,
                    exchange_status=str(recovered.get("status") or "").upper(), executed_qty=recovered.get("executedQty"),
                    avg_price=recovered.get("avgPrice"),
                )
                return recovered, None
            _log_order_to_ledger(
                bot_id=self.bot_id,
                symbol=binance_symbol, side=close_side, action="CLOSE", order_type=o_type,
                requested_qty=qty, requested_price=price, reference_price=close_ref, use_testnet=self.use_testnet,
                status="SEND_FAILED", client_order_id=client_order_id, error=str(e),
            )
            return None, format_binance_error(e)

    def place_futures_sl_tp(
        self,
        symbol: str,
        side: str,
        quantity: float,
        sl_price: Optional[float] = None,
        tp_price: Optional[float] = None,
        sl_order_type: str = "LIMIT",
        tp_order_type: str = "LIMIT",
        reduce_only: bool = True,
    ) -> Dict[str, Any]:
        """Coloca órdenes condicionales de Stop Loss y Take Profit en Binance Futures protegidas por el Candado de Seguridad.

        reduce_only=False: ver close_futures_position. Sin reduceOnly, el SL/TP de un bot actúa por SU cantidad
        aunque la posición neta de la cuenta sea de signo contrario (otro bot en el mismo símbolo); a cambio
        Binance ya no cancela solo la pata sobrante, así que quien llama debe cancelarla (y verificarlo)."""
        if not self.api_key or not self.api_secret:
            return {"sl_order": None, "tp_order": None, "error": "Credenciales no configuradas"}

        if not self.use_testnet and not is_real_trading_enabled():
            return {"sl_order": None, "tp_order": None, "error": "⛔ BLOQUEO: Cuenta Real en MODO SOLO LECTURA."}

        binance_symbol = symbol.replace("/", "").upper()
        close_side = "SELL" if side.lower() == "long" else "BUY"
        qty = self.format_quantity(binance_symbol, quantity)

        results = {"sl_order": None, "tp_order": None, "errors": []}

        # 1. Take Profit
        if tp_price and tp_price > 0:
            tp_client_order_id = _new_client_order_id()
            try:
                tp_type = "TAKE_PROFIT" if tp_order_type.upper() == "LIMIT" else "TAKE_PROFIT_MARKET"
                tp_params = {
                    "symbol": binance_symbol,
                    "side": close_side,
                    "type": tp_type,
                    "stopPrice": self.format_price(binance_symbol, tp_price),
                    "quantity": qty,
                    "newClientOrderId": tp_client_order_id
                }
                if reduce_only:
                    tp_params["reduceOnly"] = True
                if tp_type == "TAKE_PROFIT":
                    tp_params["price"] = self.format_price(binance_symbol, tp_price)
                    tp_params["timeInForce"] = "GTC"

                tp_res = self.client.futures_create_order(**tp_params)
                tp_ref_id = tp_res.get("orderId") or tp_res.get("algoId")
                if not tp_ref_id:
                    # python-binance no lanza excepción si Binance responde 200 OK con cuerpo
                    # vacío (ver binance/client.py:_handle_response, `if response.text == "":
                    # return {}`) — algo que Testnet hace en cortes de red/timeouts parciales.
                    # Sin esta validación, la app confiaba en "no hubo excepción" = "la orden
                    # se creó" y registraba SENT_OK aunque Binance nunca haya creado la orden
                    # real, dejando la posición sin protección de Take Profit mientras el
                    # ledger mentía que sí se había enviado (ver auditoría 2026-09-18).
                    # NOTA: las órdenes condicionales TP/SL en Futures se enrutan como "Algo
                    # Orders" y devuelven `algoId` en vez de `orderId` (igual que el resto de
                    # este cliente ya asume en cancel_all_open_orders/futures_get_open_algo_orders
                    # más abajo) — por eso se comprueba también algoId antes de dar por fallida
                    # una orden que Binance sí aceptó.
                    raise RuntimeError(f"Binance devolvió una respuesta vacía/sin orderId ni algoId para la orden TP: {tp_res!r}")
                results["tp_order"] = tp_res
                logger.info("Orden TP (%s) enviada a Binance: %s", tp_type, tp_res)
                _log_order_to_ledger(
                    bot_id=self.bot_id,
                    symbol=binance_symbol, side=close_side, action="TAKE_PROFIT", order_type=tp_type,
                    requested_qty=qty, requested_price=tp_price, use_testnet=self.use_testnet,
                    status="SENT_OK", binance_order_id=tp_ref_id, client_order_id=tp_client_order_id,
                    exchange_status=str(tp_res.get("status") or tp_res.get("algoStatus") or "").upper(),
                )
            except Exception as e:
                logger.warning("No se pudo colocar orden TP en Binance: %s", e)
                recovered = self._recover_algo_order_after_ambiguous_error(binance_symbol, tp_client_order_id, e)
                if recovered:
                    results["tp_order"] = recovered
                    _log_order_to_ledger(
                        bot_id=self.bot_id,
                        symbol=binance_symbol, side=close_side, action="TAKE_PROFIT",
                        order_type=str(recovered.get("orderType") or tp_order_type.upper()),
                        requested_qty=qty, requested_price=tp_price, use_testnet=self.use_testnet,
                        status="SENT_OK", binance_order_id=recovered.get("algoId"), client_order_id=tp_client_order_id,
                        exchange_status=str(recovered.get("algoStatus") or "").upper(),
                    )
                else:
                    results["errors"].append(f"TP Error: {e}")
                    _log_order_to_ledger(
                        bot_id=self.bot_id,
                        symbol=binance_symbol, side=close_side, action="TAKE_PROFIT", order_type=tp_order_type.upper(),
                        requested_qty=qty, requested_price=tp_price, use_testnet=self.use_testnet,
                        status="SEND_FAILED", client_order_id=tp_client_order_id, error=str(e),
                    )

        # 2. Stop Loss
        if sl_price and sl_price > 0:
            sl_client_order_id = _new_client_order_id()
            try:
                sl_type = "STOP" if sl_order_type.upper() == "LIMIT" else "STOP_MARKET"
                sl_params = {
                    "symbol": binance_symbol,
                    "side": close_side,
                    "type": sl_type,
                    "stopPrice": self.format_price(binance_symbol, sl_price),
                    "quantity": qty,
                    "newClientOrderId": sl_client_order_id
                }
                if reduce_only:
                    sl_params["reduceOnly"] = True
                if sl_type == "STOP":
                    sl_params["price"] = self.format_price(binance_symbol, sl_price)
                    sl_params["timeInForce"] = "GTC"

                sl_res = self.client.futures_create_order(**sl_params)
                sl_ref_id = sl_res.get("orderId") or sl_res.get("algoId")
                if not sl_ref_id:
                    # Ver comentario equivalente en la rama de Take Profit arriba: Binance
                    # puede responder 200 OK con cuerpo vacío, y python-binance no lo trata
                    # como error (auditoría 2026-09-18); también se acepta algoId, ya que las
                    # órdenes condicionales se enrutan como Algo Orders (ver nota en la rama TP).
                    raise RuntimeError(f"Binance devolvió una respuesta vacía/sin orderId ni algoId para la orden SL: {sl_res!r}")
                results["sl_order"] = sl_res
                logger.info("Orden SL (%s) enviada a Binance: %s", sl_type, sl_res)
                _log_order_to_ledger(
                    bot_id=self.bot_id,
                    symbol=binance_symbol, side=close_side, action="STOP_LOSS", order_type=sl_type,
                    requested_qty=qty, requested_price=sl_price, use_testnet=self.use_testnet,
                    status="SENT_OK", binance_order_id=sl_ref_id, client_order_id=sl_client_order_id,
                    exchange_status=str(sl_res.get("status") or sl_res.get("algoStatus") or "").upper(),
                )
            except Exception as e:
                logger.warning("No se pudo colocar orden SL en Binance: %s", e)
                recovered = self._recover_algo_order_after_ambiguous_error(binance_symbol, sl_client_order_id, e)
                if recovered:
                    results["sl_order"] = recovered
                    _log_order_to_ledger(
                        bot_id=self.bot_id,
                        symbol=binance_symbol, side=close_side, action="STOP_LOSS",
                        order_type=str(recovered.get("orderType") or sl_order_type.upper()),
                        requested_qty=qty, requested_price=sl_price, use_testnet=self.use_testnet,
                        status="SENT_OK", binance_order_id=recovered.get("algoId"), client_order_id=sl_client_order_id,
                        exchange_status=str(recovered.get("algoStatus") or "").upper(),
                    )
                else:
                    results["errors"].append(f"SL Error: {e}")
                    _log_order_to_ledger(
                        bot_id=self.bot_id,
                        symbol=binance_symbol, side=close_side, action="STOP_LOSS", order_type=sl_order_type.upper(),
                        requested_qty=qty, requested_price=sl_price, use_testnet=self.use_testnet,
                        status="SEND_FAILED", client_order_id=sl_client_order_id, error=str(e),
                    )

        return results

    def get_available_balance(self, asset: str = "USDT") -> float:
        """Consulta el margen disponible REAL de la cuenta de Futuros para un activo (endpoint
        ligero, sin traer posiciones/ordenes). Usado por los guardarraíles para verificar que
        una orden no exceda el capital realmente disponible en Binance."""
        if not self.client or not self.api_key or not self.api_secret:
            return 0.0
        try:
            balances = self.client.futures_account_balance()
            for b in balances:
                if b.get("asset", "").upper() == asset.upper():
                    return float(b.get("availableBalance", 0.0))
        except Exception as e:
            logger.debug("Error consultando balance disponible de %s: %s", asset, e)
        return 0.0

    def get_symbol_leverage(self, symbol: str) -> int:
        """Consulta el apalancamiento REAL configurado en Binance para un simbolo, sin importar
        si hay o no posicion abierta (el leverage es una configuracion de cuenta por simbolo).
        Usado por los guardarraíles de seguridad para no asumir 1x cuando la cuenta real puede
        tener un apalancamiento mucho mayor configurado."""
        if not self.client or not self.api_key or not self.api_secret:
            return 1
        try:
            binance_symbol = symbol.replace("/", "").upper()
            info = self.client.futures_position_information(symbol=binance_symbol)
            if info:
                return int(float(info[0].get("leverage", 1)))
        except Exception as e:
            logger.debug("Error consultando apalancamiento real de %s: %s", symbol, e)
        return 1

    def get_open_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Devuelve todas las posiciones actualmente abiertas con positionAmt != 0 en Binance Futures."""
        if not self.client or not self.api_key or not self.api_secret:
            return []
        try:
            params = {}
            if symbol:
                params["symbol"] = symbol.replace("/", "").upper()
            pos_list = self.client.futures_position_information(**params)
            open_pos = []
            for p in pos_list:
                amt = float(p.get("positionAmt", 0.0))
                if amt != 0:
                    open_pos.append(p)
            return open_pos
        except Exception as e:
            logger.debug("Error obteniendo posiciones abiertas de Binance: %s", e)
            return []

    # ── Órdenes propias de un bot (referencias) ─────────────────────────────
    # Con varios bots operando el mismo símbolo, "todas las órdenes del símbolo" NO son del bot:
    # cancelar en masa (cancel_all_open_orders) borraba el SL/TP de los demás. Cada bot conoce las
    # referencias de SUS órdenes de protección y solo actúa sobre esas.

    @staticmethod
    def order_ref(order: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Referencia estable {'kind': 'algo'|'order', 'id': int} de una orden devuelta por Binance."""
        if not order:
            return None
        if order.get("algoId"):
            return {"kind": "algo", "id": int(order["algoId"])}
        if order.get("orderId"):
            return {"kind": "order", "id": int(order["orderId"])}
        return None

    def get_open_order_refs(self, symbol: str) -> Optional[set]:
        """Conjunto {(kind, id)} de órdenes abiertas (estándar y condicionales) del símbolo.
        None si no se pudo consultar (el llamador NO debe asumir que no hay órdenes)."""
        if not self.client:
            return None
        binance_symbol = symbol.replace("/", "").upper()
        try:
            refs = {("order", int(o["orderId"])) for o in (self.client.futures_get_open_orders(symbol=binance_symbol) or [])}
            refs |= {("algo", int(a["algoId"])) for a in (self.client.futures_get_open_algo_orders(symbol=binance_symbol) or [])}
            return refs
        except Exception as e:
            logger.warning("No se pudieron listar las órdenes abiertas de %s: %s", binance_symbol, e)
            return None

    def get_order_ref_state(self, symbol: str, ref: Dict[str, Any]) -> Dict[str, Any]:
        """
        Estado normalizado de UNA orden propia. 'state' es uno de:
        OPEN | FILLED | TRIGGERED (disparada, aún sin ejecutar) | CANCELED | EXPIRED | REJECTED | UNKNOWN.
        Para condicionales disparadas se consulta la orden real que generaron (actualOrderId): la
        respuesta de Algo Orders no trae cantidad ni precio ejecutados.
        """
        binance_symbol = symbol.replace("/", "").upper()
        try:
            if ref["kind"] == "algo":
                algo = self.client.futures_get_algo_order(algoId=ref["id"])
                status = str(algo.get("algoStatus") or "").upper()
                if status in ("NEW", "WORKING"):
                    return {"state": "OPEN"}
                if status in ("CANCELED", "CANCELLED"):
                    return {"state": "CANCELED"}
                if status in ("EXPIRED", "REJECTED"):
                    return {"state": status}
                # TRIGGERING/TRIGGERED: disparada, la orden real se está creando; se trata igual que FINISHED
                # (antes caían en UNKNOWN y el bot no sabía que el SL/TP acababa de ejecutarse).
                if status in ("FINISHED", "TRIGGERING", "TRIGGERED"):
                    actual = algo.get("actualOrderId")
                    if not actual:
                        return {"state": "TRIGGERED"}
                    order = self.client.futures_get_order(symbol=binance_symbol, orderId=int(actual))
                    executed = float(order.get("executedQty") or 0.0)
                    if executed > 0:
                        return {
                            "state": "FILLED", "executed_qty": executed,
                            "avg_price": float(order.get("avgPrice") or algo.get("actualPrice") or 0.0),
                            "actual_order_id": int(actual),
                        }
                    if str(order.get("status", "")).upper() in ("NEW", "PARTIALLY_FILLED"):
                        return {"state": "TRIGGERED", "actual_order_id": int(actual)}
                    return {"state": "CANCELED", "actual_order_id": int(actual)}
                logger.warning("Estado de la condicional %s no reconocido: %r", ref["id"], status)
                return {"state": "UNKNOWN", "raw_status": status}

            order = self.client.futures_get_order(symbol=binance_symbol, orderId=ref["id"])
            status = str(order.get("status", "")).upper()
            executed = float(order.get("executedQty") or 0.0)
            if status == "FILLED":
                return {"state": "FILLED", "executed_qty": executed, "avg_price": float(order.get("avgPrice") or 0.0)}
            if status in ("NEW", "PARTIALLY_FILLED"):
                return {"state": "OPEN", "executed_qty": executed}
            if status in ("CANCELED", "EXPIRED", "REJECTED"):
                return {"state": status}
            return {"state": "UNKNOWN", "raw_status": status}
        except Exception as e:
            logger.warning("No se pudo consultar el estado de la orden %s (%s): %s", ref.get("id"), ref.get("kind"), e)
            return {"state": "UNKNOWN", "error": str(e)}

    def cancel_order_refs(self, symbol: str, refs) -> Tuple[bool, List[str]]:
        """Cancela SOLO las órdenes indicadas. Una orden que ya no existe cuenta como cancelada."""
        # Mismo candado que cancel_all_open_orders: cancelar es una mutación autenticada.
        if not self.use_testnet and not is_real_trading_enabled():
            return False, ["⛔ BLOQUEO: la cuenta Real está en MODO SOLO LECTURA"]
        if not self.client:
            return False, ["Cliente de Binance no disponible"]
        binance_symbol = symbol.replace("/", "").upper()
        errors: List[str] = []
        for ref in refs or []:
            if not ref:
                continue
            try:
                if ref["kind"] == "algo":
                    self.client.futures_cancel_algo_order(algoId=ref["id"])
                else:
                    self.client.futures_cancel_order(symbol=binance_symbol, orderId=ref["id"])
            except BinanceAPIException as e:
                text = str(e).lower()
                if e.code in (-2011, -2013) or "unknown order" in text or "does not exist" in text or "not found" in text:
                    continue  # ya no existe / ya estaba cancelada
                errors.append(f"{ref['kind']} {ref['id']}: {e}")
            except Exception as e:
                errors.append(f"{ref['kind']} {ref['id']}: {e}")
        return (not errors), errors

    def cancel_all_open_orders(self, symbol: str) -> Tuple[bool, Optional[str]]:
        """Cancela todas las órdenes abiertas estándar y condicionales (algo SL/TP) pendientes en Binance Futures."""
        # ── CANDADO DE SEGURIDAD OPERATIVA ──
        # Cancelar es una mutación autenticada tan real como colocar una orden: si el
        # candado de cuenta real está cerrado, tampoco debe poder tocar (y desproteger)
        # órdenes SL/TP reales ya colocadas. Antes esta función no tenía ninguna
        # comprobación del candado, permitiendo cancelar órdenes reales (vía MCP/API/UI)
        # incluso en Modo Solo Lectura.
        if not self.use_testnet and not is_real_trading_enabled():
            return False, (
                "⛔ BLOQUEO DE SEGURIDAD OPERATIVA: La cuenta Real está en MODO SOLO LECTURA. "
                "El candado de trading con dinero real está cerrado."
            )

        if not self.api_key or not self.api_secret:
            return False, "API Key o Secret no configuradas en .env"
        try:
            binance_symbol = symbol.replace("/", "").upper()
            
            # 1. Cancelación masiva de órdenes estándar
            try:
                self.client.futures_cancel_all_open_orders(symbol=binance_symbol)
            except Exception as e_mass:
                logger.debug("futures_cancel_all_open_orders note: %s", e_mass)

            # 2. Cancelación masiva de órdenes ALGO / CONDICIONALES (Stop Loss y Take Profit)
            try:
                self.client.futures_cancel_all_algo_open_orders(symbol=binance_symbol)
            except Exception as e_algo_mass:
                logger.debug("futures_cancel_all_algo_open_orders note: %s", e_algo_mass)

            # 3. Verificación y cancelación individual de cualquier orden estándar residual
            try:
                open_orders = self.client.futures_get_open_orders(symbol=binance_symbol)
                if open_orders:
                    for o in open_orders:
                        oid = o.get('orderId')
                        if oid:
                            try:
                                self.client.futures_cancel_order(symbol=binance_symbol, orderId=oid)
                            except Exception:
                                logger.warning("No se pudo cancelar la orden estándar %s de %s", oid, binance_symbol, exc_info=True)
            except Exception:
                logger.warning("No se pudieron listar las órdenes abiertas de %s para cancelarlas", binance_symbol, exc_info=True)

            # 4. Verificación y cancelación individual de cualquier orden ALGO/CONDICIONAL residual
            try:
                open_algo = self.client.futures_get_open_algo_orders()
                if open_algo:
                    for a in open_algo:
                        if a.get('symbol') == binance_symbol or not binance_symbol:
                            algo_id = a.get('algoId')
                            if algo_id:
                                try:
                                    self.client.futures_cancel_algo_order(algoId=algo_id)
                                except Exception:
                                    logger.warning("No se pudo cancelar la orden algo %s de %s", algo_id, binance_symbol, exc_info=True)
            except Exception:
                logger.warning("No se pudieron listar las órdenes algo abiertas para cancelarlas", exc_info=True)

            # Verificación final: todos los pasos anteriores tragan sus propias excepciones
            # (para intentar el resto de vías de cancelación aunque una falle), asi que sin
            # esta comprobación el metodo reportaba "True, None" incluso si NINGUNA de las
            # cancelaciones tuvo exito realmente (ej. permisos de API insuficientes) — un
            # falso positivo critico para quien lo usa como parte del kill switch.
            remaining = 0
            try:
                still_open = self.client.futures_get_open_orders(symbol=binance_symbol) or []
                remaining = len(still_open)
            except Exception as e_check:
                logger.debug("No se pudo verificar órdenes remanentes tras cancelar %s: %s", binance_symbol, e_check)

            if remaining > 0:
                msg = f"{remaining} orden(es) siguen abiertas en {binance_symbol} tras el intento de cancelación"
                logger.warning(msg)
                return False, msg

            logger.info("Órdenes abiertas y condicionales (Algo) canceladas con éxito para %s", binance_symbol)
            return True, None
        except Exception as e:
            logger.warning("Error cancelando órdenes pendientes para %s: %s", symbol, e)
            return False, str(e)

    def cancel_all_futures_orders(self, symbol: str = "BTCUSDT", use_testnet: Optional[bool] = None) -> Tuple[bool, Optional[str]]:
        """Cancela todas las órdenes abiertas de un símbolo en Binance Futures (compatible con testnet y mainnet)."""
        if use_testnet is not None:
            self.use_testnet = use_testnet
            self.client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi' if use_testnet else 'https://fapi.binance.com/fapi'
        return self.cancel_all_open_orders(symbol)

    def cancel_all_futures_orders_every_symbol(self, use_testnet: Optional[bool] = None) -> Tuple[bool, Optional[str]]:
        """Cancela todas las órdenes abiertas (estándar y condicionales) en TODOS los símbolos con actividad
        en Binance Futures. La API de Binance no ofrece cancelación masiva multi-símbolo en una sola llamada,
        así que se detectan los símbolos con órdenes u posiciones abiertas y se cancela cada uno por separado."""
        if use_testnet is not None:
            self.use_testnet = use_testnet
            self.client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi' if use_testnet else 'https://fapi.binance.com/fapi'

        if not self.client or not self.api_key or not self.api_secret:
            return False, "API Key o Secret no configuradas en .env"

        symbols = set()
        try:
            for o in (self.client.futures_get_open_orders() or []):
                sym = o.get("symbol")
                if sym:
                    symbols.add(sym)
        except Exception as e:
            logger.warning("Error listando órdenes abiertas de todos los símbolos: %s", e)

        try:
            for a in (self.client.futures_get_open_algo_orders() or []):
                sym = a.get("symbol")
                if sym:
                    symbols.add(sym)
        except Exception as e:
            logger.debug("Error listando órdenes algo abiertas de todos los símbolos: %s", e)

        try:
            for p in self.get_open_positions():
                sym = p.get("symbol")
                if sym:
                    symbols.add(sym)
        except Exception as e:
            logger.debug("Error listando posiciones abiertas de todos los símbolos: %s", e)

        if not symbols:
            return True, None

        errors = []
        for sym in symbols:
            ok, err = self.cancel_all_open_orders(sym)
            if not ok:
                errors.append(f"{sym}: {err}")

        if errors:
            return False, "; ".join(errors)
        return True, None

    def get_multi_assets_margin(self) -> bool:
        """Consulta si el Modo Multiactivos (Multi-Assets Margin) está habilitado en Binance Futures."""
        try:
            res = self.client._request_futures_api('get', 'multiAssetsMargin', True, data={})
            return bool(res.get('multiAssetsMargin', False))
        except Exception as e:
            logger.debug("Error consultando multiAssetsMargin: %s", e)
            return False

    def set_multi_assets_margin(self, enabled: bool = True) -> Tuple[bool, Optional[str]]:
        """Habilita o deshabilita el Modo Multiactivos en Binance Futures para usar BTC como colateral global."""
        try:
            val_str = "true" if enabled else "false"
            res = self.client._request_futures_api('post', 'multiAssetsMargin', True, data={'multiAssetsMargin': val_str})
            logger.info("Modo Multiactivos configurado a %s: %s", val_str, res)
            return True, None
        except Exception as e:
            logger.warning("Error configurando multiAssetsMargin: %s", e)
            return False, str(e)

    def get_symbol_price(self, symbol: str = "BTCUSDT") -> float:
        """Obtiene el último precio de mercado de un símbolo en Binance Futures."""
        try:
            if not self.client:
                return 0.0
            binance_symbol = symbol.replace("/", "").upper()
            ticker = self.client.futures_symbol_ticker(symbol=binance_symbol)
            return float(ticker.get('price', 0.0))
        except Exception as e:
            logger.debug("Error obteniendo precio de %s: %s", symbol, e)
            return 0.0

    def get_reference_price(self, symbol: str, side: str, limit_price: Optional[float] = None,
                            order_type: str = "MARKET") -> float:
        """
        Precio contra el que el reconciliador mide el deslizamiento de una orden: el LIMIT pedido si lo hay; para
        MARKET, el mejor precio del lado que la orden cruza (ask al comprar, bid al vender) tomado justo antes de
        enviarla. Antes se usaba el precio de la vela/señal (`price`), que mezclaba el spread y la latencia con el
        deslizamiento real y castigaba siempre al lado vendedor. Sin libro disponible cae al último precio.
        """
        if order_type.upper() == "LIMIT" and limit_price and limit_price > 0:
            return float(limit_price)
        binance_symbol = symbol.replace("/", "").upper()
        try:
            if self.client:
                book = self.client.futures_orderbook_ticker(symbol=binance_symbol)
                book = book[0] if isinstance(book, list) and book else book
                px = float(book.get("askPrice" if side.upper() == "BUY" else "bidPrice") or 0.0)
                if px > 0:
                    return px
        except Exception as e:
            logger.debug("No se pudo leer el libro de %s para la referencia de deslizamiento: %s", binance_symbol, e)
        return self.get_symbol_price(binance_symbol) or (float(limit_price) if limit_price else 0.0)

    # ──────────────────────────────────────────────────────────────
    # Métodos de Diagnóstico y Pruebas de Conexión
    # ──────────────────────────────────────────────────────────────

    def test_connection_and_orders(self, symbol: str = "BTC/USDT") -> Dict[str, Any]:
        """Ejecuta una prueba completa de ciclo de orden en Binance Futures Testnet."""
        return self.test_testnet_connection(symbol=symbol)

    def test_testnet_connection(self, symbol: str = "BTC/USDT") -> Dict[str, Any]:
        """Diagnóstico completo de conectividad y órdenes en Binance Futures Testnet."""
        t0 = time.time()
        results = {
            "network": "Binance Futures Testnet",
            "api_keys_configured": bool(self.api_key and self.api_secret),
            "ping_ok": False,
            "latency_ms": 0,
            "account_balance_usdt": None,
            "assets_count": 0,
            "buy_order": None,
            "sell_order": None,
            "success": False,
            "error": None
        }

        if not results["api_keys_configured"]:
            results["error"] = "BINANCE_API_KEY o BINANCE_SECRET_KEY no configuradas en .env"
            return results

        try:
            # 1. Testnet Futures Client
            test_client = ResilientClient(self.api_key, self.api_secret, testnet=True, ping=False, requests_params={'timeout': 10})
            test_client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'
            _sync_timestamp_offset(test_client, use_futures=True)

            # 2. Ping & Account query
            acc = test_client.futures_account()
            latency = int((time.time() - t0) * 1000)
            results["latency_ms"] = latency
            results["ping_ok"] = True

            usdt_assets = [a for a in acc.get('assets', []) if a.get('asset') == 'USDT']
            results["account_balance_usdt"] = float(usdt_assets[0].get('walletBalance', 0.0)) if usdt_assets else 0.0
            results["assets_count"] = len([a for a in acc.get('assets', []) if float(a.get('walletBalance', 0)) > 0])

            # 3. Orden de Compra de Prueba (0.001 BTC)
            binance_symbol = symbol.replace("/", "").upper()
            buy_res = test_client.futures_create_order(
                symbol=binance_symbol,
                side="BUY",
                type="MARKET",
                quantity=0.001
            )
            results["buy_order"] = {
                "orderId": buy_res.get('orderId'),
                "symbol": buy_res.get('symbol'),
                "side": buy_res.get('side'),
                "origQty": buy_res.get('origQty'),
                "status": buy_res.get('status'),
                "avgPrice": buy_res.get('avgPrice')
            }

            # 4. Orden de Cierre de Prueba (0.001 BTC reduceOnly)
            sell_res = test_client.futures_create_order(
                symbol=binance_symbol,
                side="SELL",
                type="MARKET",
                quantity=0.001,
                reduceOnly=True
            )
            results["sell_order"] = {
                "orderId": sell_res.get('orderId'),
                "symbol": sell_res.get('symbol'),
                "side": sell_res.get('side'),
                "origQty": sell_res.get('origQty'),
                "status": sell_res.get('status'),
                "avgPrice": sell_res.get('avgPrice')
            }

            results["success"] = True
            return results

        except Exception as e:
            logger.error("Error en testnet connection test: %s", e)
            results["error"] = format_binance_error(e)
            results["latency_ms"] = int((time.time() - t0) * 1000)
            return results

    def test_mainnet_connection(self) -> Dict[str, Any]:
        """Diagnóstico de conectividad con Binance Real (Mainnet)."""
        t0 = time.time()
        results = {
            "network": "Binance Real (Mainnet)",
            "ping_ok": False,
            "latency_ms": 0,
            "system_status": None,
            "server_time_offset_ms": 0,
            "api_keys_configured": bool(self.api_key and self.api_secret),
            "spot_account_accessible": False,
            "spot_balances_count": 0,
            "success": False,
            "error": None
        }

        try:
            # Cliente público / privado para Mainnet
            main_client = ResilientClient(self.api_key, self.api_secret, testnet=False, ping=False, requests_params={'timeout': 8})

            # 1. Ping
            main_client.ping()
            results["ping_ok"] = True
            results["latency_ms"] = int((time.time() - t0) * 1000)

            # 2. Estado del sistema
            status = main_client.get_system_status()
            results["system_status"] = "Normal (Operativo)" if status.get('status') == 0 else f"Mantenimiento ({status.get('msg')})"

            # 3. Sincronización de hora del servidor
            server_time = main_client.get_server_time().get('serverTime', 0)
            local_time_ms = int(time.time() * 1000)
            results["server_time_offset_ms"] = server_time - local_time_ms
            main_client.timestamp_offset = server_time - local_time_ms

            # 4. Probar acceso a cuenta si las claves están presentes
            if results["api_keys_configured"]:
                try:
                    spot_acc = main_client.get_account()
                    results["spot_account_accessible"] = True
                    balances = [b for b in spot_acc.get('balances', []) if float(b.get('free', 0)) > 0 or float(b.get('locked', 0)) > 0]
                    results["spot_balances_count"] = len(balances)
                except Exception as acc_e:
                    # Las claves del usuario pueden ser exclusivas de Testnet o de Futuros
                    results["spot_account_accessible"] = False
                    results["account_note"] = f"Nota de API: {acc_e}"

            results["success"] = True
            return results

        except Exception as e:
            logger.error("Error en mainnet connection test: %s", e)
            results["error"] = format_binance_error(e)
            results["latency_ms"] = int((time.time() - t0) * 1000)
            return results

    # ──────────────────────────────────────────────────────────────
    # Consulta Completa de Información de la Cuenta
    # ──────────────────────────────────────────────────────────────

    def get_full_account_info(self, use_testnet: bool = True) -> Dict[str, Any]:
        """
        Obtiene toda la información detallada de la cuenta en Binance:
        - Balances y activos (USDT, BTC, ETH, etc.)
        - Posiciones activas en futuros con PnL no realizado y margen
        - Órdenes abiertas pendientes
        - Historial reciente de órdenes ejecutadas
        """
        if use_testnet:
            api_k = os.getenv("BINANCE_TESTNET_API_KEY", "").strip() or os.getenv("BINANCE_API_KEY", "").strip()
            api_s = os.getenv("BINANCE_TESTNET_SECRET_KEY", "").strip() or os.getenv("BINANCE_SECRET_KEY", "").strip()
        else:
            api_k = os.getenv("BINANCE_REAL_API_KEY", "").strip() or os.getenv("BINANCE_API_KEY", "").strip()
            api_s = os.getenv("BINANCE_REAL_SECRET_KEY", "").strip() or os.getenv("BINANCE_SECRET_KEY", "").strip()

        data: Dict[str, Any] = {
            "network": "Binance Futures Testnet" if use_testnet else "Binance Real (Mainnet)",
            "use_testnet": use_testnet,
            "api_keys_configured": bool(api_k and api_s),
            "total_wallet_balance": 0.0,
            "total_unrealized_pnl": 0.0,
            "available_balance": 0.0,
            "total_margin_balance": 0.0,
            "assets": [],
            "positions": [],
            "open_orders": [],
            "recent_trades": [],
            "success": False,
            "error": None
        }

        if not data["api_keys_configured"]:
            env_name = "BINANCE_TESTNET_API_KEY" if use_testnet else "BINANCE_REAL_API_KEY"
            data["error"] = f"No se han configurado {env_name} o BINANCE_API_KEY en el archivo .env"
            return data

        try:
            client = ResilientClient(api_k, api_s, testnet=use_testnet, ping=False, requests_params={'timeout': 10})
            if use_testnet:
                client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'
            else:
                client.FUTURES_URL = 'https://fapi.binance.com/fapi'
            _sync_timestamp_offset(client, use_futures=True)

            # 1. Información de la cuenta de Futuros
            acc = client.futures_account()
            data["total_wallet_balance"] = float(acc.get('totalWalletBalance', 0.0))
            data["total_unrealized_pnl"] = float(acc.get('totalUnrealizedProfit', 0.0))
            data["available_balance"] = float(acc.get('availableBalance', 0.0))
            data["total_margin_balance"] = float(acc.get('totalMarginBalance', 0.0))

            # Obtener precio actual de BTC para conversión multiactivo y estado Multi-Assets
            btc_price = 80000.0
            try:
                btc_ticker = client.futures_symbol_ticker(symbol='BTCUSDT')
                btc_price = float(btc_ticker.get('price', 80000.0))
            except Exception:
                logger.warning("No se pudo obtener el precio de BTCUSDT; se valora con el respaldo fijo de 80000", exc_info=True)
            data["btc_price"] = btc_price

            try:
                mam = client._request_futures_api('get', 'multiAssetsMargin', True, data={})
                data["multi_assets_margin"] = bool(mam.get('multiAssetsMargin', False))
            except Exception:
                data["multi_assets_margin"] = False

            # 2. Filtrar Activos con saldo > 0 y calcular valor en USD
            total_usd_portfolio = 0.0
            for a in acc.get('assets', []):
                wb = float(a.get('walletBalance', 0.0))
                ab = float(a.get('availableBalance', 0.0))
                upnl = float(a.get('unrealizedProfit', 0.0))
                asset = a.get('asset', '')
                if wb > 0 or ab > 0:
                    if asset in ['USDT', 'USDC', 'USD', 'BUSD', 'FDUSD', 'DAI']:
                        usd_val = wb
                    elif asset == 'BTC':
                        usd_val = wb * btc_price
                    else:
                        usd_val = wb
                    total_usd_portfolio += usd_val

                    data["assets"].append({
                        "asset": asset,
                        "wallet_balance": wb,
                        "available_balance": ab,
                        "unrealized_pnl": upnl,
                        "margin_balance": float(a.get('marginBalance', 0.0)),
                        "max_withdraw": float(a.get('maxWithdrawAmount', 0.0)),
                        "usd_value": usd_val
                    })
            data["total_usd_value"] = total_usd_portfolio

            # 3. Filtrar Posiciones Abiertas
            pos_info_map = {}
            try:
                raw_pos_info = client.futures_position_information()
                for rpi in raw_pos_info:
                    s_sym = rpi.get('symbol')
                    if s_sym:
                        pos_info_map[s_sym] = rpi
            except Exception:
                logger.warning("No se pudo leer la información de riesgo de posiciones (positionRisk)", exc_info=True)

            for p in acc.get('positions', []):
                amt = float(p.get('positionAmt', 0.0))
                if amt != 0:
                    sym = p.get('symbol', '')
                    entry = float(p.get('entryPrice', 0.0))
                    upnl = float(p.get('unrealizedProfit', 0.0))
                    im = float(p.get('initialMargin', 0.0))
                    roi_pct = (upnl / im * 100.0) if im > 0 else 0.0
                    
                    rpi = pos_info_map.get(sym, {})
                    mark_p = float(rpi.get('markPrice', 0.0) or 0.0)
                    be_p = float(rpi.get('breakEvenPrice', 0.0) or entry)
                    liq_p = float(rpi.get('liquidationPrice', 0.0) or 0.0)
                    m_type = str(rpi.get('marginType', 'cross')).capitalize()
                    
                    data["positions"].append({
                        "symbol": sym,
                        "symbol_display": f"{sym} Perp",
                        "side": "LONG" if amt > 0 else "SHORT",
                        "positionAmt": amt,
                        "amount": abs(amt),
                        "size_display": f"{abs(amt):.4f} {sym.replace('USDT', '').replace('USDC', '')}",
                        "entry_price": entry,
                        "break_even_price": be_p,
                        "mark_price": mark_p,
                        "liquidation_price": liq_p if liq_p > 0 else None,
                        "leverage": int(p.get('leverage', 1)),
                        "initial_margin": im,
                        "margin_display": f"{im:.2f} USDT ({m_type})",
                        "unrealized_pnl": upnl,
                        "roi_pct": roi_pct,
                        "pnl_display": f"{upnl:+.2f} USDT ({roi_pct:+.2f}%)",
                        "isolated": p.get('isolated', False),
                        "raw_amt": amt
                    })

            # 4. Órdenes Abiertas en Futuros (Estándar y Condicionales Algo)
            try:
                open_o = client.futures_get_open_orders()
                for o in open_o:
                    data["open_orders"].append({
                        "orderId": o.get('orderId'),
                        "symbol": o.get('symbol'),
                        "side": o.get('side'),
                        "type": o.get('type'),
                        "origQty": float(o.get('origQty', 0.0)),
                        "price": float(o.get('price', 0.0)),
                        "stopPrice": float(o.get('stopPrice', 0.0)),
                        "time": o.get('time')
                    })
            except Exception as o_e:
                logger.debug("No se pudieron cargar órdenes estándar abiertas: %s", o_e)

            try:
                open_algo = client.futures_get_open_algo_orders()
                for a in open_algo:
                    data["open_orders"].append({
                        "orderId": a.get('algoId'),
                        "symbol": a.get('symbol'),
                        "side": a.get('side'),
                        "type": f"{a.get('orderType', 'CONDITIONAL')} (Algo)",
                        "origQty": float(a.get('quantity', 0.0)),
                        "price": float(a.get('price', 0.0)),
                        "stopPrice": float(a.get('triggerPrice', 0.0)),
                        "time": a.get('createTime')
                    })
            except Exception as a_e:
                logger.debug("No se pudieron cargar órdenes algo abiertas: %s", a_e)

            # 5. Historial Reciente de Órdenes (BTCUSDT y ETHUSDT)
            try:
                for sym in ['BTCUSDT', 'ETHUSDT']:
                    orders = client.futures_get_all_orders(symbol=sym, limit=15)
                    for o in reversed(orders):
                        data["recent_trades"].append({
                            "orderId": o.get('orderId'),
                            "symbol": o.get('symbol'),
                            "side": o.get('side'),
                            "type": o.get('type'),
                            "origQty": float(o.get('origQty', 0.0)),
                            "executedQty": float(o.get('executedQty', 0.0)),
                            "avgPrice": float(o.get('avgPrice', 0.0)) if o.get('avgPrice') else float(o.get('price', 0.0)),
                            "status": o.get('status'),
                            "time": o.get('time') or o.get('updateTime')
                        })
                # Ordenar por tiempo descendente
                data["recent_trades"].sort(key=lambda x: x.get('time') or 0, reverse=True)
            except Exception as t_e:
                logger.warning("No se pudieron cargar trades recientes: %s", t_e)

            data["success"] = True
            return data

        except Exception as e:
            logger.error("Error al obtener información de la cuenta de Binance: %s", e)
            data["error"] = format_binance_error(e)
            return data

    def cancel_futures_order(self, symbol: str, order_id: int, use_testnet: bool = True) -> Tuple[bool, Optional[str]]:
        """Cancela una orden abierta en Binance Futures."""
        try:
            client = ResilientClient(self.api_key, self.api_secret, testnet=use_testnet, ping=False, requests_params={'timeout': 10})
            if use_testnet:
                client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'
            _sync_timestamp_offset(client, use_futures=True)
            binance_symbol = symbol.replace("/", "").upper()
            res = client.futures_cancel_order(symbol=binance_symbol, orderId=order_id)
            return True, None
        except Exception as e:
            return False, str(e)

    # ──────────────────────────────────────────────────────────────
    # Consulta de Información de Cartera SPOT (Contado)
    # ──────────────────────────────────────────────────────────────

    def get_spot_account_info(self, use_testnet: bool = False) -> Dict[str, Any]:
        """
        Obtiene la información detallada de la cartera SPOT (Contado) de Binance:
        - Balances de activos (free, locked, total)
        - Precios en tiempo real y valoración estimada en USD
        - Órdenes abiertas de Spot
        """
        if use_testnet:
            api_k = os.getenv("BINANCE_TESTNET_API_KEY", "").strip() or os.getenv("BINANCE_API_KEY", "").strip()
            api_s = os.getenv("BINANCE_TESTNET_SECRET_KEY", "").strip() or os.getenv("BINANCE_SECRET_KEY", "").strip()
        else:
            api_k = os.getenv("BINANCE_REAL_API_KEY", "").strip() or os.getenv("BINANCE_API_KEY", "").strip()
            api_s = os.getenv("BINANCE_REAL_SECRET_KEY", "").strip() or os.getenv("BINANCE_SECRET_KEY", "").strip()

        data: Dict[str, Any] = {
            "network": "Binance Spot Testnet" if use_testnet else "Binance Spot Real (Mainnet)",
            "use_testnet": use_testnet,
            "wallet_type": "SPOT",
            "api_keys_configured": bool(api_k and api_s),
            "total_usd_value": 0.0,
            "free_usd_value": 0.0,
            "locked_usd_value": 0.0,
            "assets": [],
            "open_orders": [],
            "success": False,
            "is_permission_error": False,
            "error": None
        }

        if not data["api_keys_configured"]:
            env_name = "BINANCE_TESTNET_API_KEY" if use_testnet else "BINANCE_REAL_API_KEY"
            data["error"] = f"No se han configurado {env_name} o BINANCE_API_KEY en el archivo .env"
            return data

        try:
            client = ResilientClient(api_k, api_s, testnet=use_testnet, ping=False, requests_params={'timeout': 10})
            _sync_timestamp_offset(client, use_futures=False)

            # 1. Obtener datos de la cuenta Spot
            account = client.get_account()

            # 2. Obtener tickers de precios Spot para calcular valor USD
            price_map: Dict[str, float] = {}
            try:
                tickers = client.get_all_tickers()
                for t in tickers:
                    sym = t.get('symbol', '')
                    p = float(t.get('price', 0.0))
                    if sym and p > 0:
                        price_map[sym] = p
            except Exception as pe:
                logger.debug("No se pudieron cargar todos los tickers Spot: %s", pe)

            # 3. Filtrar activos con saldo
            total_usd = 0.0
            free_usd = 0.0
            locked_usd = 0.0
            stables = {'USDT', 'USD', 'USDC', 'BUSD', 'FDUSD', 'DAI'}

            for b in account.get('balances', []):
                free_qty = float(b.get('free', 0.0))
                locked_qty = float(b.get('locked', 0.0))
                total_qty = free_qty + locked_qty

                if total_qty > 1e-8:
                    asset = b.get('asset', '')
                    unit_price = 0.0

                    if asset in stables:
                        unit_price = 1.0
                    elif f"{asset}USDT" in price_map:
                        unit_price = price_map[f"{asset}USDT"]
                    elif f"{asset}USDC" in price_map:
                        unit_price = price_map[f"{asset}USDC"]
                    elif f"{asset}BTC" in price_map and "BTCUSDT" in price_map:
                        unit_price = price_map[f"{asset}BTC"] * price_map["BTCUSDT"]

                    val_usd = total_qty * unit_price
                    f_val = free_qty * unit_price
                    l_val = locked_qty * unit_price

                    total_usd += val_usd
                    free_usd += f_val
                    locked_usd += l_val

                    data["assets"].append({
                        "asset": asset,
                        "free": free_qty,
                        "locked": locked_qty,
                        "total": total_qty,
                        "unit_price_usd": unit_price,
                        "usd_value": val_usd,
                        "free_str": f"{free_qty:,.6f}".rstrip('0').rstrip('.') if free_qty < 1 else f"{free_qty:,.4f}",
                        "locked_str": f"{locked_qty:,.6f}".rstrip('0').rstrip('.') if locked_qty < 1 else f"{locked_qty:,.4f}",
                        "total_str": f"{total_qty:,.6f}".rstrip('0').rstrip('.') if total_qty < 1 else f"{total_qty:,.4f}",
                        "usd_value_str": f"${val_usd:,.2f}"
                    })

            # Ordenar activos por valor USD descendente
            data["assets"].sort(key=lambda x: x["usd_value"], reverse=True)
            data["total_usd_value"] = total_usd
            data["free_usd_value"] = free_usd
            data["locked_usd_value"] = locked_usd

            # 4. Órdenes abiertas en Spot
            try:
                open_orders = client.get_open_orders()
                for o in open_orders:
                    ts = o.get('time')
                    ts_str = "-"
                    if ts:
                        try:
                            from datetime import datetime
                            ts_str = datetime.fromtimestamp(ts / 1000.0).strftime("%d/%m %H:%M:%S")
                        except Exception:
                            ts_str = str(ts)

                    data["open_orders"].append({
                        "orderId": str(o.get('orderId')),
                        "symbol": o.get('symbol'),
                        "side": o.get('side'),
                        "type": o.get('type'),
                        "origQty": float(o.get('origQty', 0.0)),
                        "price": float(o.get('price', 0.0)),
                        "stopPrice": float(o.get('stopPrice', 0.0)),
                        "time": ts,
                        "time_str": ts_str
                    })
            except Exception as oe:
                logger.debug("Error al consultar órdenes abiertas Spot: %s", oe)

            data["success"] = True
            return data

        except Exception as e:
            err_msg = str(e)
            logger.error("Error al obtener información de Spot en Binance: %s", err_msg)
            if "-2015" in err_msg:
                data["is_permission_error"] = True
                if use_testnet:
                    data["error"] = "Tus credenciales pertenecen a Binance Futures Testnet (fapi). Las claves de Futures Testnet no tienen acceso a Spot Testnet (testnet.binance.vision). Para ver Spot se requieren claves con permisos de Spot habilitados."
                else:
                    data["error"] = "La clave API de Binance Real no tiene habilitados permisos de lectura de Spot ('Enable Reading' / 'Spot & Margin Trading')."
            elif "-1021" in err_msg:
                data["error"] = "Desincronización de reloj del sistema con el servidor de Binance (Error -1021)."
            else:
                data["error"] = err_msg
            return data

    def cancel_spot_order(self, symbol: str, order_id: int, use_testnet: bool = False) -> Tuple[bool, Optional[str]]:
        """Cancela una orden abierta en Binance Spot."""
        try:
            client = ResilientClient(self.api_key, self.api_secret, testnet=use_testnet, ping=False, requests_params={'timeout': 10})
            _sync_timestamp_offset(client, use_futures=False)
            binance_symbol = symbol.replace("/", "").upper()
            client.cancel_order(symbol=binance_symbol, orderId=order_id)
            return True, None
        except Exception as e:
            return False, str(e)

    # ──────────────────────────────────────────────────────────────
    # Historial completo de Operativa (Order/Trade/Transaction History)
    # Replica las mismas pestañas que la interfaz real de Binance.
    # ──────────────────────────────────────────────────────────────

    def get_futures_order_history(self, symbol: str = "BTCUSDT", limit: int = 200) -> List[Dict[str, Any]]:
        """Historial completo de órdenes (llenadas, canceladas, expiradas) de Futures para un símbolo.
        La API de Binance Futures (GET /fapi/v1/allOrders) exige un símbolo, igual que en la propia
        web de Binance cuando se filtra por par."""
        if not self.client:
            return []
        binance_symbol = symbol.replace("/", "").upper()
        try:
            orders = self.client.futures_get_all_orders(symbol=binance_symbol, limit=limit)
            result = []
            for o in reversed(orders):
                result.append({
                    "time_str": _fmt_ts(o.get("time")),
                    "time": o.get("time"),
                    "symbol": o.get("symbol"),
                    "type": o.get("type"),
                    "side": o.get("side"),
                    "price": float(o.get("price", 0.0)),
                    "avgPrice": float(o.get("avgPrice", 0.0)),
                    "origQty": float(o.get("origQty", 0.0)),
                    "executedQty": float(o.get("executedQty", 0.0)),
                    "status": o.get("status"),
                    "reduceOnly": bool(o.get("reduceOnly", False)),
                    "orderId": o.get("orderId"),
                })
            return result
        except Exception as e:
            logger.warning("Error obteniendo historial de órdenes Futures %s: %s", binance_symbol, e)
            return []

    def get_futures_trade_history(self, symbol: str = "BTCUSDT", limit: int = 500) -> List[Dict[str, Any]]:
        """Historial de ejecuciones/fills (GET /fapi/v1/userTrades) de Futures para un símbolo."""
        if not self.client:
            return []
        binance_symbol = symbol.replace("/", "").upper()
        try:
            trades = self.client.futures_account_trades(symbol=binance_symbol, limit=limit)
            result = []
            for t in reversed(trades):
                result.append({
                    "time_str": _fmt_ts(t.get("time")),
                    "time": t.get("time"),
                    "symbol": t.get("symbol"),
                    "side": t.get("side"),
                    "price": float(t.get("price", 0.0)),
                    "qty": float(t.get("qty", 0.0)),
                    "quoteQty": float(t.get("quoteQty", 0.0)),
                    "commission": float(t.get("commission", 0.0)),
                    "commissionAsset": t.get("commissionAsset"),
                    "realizedPnl": float(t.get("realizedPnl", 0.0)),
                    "orderId": t.get("orderId"),
                })
            return result
        except Exception as e:
            logger.warning("Error obteniendo historial de trades Futures %s: %s", binance_symbol, e)
            return []

    def get_futures_transaction_history(self, income_type: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
        """Historial de movimientos de la cuenta de Futures (funding, comisiones, PnL realizado,
        transferencias) vía GET /fapi/v1/income. A diferencia de Order/Trade History, este endpoint
        SÍ cubre todos los símbolos a la vez (no requiere filtrar por par)."""
        if not self.client:
            return []
        try:
            kwargs = {"limit": limit}
            if income_type:
                kwargs["incomeType"] = income_type
            income = self.client.futures_income_history(**kwargs)
            result = []
            for i in reversed(income):
                result.append({
                    "time_str": _fmt_ts(i.get("time")),
                    "time": i.get("time"),
                    "symbol": i.get("symbol") or "-",
                    "type": i.get("incomeType"),
                    "income": float(i.get("income", 0.0)),
                    "asset": i.get("asset"),
                    "info": i.get("info", ""),
                })
            return result
        except Exception as e:
            logger.warning("Error obteniendo historial de transacciones Futures: %s", e)
            return []

    def get_spot_order_history(self, symbol: str = "BTCUSDT", limit: int = 200) -> List[Dict[str, Any]]:
        """Historial completo de órdenes Spot (GET /api/v3/allOrders) para un símbolo."""
        if not self.client:
            return []
        binance_symbol = symbol.replace("/", "").upper()
        try:
            orders = self.client.get_all_orders(symbol=binance_symbol, limit=limit)
            result = []
            for o in reversed(orders):
                result.append({
                    "time_str": _fmt_ts(o.get("time")),
                    "time": o.get("time"),
                    "symbol": o.get("symbol"),
                    "type": o.get("type"),
                    "side": o.get("side"),
                    "price": float(o.get("price", 0.0)),
                    "origQty": float(o.get("origQty", 0.0)),
                    "executedQty": float(o.get("executedQty", 0.0)),
                    "cummulativeQuoteQty": float(o.get("cummulativeQuoteQty", 0.0)),
                    "status": o.get("status"),
                    "orderId": o.get("orderId"),
                })
            return result
        except Exception as e:
            logger.warning("Error obteniendo historial de órdenes Spot %s: %s", binance_symbol, e)
            return []

    def get_spot_trade_history(self, symbol: str = "BTCUSDT", limit: int = 500) -> List[Dict[str, Any]]:
        """Historial de ejecuciones/fills Spot (GET /api/v3/myTrades) para un símbolo."""
        if not self.client:
            return []
        binance_symbol = symbol.replace("/", "").upper()
        try:
            trades = self.client.get_my_trades(symbol=binance_symbol, limit=limit)
            result = []
            for t in reversed(trades):
                result.append({
                    "time_str": _fmt_ts(t.get("time")),
                    "time": t.get("time"),
                    "symbol": t.get("symbol"),
                    "side": "BUY" if t.get("isBuyer") else "SELL",
                    "price": float(t.get("price", 0.0)),
                    "qty": float(t.get("qty", 0.0)),
                    "quoteQty": float(t.get("quoteQty", 0.0)),
                    "commission": float(t.get("commission", 0.0)),
                    "commissionAsset": t.get("commissionAsset"),
                    "orderId": t.get("orderId"),
                })
            return result
        except Exception as e:
            logger.warning("Error obteniendo historial de trades Spot %s: %s", binance_symbol, e)
            return []

    def get_spot_transaction_history(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Historial combinado de depósitos y retiros de la cuenta (GET /sapi/v1/capital/deposit/hisrec
        y /sapi/v1/capital/withdraw/history)."""
        if not self.client:
            return []
        result = []
        try:
            for d in self.client.get_deposit_history():
                result.append({
                    "time_str": _fmt_ts(d.get("insertTime")),
                    "time": d.get("insertTime"),
                    "type": "DEPOSIT",
                    "asset": d.get("coin"),
                    "amount": float(d.get("amount", 0.0)),
                    "status": d.get("status"),
                    "txId": d.get("txId", ""),
                })
        except Exception as e:
            logger.debug("Error obteniendo historial de depósitos: %s", e)
        try:
            for w in self.client.get_withdraw_history():
                result.append({
                    "time_str": _fmt_ts(w.get("applyTime")),
                    "time": w.get("applyTime"),
                    "type": "WITHDRAW",
                    "asset": w.get("coin"),
                    "amount": float(w.get("amount", 0.0)),
                    "status": w.get("status"),
                    "txId": w.get("txId", ""),
                })
        except Exception as e:
            logger.debug("Error obteniendo historial de retiros: %s", e)
        result.sort(key=lambda r: r.get("time") or 0, reverse=True)
        return result[:limit]

    def get_p2p_trade_history(self, trade_type: str = "BUY", rows: int = 100) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Historial de operaciones P2P/C2C (GET /sapi/v1/c2c/orderMatch/listUserOrderHistory).
        EXCLUSIVO de cuentas Reales: Binance Testnet no ofrece mercado P2P en absoluto."""
        if self.use_testnet:
            return [], "P2P no está disponible en Binance Testnet, solo en cuentas Reales."
        if not self.client:
            return [], "Cliente de Binance no disponible"
        try:
            res = self.client.get_c2c_trade_history(tradeType=trade_type, rows=rows)
            data = res.get("data", []) if isinstance(res, dict) else []
            result = []
            for o in data:
                result.append({
                    "orderNumber": o.get("orderNumber"),
                    "type": o.get("orderStatus"),
                    "trade_type": o.get("tradeType"),
                    "asset": o.get("asset"),
                    "amount": float(o.get("amount", 0.0)),
                    "totalPrice": float(o.get("totalPrice", 0.0)),
                    "unitPrice": float(o.get("unitPrice", 0.0)),
                    "fiat": o.get("fiat"),
                    "counterPartNickName": o.get("counterPartNickName", "-"),
                    "createTime": _fmt_ts(o.get("createTime")),
                })
            return result, None
        except Exception as e:
            return [], format_binance_error(e)

    def stop(self):
        pass

