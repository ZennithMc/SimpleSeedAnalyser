"""
qtui/layers_panel.py — right-hand "Layers" panel of the map: what to show and how.

  * Landmarks & structures (one toggle each, unavailable ones greyed)
  * Noise overlay: climate noises, approximate height, exact Y — palette,
    opacity, octave selection (all / up to / only one) with each octave's share,
    octave arrows (gradient field or raw Perlin lattice vectors)
  * Display: biome colours, icon size
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget)

import cubiomes_wrap as cw
import noise_palettes as npal

from . import icons, struct_icons, theme
from .palette_combo import fill_palette_combo
from .map_engine import CLIMATE, palette_names
from .map_view import ARROW_UNIFORM
from .widgets import LabeledSlider, Segmented, body, hline, section_title

LANDMARKS = [("__spawn", "Spawn", "spawn"), ("__strongholds", "Strongholds", "stronghold"),
             ("__origin", "Origin (0, 0)", "origin_cross")]
SURFACE = ["village", "pillager_outpost", "desert_pyramid", "jungle_temple", "swamp_hut",
           "igloo", "mansion", "monument", "shipwreck", "ocean_ruin", "ruined_portal",
           "buried_treasure", "trail_ruins", "ancient_city", "trial_chambers", "abandoned_camp"]
NOISE_CHOICES = ["Off", "Temperature", "Humidity", "Continentalness", "Erosion", "Weirdness",
                 "Approx height", "Exact Y"]
# keyboard shortcuts of the noise layer (on any page with a map)
NOISE_KEYS = {"Off": "Alt+B", "Temperature": "Alt+T", "Humidity": "Alt+H",
              "Continentalness": "Alt+C", "Erosion": "Alt+E", "Weirdness": "Alt+W",
              "Approx height": "Alt+Y", "Exact Y": "Alt+Shift+Y"}


def _qicon(key: str) -> QIcon:
    return struct_icons.icon(key, 18)


class LayersPanel(QWidget):
    changed = Signal()

    def __init__(self, map_view, world, parent=None):
        super().__init__(parent)
        self.setObjectName("SidePanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(288)
        self.map = map_view
        self.world = world
        self.boxes: dict = {}

        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        head = body("Layers"); head.setObjectName("PageTitle")
        head.setStyleSheet("font-size: 11pt; padding: 14px 16px 8px 16px;")
        outer.addWidget(head)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        bodyw = QWidget(); lay = QVBoxLayout(bodyw)
        lay.setContentsMargins(16, 4, 14, 16); lay.setSpacing(4)
        scroll.setWidget(bodyw)
        outer.addWidget(scroll, 1)

        # ── landmarks & structures ──────────────────────────────────────
        lay.addWidget(section_title("Landmarks"))
        for key, label, ikey in LANDMARKS:
            lay.addWidget(self._toggle(key, label, ikey))
            if key == "__strongholds":
                sh = QPushButton("All 128 strongholds · eyes, top Y…")
                sh.setProperty("kind", "ghost")
                sh.setStyleSheet("text-align: left; padding: 2px 8px 2px 26px; font-size: 8.5pt;")
                sh.setToolTip("Every stronghold of the seed with its eye count, top Y and (1.13–1.17)\n"
                              "corruptible portal. Right-click / double-click a stronghold on the map\n"
                              "for its details.")
                sh.clicked.connect(lambda: self.map.show_all_strongholds())
                lay.addWidget(sh)
        lay.addSpacing(10)
        row = QHBoxLayout(); row.setSpacing(4)
        row.addWidget(section_title("Structures")); row.addStretch(1)
        for txt, val in (("All", True), ("None", False)):
            b = QPushButton(txt); b.setProperty("kind", "ghost")
            b.setStyleSheet("padding: 1px 8px; min-height: 14px; font-size: 8pt;")
            b.clicked.connect(lambda _=False, v=val: self._set_all(v))
            row.addWidget(b)
        lay.addLayout(row)
        from condition_types import DISABLED_TYPES
        for key in SURFACE:
            if key in DISABLED_TYPES:            # temporarily hidden (abandoned camp, 01/10)
                self.map.visible[key] = False
                continue
            if key in cw.STRUCTURE_TYPES:
                label = struct_icons.label(key)
                tg = self._toggle(key, label, key)
                if key == "village":             # abandoned villages: own icon, same switch (01/10)
                    tg.setToolTip("Also shows abandoned (zombie) villages, ≈ 2 % of villages,\n"
                                  "with their own icon.")
                lay.addWidget(tg)
        lay.addWidget(body("Structures appear when zoomed in (≤ 48 blocks per pixel).",
                           muted=True, small=True))
        # coordinate grid (like cubiomes-viewer), 01/10
        grow = QHBoxLayout(); grow.setSpacing(6)
        self.grid = QCheckBox("Grid")
        self.grid.setIcon(icons.icon("grid", 16))
        self.grid.setChecked(bool(self.map.visible.get("__grid")))
        self.grid.setToolTip("Coordinate lines on the map, with their X / Z along the edges.\n"
                             "Every 8th line is brighter (with Chunks: the 512-block regions);\n"
                             "X = 0 and Z = 0 are the brightest.")
        self.grid_step = QComboBox()
        for lab, v in (("Auto", 0), ("Chunks (16)", 16), ("64", 64), ("256", 256),
                       ("Regions (512)", 512), ("1 024", 1024), ("4 096", 4096), ("16 384", 16384)):
            self.grid_step.addItem(lab, v)
        i = self.grid_step.findData(int(self.map.ov.get("grid_step", 0) or 0))
        self.grid_step.setCurrentIndex(max(0, i))
        self.grid_step.setToolTip("Line spacing in blocks. Auto follows the zoom; a fixed spacing\n"
                                  "that would be too dense when zoomed out is doubled until readable.")
        self.grid_step.setEnabled(self.grid.isChecked())

        def _grid_on(on):
            self.map.visible["__grid"] = on
            self.grid_step.setEnabled(on)
            self.map.update(); self.changed.emit()

        def _grid_step(_i):
            self.map.ov["grid_step"] = int(self.grid_step.currentData() or 0)
            self.map.update(); self.changed.emit()
        self.grid.toggled.connect(_grid_on)
        self.grid_step.currentIndexChanged.connect(_grid_step)
        grow.addWidget(self.grid); grow.addWidget(self.grid_step, 1)
        lay.addSpacing(4); lay.addLayout(grow)
        self.legend = QCheckBox("Legend on the map")
        self.legend.setIcon(icons.icon("legend", 16))
        self.legend.setToolTip("A small card on the map: the icons shown and how many of each are on screen.")
        lay.addWidget(self.legend)
        lay.addSpacing(12); lay.addWidget(hline()); lay.addSpacing(10)

        # ── noise overlay ──────────────────────────────────────────────
        lay.addWidget(section_title("Noise overlay"))
        self.noise = QComboBox(); self.noise.addItems(NOISE_CHOICES)
        for i, name in enumerate(NOISE_CHOICES):
            self.noise.setItemData(i, f"{name}  ({NOISE_KEYS[name]})" if name != "Off"
                                   else f"Biomes only, no noise  ({NOISE_KEYS[name]})",
                                   Qt.ItemDataRole.ToolTipRole)
        self.noise.currentTextChanged.connect(self._on_noise)
        lay.addWidget(self.noise)
        self.noise_box = QWidget(); nl = QVBoxLayout(self.noise_box)
        nl.setContentsMargins(0, 6, 0, 0); nl.setSpacing(6)
        prow = QHBoxLayout(); prow.setSpacing(6)
        prow.addWidget(body("Palette", wrap=False))
        self.npal = QComboBox()
        fill_palette_combo(self.npal, self.map.ov["palette"])
        self._tint_npal()
        self.npal.currentIndexChanged.connect(self._on_npal)
        prow.addWidget(self.npal, 1)
        ed = QToolButton(); icons.bind(ed, "palette", 17)
        ed.setToolTip("Palette editor: create / edit your own noise palettes")
        ed.clicked.connect(self.open_palette_editor)
        prow.addWidget(ed)
        nl.addLayout(prow)
        self.opacity = LabeledSlider("Opacity", 5, 100, self.map.ov["opacity"] * 100, "{:.0f} %")
        self.opacity.changed.connect(lambda v: self.map.set_overlay(opacity=v / 100.0))
        nl.addWidget(self.opacity)
        self.caves = QCheckBox("Include caves"); self.caves.setChecked(True)
        self.caves.toggled.connect(lambda on: self.map.set_overlay(caves=on))
        nl.addWidget(self.caves)

        # octaves
        self.oct_box = QWidget(); ol = QVBoxLayout(self.oct_box)
        ol.setContentsMargins(0, 6, 0, 0); ol.setSpacing(6)
        ol.addWidget(section_title("Octaves"))
        self.oct_mode = Segmented(["All", "Up to", "Only"], "All")
        self.oct_mode.changed.connect(lambda _v: self._apply_octave())
        ol.addWidget(self.oct_mode)
        self.oct_list = QListWidget(); self.oct_list.setFixedHeight(150); self.oct_list.setFont(theme.mono_font(9))
        self.oct_list.currentRowChanged.connect(lambda _r: self._apply_octave())
        ol.addWidget(self.oct_list)
        self.oct_hint = body("", muted=True, small=True)
        ol.addWidget(self.oct_hint)

        # arrows
        ol.addSpacing(4)
        self.arrows = QCheckBox("Show octave arrows")
        self.arrows.setToolTip("Gradient / lattice vectors of the climate noise  (Alt+G;"
                               " Alt+Shift+G: Gradient ↔ Lattice)")
        self.arrows.toggled.connect(self._on_arrows)
        ol.addWidget(self.arrows)
        self.arrow_box = QWidget(); al = QVBoxLayout(self.arrow_box)
        al.setContentsMargins(0, 2, 0, 0); al.setSpacing(6)
        self.arrow_mode = Segmented(["Gradient", "Lattice"], "Gradient")
        self.arrow_mode.changed.connect(self._on_arrow_mode)
        al.addWidget(self.arrow_mode)
        self.arrow_hint = body("", muted=True, small=True)
        al.addWidget(self.arrow_hint)
        crow = QHBoxLayout(); crow.addWidget(body("Colour", wrap=False))
        self.arrow_color = QComboBox()
        self.arrow_color.addItems(["Direction", "Magnitude"] + list(ARROW_UNIFORM))
        self.arrow_color.currentTextChanged.connect(lambda v: self.map.set_overlay(arrow_color=v))
        crow.addWidget(self.arrow_color, 1); al.addLayout(crow)
        self.arrow_density = LabeledSlider("Density", 0.3, 3.0, 1.0, "{:.1f}×", 0.1)
        self.arrow_density.changed.connect(lambda v: self.map.set_overlay(arrow_density=v))
        al.addWidget(self.arrow_density)
        self.arrow_length = LabeledSlider("Length", 0.4, 3.0, 1.0, "{:.1f}×", 0.1)
        self.arrow_length.changed.connect(lambda v: self.map.set_overlay(arrow_length=v))
        al.addWidget(self.arrow_length)
        self.arrow_y = LabeledSlider("Lattice Y slice", -64, 320, 0, "Y {:.0f}")
        self.arrow_y.changed.connect(lambda v: self.map.set_overlay(arrow_y=v))
        al.addWidget(self.arrow_y)
        ol.addWidget(self.arrow_box)
        nl.addWidget(self.oct_box)
        self.arrow_box.setVisible(False)
        lay.addWidget(self.noise_box)
        lay.addSpacing(12); lay.addWidget(hline()); lay.addSpacing(10)

        # ── crunchiness of the seed (classic UI's heatmap, 27/09) ──────
        from .crunch_view import CrunchView, DelayedTipTitle, GradientLegend
        lay.addWidget(DelayedTipTitle(section_title("Crunchiness"),
            "Each cell: one octave (streams A, B) of a climate noise, |y − 0.5| of its\n"
            "lattice offset. Score: the weighted mean of these octave offsets —\n"
            "the Crunchiness condition's scale: 0 = crunchy, ≈ 0.25 = random, 0.5 = bland.\n"
            "Click a cell or a score to show it on the map."))
        self.crunch = CrunchView()
        self.crunch.picked.connect(self._crunch_pick)
        self.crunch.hovered.connect(self._crunch_info)
        lay.addWidget(self.crunch)
        self.crunch_line = QLabel(""); self.crunch_line.setObjectName("Muted")
        self.crunch_line.setWordWrap(True)
        self.crunch_line.setStyleSheet("font-size: 8pt;")
        lay.addWidget(self.crunch_line)
        lay.addWidget(GradientLegend())
        lay.addSpacing(12); lay.addWidget(hline()); lay.addSpacing(10)

        # ── display ────────────────────────────────────────────────────
        lay.addWidget(section_title("Display"))
        lay.addWidget(body("Biome colours"))
        self.palette = QComboBox(); self.palette.addItems(palette_names())
        if self.map.engine.palette in palette_names():
            self.palette.setCurrentText(self.map.engine.palette)
        self.palette.currentTextChanged.connect(self._on_palette)
        lay.addWidget(self.palette)
        lay.addSpacing(8)
        self.size = LabeledSlider("Icon size", 12, 40, self.map.icon_px, "{:.0f} px")
        self.size.changed.connect(self._on_size)
        lay.addWidget(self.size)
        lay.addStretch(1)

        world.changed.connect(self._refresh_availability)
        world.changed.connect(self._crunch_refresh)
        self._era_palette = dict(self.ERA_PALETTE)
        world.changed.connect(self._apply_era_palette)
        self._apply_era_palette()
        theme.signals.changed.connect(self._crunch_info)      # colour baked in its style
        self.map.overlayChanged.connect(self._crunch_current)
        self._refresh_availability()
        self._on_noise("Off")
        self._crunch_refresh()

    # ── crunchiness ────────────────────────────────────────────────────────
    def _crunch_refresh(self):
        modern = self.world.mc >= cw.MC_VERSIONS["Java 1.18"]
        self.crunch.setEnabled(modern)
        if modern:
            self.crunch.set_seed(self.world.seed, self.world.large_biomes)
        self._crunch_info()

    def _crunch_info(self):
        if self.world.mc < cw.MC_VERSIONS["Java 1.18"]:
            self.crunch_line.setText("Climate noises exist from Java 1.18.")
            self.crunch_line.setStyleSheet(f"font-size: 8pt; color: {theme.TEXT3};")
            return
        d = self.crunch.describe(self.crunch.hover)
        if d is None:
            from .crunch_view import CLIMATES as _CL
            best = [(v["score"], n) for (cid, _a, n) in _CL
                    for v in [self.crunch.data.get(cid, {})] if v.get("score") is not None]
            if best:
                s, n = min(best)
                d = (f"Most crunchy: {n} ({s:.2f}; 0 = crunchy, 0.5 = bland) · hover a cell for details",
                     QColor(theme.TEXT2))
            else:
                d = ("—", QColor(theme.TEXT3))
        text, col = d
        self.crunch_line.setText(text)
        self.crunch_line.setStyleSheet(f"font-size: 8pt; color: {col.name()};")

    def _crunch_pick(self, name, pair, is_b):
        if self.noise.currentText() != name:
            self.noise.setCurrentText(name)
        if pair is None:
            self.oct_mode.set_value("All")
        else:
            self.oct_mode.set_value("Only")
            for i in range(self.oct_list.count()):
                if tuple(self.oct_list.item(i).data(Qt.ItemDataRole.UserRole)) == (pair, bool(is_b)):
                    self.oct_list.blockSignals(True)
                    self.oct_list.setCurrentRow(i)
                    self.oct_list.blockSignals(False)
                    break
        self._apply_octave()
        self._crunch_current()

    def _crunch_current(self):
        from .crunch_view import CLIMATES as _CL
        ov = self.map.ov
        cid = next((c for c, _a, n in _CL if n == ov.get("noise")), None)
        cur = None
        if cid is not None:
            o = ov.get("octave")
            if not o:
                cur = (cid, None, False)
            elif o[2]:                                   # "Only"
                cur = (cid, int(o[0]), bool(o[1]))
        if cur != self.crunch.current:
            self.crunch.current = cur
            self.crunch.update()

    # ── noise palette editor ───────────────────────────────────────────────
    def open_palette_editor(self):
        ed = getattr(self, "_pal_editor", None)
        if ed is not None and ed.isVisible():
            ed.raise_(); ed.activateWindow()
            return
        from .palette_editor import PaletteEditor
        cur_noise = self.noise.currentText()
        modern = self.world.mc >= cw.MC_VERSIONS["Java 1.18"]
        ed = PaletteEditor(self.map.ov["palette"], None if cur_noise == "Off" else cur_noise,
                           modern=modern, parent=self.window())
        ed.applied.connect(self._editor_applied)
        ed.palettesChanged.connect(self._reload_palettes)
        ed.edited_live.connect(self._palette_live)
        self._pal_editor = ed
        ed.show()
        ed._show_on_map()                    # overlay on, edited noise + palette

    def _on_npal(self, _i):
        v = self.npal.currentData()
        if v:
            self.map.set_overlay(palette=v)
        self._tint_npal()

    def _tint_npal(self):
        """The closed combo shows the chosen palette's name in its colour."""
        from .palette_combo import name_color
        v = self.npal.currentData()
        c = name_color(v).name() if v else theme.TEXT
        self.npal.setStyleSheet(f"QComboBox {{ color: {c}; font-weight: 600; }}")

    def _reload_palettes(self):
        cur = self.map.ov["palette"]
        ok = cur in npal.NOISE_PALETTE_NAMES
        fill_palette_combo(self.npal, cur if ok else "Semantic")
        self._tint_npal()
        if not ok:
            self.map.set_overlay(palette="Semantic")

    def _editor_applied(self, palette, map_noise):
        self._reload_palettes()
        if self.noise.currentText() != map_noise:
            self.noise.setCurrentText(map_noise)
        i = self.npal.findData(palette)
        if i >= 0:
            self.npal.blockSignals(True); self.npal.setCurrentIndex(i); self.npal.blockSignals(False)
            self._tint_npal()
        self.map.set_overlay(palette=palette)

    def _palette_live(self, name):
        self.map.engine.palette_edited(name)
        if self.map.link is not None:
            self.map.link.repaint_all()
        else:
            self.map.update()

    # ── landmarks / structures ─────────────────────────────────────────────
    def _toggle(self, key, label, icon_key):
        cb = QCheckBox(label)
        cb.setIcon(_qicon(icon_key))
        cb.setChecked(bool(self.map.visible.get(key, False)))
        cb.setStyleSheet("QCheckBox { padding: 3px 0; }")
        cb.toggled.connect(lambda on, k=key: self._on_toggle(k, on))
        self.boxes[key] = cb
        return cb

    def _on_toggle(self, key, on):
        self.map.visible[key] = on
        self.map.update()
        self.changed.emit()

    def _set_all(self, on):
        for key, cb in self.boxes.items():
            if not key.startswith("__") and cb.isEnabled():
                cb.setChecked(on)

    # default biome colours by era (03/10): Cooler & Brighter before 1.18, True
    # Colors from 1.18; a palette chosen by hand is kept for that era
    ERA_PALETTE = {False: "Cooler & Brighter (Zennith)", True: "True Colors (ShowMe7)"}

    def _era(self):
        return self.world.mc >= cw.MC_VERSIONS["Java 1.18"]

    def _apply_era_palette(self):
        era = self._era()
        if era == getattr(self, "_pal_era", None):
            return
        self._pal_era = era
        want = self._era_palette.get(era)
        if want in palette_names() and self.palette.currentText() != want:
            self.palette.setCurrentText(want)

    def _on_palette(self, name):
        if not hasattr(self, "_era_palette"):
            self._era_palette = dict(self.ERA_PALETTE)
        self._era_palette[self._era()] = name
        self.map.engine.set_palette(name)
        self.map.update()

    def _on_size(self, v):
        self.map.icon_px = int(v)
        self.map.update()

    def _refresh_availability(self):
        for key, cb in self.boxes.items():
            if key.startswith("__"):
                continue
            ok = self.world.structure_available(key)
            cb.setEnabled(ok)
            cb.setToolTip("" if ok else f"Not generated in {self.world.version_name}")
        modern = self.world.mc >= cw.MC_VERSIONS["Java 1.18"]
        m = self.noise.model()
        for i, name in enumerate(NOISE_CHOICES):
            if name != "Off":
                m.item(i).setEnabled(modern)
        if not modern and self.noise.currentText() != "Off":
            self.noise.setCurrentText("Off")
        self.noise.setToolTip("" if modern else "Noise layers exist from Java 1.18 (noise-based generation).")
        if self.noise.currentText() in CLIMATE:
            self._fill_octaves(self.noise.currentText())

    # ── noise ──────────────────────────────────────────────────────────────
    def _on_noise(self, name):
        noise = None if name == "Off" else name
        self.noise_box.setVisible(noise is not None)
        self.caves.setVisible(noise == "Exact Y")
        self.oct_box.setVisible(noise in CLIMATE)
        if noise in CLIMATE:
            self._fill_octaves(noise)
        self.map.set_overlay(noise=noise, octave=self._octave_spec() if noise in CLIMATE else None)

    def _fill_octaves(self, noise):
        self.oct_list.blockSignals(True)
        self.oct_list.clear()
        info = self.map.octave_info(noise)
        if info:
            a, b = info
            total = sum(abs(x) for x in a) + sum(abs(x) for x in b) or 1.0
            cum = 0.0
            for i in range(max(len(a), len(b))):
                for stream, amps in (("A", a), ("B", b)):
                    if i < len(amps):
                        cum += abs(amps[i])
                        it = QListWidgetItem(f"{i}{stream}   {abs(amps[i]) / total * 100:4.1f} %"
                                             f"   Σ {cum / total * 100:5.1f} %")
                        it.setToolTip(f"Octave {i}{stream}: {abs(amps[i]) / total * 100:.1f} % of the noise, "
                                      f"{cum / total * 100:.1f} % cumulated")
                        it.setData(Qt.ItemDataRole.UserRole, (i, stream == "B"))
                        self.oct_list.addItem(it)
            self.oct_hint.setText("Octave · share of the final noise · cumulated share Σ "
                                  "(A and B are the two Perlin streams of the double noise).")
        else:
            self.oct_hint.setText("Octave data needs a newer libcubiomes.")
        self.oct_list.setCurrentRow(0)
        self.oct_list.blockSignals(False)
        self._sync_oct_enabled()

    def _sync_oct_enabled(self):
        self.oct_list.setEnabled(self.oct_mode.value() != "All")

    def _octave_spec(self):
        mode = self.oct_mode.value()
        it = self.oct_list.currentItem()
        if mode == "All" or it is None:
            return None
        pair, inclb = it.data(Qt.ItemDataRole.UserRole)
        return (int(pair), bool(inclb), mode == "Only")

    def _apply_octave(self):
        self._sync_oct_enabled()
        if self.map.ov["noise"] in CLIMATE:
            self.map.set_overlay(octave=self._octave_spec())

    def _on_arrows(self, on):
        self.arrow_box.setVisible(on)
        self._on_arrow_mode(self.arrow_mode.value(), apply=False)
        self.map.set_overlay(arrows=on)

    def _on_arrow_mode(self, mode, apply=True):
        lattice = mode == "Lattice"
        self.arrow_y.setVisible(lattice)
        self.arrow_hint.setText(
            "Raw Perlin lattice gradient vectors of the selected octave (true 3D: they "
            "change with the Y slice). Hue = direction, brightness = vertical part."
            if lattice else
            "Arrows point uphill in the displayed noise (all octaves, up to one, or only one); "
            "length = slope.")
        if apply:
            self.map.set_overlay(arrow_mode=mode)
