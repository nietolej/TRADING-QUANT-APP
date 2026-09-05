"""
Componente de Chat Copiloto Cuantitativo para la Web GUI (NiceGUI).
Conectado directamente a las herramientas del Servidor Binance MCP.
Diseño Bloomberg Obsidian Edition con panel flotante colapsable.
"""

import json
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional
from nicegui import ui

from api.mcp.binance_mcp_server import (
    binance_test_connection,
    binance_get_account_balance,
    binance_get_positions,
    binance_get_open_orders,
    binance_get_market_price,
    binance_analyze_portfolio_risk,
    binance_cancel_all_orders,
)


class QuantCopilotChat:
    """Gestor del Copiloto Cuantitativo conectado al Binance MCP Server."""

    def __init__(self):
        self.is_open = False
        self.is_thinking = False
        self.messages: List[Dict[str, Any]] = [
            {
                "sender": "bot",
                "time": datetime.now().strftime("%H:%M"),
                "text": "¡Hola! Soy tu **Copiloto Cuantitativo** conectado a **Binance MCP (Testnet)**.\n\nPuedo auditar tu saldo, vigilar tus posiciones abiertas, calcular tu **VaR de riesgo** en tiempo real o cancelar órdenes huérfanas.\n\n¿Qué deseas consultar hoy?",
                "chips": [
                    {"label": "💰 Mi Saldo Testnet", "query": "¿Cuál es mi saldo y margen disponible en Testnet?"},
                    {"label": "📈 Posiciones Abiertas", "query": "¿Tengo posiciones abiertas en futuros?"},
                    {"label": "🛡️ Análisis de Riesgo & VaR", "query": "Analiza el riesgo de mi cartera y calcula el VaR"},
                    {"label": "⚡ Precio BTC & Funding", "query": "Dame el precio y tasa de financiación de BTC"},
                ]
            }
        ]

    def render(self):
        """Renderiza el widget flotante del Copiloto en la interfaz web."""
        
        # 1. Contenedor de la Ventana Flotante de Chat
        self.chat_card = ui.card().classes(
            'fixed bottom-20 right-4 md:right-6 z-50 '
            'w-[92vw] sm:w-[420px] md:w-[440px] h-[560px] max-h-[82vh] '
            'bg-[#0a0e17]/95 backdrop-blur-xl border border-amber-500/40 '
            'rounded-2xl shadow-2xl flex flex-col p-0 overflow-hidden transition-all duration-300'
        )
        self.chat_card.set_visibility(self.is_open)

        with self.chat_card:
            # Cabecera del Chat
            with ui.row().classes('w-full bg-[#111827] px-4 py-3 border-b border-[#1e293b] items-center justify-between flex-none'):
                with ui.row().classes('items-center gap-2.5'):
                    with ui.row().classes('items-center justify-center w-8 h-8 rounded-lg bg-amber-500/20 border border-amber-500/40 text-amber-400'):
                        ui.icon('smart_toy', size='20px')
                    with ui.column().classes('gap-0'):
                        with ui.row().classes('items-center gap-1.5'):
                            ui.label('COPILOTO CUANT IA').classes('text-xs font-black text-white tracking-wider font-heading')
                            ui.icon('circle', size='8px').classes('text-emerald-400 animate-pulse')
                        ui.label('Binance MCP Server Activo').classes('text-[10px] text-amber-400 font-mono')

                with ui.row().classes('items-center gap-1'):
                    ui.button(icon='delete_outline', on_click=self._clear_history).props('flat round dense').classes('text-slate-400 hover:text-red-400').tooltip('Limpiar conversación')
                    ui.button(icon='close', on_click=self.toggle).props('flat round dense').classes('text-slate-400 hover:text-white').tooltip('Cerrar')

            # Área de Mensajes con Scroll
            self.messages_scroll = ui.scroll_area().classes('w-full flex-1 p-4 gap-3 bg-[#0a0e17]/60')
            with self.messages_scroll:
                self.messages_container = ui.column().classes('w-full gap-3.5')
                self._render_messages_dom()

            # Píldoras de Acciones Rápidas
            with ui.row().classes('w-full px-3 py-1.5 bg-[#111827]/80 border-t border-[#1e293b]/60 items-center gap-1.5 overflow-x-auto flex-nowrap'):
                def quick_pill(lbl: str, prompt_text: str):
                    ui.button(
                        lbl, 
                        on_click=lambda p=prompt_text: self._send_user_prompt(p)
                    ).props('dense flat').classes('text-[10px] text-amber-400 hover:text-amber-300 bg-amber-500/10 hover:bg-amber-500/20 px-2 py-0.5 rounded-full whitespace-nowrap border border-amber-500/30 transition-all font-mono')

                quick_pill('💰 Saldo', '¿Cuál es mi saldo en Testnet?')
                quick_pill('📈 Posiciones', '¿Qué posiciones tengo abiertas?')
                quick_pill('🛡️ VaR & Riesgo', 'Analiza el riesgo de mi cartera')
                quick_pill('⚡ BTC & Funding', 'Precio y funding de BTCUSDT')
                quick_pill('🗑️ Limpiar Órdenes', 'Cancela todas las órdenes de BTCUSDT')

            # Input inferior para escribir mensaje
            with ui.row().classes('w-full bg-[#111827] p-2.5 border-t border-[#1e293b] items-center gap-2 flex-none'):
                self.input_field = ui.input(
                    placeholder='Pregunta sobre tu balance, riesgo, precios...'
                ).props('outlined dense dark autofocus').classes('flex-1 font-mono text-xs')
                self.input_field.on('keydown.enter', self._on_enter_press)

                self.btn_send = ui.button(
                    icon='send', 
                    on_click=self._handle_send_click
                ).props('dense round').classes('bg-amber-500 hover:bg-amber-400 text-black shadow-md flex-none')

        # 2. Botón Flotante Animado (Floating Action Button - FAB)
        with ui.button(
            on_click=self.toggle
        ).classes(
            'fixed bottom-5 right-5 z-50 rounded-full w-14 h-14 '
            'bg-gradient-to-r from-amber-500 to-yellow-400 hover:from-amber-400 hover:to-yellow-300 '
            'text-black shadow-2xl hover:scale-105 transition-all flex items-center justify-center border-2 border-amber-300/50'
        ).tooltip('Abrir Copiloto Cuantitativo (Binance MCP)'):
            ui.icon('smart_toy', size='28px')
            ui.badge('IA', color='emerald-500').classes('absolute -top-1 -right-1 font-mono font-bold text-[10px] px-1.5 py-0.5 rounded-full shadow')

    def toggle(self):
        """Abre o cierra la ventana de chat."""
        self.is_open = not self.is_open
        self.chat_card.set_visibility(self.is_open)
        if self.is_open:
            ui.timer(0.1, lambda: self.messages_scroll.scroll_to(percent=1.0), once=True)

    def _on_enter_press(self, e):
        self._handle_send_click()

    def _handle_send_click(self):
        text = (self.input_field.value or "").strip()
        if not text:
            return
        self.input_field.value = ""
        self._send_user_prompt(text)

    def _send_user_prompt(self, text: str):
        """Añade el mensaje del usuario y procesa la respuesta usando el MCP Server."""
        self.messages.append({
            "sender": "user",
            "time": datetime.now().strftime("%H:%M"),
            "text": text
        })
        self._render_messages_dom()
        asyncio.create_task(self._process_copilot_response(text))

    def _render_messages_dom(self):
        """Reconstruye los mensajes dentro del contenedor."""
        self.messages_container.clear()
        with self.messages_container:
            for m in self.messages:
                is_user = (m["sender"] == "user")
                align = "justify-end" if is_user else "justify-start"
                bubble_bg = "bg-blue-600/90 text-white" if is_user else "bg-[#111827] text-slate-200 border border-[#1e293b]"

                with ui.row().classes(f'w-full {align}'):
                    with ui.column().classes(f'max-w-[88%] p-3 rounded-2xl {bubble_bg} shadow-md gap-1.5'):
                        with ui.row().classes('items-center justify-between w-full gap-2 border-b border-white/10 pb-1'):
                            sender_title = "TÚ" if is_user else "🤖 COPILOTO CUANT"
                            title_color = "text-blue-200" if is_user else "text-amber-400"
                            ui.label(sender_title).classes(f'text-[10px] font-bold uppercase font-mono {title_color}')
                            ui.label(m.get("time", "")).classes('text-[9px] text-slate-400 font-mono')

                        # Contenido Markdown
                        ui.markdown(m["text"]).classes('text-xs leading-relaxed break-words')

                        # Chips interactivos sugeridos
                        if m.get("chips"):
                            with ui.row().classes('gap-1.5 pt-1.5 flex-wrap'):
                                for c in m["chips"]:
                                    ui.button(
                                        c["label"], 
                                        on_click=lambda q=c["query"]: self._send_user_prompt(q)
                                    ).props('dense outline').classes('text-[10px] text-amber-400 border-amber-500/30 rounded-lg px-2 py-0.5 hover:bg-amber-500/20 font-mono')

            if self.is_thinking:
                with ui.row().classes('w-full justify-start items-center gap-2 p-2'):
                    ui.spinner('dots', size='sm', color='amber-400')
                    ui.label('Consultando Binance MCP Server...').classes('text-[11px] text-amber-400 font-mono animate-pulse')

        ui.timer(0.05, lambda: self.messages_scroll.scroll_to(percent=1.0), once=True)

    async def _process_copilot_response(self, query: str):
        """Despachador agéntico que mapea la intención del usuario a las herramientas del MCP."""
        self.is_thinking = True
        self._render_messages_dom()

        q_lower = query.lower()
        loop = asyncio.get_event_loop()

        reply_text = ""
        chips = []

        try:
            # 1. Saldo y Balances
            if any(w in q_lower for w in ["saldo", "balance", "margen", "fondos", "cuenta", "capital"]):
                raw = await loop.run_in_executor(None, lambda: binance_get_account_balance(use_testnet=True))
                data = json.loads(raw)
                if data.get("success"):
                    tot = data.get("total_wallet_balance_usd", 0)
                    avail = data.get("available_balance_usd", 0)
                    pnl = data.get("total_unrealized_pnl_usd", 0)
                    sign = "+" if pnl >= 0 else ""
                    reply_text = (
                        f"📊 **Estado de Cuenta Binance ({data.get('network', 'Testnet')}):**\n\n"
                        f"• **Balance Total:** `${tot:,.2f} USD`\n"
                        f"• **Margen Disponible:** `${avail:,.2f} USDT`\n"
                        f"• **PnL No Realizado:** `{sign}${pnl:,.2f} USDT`\n"
                        f"• **Modo Multiactivos (BTC Colateral):** `{'ACTIVO ✅' if data.get('multi_assets_margin') else 'INACTIVO ⚪'}`\n\n"
                    )
                    assets = data.get("assets", [])
                    if assets:
                        reply_text += "**Activos Principales:**\n"
                        for a in assets[:4]:
                            reply_text += f"- **{a['asset']}:** {a['wallet_balance']} (≈ ${a.get('usd_value', 0):,.2f} USD)\n"
                else:
                    reply_text = f"⚠️ **Error al consultar balance:** {data.get('error')}\n\n*Recuerda que puedes conectar o actualizar tus claves de Testnet en el botón `Conectar APIs Exchange`.*"

                chips = [
                    {"label": "🛡️ Calcular VaR de Riesgo", "query": "Analiza el riesgo de mi cartera"},
                    {"label": "📈 Ver Posiciones", "query": "¿Qué posiciones tengo abiertas?"}
                ]

            # 2. Posiciones Abiertas
            elif any(w in q_lower for w in ["posicion", "posiciones", "trades", "abiertas", "contratos"]):
                raw = await loop.run_in_executor(None, lambda: binance_get_positions(use_testnet=True))
                data = json.loads(raw)
                if data.get("success"):
                    count = data.get("open_positions_count", 0)
                    if count == 0:
                        reply_text = "🟢 **Sin Posiciones Abiertas:**\n\nActualmente no tienes ninguna posición activa en Binance Futures Testnet. Tu margen está al 100% disponible."
                    else:
                        reply_text = f"📈 **Posiciones Activas ({count}):**\n\n"
                        reply_text += f"• **Exposición Nocional Total:** `${data.get('total_notional_usd', 0):,.2f} USD`\n"
                        reply_text += f"• **Apalancamiento Efectivo:** `{data.get('effective_leverage', 0)}x`\n\n"
                        for p in data.get("positions", []):
                            side_icon = "📈 LONG" if p.get('side') == 'LONG' else "📉 SHORT"
                            reply_text += (
                                f"**{p.get('symbol')}** ({side_icon}):\n"
                                f"  - Tamaño: `{p.get('amount')}` | Entrada: `${p.get('entry_price'):,.2f}`\n"
                                f"  - Liq. Price: `${p.get('liq_price'):,.2f}` (Distancia: `{p.get('liq_distance_pct', '--')}%`)\n"
                                f"  - PnL: `{p.get('unrealized_pnl')} USDT`\n\n"
                            )
                else:
                    reply_text = f"⚠️ **Error al consultar posiciones:** {data.get('error')}"

                chips = [
                    {"label": "💰 Ver Balance", "query": "¿Cuál es mi saldo?"},
                    {"label": "🛡️ Test de Estrés", "query": "Haz un test de estrés de mercado"}
                ]

            # 3. Diagnóstico Cuantitativo y VaR de Riesgo
            elif any(w in q_lower for w in ["riesgo", "var", "cvar", "estres", "salud", "drawdown", "caida"]):
                raw = await loop.run_in_executor(None, lambda: binance_analyze_portfolio_risk(use_testnet=True))
                data = json.loads(raw)
                if data.get("success"):
                    score = data.get("health_score", 100)
                    badge = data.get("health_badge", "🟢 ÓPTIMO")
                    var_usd = data.get("var_95_usd", 0)
                    var_pct = data.get("var_95_pct", 0)
                    cvar_pct = data.get("cvar_95_pct", 0)

                    reply_text = (
                        f"🛡️ **Diagnóstico Cuantitativo de Riesgo:**\n\n"
                        f"• **Score de Salud:** `{score:.0f}/100` ({badge})\n"
                        f"• **Value at Risk (VaR 95% 1D):** `${var_usd:,.2f} USD` (`{var_pct:.1f}%` de la cuenta)\n"
                        f"• **Conditional VaR (CVaR 95%):** `{cvar_pct:.1f}%`\n"
                        f"• **Postura de Mercado:** {data.get('market_posture', '-')}\n"
                        f"• **Seguridad de Liquidación:** {data.get('liquidation_safety', '-')}\n\n"
                    )
                    recs = data.get("recommendations", [])
                    if recs:
                        reply_text += "**Recomendaciones del Motor Cuant:**\n"
                        for r in recs[:3]:
                            reply_text += f"• {r}\n"
                else:
                    reply_text = f"⚠️ **Error calculando riesgo:** {data.get('error')}"

                chips = [
                    {"label": "📈 Posiciones", "query": "¿Qué posiciones tengo abiertas?"},
                    {"label": "⚡ Precio BTC", "query": "Precio actual de BTC"}
                ]

            # 4. Precio de Mercado y Funding Rate
            elif any(w in q_lower for w in ["precio", "cotizacion", "funding", "tasa", "btc", "eth", "sol"]):
                sym = "BTCUSDT"
                if "eth" in q_lower:
                    sym = "ETHUSDT"
                elif "sol" in q_lower:
                    sym = "SOLUSDT"

                raw = await loop.run_in_executor(None, lambda: binance_get_market_price(symbol=sym, use_testnet=True))
                data = json.loads(raw)
                if data.get("success"):
                    p = data.get("price", 0)
                    mp = data.get("mark_price", 0)
                    fr = data.get("last_funding_rate", 0) * 100.0
                    reply_text = (
                        f"⚡ **Mercado en Tiempo Real ({data.get('symbol')}):**\n\n"
                        f"• **Último Precio:** `${p:,.2f} USDT`\n"
                        f"• **Precio de Marca:** `${mp:,.2f} USDT`\n"
                        f"• **Tasa de Financiación (Funding Rate):** `{fr:+.4f}%`\n"
                        f"• **Entorno:** Binance Futures Testnet\n"
                    )
                else:
                    reply_text = f"⚠️ **Error al consultar mercado:** {data.get('error')}"

                chips = [
                    {"label": "💰 Consultar Saldo", "query": "¿Cuál es mi saldo?"},
                    {"label": "⚡ Ver ETHUSDT", "query": "Precio y funding de ETH"},
                ]

            # 5. Cancelación de Órdenes
            elif any(w in q_lower for w in ["cancelar", "cancela", "cerrar ordenes", "limpiar"]):
                raw = await loop.run_in_executor(None, lambda: binance_cancel_all_orders(symbol="BTCUSDT", use_testnet=True))
                data = json.loads(raw)
                if data.get("success"):
                    reply_text = f"🗑️ **Órdenes Canceladas con Éxito:**\n\n{data.get('message')}\n\nEl libro de órdenes para BTCUSDT en Testnet ha quedado limpio."
                else:
                    reply_text = f"⚠️ **Fallo al cancelar:** {data.get('message')}"

                chips = [
                    {"label": "💰 Ver Saldo", "query": "¿Cuál es mi saldo en Testnet?"}
                ]

            # 6. Preguntas educativas o de funcionamiento de la plataforma
            elif any(w in q_lower for w in ["que es", "como funciona", "ayuda", "mle", "halving", "sharpe", "sortino"]):
                reply_text = (
                    "💡 **Guía de la Plataforma Cuantitativa:**\n\n"
                    "• **Value at Risk (VaR 95%):** Máxima pérdida estimada a 1 día con 95% de confianza estadística.\n"
                    "• **Filtro MLE:** Termómetro de probabilidad basado en modelos de Maximum Likelihood Estimation para filtrar falsas rupturas.\n"
                    "• **Modo Multiactivos:** Permite utilizar BTC u otras criptomonedas como garantía global sin necesidad de venderlas a USDT.\n"
                    "• **Binance MCP:** Servidor estandarizado que me permite inspeccionar balances, posiciones y ejecutar órdenes de forma segura."
                )
                chips = [
                    {"label": "💰 Ver mi Saldo", "query": "¿Cuál es mi saldo?"},
                    {"label": "🛡️ Mi Nivel de Riesgo", "query": "Analiza el riesgo de mi cartera"}
                ]

            # 7. Respuesta genérica
            else:
                reply_text = (
                    f"He recibido tu consulta: *\"{query}\"*.\n\n"
                    "Puedo ejecutar consultas en tiempo real a través del **Binance MCP Server**:\n"
                    "1. Consulta de **Saldo y Margen** (`💰 Mi Saldo`)\n"
                    "2. Auditoría de **Posiciones de Futuros** (`📈 Posiciones`)\n"
                    "3. Cálculo de **VaR y Test de Estrés** (`🛡️ Análisis de Riesgo`)\n"
                    "4. Consulta de **Precio y Tasa de Financiación** (`⚡ Precios`)\n"
                    "5. **Cancelación de órdenes huérfanas** (`🗑️ Cancelar Órdenes`)"
                )
                chips = [
                    {"label": "💰 Mi Saldo", "query": "¿Cuál es mi saldo?"},
                    {"label": "🛡️ Mi Riesgo VaR", "query": "Analiza el riesgo de mi cartera"},
                    {"label": "⚡ Precio BTC", "query": "Precio y funding de BTC"}
                ]

        except Exception as err:
            reply_text = f"🚨 **Error interno del Copiloto:** {err}"

        self.is_thinking = False
        self.messages.append({
            "sender": "bot",
            "time": datetime.now().strftime("%H:%M"),
            "text": reply_text,
            "chips": chips
        })
        self._render_messages_dom()

    def _clear_history(self):
        """Limpia el historial de chat conservando el saludo inicial."""
        self.messages = [
            {
                "sender": "bot",
                "time": datetime.now().strftime("%H:%M"),
                "text": "Historial limpiado. ¿En qué más puedo ayudarte con Binance MCP?",
                "chips": [
                    {"label": "💰 Mi Saldo Testnet", "query": "¿Cuál es mi saldo en Testnet?"},
                    {"label": "🛡️ Análisis de Riesgo", "query": "Analiza el riesgo de mi cartera"},
                    {"label": "⚡ Precio BTC", "query": "Precio y funding de BTC"}
                ]
            }
        ]
        self._render_messages_dom()


def render_quant_copilot():
    """Función para renderizar el Copiloto Cuantitativo en cualquier página o en main.py."""
    copilot = QuantCopilotChat()
    copilot.render()
    return copilot
