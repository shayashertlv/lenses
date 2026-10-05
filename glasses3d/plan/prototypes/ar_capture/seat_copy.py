"""Scratch-only geometry A/B: write a COPY of a Studio FT1119 GLB in which the lens top and the eyewire's top section are
raised into the acetate brow (a smooth vertical displacement field that is full at the lens top and zero 3 mm below it).
Nothing else changes (materials, UVs, normals, other parts). Purpose: test whether a seated lens renders cleanly in the
unchanged runtime, i.e. separate the geometry cause from runtime causes. Not a proposed asset fix."""
import json, struct, sys, numpy as np
from glbmesh import load, NC, CT

src, dst = sys.argv[1], sys.argv[2]
lens_prefixes = sys.argv[3].split(',')      # node-name prefixes of lens meshes
wire_prefixes = sys.argv[4].split(',')      # node-name prefixes of eyewire meshes
D = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0012   # lift at the top, metres
R = float(sys.argv[6]) if len(sys.argv) > 6 else 0.003    # ramp length below the top
d, b = load(src)
b = bytearray(b)

def view(acc_index):
    a = d['accessors'][acc_index]; bv = d['bufferViews'][a['bufferView']]
    assert bv.get('byteStride', 0) in (0, 12)
    start = bv.get('byteOffset', 0) + a.get('byteOffset', 0)
    return np.frombuffer(b, dtype=np.float32, count=a['count'] * 3, offset=start).reshape(-1, 3), a

def nodes(prefixes):
    return [n for n in d['nodes'] if 'mesh' in n and any(n.get('name', '').startswith(p) for p in prefixes)]

def smooth(t):
    t = np.clip(t, 0, 1); return t * t * (3 - 2 * t)

report = {}
for lp in lens_prefixes:
    side = 'Left' if 'Left' in lp else 'Right'
    lens = nodes([lp]); wires = [n for n in nodes(wire_prefixes) if side in n['name']]
    assert len(lens) == 1, (lp, lens)
    prim = d['meshes'][lens[0]['mesh']]['primitives'][0]
    LP, _ = view(prim['attributes']['POSITION'])
    # lens top y as a function of x (max y per 0.5 mm bin, interpolated)
    xs = np.arange(LP[:, 0].min(), LP[:, 0].max() + 5e-4, 5e-4)
    tops = np.array([LP[np.abs(LP[:, 0] - x) < 5e-4, 1].max() if (np.abs(LP[:, 0] - x) < 5e-4).any() else np.nan for x in xs])
    ok = ~np.isnan(tops); xs, tops = xs[ok], tops[ok]
    def lift(P):
        top = np.interp(P[:, 0], xs, tops)
        return D * smooth((P[:, 1] - (top - R)) / R) * (P[:, 1] > 0)
    for n in lens + wires:
        for pr in d['meshes'][n['mesh']]['primitives']:
            P, a = view(pr['attributes']['POSITION'])
            P = P.copy(); dy = lift(P); P[:, 1] += dy
            bv = d['bufferViews'][a['bufferView']]; start = bv.get('byteOffset', 0) + a.get('byteOffset', 0)
            b[start:start + P.nbytes] = P.astype(np.float32).tobytes()
            a['min'] = P.min(0).tolist(); a['max'] = P.max(0).tolist()
            report[n['name']] = {'moved_vertices': int((dy > 1e-7).sum()), 'max_lift_mm': float(dy.max() * 1000)}
js = json.dumps(d, separators=(',', ':')).encode()
js += b' ' * ((4 - len(js) % 4) % 4)
binchunk = bytes(b)
binchunk += b'\0' * ((4 - len(binchunk) % 4) % 4)
total = 12 + 8 + len(js) + 8 + len(binchunk)
out = struct.pack('<III', 0x46546C67, 2, total) + struct.pack('<I', len(js)) + b'JSON' + js + struct.pack('<I', len(binchunk)) + b'BIN\0' + binchunk
open(dst, 'wb').write(out)
print(json.dumps(report, indent=1))
