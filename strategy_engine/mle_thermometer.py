import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta

class MLEThermometer:
    """
    Modelo de Filtro de Liquidez Estructural (MLE) - Termómetro de Mercado.
    Calcula un valor de 0 a 100 integrando:
    1. Z-Score del SSR (Poder Adquisitivo) - 40%
    2. Delta Inflows de Stablecoins (Velocidad de Capital) - 30%
    3. Ratio de Apalancamiento Sostenido (Riesgo de Estructura) - 30%
    """
    def __init__(self, z_score_window=90):
        self.z_score_window = z_score_window
        self.weights = {
            'ssr': 0.40,
            'inf': 0.30,
            'lev': 0.30
        }
        # Caché simple
        self.last_theta = 50.0
        self.last_metrics = {}
        self.last_update = None
        
    def set_weights(self, ssr_w, inf_w, lev_w):
        """Actualiza y normaliza los pesos (asegurando que sumen 1.0 internamente)"""
        total = ssr_w + inf_w + lev_w
        if total <= 0:
            total = 100
            ssr_w, inf_w, lev_w = 40, 30, 30
            
        self.weights = {
            'ssr': ssr_w / total,
            'inf': inf_w / total,
            'lev': lev_w / total
        }
        self.last_update = None # Forzar recálculo
        
    def get_thermometer_value(self) -> dict:
        """
        Obtiene el valor actual del termómetro y sus componentes.
        Retorna un dict con theta, n_ssr, n_inf, n_lev.
        """
        # Evitar sobrecargar APIs si se llama muy seguido (caché de 1 hora)
        if self.last_update and (datetime.now() - self.last_update).total_seconds() < 3600:
            return self.last_metrics
            
        try:
            # Traer el precio de BTC completo una sola vez para las gráficas
            btc_series = self._fetch_binance_btc_prices()
            btc_hist = btc_series.tail(self.z_score_window) if not btc_series.empty else pd.Series()
            
            n_ssr, hist_ssr, raw_ssr, hist_raw_ssr = self._calculate_n_ssr(btc_series)
            n_inf, hist_inf, raw_inf, hist_raw_inf = self._calculate_n_inf()
            n_lev, hist_lev, raw_lev, hist_raw_lev = self._calculate_n_lev()
            
            # Ecuación del Termómetro: Θ = 0.40(N_ssr) + 0.30(N_inf) + 0.30(N_lev)
            theta = (self.weights['ssr'] * n_ssr) + \
                    (self.weights['inf'] * n_inf) + \
                    (self.weights['lev'] * n_lev)
                    
            # Asegurar que esté entre 0 y 100
            theta = max(0, min(100, theta))
            
            # Calcular serie histórica combinada de Theta
            hist_theta = pd.Series(dtype=float)
            if not hist_ssr.empty or not hist_inf.empty or not hist_lev.empty:
                series_list = []
                if not hist_ssr.empty: series_list.append(hist_ssr.rename('ssr'))
                if not hist_inf.empty: series_list.append(hist_inf.rename('inf'))
                if not hist_lev.empty: series_list.append(hist_lev.rename('lev'))
                if series_list:
                    df_comb = pd.concat(series_list, axis=1).ffill().bfill()
                    # Si falta alguna columna, usar 50 como fallback
                    ssr_col = df_comb['ssr'] if 'ssr' in df_comb else 50.0
                    inf_col = df_comb['inf'] if 'inf' in df_comb else 50.0
                    lev_col = df_comb['lev'] if 'lev' in df_comb else 50.0
                    hist_theta = (self.weights['ssr'] * ssr_col) + \
                                 (self.weights['inf'] * inf_col) + \
                                 (self.weights['lev'] * lev_col)
                    hist_theta = hist_theta.clip(0, 100).tail(self.z_score_window)

            self.last_theta = theta
            self.last_metrics = {
                'theta': round(theta, 2),
                'n_ssr': round(n_ssr, 2),
                'n_inf': round(n_inf, 2),
                'n_lev': round(n_lev, 2),
                'raw_ssr': round(raw_ssr, 3) if raw_ssr is not None else None,
                'raw_inf': round(raw_inf, 3) if raw_inf is not None else None,
                'raw_lev': round(raw_lev, 3) if raw_lev is not None else None,
                'history_theta': hist_theta,
                'history_ssr': hist_ssr,
                'history_inf': hist_inf,
                'history_lev': hist_lev,
                'history_raw_ssr': hist_raw_ssr,
                'history_raw_inf': hist_raw_inf,
                'history_raw_lev': hist_raw_lev,
                'history_btc': btc_hist
            }
            self.last_update = datetime.now()
            return self.last_metrics
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Error calculando Termómetro MLE: {e}")
            # Si hay error en la red o APIs, devolver el último conocido o neutral (50)
            if not self.last_metrics:
                return {'theta': 50.0, 'n_ssr': 50.0, 'n_inf': 50.0, 'n_lev': 50.0, 
                        'raw_ssr': 0, 'raw_inf': 0, 'raw_lev': 0,
                        'history_theta': pd.Series(), 'history_ssr': pd.Series(), 'history_inf': pd.Series(), 'history_lev': pd.Series(), 'history_btc': pd.Series(),
                        'history_raw_ssr': pd.Series(), 'history_raw_inf': pd.Series(), 'history_raw_lev': pd.Series()}
            return self.last_metrics

    def _fetch_defillama_stablecoins(self):
        """Descarga el histórico de Market Cap de Stablecoins desde DefiLlama (Gratis)"""
        url = "https://stablecoins.llama.fi/stablecoincharts/all"
        resp = requests.get(url)
        if resp.status_code == 200:
            data = resp.json()
            df = pd.DataFrame(data)
            df['date'] = pd.to_datetime(df['date'].astype(int), unit='s')
            df.set_index('date', inplace=True)
            mc = df['totalCirculatingUSD'].apply(lambda x: x.get('peggedUSD', 0) if isinstance(x, dict) else 0)
            return mc
        return pd.Series()

    def _fetch_binance_btc_prices(self):
        """Descarga el histórico de precios de BTC desde Binance para calcular Market Cap"""
        url = "https://api.binance.com/api/v3/klines"
        # Traer suficiente profundidad histórica para que la ventana móvil tenga datos previos
        limit = min(1000, max(500, self.z_score_window + 365))
        params = {
            "symbol": "BTCUSDT",
            "interval": "1d",
            "limit": limit
        }
        resp = requests.get(url, params=params)
        if resp.status_code == 200:
            data = resp.json()
            df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df['close'] = df['close'].astype(float)
            df.set_index('timestamp', inplace=True)
            return df['close']
        return pd.Series()

    def _fetch_binance_open_interest(self):
        """
        Descarga Open Interest de BTC desde Binance Futures.
        Combina datos reales (últimos 30 días) con el volumen nocional de futuros de 1000 días
        calibrado para proveer una serie continua histórica sin interrupciones ni vacíos.
        """
        try:
            # 1. Traer 1000 días de velas diarias de futuros (quote_volume nocional en USD)
            url_fapi = "https://fapi.binance.com/fapi/v1/klines"
            params_fapi = {"symbol": "BTCUSDT", "interval": "1d", "limit": 1000}
            resp_fapi = requests.get(url_fapi, params=params_fapi, timeout=10)
            
            if resp_fapi.status_code != 200:
                return pd.Series()
                
            data_fapi = resp_fapi.json()
            df_fapi = pd.DataFrame(data_fapi, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_volume', 'trades', 'taker_base', 'taker_quote', 'ignore'])
            df_fapi['timestamp'] = pd.to_datetime(df_fapi['timestamp'], unit='ms').dt.normalize()
            df_fapi['quote_volume'] = df_fapi['quote_volume'].astype(float)
            df_fapi.set_index('timestamp', inplace=True)
            
            # 2. Traer los datos exactos de Open Interest (disponibles en los últimos ~30 días por API pública)
            url_oi = "https://fapi.binance.com/futures/data/openInterestHist"
            params_oi = {"symbol": "BTCUSDT", "period": "1d", "limit": 500}
            resp_oi = requests.get(url_oi, params=params_oi, timeout=10)
            
            if resp_oi.status_code == 200:
                data_oi = resp_oi.json()
                if isinstance(data_oi, list) and len(data_oi) > 0:
                    df_oi = pd.DataFrame(data_oi)
                    df_oi['timestamp'] = pd.to_datetime(df_oi['timestamp'], unit='ms').dt.normalize()
                    df_oi['oi'] = df_oi['sumOpenInterestValue'].astype(float)
                    df_oi.set_index('timestamp', inplace=True)
                    
                    # Factor de calibración promedio entre OI real y volumen de futuros
                    df_joined = pd.concat([df_fapi['quote_volume'], df_oi['oi']], axis=1).dropna()
                    calib_factor = (df_joined['oi'] / df_joined['quote_volume']).median() if not df_joined.empty else 1.0
                    
                    # Crear serie completa de 1000 días calibrada y actualizar con los valores reales
                    full_oi = df_fapi['quote_volume'] * calib_factor
                    full_oi.update(df_oi['oi'])
                    return full_oi
                    
            return df_fapi['quote_volume']
        except Exception as e:
            print(f"Error fetching Open Interest: {e}")
            return pd.Series()

    def _calculate_n_ssr(self, btc_price_series=None):
        """
        Calcula N_ssr (Poder Adquisitivo).
        Mide si hay suficientes dólares (Stablecoins) respecto a BTC.
        SSR = BTC Market Cap / Stablecoin Market Cap.
        Escala invertida: Z-score bajo (-2) = 100, alto (+2) = 0.
        """
        stables_mc = self._fetch_defillama_stablecoins()
        btc_price = btc_price_series if btc_price_series is not None and not btc_price_series.empty else self._fetch_binance_btc_prices()
        
        if stables_mc.empty or btc_price.empty:
            return 50.0, pd.Series(), None, pd.Series()
            
        stables_mc.index = stables_mc.index.normalize()
        btc_price.index = btc_price.index.normalize()
        
        df = pd.concat([stables_mc, btc_price], axis=1, join='inner').dropna()
        df.columns = ['stable_mc', 'btc_price']
        
        # Asumimos ~19.7M BTC en circulación
        df['btc_mc'] = df['btc_price'] * 19_700_000
        
        # Calcular SSR
        df['ssr'] = df['btc_mc'] / df['stable_mc']
        
        # Calcular Z-Score con min_periods para evitar NaNs iniciales
        window = max(5, self.z_score_window)
        min_p = max(5, min(30, window // 2))
        df['ssr_mean'] = df['ssr'].rolling(window=window, min_periods=min_p).mean()
        df['ssr_std'] = df['ssr'].rolling(window=window, min_periods=min_p).std().replace(0, np.nan)
        
        df['z_score'] = ((df['ssr'] - df['ssr_mean']) / df['ssr_std']).fillna(0)
        
        current_z = df['z_score'].iloc[-1]
        raw_ssr = current_z
        
        current_z_clipped = max(-2, min(2, current_z))
        n_ssr = 100 - ((current_z_clipped + 2) / 4) * 100
        
        # Normalización histórica continua
        hist_n_ssr = 100 - ((df['z_score'].clip(-2, 2) + 2) / 4) * 100
        hist_raw_ssr = df['z_score'].tail(self.z_score_window)
        
        return n_ssr, hist_n_ssr.tail(self.z_score_window), raw_ssr, hist_raw_ssr

    def _calculate_n_inf(self):
        """
        Calcula N_inf (Velocidad de Capital).
        Flujo positivo/creciente = 100, Redenciones/caída = 0.
        Usamos la derivada del Total Stablecoin MarketCap como Proxy.
        """
        stables_mc = self._fetch_defillama_stablecoins()
        if stables_mc.empty:
            return 50.0, pd.Series(), None, pd.Series()
            
        delta = stables_mc.diff()
        ema_7 = delta.ewm(span=7, adjust=False).mean()
        
        w = max(5, self.z_score_window)
        min_p = max(5, min(30, w // 2))
        roll_min = ema_7.rolling(window=w, min_periods=min_p).min()
        roll_max = ema_7.rolling(window=w, min_periods=min_p).max()
        denom = (roll_max - roll_min).replace(0, np.nan)
        hist_n_inf = (((ema_7 - roll_min) / denom) * 100).fillna(50).clip(0, 100)
        
        current_n_inf = hist_n_inf.iloc[-1] if not hist_n_inf.empty else 50.0
        current_raw = ema_7.iloc[-1] if not ema_7.empty else 0.0
        
        return current_n_inf, hist_n_inf.tail(self.z_score_window), current_raw, ema_7.tail(self.z_score_window)

    def _calculate_n_lev(self):
        """
        Calcula N_lev (Riesgo de Estructura).
        Ratio = Open Interest / Stablecoin Reserves (usamos Total Stables).
        Escala invertida: Ratio bajo = 100, Ratio alto = 0.
        """
        oi = self._fetch_binance_open_interest()
        stables_mc = self._fetch_defillama_stablecoins()
        
        if oi.empty or stables_mc.empty:
            return 50.0, pd.Series(), None, pd.Series()
            
        oi.index = oi.index.normalize()
        stables_mc.index = stables_mc.index.normalize()
        
        df = pd.concat([oi, stables_mc], axis=1, join='inner').dropna()
        df.columns = ['oi', 'stable_mc']
        df['lev_ratio'] = df['oi'] / df['stable_mc']
        
        w = max(5, self.z_score_window)
        min_p = max(5, min(30, w // 2))
        roll_min = df['lev_ratio'].rolling(window=w, min_periods=min_p).min()
        roll_max = df['lev_ratio'].rolling(window=w, min_periods=min_p).max()
        denom = (roll_max - roll_min).replace(0, np.nan)
        hist_n_lev = (100 - (((df['lev_ratio'] - roll_min) / denom) * 100)).fillna(50).clip(0, 100)
        
        current_n_lev = hist_n_lev.iloc[-1] if not hist_n_lev.empty else 50.0
        current_raw = df['lev_ratio'].iloc[-1] if not df['lev_ratio'].empty else 0.0
        
        return current_n_lev, hist_n_lev.tail(self.z_score_window), current_raw, df['lev_ratio'].tail(self.z_score_window)
