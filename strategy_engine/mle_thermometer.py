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
            
            self.last_theta = theta
            self.last_metrics = {
                'theta': round(theta, 2),
                'n_ssr': round(n_ssr, 2),
                'n_inf': round(n_inf, 2),
                'n_lev': round(n_lev, 2),
                'raw_ssr': round(raw_ssr, 3) if raw_ssr is not None else None,
                'raw_inf': round(raw_inf, 3) if raw_inf is not None else None,
                'raw_lev': round(raw_lev, 3) if raw_lev is not None else None,
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
                        'history_ssr': pd.Series(), 'history_inf': pd.Series(), 'history_lev': pd.Series(), 'history_btc': pd.Series(),
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
        params = {
            "symbol": "BTCUSDT",
            "interval": "1d",
            "limit": self.z_score_window + 10
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
        """Descarga Open Interest de BTC desde Binance Futures"""
        url = "https://fapi.binance.com/futures/data/openInterestHist"
        params = {
            "symbol": "BTCUSDT",
            "period": "1d",
            "limit": self.z_score_window + 10
        }
        resp = requests.get(url, params=params)
        if resp.status_code == 200:
            data = resp.json()
            df = pd.DataFrame(data)
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df['sumOpenInterestValue'] = df['sumOpenInterestValue'].astype(float)
            df.set_index('timestamp', inplace=True)
            return df['sumOpenInterestValue']
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
            return 50.0, pd.Series(), None
            
        stables_mc.index = stables_mc.index.normalize()
        btc_price.index = btc_price.index.normalize()
        
        df = pd.concat([stables_mc, btc_price], axis=1, join='inner')
        df.columns = ['stable_mc', 'btc_price']
        
        # Asumimos ~19.7M BTC en circulación
        df['btc_mc'] = df['btc_price'] * 19_700_000
        
        # Calcular SSR
        df['ssr'] = df['btc_mc'] / df['stable_mc']
        
        # Calcular Z-Score
        window = min(self.z_score_window, len(df))
        df['ssr_mean'] = df['ssr'].rolling(window=window).mean()
        df['ssr_std'] = df['ssr'].rolling(window=window).std()
        
        df['z_score'] = (df['ssr'] - df['ssr_mean']) / df['ssr_std']
        
        current_z = df['z_score'].iloc[-1]
        raw_ssr = current_z
        if pd.isna(current_z):
            return 50.0, pd.Series(), None
            
        current_z = max(-2, min(2, current_z))
        n_ssr = 100 - ((current_z + 2) / 4) * 100
        
        # Para la gráfica, normalizamos toda la serie de Z-Score de la misma forma para ver la historia
        # clip(-2, 2) y normalización
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
            return 50.0, pd.Series(), None
            
        delta = stables_mc.diff()
        ema_7 = delta.ewm(span=7, adjust=False).mean()
        
        window_data = ema_7.tail(self.z_score_window)
        current_val = window_data.iloc[-1]
        
        min_val = window_data.min()
        max_val = window_data.max()
        
        raw_inf = current_val
        if pd.isna(current_val) or max_val == min_val:
            return 50.0, pd.Series(), None
            
        n_inf = ((current_val - min_val) / (max_val - min_val)) * 100
        
        hist_n_inf = ((window_data - min_val) / (max_val - min_val)) * 100
        hist_raw_inf = window_data
        return n_inf, hist_n_inf, raw_inf, hist_raw_inf

    def _calculate_n_lev(self):
        """
        Calcula N_lev (Riesgo de Estructura).
        Ratio = Open Interest / Stablecoin Reserves (usamos Total Stables).
        Escala invertida: Ratio bajo = 100, Ratio alto = 0.
        """
        oi = self._fetch_binance_open_interest()
        stables_mc = self._fetch_defillama_stablecoins()
        
        if oi.empty or stables_mc.empty:
            return 50.0, pd.Series(), None
            
        oi.index = oi.index.normalize()
        stables_mc.index = stables_mc.index.normalize()
        
        df = pd.concat([oi, stables_mc], axis=1, join='inner')
        df.columns = ['oi', 'stable_mc']
        
        df['lev_ratio'] = df['oi'] / df['stable_mc']
        
        window_data = df['lev_ratio'].tail(self.z_score_window)
        current_val = window_data.iloc[-1]
        
        min_val = window_data.min()
        max_val = window_data.max()
        
        raw_lev = current_val
        if pd.isna(current_val) or max_val == min_val:
            return 50.0, pd.Series(), None
            
        n_lev = 100 - (((current_val - min_val) / (max_val - min_val)) * 100)
        hist_n_lev = 100 - (((window_data - min_val) / (max_val - min_val)) * 100)
        hist_raw_lev = window_data
        return n_lev, hist_n_lev, raw_lev, hist_raw_lev
