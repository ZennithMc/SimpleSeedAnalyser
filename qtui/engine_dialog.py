"""
qtui/engine_dialog.py — "Engine" window of a session: CPU or GPU, and why.

  ┌ Engine for this session ───────────────────────────────────────────┐
  │ (•) Auto — analyse at Start  ( ) Analysed plan  ( ) As set  ( ) CPU │
  │ [ Analyse now ]                                                     │
  │ MEASURED            CPU only       ████████████   6.3 M/s           │
  │                     structures     ██████████████████  9.1 M/s ✓    │
  │                     #3 biome …     GPU not engaged                  │
  │ NOT ON THE GPU (why) 12 × depends on another condition …            │
  │ Recommendation: GPU pre-filter on “structures” · ×1.4 vs CPU        │
  │                          [ Apply ]  [ Apply and start search ]  [Close]  │
  └─────────────────────────────────────────────────────────────────────┘

The analysis is the one of GPU: Auto (gpu_planner.plan_gpu) run on demand,
shown live, with the reason for every condition that cannot go on the GPU.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from . import theme
from .widgets import Segmented, body, section_title

MODES = (("auto", "Auto", "Analyse when the search starts (a few seconds, once per session) "
                          "and use the fastest."),
         ("plan", "Analysed plan", "Use the result of the last analysis below."),
         ("as_set", "As set", "The GPU boxes ticked on the conditions."),
         ("cpu", "CPU only", "No GPU at all."))


def fmt_rate(r):
    from .sessions_page import fmt_rate as f
    return f(r)


class _Bus(QObject):
    event = Signal(str, object)


class _Bar(QWidget):
    """One measured line: name · bar · speed."""
    def __init__(self, name, parent=None, shown=None):
        super().__init__(parent)
        self.shown = shown or name
        self.name, self.rate, self.state, self.best, self.max = name, None, "waiting", False, 1.0
        self.why = None
        self.setMinimumHeight(26); self.setMaximumHeight(26)

    def set(self, rate=None, state=None, best=None, vmax=None, why=None):
        if why is not None:
            self.why = why
            self.setToolTip(f"<b>{self.shown}</b><br>No speed: {why}")
        if rate is not None:
            self.rate = rate
        if state:
            self.state = state
        if best is not None:
            self.best = best
        if vmax:
            self.max = vmax
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        name_w = min(250, int(w * 0.42))
        p.setFont(theme.ui_font(9, QFont.Weight.DemiBold if self.best else QFont.Weight.Normal))
        p.setPen(QColor(theme.TEXT if self.best else theme.TEXT2))
        txt = p.fontMetrics().elidedText(self.shown, Qt.TextElideMode.ElideRight, name_w - 8)
        p.drawText(QRectF(0, 0, name_w, h), Qt.AlignmentFlag.AlignVCenter, txt)
        bx, bw = name_w, w - name_w - 120
        track = QRectF(bx, h / 2 - 5, max(10, bw), 10)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(theme.BG3)); p.drawRoundedRect(track, 5, 5)
        right = QRectF(w - 114, 0, 114, h)
        p.setFont(theme.mono_font(8.5))
        if self.state == "measured" and self.rate:
            f = max(0.02, min(1.0, self.rate / max(self.max, 1e-9)))
            col = QColor(theme.OK if self.best else theme.ACCENT)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(track.left(), track.top(), track.width() * f, track.height()), 5, 5)
            p.setPen(QColor(theme.TEXT))
            p.drawText(right, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       fmt_rate(self.rate) + ("  ✓" if self.best else ""))
        else:
            p.setPen(QColor(theme.TEXT3))
            short = "GPU not engaged"
            if self.state == "not_engaged" and self.why:
                w = self.why
                short = ("no progress yet" if w.startswith("the GPU ran") else
                         "GPU refused ⓘ" if w.startswith("GPU pre-filter not used") else "back to CPU ⓘ")
            msg = {"waiting": "waiting", "measuring": "measuring…",
                   "not_engaged": short}.get(self.state, self.state)
            p.drawText(right, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, msg)
        p.end()


class EngineDialog(QDialog):
    """mode_changed(mode, plan_or_None) when the user chooses; start_requested()"""
    chosen = Signal(str, object)
    modeChosen = Signal(str)                  # "inc" / "fam48": the analysis recommends it
    startRequested = Signal()

    def __init__(self, get_context, mode="auto", plan=None, parent=None):
        """get_context() → (conditions, mc, start, threads, search_mode[, compare_modes])
        or an error text (e.g. no condition)."""
        super().__init__(parent)
        self.setWindowTitle("Engine for this session")
        self.resize(700, 680)
        self.get_context = get_context
        self.plan = plan
        self._stop = None
        self._bars = {}
        self._prefix = ""                     # current mode section ("48-bit family · ")
        self._cur_mode = None
        try:
            ctx = get_context()
            if not isinstance(ctx, str):
                self._cur_mode = ctx[4]
        except Exception:
            pass
        self._bus = _Bus(self)
        self._bus.event.connect(self._on_event)

        v = QVBoxLayout(self); v.setContentsMargins(18, 16, 18, 14); v.setSpacing(10)
        t = QLabel("Engine for this session"); t.setObjectName("PageTitle"); v.addWidget(t)
        v.addWidget(body("The GPU never replaces the CPU: it pre-filters the seeds on ONE "
                         "condition, then the C engine re-checks every survivor with the whole "
                         "session — results are always exact. The analysis runs the real search "
                         "a few seconds with each option, in each search mode (Incremental and "
                         "48-bit family), and keeps the fastest.",
                         muted=True, small=True))
        row = QHBoxLayout()
        self.seg = Segmented([m[1] for m in MODES], dict((k, l) for k, l, _t in MODES).get(mode, "Auto"))
        self._key_of = {l: k for k, l, _t in MODES}
        self.radios = {k: self.seg.buttons[l] for k, l, _t in MODES}
        for k, l, tip in MODES:
            self.radios[k].setToolTip(tip)
        row.addWidget(self.seg); row.addStretch(1)
        v.addLayout(row)
        self.radios["plan"].setEnabled(plan is not None)

        arow = QHBoxLayout()
        self.b_analyse = QPushButton("  Analyse now"); self.b_analyse.setProperty("kind", "primary")
        self.b_analyse.clicked.connect(self._analyse)
        self.status = body("", muted=True, wrap=False)
        arow.addWidget(self.b_analyse); arow.addSpacing(8); arow.addWidget(self.status, 1)
        v.addLayout(arow)

        # measured lines + reasons in ONE scroll area: the lines keep their
        # height (two modes × several lines no longer squeeze into each other)
        content = QWidget(); cv = QVBoxLayout(content)
        cv.setContentsMargins(0, 0, 6, 0); cv.setSpacing(8)
        cv.addWidget(section_title("Measured"))
        self.meas_host = QWidget(); self.meas = QVBoxLayout(self.meas_host)
        self.meas.setContentsMargins(0, 0, 0, 0); self.meas.setSpacing(2)
        cv.addWidget(self.meas_host)
        cv.addWidget(section_title("Not on the GPU — why"))
        self.why_host = QWidget(); self.why = QVBoxLayout(self.why_host)
        self.why.setContentsMargins(0, 0, 0, 0); self.why.setSpacing(3)
        cv.addWidget(self.why_host)
        cv.addStretch(1)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(content)
        sc.setFrameShape(QFrame.Shape.NoFrame); sc.setMinimumHeight(160)
        sc.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        v.addWidget(sc, 1)
        self.reco = QLabel(""); self.reco.setWordWrap(True); self.reco.setObjectName("Body")
        v.addWidget(self.reco)

        brow = QHBoxLayout(); brow.addStretch(1)
        self.b_use = QPushButton("Apply"); self.b_use.clicked.connect(self._use)
        self.b_use.setToolTip("Use this engine setting (and the recommended search mode) for your "
                              "next searches. Nothing starts now; the window stays open.")
        self.b_use_start = QPushButton("Apply and start search"); self.b_use_start.setProperty("kind", "primary")
        self.b_use_start.setToolTip("The same, then start the search right away and close this window.")
        self.b_use_start.clicked.connect(lambda: (self._use(), self.startRequested.emit(), self.close()))
        close = QPushButton("Close"); close.setProperty("kind", "ghost"); close.clicked.connect(self.close)
        for b in (self.b_use, self.b_use_start, close):
            brow.addWidget(b)
        v.addLayout(brow)
        if plan is not None:
            self._show_plan(plan)
        else:
            self.reco.setText("No analysis yet for this session.")

    # ── analysis ────────────────────────────────────────────────────────────
    def _analyse(self):
        ctx = self.get_context()
        if isinstance(ctx, str):
            self.status.setText(ctx)
            return
        conds, mc, start, threads, smode = ctx[:5]
        compare = bool(ctx[5]) if len(ctx) > 5 else False
        self._cur_mode = smode
        self._clear()
        self.b_analyse.setEnabled(False)
        self.status.setText("Analysing…")
        self.reco.setText("")
        self._stop = threading.Event()
        bus, stop = self._bus, self._stop

        def _emit(*a):                 # the dialog may be gone (Esc during the analysis)
            try:
                bus.event.emit(*a)
            except RuntimeError:
                stop.set()

        def work():
            import gpu_planner as gp
            try:
                kw = dict(stop=stop, use_cache=False,
                          status=lambda t: _emit("status", t),
                          events=lambda kind, **d: _emit(kind, d))
                if compare:
                    gp.plan_modes(conds, mc, start, threads, current=smode, **kw)
                else:
                    gp.plan_gpu(conds, mc, start, threads, smode, **kw)
            except Exception as e:
                _emit("error", f"{type(e).__name__}: {e}")
        threading.Thread(target=work, daemon=True, name="ssa-engine-analysis").start()

    def _clear(self):
        for lay in (self.meas, self.why):
            while lay.count():
                it = lay.takeAt(0)
                if it.widget():
                    it.widget().hide(); it.widget().deleteLater()
        self._bars = {}
        self._prefix = ""

    def _bar(self, name):
        b = self._bars.get(name)
        if b is None:
            pre = next((q for q in self._mode_prefixes() if name.startswith(q)), "")
            b = _Bar(name, shown=name[len(pre):]); self._bars[name] = b; self.meas.addWidget(b)
        return b

    @staticmethod
    def _mode_prefixes():
        import gpu_planner as gp
        return [n + " · " for n in gp.MODE_NAMES.values()]

    def _mode_header(self, title):
        lab = QLabel(title.upper())
        lab.setStyleSheet(f"color:{theme.TEXT3}; font-size:7.5pt; font-weight:600; "
                          f"letter-spacing:0.8px; padding-top:6px;")
        self.meas.addWidget(lab)

    def _fill_why(self, excluded):
        while self.why.count():
            it = self.why.takeAt(0)
            if it.widget():
                it.widget().hide(); it.widget().deleteLater()
        if not excluded:
            self.why.addWidget(body("Nothing: every condition that a GPU filter supports was "
                                    "measured above.", muted=True, small=True))
            return
        # grouped by reason (80 biome conditions → one line with the count)
        by = {}
        for name, why in excluded:
            by.setdefault(why, []).append(name)
        for why, names in sorted(by.items(), key=lambda kv: -len(kv[1])):
            shown = ", ".join(names[:4]) + (f" … (+{len(names) - 4})" if len(names) > 4 else "")
            lab = QLabel(f"<b>{len(names)} ×</b> {why}<br><span style='color:{theme.TEXT3}'>"
                         f"{shown}</span>")
            lab.setWordWrap(True); lab.setObjectName("Body")
            lab.setStyleSheet("font-size: 8.5pt;")
            self.why.addWidget(lab)

    def _on_event(self, kind, d):
        if kind == "status":
            self.status.setText(str(d))
        elif kind == "error":
            self.status.setText("Analysis failed: " + str(d))
            self.b_analyse.setEnabled(True)
        elif kind == "mode":
            self._prefix = d["title"] + " · "
            self._mode_header(d["title"])
        elif kind == "candidates":
            pre = d.get("prefix", self._prefix)
            keep = {pre + "CPU only", *d["names"]}
            for n in [n for n in self._bars if n.startswith(pre) and n not in keep]:
                b = self._bars.pop(n); b.hide(); b.deleteLater()      # dropped by the ranking
            self._bar(pre + "CPU only")
            for n in d["names"]:
                self._bar(n)
            if not d["names"]:
                self._bar(pre + "CPU only").set(state="n/a")
            self._fill_why(d.get("excluded", []))
        elif kind == "measuring":
            self._bar(d["name"]).set(state="measuring")
        elif kind == "measured":
            self._bar(d["name"]).set(rate=d.get("rate"), why=d.get("why"),
                                     state="measured" if d.get("rate") else "not_engaged")
            vmax = max([b.rate or 0 for b in self._bars.values()] + [1e-9])
            for b in self._bars.values():
                b.set(vmax=vmax)
        elif kind == "done":
            self.plan = d["plan"]
            self.b_analyse.setEnabled(True)
            self.status.setText("Done.")
            self.radios["plan"].setEnabled(True)
            self.radios["plan"].setChecked(True)
            self._show_plan(self.plan)

    def _show_plan(self, plan):
        import gpu_planner as gp
        if not self._bars:
            seen = None
            for name, _r in plan.report:              # reopened: rebuild the mode sections
                pre = next((q for q in self._mode_prefixes() if name.startswith(q)), "")
                if pre and pre != seen:
                    self._mode_header(pre[:-3]); seen = pre
                self._bar(name)
            if not plan.report:
                self._bar("CPU only").set(state="n/a")
        notes = getattr(plan, "notes", {}) or {}
        for name, rate in plan.report:
            self._bar(name).set(rate=rate, why=notes.get(name),
                                state="measured" if rate else "not_engaged")
        vmax = max([r or 0 for _n, r in plan.report] + [1e-9])
        chosen = getattr(plan, "best", None) or (
            "CPU only" if "CPU" in plan.label else plan.label.split("GPU · ", 1)[-1])
        for n, b in self._bars.items():
            b.set(vmax=vmax, best=(n == chosen))
        self._fill_why(plan.excluded)
        col = theme.OK if "GPU" in plan.label and "CPU" not in plan.label else theme.TEXT
        txt = plan.detail
        pm = getattr(plan, "mode", "")
        if getattr(plan, "modes", None) and pm:
            name = gp.MODE_NAMES.get(pm, pm)
            if self._cur_mode and pm != self._cur_mode:
                txt = (f"switch the search mode to <b>{name}</b> — " + txt +
                       " “Apply” switches it.")
            else:
                txt = f"keep <b>{name}</b>. " + txt
            if pm == "fam48":
                txt += (f"<br><span style='color:{theme.TEXT3}'>48-bit family: seeds come in "
                        "families of 65 536 that share the same structure layout (biomes "
                        "differ), so many results look alike.</span>")
        self.reco.setText(f"<b style='color:{col}'>Recommendation:</b> {txt}")

    def _use(self):
        mode = self._key_of.get(self.seg.value(), "auto")
        p = self.plan
        if p is not None and getattr(p, "modes", None) and p.mode and self._cur_mode \
                and p.mode != self._cur_mode:
            self.modeChosen.emit(p.mode)          # first: the plan is valid for that mode
        self.chosen.emit(mode, p if mode == "plan" else None)

    def closeEvent(self, e):
        if self._stop is not None:
            self._stop.set()
        super().closeEvent(e)

    def done(self, r):
        # Esc / reject() never sends closeEvent: the analysis kept running in the
        # background (competing with the next search)
        if self._stop is not None:
            self._stop.set()
        super().done(r)
