import ccxt
import pandas as pd
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, 
    QPushButton, QLabel, QComboBox, QDateEdit, QProgressBar, QTextEdit, QMessageBox,
    QLineEdit, QStackedWidget, QWidget
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QDate
from datetime import datetime, timezone
import time

from data_layer.storage import SessionLocal
from data_layer.market_data import MarketDataManager, get_binance_exchange

class DownloadWorker(QThread):
    progress = pyqtSignal(str)
    finished_batch = pyqtSignal()
    
    def __init__(self, symbols, timeframe, start_date, end_date, source="binance"):
        super().__init__()
        self.symbols = symbols
        self.timeframe = timeframe
        self.start_date = start_date
        self.end_date = end_date
        self.source = source
        
    def run(self):
        db = SessionLocal()
        mgr = MarketDataManager(db)
        
        for i, sym in enumerate(self.symbols):
            self.progress.emit(f"=== Starting {sym} ({i+1}/{len(self.symbols)}) ===")
            
            def emit_prog(msg):
                self.progress.emit(msg)
                
            try:
                mgr.update_historical_data(
                    sym, self.timeframe, self.start_date, self.end_date, 
                    progress_callback=emit_prog, source=self.source
                )
            except Exception as e:
                self.progress.emit(f"ERROR downloading {sym}: {e}")
                
            self.progress.emit(f"=== Finished {sym} ===\n")
            
        db.close()
        self.finished_batch.emit()

class BulkDownloaderDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bulk Market Data Downloader")
        self.resize(600, 700)
        
        self.setup_ui()
        self.load_markets()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Source Source
        src_layout = QHBoxLayout()
        src_layout.addWidget(QLabel("Data Source:"))
        self.source_combo = QComboBox()
        self.source_combo.addItems(["Binance (Crypto)", "Yahoo Finance (Stocks/Forex/Crypto)"])
        self.source_combo.currentIndexChanged.connect(self.on_source_changed)
        src_layout.addWidget(self.source_combo)
        src_layout.addStretch()
        layout.addLayout(src_layout)
        
        # Filtros / Fechas
        date_layout = QHBoxLayout()
        
        date_layout.addWidget(QLabel("Timeframe:"))
        self.tf_combo = QComboBox()
        self.tf_combo.addItems(["1m", "5m", "15m", "1h", "4h", "1d"])
        self.tf_combo.setCurrentText("4h")
        date_layout.addWidget(self.tf_combo)
        
        date_layout.addWidget(QLabel("Start Date:"))
        self.start_date = QDateEdit()
        self.start_date.setCalendarPopup(True)
        self.start_date.setDate(QDate.currentDate().addYears(-1)) # Por defecto 1 año atrás
        date_layout.addWidget(self.start_date)
        
        date_layout.addWidget(QLabel("End Date:"))
        self.end_date = QDateEdit()
        self.end_date.setCalendarPopup(True)
        self.end_date.setDate(QDate.currentDate())
        date_layout.addWidget(self.end_date)
        
        layout.addLayout(date_layout)
        
        # Opciones de selección según la fuente
        self.stacked_widget = QStackedWidget()
        
        # Pagina 0: Binance (Lista)
        self.page_binance = QWidget()
        bin_layout = QVBoxLayout(self.page_binance)
        bin_layout.setContentsMargins(0, 0, 0, 0)
        
        list_btn_layout = QHBoxLayout()
        list_btn_layout.addWidget(QLabel("Select Pairs (Default: filtered by USDT):"))
        list_btn_layout.addStretch()
        
        self.btn_select_all = QPushButton("Select All")
        self.btn_select_all.clicked.connect(self.select_all)
        list_btn_layout.addWidget(self.btn_select_all)
        
        self.btn_deselect_all = QPushButton("Deselect All")
        self.btn_deselect_all.clicked.connect(self.deselect_all)
        list_btn_layout.addWidget(self.btn_deselect_all)
        
        bin_layout.addLayout(list_btn_layout)
        
        self.list_widget = QListWidget()
        bin_layout.addWidget(self.list_widget)
        self.stacked_widget.addWidget(self.page_binance)
        
        # Pagina 1: Yahoo Finance (Input Text)
        self.page_yahoo = QWidget()
        yf_layout = QVBoxLayout(self.page_yahoo)
        yf_layout.setContentsMargins(0, 0, 0, 0)
        
        yf_layout.addWidget(QLabel("Enter Symbols (comma separated, e.g. AAPL, MSFT, EURUSD=X):"))
        self.yf_input = QLineEdit()
        self.yf_input.setPlaceholderText("AAPL, TSLA, GC=F, EURUSD=X, BTC-USD")
        yf_layout.addWidget(self.yf_input)
        yf_layout.addStretch()
        self.stacked_widget.addWidget(self.page_yahoo)
        
        layout.addWidget(self.stacked_widget)
        
        # Log y Progreso
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(150)
        layout.addWidget(self.log_output)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        
        # Botón de Inicio
        self.btn_start = QPushButton("Start Bulk Download")
        self.btn_start.clicked.connect(self.start_download)
        self.btn_start.setMinimumHeight(40)
        self.btn_start.setStyleSheet("font-weight: bold; background-color: #2e7d32; color: white;")
        layout.addWidget(self.btn_start)
        
    def on_source_changed(self):
        if self.source_combo.currentIndex() == 0:
            self.stacked_widget.setCurrentIndex(0)
        else:
            self.stacked_widget.setCurrentIndex(1)
            
    def load_markets(self):
        try:
            self.log_output.append("Fetching markets from Binance...")
            exchange = get_binance_exchange({'enableRateLimit': True})
            markets = exchange.load_markets()
            
            # Filtramos por defecto los de USDT
            usdt_pairs = [symbol for symbol in markets.keys() if symbol.endswith('/USDT')]
            usdt_pairs.sort()
            
            for symbol in usdt_pairs:
                item = QListWidgetItem(symbol)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                self.list_widget.addItem(item)
                
            self.log_output.append(f"Loaded {len(usdt_pairs)} USDT pairs.\n")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not load markets: {e}")
            
    def select_all(self):
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.CheckState.Checked)
            
    def deselect_all(self):
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)
            
    def start_download(self):
        selected_symbols = []
        source_name = "binance"
        
        if self.source_combo.currentIndex() == 0:
            # Binance
            for i in range(self.list_widget.count()):
                if self.list_widget.item(i).checkState() == Qt.CheckState.Checked:
                    selected_symbols.append(self.list_widget.item(i).text())
        else:
            # Yahoo
            source_name = "yahoo"
            raw_text = self.yf_input.text()
            if raw_text.strip():
                selected_symbols = [s.strip().upper() for s in raw_text.split(",") if s.strip()]
                
        if not selected_symbols:
            QMessageBox.warning(self, "Warning", "Please provide at least one pair to download.")
            return
            
        tf = self.tf_combo.currentText()
        
        start_qdate = self.start_date.date()
        start_dt = datetime(start_qdate.year(), start_qdate.month(), start_qdate.day(), tzinfo=timezone.utc)
        
        end_qdate = self.end_date.date()
        end_dt = datetime(end_qdate.year(), end_qdate.month(), end_qdate.day(), 23, 59, 59, tzinfo=timezone.utc)
        
        if start_dt > end_dt:
            QMessageBox.warning(self, "Warning", "Start Date cannot be after End Date.")
            return
        
        self.btn_start.setEnabled(False)
        self.stacked_widget.setEnabled(False)
        self.source_combo.setEnabled(False)
        self.start_date.setEnabled(False)
        self.end_date.setEnabled(False)
        self.tf_combo.setEnabled(False)
        
        self.log_output.append(f"Starting bulk download for {len(selected_symbols)} pairs from {source_name.upper()}...")
        self.progress_bar.setMaximum(len(selected_symbols))
        self.progress_bar.setValue(0)
        self.completed_count = 0
        
        # Start Worker
        self.worker = DownloadWorker(selected_symbols, tf, start_dt, end_dt, source=source_name)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_batch.connect(self.on_finished)
        self.worker.start()
        
    def on_progress(self, msg):
        self.log_output.append(msg)
        self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum())
        
        # Si el mensaje dice Finished, aumentamos la barra general
        if "=== Finished" in msg:
            self.completed_count += 1
            self.progress_bar.setValue(self.completed_count)
            
    def on_finished(self):
        self.btn_start.setEnabled(True)
        self.stacked_widget.setEnabled(True)
        self.source_combo.setEnabled(True)
        self.start_date.setEnabled(True)
        self.end_date.setEnabled(True)
        self.tf_combo.setEnabled(True)
        self.log_output.append("=== ALL DOWNLOADS COMPLETED ===")
        QMessageBox.information(self, "Success", "Bulk download finished.")
