"""
mc_checker.py — the exact « broken end portal » check, by the real Minecraft
1.16.1 world generation (jellejurre's seed-checker), run as warm worker
processes (02/10).

Why: a ruined portal over a stronghold breaks end-portal frames through the
netherrack / lava it spreads over chunks that are ALREADY generated, so the
answer depends on Minecraft's own terrain, structure pieces and decoration
randomness. SSA's fast search only finds geometric candidates (about 1 in 10
is really broken); this module confirms each one exactly.

Compared with the old checker (checker_client.py: Gradle « test » task + files)
  * no Gradle: SSA compiles tools/checker/SsaChecker.java once with the JDK,
    against the Minecraft jar the seed-checker project already downloaded in
    ~/.gradle/caches — start-up a few seconds instead of ~30 s;
  * several workers side by side (stdin / stdout pipes, no shared files);
  * only the « direct /tp onto the portal » world is built (frame chunks
    first, then the ruined portal's chunks): about half the time per seed.

API
  status()                 -> (ok, message)
  pool()                   -> CheckerPool (started on first use, kept warm)
  CheckerPool.submit(seed, frames, rox, roz, callback, natural=False)
        callback(result | None, error | None); result = dict(forced=int,
        natural=int (-1 if not asked), broken=[(x, y, z, block)], ms=int)
  verify_many([(seed, frames, rox, roz)], timeout) -> {seed: result}
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "tools", "checker")
_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ── locating the JDK, the Minecraft jars and the seed-checker sources ─────────
def _jdk_bin(name):
    try:
        import checker_client as cc
        jh = cc.find_java_home()
    except Exception:
        jh = None
    exe = name + (".exe" if os.name == "nt" else "")
    if jh and os.path.isfile(os.path.join(jh, "bin", exe)):
        return os.path.join(jh, "bin", exe)
    return shutil.which(name)


def _gradle_caches():
    roots = []
    if os.environ.get("GRADLE_USER_HOME"):
        roots.append(os.path.join(os.environ["GRADLE_USER_HOME"], "caches"))
    roots.append(os.path.join(os.path.expanduser("~"), ".gradle", "caches"))
    return next((r for r in roots if os.path.isdir(r)), None)


_MISSING = []                                 # libraries Gradle no longer has (03/10)


def _lib_dir():
    return os.path.join(_classes_dir(), "lib")


def _private_jars():
    """SSA's own copy of the jars (made at the first successful compile): Gradle
    deletes cache files unused for 30 days, which broke the checker (03/10)."""
    man = os.path.join(_lib_dir(), "jars.txt")
    if not os.path.isfile(man):
        return None
    try:
        names = [n.strip() for n in open(man, encoding="utf-8") if n.strip()]
    except OSError:
        return None
    jars = [os.path.join(_lib_dir(), n) for n in names]
    return jars if jars and all(os.path.isfile(j) for j in jars) else None


def _keep_private_copy(jars):
    """Copies the jars next to the compiled checker; returns the copies."""
    d = _lib_dir()
    os.makedirs(d, exist_ok=True)
    out = []
    for i, j in enumerate(jars):
        name = f"{i:02d}_{os.path.basename(j)}"
        dst = os.path.join(d, name)
        if not os.path.isfile(dst) or os.path.getsize(dst) != os.path.getsize(j):
            shutil.copy2(j, dst)
        out.append(dst)
    with open(os.path.join(d, "jars.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(os.path.basename(o) for o in out))
    return out


def _minecraft_jars():
    """[mapped Minecraft 1.16.1 jar, its libraries…]: SSA's own copy, else the
    Gradle cache the seed-checker project filled when it was built, or None
    (then _MISSING lists the libraries Gradle has removed since)."""
    own = _private_jars()
    if own:
        return own
    del _MISSING[:]
    caches = _gradle_caches()
    if not caches:
        return None
    mapped = sorted(glob.glob(os.path.join(
        caches, "fabric-loom", "1.16.1-mapped-*", "minecraft-1.16.1-mapped-*.jar")))
    info = os.path.join(caches, "fabric-loom", "minecraft-1.16.1-info.json")
    if not mapped or not os.path.isfile(info):
        return None
    jars = [mapped[-1]]
    try:
        libs = json.load(open(info, encoding="utf-8")).get("libraries", [])
    except Exception:
        return None
    base = os.path.join(caches, "modules-2", "files-2.1")
    seen = set()
    for lib in libs:
        parts = str(lib.get("name", "")).split(":")
        if len(parts) != 3 or parts[1].startswith("lwjgl") or parts[0] == "org.lwjgl":
            continue                          # natives / client-only
        g, a, v = parts
        hit = glob.glob(os.path.join(base, g, a, v, "*", f"{a}-{v}.jar"))
        if not hit:
            _MISSING.append(f"{a}-{v}")
            continue
        if hit[0] not in seen:
            seen.add(hit[0])
            jars.append(hit[0])
    return jars                      # (some may be missing: see _compile)


def _seedchecker_src():
    """src/main/java of the seed-checker 1.16.1 project: the folder set in the
    checker settings, else « Sources to relie on » next to SSA or in one of its
    parent folders (the SSA folder is often nested a few levels deep)."""
    cands = []
    try:
        import checker_client as cc
        cands.append(cc.checker_dir())
    except Exception:
        pass
    d = _HERE
    for _ in range(5):
        cands.append(os.path.join(d, "Sources to relie on", "seed-checker-1.16.1",
                                  "seed-checker-1.16.1"))
        d = os.path.dirname(d)
    for c in cands:
        src = os.path.join(c or "", "src", "main", "java")
        if c and "1.16" in c.replace("\\", "/") and os.path.isdir(
                os.path.join(src, "nl", "jellejurre", "seedchecker")):
            return src
    return None


def _classes_dir():
    import ssa_paths
    return os.path.join(ssa_paths.data_dir(), "mc_checker_116")


def _sources():
    out = []
    # (09/10) tools/checker/overrides: files compiled INSTEAD of the seed-checker's
    # file at the same path (SeedChunkGenerator: a faster region, opt-in)
    ov_root = os.path.join(_SRC, "overrides")
    ov = {}
    if os.path.isdir(ov_root):
        for dp, _dn, fn in os.walk(ov_root):
            for f in fn:
                if f.endswith(".java"):
                    full = os.path.join(dp, f)
                    ov[os.path.relpath(full, ov_root).replace("\\", "/")] = full
    sc_root = _seedchecker_src()
    for root in (sc_root, os.path.join(_SRC, "stubs")):
        if root:
            for dp, _dn, fn in os.walk(root):
                for f in fn:
                    if not f.endswith(".java"):
                        continue
                    full = os.path.join(dp, f)
                    rel = os.path.relpath(full, root).replace("\\", "/")
                    if root == sc_root and rel in ov:
                        continue
                    out.append(full)
    out += list(ov.values())
    out.append(os.path.join(_SRC, "SsaChecker.java"))
    if os.path.isfile(os.path.join(_SRC, "SpawnWorker.java")):     # 1.16 spawns (09/10)
        out.append(os.path.join(_SRC, "SpawnWorker.java"))
    return out


def status():
    """(ok, message) — what is missing, if anything."""
    if not _jdk_bin("java") or not _jdk_bin("javac"):
        return False, "a JDK 17 is needed (Temurin / Zulu / Microsoft…)"
    if not _seedchecker_src():
        return False, "the seed-checker 1.16.1 project was not found (Sources to relie on)"
    if not _minecraft_jars():
        return False, ("Minecraft 1.16.1 is not in the Gradle cache yet: build the "
                       "seed-checker project once (gradlew test)")
    return True, "Minecraft 1.16.1 checker ready"


def _compile():
    """Compile once (and again when a source or the jar set changes)."""
    ok, why = status()
    if not ok:
        raise RuntimeError(why)
    jars = _minecraft_jars()
    if _private_jars() is None:                 # first time: keep SSA's own copy
        jars = _keep_private_copy(jars)
    srcs = _sources()
    h = hashlib.sha1()
    for p in sorted(srcs) + sorted(jars):
        st = os.stat(p)
        h.update(f"{p}|{st.st_size}|{int(st.st_mtime)}".encode())
    out = _classes_dir()
    stamp = os.path.join(out, "stamp.txt")
    if os.path.isfile(stamp) and open(stamp).read().strip() == h.hexdigest():
        return out, jars
    cls = os.path.join(out, "classes")
    shutil.rmtree(cls, ignore_errors=True)
    os.makedirs(cls, exist_ok=True)
    listfile = os.path.join(out, "sources.txt")
    with open(listfile, "w", encoding="utf-8") as f:
        f.write("\n".join('"' + s.replace("\\", "/") + '"' for s in srcs))
    r = subprocess.run([_jdk_bin("javac"), "-nowarn", "-encoding", "UTF-8", "-d", cls,
                        "-cp", os.pathsep.join(jars), "@" + listfile],
                       capture_output=True, text=True, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        if _MISSING:                           # the usual cause: Gradle's cache cleanup
            shutil.rmtree(_lib_dir(), ignore_errors=True)
            raise RuntimeError(
                f"The Minecraft 1.16.1 checker cannot be built: Gradle has removed "
                f"{len(_MISSING)} of its libraries from its cache (Gradle deletes files "
                f"unused for 30 days: {', '.join(_MISSING[:4])}…).\n\nFix: open a terminal "
                f"in the seed-checker-1.16.1 folder and run « gradlew test » once (it "
                f"downloads them again). SSA then keeps its own copy, so this cannot "
                f"happen again.")
        raise RuntimeError("checker compilation failed: " + (r.stderr or r.stdout)[-800:])
    open(stamp, "w").write(h.hexdigest())
    return out, jars


# ── worker processes ─────────────────────────────────────────────────────────
class _Worker:
    def __init__(self, cp, workdir, main="SsaChecker"):
        self.p = subprocess.Popen(
            [_jdk_bin("java"), "-Xmx1500m", "-XX:+UseSerialGC", "-cp", cp, main],
            cwd=workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
            creationflags=_NO_WINDOW)
        line = self.p.stdout.readline()
        if not line.startswith("READY"):
            self.close()
            raise RuntimeError("the Minecraft checker did not start")

    def ask(self, line):
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        return self.p.stdout.readline()

    def alive(self):
        return self.p.poll() is None

    def close(self):
        try:
            self.p.stdin.write("QUIT\n"); self.p.stdin.flush()
        except Exception:
            pass
        try:
            self.p.wait(timeout=2)
        except Exception:
            try:
                self.p.kill()
            except Exception:
                pass


def _parse(line):
    p = line.split()
    if p and p[0] == "MISS":
        raise RuntimeError("frames not found in Minecraft: " + " ".join(p[2:]))
    if not p or p[0] != "RES":
        raise RuntimeError(line.strip() or "no answer from the checker")
    broken = [(int(p[i]), int(p[i + 1]), int(p[i + 2]), p[i + 3]) for i in range(5, len(p) - 3, 4)]
    return dict(forced=int(p[2]), natural=int(p[3]), ms=int(p[4]), broken=broken)


def _total_ram_gb():
    """Physical memory in GB (None if unknown)."""
    try:
        if os.name == "nt":
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = _MS(); m.dwLength = ctypes.sizeof(_MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return m.ullTotalPhys / 2 ** 30
            return None
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
    except Exception:
        return None


def default_workers():
    """Minecraft workers: one per 3 CPU threads, at most 8, and ~1.6 GB of RAM
    each while leaving 6 GB to the rest (03/10: was cpu/4 capped at 4). The
    checks are what limits the broken-portal search with lakes."""
    cpu = os.cpu_count() or 2
    n = min(8, cpu // 3)
    ram = _total_ram_gb()
    if ram is not None:
        n = min(n, int((ram - 6) // 1.6))
    else:
        n = min(n, 4)
    return max(1, n)


class CheckerPool:
    """N warm Minecraft workers; requests are served in order of submission."""

    def __init__(self, n=None, main="SsaChecker", parse=None):
        self.main = main
        self.parse = parse or _parse
        out, jars = _compile()
        self.cp = os.pathsep.join([os.path.join(out, "classes")] + jars)
        self.workdir = os.path.join(out, "run")
        os.makedirs(self.workdir, exist_ok=True)
        if n is None:                         # each worker ≈ 1 core and ~1.5 GB
            n = default_workers()
        self.n = n
        self.q = queue.Queue()
        self.threads = []
        self.closed = False
        self.ready = threading.Event()
        self.error = None
        for i in range(n):
            t = threading.Thread(target=self._run, args=(i,), daemon=True, name=f"mc-checker-{i}")
            t.start()
            self.threads.append(t)

    def _run(self, i):
        w = None
        while not self.closed:
            try:
                if w is None or not w.alive():
                    w = _Worker(self.cp, self.workdir, self.main)
                    self.ready.set()
            except Exception as e:                    # cannot start: report, retry later
                self.error = str(e)
                self.ready.set()
                time.sleep(5)
                continue
            try:
                item = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            line, cb = item
            try:
                res = self.parse(w.ask(line))
                err = None
            except Exception as e:
                res, err = None, str(e)
                if not w.alive():
                    w = None
            try:
                cb(res, err)
            except Exception:
                pass
        if w is not None:
            w.close()

    def submit(self, seed, frames, rox, roz, callback, natural=False, sh=None):
        """natural: False = only the /tp order; True = both orders; "auto" = the
        natural order too, only when the /tp order breaks a frame.
        sh = (chunk x, chunk z) of the stronghold start as SSA computed it (the
        request's .sh): Minecraft is told the stronghold is there, and the frames
        must be in its portal room, else the callback gets an error (03/10)."""
        cells = " ".join("%d %d %d" % tuple(f) for f in frames)
        flag = "A " if natural == "auto" else ("N " if natural else "")
        if sh is not None:
            flag += "S:%d,%d " % (int(sh[0]), int(sh[1]))
        line = flag + "%d %d %d %s" % (seed, rox, roz, cells)
        self.q.put((line, callback))

    def submit_line(self, line, callback):
        """A raw request line (workers other than SsaChecker)."""
        self.q.put((line, callback))

    def pending(self):
        return self.q.qsize()

    def clear(self):
        """Drop the requests not started yet (a new search)."""
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass

    def close(self):
        self.closed = True
        self.clear()
        for _ in self.threads:
            self.q.put(None)


_POOL = None
_POOL_LOCK = threading.Lock()


def pool():
    global _POOL
    with _POOL_LOCK:
        if _POOL is None or _POOL.closed:
            _POOL = CheckerPool()
        return _POOL


def shutdown():
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.close()
            _POOL = None


import atexit                                        # noqa: E402
atexit.register(shutdown)


def verify_many(requests, timeout=600.0, natural=False):
    """[(seed, frames, rox, roz[, sh])] -> {seed: result}, blocking (tests, tools)."""
    p = pool()
    done = threading.Event()
    out = {}
    left = [len(requests)]
    lock = threading.Lock()

    def cb_for(seed):
        def cb(res, err):
            with lock:
                out[seed] = res if res is not None else {"error": err}
                left[0] -= 1
                if left[0] == 0:
                    done.set()
        return cb
    if not requests:
        return out
    for rq in requests:
        s, f, rx, rz = rq[:4]
        p.submit(s, f, rx, rz, cb_for(s), natural=natural, sh=rq[4] if len(rq) > 4 else None)
    done.wait(timeout)
    return out


if __name__ == "__main__":                            # py mc_checker.py SEED…  (1.16)
    import ruined_portal as rp
    print(status())
    reqs = []
    for a in sys.argv[1:]:
        r = rp.corrupt_request_for(int(a), 19)
        if r:
            frames, _ex, _ez, rox, roz = r
            reqs.append((int(a), frames, rox, roz, r.sh))
    t = time.time()
    for s, r in verify_many(reqs, natural=True).items():
        print(s, r)
    print(f"{time.time() - t:.1f} s")
    shutdown()
