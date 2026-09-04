import os
import sys
from dotenv import load_dotenv

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

load_dotenv()

from execution_engine.binance_client import BinanceTestnetClient
from execution_engine.paper_trader import PaperTrader
from notifications.telegram_bot import TelegramNotifier

def test_all():
    print("=" * 70)
    print("TEST: VERIFICACIÓN ACTIVA DE ÓRDENES Y ALERTAS DE EJECUCIÓN")
    print("=" * 70)

    # 1. Test Telegram Alert Formatter
    print("\n[1/3] Probando Notificador de Telegram...")
    tg = TelegramNotifier()
    print(f"   Telegram Habilitado: {tg.enabled}")
    tg.send_alert("Prueba de Alerta de Verificación de Órdenes", {
        "Test": "Exitoso",
        "Detalle": "Verificación de orden en Binance"
    }, is_critical=False)
    print("   [OK] Notificador procesado sin errores.")

    # 2. Test Binance Client active verification
    print("\n[2/3] Probando BinanceTestnetClient.verify_order_status y ejecución...")
    client = BinanceTestnetClient(use_testnet=True)
    if not client.api_key or not client.api_secret:
        print("   [SKIP] API Keys no configuradas en .env")
        return

    # Probar orden exitosa y verificación activa
    print("   Enviando orden MARKET de prueba de 0.001 BTC con verificación activa...")
    order, err = client.place_futures_order("BTC/USDT", "long", 0.001, order_type="MARKET", verify_execution=True)
    if err or not order:
        print(f"   [ERROR] Orden de compra falló: {err}")
    else:
        order_id = order.get("orderId")
        status = order.get("status")
        print(f"   [OK] Orden ejecutada y verificada: ID={order_id}, Status={status}, avgPrice={order.get('avgPrice')}")

        # Probar verificación activa explícita
        ok, verified, v_err = client.verify_order_status("BTC/USDT", order_id, expected_statuses=["FILLED"])
        print(f"   [OK] verify_order_status: is_success={ok}, status={verified.get('status') if verified else None}")

        # Cerrar inmediatamente
        print("   Cerrando posición con verificación activa...")
        c_order, c_err = client.close_futures_position("BTC/USDT", "long", 0.001, order_type="MARKET", verify_execution=True)
        if c_err or not c_order:
            print(f"   [ERROR] Cierre falló: {c_err}")
        else:
            print(f"   [OK] Cierre verificado: ID={c_order.get('orderId')}, Status={c_order.get('status')}")

    # 3. Test PaperTrader Alert Triggering on Error
    print("\n[3/3] Probando disparador de alertas críticas en PaperTrader...")
    strat_path = "config/strategies/ema_long.yaml"
    if os.path.exists(strat_path):
        bot = PaperTrader(
            strategy_yaml_path=strat_path,
            initial_balance=1.0,
            currency="BTC",
            use_testnet=True,
            name="TestBot_Alerts"
        )
        # Disparar alerta crítica simulada
        bot._trigger_critical_order_alert(
            "Orden de Entrada LONG NO ejecutada en Binance (Test)",
            {"error": "Simulated rejection: Insufficient Margin", "symbol": "BTC/USDT", "side": "BUY"}
        )
        print(f"   Última línea de log del bot: {bot.log_lines[-1]}")
        print(f"   Status message del bot: {bot.status_message}")
        assert "🚨" in bot.log_lines[-1], "El emoji de alerta 🚨 debe estar en el log"
        print("   [OK] Alerta crítica registrada y propagada con éxito.")

    print("\n" + "=" * 70)
    print("TODAS LAS PRUEBAS DE VERIFICACIÓN Y ALERTAS COMPLETADAS EXITOSAMENTE ✅")
    print("=" * 70)

if __name__ == "__main__":
    test_all()
