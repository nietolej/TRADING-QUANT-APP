import os
import yaml
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QLabel, QLineEdit, QComboBox, QDoubleSpinBox, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QDialog,
    QFormLayout, QDialogButtonBox, QFileDialog
)
from PyQt6.QtCore import Qt

class ConditionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Condition")
        self.setMinimumWidth(400)
        
        layout = QVBoxLayout(self)
        form = QFormLayout()
        
        # Tipo de Condición
        self.type_combo = QComboBox()
        self.type_combo.addItems(["technical_indicator", "onchain_threshold"])
        form.addRow("Type:", self.type_combo)
        
        # Indicador Rápido (1)
        self.ind1_combo = QComboBox()
        self.ind1_combo.addItems(["EMA", "SMA", "Price"])
        form.addRow("Fast Indicator (Rápida):", self.ind1_combo)
        
        self.period1_spin = QDoubleSpinBox()
        self.period1_spin.setRange(1, 1000)
        self.period1_spin.setValue(20)
        self.period1_spin.setDecimals(0)
        form.addRow("Fast Period:", self.period1_spin)
        
        # Relación
        self.operator_combo = QComboBox()
        self.operator_combo.addItems(["crosses_above", "crosses_below", "is_above", "is_below", "increasing", "decreasing"])
        form.addRow("Operator:", self.operator_combo)
        
        # Indicador Lento (2)
        self.ind2_combo = QComboBox()
        self.ind2_combo.addItems(["EMA", "SMA", "Price"])
        form.addRow("Slow Indicator (Lenta):", self.ind2_combo)
        
        self.period2_spin = QDoubleSpinBox()
        self.period2_spin.setRange(1, 1000)
        self.period2_spin.setValue(50)
        self.period2_spin.setDecimals(0)
        form.addRow("Slow Period:", self.period2_spin)
        
        layout.addLayout(form)
        
        # Botones
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        
    def get_condition(self):
        c_type = self.type_combo.currentText()
        if c_type == "technical_indicator":
            return {
                "type": c_type,
                "indicator1": self.ind1_combo.currentText(),
                "period1": int(self.period1_spin.value()),
                "operator": self.operator_combo.currentText(),
                "indicator2": self.ind2_combo.currentText(),
                "period2": int(self.period2_spin.value())
            }
        else:
            return {
                "type": c_type,
                "metric": "usdt_market_cap",
                "condition": self.operator_combo.currentText(),
                "lookback_days": int(self.period1_spin.value()),
                "min_change_pct": 0.1
            }

class StrategyBuilderWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Strategy Builder")
        self.setGeometry(150, 150, 800, 600)
        
        self.entry_rules = []
        self.exit_rules = []
        
        self.setup_ui()
        
    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        self.tabs = QTabWidget()
        
        # Load Strategy Button
        top_layout = QHBoxLayout()
        self.btn_load = QPushButton("Load Existing Strategy")
        self.btn_load.clicked.connect(self.load_strategy)
        top_layout.addWidget(self.btn_load)
        top_layout.addStretch()
        main_layout.addLayout(top_layout)
        
        main_layout.addWidget(self.tabs)
        
        # Tab 1: General
        self.tab_general = QWidget()
        self.setup_general_tab()
        self.tabs.addTab(self.tab_general, "1. General")
        
        # Tab 2: Risk Management
        self.tab_risk = QWidget()
        self.setup_risk_tab()
        self.tabs.addTab(self.tab_risk, "2. Risk Management")
        
        # Tab 3: Entry Conditions
        self.tab_entry = QWidget()
        self.setup_entry_tab()
        self.tabs.addTab(self.tab_entry, "3. Entry Conditions")
        
        # Tab 4: Exit Conditions
        self.tab_exit = QWidget()
        self.setup_exit_tab()
        self.tabs.addTab(self.tab_exit, "4. Exit Conditions")
        
        # Bottom Bar
        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()
        self.btn_save = QPushButton("Build & Save Strategy")
        self.btn_save.setMinimumHeight(40)
        self.btn_save.clicked.connect(self.save_strategy)
        bottom_layout.addWidget(self.btn_save)
        main_layout.addLayout(bottom_layout)
        
    def setup_general_tab(self):
        layout = QFormLayout(self.tab_general)
        
        self.name_input = QLineEdit("MyCustomStrategy")
        layout.addRow("Strategy Name:", self.name_input)
        
        self.desc_input = QLineEdit("A custom built strategy")
        layout.addRow("Description:", self.desc_input)
        
        self.symbol_input = QLineEdit("BTC/USDT")
        layout.addRow("Default Symbol:", self.symbol_input)
        
        self.timeframe_input = QComboBox()
        self.timeframe_input.addItems(["1m", "5m", "15m", "1h", "4h", "1d"])
        layout.addRow("Default Timeframe:", self.timeframe_input)
        
        # Dirección de operación
        self.direction_input = QComboBox()
        self.direction_input.addItems(["Long", "Short"])
        layout.addRow("Trade Direction:", self.direction_input)
        
    def setup_risk_tab(self):
        layout = QFormLayout(self.tab_risk)
        
        # Take Profit
        self.tp_type = QComboBox()
        self.tp_type.addItems(["None", "Percentage", "Fixed Price"])
        layout.addRow("Take Profit Type:", self.tp_type)
        
        self.tp_val = QDoubleSpinBox()
        self.tp_val.setRange(0, 1000)
        self.tp_val.setValue(5.0)
        layout.addRow("Take Profit Value (%):", self.tp_val)
        
        # Stop Loss
        self.sl_type = QComboBox()
        self.sl_type.addItems(["None", "Percentage", "Trailing Percent"])
        layout.addRow("Stop Loss Type:", self.sl_type)
        
        self.sl_val = QDoubleSpinBox()
        self.sl_val.setRange(0, 1000)
        self.sl_val.setValue(2.0)
        layout.addRow("Stop Loss Value (%):", self.sl_val)
        
    def setup_entry_tab(self):
        layout = QVBoxLayout(self.tab_entry)
        
        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("Logic Operator:"))
        self.entry_logic_combo = QComboBox()
        self.entry_logic_combo.addItems(["AND", "OR"])
        top_layout.addWidget(self.entry_logic_combo)
        top_layout.addStretch()
        
        self.btn_add_entry = QPushButton("Add Condition")
        self.btn_add_entry.clicked.connect(lambda: self.add_rule('entry'))
        top_layout.addWidget(self.btn_add_entry)
        layout.addLayout(top_layout)
        
        self.entry_table = QTableWidget(0, 1)
        self.entry_table.setHorizontalHeaderLabels(["Condition Definition"])
        self.entry_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.entry_table)
        
    def setup_exit_tab(self):
        layout = QVBoxLayout(self.tab_exit)
        
        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("Logic Operator:"))
        self.exit_logic_combo = QComboBox()
        self.exit_logic_combo.addItems(["OR", "AND"])
        top_layout.addWidget(self.exit_logic_combo)
        top_layout.addStretch()
        
        self.btn_add_exit = QPushButton("Add Condition")
        self.btn_add_exit.clicked.connect(lambda: self.add_rule('exit'))
        top_layout.addWidget(self.btn_add_exit)
        layout.addLayout(top_layout)
        
        self.exit_table = QTableWidget(0, 1)
        self.exit_table.setHorizontalHeaderLabels(["Condition Definition"])
        self.exit_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.exit_table)
        
    def add_rule(self, tab_type):
        dialog = ConditionDialog(self)
        if dialog.exec():
            cond = dialog.get_condition()
            
            # Format text for UI
            if cond['type'] == 'technical_indicator':
                text = f"{cond['indicator1']}({cond['period1']}) {cond['operator']} {cond['indicator2']}({cond['period2']})"
            else:
                text = f"OnChain({cond['metric']}) {cond['condition']} ({cond['lookback_days']} days)"
                
            if tab_type == 'entry':
                self.entry_rules.append(cond)
                row = self.entry_table.rowCount()
                self.entry_table.insertRow(row)
                self.entry_table.setItem(row, 0, QTableWidgetItem(text))
            else:
                self.exit_rules.append(cond)
                row = self.exit_table.rowCount()
                self.exit_table.insertRow(row)
                self.exit_table.setItem(row, 0, QTableWidgetItem(text))
                
    def save_strategy(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Error", "Strategy Name is required.")
            return
            
        config = {
            "strategy_name": self.name_input.text() or "MyStrategy",
            "description": self.desc_input.text(),
            "symbol": self.symbol_input.text(),
            "timeframe": self.timeframe_input.currentText(),
            "trade_direction": self.direction_input.currentText(),
            "risk_management": {
                "take_profit": {
                    "type": self.tp_type.currentText().lower(),
                    "value": float(self.tp_val.value())
                },
                "stop_loss": {
                    "type": self.sl_type.currentText().lower().replace(" ", "_"),
                    "value": float(self.sl_val.value())
                }
            },
            "entry_conditions": {
                "logic": self.entry_logic_combo.currentText(),
                "rules": self.entry_rules
            },
            "exit_conditions": {
                "logic": self.exit_logic_combo.currentText(),
                "rules": self.exit_rules
            }
        }
        
        filename = f"config/strategies/{name.lower().replace(' ', '_')}.yaml"
        try:
            with open(filename, 'w') as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)
            QMessageBox.information(self, "Success", f"Strategy saved successfully to {filename}!\nYou can now run it in the Strategy Analyzer.")
            self.close()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not save strategy:\n{e}")

    def load_strategy(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Load Strategy", "config/strategies", "YAML Files (*.yaml *.yml)")
        if not filename:
            return
            
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                
            self.name_input.setText(config.get("strategy_name", ""))
            self.desc_input.setText(config.get("description", ""))
            self.symbol_input.setText(config.get("symbol", "BTC/USDT"))
            
            tf = config.get("timeframe", "1d")
            idx = self.timeframe_input.findText(tf)
            if idx >= 0: self.timeframe_input.setCurrentIndex(idx)
                
            direction = config.get("trade_direction", "Long")
            idx = self.direction_input.findText(direction)
            if idx >= 0: self.direction_input.setCurrentIndex(idx)
            
            rm = config.get("risk_management", {})
            tp = rm.get("take_profit", {})
            idx = self.tp_type.findText(tp.get("type", "none").replace("_", " ").title())
            if idx >= 0: self.tp_type.setCurrentIndex(idx)
            self.tp_val.setValue(float(tp.get("value", 0)))
            
            sl = rm.get("stop_loss", {})
            idx = self.sl_type.findText(sl.get("type", "none").replace("_", " ").title())
            if idx >= 0: self.sl_type.setCurrentIndex(idx)
            self.sl_val.setValue(float(sl.get("value", 0)))
            
            self.entry_rules = []
            self.entry_table.setRowCount(0)
            entry_logic = config.get("entry_conditions", {}).get("logic", "AND")
            idx = self.entry_logic_combo.findText(entry_logic)
            if idx >= 0: self.entry_logic_combo.setCurrentIndex(idx)
            
            for cond in config.get("entry_conditions", {}).get("rules", []):
                self.add_rule_from_dict('entry', cond)
                
            self.exit_rules = []
            self.exit_table.setRowCount(0)
            exit_logic = config.get("exit_conditions", {}).get("logic", "OR")
            idx = self.exit_logic_combo.findText(exit_logic)
            if idx >= 0: self.exit_logic_combo.setCurrentIndex(idx)
            
            for cond in config.get("exit_conditions", {}).get("rules", []):
                self.add_rule_from_dict('exit', cond)
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load strategy: {e}")
            
    def add_rule_from_dict(self, tab_type, cond):
        if cond['type'] == 'technical_indicator':
            text = f"{cond.get('indicator1')}({cond.get('period1')}) {cond.get('operator')} {cond.get('indicator2')}({cond.get('period2')})"
        else:
            text = f"OnChain({cond.get('metric')}) {cond.get('condition')} ({cond.get('lookback_days')} days)"
            
        if tab_type == 'entry':
            self.entry_rules.append(cond)
            row = self.entry_table.rowCount()
            self.entry_table.insertRow(row)
            self.entry_table.setItem(row, 0, QTableWidgetItem(text))
        else:
            self.exit_rules.append(cond)
            row = self.exit_table.rowCount()
            self.exit_table.insertRow(row)
            self.exit_table.setItem(row, 0, QTableWidgetItem(text))
