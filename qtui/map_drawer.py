"""
qtui/map_drawer.py — the Layers panel as a drawer over the right edge of a map.

Closed, it is only a small tab with a chevron on the map's edge (takes no
room); open, the panel slides over the map. The window owns ONE LayersPanel
and hands it to the drawer of the page being shown (set_content), so every
page has the same layers — which are shared by the linked maps anyway.
"""
from __future__ import annotations

import time

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QEvent, QObject, QPoint,
                            QPropertyAnimation, Qt, QTimer)
from PySide6.QtGui import QCursor, QPainter
from PySide6.QtWidgets import (QAbstractSlider, QAbstractSpinBox, QApplication, QComboBox, QLineEdit,
                               QToolButton, QVBoxLayout, QWidget)

from . import icons

PANEL_W = 304
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


class _Snap(QWidget):
    """(15/11) The open drawer as a picture while the map moves under it: each
    frame of the map (pan, zoom, tiles arriving) repainted the 75 widgets of the
    Layers panel floating over it — a third of the frame. The picture is the
    panel itself (QWidget.grab, its glass background included), shown in its
    place; the live panel comes back as soon as the map rests or the cursor
    comes near."""

    def __init__(self, parent, drawer):
        super().__init__(parent)
        self.d = drawer
        self.pm = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.hide()

    def paintEvent(self, _e):
        if self.pm is not None:
            QPainter(self).drawPixmap(0, 0, self.pm)

    def enterEvent(self, e):
        super().enterEvent(e)
        self.d.thaw()

    def mousePressEvent(self, e): e.accept(); self.d.thaw()
    def mouseReleaseEvent(self, e): e.accept()
    def mouseMoveEvent(self, e): e.accept(); self.d.thaw()
    def mouseDoubleClickEvent(self, e): e.accept()
    def wheelEvent(self, e): e.accept(); self.d.thaw()
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
        self.snap = _Snap(map_view, self)
        self._frozen = self._pending = False
        self._last_frame = 0.0
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(260)
        self._idle.timeout.connect(self.thaw)
        map_view._frame_hook = self._on_frame
        # the panel changes (seed / version, theme, a layer): the live one comes back
        # (the next moves of the map take a new picture of it)
        self._watched = set()
        world = getattr(map_view, "world", None)
        if world is not None and hasattr(world, "changed"):
            world.changed.connect(self.thaw)
        from . import theme
        theme.signals.changed.connect(self.thaw)
        theme.signals.accent.connect(self.thaw)
        self._apply(animate=False)

    # (15/11) the panel as a picture while the map moves ---------------------
    def _on_frame(self):
        """Called by the map at each of its frames (inside its paint: nothing is
        moved here). Two frames close together = the map is moving."""
        now = time.monotonic()
        quick = now - self._last_frame < 0.15
        self._last_frame = now
        if self._frozen:
            self._idle.start()
            # the cursor came onto the picture without an Enter (a drag released over it…)
            if QApplication.mouseButtons() == Qt.MouseButton.NoButton and \
                    self.snap.rect().adjusted(-12, -12, 12, 12).contains(self.snap.mapFromGlobal(QCursor.pos())):
                QTimer.singleShot(0, self.thaw)
        elif quick and not self._pending and self.host.isVisible():
            self._pending = True
            QTimer.singleShot(0, self._freeze)

    def _may_freeze(self):
        h = self.host
        if not h.isVisible() or not self.opened or not self.map.isVisible() \
                or self._anim.state() == QAbstractAnimation.State.Running:
            return False
        if QApplication.activePopupWidget() is not None:          # a list or menu open
            return False
        g = QWidget.mouseGrabber()
        if g is not None and (g is h or h.isAncestorOf(g)):
            return False
        if h.rect().adjusted(-12, -12, 12, 12).contains(h.mapFromGlobal(QCursor.pos())):
            return False
        f = QApplication.focusWidget()                            # typing in a field of the panel
        if f is not None and h.isAncestorOf(f) and isinstance(
                f, (QAbstractSpinBox, QLineEdit, QComboBox, QAbstractSlider)):
            return False
        return True

    def _freeze(self):
        self._pending = False
        if self._frozen or not self._may_freeze():
            return
        self.snap.pm = self.host.grab()
        self.snap.setGeometry(self.host.geometry())
        self.snap.show()
        self.snap.raise_()
        self.tab.raise_()
        self._frozen = True
        self.host.move(self.map.width() + 40, self.host.y())     # out of the map: not painted
        self._idle.start()

    def thaw(self):
        if not self._frozen:
            return
        self._frozen = False
        self._idle.stop()
        self.host.move(self.snap.pos())
        self.snap.hide()
        self.snap.pm = None

    # content ---------------------------------------------------------------
    def set_content(self, w):
        self.thaw()
        if id(w) not in self._watched and hasattr(w, "changed"):
            self._watched.add(id(w))
            w.changed.connect(self.thaw)
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
        self.thaw()
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
