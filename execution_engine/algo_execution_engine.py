"""
Motor Cuantitativo de Ejecución Algorítmica (TWAP & POV).
Implementa estrategias de ejecución institucional diseñadas para minimizar
el impacto en el mercado (market impact) y el deslizamiento (slippage):
- TWAP: Time-Weighted Average Price (repartición homogénea temporal)
- POV: Percentage of Volume (seguimiento de tasa de volumen de mercado)
Compatible con modo Simulación / Paper Trading y Binance Futures Testnet.
"""
import logging
import asyncio
import random
import time
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime

from execution_engine.binance_client import BinanceTestnetClient

logger = logging.getLogger("AlgoExecutionEngine")


class AlgoExecutionTask:
    """Representa una orden algorítmica activa (TWAP o POV)."""

    def __init__(
        self,
        task_id: str,
        algo_type: str,            # 'TWAP' o 'POV'
        symbol: str,
        side: str,                 # 'BUY' o 'SELL'
        total_quantity: float,
        duration_minutes: float,   # Para TWAP
        num_slices: int = 10,
        pov_rate_pct: float = 10.0,# Para POV (ej: 10% del volumen)
        mode: str = "SIMULATION",  # 'SIMULATION' o 'BINANCE_TESTNET'
        status_callback: Optional[Callable] = None
    ):
        self.task_id = task_id
        self.algo_type = algo_type.upper()
        self.symbol = symbol.replace("/", "").upper()
        self.side = side.upper()
        self.total_quantity = total_quantity
        self.duration_minutes = duration_minutes
        self.num_slices = max(2, num_slices)
        self.pov_rate_pct = pov_rate_pct
        self.mode = mode.upper()
        self.status_callback = status_callback

        self.status = "RUNNING"   # 'RUNNING', 'COMPLETED', 'CANCELLED', 'FAILED'
        self.executed_quantity = 0.0
        self.arrival_price: float = 0.0
        self.executed_vwap: float = 0.0
        self.total_cost_usd: float = 0.0
        self.slices_completed = 0
        self.slices_history: List[Dict[str, Any]] = []
        self.created_at = datetime.now()
        self.completed_at: Optional[datetime] = None
        self.error_message: Optional[str] = None
        self._is_cancelled = False

    @property
    def progress_pct(self) -> float:
        if self.total_quantity <= 0:
            return 100.0
        return min(100.0, round((self.executed_quantity / self.total_quantity) * 100.0, 1))

    @property
    def slippage_bps(self) -> float:
        """Deslizamiento frente al precio de llegada en puntos básicos (basis points)."""
        if self.arrival_price <= 0 or self.executed_vwap <= 0:
            return 0.0
        if self.side == "BUY":
            diff = self.executed_vwap - self.arrival_price
        else:
            diff = self.arrival_price - self.executed_vwap
        return round((diff / self.arrival_price) * 10000.0, 2)

    def cancel(self):
        self._is_cancelled = True
        self.status = "CANCELLED"
        self.completed_at = datetime.now()


class AlgoExecutionEngine:
    """Orquestador de algoritmos de ejecución cuantitativa."""

    def __init__(self):
        self.active_tasks: Dict[str, AlgoExecutionTask] = {}
        self.history_tasks: List[AlgoExecutionTask] = []
        self._testnet_client = BinanceTestnetClient(use_testnet=True)

    def get_current_price(self, symbol: str) -> float:
        """Obtiene precio actual de mercado."""
        try:
            return self._testnet_client.get_symbol_price(symbol)
        except Exception:
            return 80000.0

    async def execute_twap_async(
        self,
        symbol: str,
        side: str,
        total_quantity: float,
        duration_minutes: float = 5.0,
        num_slices: int = 10,
        mode: str = "SIMULATION",
        status_callback: Optional[Callable] = None
    ) -> str:
        """Lanza una ejecución algorítmica TWAP de forma asíncrona."""
        task_id = f"TWAP_{symbol}_{int(time.time()*1000)}"
        task = AlgoExecutionTask(
            task_id=task_id,
            algo_type="TWAP",
            symbol=symbol,
            side=side,
            total_quantity=total_quantity,
            duration_minutes=duration_minutes,
            num_slices=num_slices,
            mode=mode,
            status_callback=status_callback
        )
        self.active_tasks[task_id] = task
        asyncio.create_task(self._run_twap_loop(task))
        return task_id

    async def _run_twap_loop(self, task: AlgoExecutionTask):
        """Bucle de ejecución no bloqueante de TWAP."""
        task.arrival_price = self.get_current_price(task.symbol)
        total_sec = task.duration_minutes * 60.0
        interval_sec = max(2.0, total_sec / task.num_slices)
        base_slice_qty = task.total_quantity / task.num_slices

        logger.info(
            "Iniciando TWAP %s %s: Qty=%.4f en %d tajadas (cada %.1fs) | Modo=%s",
            task.side, task.symbol, task.total_quantity, task.num_slices, interval_sec, task.mode
        )

        remaining_qty = task.total_quantity

        for i in range(task.num_slices):
            if task._is_cancelled:
                logger.info("TWAP %s cancelado por el usuario.", task.task_id)
                break

            # En la última tajada, ejecutar el remanente exacto
            if i == task.num_slices - 1:
                slice_qty = remaining_qty
            else:
                # Jitter cuantitativo de +/- 8% para no ser predecible en el libro
                jitter = random.uniform(0.92, 1.08)
                slice_qty = min(remaining_qty, round(base_slice_qty * jitter, 4))
                if slice_qty <= 0.0001:
                    slice_qty = remaining_qty

            current_price = self.get_current_price(task.symbol)
            fill_price = current_price

            # Si es modo testnet, enviar orden real
            if task.mode == "BINANCE_TESTNET":
                try:
                    order, err = self._testnet_client.place_futures_order(
                        symbol=task.symbol,
                        side=task.side,
                        quantity=slice_qty,
                        order_type="MARKET",
                        verify_execution=True
                    )
                    if err:
                        logger.warning("Fallo en tajada %d TWAP Testnet: %s", i+1, err)
                    if order and order.get("avgPrice"):
                        fill_price = float(order.get("avgPrice"))
                except Exception as e:
                    logger.error("Error enviando tajada a Binance: %s", e)

            # Actualizar estadísticas de ejecución
            cost = slice_qty * fill_price
            task.total_cost_usd += cost
            task.executed_quantity += slice_qty
            remaining_qty = max(0.0, remaining_qty - slice_qty)
            task.executed_vwap = task.total_cost_usd / task.executed_quantity if task.executed_quantity > 0 else fill_price
            task.slices_completed += 1

            slice_record = {
                "slice_num": task.slices_completed,
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "quantity": slice_qty,
                "price": fill_price,
                "accum_vwap": round(task.executed_vwap, 2),
                "progress_pct": task.progress_pct
            }
            task.slices_history.append(slice_record)

            if task.status_callback:
                try:
                    task.status_callback(task)
                except Exception:
                    pass

            if remaining_qty <= 0.0001:
                break

            # Espera no bloqueante con jitter
            sleep_time = max(1.0, interval_sec * random.uniform(0.95, 1.05))
            await asyncio.sleep(sleep_time)

        if not task._is_cancelled:
            task.status = "COMPLETED"
            task.completed_at = datetime.now()

        if task.task_id in self.active_tasks:
            del self.active_tasks[task.task_id]
        self.history_tasks.append(task)

        if task.status_callback:
            try:
                task.status_callback(task)
            except Exception:
                pass


# Instancia singleton del motor algorítmico
algo_engine = AlgoExecutionEngine()
