import os
import asyncio
from typing import Optional, Callable, Dict, Any
from nicegui import ui

from execution_engine.binance_client import (
    get_binance_credentials,
    save_binance_credentials,
    verify_binance_credentials,
)
from execution_engine.security_manager import (
    load_security_config,
    save_security_config,
    is_real_trading_enabled,
    set_real_trading_enabled,
)


def _mask_key(key: str) -> str:
    """Oculta parcialmente una clave para previsualización."""
    if not key:
        return ""
    if len(key) <= 8:
        return "********"
    return f"{key[:4]}...{key[-4:]}"


class ApiCredentialsManager:
    """
    Componente para configurar, conectar, verificar y almacenar claves de API
    de Binance tanto para Futures Testnet (Demo) como para Real (Mainnet),
    con gestión del Candado de Seguridad de Cuenta Real y Guardarraíles Modulares.
    """

    def __init__(self, on_saved: Optional[Callable] = None, in_dialog: bool = False, dialog_ref=None):
        self.on_saved = on_saved
        self.in_dialog = in_dialog
        self.dialog_ref = dialog_ref
        self.creds = get_binance_credentials()
        self.sec_cfg = load_security_config()

    def render(self):
        with ui.column().classes('w-full gap-4 text-white'):
            # Cabecera
            with ui.row().classes('w-full justify-between items-center pb-3 border-b border-[#1e293b] flex-wrap gap-2'):
                with ui.row().classes('items-center gap-3'):
                    with ui.row().classes('items-center justify-center w-10 h-10 rounded-xl bg-amber-500/15 border border-amber-500/30 text-amber-400'):
                        ui.icon('vpn_key', size='24px')
                    with ui.column().classes('gap-0.5'):
                        ui.label('Conexión y Gestión de APIs de Exchange').classes('text-lg md:text-xl font-extrabold text-white font-heading')
                        ui.label('Configura tus claves para Binance Futures Testnet y Binance Real con Candado de Seguridad').classes('text-xs text-slate-400')
                
                if self.in_dialog and self.dialog_ref:
                    ui.button(icon='close', on_click=self.dialog_ref.close).props('flat round dense').classes('text-slate-400 hover:text-white')

            # Pestañas
            with ui.tabs().classes('w-full text-slate-300 border-b border-[#1e293b]') as tabs:
                tab_testnet = ui.tab('testnet', label='🟡 Futures Testnet (Demo)', icon='science').classes('text-xs md:text-sm font-bold')
                tab_real = ui.tab('real', label='🌐 Binance Real (Producción)', icon='public').classes('text-xs md:text-sm font-bold')
                tab_guardrails = ui.tab('guardrails', label='🛡️ Candado & Guardarraíles', icon='gavel').classes('text-xs md:text-sm font-bold')
                tab_guide = ui.tab('guide', label='ℹ️ Guía de Seguridad', icon='shield').classes('text-xs md:text-sm font-bold')

            with ui.tab_panels(tabs, value='testnet').classes('w-full bg-transparent p-0 pt-3'):
                
                # ──────────────────────────────────────────────────────────
                # PANEL 1: TESTNET
                # ──────────────────────────────────────────────────────────
                with ui.tab_panel('testnet').classes('p-0 gap-4 flex flex-col'):
                    with ui.card().classes('bg-[#111827] border border-[#1e293b] p-4 rounded-xl w-full shadow-lg gap-3'):
                        with ui.row().classes('w-full justify-between items-center flex-wrap gap-2'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('science', color='amber-400', size='20px')
                                ui.label('Credenciales Binance Futures Testnet').classes('text-sm font-bold text-amber-400 font-heading')
                            
                            with ui.row().classes('items-center gap-2'):
                                ui.link('🔗 Obtener API Keys en Testnet', 'https://testnet.binancefuture.com', new_tab=True).classes('text-xs text-amber-400 hover:underline font-semibold')
                                self.badge_testnet = ui.badge(
                                    'Configurada' if self.creds['has_testnet'] else 'No configurada',
                                    color='emerald-900' if self.creds['has_testnet'] else 'gray-800'
                                ).classes('text-[11px] font-bold px-2 py-0.5 rounded')

                        ui.label(
                            'El entorno Testnet te permite simular operaciones en Binance Futures con saldo ficticio sin arriesgar capital real.'
                        ).classes('text-xs text-slate-400')

                        # Formulario Testnet
                        with ui.column().classes('w-full gap-3 mt-1'):
                            with ui.column().classes('w-full gap-1'):
                                ui.label('TESTNET API KEY').classes('text-[10px] font-extrabold text-slate-400 uppercase tracking-wider font-mono')
                                self.input_testnet_key = ui.input(
                                    placeholder='Ingresa tu API Key de Testnet...',
                                    value=self.creds['testnet_api_key'],
                                    password=True,
                                    password_toggle_button=True
                                ).props('outlined dense dark').classes('w-full font-mono text-xs')

                            with ui.column().classes('w-full gap-1'):
                                ui.label('TESTNET SECRET KEY (PROTEGIDA)').classes('text-[10px] font-extrabold text-slate-400 uppercase tracking-wider font-mono')
                                # Blind Secret Pattern: si ya existe, placeholder ciego para evitar exposición en el DOM
                                t_ph = '•••••••••••••••• (Configurada en Servidor)' if self.creds['testnet_secret_key'] else 'Ingresa tu Secret Key de Testnet...'
                                self.input_testnet_secret = ui.input(
                                    placeholder=t_ph,
                                    password=True,
                                    password_toggle_button=True
                                ).props('outlined dense dark').classes('w-full font-mono text-xs')

                        # Resultados de prueba Testnet
                        self.testnet_result_card = ui.card().classes('w-full bg-[#0a0e17] border border-[#1e293b] p-3 rounded-lg hidden')
                        with self.testnet_result_card:
                            self.testnet_result_label = ui.label('').classes('text-xs font-mono')

                        # Botón de prueba Testnet
                        with ui.row().classes('w-full justify-end items-center gap-2 pt-2'):
                            self.btn_test_testnet = ui.button(
                                '🧪 Probar Conexión Testnet',
                                icon='speed',
                                on_click=self._test_testnet_connection
                            ).props('dense outline color=amber-400').classes('text-xs text-amber-400 font-bold px-3 py-1.5 rounded-lg')

                # ──────────────────────────────────────────────────────────
                # PANEL 2: MAINNET / REAL
                # ──────────────────────────────────────────────────────────
                with ui.tab_panel('real').classes('p-0 gap-4 flex flex-col'):
                    with ui.card().classes('bg-[#111827] border border-[#1e293b] p-4 rounded-xl w-full shadow-lg gap-3'):
                        with ui.row().classes('w-full justify-between items-center flex-wrap gap-2'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('public', color='sky-400', size='20px')
                                ui.label('Credenciales Binance Real (Mainnet / Producción)').classes('text-sm font-bold text-sky-400 font-heading')
                            
                            with ui.row().classes('items-center gap-2'):
                                ui.link('🔗 Gestor de API Keys Binance', 'https://www.binance.com/es/my/settings/api-management', new_tab=True).classes('text-xs text-sky-400 hover:underline font-semibold')
                                self.badge_real = ui.badge(
                                    'Configurada' if self.creds['has_real'] else 'No configurada',
                                    color='emerald-900' if self.creds['has_real'] else 'gray-800'
                                ).classes('text-[11px] font-bold px-2 py-0.5 rounded')

                        with ui.row().classes('w-full bg-emerald-950/40 border border-emerald-500/30 p-2.5 rounded-lg items-center gap-2 text-emerald-300 text-xs'):
                            ui.icon('verified_user', size='18px').classes('flex-none text-emerald-400')
                            ui.label('Para Solo Lectura: En Binance solo activa "Enable Reading". NUNCA actives "Enable Withdrawals" (Retiros).').classes('flex-1')

                        # Formulario Real
                        with ui.column().classes('w-full gap-3 mt-1'):
                            with ui.column().classes('w-full gap-1'):
                                ui.label('REAL API KEY (MAINNET)').classes('text-[10px] font-extrabold text-slate-400 uppercase tracking-wider font-mono')
                                self.input_real_key = ui.input(
                                    placeholder='Ingresa tu API Key de Binance Real...',
                                    value=self.creds['real_api_key'],
                                    password=True,
                                    password_toggle_button=True
                                ).props('outlined dense dark').classes('w-full font-mono text-xs')

                            with ui.column().classes('w-full gap-1'):
                                ui.label('REAL SECRET KEY (PROTEGIDA CON CLAVE CIEGA)').classes('text-[10px] font-extrabold text-slate-400 uppercase tracking-wider font-mono')
                                # Blind Secret Pattern: jamás exponer el secret real en el DOM del navegador
                                r_ph = '•••••••••••••••• (Configurada en Servidor)' if self.creds['real_secret_key'] else 'Ingresa tu Secret Key de Binance Real...'
                                self.input_real_secret = ui.input(
                                    placeholder=r_ph,
                                    password=True,
                                    password_toggle_button=True
                                ).props('outlined dense dark').classes('w-full font-mono text-xs')

                        # Resultados de prueba Real
                        self.real_result_card = ui.card().classes('w-full bg-[#0a0e17] border border-[#1e293b] p-3 rounded-lg hidden')
                        with self.real_result_card:
                            self.real_result_label = ui.label('').classes('text-xs font-mono')

                        # Botón de prueba Real
                        with ui.row().classes('w-full justify-end items-center gap-2 pt-2'):
                            self.btn_test_real = ui.button(
                                '🌐 Probar Conexión Real',
                                icon='speed',
                                on_click=self._test_real_connection
                            ).props('dense outline color=sky-400').classes('text-xs text-sky-400 font-bold px-3 py-1.5 rounded-lg')

                # ──────────────────────────────────────────────────────────
                # PANEL 3: CANDADO DE SEGURIDAD Y GUARDARRAÍLES MODULARES
                # ──────────────────────────────────────────────────────────
                with ui.tab_panel('guardrails').classes('p-0 gap-4 flex flex-col'):
                    
                    # Candado Maestro de Cuenta Real
                    is_unlocked = self.sec_cfg.get("real_trading_enabled", False)
                    lock_card_border = "border-red-500/60 bg-red-950/20" if is_unlocked else "border-emerald-500/50 bg-emerald-950/20"
                    with ui.card().classes(f'w-full p-4 rounded-xl border {lock_card_border} shadow-xl flex flex-col gap-3'):
                        with ui.row().classes('w-full justify-between items-center flex-wrap gap-2'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('lock_open' if is_unlocked else 'lock', color='red-400' if is_unlocked else 'emerald-400', size='24px')
                                ui.label('Candado de Seguridad de Cuenta Real (Dual-Lock)').classes('text-sm font-extrabold text-white')
                            self.badge_lock_state = ui.badge(
                                '⚡ OPERATIVA REAL HABILITADA' if is_unlocked else '🔒 MODO SOLO LECTURA (SEGURO)',
                                color='red-900' if is_unlocked else 'emerald-950'
                            ).classes('text-xs font-black px-3 py-1 rounded-lg border border-white/20')

                        ui.label(
                            'Mientras este candado esté cerrado, la aplicación tiene prohibido por código enviar cualquier orden de compra o venta a Binance Real, garantizando seguridad absoluta en modo Solo Lectura.'
                        ).classes('text-xs text-slate-300')

                        with ui.row().classes('w-full items-center justify-between pt-2 border-t border-white/10'):
                            self.switch_real_trading = ui.switch(
                                'Permitir Operaciones con Dinero Real en Binance',
                                value=is_unlocked,
                                on_change=self._toggle_real_trading_safety_switch
                            ).classes('text-xs font-bold text-white')

                    # Panel de Guardarraíles Modulares (Activables/Desactivables y Configurables)
                    with ui.card().classes('bg-[#111827] border border-[#1e293b] p-4 rounded-xl w-full shadow-lg gap-4'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('tune', color='amber-400', size='20px')
                            ui.label('Guardarraíles Cuantitativos de Riesgo (Modulares)').classes('text-sm font-bold text-white')
                        ui.label('Activa o desactiva cada protección individualmente y define los umbrales numéricos de seguridad para cuando operes en real:').classes('text-xs text-slate-400')

                        with ui.grid(columns=1).classes('w-full gap-3 md:grid-cols-3'):
                            # Guardarraíl 1: Apalancamiento Máximo
                            with ui.card().classes('bg-[#0a0e17] border border-[#1e293b] p-3.5 rounded-xl flex flex-col justify-between gap-2'):
                                with ui.column().classes('gap-1'):
                                    with ui.row().classes('w-full justify-between items-center'):
                                        ui.label('1. TOPE APALANCAMIENTO').classes('text-[10px] font-black text-amber-400 tracking-wider')
                                        self.switch_g_lev = ui.switch(value=self.sec_cfg.get("guardrail_max_leverage_enabled", True)).props('dense')
                                    ui.label('Bloquea órdenes que excedan este multiplicador').classes('text-[11px] text-slate-400')
                                with ui.row().classes('items-center gap-2'):
                                    self.input_g_lev = ui.number(
                                        label='Máx Leverage (x)',
                                        value=self.sec_cfg.get("max_allowed_leverage", 5),
                                        min=1, max=50, step=1
                                    ).props('outlined dense dark').classes('w-full text-xs font-mono')

                            # Guardarraíl 2: Límite Nocional por Orden ($ USD)
                            with ui.card().classes('bg-[#0a0e17] border border-[#1e293b] p-3.5 rounded-xl flex flex-col justify-between gap-2'):
                                with ui.column().classes('gap-1'):
                                    with ui.row().classes('w-full justify-between items-center'):
                                        ui.label('2. TOPE NOCIONAL ($ USD)').classes('text-[10px] font-black text-amber-400 tracking-wider')
                                        self.switch_g_usd = ui.switch(value=self.sec_cfg.get("guardrail_max_order_usd_enabled", True)).props('dense')
                                    ui.label('Protección contra fat-finger o cálculo excesivo').classes('text-[11px] text-slate-400')
                                with ui.row().classes('items-center gap-2'):
                                    self.input_g_usd = ui.number(
                                        label='Máx Orden ($ USD)',
                                        value=self.sec_cfg.get("max_order_notional_usd", 500.0),
                                        min=10, max=100000, step=50
                                    ).props('outlined dense dark').classes('w-full text-xs font-mono')

                            # Guardarraíl 3: Circuit Breaker de Pérdida Diaria
                            with ui.card().classes('bg-[#0a0e17] border border-[#1e293b] p-3.5 rounded-xl flex flex-col justify-between gap-2'):
                                with ui.column().classes('gap-1'):
                                    with ui.row().classes('w-full justify-between items-center'):
                                        ui.label('3. CIRCUIT BREAKER (%)').classes('text-[10px] font-black text-amber-400 tracking-wider')
                                        self.switch_g_cb = ui.switch(value=self.sec_cfg.get("guardrail_circuit_breaker_enabled", True)).props('dense')
                                    ui.label('Pausa bots y cierra candado si hay drawdown').classes('text-[11px] text-slate-400')
                                with ui.row().classes('items-center gap-2'):
                                    self.input_g_cb = ui.number(
                                        label='Máx Pérdida Diaria (%)',
                                        value=self.sec_cfg.get("daily_loss_circuit_breaker_pct", 3.0),
                                        min=0.5, max=25.0, step=0.5
                                    ).props('outlined dense dark').classes('w-full text-xs font-mono')

                        with ui.row().classes('w-full justify-end pt-2'):
                            ui.button(
                                'Guardar Parámetros de Guardarraíles',
                                icon='shield',
                                on_click=self._save_guardrails_action
                            ).props('dense outline color=amber-400').classes('text-xs text-amber-300 font-bold px-3 py-1.5 rounded-lg')

                # ──────────────────────────────────────────────────────────
                # PANEL 4: GUÍA Y PROTOCOLO DE SEGURIDAD
                # ──────────────────────────────────────────────────────────
                with ui.tab_panel('guide').classes('p-0 gap-3 flex flex-col'):
                    with ui.card().classes('bg-[#111827] border border-[#1e293b] p-4 rounded-xl w-full text-xs text-slate-300 gap-3'):
                        with ui.row().classes('items-center gap-2 text-white font-bold text-sm'):
                            ui.icon('security', color='emerald-400', size='20px')
                            ui.label('Protocolo de Seguridad de 4 Capas (Defensa en Profundidad)')
                        
                        ui.markdown('''
- **1. Regla de Oro en Binance:** NUNCA actives el permiso `Enable Withdrawals` (Retiros). Para monitoreo actual solo activa `Enable Reading`.
- **2. Candado Interno de Doble Llave:** Aunque una API tenga permisos de operar, la app NO ejecutará órdenes reales a menos que abras explícitamente el candado en esta pestaña.
- **3. Protección de Secret Ciego:** Tus claves secretas se guardan localmente en tu archivo `.env` y el servidor jamás las envía en texto claro al navegador del usuario.
- **4. Restricción de Red (CORS & Localhost):** La API solo acepta peticiones procedentes de `localhost:8000` y `127.0.0.1`, evitando accesos indebidos desde páginas web externas.
                        ''')

            # ──────────────────────────────────────────────────────────────
            # BARRA INFERIOR: SELECCIÓN DE ENTORNO PREDETERMINADO Y GUARDAR
            # ──────────────────────────────────────────────────────────────
            with ui.card().classes('bg-[#111827] border border-[#1e293b] p-4 rounded-xl w-full flex flex-col md:flex-row justify-between items-center gap-4 mt-2'):
                with ui.row().classes('items-center gap-3 flex-wrap'):
                    ui.label('Entorno Activo Global:').classes('text-xs font-bold text-slate-300 uppercase tracking-wide')
                    self.radio_active_net = ui.radio(
                        ['testnet', 'mainnet'],
                        value=self.creds['default_network']
                    ).props('inline dense dark').classes('text-xs')
                    
                    self.radio_active_net.props(':options="[\
                        {label: \'🟡 Testnet (Demo)\', value: \'testnet\'},\
                        {label: \'🌐 Real (Mainnet)\', value: \'mainnet\'}\
                    ]"')

                with ui.row().classes('items-center gap-2'):
                    if self.in_dialog and self.dialog_ref:
                        ui.button('Cancelar', on_click=self.dialog_ref.close).props('flat dense').classes('text-xs text-slate-400 hover:text-white px-3 py-2')
                    
                    self.btn_save = ui.button(
                        '💾 Guardar Credenciales',
                        icon='save',
                        on_click=self._save_credentials_action
                    ).classes('bg-amber-500 hover:bg-amber-400 text-black font-extrabold text-xs px-4 py-2 rounded-xl shadow-lg transition-all')

    async def _toggle_real_trading_safety_switch(self, e):
        """Maneja el encendido o apagado del candado de trading con dinero real con confirmación."""
        target_state = bool(e.value)
        if target_state:
            # Si intenta activar operativa real, exigir confirmación de advertencia
            async def confirm_unlock():
                ok, err = set_real_trading_enabled(True)
                if ok:
                    self.sec_cfg["real_trading_enabled"] = True
                    self.badge_lock_state.set_text('⚡ OPERATIVA REAL HABILITADA')
                    self.badge_lock_state.props('color=red-900')
                    ui.notify("⚠️ CANDADO ABIERTO: Las operaciones en Binance Real están ahora autorizadas.", type='warning', duration=6000)
                else:
                    self.switch_real_trading.value = False
                    ui.notify(f"Error al desbloquear candado: {err}", type='negative')

            def cancel_unlock():
                self.switch_real_trading.value = False

            with ui.dialog() as confirm_dialog, ui.card().classes('bg-[#0a0e17] border border-red-500 p-5 rounded-2xl max-w-md gap-3'):
                ui.label('⚠️ ADVERTENCIA: DESBLOQUEO DE CUENTA REAL').classes('text-sm font-black text-red-400')
                ui.label(
                    'Estás a punto de abrir el candado de seguridad para operar con DINERO REAL en Binance Futures. '
                    'Los bots activos podrán ejecutar órdenes en el exchange sujetas a los guardarraíles de riesgo configurados.'
                ).classes('text-xs text-slate-300 leading-relaxed')
                with ui.row().classes('w-full justify-end gap-2 mt-2'):
                    ui.button('Cancelar (Mantener Candado Cerrado)', on_click=lambda: [cancel_unlock(), confirm_dialog.close()]).props('dense flat').classes('text-xs text-slate-400')
                    ui.button('Acepto el Riesgo y Desbloqueo', on_click=lambda: [confirm_dialog.close(), asyncio.create_task(confirm_unlock())]).classes('bg-red-600 hover:bg-red-500 text-white font-extrabold text-xs px-3 py-1.5 rounded-lg')
            confirm_dialog.open()
        else:
            # Apagar candado -> MODO SOLO LECTURA INMEDIATO
            ok, err = set_real_trading_enabled(False)
            if ok:
                self.sec_cfg["real_trading_enabled"] = False
                self.badge_lock_state.set_text('🔒 MODO SOLO LECTURA (SEGURO)')
                self.badge_lock_state.props('color=emerald-950')
                ui.notify("🔒 Candado Cerrado: Cuenta Real en Modo Solo Lectura.", type='positive')
            else:
                ui.notify(f"Error al cerrar candado: {err}", type='negative')

    async def _save_guardrails_action(self):
        """Guarda la configuración modular de los guardarraíles de riesgo."""
        cfg = load_security_config()
        cfg["guardrail_max_leverage_enabled"] = bool(self.switch_g_lev.value)
        cfg["max_allowed_leverage"] = int(self.input_g_lev.value or 5)
        cfg["guardrail_max_order_usd_enabled"] = bool(self.switch_g_usd.value)
        cfg["max_order_notional_usd"] = float(self.input_g_usd.value or 500.0)
        cfg["guardrail_circuit_breaker_enabled"] = bool(self.switch_g_cb.value)
        cfg["daily_loss_circuit_breaker_pct"] = float(self.input_g_cb.value or 3.0)

        ok, err = save_security_config(cfg)
        if ok:
            self.sec_cfg = cfg
            ui.notify("🛡️ Guardarraíles de riesgo actualizados y aplicados.", type='positive')
        else:
            ui.notify(f"Error al guardar guardarraíles: {err}", type='negative')

    async def _test_testnet_connection(self):
        """Ejecuta test asíncrono de conectividad con Binance Testnet."""
        k = (self.input_testnet_key.value or "").strip()
        # Si el input de secret está vacío, usar el secreto preexistente cargado en el backend
        s = (self.input_testnet_secret.value or "").strip() or self.creds.get("testnet_secret_key", "")

        if not k or not s:
            ui.notify("Por favor ingresa tanto la API Key como el Secret de Testnet para probar.", type='warning')
            return

        self.btn_test_testnet.props('loading')
        self.testnet_result_card.classes(remove='hidden')
        self.testnet_result_label.set_text('⏳ Verificando conexión con Binance Futures Testnet...')
        self.testnet_result_label.classes('text-amber-400', remove='text-emerald-400 text-red-400')

        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(
            None,
            lambda: verify_binance_credentials(use_testnet=True, api_key=k, api_secret=s)
        )

        self.btn_test_testnet.props(remove='loading')
        if res.get("success"):
            lat = res.get("latency_ms", 0)
            bal = res.get("wallet_balance", 0.0)
            can_tr = "Sí" if res.get("can_trade") else "No"
            msg = f"✅ CONEXIÓN EXITOSA con Binance Testnet | Latencia: {lat}ms | Saldo: ${bal:,.2f} USDT | Trading Habilitado: {can_tr}"
            self.testnet_result_label.set_text(msg)
            self.testnet_result_label.classes('text-emerald-400 font-bold', remove='text-amber-400 text-red-400')
            self.badge_testnet.set_text('Conectada 🟢')
            self.badge_testnet.props('color=emerald-900')
            ui.notify("✅ Conexión con Binance Futures Testnet verificada con éxito.", type='positive')
        else:
            err = res.get("error", "Error desconocido")
            msg = f"❌ ERROR EN TESTNET: {err}"
            self.testnet_result_label.set_text(msg)
            self.testnet_result_label.classes('text-red-400', remove='text-amber-400 text-emerald-400')
            self.badge_testnet.set_text('Error 🔴')
            self.badge_testnet.props('color=red-950')
            ui.notify(f"🚨 Error en Binance Testnet: {err}", type='negative', duration=7000)

    async def _test_real_connection(self):
        """Ejecuta test asíncrono de conectividad con Binance Real."""
        k = (self.input_real_key.value or "").strip()
        # Si el input de secret está vacío, usar el secreto preexistente del backend
        s = (self.input_real_secret.value or "").strip() or self.creds.get("real_secret_key", "")

        if not k or not s:
            ui.notify("Por favor ingresa tanto la API Key como el Secret de Binance Real para probar.", type='warning')
            return

        self.btn_test_real.props('loading')
        self.real_result_card.classes(remove='hidden')
        self.real_result_label.set_text('⏳ Verificando conexión con Binance Real (Mainnet)...')
        self.real_result_label.classes('text-sky-400', remove='text-emerald-400 text-red-400')

        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(
            None,
            lambda: verify_binance_credentials(use_testnet=False, api_key=k, api_secret=s)
        )

        self.btn_test_real.props(remove='loading')
        if res.get("success"):
            lat = res.get("latency_ms", 0)
            bal = res.get("wallet_balance", 0.0)
            can_tr = "Sí" if res.get("can_trade") else "No"
            msg = f"✅ CONEXIÓN EXITOSA con Binance Real | Latencia: {lat}ms | Saldo Futuros: ${bal:,.2f} USDT | Trading Exchange: {can_tr}"
            self.real_result_label.set_text(msg)
            self.real_result_label.classes('text-emerald-400 font-bold', remove='text-sky-400 text-red-400')
            self.badge_real.set_text('Conectada 🟢')
            self.badge_real.props('color=emerald-900')
            ui.notify("✅ Conexión con Binance Real (Mainnet) verificada con éxito.", type='positive')
        else:
            err = res.get("error", "Error desconocido")
            msg = f"❌ ERROR EN BINANCE REAL: {err}"
            self.real_result_label.set_text(msg)
            self.real_result_label.classes('text-red-400', remove='text-sky-400 text-emerald-400')
            self.badge_real.set_text('Error 🔴')
            self.badge_real.props('color=red-950')
            ui.notify(f"🚨 Error en Binance Real: {err}", type='negative', duration=7000)

    async def _save_credentials_action(self):
        """Guarda las claves en el archivo .env sin sobreescribir secretos con cadenas vacías."""
        t_key = (self.input_testnet_key.value or "").strip()
        # Si el usuario no escribió un nuevo secret, conservar el existente
        t_sec_input = (self.input_testnet_secret.value or "").strip()
        t_sec = t_sec_input if t_sec_input else self.creds.get("testnet_secret_key", "")

        r_key = (self.input_real_key.value or "").strip()
        r_sec_input = (self.input_real_secret.value or "").strip()
        r_sec = r_sec_input if r_sec_input else self.creds.get("real_secret_key", "")

        active_net = self.radio_active_net.value or "testnet"

        self.btn_save.props('loading')
        loop = asyncio.get_event_loop()
        ok, err = await loop.run_in_executor(
            None,
            lambda: save_binance_credentials(
                testnet_key=t_key,
                testnet_secret=t_sec,
                real_key=r_key,
                real_secret=r_sec,
                default_network=active_net
            )
        )
        self.btn_save.props(remove='loading')

        if ok:
            ui.notify("💾 Credenciales de Binance guardadas y blindadas exitosamente.", type='positive', duration=5000)
            if self.on_saved:
                try:
                    if asyncio.iscoroutinefunction(self.on_saved):
                        await self.on_saved()
                    else:
                        self.on_saved()
                except Exception:
                    pass
            if self.in_dialog and self.dialog_ref:
                self.dialog_ref.close()
        else:
            ui.notify(f"🚨 Error al guardar credenciales: {err}", type='negative', duration=8000)


def open_api_credentials_dialog(on_saved_callback: Optional[Callable] = None):
    """Abre un diálogo modal para conectar y gestionar las APIs de Testnet y Real con Candado de Seguridad."""
    dialog = ui.dialog()
    with dialog, ui.card().classes('bg-[#0a0e17] border border-[#1e293b] p-5 rounded-2xl w-full max-w-4xl shadow-2xl'):
        manager = ApiCredentialsManager(on_saved=on_saved_callback, in_dialog=True, dialog_ref=dialog)
        manager.render()
    dialog.open()
    return dialog


def render_api_credentials_panel(on_saved_callback: Optional[Callable] = None):
    """Renderiza el panel de gestión de APIs directamente dentro de una página."""
    manager = ApiCredentialsManager(on_saved=on_saved_callback, in_dialog=False)
    manager.render()
    return manager
