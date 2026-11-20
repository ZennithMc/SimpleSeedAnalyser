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
    # (13/11) the 1.3 look's own theme: neutral charcoal, one colour per workspace
    # (EXTRAS["Prism"]["ws"])
    "Prism": dict(
        base_top="#17181b", base_bottom="#0f1012", bg0="#0b0c0e", bg1="#1a1b1f",
        bg2="#232429", bg3="#2d2f36", bg4="#3a3c45", line="#2a2c32", line2="#3b3e48",
        text="#f4f4f5", text2="#b4b6be", text3="#7b7e89",
        accent="#5b9dff", ok="#34d399", warn="#f5b83d", danger="#fb7185"),
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
    # wood (08/10): Minecraft planks
    "Oak": _t(
        base_top="#2a1d11", base_bottom="#170f08", bg0="#140d07", bg1="#22170d",
        bg2="#2d1f12", bg3="#3a2817", bg4="#47321d", line="#352515", line2="#4d3720",
        text="#f6ecdf", text2="#d4bc9c", text3="#97805f",
        accent="#e4a957", ok="#8fd46b", warn="#f2c14e", danger="#ef6a5e"),
    "Dark Oak": _t(
        base_top="#18100a", base_bottom="#0b0704", bg0="#0a0604", bg1="#150e08",
        bg2="#1d140c", bg3="#271b10", bg4="#322315", line="#24190f", line2="#382717",
        text="#f1e7dc", text2="#c4ad94", text3="#86715b",
        accent="#c9955c", ok="#86cf73", warn="#e8b64e", danger="#e6655d"),
    "Cherry": _t(
        base_top="#f7e3dd", base_bottom="#efd2ca", bg0="#e6c2b8", bg1="#fff8f6",
        bg2="#ffffff", bg3="#fbeee9", bg4="#f2dbd3", line="#efd9d1", line2="#e1c0b6",
        text="#2e1c1a", text2="#6d4c47", text3="#a57f78",
        accent="#c4466a", ok="#2f9a64", warn="#b7791f", danger="#cc3d4f"),
    "Crimson": _t(
        base_top="#2a0d1a", base_bottom="#14060d", bg0="#12050b", bg1="#210b15",
        bg2="#2c101d", bg3="#391526", bg4="#471b30", line="#35121f", line2="#4e1c2f",
        text="#fbe9f0", text2="#dba9bd", text3="#9a6a7e",
        accent="#ff6b8f", ok="#8fd46b", warn="#ffc65a", danger="#ff5a5a"),
    "Warped": _t(
        base_top="#0b2422", base_bottom="#051312", bg0="#041110", bg1="#0a1f1d",
        bg2="#0f2a27", bg3="#153633", bg4="#1b433f", line="#122f2c", line2="#1f4642",
        text="#e7f8f5", text2="#a6cfc8", text3="#678f89",
        accent="#4fe3c8", ok="#7fe39a", warn="#f2c14e", danger="#f0616d"),
    # (10/10) retro, tech, paper and a few more
    "Synthwave": _t(
        base_top="#1a0830", base_bottom="#07020f", bg0="#08030f", bg1="#150a26",
        bg2="#1e1033", bg3="#291643", bg4="#351d55", line="#25143c", line2="#3b2160",
        text="#fbeefe", text2="#d2b4e4", text3="#8f71a6",
        accent="#ff4fa0", ok="#4ff0c0", warn="#ffd34d", danger="#ff5a6e"),
    "Blueprint": _t(
        base_top="#0d3a73", base_bottom="#082248", bg0="#071d3d", bg1="#0c2c57",
        bg2="#113566", bg3="#174176", bg4="#1e4d88", line="#16396a", line2="#2a5590",
        text="#eef5ff", text2="#b4cbe8", text3="#7b97bd",
        accent="#ffd84d", ok="#6fe0a0", warn="#ffc04d", danger="#ff7070"),
    "Parchment": _t(
        base_top="#f1e7d0", base_bottom="#e3d4b3", bg0="#d6c6a2", bg1="#fbf6ea",
        bg2="#fffdf7", bg3="#f3ead6", bg4="#e6d9bb", line="#e5d8bb", line2="#cdbb92",
        text="#2a2116", text2="#5e4f38", text3="#968566",
        accent="#8a3b1e", ok="#3c8a4f", warn="#a8701c", danger="#b8322e"),
    "Redstone": _t(
        base_top="#2a0707", base_bottom="#0e0202", bg0="#0f0303", bg1="#1c0606",
        bg2="#260a0a", bg3="#320e0e", bg4="#3f1414", line="#2e0c0c", line2="#4a1717",
        text="#fdeceb", text2="#dcaaa6", text3="#9a6762",
        accent="#ff3b2f", ok="#8fd46b", warn="#ffc65a", danger="#ff7a5a"),
    "Honeycomb": _t(
        base_top="#2a1702", base_bottom="#120900", bg0="#110900", bg1="#1e1104",
        bg2="#291806", bg3="#35200a", bg4="#42290e", line="#311d07", line2="#4a2f0f",
        text="#fff4dd", text2="#e3c896", text3="#a08656",
        accent="#ffc23a", ok="#9ad46b", warn="#ffd36b", danger="#ff6a5a"),
    "Sculk": _t(
        base_top="#08141a", base_bottom="#020507", bg0="#020609", bg1="#071118",
        bg2="#0b1922", bg3="#10232e", bg4="#162d3a", line="#0e1f28", line2="#1a3442",
        text="#e4f9fb", text2="#9fcbd2", text3="#5f8a92",
        accent="#29dfe8", ok="#4fe0a2", warn="#f2c05a", danger="#ff6b7a"),
    "Topographic": _t(
        base_top="#f7ecd0", base_bottom="#ecd7a8", bg0="#ddc795", bg1="#fffaee",
        bg2="#ffffff", bg3="#f8efd9", bg4="#ecdcb6", line="#ecdfbe", line2="#d5c094",
        text="#2a2214", text2="#5f5035", text3="#978664",
        accent="#2f7d6d", ok="#2f9a64", warn="#b7791f", danger="#c8423a"),
    "Lavender Dusk": _t(
        base_top="#2a0f2e", base_bottom="#0b0410", bg0="#0b0510", bg1="#190c1e",
        bg2="#221229", bg3="#2d1836", bg4="#391f44", line="#2a1531", line2="#41244b",
        text="#f8edfb", text2="#d0b4da", text3="#8e7299",
        accent="#c792ff", ok="#6fe0a8", warn="#f4c65c", danger="#ff6f95"),
    "Glacier": _t(
        base_top="#0a1f2a", base_bottom="#030b10", bg0="#040c11", bg1="#0a1820",
        bg2="#0f212b", bg3="#152b37", bg4="#1c3644", line="#132631", line2="#22404f",
        text="#ecf8fc", text2="#a9cbd8", text3="#6a8d9b",
        accent="#8fe3ff", ok="#5fdca0", warn="#f2c05a", danger="#ff6b7a"),
    "Graphite": _t(
        base_top="#23252a", base_bottom="#141518", bg0="#121316", bg1="#1b1c20",
        bg2="#232529", bg3="#2d3035", bg4="#383b42", line="#2a2c31", line2="#3b3e45",
        text="#ecedf0", text2="#aaadb5", text3="#6e717a",
        accent="#9fb6d4", ok="#62d39a", warn="#f0b654", danger="#f0636e"),
})

# per theme: description, backdrop (procedural, backdrops.py), second accent
# (two-tone buttons) and panel opacity (the backdrop shows through a little)
EXTRAS = {
    # (14/11) SSA's own theme: two colours per workspace (gradients, lights), and
    # "live": the window's background is painted and animated (motion.Backdrop)
    "Prism": dict(desc="SSA's own look — a colour per workspace, alive", live=True, panel=0.62,
                  ws=dict(home=["#5b9dff", "#b17cff"], map=["#34d399", "#22c1dc"],
                          sessions=["#5b9dff", "#7c6cff"], loot=["#f5b83d", "#ff8a4c"],
                          seedcrack=["#a78bfa", "#f472b6"], drawing=["#fb7185", "#fbbf5d"])),
    "Slate": dict(desc="Cool slate gradient"),
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
    "Oak": dict(desc="Oak planks", accent2="#f3c98a", panel=0.84,
                backdrop=dict(kind="planks", seed=3, base=["#5a3c20", "#a87a48"], seam="#24160b")),
    "Dark Oak": dict(desc="Dark oak planks", accent2="#e3b783", panel=0.84,
                     backdrop=dict(kind="planks", seed=5, base=["#21150b", "#4d3420"],
                                   seam="#0d0804")),
    "Cherry": dict(desc="Cherry planks — light", accent2="#e5809e", panel=0.86,
                   backdrop=dict(kind="planks", seed=7, base=["#d99a8e", "#f4d2c8"],
                                 seam="#9c5a52", light=True)),
    "Crimson": dict(desc="Crimson stem planks (Nether)", accent2="#ff9fb8", panel=0.84,
                    backdrop=dict(kind="planks", seed=9, base=["#4a1a30", "#8a3558"],
                                  seam="#1c0812")),
    "Warped": dict(desc="Warped stem planks (Nether)", accent2="#9ff5e3", panel=0.84,
                   backdrop=dict(kind="planks", seed=11, base=["#173f3c", "#2f8279"],
                                 seam="#081a19")),
    "Synthwave": dict(desc="Retro 80s sunset over a neon grid", accent2="#38e8ff", panel=0.80,
                      backdrop=dict(kind="synthwave", seed=1)),
    "Blueprint": dict(desc="Technical blueprint paper", accent2="#fff0a0", panel=0.84,
                      backdrop=dict(kind="blueprint", seed=2)),
    "Parchment": dict(desc="Old parchment, ink accent — light", accent2="#c0662e", panel=0.86,
                      backdrop=dict(kind="paper", seed=3)),
    "Redstone": dict(desc="Redstone circuits", accent2="#ffb0a0", panel=0.84,
                     backdrop=dict(kind="circuit", seed=6, base=["#160404", "#2a0707"],
                                   trace="#ff3b2f", pad="#ffb0a0")),
    "Honeycomb": dict(desc="Bee nest honeycomb", accent2="#ffe08a", panel=0.84,
                      backdrop=dict(kind="hexagons", seed=5)),
    "Sculk": dict(desc="The deep dark: sculk veins", accent2="#9ff8ff", panel=0.84,
                  backdrop=dict(kind="stone", seed=44, dark="#03080c", light="#0d1c24",
                                specks=["#020507", "#1d3440"], cell=70, accent_specks="#29dfe8",
                                crack="#0b6f78")),
    "Topographic": dict(desc="Contour map on sand — light", accent2="#5fb3a0", panel=0.86,
                        backdrop=dict(kind="glow", seed=51, base=["#f6e7c4", "#e8cf9c"],
                                      blobs=[["#ffd98a", 0.8, 0.1, 0.4], ["#f2b86a", 0.1, 0.9, 0.4]],
                                      light=True, grain=3, ridges=["#c79a5a", 0.35, 420])),
    "Lavender Dusk": dict(desc="Violet dusk and drifting petals", accent2="#ff9fd0", panel=0.82,
                          backdrop=dict(kind="glow", seed=53, base=["#2a0f2e", "#0b0410"],
                                        blobs=[["#c43b6b", 0.25, 0.2, 0.4], ["#6b2a9e", 0.8, 0.7, 0.4]],
                                        petals=["#9a6bd8", 110], crisp=True, stars=1400)),
    "Glacier": dict(desc="Dark glacier ice with white veins", accent2="#d6f6ff", panel=0.84,
                    backdrop=dict(kind="marble", seed=61, base=["#03121a", "#0f3344"],
                                  veins=["#9fdcf0", "#f2fbff"], secondary="#1f5266", vein_amt=0.6,
                                  scale=0.9, angle=0.9)),
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
DEFAULT_THEME = "Prism"

# live values (module globals read everywhere)
THEME = DEFAULT_THEME
TOKENS: dict = dict(BUILTIN[DEFAULT_THEME])
BASE_TOP = BASE_BOTTOM = BG0 = BG1 = BG2 = BG3 = BG4 = LINE = LINE2 = ""
TEXT = TEXT2 = TEXT3 = ACCENT = ACCENT_H = ACCENT_P = ACCENT_SOFT = ACCENT_LINE = ""
OK = WARN = DANGER = HOVER_LINE = OVERLAY = ""
ON_ACCENT = "#ffffff"               # text / icons on an accent-filled button
WORKSPACE = None                    # the page on screen (set_workspace)
IS_LIGHT = False
ELEVATED = True

RADIUS = 6
SP = 8

# (13/11) Inter is shipped (assets/fonts, SIL Open Font License): the same
# typography on Windows and Linux; the system fonts are the fallback
UI_FAMILIES = ["Inter", "Segoe UI Variable Text", "Segoe UI", "Helvetica Neue", "Arial"]
DISPLAY_FAMILIES = ["Inter Display", "Inter", "Segoe UI Variable Display", "Segoe UI", "Arial"]
MONO_FAMILIES = ["Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono"]


class _Signals(QObject):
    changed = Signal()
    accent = Signal()                # the workspace (so theme.ACCENT) changed


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


def _raw_config() -> dict:
    try:
        with open(_config_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_config(data: dict) -> None:
    with open(_config_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def user_themes() -> dict:
    return {k: v for k, v in _raw_config().items() if k in EDITABLE and isinstance(v, dict)}


def theme_tokens(name: str) -> dict:
    t = dict(BUILTIN.get(name, BUILTIN[DEFAULT_THEME]))
    if name in EDITABLE:
        t.update({k: v for k, v in user_themes().get(name, {}).items() if k in t})
    return t


def save_user_theme(name: str, tokens: dict) -> None:
    if name not in EDITABLE:
        return
    data = _raw_config()                 # (17/10) keeps the image themes too
    data[name] = {k: tokens[k] for k, _ in TOKEN_LABELS if k in tokens}
    _write_config(data)


def reset_user_theme(name: str) -> None:
    data = _raw_config()
    data.pop(name, None)
    _write_config(data)


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
    global ON_ACCENT
    ON_ACCENT = on_accent(ACCENT)
    if WORKSPACE:
        _ws_accent()


def on_accent(a: str) -> str:
    """Text colour on a button filled with the accent a"""
    return "#14110a" if QColor(a).lightness() > 150 else "#ffffff"


def accent2(a: str | None = None) -> str:
    """Second colour of the accent gradient (the theme's own, else a lighter
    accent). A workspace's own pair: ws_pair()."""
    return EXTRAS.get(THEME, {}).get("accent2") or _mix(a or ACCENT, "#ffffff", 0.30)


def is_live() -> bool:
    """SSA's own theme: an animated, painted background (motion.Backdrop)"""
    return bool(EXTRAS.get(THEME, {}).get("live"))


def _mix(a: str, b: str, t: float) -> str:
    a, b = QColor(a), QColor(b)
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t)).name()


# ── one colour per workspace (13/11) ────────────────────────────────────────
WS_KEYS = ("home", "map", "sessions", "loot", "seedcrack", "drawing")


def ws_pair(key) -> tuple:
    """(colour, second colour) of a workspace: the theme's own pair, else its accent"""
    v = (EXTRAS.get(THEME, {}).get("ws") or {}).get(key)
    if isinstance(v, (list, tuple)):
        return v[0], v[1]
    a = v or TOKENS.get("accent", ACCENT)
    return a, accent2(a)


def ws_color(key) -> str:
    return ws_pair(key)[0]


def _set_accent(a: str, a2: str | None = None) -> None:
    global ACCENT, ACCENT_H, ACCENT_P, ACCENT_SOFT, ACCENT_LINE, ACCENT2, ON_ACCENT
    ACCENT = a
    ACCENT_H = _shade(a, 1.15)
    ACCENT_P = _shade(a, 0.87)
    ACCENT_SOFT = _rgba(a, 0.16 if not IS_LIGHT else 0.12)
    ACCENT_LINE = _rgba(a, 0.45)
    ACCENT2 = a2 or EXTRAS.get(THEME, {}).get("accent2") or ACCENT_H
    ON_ACCENT = on_accent(a)


def set_workspace(key) -> None:
    """The page on screen. In a theme with one colour per workspace the accent
    follows it: the stylesheet's rules are scoped by the pages' `ws` property,
    and what is painted by hand reads theme.ACCENT (signals.accent → repaint)."""
    global WORKSPACE
    WORKSPACE = key
    _ws_accent()
    signals.accent.emit()


def _ws_accent() -> None:
    """theme.ACCENT* = the colours of the workspace on screen"""
    if (EXTRAS.get(THEME, {}).get("ws") or {}).get(WORKSPACE):
        _set_accent(*ws_pair(WORKSPACE))
    else:
        _set_accent(TOKENS.get("accent", ACCENT))


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


_FONTS_LOADED = False


def load_fonts() -> None:
    """Register the shipped fonts (assets/fonts) once, before the first font is asked for"""
    global _FONTS_LOADED
    if _FONTS_LOADED:
        return
    _FONTS_LOADED = True
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
    try:
        for fn in sorted(os.listdir(d)):
            if fn.lower().endswith((".ttf", ".otf")):
                QFontDatabase.addApplicationFont(os.path.join(d, fn))
    except OSError:
        pass


def num_font(pt: float = 9.5, weight: QFont.Weight = QFont.Weight.Medium, display=False) -> QFont:
    """The UI font with tabular figures and a slashed zero: seeds, coordinates, counts"""
    f = QFont(_pick(DISPLAY_FAMILIES if display else UI_FAMILIES))
    f.setPointSizeF(pt)
    f.setWeight(weight)
    try:
        f.setFeature(QFont.Tag("tnum"), 1)
        f.setFeature(QFont.Tag("zero"), 1)
    except Exception:                    # Qt < 6.7: proportional figures
        pass
    return f


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
                 f'fill="none" stroke="{ON_ACCENT}" stroke-width="2.2" stroke-linecap="round" '
                 'stroke-linejoin="round"/></svg>',
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
    tag = QColor(TEXT2).name()[1:] + QColor(ON_ACCENT).name()[1:]
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
# Frame of the textured themes (08/10): "bands" = translucent bars (before),
# "veil" = the whole backdrop under an even veil, "glass" = a lighter veil and
# hairlines, "clear" = the pure backdrop, the chrome items on small pills.
FRAME_STYLES = ("veil", "glass", "clear", "bands")
FRAME_VEIL = {"veil": 0.42, "glass": 0.22}


FRAME_OVERRIDE = None          # the Appearance window's live preview


def frame_style() -> str:
    if FRAME_OVERRIDE in FRAME_STYLES:
        return FRAME_OVERRIDE
    v = os.environ.get("SSA_FRAME")
    if not v:
        try:
            from PySide6.QtCore import QSettings
            v = str(QSettings("SSA", "SimpleSeedAnalyser").value("theme/frame", "") or "")
        except Exception:
            v = ""
    return v if v in FRAME_STYLES else "veil"


def stylesheet() -> str:
    """The application's stylesheet: the base rules, then the 1.3 look (13/11).
    Built with the theme's own accent; the workspace colours are scoped rules."""
    _set_accent(TOKENS.get("accent", ACCENT))
    try:
        return _base_stylesheet() + _look()
    finally:
        if WORKSPACE:
            _ws_accent()


def _base_stylesheet() -> str:
    mono = _pick(MONO_FAMILIES)
    A = _assets()
    root_bg = f"qlineargradient(x1:0, y1:0, x2:0.35, y2:1, stop:0 {BASE_TOP}, stop:1 {BASE_BOTTOM})"
    root_img = ""
    frame = frame_style()
    if BACKDROP:
        from .backdrops import backdrop_path
        spec = dict(BACKDROP)
        if frame in FRAME_VEIL:
            spec["veil"] = [BG1, FRAME_VEIL[frame]]
        bp = backdrop_path(spec)
        if bp:
            root_img = f'border-image: url("{bp}") 0 0 0 0 stretch stretch;'
    acc_grad = (f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {ACCENT}, stop:1 {ACCENT2})"
                if EXTRAS.get(THEME, {}).get("accent2") else ACCENT)
    on_acc = "#1a1408" if QColor(ACCENT).lightness() > 150 else "white"
    # the nav rail / status bar sat right on the textured backdrop (strata,
    # marble…): their muted text vanished on it (Mesa « Theme », 01/10). A
    # translucent panel tint under them keeps the backdrop visible but readable.
    chrome_bg = _rgba(BG1, 0.72) if (BACKDROP and frame == "bands") else "transparent"
    nav_text = TEXT2 if BACKDROP else TEXT3
    extra = ""
    if BACKDROP and frame == "clear":
        # pure backdrop: no band; the chrome's own items carry a small pill
        pill = _rgba(BG1, 0.62)
        extra = f"""
QToolButton#NavButton {{ background: {pill}; }}
#StatusBar QLabel {{ background: {pill}; border-radius: 9px; padding: 1px 9px; }}
#TopBar QLabel#FieldLabel {{ background: {pill}; border-radius: 6px; padding: 0px 5px; }}
#TopBar QLabel#AppName {{ background: {pill}; border-radius: 8px; padding: 2px 8px; }}
"""
    elif BACKDROP and frame == "glass":
        extra = f"""
#TopBar {{ border-bottom: 1px solid {_rgba(ACCENT, 0.28)}; }}
#NavRail {{ border-right: 1px solid {_rgba(ACCENT, 0.28)}; }}
#StatusBar {{ border-top: 1px solid {_rgba(ACCENT, 0.28)}; }}
"""
    return f"""
* {{ outline: 0; }}
QWidget {{ color: {TEXT}; background: transparent; selection-background-color: {ACCENT}; selection-color: white; }}
QMainWindow, #Root {{ background: {root_bg}; {root_img} }}
QDialog {{ background: {BG1}; }}
QToolTip {{ background: {BG2}; color: {TEXT}; border: 1px solid {LINE2}; padding: 6px 10px; border-radius: 10px;{_tip_margin()} }}

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
QPushButton:disabled {{ color: {TEXT3}; background: transparent; border: 1px dashed {LINE2}; }}
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
/* (08/10) a control greyed ON PURPOSE looks it: dashed outline, faint text —
   hover it for the reason */
QPushButton[seg]:disabled {{ background: transparent; color: {_rgba(TEXT3, 0.7)}; border: 1px dashed {LINE2}; }}
QPushButton[seg]:checked:disabled {{ background: {_rgba(ACCENT, 0.08)}; color: {TEXT3}; border: 1px dashed {ACCENT_LINE}; }}
QToolButton:disabled {{ border: 1px dashed {LINE}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QTimeEdit:disabled, QComboBox:disabled {{
    color: {TEXT3}; background: transparent; border: 1px dashed {LINE2}; }}
QCheckBox::indicator:disabled {{ border: 1px dashed {LINE2}; background: transparent; }}

/* ---------- inputs ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QTimeEdit, QComboBox {{
    background: {BG2}; border: 1px solid {LINE2}; border-radius: {RADIUS}px;
    padding: 4px 8px; min-height: 18px;
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: {HOVER_LINE}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit#SeedEdit {{ font-family: "{mono}"; font-size: 10pt; padding-left: 10px; }}
QComboBox::drop-down {{ border: 0; width: 20px; }}
QToolButton#VersionButton {{
    background: {BG2}; border: 1px solid {LINE2}; border-radius: {RADIUS}px;
    padding: 4px 22px 4px 8px; min-height: 18px; text-align: left;
}}
QToolButton#VersionButton:hover {{ border-color: {HOVER_LINE}; }}
QToolButton#VersionButton::menu-indicator {{ subcontrol-origin: padding; subcontrol-position: right center; right: 6px; }}
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
QPushButton#BiomeChip {{ background: {BG3}; border: 1px solid {LINE}; border-radius: 11px;
                         padding: 3px 10px 3px 7px; min-height: 16px; color: {TEXT2}; }}
QPushButton#BiomeChip:hover {{ border-color: {HOVER_LINE}; color: {TEXT}; }}
QPushButton#BiomeChip:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {TEXT}; font-weight: 600; }}
QPushButton#BiomeChip:disabled {{ background: transparent; border: 1px dashed {LINE}; color: {_rgba(TEXT3, 0.6)}; }}
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
QPushButton[kind="danger"]:disabled, QPushButton[kind="ghost"]:disabled {{ color: {TEXT3}; border: 1px dashed {LINE}; background: transparent; }}
QTableView#Results {{ background: transparent; border: 0; }}
QProgressBar {{ background: {BG3}; border: 0; border-radius: 3px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QTreeView::indicator, QTreeWidget::indicator {{ width: 14px; height: 14px; border-radius: 4px; border: 1px solid {LINE2}; background: {BG2}; }}
QTreeView::indicator:checked, QTreeWidget::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{A['check']}"); }}
QMenu::item:disabled {{ color: {TEXT3}; }}
{extra}
"""


# ── the 1.3 look (13/11) ─────────────────────────────────────────────────────
# Appended to the base rules: larger radii, inset fields, gradient primary
# buttons, the top tabs, cards. Everything comes from the theme's tokens, so
# every theme (built-in, user, picture) gets it.
def _accent_rules(p: str, a: str, a2: str | None = None) -> str:
    """The rules that carry the accent, for the widgets under the selector p
    ("" = everywhere, 'QWidget[ws="loot"] ' = one workspace)."""
    a2, on = a2 or accent2(a), on_accent(a)
    grad = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {a}, stop:1 {a2})"
    soft = _rgba(a, 0.16 if not IS_LIGHT else 0.12)
    return f"""
{p}QPushButton[kind="primary"] {{ background: {grad}; border: 0; color: {on}; }}
{p}QPushButton[kind="primary"]:hover {{ background: {a2}; }}
{p}QPushButton[kind="primary"]:pressed {{ background: {_shade(a, 0.87)}; }}
{p}QPushButton[kind="primary"]:disabled {{ background: {_rgba(a, 0.30)}; color: {_rgba(on, 0.70)}; border: 0; }}
{p}QPushButton[seg]:checked {{ background: {soft}; color: {TEXT}; border-color: {_rgba(a, 0.55)}; }}
{p}QPushButton[seg]:checked:disabled {{ background: {_rgba(a, 0.08)}; color: {TEXT3}; border: 1px dashed {_rgba(a, 0.45)}; }}
{p}QLineEdit:focus, {p}QSpinBox:focus, {p}QDoubleSpinBox:focus, {p}QTimeEdit:focus, {p}QComboBox:focus {{ border-color: {a}; }}
{p}QCheckBox::indicator:hover {{ border-color: {a}; }}
{p}QCheckBox::indicator:checked {{ background: {a}; border-color: {a}; }}
{p}QTreeView::indicator:checked, {p}QTreeWidget::indicator:checked {{ background: {a}; border-color: {a}; }}
{p}QSlider::sub-page:horizontal {{ background: {a}; }}
{p}QProgressBar::chunk {{ background: {grad}; }}
{p}QToolButton:checked {{ background: {soft}; border-color: {_rgba(a, 0.45)}; }}
{p}QToolButton#MapTool:checked {{ border: 1px solid {a}; }}
{p}QPushButton#BiomeChip:checked {{ background: {soft}; border-color: {a}; }}
{p}QTableView::item:selected, {p}QTreeView::item:selected, {p}QListView::item:selected, {p}QListWidget::item:selected {{ background: {soft}; }}
{p}QMenu::item:selected {{ background: {soft}; }}
{p}QAbstractItemView {{ selection-background-color: {soft}; selection-color: {TEXT}; }}
{p}QLineEdit, {p}QAbstractSpinBox, {p}QTextEdit, {p}QPlainTextEdit {{ selection-background-color: {a}; selection-color: {on}; }}
{p}QPushButton#TypeButton:hover {{ border-color: {_rgba(a, 0.45)}; }}
{p}QFrame#AreaRow[active="true"] {{ background: {_rgba(a, 0.10)}; border: 1px solid {_rgba(a, 0.42)}; }}
{p}QFrame#CrackSource:hover {{ background: {_rgba(a, 0.07)}; border-color: {_rgba(a, 0.45)}; }}
{p}QFrame#CrackSource[used="true"] {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {_rgba(a, 0.16)}, stop:1 {_rgba(a, 0.04)}); border-color: {_rgba(a, 0.55)}; }}
""" + (f"""
{p}#SidePanel, {p}#Surface, {p}QFrame#RunBar {{ border: 1px solid qlineargradient(x1:0, y1:0, x2:1, y2:1,
    stop:0 {_rgba(a, 0.55)}, stop:0.22 {LINE}, stop:0.78 {LINE}, stop:1 {_rgba(a2, 0.40)}); }}
{p}QLabel#FieldLabel {{ color: {_mix(TEXT3, a, 0.42)}; }}
{p}QLabel#SectionTitle {{ color: {_mix(TEXT2, a, 0.62)}; }}
{p}QFrame#HLine {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {_rgba(a, 0.55)}, stop:0.5 {LINE}, stop:1 {_rgba(a2, 0.35)}); }}
{p}QPushButton[kind="ghost"]:hover {{ border-color: {_rgba(a, 0.55)}; background: {_rgba(a, 0.10)}; }}
{p}QToolButton:hover {{ background: {_rgba(a, 0.14)}; }}
{p}QScrollBar::handle:vertical:hover, {p}QScrollBar::handle:horizontal:hover {{ background: {_rgba(a, 0.55)}; }}
""" if is_live() else "")


def _tip_margin() -> str:
    """(19/11) the help bubbles' transparent margin (their shadow), where their window is
    translucent (qtui/bubbles.py)"""
    try:
        from PySide6.QtGui import QGuiApplication
        from .bubbles import MARGIN
        if QGuiApplication.platformName() in ("windows", "xcb", "cocoa", "offscreen"):
            return f" margin: {MARGIN}px;"
    except Exception:
        pass
    return ""


def field_bg() -> str:
    """Background of the inset fields (inputs, segmented controls, capsules)"""
    return _mix(BG1, "#000000", 0.26 if not IS_LIGHT else 0.035)


def _look() -> str:
    R, PR = RADIUS, 14
    a = ACCENT
    field = field_bg()
    top = _mix(BG1, "#ffffff", 0.045)
    panel = (f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {top}, stop:0.10 {BG1}, stop:1 {BG1})"
             if PANEL == BG1 else PANEL)
    if is_live() and PANEL != BG1:               # glass: the lights of the workspace show through
        ga = float(EXTRAS.get(THEME, {}).get("panel", 0.62))
        panel = (f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {_rgba(top, min(1.0, ga + 0.14))}, "
                 f"stop:0.12 {_rgba(BG1, ga + 0.06)}, stop:1 {_rgba(BG1, ga)})")
    disp = _pick(DISPLAY_FAMILIES)
    s = ""
    if not BACKDROP and not is_live():      # a soft light of the accent, top left (live: motion.Backdrop)
        s += (f"QMainWindow, #Root {{ background: qradialgradient(cx:0.08, cy:0, radius:1.05, fx:0.08, fy:0, "
              f"stop:0 {_mix(BASE_TOP, a, 0.11)}, stop:0.28 {BASE_TOP}, stop:1 {BASE_BOTTOM}); }}\n")
    s += f"""
#SidePanel, #Surface, QFrame#RunBar {{ background: {panel}; border: 1px solid {LINE}; border-radius: {PR}px; }}
QLabel#PanelTitle {{ font-family: "{disp}"; font-size: 13.5pt; font-weight: 700; }}
QLabel#AppName {{ font-family: "{disp}"; font-size: 12pt; font-weight: 700; }}
QLabel#PageTitle {{ font-family: "{disp}"; font-weight: 700; }}
QLabel#FieldLabel {{ font-size: 7pt; font-weight: 700; letter-spacing: 1.3px; color: {TEXT3}; }}
QLabel#SectionTitle {{ font-size: 7.5pt; letter-spacing: 1.3px; }}
QFrame#AreaRow {{ border: 1px solid transparent; border-radius: 10px; }}
QFrame#CrackSource {{ background: {_rgba(TEXT, 0.04)}; border: 1px solid {LINE}; border-radius: 12px; }}
QFrame#AreaRow[active="false"]:hover {{ background: {_rgba(TEXT, 0.035)}; border: 1px solid {LINE}; }}
QFrame#AreaRow[active="false"] QLabel {{ color: {TEXT3}; }}
QToolTip {{ border-radius: 10px; padding: 5px 10px; background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {_mix(BG3, '#ffffff', 0.04)}, stop:1 {BG3}); }}
QMenu {{ border-radius: 12px; padding: 6px; }}
QMenu::item {{ padding: 7px 20px; border-radius: 7px; }}

QPushButton {{ border-radius: {R}px; padding: 6px 13px; }}
QPushButton[kind="primary"] {{ font-weight: 700; padding: 7px 16px; }}
QPushButton[kind="ghost"] {{ border: 1px solid {LINE2}; color: {TEXT}; }}
QFrame#RunBar QPushButton {{ min-height: 24px; padding: 7px 18px; border-radius: {R + 3}px; font-weight: 600; }}
QFrame#RunBar QPushButton[kind="primary"] {{ padding: 7px 28px; font-size: 10.5pt; font-weight: 700; }}
QToolButton {{ border-radius: {R}px; }}
QToolButton#PanelTool {{ padding: 5px; border-radius: 9px; }}

QPushButton[seg="mid"], QPushButton[seg="first"], QPushButton[seg="last"], QPushButton[seg="only"] {{
    border-radius: 0; padding: 4px 11px; background: {field}; border: 1px solid {LINE2}; color: {TEXT2}; }}
QPushButton[seg="first"] {{ border-top-left-radius: {R}px; border-bottom-left-radius: {R}px; }}
QPushButton[seg="last"] {{ border-top-right-radius: {R}px; border-bottom-right-radius: {R}px; }}
QPushButton[seg="only"] {{ border-radius: {R}px; }}
QPushButton[seg]:checked {{ font-weight: 600; }}
QPushButton[seg]:disabled {{ background: transparent; border: 1px dashed {LINE2}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QTimeEdit, QComboBox {{
    background: {field}; border: 1px solid {LINE2}; border-radius: {R}px; padding: 5px 9px; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QTimeEdit:disabled, QComboBox:disabled {{
    background: transparent; border: 1px dashed {LINE2}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; background: {field}; }}
QCheckBox::indicator:disabled {{ background: transparent; }}
QTreeView::indicator, QTreeWidget::indicator {{ width: 15px; height: 15px; border-radius: 5px; background: {field}; }}
QScrollBar:vertical {{ width: 8px; margin: 3px 1px; }}
QScrollBar::handle:vertical {{ border-radius: 3px; }}
QLabel#Chip {{ border-radius: 11px; padding: 3px 11px; min-height: 16px; }}
QProgressBar {{ border-radius: 3px; background: {field}; }}
QProgressBar::chunk {{ border-radius: 3px; }}
#StatusBar QLabel {{ color: {TEXT3}; font-size: 8.5pt; }}

/* conditions and results: cards painted by their delegates (sessions_page) */
QTreeWidget#CondTree {{ font-size: 9.75pt; font-weight: 500; }}
QTreeWidget#CondTree::item {{ padding: 9px 2px; border-radius: 0; background: transparent; }}
QTreeWidget#CondTree::item:hover, QTreeWidget#CondTree::item:selected {{ background: transparent; }}
QTableView#Results::item, QTableView#Results::item:selected, QTableView#Results::item:hover {{ background: transparent; }}

/* top bar: the seed capsule, the workspace tabs, the tools */
QFrame#SeedCapsule {{ background: {field}; border: 1px solid {LINE2}; border-radius: 13px; }}
QFrame#SeedCapsule QLineEdit#SeedEdit {{ background: transparent; border: 0; font-family: "{_pick(UI_FAMILIES)}";
    font-size: 11.5pt; font-weight: 600; padding: 0 4px; }}
QFrame#SeedCapsule QToolButton {{ border: 0; border-radius: 9px; }}
QToolButton#VersionButton {{ border-radius: 13px; padding: 8px 26px 8px 13px; background: {field};
    font-weight: 600; min-height: 22px; }}
QToolButton#TopTool {{ border: 0; border-radius: 10px; padding: 7px; }}
QToolButton#TopTool:hover {{ background: {BG3}; }}
QFrame#TopTabs {{ background: {field}; border: 1px solid {LINE}; border-radius: 14px; }}
QToolButton#NavButton {{ color: {TEXT2}; border: 0; border-radius: 10px; padding: 6px 13px 6px 10px;
    font-size: 9.5pt; font-weight: 600; }}
QToolButton#NavButton:hover {{ background: {BG3}; color: {TEXT}; }}
QToolButton#NavButton:checked {{ border: 0; }}

/* Home */
QFrame#WsTile {{ background: {panel}; border: 1px solid {LINE}; border-radius: 16px; }}
QFrame#RecentRow {{ border-radius: 10px; background: {_rgba(BG2, 0.55)}; }}
QFrame#RecentRow:hover {{ background: {BG3}; }}
QLabel#TileTitle {{ font-family: "{disp}"; font-size: 12.5pt; font-weight: 700; }}
QLabel#NewChip {{ border-radius: 12px; padding: 5px 12px; font-weight: 600; font-size: 9pt; }}
QPushButton#VerPill {{ border: 1px solid transparent; border-radius: 11px; padding: 3px 10px; min-height: 0;
    background: transparent; color: {TEXT3}; font-size: 8.5pt; font-weight: 600; }}
QPushButton#VerPill:hover {{ color: {TEXT}; background: {_rgba(TEXT, 0.06)}; }}
QPushButton#VerPill:checked {{ color: {TEXT}; background: {_rgba(a, 0.16)}; border-color: {_rgba(a, 0.45)}; }}
QPushButton#CardLink {{ border: 0; background: transparent; color: {TEXT2}; padding: 4px 8px; font-weight: 600; }}
QPushButton#CardLink:hover {{ color: {TEXT}; background: {_rgba(TEXT, 0.06)}; border-radius: 9px; }}
QLabel#NewsDetail {{ color: {TEXT2}; }}

/* Settings (19/11) */
QFrame#SetGroup {{ background: {_rgba(TEXT, 0.035)}; border: 1px solid {LINE}; border-radius: 14px; }}
QFrame#SetRow {{ border-radius: 10px; background: transparent; border: 0; }}
QFrame#SetRow:hover {{ background: {_rgba(TEXT, 0.04)}; }}
QLabel#SetTitle {{ font-weight: 600; }}
QLabel#SetPageTitle {{ font-family: "{disp}"; font-size: 16pt; font-weight: 700; }}
QLabel#SetSection {{ color: {TEXT3}; font-size: 7.5pt; font-weight: 700; letter-spacing: 1.3px; padding-left: 6px; }}
QPushButton#SetNavButton {{ border: 0; background: transparent; color: {TEXT2}; text-align: left;
    padding: 6px 10px 6px 14px; font-weight: 600; font-size: 9.5pt; border-radius: 10px; }}
QPushButton#SetNavButton:hover {{ background: transparent; color: {TEXT}; border: 0; }}
QPushButton#SetNavButton:checked {{ color: {TEXT}; background: transparent; border: 0; }}
QPushButton#SetNavButton[dim="true"] {{ color: {TEXT3}; }}
QFrame#SetBar {{ background: transparent; border: 0; border-top: 1px solid {LINE}; }}
QFrame#AboutHero {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {_rgba(a, 0.16)},
    stop:0.55 {_rgba(a, 0.05)}, stop:1 {_rgba(TEXT, 0.02)}); border: 1px solid {_rgba(a, 0.32)}; border-radius: 16px; }}
QLabel#AboutName {{ font-family: "{disp}"; font-size: 17pt; font-weight: 700; }}

/* Seed Library (19/11) */
QPushButton#ShelfButton {{ background: transparent; border: 0; color: transparent; text-align: left; }}
QFrame#SeedHead {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {_rgba("#f5b942", 0.13)},
    stop:0.45 {_rgba(BG1, 0.0)}, stop:1 {_rgba(a, 0.07)}); border: 1px solid {_rgba("#f5b942", 0.30)};
    border-radius: 18px; }}
QLineEdit#SeedName {{ font-family: "{disp}"; font-size: 18pt; font-weight: 700; background: transparent;
    border: 1px solid transparent; border-radius: 8px; padding: 2px 6px; }}
QLineEdit#SeedName:hover {{ border-color: {LINE2}; }}
QLineEdit#SeedName:focus {{ border-color: #f5b942; background: {_rgba(BG2, 0.8)}; }}
QPushButton#TagChip {{ background: {_rgba("#f5b942", 0.14)}; color: #f5c25a; border: 0; border-radius: 9px;
    padding: 2px 10px; font-size: 8.5pt; font-weight: 600; min-height: 18px; max-height: 20px; }}
QPushButton#TagChip:hover {{ background: {_rgba(DANGER, 0.18)}; color: {DANGER}; }}
QLineEdit#TagAdd {{ background: {_rgba("#f5b942", 0.05)}; border: 1px dashed {_rgba("#f5b942", 0.55)}; border-radius: 9px;
    padding: 1px 10px; font-size: 8.5pt; font-weight: 600; color: #f5c25a; min-height: 18px; max-height: 20px; }}
QLineEdit#TagAdd:hover {{ background: {_rgba("#f5b942", 0.12)}; border-color: #f5b942; }}
QLineEdit#TagAdd:focus {{ border: 1px solid #f5b942; background: {_rgba(BG2, 0.85)}; color: {TEXT}; }}
QFrame#MapEdSide {{ background: {_rgba(BG1, 0.72)}; border: 1px solid {LINE}; border-radius: 16px; }}
QLabel#MapEdSection {{ color: {TEXT3}; font-size: 7.6pt; font-weight: 700; letter-spacing: 1px; padding-top: 4px; }}
QLineEdit#MapEdNum {{ background: {_rgba(BG3, 0.9)}; border: 1px solid {LINE}; border-radius: 8px; padding: 4px 8px; }}
QLineEdit#MapEdNum:focus {{ border-color: #f5b942; }}
QPushButton#MapPlace {{ background: {_rgba(TEXT, 0.06)}; border: 1px solid {LINE}; border-radius: 11px;
    padding: 3px 11px; font-size: 8.5pt; font-weight: 600; }}
QPushButton#MapPlace:hover {{ background: {_rgba("#f5b942", 0.14)}; border-color: {_rgba("#f5b942", 0.6)}; color: #f5c25a; }}
QPushButton#MapChip {{ background: {_rgba(TEXT, 0.04)}; border: 1px solid {LINE}; border-radius: 11px;
    padding: 3px 10px 3px 7px; font-size: 8.5pt; color: {TEXT3}; }}
QPushButton#MapChip:hover {{ border-color: {LINE2}; color: {TEXT2}; }}
QPushButton#MapChip:checked {{ background: {_rgba("#f5b942", 0.15)}; border-color: {_rgba("#f5b942", 0.65)}; color: {TEXT}; }}
QScrollArea#MapEdChips, QScrollArea#MapEdChips > QWidget > QWidget {{ background: transparent; }}
QLabel#LibToast {{ background: {_rgba(BG3, 0.94)}; color: {TEXT}; border: 1px solid {_rgba("#f5b942", 0.55)};
    border-radius: 12px; padding: 7px 16px; font-weight: 600; }}
QLabel#MapEdZoom {{ color: #f5c25a; font-weight: 700; }}
QLineEdit#AddSeedNum {{ background: {_rgba(BG3, 0.9)}; border: 1px solid {LINE}; border-radius: 10px; padding: 6px 10px; }}
QLineEdit#AddSeedNum:focus {{ border-color: #f5b942; }}
QPushButton#AddBlock {{ background: {_rgba(TEXT, 0.05)}; border: 1px solid {LINE}; border-radius: 10px;
    padding: 6px 12px; font-weight: 600; }}
QPushButton#AddBlock:hover {{ background: {_rgba("#f5b942", 0.14)}; border-color: {_rgba("#f5b942", 0.55)}; }}
QScrollArea#BoardScroll {{ background: {_rgba(BG1, 0.55)}; border: 1px solid {LINE}; border-radius: 18px; }}
QScrollArea#BoardScroll > QWidget > QWidget {{ background: transparent; }}
QLineEdit#BlockCaption {{ background: transparent; border: 0; color: #ffffff; font-weight: 600; }}
QLineEdit#FlatEdit {{ background: {_rgba(TEXT, 0.05)}; border: 1px solid transparent; border-radius: 6px;
    padding: 2px 5px; }}
QLineEdit#FlatEdit:focus {{ border-color: #f5b942; }}
"""
    s += _accent_rules("", a)
    for k in WS_KEYS:
        c = ws_color(k)
        # (the checked tab's pill is painted, and slides, by TopTabs)
        s += (f'QToolButton#NavButton[page="{k}"]:checked {{ color: {c}; background: transparent; }}\n'
              f'QFrame#WsTile[ws="{k}"]:hover {{ border-color: {_rgba(c, 0.8)}; }}\n')
        if (EXTRAS.get(THEME, {}).get("ws") or {}).get(k):
            s += _accent_rules(f'QWidget[ws="{k}"] ', *ws_pair(k))
    if is_live():                                # (14/11 bis) no plain black window, menu or tip
        h1, h2 = ws_pair("home")
        s += f"""
QDialog {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {_mix(BG1, h1, 0.22)},
    stop:0.42 {_mix(BG1, h1, 0.05)}, stop:1 {_mix(BG1, h2, 0.20)}); }}
QMenu {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {_mix(BG2, h1, 0.14)}, stop:1 {_mix(BG2, h2, 0.10)});
    border: 1px solid {_rgba(h1, 0.40)}; }}
QToolTip {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {_mix(BG3, h1, 0.20)}, stop:1 {_mix(BG3, h2, 0.10)}); border: 1px solid {_rgba(h1, 0.50)}; }}
#SidePanel[float="1"] {{ background: {_rgba(BG1, 0.90)}; }}
#SidePanel[float="1"] QCheckBox, #SidePanel[float="1"] QLabel#Body {{ color: {TEXT}; }}
QComboBox QAbstractItemView {{ background: {_mix(BG2, h1, 0.10)}; border: 1px solid {_rgba(h1, 0.40)}; }}
"""
    return s


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
        from . import bubbles                    # theme change cost ~1 s (full re-polish)
        bubbles.install(app)                     # (19/11) Fusion + the round help bubbles
        app._ssa_fusion = True
    load_fonts()
    app.setFont(ui_font(9.5))
    app.setPalette(_palette())
    app.setStyleSheet(stylesheet())
    signals.changed.emit()


def _saved_theme_name() -> str:
    try:
        from PySide6.QtCore import QSettings
        qs = QSettings("SSA", "SimpleSeedAnalyser")
        if int(qs.value("ui/look", 0) or 0) < 2:     # (14/11) the redesign: SSA's own theme, once —
            qs.setValue("ui/look", 2)                 # a theme chosen afterwards is kept as before
            qs.setValue("ui/theme", DEFAULT_THEME)
        v = qs.value("ui/theme", DEFAULT_THEME)
        return v if v in BUILTIN else DEFAULT_THEME
    except Exception:
        return DEFAULT_THEME


def remember_theme(name: str) -> None:
    from PySide6.QtCore import QSettings
    QSettings("SSA", "SimpleSeedAnalyser").setValue("ui/theme", name)


# ── themes made from the user's own picture (17/10) ─────────────────────────
# Stored in themes.json under "__images": name -> {path (a copy of the picture
# in the configuration folder), tokens, accent2, light}. The picture is the
# window backdrop (backdrops.image), the colours are derived from it.
IMAGE_KEY = "__images"


def image_themes() -> dict:
    d = _raw_config().get(IMAGE_KEY, {})
    return d if isinstance(d, dict) else {}


def is_image_theme(name: str) -> bool:
    return bool(EXTRAS.get(name, {}).get("custom_image"))


def _register_image_themes() -> None:
    for name, d in image_themes().items():
        try:
            if not os.path.exists(d["path"]):
                continue
            BUILTIN[name] = dict(d["tokens"])
            EXTRAS[name] = dict(desc="Your picture" + (" — light" if d.get("light") else ""),
                                accent2=d.get("accent2"), panel=float(d.get("panel", 0.82)),
                                custom_image=True,
                                backdrop=dict(kind="image", path=d["path"],
                                              stamp=int(d.get("stamp", 0)),
                                              light=bool(d.get("light"))))
        except Exception:
            continue


def _image_colours(path: str):
    """(average colour, accent colour, mean lightness 0..1) of a picture."""
    from PySide6.QtGui import QImage
    img = QImage(path)
    if img.isNull():
        raise ValueError("This file is not a picture SSA can read.")
    small = img.scaled(64, 40)
    tot = [0.0, 0.0, 0.0]; n = 0
    best, best_w = None, -1.0
    buckets: dict = {}
    for y in range(small.height()):
        for x in range(small.width()):
            c = QColor(small.pixel(x, y))
            tot[0] += c.red(); tot[1] += c.green(); tot[2] += c.blue(); n += 1
            h, s_, v, _a = c.getHsvF()
            if h < 0 or s_ < 0.25 or v < 0.25:
                continue
            k = int(h * 18) % 18
            b = buckets.setdefault(k, [0.0, 0.0, 0.0, 0.0, 0])
            w = s_ * v
            b[0] += c.red() * w; b[1] += c.green() * w; b[2] += c.blue() * w; b[3] += w; b[4] += 1
    avg = QColor(int(tot[0] / n), int(tot[1] / n), int(tot[2] / n))
    for b in buckets.values():                 # the most present vivid hue
        if b[3] * (1 + 0.02 * b[4]) > best_w:
            best_w = b[3] * (1 + 0.02 * b[4])
            best = QColor(int(b[0] / b[3]), int(b[1] / b[3]), int(b[2] / b[3]))
    return avg, best, avg.lightnessF()


def _mix_hex(a: str, b: str, t: float) -> str:
    ca, cb = QColor(a), QColor(b)
    return QColor(int(ca.red() + (cb.red() - ca.red()) * t), int(ca.green() + (cb.green() - ca.green()) * t),
                  int(ca.blue() + (cb.blue() - ca.blue()) * t)).name()


def _image_tokens(src: str, accent: str | None = None):
    """(22/10) The colours of a theme drawn from a picture: dark or light from
    its brightness, tinted with its average colour, accent = its most present
    vivid colour (or `accent`). -> (tokens, light)"""
    avg, acc, light = _image_colours(src)
    is_light = light > 0.62
    base = dict(BUILTIN["Daylight" if is_light else "Slate"])
    tint = 0.10 if is_light else 0.22
    t = {}
    for k, v in base.items():
        if k in ("ok", "warn", "danger", "accent", "text"):
            t[k] = v
        else:
            t[k] = _mix_hex(v, avg.name(), tint)
    if acc is not None:
        a = QColor(acc)
        h, s_, v, _x = a.getHsvF()
        a.setHsvF(max(h, 0), min(1.0, max(s_, 0.55)), 0.62 if is_light else 0.95)
        t["accent"] = a.name()
    if accent:
        t["accent"] = accent
    t["accent"] = readable(t["accent"], t["text"], [t["bg1"], t["bg2"]], 3.0)
    return t, is_light


def create_image_theme(src: str, name: str | None = None) -> str:
    """New theme from a picture: copies it to the configuration folder and
    derives the colours (see _image_tokens). Returns the name."""
    import shutil
    t, is_light = _image_tokens(src)
    d = os.path.join(os.path.dirname(_config_path()), "theme_images")
    os.makedirs(d, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src))[0][:28] or "Picture"
    if not name:
        name = stem.replace("_", " ").strip().title() or "My picture"
        base_name, i = name, 2
        while name in BUILTIN and not is_image_theme(name) or name in image_themes():
            name = f"{base_name} {i}"; i += 1
    ext = os.path.splitext(src)[1].lower() or ".png"
    dst = os.path.join(d, f"{abs(hash((name, os.path.getsize(src)))) & 0xffffffff:08x}{ext}")
    shutil.copyfile(src, dst)
    data = _raw_config()
    imgs = data.get(IMAGE_KEY) if isinstance(data.get(IMAGE_KEY), dict) else {}
    imgs[name] = dict(path=dst, tokens=t, accent2=_shade(t["accent"], 1.25), light=is_light,
                      panel=0.86 if is_light else 0.82, stamp=int(os.path.getmtime(dst)))
    data[IMAGE_KEY] = imgs
    _write_config(data)
    _register_image_themes()
    return name


def rename_image_theme(old: str, new: str) -> str:
    """(22/10) Renames a picture theme; returns the name kept (stripped).
    Raises ValueError if the name is empty or already used."""
    new = " ".join((new or "").split())[:40]
    if not new:
        raise ValueError("The name is empty.")
    if new == old:
        return old
    if new in BUILTIN or new in image_themes():
        raise ValueError(f"A theme called « {new} » already exists.")
    data = _raw_config()
    imgs = data.get(IMAGE_KEY) if isinstance(data.get(IMAGE_KEY), dict) else {}
    if old not in imgs:
        raise ValueError("Only your picture themes can be renamed.")
    imgs[new] = imgs.pop(old)
    data[IMAGE_KEY] = imgs
    _write_config(data)
    BUILTIN.pop(old, None); EXTRAS.pop(old, None)
    _register_image_themes()
    from PySide6.QtCore import QSettings
    qs = QSettings("SSA", "SimpleSeedAnalyser")
    if str(qs.value("ui/theme", "") or "") == old:
        qs.setValue("ui/theme", new)
    return new


def delete_image_theme(name: str) -> None:
    data = _raw_config()
    imgs = data.get(IMAGE_KEY) if isinstance(data.get(IMAGE_KEY), dict) else {}
    d = imgs.pop(name, None)
    data[IMAGE_KEY] = imgs
    _write_config(data)
    BUILTIN.pop(name, None); EXTRAS.pop(name, None)
    if d:
        try:
            os.remove(d["path"])
        except OSError:
            pass


# ── (22/10) Landscapes: built-in photo themes ────────────────────────────────
# Free photos shipped in assets/landscapes (credits: THIRD_PARTY_LICENSES.txt).
# The colours come from the picture like a picture theme, with a hand-picked accent.
LANDSCAPES = [
    # name, file, accent, description
    ("Mountain Lake", "mountain_lake.jpg", "#4fa3e0", "A calm lake under snowy peaks"),
    ("Fern Forest", "fern_forest.jpg", "#5cc27a", "Deep green ferns in the shade"),
    ("Ocean Shore", "ocean_shore.jpg", "#33b5d6", "Waves rolling onto the sand, from above"),
    ("Pier at Dusk", "pier_dusk.jpg", "#f08a5d", "A wooden pier on a still lake at sunset"),
    ("Desert Night", "desert_night.jpg", "#e2b26f", "Sand dunes under the first stars"),
]
LANDSCAPE_NAMES = [n for n, *_r in LANDSCAPES]


def landscape_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "landscapes")


def _register_landscapes() -> None:
    for name, fn, acc, desc in LANDSCAPES:
        path = os.path.join(landscape_dir(), fn)
        if not os.path.exists(path):
            continue
        try:
            toks, light = _image_tokens(path, acc)
        except Exception:
            continue
        BUILTIN[name] = toks
        EXTRAS[name] = dict(desc=desc, accent2=_shade(toks["accent"], 1.25),
                            panel=0.86 if light else 0.80,
                            backdrop=dict(kind="image", path=path, stamp=1, light=bool(light)))


try:
    _register_landscapes()
except Exception:
    pass

try:
    _register_image_themes()
except Exception:
    pass
