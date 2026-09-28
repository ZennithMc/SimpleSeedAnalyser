"""
checker_client.py — bridge to the "perfect checker" (real Minecraft 1.16.1
headless).

Talks to the Java `CheckerServer` (jellejurre's seed-checker project) through a
file protocol, keeping the JVM warm:

  - we write  checker_req.txt  (one line per request: seed EPx EPz rox roz)
    then create  checker_req.ready
  - the server writes  checker_output.txt  and creates  checker_resp.ready
  - we read the output once checker_resp.ready appears

The server starts once (~20-30s JVM startup), then each verify takes ~3s.
1.16.1 only for now.
"""
import os
import glob
import time
import subprocess

# ── seed-checker project location ─────────────────────────────────────────────
_CHECKER_DIR = None


def _default_checker_dir():
    here = os.path.dirname(os.path.abspath(__file__))
    # SSA lives in .../SimpleSeedAnalyser/SimpleSeedAnalyser ; the checker in
    # .../SimpleSeedAnalyser/Sources to relie on/seed-checker-1.16.1/seed-checker-1.16.1
    return os.path.normpath(os.path.join(
        here, "..", "Sources to relie on",
        "seed-checker-1.16.1", "seed-checker-1.16.1"))


def _cfg_path():
    # user settings folder (next to the code it could not be written in an
    # installed app); the old location is still read if present
    old = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checker_path.txt")
    d = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "SimpleSeedAnalyser")
    new = os.path.join(d, "checker_path.txt")
    if not os.path.isfile(new) and os.path.isfile(old):
        return old
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return new


def _default_checker_dir_for_mc(mc):
    """The seed-checker project matching the MC version: 1.16.1 for 1.13-1.17
    (mc <= 21), 1.18.1 for 1.18+ (mc >= 22)."""
    here = os.path.dirname(os.path.abspath(__file__))
    sub = "seed-checker-1.18.1" if mc >= 22 else "seed-checker-1.16.1"
    return os.path.normpath(os.path.join(here, "..", "Sources to relie on", sub, sub))


def set_active_mc(mc):
    """Point the checker at the project for this MC version. If it changes, the
    warm server is stopped so the next verify spins up the right one."""
    global _CHECKER_DIR
    new_dir = _default_checker_dir_for_mc(mc)
    if _CHECKER_DIR != new_dir:
        stop_server()
        _CHECKER_DIR = new_dir


def checker_dir():
    """seed-checker project folder (config file > relative default)."""
    global _CHECKER_DIR
    if _CHECKER_DIR:
        return _CHECKER_DIR
    cfg = _cfg_path()
    if os.path.isfile(cfg):
        try:
            p = open(cfg, encoding="utf-8").read().strip()
            if p and os.path.isdir(p):
                _CHECKER_DIR = p
                return p
        except Exception:
            pass
    _CHECKER_DIR = _default_checker_dir()
    return _CHECKER_DIR


def set_checker_dir(path):
    """Force and remember the seed-checker project location."""
    global _CHECKER_DIR
    _CHECKER_DIR = path
    try:
        with open(_cfg_path(), "w", encoding="utf-8") as f:
            f.write(path)
    except Exception:
        pass


def find_java_home():
    """Find a JDK 17 (Temurin/Zulu/MS/Oracle); fall back to JAVA_HOME."""
    pats = [r"C:\Program Files\Eclipse Adoptium\jdk-17*",
            r"C:\Program Files\Zulu\zulu-17*",
            r"C:\Program Files\Java\jdk-17*",
            r"C:\Program Files\Microsoft\jdk-17*",
            r"C:\Program Files\BellSoft\*jdk-17*"]
    for pat in pats:
        m = sorted(glob.glob(pat))
        if m:
            return m[-1]
    jh = os.environ.get("JAVA_HOME")
    return jh if jh and os.path.isdir(jh) else None


def is_available():
    """True if the seed-checker project and a JDK 17 are present."""
    d = checker_dir()
    return os.path.isfile(os.path.join(d, "gradlew.bat")) and bool(find_java_home())


def _f(name):
    return os.path.join(checker_dir(), name)


# ── Server lifecycle ──────────────────────────────────────────────────────────
_proc = None


def is_running():
    return _proc is not None and _proc.poll() is None


def start_server():
    """Start CheckerServer in the background (idempotent). Returns True if
    started or already running, False if unavailable."""
    global _proc
    if is_running():
        return True
    if not is_available():
        return False
    d = checker_dir()
    env = dict(os.environ)
    jh = find_java_home()
    if jh:
        env["JAVA_HOME"] = jh
    for n in ("checker_req.ready", "checker_resp.ready", "checker_shutdown.flag"):
        try:
            os.remove(_f(n))
        except OSError:
            pass
    cmd = [os.path.join(d, "gradlew.bat"), "test", "--tests", "CheckerServer",
           "--rerun-tasks", "-q"]
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    _proc = subprocess.Popen(cmd, cwd=d, env=env,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             creationflags=flags)
    return True


def stop_server():
    """Stop the server cleanly (shutdown flag)."""
    global _proc
    try:
        open(_f("checker_shutdown.flag"), "w").close()
    except Exception:
        pass
    _proc = None


# ── Request ───────────────────────────────────────────────────────────────────
def verify(requests, timeout=150.0, poll=0.1):
    """requests: list of (seed, frames, rox, roz) where frames is a list of 12
    exact (x, y, z) end-portal frame positions.
    Returns {seed: {"natural": int, "forced": int, "broken": [(x,y,z,block), ...]}}.
      - natural = frames broken even in the default generation
      - forced  = frames broken with the ruined-last approach (>= natural)
      - broken  = the forced-broken frames + their replacement block
    Starts the server if needed. The first call includes the JVM startup
    (~20-30s), hence the generous timeout; later calls take ~3s.
    Raises RuntimeError if the checker is unavailable, TimeoutError on no reply."""
    requests = list(requests)
    if not requests:
        return {}
    if not is_available():
        raise RuntimeError(
            "Checker unavailable: seed-checker project not found (%s) or JDK 17 missing."
            % checker_dir())
    if not is_running():
        start_server()
    lines = []
    for (s, frames, rox, roz) in requests:
        cells = " ".join("%d %d %d" % (x, y, z) for (x, y, z) in frames)
        lines.append("%d %d %d %s\n" % (s, rox, roz, cells))
    try:
        os.remove(_f("checker_resp.ready"))
    except OSError:
        pass
    with open(_f("checker_req.txt"), "w", encoding="utf-8") as fh:
        fh.write("".join(lines))
    open(_f("checker_req.ready"), "w").close()

    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.isfile(_f("checker_resp.ready")):
            break
        if not is_running():
            raise RuntimeError("The checker server stopped (see Gradle).")
        time.sleep(poll)
    else:
        raise TimeoutError("No reply from the checker (startup too slow?).")

    res = {}
    cur = None
    try:
        with open(_f("checker_output.txt"), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("SEED "):
                    cur = int(line[5:])
                    res[cur] = {"natural": 0, "forced": 0, "broken": []}
                elif line.startswith("NATURAL ") and cur is not None:
                    res[cur]["natural"] = int(line[8:])
                elif line.startswith("FORCED ") and cur is not None:
                    res[cur]["forced"] = int(line[7:])
                elif line.startswith("FRAME ") and cur is not None:
                    p = line.split()
                    res[cur]["broken"].append(
                        (int(p[1]), int(p[2]), int(p[3]), p[4]))
                elif line.startswith("ERROR"):
                    raise RuntimeError("Checker: " + line)
    finally:
        try:
            os.remove(_f("checker_resp.ready"))
        except OSError:
            pass
    return res


def verify_one(seed, frames, rox, roz, timeout=150.0):
    """Verify a single seed. Returns {"natural","forced","broken"} or None."""
    r = verify([(seed, frames, rox, roz)], timeout=timeout)
    return r.get(seed)
