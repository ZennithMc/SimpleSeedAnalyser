"""
qtui/draw_canvas.py — the Draw page's map: the real map of the current seed
with the drawing painted over it in transparency, in world coordinates.

Two display modes:
  * edit:   the drawing (its classes' colours) at its place, with its slide
            zone; the tools paint directly on the map;
  * result: a found / tested seed — the drawing at the position the search
            found, each painted cell green (matches) or red (does not).
            Painting again goes back to the edit mode.

Mouse: left = tool, right = the tool's "erase" (brush / shapes / fill paint
"nothing", the weight brush resets to ×1), middle drag or Space + drag =
pan, wheel = zoom, Ctrl + wheel = brush size.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen

import draw_engine as de

from . import theme
from .map_view import MapView

PAINT_TOOLS = ("brush", "eraser", "line", "rect", "circle", "fill", "picker", "weight")
MATCH_OK = (46, 204, 113)
MATCH_BAD = (231, 76, 60)


def code_rgb(code: int):
    return tuple(int(v) for v in de.code_color(int(code)))


def grid_image(grid: np.ndarray, alpha: int = 255, lut: dict | None = None) -> QImage:
    """RGBA image, one pixel per cell, unpainted = transparent."""
    H, W = grid.shape
    rgba = np.zeros((H, W, 4), np.uint8)
    for c in np.unique(grid):
        c = int(c)
        if c == de.ANY:
            continue
        r, g, b = (lut or {}).get(c) or code_rgb(c)
        m = grid == c
        rgba[m] = (r, g, b, alpha)
    img = QImage(rgba.tobytes(), W, H, 4 * W, QImage.Format.Format_RGBA8888)
    return img.copy()


class DrawCanvas(MapView):
    MEASURE = False                       # clicks paint here: no ruler tool
    STRUCT_LEGEND = False
    cellHover = Signal(int, int)          # cell under the mouse (edit placement) or (-1, -1)
    toolUsed = Signal()                   # an edit started (the page leaves result mode)
    codePicked = Signal(int)              # eyedropper → code
    brushChanged = Signal(float)

    def __init__(self, world, model, parent=None, link=None):
        super().__init__(world, parent, link=link)
        self.model = model
        self.tool = "brush"
        self.code = de.LAND
        self.weight = 2.0
        self.radius = 1.5                  # brush radius in cells
        self.filled = True
        self.opacity = 0.5                 # map visibility: 0 = dark page + drawing, 1 = map only
        self._frame = None                 # dragging the square: dict(kind, ...)
        self._frame_hover = None
        self.show_grid = True
        self.show_weights = False
        self.mode = "edit"
        self.result = None                 # dict(seed, ox, oz, score, mm)
        self._img = None
        self._img_key = None
        self._wimg = None
        self._stroke = None                # dict(button, last=(i, j), start=(i, j), erase)
        self._pan = None
        self._space = False
        self._mouse = None
        model.changed.connect(self._model_changed)

    # ── state from the page ─────────────────────────────────────────────────
    def _model_changed(self, _kind):
        self._img_key = None
        self.update()

    def set_tool(self, tool):
        self.tool = tool
        self._img_key = None
        self.setCursor(Qt.CursorShape.OpenHandCursor if tool == "pan" else Qt.CursorShape.CrossCursor)
        self.update()

    def set_result(self, res):
        """res = None (edit mode) or dict(seed, ox, oz, score, mm)."""
        self.result = res
        self.mode = "result" if res else "edit"
        self._img_key = None
        self.update()

    def offset(self):
        if self.mode == "result" and self.result:
            return self.result["ox"], self.result["oz"]
        return 0, 0

    # ── geometry helpers ────────────────────────────────────────────────────
    def drawing_rect(self, ox=0, oz=0):
        m = self.model
        x0, z0 = m.corner(ox, oz)
        a = self.to_screen(x0, z0)
        b = self.to_screen(x0 + m.W * m.cell, z0 + m.H * m.cell)
        return QRectF(QPointF(*a), QPointF(*b))

    def cell_of(self, pos):
        wx, wz = self.to_world(pos.x(), pos.y())
        m = self.model
        x0, z0 = m.corner(*self.offset())
        return (wx - x0) / m.cell, (wz - z0) / m.cell

    def cell_px(self):
        return self.model.cell / max(1e-9, self.bpp)

    # ── painting ────────────────────────────────────────────────────────────
    def drawing_alpha(self):
        """Map slider t: 0 % = the drawing alone on dark grey, 100 % = the map
        alone; in between the map is veiled in dark grey and the drawing fades."""
        return int(round(255 * min(1.0, 1.25 * (1.0 - self.opacity))))

    def _images(self):
        m = self.model
        key = (id(m.grid), m.grid.shape, self.mode, id(self.result), m.W, m.H,
               int(self.opacity * 255), self.show_weights or self.tool == "weight")
        if key == self._img_key and self._img is not None:
            return self._img, self._wimg
        a = self.drawing_alpha()
        if self.mode == "result" and self.result is not None and self.result.get("mm") is not None:
            mm = self.result["mm"]
            rgba = np.zeros((m.H, m.W, 4), np.uint8)
            if mm.shape == (m.H, m.W):           # matches light, misses strong
                rgba[mm == 1] = (*MATCH_OK, int(a * 0.55))
                rgba[mm == 0] = (*MATCH_BAD, min(255, int(a * 1.1)))
            self._img = QImage(rgba.tobytes(), m.W, m.H, 4 * m.W, QImage.Format.Format_RGBA8888).copy()
        else:
            self._img = grid_image(m.grid, a)
        self._wimg = None
        if self.show_weights or self.tool == "weight":
            w = m.weights
            painted = m.grid != de.ANY
            rgba = np.zeros((m.H, m.W, 4), np.uint8)
            lw = np.log2(np.clip(w, 0.125, 16.0))
            up = painted & (lw > 0.01)
            dn = painted & (lw < -0.01)
            rgba[up] = (255, 190, 60, 0)
            rgba[dn] = (90, 160, 255, 0)
            rgba[..., 3] = np.where(up | dn, np.clip(60 + np.abs(lw) * 55, 0, 230), 0).astype(np.uint8)
            self._wimg = QImage(rgba.tobytes(), m.W, m.H, 4 * m.W, QImage.Format.Format_RGBA8888).copy()
        self._img_key = key
        return self._img, self._wimg

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        clip = QPainterPath(); clip.addRoundedRect(QRectF(self.rect()), 10, 10)
        p.setClipPath(clip)
        m = self.model
        ox, oz = self.offset()
        r = self.drawing_rect(ox, oz)
        img, wimg = self._images()
        veil = int(round(255 * (1.0 - self.opacity)))
        if veil > 0:                                  # dark page over the map, in the square only
            p.fillRect(r, QColor(24, 26, 32, veil))
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawImage(r, img)
        if wimg is not None:
            p.drawImage(r, wimg)
        cpx = self.cell_px()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        # grid lines (only when cells are big enough to be worth it)
        if self.show_grid and cpx >= 7:
            col = QColor(255, 255, 255, 30 if cpx < 14 else 44)
            p.setPen(QPen(col, 1))
            vis = QRectF(self.rect()).intersected(r)
            if not vis.isEmpty():
                i0 = max(0, int((vis.left() - r.left()) / cpx))
                i1 = min(m.W, int((vis.right() - r.left()) / cpx) + 1)
                j0 = max(0, int((vis.top() - r.top()) / cpx))
                j1 = min(m.H, int((vis.bottom() - r.top()) / cpx) + 1)
                for i in range(i0, i1 + 1):
                    x = r.left() + i * cpx
                    p.drawLine(QPointF(x, vis.top()), QPointF(x, vis.bottom()))
                for j in range(j0, j1 + 1):
                    y = r.top() + j * cpx
                    p.drawLine(QPointF(vis.left(), y), QPointF(vis.right(), y))
        # weight numbers when cells are large
        if (self.show_weights or self.tool == "weight") and cpx >= 22:
            p.setFont(theme.ui_font(7.5, QFont.Weight.Bold))
            vis = QRectF(self.rect()).intersected(r)
            if not vis.isEmpty():
                i0 = max(0, int((vis.left() - r.left()) / cpx)); i1 = min(m.W, int((vis.right() - r.left()) / cpx) + 1)
                j0 = max(0, int((vis.top() - r.top()) / cpx)); j1 = min(m.H, int((vis.bottom() - r.top()) / cpx) + 1)
                sub_g = m.grid[j0:j1, i0:i1]; sub_w = m.weights[j0:j1, i0:i1]
                for (jj, ii) in zip(*np.nonzero((sub_g != de.ANY) & (np.abs(sub_w - 1.0) > 1e-6))):
                    w = float(sub_w[jj, ii])
                    txt = f"×{w:g}" if w >= 1 else f"×{w:g}"
                    cr = QRectF(r.left() + (i0 + ii) * cpx, r.top() + (j0 + jj) * cpx, cpx, cpx)
                    p.setPen(QColor(0, 0, 0, 170)); p.drawText(cr.translated(1, 1), Qt.AlignmentFlag.AlignCenter, txt)
                    p.setPen(QColor(255, 255, 255)); p.drawText(cr, Qt.AlignmentFlag.AlignCenter, txt)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # slide zone: where the drawing's corner may go (edit mode)
        if self.mode == "edit" and m.slide > 0:
            sr = r.adjusted(-m.slide * cpx, -m.slide * cpx, m.slide * cpx, m.slide * cpx)
            pen = QPen(QColor(theme.TEXT2), 1.2); pen.setStyle(Qt.PenStyle.DashLine)
            pen.setDashPattern([5, 4]); p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(sr)
            p.setFont(theme.ui_font(8, QFont.Weight.DemiBold))
            lab = f"slide ±{m.slide} cells  (±{m.slide * m.cell:,} blocks)".replace(",", " ")
            self._label(p, QPointF(sr.left() + 4, sr.bottom() + 16), lab, QColor(225, 228, 235))
        # the drawing's frame
        acc = QColor(theme.TEXT) if self.mode == "result" else QColor(theme.ACCENT)
        hot = self.mode == "edit" and (self._frame_hover == "edge" or
                                       (self._frame is not None and self._frame["kind"] == "edge"))
        p.setPen(QPen(acc, 3.5 if hot else 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        if self.mode == "edit":                       # resize handles
            for c in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()):
                big = self._frame_hover == "corner" or (self._frame is not None and self._frame["kind"] == "corner")
                h = 5.5 if big else 4.0
                p.setPen(QPen(QColor(0, 0, 0, 160), 1)); p.setBrush(acc)
                p.drawRect(QRectF(c.x() - h, c.y() - h, 2 * h, 2 * h))
            if self._frame is not None:
                p.setFont(theme.ui_font(8.5, QFont.Weight.Bold))
                txt = (f"{m.W * m.cell:,} × {m.H * m.cell:,} blocks  ·  cell {m.cell}".replace(",", " ")
                       if self._frame["kind"] == "corner" else f"centre {m.center[0]}, {m.center[1]}")
                self._label(p, QPointF(r.left() + 4, r.top() - 8), txt, QColor(255, 255, 255))
        # the placement centre (small cross) in edit mode
        if self.mode == "edit":
            c = QPointF(*self.to_screen(*m.center))
            p.setPen(QPen(QColor(theme.ACCENT), 1.6))
            p.drawLine(c + QPointF(-5, -5), c + QPointF(5, 5)); p.drawLine(c + QPointF(-5, 5), c + QPointF(5, -5))
        if self.mode == "result" and self.result is not None:
            res = self.result
            txt = f"{res['score'] * 100:.1f} %   ·   offset {res['ox']:+d}, {res['oz']:+d} cells"
            p.setFont(theme.ui_font(8.5, QFont.Weight.Bold))
            self._label(p, QPointF(r.left() + 4, r.bottom() + 17), txt, QColor(255, 255, 255))
        # tool previews
        self._paint_tool_preview(p, r, cpx)
        p.end()

    def _label(self, p, pt, text, color):
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(text) + 12
        box = QRectF(pt.x() - 2, pt.y() - fm.ascent() - 3, w, fm.height() + 4)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(0, 0, 0, 150))
        p.drawRoundedRect(box, 4, 4)
        p.setPen(color); p.drawText(QPointF(pt.x() + 4, pt.y()), text)

    def _paint_tool_preview(self, p, r, cpx):
        if self._mouse is None or self.tool == "pan" or self._space or self._pan is not None \
                or self._drag is not None or self._frame is not None:
            return
        if self._stroke is None and (not r.contains(self._mouse) or self._frame_hover is not None):
            return                                # outside the square: no brush preview
        fi, fj = self.cell_of(self._mouse)
        st = self._stroke
        if self.tool == "circle" and st is not None and st.get("start") is not None:
            i0, j0 = st["start"]
            rc = math.hypot(math.floor(fi) - i0, math.floor(fj) - j0)
            col = QColor(*code_rgb(self.code)) if not st.get("erase") else QColor(theme.TEXT2)
            pen = QPen(col, 2); pen.setStyle(Qt.PenStyle.DashLine); p.setPen(pen)
            p.setBrush(QColor(col.red(), col.green(), col.blue(), 50))
            c = QPointF(r.left() + (i0 + .5) * cpx, r.top() + (j0 + .5) * cpx)
            p.drawEllipse(c, (rc + .5) * cpx, (rc + .5) * cpx)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(col); p.drawEllipse(c, 3, 3)
            p.setFont(theme.ui_font(8, QFont.Weight.Bold))
            self._label(p, c + QPointF(8, -8), f"r {rc:.1f} cells", QColor(255, 255, 255))
            return
        if self.tool in ("rect", "line") and st is not None and st.get("start") is not None:
            i0, j0 = st["start"]
            i1, j1 = int(math.floor(fi)), int(math.floor(fj))
            a = QPointF(r.left() + min(i0, i1) * cpx, r.top() + min(j0, j1) * cpx)
            b = QPointF(r.left() + (max(i0, i1) + 1) * cpx, r.top() + (max(j0, j1) + 1) * cpx)
            col = QColor(*code_rgb(self.code)) if not st.get("erase") else QColor(theme.TEXT2)
            pen = QPen(col, 2); pen.setStyle(Qt.PenStyle.DashLine); p.setPen(pen)
            p.setBrush(QColor(col.red(), col.green(), col.blue(), 50))
            if self.tool == "rect":
                p.drawRect(QRectF(a, b))
            elif self.tool == "ellipse":
                p.drawEllipse(QRectF(a, b))
            else:
                p.drawLine(QPointF(r.left() + (i0 + .5) * cpx, r.top() + (j0 + .5) * cpx),
                           QPointF(r.left() + (i1 + .5) * cpx, r.top() + (j1 + .5) * cpx))
            return
        if self.tool in ("brush", "eraser", "weight", "line"):
            c = QPointF(r.left() + (math.floor(fi) + 0.5) * cpx, r.top() + (math.floor(fj) + 0.5) * cpx)
            rad = max(3.0, (self.radius + 0.5) * cpx)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(0, 0, 0, 150), 3)); p.drawEllipse(c, rad, rad)
            p.setPen(QPen(QColor(255, 255, 255, 220), 1.2)); p.drawEllipse(c, rad, rad)

    # ── mouse ───────────────────────────────────────────────────────────────
    def _cell_int(self, pos):
        fi, fj = self.cell_of(pos)
        return int(math.floor(fi)), int(math.floor(fj))

    def _paint_args(self, erase):
        if self.tool == "weight":
            return dict(weight=1.0 if erase else self.weight)
        if self.tool == "eraser" or erase:
            return dict(code=de.ANY)
        return dict(code=self.code)

    def mousePressEvent(self, e):
        b = e.button()
        if b == Qt.MouseButton.MiddleButton or (b == Qt.MouseButton.LeftButton and self._space):
            self._stop_motion()
            self._pan = (e.position(), self.cx, self.cz)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if self.tool == "pan" or b not in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            if b == Qt.MouseButton.LeftButton:
                super().mousePressEvent(e)
            return
        # the square: corners = resize, border = move
        hit = self._hit_frame(e.position()) if b == Qt.MouseButton.LeftButton else None
        if hit is not None:
            m = self.model
            m.flush()
            m.begin("Resize the area" if hit == "corner" else "Move the drawing")
            c = QPointF(*self.to_screen(*m.center))
            r = self.drawing_rect()
            self._frame = dict(kind=hit, start=e.position(), cell0=m.cell, center0=tuple(m.center),
                               half0=max(4.0, max(abs(e.position().x() - c.x()), abs(e.position().y() - c.y()))),
                               rect0=r)
            self.update()
            return
        erase = b == Qt.MouseButton.RightButton
        i, j = self._cell_int(e.position())
        # outside the drawing: the map moves (the picker still reads the world)
        if self.tool != "picker" and not self._inside_drawing(e.position()):
            if b == Qt.MouseButton.LeftButton:
                super().mousePressEvent(e)
            return
        if self.tool == "picker":
            self._pick_code(e.position(), i, j)
            return
        self.toolUsed.emit()               # result mode → edit mode (offset 0)
        i, j = self._cell_int(e.position())
        m = self.model
        m.flush()
        m.begin()
        self._stroke = dict(button=b, start=(i, j), last=(i, j), erase=erase)
        args = self._paint_args(erase)
        if self.tool in ("brush", "eraser", "weight"):
            m.stamp(i, j, self.radius, **args)
        elif self.tool == "fill":
            m.fill(i, j, **args)
            m.commit("Fill")
            self._stroke = None
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        self._mouse = pos
        if self._pan is not None:
            start, cx, cz = self._pan
            self.cx = cx - (pos.x() - start.x()) * self.bpp
            self.cz = cz - (pos.y() - start.y()) * self.bpp
            self._view_changed()
            return
        if self._frame is not None:
            self._drag_frame(pos)
            return
        if self._drag is not None:                    # map drag started outside the drawing
            super().mouseMoveEvent(e)
            return
        st = self._stroke
        if st is not None:
            i, j = self._cell_int(pos)
            if self.tool in ("brush", "eraser", "weight") and (i, j) != st["last"]:
                li, lj = st["last"]
                self.model.line(li, lj, i, j, self.radius, **self._paint_args(st["erase"]))
                st["last"] = (i, j)
            self.update()
            return
        if self.tool == "pan":
            super().mouseMoveEvent(e)
        else:
            self._hover_pos = pos
            if not self._hover_timer.isActive():
                self._hover_timer.start()
            hit = None if self._space else self._hit_frame(pos)
            if hit != self._frame_hover:
                self._frame_hover = hit
            if hit == "corner":
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif hit == "edge":
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            elif not self._space:
                self.setCursor(Qt.CursorShape.CrossCursor if (self._inside_drawing(pos) or self.tool == "picker")
                               else Qt.CursorShape.OpenHandCursor)
        i, j = self._cell_int(pos)
        if self.model.inside(i, j):
            self.cellHover.emit(i, j)
        else:
            self.cellHover.emit(-1, -1)
        self.update()

    def mouseReleaseEvent(self, e):
        if self._pan is not None and e.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton):
            self._pan = None
            self.setCursor(Qt.CursorShape.OpenHandCursor if (self._space or self.tool == "pan")
                           else Qt.CursorShape.CrossCursor)
            return
        if self._frame is not None and e.button() == Qt.MouseButton.LeftButton:
            kind = self._frame["kind"]
            self._frame = None
            self.model.commit("Resize the area" if kind == "corner" else "Move the drawing")
            self.update()
            return
        if self._drag is not None and e.button() == Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(e)
            self._mouse = e.position()
            self.update()
            return
        st = self._stroke
        if st is not None and e.button() == st["button"]:
            m = self.model
            i, j = self._cell_int(e.position())
            args = self._paint_args(st["erase"])
            label = {"brush": "Paint", "eraser": "Erase", "weight": "Weight", "rect": "Rectangle",
                     "circle": "Circle", "line": "Line"}.get(self.tool, "Edit")
            if self.tool == "circle":
                i0, j0 = st["start"]
                m.circle(i0, j0, math.hypot(i - i0, j - j0), filled=self.filled,
                         width=max(1.0, 2 * self.radius), **args)
            elif self.tool == "rect":
                i0, j0 = st["start"]
                m.shape(self.tool, i0, j0, i, j, filled=self.filled, radius=self.radius, **args)
            elif self.tool == "line":
                i0, j0 = st["start"]
                m.line(i0, j0, i, j, self.radius, **args)
            m.commit(label)
            self._stroke = None
            self.update()
            return
        if self.tool == "pan":
            super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self.tool == "pan" or not self._inside_drawing(e.position()):
            super().mouseDoubleClickEvent(e)

    def leaveEvent(self, e):
        self._mouse = None
        self.cellHover.emit(-1, -1)
        super().leaveEvent(e)
        self.update()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            d = e.angleDelta().y()
            if d:
                self.set_radius(self.radius + (0.5 if d > 0 else -0.5))
            return
        super().wheelEvent(e)

    def set_radius(self, r):
        self.radius = max(0.0, min(40.0, float(r)))
        self.brushChanged.emit(self.radius)
        self.update()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        if e.key() == Qt.Key.Key_Escape and self._frame is not None:
            self._frame = None
            self.model.cancel()
            self.update()
            return
        if e.key() == Qt.Key.Key_Escape and self._stroke is not None:
            self.model.cancel()
            self._stroke = None
            self.update()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = False
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.tool == "pan" else Qt.CursorShape.CrossCursor)
            return
        super().keyReleaseEvent(e)

    def contextMenuEvent(self, e):
        # right button erases inside the drawing; outside it, the map's menu
        if self.tool == "pan" or not self._inside_drawing(QPointF(e.pos())):
            super().contextMenuEvent(e)

    # ── the square itself: inside / border / corners ────────────────────────
    def _inside_drawing(self, pos):
        return self.drawing_rect(*self.offset()).contains(pos)

    def _hit_frame(self, pos, tol=7.0):
        if self.mode != "edit" or self.tool == "pan":
            return None
        r = self.drawing_rect()
        for c in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()):
            if abs(pos.x() - c.x()) <= tol + 2 and abs(pos.y() - c.y()) <= tol + 2:
                return "corner"
        near_x = r.top() - tol <= pos.y() <= r.bottom() + tol
        near_y = r.left() - tol <= pos.x() <= r.right() + tol
        if (near_x and (abs(pos.x() - r.left()) <= 4 or abs(pos.x() - r.right()) <= 4)) or \
                (near_y and (abs(pos.y() - r.top()) <= 4 or abs(pos.y() - r.bottom()) <= 4)):
            return "edge"
        return None

    def _drag_frame(self, pos):
        f, m = self._frame, self.model
        if f["kind"] == "corner":
            # the square grows / shrinks around its centre; the grid keeps its
            # cells (the painting is kept), the cell size follows
            c = QPointF(*self.to_screen(*f["center0"]))
            d = max(abs(pos.x() - c.x()), abs(pos.y() - c.y()))
            cell = max(1, int(round(f["cell0"] * d / f["half0"])))
            cell = min(4096, cell)
            if cell != m.cell:
                m.set_geometry(cell=cell)
        else:
            dx = (pos.x() - f["start"].x()) * self.bpp
            dz = (pos.y() - f["start"].y()) * self.bpp
            cx, cz = f["center0"]
            m.set_geometry(center=(int(round(cx + dx)), int(round(cz + dz))))
        self.update()

    # ── eyedropper: the painted class, else the real world's biome ──────────
    def _pick_code(self, pos, i, j):
        m = self.model
        if self.mode == "edit" and m.inside(i, j) and m.grid[j, i] != de.ANY:
            self.codePicked.emit(int(m.grid[j, i]))
            return
        wx, wz = (int(v) for v in self.to_world(pos.x(), pos.y()))
        try:
            import cubiomes_wrap as cw
            scale = 1 if self.world.mc >= cw.MC_VERSIONS["Java 1.18"] else 4
            bid = self._ui_lib().get_biome_at(wx, wz, scale=scale)
        except Exception:
            return
        if bid is not None and bid >= 0:
            self.codePicked.emit(de.biome_code(int(bid)))
