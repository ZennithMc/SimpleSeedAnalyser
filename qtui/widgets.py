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
        ("Alt+G", "Octave arrows on / off (climate noises)"),
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

    def textFromValue(self, v):
        s = f"{v:.{self.decimals()}f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s if s not in ("-0", "") else "0"

    def fixup(self, text):
        return self.textFromValue(self.valueFromText(text))


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
