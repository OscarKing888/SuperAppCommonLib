"""Small reusable photo controls. Pixel algorithms and persistence live elsewhere."""
from __future__ import annotations

import math

try:
    from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal, QSignalBlocker
    from PyQt6.QtGui import QColor, QPainter, QPen, QPainterPath, QConicalGradient, QRadialGradient, QPalette
    from PyQt6.QtWidgets import QWidget, QSlider, QDoubleSpinBox, QHBoxLayout, QSizePolicy
except ImportError:
    from PyQt5.QtCore import Qt, QPointF, QRectF, pyqtSignal, QSignalBlocker
    from PyQt5.QtGui import QColor, QPainter, QPen, QPainterPath, QConicalGradient, QRadialGradient, QPalette
    from PyQt5.QtWidgets import QWidget, QSlider, QDoubleSpinBox, QHBoxLayout, QSizePolicy

_ORIENT = getattr(Qt,"Orientation",Qt)
_FOCUS = getattr(Qt,"FocusPolicy",Qt)
_BUTTON = getattr(Qt,"MouseButton",Qt)
_KEY = getattr(Qt,"Key",Qt)
_ALIGN = getattr(Qt,"AlignmentFlag",Qt)
_ROLE = getattr(QPalette,"ColorRole",QPalette)
_HINT = getattr(QPainter,"RenderHint",QPainter)


def _position(event):
    return event.position() if hasattr(event,"position") else QPointF(event.pos())


class ScalarEditor(QWidget):
    changed = pyqtSignal(float,bool)

    def __init__(self, minimum=-100, maximum=100, decimals=0, parent=None):
        super().__init__(parent)
        self._scale = 10**decimals
        self._dragging = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        self.slider = QSlider(_ORIENT.Horizontal,self)
        self.slider.setMinimumWidth(55)
        self.slider.setRange(round(minimum*self._scale),round(maximum*self._scale))
        self.spin = QDoubleSpinBox(self)
        self.spin.setDecimals(decimals)
        self.spin.setRange(minimum,maximum)
        self.spin.setSingleStep(1/self._scale)
        self.spin.setKeyboardTracking(False)
        self.spin.setMinimumWidth(66)
        layout.addWidget(self.slider,1)
        layout.addWidget(self.spin)
        self.slider.sliderPressed.connect(self._begin)
        self.slider.sliderReleased.connect(self._finish)
        self.slider.valueChanged.connect(self._slide)
        self.spin.valueChanged.connect(self._spin)

    def _begin(self):
        self._dragging = True

    def _finish(self):
        self._dragging = False
        self.changed.emit(self.value(),True)

    def _slide(self, value):
        with QSignalBlocker(self.spin):
            self.spin.setValue(value/self._scale)
        self.changed.emit(self.value(),not self._dragging)

    def _spin(self, value):
        with QSignalBlocker(self.slider):
            self.slider.setValue(round(value*self._scale))
        self.changed.emit(value,True)

    def value(self):
        return self.spin.value()

    def set_value(self, value):
        with QSignalBlocker(self.spin),QSignalBlocker(self.slider):
            self.spin.setValue(float(value))
            self.slider.setValue(round(float(value)*self._scale))


class CurveEditor(QWidget):
    changed = pyqtSignal(object,bool)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setMinimumSize(150,150)
        self.setFocusPolicy(_FOCUS.StrongFocus)
        self.setToolTip("点击添加控制点，拖动调整。方向键微调；Delete 删除内部点。最多 16 个点。")
        self.setAccessibleName("曲线控制点编辑器")
        self.points = [[0.,0.],[1.,1.]]
        self.selected = -1
        self._dragging = False

    def set_points(self, points):
        self.points = [list(p) for p in points] if points else [[0.,0.],[1.,1.]]
        self.selected = min(self.selected,len(self.points)-1)
        self.update()

    def plot_rect(self):
        return QRectF(self.rect()).adjusted(14,10,-12,-22)

    def _point(self, pair):
        box = self.plot_rect()
        return QPointF(box.left()+pair[0]*box.width(),box.bottom()-pair[1]*box.height())

    def _value(self, point):
        box = self.plot_rect()
        return [max(0.,min(1.,(point.x()-box.left())/box.width())),
                max(0.,min(1.,(box.bottom()-point.y())/box.height()))]

    def _move(self, x, y, commit):
        if self.selected<0:
            return
        index = self.selected
        lower = self.points[index-1][0]+1/255 if index else 0
        upper = self.points[index+1][0]-1/255 if index+1<len(self.points) else 1
        self.points[index] = [max(lower,min(upper,x)),max(0.,min(1.,y))]
        self.changed.emit([p[:] for p in self.points],commit)
        self.update()

    def mousePressEvent(self,event):
        if event.button()!=_BUTTON.LeftButton or not self.plot_rect().contains(_position(event)):
            return super().mousePressEvent(event)
        self.setFocus()
        point = _position(event)
        distances = [(self._point(p)-point).manhattanLength() for p in self.points]
        index = min(range(len(distances)),key=distances.__getitem__)
        if distances[index]<14:
            self.selected = index
        elif len(self.points)<16:
            value = self._value(point)
            if min(abs(p[0]-value[0]) for p in self.points)<1/255:
                return
            self.points.append(value)
            self.points.sort(key=lambda p:p[0])
            self.selected = self.points.index(value)
            self.changed.emit([p[:] for p in self.points],False)
        else:
            return
        self._dragging = True
        self.update()

    def mouseMoveEvent(self,event):
        if self._dragging:
            self._move(*self._value(_position(event)),False)

    def mouseReleaseEvent(self,event):
        if self._dragging and event.button()==_BUTTON.LeftButton:
            self._dragging = False
            self.changed.emit([p[:] for p in self.points],True)

    def keyPressEvent(self,event):
        if self.selected<0:
            self.selected = 0
        key = event.key()
        if key in (_KEY.Key_Delete,_KEY.Key_Backspace) and 0<self.selected<len(self.points)-1:
            self.points.pop(self.selected)
            self.selected = min(self.selected,len(self.points)-1)
            self.changed.emit([p[:] for p in self.points],True)
            self.update()
            return
        delta = {_KEY.Key_Left:(-1,0),_KEY.Key_Right:(1,0),_KEY.Key_Up:(0,1),_KEY.Key_Down:(0,-1)}.get(key)
        if delta:
            x,y = self.points[self.selected]
            self._move(x+delta[0]/255,y+delta[1]/255,True)
            return
        super().keyPressEvent(event)

    def paintEvent(self,event):
        painter = QPainter(self)
        painter.setRenderHint(_HINT.Antialiasing)
        palette,box = self.palette(),self.plot_rect()
        painter.fillRect(box,palette.color(_ROLE.Base))
        grid = palette.color(_ROLE.Mid)
        grid.setAlpha(100)
        painter.setPen(grid)
        for i in range(5):
            x,y = box.left()+box.width()*i/4,box.top()+box.height()*i/4
            painter.drawLine(QPointF(x,box.top()),QPointF(x,box.bottom()))
            painter.drawLine(QPointF(box.left(),y),QPointF(box.right(),y))
        painter.drawLine(box.bottomLeft(),box.topRight())
        painter.setPen(QPen(palette.color(_ROLE.Highlight),2))
        path = QPainterPath(self._point(self.points[0]))
        for point in self.points[1:]:
            path.lineTo(self._point(point))
        painter.drawPath(path)
        for index,point in enumerate(self.points):
            painter.setBrush(palette.color(_ROLE.Highlight if index==self.selected else _ROLE.Base))
            painter.drawEllipse(self._point(point),4,4)
        painter.setPen(palette.color(_ROLE.Text))
        text = "输入 → 输出"
        if self.selected>=0:
            x,y = self.points[self.selected]
            text = f"{round(x*255)} → {round(y*255)}"
        painter.drawText(QRectF(0,self.height()-20,self.width(),20),_ALIGN.AlignCenter,text)


class ColorWheel(QWidget):
    changed = pyqtSignal(float,float,bool)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.hue,self.saturation = 0.,0.
        self._dragging = False
        self.setMinimumSize(100,100)
        self.setMaximumSize(200,200)
        self.setFocusPolicy(_FOCUS.StrongFocus)
        self.setAccessibleName("色相与饱和度色轮")
        self.setToolTip("拖动选择颜色；左右键调整色相，上下键调整饱和度。")

    def set_value(self,hue,saturation):
        self.hue,self.saturation = float(hue),float(saturation)
        self.update()

    def _geometry(self):
        radius = max(1,min(self.width(),self.height())/2-7)
        return QPointF(self.width()/2,self.height()/2),radius

    def _pick(self,point,commit=False):
        center,radius = self._geometry()
        dx,dy = point.x()-center.x(),center.y()-point.y()
        self.hue = math.degrees(math.atan2(dy,dx))%360
        self.saturation = min(100.,math.hypot(dx,dy)/radius*100)
        self.changed.emit(self.hue,self.saturation,commit)
        self.update()

    def mousePressEvent(self,event):
        if event.button()==_BUTTON.LeftButton:
            self.setFocus()
            self._dragging = True
            self._pick(_position(event))

    def mouseMoveEvent(self,event):
        if self._dragging:
            self._pick(_position(event))

    def mouseReleaseEvent(self,event):
        if self._dragging and event.button()==_BUTTON.LeftButton:
            self._dragging = False
            self._pick(_position(event),True)

    def keyPressEvent(self,event):
        key = event.key()
        if key in (_KEY.Key_Left,_KEY.Key_Right):
            self.hue = (self.hue+(-1 if key==_KEY.Key_Left else 1))%360
        elif key in (_KEY.Key_Up,_KEY.Key_Down):
            self.saturation = max(0.,min(100.,self.saturation+(1 if key==_KEY.Key_Up else -1)))
        else:
            return super().keyPressEvent(event)
        self.changed.emit(self.hue,self.saturation,True)
        self.update()

    def paintEvent(self,event):
        painter = QPainter(self)
        painter.setRenderHint(_HINT.Antialiasing)
        center,radius = self._geometry()
        gradient = QConicalGradient(center,0)
        for i in range(7):
            gradient.setColorAt(i/6,QColor.fromHsv(round(i*60)%360,255,255))
        painter.setPen(QPen(self.palette().color(_ROLE.Mid),1))
        painter.setBrush(gradient)
        painter.drawEllipse(center,radius,radius)
        radial = QRadialGradient(center,radius)
        radial.setColorAt(0,QColor(255,255,255,255))
        radial.setColorAt(1,QColor(255,255,255,0))
        painter.setBrush(radial)
        painter.drawEllipse(center,radius,radius)
        angle = math.radians(self.hue)
        marker = center+QPointF(math.cos(angle),-math.sin(angle))*radius*self.saturation/100
        painter.setBrush(QColor.fromHsv(round(self.hue)%360,round(self.saturation*2.55),255))
        painter.setPen(QPen(QColor("black"),3))
        painter.drawEllipse(marker,5,5)
        painter.setPen(QPen(QColor("white"),1))
        painter.drawEllipse(marker,5,5)


class HistogramView(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.histogram = None
        self.setMinimumHeight(108)
        self.setMaximumHeight(160)
        self.setAccessibleName("RGB 直方图与溢出统计")

    def set_histogram(self, histogram):
        self.histogram = histogram
        self.update()

    def paintEvent(self,event):
        painter = QPainter(self)
        painter.setRenderHint(_HINT.Antialiasing)
        palette = self.palette()
        box = QRectF(self.rect()).adjusted(4,4,-4,-26)
        painter.fillRect(box,palette.color(_ROLE.Base))
        painter.setPen(palette.color(_ROLE.Text))
        if not self.histogram:
            painter.drawText(box,_ALIGN.AlignCenter,"等待编辑预览…")
            return
        peak = max(1,max(int(max(values)) for values in self.histogram["channels"]))
        for values,color in zip(self.histogram["channels"],("#e95c5c","#49b968","#599ae9")):
            path = QPainterPath(box.bottomLeft())
            for i,value in enumerate(values):
                path.lineTo(box.left()+i/255*box.width(),box.bottom()-float(value)/peak*box.height())
            path.lineTo(box.bottomRight())
            path.closeSubpath()
            fill = QColor(color)
            fill.setAlpha(80)
            painter.fillPath(path,fill)
            painter.setPen(QColor(color))
            painter.drawPath(path)
        count = max(1,self.histogram["samples"])
        text = f"阴影溢出 {self.histogram['shadows']/count:.1%}   高光溢出 {self.histogram['highlights']/count:.1%}"
        painter.setPen(palette.color(_ROLE.Text))
        painter.drawText(QRectF(0,self.height()-24,self.width(),24),_ALIGN.AlignCenter,text)
