"""
qtui/mode_picker.py — (05/11) the search mode of the run bar: a button showing the
chosen mode; a click unfolds every mode, one line each (15/11: what each one does is
in its tooltip; the unavailable ones greyed, why in the tooltip). value() / set_value(label) / changed(label), like Segmented.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout)

from . import theme


class _Row(QFrame):
    """(15/11) one line per mode: its name; what it does is in the tooltip (the descriptions
    took a lot of room). An unavailable mode is greyed, why in its tooltip."""
    clicked = Signal(str)

    def __init__(self, label, desc, current, enabled, why):
        super().__init__()
        self.label, self.ok, self.current = label, enabled, current
        self.setObjectName("ModeRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ForbiddenCursor)
        self.setToolTip(desc if enabled else (why or desc))
        h = QHBoxLayout(self); h.setContentsMargins(10, 6, 12, 6); h.setSpacing(9)
        mark = QLabel("●" if current else ""); mark.setFixedWidth(10)
        mark.setStyleSheet(f"color: {theme.ACCENT}; font-size: 8pt; background: transparent;")
        h.addWidget(mark)
        t = QLabel(label)
        f = t.font(); f.setWeight(QFont.Weight.DemiBold if current else QFont.Weight.Medium); t.setFont(f)
        col = theme.TEXT if enabled else (theme.TEXT3 or theme.TEXT2)
        t.setStyleSheet(f"color: {col}; background: transparent;")
        h.addWidget(t, 1)
        self._hover(False)

    def _hover(self, on):
        a = theme.ACCENT
        if self.current:
            bg = f"background: {theme._rgba(a, 0.18)}; border: 1px solid {theme._rgba(a, 0.45)};"
        elif on and self.ok:
            bg = f"background: {theme._rgba(a, 0.10)}; border: 1px solid transparent;"
        else:
            bg = "background: transparent; border: 1px solid transparent;"
        self.setStyleSheet(f"#ModeRow {{ {bg} border-radius: 9px; }}")

    def enterEvent(self, e):
        self._hover(True)

    def leaveEvent(self, e):
        self._hover(False)

    def mouseReleaseEvent(self, e):
        if self.ok and e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.label)


class _Popup(QFrame):
    def __init__(self, picker):
        super().__init__(picker, Qt.WindowType.Popup)
        self.setObjectName("ModePopup")
        bg = theme.BG1
        if theme.is_live():                          # (15/11) SSA's theme: tinted, as the other popups
            a = theme.ACCENT
            bg = (f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {theme._mix(theme.BG2, a, 0.16)}, "
                  f"stop:1 {theme._mix(theme.BG2, a, 0.04)})")
        self.setStyleSheet(f"#ModePopup {{ background: {bg}; border: 1px solid {theme._rgba(theme.ACCENT, 0.40)}; "
                           f"border-radius: 12px; }}")
        v = QVBoxLayout(self); v.setContentsMargins(6, 6, 6, 6); v.setSpacing(2)
        for label, desc in picker.modes:
            ok, why = picker._state.get(label, (True, ""))
            r = _Row(label, desc, label == picker._value, ok, why)
            r.clicked.connect(lambda l, p=picker: (self.close(), p.set_value(l, emit=True)))
            v.addWidget(r)
        self.setFixedWidth(max(220, picker.width()))


class ModePicker(QPushButton):
    """The chosen mode, ▾; a click lists them all with their description."""
    changed = Signal(str)

    def __init__(self, modes, value, parent=None):
        super().__init__(parent)
        self.modes = list(modes)                        # [(label, description)]
        self._value = value
        self._state = {}                                # label -> (enabled, why)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(self._open)
        self._refresh()

    def value(self):
        return self._value

    def set_value(self, label, emit=False):
        if label not in dict(self.modes) or label == self._value:
            return
        self._value = label
        self._refresh()
        if emit:
            self.changed.emit(label)

    def set_available(self, label, ok, why=""):
        self._state[label] = (bool(ok), why)

    def _refresh(self):
        self.setText(f"{self._value}   ▾")
        self.setToolTip(dict(self.modes).get(self._value, ""))

    def _open(self):
        pop = _Popup(self)
        pop.adjustSize()
        g = self.mapToGlobal(QPoint(0, 0))
        y = g.y() - pop.sizeHint().height() - 6           # above: the run bar is at the bottom
        if y < 0:
            y = g.y() + self.height() + 4
        pop.move(g.x(), y)
        pop.show()
