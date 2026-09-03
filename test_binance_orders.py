import os
import sys
from dotenv import load_dotenv

# Asegurar codificación utf-8 en consola de Windows
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Cargar variables de entorno
load_dotenv()

from execution_engine.binance_client import BinanceTestnetClient

def run_test():
    print("=" * 60)
    print("TEST: PRUEBA DE CONECTIVIDAD Y EJECUCION CON BINANCE FUTURES TESTNET")
    print("=" * 60)

    client = BinanceTestnetClient(use_testnet=True)

    print(f"\n1. Verificando Credenciales API...")
    print(f"   - API Key configurada: {'SI' if client.api_key else 'NO'}")
    print(f"   - Secret Key configurada: {'SI' if client.api_secret else 'NO'}")

    if not client.api_key or not client.api_secret:
        print("\n[ERROR] No se encontraron las credenciales en .env")
        return

    print(f"\n2. Conectando con Binance Futures Testnet (https://testnet.binancefuture.com)...")
    try:
        acc = client.client.futures_account()
        usdt_assets = [a for a in acc.get('assets', []) if a.get('asset') == 'USDT']
        bal = float(usdt_assets[0].get('walletBalance', 0.0)) if usdt_assets else 0.0
        print(f"   [OK] Conexión Exitosa!")
        print(f"   Saldo Disponible en Testnet: {bal:,.2f} USDT")
    except Exception as e:
        print(f"   [ERROR] Error de Conexión: {e}")
        return

    print(f"\n3. Enviando Orden de Prueba: BUY 0.001 BTCUSDT (MARKET)...")
    buy_order, err = client.place_futures_order(symbol="BTC/USDT", side="long", quantity=0.001)
    if err or not buy_order:
        print(f"   [ERROR] Error al enviar orden de compra: {err}")
        return
    
    order_id = buy_order.get('orderId')
    status = buy_order.get('status')
    avg_price = buy_order.get('avgPrice')
    print(f"   [OK] ORDEN DE COMPRA EJECUTADA CON EXITO!")
    print(f"   Order ID: {order_id} | Status: {status} | Precio: {avg_price or 'Mercado'}")

    print(f"\n4. Enviando Orden de Cierre Inmediato: SELL 0.001 BTCUSDT (reduceOnly)...")
    close_order, err2 = client.close_futures_position(symbol="BTC/USDT", side="long", quantity=0.001)
    if err2 or not close_order:
        print(f"   [ERROR] Error al cerrar la posición: {err2}")
        return

    close_id = close_order.get('orderId')
    close_status = close_order.get('status')
    print(f"   [OK] ORDEN DE CIERRE EJECUTADA CON EXITO!")
    print(f"   Order ID: {close_id} | Status: {close_status}")

    print("\n" + "=" * 60)
    print("RESULTADO: LA CONEXION Y EJECUCION DE ORDENES FUNCIONA AL 100%")
    print(f"Puedes verificar ambas ordenes en Binance Testnet:")
    print("https://testnet.binancefuture.com/en/futures/BTCUSDT (Trade History)")
    print("=" * 60)

if __name__ == "__main__":
    run_test()
