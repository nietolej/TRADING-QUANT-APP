from nicegui import ui

def glass_card(title: str = None, icon: str = None, height: str = 'auto', color: str = 'slate-900'):
    """
    Crea una tarjeta con el estilo 'Dark Quant Terminal' (Glassmorphism).
    Devuelve el contexto de la tarjeta (ui.card) para usarse con `with glass_card():`
    """
    # Usamos bg-slate-800/50 y backdrop-blur para el glassmorphism
    card = ui.card().classes(f'w-full bg-{color}/50 backdrop-blur-md border border-slate-700/50 rounded-xl shadow-lg p-4')
    if height != 'auto':
        card.classes(f'h-[{height}]')
        
    if title or icon:
        with card:
            with ui.row().classes('items-center gap-2 mb-4 w-full border-b border-slate-700/50 pb-2'):
                if icon:
                    ui.icon(icon, size='1.5rem').classes('text-cyan-400')
                if title:
                    ui.label(title).classes('text-lg font-bold text-slate-200 tracking-wide')
    return card

def stat_card(title: str, value: str, icon: str, color_class: str = 'text-cyan-400'):
    """
    Crea una tarjeta pequeña de estadística para los dashboards (ej. Sharpe Ratio, Win Rate).
    """
    with ui.card().classes('flex-1 bg-slate-800/40 backdrop-blur-sm border border-slate-700/50 rounded-xl p-4 shadow-md'):
        with ui.row().classes('items-center justify-between w-full mb-1'):
            ui.label(title).classes('text-sm text-slate-400 font-semibold uppercase tracking-wider')
            ui.icon(icon, size='1.2rem').classes(color_class)
        ui.label(value).classes(f'text-2xl font-black {color_class}')
