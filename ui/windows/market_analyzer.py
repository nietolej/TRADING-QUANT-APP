from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QHBoxLayout, QMessageBox, QLineEdit, QLabel, QComboBox, QGroupBox, QGridLayout
)
from PyQt6.QtCore import Qt
import pandas as pd
from data_layer.storage import SessionLocal, OHLCV, OnChainMetric
from sqlalchemy import func
from data_layer.market_data import MarketDataManager
from ui.windows.bulk_downloader import BulkDownloaderDialog

class MarketAnalyzerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Market Analyzer (Data Catalog)")
        self.resize(800, 600)
        self.db = SessionLocal()
        
        self.setup_ui()
        self.load_data()
        
    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        # Grupo de Descarga
        dl_group = QGroupBox("Download Market Data")
        dl_layout = QHBoxLayout(dl_group)
        
        self.btn_bulk_dl = QPushButton("Open Bulk Data Downloader")
        self.btn_bulk_dl.setMinimumHeight(40)
        self.btn_bulk_dl.clicked.connect(self.open_bulk_downloader)
        dl_layout.addWidget(self.btn_bulk_dl)
        
        layout.addWidget(dl_group)
        
        # Botones superiores de la tabla
        btn_layout = QHBoxLayout()
        self.btn_refresh = QPushButton("Refresh List")
        self.btn_refresh.clicked.connect(self.load_data)
        
        btn_layout.addWidget(self.btn_refresh)
        layout.addLayout(btn_layout)
        
        # Tabla
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Symbol", "Type", "Timeframe/Metric", "Start Date", "End Date"])
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        
    def load_data(self):
        self.table.setRowCount(0)
        
        # Cargar Mercado
        market_query = self.db.query(
            OHLCV.symbol, 
            OHLCV.timeframe, 
            func.min(OHLCV.timestamp).label('start'),
            func.max(OHLCV.timestamp).label('end')
        ).group_by(OHLCV.symbol, OHLCV.timeframe)
        
        for row in market_query:
            r_idx = self.table.rowCount()
            self.table.insertRow(r_idx)
            self.table.setItem(r_idx, 0, QTableWidgetItem(row.symbol))
            self.table.setItem(r_idx, 1, QTableWidgetItem("Market (OHLCV)"))
            self.table.setItem(r_idx, 2, QTableWidgetItem(row.timeframe))
            self.table.setItem(r_idx, 3, QTableWidgetItem(str(row.start)))
            self.table.setItem(r_idx, 4, QTableWidgetItem(str(row.end)))
            
        # Cargar OnChain
        onchain_query = self.db.query(
            OnChainMetric.metric_name, 
            OnChainMetric.symbol, 
            func.min(OnChainMetric.timestamp).label('start'),
            func.max(OnChainMetric.timestamp).label('end')
        ).group_by(OnChainMetric.metric_name, OnChainMetric.symbol)
        
        for row in onchain_query:
            r_idx = self.table.rowCount()
            self.table.insertRow(r_idx)
            self.table.setItem(r_idx, 0, QTableWidgetItem(row.symbol))
            self.table.setItem(r_idx, 1, QTableWidgetItem("On-Chain"))
            self.table.setItem(r_idx, 2, QTableWidgetItem(row.metric_name))
            self.table.setItem(r_idx, 3, QTableWidgetItem(str(row.start)))
            self.table.setItem(r_idx, 4, QTableWidgetItem(str(row.end)))

    def open_bulk_downloader(self):
        self.bulk_dl_dialog = BulkDownloaderDialog(self)
        # When closed, refresh the data table
        self.bulk_dl_dialog.finished.connect(self.load_data)
        self.bulk_dl_dialog.show()
