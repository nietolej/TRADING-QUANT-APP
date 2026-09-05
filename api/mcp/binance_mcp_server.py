"""
Servidor MCP (Model Context Protocol) para Binance en TRADING-QUANT-APP.
Permite a agentes de IA y al entorno de desarrollo interactuar con Binance
(Futures Testnet y Real Mainnet) mediante herramientas estandarizadas.
"""

import sys
import os
import json
import logging
from typing import Optional, Dict, Any

# Asegurar que el directorio raíz del proyecto esté en el PYTHONPATH
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT_DIR, ".env"))

from execution_engine.binance_client import (
    BinanceTestnetClient,
    get_binance_credentials,
    verify_binance_credentials,
)
from analytics.portfolio_risk_analyzer import PortfolioRiskAnalyzer
from mcp.server.mcpserver import MCPServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("BinanceMCPServer")

# Instanciación del servidor MCP
mcp = MCPServer(name="binance-trading-quant")


# ─────────────────────────────────────────────────────────────────────────────
# HERRAMIENTAS MCP
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def binance_test_connection(use_testnet: bool = True) -> str:
    """
    Diagnóstico de conexión con Binance (Futures Testnet o Real Mainnet).
    Mide latencia en ms, ping, acceso a la cuenta y permisos de trading.
    Por defecto usa testnet=True para seguridad.
    """
    network_name = "Binance Futures Testnet (Demo)" if use_testnet else "Binance Real (Mainnet)"
    try:
        res = verify_binance_credentials(use_testnet=use_testnet)
        if res.get("success"):
            return json.dumps({
                "status": "CONNECTED",
                "network": network_name,
                "latency_ms": res.get("latency_ms", 0),
                "wallet_balance_usdt": res.get("wallet_balance", 0.0),
                "available_balance_usdt": res.get("available_balance", 0.0),
                "can_trade": res.get("can_trade", True),
                "message": f"Conexión exitosa con {network_name} | Latencia: {res.get('latency_ms')} ms"
            }, indent=2)
        else:
            return json.dumps({
                "status": "ERROR",
                "network": network_name,
                "error": res.get("error", "Error desconocido"),
                "message": f"Fallo al conectar con {network_name}: {res.get('error')}"
            }, indent=2)
    except Exception as e:
        return json.dumps({
            "status": "EXCEPTION",
            "network": network_name,
            "error": str(e)
        }, indent=2)


@mcp.tool()
def binance_get_account_balance(use_testnet: bool = True) -> str:
    """
    Obtiene el balance total en USD, margen disponible, saldo USDT y lista de
    activos con saldo en la cuenta de Binance Futures.
    Por defecto usa testnet=True para seguridad.
    """
    client = BinanceTestnetClient(use_testnet=use_testnet)
    try:
        acc_info = client.get_full_account_info(use_testnet=use_testnet)
        if not acc_info.get("success"):
            return json.dumps({
                "success": False,
                "error": acc_info.get("error", "No se pudo obtener información de cuenta")
            }, indent=2)

        assets_clean = []
        for a in acc_info.get("assets", []):
            if float(a.get("wallet_balance", 0)) > 0 or float(a.get("available_balance", 0)) > 0:
                assets_clean.append({
                    "asset": a.get("asset"),
                    "wallet_balance": a.get("wallet_balance"),
                    "available_balance": a.get("available_balance"),
                    "usd_value": round(float(a.get("usd_value", 0)), 2),
                    "unrealized_pnl": a.get("unrealized_pnl")
                })

        return json.dumps({
            "success": True,
            "network": acc_info.get("network"),
            "total_wallet_balance_usd": round(float(acc_info.get("total_wallet_balance", 0)), 2),
            "available_balance_usd": round(float(acc_info.get("available_balance", 0)), 2),
            "total_unrealized_pnl_usd": round(float(acc_info.get("total_unrealized_pnl", 0)), 2),
            "multi_assets_margin": acc_info.get("multi_assets_margin", False),
            "assets": assets_clean
        }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@mcp.tool()
def binance_get_positions(use_testnet: bool = True) -> str:
    """
    Obtiene las posiciones abiertas en Binance Futures con tamaño, precio de entrada,
    precio de liquidación, distancia de seguridad porcentual a liquidación y PnL no realizado.
    Por defecto usa testnet=True para seguridad.
    """
    client = BinanceTestnetClient(use_testnet=use_testnet)
    try:
        acc_info = client.get_full_account_info(use_testnet=use_testnet)
        if not acc_info.get("success"):
            return json.dumps({"success": False, "error": acc_info.get("error")}, indent=2)

        risk_data = PortfolioRiskAnalyzer.analyze_portfolio(acc_info)
        metrics = risk_data.get("metrics", {})
        pos_details = metrics.get("position_details", [])

        return json.dumps({
            "success": True,
            "network": acc_info.get("network"),
            "open_positions_count": len(pos_details),
            "positions": pos_details,
            "total_notional_usd": round(float(metrics.get("total_notional_usd", 0)), 2),
            "effective_leverage": round(float(metrics.get("effective_leverage", 0)), 2),
            "min_liq_distance_pct": metrics.get("min_liq_distance_pct")
        }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@mcp.tool()
def binance_get_open_orders(symbol: str = "BTCUSDT", use_testnet: bool = True) -> str:
    """
    Lista todas las órdenes pendientes (Limit, Stop, Take-Profit) en Binance Futures.
    symbol: Par de trading (ej. 'BTCUSDT').
    use_testnet: True para Testnet, False para Real.
    """
    client = BinanceTestnetClient(use_testnet=use_testnet)
    try:
        acc_info = client.get_full_account_info(use_testnet=use_testnet)
        if not acc_info.get("success"):
            return json.dumps({"success": False, "error": acc_info.get("error")}, indent=2)

        sym_clean = symbol.replace("/", "").upper()
        orders = acc_info.get("open_orders", [])
        if sym_clean:
            orders = [o for o in orders if o.get("symbol", "").upper() == sym_clean]

        return json.dumps({
            "success": True,
            "network": acc_info.get("network"),
            "symbol": sym_clean,
            "open_orders_count": len(orders),
            "orders": orders
        }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@mcp.tool()
def binance_get_market_price(symbol: str = "BTCUSDT", use_testnet: bool = True) -> str:
    """
    Obtiene el precio de mercado actual, precio de marca y tasa de financiación
    (funding rate) de un símbolo en Binance Futures.
    """
    client = BinanceTestnetClient(use_testnet=use_testnet)
    sym_clean = symbol.replace("/", "").upper()
    try:
        ticker = client.client.futures_symbol_ticker(symbol=sym_clean)
        mark_price_info = client.client.futures_mark_price(symbol=sym_clean)
        
        return json.dumps({
            "success": True,
            "symbol": sym_clean,
            "price": float(ticker.get("price", 0)),
            "mark_price": float(mark_price_info.get("markPrice", 0)),
            "index_price": float(mark_price_info.get("indexPrice", 0)),
            "last_funding_rate": float(mark_price_info.get("lastFundingRate", 0)),
            "next_funding_time": mark_price_info.get("nextFundingTime")
        }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "symbol": sym_clean, "error": str(e)}, indent=2)


@mcp.tool()
def binance_analyze_portfolio_risk(use_testnet: bool = True) -> str:
    """
    Ejecuta el análisis cuantitativo completo de riesgo de la cartera:
    Value at Risk (VaR 95% 1D), Conditional VaR (CVaR), simulación de estrés ante
    caídas/subidas del mercado (-20% a +20%), puntuación de salud y recomendaciones.
    """
    client = BinanceTestnetClient(use_testnet=use_testnet)
    try:
        acc_info = client.get_full_account_info(use_testnet=use_testnet)
        if not acc_info.get("success"):
            return json.dumps({"success": False, "error": acc_info.get("error")}, indent=2)

        risk_analysis = PortfolioRiskAnalyzer.analyze_portfolio(acc_info)
        return json.dumps({
            "success": True,
            "network": acc_info.get("network"),
            "health_score": risk_analysis.get("interpretation", {}).get("health_score"),
            "health_badge": risk_analysis.get("interpretation", {}).get("health_badge"),
            "market_posture": risk_analysis.get("interpretation", {}).get("market_posture"),
            "margin_and_leverage": risk_analysis.get("interpretation", {}).get("margin_and_leverage"),
            "liquidation_safety": risk_analysis.get("interpretation", {}).get("liquidation_safety"),
            "var_95_usd": risk_analysis.get("var_metrics", {}).get("var_95_usd"),
            "var_95_pct": risk_analysis.get("var_metrics", {}).get("var_95_pct"),
            "cvar_95_pct": risk_analysis.get("var_metrics", {}).get("cvar_95_pct"),
            "recommendations": risk_analysis.get("interpretation", {}).get("recommendations", []),
            "stress_test": risk_analysis.get("stress_test", [])
        }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@mcp.tool()
def binance_place_futures_order(
    symbol: str,
    side: str,
    quantity: float,
    order_type: str = "MARKET",
    price: float = 0.0,
    use_testnet: bool = True
) -> str:
    """
    Coloca una orden de compra o venta en Binance Futures (Testnet por defecto).
    symbol: Par (ej. 'BTCUSDT').
    side: 'BUY' o 'SELL' (o 'long' / 'short').
    quantity: Cantidad en contratos (ej. 0.002).
    order_type: 'MARKET' o 'LIMIT'.
    price: Precio límite si order_type es 'LIMIT'.
    use_testnet: Por defecto True (Demo). Solo poner False si se autoriza explícitamente en Real.
    """
    client = BinanceTestnetClient(use_testnet=use_testnet)
    target_price = price if (order_type.upper() == "LIMIT" and price > 0) else None
    
    try:
        order, err = client.place_futures_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            price=target_price,
            verify_execution=True
        )
        if order and not err:
            return json.dumps({
                "success": True,
                "network": "Testnet" if use_testnet else "Real",
                "orderId": order.get("orderId"),
                "symbol": order.get("symbol"),
                "status": order.get("status"),
                "side": order.get("side"),
                "origQty": order.get("origQty"),
                "avgPrice": order.get("avgPrice")
            }, indent=2)
        else:
            return json.dumps({
                "success": False,
                "network": "Testnet" if use_testnet else "Real",
                "error": err or "Orden no ejecutada"
            }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)


@mcp.tool()
def binance_cancel_all_orders(symbol: str = "BTCUSDT", use_testnet: bool = True) -> str:
    """
    Cancela todas las órdenes abiertas pendientes de un símbolo en Binance Futures.
    symbol: Par a cancelar (ej. 'BTCUSDT').
    """
    client = BinanceTestnetClient(use_testnet=use_testnet)
    try:
        sym_clean = symbol.replace("/", "").upper()
        ok, err = client.cancel_all_futures_orders(symbol=sym_clean, use_testnet=use_testnet)
        return json.dumps({
            "success": ok,
            "symbol": sym_clean,
            "network": "Testnet" if use_testnet else "Real",
            "message": f"Órdenes canceladas para {sym_clean}" if ok else f"Fallo al cancelar: {err}"
        }, indent=2)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRYPOINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Iniciando Binance MCP Server sobre stdio transport...")
    mcp.run(transport="stdio")
