import pandas as pd
from datetime import datetime, timezone
import yaml
from collections import deque
import time

from .binance_client import BinanceTestnetClient
from strategy_engine.base_strategy import BaseStrategy
from strategy_engine.conditions import ConditionEvaluator
from strategy_engine.risk_management import RiskManager
from notifications.telegram_bot import TelegramNotifier

class Position:
    def __init__(self, side: str, entry_price: float, quantity: float, timestamp: int):
        self.side = side  # 'long' or 'short'
        self.entry_price = entry_price
        self.quantity = quantity
        self.entry_timestamp = timestamp
        self.sl_price = None
        self.tp_price = None

class PaperTrader:
    def __init__(self, strategy_yaml_path: str, initial_balance: float = 10000.0, update_callback=None):
        self.strategy = BaseStrategy(strategy_yaml_path)
        self.client = BinanceTestnetClient()
        self.telegram = TelegramNotifier()
        self.update_callback = update_callback
        
        self.symbol = self.strategy.symbol
        self.timeframe = self.strategy.timeframe
        
        self.initial_balance = initial_balance
        self.current_balance = initial_balance
        
        self.position = None
        self.trade_history = []
        
        # Guardaremos un historial limitado de velas para cálculos
        self.klines_df = pd.DataFrame()
        
        # Risk Manager
        self.risk_manager = self.strategy.risk_manager
        
        self.is_running = False

    def start(self):
        self.is_running = True
        self._notify(f"Iniciando Paper Trading para {self.symbol} en {self.timeframe}. Saldo Inicial: {self.current_balance}")
        
        # 1. Warm-up: Descargar últimas 200 velas
        self._notify("Descargando histórico para calentar indicadores...")
        raw_klines = self.client.get_historical_klines(self.symbol, self.timeframe, "300 candles ago UTC")
        
        records = []
        for k in raw_klines:
            records.append({
                'timestamp': pd.to_datetime(k[0], unit='ms', utc=True),
                'open': float(k[1]),
                'high': float(k[2]),
                'low': float(k[3]),
                'close': float(k[4]),
                'volume': float(k[5])
            })
            
        self.klines_df = pd.DataFrame(records)
        self.klines_df.set_index('timestamp', inplace=True)
        self._notify("Histórico descargado. Escuchando mercado en vivo...")
        
        # 2. Conectar WebSocket
        self.stream_name = self.client.subscribe_to_klines(self.symbol, self.timeframe, self._on_new_kline)

    def stop(self):
        self.is_running = False
        self.client.stop()
        self._notify(f"Paper Trading detenido. Saldo Final: {self.current_balance:.2f}")

    def _on_new_kline(self, kline_data):
        if not self.is_running:
            return
            
        # Añadir nueva vela al DF
        ts = pd.to_datetime(kline_data['timestamp'], unit='ms', utc=True)
        new_row = pd.DataFrame([{
            'open': kline_data['open'],
            'high': kline_data['high'],
            'low': kline_data['low'],
            'close': kline_data['close'],
            'volume': kline_data['volume']
        }], index=[ts])
        
        self.klines_df = pd.concat([self.klines_df, new_row])
        # Mantener solo las últimas 300 velas por memoria
        if len(self.klines_df) > 300:
            self.klines_df = self.klines_df.iloc[-300:]
            
        # Evaluar lógica
        self._evaluate_market()

    def _evaluate_market(self):
        # Evaluar riesgo (SL/TP) si hay posición abierta
        current_price = self.klines_df.iloc[-1]['close']
        current_ts = self.klines_df.index[-1]
        
        if self.position:
            action = self.risk_manager.evaluate_risk(self.position, current_price, self.klines_df)
            
            # Chequear también exit_conditions
            evaluator = ConditionEvaluator(self.klines_df, pd.DataFrame()) # Sin onchain temporalmente para testnet rápida
            exit_signal = evaluator.evaluate(self.strategy.config.get("exit_conditions", {}))
            
            if action == 'SL' or action == 'TP' or (exit_signal.iloc[-1] if not exit_signal.empty else False):
                reason = action if action else 'EXIT_SIGNAL'
                self._close_position(current_price, current_ts, reason)
                return
                
        # Evaluar condiciones de entrada si no hay posición
        if not self.position:
            evaluator = ConditionEvaluator(self.klines_df, pd.DataFrame())
            entry_signal = evaluator.evaluate(self.strategy.config.get("entry_conditions", {}))
            
            if entry_signal.iloc[-1] if not entry_signal.empty else False:
                # Determinar side (para spot siempre es long)
                self._open_position('long', current_price, current_ts)

    def _open_position(self, side: str, price: float, ts):
        # Risk sizing
        qty = self.risk_manager.calculate_position_size(self.current_balance, price)
        self.position = Position(side, price, qty, ts)
        
        # Calcular SL y TP iniciales
        self.position.sl_price = self.risk_manager.calculate_sl(self.position, self.klines_df)
        self.position.tp_price = self.risk_manager.calculate_tp(self.position, self.klines_df)
        
        msg = f"🟢 OPEN {side.upper()} at {price}. SL: {self.position.sl_price}, TP: {self.position.tp_price}"
        self._notify(msg)

    def _close_position(self, price: float, ts, reason: str):
        # Calcular PNL
        pnl = 0
        if self.position.side == 'long':
            pnl = (price - self.position.entry_price) * self.position.quantity
        else:
            pnl = (self.position.entry_price - price) * self.position.quantity
            
        # Comisiones (estimado 0.1% * 2)
        fee = (self.position.quantity * price) * 0.002
        net_pnl = pnl - fee
        
        self.current_balance += net_pnl
        
        trade = {
            'entry_time': self.position.entry_timestamp,
            'exit_time': ts,
            'side': self.position.side,
            'entry_price': self.position.entry_price,
            'exit_price': price,
            'pnl': net_pnl,
            'reason': reason
        }
        self.trade_history.append(trade)
        
        msg = f"🔴 CLOSE {self.position.side.upper()} at {price}. Reason: {reason}. PNL: {net_pnl:.2f}. Saldo: {self.current_balance:.2f}"
        self._notify(msg)
        self.position = None

    def _notify(self, message: str):
        print(f"[PaperTrader] {message}")
        self.telegram.send_message(f"<b>[PaperTrader]</b>\n{message}")
        if self.update_callback:
            # Notificamos a la UI
            state = {
                'message': message,
                'balance': self.current_balance,
                'position': self.position,
                'trades': self.trade_history
            }
            self.update_callback(state)
