"""qtui/widgets.py — small reusable widgets of the design system."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (QAbstractSlider, QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider,
                               QVBoxLayout, QWidget)


def section_title(text: str) -> QLabel:
    lab = QLabel(text.upper())
    lab.setObjectName("SectionTitle")
    return lab


def hline() -> QFrame:
    f = QFrame(); f.setObjectName("HLine")
    return f


def explain_disabled(w, enabled: bool, reason: str = "") -> None:
    """Enable / disable `w`; while disabled its tooltip ends with WHY (08/10:
    a greyed control looked like a bug). The normal tooltip is kept apart."""
    base = w.property("_tip0")
    if base is None or w.isEnabled() and not w.property("_tip_reason"):
        base = w.toolTip() if not w.property("_tip_reason") else base
        w.setProperty("_tip0", base or "")
    w.setEnabled(bool(enabled))
    if enabled or not reason:
        w.setProperty("_tip_reason", "")
        w.setToolTip(base or "")
    else:
        w.setProperty("_tip_reason", reason)
        w.setToolTip(((base + "\n\n") if base else "") + "Unavailable: " + reason)


from PySide6.QtWidgets import QLayout as _QLayout


class FlowLayout(_QLayout):
    """Items left to right, wrapping to the next line (Qt's flow layout
    example). Used for the biome chips (08/10)."""

    def __init__(self, parent=None, h_spacing=6, v_spacing=6):
        super().__init__(parent)
        self._items = []
        self._h, self._v = h_spacing, v_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        from PySide6.QtCore import QRect
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        from PySide6.QtCore import QSize
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do(self, rect, test):
        from PySide6.QtCore import QPoint, QRect
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line = r.x(), r.y(), 0
        for it in self._items:
            w = it.widget()
            if w is not None and w.isHidden():
                continue
            hint = it.sizeHint()
            nx = x + hint.width() + self._h
            if nx - self._h > r.right() + 1 and line > 0:
                x = r.x(); y = y + line + self._v
                nx = x + hint.width() + self._h
                line = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x = nx
            line = max(line, hint.height())
        return y + line - rect.y() + m.bottom()


def body(text: str, muted=False, wrap=True, small=False) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("Muted" if muted else "Body")
    lab.setWordWrap(wrap)
    if small:
        lab.setStyleSheet("font-size: 8pt;")
    return lab


class Segmented(QWidget):
    """Exclusive choice shown as joined buttons (All | Up to | Only)."""
    changed = Signal(str)

    def __init__(self, options, current=None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
        self.group = QButtonGroup(self); self.group.setExclusive(True)
        self.buttons = {}
        n = len(options)
        for i, opt in enumerate(options):
            b = QPushButton(opt); b.setCheckable(True)
            b.setProperty("seg", "only" if n == 1 else "first" if i == 0 else "last" if i == n - 1 else "mid")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(b); lay.addWidget(b, 1)
            self.buttons[opt] = b
            b.clicked.connect(lambda _=False, o=opt: self.changed.emit(o))
        (self.buttons.get(current) or self.buttons[options[0]]).setChecked(True)

    def value(self) -> str:
        for k, b in self.buttons.items():
            if b.isChecked():
                return k
        return ""

    def set_value(self, v: str):
        if v in self.buttons:
            self.buttons[v].setChecked(True)


class EasySlider(QSlider):
    """Horizontal slider that is easy to hit: the whole widget height (24 px)
    is clickable and a click jumps to that value, then drags from there."""

    def __init__(self, *a, **k):
        super().__init__(Qt.Orientation.Horizontal, *a, **k)
        self.setMinimumHeight(24)
        self._dragging = False

    def _value_at(self, x):
        from PySide6.QtWidgets import QStyle, QStyleOptionSlider
        opt = QStyleOptionSlider(); self.initStyleOption(opt)
        handle = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt,
                                             QStyle.SubControl.SC_SliderHandle, self)
        span = max(1, self.width() - handle.width())
        pos = int(x - handle.width() / 2)
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(),
                                              max(0, min(span, pos)), span, opt.upsideDown)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.setSliderDown(True)
            self.setValue(self._value_at(e.position().x()))
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._dragging:
            self.setValue(self._value_at(e.position().x()))
            e.accept()
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._dragging:
            self._dragging = False
            self.setSliderDown(False)
            e.accept()
        else:
            super().mouseReleaseEvent(e)


class LabeledSlider(QWidget):
    """Label on the left, value on the right, slider below."""
    changed = Signal(float)

    def __init__(self, label, lo, hi, value, fmt="{:.0f}", scale=1.0, parent=None):
        super().__init__(parent)
        self.scale, self.fmt = scale, fmt
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(2)
        row = QHBoxLayout()
        self.lab = body(label, wrap=False)
        self.val = QLabel(); self.val.setObjectName("Muted")
        row.addWidget(self.lab); row.addStretch(1); row.addWidget(self.val)
        v.addLayout(row)
        self.slider = EasySlider()
        self.slider.setRange(int(lo / scale), int(hi / scale))
        self.slider.setValue(int(round(value / scale)))
        self.slider.valueChanged.connect(self._on)
        v.addWidget(self.slider)
        self._on(self.slider.value(), emit=False)

    def _on(self, raw, emit=True):
        val = raw * self.scale
        self.val.setText(self.fmt.format(val))
        if emit:
            self.changed.emit(val)

    def value(self) -> float:
        return self.slider.value() * self.scale



class WheelGuard(QObject):
    """App-wide: the mouse wheel never changes a slider, combo box or spin box
    (it scrolled them by accident when the user only wanted to scroll the
    panel). The wheel goes to the scroll area around them instead."""

    def eventFilter(self, obj, e):
        if e.type() != QEvent.Type.Wheel:
            return False
        from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSpinBox, QComboBox, QScrollBar
        if not isinstance(obj, QWidget) or isinstance(obj, QScrollBar):
            return False
        if not isinstance(obj, (QAbstractSlider, QComboBox, QAbstractSpinBox)):
            return False
        p = obj.parentWidget()
        while p is not None and not isinstance(p, QAbstractScrollArea):
            p = p.parentWidget()
        if p is not None:
            bar = p.verticalScrollBar()
            if bar is not None and bar.isVisible():
                QApplication.sendEvent(bar, e)
        return True                                   # the control itself never sees it


# ── keyboard shortcuts (F1) ───────────────────────────────────────────────────
SHORTCUTS = [
    ("Everywhere", [
        ("F1", "This list of shortcuts  (also Ctrl+/)"),
        ("Ctrl+R", "Random seed"),
        ("Ctrl+G", "Go to coordinates on the map"),
        ("Ctrl+Shift+V", "Paste seed + coordinates (chat line, F3+C, “X: … Z: …”)"),
        ("F11", "Fullscreen on / off"),
    ]),
    ("Map — measuring tool", [
        ("M", "Ruler on / off  (map focused; also the ruler button on the map)"),
        ("Click", "Add a point  (a drag still moves the map)"),
        ("Shift", "Straight line: 8 directions"),
        ("Double-click · Enter · right-click", "Finish the line"),
        ("Backspace", "Remove the last point"),
        ("Esc", "Drop the line being drawn · again: leave the tool"),
    ]),
    ("Map — navigation", [
        ("Drag", "Move the map"),
        ("Wheel", "Zoom on the cursor"),
        ("Double-click", "Zoom in  ·  on a stronghold: its details"),
        ("+  /  −", "Zoom in / out"),
        ("← ↑ → ↓", "Move the map"),
        ("Home", "Centre on 0, 0"),
        ("Right-click", "Copy coordinates / /tp, stronghold menu…"),
        ("Esc", "Cancel drawing an area"),
    ]),
    ("Map — noise layer  (Map, Sessions and Stronghold pages)", [
        ("Alt+B", "Biomes only (no noise)"),
        ("Alt+T", "Temperature"),
        ("Alt+H", "Humidity"),
        ("Alt+C", "Continentalness"),
        ("Alt+E", "Erosion"),
        ("Alt+W", "Weirdness"),
        ("Alt+Y", "Approx height"),
        ("Alt+Shift+Y", "Exact Y"),
        ("Alt+1 … Alt+9", "Climate noise: only that octave (0A, 0B, 1A… in the list order)"),
        ("Alt+Shift+1 … 9", "Climate noise: up to that octave"),
        ("Alt+0", "Climate noise: all octaves"),
        ("Alt+O", "Climate noise: switch Up to ↔ Only (same octave)"),
        ("Alt+G", "Octave arrows on / off (climate noises)"),
        ("Alt+S", "Structure seed view: potential structure positions, biomes ignored"),
        ("Alt+Shift+G", "Octave arrows: Gradient ↔ Lattice"),
    ]),
    ("Sessions", [
        ("H", "Hits on the map: Off → Simple → Detailed  (map focused)"),
        ("Click / slide", "Detailed legend: show / hide a condition (button held: many)"),
        ("Right-click", "Detailed legend: only this condition (again: all)"),
        ("Ctrl+N", "Add a condition"),
        ("Ctrl+D", "Duplicate the selected condition"),
        ("Ctrl+C  /  Ctrl+V", "Copy / paste conditions"),
        ("Delete", "Remove the selected conditions"),
        ("Ctrl+Z  /  Ctrl+Y", "Undo / redo a change of the conditions"),
        ("Ctrl+T", "Check the seed of the top bar: ✓ / ✗ on every condition"),
        ("Ctrl+L", "Session library (your saved sessions)"),
        ("Ctrl+S", "Save (new session → library; library entry / file → updated)"),
        ("Ctrl+O", "Open a .session file"),
        ("Double-click", "Edit a condition"),
    ]),
    ("Draw", [
        ("B  E  L  R  C", "Brush · Eraser · Line · Rectangle · Circle (click = centre)"),
        ("G  W", "Fill · Weight brush"),
        ("Outside the square", "Drag = move the map (with any tool)"),
        ("Right button", "Erase with the current tool  (weight brush: back to ×1)"),
        ("Middle drag / Space+drag", "Move the map with any tool"),
        ("[  /  ]", "Smaller / bigger brush  (also Ctrl+wheel)"),
        ("X", "Back to the previous class"),
        ("F", "Show the whole drawing"),
        ("Ctrl+Z  /  Ctrl+Y", "Undo / redo (painting, weights, grid and placement)"),
        ("Ctrl+T", "Test the seed of the top bar"),
        ("Ctrl+S", "Save the drawing to the library"),
        ("Esc", "Cancel the stroke / shape being drawn"),
    ]),
    ("Palette editor", [
        ("Ctrl+Z  /  Ctrl+Y", "Undo / redo"),
        ("Delete", "Remove the selected colour stop"),
        ("←  /  →", "Move the selected colour stop"),
    ]),
]


def _make_shortcuts_dialog():
    from PySide6.QtWidgets import QDialog, QGridLayout, QScrollArea, QVBoxLayout

    class _Dlg(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Keyboard shortcuts")
            self.resize(560, 680)
            outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
            sc = QScrollArea(); sc.setWidgetResizable(True); sc.setFrameShape(QFrame.Shape.NoFrame)
            inner = QWidget(); inner.setObjectName("Page")
            v = QVBoxLayout(inner); v.setContentsMargins(22, 18, 22, 18); v.setSpacing(6)
            for title, rows in SHORTCUTS:
                v.addSpacing(6)
                v.addWidget(section_title(title.upper()))
                g = QGridLayout(); g.setHorizontalSpacing(14); g.setVerticalSpacing(5)
                for r, (key, what) in enumerate(rows):
                    k = QLabel(key); k.setObjectName("KeyCap")
                    k.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    g.addWidget(k, r, 0, Qt.AlignmentFlag.AlignLeft)
                    g.addWidget(body(what), r, 1)
                g.setColumnMinimumWidth(0, 130); g.setColumnStretch(1, 1)
                v.addLayout(g)
            v.addStretch(1)
            sc.setWidget(inner)
            outer.addWidget(sc)

    return _Dlg


ShortcutsDialog = _make_shortcuts_dialog()


# ── decimal input that accepts "." AND "," ─────────────────────────────────
from PySide6.QtCore import QLocale as _QLocale
from PySide6.QtGui import QValidator as _QValidator
from PySide6.QtWidgets import QDoubleSpinBox as _QDSB


class DecimalSpin(_QDSB):
    """QDoubleSpinBox that takes both 99.5 and 99,5. A plain QDoubleSpinBox
    follows the system locale: on a French Windows it refused "." (28/09), and
    the octave weights / coverage could not be typed as decimals. Shows "."
    and no trailing zeros (50 %, 99.5 %, 0.125)."""

    def __init__(self, decimals=3, parent=None):
        super().__init__(parent)
        self.setLocale(_QLocale.c())
        self.setDecimals(decimals)
        self.setKeyboardTracking(True)
        self.setGroupSeparatorShown(False)

    def _clean(self, text):
        t = text
        for suf in (self.suffix(), self.prefix()):
            if suf:
                t = t.replace(suf, "")
        return t.strip().replace(",", ".").replace(" ", "")

    def validate(self, text, pos):
        t = self._clean(text)
        if t in ("", "-", "+", ".", "-.", "+."):
            return (_QValidator.State.Intermediate, text, pos)
        try:
            v = float(t)
        except ValueError:
            return (_QValidator.State.Invalid, text, pos)
        if "." in t and len(t.split(".", 1)[1]) > self.decimals():
            return (_QValidator.State.Invalid, text, pos)
        if v < self.minimum() or v > self.maximum():
            return (_QValidator.State.Intermediate, text, pos)
        return (_QValidator.State.Acceptable, text, pos)

    def valueFromText(self, text):
        try:
            return float(self._clean(text))
        except ValueError:
            return self.value()

    def fixup(self, text):
        """Out of range when the field is left: clamp to the nearest bound
        (01/10: typing 0.9 for a max of 0.5 was silently ignored)."""
        try:
            v = float(self._clean(text))
        except ValueError:
            return text
        v = min(self.maximum(), max(self.minimum(), v))
        return self.textFromValue(v)

    def textFromValue(self, v):
        s = f"{v:.{self.decimals()}f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s if s not in ("-0", "") else "0"



# ── default folder of the file dialogs ─────────────────────────────────────
def user_path(name=""):
    """name inside the folder the user last saved to / opened from, else his
    Documents. A bare file name opened the dialog in the working folder — the
    install folder when SSA starts from a shortcut (Program Files: not
    writable)."""
    import os as _os
    from PySide6.QtCore import QSettings, QStandardPaths
    d = str(QSettings("SSA", "SimpleSeedAnalyser").value("paths/last_dir", "") or "")
    if not d or not _os.path.isdir(d):
        d = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) \
            or _os.path.expanduser("~")
    return _os.path.join(d, name) if name else d


def remember_path(path):
    """Remember the folder of a file the user just chose (see user_path)."""
    import os as _os
    from PySide6.QtCore import QSettings
    if path:
        QSettings("SSA", "SimpleSeedAnalyser").setValue("paths/last_dir",
                                                        _os.path.dirname(_os.path.abspath(path)))


from PySide6.QtWidgets import QPushButton as _QPB


class AdaptiveButton(_QPB):
    """A push button that drops its text (icon only, text in the tooltip) when
    the window is too narrow for it: its minimum width is the icon only, its
    preferred width the full text. Used for the Start / Pause / Stop buttons,
    whose run bars set the minimum window width (1276 px before, 29/09)."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._full = text
        self._compact = False
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def setText(self, text):
        self._full = text
        if not self._compact:
            super().setText(text)
        self.setToolTip(text.strip() if self._compact else self.toolTip())
        self.updateGeometry()

    def text(self):
        return self._full

    def _full_width(self):
        fm = self.fontMetrics()
        icon = self.iconSize().width() + 8 if not self.icon().isNull() else 0
        return fm.horizontalAdvance(self._full) + icon + 34

    def sizeHint(self):
        s = super().sizeHint()
        s.setWidth(max(s.width(), self._full_width()))
        return s

    def minimumSizeHint(self):
        s = super().minimumSizeHint()
        s.setWidth(self.iconSize().width() + 26 if not self.icon().isNull() else 40)
        return s

    def resizeEvent(self, e):
        super().resizeEvent(e)
        compact = self.width() < self._full_width() - 2
        if compact != self._compact:
            self._compact = compact
            _QPB.setText(self, "" if compact else self._full)
            if compact:
                self.setToolTip(self._full.strip())


# ── RangeSlider (08/10): two handles, like cubiomes-viewer's height filter ───
class RangeSlider(QWidget):
    """Min / max on one bar: drag a handle, or click the bar to move the nearest
    one. `inverted` paints the kept part OUTSIDE the two handles (an "Outside
    range" test). Integer values. rangeChanged(lo, hi)."""
    rangeChanged = Signal(int, int)
    R = 7                                         # handle radius (px)

    def __init__(self, lo=-64, hi=320, vlo=None, vhi=None, parent=None):
        super().__init__(parent)
        self._min, self._max = int(lo), int(hi)
        self._lo = self._min if vlo is None else int(vlo)
        self._hi = self._max if vhi is None else int(vhi)
        self._inv = False
        self._drag = None
        self.setMinimumHeight(26)
        self.setMinimumWidth(120)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # values
    def values(self):
        return self._lo, self._hi

    def set_values(self, lo, hi, emit=False):
        lo, hi = int(lo), int(hi)
        lo = max(self._min, min(self._max, lo)); hi = max(self._min, min(self._max, hi))
        if lo > hi:
            lo, hi = hi, lo
        if (lo, hi) == (self._lo, self._hi):
            return
        self._lo, self._hi = lo, hi
        self.update()
        if emit:
            self.rangeChanged.emit(lo, hi)

    def set_inverted(self, on):
        self._inv = bool(on)
        self.update()

    # geometry
    def _x(self, v):
        w = max(1, self.width() - 2 * self.R - 2)
        return self.R + 1 + (v - self._min) * w / max(1, self._max - self._min)

    def _v(self, x):
        w = max(1, self.width() - 2 * self.R - 2)
        v = self._min + (x - self.R - 1) * (self._max - self._min) / w
        return int(round(max(self._min, min(self._max, v))))

    def paintEvent(self, _e):
        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtGui import QColor, QPainter, QPen
        from . import theme as _th
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cy = self.height() / 2
        x0, x1 = self._x(self._min), self._x(self._max)
        xl, xh = self._x(self._lo), self._x(self._hi)
        track = QColor(_th.LINE2)
        acc = QColor(_th.ACCENT)
        if not self.isEnabled():
            acc = QColor(_th.TEXT3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(x0, cy - 2, x1 - x0, 4), 2, 2)
        p.setBrush(acc)
        if self._inv:
            p.drawRoundedRect(QRectF(x0, cy - 2, max(0.0, xl - x0), 4), 2, 2)
            p.drawRoundedRect(QRectF(xh, cy - 2, max(0.0, x1 - xh), 4), 2, 2)
        else:
            p.drawRoundedRect(QRectF(xl, cy - 2, max(0.0, xh - xl), 4), 2, 2)
        for x in (xl, xh):
            p.setPen(QPen(acc, 2))
            p.setBrush(QColor(_th.BG1))
            p.drawEllipse(QPointF(x, cy), self.R - 1, self.R - 1)
        p.end()

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(e)
        x = e.position().x()
        dl, dh = abs(x - self._x(self._lo)), abs(x - self._x(self._hi))
        if dl == dh:                              # stacked handles: by direction
            self._drag = "lo" if x < self._x(self._lo) else "hi"
        else:
            self._drag = "lo" if dl < dh else "hi"
        self._move(x)
        e.accept()

    def mouseMoveEvent(self, e):
        if self._drag:
            self._move(e.position().x())
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag = None
        e.accept()

    def _move(self, x):
        v = self._v(x)
        lo, hi = self._lo, self._hi
        if self._drag == "lo":
            lo = min(v, hi)
        else:
            hi = max(v, lo)
        self.set_values(lo, hi, emit=True)
