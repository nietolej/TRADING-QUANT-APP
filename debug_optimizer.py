import pandas as pd
import asyncio
from datetime import datetime, timezone
import traceback
import sys

from data_layer.market_data import MarketDataManager, normalize_timeframe
from backtest_engine.optimizer import run_grid_search

def dummy_progress(done, total):
    if done % 10 == 0 or done == total:
        print(f"Progreso: {done}/{total} ({(done/total)*100:.1f}%)")

async def test_optimizer():
    try:
        print("Obteniendo datos...")
        from data_layer.storage import SessionLocal
        db = SessionLocal()
        mgr = MarketDataManager(db)
        df = mgr.get_data("BTC/USDT", "15m", start_date=datetime(2024,1,1, tzinfo=timezone.utc), end_date=datetime(2024,2,1, tzinfo=timezone.utc))
        db.close()
        
        if df.empty:
            print("Error: DataFrame vacío")
            return
            
        print(f"Datos obtenidos: {len(df)} velas.")
        
        strategy_path = "c:\\Users\\pc\\Documents\\PROYECTOS IDE\\TRADING-QUANT-APP\\config\\strategies\\cruce_de_ema.yaml"
        
        # Ranges for 2 parameters (approx 4x4 = 16 combinations)
        param_ranges = {
            "FAST": {"min": 10, "max": 25, "step": 5},
            "LOW": {"min": 30, "max": 60, "step": 10}
        }
        
        print(f"Iniciando optimización con rangos: {param_ranges}")
        start_time = datetime.now()
        
        results = run_grid_search(
            strategy_path=strategy_path,
            df=df,
            initial_capital=1000.0,
            param_ranges=param_ranges,
            optimize_metric='cagr',
            progress_callback=dummy_progress
        )
        
        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"\n¡Optimización Completada en {elapsed:.2f} segundos!")
        print(f"Se procesaron {len(results)} combinaciones.")
        
        print("\n--- MEJOR RESULTADO ---")
        if results:
            best = results[0]
            print(f"Parámetros: {best.get('params')}")
            print(f"CAGR: {best.get('cagr')}%")
            print(f"Sharpe: {best.get('sharpe_ratio')}")
            print(f"Max DD: {best.get('max_drawdown_pct')}%")
            print(f"Trades: {best.get('total_trades')}")
            if 'error' in best:
                print(f"ERROR CAPTURADO: {best['error']}")
        
    except Exception as e:
        print(f"ERROR FATAL:\n{traceback.format_exc()}")

if __name__ == "__main__":
    # Necesario en Windows para evitar RuntimeError: Event loop is closed
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test_optimizer())
