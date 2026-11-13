"""
qtui/mode_picker.py — (05/11) the search mode of the run bar: a button showing the
chosen mode; a click unfolds every mode with a short description (the unavailable
ones greyed, with why). value() / set_value(label) / changed(label), like Segmented.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout)

from . import theme


class _Row(QFrame):
    clicked = Signal(str)

    def __init__(self, label, desc, current, enabled, why):
        super().__init__()
        self.label, self.ok = label, enabled
        self.setObjectName("ModeRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ForbiddenCursor)
        h = QHBoxLayout(self); h.setContentsMargins(10, 7, 12, 7); h.setSpacing(10)
        mark = QLabel("●" if current else ""); mark.setFixedWidth(10)
        mark.setStyleSheet(f"color: {theme.ACCENT}; font-size: 8pt;")
        h.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        v = QVBoxLayout(); v.setSpacing(1)
        t = QLabel(label)
        f = t.font(); f.setBold(True); t.setFont(f)
        d = QLabel(desc if enabled else (why or desc)); d.setWordWrap(True)
        d.setObjectName("Muted")
        v.addWidget(t); v.addWidget(d)
        h.addLayout(v, 1)
        col = theme.TEXT if enabled else (theme.TEXT3 or theme.TEXT2)
        t.setStyleSheet(f"color: {col};")
        if not enabled:
            d.setStyleSheet(f"color: {theme.TEXT3 or theme.TEXT2};")
        self._hover(False)

    def _hover(self, on):
        bg = (theme.ACCENT_SOFT or theme.BG3) if (on and self.ok) else "transparent"
        self.setStyleSheet(f"#ModeRow {{ background: {bg}; border-radius: 7px; }}")

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
        self.setStyleSheet(f"#ModePopup {{ background: {theme.BG1}; border: 1px solid {theme.LINE}; "
                           f"border-radius: 10px; }}")
        v = QVBoxLayout(self); v.setContentsMargins(6, 6, 6, 6); v.setSpacing(2)
        for label, desc in picker.modes:
            ok, why = picker._state.get(label, (True, ""))
            r = _Row(label, desc, label == picker._value, ok, why)
            r.clicked.connect(lambda l, p=picker: (self.close(), p.set_value(l, emit=True)))
            v.addWidget(r)
        self.setFixedWidth(360)


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
