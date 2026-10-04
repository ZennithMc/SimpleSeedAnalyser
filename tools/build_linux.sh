#!/usr/bin/env bash
# Build the Linux release of SimpleSeedAnalyser (onedir, like the Windows .exe).
#
#   bash tools/build_linux.sh            → dist/SimpleSeedAnalyser-linux-x86_64.tar.gz
#
# Portability: the Python used is a python-build-standalone one (via uv, needs
# glibc 2.17), the Qt wheels need glibc 2.34, the system libraries of the build
# machine are NOT shipped (see the .spec) and OpenMP comes from a portable
# libgomp. Result: runs on glibc ≥ 2.34 (Ubuntu 22.04+, Debian 12+, Fedora 35+,
# Linux Mint 21+, Arch…). Needs: gcc, g++, pip, ~4 GB free (CUDA compiler).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
VENV=${VENV:-$ROOT/.venv-build-linux}

command -v uv >/dev/null || python3 -m pip install --user uv
PY=$(uv python find 3.11 2>/dev/null || (uv python install 3.11 >/dev/null && uv python find 3.11))
[ -d "$VENV" ] || uv venv -p "$PY" "$VENV"
VIRTUAL_ENV=$VENV uv pip install -q pyinstaller pyinstaller-hooks-contrib PySide6-Essentials numpy pillow zstandard

# portable OpenMP runtime (GCC runtime library exception): the one vendored in
# the scikit-learn manylinux2014 wheel (glibc 2.17)
mkdir -p tools/linux_vendor
if [ ! -f tools/linux_vendor/libgomp.so.1 ]; then
    tmp=$(mktemp -d)
    python3 -m pip download scikit-learn --no-deps --only-binary=:all: \
        --python-version 3.11 --platform manylinux2014_x86_64 -q -d "$tmp"
    "$VENV/bin/python" - "$tmp" <<'PY'
import glob, sys, zipfile
w = glob.glob(sys.argv[1] + "/*.whl")[0]
z = zipfile.ZipFile(w)
n = next(x for x in z.namelist() if "/libgomp" in x)
open("tools/linux_vendor/libgomp.so.1", "wb").write(z.read(n))
print("libgomp:", n)
PY
    rm -rf "$tmp"
fi

# native libraries, rebuilt from the sources of this tree
rm -f libcubiomes.so libgaider_layouts.so libterrain.so terrain.so
"$VENV/bin/python" - <<'PY'
import cubiomes_wrap, gaider_layouts, terrain
cubiomes_wrap._compile_cubiomes()
assert gaider_layouts._compile(), gaider_layouts._LIB_LOAD_ERROR
assert terrain._compile()
PY

# GPU (CUDA) libraries: a system nvcc, else the CUDA 13.0 compiler from PyPI
# (13.0 = what driver 580, the minimum, supports; the libraries link cudart
# statically, so users only need the NVIDIA driver). SKIP_CUDA=1 to skip.
if [ "${SKIP_CUDA:-0}" != 1 ]; then
    if ! command -v nvcc >/dev/null; then
        CU=$ROOT/.cuda13
        [ -x "$CU/nvidia/cu13/bin/nvcc" ] || python3 -m pip install -q --target "$CU" \
            "nvidia-cuda-nvcc==13.0.88" "nvidia-cuda-runtime==13.0.96" \
            "nvidia-cuda-crt==13.0.88" "nvidia-nvvm==13.0.88"
        chmod +x "$CU"/nvidia/cu13/bin/* "$CU"/nvidia/cu13/nvvm/bin/* 2>/dev/null || true
        export PATH="$CU/nvidia/cu13/bin:$PATH" LIBRARY_PATH="$CU/nvidia/cu13/lib:${LIBRARY_PATH:-}"
    fi
    SSA_NO_AUTOBUILD=1 "$VENV/bin/python" tools/build_native.py
fi

"$VENV/bin/python" -m PyInstaller --noconfirm --clean SimpleSeedAnalyser.spec
for f in README.md LICENSE THIRD_PARTY_LICENSES.txt; do
    [ -f "$f" ] && cp "$f" dist/SimpleSeedAnalyser/
done
tar -C dist -czf dist/SimpleSeedAnalyser-linux-x86_64.tar.gz SimpleSeedAnalyser
echo "→ dist/SimpleSeedAnalyser-linux-x86_64.tar.gz"
