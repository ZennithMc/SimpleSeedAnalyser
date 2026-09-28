"""
qtui/map_drawer.py — the Layers panel as a drawer over the right edge of a map.

Closed, it is only a small tab with a chevron on the map's edge (takes no
room); open, the panel slides over the map. The window owns ONE LayersPanel
and hands it to the drawer of the page being shown (set_content), so every
page has the same layers — which are shared by the linked maps anyway.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, Qt
from PySide6.QtWidgets import QToolButton, QVBoxLayout, QWidget

from . import icons

PANEL_W = 288
MARGIN = 8
TAB_W, TAB_H = 22, 64


class _Host(QWidget):
    """The drawer surface: it swallows every mouse / wheel event so nothing
    under the panel reaches the map (no panning through it), normal cursor."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def mousePressEvent(self, e): e.accept()
    def mouseReleaseEvent(self, e): e.accept()
    def mouseMoveEvent(self, e): e.accept()
    def mouseDoubleClickEvent(self, e): e.accept()
    def wheelEvent(self, e): e.accept()
    def contextMenuEvent(self, e): e.accept()


class MapDrawer(QObject):
    def __init__(self, map_view, opened=False):
        super().__init__(map_view)
        self.map = map_view
        self.opened = opened
        self.host = _Host(map_view)
        self.host.setObjectName("DrawerHost")
        lay = QVBoxLayout(self.host); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        self.tab = QToolButton(map_view)
        self.tab.setObjectName("MapTool")
        self.tab.setFixedSize(TAB_W, TAB_H)
        self.tab.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tab.clicked.connect(self.toggle)
        self._anim = QPropertyAnimation(self.host, b"pos", self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(lambda _v: self._place_tab())
        self._anim.finished.connect(self._anim_done)
        map_view.installEventFilter(self)
        self._apply(animate=False)

    # content ---------------------------------------------------------------
    def set_content(self, w):
        lay = self.host.layout()
        if lay.indexOf(w) < 0:
            lay.addWidget(w)
        w.show()

    # geometry --------------------------------------------------------------
    def _open_pos(self):
        return QPoint(self.map.width() - PANEL_W - MARGIN, MARGIN)

    def _closed_pos(self):
        return QPoint(self.map.width() + 2, MARGIN)

    def _place_tab(self):
        x = (self.host.x() - TAB_W + 1) if self.host.isVisible() else self.map.width() - TAB_W - 4
        x = min(x, self.map.width() - TAB_W - 4)
        self.tab.move(x, max(MARGIN, (self.map.height() - TAB_H) // 2))
        self.tab.raise_()

    def _apply(self, animate=True):
        h = max(100, self.map.height() - 2 * MARGIN)
        self.host.setFixedSize(PANEL_W, h)
        icons.bind(self.tab, "chevron-right" if self.opened else "chevron-left", 16)
        self.tab.setToolTip("Hide the layers" if self.opened else
                            "Layers: structures, noises, display")
        self.map.right_inset = (PANEL_W + MARGIN) if self.opened else TAB_W + 4
        if animate and self.map.isVisible():
            self.host.show(); self.host.raise_()
            self._anim.stop()
            self._anim.setStartValue(self.host.pos() if self.host.isVisible() else self._closed_pos())
            self._anim.setEndValue(self._open_pos() if self.opened else self._closed_pos())
            self._anim.start()
        else:
            self.host.move(self._open_pos() if self.opened else self._closed_pos())
            self.host.setVisible(self.opened)
            self.host.raise_()
            self._place_tab()
        self.map.update()

    def _anim_done(self):
        self.host.setVisible(self.opened)
        self._place_tab()

    def toggle(self):
        self.opened = not self.opened
        self._apply()

    def eventFilter(self, obj, e):
        if obj is self.map and e.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._anim.stop()
            self._apply(animate=False)
        return False
