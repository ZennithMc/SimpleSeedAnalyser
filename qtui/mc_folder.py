"""
qtui/mc_folder.py — (06/11) where SSA reads the game's textures: found by itself
(official launcher, Modrinth, Prism, CurseForge…), or a folder the player picks.
"""
from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QFileDialog, QMessageBox

import mc_assets as ma


def choose_minecraft_folder(parent=None) -> bool:
    d = QFileDialog.getExistingDirectory(
        parent, "Minecraft folder (with a “versions” folder, or the launcher's folder)")
    if not d:
        return False
    s = QSettings("SSA", "SimpleSeedAnalyser")
    old = s.value("assets/minecraft_dir", "")
    s.setValue("assets/minecraft_dir", d)
    ma._ASSETS.clear()
    jars = ma.find_jars(refresh=True)
    if not jars:
        s.setValue("assets/minecraft_dir", old)
        ma.find_jars(refresh=True)
        QMessageBox.information(parent, "Minecraft", "No game version found in this folder.")
        return False
    return True


def status() -> str:
    jars = ma.find_jars()
    if not jars:
        return "Not found"
    vs = list(jars)
    return f"{len(vs)} version{'s' * (len(vs) != 1)} · {vs[0]} → {vs[-1]}"
