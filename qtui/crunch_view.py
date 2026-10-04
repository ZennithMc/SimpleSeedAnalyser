"""
qtui/crunch_view.py — Crunchiness heatmap of the current seed (port of the
classic UI's CrunchPanel).

For every climate noise (T, H, C, E, W) and each of its first octaves, both
Perlin streams (A, B) get the fractional Y offset of their lattice (y_frac,
from the C engine: ssa_crunch_yfrac). y ≈ 0.5 = "crunchy" octave (strong 3D
interpolation artefacts), y near 0 or 1 = bland. The right column is the
weighted score of the climate (weights 35/35/11/11/4/4 per octave pair).

ONE scale everywhere, the Crunchiness condition's (29/09): score = |y − 0.5|
(weighted): 0 = crunchy, ≈ 0.25 = a random seed, 0.5 = bland. (The panel used
to show 1 − 2·score, "1 = very crunchy": the opposite of the condition.)

Click a cell: the map shows that climate, that octave only (A or B).
Click a score: the map shows that climate, all octaves.
"""
from __future__ import annotations

import ctypes as ct

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QLabel, QToolTip, QWidget

from . import theme

CLIMATES = [(0, "T", "Temperature"), (1, "H", "Humidity"), (2, "C", "Continentalness"),
            (3, "E", "Erosion"), (5, "W", "Weirdness")]
WEIGHTS = [0.35, 0.35, 0.11, 0.11, 0.04, 0.04]
MAX_OCT = 6
GOOD, MID, BAD = QColor("#2fbf71"), QColor("#d9b43a"), QColor("#d9534f")


def _lerp(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def crunch_color(score: float) -> QColor:
    """score (condition scale): 0 = crunchy (green) → 0.25 amber → 0.5 = bland (red)."""
    t = max(0.0, min(1.0, score * 2.0))
    return _lerp(GOOD, MID, t * 2) if t < 0.5 else _lerp(MID, BAD, (t - 0.5) * 2)


def tag_of(score: float):
    """score on the condition scale (0 … 0.5)."""
    if score < 0.05:
        return "very crunchy", GOOD
    if score < 0.15:
        return "crunchy", _lerp(GOOD, MID, 0.5)
    if score < 0.35:
        return "average", QColor(theme.TEXT2)
    return "bland", BAD


class CrunchView(QWidget):
    """The heatmap. pick(climate_name, pair|None, is_b) when a cell is clicked."""
    picked = Signal(str, object, bool)

    LABEL_W = 22
    HEAD_H = 16
    CELL_H = 13
    GAP = 2
    SCORE_W = 40

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.seed, self.large = None, False
        self.data = {}          # climate -> {"n": n, "y": {(pair, is_b): y}, "score": s}
        self.hover = None       # (climate_id, pair|None, is_b)
        self.current = None     # what the map shows (same shape)
        self._fn = None
        n_rows = 2 * len(CLIMATES)
        self.setFixedHeight(self.HEAD_H + n_rows * (self.CELL_H + self.GAP)
                            + (len(CLIMATES) - 1) * 5 + 4)
        self.setMinimumWidth(200)

    # ── data ────────────────────────────────────────────────────────────────
    def _lib(self):
        if self._fn is None:
            from cubiomes_wrap import CubiomesLib
            lib = CubiomesLib()._lib
            fn = lib.ssa_crunch_yfrac
            fn.argtypes = [ct.c_int64, ct.c_int, ct.c_int, ct.c_int, ct.c_int]
            fn.restype = ct.c_float
            nf = lib.ssa_crunch_n_octaves
            nf.argtypes = [ct.c_int]; nf.restype = ct.c_int
            self._fn, self._nf = fn, nf
        return self._fn, self._nf

    def set_seed(self, seed, large):
        self.seed, self.large = int(seed), bool(large)
        self.data = {}
        try:
            fn, nf = self._lib()
        except Exception:
            self.update()
            return
        s64 = ((self.seed + (1 << 63)) % (1 << 64)) - (1 << 63)
        for cid, _ab, _nm in CLIMATES:
            n = min(MAX_OCT, max(0, int(nf(cid))))
            ys = {}
            for pair in range(n):
                for is_b in (0, 1):
                    y = float(fn(s64, cid, pair, is_b, 1 if self.large else 0))
                    if y >= 0:
                        ys[(pair, bool(is_b))] = y
            num = den = 0.0
            for pair in range(n):
                vals = [abs(ys[(pair, b)] - 0.5) for b in (False, True) if (pair, b) in ys]
                if vals and pair < len(WEIGHTS):
                    num += WEIGHTS[pair] * sum(vals) / len(vals)
                    den += WEIGHTS[pair]
            score = (num / den) if den else None          # condition scale, 0 … 0.5
            self.data[cid] = dict(n=n, y=ys, score=score)
        self.update()

    # ── geometry ────────────────────────────────────────────────────────────
    def _cell_w(self):
        return (self.width() - self.LABEL_W - self.SCORE_W - 8 - MAX_OCT * self.GAP) / MAX_OCT

    def _row_y(self, ci, is_b):
        return self.HEAD_H + ci * (2 * (self.CELL_H + self.GAP) + 5) + (self.CELL_H + self.GAP) * is_b

    def _cells(self):
        cw = self._cell_w()
        for ci, (cid, abbr, name) in enumerate(CLIMATES):
            d = self.data.get(cid, {})
            for pair in range(MAX_OCT):
                for is_b in (False, True):
                    x = self.LABEL_W + pair * (cw + self.GAP)
                    yield (cid, pair, is_b), QRectF(x, self._row_y(ci, is_b), cw, self.CELL_H), \
                        d.get("y", {}).get((pair, is_b)), pair < d.get("n", MAX_OCT)
            xs = self.width() - self.SCORE_W - 2
            top = self._row_y(ci, 0)
            yield (cid, None, False), QRectF(xs, top, self.SCORE_W, 2 * self.CELL_H + self.GAP), \
                d.get("score"), True

    def _hit(self, pos):
        for key, r, _v, exists in self._cells():
            if exists and r.contains(pos):
                return key
        return None

    # ── painting ────────────────────────────────────────────────────────────
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        small = theme.ui_font(7, QFont.Weight.DemiBold)
        p.setFont(small); p.setPen(QColor(theme.TEXT3))
        cw = self._cell_w()
        for pair in range(MAX_OCT):
            p.drawText(QRectF(self.LABEL_W + pair * (cw + self.GAP), 0, cw, self.HEAD_H - 2),
                       Qt.AlignmentFlag.AlignCenter, str(pair))
        p.drawText(QRectF(self.width() - self.SCORE_W - 2, 0, self.SCORE_W, self.HEAD_H - 2),
                   Qt.AlignmentFlag.AlignCenter, "score")
        for ci, (cid, abbr, name) in enumerate(CLIMATES):
            p.setFont(theme.ui_font(8, QFont.Weight.Bold)); p.setPen(QColor(theme.TEXT2))
            top = self._row_y(ci, 0)
            p.drawText(QRectF(0, top, self.LABEL_W - 6, 2 * self.CELL_H + self.GAP),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, abbr)
        mono = theme.mono_font(7)
        for key, r, v, exists in self._cells():
            if not exists:
                continue
            is_score = key[1] is None
            if v is None:
                fill = QColor(theme.BG3)
            else:
                fill = crunch_color(v if is_score else abs(v - 0.5))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(fill)
            p.drawRoundedRect(r, 3, 3)
            if is_score and v is not None:
                p.setFont(mono); p.setPen(QColor("#10131a"))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, f"{v:.2f}")
            if key == self.current or key == self.hover:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(theme.TEXT if key == self.current else theme.TEXT2),
                              2 if key == self.current else 1))
                p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 3.5, 3.5)
        if self.seed is None:
            p.setPen(QColor(theme.TEXT3)); p.setFont(theme.ui_font(8))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "no seed")

    # ── interaction ─────────────────────────────────────────────────────────
    def describe(self, key):
        """Text for the info line under the grid."""
        if key is None:
            return None
        cid, pair, is_b = key
        name = next(n for c, _a, n in CLIMATES if c == cid)
        d = self.data.get(cid, {})
        if pair is None:
            s = d.get("score")
            if s is None:
                return f"{name}: —", QColor(theme.TEXT3)
            tag, col = tag_of(s)
            return f"{name} · score {s:.3f} · {tag}  (0 crunchy · 0.25 average · 0.5 bland)", col
        y = d.get("y", {}).get((pair, is_b))
        if y is None:
            return f"{name} {pair}{'B' if is_b else 'A'}: no data", QColor(theme.TEXT3)
        tag, col = tag_of(abs(y - 0.5))
        w = WEIGHTS[pair] if pair < len(WEIGHTS) else 0
        return (f"{name} octave {pair}{'B' if is_b else 'A'} · y = {y:.3f} · score {abs(y - 0.5):.3f} · {tag}"
                f"  (weight {w * 100:.0f} %)", col)

    def event(self, e):
        # compact tip on a cell: only its value (the whole explanation used to
        # pop up at once over the grid and hide everything, 01/10)
        if e.type() == QEvent.Type.ToolTip:
            k = self._hit(QPointF(e.pos()))
            if k is None:
                QToolTip.hideText(); e.ignore()
                return True
            cid, pair, is_b = k
            abbr = next(a for c, a, _n in CLIMATES if c == cid)
            d = self.data.get(cid, {})
            if pair is None:
                v = d.get("score")
                txt = f"{abbr} score {v:.3f}" if v is not None else f"{abbr} —"
            else:
                y = d.get("y", {}).get((pair, is_b))
                txt = (f"{abbr} {pair}{'B' if is_b else 'A'}  {abs(y - 0.5):.3f}"
                       if y is not None else "—")
            QToolTip.showText(e.globalPos(), txt, self)
            return True
        return super().event(e)

    def mouseMoveEvent(self, e):
        k = self._hit(e.position())
        if k != self.hover:
            self.hover = k
            self.setCursor(Qt.CursorShape.PointingHandCursor if k else Qt.CursorShape.ArrowCursor)
            self.update()
            self.hovered.emit()

    def leaveEvent(self, _e):
        self.hover = None
        self.update()
        self.hovered.emit()

    def mousePressEvent(self, e):
        k = self._hit(e.position())
        if k is None or e.button() != Qt.MouseButton.LeftButton:
            return
        cid, pair, is_b = k
        name = next(n for c, _a, n in CLIMATES if c == cid)
        self.picked.emit(name, pair, bool(is_b))

    hovered = Signal()


class GradientLegend(QWidget):
    """'crunchy ▬▬▬ bland' bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(16)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setFont(theme.ui_font(7.5))
        fm = p.fontMetrics()
        l, r = "0 crunchy", "0.5 bland"
        lw, rw = fm.horizontalAdvance(l), fm.horizontalAdvance(r)
        bar = QRectF(lw + 8, 5, self.width() - lw - rw - 16, 6)
        g = QLinearGradient(bar.topLeft(), bar.topRight())
        g.setColorAt(0, GOOD); g.setColorAt(0.5, MID); g.setColorAt(1, BAD)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(g); p.drawRoundedRect(bar, 3, 3)
        p.setPen(QColor(theme.TEXT3))
        p.drawText(QPointF(0, 12), l)
        p.drawText(QPointF(self.width() - rw, 12), r)
        mid = "0.25"
        mw = fm.horizontalAdvance(mid)
        p.setPen(QColor("#10131a"))
        f = theme.ui_font(6.5); p.setFont(f)
        p.drawText(QRectF(bar.center().x() - mw, bar.top() - 3, 2 * mw, bar.height() + 6),
                   Qt.AlignmentFlag.AlignCenter, mid)


class DelayedTipTitle(QLabel):
    """A section title whose explanation only appears after resting on it
    (default 2 s) — the Crunchiness tip was immediate and huge (01/10)."""

    def __init__(self, label: QLabel, tip: str, delay_ms: int = 2000):
        super().__init__(label.text())
        self.setObjectName(label.objectName())
        self._tip = tip
        self._timer = QTimer(self); self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self._show)

    def _show(self):
        if self.underMouse():
            QToolTip.showText(self.mapToGlobal(self.rect().bottomLeft()), self._tip, self)

    def enterEvent(self, e):
        self._timer.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._timer.stop()
        QToolTip.hideText()
        super().leaveEvent(e)
