import os
import glob
import pandas as pd
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QSplitter, QTreeWidget, QTreeWidgetItem, QComboBox, 
    QPushButton, QTabWidget, QLabel, QTableWidget, QTableWidgetItem,
    QMessageBox, QDoubleSpinBox, QScrollArea, QFormLayout, QLineEdit, QDateEdit
)
from PyQt6.QtCore import Qt, QDate

from strategy_engine.base_strategy import BaseStrategy
from backtest_engine.backtester import Backtester
from data_layer.market_data import MarketDataManager
from data_layer.onchain_data import OnChainDataManager
from data_layer.storage import SessionLocal, BacktestRun, OHLCV

import pyqtgraph as pg
from ui.components.charting import CandlestickItem, TimeAxisItem

class StrategyAnalyzerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Strategy Analyzer")
        self.resize(1000, 700)
        self.db = SessionLocal()
        
        self.setup_ui()
        self.load_strategies()
        
    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Panel Izquierdo: Configuración (Properties)
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        
        left_layout.addWidget(QLabel("Strategy:"))
        self.strategy_combo = QComboBox()
        self.strategy_combo.currentIndexChanged.connect(self.on_strategy_selected)
        left_layout.addWidget(self.strategy_combo)
        
        # Capital
        cap_layout = QHBoxLayout()
        cap_layout.addWidget(QLabel("Initial Capital:"))
        self.capital_spinbox = QDoubleSpinBox()
        self.capital_spinbox.setRange(0.001, 100000000)
        self.capital_spinbox.setDecimals(4)
        self.capital_spinbox.setValue(10000)
        self.capital_currency_combo = QComboBox()
        self.capital_currency_combo.addItems(["Quote Asset", "Base Asset"])
        
        cap_layout.addWidget(self.capital_spinbox)
        cap_layout.addWidget(self.capital_currency_combo)
        left_layout.addLayout(cap_layout)
        
        left_layout.addWidget(QLabel("Parameters:"))
        
        self.param_scroll = QScrollArea()
        self.param_scroll.setWidgetResizable(True)
        self.param_widget = QWidget()
        self.param_layout = QFormLayout(self.param_widget)
        self.param_scroll.setWidget(self.param_widget)
        left_layout.addWidget(self.param_scroll)
        
        self.param_inputs = {} # Store widget references
        
        self.btn_run = QPushButton("Run Backtest")
        self.btn_run.clicked.connect(self.run_backtest)
        left_layout.addWidget(self.btn_run)
        
        splitter.addWidget(left_panel)
        
        # Panel Derecho: Resultados
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        self.tabs = QTabWidget()
        
        # Tab Summary
        self.tab_summary = QWidget()
        self.setup_summary_tab(self.tab_summary)
        self.tabs.addTab(self.tab_summary, "Summary")
        
        # Tab Executions
        self.tab_executions = QWidget()
        self.setup_executions_tab(self.tab_executions)
        self.tabs.addTab(self.tab_executions, "Executions")
        
        # Tab Chart
        self.tab_chart = QWidget()
        self.setup_chart_tab(self.tab_chart)
        self.tabs.addTab(self.tab_chart, "Chart")
        
        right_layout.addWidget(self.tabs)
        splitter.addWidget(right_panel)
        
        splitter.setSizes([300, 700])
        layout.addWidget(splitter)
        
    def setup_summary_tab(self, parent):
        from PyQt6.QtWidgets import QSplitter
        from PyQt6.QtCore import Qt
        
        layout = QVBoxLayout(parent)
        
        # Opciones de visualización
        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("Evaluar Ganancia en:"))
        self.denom_combo = QComboBox()
        self.denom_combo.addItems(["Quote Asset (ej. USDT)", "Base Asset (ej. BTC)"])
        self.denom_combo.currentIndexChanged.connect(self.update_summary_display)
        top_layout.addWidget(self.denom_combo)
        top_layout.addStretch()
        layout.addLayout(top_layout)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        self.summary_table = QTableWidget(0, 2)
        self.summary_table.setHorizontalHeaderLabels(["Metric", "Value"])
        self.summary_table.horizontalHeader().setStretchLastSection(True)
        splitter.addWidget(self.summary_table)
        
        self.summary_equity_plot = pg.PlotWidget()
        self.summary_equity_plot.showGrid(x=True, y=True)
        self.summary_equity_plot.setLabel('left', 'Equity Curve')
        splitter.addWidget(self.summary_equity_plot)
        
        layout.addWidget(splitter)
        
        self.last_run_results = None
        self.last_df = None
        
    def setup_executions_tab(self, parent):
        layout = QVBoxLayout(parent)
        self.executions_table = QTableWidget(0, 8)
        self.executions_table.setHorizontalHeaderLabels(
            ["Entry Time", "Exit Time", "Side", "Entry Price", "Exit Price", "Qty", "P&L", "Reason"]
        )
        layout.addWidget(self.executions_table)
        
    def setup_chart_tab(self, parent):
        layout = QVBoxLayout(parent)
        self.chart_widget = pg.GraphicsLayoutWidget()
        layout.addWidget(self.chart_widget)
        
        # Plot Principal (Velas)
        self.plot_main = self.chart_widget.addPlot(row=0, col=0)
        self.plot_main.showGrid(x=True, y=True)
        self.plot_main.setLabel('left', 'Price')
        
        # Plot Secundario (Equity)
        self.plot_equity = self.chart_widget.addPlot(row=1, col=0)
        self.plot_equity.showGrid(x=True, y=True)
        self.plot_equity.setLabel('left', 'Equity')
        
        # Plot Terciario (On-Chain)
        self.plot_onchain = self.chart_widget.addPlot(row=2, col=0)
        self.plot_onchain.showGrid(x=True, y=True)
        self.plot_onchain.setLabel('left', 'On-Chain')
        self.plot_onchain.hide() # Oculto por defecto si no hay data
        
        self.chart_widget.ci.layout.setRowStretchFactor(0, 4)
        self.chart_widget.ci.layout.setRowStretchFactor(1, 1)
        self.chart_widget.ci.layout.setRowStretchFactor(2, 1)
        
        # Vincular Eje X
        self.plot_equity.setXLink(self.plot_main)
        self.plot_onchain.setXLink(self.plot_main)
        
    def load_strategies(self):
        strategy_files = glob.glob("config/strategies/*.yaml")
        for f in strategy_files:
            self.strategy_combo.addItem(os.path.basename(f), f)
            
    def on_strategy_selected(self):
        file_path = self.strategy_combo.currentData()
        if not file_path:
            return
            
        try:
            self.current_strategy = BaseStrategy(file_path)
            
            # Clear old widgets
            while self.param_layout.count():
                child = self.param_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
                    
            self.param_inputs = {}
            
            # Symbol & Timeframe
            symbols = [r[0] for r in self.db.query(OHLCV.symbol).distinct().all()]
            
            sym_combo = QComboBox()
            sym_combo.addItems(symbols)
            if self.current_strategy.symbol in symbols:
                sym_combo.setCurrentText(self.current_strategy.symbol)
            elif symbols:
                sym_combo.setCurrentIndex(0)
            
            self.param_layout.addRow("Symbol:", sym_combo)
            self.param_inputs['symbol'] = sym_combo
            
            tf_combo = QComboBox()
            self.param_layout.addRow("Timeframe:", tf_combo)
            self.param_inputs['timeframe'] = tf_combo
            
            def update_timeframes():
                current_sym = sym_combo.currentText()
                tfs = [r[0] for r in self.db.query(OHLCV.timeframe).filter(OHLCV.symbol == current_sym).distinct().all()]
                tf_combo.clear()
                tf_combo.addItems(tfs)
                if self.current_strategy.timeframe in tfs:
                    tf_combo.setCurrentText(self.current_strategy.timeframe)
            
            sym_combo.currentIndexChanged.connect(update_timeframes)
            update_timeframes() # Populate initially
            
            # Dates
            start_date_edit = QDateEdit()
            start_date_edit.setCalendarPopup(True)
            start_date_edit.setDate(QDate.currentDate().addYears(-1))
            self.param_layout.addRow("Start Date:", start_date_edit)
            self.param_inputs['start_date'] = start_date_edit
            
            end_date_edit = QDateEdit()
            end_date_edit.setCalendarPopup(True)
            end_date_edit.setDate(QDate.currentDate())
            self.param_layout.addRow("End Date:", end_date_edit)
            self.param_inputs['end_date'] = end_date_edit
            
            # Risk Management
            rm = self.current_strategy.config.get("risk_management", {})
            for key in ["take_profit", "stop_loss"]:
                if key in rm and "value" in rm[key]:
                    val = rm[key]["value"]
                    spin = QDoubleSpinBox()
                    spin.setRange(0, 100000)
                    spin.setValue(float(val))
                    self.param_layout.addRow(f"{key.replace('_', ' ').title()} Value:", spin)
                    self.param_inputs[f'risk_{key}'] = spin
                    
            # Conditions (Entry/Exit periods)
            for cond_type in ["entry_conditions", "exit_conditions"]:
                rules = self.current_strategy.config.get(cond_type, {}).get("rules", [])
                for i, rule in enumerate(rules):
                    if rule.get("type") == "technical_indicator":
                        # Fast period
                        spin1 = QDoubleSpinBox()
                        spin1.setRange(1, 1000)
                        spin1.setDecimals(0)
                        spin1.setValue(rule.get("period1", 20))
                        self.param_layout.addRow(f"{cond_type.split('_')[0].title()} Rule {i+1} Fast Period:", spin1)
                        self.param_inputs[f'{cond_type}_{i}_period1'] = spin1
                        
                        # Slow period
                        spin2 = QDoubleSpinBox()
                        spin2.setRange(1, 1000)
                        spin2.setDecimals(0)
                        spin2.setValue(rule.get("period2", 50))
                        self.param_layout.addRow(f"{cond_type.split('_')[0].title()} Rule {i+1} Slow Period:", spin2)
                        self.param_inputs[f'{cond_type}_{i}_period2'] = spin2
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not load strategy: {e}")
            
    def run_backtest(self):
        if not hasattr(self, 'current_strategy'):
            return
            
        self.btn_run.setEnabled(False)
        self.btn_run.setText("Calculating...")
        
        try:
            from datetime import datetime, timezone
            
            # 1. Aplicar cambios dinámicos desde UI a la estrategia antes de correr
            self.current_strategy.symbol = self.param_inputs['symbol'].currentText()
            self.current_strategy.timeframe = self.param_inputs['timeframe'].currentText()
            
            start_qdate = self.param_inputs['start_date'].date()
            end_qdate = self.param_inputs['end_date'].date()
            start_dt = datetime(start_qdate.year(), start_qdate.month(), start_qdate.day(), tzinfo=timezone.utc)
            end_dt = datetime(end_qdate.year(), end_qdate.month(), end_qdate.day(), 23, 59, 59, tzinfo=timezone.utc)
            
            rm = self.current_strategy.config.get("risk_management", {})
            for key in ["take_profit", "stop_loss"]:
                if f'risk_{key}' in self.param_inputs:
                    rm[key]['value'] = self.param_inputs[f'risk_{key}'].value()
                    
            for cond_type in ["entry_conditions", "exit_conditions"]:
                rules = self.current_strategy.config.get(cond_type, {}).get("rules", [])
                for i, rule in enumerate(rules):
                    if f'{cond_type}_{i}_period1' in self.param_inputs:
                        rule['period1'] = int(self.param_inputs[f'{cond_type}_{i}_period1'].value())
                    if f'{cond_type}_{i}_period2' in self.param_inputs:
                        rule['period2'] = int(self.param_inputs[f'{cond_type}_{i}_period2'].value())
            
            market_mgr = MarketDataManager(self.db)
            df = market_mgr.get_data(
                self.current_strategy.symbol, 
                self.current_strategy.timeframe, 
                start_dt,
                end_dt
            )
            
            if df.empty:
                QMessageBox.warning(self, "No Data", "No historical data found for this symbol.")
                return
                
            # Extraer qué métricas on-chain necesita la estrategia
            required_metrics = set()
            for block in [self.current_strategy.config.get("entry_conditions", {}), self.current_strategy.config.get("exit_conditions", {})]:
                for rule in block.get("rules", []):
                    if rule.get("type") == "onchain_threshold":
                        required_metrics.add(rule.get("metric"))
                        
            # Cargar y fusionar datos On-Chain
            onchain_mgr = OnChainDataManager(self.db)
            self.plotted_onchain_metric = None # Guardar cual graficar
            for metric in required_metrics:
                # El símbolo de onchain a veces varía, intentamos el de la estrategia y los genéricos (ej. USDT)
                # Defillama usa 'USDT', CryptoQuant usa 'BTC/USDT'
                df_oc = onchain_mgr.get_data(metric, self.current_strategy.symbol, pd.to_datetime("2020-01-01", utc=True))
                if df_oc.empty:
                    # Intento 2: Solo base asset (ej BTC)
                    base_asset = self.current_strategy.symbol.split('/')[0]
                    df_oc = onchain_mgr.get_data(metric, base_asset, pd.to_datetime("2020-01-01", utc=True))
                if df_oc.empty:
                    # Intento 3: Solo quote asset (ej USDT)
                    quote_asset = self.current_strategy.symbol.split('/')[1] if '/' in self.current_strategy.symbol else 'USDT'
                    df_oc = onchain_mgr.get_data(metric, quote_asset, pd.to_datetime("2020-01-01", utc=True))
                    
                if not df_oc.empty:
                    # Renombrar columna 'value' al nombre de la métrica
                    df_oc = df_oc[['value']].rename(columns={'value': metric})
                    
                    # Merge asof o join con ffill. Los onchain suelen ser diarios o esporádicos.
                    # Primero nos aseguramos de que el index de df tenga tz info
                    if df.index.tz is None:
                        df.index = df.index.tz_localize('UTC')
                    if df_oc.index.tz is None:
                        df_oc.index = df_oc.index.tz_localize('UTC')
                        
                    # Para ffill, unimos (outer join) y rellenamos hacia adelante, luego dropeamos los index extras
                    df = df.join(df_oc, how='outer')
                    df[metric] = df[metric].ffill()
                    df = df.dropna(subset=['close']) # Nos quedamos solo con las filas originales del timeframe de precio
                    
                    self.plotted_onchain_metric = metric # Marcar para graficar
                else:
                    print(f"Advertencia: No se encontró data on-chain para {metric}")
                
            capital = self.capital_spinbox.value()
            if self.capital_currency_combo.currentIndex() == 1:
                # El usuario configuró el capital en Base Asset (ej. BTC)
                # Lo convertimos a Quote Asset (ej. USDT) para el motor del backtester
                capital = capital * df.iloc[0]['close']
                
            backtester = Backtester(self.current_strategy, initial_capital=capital)
            
            # Idealmente esto correría en un QThread para no bloquear la UI
            results = backtester.run(df)
            
            self.display_results(results)
            
            # Update Summary Tab
            self.last_run_results = results
            self.last_df = df
            self.update_summary_display()
            
            # Save run to database
            try:
                db_run = BacktestRun(
                    run_id=results.get('run_id'),
                    strategy_name=results.get('strategy_name'),
                    config_snapshot=results.get('config_snapshot'),
                    symbol=results.get('symbol'),
                    timeframe=results.get('timeframe'),
                    start_date=results.get('start_date'),
                    end_date=results.get('end_date'),
                    created_at=results.get('created_at'),
                    cagr=results.get('cagr', 0.0),
                    sharpe_ratio=results.get('sharpe_ratio', 0.0),
                    sortino_ratio=results.get('sortino_ratio', 0.0),
                    max_drawdown_pct=results.get('max_drawdown_pct', 0.0),
                    win_rate=results.get('win_rate', 0.0),
                    profit_factor=results.get('profit_factor', 0.0),
                    total_trades=results.get('total_trades', 0),
                    percent_profitable=results.get('percent_profitable', 0.0),
                    average_trade_net_profit=results.get('average_trade_net_profit', 0.0)
                )
                self.db.add(db_run)
                self.db.commit()
            except Exception as db_e:
                print(f"Failed to save backtest to DB: {db_e}")
                self.db.rollback()
            
            QMessageBox.information(self, "Success", "Backtest completed and saved to database.")
            
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
        finally:
            self.btn_run.setEnabled(True)
            self.btn_run.setText("Run Backtest")
            
    def display_results(self, results):
        # Executions
        trades = results.get("trades", pd.DataFrame())
        self.executions_table.setRowCount(0)
        for idx, trade in trades.iterrows():
            row = self.executions_table.rowCount()
            self.executions_table.insertRow(row)
            
            self.executions_table.setItem(row, 0, QTableWidgetItem(str(trade['entry_time'])))
            self.executions_table.setItem(row, 1, QTableWidgetItem(str(trade['exit_time'])))
            self.executions_table.setItem(row, 2, QTableWidgetItem(trade['side']))
            self.executions_table.setItem(row, 3, QTableWidgetItem(f"{trade['entry_price']:.2f}"))
            self.executions_table.setItem(row, 4, QTableWidgetItem(f"{trade['exit_price']:.2f}"))
            self.executions_table.setItem(row, 5, QTableWidgetItem(f"{trade['quantity']:.4f}"))
            self.executions_table.setItem(row, 6, QTableWidgetItem(f"{trade['pnl']:.2f}"))
            self.executions_table.setItem(row, 7, QTableWidgetItem(trade['exit_reason']))
            
        # Draw Charts
        self.plot_main.clear()
        self.plot_equity.clear()
        
        df = results.get("raw_data")
        if df is not None and not df.empty:
            timestamps = dict(enumerate(df.index.strftime('%Y-%m-%d %H:%M')))
            
            # Recreate X-Axis with timestamps mapping
            axis_main = TimeAxisItem(timestamps, orientation='bottom')
            self.plot_main.setAxisItems({'bottom': axis_main})
            
            axis_eq = TimeAxisItem(timestamps, orientation='bottom')
            self.plot_equity.setAxisItems({'bottom': axis_eq})
            
            # Convert to list of tuples: (x_index, open, close, low, high)
            x_indices = list(range(len(df)))
            ohlc_data = [
                (i, row['open'], row['close'], row['low'], row['high']) 
                for i, row in zip(x_indices, df.to_dict('records'))
            ]
            
            # Plot Candlesticks
            candlesticks = CandlestickItem(ohlc_data)
            self.plot_main.addItem(candlesticks)
            
            # Plot Equity
            eq = results.get("equity_curve")
            if eq is not None and not eq.empty:
                # Merge con index para obtener el mismo espaciado
                eq_indices = [df.index.get_loc(t) for t in eq.index if t in df.index]
                self.plot_equity.plot(eq_indices, eq['equity'].values, pen='y')
                
            # Plot Trades (Markers)
            for idx, trade in trades.iterrows():
                try:
                    entry_idx = df.index.get_loc(trade['entry_time'])
                    exit_idx = df.index.get_loc(trade['exit_time'])
                    
                    # Entry
                    color = 'g' if trade['side'] == 'long' else 'r'
                    symbol = 't1' if trade['side'] == 'long' else 't' # Flecha arriba/abajo
                    self.plot_main.plot([entry_idx], [trade['entry_price']], pen=None, symbol=symbol, symbolBrush=color, symbolSize=14)
                    
                    # Exit
                    self.plot_main.plot([exit_idx], [trade['exit_price']], pen=None, symbol='x', symbolBrush='y', symbolSize=12)
                except KeyError:
                    continue # Fecha no encontrada en el df
                    
            # Plot On-Chain if available
            self.plot_onchain.clear()
            if getattr(self, 'plotted_onchain_metric', None) and self.plotted_onchain_metric in df.columns:
                self.plot_onchain.show()
                self.plot_onchain.setLabel('left', self.plotted_onchain_metric)
                # Plot como histograma (barras) para netflow, o linea para mcap
                metric_values = df[self.plotted_onchain_metric].values
                if "flow" in self.plotted_onchain_metric.lower():
                    # Usar BarGraphItem para netflows
                    bg = pg.BarGraphItem(x=x_indices, height=metric_values, width=0.6, brush='c')
                    self.plot_onchain.addItem(bg)
                else:
                    # Linea normal
                    self.plot_onchain.plot(x_indices, metric_values, pen='m')
            else:
                self.plot_onchain.hide()

    def update_summary_display(self):
        if getattr(self, 'last_run_results', None) is None:
            return
            
        run_results = self.last_run_results
        df = self.last_df
        denom = self.denom_combo.currentIndex() # 0 = Quote (USDT), 1 = Base (BTC)
        
        from backtest_engine.metrics import calculate_metrics, calculate_equity_curve_metrics
        
        trades_df = run_results["trades"].copy()
        equity_curve = run_results["equity_curve"]['equity'].copy()
        
        ui_capital = self.capital_spinbox.value()
        is_ui_capital_base = (self.capital_currency_combo.currentIndex() == 1)
        
        symbol_prefix = "$"
        
        if denom == 1: # Evaluar en Base Asset
            if is_ui_capital_base:
                initial_capital = ui_capital
            else:
                initial_capital = ui_capital / df.iloc[0]['close']
                
            if not trades_df.empty:
                # Convertir PnL a Base Asset (dividiendo por el precio de salida de cada trade)
                trades_df['pnl'] = trades_df.apply(lambda row: row['pnl'] / row['exit_price'], axis=1)
                # Convertir Equity Curve
                equity_curve = equity_curve / df['close']
                
            symbol_prefix = "₿" if "BTC" in self.current_strategy.symbol else "Ξ"
            
        else: # Evaluar en Quote Asset
            if is_ui_capital_base:
                initial_capital = ui_capital * df.iloc[0]['close']
            else:
                initial_capital = ui_capital
                
            symbol_prefix = "$"

        trade_metrics = calculate_metrics(trades_df, initial_capital)
        eq_metrics = calculate_equity_curve_metrics(equity_curve)
        
        final_capital = equity_curve.iloc[-1] if not equity_curve.empty else initial_capital
        
        metrics = {
            "Initial Capital": f"{symbol_prefix}{initial_capital:.4f}",
            "Final Capital": f"{symbol_prefix}{final_capital:.4f}",
            "Total Trades": trade_metrics.get("total_trades", 0),
            "Winning Trades": trade_metrics.get("winning_trades", 0),
            "Losing Trades": trade_metrics.get("losing_trades", 0),
            "Net Profit": f"{symbol_prefix}{trade_metrics.get('net_profit', 0):.4f}",
            "Profit Factor": f"{trade_metrics.get('profit_factor', 0):.2f}",
            "Max Drawdown": f"{eq_metrics.get('max_drawdown_pct', 0):.2f}%",
            "CAGR": f"{eq_metrics.get('cagr', 0):.2f}%"
        }
        
        self.summary_table.setRowCount(len(metrics))
        for i, (k, v) in enumerate(metrics.items()):
            self.summary_table.setItem(i, 0, QTableWidgetItem(k))
            self.summary_table.setItem(i, 1, QTableWidgetItem(str(v)))
            
        # Graficar Equity en la pestaña Summary
        self.summary_equity_plot.clear()
        if not equity_curve.empty:
            x_indices = range(len(equity_curve))
            self.summary_equity_plot.plot(list(x_indices), equity_curve.values, pen='y')
            self.summary_equity_plot.setLabel('left', 'Equity (' + ('Base' if denom == 1 else 'Quote') + ')')

        # Renderizar Tabla de Ejecuciones (Trades)
        self.executions_table.setRowCount(0)
        
        raw_trades_df = run_results["trades"] # Use original quote-denominated trades
        
        if not raw_trades_df.empty:
            base_asset = self.current_strategy.symbol.split('/')[0]
            quote_asset = self.current_strategy.symbol.split('/')[1] if '/' in self.current_strategy.symbol else 'USDT'
            
            headers = ["Entry Time", "Exit Time", "Side", "Entry Price", "Exit Price", "Qty", 
                       f"P&L ({quote_asset})", f"P&L ({base_asset})", "Reason", 
                       f"Portfolio ({quote_asset})", f"Portfolio ({base_asset})"]
            self.executions_table.setColumnCount(len(headers))
            self.executions_table.setHorizontalHeaderLabels(headers)
            
            for _, row in raw_trades_df.iterrows():
                row_idx = self.executions_table.rowCount()
                self.executions_table.insertRow(row_idx)
                
                self.executions_table.setItem(row_idx, 0, QTableWidgetItem(str(row.get('entry_time', ''))[:19]))
                self.executions_table.setItem(row_idx, 1, QTableWidgetItem(str(row.get('exit_time', ''))[:19]))
                self.executions_table.setItem(row_idx, 2, QTableWidgetItem(row.get('side', '')))
                self.executions_table.setItem(row_idx, 3, QTableWidgetItem(f"{row.get('entry_price', 0):.2f}"))
                self.executions_table.setItem(row_idx, 4, QTableWidgetItem(f"{row.get('exit_price', 0):.2f}"))
                self.executions_table.setItem(row_idx, 5, QTableWidgetItem(f"{row.get('quantity', 0):.4f}"))
                
                
                # PnL en Quote Asset (el original de backtester es en quote)
                pnl_quote = row.get('pnl', 0)
                # PnL en Base Asset
                pnl_base = pnl_quote / row.get('exit_price', 1)
                
                self.executions_table.setItem(row_idx, 6, QTableWidgetItem(f"${pnl_quote:.4f}"))
                self.executions_table.setItem(row_idx, 7, QTableWidgetItem(f"{pnl_base:.6f}"))
                self.executions_table.setItem(row_idx, 8, QTableWidgetItem(row.get('exit_reason', '')))
                
                # Portfolio Value
                pv_quote = row.get('portfolio_value', 0)
                pv_base = pv_quote / row.get('exit_price', 1)
                self.executions_table.setItem(row_idx, 9, QTableWidgetItem(f"${pv_quote:.4f}"))
                self.executions_table.setItem(row_idx, 10, QTableWidgetItem(f"{pv_base:.6f}"))
