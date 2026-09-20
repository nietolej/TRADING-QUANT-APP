"""
Página de Conciliación App ↔ Binance.

Muestra, sobre el módulo separado `reconciliation/`, si las órdenes que la app envió
realmente se ejecutaron en Binance (Demo/Testnet por defecto) y si hay órdenes ejecutadas
en Binance (con la etiqueta QTAPP_) que nunca quedaron registradas del lado de la app.
"""
import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List

from nicegui import ui

from data_layer.storage import SessionLocal
from execution_engine.daemon_client import daemon_client as bot_manager
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

REPORT_COLUMNS = [
    {"name": "run_at", "label": "Fecha", "field": "run_at", "align": "left"},
    {"name": "symbol", "label": "Símbolo", "field": "symbol", "align": "left"},
    {"name": "position_side", "label": "Long/Short", "field": "position_side", "align": "left"},
    {"name": "action", "label": "Acción", "field": "action", "align": "left"},
    {"name": "order_type", "label": "Tipo", "field": "order_type", "align": "left"},
    {"name": "requested_qty", "label": "Cant. Solicitada", "field": "requested_qty", "align": "right"},
    {"name": "executed_qty", "label": "Cant. Ejecutada", "field": "executed_qty", "align": "right"},
    {"name": "reference_price", "label": "Precio Referencia", "field": "reference_price", "align": "right"},
    {"name": "avg_price", "label": "Precio Ejecutado", "field": "avg_price", "align": "right"},
    {"name": "slippage_pct", "label": "Deslizamiento %", "field": "slippage_pct", "align": "right"},
    {"name": "match_status", "label": "Estado", "field": "match_status", "align": "left"},
    {"name": "severity", "label": "Severidad", "field": "severity", "align": "left"},
    {"name": "binance_order_id", "label": "Orden Binance", "field": "binance_order_id", "align": "left"},
    {"name": "details", "label": "Detalle", "field": "details", "align": "left"},
]


class ReconciliationPage:
    def __init__(self):
        self.use_testnet = True
        self.is_loading = False
        self.last_summary: Dict[str, Any] = {}
        self._test2_timer = None
        self.last_report: Dict[str, Any] = {}

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

            ui.label('🧪 Modo Test — Validación antes de dinero real').classes('text-lg font-bold text-white mt-2')
            ui.label(
                'Test 1 corre un ciclo completo de orden real en Testnet ahora mismo. Test 2 vigila '
                'automáticamente un bot en vivo, conciliando cada cierto intervalo sin que tengas que '
                'pulsar "Conciliar ahora" cada vez. Test 3 verifica, orden por orden, que se haya creado '
                'en la app, enviado y ejecutado realmente en Binance, midiendo el deslizamiento de precio.'
            ).classes('text-xs text-gray-400 -mt-1')

            with ui.row().classes('w-full gap-4 flex-wrap items-stretch'):
                # ── Test 1: ciclo completo inmediato ──
                with ui.card().classes('bg-[#111827] border border-[#1e293b] rounded-xl p-4 flex-1 min-w-[340px] gap-2'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('bolt', color='cyan-400', size='20px')
                        ui.label('Test 1 — Ciclo Completo Inmediato').classes('text-base font-bold text-white')
                    ui.label(
                        'Envía una orden real de prueba a Testnet (entrada + SL/TP condicional + cierre) usando '
                        'el mismo código que usan los bots, y la concilia al instante. Solo corre en Testnet.'
                    ).classes('text-xs text-gray-400')
                    with ui.row().classes('gap-2 items-center flex-wrap'):
                        self.test1_symbol_input = ui.input('Símbolo', value='BTC/USDT').classes('w-32')
                        self.test1_qty_input = ui.number('Cantidad', value=0.001, step=0.001, format='%.3f').classes('w-28')
                        self.test1_btn = ui.button('▶ Ejecutar Test 1', icon='science', on_click=self._run_test1_async) \
                            .classes('bg-cyan-600 hover:bg-cyan-500 text-white font-bold px-3 py-2 rounded-lg')
                    self.test1_results_col = ui.column().classes('w-full gap-1 mt-2')

                # ── Test 2: monitoreo en vivo de un bot ──
                with ui.card().classes('bg-[#111827] border border-[#1e293b] rounded-xl p-4 flex-1 min-w-[340px] gap-2'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('monitor_heart', color='emerald-400', size='20px')
                        ui.label('Test 2 — Monitoreo en Vivo de un Bot').classes('text-base font-bold text-white')
                    ui.label(
                        'Selecciona un bot en Testnet ya corriendo: se concilia automáticamente cada '
                        'cierto intervalo mientras opera en vivo, alertando discrepancias en tiempo real.'
                    ).classes('text-xs text-gray-400')
                    with ui.row().classes('gap-2 items-center flex-wrap'):
                        self.test2_bot_select = ui.select({}, label='Bot (Testnet)').classes('w-56')
                        self.test2_interval_input = ui.number('Intervalo (s)', value=30, min=10, max=300).classes('w-28')
                        self.test2_toggle_btn = ui.button('▶ Iniciar Monitoreo', icon='play_arrow', on_click=self._toggle_test2) \
                            .classes('bg-emerald-600 hover:bg-emerald-500 text-white font-bold px-3 py-2 rounded-lg')
                    self.test2_status_label = ui.label('Inactivo').classes('text-xs text-gray-400 font-mono')
                    self.test2_log_col = ui.column().classes('w-full gap-1 mt-2 max-h-64 overflow-y-auto')

                # ── Test 3: verificación creación → envío → ejecución ──
                with ui.card().classes('bg-[#111827] border border-[#1e293b] rounded-xl p-4 flex-1 min-w-[340px] gap-2'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('verified', color='violet-400', size='20px')
                        ui.label('Test 3 — Verificación Creación → Envío → Ejecución').classes('text-base font-bold text-white')
                    ui.label(
                        'Envía una orden real a Testnet y confirma, paso a paso, que quedó creada en el ledger '
                        'de la app, que se envió a Binance y que Binance la ejecutó de verdad — midiendo además '
                        'el deslizamiento de precio contra la referencia tomada al enviarla. Cierra la posición al final.'
                    ).classes('text-xs text-gray-400')
                    with ui.row().classes('gap-2 items-center flex-wrap'):
                        self.test3_symbol_input = ui.input('Símbolo', value='BTC/USDT').classes('w-32')
                        self.test3_qty_input = ui.number('Cantidad', value=0.001, step=0.001, format='%.3f').classes('w-28')
                        self.test3_side_select = ui.select({'long': 'Long', 'short': 'Short'}, value='long').classes('w-24')
                        self.test3_btn = ui.button('▶ Ejecutar Test 3', icon='fact_check', on_click=self._run_test3_async) \
                            .classes('bg-violet-600 hover:bg-violet-500 text-white font-bold px-3 py-2 rounded-lg')
                    self.test3_results_col = ui.column().classes('w-full gap-1 mt-2')

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

            # ── Informe de Confiabilidad del Bot (Reporte de Tests 1/2/3) ─
            with ui.row().classes('w-full justify-between items-center mt-6 flex-wrap gap-2'):
                with ui.column().classes('gap-0.5'):
                    ui.label('🛡️ Informe de Confiabilidad del Bot').classes('text-lg font-bold text-white')
                    ui.label(
                        'Resultado de Test 1/2/3: cuántas órdenes se crearon en la app, se enviaron y se '
                        'ejecutaron realmente en Binance, y cuántas quedaron conciliadas en cantidad y precio '
                        'con deslizamiento menor al umbral — con el detalle completo por orden (activo, '
                        'long/short, cantidad, precio, % de deslizamiento) para evaluar qué tan segura es la '
                        'ejecución del bot.'
                    ).classes('text-xs text-gray-400')
                with ui.row().classes('gap-2 items-center'):
                    self.report_hours_input = ui.number('Horas atrás', value=24, min=1, max=720).classes('w-24')
                    self.report_btn = ui.button('Ver Informe de Confiabilidad', icon='summarize', on_click=self._load_test_report_async) \
                        .classes('bg-indigo-600 hover:bg-indigo-500 text-white font-bold px-4 py-2 rounded-xl')

            with ui.row().classes('w-full gap-4 flex-wrap') as self.report_summary_row:
                self._render_report_summary_cards({})

            self.report_table = ui.table(
                columns=REPORT_COLUMNS, rows=[], row_key='binance_order_id', pagination={'rowsPerPage': 15}
            ).classes('w-full')
            self.report_table.add_slot('body-cell-severity', '''
                <q-td :props="props">
                    <q-badge :color="props.value === 'CRITICAL' ? 'red' : (props.value === 'WARNING' ? 'amber' : 'green')">
                        {{ props.value }}
                    </q-badge>
                </q-td>
            ''')
            self.report_table.add_slot('body-cell-position_side', '''
                <q-td :props="props">
                    <q-badge :color="props.value === 'LONG' ? 'emerald' : (props.value === 'SHORT' ? 'red' : 'grey')">
                        {{ props.value || '-' }}
                    </q-badge>
                </q-td>
            ''')

        ui.timer(0.5, self._load_persisted_data_async, once=True)
        ui.timer(0.5, self._refresh_test2_bot_options, once=True)

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

    def _render_report_summary_cards(self, report: Dict[str, Any]):
        self.report_summary_row.clear()
        with self.report_summary_row:
            reliability_pct = report.get("reliability_pct")
            reliability_label = report.get("reliability_label", "Sin datos")
            reliability_color = {
                "Alta": "emerald-400", "Media": "amber-400", "Baja": "red-400", "Sin datos": "gray-400",
            }.get(reliability_label, "gray-400")
            reliability_value = f"{reliability_pct:.1f}% ({reliability_label})" if reliability_pct is not None else "-"

            # Embudo: creada en la app → enviada a Binance → ejecutada en Binance → confiable.
            funnel_cards = [
                ("Órdenes creadas en la app", report.get("created_in_app_count", "-"), "note_add", "cyan-400"),
                ("Enviadas a Binance", report.get("sent_to_binance_count", "-"), "send", "sky-400"),
                ("Ejecutadas en Binance", report.get("executed_in_binance_count", "-"), "bolt", "violet-400"),
                (
                    f"Confiables (deslizamiento < {report.get('slippage_tolerance_pct', 0.05)}%)",
                    report.get("effective_count", "-"), "verified", "emerald-400",
                ),
            ]
            for label, value, icon, color in funnel_cards:
                with ui.column().classes('bg-[#111827] border border-[#1e293b] rounded-xl px-4 py-3 min-w-[190px] gap-1'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon(icon, size='18px', color=color)
                        ui.label(label).classes('text-xs text-gray-400 font-semibold uppercase tracking-wide')
                    ui.label(str(value)).classes(f'text-2xl font-extrabold text-{color} font-mono')

            other_cards = [
                ("Fallidas", report.get("failed_count", "-"), "cancel", "red-400"),
                ("Fallos por deslizamiento", report.get("slippage_failed_count", "-"), "trending_down", "amber-400"),
                ("Long", report.get("long_count", "-"), "trending_up", "emerald-400"),
                ("Short", report.get("short_count", "-"), "trending_down", "red-400"),
            ]
            for label, value, icon, color in other_cards:
                with ui.column().classes('bg-[#111827] border border-[#1e293b] rounded-xl px-4 py-3 min-w-[150px] gap-1'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon(icon, size='18px', color=color)
                        ui.label(label).classes('text-xs text-gray-400 font-semibold uppercase tracking-wide')
                    ui.label(str(value)).classes(f'text-2xl font-extrabold text-{color} font-mono')

            with ui.column().classes(
                f'bg-[#111827] border-2 border-{reliability_color} rounded-xl px-4 py-3 min-w-[220px] gap-1'
            ):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('shield', size='18px', color=reliability_color)
                    ui.label('Confiabilidad del bot').classes('text-xs text-gray-400 font-semibold uppercase tracking-wide')
                ui.label(reliability_value).classes(f'text-2xl font-extrabold text-{reliability_color} font-mono')

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

    # ── Modo Test 1: ciclo completo inmediato ───────────────────────────

    async def _run_test1_async(self):
        self.test1_btn.props('loading')
        self.test1_results_col.clear()
        try:
            symbol = (self.test1_symbol_input.value or 'BTC/USDT').strip()
            qty = float(self.test1_qty_input.value or 0.001)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, lambda: OrderReconciler(use_testnet=True).run_full_cycle_test(symbol=symbol, quantity=qty)
            )

            with self.test1_results_col:
                for s in result.get('steps', []):
                    ok = bool(s.get('ok'))
                    icon = 'check_circle' if ok else 'cancel'
                    color = 'emerald-400' if ok else 'red-400'
                    with ui.row().classes('items-center gap-2'):
                        ui.icon(icon, color=color, size='16px')
                        ui.label(f"{s.get('step')}: {s.get('detail')}").classes(f'text-xs text-{color}')
                recon = result.get('reconciliation', [])
                if recon:
                    crit = sum(1 for r in recon if r.get('severity') == 'CRITICAL')
                    ui.label(
                        f"Reconciliación: {len(recon)} orden(es) verificadas, {crit} crítica(s)"
                    ).classes('text-xs text-gray-400 mt-1')

            if result.get('success'):
                ui.notify('✅ Test 1 completado con éxito: ciclo completo validado en Testnet.', type='positive', duration=6000)
            else:
                ui.notify('⚠️ Test 1 encontró fallos — revisa el detalle en la tarjeta.', type='negative', duration=8000)

            await self._load_persisted_data_async()
        except Exception as e:
            logger.error("Error ejecutando Test 1: %s", e)
            ui.notify(f"Error ejecutando Test 1: {e}", type='negative', duration=8000)
        finally:
            self.test1_btn.props(remove='loading')

    # ── Modo Test 3: verificación creación → envío → ejecución ──────────

    async def _run_test3_async(self):
        self.test3_btn.props('loading')
        self.test3_results_col.clear()
        try:
            symbol = (self.test3_symbol_input.value or 'BTC/USDT').strip()
            qty = float(self.test3_qty_input.value or 0.001)
            side = self.test3_side_select.value or 'long'
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: OrderReconciler(use_testnet=True).run_execution_verification_test(
                    symbol=symbol, quantity=qty, side=side
                )
            )

            with self.test3_results_col:
                for s in result.get('steps', []):
                    ok = bool(s.get('ok'))
                    icon = 'check_circle' if ok else 'cancel'
                    color = 'emerald-400' if ok else 'red-400'
                    with ui.row().classes('items-center gap-2'):
                        ui.icon(icon, color=color, size='16px')
                        ui.label(f"{s.get('step')}: {s.get('detail')}").classes(f'text-xs text-{color}')
                for o in result.get('orders', []):
                    slip = o.get('slippage_pct')
                    slip_txt = f", deslizamiento {slip:.3f}%" if slip is not None else ""
                    ui.label(
                        f"{o.get('symbol')} {o.get('position_side') or ''} {o.get('order_type') or ''} — "
                        f"{o.get('match_status')}: cant. {o.get('executed_qty')} @ {o.get('avg_price')}{slip_txt}"
                    ).classes('text-xs text-gray-400 mt-1')

            if result.get('success'):
                ui.notify('✅ Test 3 completado: orden creada, enviada y ejecutada en Binance sin discrepancias.', type='positive', duration=6000)
            else:
                ui.notify('⚠️ Test 3 encontró fallos — revisa el detalle en la tarjeta.', type='negative', duration=8000)

            await self._load_persisted_data_async()
        except Exception as e:
            logger.error("Error ejecutando Test 3: %s", e)
            ui.notify(f"Error ejecutando Test 3: {e}", type='negative', duration=8000)
        finally:
            self.test3_btn.props(remove='loading')

    # ── Modo Test 2: monitoreo en vivo de un bot ────────────────────────

    async def _refresh_test2_bot_options(self):
        loop = asyncio.get_event_loop()
        try:
            bots = await loop.run_in_executor(None, bot_manager.get_all_bots)
        except Exception as e:
            logger.debug("No se pudo listar bots para Test 2: %s", e)
            return
        options = {
            b.bot_id: f"{b.name} ({b.symbol}) {'🟡 Testnet' if b.use_testnet else '🌐 Real'}"
            for b in bots if getattr(b, 'use_testnet', True)
        }
        self.test2_bot_select.options = options
        self.test2_bot_select.update()

    def _toggle_test2(self):
        if self._test2_timer is not None:
            self._test2_timer.deactivate()
            self._test2_timer = None
            self.test2_toggle_btn.set_text('▶ Iniciar Monitoreo')
            self.test2_toggle_btn.classes(replace='bg-emerald-600 hover:bg-emerald-500 text-white font-bold px-3 py-2 rounded-lg')
            self.test2_status_label.set_text('Inactivo')
            return

        bot_id = self.test2_bot_select.value
        if not bot_id:
            ui.notify('Selecciona un bot de Testnet primero.', type='warning')
            return

        interval = max(10.0, float(self.test2_interval_input.value or 30))

        async def _tick():
            await self._run_test2_tick(bot_id)

        self._test2_timer = ui.timer(interval, _tick)
        self.test2_toggle_btn.set_text('⏹ Detener Monitoreo')
        self.test2_toggle_btn.classes(replace='bg-red-600 hover:bg-red-500 text-white font-bold px-3 py-2 rounded-lg')
        self.test2_status_label.set_text('Monitoreando... (primera pasada en curso)')
        asyncio.create_task(_tick())

    async def _run_test2_tick(self, bot_id: str):
        loop = asyncio.get_event_loop()
        try:
            bots = await loop.run_in_executor(None, bot_manager.get_all_bots)
        except Exception as e:
            self._append_test2_log(f"⚠️ No se pudo listar bots: {e}", critical=True)
            return

        bot = next((b for b in bots if b.bot_id == bot_id), None)
        if not bot:
            self.test2_status_label.set_text('⚠️ El bot seleccionado ya no existe.')
            self._toggle_test2()  # detiene el monitoreo automáticamente
            return

        if not bot.is_running:
            self.test2_status_label.set_text(f"⏸ {bot.name} no está corriendo — esperando a que arranque...")
            return

        try:
            result = await loop.run_in_executor(
                None,
                lambda: OrderReconciler(use_testnet=True).run(
                    symbols=[bot.symbol.replace('/', '').upper()], lookback_hours=2.0
                )
            )
        except Exception as e:
            self._append_test2_log(f"⚠️ Error conciliando {bot.name}: {e}", critical=True)
            return

        critical = result.get('critical_count', 0)
        checked = result.get('total_checked', 0)
        if critical:
            self.test2_status_label.set_text(f"🚨 {bot.name}: {critical} discrepancia(s) crítica(s) detectada(s)")
            self._append_test2_log(f"🚨 {bot.name}: {critical} crítica(s) de {checked} revisadas", critical=True)
        else:
            self.test2_status_label.set_text(f"✅ {bot.name}: OK ({checked} orden(es) revisadas)")
            self._append_test2_log(f"✅ {bot.name}: sin discrepancias ({checked} revisadas)", critical=False)

        await self._load_persisted_data_async()

    def _append_test2_log(self, text: str, critical: bool):
        ts = datetime.now().strftime('%H:%M:%S')
        entries = getattr(self, '_test2_log_entries', None)
        if entries is None:
            entries = self._test2_log_entries = []
        entries.append((ts, text, critical))
        # Limitar el log a las últimas 30 líneas para no crecer indefinidamente en sesiones largas
        del entries[:-30]

        self.test2_log_col.clear()
        with self.test2_log_col:
            for e_ts, e_text, e_critical in reversed(entries):
                color = 'red-400' if e_critical else 'emerald-400'
                ui.label(f"[{e_ts}] {e_text}").classes(f'text-xs font-mono text-{color}')

    # ── Reporte de Tests ─────────────────────────────────────────────────

    async def _load_test_report_async(self):
        self.report_btn.props('loading')
        try:
            hours = float(self.report_hours_input.value or 24)
            use_testnet = self.use_testnet
            loop = asyncio.get_event_loop()
            report = await loop.run_in_executor(
                None, lambda: OrderReconciler(use_testnet=use_testnet, notify=False).build_test_report(lookback_hours=hours)
            )
            self.last_report = report
            self._render_report_summary_cards(report)

            self.report_table.rows = report.get('orders', [])
            self.report_table.update()

            rel_pct = report.get('reliability_pct')
            rel_txt = f"{rel_pct:.1f}% ({report.get('reliability_label')})" if rel_pct is not None else "sin datos"
            ui.notify(
                f"Confiabilidad del bot: {rel_txt} — {report.get('created_in_app_count', 0)} creada(s) en la app, "
                f"{report.get('sent_to_binance_count', 0)} enviada(s), {report.get('executed_in_binance_count', 0)} "
                f"ejecutada(s) en Binance, {report.get('effective_count', 0)} confiable(s) de "
                f"{report.get('total_checked', 0)} ({report.get('slippage_failed_count', 0)} por deslizamiento).",
                type='positive' if not report.get('failed_count') else 'warning', duration=8000
            )
        except Exception as e:
            logger.error("Error generando el Reporte de Tests: %s", e)
            ui.notify(f"Error generando el Reporte de Tests: {e}", type='negative', duration=8000)
        finally:
            self.report_btn.props(remove='loading')


def render_reconciliation_page():
    page = ReconciliationPage()
    page.render()
    return page
