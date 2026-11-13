"""tools/servercheck/conv116.py — registries dumped by ssadump.Dump (1.16.2-1.17.1,
inline) -> a reports/ tree like the 1.18.2 data generator, for
tools/build_jigsaw_data.py: inline processor lists get a name (inline_<md5>), inline
features their registry name, configured structures their start pool name and biomes.
    python3 conv116.py <dump dir> <out dir>"""
import hashlib, json, os, sys
src, out = sys.argv[1], sys.argv[2]
pools = json.load(open(f'{src}/template_pool.json'))
feats = json.load(open(f'{src}/configured_feature.json'))
csf = json.load(open(f'{src}/configured_structure_feature.json'))
canon = lambda o: json.dumps(o, sort_keys=True)
fidx = {canon(v): k for k, v in feats.items()}
base = f'{out}/reports/worldgen/minecraft/worldgen'
procs = {}


def fix(el):
    t = el.get('element_type', '')
    if 'processors' in el and isinstance(el['processors'], dict):
        c = canon(el['processors'])
        name = 'minecraft:inline_' + hashlib.md5(c.encode()).hexdigest()[:10]
        procs[name] = el['processors']
        el['processors'] = name
    if t.endswith('feature_pool_element') and isinstance(el.get('feature'), dict):
        el['feature'] = fidx.get(canon(el['feature'])) or 'minecraft:unknown_feature'
    if t.endswith('list_pool_element'):
        for e in el['elements']:
            fix(e)


for p in pools.values():
    for e in p['elements']:
        fix(e['element'])


def w(kind, name, obj):
    path = f"{base}/{kind}/{name.split(':', 1)[-1]}.json"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(obj, open(path, 'w'))


for k, p in pools.items():
    w('template_pool', k, p)
for k, p in procs.items():
    w('processor_list', k, p)
biomes = json.load(open(f'{src}/biome.json'))
cidx = {canon(v): k for k, v in json.load(open(f'{src}/configured_structure_feature.json')).items()}
bstarts = {b: [cidx.get(canon(x)) if isinstance(x, dict) else x for x in d.get('starts', [])]
           for b, d in biomes.items()}
for k, s in csf.items():
    sp = s.get('config', {}).get('start_pool')
    if isinstance(sp, dict):
        s['config']['start_pool'] = sp['name']
    s['biomes'] = [b for b, st in bstarts.items() if k in st]
    w('configured_structure_feature', k, s)
print(len(pools), 'pools', len(procs), 'inline processor lists')
