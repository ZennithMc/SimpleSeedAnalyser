"""tools/servercheck/srv.py — drive a headless server built by mkserver.py (FIFO cmd,
run.log), and make it generate chunks: forceload, wait, save."""
import os, re, subprocess, time

import anvil


class Server:
    def __init__(self, d):
        self.d = d
        self.world = os.path.join(d, "w12345")

    def running(self):
        try:
            os.kill(int(open(self.d + "/pid").read()), 0)
            return True
        except Exception:
            return False

    def start(self, timeout=300):
        if self.running():
            return
        try:
            os.rename(self.d + "/run.log", self.d + "/run.old.log")
        except OSError:
            pass
        subprocess.run(["setsid", "bash", self.d + "/start.sh"])
        if not self.wait(r"Done \(", timeout, 0):
            raise RuntimeError("server did not start")

    def _loglen(self):
        try:
            return os.path.getsize(self.d + "/run.log")
        except OSError:
            return 0

    def cmd(self, *lines):
        with open(self.d + "/cmd", "w") as f:
            for l in lines:
                f.write(l + "\n")
            f.flush()

    def wait(self, pat, timeout=600, since=0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                with open(self.d + "/run.log", "rb") as f:
                    f.seek(since)
                    if re.search(pat, f.read().decode("utf-8", "replace")):
                        return True
            except OSError:
                pass
            time.sleep(2)
        return False

    def save(self, timeout=600):
        n0 = self._loglen()
        self.cmd("save-all flush")
        return self.wait(r"Saved the game", timeout, n0)

    def stop(self):
        if not self.running():
            return
        self.cmd("stop")
        pid = int(open(self.d + "/pid").read())
        for _ in range(120):
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(1)
        os.kill(pid, 9)

    def generate(self, chunks, full=True, batch=64, timeout=3600, log=print):
        """Force-load `chunks` [(cx, cz)] (batches), until each is saved (full or at
        least structure_starts), then release them."""
        todo = sorted(set(chunks))

        def done(c):
            st = anvil.status(anvil.chunk(self.world, *c))
            return st == "full" if full else st is not None
        todo = [c for c in todo if not done(c)]
        t0 = time.time()
        while todo and time.time() - t0 < timeout:
            part = todo[:batch]
            self.cmd(*[f"forceload add {cx * 16} {cz * 16}" for cx, cz in part])
            for _ in range(60):
                time.sleep(5)
                self.save()
                left = [c for c in part if not done(c)]
                if not left:
                    break
            self.cmd("forceload remove all")
            todo = [c for c in todo if not done(c)]
            log(f"  generated, {len(todo)} chunks left")
        return not todo
