"""
qtui/theme.py — SSA design system (Qt) and themes.

One source of truth for colours, type, spacing and widget styling: no widget
hard-codes a colour, everything reads these tokens, so a theme switch restyles
the whole application live.

Themes
  * Slate     — default, fixed: slate gradient base with floating rounded panels
  * Midnight  — editable (starts as a deep night-blue variant)
  * Daylight  — editable (starts as a light variant)
The two editable themes are saved in the user configuration (themes.json) and
edited with the theme editor, like the biome palettes.
"""
from __future__ import annotations

import json
import os
import tempfile

from PySide6.QtCore import QObject, QStandardPaths, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

# ── Token definitions ────────────────────────────────────────────────────────
TOKEN_LABELS = [   # (key, label shown in the editor)
    ("base_top", "Background — top"), ("base_bottom", "Background — bottom"),
    ("bg0", "Map letterbox"), ("bg1", "Panels"), ("bg2", "Fields & cards"),
    ("bg3", "Hover"), ("bg4", "Pressed / tracks"),
    ("line", "Hairlines"), ("line2", "Control borders"),
    ("text", "Text"), ("text2", "Secondary text"), ("text3", "Muted text"),
    ("accent", "Accent"), ("ok", "Success"), ("warn", "Warning"), ("danger", "Danger"),
]

BUILTIN = {
    "Slate": dict(
        base_top="#171c26", base_bottom="#0f1218", bg0="#10141b", bg1="#1a1f2a",
        bg2="#212735", bg3="#283042", bg4="#313a4f", line="#252c3a", line2="#343d50",
        text="#e7e9ee", text2="#a4aaba", text3="#6d7486",
        accent="#4c8dff", ok="#3ecf8e", warn="#f2b544", danger="#f0616d"),
    "Midnight": dict(
        base_top="#121c3a", base_bottom="#0a1024", bg0="#0b1226", bg1="#131d3d",
        bg2="#19254b", bg3="#21305c", bg4="#2a3b6d", line="#1d2a52", line2="#2c3c68",
        text="#e9ecf6", text2="#a7b1d0", text3="#6b77a0",
        accent="#7c9cff", ok="#45d49a", warn="#f4bd52", danger="#f26b7a"),
    "Daylight": dict(
        base_top="#e9edf3", base_bottom="#dfe4ec", bg0="#cfd6e0", bg1="#f7f9fc",
        bg2="#ffffff", bg3="#eef2f7", bg4="#dde3ec", line="#d6dce6", line2="#c3cbd8",
        text="#1b2130", text2="#4c5568", text3="#8a93a6",
        accent="#2f6fe4", ok="#1f9d68", warn="#b7791f", danger="#d64550"),
}

# ── textured themes (27/09): a procedural backdrop behind translucent panels,
# a two-tone accent (primary buttons, selected tabs) — see backdrops.py
def _t(**k):
    return k


BUILTIN.update({
    "Obsidian Gold": _t(
        base_top="#14120e", base_bottom="#0a0908", bg0="#0c0b09", bg1="#17150f",
        bg2="#211e17", bg3="#2c281e", bg4="#383226", line="#2b271e", line2="#40392a",
        text="#f1eadb", text2="#c4b89e", text3="#847a64",
        accent="#d6aa3c", ok="#6fcf8e", warn="#eea847", danger="#e8636b"),
    "Carrara": _t(
        base_top="#efeeea", base_bottom="#e3e1dc", bg0="#d8d5ce", bg1="#fbfaf7",
        bg2="#ffffff", bg3="#f1efe9", bg4="#e2dfd6", line="#e3dfd5", line2="#cdc7b8",
        text="#23211c", text2="#5a554a", text3="#938c7c",
        accent="#a87a22", ok="#2f9a64", warn="#b7791f", danger="#cc4450"),
    "Emerald": _t(
        base_top="#082119", base_bottom="#04130e", bg0="#051410", bg1="#0b231b",
        bg2="#113026", bg3="#173d31", bg4="#1f4a3c", line="#15352a", line2="#24493c",
        text="#e9f3ec", text2="#a9c6b5", text3="#6b8c7b",
        accent="#e0bb62", ok="#5fdc9a", warn="#f0b54c", danger="#f06c74"),
    "Aurora": _t(
        base_top="#07142a", base_bottom="#02040a", bg0="#040a14", bg1="#0b1424",
        bg2="#111d32", bg3="#182740", bg4="#20314f", line="#172238", line2="#253553",
        text="#e8f1fb", text2="#a5b7cf", text3="#687c98",
        accent="#3ddfb0", ok="#52e19b", warn="#f4c05a", danger="#ff6b86"),
    "Nebula": _t(
        base_top="#120a26", base_bottom="#05030d", bg0="#07040f", bg1="#140d26",
        bg2="#1c1433", bg3="#261c43", bg4="#302553", line="#221939", line2="#342856",
        text="#f1ebfb", text2="#bdb0d6", text3="#7d6f9b",
        accent="#b67cff", ok="#5fe0a8", warn="#f6c15c", danger="#ff6f95"),
    "Sunset Dune": _t(
        base_top="#2a160e", base_bottom="#140b07", bg0="#120a06", bg1="#1f140e",
        bg2="#2a1c14", bg3="#36251b", bg4="#433023", line="#33241a", line2="#4a3526",
        text="#fbefe4", text2="#d4b8a2", text3="#937762",
        accent="#ff8a3d", ok="#7fd38e", warn="#ffc15e", danger="#ff6a5c"),
    "Abyss": _t(
        base_top="#062630", base_bottom="#020b10", bg0="#030d12", bg1="#081820",
        bg2="#0d222c", bg3="#132e3a", bg4="#1a3b49", line="#112a35", line2="#1f4150",
        text="#e4f6f8", text2="#9ec4cb", text3="#5f8791",
        accent="#2bd4d4", ok="#4fe0a2", warn="#f2c05a", danger="#ff6b7a"),
    "Deepslate": _t(
        base_top="#26272c", base_bottom="#151619", bg0="#131417", bg1="#1c1d21",
        bg2="#25272c", bg3="#2f3238", bg4="#3a3d45", line="#2c2e34", line2="#3d4048",
        text="#eceef2", text2="#aeb2bc", text3="#72767f",
        accent="#4ee6e0", ok="#5fdc8a", warn="#f2b544", danger="#f0616d"),
    "Nether": _t(
        base_top="#2a0c0c", base_bottom="#140505", bg0="#120404", bg1="#200a0a",
        bg2="#2b1010", bg3="#381616", bg4="#461d1d", line="#351313", line2="#4d1f1f",
        text="#fbe9e4", text2="#d9aaa0", text3="#95665e",
        accent="#ff8f2e", ok="#8fd46b", warn="#ffcc4d", danger="#ff5a5a"),
    "The End": _t(
        base_top="#140d1f", base_bottom="#07040c", bg0="#08050e", bg1="#140e1f",
        bg2="#1c1529", bg3="#261d36", bg4="#312644", line="#221a31", line2="#352a47",
        text="#f4eefb", text2="#c3b5d6", text3="#80719a",
        accent="#d58cff", ok="#8fe8b0", warn="#f1e28a", danger="#ff6f9a"),
    "Copper": _t(
        base_top="#1b1714", base_bottom="#0e0c0b", bg0="#0f0d0c", bg1="#191513",
        bg2="#221d1a", bg3="#2d2622", bg4="#39302b", line="#2c2521", line2="#40362f",
        text="#f4ece6", text2="#c9b6a8", text3="#86766a",
        accent="#e07a45", ok="#4fb3a0", warn="#f0b44c", danger="#e8606a"),
    "Sakura": _t(
        base_top="#fbeef2", base_bottom="#f3e3ea", bg0="#ead4dd", bg1="#fffafc",
        bg2="#ffffff", bg3="#fbeff4", bg4="#f1dde6", line="#f0dde6", line2="#e2c6d3",
        text="#2c1f26", text2="#6b5360", text3="#a48c98",
        accent="#d9487a", ok="#2f9a64", warn="#b7791f", danger="#cc3d4f"),
    "Lapis": _t(
        base_top="#0b1633", base_bottom="#050a1a", bg0="#060b1b", bg1="#0c1631",
        bg2="#112040", bg3="#172a52", bg4="#1f3564", line="#152546", line2="#233a68",
        text="#eaf0fc", text2="#a9b8da", text3="#6c7ca2",
        accent="#e2b64a", ok="#5fd79a", warn="#f2b95a", danger="#f26b78"),
    "Rose Quartz": _t(
        base_top="#f6ecee", base_bottom="#ecdde1", bg0="#e3cfd5", bg1="#fffafb",
        bg2="#ffffff", bg3="#f8eef1", bg4="#ecdce2", line="#eddde2", line2="#dcc3cc",
        text="#2b2024", text2="#66545b", text3="#a08b93",
        accent="#b8566f", ok="#2f9a64", warn="#b7791f", danger="#c73c4e"),
    "Mesa": _t(
        base_top="#2a150c", base_bottom="#170a05", bg0="#150905", bg1="#22120b",
        bg2="#2d1810", bg3="#3a2016", bg4="#48281c", line="#35201a", line2="#4d2e22",
        text="#fbeee4", text2="#dcb9a1", text3="#9a7560",
        accent="#f08a4b", ok="#8fcf7a", warn="#ffc561", danger="#ff6a5a"),
    "Amethyst": _t(
        base_top="#1a1030", base_bottom="#0b0616", bg0="#0a0614", bg1="#160e2a",
        bg2="#1f1438", bg3="#2a1b49", bg4="#35235a", line="#241838", line2="#3a2a58",
        text="#f3ecfd", text2="#c5b2e4", text3="#8574a6",
        accent="#b98cff", ok="#6fe0a8", warn="#f4c65c", danger="#ff6f95"),
    "Prismarine": _t(
        base_top="#0b2a2a", base_bottom="#041313", bg0="#051414", bg1="#0b2222",
        bg2="#102e2e", bg3="#163b3a", bg4="#1e4847", line="#143333", line2="#23504e",
        text="#e6f8f4", text2="#a0cfc6", text3="#5f8f86",
        accent="#57e3c8", ok="#62e39c", warn="#f1c25c", danger="#ff6b7a"),
    "Arctic": _t(
        base_top="#eef4f9", base_bottom="#dde8f1", bg0="#cfdce8", bg1="#fbfdff",
        bg2="#ffffff", bg3="#eef4fa", bg4="#dce7f1", line="#dbe5ef", line2="#c3d3e2",
        text="#18222e", text2="#4a5a6d", text3="#8898ab",
        accent="#2b86c9", ok="#1f9d68", warn="#b7791f", danger="#d64550"),
    "Ember": _t(
        base_top="#1f0a06", base_bottom="#0a0302", bg0="#0b0403", bg1="#1a0906",
        bg2="#240e09", bg3="#30140d", bg4="#3d1a11", line="#2c120c", line2="#432016",
        text="#fcede6", text2="#dcae9c", text3="#976b5c",
        accent="#ff6a2b", ok="#8fd46b", warn="#ffc24d", danger="#ff4f5e"),
    "Jungle": _t(
        base_top="#0b1f10", base_bottom="#040d06", bg0="#050e07", bg1="#0c1c10",
        bg2="#122816", bg3="#18341d", bg4="#1f4125", line="#153019", line2="#234a2a",
        text="#eaf6e9", text2="#a8c9a6", text3="#6a8c69",
        accent="#9fdc3a", ok="#5fdc8a", warn="#f2c14e", danger="#f0616d"),
    "Graphite": _t(
        base_top="#23252a", base_bottom="#141518", bg0="#121316", bg1="#1b1c20",
        bg2="#232529", bg3="#2d3035", bg4="#383b42", line="#2a2c31", line2="#3b3e45",
        text="#ecedf0", text2="#aaadb5", text3="#6e717a",
        accent="#9fb6d4", ok="#62d39a", warn="#f0b654", danger="#f0636e"),
})

# per theme: description, backdrop (procedural, backdrops.py), second accent
# (two-tone buttons) and panel opacity (the backdrop shows through a little)
EXTRAS = {
    "Slate": dict(desc="Default — cool slate gradient"),
    "Midnight": dict(desc="Deep night blue — editable"),
    "Daylight": dict(desc="Light — editable"),
    "Obsidian Gold": dict(desc="Black marble with gold veins", accent2="#f3d27a", panel=0.84,
                          backdrop=dict(kind="marble", seed=11, base=["#070605", "#1a1712"],
                                        veins=["#b8892a", "#f6d98a"], secondary="#4a4438")),
    "Carrara": dict(desc="White marble, grey veins, gold accent", accent2="#d6a94a", panel=0.86,
                    backdrop=dict(kind="marble", seed=5, base=["#f3f1ec", "#dcd9d2"],
                                  veins=["#7d7a74", "#a9a59c"], secondary="#c9c4b8", vein_amt=0.55,
                                  light=True)),
    "Emerald": dict(desc="Green marble with gold veins", accent2="#f5dc93", panel=0.84,
                    backdrop=dict(kind="marble", seed=23, base=["#03140e", "#0e3a2b"],
                                  veins=["#c9a55a", "#f1e2b0"], secondary="#1d5a45", vein_amt=0.75)),
    "Aurora": dict(desc="Northern lights over a starry sky", accent2="#8f7cff", panel=0.80,
                   backdrop=dict(kind="aurora", seed=3)),
    "Nebula": dict(desc="Deep space, violet and pink clouds", accent2="#ff7cc8", panel=0.80,
                   backdrop=dict(kind="nebula", seed=8)),
    "Sunset Dune": dict(desc="Warm dunes under a setting sun", accent2="#ffc15e", panel=0.84,
                        backdrop=dict(kind="glow", seed=4, base=["#3a1c12", "#120806"],
                                      blobs=[["#ff7a2e", 0.78, 0.08, 0.42], ["#b13a6a", 0.2, 0.0, 0.5],
                                             ["#ffb45e", 0.95, 0.2, 0.18]],
                                      ridges=["#c77a45", 0.25, 520])),
    "Abyss": dict(desc="Ocean depths with light caustics", accent2="#6ff0c8", panel=0.82,
                  backdrop=dict(kind="glow", seed=9, base=["#073241", "#01080c"],
                                blobs=[["#1c8aa0", 0.35, -0.1, 0.55], ["#0e5d7a", 0.85, 0.1, 0.35]],
                                ridges=["#3fc6d0", 0.18, 300])),
    "Deepslate": dict(desc="Minecraft deepslate, diamond accent", accent2="#9df6f2", panel=0.86,
                      backdrop=dict(kind="stone", seed=2, dark="#17181c", light="#34363d",
                                    specks=["#101114", "#50535c"], cell=70, crack="#0e0f12")),
    "Nether": dict(desc="Netherrack and lava glow", accent2="#ffd257", panel=0.84,
                   backdrop=dict(kind="stone", seed=6, dark="#2a0707", light="#5a1414",
                                 specks=["#1c0404", "#7a2a20"], cell=60, crack="#ff7a1a",
                                 accent_specks="#ff9b3d")),
    "The End": dict(desc="The void, end stone and chorus", accent2="#f4b8ff", panel=0.80,
                    backdrop=dict(kind="nebula", seed=31, deep="#040208",
                                  clouds=["#5a2d8c", "#8a3a9a", "#2a2070"], star="#e9dcff",
                                  density=1.3)),
    "Copper": dict(desc="Brushed copper turning to verdigris", accent2="#4fb3a0", panel=0.84,
                   backdrop=dict(kind="brushed", seed=12, base=["#3a2216", "#6b3f27"],
                                 sheen="#f0b080", tint=["#3a8a78", 0.5])),
    "Sakura": dict(desc="Cherry blossom — light", accent2="#f08bb0", panel=0.86,
                   backdrop=dict(kind="glow", seed=14, base=["#fdf3f6", "#f3dce6"],
                                 blobs=[["#ffb6cc", 0.15, 0.1, 0.35], ["#ffd3e0", 0.85, 0.85, 0.4],
                                        ["#f7a8c4", 0.9, 0.05, 0.25]], light=True, grain=2,
                                 petals=["#f4a6c0", 140])),
    "Graphite": dict(desc="Brushed graphite, silver accent", accent2="#dfe8f5", panel=0.86,
                     backdrop=dict(kind="brushed", seed=1)),
    "Lapis": dict(desc="Lapis lazuli with gold flecks", accent2="#f5d98a", panel=0.84,
                  backdrop=dict(kind="marble", seed=41, base=["#06102a", "#15306a"],
                                veins=["#b8923a", "#f3d68a"], secondary="#2a4c9a", vein_amt=0.6,
                                scale=0.7, angle=1.1)),
    "Rose Quartz": dict(desc="Pink quartz marble — light", accent2="#e39bb0", panel=0.86,
                        backdrop=dict(kind="marble", seed=17, base=["#fbf1f3", "#ecd2da"],
                                      veins=["#c98f9f", "#e8bfca"], secondary="#dcb5c1",
                                      vein_amt=0.5, light=True)),
    "Mesa": dict(desc="Badlands terracotta strata", accent2="#ffd08a", panel=0.84,
                 backdrop=dict(kind="strata", seed=19)),
    "Amethyst": dict(desc="Amethyst geode facets", accent2="#f0d6ff", panel=0.82,
                     backdrop=dict(kind="crystal", seed=27)),
    "Prismarine": dict(desc="Ocean monument prismarine", accent2="#b8fff0", panel=0.82,
                       backdrop=dict(kind="crystal", seed=33, dark="#062220", light="#2f7f73",
                                     edge="#8ff2dc", n=120, glint="#e8fffa")),
    "Arctic": dict(desc="Cracked blue ice — light", accent2="#7cc3ef", panel=0.86,
                   backdrop=dict(kind="marble", seed=29, base=["#f1f7fc", "#cddfee"],
                                 veins=["#7fb4de", "#b5d8f2"], secondary="#dcebf6",
                                 vein_amt=0.55, scale=0.8, angle=0.2, light=True)),
    "Ember": dict(desc="Embers rising over a dark forge", accent2="#ffc24d", panel=0.82,
                  backdrop=dict(kind="aurora", seed=13, sky=["#050101", "#2a0906"],
                                curtains=["#ff5a1f", "#ffae3d", "#c2263a"])),
    "Jungle": dict(desc="Deep jungle canopy and falling leaves", accent2="#d8f58a", panel=0.84,
                   backdrop=dict(kind="glow", seed=37, base=["#0f2a14", "#030a05"],
                                 blobs=[["#3f8f2a", 0.2, 0.05, 0.45], ["#1f6b3a", 0.85, 0.3, 0.4],
                                        ["#9fdc3a", 0.6, 0.0, 0.18]],
                                 leaves=[["#3f8f2a", "#57a832", "#2d7a3a", "#79b83a"], 260])),
}
ACCENT2 = ""
PANEL = ""
BACKDROP = None
EDITABLE = ("Midnight", "Daylight")
DEFAULT_THEME = "Slate"

# live values (module globals read everywhere)
THEME = DEFAULT_THEME
TOKENS: dict = dict(BUILTIN[DEFAULT_THEME])
BASE_TOP = BASE_BOTTOM = BG0 = BG1 = BG2 = BG3 = BG4 = LINE = LINE2 = ""
TEXT = TEXT2 = TEXT3 = ACCENT = ACCENT_H = ACCENT_P = ACCENT_SOFT = ACCENT_LINE = ""
OK = WARN = DANGER = HOVER_LINE = OVERLAY = ""
IS_LIGHT = False
ELEVATED = True

RADIUS = 6
SP = 8

UI_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Inter", "Helvetica Neue", "Arial"]
MONO_FAMILIES = ["Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono"]


class _Signals(QObject):
    changed = Signal()


signals = _Signals()


def _rgba(hex_: str, a: float) -> str:
    c = QColor(hex_)
    return f"rgba({c.red()},{c.green()},{c.blue()},{a})"


def _shade(hex_: str, f: float) -> str:
    c = QColor(hex_)
    return (c.lighter(int(100 * f)) if f >= 1 else c.darker(int(100 / f))).name()


# ── user themes (the two editable ones) ─────────────────────────────────────
def _config_path() -> str:
    d = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
    if not d:
        d = os.path.join(os.path.expanduser("~"), ".ssa")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "themes.json")


def user_themes() -> dict:
    try:
        with open(_config_path(), encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if k in EDITABLE and isinstance(v, dict)}
    except Exception:
        return {}


def theme_tokens(name: str) -> dict:
    t = dict(BUILTIN.get(name, BUILTIN[DEFAULT_THEME]))
    if name in EDITABLE:
        t.update({k: v for k, v in user_themes().get(name, {}).items() if k in t})
    return t


def save_user_theme(name: str, tokens: dict) -> None:
    if name not in EDITABLE:
        return
    data = user_themes()
    data[name] = {k: tokens[k] for k, _ in TOKEN_LABELS if k in tokens}
    with open(_config_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def reset_user_theme(name: str) -> None:
    data = user_themes()
    data.pop(name, None)
    with open(_config_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def set_theme(name: str, tokens: dict | None = None) -> None:
    """Load a theme into the module globals (tokens = live, unsaved edits)."""
    global THEME, TOKENS, BASE_TOP, BASE_BOTTOM, BG0, BG1, BG2, BG3, BG4, LINE, LINE2
    global TEXT, TEXT2, TEXT3, ACCENT, ACCENT_H, ACCENT_P, ACCENT_SOFT, ACCENT_LINE
    global OK, WARN, DANGER, HOVER_LINE, OVERLAY, IS_LIGHT
    THEME = name if name in BUILTIN else DEFAULT_THEME
    TOKENS = dict(tokens) if tokens else theme_tokens(THEME)
    t = TOKENS
    BASE_TOP, BASE_BOTTOM = t["base_top"], t["base_bottom"]
    BG0, BG1, BG2, BG3, BG4 = t["bg0"], t["bg1"], t["bg2"], t["bg3"], t["bg4"]
    LINE, LINE2 = t["line"], t["line2"]
    TEXT, TEXT2, TEXT3 = t["text"], t["text2"], t["text3"]
    ACCENT, OK, WARN, DANGER = t["accent"], t["ok"], t["warn"], t["danger"]
    # readable everywhere (01/10): the secondary / muted texts are nudged towards
    # the main text until they reach a minimum contrast on the panels — on the
    # translucent panels of the textured themes they were too faint
    TEXT2 = readable(TEXT2, TEXT, [BG1, BG2, BG3], 5.0)
    TEXT3 = readable(TEXT3, TEXT, [BG1, BG2, BG3], 3.6)
    IS_LIGHT = QColor(BG1).lightness() > 128
    ACCENT_H = _shade(ACCENT, 1.15)
    ACCENT_P = _shade(ACCENT, 0.87)
    ACCENT_SOFT = _rgba(ACCENT, 0.16 if not IS_LIGHT else 0.12)
    ACCENT_LINE = _rgba(ACCENT, 0.45)
    HOVER_LINE = _shade(LINE2, 0.85 if IS_LIGHT else 1.25)
    OVERLAY = _rgba(BG1, 0.92)
    global ACCENT2, PANEL, BACKDROP
    ex = EXTRAS.get(THEME, {})
    ACCENT2 = ex.get("accent2") or ACCENT_H
    PANEL = _rgba(BG1, ex["panel"]) if ex.get("panel") and not tokens_overridden(tokens) else BG1
    BACKDROP = ex.get("backdrop")


def _lum(hex_: str) -> float:
    c = QColor(hex_)

    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio of two colours (1 … 21)."""
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def readable(col: str, towards: str, backs, target: float) -> str:
    """`col`, mixed a little towards `towards` (the main text colour) until its
    contrast on every colour of `backs` reaches `target` (unchanged if it does)."""
    a, b = QColor(col), QColor(towards)
    for k in range(0, 21):
        f = k / 20.0
        c = QColor(round(a.red() + (b.red() - a.red()) * f),
                   round(a.green() + (b.green() - a.green()) * f),
                   round(a.blue() + (b.blue() - a.blue()) * f)).name()
        if all(contrast(c, x) >= target for x in backs):
            return c
    return towards


def tokens_overridden(tokens) -> bool:
    return False


set_theme(DEFAULT_THEME)


def _pick(families):
    have = set(QFontDatabase.families())
    for f in families:
        if f in have:
            return f
    return families[-1]


def ui_font(pt: float = 9.5, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(_pick(UI_FAMILIES))
    f.setPointSizeF(pt)
    f.setWeight(weight)
    return f


def mono_font(pt: float = 9.5) -> QFont:
    f = QFont(_pick(MONO_FAMILIES))
    f.setPointSizeF(pt)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


def qcolor(hex_: str) -> QColor:
    return QColor(hex_)


# ── SVG assets referenced by the stylesheet (regenerated per theme) ─────────
def _assets() -> dict:
    svg = {
        "check": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M3.5 8.5l3 3 6-7" '
                 'fill="none" stroke="#ffffff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        "down": f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M4 6l4 4 4-4" fill="none" '
                f'stroke="{TEXT2}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        "up": f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M4 10l4-4 4 4" fill="none" '
              f'stroke="{TEXT2}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        "right": f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path d="M6 4l4 4-4 4" fill="none" '
                 f'stroke="{TEXT2}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    }
    d = os.path.join(tempfile.gettempdir(), "ssa_qt_assets")
    os.makedirs(d, exist_ok=True)
    out = {}
    tag = QColor(TEXT2).name()[1:]
    for name, s in svg.items():
        path = os.path.join(d, f"{name}_{tag}.svg")
        try:
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(s)
        except OSError:
            pass
        out[name] = path.replace("\\", "/")
    return out


# ── Stylesheet ───────────────────────────────────────────────────────────────
def stylesheet() -> str:
    mono = _pick(MONO_FAMILIES)
    A = _assets()
    root_bg = f"qlineargradient(x1:0, y1:0, x2:0.35, y2:1, stop:0 {BASE_TOP}, stop:1 {BASE_BOTTOM})"
    root_img = ""
    if BACKDROP:
        from .backdrops import backdrop_path
        bp = backdrop_path(BACKDROP)
        if bp:
            root_img = f'border-image: url("{bp}") 0 0 0 0 stretch stretch;'
    acc_grad = (f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {ACCENT}, stop:1 {ACCENT2})"
                if EXTRAS.get(THEME, {}).get("accent2") else ACCENT)
    on_acc = "#1a1408" if QColor(ACCENT).lightness() > 150 else "white"
    # the nav rail / status bar sat right on the textured backdrop (strata,
    # marble…): their muted text vanished on it (Mesa « Theme », 01/10). A
    # translucent panel tint under them keeps the backdrop visible but readable.
    chrome_bg = _rgba(BG1, 0.72) if BACKDROP else "transparent"
    nav_text = TEXT2 if BACKDROP else TEXT3
    return f"""
* {{ outline: 0; }}
QWidget {{ color: {TEXT}; background: transparent; selection-background-color: {ACCENT}; selection-color: white; }}
QMainWindow, #Root {{ background: {root_bg}; {root_img} }}
QDialog {{ background: {BG1}; }}
QToolTip {{ background: {BG2}; color: {TEXT}; border: 1px solid {LINE2}; padding: 5px 8px; border-radius: 4px; }}

/* ---------- regions ---------- */
#TopBar, #NavRail, #StatusBar {{ background: {chrome_bg}; border: 0; }}
#StatusBar QLabel {{ color: {TEXT2}; }}
#SidePanel {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 10px; }}
#Surface {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 10px; }}
#Page {{ background: transparent; }}

/* ---------- typography roles ---------- */
QLabel#AppName {{ font-weight: 600; font-size: 10.5pt; color: {TEXT}; }}
QLabel#FieldLabel {{ color: {TEXT3}; font-size: 8pt; font-weight: 600; letter-spacing: 0.4px; }}
QLabel#SectionTitle {{ color: {TEXT2}; font-size: 8pt; font-weight: 700; letter-spacing: 0.8px; }}
QLabel#PageTitle {{ font-size: 15pt; font-weight: 600; }}
QLabel#Muted {{ color: {TEXT3}; }}
QLabel#Body {{ color: {TEXT2}; }}
QLabel#MapReadout {{ background: {OVERLAY}; border: 1px solid {LINE2}; border-radius: 6px;
                     padding: 5px 10px; color: {TEXT}; }}

/* ---------- nav rail ---------- */
QToolButton#NavButton {{
    color: {nav_text}; border: 0; border-radius: {RADIUS}px; padding: 6px 0 5px 0;
    font-size: 7.5pt; font-weight: 600;
}}
QToolButton#NavButton:hover {{ color: {TEXT2}; background: {BG3}; }}
QToolButton#NavButton:checked {{ color: {TEXT}; background: {ACCENT_SOFT}; border-left: 2px solid {ACCENT2}; }}

/* ---------- buttons ---------- */
QPushButton {{
    background: {BG2}; border: 1px solid {LINE2}; border-radius: {RADIUS}px;
    padding: 5px 12px; min-height: 18px;
}}
QPushButton:hover {{ background: {BG3}; border-color: {HOVER_LINE}; }}
QPushButton:pressed {{ background: {BG4}; }}
QPushButton:disabled {{ color: {TEXT3}; background: {BG1}; border-color: {LINE}; }}
QPushButton[kind="primary"] {{ background: {acc_grad}; border-color: {ACCENT}; color: {on_acc}; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {ACCENT_H}; border-color: {ACCENT_H}; }}
QPushButton[kind="primary"]:pressed {{ background: {ACCENT_P}; }}
QPushButton[kind="primary"]:disabled {{ background: {_rgba(ACCENT, 0.35)}; border-color: transparent; color: {_rgba("#ffffff", 0.6)}; }}
QPushButton[kind="danger"] {{ background: transparent; border-color: {_rgba(DANGER, 0.5)}; color: {DANGER}; }}
QPushButton[kind="danger"]:hover {{ background: {_rgba(DANGER, 0.12)}; }}
QPushButton[kind="ghost"] {{ background: transparent; border-color: transparent; color: {TEXT2}; }}
QPushButton[kind="ghost"]:hover {{ background: {BG3}; color: {TEXT}; }}
QPushButton#Swatch {{ border-radius: 4px; min-width: 44px; max-width: 44px; min-height: 18px; padding: 0; }}

QToolButton {{ border: 1px solid transparent; border-radius: {RADIUS}px; padding: 4px; }}
QToolButton:hover {{ background: {BG3}; }}
QToolButton:pressed {{ background: {BG4}; }}
QToolButton:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT_LINE}; }}
QToolButton#MapTool {{ background: {OVERLAY}; border: 1px solid {LINE2}; }}
QToolButton#MapTool:hover {{ background: {BG3}; }}
QToolButton#MapTool:checkable:!checked {{ border: 1px dashed {LINE2}; }}
QToolButton#MapTool:checked {{ border: 1px solid {ACCENT}; }}
QLabel#KeyCap {{ background: {BG2}; border: 1px solid {LINE2}; border-bottom-width: 2px;
                 border-radius: 5px; padding: 2px 8px; font-family: "{mono}"; font-size: 8.5pt; color: {TEXT}; }}
QFrame#HitsBar {{ background: {OVERLAY}; border: 1px solid {LINE2}; border-radius: 8px; }}

/* segmented control */
QPushButton[seg="mid"], QPushButton[seg="first"], QPushButton[seg="last"], QPushButton[seg="only"] {{
    border-radius: 0; padding: 3px 9px; background: {BG2}; color: {TEXT2}; min-height: 16px; }}
QPushButton[seg="first"] {{ border-top-left-radius: {RADIUS}px; border-bottom-left-radius: {RADIUS}px; }}
QPushButton[seg="last"] {{ border-top-right-radius: {RADIUS}px; border-bottom-right-radius: {RADIUS}px; }}
QPushButton[seg="only"] {{ border-radius: {RADIUS}px; }}
QPushButton[seg]:checked {{ background: {ACCENT_SOFT}; color: {TEXT}; border-color: {ACCENT_LINE}; }}

/* ---------- inputs ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QTimeEdit, QComboBox {{
    background: {BG2}; border: 1px solid {LINE2}; border-radius: {RADIUS}px;
    padding: 4px 8px; min-height: 18px;
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: {HOVER_LINE}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit#SeedEdit {{ font-family: "{mono}"; font-size: 10pt; padding-left: 10px; }}
QComboBox::drop-down {{ border: 0; width: 20px; }}
QComboBox::down-arrow {{ width: 12px; height: 12px; image: url("{A['down']}"); }}
QComboBox QAbstractItemView {{ background: {BG2}; border: 1px solid {LINE2}; selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button, QTimeEdit::up-button, QTimeEdit::down-button {{ width: 16px; border: 0; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QTimeEdit::up-arrow {{ image: url("{A['up']}"); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QTimeEdit::down-arrow {{ image: url("{A['down']}"); width: 10px; height: 10px; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 4px; border: 1px solid {LINE2}; background: {BG2}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{A['check']}"); }}
QCheckBox:disabled {{ color: {TEXT3}; }}

QSlider::groove:horizontal {{ height: 4px; background: {BG4}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}

/* ---------- scroll ---------- */
QScrollArea {{ border: 0; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BG4}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {HOVER_LINE}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BG4}; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---------- lists / tables ---------- */
QTableView, QTreeView, QListView, QListWidget {{
    background: {"transparent" if PANEL != BG1 else BG1}; alternate-background-color: {BG2}; border: 1px solid {LINE};
    border-radius: {RADIUS}px; gridline-color: {LINE};
}}
QListWidget::item {{ padding: 3px 6px; border-radius: 4px; }}
QHeaderView::section {{ background: {"transparent" if PANEL != BG1 else BG1}; color: {TEXT3}; border: 0; border-bottom: 1px solid {LINE}; padding: 6px 8px; font-weight: 600; font-size: 8pt; }}
QTableView::item:selected, QTreeView::item:selected, QListView::item:selected, QListWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}

/* ---------- misc ---------- */
QFrame#Card {{ background: {BG2}; border: 1px solid {LINE}; border-radius: 8px; }}
QFrame#HLine {{ background: {LINE}; max-height: 1px; min-height: 1px; }}
QLabel#Chip {{ border-radius: 10px; padding: 2px 9px; font-size: 8pt; font-weight: 600; }}
QLabel#Chip[state="ok"] {{ background: {_rgba(OK, 0.14)}; color: {OK}; }}
QLabel#Chip[state="warn"] {{ background: {_rgba(WARN, 0.14)}; color: {WARN}; }}
QLabel#Chip[state="off"] {{ background: {BG3}; color: {TEXT2}; }}
QMenu {{ background: {BG2}; border: 1px solid {LINE2}; padding: 4px; border-radius: 6px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 6px; }}
QSplitter::handle {{ background: transparent; }}

/* ---------- sessions workspace ---------- */
QLabel#PanelTitle {{ font-size: 11.5pt; font-weight: 600; }}
QLabel#Error {{ color: {DANGER}; }}
QFrame#RunBar {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 10px; }}
QFrame#FormHeader {{ background: transparent; }}
QToolButton#PanelTool {{ padding: 4px; }}
QPushButton#TypeButton {{ text-align: left; padding: 7px 10px; background: {BG2}; }}
QPushButton#TypeButton:hover {{ background: {BG3}; border-color: {ACCENT_LINE}; }}
QTreeWidget#CondTree {{ background: transparent; border: 0; show-decoration-selected: 0; }}
QTreeWidget#CondTree::item {{ padding: 5px 2px; border-radius: 4px; }}
QTreeWidget#CondTree::item:hover {{ background: {BG2}; }}
QTreeView::branch {{ background: transparent; }}
QTreeView::branch:selected {{ background: transparent; }}
QTreeView::branch:has-children:closed {{ image: url({A["right"]}); }}
QTreeView::branch:has-children:open {{ image: url({A["down"]}); }}
QPushButton[kind="danger"]:disabled, QPushButton[kind="ghost"]:disabled {{ color: {TEXT3}; border-color: transparent; background: transparent; }}
QTableView#Results {{ background: transparent; border: 0; }}
QProgressBar {{ background: {BG3}; border: 0; border-radius: 3px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QTreeView::indicator, QTreeWidget::indicator {{ width: 14px; height: 14px; border-radius: 4px; border: 1px solid {LINE2}; background: {BG2}; }}
QTreeView::indicator:checked, QTreeWidget::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{A['check']}"); }}
QMenu::item:disabled {{ color: {TEXT3}; }}
"""


def _palette() -> QPalette:
    pal = QPalette()
    for role, col in (
        (QPalette.ColorRole.Window, BG0), (QPalette.ColorRole.Base, BG2),
        (QPalette.ColorRole.AlternateBase, BG1), (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.WindowText, TEXT), (QPalette.ColorRole.Button, BG2),
        (QPalette.ColorRole.ButtonText, TEXT), (QPalette.ColorRole.Highlight, ACCENT),
        (QPalette.ColorRole.HighlightedText, "#ffffff"), (QPalette.ColorRole.ToolTipBase, BG2),
        (QPalette.ColorRole.ToolTipText, TEXT), (QPalette.ColorRole.PlaceholderText, TEXT3),
    ):
        pal.setColor(role, QColor(col))
    return pal


def apply(app: QApplication, name: str | None = None, tokens: dict | None = None) -> None:
    """Apply a theme to the whole application (callable at any time: live)."""
    if name is None:
        name = os.environ.get("SSA_THEME") or _saved_theme_name()
    set_theme(name, tokens)
    if not getattr(app, "_ssa_fusion", False):   # once: re-setting the style on each
        app.setStyle("Fusion")                   # theme change cost ~1 s (full re-polish)
        app._ssa_fusion = True
    app.setFont(ui_font(9.5))
    app.setPalette(_palette())
    app.setStyleSheet(stylesheet())
    signals.changed.emit()


def _saved_theme_name() -> str:
    try:
        from PySide6.QtCore import QSettings
        v = QSettings("SSA", "SimpleSeedAnalyser").value("ui/theme", DEFAULT_THEME)
        return v if v in BUILTIN else DEFAULT_THEME
    except Exception:
        return DEFAULT_THEME


def remember_theme(name: str) -> None:
    from PySide6.QtCore import QSettings
    QSettings("SSA", "SimpleSeedAnalyser").setValue("ui/theme", name)
