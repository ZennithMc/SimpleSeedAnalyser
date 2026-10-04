"""
qtui/ui_state.py — SSA comes back exactly as it was closed (03/10).

Saved at close in <data dir>/ui_state.json, restored at the next launch:
  * the window (size, position, maximised) and the page on screen;
  * the world of the top bar: seed, version, Large biomes;
  * the map: centre, zoom, structure-seed view (Alt+S), the Layers drawer
    opened or not on each page;
  * the Layers panel: every landmark / structure ticked, grid, noise overlay
    (palette, opacity, caves, octaves, arrows), biome colours, icon size;
  * the Stronghold page: every setting, its seed list, its results and a
    paused search (Resume continues it).
The Sessions page (autosave.session) and the Draw page keep their own autosave.
Everything is best effort: a missing / older / broken file only means defaults.
"""
from __future__ import annotations

import json
import os

from PySide6.QtCore import QByteArray
from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QPushButton, QSpinBox

from .widgets import LabeledSlider, Segmented

VERSION = 1
MAX_SH_ROWS = 100_000


def _path():
    import ssa_paths
    return os.path.join(ssa_paths.data_dir(), "ui_state.json")


# ── generic widgets ──────────────────────────────────────────────────────────
def grab(obj, names=None, skip=(), texts=()):
    """{attribute: [kind, value]} of the input widgets of `obj` (in creation
    order, so restoring replays the same order)."""
    out = {}
    items = [(n, getattr(obj, n, None)) for n in names] if names else list(vars(obj).items())
    for name, w in items:
        if w is None or name.startswith("_") or name.startswith("b_") or name in skip:
            continue
        try:
            if isinstance(w, Segmented):
                out[name] = ["seg", w.value()]
            elif isinstance(w, QCheckBox) or (isinstance(w, QPushButton) and w.isCheckable()):
                out[name] = ["chk", bool(w.isChecked())]
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                out[name] = ["num", w.value()]
            elif isinstance(w, QComboBox):
                out[name] = ["cmb", w.currentText()]
            elif isinstance(w, LabeledSlider):
                out[name] = ["sld", int(w.slider.value())]
            elif isinstance(w, QLineEdit) and name in texts:
                out[name] = ["txt", w.text()]
        except RuntimeError:                    # widget already deleted
            pass
    return out


def put(obj, saved):
    for name, kv in (saved or {}).items():
        w = getattr(obj, name, None)
        if w is None or not isinstance(kv, (list, tuple)) or len(kv) != 2:
            continue
        kind, v = kv
        try:
            if kind == "seg" and isinstance(w, Segmented):
                if v in w.buttons and w.value() != v:
                    w.set_value(v)
                    w.changed.emit(v)
            elif kind == "chk" and hasattr(w, "setChecked"):
                if w.isChecked() != bool(v):
                    w.setChecked(bool(v))
            elif kind == "num" and isinstance(w, (QSpinBox, QDoubleSpinBox)):
                w.setValue(v)
            elif kind == "cmb" and isinstance(w, QComboBox):
                i = w.findText(str(v))
                if i >= 0 and i != w.currentIndex():
                    w.setCurrentIndex(i)
            elif kind == "sld" and isinstance(w, LabeledSlider):
                w.slider.setValue(int(v))
            elif kind == "txt" and isinstance(w, QLineEdit):
                w.setText(str(v))
        except Exception:                       # noqa: BLE001
            pass


# ── layers / map ─────────────────────────────────────────────────────────────
_LAYERS = ("grid", "grid_step", "noise", "npal", "opacity", "caves", "oct_mode", "arrows",
           "arrow_mode", "arrow_color", "arrow_density", "arrow_length", "arrow_y", "size")


def _grab_layers(lp):
    d = dict(boxes={k: bool(cb.isChecked()) for k, cb in lp.boxes.items()},
             widgets=grab(lp, names=_LAYERS),
             oct_row=lp.oct_list.currentRow(),
             palette=lp.palette.currentText(),
             era_palette={("modern" if k else "old"): v
                          for k, v in getattr(lp, "_era_palette", {}).items()})
    return d


def _put_layers(lp, d):
    for k, on in (d.get("boxes") or {}).items():
        cb = lp.boxes.get(k)
        if cb is not None and cb.isChecked() != bool(on):
            cb.setChecked(bool(on))
    ep = d.get("era_palette") or {}
    if ep:
        lp._era_palette = {False: ep.get("old", lp.ERA_PALETTE[False]),
                           True: ep.get("modern", lp.ERA_PALETTE[True])}
    pal = d.get("palette")
    if pal and lp.palette.findText(pal) >= 0:
        lp.palette.setCurrentText(pal)
    put(lp, d.get("widgets"))
    row = d.get("oct_row")
    if isinstance(row, int) and 0 <= row < lp.oct_list.count():
        lp.oct_list.setCurrentRow(row)
        lp._apply_octave()


# ── stronghold page ──────────────────────────────────────────────────────────
def _jsonable(v):
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)


def _grab_stronghold(sp):
    d = dict(widgets=grab(sp, skip=("start_seed",), texts=("rule_seeds",)))
    try:
        d["start_other"] = sp.start_src.other
    except Exception:                           # noqa: BLE001
        pass
    sl = getattr(sp, "seed_list", None)
    if sl is not None:
        d["seed_list"] = getattr(sl, "path", None)
    r = sp.results
    rows = r.all_rows[:MAX_SH_ROWS]
    d["results"] = dict(mode=r.mode, topy_label=getattr(r, "topy_label", "Top Y"),
                        rows=[[str(s), _jsonable(x)] for s, x in rows])
    if getattr(sp, "_paused", None) is not None and sp.runner.state not in (
            "running", "pausing", "stopping"):
        d["paused"] = {k: (str(v) if k == "start" else v) for k, v in sp._paused.items()}
    elif sp.runner.state in ("running", "pausing") and hasattr(sp, "_next_start"):
        # closed while searching: it comes back paused where it was
        try:
            nxt = sp._next_start()
            if nxt is not None:
                prev = int(getattr(sp, "_resume_done", 0) or 0)
                d["paused"] = dict(start=str(nxt), done=prev + int(sp.runner._done_before))
        except Exception:                       # noqa: BLE001
            pass
    return d


def _put_stronghold(sp, d):
    put(sp, d.get("widgets"))
    if d.get("start_other") is not None:
        try:
            sp.start_src.set_other(d["start_other"])
        except Exception:                       # noqa: BLE001
            pass
    path = d.get("seed_list")
    if path and os.path.exists(path) and getattr(sp, "seed_list", None) is None:
        try:
            from seedlist_io import SeedListSource
            src = SeedListSource(path)
            sp.seed_list = src
            from .sessions_page import fmt_int
            sp.list_btn.setText(f"{os.path.basename(path)} · {fmt_int(len(src))}  ✕")
            sp._refresh_support()
            sp._sync_start_ui()
        except Exception:                       # noqa: BLE001
            pass
    res = d.get("results") or {}
    rows = res.get("rows") or []
    r = sp.results
    if res.get("mode") in r.COLS and res["mode"] != r.mode:
        r.set_mode(res["mode"])
    r.topy_label = res.get("topy_label", r.topy_label)
    if rows:
        batch = []
        for s, x in rows:
            try:
                batch.append((int(s), dict(x)))
            except (TypeError, ValueError):
                pass
        r.clear()
        r.add(batch)
        try:
            sp._count()
        except Exception:                       # noqa: BLE001
            pass
    p = d.get("paused")
    if isinstance(p, dict) and p.get("start") is not None:
        try:
            sp._paused = dict(p, start=int(p["start"]))
            sp._pause_ui()
        except Exception:                       # noqa: BLE001
            pass


# ── window ───────────────────────────────────────────────────────────────────
def _log(where):
    """Errors go to <data dir>/ui_state_errors.log (the .exe has no console)."""
    import traceback
    try:
        import time
        with open(os.path.join(os.path.dirname(_path()), "ui_state_errors.log"), "a",
                  encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {where}\n")
            traceback.print_exc(file=f)
    except Exception:                           # noqa: BLE001
        traceback.print_exc()


def _write(path, st):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, default=str)            # never fails on an odd value
    os.replace(tmp, path)


def _heavy_path():
    return os.path.join(os.path.dirname(_path()), "ui_state_results.json")


def save(win, heavy=True):
    """Light state (window, world, map, layers, settings): cheap, saved often.
    heavy: also the Stronghold page results / paused search (separate file)."""
    st = dict(version=VERSION)
    try:
        page = next((k for k, i in win._page_index.items() if i == win.pages.currentIndex()), "map")
        st["page"] = page
        st["geometry"] = bytes(win.saveGeometry().toBase64()).decode("ascii")
    except Exception:                           # noqa: BLE001
        _log("save window")
    try:
        st["world"] = dict(seed=str(win.world.seed), version_name=win.world.version_name,
                           large_biomes=bool(win.world.large_biomes))
    except Exception:                           # noqa: BLE001
        _log("save world")
    try:
        v0 = win.link.views[0] if win.link.views else win.map
        st["map"] = dict(cx=float(v0.cx), cz=float(v0.cz), bpp=float(v0.bpp),
                         seed_view=bool(win.link.engine.seed_view),
                         drawers={k: bool(d.opened) for k, d in win.drawers.items()})
    except Exception:                           # noqa: BLE001
        _log("save map")
    try:
        st["layers"] = _grab_layers(win.layers)
    except Exception:                           # noqa: BLE001
        _log("save layers")
    shd = None
    try:
        shd = _grab_stronghold(win.strongholds)
        st["stronghold"] = {k: v for k, v in shd.items() if k not in ("results", "paused")}
    except Exception:                           # noqa: BLE001
        _log("save stronghold page")
    try:
        _write(_path(), st)
    except Exception:                           # noqa: BLE001
        _log("write ui_state.json")
    if heavy and shd is not None:
        try:
            _write(_heavy_path(), {k: shd[k] for k in ("results", "paused") if k in shd})
        except Exception:                       # noqa: BLE001
            _log("write ui_state_results.json")


def load():
    st = None
    try:
        with open(_path(), encoding="utf-8") as f:
            st = json.load(f)
    except FileNotFoundError:
        return None
    except Exception:                           # noqa: BLE001
        _log("read ui_state.json")
        return None
    if not isinstance(st, dict):
        return None
    try:
        with open(_heavy_path(), encoding="utf-8") as f:
            h = json.load(f)
        if isinstance(h, dict):
            st.setdefault("stronghold", {}).update(h)
    except FileNotFoundError:
        pass
    except Exception:                           # noqa: BLE001
        _log("read ui_state_results.json")
    return st


def restore_map(win, st):
    """Centre / zoom again (the Draw page's restore centres the shared map on its
    drawing: this runs after it)."""
    try:
        m = (st or {}).get("map") or {}
        if m.get("cx") is not None:
            win.map.center_on(float(m["cx"]), float(m["cz"]), bpp=float(m.get("bpp", 8.0)))
            if win.link is not None:
                win.link.moved(win.map)
                win.link.repaint_all()
    except Exception:                           # noqa: BLE001
        _log("restore map")


def restore(win, st=None):
    """Each part on its own: one that fails never blocks the others. Returns the
    state read (for restore_map) or None."""
    st = st if st is not None else load()
    if not st:
        return None
    g = st.get("geometry")
    if g:
        try:
            win.restoreGeometry(QByteArray.fromBase64(g.encode("ascii")))
        except Exception:                       # noqa: BLE001
            _log("restore geometry")
    try:
        w = st.get("world") or {}
        from .world import VERSION_NAMES
        vn = w.get("version_name")
        win.world.set(seed=int(w["seed"]) if w.get("seed") not in (None, "") else None,
                      version_name=vn if vn in VERSION_NAMES else None,
                      large_biomes=w.get("large_biomes"))
    except Exception:                           # noqa: BLE001
        _log("restore world")
    if st.get("layers"):
        try:
            _put_layers(win.layers, st["layers"])
        except Exception:                       # noqa: BLE001
            _log("restore layers")
    m = st.get("map") or {}
    try:
        restore_map(win, st)
        if bool(m.get("seed_view")) != bool(win.link.engine.seed_view):
            win.link.engine.set_seed_view(bool(m.get("seed_view")))
        for k, on in (m.get("drawers") or {}).items():
            d = win.drawers.get(k)
            if d is not None and bool(on) != d.opened:
                d.opened = bool(on)
                d._apply(animate=False)
    except Exception:                           # noqa: BLE001
        _log("restore map / drawers")
    if st.get("stronghold"):
        try:
            _put_stronghold(win.strongholds, st["stronghold"])
        except Exception:                       # noqa: BLE001
            _log("restore stronghold page")
    try:
        page = st.get("page")
        if page in win._page_index:
            win.show_page(page)
        win.link.repaint_all()
    except Exception:                           # noqa: BLE001
        _log("restore page")
    return st
