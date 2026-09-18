"""
Gestión centralizada de API Keys de proveedores de datos (CryptoQuant, Glassnode,
CoinGecko, Etherscan, etc.), con el mismo patrón de "Blind Key" que
execution_engine.binance_client usa para las credenciales de Binance: la clave se
guarda en el archivo .env (nunca en la BD ni en el DOM del navegador) y solo se
expone al frontend enmascarada.

Además de los proveedores conocidos y ya integrados en el código (KNOWN_PROVIDERS),
permite dar de alta "conexiones personalizadas" (nombre + API Key) para futuros
proveedores que aún no tienen un cliente dedicado en data_layer/data_sources/. El
registro de esas conexiones (solo nombre y variable de entorno, jamás el valor de
la clave) se guarda en data/custom_api_connections.json; el valor real siempre vive
únicamente en .env.
"""
import os
import json
import re
from typing import Any, Dict, List, Optional, Tuple

import dotenv

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENV_PATH = os.path.join(_ROOT_DIR, ".env")
_CUSTOM_REGISTRY_PATH = os.path.join(_ROOT_DIR, "data", "custom_api_connections.json")

dotenv.load_dotenv(_ENV_PATH)

# Proveedores de datos on-chain/mercado ya soportados por el código
# (data_layer/data_sources/*.py). `test` es opcional: una función sin argumentos que
# hace una llamada mínima real a la API para verificar que la key funciona.
KNOWN_PROVIDERS: List[Dict[str, str]] = [
    {
        "id": "cryptoquant",
        "label": "CryptoQuant",
        "env_var": "CRYPTOQUANT_API_KEY",
        "help": "Métricas on-chain de BTC/ETH: MVRV, SOPR, Puell Multiple, exchange flows, etc.",
        "signup_url": "https://cryptoquant.com/",
    },
    {
        "id": "glassnode",
        "label": "Glassnode",
        "env_var": "GLASSNODE_API_KEY",
        "help": "Alternativa de pago a CryptoQuant para las mismas métricas on-chain (requiere plan Advanced).",
        "signup_url": "https://glassnode.com/",
    },
    {
        "id": "coingecko",
        "label": "CoinGecko",
        "env_var": "COINGECKO_API_KEY",
        "help": "Precios, market cap y volumen. Requiere una Demo API Key gratuita.",
        "signup_url": "https://www.coingecko.com/en/api/pricing",
    },
    {
        "id": "etherscan",
        "label": "Etherscan",
        "env_var": "ETHERSCAN_API_KEY",
        "help": "Mint/Burn/Exchange flows on-chain de USDT/USDC (contratos ERC-20).",
        "signup_url": "https://etherscan.io/apis",
    },
    {
        "id": "tronscan",
        "label": "Tronscan",
        "env_var": "TRONSCAN_API_KEY",
        "help": "Mint/Burn/Exchange flows on-chain de USDT en la red TRC-20 (Tron).",
        "signup_url": "https://tronscan.org/#/tools/api",
    },
]


def _is_placeholder(value: str) -> bool:
    """Todos los placeholders de .env.example en este proyecto siguen el patrón
    'tu_..._aqui' (español); cualquier valor con esa forma no es una key real."""
    v = value.strip().lower()
    return v == "" or (v.startswith("tu_") and v.endswith("aqui")) or v == "your_api_key_here"

_ENV_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _load_custom_registry() -> List[Dict[str, str]]:
    if not os.path.exists(_CUSTOM_REGISTRY_PATH):
        return []
    try:
        with open(_CUSTOM_REGISTRY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_custom_registry(entries: List[Dict[str, str]]) -> None:
    os.makedirs(os.path.dirname(_CUSTOM_REGISTRY_PATH), exist_ok=True)
    with open(_CUSTOM_REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def _slug_to_env_var(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name.strip()).strip("_").upper()
    return f"CUSTOM_{slug}_API_KEY" if slug else "CUSTOM_API_KEY"


def list_providers() -> List[Dict[str, Any]]:
    """Devuelve el estado actual (enmascarado) de todos los proveedores: los ya
    integrados en el código (KNOWN_PROVIDERS) y las conexiones personalizadas que el
    usuario haya dado de alta."""
    providers = []
    for p in KNOWN_PROVIDERS:
        raw = os.getenv(p["env_var"], "").strip()
        has_key = not _is_placeholder(raw)
        providers.append({
            **p,
            "custom": False,
            "has_key": has_key,
            "masked_key": _mask(raw) if has_key else "",
        })

    for c in _load_custom_registry():
        raw = os.getenv(c["env_var"], "").strip()
        has_key = not _is_placeholder(raw)
        providers.append({
            "id": c["env_var"],
            "label": c["name"],
            "env_var": c["env_var"],
            "help": "Conexión personalizada.",
            "signup_url": "",
            "custom": True,
            "has_key": has_key,
            "masked_key": _mask(raw) if has_key else "",
        })

    return providers


def save_provider_key(env_var: str, api_key: str) -> Tuple[bool, Optional[str]]:
    """Guarda (o actualiza) la API Key de un proveedor conocido o personalizado en .env."""
    if not _ENV_VAR_RE.match(env_var):
        return False, f"Nombre de variable de entorno inválido: {env_var}"

    key = api_key.strip()
    if not key:
        return False, "La API Key no puede estar vacía."

    try:
        if not os.path.exists(_ENV_PATH):
            with open(_ENV_PATH, "w", encoding="utf-8") as f:
                f.write("# Archivo de entorno\n")
        dotenv.set_key(_ENV_PATH, env_var, key)
        os.environ[env_var] = key
        return True, None
    except Exception as e:
        return False, f"Error al guardar la API Key en .env: {e}"


def create_custom_connection(name: str, api_key: str) -> Tuple[bool, Optional[str]]:
    """Da de alta una nueva conexión personalizada: registra {name, env_var} en
    data/custom_api_connections.json y guarda la key real solo en .env."""
    name = name.strip()
    if not name:
        return False, "El nombre de la conexión no puede estar vacío."

    known_env_vars = {p["env_var"] for p in KNOWN_PROVIDERS}
    registry = _load_custom_registry()
    existing = next((c for c in registry if c["name"].lower() == name.lower()), None)
    env_var = existing["env_var"] if existing else _slug_to_env_var(name)

    if env_var in known_env_vars:
        return False, f"'{name}' coincide con un proveedor ya soportado; usa su formulario dedicado."

    ok, err = save_provider_key(env_var, api_key)
    if not ok:
        return False, err

    if not existing:
        registry.append({"name": name, "env_var": env_var})
        _save_custom_registry(registry)

    return True, None


def delete_custom_connection(env_var: str) -> Tuple[bool, Optional[str]]:
    """Elimina una conexión personalizada del registro y borra su key de .env."""
    registry = _load_custom_registry()
    remaining = [c for c in registry if c["env_var"] != env_var]
    if len(remaining) == len(registry):
        return False, "Conexión personalizada no encontrada."

    try:
        dotenv.unset_key(_ENV_PATH, env_var)
        os.environ.pop(env_var, None)
        _save_custom_registry(remaining)
        return True, None
    except Exception as e:
        return False, f"Error al eliminar la conexión: {e}"
