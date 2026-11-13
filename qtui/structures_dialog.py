"""
qtui/structures_dialog.py — (06/11) enter the structures seen in a world (type and
coordinates) for the Seedcracking tab: each one says in which chunk of its region the
structure started → ~9 bits of the structure seed (struct_crack.py).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget)

import struct_crack as stc

from . import icons, theme
from .widgets import CoordEdit, body


def _color(b, need=48.0):
    return theme.OK if b >= need else (theme.WARN if b >= need - 12 else theme.DANGER)


# column widths (header and rows)
W_X, W_Z, W_M, W_CH, W_B, W_RM = 104, 104, 118, 112, 66, 30


class _Row(QWidget):
    def __init__(self, dlg, o: stc.Obs | None):
        super().__init__()
        self.dlg = dlg
        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
        self.kind = QComboBox()
        for k in stc.available(dlg.mc):
            self.kind.addItem(stc.STRUCTS[k][0], k)
        self.kind.setMinimumWidth(130)
        self.x = CoordEdit(live=True, placeholder="X")
        self.z = CoordEdit(live=True, placeholder="Z")
        self.x.setFixedWidth(W_X); self.z.setFixedWidth(W_Z)
        self.unsure = QComboBox(); self.unsure.setFixedWidth(W_M)
        for lab, r in (("exact chunk", 0), ("± 1 chunk", 1), ("± 2 chunks", 2)):
            self.unsure.addItem(lab, r)
        self.unsure.setToolTip("Which chunks the structure may start in, around the one of the "
                               "coordinates: exact (the temples' north-west corner), ± 1 (the "
                               "middle of the other structures: costs ~3 bits), ± 2 (~4.6 bits).\n"
                               "Too narrow loses the seed; too wide only costs a few bits.")
        self.chunk = QLabel(); self.chunk.setObjectName("Muted"); self.chunk.setFixedWidth(W_CH)
        self.chunk.setToolTip("The chunk of these coordinates (block ÷ 16, rounded down).")
        self.bits = QLabel(); self.bits.setFixedWidth(W_B)
        self.bits.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.rm = QToolButton(); icons.bind(self.rm, "trash", 16); self.rm.setToolTip("Remove")
        self.rm.setFixedWidth(W_RM)
        self.rm.clicked.connect(lambda: dlg._remove(self))
        h.addWidget(self.kind, 1)
        for w in (self.x, self.z, self.unsure, self.chunk, self.bits, self.rm):
            h.addWidget(w)
        if o is not None:
            i = self.kind.findData(o.key)
            self.kind.setCurrentIndex(max(0, i))
            self.x.setValue(o.x); self.z.setValue(o.z)
            self.unsure.setCurrentIndex(max(0, min(2, o.radius)))
        else:
            self._default_radius()
        self.kind.currentIndexChanged.connect(lambda *_: (self._default_radius(), dlg._changed(self)))
        self.x.valueChanged.connect(lambda *_: dlg._changed(self))
        self.z.valueChanged.connect(lambda *_: dlg._changed(self))
        self.x.pasted.connect(self._pasted)
        self.z.pasted.connect(self._pasted)
        self.unsure.currentIndexChanged.connect(lambda *_: dlg._changed(self))
        for w in (self.kind, self.x, self.z, self.unsure):
            w.installEventFilter(dlg)

    def _pasted(self, xz):
        self.x.setValue(xz[0]); self.z.setValue(xz[1])
        self.dlg._changed(self)

    def _default_radius(self):
        k = self.kind.currentData()
        if k in stc.STRUCTS:
            self.unsure.blockSignals(True)
            self.unsure.setCurrentIndex(stc.STRUCTS[k][4])
            self.unsure.blockSignals(False)

    def obs(self) -> stc.Obs:
        return stc.Obs(self.kind.currentData() or "village", self.x.value(), self.z.value(),
                       int(self.unsure.currentData() or 0))


class StructuresDialog(QDialog):
    def __init__(self, obs, mc: int, version_label: str, parent=None):
        super().__init__(parent)
        self.mc = mc
        self.setWindowTitle("Structures")
        self.setMinimumSize(820, 560)
        v = QVBoxLayout(self); v.setSpacing(10)
        v.addWidget(body(
            f"Structures seen in the world ({version_label}). Each one is tried once per region "
            "of chunks, in a chunk the structure seed alone picks: enter where it <b>starts</b> "
            "(the block to read is shown below) — 6 to 9 bits each; ~7 single out the "
            "structure seed. Each type comes with a safe margin (exact chunk / ± 1 / ± 2): "
            "widen it if unsure — a too narrow one loses the seed. Tip: paste F3's “XYZ” line "
            "(or “x y z”) in X or Z to fill both.", muted=True, small=True))
        hdr = QHBoxLayout(); hdr.setContentsMargins(0, 0, 0, 0); hdr.setSpacing(6)
        for text, w in (("Structure", None), ("X", W_X), ("Z", W_Z), ("Margin", W_M),
                        ("Chunk", W_CH), ("Bits", W_B), ("", W_RM)):
            l = QLabel(text); l.setObjectName("FieldLabel")
            if w:
                l.setFixedWidth(w)
            if text in ("X", "Z", "Bits"):
                l.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            hdr.addWidget(l, 0 if w else 1)
        hw = QWidget(); hw.setLayout(hdr)
        hdr.setContentsMargins(0, 0, 12, 0)
        v.addWidget(hw)
        self.rows_box = QVBoxLayout(); self.rows_box.setSpacing(6); self.rows_box.setContentsMargins(0, 0, 12, 0)
        inner = QWidget(); inner.setLayout(self.rows_box)
        self.rows_box.addStretch(1)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(inner)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        v.addWidget(sc, 1)
        ar = QHBoxLayout()
        add = QPushButton("  Add structure"); icons.bind(add, "plus", 16)
        add.clicked.connect(lambda: self._add(None))
        ar.addWidget(add); ar.addStretch(1)
        v.addLayout(ar)
        self.hint = body("", muted=True, small=True)
        v.addWidget(self.hint)
        self.warn = body("", small=True)
        self.warn.setStyleSheet(f"color: {theme.DANGER};")
        v.addWidget(self.warn)
        self.result = QLabel(); self.result.setTextFormat(Qt.TextFormat.RichText); self.result.setWordWrap(True)
        v.addWidget(self.result)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.rows = []
        for o in obs or []:
            self._add(o, update=False)
        if not self.rows:
            self._add(None, update=False)
        self._show_hint(self.rows[0])
        self._update()

    def _add(self, o, update=True):
        r = _Row(self, o)
        self.rows.append(r)
        self.rows_box.insertWidget(self.rows_box.count() - 1, r)
        if update:
            self._show_hint(r)
            self._update()
            r.x.setFocus()

    def _remove(self, r):
        self.rows.remove(r)
        r.setParent(None)
        r.deleteLater()
        self._update()

    def _changed(self, r):
        self._show_hint(r)
        self._update()

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if ev.type() == QEvent.Type.FocusIn:
            for r in self.rows:
                if obj in (r.kind, r.x, r.z, r.unsure):
                    self._show_hint(r)
        return False

    def _show_hint(self, r):
        o = r.obs()
        cf = stc.config(o.key, self.mc)
        lab, _ck, dim, what, _r = stc.STRUCTS[o.key]
        if cf is None:
            self.hint.setText("")
            return
        _s, reg, rng, _k = cf
        cx, cz = o.x >> 4, o.z >> 4
        d = {0: "", -1: " (Nether coordinates)", 1: " (End coordinates)"}[dim]
        self.hint.setText(f"<b>{lab}</b>: read the coordinates of {what}{d}. Chunk {cx}, {cz} — "
                          f"region {cx // reg}, {cz // reg} ({reg}×{reg} chunks, the structure "
                          f"starts in its first {rng}×{rng}).")

    def _update(self):
        obs = self.value()
        tot_ok = []
        for r in self.rows:
            o = r.obs()
            b = stc.obs_bits(o, self.mc)
            ok = bool(stc.candidates(o, self.mc))
            r.bits.setText(f"<b>{b:.1f}</b>" if ok else f"<span style='color:{theme.DANGER}'>impossible</span>")
            r.chunk.setText(f"{o.x >> 4}, {o.z >> 4}")
            if ok:
                tot_ok.append(o)
        pr = [p[1] for p in stc.problems(obs, self.mc) if p[0] == "error"]
        self.warn.setText(" ".join(pr[:2]))
        if not tot_ok:
            self.result.setText("No structure yet.")
            return
        eff = stc.effective_bits(tot_ok, self.mc)
        lift = sum(stc.lift_bits(o, self.mc) for o in tot_ok)
        sec = stc.search_seconds(tot_ok, self.mc)
        import tree_crack as tc
        txt = (f"<b style='color:{_color(eff)}'>{eff:.0f} bits</b> on the structure seed "
               f"(lifting {min(lift, stc.LOWBITS):.0f}/{stc.LOWBITS} low bits)  ·  search ≈ {tc._fmt_t(sec)} "
               f"on every CPU thread")
        if eff < 48:
            txt += (f"<br>About {2 ** (48 - eff):,.0f} structure seeds will fit: add structures, "
                    "trees (Java 1.14 – 1.17) or the End pillars to sort them out.")
        if sec > tc.STRUCT_MAX_SECONDS:
            txt += ("<br><span style='color:%s'>Too long: add temples, villages (up to 1.17), "
                    "shipwrecks or outposts — they fix the 20 low bits first.</span>" % theme.WARN)
        elif eff < tc.STRUCT_MIN_BITS:
            txt += ("<br><span style='color:%s'>Under %d bits: too many seeds to list — add "
                    "structures.</span>" % (theme.WARN, tc.STRUCT_MIN_BITS))
        self.result.setText(txt)

    def value(self) -> list:
        return [r.obs() for r in self.rows]
