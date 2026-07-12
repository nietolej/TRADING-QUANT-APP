import sys
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QSplitter, QTableWidget, QTableWidgetItem, QPushButton,
    QMessageBox, QCheckBox
)
from PyQt6.QtCore import Qt
import pyqtgraph as pg

from data_layer.storage import SessionLocal, BacktestRun

class BacktestHistoryWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Backtest History & Comparison")
        self.resize(1100, 700)
        self.db = SessionLocal()
        
        self.setup_ui()
        self.load_data()
        
    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        
        # Toolbar
        toolbar = QHBoxLayout()
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.load_data)
        toolbar.addWidget(self.btn_refresh)
        
        self.btn_delete = QPushButton("Delete Selected")
        self.btn_delete.clicked.connect(self.delete_selected)
        toolbar.addWidget(self.btn_delete)
        
        self.btn_compare = QPushButton("Compare Selected")
        self.btn_compare.clicked.connect(self.compare_selected)
        toolbar.addWidget(self.btn_compare)
        
        toolbar.addStretch()
        main_layout.addLayout(toolbar)
        
        # Splitter
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        
        # Table
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([
            "Compare", "Date", "Strategy", "Symbol", "TF", 
            "CAGR (%)", "Max DD (%)", "Win Rate (%)", "Profit Factor", "Trades"
        ])
        self.splitter.addWidget(self.table)
        
        # Charts Panel
        self.chart_widget = pg.GraphicsLayoutWidget()
        
        self.plot_cagr = self.chart_widget.addPlot(row=0, col=0, title="CAGR (%)")
        self.plot_dd = self.chart_widget.addPlot(row=0, col=1, title="Max Drawdown (%)")
        self.plot_wr = self.chart_widget.addPlot(row=0, col=2, title="Win Rate (%)")
        
        self.plot_cagr.showGrid(y=True)
        self.plot_dd.showGrid(y=True)
        self.plot_wr.showGrid(y=True)
        
        self.splitter.addWidget(self.chart_widget)
        self.splitter.setSizes([400, 300])
        
        main_layout.addWidget(self.splitter)
        
    def load_data(self):
        self.table.setRowCount(0)
        runs = self.db.query(BacktestRun).order_by(BacktestRun.created_at.desc()).all()
        
        for run in runs:
            row = self.table.rowCount()
            self.table.insertRow(row)
            
            # Checkbox
            chk = QCheckBox()
            # Store run_id as property
            chk.setProperty("run_id", run.run_id)
            chk_widget = QWidget()
            chk_layout = QHBoxLayout(chk_widget)
            chk_layout.addWidget(chk)
            chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk_layout.setContentsMargins(0,0,0,0)
            self.table.setCellWidget(row, 0, chk_widget)
            
            self.table.setItem(row, 1, QTableWidgetItem(run.created_at.strftime("%Y-%m-%d %H:%M") if run.created_at else ""))
            self.table.setItem(row, 2, QTableWidgetItem(run.strategy_name or ""))
            self.table.setItem(row, 3, QTableWidgetItem(run.symbol or ""))
            self.table.setItem(row, 4, QTableWidgetItem(run.timeframe or ""))
            self.table.setItem(row, 5, QTableWidgetItem(f"{run.cagr or 0:.2f}"))
            self.table.setItem(row, 6, QTableWidgetItem(f"{run.max_drawdown_pct or 0:.2f}"))
            self.table.setItem(row, 7, QTableWidgetItem(f"{run.win_rate or 0:.2f}"))
            self.table.setItem(row, 8, QTableWidgetItem(f"{run.profit_factor or 0:.2f}"))
            self.table.setItem(row, 9, QTableWidgetItem(str(run.total_trades or 0)))
            
    def get_selected_run_ids(self):
        selected_ids = []
        for row in range(self.table.rowCount()):
            chk_widget = self.table.cellWidget(row, 0)
            if chk_widget:
                chk = chk_widget.findChild(QCheckBox)
                if chk and chk.isChecked():
                    selected_ids.append(chk.property("run_id"))
        return selected_ids
        
    def delete_selected(self):
        selected_ids = self.get_selected_run_ids()
        if not selected_ids:
            QMessageBox.warning(self, "No Selection", "Please select at least one backtest to delete.")
            return
            
        confirm = QMessageBox.question(self, "Confirm Delete", f"Are you sure you want to delete {len(selected_ids)} backtests?",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if confirm == QMessageBox.StandardButton.Yes:
            try:
                self.db.query(BacktestRun).filter(BacktestRun.run_id.in_(selected_ids)).delete(synchronize_session=False)
                self.db.commit()
                self.load_data()
            except Exception as e:
                self.db.rollback()
                QMessageBox.critical(self, "Error", f"Failed to delete records: {e}")
                
    def compare_selected(self):
        selected_ids = self.get_selected_run_ids()
        if not selected_ids:
            QMessageBox.warning(self, "No Selection", "Please select at least one backtest to compare.")
            return
            
        runs = self.db.query(BacktestRun).filter(BacktestRun.run_id.in_(selected_ids)).all()
        
        self.plot_cagr.clear()
        self.plot_dd.clear()
        self.plot_wr.clear()
        
        names = []
        cagrs = []
        dds = []
        wrs = []
        
        for idx, run in enumerate(runs):
            name = f"{run.strategy_name} ({run.timeframe})"
            names.append(name)
            cagrs.append(run.cagr or 0)
            dds.append(run.max_drawdown_pct or 0)
            wrs.append(run.win_rate or 0)
            
        x = range(len(runs))
        
        bg_cagr = pg.BarGraphItem(x=x, height=cagrs, width=0.6, brush='g')
        self.plot_cagr.addItem(bg_cagr)
        
        bg_dd = pg.BarGraphItem(x=x, height=dds, width=0.6, brush='r')
        self.plot_dd.addItem(bg_dd)
        
        bg_wr = pg.BarGraphItem(x=x, height=wrs, width=0.6, brush='b')
        self.plot_wr.addItem(bg_wr)
        
        # Set X Axis ticks
        ticks = [list(zip(x, names))]
        self.plot_cagr.getAxis('bottom').setTicks(ticks)
        self.plot_dd.getAxis('bottom').setTicks(ticks)
        self.plot_wr.getAxis('bottom').setTicks(ticks)
        
    def closeEvent(self, event):
        self.db.close()
        super().closeEvent(event)
