"""
Módulo de Seguridad Operativa y Gestión de Riesgo (TRADING-QUANT-APP).
Implementa:
- Candado de Seguridad de Doble Llave (Dual-Lock Safety Switch): MODO SOLO LECTURA vs TRADING REAL.
- Guardarraíles Cuantitativos Modulares (Activables/Desactivables y Configurables):
    1. Límite de Apalancamiento Máximo (Leverage Cap)
    2. Límite Nocional Máximo por Orden ($ USD)
    3. Circuit Breaker de Pérdida Diaria (Daily Loss Limit Drawdown)
- Persistencia atómica en config/security_config.json y variables de entorno.
"""

import os
import json
import logging
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger("SecurityManager")

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT_DIR, "config", "security_config.json")

DEFAULT_SECURITY_CONFIG: Dict[str, Any] = {
    # ── Candado Principal de Cuenta Real ──
    # False = MODO SOLO LECTURA (Rechaza toda orden real a nivel de CPU)
    # True  = MODO TRADING REAL HABILITADO
    "real_trading_enabled": False,

    # ── Guardarraíl 1: Límite de Apalancamiento ──
    "guardrail_max_leverage_enabled": True,
    "max_allowed_leverage": 5,

    # ── Guardarraíl 2: Límite Nocional por Orden ($ USD) ──
    "guardrail_max_order_usd_enabled": True,
    "max_order_notional_usd": 500.0,

    # ── Guardarraíl 3: Circuit Breaker de Pérdida Máxima Diaria ──
    "guardrail_circuit_breaker_enabled": True,
    "daily_loss_circuit_breaker_pct": 3.0,

    # Timestamp de última activación/modificación
    "last_updated": None
}


def load_security_config() -> Dict[str, Any]:
    """Carga la configuración de seguridad desde el archivo local o valores por defecto."""
    if not os.path.exists(CONFIG_PATH):
        save_security_config(DEFAULT_SECURITY_CONFIG)
        return DEFAULT_SECURITY_CONFIG.copy()

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Combinar con defaults para asegurar que todas las llaves existan
            cfg = DEFAULT_SECURITY_CONFIG.copy()
            cfg.update(data)
            return cfg
    except Exception as e:
        logger.error("Error al cargar %s: %s. Usando defaults seguros.", CONFIG_PATH, e)
        return DEFAULT_SECURITY_CONFIG.copy()


def save_security_config(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """Guarda la configuración de seguridad en disco y actualiza variables de entorno."""
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

        # Sincronizar variable de entorno para compatibilidad global
        os.environ["BINANCE_REAL_TRADING_ENABLED"] = "true" if config.get("real_trading_enabled") else "false"
        return True, None
    except Exception as e:
        logger.error("Error al guardar security_config.json: %s", e)
        return False, str(e)


def is_real_trading_enabled() -> bool:
    """
    Retorna True solo si el candado interno ha sido explícitamente abierto
    por el usuario para permitir operaciones en Binance Real.
    Por defecto es False (Modo Solo Lectura).
    """
    cfg = load_security_config()
    return bool(cfg.get("real_trading_enabled", False))


def set_real_trading_enabled(enabled: bool) -> Tuple[bool, Optional[str]]:
    """Abre o cierra el candado de operaciones con dinero real."""
    cfg = load_security_config()
    cfg["real_trading_enabled"] = bool(enabled)
    from datetime import datetime
    cfg["last_updated"] = datetime.now().isoformat()
    logger.warning("SEGURIDAD: Candado de trading real cambiado a %s", "ABIERTO (OPERATIVA HABILITADA)" if enabled else "CERRADO (SOLO LECTURA)")
    return save_security_config(cfg)


def check_circuit_breaker(daily_pnl_pct: float, bot_name: str = "") -> Tuple[bool, Optional[str]]:
    """
    Evalúa el Guardarraíl 3 (Circuit Breaker de Pérdida Diaria) para cuentas reales.
    Si la pérdida diaria (%) de un bot supera el umbral configurado, cierra el candado
    de trading real automáticamente (una sola vez por día) para prevenir mayor exposición.

    Returns:
        (breaker_tripped, alert_message) — alert_message es None si ya se había
        disparado hoy (para evitar notificaciones repetidas).
    """
    cfg = load_security_config()
    if not cfg.get("guardrail_circuit_breaker_enabled", True):
        return False, None

    max_loss_pct = abs(float(cfg.get("daily_loss_circuit_breaker_pct", 3.0)))
    if daily_pnl_pct > -max_loss_pct:
        return False, None

    from datetime import datetime
    today_str = datetime.now().strftime("%Y-%m-%d")
    if cfg.get("circuit_breaker_tripped_date") == today_str:
        return True, None

    cfg["circuit_breaker_tripped_date"] = today_str
    cfg["real_trading_enabled"] = False
    cfg["last_updated"] = datetime.now().isoformat()
    save_security_config(cfg)

    msg = (
        f"🚨 CIRCUIT BREAKER ACTIVADO: Pérdida diaria de '{bot_name}' ({daily_pnl_pct:.2f}%) "
        f"superó el límite de -{max_loss_pct:.2f}%. Candado de trading real cerrado automáticamente."
    )
    logger.critical(msg)
    return True, msg


def validate_order_guardrails(
    symbol: str,
    quantity: float,
    price: float,
    leverage: int = 1,
    use_testnet: bool = True,
    available_balance_usd: Optional[float] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Valida una orden contra los guardarraíles cuantitativos antes de permitir su envío.
    En Testnet se registran advertencias; en Real con candado abierto se bloquean si violan las reglas activas.
    
    Returns:
        (is_allowed, error_message)
    """
    # Si es cuenta real y el candado está cerrado -> BLOQUEO ABSOLUTO
    if not use_testnet and not is_real_trading_enabled():
        return False, (
            "⛔ BLOQUEO DE SEGURIDAD OPERATIVA: La cuenta Real está en MODO SOLO LECTURA. "
            "El candado de trading con dinero real está cerrado."
        )

    cfg = load_security_config()

    # Guardarraíl 1: Apalancamiento Máximo
    if cfg.get("guardrail_max_leverage_enabled", True):
        max_lev = int(cfg.get("max_allowed_leverage", 5))
        if leverage > max_lev:
            msg = f"⛔ GUARDARRAÍL DE RIESGO: Apalancamiento solicitado ({leverage}x) excede el máximo permitido ({max_lev}x)."
            logger.warning(msg)
            return False, msg

    # Guardarraíl 2: Tamaño Nocional Máximo por Orden ($ USD)
    if cfg.get("guardrail_max_order_usd_enabled", True):
        max_usd = float(cfg.get("max_order_notional_usd", 500.0))
        notional_usd = quantity * price
        if notional_usd > max_usd:
            msg = (
                f"⛔ GUARDARRAÍL DE RIESGO: Valor nocional de la orden (${notional_usd:,.2f} USD) "
                f"supera el límite de protección configurado (${max_usd:,.2f} USD)."
            )
            logger.warning(msg)
            return False, msg

    # Guardarraíl 3: Margen Requerido vs Margen Disponible Real en Binance
    # (una orden puede pasar el limite nocional fijo del Guardarraíl 2 y aun asi ser mayor
    # al capital realmente disponible en la cuenta, resultando en rechazo o sobreapalancamiento
    # en el exchange).
    if available_balance_usd is not None and leverage > 0:
        notional_usd = quantity * price
        required_margin_usd = notional_usd / leverage
        if required_margin_usd > available_balance_usd:
            msg = (
                f"⛔ GUARDARRAÍL DE RIESGO: Margen requerido (${required_margin_usd:,.2f} USD a {leverage}x) "
                f"supera el margen disponible real en Binance (${available_balance_usd:,.2f} USD)."
            )
            logger.warning(msg)
            return False, msg

    return True, None


class SecurityManager:
    """Clase administradora de seguridad y guardarraíles para TRADING-QUANT-APP."""
    
    @staticmethod
    def load_config() -> Dict[str, Any]:
        return load_security_config()

    @staticmethod
    def save_config(config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        return save_security_config(config)

    @staticmethod
    def is_real_trading_enabled() -> bool:
        return is_real_trading_enabled()

    @staticmethod
    def set_real_trading_enabled(enabled: bool) -> Tuple[bool, Optional[str]]:
        return set_real_trading_enabled(enabled)

    @staticmethod
    def update_guardrails(
        real_trading_enabled: Optional[bool] = None,
        max_leverage_enabled: Optional[bool] = None,
        max_leverage_limit: Optional[int] = None,
        max_order_usd_enabled: Optional[bool] = None,
        max_order_usd_limit: Optional[float] = None,
        circuit_breaker_enabled: Optional[bool] = None,
        circuit_breaker_loss_pct: Optional[float] = None
    ) -> Tuple[bool, Optional[str]]:
        cfg = load_security_config()
        if real_trading_enabled is not None:
            cfg["real_trading_enabled"] = bool(real_trading_enabled)
        if max_leverage_enabled is not None:
            cfg["guardrail_max_leverage_enabled"] = bool(max_leverage_enabled)
        if max_leverage_limit is not None:
            cfg["max_allowed_leverage"] = int(max_leverage_limit)
        if max_order_usd_enabled is not None:
            cfg["guardrail_max_order_usd_enabled"] = bool(max_order_usd_enabled)
        if max_order_usd_limit is not None:
            cfg["max_order_notional_usd"] = float(max_order_usd_limit)
        if circuit_breaker_enabled is not None:
            cfg["guardrail_circuit_breaker_enabled"] = bool(circuit_breaker_enabled)
        if circuit_breaker_loss_pct is not None:
            cfg["daily_loss_circuit_breaker_pct"] = float(circuit_breaker_loss_pct)
        from datetime import datetime
        cfg["last_updated"] = datetime.now().isoformat()
        return save_security_config(cfg)

    @staticmethod
    def validate_order_guardrails(
        symbol: str,
        notional_usd: float = 0.0,
        leverage: int = 1,
        quantity: float = 1.0,
        price: Optional[float] = None,
        use_testnet: bool = False
    ) -> Tuple[bool, Optional[str]]:
        p = price if price is not None else (notional_usd / quantity if quantity > 0 else 0.0)
        return validate_order_guardrails(
            symbol=symbol,
            quantity=quantity,
            price=p,
            leverage=leverage,
            use_testnet=use_testnet
        )

    @staticmethod
    def get_security_status() -> Dict[str, Any]:
        return load_security_config()

    @staticmethod
    def check_circuit_breaker(daily_pnl_pct: float, bot_name: str = "") -> Tuple[bool, Optional[str]]:
        return check_circuit_breaker(daily_pnl_pct, bot_name=bot_name)
