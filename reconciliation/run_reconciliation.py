"""
Ejecuta una reconciliación de órdenes entre la app y Binance (Testnet/Demo por defecto).

Uso:
    python -m reconciliation.run_reconciliation
    python -m reconciliation.run_reconciliation --symbols BTCUSDT ETHUSDT --hours 12
    python -m reconciliation.run_reconciliation --real   # concilia contra Binance Real en vez de Demo

Pensado para correr como tarea programada (Task Scheduler / cron) independiente del proceso
principal de la app, ya que sólo lee el ledger local y el historial de Binance.
"""
import argparse
import json
import logging

from data_layer.storage import init_db
from reconciliation.reconciler import OrderReconciler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("ReconciliationCLI")


def main():
    parser = argparse.ArgumentParser(
        description="Concilia las órdenes enviadas por la app contra el historial real de Binance."
    )
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"], help="Símbolos a conciliar")
    parser.add_argument("--hours", type=float, default=24.0, help="Ventana de horas hacia atrás a conciliar")
    parser.add_argument(
        "--real", action="store_true",
        help="Conciliar contra Binance Real (Mainnet) en vez de Testnet/Demo (por defecto)"
    )
    parser.add_argument("--no-notify", action="store_true", help="No enviar alertas por Telegram")
    args = parser.parse_args()

    init_db()
    reconciler = OrderReconciler(use_testnet=not args.real, notify=not args.no_notify)
    summary = reconciler.run(symbols=args.symbols, lookback_hours=args.hours)

    logger.info(
        "Reconciliación completada [%s] | revisadas=%s | críticas=%s | resumen=%s",
        summary["network"], summary["total_checked"], summary["critical_count"], summary["summary"]
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))

    # Código de salida distinto de 0 si hay discrepancias críticas: útil para que un
    # scheduler externo (cron/Task Scheduler) detecte fallos sin tener que parsear el JSON.
    if summary["critical_count"] > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
