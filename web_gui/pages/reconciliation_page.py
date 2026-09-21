"""
Página de Conciliación App ↔ Binance.

Muestra, sobre el módulo separado `reconciliation/`, si las órdenes que la app envió
realmente se ejecutaron en Binance (Demo/Testnet por defecto) y si hay órdenes ejecutadas
en Binance (con la etiqueta QTAPP_) que nunca quedaron registradas del lado de la app.

Tests:
- Test 1: envía UNA orden real a Testnet y verifica creación → envío → ejecución y el
  deslizamiento de precio, con un veredicto de si se ejecutó según lo pedido por el bot.
  (Test 1b: el ciclo completo con SL/TP.)
- Test 2: sesiones de conciliación en vivo, una por bot y varias a la vez; cada una genera y guarda
  en BD su informe de conciliación (estadísticas, deslizamientos y confiabilidad), consultable después.
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
from reconciliation.reports import (
    finalize_session_report, get_session_report, list_session_reports, save_session_report,
)
from app_runtime.async_utils import spawn

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
    {"name": "cycle", "label": "Ciclo", "field": "cycle", "align": "right"},
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
    {"name": "exposure", "label": "Exposición cuenta", "field": "exposure", "align": "left"},
    {"name": "actions", "label": "", "field": "actions", "align": "right"},
]

EXPOSURE_LABEL = {"OK": "OK", "WARNING": "⚠ Aviso", "CRITICAL": "🚨 Crítica", "UNKNOWN": "? Sin datos"}
EXPOSURE_COLOR = {"OK": "emerald-400", "WARNING": "amber-400", "CRITICAL": "red-400", "UNKNOWN": "gray-400"}

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
        if o.get('cycle_issue'):
            row['details'] = o['cycle_issue']
        slip = o.get('slippage_pct')
        row['slippage_pct'] = _fmt_pct(slip, 4) if slip is not None else '-'
        rows.append(row)
    return rows


class ReconciliationPage:
    def __init__(self):
        self.use_testnet = True
        self.is_loading = False
        self.last_summary: Dict[str, Any] = {}
        # Sesiones de Test 2 por bot_id (varias a la vez); las finalizadas conservan su panel hasta limpiarlas.
        self.test2_sessions: Dict[str, Dict[str, Any]] = {}

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

                # ── Test 2: sesiones de conciliación en vivo (uno o varios bots a la vez) ──
                with ui.card().classes('bg-[#111827] border border-[#1e293b] rounded-xl p-4 flex-1 min-w-[340px] gap-2'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('monitor_heart', color='emerald-400', size='20px')
                        ui.label('Test 2 — Conciliación en Vivo de Bots').classes('text-base font-bold text-white')
                    ui.label(
                        'Elige uno o VARIOS bots de Testnet y inicia una sesión para cada uno, en paralelo: concilia '
                        'contra Binance, orden por orden, todo lo que cada bot envía desde ese momento (Entrada, SL, '
                        'TP, Salida) y calcula en vivo las estadísticas, los deslizamientos y la confiabilidad. Cada '
                        'sesión guarda su propio informe en la base de datos en cada ciclo.'
                    ).classes('text-xs text-gray-400')
                    self.test2_bot_select = ui.select({}, label='Bots (Testnet)', multiple=True) \
                        .props('use-chips').classes('w-full')
                    with ui.row().classes('gap-2 items-center flex-wrap'):
                        self.test2_interval_input = ui.number('Intervalo (s)', value=15, min=5, max=300).classes('w-28')
                        self.test2_start_btn = ui.button('▶ Iniciar sesiones', icon='play_arrow', on_click=self._start_test2_sessions) \
                            .classes('bg-emerald-600 hover:bg-emerald-500 text-white font-bold px-3 py-2 rounded-lg')
                        ui.button('⏹ Detener todas', icon='stop', on_click=self._stop_all_test2) \
                            .classes('bg-red-600 hover:bg-red-500 text-white font-bold px-3 py-2 rounded-lg')
                    self.test2_status_label = ui.label('Inactivo').classes('text-xs text-gray-400 font-mono')

            # ── Test 2: informes en vivo, un panel por sesión ──
            with ui.row().classes('w-full items-center justify-between mt-2 flex-wrap gap-2'):
                ui.label('Test 2 — Informes en vivo de las sesiones').classes('text-lg font-bold text-white')
                ui.button('Limpiar finalizadas', icon='cleaning_services', on_click=self._clear_finished_test2) \
                    .props('flat dense').classes('text-slate-300 text-xs')
            self.test2_panels_col = ui.column().classes('w-full gap-4')
            with self.test2_panels_col:
                self.test2_empty_label = ui.label(
                    'Sin sesiones activas. Elige bots y pulsa "Iniciar sesiones".'
                ).classes('text-sm text-gray-500')

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
                    <q-badge :color="props.value === 'FINISHED' ? 'green' : (props.value === 'RUNNING' ? 'blue' : 'orange')">
                        {{ props.value }}
                    </q-badge>
                </q-td>
            ''')
            # Una sesión INTERRUMPIDA (la página o el PC se cerraron a mitad del test) se puede retomar o cerrar.
            self.sessions_table.add_slot('body-cell-actions', '''
                <q-td :props="props">
                    <template v-if="props.row.status === 'INTERRUMPIDA'">
                        <q-btn dense flat size="sm" color="emerald" icon="play_arrow" label="Reanudar"
                               @click.stop="() => $parent.$emit('resume', props.row)" />
                        <q-btn dense flat size="sm" color="grey" icon="stop" label="Finalizar"
                               @click.stop="() => $parent.$emit('finalize', props.row)" />
                    </template>
                </q-td>
            ''')
            self.sessions_table.on('resume', self._on_resume_session)
            self.sessions_table.on('finalize', self._on_finalize_session)

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

            ReconciliationPage._stat_card("Ciclos completos", report.get("cycles_completed", "-"), "autorenew", "cyan-400", 170)
            ReconciliationPage._stat_card("Ciclos efectivos", report.get("cycles_effective", "-"), "task_alt", "emerald-400", 170)
            ReconciliationPage._stat_card("Ciclos fallidos", report.get("cycles_failed", "-"), "error", "red-400", 170)
            ReconciliationPage._stat_card("Órdenes", report.get("total_orders", "-"), "receipt_long", "cyan-400")
            ReconciliationPage._stat_card("Creadas en la app", report.get("created_count", "-"), "note_add", "cyan-400", 170)
            ReconciliationPage._stat_card("Enviadas a Binance", report.get("sent_count", "-"), "send", "sky-400", 170)
            ReconciliationPage._stat_card("Fills en Binance", report.get("executed_count", "-"), "bolt", "violet-400", 190)
            ReconciliationPage._stat_card("Conciliadas OK", report.get("effective_count", "-"), "verified", "emerald-400", 170)
            ReconciliationPage._stat_card("Fallidas", report.get("failed_count", "-"), "cancel", "red-400")
            ReconciliationPage._stat_card("Desliz. promedio", _fmt_pct(report.get("slippage_avg_pct"), 4), "trending_flat", "amber-400", 170)
            ReconciliationPage._stat_card("Desliz. máximo", _fmt_pct(slip_max, 4), "trending_up", slip_max_color, 170)
            ReconciliationPage._stat_card("Desliz. p95", _fmt_pct(report.get("slippage_p95_pct"), 4), "show_chart", "amber-400", 170)
            ReconciliationPage._stat_card("Entradas Long / Short", long_short, "swap_vert", "sky-400", 190)

            reliability_pct = report.get("reliability_pct")
            reliability_label = report.get("reliability_label", "Sin datos")
            color = RELIABILITY_COLOR.get(reliability_label, "gray-400")
            value = f"{reliability_pct:.1f}% ({reliability_label})" if reliability_pct is not None else "-"
            with ui.column().classes(f'bg-[#111827] border-2 border-{color} rounded-xl px-4 py-3 min-w-[220px] gap-1'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('shield', size='18px', color=color)
                    ui.label('Confiabilidad del bot').classes('text-xs text-gray-400 font-semibold uppercase tracking-wide')
                ui.label(value).classes(f'text-2xl font-extrabold text-{color} font-mono')
                lower = report.get("reliability_lower_pct")
                if lower is not None:
                    ui.label(f"Mínimo con 95 % de confianza: {lower:.1f}%").classes('text-xs text-gray-400 font-mono')
                if report.get("reliability_note"):
                    ui.label(report["reliability_note"]).classes('text-xs text-amber-400 max-w-[300px]')

            exposure = report.get("exposure")
            if exposure:
                sev = exposure.get("severity", "UNKNOWN")
                ex_color = EXPOSURE_COLOR.get(sev, "gray-400")
                with ui.column().classes(f'bg-[#111827] border-2 border-{ex_color} rounded-xl px-4 py-3 min-w-[260px] max-w-[420px] gap-1'):
                    with ui.row().classes('items-center gap-2'):
                        ui.icon('account_balance_wallet', size='18px', color=ex_color)
                        ui.label('Exposición de la cuenta').classes('text-xs text-gray-400 font-semibold uppercase tracking-wide')
                    ui.label(EXPOSURE_LABEL.get(sev, sev)).classes(f'text-2xl font-extrabold text-{ex_color} font-mono')
                    if exposure.get("binance_net") is not None:
                        expected = exposure.get("expected_net")
                        ui.label(
                            f"Binance {exposure['binance_net']:+g} · bots {f'{expected:+g}' if expected is not None else '?'}"
                        ).classes('text-xs text-gray-300 font-mono')
                    for issue in exposure.get("issues", []):
                        ui.label(issue.get("text", "")).classes(f'text-xs text-{EXPOSURE_COLOR.get(issue.get("severity"), "gray-400")}')

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
        """Alimenta el selector de bots de Test 2 (bots de Testnet)."""
        loop = asyncio.get_event_loop()
        try:
            bots = await loop.run_in_executor(None, bot_manager.get_all_bots)
        except Exception as e:
            logger.debug("No se pudo listar bots para Test 2: %s", e)
            return
        self.test2_bot_select.options = {
            b.bot_id: f"{b.name} {'🟡 Testnet' if b.use_testnet else '🌐 Real'}"
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

    # ── Test 2: sesiones de conciliación en vivo (varios bots en paralelo) ─────────

    def _active_test2_sessions(self) -> List[Dict[str, Any]]:
        return [s for s in self.test2_sessions.values() if not s["finished"]]

    def _update_test2_summary(self):
        active = self._active_test2_sessions()
        finished = len(self.test2_sessions) - len(active)
        if active:
            names = ', '.join(s["bot_name"] or s["bot_id"][-6:] for s in active)
            self.test2_status_label.set_text(f"{len(active)} sesión(es) activa(s): {names}")
        elif finished:
            self.test2_status_label.set_text(f"Sin sesiones activas ({finished} finalizada(s) con su informe guardado).")
        else:
            self.test2_status_label.set_text('Inactivo')
        self.test2_empty_label.set_visibility(not self.test2_sessions)

    def _make_test2_panel(self, session: Dict[str, Any]):
        """Panel de una sesión: estado, estadísticas en vivo y tabla de órdenes de ESE bot."""
        with self.test2_panels_col:
            with ui.card().classes('w-full bg-[#0f172a] border border-[#1e293b] rounded-xl p-3 gap-2') as card:
                with ui.row().classes('w-full items-center justify-between flex-wrap gap-2'):
                    with ui.column().classes('gap-0'):
                        title = ui.label(f"🤖 {session['bot_name'] or 'Bot ' + session['bot_id'][-6:]}") \
                            .classes('text-base font-bold text-white')
                        status = ui.label('Iniciando...').classes('text-xs text-gray-400 font-mono')
                    stop_btn = ui.button('⏹ Detener', on_click=lambda s=session: spawn(self._stop_test2_session(s))) \
                        .props('dense').classes('bg-red-600 hover:bg-red-500 text-white text-xs font-bold px-3 rounded-lg')
                with ui.row().classes('w-full gap-4 flex-wrap') as cards_row:
                    self._render_session_cards(cards_row, {})
                table = ui.table(
                    columns=ORDER_COLUMNS, rows=[], row_key='app_order_ref', pagination={'rowsPerPage': 8}
                ).classes('w-full')
                table.add_slot('body-cell-severity', SEVERITY_SLOT)
        session["ui"] = {"card": card, "title": title, "status": status, "stop_btn": stop_btn,
                         "cards_row": cards_row, "table": table}

    def _set_test2_status(self, session: Dict[str, Any], text: str):
        session["ui"]["status"].set_text(text)

    def _launch_test2_session(self, bot_id: str, bot_name: Optional[str], session_id: str,
                              started_at: datetime, interval: float, resumed: bool = False):
        """Crea el panel y el timer de una sesión (nueva o retomada) y lanza su primer ciclo."""
        session = {
            "session_id": session_id,
            "bot_id": bot_id,
            "bot_name": bot_name,
            "symbol": None,
            "started_at": started_at,
            "interval": interval,
            "busy": False,
            "stopping": False,
            "finished": False,
            "cache": {},   # resultados definitivos de órdenes ya cerradas en Binance: no se vuelven a consultar
        }
        self.test2_sessions[bot_id] = session
        self._make_test2_panel(session)
        since = started_at.strftime('%H:%M:%S')
        self._set_test2_status(
            session,
            f"Sesión reanudada: se concilia todo lo enviado desde las {since} UTC, incluido lo ocurrido mientras estuvo interrumpida."
            if resumed else f"Sesión iniciada a las {since} UTC. Esperando órdenes del bot..."
        )

        async def _tick(s=session):
            await self._run_test2_tick(s)

        session["timer"] = ui.timer(interval, _tick)
        spawn(self._run_test2_tick(session))

    def _start_test2_sessions(self):
        bot_ids = list(self.test2_bot_select.value or [])
        if not bot_ids:
            ui.notify('Selecciona al menos un bot de Testnet.', type='warning')
            return

        interval = max(5.0, float(self.test2_interval_input.value or 15))
        started = []
        for bot_id in bot_ids:
            previous = self.test2_sessions.get(bot_id)
            if previous is not None and not previous["finished"]:
                continue  # ya tiene una sesión en curso
            if previous is not None:
                previous["ui"]["card"].delete()  # se reemplaza el panel de la sesión anterior ya finalizada

            # Ancla de la sesión: solo se concilia lo que ESTE bot envíe a Binance a partir de este
            # instante (naive UTC, igual que `created_at` en el ledger), no todo su historial.
            options = self.test2_bot_select.options
            label = options.get(bot_id, bot_id) if isinstance(options, dict) else bot_id
            name = str(label).replace(' 🟡 Testnet', '').replace(' 🌐 Real', '') if label != bot_id else None
            self._launch_test2_session(
                bot_id, name, str(uuid.uuid4()), datetime.now(timezone.utc).replace(tzinfo=None), interval
            )
            started.append(bot_id)

        if not started:
            ui.notify('Los bots elegidos ya tienen una sesión en curso.', type='info')
        self._update_test2_summary()

    @staticmethod
    def _bot_positions(symbol: str) -> Optional[List[Dict[str, Any]]]:
        """
        Posiciones abiertas de los bots de Testnet en `symbol`, para comparar con la cuenta. None si no se pudo
        saber: con el daemon caído `get_all_bots` devuelve [], y eso NO significa que todos estén planos.
        """
        bots = bot_manager.get_all_bots()
        if not bots:
            return None
        target = symbol.replace('/', '').upper()
        return [
            {"bot_id": b.bot_id, "name": b.name, "side": b.position.side, "quantity": b.position.quantity}
            for b in bots
            if getattr(b, 'use_testnet', True) and b.symbol.replace('/', '').upper() == target and b.position
        ]

    async def _on_resume_session(self, e):
        """Retoma una sesión INTERRUMPIDA con su mismo informe: se reconstruye desde el ledger desde su inicio."""
        try:
            session_id = e.args["session_id"]
        except (KeyError, TypeError):
            return
        loop = asyncio.get_event_loop()
        report = await loop.run_in_executor(None, lambda: get_session_report(session_id))
        if report is None or report["status"] != "INTERRUMPIDA":
            ui.notify('Esa sesión ya no está interrumpida.', type='info')
            await self._refresh_sessions_async()
            return
        bot_id = report["bot_id"]
        previous = self.test2_sessions.get(bot_id)
        if previous is not None and not previous["finished"]:
            ui.notify('Ese bot ya tiene una sesión en curso en esta página.', type='warning')
            return
        if previous is not None:
            previous["ui"]["card"].delete()
        self._launch_test2_session(
            bot_id, report["bot_name"], session_id, report["started_at"],
            max(5.0, float(self.test2_interval_input.value or 15)), resumed=True,
        )
        self._update_test2_summary()
        ui.notify(f"Sesión de {report['bot_name'] or bot_id} reanudada.", type='positive')

    async def _on_finalize_session(self, e):
        """Cierra una sesión INTERRUMPIDA sin retomarla (queda FINISHED hasta donde llegaron sus datos)."""
        try:
            session_id = e.args["session_id"]
        except (KeyError, TypeError):
            return
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(None, lambda: finalize_session_report(session_id))
        ui.notify('Sesión finalizada.' if ok else 'No se pudo finalizar: ya no está en curso.',
                  type='positive' if ok else 'warning')
        await self._refresh_sessions_async()

    async def _build_and_render_test2(self, session: Dict[str, Any], finished: bool) -> Optional[Dict[str, Any]]:
        """Concilia la sesión, guarda el informe en BD (en curso o finalizado) y refresca su panel."""
        loop = asyncio.get_event_loop()

        def _reconcile() -> Dict[str, Any]:
            reconciler = OrderReconciler(use_testnet=True, notify=False)
            # Posición neta y órdenes vivas de la cuenta: lo que la conciliación orden por orden no ve.
            try:
                exposure = reconciler.check_account_exposure(session["symbol"], self._bot_positions(session["symbol"]))
            except Exception:
                logger.exception("No se pudo revisar la exposición de la cuenta en %s", session["symbol"])
                exposure = None
            return reconciler.build_session_report(
                session_id=session["session_id"],
                bot_id=session["bot_id"],
                bot_name=session["bot_name"],
                symbol=session["symbol"],
                started_at=session["started_at"],
                outcome_cache=session["cache"],
                exposure=exposure,
            )

        report = await loop.run_in_executor(None, _reconcile)
        # Un ciclo en curso que termina DESPUÉS de detener la sesión no debe volver a guardarla
        # como RUNNING encima del informe ya marcado FINISHED.
        if finished or not session["finished"]:
            saved = await loop.run_in_executor(None, lambda: save_session_report(report, finished=finished))
            if not saved:
                ui.notify('⚠️ No se pudo guardar el informe de la sesión en la base de datos.', type='warning')

        panel = session["ui"]
        self._render_session_cards(panel["cards_row"], report)
        panel["table"].rows = _order_rows(report['orders'])
        panel["table"].update()
        await self._refresh_sessions_async()
        return report

    async def _run_test2_tick(self, session: Dict[str, Any]):
        if session["busy"] or session["finished"] or session["stopping"]:
            return
        session["busy"] = True
        try:
            loop = asyncio.get_event_loop()
            try:
                bot = await loop.run_in_executor(None, bot_manager.get_bot, session["bot_id"])
            except Exception as e:
                self._set_test2_status(session, f'⚠️ No se pudo consultar el bot: {e}')
                return

            if bot is None:
                if not bot_manager.is_daemon_online():
                    self._set_test2_status(session, '⏸ Daemon apagado: sesión en pausa hasta que vuelva.')
                    return
                self._set_test2_status(session, '⚠️ El bot ya no existe: sesión finalizada.')
                await self._stop_test2_session(session)
                return
            session["bot_name"] = bot.name
            session["symbol"] = bot.symbol.replace('/', '').upper()
            session["ui"]["title"].set_text(f"🤖 {bot.name}")

            try:
                report = await self._build_and_render_test2(session, finished=False)
            except Exception as e:
                logger.exception("Error conciliando la sesión de Test 2 de %s", bot.name)
                self._set_test2_status(session, f'⚠️ Error conciliando {bot.name}: {e}')
                return

            if session["finished"]:  # se detuvo mientras se conciliaba
                return
            since = session["started_at"].strftime('%H:%M:%S')
            total = report["total_orders"]
            if not total:
                estado = 'corriendo' if bot.is_running else 'detenido'
                self._set_test2_status(
                    session, f"Monitoreando ({estado}) desde las {since} UTC — aún no envió ninguna orden."
                )
            else:
                pct = report["reliability_pct"]
                self._set_test2_status(
                    session,
                    f"{'✅' if not report['failed_count'] else '🚨'} {total} orden(es), {report['failed_count']} con "
                    f"discrepancia — confiabilidad {f'{pct:.1f}%' if pct is not None else 'sin datos'} "
                    f"({report['reliability_label']}). Desde las {since} UTC."
                )
        finally:
            session["busy"] = False

    async def _stop_test2_session(self, session: Dict[str, Any]):
        """Detiene UNA sesión: última conciliación y el informe queda guardado como FINISHED."""
        if session["finished"] or session["stopping"]:
            return
        session["stopping"] = True
        timer = session.get("timer")
        if timer is not None:
            timer.deactivate()
        session["ui"]["stop_btn"].set_enabled(False)

        report = None
        if session["symbol"] is not None:  # si nunca llegó a correr un ciclo no hay nada que guardar
            try:
                report = await self._build_and_render_test2(session, finished=True)
            except Exception as e:
                logger.exception("Error finalizando la sesión de Test 2 de %s", session["bot_name"])
                ui.notify(f"Error guardando el informe final de {session['bot_name']}: {e}", type='negative')
        session["finished"] = True

        if report is not None:
            pct = report["reliability_pct"]
            self._set_test2_status(
                session,
                f"Finalizada — {report['total_orders']} orden(es), confiabilidad "
                f"{f'{pct:.1f}%' if pct is not None else 'sin datos'} ({report['reliability_label']}). Informe guardado."
            )
        else:
            self._set_test2_status(session, 'Finalizada sin órdenes que informar.')
        self._update_test2_summary()

    def _stop_all_test2(self):
        active = self._active_test2_sessions()
        if not active:
            ui.notify('No hay sesiones activas.', type='info')
            return
        for session in active:
            spawn(self._stop_test2_session(session))
        ui.notify(f'Deteniendo {len(active)} sesión(es): los informes quedan guardados.', type='positive')

    def _clear_finished_test2(self):
        for bot_id, session in list(self.test2_sessions.items()):
            if session["finished"]:
                session["ui"]["card"].delete()
                del self.test2_sessions[bot_id]
        self._update_test2_summary()

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
                "exposure": EXPOSURE_LABEL.get(s.get("exposure_severity"), '-'),
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
