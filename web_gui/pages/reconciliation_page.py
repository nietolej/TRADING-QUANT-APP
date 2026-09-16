"""
Página de Conciliación App ↔ Binance.

Muestra, sobre el módulo separado `reconciliation/`, si las órdenes que la app envió
realmente se ejecutaron en Binance (Demo/Testnet por defecto) y si hay órdenes ejecutadas
en Binance (con la etiqueta QTAPP_) que nunca quedaron registradas del lado de la app.
"""
import asyncio
import logging
from typing import Any, Dict, List

from nicegui import ui

from data_layer.storage import SessionLocal
from reconciliation.models import AppOrderRecord, ReconciliationRecord
from reconciliation.reconciler import OrderReconciler

logger = logging.getLogger("ReconciliationPage")

SEVERITY_BADGE = {
    "CRITICAL": "bg-red-500/15 text-red-400 border border-red-500/40",
    "WARNING": "bg-amber-500/15 text-amber-400 border border-amber-500/40",
    "INFO": "bg-emerald-500/15 text-emerald-400 border border-emerald-500/40",
}

RESULT_COLUMNS = [
    {"name": "symbol", "label": "Símbolo", "field": "symbol", "align": "left"},
    {"name": "match_status", "label": "Estado", "field": "match_status", "align": "left"},
    {"name": "severity", "label": "Severidad", "field": "severity", "align": "left"},
    {"name": "binance_order_id", "label": "Orden Binance", "field": "binance_order_id", "align": "left"},
    {"name": "details", "label": "Detalle", "field": "details", "align": "left"},
]

HISTORY_COLUMNS = [
    {"name": "run_at", "label": "Fecha", "field": "run_at", "align": "left"},
    {"name": "symbol", "label": "Símbolo", "field": "symbol", "align": "left"},
    {"name": "match_status", "label": "Estado", "field": "match_status", "align": "left"},
    {"name": "severity", "label": "Severidad", "field": "severity", "align": "left"},
    {"name": "binance_order_id", "label": "Orden Binance", "field": "binance_order_id", "align": "left"},
    {"name": "details", "label": "Detalle", "field": "details", "align": "left"},
]

LEDGER_COLUMNS = [
    {"name": "created_at", "label": "Fecha", "field": "created_at", "align": "left"},
    {"name": "symbol", "label": "Símbolo", "field": "symbol", "align": "left"},
    {"name": "side", "label": "Lado", "field": "side", "align": "left"},
    {"name": "action", "label": "Acción", "field": "action", "align": "left"},
    {"name": "status", "label": "Envío", "field": "status", "align": "left"},
    {"name": "binance_order_id", "label": "Orden Binance", "field": "binance_order_id", "align": "left"},
    {"name": "reconciliation_status", "label": "Conciliación", "field": "reconciliation_status", "align": "left"},
]


class ReconciliationPage:
    def __init__(self):
        self.use_testnet = True
        self.is_loading = False
        self.last_summary: Dict[str, Any] = {}

    def render(self):
        with ui.column().classes('w-full h-full p-2 md:p-4 gap-6 bg-[#0a0e17] text-white'):

            with ui.row().classes('w-full justify-between items-center pb-4 border-b border-gray-800 flex-wrap gap-4'):
                with ui.column().classes('gap-1'):
                    with ui.row().classes('items-center gap-3'):
                        ui.icon('fact_check', size='32px', color='yellow-400')
                        ui.label('Conciliación App ↔ Binance').classes('text-2xl md:text-3xl font-extrabold text-white tracking-tight font-heading')
                    ui.label(
                        'Verifica que cada orden enviada por la app se haya ejecutado realmente en Binance '
                        '(Demo/Testnet) y detecta órdenes huérfanas sin registro local.'
                    ).classes('text-xs md:text-sm text-gray-400')

                with ui.row().classes('gap-3 items-center flex-wrap'):
                    with ui.row().classes('bg-gray-950 p-1 rounded-xl border border-gray-800 gap-1 shadow-inner'):
                        self.btn_testnet = ui.button('🟡 Demo (Testnet)', on_click=lambda: self._switch_network(True)) \
                            .props('dense').classes('bg-yellow-500 text-black text-xs font-bold px-3 py-1.5 rounded-lg transition-all shadow')
                        self.btn_mainnet = ui.button('🌐 Real (Mainnet)', on_click=lambda: self._switch_network(False)) \
                            .props('dense flat').classes('text-gray-400 hover:text-white text-xs font-semibold px-3 py-1.5 rounded-lg transition-all')

                    self.symbols_input = ui.input('Símbolos (coma)', value='BTCUSDT,ETHUSDT').classes('w-44')
                    self.hours_input = ui.number('Horas atrás', value=24, min=1, max=168).classes('w-24')

                    self.run_btn = ui.button('Conciliar ahora', icon='sync', on_click=self._run_reconciliation_async) \
                        .classes('bg-amber-500 text-black font-bold px-4 py-2 rounded-xl')

            with ui.row().classes('w-full gap-4 flex-wrap') as self.summary_row:
                self._render_summary_cards({})

            ui.label('Resultados de la última conciliación').classes('text-lg font-bold text-white mt-2')
            self.results_table = ui.table(
                columns=RESULT_COLUMNS, rows=[], row_key='binance_order_id', pagination={'rowsPerPage': 10}
            ).classes('w-full')
            self.results_table.add_slot('body-cell-severity', '''
                <q-td :props="props">
                    <q-badge :color="props.value === 'CRITICAL' ? 'red' : (props.value === 'WARNING' ? 'amber' : 'green')">
                        {{ props.value }}
                    </q-badge>
                </q-td>
            ''')

            ui.label('Historial de conciliaciones (persistido)').classes('text-lg font-bold text-white mt-4')
            self.history_table = ui.table(
                columns=HISTORY_COLUMNS, rows=[], row_key='id', pagination={'rowsPerPage': 15}
            ).classes('w-full')

            ui.label('Ledger de órdenes enviadas por la app a Binance').classes('text-lg font-bold text-white mt-4')
            self.ledger_table = ui.table(
                columns=LEDGER_COLUMNS, rows=[], row_key='id', pagination={'rowsPerPage': 15}
            ).classes('w-full')

        ui.timer(0.5, self._load_persisted_data_async, once=True)

    # ── Helpers de UI ────────────────────────────────────────────────────

    def _render_summary_cards(self, summary: Dict[str, Any]):
        self.summary_row.clear()
        with self.summary_row:
            cards = [
                ("Revisadas", summary.get("total_checked", "-"), "checklist", "cyan-400"),
                ("Críticas", summary.get("critical_count", "-"), "warning", "red-400"),
                ("Coincidencias (MATCHED)", summary.get("summary", {}).get("MATCHED", "-"), "check_circle", "emerald-400"),
                ("Ausentes en Binance", summary.get("summary", {}).get("MISSING_ON_BINANCE", "-"), "help", "red-400"),
                ("Huérfanas en Binance", summary.get("summary", {}).get("ORPHAN_ON_BINANCE", "-"), "report", "amber-400"),
            ]
            for label, value, icon, color in cards:
                with ui.column().classes('bg-[#111827] border border-[#1e293b] rounded-xl px-4 py-3 min-w-[160px] gap-1'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon(icon, size='18px', color=color)
                        ui.label(label).classes('text-xs text-gray-400 font-semibold uppercase tracking-wide')
                    ui.label(str(value)).classes(f'text-2xl font-extrabold text-{color} font-mono')

    def _switch_network(self, use_testnet: bool):
        self.use_testnet = use_testnet
        if use_testnet:
            self.btn_testnet.classes(replace='bg-yellow-500 text-black text-xs font-bold px-3 py-1.5 rounded-lg transition-all shadow')
            self.btn_mainnet.classes(replace='text-gray-400 hover:text-white text-xs font-semibold px-3 py-1.5 rounded-lg transition-all')
        else:
            self.btn_mainnet.classes(replace='bg-yellow-500 text-black text-xs font-bold px-3 py-1.5 rounded-lg transition-all shadow')
            self.btn_testnet.classes(replace='text-gray-400 hover:text-white text-xs font-semibold px-3 py-1.5 rounded-lg transition-all')

    # ── Carga de datos ───────────────────────────────────────────────────

    async def _run_reconciliation_async(self):
        if self.is_loading:
            return
        self.is_loading = True
        self.run_btn.props('loading')
        try:
            symbols = [s.strip().upper() for s in (self.symbols_input.value or '').split(',') if s.strip()]
            if not symbols:
                symbols = ["BTCUSDT"]
            hours = float(self.hours_input.value or 24)
            use_testnet = self.use_testnet

            loop = asyncio.get_event_loop()
            summary = await loop.run_in_executor(
                None, lambda: OrderReconciler(use_testnet=use_testnet).run(symbols=symbols, lookback_hours=hours)
            )
            self.last_summary = summary
            self._render_summary_cards(summary)

            self.results_table.rows = summary.get('results', [])
            self.results_table.update()

            if summary.get('critical_count'):
                ui.notify(
                    f"⚠️ Conciliación completada: {summary['critical_count']} discrepancia(s) crítica(s) "
                    f"de {summary['total_checked']} revisadas.",
                    type='negative', duration=8000
                )
            else:
                ui.notify(
                    f"✅ Conciliación completada: {summary['total_checked']} orden(es) revisadas, sin discrepancias críticas.",
                    type='positive', duration=6000
                )

            await self._load_persisted_data_async()
        except Exception as e:
            logger.error("Error ejecutando conciliación: %s", e)
            ui.notify(f"Error al conciliar: {e}", type='negative', duration=8000)
        finally:
            self.is_loading = False
            self.run_btn.props(remove='loading')

    async def _load_persisted_data_async(self):
        loop = asyncio.get_event_loop()

        def _load():
            db = SessionLocal()
            try:
                history = (
                    db.query(ReconciliationRecord)
                    .order_by(ReconciliationRecord.run_at.desc())
                    .limit(100)
                    .all()
                )
                ledger = (
                    db.query(AppOrderRecord)
                    .order_by(AppOrderRecord.created_at.desc())
                    .limit(100)
                    .all()
                )
                return history, ledger
            finally:
                db.close()

        try:
            history, ledger = await loop.run_in_executor(None, _load)
        except Exception as e:
            logger.warning("No se pudo cargar historial de conciliación: %s", e)
            return

        self.history_table.rows = [
            {
                "id": r.id,
                "run_at": r.run_at.strftime('%d/%m %H:%M:%S') if r.run_at else '-',
                "symbol": r.symbol,
                "match_status": r.match_status,
                "severity": r.severity,
                "binance_order_id": r.binance_order_id or '-',
                "details": r.details or '',
            }
            for r in history
        ]
        self.history_table.update()

        self.ledger_table.rows = [
            {
                "id": r.id,
                "created_at": r.created_at.strftime('%d/%m %H:%M:%S') if r.created_at else '-',
                "symbol": r.symbol,
                "side": r.side,
                "action": r.action,
                "status": r.status,
                "binance_order_id": r.binance_order_id or '-',
                "reconciliation_status": r.reconciliation_status or 'pendiente',
            }
            for r in ledger
        ]
        self.ledger_table.update()


def render_reconciliation_page():
    page = ReconciliationPage()
    page.render()
    return page
