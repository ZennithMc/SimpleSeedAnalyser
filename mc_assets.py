"""
mc_assets.py — (06/11) the game's own look for SSA's loot: item icons exactly as in
the inventory, the item names, the pixel font and the chest window, read from the
user's OWN Minecraft installation (client jars of the official launcher, Modrinth,
Prism / MultiMC, CurseForge, or a folder chosen in Settings).

Nothing of the game is shipped with SSA: Minecraft's textures are Mojang's and may
not be redistributed. They are read at run time from the player's copy of the game.

    a = assets_for("1.21.4")          # the closest installed version (None: no jar)
    img = a.icon("diamond_sword", 64, glint=True)       # QImage, like the inventory
    a.name("diamond_sword")                              # "Diamond Sword"

Item models: the 1.21.4+ item definitions (assets/minecraft/items/*.json: model,
select, condition, range_dispatch, composite, special, tints) and the older
models/item/*.json (parents, layers, overrides ignored). Flat items (item/generated)
are their layers; block items are drawn in 3D from their elements with the GUI
transform of the model (rotation 30°, 225°, scale 0.625 for a block) and the
game's face shading (top 1.0, north / south 0.8, east / west 0.6).
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
import zipfile

_WIN = sys.platform == "win32"
_MAC = sys.platform == "darwin"
_HOME = os.path.expanduser("~")

# ── where the game is ─────────────────────────────────────────────────────────
_VER_RE = re.compile(r"(?:^|[^\d.])(\d+\.\d+(?:\.\d+)?)(?=$|[^\d.])")
_BAD = re.compile(r"snapshot|pre|rc\d|-rc|w\d\d[a-z]|experimental", re.I)


def vkey(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def _roots() -> list:
    out = []
    try:
        from PySide6.QtCore import QSettings
        p = QSettings("SSA", "SimpleSeedAnalyser").value("assets/minecraft_dir", "")
        if p:
            out.append(str(p))
    except Exception:
        pass
    env = os.environ.get("SSA_MC_JARS")
    if env:
        out += env.split(os.pathsep)
    if _WIN:
        ap = os.environ.get("APPDATA") or os.path.join(_HOME, "AppData", "Roaming")
        out += [os.path.join(ap, "ModrinthApp", "meta", "versions"),
                os.path.join(ap, "com.modrinth.theseus", "meta", "versions"),
                os.path.join(ap, ".minecraft", "versions"),
                os.path.join(ap, "PrismLauncher", "libraries", "com", "mojang", "minecraft"),
                os.path.join(_HOME, "curseforge", "minecraft", "Install", "versions"),
                os.path.join(ap, ".tlauncher", "legacy", "Minecraft", "game", "versions")]
    elif _MAC:
        sup = os.path.join(_HOME, "Library", "Application Support")
        out += [os.path.join(sup, "minecraft", "versions"),
                os.path.join(sup, "ModrinthApp", "meta", "versions"),
                os.path.join(sup, "PrismLauncher", "libraries", "com", "mojang", "minecraft")]
    else:
        sh = os.environ.get("XDG_DATA_HOME") or os.path.join(_HOME, ".local", "share")
        out += [os.path.join(_HOME, ".minecraft", "versions"),
                os.path.join(sh, "ModrinthApp", "meta", "versions"),
                os.path.join(sh, "PrismLauncher", "libraries", "com", "mojang", "minecraft"),
                os.path.join(_HOME, ".var", "app", "com.modrinth.ModrinthApp", "data", "ModrinthApp",
                             "meta", "versions")]
    return out


_JARS = None
_JAR_LOCK = threading.Lock()


def _jar_ok(path: str) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            z.getinfo("assets/minecraft/textures/gui/container/generic_54.png")
        return True
    except Exception:
        return False


def find_jars(refresh: bool = False) -> dict:
    """{version: jar path} of every installed release with the game's assets"""
    global _JARS
    with _JAR_LOCK:
        if _JARS is not None and not refresh:
            return _JARS
        found = {}
        for root in _roots():
            if not root or not os.path.isdir(root):
                continue
            for d, _dirs, files in os.walk(root):
                if d[len(root):].count(os.sep) > 3:
                    _dirs[:] = []
                    continue
                for f in files:
                    if not f.endswith(".jar") or f.endswith(("-server.jar", "-sources.jar")):
                        continue
                    if _BAD.search(f) or _BAD.search(os.path.basename(d)):
                        continue
                    m = _VER_RE.search(os.path.basename(d)) or _VER_RE.search(f)
                    if not m:
                        continue
                    v = m.group(1)
                    p = os.path.join(d, f)
                    if v in found or os.path.getsize(p) < 5_000_000 or not _jar_ok(p):
                        continue
                    found[v] = p
        _JARS = dict(sorted(found.items(), key=lambda kv: vkey(kv[0])))
        return _JARS


def jar_for(version: str | None):
    """the installed jar closest to `version` (the same, else the nearest — newer
    on a tie); None when the game isn't found"""
    jars = find_jars()
    if not jars:
        return None
    if version in jars:
        return jars[version]
    if not version:
        return list(jars.values())[-1]
    k = vkey(version)

    def dist(v):
        a, b = vkey(v), k
        n = max(len(a), len(b))
        a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
        return (abs(a[0] - b[0]) * 10000 + abs(a[1] - b[1]) * 100 + abs(a[2] - b[2]) if n >= 3 else 0,
                0 if a >= b else 1)
    return jars[min(jars, key=dist)]


_ASSETS = {}


def assets_for(version: str | None):
    """the Assets of the jar closest to `version` (shared), or None"""
    p = jar_for(version)
    if p is None:
        return None
    a = _ASSETS.get(p)
    if a is None:
        a = _ASSETS[p] = Assets(p)
    return a


def newest():
    return assets_for(None)


# ── colours the game computes (not in the assets) ─────────────────────────────
DYE_DEFAULT = 0xA06540                 # undyed leather
MAP_MARKINGS = 0x46402E
FOLIAGE = 0x48B518
# potion effect colours (MobEffects): (≤ 1.19.3, 1.19.4+) — see potion_color
_EFFECT_COL = {
    "regeneration": (0xCD5CAB, 0xCD5CAB), "strength": (0x932423, 0xFFC700),
    "instant_health": (0xF82423, 0xF82423), "jump_boost": (0x22FF4C, 0xFDFF84),
    "night_vision": (0x1F1FA1, 0xC2FF66), "speed": (0x7CAFC6, 0x33EBFF),
    "water_breathing": (0x2E5299, 0x98DAC0), "poison": (0x4E9331, 0x87A363),
    "slowness": (0x5A6C81, 0x8BAFE0), "fire_resistance": (0xE49A3A, 0xFF9900),
    "invisibility": (0x7F8392, 0xF6F6F6), "weakness": (0x484D48, 0x484D48),
}
_POTION_EFFECT = {"regeneration": "regeneration", "strong_regeneration": "regeneration",
                  "long_regeneration": "regeneration", "strength": "strength",
                  "strong_strength": "strength", "healing": "instant_health",
                  "strong_healing": "instant_health", "leaping": "jump_boost",
                  "night_vision": "night_vision", "swiftness": "speed",
                  "water_breathing": "water_breathing", "poison": "poison",
                  "strong_poison": "poison", "slowness": "slowness",
                  "strong_slowness": "slowness", "fire_resistance": "fire_resistance",
                  "invisibility": "invisibility", "weakness": "weakness"}
NEW_EFFECT_COLOURS = (1, 19, 4)        # MobEffects colours changed in 1.19.4 (checked in the jars)


def potion_color(potion: str | None, version: str | None) -> int:
    eff = _POTION_EFFECT.get((potion or "").replace("minecraft:", ""))
    if eff is None:
        return 0x385DC6                # water bottle (-13083194)
    old, new = _EFFECT_COL[eff]
    return new if version and vkey(version) >= NEW_EFFECT_COLOURS else old


# rarity colours of the names (Rarity): uncommon yellow, rare aqua, epic magenta
RARITY = {"enchanted_golden_apple": "epic", "golden_apple": "rare", "enchanted_book": "uncommon",
          "experience_bottle": "uncommon", "heart_of_the_sea": "uncommon", "trident": "rare",
          "heavy_core": "epic", "netherite_upgrade_smithing_template": "uncommon",
          "trial_key": "common", "ominous_bottle": "uncommon", "totem_of_undying": "uncommon",
          "music_disc_": "rare", "_armor_trim_smithing_template": "uncommon"}
RARITY_COL = {"common": 0xFFFFFF, "uncommon": 0xFFFF55, "rare": 0x55FFFF, "epic": 0xFF55FF}


def rarity(item: str, enchanted: bool = False) -> str:
    r = RARITY.get(item)
    if r is None:
        for k, v in RARITY.items():
            if (k.endswith("_") and item.startswith(k)) or (k.startswith("_") and item.endswith(k)):
                r = v
                break
    r = r or "common"
    if enchanted:
        r = {"common": "rare", "uncommon": "rare", "rare": "epic"}.get(r, r)
    return r


# items whose texture is drawn by code in the game (special models)
SPECIAL = {"shield"}
# 26.3 explorer maps are their own items, older versions a map + name
MAP_ITEMS = ("_map",)


class Assets:
    """One client jar: textures, models, names, font, GUI."""

    def __init__(self, path: str):
        self.path = path
        m = _VER_RE.search(os.path.basename(os.path.dirname(path))) or _VER_RE.search(os.path.basename(path))
        self.version = m.group(1) if m else "?"
        self._z = zipfile.ZipFile(path)
        self._names = set(self._z.namelist())
        self._lock = threading.RLock()
        self._json = {}
        self._img = {}
        self._icons = {}
        self._lang = None
        self._font = None

    # ── raw files ─────────────────────────────────────────────────────────────
    def has(self, p):
        return p in self._names

    def read(self, p):
        with self._lock:
            return self._z.read(p)

    def json(self, p):
        if p not in self._json:
            try:
                self._json[p] = json.loads(self.read(p).decode("utf-8"))
            except Exception:
                self._json[p] = None
        return self._json[p]

    def texture(self, ref: str):
        """QImage (ARGB32) of a texture id (first frame of an animation), or None"""
        from PySide6.QtGui import QImage
        ref = ref.replace("minecraft:", "")
        if ref in self._img:
            return self._img[ref]
        p = f"assets/minecraft/textures/{ref}.png"
        img = None
        if self.has(p):
            img = QImage.fromData(self.read(p))
            if not img.isNull():
                img = img.convertToFormat(QImage.Format.Format_ARGB32)
                w, h = img.width(), img.height()
                if h > w and h % w == 0:                     # animation strip: frame 0
                    f0 = 0
                    meta = self.json(p + ".mcmeta") if self.has(p + ".mcmeta") else None
                    try:
                        fr = (meta or {}).get("animation", {}).get("frames")
                        if fr:
                            f0 = fr[0] if isinstance(fr[0], int) else int(fr[0].get("index", 0))
                    except Exception:
                        f0 = 0
                    img = img.copy(0, f0 * w, w, w)
            else:
                img = None
        self._img[ref] = img
        return img

    # ── names ─────────────────────────────────────────────────────────────────
    def lang(self) -> dict:
        if self._lang is None:
            self._lang = self.json("assets/minecraft/lang/en_us.json") or {}
        return self._lang

    def name(self, item: str, key: str | None = None) -> str:
        L = self.lang()
        if key and key in L:
            return L[key]
        item = item.replace("minecraft:", "")
        for k in (f"item.minecraft.{item}", f"block.minecraft.{item}"):
            if k in L:
                return L[k]
        return item.replace("_", " ").title()

    def text(self, key: str, default: str = "") -> str:
        return self.lang().get(key, default)

    # ── models ────────────────────────────────────────────────────────────────
    def _model(self, ref: str):
        """a model json merged with its parents: {textures, elements, display, gen,
        builtin}"""
        ref = ref.replace("minecraft:", "")
        if "/" not in ref:
            ref = "item/" + ref
        tex, disp, elements, chain = {}, {}, None, []
        gen = builtin = None
        cur = ref
        for _ in range(16):
            if cur in ("builtin/generated", "item/generated"):
                gen = True
                break
            if cur == "builtin/entity":
                builtin = True
                break
            j = self.json(f"assets/minecraft/models/{cur}.json")
            if j is None:
                break
            chain.append(cur)
            for k, v in (j.get("textures") or {}).items():
                tex.setdefault(k, v)
            for k, v in (j.get("display") or {}).items():
                disp.setdefault(k, v)
            if elements is None and j.get("elements") is not None:
                elements = j["elements"]
            par = j.get("parent")
            if not par:
                break
            cur = par.replace("minecraft:", "")
        return {"textures": tex, "display": disp, "elements": elements,
                "gen": bool(gen) and elements is None, "builtin": bool(builtin), "ref": ref}

    @staticmethod
    def _resolve(tex: dict, v: str):
        for _ in range(8):
            if v is None:
                return None
            if v.startswith("#"):
                v = tex.get(v[1:])
            elif "/" not in v and v in tex:      # "texture": "all" (heavy core) = #all
                v = tex.get(v)
            else:
                return v
        return None

    def _item_def(self, item: str):
        """1.21.4+: the item definition → [(model ref, tints, transformation)] or
        ("special", kind); None: older format"""
        j = self.json(f"assets/minecraft/items/{item}.json")
        if j is None:
            return None
        return self._pick(j.get("model") or {})

    def _pick(self, m):
        t = str(m.get("type", "")).replace("minecraft:", "")
        if t == "model":
            return [(m.get("model", ""), m.get("tints") or [], None)]
        if t == "composite":
            out = []
            for sub in m.get("models") or []:
                r = self._pick(sub)
                if isinstance(r, list):
                    for ref, tints, _tr in r:
                        out.append((ref, tints, sub.get("transformation")))
            return out
        if t == "select":
            prop = str(m.get("property", "")).replace("minecraft:", "")
            if prop == "display_context":
                for c in m.get("cases") or []:
                    w = c.get("when")
                    w = w if isinstance(w, list) else [w]
                    if "gui" in w:
                        return self._pick(c.get("model") or {})
            if m.get("fallback"):
                return self._pick(m["fallback"])
            cs = m.get("cases") or []
            return self._pick(cs[-1].get("model") or {}) if cs else []
        if t == "condition":
            return self._pick(m.get("on_false") or m.get("on_true") or {})
        if t == "range_dispatch":
            es = m.get("entries") or []
            if es:
                return self._pick(es[0].get("model") or {})
            return self._pick(m.get("fallback") or {})
        if t == "special":
            kind = str((m.get("model") or {}).get("type", "")).replace("minecraft:", "")
            return ("special", kind, m.get("base"))
        return []

    # ── icons ─────────────────────────────────────────────────────────────────
    def icon(self, item: str, size: int = 64, glint: bool = False, potion: str | None = None,
             version: str | None = None):
        """QImage size×size of the item as in an inventory slot (None if unknown)"""
        item = item.replace("minecraft:", "")
        key = (item, size, glint, potion, version)
        if key in self._icons:
            return self._icons[key]
        try:
            img = self._render(item, size, potion, version or self.version)
        except Exception:                                    # noqa: BLE001
            img = None
        if img is not None and glint:
            img = self._glint(img)
        self._icons[key] = img
        return img

    def _tint_value(self, t, item, potion, version):
        tt = str(t.get("type", "")).replace("minecraft:", "") if isinstance(t, dict) else ""
        if tt == "constant":
            return int(t.get("value", -1)) & 0xFFFFFF
        if tt == "potion":
            return potion_color(potion, version) if potion else int(t.get("default", -13083194)) & 0xFFFFFF
        if tt in ("dye", "firework", "map_color", "team", "custom_model_data"):
            return int(t.get("default", -1)) & 0xFFFFFF
        if tt == "grass":
            return self._grass(float(t.get("temperature", 0.5)), float(t.get("downfall", 1.0)))
        if tt == "foliage":
            return FOLIAGE
        return 0xFFFFFF

    def _old_tints(self, item, potion, version):
        """tint per tintindex for the pre-1.21.4 item colours (ItemColors)"""
        if item in ("potion", "splash_potion", "lingering_potion", "tipped_arrow"):
            return [potion_color(potion, version)]
        if item.startswith("leather_") and item != "leather":
            return [DYE_DEFAULT]
        if item.endswith("_map") or item == "filled_map":
            return [0xFFFFFF, MAP_MARKINGS]
        if item in ("grass", "short_grass", "fern", "tall_grass", "large_fern"):
            g = self._grass(0.5, 1.0)
            return [g]
        if item in ("vine",) or item.endswith("_leaves"):
            return [FOLIAGE]
        return []

    def _grass(self, temp, down):
        img = self.texture("colormap/grass")
        if img is None:
            return 0x7CBD6B
        temp = max(0.0, min(1.0, temp))
        down = max(0.0, min(1.0, down)) * temp
        i, j = int((1 - temp) * 255), int((1 - down) * 255)
        return img.pixel(min(i, img.width() - 1), min(j, img.height() - 1)) & 0xFFFFFF

    def _render(self, item, size, potion, version):
        if item in SPECIAL:
            return self._special(item, size)
        d = self._item_def(item)
        if d is None and not self.has(f"assets/minecraft/models/item/{item}.json"):
            if any(item.endswith(s) for s in MAP_ITEMS) and item != "map":
                return self._render("filled_map", size, potion, version)
            if item == "grass":
                return self._render("short_grass", size, potion, version)
            if item == "short_grass":
                d = None
                if self.has("assets/minecraft/models/item/grass.json"):
                    return self._render("grass", size, potion, version)
            if item == "iron_chain":
                return self._render("chain", size, potion, version)
            if item == "chain":
                return self._render("iron_chain", size, potion, version) \
                    if self.has("assets/minecraft/items/iron_chain.json") else None
            if d is None:
                return None
        if isinstance(d, tuple) and d[0] == "special":
            return self._special(item, size, d[1], d[2])
        if d is None:
            parts = [(f"item/{item}", None, None)]
            tints = self._old_tints(item, potion, version)
        else:
            parts = [(ref, [self._tint_value(t, item, potion, version) for t in tints], tr)
                     for ref, tints, tr in d]
            tints = None
        if not parts:
            return None
        if len(parts) == 1:
            ref, tl, _tr = parts[0]
            m = self._model(ref)
            tl = tints if tl is None else tl
            if m["gen"]:
                return self._flat(m, size, tl)
            if m["elements"]:
                return self._block(m, size, tl)
            return None
        # composite (26.3 beds): every model in its transformation, the first one's display
        ms = [(self._model(ref), tl or [], tr) for ref, tl, tr in parts]
        if all(m["elements"] for m, _t, _tr in ms):
            return self._block(ms[0][0], size, ms[0][1], extra=ms)
        for m, tl, _tr in ms:
            if m["gen"]:
                return self._flat(m, size, tl)
        return None

    # flat items: their layers, tinted, 16×16 scaled to size (nearest)
    def _flat(self, m, size, tints):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage, QPainter
        tex = m["textures"]
        layers = []
        i = 0
        while f"layer{i}" in tex:
            layers.append(self._resolve(tex, tex[f"layer{i}"]))
            i += 1
        if not layers:
            return None
        base = None
        for i, ref in enumerate(layers):
            img = self.texture(ref) if ref else None
            if img is None:
                continue
            if base is None:
                base = QImage(img.width(), img.height(), QImage.Format.Format_ARGB32_Premultiplied)
                base.fill(Qt.GlobalColor.transparent)
            if i < len(tints or []) and (tints[i] & 0xFFFFFF) != 0xFFFFFF:
                img = _tinted(img, tints[i])
            p = QPainter(base)
            p.drawImage(0, 0, img.scaled(base.width(), base.height()))
            p.end()
        if base is None:
            return None
        return base.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.FastTransformation)

    # block items: the model's elements in the GUI transform
    def _block(self, m, size, tints, extra=None):
        from PySide6.QtCore import QPointF, QRectF, Qt
        from PySide6.QtGui import QImage, QPainter, QPolygonF, QTransform
        disp = (m["display"] or {}).get("gui") or {"rotation": [30, 225, 0], "translation": [0, 0, 0],
                                                    "scale": [0.625, 0.625, 0.625]}
        rx, ry, rz = [math.radians(a) for a in disp.get("rotation", [0, 0, 0])]
        tx, ty, tz = [a / 16.0 for a in disp.get("translation", [0, 0, 0])]
        sx, sy, sz = disp.get("scale", [1, 1, 1])
        R = _mat_xyz(rx, ry, rz)
        faces = []
        groups = extra or [(m, tints, None)]
        for mm, tl, tr in groups:
            texmap = mm["textures"]
            for el in mm["elements"] or []:
                f0, t0 = el.get("from", [0, 0, 0]), el.get("to", [16, 16, 16])
                rot = el.get("rotation")
                for dname, fc in (el.get("faces") or {}).items():
                    tref = self._resolve(texmap, fc.get("texture"))
                    img = self.texture(tref) if tref else None
                    if img is None:
                        continue
                    ti = fc.get("tintindex")
                    if ti is not None and tl and ti < len(tl) and (tl[ti] & 0xFFFFFF) != 0xFFFFFF:
                        img = _tinted(img, tl[ti])
                    corners = _face_corners(dname, f0, t0)
                    if corners is None:
                        continue
                    pts = []
                    for c in corners:
                        v = [c[0] / 16.0, c[1] / 16.0, c[2] / 16.0]
                        if rot:
                            v = _el_rot(v, rot)
                        if tr:
                            v = _composite_tr(v, tr)
                        v = [(v[0] - 0.5) * sx, (v[1] - 0.5) * sy, (v[2] - 0.5) * sz]
                        v = _mul(R, v)
                        pts.append((v[0] + tx, v[1] + ty, v[2] + tz))
                    n = _normal(pts)
                    if n[2] <= 1e-6:
                        continue
                    shade = {"up": 1.0, "down": 0.5, "north": 0.8, "south": 0.8,
                             "east": 0.6, "west": 0.6}.get(dname, 1.0)
                    if el.get("shade") is False:
                        shade = 1.0
                    uv = fc.get("uv") or _default_uv(dname, f0, t0)
                    depth = sum(p[2] for p in pts) / 4.0
                    faces.append((depth, img, uv, int(fc.get("rotation", 0)), pts, shade))
        if not faces:
            return None
        faces.sort(key=lambda f: f[0])
        out = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        k = size                                  # 1 block = 16 GUI px = size px
        for _d, img, uv, urot, pts, shade in faces:
            W, H = img.width(), img.height()
            u1, v1, u2, v2 = uv[0] * W / 16.0, uv[1] * H / 16.0, uv[2] * W / 16.0, uv[3] * H / 16.0
            src = [(u1, v1), (u2, v1), (u2, v2), (u1, v2)]
            r = (urot // 90) % 4
            src = src[r:] + src[:r]
            dst = [QPointF(size / 2 + q[0] * k, size / 2 - q[1] * k) for q in pts]
            sp = QPolygonF([QPointF(a, b) for a, b in src])
            t = QTransform()
            if not QTransform.quadToQuad(sp, QPolygonF(dst), t):
                continue
            tex = _shaded(img, shade)
            p.save()
            p.setTransform(t)
            x0, x1 = sorted((u1, u2))
            y0, y1 = sorted((v1, v2))
            if x1 - x0 < 1e-6 or y1 - y0 < 1e-6:
                p.restore()
                continue
            p.drawImage(QRectF(x0, y0, x1 - x0, y1 - y0), tex, QRectF(x0, y0, x1 - x0, y1 - y0))
            p.restore()
        p.end()
        return out

    def _special(self, item, size, kind=None, base=None):
        """shield: its front from the entity texture"""
        from PySide6.QtCore import QRect, Qt
        from PySide6.QtGui import QImage, QPainter
        if item == "shield" or kind == "shield":
            img = None
            for ref in ("entity/shield/shield_base_nopattern", "entity/shield_base_nopattern"):
                img = self.texture(ref)
                if img is not None:
                    break
            if img is None:
                return None
            s = img.width() / 64.0
            face = img.copy(QRect(int(1 * s), int(1 * s), int(12 * s), int(22 * s)))
            out = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
            out.fill(Qt.GlobalColor.transparent)
            p = QPainter(out)
            h = size * 0.92
            w = h * 12 / 22
            p.drawImage(QRectF_((size - w) / 2, (size - h) / 2, w, h), face)
            p.end()
            return out
        return None

    def chest_icon(self, size, variant="normal"):
        """a chest as in the inventory (its item is drawn by code): the chest
        model's boxes with the entity texture, in the block GUI transform"""
        key = ("~chest", variant, size)
        if key in self._icons:
            return self._icons[key]
        ref = None
        for r in (f"entity/chest/{variant}", f"entity/chest/{variant}_chest", "entity/chest/normal"):
            if self.texture(r) is not None:
                ref = r
                break
        img = None
        if ref is not None:
            def box(u, v, w, h, d):          # ModelPart cube UV (texture px → model uv /4)
                q = lambda x0, y0, x1, y1: [x0 / 4, y0 / 4, x1 / 4, y1 / 4]
                # entity models are upside down: the "down" square is the top
                return {"down": {"uv": q(u + d, v, u + d + w, v + d), "texture": "#t"},
                        "up": {"uv": q(u + d + w, v, u + d + 2 * w, v + d), "texture": "#t"},
                        "north": {"uv": q(u + d, v + d, u + d + w, v + d + h), "texture": "#t"},
                        "east": {"uv": q(u, v + d, u + d, v + d + h), "texture": "#t"},
                        "south": {"uv": q(u + 2 * d + w, v + d, u + 2 * d + 2 * w, v + d + h), "texture": "#t"},
                        "west": {"uv": q(u + d + w, v + d, u + 2 * d + w, v + d + h), "texture": "#t"}}
            m = {"textures": {"t": ref}, "display": {}, "gen": False, "builtin": False, "ref": "chest",
                 "elements": [{"from": [1, 0, 1], "to": [15, 10, 15], "faces": box(0, 19, 14, 10, 14)},
                              {"from": [1, 9, 1], "to": [15, 14, 15], "faces": box(0, 0, 14, 5, 14)},
                              {"from": [7, 7, 0], "to": [9, 11, 1], "faces": box(0, 0, 2, 4, 1)}]}
            try:
                img = self._block(m, size, [])
            except Exception:                                # noqa: BLE001
                img = None
        self._icons[key] = img
        return img

    def _glint(self, img):
        """the enchantment glint: the game adds its purple texture (scaled up,
        turned 10°) to the item's pixels — a still frame of it"""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QImage, QPainter, QTransform
        g = self.texture("misc/enchanted_glint_item") or self.texture("misc/enchanted_item_glint")
        n = img.width()
        lay = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
        lay.fill(Qt.GlobalColor.transparent)
        q = QPainter(lay)
        if g is not None:
            k = 4.0 * n / g.width()              # the item covers a quarter of the glint
            t = QTransform()
            t.translate(n * 0.5, n * 0.5)
            t.rotate(-10)
            t.scale(k, k)
            t.translate(-g.width() * 0.37, -g.height() * 0.55)
            q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            q.setTransform(t)
            q.drawImage(0, 0, g)
            q.setTransform(QTransform())
        else:
            q.fillRect(lay.rect(), Qt.GlobalColor.magenta)
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        q.drawImage(0, 0, img)
        q.end()
        res = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        p = QPainter(res)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        p.setOpacity(0.7)
        p.drawImage(0, 0, lay)
        p.end()
        return res

    # ── the font (assets/minecraft/textures/font/ascii.png) ───────────────────
    def font(self):
        if self._font is None:
            self._font = PixelFont(self)
        return self._font

    def gui(self, ref):
        """a GUI texture: container/generic_54, sprites/container/slot…"""
        return self.texture(f"gui/{ref}")


def QRectF_(x, y, w, h):
    from PySide6.QtCore import QRectF
    return QRectF(x, y, w, h)


# ── geometry ───────────────────────────────────────────────────────────────────
def _mat_xyz(ax, ay, az):
    """JOML rotationXYZ: v' = Rx · Ry · Rz · v"""
    cx, sx, cy, sy, cz, sz = math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay), math.cos(az), math.sin(az)
    Rx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    Ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    Rz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    return _mm(_mm(Rx, Ry), Rz)


def _mm(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _mul(M, v):
    return [M[0][0] * v[0] + M[0][1] * v[1] + M[0][2] * v[2],
            M[1][0] * v[0] + M[1][1] * v[1] + M[1][2] * v[2],
            M[2][0] * v[0] + M[2][1] * v[1] + M[2][2] * v[2]]


def _normal(p):
    """outward normal: corner 1 - 0 is +u, corner 3 - 0 is +v (down the texture)"""
    a = (p[1][0] - p[0][0], p[1][1] - p[0][1], p[1][2] - p[0][2])
    b = (p[3][0] - p[0][0], p[3][1] - p[0][1], p[3][2] - p[0][2])
    return _cross(b, a)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _face_corners(d, f, t):
    """the 4 corners (block units ×16) of a face, matching the uv corners
    (u1, v1), (u2, v1), (u2, v2), (u1, v2) — texture seen from outside"""
    x0, y0, z0 = f
    x1, y1, z1 = t
    return {
        "up": [(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)],
        "down": [(x0, y0, z1), (x1, y0, z1), (x1, y0, z0), (x0, y0, z0)],
        "north": [(x1, y1, z0), (x0, y1, z0), (x0, y0, z0), (x1, y0, z0)],
        "south": [(x0, y1, z1), (x1, y1, z1), (x1, y0, z1), (x0, y0, z1)],
        "west": [(x0, y1, z0), (x0, y1, z1), (x0, y0, z1), (x0, y0, z0)],
        "east": [(x1, y1, z1), (x1, y1, z0), (x1, y0, z0), (x1, y0, z1)],
    }.get(d)


def _default_uv(d, f, t):
    x0, y0, z0 = f
    x1, y1, z1 = t
    return {"up": [x0, z0, x1, z1], "down": [x0, 16 - z1, x1, 16 - z0],
            "north": [16 - x1, 16 - y1, 16 - x0, 16 - y0], "south": [x0, 16 - y1, x1, 16 - y0],
            "west": [z0, 16 - y1, z1, 16 - y0], "east": [16 - z1, 16 - y1, 16 - z0, 16 - y0]}.get(d, [0, 0, 16, 16])


def _el_rot(v, rot):
    o = [c / 16.0 for c in rot.get("origin", [8, 8, 8])]
    a = math.radians(float(rot.get("angle", 0)))
    ax = rot.get("axis", "y")
    x, y, z = v[0] - o[0], v[1] - o[1], v[2] - o[2]
    c, s = math.cos(a), math.sin(a)
    if ax == "x":
        y, z = y * c - z * s, y * s + z * c
    elif ax == "y":
        x, z = x * c + z * s, -x * s + z * c
    else:
        x, y = x * c - y * s, x * s + y * c
    return [x + o[0], y + o[1], z + o[2]]


def _composite_tr(v, tr):
    """a composite item model's transformation (translation, rotations, scale)"""
    def qrot(q, p):
        x, y, z, w = q
        # v' = q v q*
        ix = w * p[0] + y * p[2] - z * p[1]
        iy = w * p[1] + z * p[0] - x * p[2]
        iz = w * p[2] + x * p[1] - y * p[0]
        iw = -x * p[0] - y * p[1] - z * p[2]
        return [ix * w + iw * -x + iy * -z - iz * -y,
                iy * w + iw * -y + iz * -x - ix * -z,
                iz * w + iw * -z + ix * -y - iy * -x]
    if isinstance(tr, dict):
        s = tr.get("scale", [1, 1, 1])
        v = [v[0] * s[0], v[1] * s[1], v[2] * s[2]]
        if tr.get("right_rotation"):
            v = qrot(tr["right_rotation"], v)
        if tr.get("left_rotation"):
            v = qrot(tr["left_rotation"], v)
        t = tr.get("translation", [0, 0, 0])
        v = [v[0] + t[0], v[1] + t[1], v[2] + t[2]]
    return v


# ── pixels ─────────────────────────────────────────────────────────────────────
def _tinted(img, rgb):
    """multiply by a colour (the game's tint), alpha kept"""
    from PySide6.QtGui import QColor, QImage, QPainter
    out = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    p = QPainter(out)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
    p.fillRect(out.rect(), QColor((rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255))
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    p.drawImage(0, 0, img)
    p.end()
    return out


_SHADE = {}


def _shaded(img, shade):
    if shade >= 0.999:
        return img
    k = (img.cacheKey(), round(shade, 3))
    s = _SHADE.get(k)
    if s is None:
        from PySide6.QtGui import QColor, QImage, QPainter
        s = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        p = QPainter(s)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
        p.fillRect(s.rect(), QColor(0, 0, 0, int(round((1 - shade) * 255))))
        p.end()
        if len(_SHADE) > 2000:
            _SHADE.clear()
        _SHADE[k] = s
    return s


class PixelFont:
    """The game's pixel font (ascii.png: 16 × 16 glyphs, width = last column used + 1)."""

    def __init__(self, a: Assets):
        self.img = a.texture("font/ascii")
        self.w = {}
        if self.img is None:
            return
        g = self.img.width() // 16
        self.g = g
        for code in range(256):
            cx, cy = (code % 16) * g, (code // 16) * g
            last = -1
            for x in range(g):
                for y in range(g):
                    if (self.img.pixel(cx + x, cy + y) >> 24) & 255:
                        last = x
                        break
            self.w[code] = (last + 2) if last >= 0 else 0
        self.w[32] = 4 * g // 8
        self._glyphs = {}

    @property
    def ok(self):
        return self.img is not None

    def width(self, text: str, scale: float = 1) -> float:
        g = getattr(self, "g", 8)
        return sum(self.w.get(ord(c), 6) if ord(c) < 256 else 6 for c in text) * scale * 8 / g

    def draw(self, p, x, y, text, rgb=0xFFFFFF, scale=2, shadow=True):
        """draw at (x, y) = top-left of the 8-px line, `scale` screen px per font px"""
        if not self.ok:
            return
        for dx, col in ((1, _shadow(rgb)), (0, rgb)) if shadow else ((0, rgb),):
            cx = x + dx * scale
            for c in text:
                code = ord(c)
                if code >= 256:
                    code = ord("?")
                gl = self._glyph(code, col)
                if gl is not None:
                    from PySide6.QtCore import QRectF
                    p.drawImage(QRectF(cx, y + dx * scale, 8 * scale, 8 * scale), gl)
                cx += self.w.get(code, 6) * scale * 8 / self.g

    def _glyph(self, code, rgb):
        k = (code, rgb)
        if k not in self._glyphs:
            g = self.g
            cell = self.img.copy((code % 16) * g, (code // 16) * g, g, g)
            self._glyphs[k] = _tinted(cell, rgb)
        return self._glyphs[k]


def _shadow(rgb):
    return ((rgb >> 16 & 255) // 4) << 16 | ((rgb >> 8 & 255) // 4) << 8 | (rgb & 255) // 4
