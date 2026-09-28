"""
qtui/map_link.py — every map of the window shows the same thing.

One MapLink per window holds what all its MapViews share:
  * the MapEngine (one tile cache / thread pool: a tab switch is instant, no
    tile is computed twice);
  * the view (centre, zoom): panning or zooming one map moves the others;
  * the layers (structures shown, noise overlay, icon size) set in the Layers
    drawer.
Per-page overlays (session zones, result markers, pick mode) stay per view.
"""
from __future__ import annotations

from PySide6.QtCore import QObject


class MapLink(QObject):
    def __init__(self, world, parent=None):
        super().__init__(parent)
        from .map_engine import MapEngine
        self.world = world
        self.engine = MapEngine(world)
        self.views = []
        self.visible = None          # the first view's dicts become the shared ones
        self.ov = None
        self.style = {"icon_px": 22}
        self._syncing = False

    def add(self, view):
        if self.visible is None:
            self.visible, self.ov = view.visible, view.ov
        else:
            view.visible, view.ov = self.visible, self.ov
            ref = self.views[0]
            view.cx, view.cz, view.bpp = ref.cx, ref.cz, ref.bpp
        view._style = self.style
        self.views.append(view)

    def moved(self, src):
        """src changed centre / zoom → the other views follow (no echo)."""
        if self._syncing:
            return
        self._syncing = True
        try:
            for v in self.views:
                if v is not src and (v.cx, v.cz, v.bpp) != (src.cx, src.cz, src.bpp):
                    v._stop_motion()
                    v.cx, v.cz, v.bpp = src.cx, src.cz, src.bpp
                    v._arrows = None
                    v.update()
        finally:
            self._syncing = False

    def overlay_changed(self, src):
        for v in self.views:
            if v is not src:
                v._arrows = None
                v.update()
                v.overlayChanged.emit()

    def repaint_all(self):
        for v in self.views:
            v.update()
