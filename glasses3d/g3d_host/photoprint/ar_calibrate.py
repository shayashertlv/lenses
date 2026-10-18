"""Production AR level calibration, migrated from the WP5 proof driver.

All photos, semantic part groups and pigment-family controls are explicit per job.
Independent instruments and historical calibration evidence remain in tests. The uncalled re-grade tail
(``regrade``, ``regrade_summary``, ``_regrade_chunk``, ``_repo_path``) was deleted on 2026-10-13; the legacy
copy in tests/photoprint/ar_calibrate.py keeps its own.
"""
from __future__ import annotations
import hashlib
import io
import json
import math
import struct
import subprocess
import threading
import time
from pathlib import Path
import numpy as np
from PIL import Image
from glasses3d.g3d_host.photoprint import shading as SH
from glasses3d.g3d_host.photoprint.procs import opt_out_ecoqos
from . import ar_levels as AL
from .metrics import part_name_matches
G3D = Path(__file__).resolve().parents[2]
ARVIEW = G3D / "arview"
STOP_TOL = 1.0
NO_TEXTURE = 10
GRID = ((0.55, 0.75, 0.95), (0.85, 1.0, 1.15))
LOG_UNIT = math.log(0.95 / 0.75)
GAMMA_UNIT = 0.15
K_NEAR = 6
MIN_RADIUS = 0.06
TARGETS = {"photo_angles": "appearance", "near_18deg": "trade-off"}
ROLE_PAIRS = {"appearance": "calibration"}
CHROMA_PROBES = ((1.25, 1.0), (1.0, 0.7), (1.25, 0.7))
SAT_BOX = (0.8, 2.2)
POWER_BOX = (0.4, 1.2)
CHROMA_UNIT_SAT = math.log(1.25)
CHROMA_UNIT_POWER = 0.3
CHROMA_STOP = 0.5
CHROMA_MAX_ITER = 4
ALL_GRADED = ("calibration", "trade-off", "held-out")
L_TOL = 2.0
CONTRAST_UNIT = math.log(1.18) / 2.0

LABELS = {
    "photo_angles": "level calibrated at the photos' measured camera directions",
    "near_18deg": "level fitted to the near-view trade-off pairs (angles recorded per pair)",
    "all_pairs_chroma": "chroma fitted over every graded pair",
}


class Calibrator:
    """The existing measured search with explicit, isolated product inputs; no API calls."""

    def __init__(self, job, groups, *, chroma_family, temple_z=None, reference_point_mm=None):
        self.job = job
        self.measurements = AL.Measurements(job, groups, temple_z=temple_z, reference_point_mm=reference_point_mm)
        self.PRINT_PART_NAMES = self.measurements.PRINT_PARTS
        if not isinstance(chroma_family, dict) or set(chroma_family) - {'chroma_hold', 'chroma_hue'}:
            raise ValueError('chroma_family accepts only explicit chroma_hold and chroma_hue controls')
        self.CHROMA_FAMILY = dict(chroma_family)


    def _chunks(self, data: bytes):
        magic, ver, total = struct.unpack_from('<4sII', data, 0)
        assert magic == b'glTF' and ver == 2, 'not a GLB 2.0'
        off, js, binc = (12, None, b'')
        while off < total:
            ln, typ = struct.unpack_from('<II', data, off)
            body = data[off + 8:off + 8 + ln]
            if typ == 1313821514:
                js = json.loads(body.decode('utf-8'))
            elif typ == 5130562:
                binc = body
            off += 8 + ln
        return (js, binc)

    def print_image_index(self, doc: dict) -> int:
        """The image the printed materials' baseColorTexture uses (the node extras carry g3d_print_uv)."""
        idx = set()
        for node in doc.get('nodes', []):
            if (not (node.get('extras') or {}).get('g3d_print_uv') or 'mesh' not in node
                    or not part_name_matches(node.get('name', ''), self.PRINT_PART_NAMES)):
                continue
            for prim in doc['meshes'][node['mesh']]['primitives']:
                mat = doc['materials'][prim['material']]
                t = (mat.get('pbrMetallicRoughness') or {}).get('baseColorTexture')
                if t is not None:
                    idx.add(doc['textures'][t['index']]['source'])
        if len(idx) != 1:
            raise ValueError(f'expected one print image, found {sorted(idx)}')
        return idx.pop()

    def part_image_indices(self, doc: dict, parts=None) -> list[int]:
        """Every image a base-colour texture of ``parts``' materials uses (node names; a '.001' suffix is ignored)."""
        parts = self.PRINT_PART_NAMES if parts is None else parts
        idx = set()
        for node in doc.get('nodes', []):
            if 'mesh' not in node or not part_name_matches(node.get('name', ''), parts):
                continue
            for prim in doc['meshes'][node['mesh']]['primitives']:
                mat = doc['materials'][prim['material']]
                t = (mat.get('pbrMetallicRoughness') or {}).get('baseColorTexture')
                if t is not None:
                    idx.add(doc['textures'][t['index']]['source'])
        return sorted(idx)

    def swap_images(self, src: Path, dst: Path, pngs: dict) -> dict:
        """Write ``dst`` = ``src`` with images {index: png bytes} replaced (bufferViews repacked, 4-byte aligned)."""
        if Path(src).resolve() == Path(dst).resolve():
            raise ValueError('calibration probes must be written to a copy')
        doc, binc = self._chunks(Path(src).read_bytes())
        for node in doc.get('nodes', []):
            if 'mesh' not in node or part_name_matches(node.get('name', ''), self.PRINT_PART_NAMES):
                continue
            for prim in doc['meshes'][node['mesh']]['primitives']:
                material = doc['materials'][prim['material']]
                texture = (material.get('pbrMetallicRoughness') or {}).get('baseColorTexture')
                if texture and doc['textures'][texture['index']]['source'] in pngs:
                    raise ValueError('print image is shared with a non-print part; attach an isolated print first')
        targets = {doc['images'][k]['bufferView']: png for k, png in pngs.items()}
        views = doc['bufferViews']
        order = sorted(range(len(views)), key=lambda i: views[i].get('byteOffset', 0))
        out = bytearray()
        for i in order:
            v = views[i]
            data = targets[i] if i in targets else binc[v.get('byteOffset', 0):v.get('byteOffset', 0) + v['byteLength']]
            out += b'\x00' * (-len(out) % 4)
            v['byteOffset'] = len(out)
            v['byteLength'] = len(data)
            out += data
        out += b'\x00' * (-len(out) % 4)
        doc['buffers'][0]['byteLength'] = len(out)
        js = json.dumps(doc, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        js += b' ' * (-len(js) % 4)
        body = struct.pack('<II', len(js), 1313821514) + js + struct.pack('<II', len(out), 5130562) + bytes(out)
        Path(dst).write_bytes(struct.pack('<4sII', b'glTF', 2, 12 + len(body)) + body)
        return {'images': sorted(pngs), 'bufferViews': sorted(targets), 'png_sha256': {k: hashlib.sha256(v).hexdigest() for k, v in pngs.items()}}

    def swap_image(self, src: Path, dst: Path, png: bytes) -> dict:
        """Write ``dst`` = ``src`` with the print image's bytes replaced by ``png`` (bufferViews repacked, 4-byte aligned)."""
        doc, _ = self._chunks(Path(src).read_bytes())
        k = self.print_image_index(doc)
        r = self.swap_images(src, dst, {k: png})
        return {'image_index': k, 'bufferView': r['bufferViews'][0], 'png_sha256': r['png_sha256'][k]}

    def _png(self, arr: np.ndarray) -> bytes:
        buf = io.BytesIO()
        Image.fromarray(np.asarray(arr, np.uint8)).save(buf, format='PNG')
        return buf.getvalue()

    def black_probe(self, src: Path, dst: Path, clearcoat: bool=True) -> dict:
        """``dst`` = ``src`` with every base-colour image of the print parts' materials black (8 x 8): its capture shows
        the renderer's reflections alone (the probe of ``ar_levels``); a PROBE, never a model. ``clearcoat=False`` (fix
        round 1 of step 6, the material note's attribution): the print parts' materials also lose their
        KHR_materials_clearcoat, so the coat's share of the reflections is measured (a probe for the note to Astra; q001's
        materials are never changed)."""
        doc, _ = self._chunks(Path(src).read_bytes())
        ks = self.part_image_indices(doc)
        if not ks:
            raise ValueError(f'{src}: no base-colour image on the print parts')
        black = self._png(np.zeros((8, 8, 3), np.uint8))
        info = self.swap_images(src, dst, {k: black for k in ks})
        if not clearcoat:
            info['clearcoat_removed_from'] = self.strip_clearcoat(dst)
        return info

    def strip_clearcoat(self, path: Path, parts=None) -> list[str]:
        """In place: remove KHR_materials_clearcoat from the materials of ``parts``' primitives (JSON chunk rewritten, the
        binary chunk copied byte for byte). Probes only."""
        parts = self.PRINT_PART_NAMES if parts is None else parts
        data = Path(path).read_bytes()
        doc, binc = self._chunks(data)
        mats = set()
        for node in doc.get('nodes', []):
            if 'mesh' in node and part_name_matches(node.get('name', ''), parts):
                mats |= {prim['material'] for prim in doc['meshes'][node['mesh']]['primitives'] if 'material' in prim}
        names = []
        for i in sorted(mats):
            ext = doc['materials'][i].get('extensions') or {}
            if ext.pop('KHR_materials_clearcoat', None) is not None:
                names.append(doc['materials'][i].get('name'))
                if not ext:
                    doc['materials'][i].pop('extensions', None)
        if not any(('KHR_materials_clearcoat' in (m.get('extensions') or {}) for m in doc['materials'])):
            doc['extensionsUsed'] = [e for e in doc.get('extensionsUsed', []) if e != 'KHR_materials_clearcoat']
        js = json.dumps(doc, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        js += b' ' * (-len(js) % 4)
        b = binc + b'\x00' * (-len(binc) % 4)
        body = struct.pack('<II', len(js), 1313821514) + js + struct.pack('<II', len(b), 5130562) + b
        Path(path).write_bytes(struct.pack('<4sII', b'glTF', 2, 12 + len(body)) + body)
        return names

    def levelled_png(self, bake: Path, level: dict) -> bytes:
        img = np.asarray(Image.open(bake / 'print.png').convert('RGB'))
        cls = np.asarray(Image.open(bake / 'classes.png'))
        m = np.ones(img.shape[:2], bool)
        if cls.shape == m.shape:
            m &= cls != NO_TEXTURE
        return self._png(SH.apply_level_u8(img, m, level))

    def capture(self, glb: Path, out: Path, log, portrait: bool=False) -> Path:
        """arview capture of ``glb`` at every pose (skin background; the worn portrait only when ``portrait``); the node
        process is opted out of EcoQoS (not inherited from this one)."""
        cmd = ['node', 'bin/arview.mjs', 'capture', str(Path(glb).resolve()), '--poses', 'all', '--backgrounds', 'skin', '--out', str(Path(out).resolve())] + ([] if portrait else ['--no-portrait'])
        t = time.perf_counter()
        p = subprocess.Popen(cmd, cwd=str(ARVIEW), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
        opt_out_ecoqos(p.pid)
        # arview owns its progress/stall guards; elapsed time alone must not kill a healthy capture.
        so, se = p.communicate()
        log(f"capture {Path(out).name}{(' (with portrait)' if portrait else '')}: exit {p.returncode} in {time.perf_counter() - t:.0f} s")
        Path(str(out) + '.log').write_text(so[-20000:] + '\n--- stderr ---\n' + se[-20000:], encoding='utf-8')
        if p.returncode not in (0, 3) or not (Path(out) / 'report.json').is_file():
            raise RuntimeError(f'arview capture failed ({p.returncode}): {se[-2000:]}')
        return Path(out)

    def capture_with_probe(self, glb: Path, out: Path, log=print, portrait: bool=False, job=None, photos=None, coat_off_probe: bool=False) -> dict:
        """``glb`` captured at every pose (out/capture) with its black probe (out/probe), graded by ``ar_levels``
        (out/ar_levels.json); ``coat_off_probe``: also the black probe without the clearcoat (out/probe_nocoat)."""
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        cap = self.capture(glb, out / 'capture', log, portrait)
        pg = out / f'probe_{Path(glb).stem}.glb'
        self.black_probe(glb, pg)
        probe = self.capture(pg, out / 'probe', log)
        if coat_off_probe:
            pn = out / f'probe_nocoat_{Path(glb).stem}.glb'
            self.black_probe(glb, pn, clearcoat=False)
            self.capture(pn, out / 'probe_nocoat', log)
        res = self.measurements.measure([cap], job=job, probes={str(cap): str(probe)}, photos=photos)
        res['glb'] = str(glb)
        (out / 'ar_levels.json').write_text(json.dumps(res, indent=1), encoding='utf-8')
        return res

    def appearance_residuals(self, l_res: list, std_ratios: list) -> list:
        """Step 6: the ``appearance`` target's residuals: the L* residuals, then per pair 2 x ln(std ratio) / ln(1.18)
        (``CONTRAST_UNIT``; a missing ratio counts as the worst case, 99)."""
        return list(l_res) + [round(math.log(x) / CONTRAST_UNIT, 3) if x else 99.0 for x in std_ratios]

    def chroma_stats(self, row: dict, pairs, form: str=AL.TARGET) -> list:
        """Per pair [[AR C*ab p50, p90, mean], [photo p50, p90, mean]] (the chroma stage's model reads these)."""
        out = []
        for p, s in pairs:
            ap = row[self.measurements.key(p, s)][form]['appearance']
            out.append([[ap['ar_chroma'][k] for k in ('p50', 'p90', 'mean')], [ap['photo_chroma'][k] for k in ('p50', 'p90', 'mean')]])
        return out

    def hue_terms(self, row: dict, pair, form: str=AL.TARGET) -> dict:
        """A pair's hue median, actual graded foreign-hue window, reference model and verdict."""
        ap = row[self.measurements.key(*pair)][form]['appearance']
        h = ap.get('hue') or {}
        if not h:
            return {}
        return {'p50': [h['ar']['p50'], h['photo']['p50']],
                'foreign_hue_window': h['foreign_window'],
                'reference': h['reference'],
                'pass': bool(ap['criteria'].get('hue_p50') and ap['criteria'].get('hue_magenta'))}

    def grade(self, cap: Path, job, probe: Path, photos: dict) -> dict:
        """``ar_levels`` rows of one capture, with its residuals per role (``print`` form, and every form)."""
        m = self.measurements.measure([cap], job=job, probes={str(cap): str(probe)}, photos=photos)
        row = m['captures'][str(cap)]
        roles = {'calibration': m['pairs'], 'trade-off': m['tradeoff'], 'held-out': m['heldout']}
        contrast = {r: self.measurements.contrast_ratios(row, self.measurements.TARGET, [tuple(p) for p in ps]) for r, ps in roles.items()}
        res = {r: self.measurements.residuals(row, self.measurements.TARGET, [tuple(p) for p in ps]) for r, ps in roles.items()}
        res['appearance'] = self.appearance_residuals(res['calibration'], contrast['calibration'])
        return {'capture': str(cap), 'pairs': roles, 'rows': row, 'chroma': {r: self.measurements.chroma_residuals(row, self.measurements.TARGET, [tuple(p) for p in ps]) for r, ps in roles.items()}, 'chroma_units': {r: self.measurements.chroma_units(row, self.measurements.TARGET, [tuple(p) for p in ps]) for r, ps in roles.items()}, 'chroma_stats': {r: self.chroma_stats(row, [tuple(p) for p in ps]) for r, ps in roles.items()}, 'contrast': contrast, 'brow_residuals': {r: self.measurements.group_residuals(row, 'brow', [tuple(p) for p in ps]) for r, ps in roles.items()}, 'temple_residuals': {r: self.measurements.group_residuals(row, 'temple', [tuple(p) for p in ps]) for r, ps in roles.items()}, 'brow_chroma_units': {r: self.measurements.group_chroma_units(row, 'brow', [tuple(p) for p in ps]) for r, ps in roles.items()}, 'hue': {r: [self.hue_terms(row, tuple(p)) for p in ps] for r, ps in roles.items()}, 'brow_pass_by_role': row.get('pass_brow'), 'residuals': res, 'residuals_by_form': {r: {f: self.measurements.residuals(row, f, [tuple(p) for p in ps]) for f in self.measurements.FORMS} for r, ps in roles.items()}, 'pass': bool(row['pass']['calibration'][self.measurements.TARGET]), 'pass_by_role': row['pass'], 'view_dependence': row['view_dependence']}

    def evaluate(self, bake: Path, glb: Path, work: Path, level: dict, tag: str, job, probe: Path, photos: dict, log) -> dict:
        work.mkdir(parents=True, exist_ok=True)
        g = work / f'cand_{tag}_TOOLTEST.glb'
        info = self.swap_image(glb, g, self.levelled_png(bake, level))
        cap = self.capture(g, work / f'cap_{tag}', log)
        return {'tag': tag, 'level': level, 'glb': str(g), 'png_sha256': info['png_sha256']} | self.grade(cap, job, probe, photos)

    def worst(self, e: dict, role: str='calibration', idx=None) -> float:
        r = e['residuals'][role]
        return max((abs(r[i]) for i in (range(len(r)) if idx is None else idx)))

    def best(self, its: list, role: str='calibration', idx=None) -> dict:
        return its[int(np.argmin([self.worst(e, role, idx) for e in its]))]

    def pair_index(self, pairs: list, keep) -> list[int]:
        """Residual indices (2 per pair: p10, p50) of the pairs in ``keep``."""
        return [2 * i + q for i, p in enumerate(pairs) if tuple(p) in set(map(tuple, keep)) for q in (0, 1)]

    def _uv(self, level: dict) -> np.ndarray:
        return np.array([math.log(level['gain']) / LOG_UNIT, level['gamma'] / GAMMA_UNIT])

    def _level(self, uv) -> dict:
        return {'gain': float(math.exp(uv[0] * LOG_UNIT)), 'gamma': float(uv[1] * GAMMA_UNIT)}

    def search_box(self, grid=GRID) -> dict:
        """The coarse grid's box extended by one grid step (``LOG_UNIT`` in log gain, ``GAMMA_UNIT`` in gamma): the
        search refines between and just beyond the grid points, never along a ridge to an extreme level."""
        lg = [math.log(g) for g in grid[0]]
        return {'gain': (math.exp(min(lg) - LOG_UNIT), math.exp(max(lg) + LOG_UNIT)), 'gamma': (min(grid[1]) - GAMMA_UNIT, max(grid[1]) + GAMMA_UNIT)}

    def propose(self, its: list, role: str, radius: float, box: dict | None=None):
        """(level, predicted worst) of the next capture for ``role``: the minimax point, inside a box of ``radius`` grid
        steps around the best captured point and inside ``box`` (default ``search_box()``), of planes fitted to the role's
        residuals over the ``K_NEAR`` nearest captures (weighted 1 / (d + 0.25)); None when they do not span a plane."""
        box = box or self.search_box()
        b = self.best(its, role)
        c0 = self._uv(b['level'])
        UV = np.array([self._uv(e['level']) for e in its])
        d = np.linalg.norm(UV - c0, axis=1)
        near = np.argsort(d, kind='stable')[:K_NEAR]
        X = np.c_[np.ones(len(near)), UV[near]]
        if len(near) < 3 or np.linalg.matrix_rank(X) < 3:
            return None
        w = 1.0 / (d[near] + 0.25)
        R = np.array([its[i]['residuals'][role] for i in near], float)
        coef, *_ = np.linalg.lstsq(X * w[:, None], R * w[:, None], rcond=None)
        U, V = np.meshgrid(np.linspace(c0[0] - radius, c0[0] + radius, 81), np.linspace(c0[1] - radius, c0[1] + radius, 81))
        U, V = (U.ravel(), V.ravel())
        gain, gamma = (np.exp(U * LOG_UNIT), V * GAMMA_UNIT)
        eps = 1e-09
        ok = (gain >= box['gain'][0] - eps) & (gain <= box['gain'][1] + eps) & (gamma >= box['gamma'][0] - eps) & (gamma <= box['gamma'][1] + eps)
        if not ok.any():
            return None
        wst = np.abs(np.c_[np.ones(U.size), U, V] @ coef).max(1)
        wst[~ok] = np.inf
        j = int(np.argmin(wst))
        return (self._level((U[j], V[j])), float(wst[j]))

    def search(self, its: list, f, role: str, max_iter: int, tag: str, log=print, box: dict | None=None) -> dict:
        """Trust-region minimax for ``role`` (method step 5); ``f(gain, gamma, tag)`` captures one level and appends it to
        ``its``. Returns the trail and why it stopped."""
        radius, steps, trail, stop = (1.0, 0, [], 'max_iter')
        while True:
            b = self.best(its, role)
            bw = self.worst(b, role)
            if bw <= STOP_TOL:
                stop = f'worst {bw:.2f} within STOP_TOL'
                break
            if radius < MIN_RADIUS:
                stop = f'trust box {radius:.3f} below MIN_RADIUS'
                break
            if steps >= max_iter:
                break
            prop = self.propose(its, role, radius, box)
            if prop is None:
                stop = 'the captures do not span a plane'
                break
            lv, pw = prop
            if min((float(np.linalg.norm(self._uv(lv) - self._uv(e['level']))) for e in its)) < 0.02:
                radius *= 0.5
                continue
            steps += 1
            log(f"[{role}] step {steps}: box {radius:.3f}, model predicts worst {pw:.2f} at gain {lv['gain']:.4f} gamma {lv['gamma']:.4f} (best so far {bw:.2f}, {b['tag']})")
            e = f(lv['gain'], lv['gamma'], f'{tag}{steps}')
            improved = self.worst(e, role) < bw - 0.01
            trail.append({'tag': e['tag'], 'level': lv, 'radius': round(radius, 4), 'predicted_worst': round(pw, 3), 'worst': round(self.worst(e, role), 3), 'improved': bool(improved)})
            radius = min(1.0, radius * 1.5) if improved else radius * 0.5
        b = self.best(its, role)
        return {'role': role, 'steps': steps, 'stop': stop, 'trail': trail, 'best': b['tag'], 'worst': round(self.worst(b, role), 3)}

    def _fmt_level(self, lv: dict) -> str:
        return f"gain {lv['gain']:.2f}, gamma {lv['gamma']:.2f}" + (f", sat {lv['sat']:.2f}" if 'sat' in lv else '') + (f", chroma power {lv['chroma_power']:.2f}" if 'chroma_power' in lv else '') + (', chroma held >= 1' if lv.get('chroma_hold') else '') + (f", hue band {lv['chroma_hue'][0]:g}-{lv['chroma_hue'][1]:g} deg" if lv.get('chroma_hue') else '')

    def chroma_worst(self, e: dict, role: str='calibration') -> float:
        """The worst |C*ab p50 difference| of a capture (step 6's chroma verdict alone)."""
        return max((abs(x) for x in e['chroma'][role]))

    def _roles(self, role) -> tuple:
        return (role,) if isinstance(role, str) else tuple(role)

    def chroma_units_worst(self, e: dict, role='calibration') -> float:
        """The worst chroma unit (C* p50, p90, mean, each in its own window) of a capture over ``role`` (a role or a tuple
        of roles, e.g. ``ALL_GRADED``): <= 1 passes."""
        return max((abs(x) for r in self._roles(role) for x in e['chroma_units'][r]))

    def chroma_level(self, level: dict) -> tuple[float, float]:
        return (float(level.get('sat', 1.0)), float(level.get('chroma_power', 1.0)))

    def with_chroma(self, gain: float, gamma: float, sat: float=1.0, power: float=1.0, family: dict | None=None) -> dict:
        """The level dict: sat and chroma_power written only when they differ from the step-5 map's (1, 1), and with them
        the chroma family's options (``family``, default ``CHROMA_FAMILY``: held at >= 1, the havana hue band; ``{}`` =
        fix round 1's plain power)."""
        lv = {'gain': float(gain), 'gamma': float(gamma)}
        if sat != 1.0 or power != 1.0:
            lv['sat'] = float(sat)
            lv.update(self.CHROMA_FAMILY if family is None else family)
        if power != 1.0:
            lv['chroma_power'] = float(power)
        return lv

    def units_of(self, ar: np.ndarray, photo: np.ndarray) -> np.ndarray:
        """``print_metrics.chroma_units`` of predicted [p50, p90, mean] arrays (..., 3) against the photo's (..., 3)."""
        ar, photo = (np.asarray(ar, float), np.asarray(photo, float))
        u50 = (ar[..., :1] - photo[..., :1]) / self.measurements.PM.CHROMA_TOL
        r = np.log(np.maximum(ar[..., 1:], 1e-09) / photo[..., 1:])
        edge = np.where(r >= 0, math.log(self.measurements.PM.CHROMA_SPREAD_RATIO[1]), math.log(1.0 / self.measurements.PM.CHROMA_SPREAD_RATIO[0]))
        return np.concatenate([u50, r / edge], -1)

    def _cuv(self, level: dict) -> np.ndarray:
        s, p = self.chroma_level(level)
        return np.array([math.log(s) / CHROMA_UNIT_SAT, p / CHROMA_UNIT_POWER])

    def choose_chroma(self, its: list, role='calibration', sat_box=SAT_BOX, power_box=POWER_BOX):
        """((sat, power), predicted worst unit, model) of the chroma stage's next capture: ln of every AR C* statistic (per
        pair of ``role``, a role or a tuple of roles: p50, p90, mean) fitted by a plane in (ln sat, power) over the
        captures ``its`` (same gain and gamma), weighted 1 / (d + 0.25) around the best captured point (d in
        ``CHROMA_UNIT_*``), and the point of ``sat_box`` x ``power_box`` whose worst predicted unit (``units_of``) is
        smallest; None when the captures do not span a plane."""
        b = min(its, key=lambda e: self.chroma_units_worst(e, role))
        UV = np.array([self._cuv(e['level']) for e in its])
        X = np.c_[np.ones(len(its)), [math.log(self.chroma_level(e['level'])[0]) for e in its], [self.chroma_level(e['level'])[1] for e in its]]
        if len(its) < 3 or np.linalg.matrix_rank(X) < 3:
            return None
        d = np.linalg.norm(UV - self._cuv(b['level']), axis=1)
        w = 1.0 / (d + 0.25)
        st = [np.concatenate([np.asarray(e['chroma_stats'][r], float).reshape(-1, 2, 3) for r in self._roles(role)]) for e in its]
        A = np.array([np.log(np.maximum(x[:, 0, :], 1e-09)).ravel() for x in st])
        photo = st[0][:, 1, :]
        coef, *_ = np.linalg.lstsq(X * w[:, None], A * w[:, None], rcond=None)
        S, P = np.meshgrid(np.exp(np.linspace(math.log(sat_box[0]), math.log(sat_box[1]), 241)), np.linspace(power_box[0], power_box[1], 161))
        S, P = (S.ravel(), P.ravel())
        pred = np.exp(np.c_[np.ones(S.size), np.log(S), P] @ coef).reshape(S.size, *photo.shape)
        wst = np.abs(self.units_of(pred, photo[None])).reshape(S.size, -1).max(1)
        j = int(np.argmin(wst))
        return ((float(S[j]), float(P[j])), float(wst[j]), {'coef_ln_stat': [[round(float(v), 4) for v in c] for c in coef.T]})

    def _has_roles(self, e: dict, roles) -> bool:
        """Whether a capture carries chroma units and statistics for every role of ``roles``."""
        return all((e.get('chroma_units', {}).get(r) and e.get('chroma_stats', {}).get(r) for r in self._roles(roles)))

    def capture_probes(self, g, probes=CHROMA_PROBES) -> list:
        """Fix round 3 of step 6: the chroma probes are independent captures, so they run at the same time (one thread
        each: every capture is its own arview node process, opted out of EcoQoS, and ``grade`` shares the job's cached
        masks); the results come back in ``probes`` order whatever order they finish in. Step 6 to fix round 2 captured
        them one after another."""
        from concurrent.futures import ThreadPoolExecutor
        args = [(sv, pv, f'chroma{k + 1}') for k, (sv, pv) in enumerate(probes)]
        if len(args) <= 1:
            return [g(*a) for a in args]
        with ThreadPoolExecutor(max_workers=len(args)) as ex:
            return list(ex.map(lambda a: g(*a), args))

    def chroma_stage(self, base: dict, g, max_iter: int=CHROMA_MAX_ITER, log=print, roles='calibration', its=None, tag: str='chroma_s') -> dict:
        """Method step 7 (b): ``g(sat, power, tag)`` captures one chroma level at the base's (gain, gamma). The base plus
        ``CHROMA_PROBES`` (captured at the same time, ``capture_probes``, so ``g`` must be thread-safe; or the captures
        ``its`` of an earlier stage at the same (gain, gamma): fix round 2's all-pairs
        stage reuses the photo-angle stage's), then the model's minimax point over ``roles`` (``choose_chroma``) until
        the captured worst unit is within ``CHROMA_STOP``, the model points back at a captured point, or ``max_iter``
        captures. Returns the trail and the CAPTURED point with the smallest worst unit over ``roles`` (``chosen``;
        ``chosen_e`` is the capture itself), and every capture's worst unit over the calibration pairs and over
        ``ALL_GRADED`` beside each other (fix round 2: the calibration worst alone hid the other pairs)."""
        roles = self._roles(roles)
        probes = its is None
        its = [base] + self.capture_probes(g) if probes else list(its)
        worst_r = lambda e: self.chroma_units_worst(e, roles)
        trail, stop, steps = ([], 'max_iter', 0)
        while True:
            b = min(its, key=worst_r)
            bw = worst_r(b)
            if bw <= CHROMA_STOP:
                stop = f'worst chroma unit {bw:.2f} within CHROMA_STOP'
                break
            if steps >= max_iter:
                break
            prop = self.choose_chroma(its, roles)
            if prop is None:
                stop = 'the captures do not span a plane'
                break
            (sv, pv), pw, _model = prop
            if min((float(np.linalg.norm(self._cuv(self.with_chroma(1, 1, sv, pv)) - self._cuv(e['level']))) for e in its)) < 0.02:
                stop = 'the model points back at a captured point'
                break
            steps += 1
            log(f"[chroma {'+'.join(roles)}] step {steps}: model predicts worst unit {pw:.2f} at sat {sv:.4f} power {pv:.4f} (best so far {bw:.2f}, {b['tag']})")
            e = g(sv, pv, f'{tag}{steps}')
            its.append(e)
            trail.append({'tag': e['tag'], 'sat': round(sv, 4), 'power': round(pv, 4), 'predicted_worst': round(pw, 3), 'worst': round(worst_r(e), 3)})
        chosen = min(its, key=worst_r)
        final = self.choose_chroma(its, roles)
        return {'roles': list(roles), 'base': base['tag'], 'probes': [e['tag'] for e in its[1:1 + len(CHROMA_PROBES)]] if probes else None, 'trail': trail, 'stop': stop, 'steps': steps, 'captures': [e['tag'] for e in its], 'worst_units': {e['tag']: round(worst_r(e), 3) for e in its}, 'worst_units_calibration': {e['tag']: round(self.chroma_units_worst(e), 3) for e in its}, 'worst_units_all_graded': {e['tag']: round(self.chroma_units_worst(e, ALL_GRADED), 3) if self._has_roles(e, ALL_GRADED) else None for e in its}, 'model_at_end': final[2] if final else None, 'chosen': chosen['tag'], 'chosen_e': chosen, 'level': chosen['level'], 'its': its}

    def summary_of(self, e: dict) -> dict:
        return {'tag': e['tag'], 'level': e['level'], 'capture': e['capture'], 'glb': e['glb'], 'pairs': e['pairs'], 'chroma': e.get('chroma'), 'chroma_units': e.get('chroma_units'), 'chroma_stats': e.get('chroma_stats'), 'contrast': e.get('contrast'), 'chroma_worst': {r: round(self.chroma_worst(e, r), 2) if e['chroma'].get(r) else None for r in self.measurements.ROLES} if e.get('chroma') else None, 'chroma_units_worst': {r: round(self.chroma_units_worst(e, r), 2) if e['chroma_units'].get(r) else None for r in self.measurements.ROLES} | ({'all_graded': round(self.chroma_units_worst(e, ALL_GRADED), 2)} if self._has_roles(e, ALL_GRADED) else {}) if e.get('chroma_units') else None, 'brow_residuals': e.get('brow_residuals'), 'temple_residuals': e.get('temple_residuals'), 'brow_chroma_units': e.get('brow_chroma_units'), 'hue': e.get('hue'), 'brow_pass_by_role': e.get('brow_pass_by_role'), 'residuals': e['residuals'], 'worst': {r: round(self.worst(e, r), 2) if e['residuals'].get(r) else None for r in self.measurements.ROLES + ('appearance',)}, 'residuals_by_form': e['residuals_by_form'], 'view_dependence': e['view_dependence'], 'pass_by_role': e['pass_by_role']}

    def assemble_candidates(self, chosen: dict, near: dict, roles: dict, all_pairs: dict | None=None) -> dict:
        """The owner's candidates, by NAME: 'photo_angles' = ``chosen`` (the chroma stage's captured choice: the delivered
        level), 'near_18deg' = ``near`` (the trade-off best re-captured at that chroma) and (fix round 2 of step 6)
        'all_pairs_chroma' = ``all_pairs`` (the same (gain, gamma) with the chroma minimax over every graded pair; skipped
        when it is the delivered capture itself). Step 6's first run filed the near capture as delivered (a role-name
        check); this is the one place that decides it."""
        picks = {'photo_angles': chosen, 'near_18deg': near}
        cands = {}
        for name, role in TARGETS.items():
            e = picks[name]
            if e is None:
                continue
            cands[name] = self.summary_of(e) | {'over': roles[ROLE_PAIRS.get(role, role)], 'role': role, 'delivered': name == 'photo_angles', 'label': f"{LABELS[name]}: {self._fmt_level(e['level'])}"}
        if all_pairs is not None and all_pairs['tag'] != chosen['tag']:
            cands['all_pairs_chroma'] = self.summary_of(all_pairs) | {'over': [p for r in ALL_GRADED for p in roles[r]], 'role': 'chroma over every graded pair', 'delivered': False, 'label': f"{LABELS['all_pairs_chroma']}: {self._fmt_level(all_pairs['level'])}"}
        return cands

    def finish(self, base_its: list, f, roles: dict, log=print, max_iter: int=CHROMA_MAX_ITER) -> dict:
        """After the L* searches (pure: ``f(gain, gamma, tag, sat=, power=)`` captures one level): the chroma stage at
        the best ``appearance`` capture of ``base_its`` (the (1, 1) captures), the near-18 deg candidate (the best
        ``trade-off`` capture of ``base_its``) captured again at the chosen chroma, and the candidates."""
        base = self.best(base_its, 'appearance')
        nb = self.best(base_its, 'trade-off') if roles.get('trade-off') else None
        gb, gm = (base['level']['gain'], base['level']['gamma'])
        g = lambda sv, pv, tag: f(gb, gm, tag, sat=sv, power=pv)
        stage = self.chroma_stage(base, g, max_iter, log)
        chosen = stage.pop('chosen_e')
        its = stage.pop('its')
        allp = self.chroma_stage(base, g, max_iter, log, roles=ALL_GRADED, its=its, tag='chroma_all_s') if self._has_roles(base, ALL_GRADED) else None
        all_e = allp.pop('chosen_e') if allp else None
        if allp:
            allp.pop('its')
        sv, pv = self.chroma_level(chosen['level'])
        near = f(nb['level']['gain'], nb['level']['gamma'], 'near_chroma', sat=sv, power=pv) if nb else None
        stage['near_18deg_at_chroma'] = near['tag'] if near else None
        stage['near_18deg_base'] = nb['tag'] if nb else None
        stage['at_chosen'] = {'chroma_p50': chosen['chroma']['calibration'], 'chroma_units': chosen['chroma_units']['calibration'], 'chroma_units_by_role': {r: chosen['chroma_units'].get(r) for r in ALL_GRADED}, 'worst_unit_calibration': round(self.chroma_units_worst(chosen), 3), 'worst_unit_all_graded': round(self.chroma_units_worst(chosen, ALL_GRADED), 3) if self._has_roles(chosen, ALL_GRADED) else None, 'l_worst': round(self.worst(chosen), 2), 'appearance_worst': round(self.worst(chosen, 'appearance'), 2), 'l_worst_at_base': round(self.worst(base), 2), 'appearance_worst_at_base': round(self.worst(base, 'appearance'), 2)}
        if allp:
            allp['at_chosen'] = {'chroma_units_by_role': {r: all_e['chroma_units'].get(r) for r in ALL_GRADED}, 'worst_unit_calibration': round(self.chroma_units_worst(all_e), 3), 'worst_unit_all_graded': round(self.chroma_units_worst(all_e, ALL_GRADED), 3), 'l_worst': round(self.worst(all_e), 2)}
        stage['all_graded'] = allp
        return {'chosen': chosen, 'near': near, 'all_pairs': all_e, 'chroma_stage': stage, 'candidates': self.assemble_candidates(chosen, near, roles, all_e)}

    def brows_alone(self, base_its: list, chosen: dict) -> dict:
        """Fix round 2 of step 6: the brows alone (print form, ``ar_levels.by_group``) at the calibration pairs: the
        delivered level's residuals and worst, and the CAPTURED (sat 1, power 1) level whose brow worst is smallest, with
        its pooled and temple residuals (reported, never chosen: the pooled print form stays the target)."""

        def bw(e):
            r = [abs(x) for x in (e.get('brow_residuals') or {}).get('calibration') or [] if x is not None]
            return max(r) if r else float('inf')
        b = min(base_its, key=bw)
        return {'delivered': {'brow_residuals': chosen.get('brow_residuals', {}).get('calibration'), 'worst': round(bw(chosen), 2), 'pass': bool(bw(chosen) <= L_TOL)}, 'best_captured': {'tag': b['tag'], 'level': b['level'], 'worst': round(bw(b), 2), 'pass': bool(bw(b) <= L_TOL), 'brow_residuals': b.get('brow_residuals', {}).get('calibration'), 'pooled_residuals': b['residuals']['calibration'], 'temple_residuals': (b.get('temple_residuals') or {}).get('calibration')}}

    def candidate_portraits(self, cands: dict, work: Path, job, probe: Path, photos: dict, log) -> None:
        """Each candidate's GLB captured again WITH the worn portrait (``sheet_capture``) for the owner sheet; its graded
        numbers must equal the calibration capture's (``sheet_capture_same_numbers``)."""
        done = {}
        for name, c in cands.items():
            if c['tag'] not in done:
                cap = self.capture(Path(c['glb']), work / f"sheet_{c['tag']}", log, portrait=True)
                g = self.grade(cap, job, probe, photos)
                done[c['tag']] = (str(cap), g['residuals'] == c['residuals'])
            c['sheet_capture'], c['sheet_capture_same_numbers'] = done[c['tag']]
            if not c['sheet_capture_same_numbers']:
                log(f"WARNING: {name}'s portrait capture grades differently from its calibration capture")

    def calibrate(self, bake: Path, glb: Path, work: Path, grid=GRID, max_iter: int=6, log=print, portraits: bool=True) -> dict:
        job = self.job
        table = self.measurements.pair_table(job)
        if not any(r['role'] == 'photo_angle' for r in table):
            raise ValueError('no photo within the calibration direction window; supply a measured level explicitly')
        photos = {}
        work.mkdir(parents=True, exist_ok=True)
        pg = work / 'probe_black_TOOLTEST.glb'
        probe_info = self.black_probe(glb, pg)
        probe = self.capture(pg, work / 'probe', log)
        its = []
        by_chroma = {}
        lock = threading.Lock()

        def f(gain, gamma, tag, sat=1.0, power=1.0):
            lv = self.with_chroma(gain, gamma, float(sat), float(power))
            e = self.evaluate(bake, glb, work, lv, tag, job, probe, photos, log)
            with lock:
                its.append(e)
                by_chroma.setdefault((round(float(sat), 6), round(float(power), 6)), []).append(e)
                r = e['residuals']
                log(f"[{tag}] gain {gain:.4f} gamma {gamma:.4f} sat {sat:.3f} power {power:.3f} -> {self.measurements.TARGET} calibration {r['calibration']} worst {self.worst(e):.2f} pass {e['pass']} | chroma p50 {e['chroma']['calibration']} units {e['chroma_units']['calibration']} (all roles present: {self._has_roles(e, ALL_GRADED)}) | contrast {e['contrast']['calibration']} | trade-off {r['trade-off']} | held-out {r['held-out']} | brows alone {e['brow_residuals']['calibration']}")
                (work / 'calibration_partial.json').write_text(json.dumps({'bake': str(bake), 'glb_template': str(glb), 'iterations': [{k: v for k, v in x.items() if k != 'rows'} for x in its]}, indent=1), encoding='utf-8')
            return e
        for gm in grid[1]:
            for gn in grid[0]:
                f(gn, gm, f'g{gn:.3f}_{gm:.3f}')
        box = self.search_box(grid)
        base_its = by_chroma[1.0, 1.0]
        searches = {name: self.search(base_its, f, role, max_iter, f'{name}_s', log, box) for name, role in TARGETS.items() if base_its[0]['residuals'].get(role)}
        roles = its[0]['pairs']
        pairs = roles['calibration']
        fin = self.finish(base_its, f, roles, log)
        chosen, cands, chroma = (fin['chosen'], fin['candidates'], fin['chroma_stage'])
        loo = {}
        for p in pairs:
            rest = self.pair_index(pairs, [q for q in pairs if q != p])
            if not rest:
                loo[self.measurements.key(*p)] = {'status': 'unavailable', 'reason': 'no other calibration pair'}
                continue
            e = self.best(its, 'calibration', rest)
            own = self.pair_index(pairs, [p])
            loo[self.measurements.key(*p)] = {'level_without_it': e['level'], 'tag': e['tag'], 'residuals_on_it': [e['residuals']['calibration'][i] for i in own], 'worst_on_the_others': round(self.worst(e, 'calibration', rest), 2)}
        brows = self.brows_alone(base_its, chosen)
        if portraits:
            self.candidate_portraits(cands, work, job, probe, photos, log)
        return {'method': 'WP5 measured gain/gamma and chroma calibration; explicit job, groups and chroma family', 'bake': str(bake), 'glb_template': str(glb), 'target_form': self.measurements.TARGET, 'pair_table': table, 'pairs': pairs, 'tradeoff': roles['trade-off'], 'heldout': roles['held-out'], 'probe': {'glb': str(pg), 'capture': str(probe)} | probe_info, 'level': chosen['level'], 'residuals_p10_p50': chosen['residuals']['calibration'], 'chroma_residuals_p50': chosen['chroma']['calibration'], 'chroma_units': chosen['chroma_units']['calibration'], 'chroma_stats': chosen['chroma_stats']['calibration'], 'contrast_std_ratio': chosen['contrast']['calibration'], 'chroma_pass': bool(self.chroma_units_worst(chosen) <= 1.0), 'chroma_stage': chroma, 'worst': round(self.worst(chosen), 2), 'residuals': chosen['residuals'], 'residuals_by_form': chosen['residuals_by_form'], 'pass': chosen['pass'], 'pass_by_role': chosen['pass_by_role'], 'view_dependence': chosen['view_dependence'], 'chosen_capture': chosen['capture'], 'search': searches, 'leave_one_pair_out': loo, 'candidates': cands, 'iterations': its, 'stop_tol': STOP_TOL, 'grid': [list(g) for g in grid], 'search_box': box, 'chroma_boxes': {'sat': list(SAT_BOX), 'power': list(POWER_BOX), 'probes': [list(x) for x in CHROMA_PROBES], 'stop': CHROMA_STOP, 'max_iter': CHROMA_MAX_ITER, 'family': self.CHROMA_FAMILY}, 'chroma_units_all_graded_worst': round(self.chroma_units_worst(chosen, ALL_GRADED), 3) if self._has_roles(chosen, ALL_GRADED) else None, 'brow_residuals': chosen.get('brow_residuals'), 'brow_pass_by_role': chosen.get('brow_pass_by_role'), 'brows_alone': brows, 'hue': chosen.get('hue')}
