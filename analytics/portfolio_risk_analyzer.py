import math
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("PortfolioRiskAnalyzer")

# Estimación típica de volatilidad diaria de activos crypto para cálculo paramétrico de VaR
DEFAULT_DAILY_VOLATILITIES = {
    "BTC": 0.035,   # ~3.5% volatilidad diaria
    "ETH": 0.045,   # ~4.5%
    "SOL": 0.060,   # ~6.0%
    "BNB": 0.040,   # ~4.0%
    "XRP": 0.055,   # ~5.5%
    "DOGE": 0.075,  # ~7.5%
    "DEFAULT": 0.050 # 5.0% para otras altcoins
}


class PortfolioRiskAnalyzer:
    """
    Analizador cuantitativo integral de la situación de cartera,
    métricas de riesgo (VaR, CVaR, Stress Testing) y motor de diagnóstico
    cualitativo e interpretación táctica.
    """

    @classmethod
    def analyze_portfolio(cls, account_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ejecuta el pipeline completo de análisis sobre los datos crudos de la cuenta
        (provenientes de Binance Futures Mainnet o Testnet).
        
        Returns:
            Dict con métricas de cartera, análisis de riesgo, simulación de estrés
            y diagnóstico interpretativo en lenguaje natural.
        """
        metrics = cls.compute_portfolio_metrics(account_data)
        var_metrics = cls.compute_var_cvar(
            positions=account_data.get("positions", []),
            total_equity=metrics["total_wallet_usd"]
        )
        stress_test = cls.run_stress_test(
            positions=account_data.get("positions", []),
            total_equity=metrics["total_wallet_usd"]
        )
        interpretation = cls.generate_ai_interpretation(
            metrics=metrics,
            var_metrics=var_metrics,
            stress_results=stress_test
        )

        return {
            "metrics": metrics,
            "var_metrics": var_metrics,
            "stress_test": stress_test,
            "interpretation": interpretation
        }

    @classmethod
    def compute_portfolio_metrics(cls, account_data: Dict[str, Any]) -> Dict[str, Any]:
        """Calcula métricas clave de patrimonio, margen, apalancamiento y distancias de liquidación."""
        total_wallet_usd = float(account_data.get("total_usd_value") or account_data.get("total_wallet_balance", 0.0))
        available_balance_usd = float(account_data.get("available_balance", 0.0))
        unrealized_pnl_usd = float(account_data.get("total_unrealized_pnl", 0.0))
        
        positions = account_data.get("positions", [])
        assets = account_data.get("assets", [])

        # Calcular métricas de posiciones abiertas
        committed_margin_usd = 0.0
        total_notional_usd = 0.0
        long_notional_usd = 0.0
        short_notional_usd = 0.0
        min_liq_distance_pct: Optional[float] = None
        highest_risk_symbol: Optional[str] = None
        highest_risk_liq_price: Optional[float] = None

        position_details = []
        for p in positions:
            amt = float(p.get("positionAmt", 0.0))
            if amt == 0:
                continue

            entry_p = float(p.get("entryPrice", 0.0))
            mark_p = float(p.get("markPrice", 0.0) or entry_p)
            liq_p = float(p.get("liquidationPrice", 0.0) or 0.0)
            upnl = float(p.get("unrealizedProfit", 0.0))
            im = float(p.get("initialMargin", 0.0) or p.get("isolatedMargin", 0.0))
            leverage = int(p.get("leverage", 1))
            sym = p.get("symbol", "")

            notional = abs(amt) * mark_p
            committed_margin_usd += im
            total_notional_usd += notional

            # Distancia a precio de liquidación
            liq_dist_pct = None
            if liq_p > 0 and mark_p > 0:
                if amt > 0:  # LONG: liquidación ocurre si el precio baja
                    liq_dist_pct = ((mark_p - liq_p) / mark_p) * 100.0 if mark_p > liq_p else 0.0
                else:        # SHORT: liquidación ocurre si el precio sube
                    liq_dist_pct = ((liq_p - mark_p) / mark_p) * 100.0 if liq_p > mark_p else 0.0

                if min_liq_distance_pct is None or (liq_dist_pct is not None and liq_dist_pct < min_liq_distance_pct):
                    min_liq_distance_pct = liq_dist_pct
                    highest_risk_symbol = sym
                    highest_risk_liq_price = liq_p

            if amt > 0:
                long_notional_usd += notional
                side_str = "LONG"
            else:
                short_notional_usd += notional
                side_str = "SHORT"

            position_details.append({
                "symbol": sym,
                "side": side_str,
                "amount": amt,
                "notional": notional,
                "entry_price": entry_p,
                "mark_price": mark_p,
                "liq_price": liq_p,
                "liq_distance_pct": liq_dist_pct,
                "margin": im,
                "unrealized_pnl": upnl,
                "leverage": leverage
            })

        # Margen de utilización y apalancamiento efectivo
        margin_utilization_pct = (committed_margin_usd / total_wallet_usd * 100.0) if total_wallet_usd > 0 else 0.0
        effective_leverage = (total_notional_usd / total_wallet_usd) if total_wallet_usd > 0 else 0.0
        net_delta_usd = long_notional_usd - short_notional_usd
        upnl_pct = (unrealized_pnl_usd / total_wallet_usd * 100.0) if total_wallet_usd > 0 else 0.0

        if net_delta_usd > (total_wallet_usd * 0.1):
            net_bias = "LONG"
        elif net_delta_usd < -(total_wallet_usd * 0.1):
            net_bias = "SHORT"
        else:
            net_bias = "NEUTRAL"

        # Distribución de activos en billetera
        asset_alloc = []
        for a in assets:
            wb = float(a.get("usd_value", 0.0) or a.get("wallet_balance", 0.0))
            if wb > 0 and total_wallet_usd > 0:
                asset_alloc.append({
                    "asset": a.get("asset", ""),
                    "usd_value": wb,
                    "pct": (wb / total_wallet_usd) * 100.0
                })

        return {
            "total_wallet_usd": total_wallet_usd,
            "available_balance_usd": available_balance_usd,
            "committed_margin_usd": committed_margin_usd,
            "margin_utilization_pct": margin_utilization_pct,
            "unrealized_pnl_usd": unrealized_pnl_usd,
            "unrealized_pnl_pct": upnl_pct,
            "total_notional_usd": total_notional_usd,
            "effective_leverage": effective_leverage,
            "long_notional_usd": long_notional_usd,
            "short_notional_usd": short_notional_usd,
            "net_delta_usd": net_delta_usd,
            "net_bias": net_bias,
            "positions_count": len(position_details),
            "position_details": position_details,
            "asset_allocation": asset_alloc,
            "min_liq_distance_pct": min_liq_distance_pct,
            "highest_risk_symbol": highest_risk_symbol,
            "highest_risk_liq_price": highest_risk_liq_price
        }

    @classmethod
    def compute_var_cvar(
        cls,
        positions: List[Dict[str, Any]],
        total_equity: float,
        confidence_level: float = 0.95,
        time_horizon_days: int = 1
    ) -> Dict[str, Any]:
        """
        Calcula el Value at Risk (VaR) paramétrico al 95% y 99% a 1 día
        y el Conditional VaR (CVaR / Expected Shortfall).
        """
        if total_equity <= 0 or not positions:
            return {
                "var_95_usd": 0.0,
                "var_95_pct": 0.0,
                "var_99_usd": 0.0,
                "var_99_pct": 0.0,
                "cvar_95_usd": 0.0,
                "cvar_95_pct": 0.0,
                "portfolio_daily_volatility_pct": 0.0,
                "risk_category": "BAJO"
            }

        # Calcular exposición nocional ponderada por volatilidad
        weighted_vol_sum = 0.0
        total_active_notional = 0.0

        for p in positions:
            amt = float(p.get("positionAmt", 0.0))
            if amt == 0:
                continue
            mark_p = float(p.get("markPrice", 0.0) or p.get("entryPrice", 0.0))
            notional = abs(amt) * mark_p
            total_active_notional += notional

            # Extraer moneda base para inferir volatilidad
            sym = p.get("symbol", "").upper()
            base = sym.replace("USDT", "").replace("BUSD", "").replace("USDC", "")
            vol = DEFAULT_DAILY_VOLATILITIES.get(base, DEFAULT_DAILY_VOLATILITIES["DEFAULT"])
            weighted_vol_sum += notional * vol

        if total_active_notional <= 0:
            return {
                "var_95_usd": 0.0,
                "var_95_pct": 0.0,
                "var_99_usd": 0.0,
                "var_99_pct": 0.0,
                "cvar_95_usd": 0.0,
                "cvar_95_pct": 0.0,
                "portfolio_daily_volatility_pct": 0.0,
                "risk_category": "BAJO"
            }

        # Volatilidad diaria agregada de las posiciones abiertas
        daily_vol = weighted_vol_sum / total_active_notional

        # Factores estadísticos estándar (distribución normal)
        # Z(95%) = 1.6449, CVaR(95%) factor = 2.0627
        # Z(99%) = 2.3263
        sqrt_t = math.sqrt(time_horizon_days)
        var_95_usd = total_active_notional * daily_vol * 1.6449 * sqrt_t
        var_99_usd = total_active_notional * daily_vol * 2.3263 * sqrt_t
        cvar_95_usd = total_active_notional * daily_vol * 2.0627 * sqrt_t

        var_95_pct = (var_95_usd / total_equity) * 100.0
        var_99_pct = (var_99_usd / total_equity) * 100.0
        cvar_95_pct = (cvar_95_usd / total_equity) * 100.0

        if var_95_pct > 15.0:
            risk_cat = "ALTO"
        elif var_95_pct > 7.0:
            risk_cat = "MODERADO"
        else:
            risk_cat = "CONTROLADO"

        return {
            "var_95_usd": var_95_usd,
            "var_95_pct": var_95_pct,
            "var_99_usd": var_99_usd,
            "var_99_pct": var_99_pct,
            "cvar_95_usd": cvar_95_usd,
            "cvar_95_pct": cvar_95_pct,
            "portfolio_daily_volatility_pct": daily_vol * 100.0,
            "risk_category": risk_cat
        }

    @classmethod
    def run_stress_test(
        cls,
        positions: List[Dict[str, Any]],
        total_equity: float,
        shocks: Optional[List[float]] = None
    ) -> List[Dict[str, Any]]:
        """
        Simulador de Stress Testing ante variaciones instantáneas del mercado (-20% a +20%).
        Proyecta el impacto directo en PnL y patrimonio.
        """
        if shocks is None:
            shocks = [-0.20, -0.15, -0.10, -0.05, -0.02, 0.0, 0.02, 0.05, 0.10, 0.15, 0.20]

        results = []
        for s in shocks:
            shock_pct = round(s * 100.0, 1)
            pnl_impact = 0.0

            for p in positions:
                amt = float(p.get("positionAmt", 0.0))
                if amt == 0:
                    continue
                mark_p = float(p.get("markPrice", 0.0) or p.get("entryPrice", 0.0))
                notional = abs(amt) * mark_p

                if amt > 0:  # LONG gana si el mercado sube, pierde si baja
                    pnl_impact += notional * s
                else:        # SHORT gana si el mercado baja, pierde si sube
                    pnl_impact += notional * (-s)

            projected_equity = max(0.0, total_equity + pnl_impact)
            return_pct = (pnl_impact / total_equity * 100.0) if total_equity > 0 else 0.0

            # Alerta de liquidación o quiebra en este escenario
            is_liq = (projected_equity <= (total_equity * 0.1)) if total_equity > 0 else False

            results.append({
                "shock_pct": shock_pct,
                "label": f"{'+' if shock_pct > 0 else ''}{shock_pct}%",
                "pnl_impact_usd": pnl_impact,
                "projected_equity": projected_equity,
                "return_pct": return_pct,
                "is_liquidation_risk": is_liq
            })

        return results

    @classmethod
    def generate_ai_interpretation(
        cls,
        metrics: Dict[str, Any],
        var_metrics: Dict[str, Any],
        stress_results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Genera un diagnóstico integral e interpretación táctica cuantitativa
        con Puntuación de Salud de la Cartera (0-100) y pautas accionables.
        """
        total_equity = metrics.get("total_wallet_usd", 0.0)
        eff_leverage = metrics.get("effective_leverage", 0.0)
        margin_util = metrics.get("margin_utilization_pct", 0.0)
        net_delta = metrics.get("net_delta_usd", 0.0)
        net_bias = metrics.get("net_bias", "NEUTRAL")
        pos_count = metrics.get("positions_count", 0)
        min_liq_dist = metrics.get("min_liq_distance_pct")
        highest_risk_sym = metrics.get("highest_risk_symbol")
        upnl_usd = metrics.get("unrealized_pnl_usd", 0.0)
        upnl_pct = metrics.get("unrealized_pnl_pct", 0.0)
        var_95_pct = var_metrics.get("var_95_pct", 0.0)

        # ── 1. Cálculo de Puntuación de Salud (Health Score 0-100) ──
        score = 100.0

        if pos_count == 0:
            health_badge = "🟢 ÓPTIMO / EN LIQUIDEZ"
            health_color = "text-emerald-400"
            health_bg = "bg-emerald-950/60 border-emerald-500/40"
            score = 100.0
        else:
            # Penalizaciones cuantitativas por apalancamiento
            if eff_leverage > 10.0:
                score -= 30.0
            elif eff_leverage > 5.0:
                score -= 18.0
            elif eff_leverage > 2.5:
                score -= 8.0

            # Penalización por utilización de margen
            if margin_util > 80.0:
                score -= 25.0
            elif margin_util > 60.0:
                score -= 15.0
            elif margin_util > 40.0:
                score -= 6.0

            # Penalización por distancia a liquidación
            if min_liq_dist is not None:
                if min_liq_dist < 8.0:
                    score -= 35.0
                elif min_liq_dist < 15.0:
                    score -= 20.0
                elif min_liq_dist < 25.0:
                    score -= 10.0

            # Penalización por pérdidas no realizadas severas
            if upnl_pct < -15.0:
                score -= 20.0
            elif upnl_pct < -7.0:
                score -= 10.0

            score = max(5.0, min(100.0, score))

            if score >= 80.0:
                health_badge = "🟢 EXCELENTE / SALUDABLE"
                health_color = "text-emerald-400"
                health_bg = "bg-emerald-950/60 border-emerald-500/40"
            elif score >= 60.0:
                health_badge = "🟡 MODERADO / ESTABLE"
                health_color = "text-yellow-400"
                health_bg = "bg-yellow-950/60 border-yellow-500/40"
            elif score >= 40.0:
                health_badge = "🟠 PRECAUCIÓN / RIESGO ELEVADO"
                health_color = "text-orange-400"
                health_bg = "bg-orange-950/60 border-orange-500/40"
            else:
                health_badge = "🔴 CRÍTICO / ALTO RIESGO"
                health_color = "text-red-400"
                health_bg = "bg-red-950/60 border-red-500/40"

        # ── 2. Diagnóstico de Postura de Mercado ──
        if pos_count == 0:
            posture_text = "Cartera 100% en liquidez (Efectivo/USDT). Sin exposición a la volatilidad del mercado."
        elif net_bias == "LONG":
            posture_text = f"Sesgo Alcista (Net Long). Exposición delta positiva de +${abs(net_delta):,.2f} USD. La cartera se beneficia directamente de alzas en el mercado."
        elif net_bias == "SHORT":
            posture_text = f"Sesgo Bajista (Net Short / Cobertura). Exposición delta negativa de -${abs(net_delta):,.2f} USD. La cartera actúa como cobertura o trade direccional a la baja."
        else:
            posture_text = f"Postura Neutral / Delta Hedged. Exposición balanceada entre largos y cortos (Delta neto cercano a $0 USD)."

        # ── 3. Diagnóstico de Margen y Apalancamiento ──
        if pos_count == 0:
            margin_text = f"Margen libre al 100% (${total_equity:,.2f} USD disponibles para nuevas estrategias)."
        else:
            margin_text = (
                f"Apalancamiento efectivo de {eff_leverage:.2f}x con una utilización de margen del {margin_util:.1f}%. "
                + ("Apalancamiento moderado y seguro (<3x)." if eff_leverage <= 3.0 else "⚠️ Apalancamiento agresivo (>3x), mayor sensibilidad a fluctuaciones de corto plazo.")
            )

        # ── 4. Diagnóstico de Seguridad de Liquidación ──
        if pos_count == 0 or min_liq_dist is None:
            liq_text = "Sin riesgo de liquidación en la cuenta."
        elif min_liq_dist < 12.0:
            liq_text = f"🚨 ALERTA CRÍTICA: El contrato {highest_risk_sym} está a tan solo {min_liq_dist:.1f}% de su precio de liquidación (${metrics.get('highest_risk_liq_price', 0):,.2f})."
        elif min_liq_dist < 25.0:
            liq_text = f"⚠️ Buffer de liquidación moderado: {highest_risk_sym} soporta hasta un {min_liq_dist:.1f}% de movimiento adverso antes de liquidación."
        else:
            liq_text = f"🛡️ Margen de seguridad amplio: La posición más cercana ({highest_risk_sym}) tiene un buffer de protección del {min_liq_dist:.1f}% frente al precio de liquidación."

        # ── 5. Recomendaciones Accionables Cuantitativas ──
        recommendations = []
        if pos_count == 0:
            recommendations.append("💼 La cuenta dispone de capital libre para iniciar bots automatizados o asignar a nuevas estrategias cuantitativas.")
            recommendations.append("📊 Verifica que el modo Multi-Assets esté activado si planeas utilizar colateral en BTC.")
        else:
            if min_liq_dist is not None and min_liq_dist < 15.0:
                recommendations.append(f"⚠️ URGENTE: Establecer o ajustar Stop Loss en {highest_risk_sym} para prevenir liquidación forzada.")
            if eff_leverage > 4.0:
                recommendations.append("📉 Reducir el tamaño nominal de contratos para situar el apalancamiento efectivo por debajo de 3x.")
            if margin_util > 70.0:
                recommendations.append("💵 Mantener al menos un 30% de margen libre no comprometido para absorber picos de volatilidad repentinos.")
            if var_95_pct > 10.0:
                recommendations.append(f"🛡️ El VaR diario al 95% es de {var_95_pct:.1f}% (${var_metrics.get('var_95_usd', 0):,.2f} USD). Considerar reducir exposición si excede la tolerancia al riesgo.")
            if len(recommendations) < 3:
                recommendations.append("✅ Los parámetros cuantitativos de la cartera se encuentran dentro de los rangos de riesgo establecidos.")
                recommendations.append("📈 Monitorear la correlación entre pares si se agregan más posiciones simultáneas.")

        return {
            "health_score": round(score, 1),
            "health_badge": health_badge,
            "health_color": health_color,
            "health_bg": health_bg,
            "summary": f"Estado de cartera {health_badge.split('/')[0].strip()} (Score: {score:.0f}/100). {posture_text}",
            "market_posture": posture_text,
            "margin_and_leverage": margin_text,
            "liquidation_safety": liq_text,
            "recommendations": recommendations
        }
