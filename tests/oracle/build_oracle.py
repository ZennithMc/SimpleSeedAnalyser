"""Build the cubiomes-viewer reference oracle used by tests/diff_cv.py and
tests/fuzz_cv.py:   py tests/oracle/build_oracle.py
Compiles tests/oracle/cvsearch.cpp (cubiomes-viewer's own search code) with
SSA's cubiomes sources into tools/cvoracle(.exe). Needs gcc / g++ (the same
toolchain as tools/build_native.py)."""
import os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CUB = os.path.join(ROOT, "cubiomes_src")
OUT = os.path.join(ROOT, "tools", "cvoracle" + (".exe" if os.name == "nt" else ""))
UNITS = ["noise", "biomenoise", "biomes", "layers", "generator", "finders", "util", "quadbase"]


def run(cmd):
    print(" ".join(cmd[:4]), "…", flush=True)
    subprocess.check_call(cmd)


def main():
    tmp = tempfile.mkdtemp(prefix="cvoracle_")
    objs = []
    for u in UNITS:
        o = os.path.join(tmp, u + ".o")
        run(["gcc", "-O2", "-fwrapv", "-c", os.path.join(CUB, u + ".c"), "-o", o, "-I" + CUB])
        objs.append(o)
    o = os.path.join(tmp, "cvsearch.o")
    run(["g++", "-O2", "-fwrapv", "-std=c++17", "-I" + CUB, "-I" + HERE, "-c",
         os.path.join(HERE, "cvsearch.cpp"), "-o", o])
    run(["g++", "-O2", "-o", OUT, o] + objs + ["-lm"] + ([] if os.name == "nt" else ["-lpthread"]))
    print("OK:", OUT)


if __name__ == "__main__":
    sys.exit(main())
