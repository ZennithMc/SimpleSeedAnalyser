"""
tools/servercheck/spawncheck.py — (12/11) the world spawn of real servers against
spawn_exact.py (cloud-side verification, not used by SSA).

For each seed: a fresh world on a headless server (mkserver.py's cp.txt: the jar and
its libraries), started until « Done », stopped, then the spawn read in level.dat
(1.21.9+: Data.spawn.pos, before: SpawnX / SpawnY / SpawnZ). 2 servers at once
(their own folder and port). Results appended to truth_<tag>.jsonl.

    python3 spawncheck.py <server dir (with cp.txt)> <tag> <seeds file> [mc] [threads]
With mc (SSA's version id), prints the comparison with spawn_exact at the end.
"""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

JAVA = "/usr/lib/jvm/java-21-openjdk-amd64/bin/java"
PROPS = """level-seed={seed}
level-name=w
online-mode=false
server-port={port}
query.port={port}
view-distance=2
simulation-distance=2
spawn-protection=0
max-tick-time=-1
sync-chunk-writes=false
pause-when-empty-seconds=-1
management-server-port=0
"""


def java_of(srv):
    """the java of the server's start.sh (26.x: Java 25)"""
    try:
        m = re.search(r"(/\S+/bin/java)", open(os.path.join(srv, "start.sh")).read())
        if m:
            return m.group(1)
    except OSError:
        pass
    return JAVA


def world_spawn(cp, seed, d, port, java=JAVA):
    """(x, y, z) of the world spawn of a new world `seed` on a server"""
    import nbtlib
    os.makedirs(d, exist_ok=True)
    open(d + "/eula.txt", "w").write("eula=true\n")
    open(d + "/server.properties", "w").write(PROPS.format(seed=seed, port=port))
    shutil.rmtree(d + "/w", ignore_errors=True)
    p = subprocess.Popen([java, "-Xmx3G", "-cp", cp, "net.minecraft.server.Main", "--nogui"], cwd=d,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True)
    for line in p.stdout:
        if "Done (" in line:
            p.stdin.write("stop\n")
            p.stdin.flush()
    p.wait()
    f = nbtlib.load(d + "/w/level.dat")
    data = f["Data"] if "Data" in f else f[""]["Data"]
    if "spawn" in data:
        return tuple(int(v) for v in data["spawn"]["pos"])
    return int(data["SpawnX"]), int(data["SpawnY"]), int(data["SpawnZ"])


def main():
    srv, tag, listf = sys.argv[1], sys.argv[2], sys.argv[3]
    mc = int(sys.argv[4]) if len(sys.argv) > 4 else None
    nth = int(sys.argv[5]) if len(sys.argv) > 5 else 2
    cp = open(os.path.join(srv, "cp.txt")).read().strip()
    java = java_of(srv)
    outf = f"truth_{tag}.jsonl"
    done = set()
    if os.path.exists(outf):
        done = {json.loads(l)["seed"] for l in open(outf)}
    q = queue.Queue()
    for l in open(listf):
        if l.strip() and int(l.split()[0]) not in done:
            q.put(int(l.split()[0]))
    lock = threading.Lock()

    def work(i):
        while True:
            try:
                s = q.get_nowait()
            except queue.Empty:
                return
            t = time.time()
            try:
                rec = {"seed": s, "pos": list(world_spawn(cp, s, f"run_{tag}_{i}", 25600 + 7 * i, java)),
                       "t": round(time.time() - t, 1)}
            except Exception as e:                       # noqa: BLE001
                rec = {"seed": s, "err": repr(e)}
            with lock:
                open(outf, "a").write(json.dumps(rec) + "\n")
    ts = [threading.Thread(target=work, args=(i,)) for i in range(nth)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    if mc is not None:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
        import spawn_exact
        n = ok = 0
        for l in open(outf):
            r = json.loads(l)
            if "pos" not in r:
                continue
            sp = spawn_exact.spawn(r["seed"], mc)
            n += 1
            good = (sp[0], sp[2]) == (r["pos"][0], r["pos"][2])
            ok += good
            if not good:
                print(r["seed"], "server", r["pos"], "SSA", sp, "doubt", spawn_exact.doubt(r["seed"], mc))
        print(f"{n} seeds, X Z exact: {ok}")


if __name__ == "__main__":
    main()
