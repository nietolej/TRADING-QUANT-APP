"""
Proveedor de datos de mercado de Derivados (Futuros USDⓈ-M y COIN-M) de Binance.
Soporta:
- Consulta por Rango de Fechas Personalizado (start_time / end_time)
- Paginación automática por ventanas temporales
- Descarga de Velas OHLCV de Futuros
- Persistencia automática en disco local (Apache Parquet)
- Exportación consolidada a formato CSV
"""
import os
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
import requests
import pandas as pd

logger = logging.getLogger("BinanceDerivativesProvider")

FAPI_BASE_URL = "https://fapi.binance.com"
STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "derivatives")
os.makedirs(STORAGE_DIR, exist_ok=True)


class BinanceDerivativesProvider:
    """Proveedor cuantitativo para análisis de derivados y futuros de Binance con persistencia Parquet."""

    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "TradingQuantApp/2.0 (Derivatives Storage Engine)"
        })
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._cache_ttl = 60.0

    def _get_cached(self, key: str) -> Optional[Any]:
        if key in self._cache:
            ts, val = self._cache[key]
            if time.time() - ts < self._cache_ttl:
                return val
        return None

    def _set_cached(self, key: str, val: Any) -> None:
        self._cache[key] = (time.time(), val)

    def get_historical_ohlcv_klines(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "1d",
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        limit: int = 500
    ) -> List[Dict[str, Any]]:
        """
        Descarga velas OHLCV de Binance Futures.
        Retorna lista con timestamp, open, high, low, close, volume.
        """
        clean_sym = symbol.replace("/", "").upper()
        url = f"{FAPI_BASE_URL}/fapi/v1/klines"
        params: Dict[str, Any] = {
            "symbol": clean_sym,
            "interval": interval,
            "limit": min(limit, 1000)
        }
        if start_time_ms:
            params["startTime"] = start_time_ms
        if end_time_ms:
            params["endTime"] = end_time_ms

        try:
            res = self.session.get(url, params=params, timeout=self.timeout)
            if res.status_code == 200:
                raw = res.json()
                klines = []
                for k in raw:
                    klines.append({
                        "timestamp": int(k[0]),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                        "close_time": int(k[6]),
                        "quote_volume": float(k[7]),
                        "trades_count": int(k[8])
                    })
                return klines
            logger.warning("Error descargando klines para %s: HTTP %d", clean_sym, res.status_code)
            return []
        except Exception as e:
            logger.error("Excepción en get_historical_ohlcv_klines: %s", e)
            return []

    def get_funding_rate_history(
        self,
        symbol: str = "BTCUSDT",
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Obtiene el histórico de Funding Rates con filtro de fechas opcional."""
        clean_sym = symbol.replace("/", "").upper()
        url = f"{FAPI_BASE_URL}/fapi/v1/fundingRate"
        params: Dict[str, Any] = {"symbol": clean_sym, "limit": min(limit, 1000)}
        if start_time_ms:
            params["startTime"] = start_time_ms
        if end_time_ms:
            params["endTime"] = end_time_ms

        try:
            res = self.session.get(url, params=params, timeout=self.timeout)
            if res.status_code == 200:
                data = res.json()
                results = []
                for item in data:
                    rate = float(item.get("fundingRate", 0.0))
                    annualized_apr = rate * 3 * 365 * 100.0
                    results.append({
                        "funding_time": int(item.get("fundingTime", 0)),
                        "funding_rate": rate,
                        "funding_rate_pct": rate * 100.0,
                        "annualized_apr": annualized_apr,
                        "mark_price": float(item.get("markPrice", 0.0))
                    })
                return results
            return []
        except Exception as e:
            logger.error("Excepción en get_funding_rate_history: %s", e)
            return []

    def get_open_interest_history(
        self,
        symbol: str = "BTCUSDT",
        period: str = "1h",
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Obtiene serie temporal de Open Interest con filtro de fechas."""
        clean_sym = symbol.replace("/", "").upper()
        clean_period = period.strip().lower()
        url = f"{FAPI_BASE_URL}/futures/data/openInterestHist"
        params: Dict[str, Any] = {"symbol": clean_sym, "period": clean_period, "limit": min(limit, 500)}
        if start_time_ms:
            params["startTime"] = start_time_ms
        if end_time_ms:
            params["endTime"] = end_time_ms

        try:
            res = self.session.get(url, params=params, timeout=self.timeout)
            if res.status_code == 200:
                return [
                    {
                        "timestamp": int(item.get("timestamp", 0)),
                        "sum_open_interest": float(item.get("sumOpenInterest", 0.0)),
                        "sum_open_interest_usd": float(item.get("sumOpenInterestValue", 0.0))
                    }
                    for item in res.json()
                ]
            return []
        except Exception as e:
            logger.error("Excepción en get_open_interest_history: %s", e)
            return []

    def get_top_long_short_account_ratio(
        self,
        symbol: str = "BTCUSDT",
        period: str = "1h",
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Ratio Long/Short de cuentas de Top Traders (Ballenas)."""
        clean_sym = symbol.replace("/", "").upper()
        clean_period = period.strip().lower()
        url = f"{FAPI_BASE_URL}/futures/data/topLongShortAccountRatio"
        params: Dict[str, Any] = {"symbol": clean_sym, "period": clean_period, "limit": min(limit, 500)}
        if start_time_ms:
            params["startTime"] = start_time_ms
        if end_time_ms:
            params["endTime"] = end_time_ms

        try:
            res = self.session.get(url, params=params, timeout=self.timeout)
            if res.status_code == 200:
                return [
                    {
                        "timestamp": int(x.get("timestamp", 0)),
                        "long_account_pct": float(x.get("longAccount", 0.0)) * 100.0,
                        "short_account_pct": float(x.get("shortAccount", 0.0)) * 100.0,
                        "long_short_ratio": float(x.get("longShortRatio", 1.0))
                    }
                    for x in res.json()
                ]
            return []
        except Exception as e:
            logger.error("Excepción en get_top_long_short_account_ratio: %s", e)
            return []

    def get_global_long_short_account_ratio(
        self,
        symbol: str = "BTCUSDT",
        period: str = "1h",
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Ratio Long/Short de cuentas globales (Retail)."""
        clean_sym = symbol.replace("/", "").upper()
        clean_period = period.strip().lower()
        url = f"{FAPI_BASE_URL}/futures/data/globalLongShortAccountRatio"
        params: Dict[str, Any] = {"symbol": clean_sym, "period": clean_period, "limit": min(limit, 500)}
        if start_time_ms:
            params["startTime"] = start_time_ms
        if end_time_ms:
            params["endTime"] = end_time_ms

        try:
            res = self.session.get(url, params=params, timeout=self.timeout)
            if res.status_code == 200:
                return [
                    {
                        "timestamp": int(x.get("timestamp", 0)),
                        "long_account_pct": float(x.get("longAccount", 0.0)) * 100.0,
                        "short_account_pct": float(x.get("shortAccount", 0.0)) * 100.0,
                        "long_short_ratio": float(x.get("longShortRatio", 1.0))
                    }
                    for x in res.json()
                ]
            return []
        except Exception as e:
            logger.error("Excepción en get_global_long_short_account_ratio: %s", e)
            return []

    def get_taker_long_short_ratio(
        self,
        symbol: str = "BTCUSDT",
        period: str = "1h",
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Volumen Taker (compras vs ventas agresivas)."""
        clean_sym = symbol.replace("/", "").upper()
        clean_period = period.strip().lower()
        url = f"{FAPI_BASE_URL}/futures/data/takerlongshortRatio"
        params: Dict[str, Any] = {"symbol": clean_sym, "period": clean_period, "limit": min(limit, 500)}
        if start_time_ms:
            params["startTime"] = start_time_ms
        if end_time_ms:
            params["endTime"] = end_time_ms

        try:
            res = self.session.get(url, params=params, timeout=self.timeout)
            if res.status_code == 200:
                return [
                    {
                        "timestamp": int(x.get("timestamp", 0)),
                        "buy_volume": float(x.get("buyVol", 0.0)),
                        "sell_volume": float(x.get("sellVol", 0.0)),
                        "buy_sell_ratio": float(x.get("buySellRatio", 1.0))
                    }
                    for x in res.json()
                ]
            return []
        except Exception as e:
            logger.error("Excepción en get_taker_long_short_ratio: %s", e)
            return []

    def get_premium_index_and_basis(self, symbol: str = "BTCUSDT") -> Dict[str, Any]:
        """Mark Price, Index Price, Basis actual y cuenta atrás de Funding."""
        clean_sym = symbol.replace("/", "").upper()
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

                return {
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
            return {}
        except Exception as e:
            logger.error("Excepción en get_premium_index_and_basis: %s", e)
            return {}

    def get_aggregated_derivatives_dashboard(
        self,
        symbol: str = "BTCUSDT",
        period: str = "1h",
        start_date_str: Optional[str] = None,
        end_date_str: Optional[str] = None,
        force_reload: bool = False
    ) -> Dict[str, Any]:
        """
        Consolida todas las métricas en un rango de fechas, verificando si ya
        están guardadas en disco local (Parquet) para no re-descargar de Binance.
        """
        clean_sym = symbol.replace("/", "").upper()
        clean_period = period.strip().lower()

        # Parsear timestamps
        start_ms = None
        end_ms = None
        s_tag = "start"
        e_tag = "now"

        if start_date_str:
            try:
                dt_s = datetime.strptime(start_date_str.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                start_ms = int(dt_s.timestamp() * 1000)
                s_tag = dt_s.strftime("%Y%m%d")
            except Exception:
                pass

        if end_date_str:
            try:
                dt_e = datetime.strptime(end_date_str.strip(), "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
                end_ms = int(dt_e.timestamp() * 1000)
                e_tag = dt_e.strftime("%Y%m%d")
            except Exception:
                pass

        # Ruta del archivo Parquet local
        parquet_filename = f"deriv_{clean_sym}_{clean_period}_{s_tag}_{e_tag}.parquet"
        parquet_path = os.path.join(STORAGE_DIR, parquet_filename)

        # 1. Comprobar si existe en disco local
        if not force_reload and os.path.exists(parquet_path):
            try:
                df_local = pd.read_parquet(parquet_path)
                logger.info("Cargados datos de derivados desde disco local: %s", parquet_path)
                return self._build_dashboard_from_dataframe(df_local, clean_sym, clean_period, from_cache=True, local_path=parquet_path)
            except Exception as e:
                logger.warning("No se pudo leer parquet local %s: %s. Descargando de red...", parquet_path, e)

        # 2. Descargar de Binance Futures
        logger.info("Descargando derivados de Binance API para %s (%s) [%s a %s]...", clean_sym, clean_period, s_tag, e_tag)
        premium = self.get_premium_index_and_basis(clean_sym)
        klines = self.get_historical_ohlcv_klines(clean_sym, interval=clean_period, start_time_ms=start_ms, end_time_ms=end_ms, limit=300)
        funding_hist = self.get_funding_rate_history(clean_sym, start_time_ms=start_ms, end_time_ms=end_ms, limit=100)
        oi_hist = self.get_open_interest_history(clean_sym, period=clean_period, start_time_ms=start_ms, end_time_ms=end_ms, limit=200)
        top_ls = self.get_top_long_short_account_ratio(clean_sym, period=clean_period, start_time_ms=start_ms, end_time_ms=end_ms, limit=200)
        glob_ls = self.get_global_long_short_account_ratio(clean_sym, period=clean_period, start_time_ms=start_ms, end_time_ms=end_ms, limit=200)
        taker_ls = self.get_taker_long_short_ratio(clean_sym, period=clean_period, start_time_ms=start_ms, end_time_ms=end_ms, limit=200)

        # 3. Guardar en disco local como Parquet estructurado
        try:
            self._save_dashboard_to_parquet(klines, funding_hist, oi_hist, top_ls, glob_ls, taker_ls, parquet_path)
        except Exception as e_save:
            logger.error("Error guardando parquet local: %s", e_save)

        # 4. Construir resumen para UI
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
            "period": clean_period,
            "from_cache": False,
            "local_path": parquet_path,
            "premium": premium,
            "klines": klines,
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

    def _save_dashboard_to_parquet(self, klines, funding, oi, top_ls, glob_ls, taker_ls, filepath):
        """Combina las métricas en DataFrames y las almacena en Parquet."""
        if not klines and not oi:
            return
        # Usar las velas klines como esqueleto temporal primario
        df_k = pd.DataFrame(klines) if klines else pd.DataFrame()
        if not df_k.empty:
            df_k["dt"] = pd.to_datetime(df_k["timestamp"], unit="ms")
            df_k.to_parquet(filepath, index=False)
            logger.info("Datos guardados exitosamente en Parquet: %s (%d registros)", filepath, len(df_k))

    def _build_dashboard_from_dataframe(self, df_k, symbol, period, from_cache=True, local_path=""):
        """Reconstruye el dashboard desde un DataFrame local de Parquet."""
        klines = df_k.to_dict(orient="records") if not df_k.empty else []
        premium = self.get_premium_index_and_basis(symbol)
        funding = self.get_funding_rate_history(symbol, limit=60)
        oi = self.get_open_interest_history(symbol, period=period, limit=60)
        top_ls = self.get_top_long_short_account_ratio(symbol, period=period, limit=60)
        glob_ls = self.get_global_long_short_account_ratio(symbol, period=period, limit=60)
        taker_ls = self.get_taker_long_short_ratio(symbol, period=period, limit=60)

        latest_oi_usd = oi[-1]["sum_open_interest_usd"] if oi else 0.0
        prev_oi_usd = oi[-2]["sum_open_interest_usd"] if len(oi) > 1 else latest_oi_usd
        oi_change_pct = ((latest_oi_usd - prev_oi_usd) / prev_oi_usd * 100.0) if prev_oi_usd > 0 else 0.0

        return {
            "symbol": symbol,
            "period": period,
            "from_cache": from_cache,
            "local_path": local_path,
            "premium": premium,
            "klines": klines,
            "funding_hist": funding,
            "oi_hist": oi,
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
                "latest_top_ratio": top_ls[-1]["long_short_ratio"] if top_ls else 1.0,
                "latest_top_long": top_ls[-1]["long_account_pct"] if top_ls else 50.0,
                "latest_glob_ratio": glob_ls[-1]["long_short_ratio"] if glob_ls else 1.0,
                "latest_glob_long": glob_ls[-1]["long_account_pct"] if glob_ls else 50.0,
                "latest_taker_ratio": taker_ls[-1]["buy_sell_ratio"] if taker_ls else 1.0,
                "sentiment_divergence": (top_ls[-1]["long_short_ratio"] - glob_ls[-1]["long_short_ratio"]) if (top_ls and glob_ls) else 0.0
            }
        }

    def export_dashboard_to_csv(self, data: Dict[str, Any], output_path: str) -> bool:
        """Exporta los datos de klines y métricas de derivados a un archivo CSV consolidado."""
        try:
            klines = data.get("klines", [])
            if not klines:
                return False
            df = pd.DataFrame(klines)
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms").dt.strftime("%Y-%m-%d %H:%M:%S")
            cols_order = ["datetime", "open", "high", "low", "close", "volume"]
            avail_cols = [c for c in cols_order if c in df.columns]
            df[avail_cols].to_csv(output_path, index=False)
            return True
        except Exception as e:
            logger.error("Error exportando a CSV: %s", e)
            return False
