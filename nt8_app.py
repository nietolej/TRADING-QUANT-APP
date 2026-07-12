import sys
from PyQt6.QtWidgets import QApplication
from ui.windows.control_center import ControlCenterWindow
from data_layer.storage import init_db

def main():
    # Asegurar que la base de datos esté lista
    init_db()
    
    app = QApplication(sys.argv)
    
    # Aplicar un estilo oscuro básico (similar a NT8 Dark Theme)
    app.setStyle("Fusion")
    
    window = ControlCenterWindow()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
