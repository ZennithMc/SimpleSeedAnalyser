"""
qtui/tools_dialogs.py — small tools of the Tools menu.

  * parse_seed_coords(text) / PasteSeedDialog — paste anything holding a seed
    and / or coordinates (a chat line, an F3+C command, "X: 12 Y: 64 Z: -3",
    a seed-finder output…) → the seed is loaded and the map goes there, with a
    pin on the spot.
  * ConvertSeedsDialog — text seed list → compact .seeds (sorted, de-duplicated,
    delta + zstd): progress bar, Cancel, what was skipped, "Use this list".
"""
from __future__ import annotations

import os
import re
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QProgressBar, QPushButton, QVBoxLayout)

from .widgets import remember_path, user_path

_NUM = r"[-+]?\d+(?:\.\d+)?"
_I64 = (-(1 << 63), 1 << 64)


def _to_seed(tok: str):
    """A seed from an integer token (unsigned 64-bit written seeds wrap)."""
    try:
        v = int(tok)
    except ValueError:
        return None
    if not (_I64[0] <= v < _I64[1]):
        return None
    return v - (1 << 64) if v >= (1 << 63) else v


def parse_seed_coords(text: str) -> dict:
    """{seed, x, z, y, dim, note} (missing parts = None) from free text.

    Understood, in this order:
      * "seed: 123" / "seed=123" / "Seed 123"
      * a teleport command — /tp @s X Y Z, F3+C "/execute in minecraft:the_nether
        run tp @s X Y Z yaw pitch" (Nether coordinates are turned into
        Overworld ones ×8, and said so)
      * "X: 12 Y: 64 Z: -3", "x=12, z=-3"
      * otherwise bare numbers: an unlabelled seed first, then X Z (or X Y Z
        when the middle one is a plausible height)."""
    t = text.strip()
    out = dict(seed=None, x=None, z=None, y=None, dim=None, note="")
    if not t:
        return out
    used = []                                   # spans already interpreted

    m = re.search(r"\bseed\b\s*[:=]?\s*([-+]?\d+)", t, re.I)
    if m:
        out["seed"] = _to_seed(m.group(1))
        used.append(m.span())

    m = re.search(r"\btp\s+(?:@[a-z](?:\[[^\]]*\])?\s+|\w{3,16}\s+)?"
                  rf"(~?{_NUM}|~)\s+(~?{_NUM}|~)\s+(~?{_NUM}|~)", t, re.I)
    if m:
        vals = [g.lstrip("~") for g in m.groups()]
        try:
            if vals[0] and vals[2]:
                out["x"], out["z"] = float(vals[0]), float(vals[2])
                out["y"] = float(vals[1]) if vals[1] else None
                used.append(m.span())
        except ValueError:
            pass
        d = re.search(r"\bin\s+(?:minecraft:)?(overworld|the_nether|the_end)\b", t, re.I)
        if d:
            out["dim"] = d.group(1).lower()
    if out["x"] is None:
        mx = re.search(rf"\bx\s*[:=]\s*({_NUM})", t, re.I)
        mz = re.search(rf"\bz\s*[:=]\s*({_NUM})", t, re.I)
        if mx and mz:
            out["x"], out["z"] = float(mx.group(1)), float(mz.group(1))
            used += [mx.span(), mz.span()]
            my = re.search(rf"\by\s*[:=]\s*({_NUM})", t, re.I)
            if my:
                out["y"] = float(my.group(1)); used.append(my.span())

    # the numbers nobody claimed yet
    rest = []
    for m in re.finditer(_NUM, t):
        if any(a <= m.start() < b for a, b in used):
            continue
        # a number glued to letters (e.g. "1.21", "sm_120") is not a seed / coordinate
        if m.start() > 0 and (t[m.start() - 1].isalnum() or t[m.start() - 1] in "._:"):
            continue
        rest.append(m.group(0))
    if out["seed"] is None and rest:
        # an unlabelled seed = the first integer, unless the text only holds
        # two numbers (then they are X Z)
        ints = [r for r in rest if "." not in r]
        if ints and not (out["x"] is None and len(rest) == 2):
            out["seed"] = _to_seed(ints[0])
            rest.remove(ints[0])
    if out["x"] is None and len(rest) >= 2:
        vals = [float(r) for r in rest]
        if len(vals) >= 3 and -64 <= vals[1] <= 320 and float(vals[1]).is_integer() \
                and (abs(vals[0]) > 320 or abs(vals[2]) > 320 or len(vals) == 3):
            out["x"], out["y"], out["z"] = vals[0], vals[1], vals[2]
        else:
            out["x"], out["z"] = vals[0], vals[1]
    if out["x"] is not None:
        if out["dim"] == "the_nether":
            out["x"] *= 8; out["z"] *= 8
            out["note"] = "Nether coordinates → Overworld ×8"
        elif out["dim"] == "the_end":
            out["note"] = "End coordinates (shown on the Overworld map)"
        out["x"], out["z"] = int(out["x"] // 1), int(out["z"] // 1)
        if out["y"] is not None:
            out["y"] = int(out["y"] // 1)
    return out


class PasteSeedDialog(QDialog):
    """Paste a seed and / or coordinates; the caller applies result()."""

    def __init__(self, parent=None, current_seed=None):
        super().__init__(parent)
        self.setWindowTitle("Paste seed + coordinates")
        self.setMinimumWidth(560)
        self._cur = current_seed
        lay = QVBoxLayout(self); lay.setContentsMargins(20, 18, 20, 16); lay.setSpacing(8)
        t = QLabel("Paste seed + coordinates"); t.setObjectName("PageTitle"); lay.addWidget(t)
        h = QLabel("Anything holding a seed and / or a position: a chat line, an F3+C command,\n"
                   "“X: 120 Y: 64 Z: -900”, “-4766305904110624072 at -11434766 5843432”…")
        h.setObjectName("Body"); lay.addWidget(h)
        self.edit = QLineEdit(); self.edit.setObjectName("SeedEdit")
        self.edit.setPlaceholderText("seed  X  Z   ·   /tp @s X Y Z   ·   seed: …  x: …  z: …")
        self.edit.textChanged.connect(self._preview)
        lay.addWidget(self.edit)
        self.pv = QLabel(" "); self.pv.setObjectName("Body"); self.pv.setTextFormat(Qt.TextFormat.RichText)
        self.pv.setMinimumHeight(40); self.pv.setWordWrap(True)
        lay.addWidget(self.pv)
        self.pin = QCheckBox("Put a pin on the spot"); self.pin.setChecked(True)
        lay.addWidget(self.pin)
        row = QHBoxLayout(); row.addStretch(1)
        cancel = QPushButton("Cancel"); cancel.setProperty("kind", "ghost"); cancel.clicked.connect(self.reject)
        self.ok = QPushButton("Go"); self.ok.setDefault(True); self.ok.clicked.connect(self.accept)
        row.addWidget(cancel); row.addWidget(self.ok)
        lay.addLayout(row)
        clip = QGuiApplication.clipboard().text() or ""
        if clip and len(clip) < 400:
            r = parse_seed_coords(clip)
            if r["seed"] is not None or r["x"] is not None:
                self.edit.setText(clip.strip())
                self.edit.selectAll()
        self._preview()

    def result_value(self):
        return parse_seed_coords(self.edit.text())

    def _preview(self):
        r = self.result_value()
        parts = []
        if r["seed"] is not None:
            same = " (already loaded)" if r["seed"] == self._cur else ""
            parts.append(f"<b>Seed</b> {r['seed']}{same}")
        if r["x"] is not None:
            y = f"  Y {r['y']}" if r["y"] is not None else ""
            parts.append(f"<b>Go to</b> X {r['x']:,}{y}  Z {r['z']:,}".replace(",", " "))
        if r["note"]:
            parts.append(f"<i>{r['note']}</i>")
        ok = bool(parts)
        if not ok:
            txt = "Nothing recognised yet." if self.edit.text().strip() else " "
        else:
            txt = "&nbsp;&nbsp;·&nbsp;&nbsp;".join(parts)
        self.pv.setText(txt)
        self.ok.setEnabled(ok)
        self.pin.setEnabled(r["x"] is not None)


class _Bridge(QObject):
    frac = Signal(float)
    stage = Signal(str)
    done = Signal(object)             # dict(ok, n, size, stats, error, out)


class ConvertSeedsDialog(QDialog):
    """Text seed list → compact .seeds, in a thread, with a real progress bar."""
    useList = Signal(str)             # "Use this list" → path of the .seeds

    def __init__(self, parent=None, in_path=None, offer_use=False):
        super().__init__(parent)
        self.setWindowTitle("Convert a seed list to .seeds")
        self.setMinimumWidth(560)
        self._offer_use = offer_use
        self._cancel = threading.Event()
        self._busy = False
        self._out = None
        lay = QVBoxLayout(self); lay.setContentsMargins(20, 18, 20, 16); lay.setSpacing(8)
        t = QLabel("Convert a seed list to .seeds"); t.setObjectName("PageTitle"); lay.addWidget(t)
        b = QLabel("A text list (one seed per line) becomes a compact .seeds file: sorted, without\n"
                   "duplicates, about 3× smaller, and it opens instantly in SSA (the seed count is in\n"
                   "its header). Comment lines (#) and unreadable lines are skipped.")
        b.setObjectName("Body"); lay.addWidget(b)
        row = QHBoxLayout()
        self.src = QLineEdit(); self.src.setReadOnly(True); self.src.setPlaceholderText("Text seed list…")
        pick = QPushButton("Choose…"); pick.setProperty("kind", "ghost"); pick.clicked.connect(self._pick)
        row.addWidget(self.src, 1); row.addWidget(pick)
        lay.addLayout(row)
        self.bar = QProgressBar(); self.bar.setRange(0, 1000); self.bar.setValue(0); self.bar.setTextVisible(False)
        lay.addWidget(self.bar)
        self.msg = QLabel(" "); self.msg.setObjectName("Body"); self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        r2 = QHBoxLayout(); r2.addStretch(1)
        self.use = QPushButton("Use this list"); self.use.setVisible(False)
        self.use.clicked.connect(self._use)
        self.close_b = QPushButton("Close"); self.close_b.setProperty("kind", "ghost")
        self.close_b.clicked.connect(self.reject)
        self.go = QPushButton("Convert"); self.go.setDefault(True); self.go.clicked.connect(self._start)
        self.go.setEnabled(False)
        r2.addWidget(self.use); r2.addWidget(self.close_b); r2.addWidget(self.go)
        lay.addLayout(r2)
        self._br = _Bridge()
        self._br.frac.connect(lambda f: self.bar.setValue(int(f * 900)))
        self._br.stage.connect(self._on_stage)
        self._br.done.connect(self._on_done)
        if in_path:
            self._set_src(in_path)

    def _set_src(self, path):
        self.src.setText(path)
        try:
            mb = os.path.getsize(path) / 1e6
            self.msg.setText(f"{os.path.basename(path)} · {mb:,.1f} MB".replace(",", " "))
        except OSError as e:
            self.msg.setText(str(e))
        self.go.setEnabled(True)
        self.use.setVisible(False)
        self.bar.setValue(0)

    def _pick(self):
        path, _ = QFileDialog.getOpenFileName(self, "Text seed list", user_path(),
                                              "Text seed lists (*.txt *.csv *.seedlist);;All files (*.*)")
        if path:
            remember_path(path)
            self._set_src(path)

    def _start(self):
        if self._busy:                          # the button reads "Cancel" while running
            self._cancel.set()
            self.go.setEnabled(False)
            self.msg.setText("Cancelling…")
            return
        src = self.src.text()
        if not src:
            return
        import seedlist_io
        if seedlist_io.is_seeds_file(src):
            self.msg.setText("This file is already a compact .seeds list.")
            return
        base = os.path.splitext(src)[0] + ".seeds"
        out, _ = QFileDialog.getSaveFileName(self, "Save the compact list as", base, "Compact seed list (*.seeds)")
        if not out:
            return
        if not out.lower().endswith(".seeds"):
            out += ".seeds"
        remember_path(out)
        self._out = out
        self._cancel.clear()
        self._busy = True
        self.go.setText("Cancel"); self.go.setProperty("kind", "ghost")
        self.go.style().unpolish(self.go); self.go.style().polish(self.go)
        self.close_b.setEnabled(False)
        self.msg.setText("Reading…")
        br, cancel = self._br, self._cancel

        def work():
            st = {}
            try:
                n, size = seedlist_io.convert_to_seeds(
                    src, out, progress=br.stage.emit, on_frac=br.frac.emit, cancel=cancel, stats=st)
                br.done.emit(dict(ok=True, n=n, size=size, stats=st, out=out))
            except seedlist_io.ConvertCancelled:
                br.done.emit(dict(ok=False, error="Cancelled — nothing was written."))
            except MemoryError:
                br.done.emit(dict(ok=False, error="Not enough memory for this list."))
            except Exception as e:           # noqa: BLE001 — shown to the user
                br.done.emit(dict(ok=False, error=str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _on_stage(self, s):
        if s.startswith("sorting"):
            self.bar.setValue(920)
            self.msg.setText(f"Sorting and removing duplicates ({s[8:]} seeds)…")
        elif s == "compressing":
            self.bar.setValue(960)
            self.msg.setText("Compressing…")

    def _on_done(self, r):
        self._busy = False
        self.go.setText("Convert"); self.go.setProperty("kind", "")
        self.go.style().unpolish(self.go); self.go.style().polish(self.go)
        self.go.setEnabled(True)
        self.close_b.setEnabled(True)
        if not r["ok"]:
            self.bar.setValue(0)
            self.msg.setText("⚠ " + r["error"])
            return
        self.bar.setValue(1000)
        st = r["stats"]
        src_b = max(1, st.get("in_bytes", 1))
        extra = []
        if st.get("duplicates"):
            extra.append(f"{st['duplicates']:,} duplicate{'s' if st['duplicates'] > 1 else ''} removed")
        if st.get("lines_bad"):
            extra.append(f"{st['lines_bad']:,} unreadable line{'s' if st['lines_bad'] > 1 else ''} skipped")
        txt = (f"✓ {r['n']:,} seeds → {os.path.basename(r['out'])} · {r['size'] / 1e6:,.1f} MB "
               f"({src_b / max(1, r['size']):.1f}× smaller)")
        if extra:
            txt += "\n" + " · ".join(extra)
        self.msg.setText(txt.replace(",", " "))
        self.use.setVisible(self._offer_use)
        self._out = r["out"]

    def _use(self):
        if self._out:
            self.useList.emit(self._out)
        self.accept()

    def reject(self):
        if self._busy:                          # Esc / window close = cancel first
            self._cancel.set()
            self.msg.setText("Cancelling…")
            QTimer.singleShot(150, self._close_when_idle)
            return
        super().reject()

    def _close_when_idle(self):
        if self._busy:
            QTimer.singleShot(150, self._close_when_idle)
        else:
            super().reject()


BIG_TEXT_LIST = 30_000_000            # bytes: above this, offer the conversion


def maybe_convert(parent, path):
    """A big text seed list is picked: offer to convert it first (a .seeds opens
    instantly; a text list is re-read and re-parsed at every search). Returns
    the path to use (the .seeds if converted and chosen, else the original)."""
    import seedlist_io
    try:
        big = (not seedlist_io.is_seeds_file(path)) and os.path.getsize(path) > BIG_TEXT_LIST
    except OSError:
        return path
    if not big:
        return path
    from PySide6.QtWidgets import QMessageBox
    box = QMessageBox(parent)
    box.setWindowTitle("Large text seed list")
    box.setText(f"{os.path.basename(path)} is a large text list "
                f"({os.path.getsize(path) / 1e6:,.0f} MB).".replace(",", " "))
    box.setInformativeText("Convert it to a compact .seeds file first? It is several times smaller, "
                           "opens instantly and loses no seed. (Tools → Convert a seed list.)")
    conv = box.addButton("Convert first", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Use it as it is", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(conv)
    box.exec()
    if box.clickedButton() is not conv:
        return path
    got = []
    dlg = ConvertSeedsDialog(parent, path, offer_use=True)
    dlg.useList.connect(got.append)
    QTimer.singleShot(0, dlg._start)
    dlg.exec()
    dlg.deleteLater()
    return got[0] if got else path
