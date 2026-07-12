import pyqtgraph as pg
from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QPicture, QPainter, QColor, QPen, QBrush

class CandlestickItem(pg.GraphicsObject):
    def __init__(self, data):
        super().__init__()
        self.data = data  # data debe ser lista de tuplas (tiempo_num, open, close, min, max)
        self.generatePicture()

    def generatePicture(self):
        self.picture = QPicture()
        p = QPainter(self.picture)
        
        w = (self.data[1][0] - self.data[0][0]) / 3. if len(self.data) > 1 else 0.3
        
        for (t, open, close, min, max) in self.data:
            if close > open:
                # Bullish (Verde)
                p.setPen(pg.mkPen(QColor(0, 255, 0)))
                p.setBrush(pg.mkBrush(QColor(0, 255, 0)))
            else:
                # Bearish (Rojo)
                p.setPen(pg.mkPen(QColor(255, 0, 0)))
                p.setBrush(pg.mkBrush(QColor(255, 0, 0)))
                
            # Dibuja la mecha (High - Low)
            p.drawLine(QPointF(t, min), QPointF(t, max))
            
            # Dibuja el cuerpo (Open - Close)
            rect = QRectF(t-w, open, w*2, close-open)
            p.drawRect(rect)
            
        p.end()

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return QRectF(self.picture.boundingRect())

class TimeAxisItem(pg.AxisItem):
    def __init__(self, timestamps, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timestamps = timestamps  # diccionario {indice: string_fecha}

    def tickStrings(self, values, scale, spacing):
        # Mapea el valor x (índice) a un string de fecha si existe
        return [self.timestamps.get(int(value), "") for value in values]
