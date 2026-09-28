"""Draw page (Qt) end-to-end, offscreen, in the real main window:
painting tools with undo / redo (weights included), grid resize as ONE undo
step, 'From seed' copy scoring 100 %, the search (results = an
uninterrupted engine run, pause / resume exact), result display and back to
editing, library save / open, autosave / restore with a resumable search,
the classic UI's presets import.

    python tests/draw_page_test.py
"""
import json
import os
import sys
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="ssa_draw_")
os.environ["APPDATA"] = TMP
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                            # noqa: E402
from PySide6.QtCore import QPoint, Qt               # noqa: E402
from PySide6.QtTest import QTest                             # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox      # noqa: E402

app = QApplication.instance() or QApplication([])
import draw_engine as de                                      # noqa: E402

bad = 0


def check(name, ok, info=""):
    global bad
    bad += not ok
    print(f"{'OK  ' if ok else 'FAIL'} {name:58s} {info}", flush=True)


def spin(sec):
    t = time.time() + sec
    while time.time() < t:
        app.processEvents(); time.sleep(0.01)


def wait(cond, limit=120):
    t = time.time()
    while not cond() and time.time() - t < limit:
        app.processEvents(); time.sleep(0.02)
    return cond()


def main():
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    from qtui import theme
    theme.apply(app)
    from qtui.main_window import MainWindow
    w = MainWindow(); w.resize(1480, 900); w.show()
    spin(0.3)
    w.world.set(seed=123456789, version_name="Java 1.21", large_biomes=False)
    w.show_page("drawing")
    pg = w.drawing
    m, cv = pg.model, pg.map
    spin(0.3)
    m.new(32, 32, 64)
    pg.fit()
    spin(0.2)

    def cell_pt(i, j):
        r = cv.drawing_rect()
        c = cv.cell_px()
        return QPoint(int(r.left() + (i + 0.5) * c), int(r.top() + (j + 0.5) * c))

    # brush stroke
    pg._set_code(de.OCEAN); pg._set_tool("brush"); cv.set_radius(1.0)
    QTest.mousePress(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, cell_pt(4, 4))
    for k in range(5, 20):
        QTest.mouseMove(cv, cell_pt(k, 4)); app.processEvents()
    QTest.mouseRelease(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, cell_pt(19, 4))
    n1 = m.painted()
    row = m.grid[4, 4:20]
    check("brush stroke paints a continuous line", n1 > 0 and bool((row == de.OCEAN).all()),
          f"{n1} cells")
    m.undo()
    check("undo removes the stroke (one step)", m.painted() == 0)
    m.redo()
    check("redo puts it back", m.painted() == n1)
    # right button erases
    QTest.mousePress(cv, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, cell_pt(10, 4))
    QTest.mouseRelease(cv, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, cell_pt(10, 4))
    check("right button erases", m.grid[4, 10] == de.ANY)
    # rectangle
    pg._set_tool("rect"); pg._set_code(de.LAND)
    QTest.mousePress(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, cell_pt(2, 10))
    QTest.mouseMove(cv, cell_pt(8, 15)); app.processEvents()
    QTest.mouseRelease(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, cell_pt(8, 15))
    check("filled rectangle", bool((m.grid[10:16, 2:9] == de.LAND).all()))
    # circle: the click is the centre
    pg._set_tool("circle"); pg._set_code(de.OCEAN)
    QTest.mousePress(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, cell_pt(22, 22))
    QTest.mouseMove(cv, cell_pt(26, 22)); app.processEvents()
    QTest.mouseRelease(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, cell_pt(26, 22))
    ok = m.grid[22, 22] == de.OCEAN and m.grid[22, 18] == de.OCEAN and m.grid[18, 22] == de.OCEAN \
        and m.grid[22, 27] != de.OCEAN and m.grid[22, 17] != de.OCEAN
    check("circle centred on the click, radius to the release", ok)
    m.undo()
    # fill: the unpainted area becomes Mushroom, the painted parts stay
    pg._set_tool("fill"); pg._set_code(de.MUSHROOM)
    QTest.mouseClick(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, cell_pt(30, 30))
    check("fill = connected region only", m.grid[30, 30] == de.MUSHROOM and m.grid[12, 5] == de.LAND
          and m.grid[4, 4] == de.OCEAN)
    m.undo()
    # weights: painted and undone together with the grid (old bug D11)
    pg._set_tool("weight"); cv.weight = 4.0
    QTest.mouseClick(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, cell_pt(5, 12))
    check("weight brush on painted cells", float(m.weights[12, 5]) == 4.0)
    before = m.weights.copy()
    m.clear()
    m.undo()
    check("Clear then Undo restores the weights", np.array_equal(m.weights, before))
    # grid resize keeping the area = one undo step
    area0 = (m.W * m.cell, m.cell, m.W)
    pg.sp_w.setValue(16)
    check("resize 32 -> 16 keeps the area", m.W == 16 and m.H == 16 and m.W * m.cell == area0[0],
          f"cell {m.cell}")
    m.undo()
    check("…and one Undo brings back 32 cells / cell size", (m.W, m.cell) == (area0[2], area0[1]))

    # outside the square the brush moves the map; the square's corners
    # resize it, its border moves it (one undo step each)
    pg._set_tool("brush")
    g0 = m.grid.copy(); cx0 = cv.cx
    r = cv.drawing_rect()
    p0 = QPoint(int(r.right() + 40), int(r.center().y()))
    QTest.mousePress(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p0)
    for k in range(1, 8):
        QTest.mouseMove(cv, p0 + QPoint(10 * k, 0)); app.processEvents()
    QTest.mouseRelease(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p0 + QPoint(70, 0))
    check("brush outside the square moves the map", cv.cx < cx0 and np.array_equal(m.grid, g0))
    r = cv.drawing_rect(); cell0, c0 = m.cell, tuple(m.center)
    corner = QPoint(int(r.right()), int(r.bottom()))
    QTest.mousePress(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, corner)
    for k in range(1, 6):
        QTest.mouseMove(cv, corner + QPoint(8 * k, 8 * k)); app.processEvents()
    QTest.mouseRelease(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, corner + QPoint(40, 40))
    check("corner drag enlarges the area (cell size)", m.cell > cell0 and np.array_equal(m.grid, g0)
          and tuple(m.center) == c0, f"cell {cell0} -> {m.cell}")
    check("size field follows", pg.sp_area.value() == m.W * m.cell)
    m.undo()
    check("…one undo", m.cell == cell0)
    r = cv.drawing_rect()
    edge = QPoint(int(r.left()), int(r.center().y()))
    QTest.mousePress(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, edge)
    for k in range(1, 6):
        QTest.mouseMove(cv, edge + QPoint(-10 * k, 0)); app.processEvents()
    QTest.mouseRelease(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, edge + QPoint(-50, 0))
    check("border drag moves the drawing", m.center[0] < c0[0] and m.center[1] == c0[1]
          and np.array_equal(m.grid, g0))
    m.undo()
    check("…one undo", tuple(m.center) == c0)
    pg.sp_area.setValue(m.W * 100)
    check("Size field sets the area", m.cell == 100)
    m.undo()
    pg.op_slider.setValue(0)
    check("map 0 %: drawing opaque", cv.drawing_alpha() == 255)
    pg.op_slider.setValue(100)
    check("map 100 %: drawing hidden", cv.drawing_alpha() == 0)
    pg.op_slider.setValue(50)

    # From seed: the drawing = the seed's own land / ocean -> 100 %
    pg._from_seed("cont")
    pg.test_seed.setText("")
    pg._test()
    res = cv.result
    check("'From seed' copy tests at 100 % at offset 0",
          res is not None and abs(res["score"] - 1.0) < 1e-9 and (res["ox"], res["oz"]) == (0, 0),
          pg.test_lbl.text()[:60])
    check("test shows the result overlay", cv.mode == "result" and res["mm"].shape == (m.H, m.W))
    # painting again leaves the result mode
    pg._set_tool("brush")
    QTest.mouseClick(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, cell_pt(1, 1))
    check("painting goes back to the edit mode", cv.mode == "edit")
    m.undo()

    # search: a range containing the world seed; results = direct engine run
    seed0 = w.world.seed
    pg.start_seed.setText(str(seed0 - 300))
    pg.min_score.setValue(70); pg.top_k.setValue(400); pg.threads.setValue(2)
    pg.rule_kind.setCurrentIndex(1); pg.rule_seeds.setText("800")
    pg._start_clicked()
    ok = wait(lambda: pg.state in ("done", "stopped", "paused"), 300)
    rows = list(pg.rows)
    ref = de.DrawEngine(m.spec(w.world.mc, False), min_score=0.70, top_k=400)
    import ctypes as ct
    ref.run(seed0 - 300, 800, 2, ct.c_int(0))
    check("search ends (seed limit)", ok and pg.state == "done", pg.stat.text())
    check("results = direct engine run", rows == ref.results(), f"{len(rows)} results")
    check("the copied seed is found at 100 %", any(s == seed0 and abs(sc - 1) < 1e-9 for s, sc, *_ in rows))
    # pause / resume: same results as uninterrupted
    pg.start_seed.setText(str(seed0 - 3000))
    pg.rule_seeds.setText("6000")
    pg._start_clicked()
    for _ in range(3):
        wait(lambda: pg.state != "running" or (pg.search is not None and pg.search.done > 0), 60)
        if pg.state != "running":
            break
        pg.pause()
        wait(lambda: pg.state in ("paused", "done"), 60)
        if pg.state == "done":
            break
        check("paused search is resumable", pg._resumable() and pg.b_start.text().strip() == "Resume")
        pg._start_clicked()
    wait(lambda: pg.state in ("done", "stopped"), 600)
    ref = de.DrawEngine(m.spec(w.world.mc, False), min_score=0.70, top_k=400)
    ref.run(seed0 - 3000, 6000, 2, ct.c_int(0))
    check("pause / resume: results = uninterrupted run", pg.state == "done" and pg.rows == ref.results(),
          f"{len(pg.rows)} results, {pg.sinfo['done']} seeds")
    check("seeds counted exactly", pg.sinfo["done"] == 6000)
    # click a result
    idx = pg.results.index(0)
    pg.list.setCurrentIndex(idx)
    spin(0.1)
    r0 = pg.rows[0]
    check("clicking a result loads the seed and its overlay",
          w.world.seed == r0[0] and cv.mode == "result" and abs(cv.result["score"] - r0[1]) < 1e-9)
    wait(lambda: pg.thumbs.cache.get((r0[0], r0[2], r0[3])) is not None, 20)
    check("result thumbnail computed", pg.thumbs.cache.get((r0[0], r0[2], r0[3])) is not None)

    # library: save, change, open -> same drawing and results
    pg.rule_seeds.setText("100000")
    pg._start_clicked()
    spin(0.6)
    pg.pause(); wait(lambda: pg.state in ("paused", "done"), 60)
    grid0, w0, rows0, done0 = m.grid.copy(), m.weights.copy(), list(pg.rows), pg.sinfo["done"]
    path = pg.save_to_library(new=True, name="Test drawing")
    from qtui.draw_library import list_entries
    check("saved in the library", path is not None and any(e["path"] == path for e in list_entries()))
    m.clear()
    pg.rows = []
    pg.open_drawing(path)
    check("open: same grid and weights", np.array_equal(m.grid, grid0) and np.array_equal(m.weights, w0))
    check("open: same results, search paused and resumable",
          pg.rows == rows0 and pg.state == "paused" and pg._resumable() and pg.sinfo["done"] == done0)
    # autosave -> a new page restores it
    pg.autosave()
    from qtui.draw_page import DrawPage
    pg2 = DrawPage(w.world, link=w.link)
    pg2.restore_autosave()
    check("autosave restores the drawing + paused search",
          np.array_equal(pg2.model.grid, grid0) and pg2.rows == rows0 and pg2._resumable())
    # resume the restored one: continues from the exact point
    pg2.rule_kind.setCurrentIndex(1); pg2.rule_seeds.setText(str(done0 + 500))
    pg2._start_clicked()
    wait(lambda: pg2.state in ("done", "stopped"), 300)
    ref = de.DrawEngine(pg2.model.spec(w.world.mc, False), min_score=0.70, top_k=400)
    ref.run(seed0 - 3000, done0 + 500, 2, ct.c_int(0))
    check("restored search resumes exactly", pg2.state == "done" and pg2.rows == ref.results()
          and pg2.sinfo["done"] == done0 + 500)
    # changing the drawing blocks the resume
    pg.model.begin(); pg.model.grid[0, 0] = de.HOT; pg.model.commit("x")
    check("changed drawing: no resume", not pg._resumable() and pg.stale_lbl.isVisibleTo(pg))
    pg.model.undo()
    check("undo the change: resumable again", pg._resumable())

    # classic presets
    g = np.zeros((192, 192), int); g[40:150, 40:150] = 2; g[80:110, 80:110] = 501
    preset = [{"name": "P", "grid_w": 192, "grid_h": 192, "grid": json.dumps(g.tolist()),
               "weights": json.dumps(np.ones((192, 192)).tolist()), "score_res": 32,
               "area_blocks": 3072, "cell_blocks": 96, "slide": True, "minscore": 0}]
    app_dir = os.path.join(TMP, "app"); os.makedirs(app_dir)
    with open(os.path.join(app_dir, "drawing_presets.json"), "w") as f:
        json.dump(preset, f)
    from qtui.draw_library import draw_dir, import_legacy_presets_once
    flag = os.path.join(draw_dir(), ".legacy_imported")
    if os.path.exists(flag):
        os.remove(flag)                   # (set by the restore above, from the app folder)
    n = import_legacy_presets_once(app_dir)
    n2 = import_legacy_presets_once(app_dir)
    ent = [e for e in list_entries() if e["name"] == "P"]
    ok = n == 1 and n2 == 0 and len(ent) == 1
    if ok:
        from qtui.draw_model import DrawModel
        mm = DrawModel(); mm.load_dict(read(ent[0]["path"]))
        ok = (mm.W, mm.H, mm.cell, mm.slide) == (32, 32, 96, 8) and \
            de.group_code(2) in mm.grid and de.OCEAN in mm.grid
    check("classic presets imported once (jungle group mapped)", ok)
    w.close()
    print("FAILURES:", bad)
    os._exit(1 if bad else 0)


def read(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    main()
