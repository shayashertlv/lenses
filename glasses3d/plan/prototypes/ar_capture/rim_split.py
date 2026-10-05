"""Scratch-only shading A/B: COPY of a GLB in which each lens primitive's rim is shaded with a sharp edge:
front/back cap corners on rim vertices take the cap-only area-weighted normal, wall (edge) faces are flat. Positions and
UVs are unchanged; the primitive is written non-indexed. Purpose: isolate the smoothed-rim highlight line."""
import json, struct, sys, numpy as np
from glbmesh import load, acc

src, dst = sys.argv[1], sys.argv[2]
prefixes = sys.argv[3].split(',')
d, b = load(src)
b = bytearray(b)

def add(arr, typ):
    global b
    arr = np.ascontiguousarray(arr, np.float32)
    while len(b) % 4: b += b'\0'
    off = len(b); b += arr.tobytes()
    d['bufferViews'].append({'buffer': 0, 'byteOffset': off, 'byteLength': arr.nbytes})
    a = {'bufferView': len(d['bufferViews']) - 1, 'componentType': 5126, 'count': int(arr.shape[0]), 'type': typ}
    if typ == 'VEC3': a['min'] = arr.min(0).tolist(); a['max'] = arr.max(0).tolist()
    d['accessors'].append(a); return len(d['accessors']) - 1

report = {}
for n in d['nodes']:
    if 'mesh' not in n or not any(n.get('name', '').startswith(p) for p in prefixes): continue
    for pr in d['meshes'][n['mesh']]['primitives']:
        P = acc(d, b, pr['attributes']['POSITION']); N = acc(d, b, pr['attributes']['NORMAL'])
        UV = acc(d, b, pr['attributes']['TEXCOORD_0']) if 'TEXCOORD_0' in pr['attributes'] else None
        I = acc(d, b, pr['indices']).reshape(-1, 3)
        T = P[I]; fn = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]); area = np.linalg.norm(fn, axis=1, keepdims=True); fu = fn / np.maximum(area, 1e-30)
        front, back = fu[:, 2] > .5, fu[:, 2] < -.5; wall = ~front & ~back
        rim = np.zeros(len(P), bool); rim[np.unique(I[wall])] = True
        capN = {}
        for cls in (front, back):
            acc_n = np.zeros_like(P)
            for k in range(3): np.add.at(acc_n, I[cls, k], fn[cls])
            capN[id(cls)] = acc_n / np.maximum(np.linalg.norm(acc_n, axis=1, keepdims=True), 1e-30)
        NC = N[I].copy()                                   # (F,3,3) per-corner
        for cls in (front, back):
            cn = capN[id(cls)]
            for k in range(3):
                sel = cls & rim[I[:, k]]
                NC[sel, k] = cn[I[sel, k]]
        NC[wall] = fu[wall][:, None, :]
        pr['attributes'] = {'POSITION': add(P[I].reshape(-1, 3), 'VEC3'), 'NORMAL': add(NC.reshape(-1, 3), 'VEC3'),
                            **({'TEXCOORD_0': add(UV[I].reshape(-1, 2), 'VEC2')} if UV is not None else {})}
        del pr['indices']
        report[n['name']] = {'faces': int(len(I)), 'wall_faces_flat': int(wall.sum()), 'rim_vertices_cap_normal': int(rim.sum())}
d['buffers'][0]['byteLength'] = len(b)
js = json.dumps(d, separators=(',', ':')).encode(); js += b' ' * ((4 - len(js) % 4) % 4)
while len(b) % 4: b += b'\0'
total = 12 + 8 + len(js) + 8 + len(b)
open(dst, 'wb').write(struct.pack('<III', 0x46546C67, 2, total) + struct.pack('<I', len(js)) + b'JSON' + js + struct.pack('<I', len(b)) + b'BIN\0' + bytes(b))
print(json.dumps(report, indent=1))
