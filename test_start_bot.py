import asyncio
import sys
import traceback
from nicegui import ui, app

# Force UTF-8 stdout
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from execution_engine.bot_manager import bot_manager
from web_gui.pages.live_monitor_page import LiveMonitorPage


async def run_test():
    print("=== TEST 1: BotManager Multi-Bot Creation ===")
    bot_manager.stop_all()
    # Clear any previous test bots
    for b in list(bot_manager.get_all_bots()):
        bot_manager.delete_bot(b.bot_id)

    # 1. Crear 2 bots diferentes
    bot1 = bot_manager.create_bot(
        strategy_yaml_path="config/strategies/ema_long.yaml",
        name="Bot 1 (BTC/USDT)",
        symbol="BTC/USDT",
        timeframe="1m",
        initial_balance=1.0,
        currency="BTC"
    )
    bot2 = bot_manager.create_bot(
        strategy_yaml_path="config/strategies/ema_long.yaml",
        name="Bot 2 (ETH/USDT)",
        symbol="ETH/USDT",
        timeframe="5m",
        initial_balance=5000.0,
        currency="USDT"
    )

    all_bots = bot_manager.get_all_bots()
    print(f"Total bots creados: {len(all_bots)}")
    assert len(all_bots) == 2, f"Esperados 2 bots, obtenidos {len(all_bots)}"

    print("=== TEST 2: Iniciar Múltiples Bots Concurrentemente ===")
    ui.notify = lambda *args, **kwargs: print("NOTIFY:", args, kwargs)

    class MockUIElement:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def set_text(self, text):
            pass
        def classes(self, *args, **kwargs):
            return self
        def clear(self):
            pass
        def update(self):
            pass
        def update_figure(self, fig):
            pass

    page = LiveMonitorPage()
    page.kpi_bots_label = MockUIElement()
    page.kpi_balance_label = MockUIElement()
    page.kpi_pnl_label = MockUIElement()
    page.kpi_positions_label = MockUIElement()
    page.bots_cards_container = MockUIElement()
    page.inspector_status_label = MockUIElement()
    page.inspector_balance_label = MockUIElement()
    page.inspector_stats_label = MockUIElement()
    page.inspector_pos_label = MockUIElement()
    page.inspector_trades_label = MockUIElement()
    page.bid_label = MockUIElement()
    page.ask_label = MockUIElement()
    page.spread_label = MockUIElement()
    page.bid_qty_label = MockUIElement()
    page.ask_qty_label = MockUIElement()
    page.chart = MockUIElement()
    page.trades_grid = MockUIElement(options={'rowData': []})
    page.log_label = MockUIElement()

    # Arrancar bot 1
    await page._start_bot_async(bot1.bot_id)
    print(f"Bot 1 running: {bot1.is_running}, status: {bot1.status}")
    print(f"Bot 1 klines: {len(bot1.klines_df)} velas descargadas")

    # Arrancar bot 2
    await page._start_bot_async(bot2.bot_id)
    print(f"Bot 2 running: {bot2.is_running}, status: {bot2.status}")
    print(f"Bot 2 klines: {len(bot2.klines_df)} velas descargadas")

    print("=== TEST 3: Portfolio Summary ===")
    summary = bot_manager.get_portfolio_summary()
    print("Resumen de Cartera:", summary)
    assert summary['running_bots'] == 2, f"Esperados 2 bots corriendo, obtenidos {summary['running_bots']}"
    assert summary['balances_by_currency']['BTC'] == 1.0, f"Esperado 1.0 BTC, obtenido {summary['balances_by_currency'].get('BTC')}"
    assert summary['balances_by_currency']['USDT'] == 5000.0, f"Esperado 5000.0 USDT, obtenido {summary['balances_by_currency'].get('USDT')}"
    assert "BTC" in summary['balance_display'] and "USDT" in summary['balance_display']

    print("=== TEST 4: UI Refresh Loop ===")
    page._refresh_ui_elements()

    print("=== TEST 5: Detener Todos los Bots ===")
    page._stop_all_bots()
    print("Bots corriendo después de stop_all:", [b.is_running for b in bot_manager.get_all_bots()])
    summary_after_stop = bot_manager.get_portfolio_summary()
    assert summary_after_stop['running_bots'] == 0, "No debe haber bots corriendo"

    print("=== TEST 6: Eliminar Bot ===")
    page._delete_bot(bot2.bot_id)
    assert len(bot_manager.get_all_bots()) == 1, "Debe quedar 1 bot"

    print("\n✅ TODAS LAS PRUEBAS DE MULTI-BOT PASARON EXITOSAMENTE.")


if __name__ == '__main__':
    asyncio.run(run_test())
