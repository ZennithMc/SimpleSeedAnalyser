"""
qtui/start_source.py — the « Saved » start seed shared by every search page (03/10).

Sessions got a « Session / Saved » start selector on 02/10. The Stronghold and
Draw pages now have the same one, and the Saved start is ONE value for the whole
app: a search started from it, on any page, moves it on to where that search
stopped, and every page in Saved mode shows the new value. The value lives in the
settings key the Sessions page already used ("sessions/saved_start"), so nothing
already saved is lost.

  saved_store()            -> the shared store: .value, .set(v, origin), .changed
  StartSource(key, edit, labels, tips)
      .layout(field_widget, tip)   the « START  [Other | Saved] » column
      .is_saved(), .set_other(v), .edited(text)
      .search_started()            remember whether this search runs from Saved
      .search_ended(next, move_field=True)
      .refresh()                   show the shared value again (page idle)
      .bind_dice(button)           the « random start » button, hidden in Saved mode
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from .widgets import Segmented

_KEY = "sessions/saved_start"


def _settings():
    return QSettings("SSA", "SimpleSeedAnalyser")


class _SavedStore(QObject):
    changed = Signal(str, object)          # value, the StartSource / page that set it

    def __init__(self):
        super().__init__()
        self._value = str(_settings().value(_KEY, "0") or "0")

    @property
    def value(self) -> str:
        return self._value

    def set(self, v, origin=None):
        v = str(v).strip() or "0"
        if v == self._value:
            return
        self._value = v
        _settings().setValue(_KEY, v)
        self.changed.emit(v, origin)


_STORE = None


def hide_dice_keep_place(button, hidden):
    """Hide the random-start button but keep its space (the field does not jump)."""
    sp = button.sizePolicy()
    sp.setRetainSizeWhenHidden(True)
    button.setSizePolicy(sp)
    button.setVisible(not hidden)


def saved_store() -> _SavedStore:
    global _STORE
    if _STORE is None:
        _STORE = _SavedStore()
    return _STORE


class StartSource(QObject):
    """« Other / Saved » for one start-seed field. `labels` = (other, "Saved")."""
    modeChanged = Signal(str)

    def __init__(self, key, edit, labels, tips, parent=None):
        super().__init__(parent)
        self.key = key
        self.edit = edit
        self.labels = tuple(labels)
        self.from_saved = False
        m = str(_settings().value(f"{key}/start_mode", self.labels[0]))
        self.mode = m if m in self.labels else self.labels[0]
        self.other = edit.text().strip() or "0"
        self.dice = None
        self.seg = Segmented(list(self.labels), self.mode)
        for b in self.seg.buttons.values():
            b.setStyleSheet("font-size: 7.5pt; padding: 0px 7px; min-height: 14px; max-height: 16px;")
        for lab, tip in zip(self.labels, tips):
            self.seg.buttons[lab].setToolTip(tip)
        self.seg.changed.connect(self._mode_changed)
        edit.textEdited.connect(self.edited)
        saved_store().changed.connect(self._store_changed)
        if self.is_saved():
            edit.setText(saved_store().value)

    def layout(self, field_widget, tip=None):
        col = QVBoxLayout(); col.setSpacing(2)
        head = QHBoxLayout(); head.setSpacing(6); head.setContentsMargins(0, 0, 0, 0)
        lab = QLabel("START"); lab.setObjectName("FieldLabel")
        if tip:
            lab.setToolTip(tip); self.edit.setToolTip(tip)
        head.addWidget(lab); head.addStretch(1); head.addWidget(self.seg)
        col.addLayout(head); col.addWidget(field_widget)
        return col

    def is_saved(self) -> bool:
        return self.mode == "Saved"

    def set_other(self, v):
        """The page's own start (a loaded drawing…): shown only outside Saved."""
        self.other = str(v)
        if not self.is_saved():
            self.edit.setText(self.other)

    def edited(self, text):
        """Typed (or dice) start: belongs to the mode shown."""
        if self.is_saved():
            saved_store().set(text, origin=self)
        else:
            self.other = text.strip() or "0"

    def _mode_changed(self, mode):
        if mode == self.mode:
            return
        if self.is_saved():
            saved_store().set(self.edit.text(), origin=self)
        else:
            self.other = self.edit.text().strip() or "0"
        self.mode = mode
        _settings().setValue(f"{self.key}/start_mode", mode)
        self.edit.setText(saved_store().value if self.is_saved() else self.other)
        self._sync_dice()
        self.modeChanged.emit(mode)

    def _store_changed(self, v, origin):
        if origin is not self and self.is_saved() and self.edit.isEnabled():
            self.edit.setText(v)

    def bind_dice(self, button):
        """A random start makes no sense for the Saved start (one misclick would lose
        it): the button is hidden there, its place kept so nothing moves."""
        self.dice = button
        hide_dice_keep_place(button, self.is_saved())

    def _sync_dice(self):
        if self.dice is not None:
            hide_dice_keep_place(self.dice, self.is_saved())

    def refresh(self):
        if self.is_saved():
            self.edit.setText(saved_store().value)

    def set_enabled(self, on):
        self.seg.setEnabled(on)

    def search_started(self):
        self.from_saved = self.is_saved()

    def search_ended(self, next_start, move_field=True):
        """A search that ran from the Saved start moves it on (any page)."""
        if not self.from_saved or next_start is None:
            return
        saved_store().set(str(next_start), origin=self)
        if move_field and self.is_saved():
            self.edit.setText(saved_store().value)
