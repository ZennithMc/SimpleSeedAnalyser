"""
qtui/loot_dialog.py — the loot of a structure's chests (25/10 exactness first;
06/11 the look). One card per container: the game's own chest window filled as the
game fills it (items, counts, durability, tooltips from the player's Minecraft —
mc_assets), the totals, the /tp, and the container's loot table (loot_table_dialog).
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

import mc_assets as ma

from . import icons, theme
from .mc_widgets import (ChestView, ItemSlot, container_icon, container_kind, container_title,
                         hide_tooltip)


def item_name(item: str) -> str:
    return item.replace("minecraft:", "").replace("_", " ").capitalize()


_CW = {"north": (1, 0), "east": (0, 1), "south": (-1, 0), "west": (0, -1)}   # facing → clockwise


def merge_double_chests(cards):
    """(31/10) two chest halves side by side with the same facing are one double
    chest in the game (ChestType LEFT / RIGHT): one card of 54 slots, the RIGHT half
    on top (DoubleBlockCombiner: RIGHT = FIRST). LEFT's partner is clockwise of its
    facing."""
    by = {}
    for c in cards:
        if c.get("block") == "chest" and c.get("facing") in _CW and c.get("pos") and c["pos"][1] is not None:
            by[tuple(c["pos"])] = c
    used, out = set(), []
    for c in cards:
        if id(c) in used:
            continue
        p, f = c.get("pos"), c.get("facing")
        mate = None
        if tuple(p or ()) in by:
            dx, dz = _CW[f]
            q = by.get((p[0] + dx, p[1], p[2] + dz))
            if q is not None and q.get("facing") == f and id(q) not in used:
                mate = q                       # c is the LEFT half, q the RIGHT one
        if mate is None or c.get("slots") is None or mate.get("slots") is None:
            out.append(c)
            continue
        used.add(id(c)); used.add(id(mate))
        left, right = c, mate
        tb = right["table"] if right["table"] == left["table"] else f"{right['table']} + {left['table']}"
        out.append(dict(name=f"Double chest  ({right['name']} + {left['name']})", pos=right["pos"],
                        pos2=left["pos"], block="chest",
                        table=tb, seed=right.get("seed"), seed2=left.get("seed"),
                        slots=list(right["slots"]) + list(left["slots"]),
                        approx=list(right.get("approx") or []) + list(left.get("approx") or []),
                        note=(f"Top half at X {right['pos'][0]} Z {right['pos'][2]}, bottom half at "
                              f"X {left['pos'][0]} Z {left['pos'][2]}. " + (right.get("note") or "")).strip(),
                        exact=right.get("exact", True) and left.get("exact", True)))
    return out


def _pix(img, size):
    if img is None:
        return QPixmap()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(img.width() / size)
    return pm


def _stack_key(st):
    return (st.item, tuple(getattr(st, "ench", []) or ()), getattr(st, "potion", None),
            getattr(st, "name_key", None))


class _Badge(QLabel):
    """a small round mark with a tooltip (≈ approximate, i note)"""

    def __init__(self, text, tip, color):
        super().__init__(text)
        self.setToolTip(tip)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(22, 22)
        self.setStyleSheet(f"QLabel {{ color: {color}; border: 1px solid {color}; border-radius: 11px;"
                           f" font-weight: 700; font-size: 9pt; background: transparent; }}")


CARD_W = 568                 # the game's window at 3 px per pixel (528) + the card's margins


class _FlowGrid(QWidget):
    """cards of one width, as many columns as fit, centred"""

    def __init__(self, min_col=CARD_W):
        super().__init__()
        self.cards, self.min_col = [], min_col
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(16); self.grid.setVerticalSpacing(16)
        self.cols = 0

    def add(self, w):
        w.setFixedWidth(self.min_col)
        self.cards.append(w)

    def resizeEvent(self, e):
        self._place()
        super().resizeEvent(e)

    def _place(self, force=False):
        cols = max(1, min(len(self.cards), (self.width() + 16) // (self.min_col + 16)))
        if cols == self.cols and not force:
            return
        self.cols = cols
        for w in self.cards:
            self.grid.removeWidget(w)
        for i, w in enumerate(self.cards):
            self.grid.addWidget(w, i // cols, 1 + i % cols, Qt.AlignmentFlag.AlignTop)
        for c in range(6):
            self.grid.setColumnStretch(c, 1 if c in (0, cols + 1) else 0)
        self.grid.setRowStretch(len(self.cards) // cols + 1, 1)


class ChestCard(QFrame):
    """one container: header (icon, name, where, marks, Loot table, /tp), the game's
    window, the totals"""

    def __init__(self, ch, version, key, dlg):
        super().__init__()
        self.ch, self.version = ch, version
        self.setObjectName("LootCard")
        v = QVBoxLayout(self); v.setContentsMargins(16, 14, 16, 14); v.setSpacing(12)
        kind = container_kind(ch)
        hd = QHBoxLayout(); hd.setSpacing(10)
        ic = QLabel()
        ic.setPixmap(_pix(container_icon(kind, 64, version), 34))
        ic.setFixedSize(34, 34)
        hd.addWidget(ic)
        col = QVBoxLayout(); col.setSpacing(0)
        t = QLabel(ch["name"]); t.setObjectName("LootTitle")
        tip = [ch.get("table", "")]
        if ch.get("seed") is not None:
            tip.append(f"Loot seed {ch['seed']}" + (f" / {ch['seed2']}" if ch.get("seed2") is not None else ""))
        t.setToolTip("\n".join(x for x in tip if x))
        col.addWidget(t)
        x, y, z = ch["pos"]
        where = QLabel(f"{x}  {'' if y is None else str(y) + '  '}{z}")
        where.setObjectName("LootWhere"); where.setFont(theme.mono_font(9))
        where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(where)
        hd.addLayout(col, 1)
        notes = []
        if ch.get("approx"):
            notes.append("Approximate: " + ", ".join(ch["approx"]))
        if ch.get("exact") is False or ch.get("approx"):
            hd.addWidget(_Badge("≈", "\n\n".join(notes + ([ch["note"]] if ch.get("note") else []))
                                or "May differ from the game", "#d6a03f"))
        elif ch.get("note"):
            hd.addWidget(_Badge("i", ch["note"], theme.TEXT3))
        tb = (ch.get("table") or "").split(" + ")[0]
        if tb and ch.get("slots") is not None:
            lt = QToolButton(); lt.setText("Loot table"); lt.setObjectName("LootBtn")
            icons.bind(lt, "legend", 15)
            lt.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            lt.setToolTip("Every item this chest can hold, with its chance")
            lt.setCursor(Qt.CursorShape.PointingHandCursor)
            lt.clicked.connect(lambda _=False, tb=tb: dlg.open_table(tb, focus_slots=ch.get("slots")))
            hd.addWidget(lt)
        cmd = self._tp(key, ch)
        if cmd:
            cp = QToolButton(); cp.setText("/tp"); cp.setObjectName("LootBtn")
            icons.bind(cp, "copy", 15)
            cp.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            cp.setToolTip(cmd)
            cp.setCursor(Qt.CursorShape.PointingHandCursor)

            def _copy(_=False, cmd=cmd, b=cp):
                QGuiApplication.clipboard().setText(cmd)
                b.setText("Copied")

                def _reset():
                    try:
                        b.setText("/tp")
                    except RuntimeError:
                        pass
                QTimer.singleShot(1300, _reset)
            cp.clicked.connect(_copy)
            hd.addWidget(cp)
        v.addLayout(hd)
        slots = ch.get("slots")
        if slots is None:
            m = QLabel(ch.get("note") or "Nothing to show here.")
            m.setObjectName("LootMuted"); m.setWordWrap(True)
            v.addWidget(m)
            return
        self.view = ChestView(slots, container_title(ch, version), version)
        v.addWidget(self.view)
        tot = {}
        rep = {}
        for st in slots:
            if st is not None:
                k = _stack_key(st)
                tot[k] = tot.get(k, 0) + st.count
                rep.setdefault(k, st)
        if tot:
            row = _Flow()
            for k, n in sorted(tot.items(), key=lambda kv: (-kv[1], kv[0][0])):
                row.add(ItemSlot(rep[k], version, k=2, count=n, framed=True))
            sep = QFrame(); sep.setObjectName("LootSep"); sep.setFixedHeight(1)
            v.addWidget(sep)
            v.addWidget(row)

    @staticmethod
    def _tp(key, ch):
        from struct_info import tp
        x, y, z = ch["pos"]
        dim = 1 if str(key).startswith("end_city") else 0
        if str(key) in ("fortress", "bastion") or str(key).endswith("_nether"):
            dim = -1
        ty = ch.get("tp_y")
        return tp(dim, x, ty if ty is not None else (None if y is None else y + 1), z)


class _Flow(QWidget):
    """small widgets in rows that wrap"""

    def __init__(self):
        super().__init__()
        self.items = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def add(self, w):
        w.setParent(self)
        self.items.append(w)

    def _layout(self, width, move=True):
        x = y = 0
        rh = 0
        for w in self.items:
            s = w.sizeHint() if not w.width() else w.size()
            if x and x + s.width() > width:
                x, y = 0, y + rh + 2
                rh = 0
            if move:
                w.move(x, y)
                w.show()
            x += s.width() + 2
            rh = max(rh, s.height())
        return y + rh

    def resizeEvent(self, e):
        self._layout(self.width())

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._layout(w, move=False)

    def sizeHint(self):
        return QSize(400, self._layout(max(200, self.width() or 400), move=False))


class LootDialog(QDialog):
    def __init__(self, key, seed, mc, x, z, title, parent=None, focus=None):
        super().__init__(parent)
        self.focus = tuple(focus) if focus else None      # (31/10) a chest to show first
        self.key, self.seed, self.mc, self.x, self.z = key, int(seed), int(mc), int(x), int(z)
        self.title_ = title
        self.setWindowTitle(f"Loot · {title}")
        self.resize(1240, 860)
        acc = theme.ACCENT or "#7c9cff"
        self.setStyleSheet(
            f"QDialog {{ background: {theme.BG1 or '#1b1f27'}; }}"
            f"#LootCard {{ background: {theme.BG2 or '#232833'}; border: 1px solid {theme.LINE2};"
            f" border-radius: 14px; }}"
            f"#LootCard[focus=\"true\"] {{ border: 2px solid #ffc928; }}"
            f"#LootTitle {{ font-size: 11pt; font-weight: 600; }}"
            f"#LootWhere {{ color: {theme.TEXT3}; }}"
            f"#LootMuted {{ color: {theme.TEXT3}; }}"
            f"#LootSep {{ background: {theme.LINE2}; }}"
            f"#LootHead {{ font-size: 15pt; font-weight: 650; }}"
            f"#LootSub {{ color: {theme.TEXT3}; }}"
            f"#LootBtn {{ padding: 5px 10px; border: 1px solid {theme.LINE2}; border-radius: 8px;"
            f" background: {theme.BG3 or theme.BG2}; color: {theme.TEXT2}; }}"
            f"#LootBtn:hover {{ border-color: {acc}; color: {theme.TEXT}; }}"
            f"#LootBanner {{ background: {theme.BG2}; border: 1px solid #d6a03f; border-radius: 10px; }}")
        v = QVBoxLayout(self); v.setContentsMargins(20, 16, 20, 14); v.setSpacing(14)
        top = QHBoxLayout(); top.setSpacing(12)
        try:
            from . import struct_icons
            ic = QLabel(); ic.setPixmap(struct_icons.pixmap(key, 36)); top.addWidget(ic)
        except Exception:                                # noqa: BLE001
            pass
        tc = QVBoxLayout(); tc.setSpacing(0)
        self._where = ""
        if "  X " in title:                      # "Village  X 120  Z -340"
            title, rest = title.split("  X ", 1)
            self._where = "X " + " ".join(rest.split())
        h = QLabel(title); h.setObjectName("LootHead"); tc.addWidget(h)
        self.sub_lbl = QLabel(""); self.sub_lbl.setObjectName("LootSub"); tc.addWidget(self.sub_lbl)
        top.addLayout(tc, 1)
        import loot_engine as le
        subs = le.MC_SUBVERSIONS.get(self.mc)
        self.sub = None
        if subs:
            self.sub = QComboBox()
            self.sub.addItems(subs)
            self.sub.setToolTip("The exact game version (its loot tables)")
            try:                                  # (31/10) the exact version of the top bar
                from .world import current_exact
                ex = current_exact()
                if ex in subs:
                    self.sub.setCurrentText(ex)
            except Exception:
                pass
            self.sub.currentTextChanged.connect(lambda _t: self._fill())
            top.addWidget(self.sub)
        lt = QPushButton("  Loot tables"); icons.bind(lt, "legend", 16)
        lt.setToolTip("Every chest's loot table, every item with its chance")
        lt.clicked.connect(lambda: self.open_table(None))
        top.addWidget(lt)
        v.addLayout(top)
        self.banner = None
        if not ma.find_jars():
            self.banner = self._banner()
            v.addWidget(self.banner)
        self.area = QScrollArea(); self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setStyleSheet("QScrollArea { background: transparent; } "
                                "QScrollArea > QWidget > QWidget { background: transparent; }")
        v.addWidget(self.area, 1)
        self._fill()

    def _banner(self):
        b = QFrame(); b.setObjectName("LootBanner")
        h = QHBoxLayout(b); h.setContentsMargins(14, 8, 10, 8)
        t = QLabel("Minecraft not found — items shown without the game's textures.")
        t.setObjectName("LootSub")
        h.addWidget(t, 1)
        pick = QPushButton("Choose the Minecraft folder…")
        pick.clicked.connect(self._pick_folder)
        h.addWidget(pick)
        return b

    def _pick_folder(self):
        from .mc_folder import choose_minecraft_folder
        if choose_minecraft_folder(self) and self.banner is not None:
            self.banner.hide()
            self._fill()

    def version(self):
        import loot_engine as le
        sub = self.sub.currentText() if self.sub is not None else None
        return le.loot_version(self.mc, sub)

    def open_table(self, table, focus_slots=None):
        hide_tooltip()
        from .loot_table_dialog import LootTableDialog
        from .loot_table_dialog import tables_of
        d = LootTableDialog(self.version(), table, self, structure=self.key, focus_slots=focus_slots,
                            tables=tables_of(getattr(self, "_res", None)) or None)
        d.show()

    def _fill(self):
        """(29/10) the chests are computed in a helper process (a mineshaft or a
        stronghold replays its chunks: seconds), the window stays responsive."""
        import structure_loot as sl
        sub = self.sub.currentText() if self.sub is not None else None
        ver = self.version()
        self._ver = ver
        self.sub_lbl.setText("  ·  ".join(x for x in (self._where, f"Java {ver}") if x))
        self._token = getattr(self, "_token", 0) + 1
        tok = self._token
        w = QWidget(); lay = QVBoxLayout(w)
        msg = QLabel("Computing the chests…"); msg.setObjectName("LootSub")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addStretch(1); lay.addWidget(msg); lay.addStretch(2)
        self.area.setWidget(w)
        try:
            from .map_engine import loot_pool
            fut = loot_pool().submit(sl.chests, self.key, self.seed, self.mc, self.x, self.z, sub)
        except Exception:
            fut = None
        if fut is None:
            self._show(sl.chests(self.key, self.seed, self.mc, self.x, self.z, sub))
            return

        def poll():
            if tok != self._token:
                return
            if not fut.done():
                QTimer.singleShot(60, poll)
                return
            try:
                res = fut.result()
            except Exception:
                res = sl.chests(self.key, self.seed, self.mc, self.x, self.z, sub)
            self._show(res)
        QTimer.singleShot(30, poll)

    def _show(self, res):
        # (31/10) not chests: the jungle temple's arrow dispensers
        res = [c for c in (res or []) if "dispenser" not in (c.get("table") or "")]
        res = merge_double_chests(res)
        self._res = res
        n = sum(1 for c in res if c.get("slots") is not None)
        self.sub_lbl.setText("  ·  ".join(x for x in (self._where, f"Java {self._ver}",
                                                         f"{n} container{'s' * (n != 1)}") if x))
        if not res:
            w = QWidget(); lay = QVBoxLayout(w)
            m = QLabel("No chest here."); m.setObjectName("LootSub")
            m.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addStretch(1); lay.addWidget(m); lay.addStretch(2)
            self.area.setWidget(w)
            return
        grid = _FlowGrid()
        target = None
        for ch in res:
            c = ChestCard(ch, self._ver, self.key, self)
            if self.focus is not None and target is None and \
                    self.focus in (tuple(ch.get("pos") or ()), tuple(ch.get("pos2") or ())):
                c.setProperty("focus", True)
                target = c
            grid.add(c)
        grid._place(force=True)
        self.area.setWidget(grid)
        if target is not None:
            QTimer.singleShot(80, lambda t=target: self.area.ensureWidgetVisible(t, 0, 40))

    def closeEvent(self, e):
        hide_tooltip()
        super().closeEvent(e)

    def hideEvent(self, e):
        hide_tooltip()
        super().hideEvent(e)
