"""
qtui/biome_climate_dialog.py — « Biome climate requirements » (19/10): where
each 1.18+ overworld biome generates, in plain words and exact numbers.

A biome is shown as one or a few GROUPS. A group = a CLIMATE (temperature +
humidity, sometimes depth) and a list of TERRAINS (continentalness + erosion +
weirdness). A spot gets the biome when it matches one climate of a group AND
one terrain of that same group (biome_climate.grouped: exactly the game's
parameter boxes). Each value is shown with the game's own level name (Cold,
Far inland, E2, Peaks…) and its exact range ×10000, the unit of SSA's climate
conditions.

Opened from the Tools menu, or from the map (right-click, overworld 1.18+)
with the spot's values: what the spot meets is green, what it misses red.
"""
from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from . import theme

NAMES = {0: "Temperature", 1: "Humidity", 2: "Continentalness", 3: "Erosion", 4: "Depth",
         5: "Weirdness"}
OK_COL, BAD_COL = "#3fd6a0", "#e5737a"


class BiomeClimateDialog(QDialog):
    def __init__(self, mc, biome=None, point=None, where="", parent=None):
        super().__init__(parent)
        import biome_climate as bc
        from biome_catalog import biome_swatch_hex, overworld_biomes_for
        self.bc, self.mc, self.point = bc, int(mc), point
        self._swatch = biome_swatch_hex
        self.setWindowTitle("Biome climate requirements")
        self.resize(1000, 700)
        acc = theme.ACCENT or "#7c9cff"
        self.setStyleSheet(
            f"#Card {{ background: {theme.BG1}; border: 1px solid {theme.LINE2}; border-radius: 12px; }}"
            f"#Chip {{ background: {theme.BG2}; border: 1px solid {theme.LINE2}; border-radius: 8px; }}"
            f"#Chip[fit=\"yes\"] {{ border: 1px solid {OK_COL}; }}"
            f"#Chip[fit=\"no\"] {{ border: 1px solid {BAD_COL}; }}"
            f"#Row[hit=\"yes\"] {{ background: rgba(63, 214, 160, 30); border-radius: 10px; }}"
            f"#Sec {{ color: {theme.TEXT3}; font-size: 7.5pt; font-weight: 600; letter-spacing: 1px; }}"
            f"#Or {{ color: {theme.TEXT3}; font-size: 8pt; }}"
            f"#Muted {{ color: {theme.TEXT2}; }}"
            f"#Big {{ font-size: 15pt; font-weight: 600; }}"
            f"QListWidget::item:selected {{ background: {acc}; color: white; }}")

        v = QVBoxLayout(self); v.setContentsMargins(18, 14, 18, 14); v.setSpacing(10)
        h = QHBoxLayout(); h.setSpacing(14)
        # ── left: the biomes ────────────────────────────────────────────────
        left = QVBoxLayout(); left.setSpacing(6)
        self.search = QLineEdit(); self.search.setPlaceholderText("Search a biome…")
        self.search.textChanged.connect(self._filter)
        left.addWidget(self.search)
        self.list = QListWidget(); self.list.setFixedWidth(230)
        for bid, name in overworld_biomes_for(self.mc):
            if bid is None:
                it = QListWidgetItem(name.upper()); it.setFlags(Qt.ItemFlag.NoItemFlags)
                it.setForeground(QColor(theme.TEXT3))
            else:
                if not bc.raw_boxes(self.mc, bid):
                    continue
                pm = QPixmap(12, 12); pm.fill(QColor(biome_swatch_hex(bid)))
                it = QListWidgetItem(QIcon(pm), name)
                it.setData(Qt.ItemDataRole.UserRole, bid)
                if point is not None and any(bc.contains(b, point) for b in bc.boxes(self.mc, bid)):
                    f = it.font(); f.setBold(True); it.setFont(f)
                    it.setText(name + "   ✓")
                    it.setToolTip("this spot meets one of its combinations")
            self.list.addItem(it)
        self.list.currentItemChanged.connect(lambda cur, _p: self._show(cur))
        left.addWidget(self.list, 1)
        h.addLayout(left)
        # ── right: the selected biome ───────────────────────────────────────
        right = QVBoxLayout(); right.setSpacing(8)
        top = QHBoxLayout(); top.setSpacing(10)
        self.sw = QLabel(); top.addWidget(self.sw)
        self.title = QLabel(); self.title.setObjectName("Big"); top.addWidget(self.title, 1)
        right.addLayout(top)
        self.how = QLabel(); self.how.setObjectName("Muted"); self.how.setWordWrap(True)
        right.addWidget(self.how)
        if point is not None:
            right.addWidget(self._spot_strip(point, where))
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right.addWidget(self.scroll, 1)
        foot = QLabel("Erosion: E0 = the most mountainous … E6 = the flattest.   "
                      "Weirdness: the terrain shape (valleys · low · mid · high · peaks); "
                      "its sign (W > 0 / W < 0) picks the variant biomes.   "
                      "Numbers ×10 000, as in SSA's climate conditions.")
        foot.setObjectName("Or"); foot.setWordWrap(True)
        right.addWidget(foot)
        h.addLayout(right, 1)
        v.addLayout(h, 1)
        row = QHBoxLayout(); row.addStretch(1)
        ok = QPushButton("Close"); ok.setProperty("kind", "primary"); ok.clicked.connect(self.accept)
        row.addWidget(ok); v.addLayout(row)
        pick = None
        for i in range(self.list.count()):
            it = self.list.item(i)
            b = it.data(Qt.ItemDataRole.UserRole)
            if b is None:
                continue
            if pick is None or b == biome:
                pick = it
                if b == biome:
                    break
        if pick is not None:
            self.list.setCurrentItem(pick)

    # ── pieces ──────────────────────────────────────────────────────────────
    def _spot_strip(self, pt, where):
        bc = self.bc
        w = QFrame(); w.setObjectName("Card")
        lay = QHBoxLayout(w); lay.setContentsMargins(12, 8, 12, 8); lay.setSpacing(14)
        t = QLabel(f"<b>This spot</b><br><span style='color:{theme.TEXT3}'>{where}</span>")
        lay.addWidget(t)
        for k in (0, 1, 2, 3, 5):
            name = bc.level_of(k, pt[k])
            lab = QLabel(f"<span style='color:{theme.TEXT3}; font-size:8pt'>{NAMES[k]}</span><br>"
                         f"<b>{html.escape(name)}</b> <span style='font-family:monospace'>{pt[k]:+d}</span>")
            lay.addWidget(lab)
        lay.addStretch(1)
        return w

    def _chip(self, k, ivs, value=None):
        bc = self.bc
        c = QFrame(); c.setObjectName("Chip")
        if value is not None:
            c.setProperty("fit", "yes" if bc.fits(ivs, value) else "no")
        lay = QVBoxLayout(c); lay.setContentsMargins(10, 5, 10, 6); lay.setSpacing(0)
        a = QLabel(NAMES[k]); a.setStyleSheet(f"color: {theme.TEXT3}; font-size: 7.5pt;")
        b = QLabel(bc.names_list(k, ivs)); bf = b.font(); bf.setBold(True); b.setFont(bf)
        n = QLabel(bc.fmt_list(k, ivs)); n.setFont(theme.mono_font(8.5))
        n.setStyleSheet(f"color: {theme.TEXT2};")
        n.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for x in (a, b, n):
            lay.addWidget(x)
        if n.text() == "any":                   # « Any » already says it
            n.hide()
        return c

    def _or(self):
        o = QLabel("or"); o.setObjectName("Or"); o.setContentsMargins(14, 0, 0, 0)
        return o

    def _sec(self, text):
        s = QLabel(text); s.setObjectName("Sec")
        return s

    def _group_card(self, g, idx, n_groups):
        bc, pt = self.bc, self.point
        card = QFrame(); card.setObjectName("Card")
        v = QVBoxLayout(card); v.setContentsMargins(14, 10, 14, 12); v.setSpacing(6)
        if n_groups > 1:
            v.addWidget(self._sec(f"COMBINATION {idx + 1} OF {n_groups}"))
        hit = pt is not None and any(
            all(bc.fits((c[i],), pt[k]) for i, k in ((0, 0), (1, 1), (2, 4)))
            for c in g["climates"]) and any(
            all(bc.fits(t[k], pt[k]) for k in (2, 3, 5)) for t in g["terrains"])
        if hit:
            m = QLabel("✓ This spot meets this combination")
            m.setStyleSheet(f"color: {OK_COL}; font-weight: 600;")
            v.addWidget(m)
        v.addWidget(self._sec("CLIMATE" + ("  ·  ONE OF THESE" if len(g["climates"]) > 1 else "")))
        clim_ok = False
        for i, (tt, hh, dd) in enumerate(g["climates"]):
            if i:
                v.addWidget(self._or())
            r = QHBoxLayout(); r.setSpacing(8)
            vals = [(0, (tt,)), (1, (hh,))]
            if dd != (0, 10000):
                vals.append((4, (dd,)))
            for k, ivs in vals:
                r.addWidget(self._chip(k, ivs, pt[k] if pt is not None else None))
            r.addStretch(1)
            v.addLayout(r)
            if pt is not None and all(bc.fits(ivs, pt[k]) for k, ivs in vals):
                clim_ok = True
        v.addSpacing(4)
        n_t = len(g["terrains"])
        v.addWidget(self._sec("TERRAIN" + ("  ·  ANY ONE OF THESE" if n_t > 1 else "")))
        terrains = list(g["terrains"])
        if pt is not None:                       # the rows the spot meets first
            terrains.sort(key=lambda t: -sum(bc.fits(t[k], pt[k]) for k in (2, 3, 5)))
        for i, tr in enumerate(terrains):
            if i:
                v.addWidget(self._or())
            roww = QWidget(); roww.setObjectName("Row")
            r = QHBoxLayout(roww); r.setContentsMargins(4, 3, 4, 3); r.setSpacing(8)
            for k in (2, 3, 5):
                r.addWidget(self._chip(k, tr[k], pt[k] if pt is not None else None))
            r.addStretch(1)
            if pt is not None and clim_ok and all(bc.fits(tr[k], pt[k]) for k in (2, 3, 5)):
                roww.setProperty("hit", "yes")
                roww.setToolTip("this spot meets this terrain")
            v.addWidget(roww)
        return card

    # ── behaviour ───────────────────────────────────────────────────────────
    def _filter(self, txt):
        txt = txt.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            bid = it.data(Qt.ItemDataRole.UserRole)
            it.setHidden(bool(txt) and (bid is None or txt not in it.text().lower()))

    def _show(self, it):
        if it is None:
            return
        bid = it.data(Qt.ItemDataRole.UserRole)
        if bid is None:
            return
        bc, pt = self.bc, self.point
        pm = QPixmap(22, 22); pm.fill(QColor(self._swatch(bid))); self.sw.setPixmap(pm)
        self.title.setText(it.text().replace("   ✓", ""))
        self.how.setText("Outside these ranges: never. Inside: possible, not guaranteed.")
        body = QWidget(); lay = QVBoxLayout(body); lay.setContentsMargins(0, 0, 6, 0); lay.setSpacing(10)
        lay.addWidget(self._envelope_card(bid))
        # the exact combinations, folded (for the curious)
        groups = bc.grouped(self.mc, bid)
        n_rows = sum(len(g["terrains"]) * len(g["climates"]) for g in groups)
        btn = QPushButton(f"Show the exact combinations ({len(groups)} group{'s' * (len(groups) > 1)}, "
                          f"{n_rows} case{'s' * (n_rows > 1)})")
        btn.setCheckable(True); btn.setFlat(True)
        btn.setStyleSheet(f"text-align: left; color: {theme.TEXT2}; padding: 4px 2px;")
        lay.addWidget(btn)
        box = QWidget(); bl = QVBoxLayout(box); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(10)
        box.hide(); lay.addWidget(box)

        def toggle(on, groups=groups):
            if on and bl.count() == 0:
                gs = groups
                if pt is not None:
                    def score(g):
                        clim = any(bc.fits((c[0],), pt[0]) and bc.fits((c[1],), pt[1])
                                   and bc.fits((c[2],), pt[4]) for c in g["climates"])
                        terr = any(all(bc.fits(t[k], pt[k]) for k in (2, 3, 5))
                                   for t in g["terrains"])
                        return 0 if (clim and terr) else 1 if clim else 2
                    gs = sorted(groups, key=score)
                for i, g in enumerate(gs):
                    bl.addWidget(self._group_card(g, i, len(gs)))
            box.setVisible(on)
            btn.setText(("Hide" if on else "Show") + btn.text()[4:])
        btn.toggled.connect(toggle)
        lay.addStretch(1)
        self.scroll.setWidget(body)

    def _envelope_card(self, bid):
        bc, pt = self.bc, self.point
        lo, hi = bc.hull(self.mc, bid)
        card = QFrame(); card.setObjectName("Card")
        g = QGridLayout(card); g.setContentsMargins(16, 12, 16, 14)
        g.setHorizontalSpacing(14); g.setVerticalSpacing(10)
        g.setColumnStretch(3, 1)
        keys = [0, 1, 2, 3, 5] + ([4] if (lo[4], hi[4]) != (0, 10000) else [])
        all_in = True
        for r, k in enumerate(keys):
            a = QLabel(NAMES[k]); a.setStyleSheet(f"color: {theme.TEXT2};")
            a.setToolTip(bc.LEVEL_HINT.get(k, ""))
            b = QLabel(bc.hull_name(k, lo[k], hi[k])); f = b.font(); f.setBold(True); b.setFont(f)
            n = QLabel(_cv_range(lo[k], hi[k], k)); n.setFont(theme.mono_font(9))
            n.setStyleSheet(f"color: {theme.TEXT2};")
            n.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            v = None
            if pt is not None:
                v = pt[k]
                all_in &= lo[k] <= v <= hi[k]
            bar = _RangeBar(k, lo[k], hi[k], v, bc.LEVELS.get(k, []))
            g.addWidget(a, r, 0); g.addWidget(b, r, 1); g.addWidget(n, r, 2); g.addWidget(bar, r, 3)
        if pt is not None:
            real = any(bc.contains(x, pt) for x in bc.boxes(self.mc, bid))
            txt = ("✓ This spot is this biome" if real else
                   "This spot is inside every range, but another biome wins here" if all_in else
                   "✗ This spot is outside at least one range (red): impossible here")
            col = OK_COL if real else (theme.TEXT2 if all_in else BAD_COL)
            m = QLabel(txt); m.setStyleSheet(f"color: {col}; font-weight: 600;")
            g.addWidget(m, len(keys), 0, 1, 4)
        return card


def _cv_range(lo, hi, k):
    """cubiomes-viewer's notation: '-1500 – 2000', '-Inf – 1000', '-1900 – +Inf'."""
    if k == 4 and (lo, hi) == (0, 10000):
        return "0 – 10000"
    if lo == hi:
        return str(lo)
    a = "-Inf" if lo <= -10000 else str(lo)
    b = "+Inf" if hi >= 10000 else str(hi)
    return f"{a} – {b}"


class _RangeBar(QWidget):
    """The parameter's whole span, the game's level ticks, the biome's range
    filled, the spot as a marker (green inside, red outside)."""
    SPAN = {2: (-12000, 10000), 4: (0, 11500)}

    def __init__(self, k, lo, hi, v, levels):
        super().__init__()
        self.k, self.lo, self.hi, self.v, self.levels = k, lo, hi, v, levels
        self.setMinimumWidth(180); self.setFixedHeight(22)

    def paintEvent(self, _e):
        from PySide6.QtGui import QPainter, QPen
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a, b = self.SPAN.get(self.k, (-10000, 10000))
        w, h = self.width() - 8, self.height()
        X = lambda t: 4 + (min(max(t, a), b) - a) / (b - a) * w
        y0, y1 = h / 2 - 4, 8
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(theme.BG3 or "#333"))
        p.drawRoundedRect(4, y0, w, y1, 4, 4)
        acc = QColor(theme.ACCENT or "#7c9cff")
        p.setBrush(acc)
        x0, x1 = X(self.lo), X(self.hi)
        p.drawRoundedRect(x0, y0, max(3.0, x1 - x0), y1, 4, 4)
        p.setPen(QPen(QColor(theme.TEXT3 or "#888"), 1))
        for lo, _hi, _n in self.levels[1:]:
            xx = X(lo); p.drawLine(int(xx), int(y0 + y1 + 1), int(xx), int(y0 + y1 + 4))
        if self.v is not None:
            ok = self.lo <= self.v <= self.hi
            p.setPen(QPen(QColor(OK_COL if ok else BAD_COL), 3))
            xx = X(self.v); p.drawLine(int(xx), 1, int(xx), h - 1)
        p.end()
