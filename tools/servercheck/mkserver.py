"""tools/servercheck/mkserver.py — a headless Minecraft server from a Modrinth
version folder (jar + json + libraries; their folder: $SSA_MC_FILES, default
/mnt/user-data/uploads).
    python3 mkserver.py 1.21.1-0.20.1 [java] [root (default: the current folder)]
Builds <root>/mcsrv_<version>/ (cp.txt, start.sh, eula, server.properties: seed 12345,
world w12345). Cloud-side verification tool (not used by SSA)."""
import json, os, sys
v = sys.argv[1]
java = sys.argv[2] if len(sys.argv) > 2 else '/usr/lib/jvm/java-21-openjdk-amd64/bin/java'
root = sys.argv[3] if len(sys.argv) > 3 else os.getcwd()
U = os.environ.get('SSA_MC_FILES', '/mnt/user-data/uploads')
d = json.load(open(f'{U}/versions/{v}/{v}.json'))
cp = [f'{U}/versions/{v}/{v}.jar']
seen = set()
for l in d['libraries']:
    n = l['name']
    if any(s in n for s in ('lwjgl', 'natives', 'tinyfd', 'openal', 'glfw', 'jemalloc', 'text2speech',
                            'java-objc', 'patchy', 'fabricmc', 'org.ow2.asm', 'freetype')):
        continue
    parts = n.split(':')
    if len(parts) > 3:
        continue
    g, a, ver = parts
    if (g, a) in seen:
        continue
    p = f"{U}/libraries/{g.replace('.', '/')}/{a}/{ver}/{a}-{ver}.jar"
    if os.path.exists(p):
        cp.append(p)
        seen.add((g, a))
    else:
        print('missing', p)
D = f'{root}/mcsrv_{v.split("-")[0]}'
os.makedirs(D, exist_ok=True)
open(D + '/cp.txt', 'w').write(':'.join(cp))
open(D + '/eula.txt', 'w').write('eula=true\n')
if not os.path.exists(D + '/server.properties'):
    open(D + '/server.properties', 'w').write(
        'level-seed=12345\nlevel-name=w12345\nonline-mode=false\nspawn-protection=0\n'
        'max-tick-time=-1\nview-distance=4\nsimulation-distance=4\nsync-chunk-writes=false\npause-when-empty-seconds=-1\n')
open(D + '/start.sh', 'w').write(f'''#!/bin/bash
cd {D}
[ -p cmd ] || mkfifo cmd
exec 3<>cmd
nohup {java} -Xmx5G -cp "$(cat cp.txt)" net.minecraft.server.Main --nogui <&3 > run.log 2>&1 &
echo $! > pid
''')
print(D, len(cp))
