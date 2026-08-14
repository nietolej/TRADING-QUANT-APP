import asyncio
from nicegui import ui, app
from web_gui.pages.live_monitor_page import LiveMonitorPage
import sys
import traceback

# Force UTF-8 stdout
sys.stdout.reconfigure(encoding='utf-8')

async def run_test():
    page = LiveMonitorPage()
    page.balance_input = type('obj', (object,), {'value': 10000})()
    page.param_inputs = {'FAST': type('obj', (object,), {'value': '1'})(), 'SLOW': type('obj', (object,), {'value': '35'})()}
    page.selected_strategy = 'ema_long.yaml'
    page.status_label = type('obj', (object,), {'set_text': lambda self, x: print("STATUS:", x)})()
    
    ui.notify = lambda *args, **kwargs: print("NOTIFY:", args, kwargs)
    
    print("Testing _start_bot...")
    try:
        await page._start_bot()
        print("Bot is_running:", page.trader.is_running)
        print("Klines DF empty?:", page.trader.klines_df.empty if hasattr(page.trader, 'klines_df') else 'No DF')
        print("Log lines:", page.state.get('log_lines'))
    except Exception as e:
        print("EXCEPTION IN START_BOT:")
        traceback.print_exc()
        sys.exit(1)
        
    print("Testing _ui_update_loop...")
    try:
        page.timer = type('obj', (object,), {'deactivate': lambda self: print("TIMER DEACTIVATED")})()
        page.trades_label = type('obj', (object,), {'set_text': lambda self, x: print("TRADES:", x)})()
        page.stats_label = type('obj', (object,), {'set_text': lambda self, x: print("STATS:", x)})()
        page.log_label = type('obj', (object,), {'set_text': lambda self, x: print("LOG:", x)})()
        page.balance_label = type('obj', (object,), {'set_text': lambda self, x: print("BAL:", x)})()
        page.pos_label = type('obj', (object,), {'set_text': lambda self, x: type('obj', (object,), {'classes': lambda self, replace: None})()})()
        page.chart = type('obj', (object,), {'update_figure': lambda self, fig: print("UPDATED FIGURE")})()
        
        page._ui_update_loop()
    except Exception as e:
        print("EXCEPTION IN _ui_update_loop:")
        traceback.print_exc()
        
    if page.trader and page.trader.is_running:
        page.trader.stop()

if __name__ == '__main__':
    asyncio.run(run_test())
