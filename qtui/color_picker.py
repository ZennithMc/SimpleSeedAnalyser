"""
qtui/color_picker.py — SSA's own colour picker (22/10), instead of Qt's
QColorDialog ("basic colors": dozens of squares in no order, small fields).

  ┌───────────────────────────┬──┐  ┌─────────┐
  │                           │  │  │ new│old │
  │  saturation × brightness  │H │  └─────────┘
  │       (drag)              │u │  Hex  #4fa3e0
  │                           │e │  R G B
  └───────────────────────────┴──┘
  Swatches: 12 hues × 5 shades, ordered, + greys
  Recent:   the last colours chosen (kept between sessions)

    pick_color(parent, QColor, title, live=callable) -> QColor | None
      live(QColor) is called while the colour changes (live preview);
      Cancel calls it back with the starting colour.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

from . import theme

RECENT_KEY = "ui/recent_colors"
RECENT_N = 14


def _hsv(c: QColor):
    h, s, v, _a = c.getHsvF()
    return (max(0.0, h), s, v)


class _SVSquare(QWidget):
    """Saturation (x) × brightness (y) of the current hue."""
    changed = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(232, 180)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.h, self.s, self.v = 0.0, 1.0, 1.0
        self._img = None
        self._img_h = None

    def set_hsv(self, h, s, v):
        self.h, self.s, self.v = h, s, v
        self.update()

    def _image(self):
        if self._img is None or self._img_h != self.h:
            w, h = self.width(), self.height()
            img = QImage(w, h, QImage.Format.Format_RGB32)
            p = QPainter(img)
            base = QColor.fromHsvF(self.h, 1.0, 1.0)
            g = QLinearGradient(0, 0, w, 0)
            g.setColorAt(0, QColor(255, 255, 255)); g.setColorAt(1, base)
            p.fillRect(0, 0, w, h, g)
            g2 = QLinearGradient(0, 0, 0, h)
            g2.setColorAt(0, QColor(0, 0, 0, 0)); g2.setColorAt(1, QColor(0, 0, 0, 255))
            p.fillRect(0, 0, w, h, g2)
            p.end()
            self._img, self._img_h = img, self.h
        return self._img

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.drawImage(0, 0, self._image())
        x = self.s * (self.width() - 1)
        y = (1.0 - self.v) * (self.height() - 1)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 160), 3)); p.drawEllipse(QPointF(x, y), 6, 6)
        p.setPen(QPen(QColor(255, 255, 255), 1.6)); p.drawEllipse(QPointF(x, y), 6, 6)
        p.setPen(QPen(QColor(theme.LINE2), 1)); p.drawRect(QRectF(0, 0, self.width() - 1, self.height() - 1))

    def _pick(self, pos):
        s = min(1.0, max(0.0, pos.x() / (self.width() - 1)))
        v = 1.0 - min(1.0, max(0.0, pos.y() / (self.height() - 1)))
        self.s, self.v = s, v
        self.update()
        self.changed.emit(s, v)

    def mousePressEvent(self, e):
        self._pick(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._pick(e.position())


class _HueBar(QWidget):
    changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(20, 180)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.h = 0.0

    def set_hue(self, h):
        self.h = h
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(3, 0, self.width() - 6, self.height())
        g = QLinearGradient(0, 0, 0, self.height())
        for i in range(7):
            g.setColorAt(i / 6, QColor.fromHsvF((i / 6) % 1.0, 1.0, 1.0))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(g)
        p.drawRoundedRect(r, 4, 4)
        y = self.h * (self.height() - 1)
        p.setPen(QPen(QColor(0, 0, 0, 170), 3)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(1, y - 3, self.width() - 2, 6), 3, 3)
        p.setPen(QPen(QColor(255, 255, 255), 1.5))
        p.drawRoundedRect(QRectF(1, y - 3, self.width() - 2, 6), 3, 3)

    def _pick(self, pos):
        self.h = min(0.9999, max(0.0, pos.y() / (self.height() - 1)))
        self.update()
        self.changed.emit(self.h)

    def mousePressEvent(self, e):
        self._pick(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._pick(e.position())


class _Preview(QWidget):
    """The new colour (left) and the starting one (right; click = back to it)."""
    clicked_old = Signal()

    def __init__(self, old, parent=None):
        super().__init__(parent)
        self.setFixedSize(120, 46)
        self.old, self.new = QColor(old), QColor(old)
        self.setToolTip("Left: new colour · right: the colour before (click it to go back)")

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.new); p.drawRoundedRect(QRectF(0, 0, w * 0.6 + 6, h), 7, 7)
        p.setBrush(self.old); p.drawRoundedRect(QRectF(w * 0.6, 0, w * 0.4, h), 7, 7)
        p.fillRect(QRectF(w * 0.6, 0, 6, h), self.new)
        p.setPen(QPen(QColor(theme.LINE2), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 7, 7)

    def mousePressEvent(self, e):
        if e.position().x() > self.width() * 0.6:
            self.clicked_old.emit()


class _Swatch(QPushButton):
    def __init__(self, color, tip="", size=18, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tip or self.color.name())
        self.setStyleSheet(
            f"QPushButton {{ background: {self.color.name()}; border: 1px solid "
            f"{theme.LINE2}; border-radius: 4px; padding: 0; min-height: 0; }}"
            f"QPushButton:hover {{ border: 2px solid {theme.TEXT}; }}")


def _swatch_colors():
    """12 hues × 5 shades (light → deep), then 12 greys: ordered, not random."""
    rows = []
    for s, v in ((0.25, 1.0), (0.5, 0.97), (0.78, 0.92), (0.9, 0.72), (0.9, 0.46)):
        rows.append([QColor.fromHsvF(i / 12, s, v) for i in range(12)])
    rows.append([QColor.fromHsvF(0, 0, 1 - i / 11) for i in range(12)])
    return rows


def recent_colors():
    try:
        v = QSettings("SSA", "SimpleSeedAnalyser").value(RECENT_KEY, "") or ""
        return [QColor(x) for x in str(v).split(",") if QColor.isValidColorName(x)][:RECENT_N]
    except Exception:
        return []


def remember_color(c: QColor):
    try:
        cur = [x.name() for x in recent_colors() if x.name() != c.name()]
        QSettings("SSA", "SimpleSeedAnalyser").setValue(RECENT_KEY, ",".join([c.name()] + cur[:RECENT_N - 1]))
    except Exception:
        pass


class ColorPicker(QDialog):
    colorChanged = Signal(QColor)

    def __init__(self, color: QColor, title="Colour", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.start = QColor(color)
        self.color = QColor(color)
        self._sync = False
        v = QVBoxLayout(self); v.setContentsMargins(16, 14, 16, 12); v.setSpacing(12)

        top = QHBoxLayout(); top.setSpacing(10)
        self.sv = _SVSquare(); self.hue = _HueBar()
        top.addWidget(self.sv); top.addWidget(self.hue)
        side = QVBoxLayout(); side.setSpacing(8)
        self.prev = _Preview(color)
        self.prev.clicked_old.connect(lambda: self.set_color(self.start))
        side.addWidget(self.prev)
        hr = QHBoxLayout(); hr.setSpacing(6)
        lab = QLabel("Hex"); lab.setObjectName("Muted"); hr.addWidget(lab)
        self.hex = QLineEdit(); self.hex.setFixedWidth(84); self.hex.setFont(theme.mono_font(9.5))
        self.hex.setMaxLength(7)
        self.hex.textEdited.connect(self._hex_edited)
        hr.addWidget(self.hex); hr.addStretch(1)
        side.addLayout(hr)
        grid = QGridLayout(); grid.setHorizontalSpacing(6); grid.setVerticalSpacing(4)
        self.spins = []
        for i, nm in enumerate("RGB"):
            l = QLabel(nm); l.setObjectName("Muted")
            sp = QSpinBox(); sp.setRange(0, 255); sp.setFixedWidth(70)
            sp.valueChanged.connect(self._rgb_edited)
            grid.addWidget(l, i, 0); grid.addWidget(sp, i, 1)
            self.spins.append(sp)
        side.addLayout(grid)
        side.addStretch(1)
        top.addLayout(side)
        v.addLayout(top)

        sw = QGridLayout(); sw.setHorizontalSpacing(4); sw.setVerticalSpacing(4)
        for r, row in enumerate(_swatch_colors()):
            for c, col in enumerate(row):
                b = _Swatch(col, size=20)
                b.clicked.connect(lambda _=False, cc=col: self.set_color(cc))
                sw.addWidget(b, r, c)
        v.addLayout(sw)
        rec = recent_colors()
        if rec:
            rl = QHBoxLayout(); rl.setSpacing(4)
            t = QLabel("Recent"); t.setObjectName("Muted"); t.setFixedWidth(50); rl.addWidget(t)
            for col in rec:
                b = _Swatch(col, size=20)
                b.clicked.connect(lambda _=False, cc=col: self.set_color(cc))
                rl.addWidget(b)
            rl.addStretch(1)
            v.addLayout(rl)

        bt = QHBoxLayout(); bt.addStretch(1)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        ok = QPushButton("Select"); ok.setProperty("kind", "primary"); ok.setDefault(True)
        ok.clicked.connect(self.accept)
        bt.addWidget(cancel); bt.addWidget(ok)
        v.addLayout(bt)

        self.sv.changed.connect(self._sv_changed)
        self.hue.changed.connect(self._hue_changed)
        self.set_color(color, emit=False)

    # ── state ────────────────────────────────────────────────────────────────
    def set_color(self, c: QColor, emit=True, from_hex=False):
        c = QColor(c)
        if not c.isValid():
            return
        self.color = c
        h, s, v = _hsv(c)
        if c.saturation() == 0:                    # a grey keeps the hue shown
            h = self.hue.h
        self._sync = True
        self.sv.h = h
        self.sv.set_hsv(h, s, v)
        self.hue.set_hue(h)
        if not from_hex:
            self.hex.setText(c.name())
        for sp, val in zip(self.spins, (c.red(), c.green(), c.blue())):
            sp.setValue(val)
        self._sync = False
        self.prev.new = QColor(c); self.prev.update()
        if emit:
            self.colorChanged.emit(QColor(c))

    def _sv_changed(self, s, v):
        self.set_color(QColor.fromHsvF(self.hue.h, s, v))

    def _hue_changed(self, h):
        c = QColor.fromHsvF(h, self.sv.s, self.sv.v)
        self.set_color(c)
        self.sv.set_hsv(h, self.sv.s, self.sv.v)

    def _hex_edited(self, t):
        t = t.strip()
        if not t.startswith("#"):
            t = "#" + t
        if len(t) == 7 and QColor.isValidColorName(t):
            self.set_color(QColor(t), from_hex=True)

    def _rgb_edited(self, _v):
        if self._sync:
            return
        self.set_color(QColor(*(sp.value() for sp in self.spins)))

    def sizeHint(self):
        return QSize(430, 420)


def pick_color(parent, color, title="Colour", live=None):
    """Modal picker. live(QColor) follows the changes (undone on Cancel).
    Returns the chosen QColor or None."""
    dlg = ColorPicker(QColor(color), title, parent)
    if live is not None:
        dlg.colorChanged.connect(live)
    ok = dlg.exec() == QDialog.DialogCode.Accepted
    res = QColor(dlg.color) if ok else None
    if not ok and live is not None:
        live(QColor(color))
    if res is not None:
        remember_color(res)
    dlg.deleteLater()
    return res
