"""
Re-descarga desde Binance global las series OHLCV guardadas y corrige las velas que
difieran (upsert), incluidas las que quedaron incompletas o se bajaron de Binance US.

Uso:
    python scripts/repair_ohlcv_from_binance.py                  # todas las series "BASE/QUOTE"
    python scripts/repair_ohlcv_from_binance.py BTC/USDT:1d ETH/USDT:1d

Hace antes una copia de seguridad de la BD en data/backups/.
"""
import os
import sqlite3
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from sqlalchemy import func  # noqa: E402

from data_layer.market_data import MarketDataManager, drop_unclosed_candles, ensure_utc  # noqa: E402
from data_layer.storage import OHLCV, SessionLocal  # noqa: E402

DB_PATH = os.path.join(ROOT, "data", "trading_quant.db")


def backup_db() -> str:
    os.makedirs(os.path.join(ROOT, "data", "backups"), exist_ok=True)
    dest = os.path.join(ROOT, "data", "backups", f"trading_quant_{datetime.now():%Y%m%d_%H%M%S}.db")
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)  # copia consistente aunque la app esté usando la BD (WAL)
    src.close()
    dst.close()
    return dest


def repair(mgr: MarketDataManager, symbol: str, timeframe: str, since: datetime) -> tuple:
    inserted = updated = 0
    since = ensure_utc(since)
    while True:
        raw = mgr.fetch_ohlcv(symbol, timeframe, since=since)
        df = drop_unclosed_candles(raw, timeframe)
        if df is None or df.empty:
            break
        ins, upd = mgr._save_df_to_db(df)
        inserted += ins
        updated += upd
        last = ensure_utc(df['timestamp'].iloc[-1])
        if len(df) < len(raw) or last <= since:
            break
        since = last
    return inserted, updated


def main(argv):
    print("Copia de seguridad:", backup_db())
    db = SessionLocal()
    mgr = MarketDataManager(db)
    try:
        ranges = {
            (s, tf): mn for s, tf, mn in db.query(OHLCV.symbol, OHLCV.timeframe, func.min(OHLCV.timestamp))
            .group_by(OHLCV.symbol, OHLCV.timeframe).all()
        }
        if argv:
            targets = [tuple(a.split(":")) for a in argv]
        else:
            # Solo series en formato ccxt "BASE/QUOTE" (las de Yahoo usan "BTC-USD").
            targets = sorted(k for k in ranges if "/" in k[0])
        for symbol, tf in targets:
            start = ranges.get((symbol, tf))
            if start is None:
                print(f"{symbol} {tf}: no existe en la BD, se omite")
                continue
            ins, upd = repair(mgr, symbol, tf, start)
            print(f"{symbol:<10} {tf:<3} desde {str(start)[:10]}: {upd:>6} velas corregidas, {ins:>5} nuevas")
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1:])
