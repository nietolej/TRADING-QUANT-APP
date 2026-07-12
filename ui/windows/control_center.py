import sys
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QTabWidget, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QMenuBar, QMenu
)
from PyQt6.QtCore import Qt

from ui.windows.strategy_analyzer import StrategyAnalyzerWindow
from ui.windows.strategy_builder import StrategyBuilderWindow
from ui.windows.market_analyzer import MarketAnalyzerWindow
from ui.windows.backtest_history import BacktestHistoryWindow
from ui.windows.strategy_optimizer import StrategyOptimizerWindow

class ControlCenterWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Control Center - Trading Quant App")
        self.resize(800, 600)
        
        self.setup_ui()
        self.setup_menus()
        
    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        layout = QVBoxLayout(central_widget)
        
        # Tabs estilo NT8
        self.tabs = QTabWidget()
        
        # Tab: Orders / Executions
        self.tab_orders = QWidget()
        self.setup_orders_tab(self.tab_orders)
        self.tabs.addTab(self.tab_orders, "Orders")
        
        # Tab: Positions
        self.tab_positions = QWidget()
        self.setup_positions_tab(self.tab_positions)
        self.tabs.addTab(self.tab_positions, "Positions")
        
        # Tab: Accounts
        self.tab_accounts = QWidget()
        self.setup_accounts_tab(self.tab_accounts)
        self.tabs.addTab(self.tab_accounts, "Accounts")
        
        # Tab: Log
        self.tab_log = QWidget()
        self.setup_log_tab(self.tab_log)
        self.tabs.addTab(self.tab_log, "Log")
        
        layout.addWidget(self.tabs)
        
    def setup_menus(self):
        menubar = self.menuBar()
        
        # New Menu
        new_menu = menubar.addMenu("New")
        
        action_strategy_analyzer = new_menu.addAction("Strategy Analyzer")
        action_strategy_analyzer.triggered.connect(self.open_strategy_analyzer)
        
        action_strategy_optimizer = new_menu.addAction("Strategy Optimizer (Grid Search)")
        action_strategy_optimizer.triggered.connect(self.open_strategy_optimizer)
        
        action_strategy_builder = new_menu.addAction("Strategy Builder")
        action_strategy_builder.triggered.connect(self.open_strategy_builder)
        
        action_market_analyzer = new_menu.addAction("Market Analyzer")
        action_market_analyzer.triggered.connect(self.open_market_analyzer)
        
        action_backtest_history = new_menu.addAction("Backtest History")
        action_backtest_history.triggered.connect(self.open_backtest_history)
        
        # Connections Menu
        connections_menu = menubar.addMenu("Connections")
        connections_menu.addAction("Connect to Binance Testnet")
        connections_menu.addAction("Connect to Binance Live")
        connections_menu.addAction("Disconnect")
        
    def setup_orders_tab(self, parent):
        layout = QVBoxLayout(parent)
        table = QTableWidget(0, 6)
        table.setHorizontalHeaderLabels(["Order ID", "Symbol", "Action", "Quantity", "Price", "State"])
        layout.addWidget(table)
        
    def setup_positions_tab(self, parent):
        layout = QVBoxLayout(parent)
        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["Symbol", "Position", "Avg Price", "Unrealized P&L", "Account"])
        layout.addWidget(table)
        
    def setup_accounts_tab(self, parent):
        layout = QVBoxLayout(parent)
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["Account Name", "Cash Value", "Total P&L", "Connection"])
        layout.addWidget(table)
        
    def setup_log_tab(self, parent):
        layout = QVBoxLayout(parent)
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["Time", "Category", "Message"])
        # table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        
    def open_strategy_analyzer(self):
        print("Abriendo Strategy Analyzer...")
        self.sa_window = StrategyAnalyzerWindow()
        self.sa_window.show()
        
    def open_strategy_optimizer(self):
        print("Abriendo Strategy Optimizer...")
        self.opt_window = StrategyOptimizerWindow()
        self.opt_window.show()

    def open_market_analyzer(self):
        print("Abriendo Market Analyzer...")
        self.ma_window = MarketAnalyzerWindow()
        self.ma_window.show()
        
    def open_strategy_builder(self):
        self.strategy_builder = StrategyBuilderWindow()
        self.strategy_builder.show()

    def open_backtest_history(self):
        self.backtest_history = BacktestHistoryWindow()
        self.backtest_history.show()
