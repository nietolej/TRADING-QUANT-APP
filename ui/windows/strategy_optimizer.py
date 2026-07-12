import os
import glob
import pandas as pd
from datetime import datetime, timezone
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QSplitter, QComboBox, QPushButton, QLabel, QTableWidget, QTableWidgetItem,
    QMessageBox, QScrollArea, QFormLayout, QLineEdit, QDateEdit, QProgressBar
)
from PyQt6.QtCore import Qt, QDate, QThread, pyqtSignal

from strategy_engine.base_strategy import BaseStrategy
from backtest_engine.optimizer import GridSearchOptimizer
from data_layer.market_data import MarketDataManager
from data_layer.storage import SessionLocal, OHLCV

class OptimizerWorker(QThread):
    progress = pyqtSignal(int, int)
    finished_opt = pyqtSignal(object)
    
    def __init__(self, optimizer, df):
        super().__init__()
        self.optimizer = optimizer
        self.df = df
        
    def run(self):
        try:
            def prog_cb(current, total):
                self.progress.emit(current, total)
                
            res_df = self.optimizer.run(self.df, progress_callback=prog_cb)
            self.finished_opt.emit(res_df)
        except Exception as e:
            self.finished_opt.emit(e)

class StrategyOptimizerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Strategy Optimizer (Grid Search)")
        self.resize(1100, 700)
        self.db = SessionLocal()
        
        self.setup_ui()
        self.load_strategies()
        
    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Panel Izquierdo: Configuración
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        
        left_layout.addWidget(QLabel("Strategy:"))
        self.strategy_combo = QComboBox()
        self.strategy_combo.currentIndexChanged.connect(self.on_strategy_selected)
        left_layout.addWidget(self.strategy_combo)
        
        self.param_scroll = QScrollArea()
        self.param_scroll.setWidgetResizable(True)
        self.param_widget = QWidget()
        self.param_layout = QFormLayout(self.param_widget)
        self.param_scroll.setWidget(self.param_widget)
        left_layout.addWidget(self.param_scroll)
        
        self.param_inputs = {}
        
        self.lbl_instructions = QLabel("Format: single value (e.g. 20) or list (e.g. 10,20,30)")
        self.lbl_instructions.setStyleSheet("color: gray;")
        left_layout.addWidget(self.lbl_instructions)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        left_layout.addWidget(self.progress_bar)
        
        self.btn_run = QPushButton("Run Optimization")
        self.btn_run.clicked.connect(self.run_optimization)
        left_layout.addWidget(self.btn_run)
        
        splitter.addWidget(left_panel)
        
        # Panel Derecho: Resultados
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        self.results_table = QTableWidget(0, 0)
        right_layout.addWidget(self.results_table)
        
        splitter.addWidget(right_panel)
        splitter.setSizes([350, 750])
        layout.addWidget(splitter)
        
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
            
            # Basic Setup (Symbol, TF, Dates)
            symbols = [r[0] for r in self.db.query(OHLCV.symbol).distinct().all()]
            sym_combo = QComboBox()
            sym_combo.addItems(symbols)
            if self.current_strategy.symbol in symbols:
                sym_combo.setCurrentText(self.current_strategy.symbol)
            self.param_layout.addRow("Symbol:", sym_combo)
            self.param_inputs['symbol'] = sym_combo
            
            tf_combo = QComboBox()
            tfs = [r[0] for r in self.db.query(OHLCV.timeframe).filter(OHLCV.symbol == sym_combo.currentText()).distinct().all()]
            tf_combo.addItems(tfs)
            if self.current_strategy.timeframe in tfs:
                tf_combo.setCurrentText(self.current_strategy.timeframe)
            self.param_layout.addRow("Timeframe:", tf_combo)
            self.param_inputs['timeframe'] = tf_combo
            
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
            
            # Dynamic Strategy Parameters
            self.param_grid_keys = []
            
            rm = self.current_strategy.config.get("risk_management", {})
            for key in ["take_profit", "stop_loss"]:
                if key in rm and "value" in rm[key]:
                    path = f"risk_management.{key}.value"
                    val = str(rm[key]["value"])
                    line = QLineEdit(val)
                    self.param_layout.addRow(f"{key.title()}:", line)
                    self.param_inputs[path] = line
                    self.param_grid_keys.append(path)
                    
            for cond_type in ["entry_conditions", "exit_conditions"]:
                rules = self.current_strategy.config.get(cond_type, {}).get("rules", [])
                for i, rule in enumerate(rules):
                    if rule.get("type") == "technical_indicator":
                        p1_path = f"{cond_type}.rules.{i}.period1"
                        line1 = QLineEdit(str(rule.get("period1", 20)))
                        self.param_layout.addRow(f"{cond_type[:3]} Rule {i} P1:", line1)
                        self.param_inputs[p1_path] = line1
                        self.param_grid_keys.append(p1_path)
                        
                        p2_path = f"{cond_type}.rules.{i}.period2"
                        line2 = QLineEdit(str(rule.get("period2", 50)))
                        self.param_layout.addRow(f"{cond_type[:3]} Rule {i} P2:", line2)
                        self.param_inputs[p2_path] = line2
                        self.param_grid_keys.append(p2_path)
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not load strategy: {e}")
            
    def run_optimization(self):
        if not hasattr(self, 'current_strategy'):
            return
            
        self.btn_run.setEnabled(False)
        
        # Parse parameter grid
        param_grid = {}
        for path in self.param_grid_keys:
            raw_text = self.param_inputs[path].text()
            # Parse commas
            parts = [p.strip() for p in raw_text.split(',')]
            values = []
            for p in parts:
                try:
                    if '.' in p:
                        values.append(float(p))
                    else:
                        values.append(int(p))
                except ValueError:
                    QMessageBox.warning(self, "Parse Error", f"Invalid number format in {path}: {p}")
                    self.btn_run.setEnabled(True)
                    return
            param_grid[path] = values
            
        total_combos = 1
        for v in param_grid.values():
            total_combos *= len(v)
            
        if total_combos > 10000:
            QMessageBox.warning(self, "Too Many Combos", f"You have selected {total_combos} combinations. Please reduce to <10,000 to avoid freezing.")
            self.btn_run.setEnabled(True)
            return
            
        # Get historical data
        symbol = self.param_inputs['symbol'].currentText()
        timeframe = self.param_inputs['timeframe'].currentText()
        start_dt = datetime(self.param_inputs['start_date'].date().year(), self.param_inputs['start_date'].date().month(), self.param_inputs['start_date'].date().day(), tzinfo=timezone.utc)
        end_dt = datetime(self.param_inputs['end_date'].date().year(), self.param_inputs['end_date'].date().month(), self.param_inputs['end_date'].date().day(), 23, 59, 59, tzinfo=timezone.utc)
        
        market_mgr = MarketDataManager(self.db)
        df = market_mgr.get_data(symbol, timeframe, start_dt, end_dt)
        if df.empty:
            QMessageBox.warning(self, "No Data", "No historical data found.")
            self.btn_run.setEnabled(True)
            return
            
        self.progress_bar.setMaximum(total_combos)
        self.progress_bar.setValue(0)
        
        # Instantiate optimizer
        optimizer = GridSearchOptimizer(self.current_strategy.config, param_grid)
        
        # Run in thread
        self.worker = OptimizerWorker(optimizer, df)
        self.worker.progress.connect(self.update_progress)
        self.worker.finished_opt.connect(self.on_finished)
        self.worker.start()
        
    def update_progress(self, current, total):
        self.progress_bar.setValue(current)
        self.btn_run.setText(f"Running... {current}/{total}")
        
    def on_finished(self, res):
        self.btn_run.setEnabled(True)
        self.btn_run.setText("Run Optimization")
        
        if isinstance(res, Exception):
            QMessageBox.critical(self, "Error", f"Optimization failed: {res}")
            return
            
        df = res
        if df.empty:
            QMessageBox.information(self, "Done", "No results generated.")
            return
            
        # Display in table
        self.results_table.clear()
        self.results_table.setRowCount(len(df))
        self.results_table.setColumnCount(len(df.columns))
        self.results_table.setHorizontalHeaderLabels(df.columns)
        
        for r_idx, row in df.iterrows():
            for c_idx, col in enumerate(df.columns):
                val = row[col]
                if isinstance(val, float):
                    s_val = f"{val:.4f}"
                else:
                    s_val = str(val)
                self.results_table.setItem(r_idx, c_idx, QTableWidgetItem(s_val))
                
        self.results_table.resizeColumnsToContents()
