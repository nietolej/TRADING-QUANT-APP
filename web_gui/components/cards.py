from nicegui import ui

def glass_card(title: str = None, icon: str = None, height: str = 'auto', color: str = 'slate-900'):
    """
    Crea una tarjeta con el estilo 'Bloomberg Obsidian' (Dark Quant Terminal).
    Devuelve el contexto de la tarjeta (ui.card) para usarse con `with glass_card():`
    """
    card = ui.card().classes(f'w-full bg-[#111827] border border-[#1e293b] rounded-xl shadow-xl p-5 hover:border-[#2a364f] transition-all')
    if height != 'auto':
        card.classes(f'h-[{height}]')
        
    if title or icon:
        with card:
            with ui.row().classes('items-center gap-2.5 mb-4 w-full border-b border-[#1e293b] pb-2.5'):
                if icon:
                    ui.icon(icon, size='1.6rem').classes('text-amber-400')
                if title:
                    ui.label(title).classes('text-lg font-bold text-white tracking-wide font-heading')
    return card

def stat_card(title: str, value: str, icon: str, color_class: str = 'text-amber-400', trend_label: str = None):
    """
    Crea una tarjeta pequeña de estadística para los dashboards (ej. Sharpe Ratio, Win Rate, Profit).
    Optimizado con JetBrains Mono para lectura numérica instantánea.
    """
    with ui.card().classes('flex-1 bg-[#111827] border border-[#1e293b] rounded-xl p-4 shadow-lg hover:border-amber-500/40 transition-all'):
        with ui.row().classes('items-center justify-between w-full mb-2'):
            ui.label(title).classes('text-xs text-slate-400 font-bold uppercase tracking-wider')
            ui.icon(icon, size='1.3rem').classes(color_class)
        with ui.row().classes('items-baseline justify-between w-full'):
            ui.label(value).classes(f'text-2xl md:text-3xl font-extrabold {color_class} font-mono')
            if trend_label:
                ui.label(trend_label).classes('text-xs font-semibold text-emerald-400 bg-emerald-950/60 px-2 py-0.5 rounded border border-emerald-800/40')

