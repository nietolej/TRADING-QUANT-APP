"""
Página de Conciliación App ↔ Binance.

Muestra, sobre el módulo separado `reconciliation/`, si las órdenes que la app envió
realmente se ejecutaron en Binance (Demo/Testnet por defecto) y si hay órdenes ejecutadas
en Binance (con la etiqueta QTAPP_) que nunca quedaron registradas del lado de la app.

Tests:
- Test 1: envía UNA orden real a Testnet y verifica creación → envío → ejecución y el
  deslizamiento de precio, con un veredicto de si se ejecutó según lo pedido por el bot.
  (Test 1b: el ciclo completo con SL/TP.)
- Test 2: sesión de conciliación en vivo de un bot; genera y guarda en BD el informe de
  conciliación (estadísticas, deslizamientos y confiabilidad), consultable después.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from nicegui import ui

from data_layer.storage import SessionLocal
from execution_engine.daemon_client import daemon_client as bot_manager
from reconciliation.models import AppOrderRecord, ReconciliationRecord
from reconciliation.reconciler import OrderReconciler
from reconciliation.reports import get_session_report, list_session_reports, save_session_report

logger = logging.getLogger("ReconciliationPage")

SEVERITY_BADGE = {
    "CRITICAL": "bg-red-500/15 text-red-400 border border-red-500/40",
    "WARNING": "bg-amber-500/15 text-amber-400 border border-amber-500/40",
    "INFO": "bg-emerald-500/15 text-emerald-400 border border-emerald-500/40",
}

SEVERITY_SLOT = '''
    <q-td :props="props">
        <q-badge :color="props.value === 'CRITICAL' ? 'red' : (props.value === 'WARNING' ? 'amber' : 'green')">
            {{ props.value }}
        </q-badge>
    </q-td>
'''

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
    {"name": "bot_id", "label": "Bot", "field": "bot_id", "align": "left"},
    {"name": "side", "label": "Lado", "field": "side", "align": "left"},
    {"name": "action", "label": "Acción", "field": "action", "align": "left"},
    {"name": "status", "label": "Envío", "field": "status", "align": "left"},
    {"name": "binance_order_id", "label": "Orden Binance", "field": "binance_order_id", "align": "left"},
    {"name": "reconciliation_status", "label": "Conciliación", "field": "reconciliation_status", "align": "left"},
]

# Detalle por orden de una sesión (Test 2, en vivo o consultada desde el historial) y de Test 1.
ORDER_COLUMNS = [
    {"name": "created_at", "label": "Hora", "field": "created_at", "align": "left"},
    {"name": "role", "label": "Rol", "field": "role", "align": "left"},
    {"name": "position_side", "label": "Long/Short", "field": "position_side", "align": "left"},
    {"name": "order_type", "label": "Tipo", "field": "order_type", "align": "left"},
    {"name": "requested_qty", "label": "Cant. Solicitada", "field": "requested_qty", "align": "right"},
    {"name": "executed_qty", "label": "Cant. Ejecutada", "field": "executed_qty", "align": "right"},
    {"name": "reference_price", "label": "Precio Referencia", "field": "reference_price", "align": "right"},
    {"name": "avg_price", "label": "Precio Ejecutado", "field": "avg_price", "align": "right"},
    {"name": "slippage_pct", "label": "Deslizamiento % (+ en contra)", "field": "slippage_pct", "align": "right"},
    {"name": "binance_status", "label": "Estado Binance", "field": "binance_status", "align": "left"},
    {"name": "match_status", "label": "Conciliación", "field": "match_status", "align": "left"},
    {"name": "severity", "label": "Severidad", "field": "severity", "align": "left"},
    {"name": "details", "label": "Detalle", "field": "details", "align": "left"},
]

SESSION_COLUMNS = [
    {"name": "started_at", "label": "Inicio (UTC)", "field": "started_at", "align": "left"},
    {"name": "ended_at", "label": "Fin (UTC)", "field": "ended_at", "align": "left"},
    {"name": "bot_name", "label": "Bot", "field": "bot_name", "align": "left"},
    {"name": "symbol", "label": "Símbolo", "field": "symbol", "align": "left"},
    {"name": "status", "label": "Estado", "field": "status", "align": "left"},
    {"name": "total_orders", "label": "Órdenes", "field": "total_orders", "align": "right"},
    {"name": "reliability", "label": "Confiabilidad", "field": "reliability", "align": "left"},
    {"name": "slippage_avg", "label": "Desliz. prom.", "field": "slippage_avg", "align": "right"},
    {"name": "slippage_max", "label": "Desliz. máx.", "field": "slippage_max", "align": "right"},
]

ROLE_BY_ACTION = {
    "OPEN": "Entrada",
    "CLOSE": "Salida",
    "TAKE_PROFIT": "Take Profit",
    "STOP_LOSS": "Stop Loss",
}

RELIABILITY_COLOR = {"Alta": "emerald-400", "Media": "amber-400", "Baja": "red-400", "Sin datos": "gray-400"}


def _fmt_pct(value: Optional[float], decimals: int = 3) -> str:
    return f"{value:.{decimals}f}%" if value is not None else "-"


def _fmt_dt(value: Optional[datetime]) -> str:
    return value.strftime('%d/%m %H:%M:%S') if value else '-'


def _order_rows(orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Adapta el detalle por orden (reconciler) a filas de tabla: agrega el rol y evita celdas None."""
    rows = []
    for o in orders:
        row = {k: ('-' if v is None else v) for k, v in o.items()}
        row['role'] = ROLE_BY_ACTION.get(o.get('action'), o.get('action') or '-')
        slip = o.get('slippage_pct')
        row['slippage_pct'] = _fmt_pct(slip, 4) if slip is not None else '-'
        rows.append(row)
    return rows


class ReconciliationPage:
    def __init__(self):
        self.use_testnet = True
        self.is_loading = False
        self.last_summary: Dict[str, Any] = {}
        # Sesión activa de Test 2 (None = no hay monitoreo en curso).
        self.test2_session: Optional[Dict[str, Any]] = None
        self._test2_timer = None

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
                'Test 1 envía una orden real a Testnet y verifica que se registró en la app, se envió, se '
                'ejecutó en Binance y con qué deslizamiento de precio. Test 2 concilia en vivo todo lo que un '
                'bot envía a Binance y genera —y guarda— el informe de conciliación con su confiabilidad.'
            ).classes('text-xs text-gray-400 -mt-1')

            with ui.row().classes('w-full gap-4 flex-wrap items-stretch'):
                # ── Test 1: verificación de una orden ──
                with ui.card().classes('bg-[#111827] border border-[#1e293b] rounded-xl p-4 flex-1 min-w-[340px] gap-2'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('verified', color='violet-400', size='20px')
                        ui.label('Test 1 — Verificación de una Orden').classes('text-base font-bold text-white')
                    ui.label(
                        'Envía una orden real a Testnet y confirma, paso a paso, que se registró en la app, que se '
                        'envió a Binance y que Binance la ejecutó de verdad, midiendo el deslizamiento contra el '
                        'precio de referencia tomado al enviarla. Termina con un veredicto: si se ejecutó de '
                        'acuerdo al bot. Cierra la posición de prueba al final.'
                    ).classes('text-xs text-gray-400')
                    with ui.row().classes('gap-2 items-center flex-wrap'):
                        self.verify_symbol_input = ui.input('Símbolo', value='BTC/USDT').classes('w-32')
                        self.verify_qty_input = ui.number('Cantidad', value=0.001, step=0.001, format='%.3f').classes('w-28')
                        self.verify_side_select = ui.select({'long': 'Long', 'short': 'Short'}, value='long').classes('w-24')
                        self.verify_btn = ui.button('▶ Ejecutar Test 1', icon='fact_check', on_click=self._run_verify_async) \
                            .classes('bg-violet-600 hover:bg-violet-500 text-white font-bold px-3 py-2 rounded-lg')
                    self.verify_results_col = ui.column().classes('w-full gap-1 mt-2')

                # ── Test 1b: ciclo completo con SL/TP ──
                with ui.card().classes('bg-[#111827] border border-[#1e293b] rounded-xl p-4 flex-1 min-w-[340px] gap-2'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('bolt', color='cyan-400', size='20px')
                        ui.label('Test 1b — Ciclo Completo Inmediato').classes('text-base font-bold text-white')
                    ui.label(
                        'Envía una orden real de prueba a Testnet (entrada + SL/TP condicional + cierre) usando '
                        'el mismo código que usan los bots, y la concilia al instante. Solo corre en Testnet.'
                    ).classes('text-xs text-gray-400')
                    with ui.row().classes('gap-2 items-center flex-wrap'):
                        self.cycle_symbol_input = ui.input('Símbolo', value='BTC/USDT').classes('w-32')
                        self.cycle_qty_input = ui.number('Cantidad', value=0.001, step=0.001, format='%.3f').classes('w-28')
                        self.cycle_btn = ui.button('▶ Ejecutar Test 1b', icon='science', on_click=self._run_cycle_async) \
                            .classes('bg-cyan-600 hover:bg-cyan-500 text-white font-bold px-3 py-2 rounded-lg')
                    self.cycle_results_col = ui.column().classes('w-full gap-1 mt-2')

                # ── Test 2: sesión de conciliación en vivo de un bot ──
                with ui.card().classes('bg-[#111827] border border-[#1e293b] rounded-xl p-4 flex-1 min-w-[340px] gap-2'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('monitor_heart', color='emerald-400', size='20px')
                        ui.label('Test 2 — Conciliación en Vivo de un Bot').classes('text-base font-bold text-white')
                    ui.label(
                        'Inicia una sesión sobre un bot de Testnet: concilia contra Binance, orden por orden, todo lo '
                        'que el bot envía desde ese momento (Entrada, SL, TP, Salida) y calcula en vivo las '
                        'estadísticas, los deslizamientos y la confiabilidad. El informe se guarda en la base de '
                        'datos en cada ciclo para consultarlo después.'
                    ).classes('text-xs text-gray-400')
                    with ui.row().classes('gap-2 items-center flex-wrap'):
                        self.test2_bot_select = ui.select({}, label='Bot (Testnet)').classes('w-56')
                        self.test2_interval_input = ui.number('Intervalo (s)', value=15, min=5, max=300).classes('w-28')
                        self.test2_toggle_btn = ui.button('▶ Iniciar Sesión', icon='play_arrow', on_click=self._toggle_test2) \
                            .classes('bg-emerald-600 hover:bg-emerald-500 text-white font-bold px-3 py-2 rounded-lg')
                    self.test2_status_label = ui.label('Inactivo').classes('text-xs text-gray-400 font-mono')

            # ── Test 2: estadística en vivo de la sesión ──
            ui.label('Test 2 — Informe en vivo de la sesión').classes('text-lg font-bold text-white mt-2')
            with ui.row().classes('w-full gap-4 flex-wrap') as self.test2_cards_row:
                self._render_session_cards(self.test2_cards_row, {})
            self.test2_table = ui.table(
                columns=ORDER_COLUMNS, rows=[], row_key='app_order_ref', pagination={'rowsPerPage': 10}
            ).classes('w-full')
            self.test2_table.add_slot('body-cell-severity', SEVERITY_SLOT)

            ui.label('Resultados de la última conciliación').classes('text-lg font-bold text-white mt-2')
            self.results_table = ui.table(
                columns=RESULT_COLUMNS, rows=[], row_key='binance_order_id', pagination={'rowsPerPage': 10}
            ).classes('w-full')
            self.results_table.add_slot('body-cell-severity', SEVERITY_SLOT)

            # ── Historial de informes de conciliación (Test 2) ──
            with ui.row().classes('w-full justify-between items-center mt-4 flex-wrap gap-2'):
                with ui.column().classes('gap-0.5'):
                    ui.label('🛡️ Informes de Conciliación (Test 2)').classes('text-lg font-bold text-white')
                    ui.label(
                        'Cada sesión de Test 2 queda guardada. Haz clic en una fila para ver su informe completo: '
                        'estadísticas, deslizamientos, confiabilidad y el detalle de cada orden.'
                    ).classes('text-xs text-gray-400')
                ui.button('Actualizar', icon='refresh', on_click=self._refresh_sessions_async) \
                    .classes('bg-indigo-600 hover:bg-indigo-500 text-white font-bold px-4 py-2 rounded-xl')
            self.sessions_table = ui.table(
                columns=SESSION_COLUMNS, rows=[], row_key='session_id', pagination={'rowsPerPage': 10}
            ).classes('w-full cursor-pointer')
            self.sessions_table.on('rowClick', self._on_session_click)
            self.sessions_table.add_slot('body-cell-status', '''
                <q-td :props="props">
                    <q-badge :color="props.value === 'FINISHED' ? 'green' : (props.value === 'RUNNING' ? 'blue' : 'grey')">
                        {{ props.value }}
                    </q-badge>
                </q-td>
            ''')

            self.detail_title = ui.label('Selecciona una sesión para ver su informe.').classes('text-sm text-gray-400')
            with ui.row().classes('w-full gap-4 flex-wrap') as self.detail_cards_row:
                pass
            self.detail_table = ui.table(
                columns=ORDER_COLUMNS, rows=[], row_key='app_order_ref', pagination={'rowsPerPage': 10}
            ).classes('w-full')
            self.detail_table.add_slot('body-cell-severity', SEVERITY_SLOT)

            ui.label('Historial de conciliaciones (persistido)').classes('text-lg font-bold text-white mt-4')
            self.history_table = ui.table(
                columns=HISTORY_COLUMNS, rows=[], row_key='id', pagination={'rowsPerPage': 15}
            ).classes('w-full')

            ui.label('Ledger de órdenes enviadas por la app a Binance').classes('text-lg font-bold text-white mt-4')
            self.ledger_table = ui.table(
                columns=LEDGER_COLUMNS, rows=[], row_key='id', pagination={'rowsPerPage': 15}
            ).classes('w-full')

        ui.timer(0.5, self._load_persisted_data_async, once=True)
        ui.timer(0.5, self._refresh_bot_options, once=True)
        ui.timer(0.5, self._refresh_sessions_async, once=True)

    # ── Helpers de UI ────────────────────────────────────────────────────

    @staticmethod
    def _stat_card(label: str, value: Any, icon: str, color: str, min_width: int = 150):
        with ui.column().classes(f'bg-[#111827] border border-[#1e293b] rounded-xl px-4 py-3 min-w-[{min_width}px] gap-1'):
            with ui.row().classes('items-center gap-2'):
                ui.icon(icon, size='18px', color=color)
                ui.label(label).classes('text-xs text-gray-400 font-semibold uppercase tracking-wide')
            ui.label(str(value)).classes(f'text-2xl font-extrabold text-{color} font-mono')

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
                self._stat_card(label, value, icon, color, min_width=160)

    @staticmethod
    def _render_session_cards(row, report: Dict[str, Any]):
        """Tarjetas de estadística de una sesión de conciliación (en vivo o consultada del historial)."""
        row.clear()
        with row:
            tolerance = report.get('slippage_tolerance_pct')
            slip_max = report.get('slippage_max_pct')
            slip_max_color = 'red-400' if (slip_max is not None and tolerance is not None and slip_max > tolerance) else 'emerald-400'
            long_short = f"{report.get('long_count', 0)} / {report.get('short_count', 0)}" if report else "-"

            ReconciliationPage._stat_card("Órdenes", report.get("total_orders", "-"), "receipt_long", "cyan-400")
            ReconciliationPage._stat_card("Creadas en la app", report.get("created_count", "-"), "note_add", "cyan-400", 170)
            ReconciliationPage._stat_card("Enviadas a Binance", report.get("sent_count", "-"), "send", "sky-400", 170)
            ReconciliationPage._stat_card("Ejecutadas en Binance", report.get("executed_count", "-"), "bolt", "violet-400", 190)
            ReconciliationPage._stat_card("Conciliadas OK", report.get("effective_count", "-"), "verified", "emerald-400", 170)
            ReconciliationPage._stat_card("Fallidas", report.get("failed_count", "-"), "cancel", "red-400")
            ReconciliationPage._stat_card("Desliz. promedio", _fmt_pct(report.get("slippage_avg_pct"), 4), "trending_flat", "amber-400", 170)
            ReconciliationPage._stat_card("Desliz. máximo", _fmt_pct(slip_max, 4), "trending_up", slip_max_color, 170)
            ReconciliationPage._stat_card("Desliz. p95", _fmt_pct(report.get("slippage_p95_pct"), 4), "show_chart", "amber-400", 170)
            ReconciliationPage._stat_card("Long / Short", long_short, "swap_vert", "sky-400")

            reliability_pct = report.get("reliability_pct")
            reliability_label = report.get("reliability_label", "Sin datos")
            color = RELIABILITY_COLOR.get(reliability_label, "gray-400")
            value = f"{reliability_pct:.1f}% ({reliability_label})" if reliability_pct is not None else "-"
            with ui.column().classes(f'bg-[#111827] border-2 border-{color} rounded-xl px-4 py-3 min-w-[220px] gap-1'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('shield', size='18px', color=color)
                    ui.label('Confiabilidad del bot').classes('text-xs text-gray-400 font-semibold uppercase tracking-wide')
                ui.label(value).classes(f'text-2xl font-extrabold text-{color} font-mono')

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
                "run_at": _fmt_dt(r.run_at),
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
                "created_at": _fmt_dt(r.created_at),
                "symbol": r.symbol,
                "bot_id": r.bot_id or '-',
                "side": r.side,
                "action": r.action,
                "status": r.status,
                "binance_order_id": r.binance_order_id or '-',
                "reconciliation_status": r.reconciliation_status or 'pendiente',
            }
            for r in ledger
        ]
        self.ledger_table.update()

    async def _refresh_bot_options(self):
        """Alimenta el selector de bot de Test 2 (bots de Testnet)."""
        loop = asyncio.get_event_loop()
        try:
            bots = await loop.run_in_executor(None, bot_manager.get_all_bots)
        except Exception as e:
            logger.debug("No se pudo listar bots para Test 2: %s", e)
            return
        self.test2_bot_select.options = {
            b.bot_id: f"{b.name} ({b.symbol}) {'🟡 Testnet' if b.use_testnet else '🌐 Real'}"
            for b in bots if getattr(b, 'use_testnet', True)
        }
        self.test2_bot_select.update()

    async def _symbol_in_use_by_bot(self, symbol: str) -> bool:
        """
        True (y avisa al usuario) si algún bot de Testnet está corriendo sobre `symbol`. Los tests
        1 y 1b abren y cierran posiciones reales en la cuenta; con un bot activo en el mismo
        símbolo, el bot ve esa posición en Binance, la ADOPTA como propia y luego registra un
        trade que nunca operó (o interfiere con sus SL/TP). Por eso se bloquean.
        """
        target = symbol.replace('/', '').upper()
        loop = asyncio.get_event_loop()
        try:
            bots = await loop.run_in_executor(None, bot_manager.get_all_bots)
        except Exception as e:
            ui.notify(f'No se pudo verificar si hay bots corriendo ({e}); el test se cancela por seguridad.', type='negative')
            return True
        running = [
            b for b in bots
            if b.is_running and getattr(b, 'use_testnet', True) and b.symbol.replace('/', '').upper() == target
        ]
        if running:
            names = ', '.join(b.name for b in running)
            ui.notify(
                f'Test cancelado: {names} está corriendo en {target} y adoptaría la posición de prueba. '
                f'Detén el bot o usa otro símbolo.',
                type='warning', duration=10000,
            )
            return True
        return False

    # ── Test 1: verificación de una orden ────────────────────────────────

    async def _run_verify_async(self):
        symbol = (self.verify_symbol_input.value or 'BTC/USDT').strip()
        if await self._symbol_in_use_by_bot(symbol):
            return
        self.verify_btn.props('loading')
        self.verify_results_col.clear()
        try:
            qty = float(self.verify_qty_input.value or 0.001)
            side = self.verify_side_select.value or 'long'
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: OrderReconciler(use_testnet=True).run_execution_verification_test(
                    symbol=symbol, quantity=qty, side=side
                )
            )

            verdict = result.get('verdict') or {}
            executed_as_bot = bool(verdict.get('executed_as_bot'))
            with self.verify_results_col:
                if result.get('error'):
                    ui.label(result['error']).classes('text-xs text-red-400')
                for s in result.get('steps', []):
                    ok = bool(s.get('ok'))
                    color = 'emerald-400' if ok else 'red-400'
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('check_circle' if ok else 'cancel', color=color, size='16px')
                        ui.label(f"{s.get('step')}: {s.get('detail')}").classes(f'text-xs text-{color}')
                orders = result.get('orders', [])
                if orders:
                    table = ui.table(
                        columns=ORDER_COLUMNS, rows=_order_rows(orders), row_key='app_order_ref'
                    ).classes('w-full mt-2').props('dense')
                    table.add_slot('body-cell-severity', SEVERITY_SLOT)
                color = 'emerald-400' if executed_as_bot else 'red-400'
                with ui.column().classes(f'w-full border-2 border-{color} rounded-lg px-3 py-2 mt-2 gap-0.5'):
                    ui.label(
                        '✅ Se ejecutó de acuerdo al bot' if executed_as_bot else '🚨 NO se ejecutó de acuerdo al bot'
                    ).classes(f'text-sm font-extrabold text-{color}')
                    ui.label(verdict.get('reason', '')).classes('text-xs text-gray-300')

            if executed_as_bot:
                ui.notify('✅ Test 1: la orden se creó, se envió y se ejecutó según el bot.', type='positive', duration=6000)
            else:
                ui.notify('⚠️ Test 1: la ejecución no coincide con lo pedido — revisa el detalle en la tarjeta.', type='negative', duration=8000)

            await self._load_persisted_data_async()
        except Exception as e:
            logger.error("Error ejecutando Test 1: %s", e)
            ui.notify(f"Error ejecutando Test 1: {e}", type='negative', duration=8000)
        finally:
            self.verify_btn.props(remove='loading')

    # ── Test 1b: ciclo completo inmediato (entrada + SL/TP + cierre) ─────

    async def _run_cycle_async(self):
        symbol = (self.cycle_symbol_input.value or 'BTC/USDT').strip()
        if await self._symbol_in_use_by_bot(symbol):
            return
        self.cycle_btn.props('loading')
        self.cycle_results_col.clear()
        try:
            qty = float(self.cycle_qty_input.value or 0.001)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, lambda: OrderReconciler(use_testnet=True).run_full_cycle_test(symbol=symbol, quantity=qty)
            )

            with self.cycle_results_col:
                for s in result.get('steps', []):
                    ok = bool(s.get('ok'))
                    color = 'emerald-400' if ok else 'red-400'
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('check_circle' if ok else 'cancel', color=color, size='16px')
                        ui.label(f"{s.get('step')}: {s.get('detail')}").classes(f'text-xs text-{color}')
                recon = result.get('reconciliation', [])
                if recon:
                    crit = sum(1 for r in recon if r.get('severity') == 'CRITICAL')
                    ui.label(
                        f"Reconciliación: {len(recon)} orden(es) verificadas, {crit} crítica(s)"
                    ).classes('text-xs text-gray-400 mt-1')

            if result.get('success'):
                ui.notify('✅ Test 1b completado con éxito: ciclo completo validado en Testnet.', type='positive', duration=6000)
            else:
                ui.notify('⚠️ Test 1b encontró fallos — revisa el detalle en la tarjeta.', type='negative', duration=8000)

            await self._load_persisted_data_async()
        except Exception as e:
            logger.error("Error ejecutando Test 1b: %s", e)
            ui.notify(f"Error ejecutando Test 1b: {e}", type='negative', duration=8000)
        finally:
            self.cycle_btn.props(remove='loading')

    # ── Test 2: sesión de conciliación en vivo de un bot ─────────────────

    def _set_test2_button(self, running: bool):
        if running:
            self.test2_toggle_btn.set_text('⏹ Detener Sesión')
            self.test2_toggle_btn.classes(replace='bg-red-600 hover:bg-red-500 text-white font-bold px-3 py-2 rounded-lg')
        else:
            self.test2_toggle_btn.set_text('▶ Iniciar Sesión')
            self.test2_toggle_btn.classes(replace='bg-emerald-600 hover:bg-emerald-500 text-white font-bold px-3 py-2 rounded-lg')

    def _toggle_test2(self):
        if self.test2_session is not None:
            asyncio.create_task(self._stop_test2())
            return

        bot_id = self.test2_bot_select.value
        if not bot_id:
            ui.notify('Selecciona un bot de Testnet primero.', type='warning')
            return

        # Ancla de la sesión: solo se concilia lo que ESTE bot envíe a Binance a partir de este
        # instante (naive UTC, igual que `created_at` en el ledger), no todo su historial.
        started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self.test2_session = {
            "session_id": str(uuid.uuid4()),
            "bot_id": bot_id,
            "bot_name": None,
            "symbol": None,
            "started_at": started_at,
            "busy": False,
        }
        self.test2_table.rows = []
        self.test2_table.update()
        self._render_session_cards(self.test2_cards_row, {})

        interval = max(5.0, float(self.test2_interval_input.value or 15))
        self._test2_timer = ui.timer(interval, self._run_test2_tick)
        self._set_test2_button(True)
        self.test2_status_label.set_text(
            f"Sesión iniciada a las {started_at.strftime('%H:%M:%S')} UTC. Esperando órdenes del bot..."
        )
        asyncio.create_task(self._run_test2_tick())

    async def _build_and_render_test2(self, finished: bool) -> Optional[Dict[str, Any]]:
        """Concilia la sesión, guarda el informe en BD (en curso o finalizado) y refresca la pantalla."""
        session = self.test2_session
        loop = asyncio.get_event_loop()
        report = await loop.run_in_executor(
            None,
            lambda: OrderReconciler(use_testnet=True, notify=False).build_session_report(
                session_id=session["session_id"],
                bot_id=session["bot_id"],
                bot_name=session["bot_name"],
                symbol=session["symbol"],
                started_at=session["started_at"],
            )
        )
        # Un ciclo en curso que termina DESPUÉS de detener la sesión no debe volver a guardarla
        # como RUNNING encima del informe ya marcado FINISHED.
        if finished or self.test2_session is session:
            saved = await loop.run_in_executor(None, lambda: save_session_report(report, finished=finished))
            if not saved:
                ui.notify('⚠️ No se pudo guardar el informe de la sesión en la base de datos.', type='warning')

        self._render_session_cards(self.test2_cards_row, report)
        self.test2_table.rows = _order_rows(report['orders'])
        self.test2_table.update()
        await self._refresh_sessions_async()
        return report

    async def _run_test2_tick(self):
        session = self.test2_session
        if session is None or session["busy"]:
            return
        session["busy"] = True
        try:
            loop = asyncio.get_event_loop()
            try:
                bots = await loop.run_in_executor(None, bot_manager.get_all_bots)
            except Exception as e:
                self.test2_status_label.set_text(f'⚠️ No se pudo listar bots: {e}')
                return

            bot = next((b for b in bots if b.bot_id == session["bot_id"]), None)
            if not bot:
                self.test2_status_label.set_text('⚠️ El bot seleccionado ya no existe: sesión finalizada.')
                await self._stop_test2()
                return
            session["bot_name"] = bot.name
            session["symbol"] = bot.symbol.replace('/', '').upper()

            try:
                report = await self._build_and_render_test2(finished=False)
            except Exception as e:
                logger.error("Error conciliando la sesión de Test 2: %s", e)
                self.test2_status_label.set_text(f'⚠️ Error conciliando {bot.name}: {e}')
                return

            if not self.test2_session:  # se detuvo mientras se conciliaba
                return
            since = session["started_at"].strftime('%H:%M:%S')
            total = report["total_orders"]
            if not total:
                estado = 'corriendo' if bot.is_running else 'detenido'
                self.test2_status_label.set_text(
                    f"Monitoreando {bot.name} ({estado}) desde las {since} UTC — aún no envió ninguna orden."
                )
            else:
                pct = report["reliability_pct"]
                self.test2_status_label.set_text(
                    f"{'✅' if not report['failed_count'] else '🚨'} {bot.name}: {total} orden(es), "
                    f"{report['failed_count']} con discrepancia — confiabilidad {pct:.1f}% ({report['reliability_label']}). "
                    f"Sesión desde las {since} UTC."
                )
        finally:
            session["busy"] = False

    async def _stop_test2(self):
        """Detiene la sesión: hace una última conciliación y deja el informe guardado como FINISHED."""
        session = self.test2_session
        if session is None or session.get("stopping"):
            return
        session["stopping"] = True
        if self._test2_timer is not None:
            self._test2_timer.deactivate()
            self._test2_timer = None
        self._set_test2_button(False)

        report = None
        if session["symbol"] is not None:  # si nunca llegó a correr un ciclo no hay nada que guardar
            try:
                report = await self._build_and_render_test2(finished=True)
            except Exception as e:
                logger.error("Error finalizando la sesión de Test 2: %s", e)
                ui.notify(f"Error guardando el informe final: {e}", type='negative')
        self.test2_session = None

        if report is not None:
            pct = report["reliability_pct"]
            self.test2_status_label.set_text(
                f"Sesión finalizada — {report['total_orders']} orden(es), confiabilidad "
                f"{f'{pct:.1f}%' if pct is not None else 'sin datos'} ({report['reliability_label']}). "
                f"Informe guardado."
            )
            ui.notify('Sesión finalizada: el informe quedó guardado en la base de datos.', type='positive')
        else:
            self.test2_status_label.set_text('Inactivo')

    # ── Historial de informes de sesión ──────────────────────────────────

    async def _refresh_sessions_async(self):
        loop = asyncio.get_event_loop()
        try:
            sessions = await loop.run_in_executor(None, list_session_reports)
        except Exception as e:
            logger.warning("No se pudo cargar el historial de informes: %s", e)
            return
        self.sessions_table.rows = [
            {
                "session_id": s["session_id"],
                "started_at": _fmt_dt(s["started_at"]),
                "ended_at": _fmt_dt(s["ended_at"]),
                "bot_name": s["bot_name"] or s["bot_id"],
                "symbol": s["symbol"] or '-',
                "status": s["status"],
                "total_orders": s["total_orders"] or 0,
                "reliability": (
                    f"{s['reliability_pct']:.1f}% ({s['reliability_label']})"
                    if s["reliability_pct"] is not None else "Sin datos"
                ),
                "slippage_avg": _fmt_pct(s["slippage_avg_pct"], 4),
                "slippage_max": _fmt_pct(s["slippage_max_pct"], 4),
            }
            for s in sessions
        ]
        self.sessions_table.update()

    async def _on_session_click(self, e):
        try:
            session_id = e.args[1]["session_id"]
        except (IndexError, KeyError, TypeError):
            return
        loop = asyncio.get_event_loop()
        report = await loop.run_in_executor(None, lambda: get_session_report(session_id))
        if report is None:
            ui.notify('No se encontró el informe de esa sesión.', type='warning')
            return
        self.detail_title.set_text(
            f"Informe de {report['bot_name'] or report['bot_id']} ({report['symbol'] or '-'}) — "
            f"{_fmt_dt(report['started_at'])} a {_fmt_dt(report['ended_at']) if report['ended_at'] else 'en curso'} UTC "
            f"[{report['status']}]"
        )
        self.detail_title.classes(replace='text-base font-bold text-white')
        self._render_session_cards(self.detail_cards_row, report)
        self.detail_table.rows = _order_rows(report['orders'])
        self.detail_table.update()


def render_reconciliation_page():
    page = ReconciliationPage()
    page.render()
    return page
