"""
qtui/biome_palette.py — (14/11) the biomes of a condition as a palette, in the
form itself: one tile per biome in its map colour, grouped by family.

    pal = BiomePalette(mc, dim, incl=[1, 4], excl=None, single=False)
    pal.property("value")          the included biomes (a list; single: the id or None)
    pal.excl.property("value")     the excluded ones, when excl was given (Nether / End)
    pal.changed                    after every change by hand
    pal.searchRequested            the magnifier: the large chooser with its search

  click           include / remove a biome            (single: choose it)
  drag            paint the same state over the tiles crossed
  right click     exclude / remove (when the condition has excluded biomes)
  a family name   its biomes, all or none

The values are the ones of the chooser dialog: ids in the catalogue's order. A
list that is not touched is left exactly as it was loaded.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QToolButton, QToolTip, QVBoxLayout, QWidget

from . import icons, motion, theme

T, G, CAP, FG, RG = 24, 3, 14, 12, 8            # tile, gap, caption height, family gap, row gap


class _Value(QObject):
    """What the form reads for the excluded list: property("value")"""


class _Mosaic(QWidget):
    hovered = Signal(object)                    # biome id or None

    def __init__(self, pal):
        super().__init__()
        self.pal = pal
        self.tiles = []                         # (QRectF, bid, name, available)
        self.caps = []                          # (QRectF, family, [bid])
        self.hot = None
        self.hot_cap = None
        self._paint = None                      # the state being painted by a drag
        self._pop = {}                          # bid -> 0 … 1 while its tile pops
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _font(self):
        f = QFont(self.font())
        f.setPointSizeF(6.8)
        f.setWeight(QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.9)
        return f

    def place(self, width):
        from PySide6.QtGui import QFontMetricsF
        fm = QFontMetricsF(self._font())
        self.tiles, self.caps = [], []
        cols = max(1, int((width + G) // (T + G)))
        x = y = 0.0
        row_h = 0.0
        for fam, items in self.pal.fams:
            n = len(items)
            per = min(n, cols)
            rows = (n + per - 1) // per
            bw = max(per * T + (per - 1) * G, fm.horizontalAdvance(fam.upper()) + 2)
            bh = CAP + rows * T + (rows - 1) * G
            if x > 0 and x + bw > width:
                x, y = 0.0, y + row_h + RG
                row_h = 0.0
            self.caps.append((QRectF(x, y, bw, CAP), fam, [b for b, _n, ok in items if ok]))
            for i, (bid, name, ok) in enumerate(items):
                self.tiles.append((QRectF(x + (i % per) * (T + G), y + CAP + (i // per) * (T + G), T, T),
                                   bid, name, ok))
            x += bw + FG
            row_h = max(row_h, bh)
        return int(y + row_h + 2)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        h = self.place(self.width())
        if h != self.height():
            self.setFixedHeight(h)

    def _at(self, pos):
        for r, bid, _name, ok in self.tiles:
            if r.adjusted(-1.5, -1.5, 1.5, 1.5).contains(pos):
                return bid, ok
        return None, False

    def _cap_at(self, pos):
        for i, (r, _fam, ids) in enumerate(self.caps):
            if r.contains(pos) and ids:
                return i
        return None

    def pop(self, bid):
        if not motion.enabled():
            return

        def step(t):
            self._pop[bid] = t
            self.update()

        def done():
            self._pop.pop(bid, None)
            self.update()
        motion.run(self, 190, step, done, QEasingCurve.Type.OutBack)

    def mousePressEvent(self, e):
        pal, pos = self.pal, e.position()
        ci = self._cap_at(pos)
        if ci is not None and not pal.single and e.button() == Qt.MouseButton.LeftButton:
            ids = self.caps[ci][2]
            on = not all(pal.state.get(b) == 1 for b in ids)
            for b in ids:
                if pal.state.get(b, 0) != (1 if on else 0):
                    self.pop(b)
            pal.set_many(ids, 1 if on else 0)
            return
        bid, ok = self._at(pos)
        if bid is None:
            return
        cur = pal.state.get(bid, 0)
        if pal.single:
            if ok and e.button() == Qt.MouseButton.LeftButton:
                pal.choose(bid)
                self.pop(bid)
            return
        if e.button() == Qt.MouseButton.RightButton and pal.tri:
            new = 0 if cur == 2 else 2
        elif e.button() == Qt.MouseButton.LeftButton:
            new = 0 if cur else 1
        else:
            return
        if not ok and new:                      # not in this version: it can only be removed
            return
        self._paint = new
        pal.set_many([bid], new)
        self.pop(bid)

    def mouseMoveEvent(self, e):
        pos = e.position()
        bid, ok = self._at(pos)
        ci = self._cap_at(pos) if bid is None else None
        if bid != self.hot or ci != self.hot_cap:
            self.hot, self.hot_cap = bid, ci
            self.hovered.emit(bid)
            self.update()
        if self._paint is not None and bid is not None and (ok or not self._paint):
            if self.pal.state.get(bid, 0) != self._paint:
                self.pal.set_many([bid], self._paint)
                self.pop(bid)

    def mouseReleaseEvent(self, e):
        self._paint = None

    def leaveEvent(self, e):
        super().leaveEvent(e)
        if self.hot is not None or self.hot_cap is not None:
            self.hot = self.hot_cap = None
            self.hovered.emit(None)
            self.update()

    def event(self, e):
        if e.type() == QEvent.Type.ToolTip:
            bid, ok = self._at(e.pos())
            if bid is not None:
                name = next(n for _r, b, n, _o in self.tiles if b == bid)
                QToolTip.showText(e.globalPos(), name if ok else
                                  f"{name}\nNot generated in this Minecraft version.", self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)

    def paintEvent(self, e):
        from biome_catalog import biome_swatch_hex
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(self._font())
        state = self.pal.state
        for i, (r, fam, ids) in enumerate(self.caps):
            n = sum(1 for b in ids if state.get(b))
            c = QColor(theme.TEXT if i == self.hot_cap else theme.TEXT2 if n else theme.TEXT3)
            p.setPen(c)
            p.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, fam.upper())
        for r, bid, _name, ok in self.tiles:
            st = state.get(bid, 0)
            col = QColor(biome_swatch_hex(bid))
            hot = bid == self.hot
            if st:
                k = 0.72 + 0.28 * self._pop.get(bid, 1.0)
            else:
                k = (0.80 if hot else 0.66) + 0.14 * (1.0 - self._pop.get(bid, 1.0))
            s = T * k
            q = QRectF(r.center().x() - s / 2, r.center().y() - s / 2, s, s)
            rad = 6.0 * k
            if not st:
                col.setAlphaF(1.0 if hot else 0.80 if ok else 0.30)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(q, rad, rad)
            if st:
                ring = QColor(theme.OK if st == 1 else theme.DANGER)
                if self.pal.single or not self.pal.tri:
                    ring = QColor(theme.TEXT)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(ring, 1.6))
                p.drawRoundedRect(q.adjusted(-1.2, -1.2, 1.2, 1.2), rad + 1.2, rad + 1.2)
                lum = 0.299 * col.red() + 0.587 * col.green() + 0.114 * col.blue()
                ink = QColor("#0b0c0e" if lum > 150 else "#ffffff")
                pen = QPen(ink, 2.0)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.setPen(pen)
                cx, cy = q.center().x(), q.center().y()
                if st == 1:
                    p.drawPolyline([_pt(cx - 4.6, cy + 0.2), _pt(cx - 1.4, cy + 3.4), _pt(cx + 4.8, cy - 3.4)])
                else:
                    p.drawLine(_pt(cx - 3.8, cy - 3.8), _pt(cx + 3.8, cy + 3.8))
                    p.drawLine(_pt(cx + 3.8, cy - 3.8), _pt(cx - 3.8, cy + 3.8))
            elif hot:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(theme.TEXT2), 1.2))
                p.drawRoundedRect(q.adjusted(-1.5, -1.5, 1.5, 1.5), rad + 1.5, rad + 1.5)


def _pt(x, y):
    from PySide6.QtCore import QPointF
    return QPointF(x, y)


class BiomePalette(QWidget):
    changed = Signal()
    searchRequested = Signal()

    def __init__(self, mc, dim, incl, excl=None, single=False, label="Biomes", search=True, parent=None):
        super().__init__(parent)
        from biome_catalog import biome_display_name, dim_biomes_for
        self.mc, self.single, self.tri = mc, single, excl is not None
        self.excl = _Value(self) if self.tri else None
        listed = list(dim_biomes_for(mc, dim))
        have = {b for b, _n in listed if b is not None}
        chosen = self._ids(incl) + self._ids(excl)
        gone = [b for b in dict.fromkeys(chosen) if b not in have]
        self.fams, cur = [], None
        for bid, name in listed:
            if bid is None:
                cur = (name, [])
                self.fams.append(cur)
                continue
            if cur is None:
                cur = ("Biomes", [])
                self.fams.append(cur)
            cur[1].append((bid, name, True))
        if gone:                                # chosen before, absent from this version
            self.fams.append(("Not in this version", [(b, biome_display_name(b), False) for b in gone]))
        self.order = [b for _f, items in self.fams for b, _n, _ok in items]
        self.names = {b: n for _f, items in self.fams for b, n, _ok in items}
        self.state = {}

        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(7)
        head = QHBoxLayout(); head.setSpacing(6)
        self.title = QLabel(label); self.title.setObjectName("Body")
        head.addWidget(self.title)
        self.count = QLabel(); self.count.setObjectName("Chip"); self.count.setProperty("state", "off")
        head.addWidget(self.count)
        head.addStretch(1)
        if search and len(self.order) > 12:
            sb = QToolButton(); icons.bind(sb, "search", 15)
            sb.setToolTip("Open the large chooser (search by name or family)")
            sb.clicked.connect(self.searchRequested.emit)
            head.addWidget(sb)
        v.addLayout(head)
        if not single:                          # Cubiomes Viewer's three buttons, above the list
            acts = [("Uncheck all", 0), ("Include all", 1)] + ([("Exclude all", 2)] if self.tri else [])
            row = QHBoxLayout(); row.setSpacing(6)
            for text, st in acts:
                b = QPushButton(text); b.setProperty("kind", "ghost")
                b.setStyleSheet("padding: 1px 8px; min-height: 16px; font-size: 7.5pt;")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _=False, s=st: self.set_many(
                    [x for x in self.order if x in have or not s], s))
                row.addWidget(b)
            row.addStretch(1)
            v.addLayout(row)
        self.mosaic = _Mosaic(self)
        self.mosaic.hovered.connect(self._hover)
        v.addWidget(self.mosaic)
        self.info = QLabel(); self.info.setObjectName("Muted")
        self.info.setStyleSheet("font-size: 8pt;")
        v.addWidget(self.info)
        self.load(incl, excl, keep=True)

    @staticmethod
    def _ids(v):
        if v is None:
            return []
        if isinstance(v, (list, tuple, set)):
            return [int(b) for b in v]
        return [int(v)]

    # ── values ───────────────────────────────────────────────────────────────
    def load(self, incl, excl=None, keep=False):
        """Show these lists. keep: they are the stored ones — left as they are
        (order included) until a change by hand."""
        self.state = {b: 2 for b in self._ids(excl) if b in self.names}
        self.state.update({b: 1 for b in self._ids(incl) if b in self.names})
        if keep:
            self.setProperty("value", incl if self.single else list(incl or []))
            if self.excl is not None:
                self.excl.setProperty("value", list(excl or []))
        else:
            self._store()
        self._refresh()

    def _store(self):
        if self.single:
            cur = next((b for b in self.order if self.state.get(b) == 1), None)
            self.setProperty("value", cur)
        else:
            self.setProperty("value", [int(b) for b in self.order if self.state.get(b) == 1])
        if self.excl is not None:
            self.excl.setProperty("value", [int(b) for b in self.order if self.state.get(b) == 2])

    def set_many(self, ids, st):
        changed = False
        for b in ids:
            if self.state.get(b, 0) != st:
                changed = True
                if st:
                    self.state[b] = st
                else:
                    self.state.pop(b, None)
        if changed:
            self._store()
            self._refresh()
            self.changed.emit()

    def choose(self, bid):
        if self.state.get(bid) == 1 and len(self.state) == 1:
            return
        self.state = {bid: 1}
        self._store()
        self._refresh()
        self.changed.emit()

    def setText(self, _text):                   # the form's old button had a text: the palette shows it all
        pass

    # ── texts ────────────────────────────────────────────────────────────────
    def _refresh(self):
        inc = [b for b in self.order if self.state.get(b) == 1]
        exc = [b for b in self.order if self.state.get(b) == 2]
        if self.single:
            txt = self.names.get(inc[0], "?") if inc else "none"
        else:
            txt = f"{len(inc)} included" if inc else "none"
            if exc:
                txt += f" · {len(exc)} excluded"
        self.count.setText(txt)
        self.count.setProperty("state", "ok" if (inc or exc) else "off")
        self.count.style().unpolish(self.count); self.count.style().polish(self.count)
        self._hover(self.mosaic.hot)
        self.mosaic.update()

    def _hover(self, bid):
        if bid is not None:
            st = self.state.get(bid, 0)
            self.info.setText(self.names.get(bid, "") + ("  ·  included" if st == 1 and not self.single else
                                                         "  ·  excluded" if st == 2 else ""))
            return
        inc = [self.names[b] for b in self.order if self.state.get(b) == 1]
        if not inc or self.single:
            self.info.setText("Click a biome" if self.single else
                              "Click or drag across the biomes" + (" · right click: exclude" if self.tri else ""))
        else:
            self.info.setText(", ".join(inc[:4]) + (f"  + {len(inc) - 4}" if len(inc) > 4 else ""))
