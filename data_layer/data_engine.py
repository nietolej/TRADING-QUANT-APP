from datetime import datetime, timezone
import pandas as pd
from typing import List, Callable, Optional

from data_layer.market_data import MarketDataManager
from data_layer.onchain_data import OnChainDataManager
from data_layer.storage import SessionLocal

class DataAggregator:
    """
    Central Engine to coordinate the download of all available data 
    (Market Data from Exchanges/Yahoo, and On-Chain Data from DeFiLlama, CryptoQuant, CoinGecko).
    """
    
    def __init__(self, db_session=None):
        self.db = db_session or SessionLocal()
        self.market_manager = MarketDataManager(self.db)
        self.onchain_manager = OnChainDataManager(self.db)
        
        # Mapeo predeterminado de métricas a sus respectivos proveedores
        self.onchain_metrics_map = {
            'defillama': ['stablecoin_market_cap', 'usdt_market_cap', 'usdc_market_cap'],
            'cryptoquant': [
                'exchange_netflow', 'exchange_inflow', 'exchange_outflow', 'exchange_reserve',
                'miner_reserve', 'miner_netflow', 'puell_multiple', 'mvrv', 'nvt_golden_cross', 
                'sopr', 'active_addresses', 'funding_rates', 'open_interest', 
                'estimated_leverage_ratio', 'taker_buy_sell_ratio', 'nupl', 'stock_to_flow'
            ],
            'coingecko': ['btc_market_cap', 'btc_volume']
        }
        
    def sync_all_data(
        self, 
        symbols: List[str], 
        timeframe: str, 
        start_date: datetime, 
        end_date: Optional[datetime] = None,
        progress_callback: Optional[Callable[[str], None]] = None
    ):
        """
        Descarga datos OHLCV para los símbolos indicados y todas las métricas On-Chain disponibles.
        """
        
        def emit_prog(msg):
            if progress_callback:
                progress_callback(msg)
                
        emit_prog(f"Starting Unified Sync for {len(symbols)} symbols from {start_date} to {end_date or 'now'}")
        
        # 1. MARKET DATA
        emit_prog("\n--- Phase 1: Market Data ---")
        for sym in symbols:
            emit_prog(f"Fetching Market Data (OHLCV) for {sym}...")
            try:
                self.market_manager.update_historical_data(
                    symbol=sym,
                    timeframe=timeframe,
                    start_date=start_date,
                    end_date=end_date,
                    progress_callback=emit_prog,
                    source="binance"
                )
            except Exception as e:
                emit_prog(f"Error fetching Market Data for {sym}: {e}")
                
        # 2. ON-CHAIN DATA
        emit_prog("\n--- Phase 2: On-Chain Data ---")
        # On-Chain Data is generally daily, but we fetch it for the timeframe requested for alignment.
        for provider, metrics in self.onchain_metrics_map.items():
            for metric in metrics:
                for sym in symbols:
                    emit_prog(f"Fetching On-Chain Metric: {metric} ({sym}) from {provider}...")
                    try:
                        self.onchain_manager.update_historical_data(
                            metric_name=metric,
                            symbol=sym,
                            start_date=start_date,
                            provider_name=provider
                        )
                        emit_prog(f"Updated {metric} for {sym}.")
                    except Exception as e:
                        emit_prog(f"Error fetching {metric} from {provider}: {e}")
                        
        emit_prog("\n=== Unified Sync Completed ===")
        
    def __del__(self):
        if hasattr(self, 'db') and self.db:
            self.db.close()
