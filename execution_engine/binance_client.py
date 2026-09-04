import os
import time
import logging
from typing import Dict, Any, Optional, List, Tuple
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()
logger = logging.getLogger("BinanceClient")

class BinanceTestnetClient:
    """Cliente unificado para interactuar con Binance (Futures Testnet y Real Mainnet)."""

    def __init__(self, use_testnet: bool = False):
        self.use_testnet = use_testnet
        if use_testnet:
            self.api_key = os.getenv("BINANCE_TESTNET_API_KEY", "").strip() or os.getenv("BINANCE_API_KEY", "").strip()
            self.api_secret = os.getenv("BINANCE_TESTNET_SECRET_KEY", "").strip() or os.getenv("BINANCE_SECRET_KEY", "").strip()
        else:
            self.api_key = os.getenv("BINANCE_REAL_API_KEY", "").strip() or os.getenv("BINANCE_API_KEY", "").strip()
            self.api_secret = os.getenv("BINANCE_REAL_SECRET_KEY", "").strip() or os.getenv("BINANCE_SECRET_KEY", "").strip()

        # Inicialización de cliente Binance con timeout
        self.client = Client(
            self.api_key,
            self.api_secret,
            testnet=use_testnet,
            requests_params={'timeout': 10}
        )
        if use_testnet:
            self.client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi/v1'
        else:
            self.client.FUTURES_URL = 'https://fapi.binance.com/fapi/v1'

    def get_historical_klines(self, symbol: str, interval: str, lookback_str: str):
        """Obtiene velas históricas para inicializar indicadores."""
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
        Envía una orden (MARKET o LIMIT) a Binance Futures Testnet y verifica su ejecución en el exchange.
        """
        if not self.use_testnet:
            return None, "Testnet desactivado en este bot"
        if not self.api_key or not self.api_secret:
            return None, "API Key o Secret no configuradas en .env"

        binance_symbol = symbol.replace("/", "").upper()
        binance_side = "BUY" if side.lower() == "long" else "SELL"

        if quantity <= 0:
            return None, "Cantidad inválida (debe ser mayor a 0)"

        qty = round(quantity, 5)
        if qty <= 0:
            qty = quantity

        o_type = order_type.upper()
        try:
            params = {
                "symbol": binance_symbol,
                "side": binance_side,
                "type": o_type,
                "quantity": qty
            }
            if o_type == "LIMIT":
                if price is None or price <= 0:
                    return None, "Precio límite requerido para órdenes LIMIT"
                params["price"] = round(price, 2)
                params["timeInForce"] = "GTC"

            order = self.client.futures_create_order(**params)
            order_id = order.get("orderId")
            initial_status = str(order.get("status", "")).upper()
            logger.info("Orden %s (%s) creada en Binance Futures. ID: %s, Estado inicial: %s", o_type, binance_side, order_id, initial_status)

            if verify_execution and order_id:
                # Para órdenes MARKET, esperamos 'FILLED' inmediatamente o en los siguientes milisegundos
                # Para órdenes LIMIT, 'NEW' o 'PARTIALLY_FILLED' o 'FILLED' son válidos
                expected = ["FILLED"] if o_type == "MARKET" else ["NEW", "PARTIALLY_FILLED", "FILLED"]
                
                if initial_status not in expected:
                    # Consultar activamente para confirmar si cambió a FILLED
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
            return None, str(e)

    def close_futures_position(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        verify_execution: bool = True
    ) -> Tuple[Optional[dict], Optional[str]]:
        """
        Cierra una posición en Binance Futures con orden contraria y verifica su ejecución en el exchange.
        """
        if not self.use_testnet:
            return None, "Testnet desactivado en este bot"
        if not self.api_key or not self.api_secret:
            return None, "API Key o Secret no configuradas en .env"

        binance_symbol = symbol.replace("/", "").upper()
        close_side = "SELL" if side.lower() == "long" else "BUY"

        if quantity <= 0:
            return None, "Cantidad inválida (debe ser mayor a 0)"

        qty = round(quantity, 5)
        if qty <= 0:
            qty = quantity

        o_type = order_type.upper()
        try:
            params = {
                "symbol": binance_symbol,
                "side": close_side,
                "type": o_type,
                "quantity": qty,
                "reduceOnly": True
            }
            if o_type == "LIMIT":
                if price is not None and price > 0:
                    params["price"] = round(price, 2)
                    params["timeInForce"] = "GTC"
                else:
                    params["type"] = "MARKET"

            order = self.client.futures_create_order(**params)
            order_id = order.get("orderId")
            initial_status = str(order.get("status", "")).upper()
            logger.info("Cierre de posición %s (%s) enviado a Binance Futures. ID: %s, Estado inicial: %s", o_type, close_side, order_id, initial_status)

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
            return None, str(e)

    def place_futures_sl_tp(
        self,
        symbol: str,
        side: str,
        quantity: float,
        sl_price: Optional[float] = None,
        tp_price: Optional[float] = None,
        sl_order_type: str = "LIMIT",
        tp_order_type: str = "LIMIT"
    ) -> Dict[str, Any]:
        """Coloca órdenes condicionales de Stop Loss y Take Profit (LIMIT o MARKET) en Binance Futures."""
        if not self.use_testnet or not self.api_key or not self.api_secret:
            return {"sl_order": None, "tp_order": None, "error": "Testnet no configurado"}

        binance_symbol = symbol.replace("/", "").upper()
        close_side = "SELL" if side.lower() == "long" else "BUY"
        qty = round(quantity, 3)
        if qty < 0.001:
            qty = 0.001

        results = {"sl_order": None, "tp_order": None, "errors": []}

        # 1. Take Profit
        if tp_price and tp_price > 0:
            try:
                tp_type = "TAKE_PROFIT" if tp_order_type.upper() == "LIMIT" else "TAKE_PROFIT_MARKET"
                tp_params = {
                    "symbol": binance_symbol,
                    "side": close_side,
                    "type": tp_type,
                    "stopPrice": round(tp_price, 2),
                    "quantity": qty,
                    "reduceOnly": True
                }
                if tp_type == "TAKE_PROFIT":
                    tp_params["price"] = round(tp_price, 2)
                    tp_params["timeInForce"] = "GTC"

                tp_res = self.client.futures_create_order(**tp_params)
                results["tp_order"] = tp_res
                logger.info("Orden TP (%s) enviada a Binance: %s", tp_type, tp_res)
            except Exception as e:
                logger.warning("No se pudo colocar orden TP en Binance: %s", e)
                results["errors"].append(f"TP Error: {e}")

        # 2. Stop Loss
        if sl_price and sl_price > 0:
            try:
                sl_type = "STOP" if sl_order_type.upper() == "LIMIT" else "STOP_MARKET"
                sl_params = {
                    "symbol": binance_symbol,
                    "side": close_side,
                    "type": sl_type,
                    "stopPrice": round(sl_price, 2),
                    "quantity": qty,
                    "reduceOnly": True
                }
                if sl_type == "STOP":
                    # Para STOP Limit, el precio de ejecución puede ser ligeramente más conservador o el mismo stopPrice
                    sl_params["price"] = round(sl_price, 2)
                    sl_params["timeInForce"] = "GTC"

                sl_res = self.client.futures_create_order(**sl_params)
                results["sl_order"] = sl_res
                logger.info("Orden SL (%s) enviada a Binance: %s", sl_type, sl_res)
            except Exception as e:
                logger.warning("No se pudo colocar orden SL en Binance: %s", e)
                results["errors"].append(f"SL Error: {e}")

        return results

    def get_open_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Devuelve todas las posiciones actualmente abiertas con positionAmt != 0 en Binance Futures."""
        if not self.api_key or not self.api_secret:
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

    def cancel_all_open_orders(self, symbol: str) -> Tuple[bool, Optional[str]]:
        """Cancela todas las órdenes abiertas estándar y condicionales (algo SL/TP) pendientes en Binance Futures."""
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
                                pass
            except Exception:
                pass

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
                                    pass
            except Exception:
                pass

            logger.info("Órdenes abiertas y condicionales (Algo) canceladas con éxito para %s", binance_symbol)
            return True, None
        except Exception as e:
            logger.warning("Error cancelando órdenes pendientes para %s: %s", symbol, e)
            return False, str(e)

    def cancel_all_futures_orders(self, symbol: str = "BTCUSDT", use_testnet: Optional[bool] = None) -> Tuple[bool, Optional[str]]:
        """Cancela todas las órdenes abiertas de un símbolo en Binance Futures (compatible con testnet y mainnet)."""
        if use_testnet is not None:
            self.use_testnet = use_testnet
            self.client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi/v1' if use_testnet else 'https://fapi.binance.com/fapi/v1'
        return self.cancel_all_open_orders(symbol)

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
            binance_symbol = symbol.replace("/", "").upper()
            ticker = self.client.futures_symbol_ticker(symbol=binance_symbol)
            return float(ticker.get('price', 0.0))
        except Exception as e:
            logger.debug("Error obteniendo precio de %s: %s", symbol, e)
            return 0.0

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
            test_client = Client(self.api_key, self.api_secret, testnet=True, requests_params={'timeout': 10})
            test_client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi/v1'

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
            results["error"] = str(e)
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
            main_client = Client(self.api_key, self.api_secret, testnet=False, requests_params={'timeout': 8})

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
            results["error"] = str(e)
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
            client = Client(api_k, api_s, testnet=use_testnet, requests_params={'timeout': 10})
            if use_testnet:
                client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi/v1'
            else:
                client.FUTURES_URL = 'https://fapi.binance.com/fapi/v1'

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
                pass
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
                pass

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
            data["error"] = str(e)
            return data

    def cancel_futures_order(self, symbol: str, order_id: int, use_testnet: bool = True) -> Tuple[bool, Optional[str]]:
        """Cancela una orden abierta en Binance Futures."""
        try:
            client = Client(self.api_key, self.api_secret, testnet=use_testnet, requests_params={'timeout': 10})
            if use_testnet:
                client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi/v1'
            binance_symbol = symbol.replace("/", "").upper()
            res = client.futures_cancel_order(symbol=binance_symbol, orderId=order_id)
            return True, None
        except Exception as e:
            return False, str(e)

    def cancel_all_futures_orders(self, symbol: str = "BTCUSDT", use_testnet: bool = True) -> Tuple[bool, Optional[str]]:
        """Cancela todas las órdenes abiertas en Binance Futures para un símbolo."""
        try:
            client = Client(self.api_key, self.api_secret, testnet=use_testnet, requests_params={'timeout': 10})
            if use_testnet:
                client.FUTURES_URL = 'https://testnet.binancefuture.com/fapi/v1'
            binance_symbol = symbol.replace("/", "").upper()
            res = client.futures_cancel_all_open_orders(symbol=binance_symbol)
            return True, None
        except Exception as e:
            return False, str(e)

    def stop(self):
        pass
