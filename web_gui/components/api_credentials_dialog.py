import os
import asyncio
from typing import Optional, Callable, Dict, Any
from nicegui import ui

from execution_engine.binance_client import (
    get_binance_credentials,
    save_binance_credentials,
    verify_binance_credentials,
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
    de Binance tanto para Futures Testnet (Demo) como para Real (Mainnet).
    """

    def __init__(self, on_saved: Optional[Callable] = None, in_dialog: bool = False, dialog_ref=None):
        self.on_saved = on_saved
        self.in_dialog = in_dialog
        self.dialog_ref = dialog_ref
        self.creds = get_binance_credentials()

    def render(self):
        with ui.column().classes('w-full gap-4 text-white'):
            # Cabecera
            with ui.row().classes('w-full justify-between items-center pb-3 border-b border-[#1e293b] flex-wrap gap-2'):
                with ui.row().classes('items-center gap-3'):
                    with ui.row().classes('items-center justify-center w-10 h-10 rounded-xl bg-amber-500/15 border border-amber-500/30 text-amber-400'):
                        ui.icon('vpn_key', size='24px')
                    with ui.column().classes('gap-0.5'):
                        ui.label('Conexión y Gestión de APIs de Exchange').classes('text-lg md:text-xl font-extrabold text-white font-heading')
                        ui.label('Configura tus claves para Binance Futures Testnet (Demo) y Binance Real (Mainnet)').classes('text-xs text-slate-400')
                
                if self.in_dialog and self.dialog_ref:
                    ui.button(icon='close', on_click=self.dialog_ref.close).props('flat round dense').classes('text-slate-400 hover:text-white')

            # Pestañas para Testnet vs Real vs Ajustes Globales
            with ui.tabs().classes('w-full text-slate-300 border-b border-[#1e293b]') as tabs:
                tab_testnet = ui.tab('testnet', label='🟡 Futures Testnet (Demo)', icon='science').classes('text-xs md:text-sm font-bold')
                tab_real = ui.tab('real', label='🌐 Binance Real (Producción)', icon='public').classes('text-xs md:text-sm font-bold')
                tab_guide = ui.tab('guide', label='ℹ️ Guía & Seguridad', icon='shield').classes('text-xs md:text-sm font-bold')

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
                                ui.label('TESTNET SECRET KEY').classes('text-[10px] font-extrabold text-slate-400 uppercase tracking-wider font-mono')
                                self.input_testnet_secret = ui.input(
                                    placeholder='Ingresa tu Secret Key de Testnet...',
                                    value=self.creds['testnet_secret_key'],
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

                        with ui.row().classes('w-full bg-amber-950/40 border border-amber-500/30 p-2.5 rounded-lg items-center gap-2 text-amber-300 text-xs'):
                            ui.icon('warning', size='18px').classes('flex-none text-amber-400')
                            ui.label('Por seguridad, tu API Key en Binance NUNCA debe tener habilitada la opción de "Habilitar Retiros" (Withdrawals). Solo requiere permisos de Lectura y Trading de Futuros.').classes('flex-1')

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
                                ui.label('REAL SECRET KEY (MAINNET)').classes('text-[10px] font-extrabold text-slate-400 uppercase tracking-wider font-mono')
                                self.input_real_secret = ui.input(
                                    placeholder='Ingresa tu Secret Key de Binance Real...',
                                    value=self.creds['real_secret_key'],
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
                # PANEL 3: GUÍA Y SEGURIDAD
                # ──────────────────────────────────────────────────────────
                with ui.tab_panel('guide').classes('p-0 gap-3 flex flex-col'):
                    with ui.card().classes('bg-[#111827] border border-[#1e293b] p-4 rounded-xl w-full text-xs text-slate-300 gap-3'):
                        with ui.row().classes('items-center gap-2 text-white font-bold text-sm'):
                            ui.icon('security', color='emerald-400', size='20px')
                            ui.label('Protocolo de Seguridad y Almacenamiento Local')
                        
                        ui.markdown('''
- **Almacenamiento Local Seguro:** Las credenciales se guardan de forma encriptada en tu archivo local `.env` en este servidor. Nunca se comparten con ningún tercero.
- **Permisos Mínimos Necesarios:**
  - ✅ **Habilitar Lectura** (*Can Read*)
  - ✅ **Habilitar Futuros** (*Futures Trading*)
  - ❌ **DESHABILITAR Retiros** (*Withdrawals*) — ¡Nunca actives retiros para aplicaciones de trading!
- **Restricción de IP (Opcional pero recomendado):** Puedes restringir el acceso de tu API Key a la dirección IP pública de este servidor para máxima protección.
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
                    
                    # Labels bonitos para el radio
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

    async def _test_testnet_connection(self):
        """Ejecuta test asíncrono de conectividad con Binance Testnet usando los valores actuales del formulario."""
        k = (self.input_testnet_key.value or "").strip()
        s = (self.input_testnet_secret.value or "").strip()

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
        """Ejecuta test asíncrono de conectividad con Binance Real usando los valores actuales del formulario."""
        k = (self.input_real_key.value or "").strip()
        s = (self.input_real_secret.value or "").strip()

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
            msg = f"✅ CONEXIÓN EXITOSA con Binance Real | Latencia: {lat}ms | Saldo Futuros: ${bal:,.2f} USDT | Trading Habilitado: {can_tr}"
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
        """Guarda las claves en el archivo .env y en os.environ de manera atómica."""
        t_key = (self.input_testnet_key.value or "").strip()
        t_sec = (self.input_testnet_secret.value or "").strip()
        r_key = (self.input_real_key.value or "").strip()
        r_sec = (self.input_real_secret.value or "").strip()
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
            ui.notify("💾 Credenciales de Binance guardadas y aplicadas exitosamente.", type='positive', duration=5000)
            if self.on_saved:
                try:
                    if asyncio.iscoroutinefunction(self.on_saved):
                        await self.on_saved()
                    else:
                        self.on_saved()
                except Exception as cb_err:
                    pass
            if self.in_dialog and self.dialog_ref:
                self.dialog_ref.close()
        else:
            ui.notify(f"🚨 Error al guardar credenciales: {err}", type='negative', duration=8000)


def open_api_credentials_dialog(on_saved_callback: Optional[Callable] = None):
    """Abre un diálogo modal para conectar y gestionar las APIs de Testnet y Real."""
    dialog = ui.dialog()
    with dialog, ui.card().classes('bg-[#0a0e17] border border-[#1e293b] p-5 rounded-2xl w-full max-w-3xl shadow-2xl'):
        manager = ApiCredentialsManager(on_saved=on_saved_callback, in_dialog=True, dialog_ref=dialog)
        manager.render()
    dialog.open()
    return dialog


def render_api_credentials_panel(on_saved_callback: Optional[Callable] = None):
    """Renderiza el panel de gestión de APIs directamente dentro de una página."""
    manager = ApiCredentialsManager(on_saved=on_saved_callback, in_dialog=False)
    manager.render()
    return manager
