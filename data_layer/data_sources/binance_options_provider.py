"""
Proveedor de datos de mercado de Opciones Vanilla de Binance (EAPI).
Descarga y estructura datos cuantitativos de alta resolución:
- Cadena de Opciones (Options Chain) por Strike y Vencimiento (DTE)
- Volatilidad Implícita (Mark IV, Bid IV, Ask IV)
- Griegas completas: Delta, Gamma, Vega, Theta
- Sonrisa de Volatilidad (Volatility Smile) y Skew
"""
import logging
import time
import re
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
import requests

logger = logging.getLogger("BinanceOptionsProvider")

EAPI_BASE_URL = "https://eapi.binance.com"


class BinanceOptionsProvider:
    """Proveedor cuantitativo de Opciones Vanilla de Binance."""

    def __init__(self, timeout: int = 8):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "TradingQuantApp/2.0 (Options Analytics Engine)"
        })
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._cache_ttl = 45.0  # 45 segundos de caché

    def _get_cached(self, key: str) -> Optional[Any]:
        if key in self._cache:
            ts, val = self._cache[key]
            if time.time() - ts < self._cache_ttl:
                return val
        return None

    def _set_cached(self, key: str, val: Any) -> None:
        self._cache[key] = (time.time(), val)

    def get_all_option_marks(self) -> List[Dict[str, Any]]:
        """Descarga todos los contratos de opciones activos con IV y griegas."""
        cache_key = "options_marks_all"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        url = f"{EAPI_BASE_URL}/eapi/v1/mark"
        try:
            res = self.session.get(url, timeout=self.timeout)
            if res.status_code == 200:
                data = res.json()
                self._set_cached(cache_key, data)
                return data
            logger.warning("Error consultando eapi/v1/mark: HTTP %d", res.status_code)
            return []
        except Exception as e:
            logger.error("Excepción en get_all_option_marks: %s", e)
            return []

    def get_underlying_index_price(self, underlying: str = "BTCUSDT") -> float:
        """Obtiene el precio índice actual del subyacente para opciones."""
        cache_key = f"options_index_{underlying}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        url = f"{EAPI_BASE_URL}/eapi/v1/index"
        try:
            res = self.session.get(url, params={"underlying": underlying}, timeout=self.timeout)
            if res.status_code == 200:
                d = res.json()
                price = float(d.get("indexPrice", 0.0))
                self._set_cached(cache_key, price)
                return price
            return 0.0
        except Exception as e:
            logger.error("Excepción en get_underlying_index_price: %s", e)
            return 0.0

    @staticmethod
    def parse_option_symbol(symbol: str) -> Optional[Dict[str, Any]]:
        """
        Parsea un símbolo de opción Binance como 'BTC-260925-145000-C'.
        Retorna: {underlying, expiry_str, expiry_date, strike, option_type, dte}
        """
        # Formato: ASSET-YYMMDD-STRIKE-TYPE
        pattern = r"^([A-Z]+)-(\d{6})-(\d+)-([CP])$"
        m = re.match(pattern, symbol.strip().upper())
        if not m:
            return None

        underlying = m.group(1)
        exp_str = m.group(2)
        strike = float(m.group(3))
        opt_type = "CALL" if m.group(4) == "C" else "PUT"

        try:
            year = 2000 + int(exp_str[:2])
            month = int(exp_str[2:4])
            day = int(exp_str[4:6])
            # Expiración estándar a las 08:00 UTC
            exp_dt = datetime(year, month, day, 8, 0, 0, tzinfo=timezone.utc)
            now_dt = datetime.now(timezone.utc)
            dte = max(0.0, (exp_dt - now_dt).total_seconds() / 86400.0)
            formatted_date = exp_dt.strftime("%d %b %Y")
        except Exception:
            formatted_date = exp_str
            dte = 0.0

        return {
            "symbol": symbol,
            "underlying": underlying,
            "expiry_code": exp_str,
            "expiry_date_str": formatted_date,
            "strike": strike,
            "option_type": opt_type,
            "dte": round(dte, 1)
        }

    def get_parsed_options_dataset(self, underlying_asset: str = "BTC") -> Dict[str, Any]:
        """
        Descarga todas las opciones para un activo (BTC o ETH) y las organiza por
        fecha de vencimiento (Expirations), estructurando una matriz de Cadena de Opciones.
        """
        raw_marks = self.get_all_option_marks()
        clean_asset = underlying_asset.upper()
        index_price = self.get_underlying_index_price(f"{clean_asset}USDT")

        expirations_set = set()
        contracts = []

        for item in raw_marks:
            sym = item.get("symbol", "")
            if not sym.startswith(f"{clean_asset}-"):
                continue

            parsed = self.parse_option_symbol(sym)
            if not parsed:
                continue

            mark_iv = float(item.get("markIV", 0.0)) * 100.0
            bid_iv = float(item.get("bidIV", 0.0)) * 100.0
            ask_iv = float(item.get("askIV", 0.0)) * 100.0
            delta = float(item.get("delta", 0.0))
            gamma = float(item.get("gamma", 0.0))
            vega = float(item.get("vega", 0.0))
            theta = float(item.get("theta", 0.0))
            mark_price = float(item.get("markPrice", 0.0))

            contract_info = {
                **parsed,
                "mark_price": mark_price,
                "mark_iv": round(mark_iv, 2),
                "bid_iv": round(bid_iv, 2),
                "ask_iv": round(ask_iv, 2),
                "delta": round(delta, 4),
                "gamma": round(gamma, 6),
                "vega": round(vega, 4),
                "theta": round(theta, 2),
                "risk_free_rate": float(item.get("riskFreeInterest", 0.0))
            }
            contracts.append(contract_info)
            expirations_set.add((parsed["expiry_code"], parsed["expiry_date_str"], parsed["dte"]))

        # Ordenar expiraciones por DTE ascendente
        sorted_expirations = sorted(list(expirations_set), key=lambda x: x[2])

        return {
            "underlying": clean_asset,
            "index_price": index_price,
            "total_contracts": len(contracts),
            "expirations": [
                {"code": e[0], "date_str": e[1], "dte": e[2]}
                for e in sorted_expirations
            ],
            "contracts": contracts
        }

    def get_options_chain_matrix(self, underlying_asset: str = "BTC", expiry_code: Optional[str] = None) -> Dict[str, Any]:
        """
        Construye una matriz de Cadena de Opciones para un vencimiento específico:
        Strikes únicos ordenados con datos emparejados de CALL a la izquierda y PUT a la derecha.
        """
        dataset = self.get_parsed_options_dataset(underlying_asset)
        expirations = dataset.get("expirations", [])
        if not expirations:
            return {"error": "No hay opciones disponibles", "chain": []}

        # Si no se indica vencimiento, usar el más cercano con DTE > 0.1
        target_expiry = expiry_code
        if not target_expiry:
            valid_exp = [e for e in expirations if e["dte"] >= 0.1]
            target_expiry = valid_exp[0]["code"] if valid_exp else expirations[0]["code"]

        contracts = dataset.get("contracts", [])
        filtered = [c for c in contracts if c["expiry_code"] == target_expiry]

        # Agrupar por strike
        strikes_map: Dict[float, Dict[str, Any]] = {}
        for c in filtered:
            s = c["strike"]
            if s not in strikes_map:
                strikes_map[s] = {"strike": s, "call": None, "put": None}

            if c["option_type"] == "CALL":
                strikes_map[s]["call"] = c
            else:
                strikes_map[s]["put"] = c

        sorted_strikes = sorted(strikes_map.keys())
        chain_rows = [strikes_map[s] for s in sorted_strikes]

        # Extraer series para Sonrisa de Volatilidad (Volatility Smile)
        smile_strikes = []
        smile_call_iv = []
        smile_put_iv = []
        greeks_strikes = []
        call_deltas = []
        put_deltas = []
        gammas = []
        vegas = []

        for row in chain_rows:
            stk = row["strike"]
            c = row["call"]
            p = row["put"]

            smile_strikes.append(stk)
            c_iv = c["mark_iv"] if (c and c["mark_iv"] > 0) else None
            p_iv = p["mark_iv"] if (p and p["mark_iv"] > 0) else None
            smile_call_iv.append(c_iv)
            smile_put_iv.append(p_iv)

            greeks_strikes.append(stk)
            call_deltas.append(c["delta"] if c else None)
            put_deltas.append(p["delta"] if p else None)
            gammas.append(c["gamma"] if c else (p["gamma"] if p else None))
            vegas.append(c["vega"] if c else (p["vega"] if p else None))

        return {
            "underlying": underlying_asset,
            "index_price": dataset.get("index_price", 0.0),
            "current_expiry": target_expiry,
            "available_expirations": expirations,
            "chain": chain_rows,
            "smile": {
                "strikes": smile_strikes,
                "call_iv": smile_call_iv,
                "put_iv": smile_put_iv
            },
            "greeks": {
                "strikes": greeks_strikes,
                "call_delta": call_deltas,
                "put_delta": put_deltas,
                "gamma": gammas,
                "vega": vegas
            }
        }
