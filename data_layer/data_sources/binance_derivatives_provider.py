"""
Proveedor de datos de mercado de Derivados (Futuros USDⓈ-M y COIN-M) de Binance.
Accede a endpoints públicos de alta frecuencia para métricas cuantitativas:
- Funding Rate (actual e histórico)
- Open Interest (Interés Abierto actual e histórico)
- Ratio Long/Short de Top Traders (Ballenas) vs Cuentas Globales (Retail)
- Flujo Taker (Volumen agresivo comprador vs vendedor)
- Mark Price, Index Price y Basis
"""
import logging
import time
from typing import Dict, Any, List, Optional
import requests
import pandas as pd

logger = logging.getLogger("BinanceDerivativesProvider")

FAPI_BASE_URL = "https://fapi.binance.com"


class BinanceDerivativesProvider:
    """Proveedor cuantitativo para análisis de derivados y futuros de Binance."""

    def __init__(self, timeout: int = 8):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "TradingQuantApp/2.0 (Derivatives Analytics Engine)"
        })
        # Caché en memoria: clave -> (timestamp, data)
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._cache_ttl = 30.0  # 30 segundos de TTL para datos de mercado

    def _get_cached(self, key: str) -> Optional[Any]:
        if key in self._cache:
            ts, val = self._cache[key]
            if time.time() - ts < self._cache_ttl:
                return val
        return None

    def _set_cached(self, key: str, val: Any) -> None:
        self._cache[key] = (time.time(), val)

    def get_funding_rate_history(self, symbol: str = "BTCUSDT", limit: int = 100) -> List[Dict[str, Any]]:
        """
        Obtiene el histórico de Funding Rates para un símbolo de futuros.
        Retorna lista de registros con fundingTime, fundingRate, markPrice y APR anualizado.
        """
        clean_sym = symbol.replace("/", "").upper()
        cache_key = f"funding_{clean_sym}_{limit}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        url = f"{FAPI_BASE_URL}/fapi/v1/fundingRate"
        params = {"symbol": clean_sym, "limit": limit}
        try:
            res = self.session.get(url, params=params, timeout=self.timeout)
            if res.status_code == 200:
                data = res.json()
                results = []
                for item in data:
                    rate = float(item.get("fundingRate", 0.0))
                    # 3 pagos de funding al día * 365 días = 1095 pagos al año
                    annualized_apr = rate * 3 * 365 * 100.0
                    results.append({
                        "funding_time": int(item.get("fundingTime", 0)),
                        "funding_rate": rate,
                        "funding_rate_pct": rate * 100.0,
                        "annualized_apr": annualized_apr,
                        "mark_price": float(item.get("markPrice", 0.0))
                    })
                self._set_cached(cache_key, results)
                return results
            else:
                logger.warning("Error consultando fundingRate para %s: HTTP %d", clean_sym, res.status_code)
                return []
        except Exception as e:
            logger.error("Excepción en get_funding_rate_history: %s", e)
            return []

    def get_open_interest_history(self, symbol: str = "BTCUSDT", period: str = "1h", limit: int = 50) -> List[Dict[str, Any]]:
        """
        Obtiene la serie temporal de Open Interest (Interés Abierto).
        Periodos admitidos por Binance: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d.
        """
        clean_sym = symbol.replace("/", "").upper()
        cache_key = f"oi_hist_{clean_sym}_{period}_{limit}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        url = f"{FAPI_BASE_URL}/futures/data/openInterestHist"
        params = {"symbol": clean_sym, "period": period, "limit": limit}
        try:
            res = self.session.get(url, params=params, timeout=self.timeout)
            if res.status_code == 200:
                data = res.json()
                results = []
                for item in data:
                    results.append({
                        "timestamp": int(item.get("timestamp", 0)),
                        "sum_open_interest": float(item.get("sumOpenInterest", 0.0)),
                        "sum_open_interest_usd": float(item.get("sumOpenInterestValue", 0.0))
                    })
                self._set_cached(cache_key, results)
                return results
            else:
                logger.warning("Error consultando openInterestHist: HTTP %d", res.status_code)
                return []
        except Exception as e:
            logger.error("Excepción en get_open_interest_history: %s", e)
            return []

    def get_current_open_interest(self, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        """Obtiene el interés abierto actual en tiempo real."""
        clean_sym = symbol.replace("/", "").upper()
        cache_key = f"oi_curr_{clean_sym}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        url = f"{FAPI_BASE_URL}/fapi/v1/openInterest"
        try:
            res = self.session.get(url, params={"symbol": clean_sym}, timeout=self.timeout)
            if res.status_code == 200:
                d = res.json()
                data = {
                    "symbol": clean_sym,
                    "open_interest_contracts": float(d.get("openInterest", 0.0)),
                    "timestamp": int(d.get("time", 0))
                }
                self._set_cached(cache_key, data)
                return data
            return {}
        except Exception as e:
            logger.error("Excepción en get_current_open_interest: %s", e)
            return {}

    def get_top_long_short_account_ratio(self, symbol: str = "BTCUSDT", period: str = "1h", limit: int = 50) -> List[Dict[str, Any]]:
        """Ratio Long/Short de cuentas de Top Traders (Ballenas)."""
        clean_sym = symbol.replace("/", "").upper()
        cache_key = f"top_ls_acc_{clean_sym}_{period}_{limit}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        url = f"{FAPI_BASE_URL}/futures/data/topLongShortAccountRatio"
        try:
            res = self.session.get(url, params={"symbol": clean_sym, "period": period, "limit": limit}, timeout=self.timeout)
            if res.status_code == 200:
                data = [
                    {
                        "timestamp": int(x.get("timestamp", 0)),
                        "long_account_pct": float(x.get("longAccount", 0.0)) * 100.0,
                        "short_account_pct": float(x.get("shortAccount", 0.0)) * 100.0,
                        "long_short_ratio": float(x.get("longShortRatio", 1.0))
                    }
                    for x in res.json()
                ]
                self._set_cached(cache_key, data)
                return data
            return []
        except Exception as e:
            logger.error("Excepción en get_top_long_short_account_ratio: %s", e)
            return []

    def get_global_long_short_account_ratio(self, symbol: str = "BTCUSDT", period: str = "1h", limit: int = 50) -> List[Dict[str, Any]]:
        """Ratio Long/Short de cuentas globales (Mercado General / Retail)."""
        clean_sym = symbol.replace("/", "").upper()
        cache_key = f"glob_ls_{clean_sym}_{period}_{limit}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        url = f"{FAPI_BASE_URL}/futures/data/globalLongShortAccountRatio"
        try:
            res = self.session.get(url, params={"symbol": clean_sym, "period": period, "limit": limit}, timeout=self.timeout)
            if res.status_code == 200:
                data = [
                    {
                        "timestamp": int(x.get("timestamp", 0)),
                        "long_account_pct": float(x.get("longAccount", 0.0)) * 100.0,
                        "short_account_pct": float(x.get("shortAccount", 0.0)) * 100.0,
                        "long_short_ratio": float(x.get("longShortRatio", 1.0))
                    }
                    for x in res.json()
                ]
                self._set_cached(cache_key, data)
                return data
            return []
        except Exception as e:
            logger.error("Excepción en get_global_long_short_account_ratio: %s", e)
            return []

    def get_taker_long_short_ratio(self, symbol: str = "BTCUSDT", period: str = "1h", limit: int = 50) -> List[Dict[str, Any]]:
        """Volumen Taker (órdenes a mercado agresivas): compra vs venta."""
        clean_sym = symbol.replace("/", "").upper()
        cache_key = f"taker_ls_{clean_sym}_{period}_{limit}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        url = f"{FAPI_BASE_URL}/futures/data/takerlongshortRatio"
        try:
            res = self.session.get(url, params={"symbol": clean_sym, "period": period, "limit": limit}, timeout=self.timeout)
            if res.status_code == 200:
                data = [
                    {
                        "timestamp": int(x.get("timestamp", 0)),
                        "buy_volume": float(x.get("buyVol", 0.0)),
                        "sell_volume": float(x.get("sellVol", 0.0)),
                        "buy_sell_ratio": float(x.get("buySellRatio", 1.0))
                    }
                    for x in res.json()
                ]
                self._set_cached(cache_key, data)
                return data
            return []
        except Exception as e:
            logger.error("Excepción en get_taker_long_short_ratio: %s", e)
            return []

    def get_premium_index_and_basis(self, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        """Obtiene Mark Price, Index Price, Basis actual y cuenta atrás de Funding."""
        clean_sym = symbol.replace("/", "").upper()
        cache_key = f"prem_idx_{clean_sym}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        url = f"{FAPI_BASE_URL}/fapi/v1/premiumIndex"
        try:
            res = self.session.get(url, params={"symbol": clean_sym}, timeout=self.timeout)
            if res.status_code == 200:
                d = res.json()
                mark = float(d.get("markPrice", 0.0))
                index = float(d.get("indexPrice", 0.0))
                spread = mark - index
                basis_pct = (spread / index * 100.0) if index > 0 else 0.0
                next_funding_time = int(d.get("nextFundingTime", 0))
                last_rate = float(d.get("lastFundingRate", 0.0))

                data = {
                    "symbol": clean_sym,
                    "mark_price": mark,
                    "index_price": index,
                    "basis_usd": spread,
                    "basis_pct": basis_pct,
                    "last_funding_rate": last_rate,
                    "last_funding_rate_pct": last_rate * 100.0,
                    "annualized_apr": last_rate * 3 * 365 * 100.0,
                    "next_funding_time": next_funding_time,
                    "timestamp": int(d.get("time", 0))
                }
                self._set_cached(cache_key, data)
                return data
            return {}
        except Exception as e:
            logger.error("Excepción en get_premium_index_and_basis: %s", e)
            return {}

    def get_aggregated_derivatives_dashboard(self, symbol: str = "BTCUSDT", period: str = "1h") -> Dict[str, Any]:
        """Consolida todas las métricas en una única estructura para el Dashboard."""
        clean_sym = symbol.replace("/", "").upper()
        clean_period = period.strip().lower()
        premium = self.get_premium_index_and_basis(clean_sym)
        funding_hist = self.get_funding_rate_history(clean_sym, limit=60)
        oi_hist = self.get_open_interest_history(clean_sym, period=clean_period, limit=48)
        top_ls = self.get_top_long_short_account_ratio(clean_sym, period=clean_period, limit=48)
        glob_ls = self.get_global_long_short_account_ratio(clean_sym, period=clean_period, limit=48)
        taker_ls = self.get_taker_long_short_ratio(clean_sym, period=clean_period, limit=48)

        # Últimas lecturas
        latest_top_ratio = top_ls[-1]["long_short_ratio"] if top_ls else 1.0
        latest_top_long = top_ls[-1]["long_account_pct"] if top_ls else 50.0
        latest_glob_ratio = glob_ls[-1]["long_short_ratio"] if glob_ls else 1.0
        latest_glob_long = glob_ls[-1]["long_account_pct"] if glob_ls else 50.0
        latest_taker_ratio = taker_ls[-1]["buy_sell_ratio"] if taker_ls else 1.0

        latest_oi_usd = oi_hist[-1]["sum_open_interest_usd"] if oi_hist else 0.0
        prev_oi_usd = oi_hist[-2]["sum_open_interest_usd"] if len(oi_hist) > 1 else latest_oi_usd
        oi_change_pct = ((latest_oi_usd - prev_oi_usd) / prev_oi_usd * 100.0) if prev_oi_usd > 0 else 0.0

        return {
            "symbol": clean_sym,
            "premium": premium,
            "funding_hist": funding_hist,
            "oi_hist": oi_hist,
            "top_ls": top_ls,
            "glob_ls": glob_ls,
            "taker_ls": taker_ls,
            "summary": {
                "latest_funding_pct": premium.get("last_funding_rate_pct", 0.0),
                "annualized_apr": premium.get("annualized_apr", 0.0),
                "mark_price": premium.get("mark_price", 0.0),
                "index_price": premium.get("index_price", 0.0),
                "basis_pct": premium.get("basis_pct", 0.0),
                "latest_oi_usd": latest_oi_usd,
                "oi_change_pct": oi_change_pct,
                "latest_top_ratio": latest_top_ratio,
                "latest_top_long": latest_top_long,
                "latest_glob_ratio": latest_glob_ratio,
                "latest_glob_long": latest_glob_long,
                "latest_taker_ratio": latest_taker_ratio,
                "sentiment_divergence": (latest_top_ratio - latest_glob_ratio)
            }
        }
