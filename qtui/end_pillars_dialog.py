"""
qtui/end_pillars_dialog.py — (06/11) enter the End's 10 obsidian pillars seen in a world
(or a video): their height order and cages → about 16 bits of the structure seed
(end_pillars.py), used by the Seedcracking tab to sort out the false seeds.

The ring is drawn seen from above, north up, as in the game: pillar 0 stands east
(x 42, z 0), then they go clockwise (towards the south).
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout,
                               QLabel, QPushButton, QVBoxLayout, QWidget)

import end_pillars as ep

from . import theme
from .widgets import Segmented, body, section_title

DIRS = ["east", "east-south-east", "south-south-east", "south-south-west", "west-south-west",
        "west", "west-north-west", "north-north-west", "north-north-east", "east-north-east"]


def bits_color(b: float) -> str:
    return theme.OK if b >= 12 else (theme.WARN if b >= 6 else theme.TEXT3)


class PillarRing(QWidget):
    """the 10 pillars seen from above; click one to select it"""
    selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(380, 380)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.sizes = [None] * 10
        self.cages = [None] * 10
        self.sel = 0
        self.hover = -1

    def _geom(self):
        r = min(self.width(), self.height()) / 2 - 48
        c = QPointF(self.width() / 2, self.height() / 2)
        return c, r

    def _pos(self, i):
        c, r = self._geom()
        x, z = ep.position(i)
        return QPointF(c.x() + (x + 0.5) / 42.5 * r, c.y() + (z + 0.5) / 42.5 * r)

    def _hit(self, p):
        best, bd = -1, 1e9
        for i in range(10):
            d = math.hypot(p.x() - self._pos(i).x(), p.y() - self._pos(i).y())
            if d < bd:
                best, bd = i, d
        return best if bd < 30 else -1

    def mouseMoveEvent(self, e):
        h = self._hit(e.position())
        if h != self.hover:
            self.hover = h
            if h >= 0:
                x, z = ep.position(h)
                self.setToolTip(f"Pillar {h + 1}: x {x}, z {z} ({DIRS[h]} of the fountain)")
            else:
                self.setToolTip("")
            self.update()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()

    def mousePressEvent(self, e):
        h = self._hit(e.position())
        if h >= 0:
            self.sel = h
            self.selected.emit(h)
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c, r = self._geom()
        # the island and the ring
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.BG2))
        p.drawEllipse(c, r + 24, r + 24)
        p.setPen(QPen(QColor(theme.LINE), 1, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, r, r)
        # the exit portal (fountain)
        p.setPen(QPen(QColor(theme.TEXT3), 1))
        p.setBrush(QColor("#2a2a2a"))
        p.drawEllipse(c, r * 0.09, r * 0.09)
        # compass
        f = QFont(self.font()); f.setPointSizeF(8.5); f.setBold(True); p.setFont(f)
        p.setPen(QColor(theme.TEXT3))
        for lab, dx, dy in (("N", 0, -1), ("S", 0, 1), ("E", 1, 0), ("W", -1, 0)):
            q = QPointF(c.x() + dx * (r + 38), c.y() + dy * (r + 38))
            p.drawText(QRectF(q.x() - 10, q.y() - 9, 20, 18), Qt.AlignmentFlag.AlignCenter, lab)
        # the pillars
        for i in range(10):
            q = self._pos(i)
            s = self.sizes[i]
            rad = 9 + (s if s is not None else 4.5) * 1.1
            known = s is not None
            col = QColor(theme.ACCENT) if known else QColor(theme.TEXT3)
            fill = QColor("#1b1426") if known else QColor(theme.BG1)
            if i == self.sel:
                p.setPen(QPen(QColor(theme.ACCENT), 3))
            elif i == self.hover:
                p.setPen(QPen(QColor(theme.TEXT), 2))
            else:
                p.setPen(QPen(col, 1.5))
            p.setBrush(fill)
            p.drawEllipse(q, rad, rad)
            cg = self.cages[i]
            if cg:                                     # iron bars: a square with bars
                p.setPen(QPen(QColor("#c9ccd1"), 1.4))
                p.setBrush(Qt.BrushStyle.NoBrush)
                b = rad + 4
                p.drawRect(QRectF(q.x() - b, q.y() - b, 2 * b, 2 * b))
                for k in (-0.5, 0, 0.5):
                    p.drawLine(QPointF(q.x() + k * b, q.y() - b), QPointF(q.x() + k * b, q.y() - b + 3))
            elif cg is False:
                p.setPen(QPen(QColor(theme.TEXT3), 1, Qt.PenStyle.DotLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(q, rad + 4, rad + 4)
            f2 = QFont(self.font()); f2.setPointSizeF(10 if known else 9); f2.setBold(known); p.setFont(f2)
            p.setPen(QColor(theme.TEXT) if known else QColor(theme.TEXT3))
            p.drawText(QRectF(q.x() - 16, q.y() - 10, 32, 20), Qt.AlignmentFlag.AlignCenter,
                       str(s + 1) if known else "?")
        p.end()


class EndPillarsDialog(QDialog):
    def __init__(self, pillars: dict | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("End pillars")
        self.setMinimumSize(760, 600)
        pillars = dict(pillars or {})
        v = QVBoxLayout(self); v.setSpacing(10)
        v.addWidget(body(
            "Enter the 10 obsidian pillars around the End's fountain, seen from above with north "
            "up (pillar 1 is due east, then they go clockwise). For each one give its height "
            "<b>rank</b> — 1 = shortest, 10 = tallest — and whether it has an iron-bar cage. "
            "Even a few pillars help: together they fix about 16 bits of the structure seed, "
            "enough to sort out false seeds from the trees.", muted=True, small=True))
        h = QHBoxLayout(); h.setSpacing(14)
        self.ring = PillarRing()
        self.ring.sizes = list((pillars.get("sizes") or [None] * 10) + [None] * 10)[:10]
        self.ring.cages = list((pillars.get("cages") or [None] * 10) + [None] * 10)[:10]
        self.ring.selected.connect(self._select)
        h.addWidget(self.ring, 1)
        side = QVBoxLayout(); side.setSpacing(8)
        self.p_title = section_title("Pillar")
        side.addWidget(self.p_title)
        self.p_info = body("", muted=True, small=True)
        side.addWidget(self.p_info)
        side.addWidget(QLabel("Height rank"))
        grid = QGridLayout(); grid.setSpacing(4)
        self.rank_btns = {}
        sel_css = (f"QPushButton:checked {{ background: {theme.ACCENT}; color: white;"
                   f" border: 1px solid {theme.ACCENT}; font-weight: 600; }}")
        for k, lab in enumerate(["?"] + [str(n) for n in range(1, 11)]):
            b = QPushButton(lab); b.setCheckable(True); b.setFixedHeight(28)
            b.setStyleSheet(sel_css)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if lab == "?":
                b.setToolTip("Unknown height  (Backspace)")
            else:
                n = int(lab)
                b.setToolTip(f"Rank {n} ({'shortest' if n == 1 else 'tallest' if n == 10 else f'{n}th from the shortest'}): "
                             f"top obsidian at y {ep.top_obsidian(n - 1)}, radius {ep.radius(n - 1)}"
                             + (" — always caged" if n - 1 in ep.CAGED else "")
                             + f"  (key {n % 10})")
            b.clicked.connect(lambda _=False, l=lab: self._set_rank(None if l == "?" else int(l) - 1))
            grid.addWidget(b, k // 6, k % 6)
            self.rank_btns[lab] = b
        side.addLayout(grid)
        side.addWidget(QLabel("Iron-bar cage"))
        self.cage = Segmented(["?", "Caged", "Open"], "?")
        self.cage.changed.connect(self._set_cage)
        side.addWidget(self.cage)
        self.warn = body("", small=True)
        self.warn.setStyleSheet(f"color: {theme.WARN};")
        side.addWidget(self.warn)
        side.addStretch(1)
        clr = QPushButton("Clear all"); clr.clicked.connect(self._clear)
        side.addWidget(clr)
        h.addLayout(side, 1)
        v.addLayout(h, 1)
        self.rot = QCheckBox("Orientation unknown — I can't tell which pillar is due east")
        self.rot.setToolTip("Tries the 10 ways the ring can be turned (keeps the order around "
                            "the ring). Costs about 3 bits.")
        self.rot.setChecked(bool(pillars.get("rot")))
        self.rot.toggled.connect(lambda *_: self._update())
        v.addWidget(self.rot)
        self.result = QLabel(); self.result.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(self.result)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._select(0)

    # ── edits ────────────────────────────────────────────────────────────────
    def _select(self, i):
        self.ring.sel = i
        x, z = ep.position(i)
        self.p_title.setText(f"PILLAR {i + 1}  ·  {DIRS[i].upper()}")
        self.p_info.setText(f"x {x}, z {z}. Keys: 1–9, 0 = rank 10, Backspace = unknown, "
                            f"C = cage, arrows = next pillar.")
        self._update()

    def _set_rank(self, s):
        i = self.ring.sel
        if s is not None:
            for j in range(10):                # a rank belongs to one pillar only
                if j != i and self.ring.sizes[j] == s:
                    self.ring.sizes[j] = None
        self.ring.sizes[i] = s
        self._update()

    def _set_cage(self, lab):
        self.ring.cages[self.ring.sel] = {"Caged": True, "Open": False}.get(lab)
        self._update()

    def _clear(self):
        self.ring.sizes = [None] * 10
        self.ring.cages = [None] * 10
        self._update()

    def keyPressEvent(self, e):
        k = e.key()
        if Qt.Key.Key_0 <= k <= Qt.Key.Key_9:
            n = k - Qt.Key.Key_0
            self._set_rank(9 if n == 0 else n - 1)
            self._select((self.ring.sel + 1) % 10)
            return
        if k in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete, Qt.Key.Key_Question):
            self._set_rank(None); return
        if k == Qt.Key.Key_C:
            cur = self.ring.cages[self.ring.sel]
            self.ring.cages[self.ring.sel] = {None: True, True: False, False: None}[cur]
            self._update(); return
        if k in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self._select((self.ring.sel + 1) % 10); return
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self._select((self.ring.sel - 1) % 10); return
        super().keyPressEvent(e)

    def _update(self):
        i = self.ring.sel
        s = self.ring.sizes[i]
        for lab, b in self.rank_btns.items():
            b.setChecked(lab == ("?" if s is None else str(s + 1)))
        self.cage.set_value({True: "Caged", False: "Open"}.get(self.ring.cages[i], "?"))
        bad = [j + 1 for j in range(10) if self.ring.sizes[j] is not None and self.ring.cages[j] is not None
               and (self.ring.sizes[j] in ep.CAGED) != bool(self.ring.cages[j])]
        n_cage = sum(1 for c in self.ring.cages if c)
        msg = []
        if bad:
            msg.append(f"Pillar{'s' * (len(bad) > 1)} {', '.join(map(str, bad))}: only ranks 2 and 3 "
                       "(the 2nd and 3rd shortest) have a cage.")
        if n_cage > 2:
            msg.append("Only 2 pillars have a cage.")
        self.warn.setText(" ".join(msg))
        self.ring.update()
        d = self.value()
        if not any(x is not None for x in d["sizes"]) and not any(x is not None for x in d["cages"]):
            self.result.setText("No pillar entered yet.")
            return
        m = ep.matching(d["sizes"], d["cages"], rotation_unknown=d["rot"])
        b = ep.bits(len(m))
        col = bits_color(b)
        if not m:
            self.result.setText(f"<span style='color:{theme.DANGER}'>No pillar layout fits — "
                                "a rank or a cage is wrong.</span>")
        else:
            self.result.setText(f"<b>{len(m):,}</b> of 65,536 pillar layouts fit  ·  "
                                f"<b style='color:{col}'>{b:.1f} bits</b> on the structure seed")

    def value(self) -> dict:
        return {"sizes": list(self.ring.sizes), "cages": list(self.ring.cages), "rot": self.rot.isChecked()}
