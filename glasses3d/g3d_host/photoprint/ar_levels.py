"""Job-specific AR/photo level measurement; no product names or fixture loading defaults.

Migrated from the WP5 AR-level instrument. See PORT_REVIEW for the independent parity proof.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
import numpy as np
from PIL import Image
from scipy import ndimage
from glasses3d.g3d_host import raster
from glasses3d.g3d_host.photoprint import inputs as IN
from . import metrics as PM
PHOTO_ERODE = 2
FORMS = ("frame", "matched", "print")
TARGET = "print"
MATCH_DEG = 20.0
LOOSE_DEG = 30.0
CLOSE_DEG = 5.0
ROLE_OF = {"photo_angle": "calibration", "near": "trade-off", "loose": "held-out"}
ROLES = ("calibration", "trade-off", "held-out")
POSE_ANGLES = ((0, 0), (30, 0), (-30, 0), (45, 0), (-45, 0), (0, 20), (0, -20), (30, 20), (30, -20), (-30, 20),
               (-30, -20))
MIN_PHOTO_GROUP_SHARE = 0.15
CACHE_KEY = "_cache"

class Measurements:
    """Explicit job and semantic part groups, isolated between concurrent jobs."""

    TARGET = TARGET
    FORMS = FORMS
    ROLES = ROLES
    PM = PM
    MIN_PHOTO_GROUP_SHARE = MIN_PHOTO_GROUP_SHARE

    def __init__(self, job, groups, *, temple_z=None, reference_point_mm=None):
        self.job = job
        self._sides = {}
        if not isinstance(groups, dict) or any(isinstance(v, str) or not v for v in groups.values()):
            raise ValueError("print groups must map labels to nonempty part lists")
        self.GROUPS = {str(k): tuple(v) for k, v in groups.items()}
        self.PRINT_PARTS = tuple(p for parts in self.GROUPS.values() for p in parts)
        if not self.PRINT_PARTS or len(set(self.PRINT_PARTS)) != len(self.PRINT_PARTS):
            raise ValueError("print groups must be nonempty and disjoint")
        missing = set(self.PRINT_PARTS) - set(job.mesh.names)
        if missing:
            raise ValueError(f'print groups contain parts absent from the job: {sorted(missing)}')
        self.AR_TEMPLE_Z = temple_z
        self.BRIDGE_MM = job.pivot_mm if reference_point_mm is None else reference_point_mm
        self._views_by_short = {IN.short_name(v): v for v in job.views}
        if len(self._views_by_short) != len(job.views):
            raise ValueError("photo short names are ambiguous")

    def view_name(self, short):
        try:
            return self._views_by_short[short]
        except KeyError as exc:
            raise ValueError(f"unknown calibration photo: {short}") from exc


    def pose_id(self, yaw: float, pitch: float) -> str:
        sg = lambda v: f"{('m' if v < 0 else 'p')}{abs(int(v))}"
        parts = ([f'yaw_{sg(yaw)}'] if yaw else []) + ([f'pitch_{sg(pitch)}'] if pitch else [])
        return '_'.join(parts) or 'front'

    def pose_direction(self, yaw: float, pitch: float) -> np.ndarray:
        """Unit direction from the glasses toward the camera, in the model frame, of an arview pose (module docstring)."""
        y, p = (math.radians(yaw), math.radians(pitch))
        return np.array([-math.sin(y), math.cos(y) * math.sin(p), math.cos(y) * math.cos(p)])

    def photo_direction(self, job, view: str) -> np.ndarray:
        t = np.asarray(raster.camera_basis(job.views[view].camera)[2], float)
        return t / np.linalg.norm(t)

    def pair_table(self, job) -> list[dict]:
        """Per arview pose: the nearest photo, the angle between the two camera directions, and its role (photo_angle /
        near / loose / None)."""
        views = sorted(job.views)
        rows = []
        for yaw, pitch in POSE_ANGLES:
            d = self.pose_direction(yaw, pitch)
            ang = {v: math.degrees(math.acos(float(np.clip(d @ self.photo_direction(job, v), -1, 1)))) for v in views}
            v = min(ang, key=ang.get)
            role = 'photo_angle' if ang[v] <= CLOSE_DEG else 'near' if ang[v] <= MATCH_DEG else 'loose' if ang[v] <= LOOSE_DEG else None
            rows.append({'pose': self.pose_id(yaw, pitch), 'photo': IN.short_name(v), 'angle_deg': round(ang[v], 1), 'role': role})
        return rows

    def auto_pairs(self, job, role: str) -> tuple:
        """The (pose, photo) pairs of a ``pair_table`` role (photo_angle / near / loose)."""
        if role not in ROLE_OF:
            raise ValueError(f'unknown pair role {role!r} (one of {sorted(ROLE_OF)})')
        return tuple(((r['pose'], r['photo']) for r in self.pair_table(job) if r['role'] == role))

    def _occ(self, job):
        from glasses3d.g3d_host.photoprint import project as PR
        from glasses3d.g3d_host.photoprint import texels as TX
        return TX.OcclusionScene(job, job.part_ids(self.PRINT_PARTS), TX.decal_geometry(PR.job_glb(job), job.mesh.skipped))

    def photo_frame_mask(self, job, view: str, occ=None, parts=None) -> np.ndarray:
        """(H, W) bool: the photo's pixels of ``parts`` (first hit at the WP3b camera, eroded) inside its matte (eroded)."""
        parts = self.PRINT_PARTS if parts is None else parts
        v = job.views[view]
        occ = occ or self._occ(job)
        fid = occ.scene(job.mesh.V).render(v.camera, v.shape, 1)['face_id']
        nf = len(job.mesh.F)
        part = np.where((fid >= 0) & (fid < nf), job.mesh.part[np.clip(fid, 0, nf - 1)], -1)
        pr = ndimage.binary_erosion(np.isin(part, job.part_ids(list(parts))), iterations=PHOTO_ERODE)
        return pr & ndimage.binary_erosion(np.asarray(v.matte, bool), iterations=PHOTO_ERODE)

    def _cached(self, cache: dict | None, key, make):
        """``cache[key]``, made by ``make()`` the first time (no cache: made every time)."""
        if cache is None:
            return make()
        if key not in cache:
            cache[key] = make()
        return cache[key]

    def photo_mask(self, job, view: str, parts, cache: dict | None=None) -> np.ndarray:
        """``photo_frame_mask`` of ``parts`` at ``view``, kept in ``cache`` (fix round 3 of step 6: the masks and the
        occlusion scene are constants of the job, and ``measure`` / ``band_energies`` rebuilt them for every row)."""
        occ = self._cached(cache, 'occ', lambda: self._occ(job))
        return self._cached(cache, ('photo_mask', view, tuple(parts)), lambda: self.photo_frame_mask(job, view, occ, parts))

    def photo_highlights(self, job, view: str, cache: dict | None=None) -> np.ndarray:
        """(H, W) bool: the photo's own achromatic highlights (``print_metrics.achromatic_highlight``), in ``cache``."""

        def make():
            img = np.asarray(job.views[view].image)
            return PM.achromatic_highlight(img.reshape(-1, 3)).reshape(img.shape[:2])
        return self._cached(cache, ('photo_highlights', view), make)

    def photo_pixels(self, job, view: str, occ=None, cache: dict | None=None):
        """(pixels (N, 3), group label (N,)) of the photo's frame pixels, brows 0 / temples 1."""
        v = job.views[view]
        cache = {} if cache is None else cache
        if occ is not None:
            cache.setdefault('occ', occ)
        px, gr = ([], [])
        for k, (_g, parts) in enumerate(self.GROUPS.items()):
            m = self.photo_mask(job, view, parts, cache)
            px.append(v.image[m])
            gr.append(np.full(int(m.sum()), k))
        return (np.concatenate(px), np.concatenate(gr))

    def photo_group_shares(self, pp, gp) -> dict:
        """Fix round 3 of step 6: per group, its share of the photo's print-form pixels (the photo's own achromatic
        highlights left out, as the ``print`` form does). Constants of the job: they depend on the photo and its masks
        only, never on a print or a capture."""
        pp = np.asarray(pp, np.float64).reshape(-1, 3)
        gp = np.asarray(gp)
        keep = ~PM.achromatic_highlight(pp)
        n = max(int(keep.sum()), 1)
        return {g: round(float((keep & (gp == k)).sum() / n), 4) for k, g in enumerate(self.GROUPS)}

    def excluded_groups(self, shares: dict, min_share: float=MIN_PHOTO_GROUP_SHARE) -> dict:
        """{group: reason} of the groups whose photo-side share is below ``min_share`` (reported, never graded)."""
        return {g: f'the photo shows it at {s:.3f} of its print-form pixels, below MIN_PHOTO_GROUP_SHARE {min_share:g}: reported, left out of every verdict and of the chroma fit' for g, s in shares.items() if s < min_share}

    def part_points(self, job, cache: dict | None=None) -> dict:
        """Fix round 3 of step 6: per print part, the point its px/mm is measured at (``band_energies``): the area-weighted
        centroid of the part's faces, the temples' over their AR-visible stretch ``AR_TEMPLE_Z`` only (model mm)."""

        def make():
            V, F = (job.mesh.V, job.mesh.F)
            T3 = V[F]
            area = 0.5 * np.linalg.norm(np.cross(T3[:, 1] - T3[:, 0], T3[:, 2] - T3[:, 0]), axis=1)
            cen = T3.mean(1)
            out = {}
            for part in self.PRINT_PARTS:
                m = np.isin(job.mesh.part, job.part_ids([part]))
                if part in self.GROUPS.get('temple', ()) and self.AR_TEMPLE_Z is not None:
                    m &= (cen[:, 2] >= self.AR_TEMPLE_Z[0]) & (cen[:, 2] <= self.AR_TEMPLE_Z[1])
                w = area[m]
                out[part] = tuple((round(float(x), 3) for x in (cen[m] * w[:, None]).sum(0) / max(float(w.sum()), 1e-12)))
            return out
        return self._cached(cache, 'part_points', make)

    def part_sides(self, job) -> dict:
        """Fix round 3 of step 6: {'-x': (brow, temple, -1), '+x': (brow, temple, 1)}: each print part's side by the x sign
        of its area-weighted centroid (``part_points``), never by its name (the house rule; q001 names its parts by the
        viewer's side, which happens to agree); raises unless each side has exactly one brow and one temple. Which parts
        are brows or temples is the part class (``GROUPS``)."""
        hit = self._sides.get(id(job))
        if hit is not None and hit[0] is job:
            return hit[1]
        pts = self.part_points(job)
        out = {}
        for sg, name in ((-1, '-x'), (1, '+x')):
            b = [p for p in self.GROUPS.get('brow', ()) if np.sign(pts[p][0]) == sg]
            t = [p for p in self.GROUPS.get('temple', ()) if np.sign(pts[p][0]) == sg]
            if len(b) != 1 or len(t) != 1:
                raise ValueError(f'side {name}: brows {b} and temples {t} by the x sign of their centroids {pts}; expected exactly one of each')
            out[name] = (b[0], t[0], sg)
        self._sides[id(job)] = (job, out)
        return out

    def _masks(self, cap: Path, pose: str):
        rep = json.loads((cap / 'report.json').read_text(encoding='utf-8'))
        pid = np.asarray(Image.open(cap / f'{pose}__part-id.png').convert('RGB'))
        return (pid, [PM.ar_frame_mask(pid, rep['id_palette'], parts) for parts in self.GROUPS.values()])

    def capture_pixels(self, cap: Path, pose: str, probe: Path | None=None):
        """(pixels, group label, highlight flags or None) of a capture's frame pixels at ``pose``; the flags are the
        probe capture's achromatic highlights on the same pixels (its part-id render must equal the capture's)."""
        cap = Path(cap)
        pid, masks = self._masks(cap, pose)
        beauty = np.asarray(Image.open(cap / f'{pose}__skin__beauty.png').convert('RGB'))
        hl = None
        if probe is not None:
            ppid, pmasks = self._masks(Path(probe), pose)
            if not (np.array_equal(ppid, pid) and all((np.array_equal(a, b) for a, b in zip(masks, pmasks)))):
                raise ValueError(f"{probe}: part-id render at {pose} differs from {cap}'s: not a probe of the same GLB")
            pb = np.asarray(Image.open(Path(probe) / f'{pose}__skin__beauty.png').convert('RGB'))
            hl = np.concatenate([PM.achromatic_highlight(pb[m]) for m in masks])
        px = np.concatenate([beauty[m] for m in masks])
        gr = np.concatenate([np.full(int(m.sum()), k) for k, m in enumerate(masks)])
        return (px, gr, hl)

    def ar_groups(self, pa, ga, ha) -> dict:
        """Per group (brow / temple): share and L* p10 / p50 of the AR's print-form pixels (highlights left out)."""
        keep = ~(np.asarray(ha, bool) if ha is not None else PM.achromatic_highlight(pa))
        out = {}
        for k, g in enumerate(self.GROUPS):
            m = keep & (np.asarray(ga) == k)
            L = PM.lstar(pa[m]) if m.any() else np.zeros(0)
            out[g] = {'share': round(float(m.sum() / max(int(keep.sum()), 1)), 4), 'p10': round(float(np.percentile(L, 10)), 2) if L.size else None, 'p50': round(float(np.percentile(L, 50)), 2) if L.size else None}
        return out

    def capture_ppm(self, cap: Path, pose: str, at_mm=None, step_mm: float=1.0) -> float:
        """The capture's px per model mm at ``at_mm`` (default the front, above the bridge), through the pose's
        asset_to_world and projection: the largest singular value of the 2 x 3 image Jacobian (the scale of displacements
        parallel to the image plane; a span along x alone is foreshortened at yaw). The temples are spread by the runtime
        (a turn about the hinge the report does not carry), so at a temple point this is the rigid model's depth; the
        spread's own change of depth is not measured (the mid-temple point lies 60 mm behind the bridge,
        ``part_points``)."""
        at_mm = self.BRIDGE_MM if at_mm is None else at_mm
        rep = json.loads((Path(cap) / 'report.json').read_text(encoding='utf-8'))
        p = next((q for q in rep['poses'] if q['id'] == pose))
        M = np.asarray(p['asset_to_world'], float).reshape(4, 4).T
        P = np.asarray(p['camera']['projection'], float).reshape(4, 4).T
        W, H = (p['camera']['width'], p['camera']['height'])
        V = np.asarray(at_mm, float)[None] + np.vstack([np.zeros(3), np.eye(3) * step_mm])
        c = (P @ M @ np.c_[V / 1000.0, np.ones(len(V))].T).T
        xy = np.stack([(c[:, 0] / c[:, 3] + 1) / 2 * W, (1 - c[:, 1] / c[:, 3]) / 2 * H], 1)
        J = (xy[1:] - xy[0]).T / step_mm
        return float(np.linalg.svd(J, compute_uv=False)[0])

    def _pooled_energy(self, rows: list) -> float | None:
        """One group's band energy from its parts' (energy, pixels) rows: the pixel-weighted RMS (pooled variance of the
        zero-mean band image); None when no part has one."""
        rows = [(e, n) for e, n in rows if e is not None and n > 0]
        if not rows:
            return None
        return float(math.sqrt(sum((n * e * e for e, n in rows)) / sum((n for _e, n in rows))))

    def band_energies(self, job, cap: Path, pose: str, short: str, probe: Path | None, occ=None, cache: dict | None=None) -> dict:
        """Per group: patch contrast at 0.5-3 mm of the AR (print-form pixels: probe highlights out) and of the photo
        (its achromatic highlights out), and their ratio (step 6). Fix round 3 of step 6: each print part at its OWN px/mm
        (``part_points``: AR through the capture's projection, ``capture_ppm``; photo through its WP3b camera,
        ``inputs.ppm_local``), the group's energy the pixel-weighted RMS of its parts' (``_pooled_energy``). Step 6 measured
        both groups at the bridge's px/mm, which mis-scaled the temples' 0.5-3 mm band: in AR the mid-temple's px/mm is
        24-26 % below the bridge's at pitch +20 and the front, 34 % / 8 % below (far / near temple) at yaw -30 pitch +20,
        36 % below for the far temple and 2 % above for the near one at yaw +-45; in photo 03 the near temple's is 20 %
        above the pivot's (``data/wp5/step6_fixes/ar_levels_corrected.json``: ``ppm_ar`` against ``ppm_ar_bridge``)."""
        cache = {} if cache is None else cache
        if occ is not None:
            cache.setdefault('occ', occ)
        cap = Path(cap)
        rep = json.loads((cap / 'report.json').read_text(encoding='utf-8'))
        pid = np.asarray(Image.open(cap / f'{pose}__part-id.png').convert('RGB'))
        beauty = np.asarray(Image.open(cap / f'{pose}__skin__beauty.png').convert('RGB'))
        hl = np.zeros(pid.shape[:2], bool)
        if probe is not None:
            pb = np.asarray(Image.open(Path(probe) / f'{pose}__skin__beauty.png').convert('RGB'))
            hl = PM.achromatic_highlight(pb.reshape(-1, 3)).reshape(pb.shape[:2])
        view = self.view_name(short)
        v = job.views[view]
        img = np.asarray(v.image)
        phl = self.photo_highlights(job, view, cache)
        pts = self.part_points(job, cache)
        La = PM.lstar(beauty.astype(np.float64))
        Lp = self._cached(cache, ('photo_lstar', view), lambda: PM.lstar(img.astype(np.float64)))
        out = {'ppm_at': "each print part's own point (part_points: area-weighted centroid; temples over AR_TEMPLE_Z)", 'ppm_ar': {}, 'ppm_photo': {}, 'ppm_ar_bridge': round(self.capture_ppm(cap, pose), 3), 'ppm_photo_bridge': round(float(job.ppm(view)), 3), 'parts': {}}
        for g, parts in self.GROUPS.items():
            ra, rp = ([], [])
            for part in parts:
                ma = PM.ar_frame_mask(pid, rep['id_palette'], (part,)) & ~hl
                mp = self.photo_mask(job, view, (part,), cache) & ~phl
                ppm_a = self.capture_ppm(cap, pose, pts[part])
                ppm_p = float(IN.ppm_local(v.camera, job.frame, np.asarray(pts[part], float)[None])[0])
                ea, ep = (PM.band_energy(beauty, ma, ppm_a, L=La), PM.band_energy(img, mp, ppm_p, L=Lp))
                out['ppm_ar'][part], out['ppm_photo'][part] = (round(ppm_a, 3), round(ppm_p, 3))
                out['parts'][part] = {'ar': None if ea is None else round(ea, 3), 'ar_px': int(ma.sum()), 'photo': None if ep is None else round(ep, 3), 'photo_px': int(mp.sum())}
                ra.append((ea, int(ma.sum())))
                rp.append((ep, int(mp.sum())))
            ea, ep = (self._pooled_energy(ra), self._pooled_energy(rp))
            out[g] = {'ar': None if ea is None else round(ea, 3), 'photo': None if ep is None else round(ep, 3), 'ratio': None if ea is None or not ep else round(ea / ep, 3)}
        return out

    def by_group(self, pa, ga, ha, pp, gp, excluded: dict | None=None) -> dict:
        """Fix round 2 of step 6: ``level_compare`` of each group (brow / temple) alone in the print form (AR: ``ha`` left
        out, else the AR pixels' own achromatic test; photo: its achromatic pixels left out); a group the AR pose does not
        show (fewer than 30 pixels on either side) is reported as such, never graded. Fix round 3: ``excluded``
        ({group: reason}, ``excluded_groups``): a group the photo barely shows is computed and reported but carries
        ``graded`` False and the reason, so no verdict counts it (``pass_brow``, ``group_residuals``)."""
        pa, pp = (np.asarray(pa, np.float64), np.asarray(pp, np.float64))
        ga, gp = (np.asarray(ga), np.asarray(gp))
        excluded = excluded or {}
        out = {}
        for k, g in enumerate(self.GROUPS):
            ma, mp = (ga == k, gp == k)
            if ma.sum() < 30 or mp.sum() < 30:
                out[g] = {'pass': None, 'graded': False, 'reason': f'{int(ma.sum())} AR / {int(mp.sum())} photo pixels'}
                continue
            r = PM.level_compare(pa[ma], pp[mp], exclude_highlights=True, ar_highlight=None if ha is None else np.asarray(ha, bool)[ma])
            r['graded'] = g not in excluded
            if g in excluded:
                r['excluded'] = excluded[g]
            out[g] = r
        return out

    def compare(self, ar, photo, min_photo_share: float=MIN_PHOTO_GROUP_SHARE) -> dict:
        """The three forms of one pair, the AR's group shares and the groups alone. Fix round 3 of step 6: a group below
        ``min_photo_share`` of the photo's print-form pixels is left out of the two weighted forms on both sides
        (``groups_excluded``) and reported ungraded in ``by_group``; ``photo_weight`` is the factor each group's photo
        pixels get in the ``print`` form without that rule (AR share / photo share)."""
        (pa, ga, ha), (pp, gp) = (ar, photo)
        pa, pp = (np.asarray(pa, np.float64).reshape(-1, 3), np.asarray(pp, np.float64).reshape(-1, 3))
        ga, gp = (np.asarray(ga), np.asarray(gp))
        shares = self.photo_group_shares(pp, gp)
        excl = self.excluded_groups(shares, min_photo_share)
        gone = [k for k, g in enumerate(self.GROUPS) if g in excl]
        ka, kp = (~np.isin(ga, gone), ~np.isin(gp, gone))
        hk = None if ha is None else np.asarray(ha, bool).reshape(-1)[ka]
        arg = self.ar_groups(pa, ga, ha)
        return {'frame': PM.level_compare(pa, pp), 'matched': PM.level_compare(pa[ka], pp[kp], ar_group=ga[ka], photo_group=gp[kp]), 'print': PM.level_compare(pa[ka], pp[kp], ar_group=ga[ka], photo_group=gp[kp], exclude_highlights=True, ar_highlight=hk), 'ar_groups': arg, 'photo_group_share': shares, 'photo_weight': {g: round(arg[g]['share'] / shares[g], 3) if shares[g] > 0 else None for g in self.GROUPS}, 'min_photo_group_share': min_photo_share, 'groups_excluded': excl, 'by_group': self.by_group(pa, ga, ha, pp, gp, excl)}

    def pass_brow(self, rows: dict) -> dict:
        """Fix round 2 of step 6: per role, the brows alone (print form, ``by_group``) pass at every row of the role.
        A missing render fails the role. Fix round 3: a row whose brow group is not graded (the pose shows fewer than 30
        brow pixels, or the photo shows the brows below ``MIN_PHOTO_GROUP_SHARE``) is left out (its numbers stay in
        ``by_group``); a role where no row grades the brows is None, never a vacuous pass."""
        out = {}
        for role in ROLES:
            rr = [r for r in rows.values() if isinstance(r, dict) and r.get('role') == role]
            if any(('missing' in r for r in rr)):
                out[role] = False
                continue
            graded = [(r.get('by_group') or {}).get('brow') or {} for r in rr]
            graded = [b for b in graded if b.get('graded', 'pass' in b and b.get('pass') is not None)]
            out[role] = None if not graded else all((bool(b.get('pass')) for b in graded))
        return out

    def view_dependence(self, rows: dict) -> dict:
        """For each photo with a calibration pair: every other graded pose's residual minus the calibration pose's
        ([p10, p50] per form) and its AR brow / temple L* p50 minus the calibration pose's. The photo is the same, so in
        the ``frame`` and ``print`` forms this is the AR render's own change with the view (``matched`` re-weights the
        photo to each pose's shares)."""
        graded = {k: r for k, r in rows.items() if isinstance(r, dict) and r.get('role') in ROLES and ('missing' not in r)}
        out = {}
        for kc, rc in graded.items():
            if rc['role'] != 'calibration':
                continue
            pc, s = kc.split('_vs_', 1)
            for k, r in graded.items():
                p, s2 = k.split('_vs_', 1)
                if s2 != s or k == kc:
                    continue
                out[f'{p} - {pc} (vs {s})'] = {'role': r['role'], **{f: [round(r[f]['ar_minus_photo'][q] - rc[f]['ar_minus_photo'][q], 2) for q in ('p10', 'p50')] for f in FORMS}, 'ar_group_p50': {g: round(r['ar_groups'][g]['p50'] - rc['ar_groups'][g]['p50'], 2) if r['ar_groups'][g]['p50'] is not None and rc['ar_groups'][g]['p50'] is not None else None for g in self.GROUPS}}
        return out

    def key(self, pose: str, short: str) -> str:
        return f'{pose}_vs_{short}'

    def measure(self, captures, pairs=None, heldout=None, job=None, probes: dict | None=None, photos: dict | None=None, tradeoff=None, cache: dict | None=None, min_photo_share: float=MIN_PHOTO_GROUP_SHARE) -> dict:
        """Rows per capture and pair (``pairs``: role 'calibration', default the photo_angle pairs of ``pair_table``;
        ``tradeoff``: role 'trade-off', default its near pairs; ``heldout``: role 'held-out', default its loose pairs).
        ``probes``: {capture dir: probe dir} (str keys). ``photos``: a cache of ``photo_pixels`` by photo short name
        (filled here); fix round 3 of step 6: its key ``CACHE_KEY`` (or ``cache`` when given) also keeps the occlusion
        scene, the photo-side masks and highlight maps and the part points, constants of the job that were rebuilt for every
        row and every capture, so a caller that keeps ``photos`` across calls (``ar_calibrate``) builds them once.
        ``min_photo_share``: the photo-side group share below which a group is reported, never graded (``compare``; 0 =
        fix round 2's instrument)."""
        job = job or self.job
        if job is not self.job:
            raise ValueError('measurements belong to one job; construct a new Measurements for another job')
        table = self.pair_table(job)
        pairs = tuple(map(tuple, pairs)) if pairs is not None else self.auto_pairs(job, 'photo_angle')
        tradeoff = tuple(map(tuple, tradeoff)) if tradeoff is not None else self.auto_pairs(job, 'near')
        heldout = tuple(map(tuple, heldout)) if heldout is not None else self.auto_pairs(job, 'loose')
        angle = {(r['pose'], r['photo']): r['angle_deg'] for r in table}
        photos = {} if photos is None else photos
        cache = photos.setdefault(CACHE_KEY, {}) if cache is None else cache
        for _p, s in pairs + tradeoff + heldout:
            if s not in photos:
                photos[s] = self.photo_pixels(job, self.view_name(s), cache=cache)
        probes = {str(Path(k)): v for k, v in (probes or {}).items()}
        out = {'pairs': [list(p) for p in pairs], 'tradeoff': [list(p) for p in tradeoff], 'heldout': [list(p) for p in heldout], 'pair_table': table, 'close_deg': CLOSE_DEG, 'match_deg': MATCH_DEG, 'loose_deg': LOOSE_DEG, 'tol': PM.LEVEL_TOL, 'target_form': TARGET, 'groups': list(self.GROUPS), 'min_photo_group_share': min_photo_share, 'photo_frame_stats': {s: PM.level_stats(photos[s][0]) for _p, s in pairs + tradeoff + heldout}, 'photo_group_share': {s: self.photo_group_shares(*photos[s]) for _p, s in pairs + tradeoff + heldout}, 'captures': {}}
        for cap in captures:
            cap = Path(cap)
            probe = probes.get(str(cap))
            rows = {}
            for role, plist in (('calibration', pairs), ('trade-off', tradeoff), ('held-out', heldout)):
                for pose, short in plist:
                    if not (cap / f'{pose}__part-id.png').is_file():
                        rows[self.key(pose, short)] = {'role': role, 'missing': f'no {pose} render in {cap}'}
                        continue
                    r = self.compare(self.capture_pixels(cap, pose, Path(probe) if probe else None), photos[short], min_photo_share)
                    r['band_energy'] = self.band_energies(job, cap, pose, short, Path(probe) if probe else None, cache=cache)
                    r['role'] = role
                    r['angle_deg'] = angle.get((pose, short))
                    rows[self.key(pose, short)] = r
            vd = self.view_dependence(rows)
            rows['pass'] = {role: {f: (all(r.get(f, {}).get('pass', False) for r in rows.values() if r.get('role') == role) if any(r.get('role') == role for r in rows.values()) else None) for f in FORMS} for role in ROLES}
            rows['pass_brow'] = self.pass_brow(rows)
            rows['view_dependence'] = vd
            rows['probe'] = str(probe) if probe else None
            out['captures'][str(cap)] = rows
        return out

    def chroma_residuals(self, row: dict, form: str=TARGET, pairs=None) -> list[float]:
        """[C*ab p50 AR - photo] per pair (step 6), in ``pairs`` order (default: the row's calibration pairs)."""
        if pairs is None:
            pairs = [tuple(k.split('_vs_', 1)) for k, r in row.items() if isinstance(r, dict) and r.get('role') == 'calibration']
        return [row[self.key(p, s)][form]['appearance']['chroma_ar_minus_photo']['p50'] for p, s in pairs]

    def chroma_units(self, row: dict, form: str=TARGET, pairs=None) -> list[float]:
        """[C*ab p50, p90, mean] per pair in units of the instrument's own windows (``print_metrics.chroma_units``: |x| <= 1
        passes), in ``pairs`` order (fix round 1 of step 6)."""
        if pairs is None:
            pairs = [tuple(k.split('_vs_', 1)) for k, r in row.items() if isinstance(r, dict) and r.get('role') == 'calibration']
        return [u for p, s in pairs for u in row[self.key(p, s)][form]['appearance']['chroma_units']]

    def _pairs_of(self, row: dict, pairs=None, role: str='calibration'):
        if pairs is None:
            pairs = [tuple(k.split('_vs_', 1)) for k, r in row.items() if isinstance(r, dict) and r.get('role') == role]
        return pairs

    def _graded(self, g: dict) -> bool:
        """Whether a ``by_group`` block is graded (rows written before fix round 3 carry no flag: graded when computed)."""
        return bool(g.get('graded', 'ar_minus_photo' in g))

    def group_residuals(self, row: dict, group: str='brow', pairs=None) -> list:
        """Fix round 2 of step 6: [p10, p50] AR - photo of one group alone (print form, ``by_group``) per pair; None where
        the pose does not show the group or (fix round 3) the photo shows it below ``MIN_PHOTO_GROUP_SHARE``."""
        out = []
        for p, s in self._pairs_of(row, pairs):
            g = row[self.key(p, s)].get('by_group', {}).get(group, {})
            out += [g['ar_minus_photo'][q] if 'ar_minus_photo' in g and self._graded(g) else None for q in ('p10', 'p50')]
        return out

    def group_chroma_units(self, row: dict, group: str='brow', pairs=None) -> list:
        """Fix round 2 of step 6: [C* p50, p90, mean] units of one group alone per pair (None where not shown, or, fix
        round 3, not graded)."""
        out = []
        for p, s in self._pairs_of(row, pairs):
            g = row[self.key(p, s)].get('by_group', {}).get(group, {})
            out += list(g['appearance']['chroma_units']) if 'appearance' in g and self._graded(g) else [None] * 3
        return out

    def contrast_ratios(self, row: dict, form: str=TARGET, pairs=None) -> list[float]:
        """[AR / photo L* standard deviation] per pair (step 6)."""
        if pairs is None:
            pairs = [tuple(k.split('_vs_', 1)) for k, r in row.items() if isinstance(r, dict) and r.get('role') == 'calibration']
        return [row[self.key(p, s)][form]['appearance']['lstar_std_ratio'] for p, s in pairs]

    def residuals(self, row: dict, form: str=TARGET, pairs=None) -> list[float]:
        """[p10, p50] AR - photo per pair, in ``pairs`` order (default: the row's calibration pairs in insertion order)."""
        if pairs is None:
            pairs = [tuple(k.split('_vs_', 1)) for k, r in row.items() if isinstance(r, dict) and r.get('role') == 'calibration']
        return [row[self.key(p, s)][form]['ar_minus_photo'][q] for p, s in pairs for q in ('p10', 'p50')]

    def table(self, res: dict, names: dict | None=None) -> str:
        """A plain-text table: per capture and pair, AR - photo L* p10 / p50 in every form (and the highlight shares),
        then the view dependence (other pose minus the photo-angle pose, same photo)."""
        lines = []
        for cap, rows in res['captures'].items():
            lines.append(f"== {(names.get(cap, cap) if names else cap)}  (probe: {rows.get('probe')})")
            for k, r in rows.items():
                if not isinstance(r, dict) or 'role' not in r or 'missing' in r:
                    continue
                cells = [f"{f} {r[f]['ar_minus_photo']['p10']:+.2f}/{r[f]['ar_minus_photo']['p50']:+.2f}" for f in FORMS]
                hs = r['print'].get('highlight_share') or {}
                ap = r['print'].get('appearance') or {}
                be = r.get('band_energy') or {}
                lines.append(f"  {r['role']:<11} {k:<26} ({r['angle_deg']} deg)  " + '  '.join(cells) + f"  hl ar {hs.get('ar')} photo {hs.get('photo')}" + (f"  | print C* p50 {ap['ar_chroma']['p50']}/{ap['photo_chroma']['p50']} p90 {ap['ar_chroma']['p90']}/{ap['photo_chroma']['p90']} mean {ap['ar_chroma']['mean']}/{ap['photo_chroma']['mean']} std ratio {ap['lstar_std_ratio']}" if ap else '') + (f"  energy 0.5-3mm brow {be.get('brow', {}).get('ratio')} temple {be.get('temple', {}).get('ratio')}" if be else ''))
                hu = ap.get('hue') if ap else None
                if hu:
                    lines.append(f"  {'':<11} {'':<26} hue p50 {hu['ar']['p50']}/{hu['photo']['p50']} magenta {hu['ar']['magenta_share']}/{hu['photo']['magenta_share']} off 20-100 {hu['ar']['off_band_share']}/{hu['photo']['off_band_share']}  appearance pass {ap.get('pass')} {[k for k, v in ap.get('criteria', {}).items() if not v]}")
                if r.get('photo_group_share') is not None:
                    lines.append(f"  {'':<11} {'':<26} photo group share {r['photo_group_share']} weight {r.get('photo_weight')} excluded {sorted(r.get('groups_excluded') or {})}")
                for g, gr in (r.get('by_group') or {}).items():
                    if 'ar_minus_photo' not in gr:
                        lines.append(f"  {'':<11} {'':<26} {g}: {gr.get('reason')}")
                        continue
                    ga_ = gr['appearance']
                    lines.append(f"  {'':<11} {'':<26} {g} alone{('' if self._graded(gr) else ' (NOT GRADED)')}: L* {gr['ar_minus_photo']['p10']:+.2f}/{gr['ar_minus_photo']['p50']:+.2f} pass {gr['pass']} | C* p50 {ga_['ar_chroma']['p50']}/{ga_['photo_chroma']['p50']} p90 {ga_['ar_chroma']['p90']}/{ga_['photo_chroma']['p90']} mean {ga_['ar_chroma']['mean']}/{ga_['photo_chroma']['mean']} units {ga_['chroma_units']} std ratio {ga_['lstar_std_ratio']}")
            for k, v in (rows.get('view_dependence') or {}).items():
                cells = [f'{f} {v[f][0]:+.2f}/{v[f][1]:+.2f}' for f in FORMS]
                lines.append(f'  view dep.   {k:<40} ' + '  '.join(cells) + f"  AR p50 by group {v['ar_group_p50']}")
        return '\n'.join(lines)

    def _pairs_arg(self, s: str | None, job, role: str):
        if s is None or s == 'auto':
            return self.auto_pairs(job, role)
        if s == 'none':
            return ()
        return tuple((tuple(x.split(':')) for x in s.split(',')))

    def group_share_table(self, job, cache: dict | None=None) -> dict:
        """Fix round 3 of step 6, the data behind ``MIN_PHOTO_GROUP_SHARE``: per photo and group, its frame pixels, the
        photo's achromatic highlights among them, the print-form pixels and their share, each part's own (eroded) pixel
        count, and the median |cos| between the surface normal and the photo's camera direction (1 = face on, 0 = edge
        on). Constants of the job (no print, no capture)."""
        cache = {} if cache is None else cache
        out = {'min_photo_group_share': MIN_PHOTO_GROUP_SHARE, 'photos': {}}
        occ = self._cached(cache, 'occ', lambda: self._occ(job))
        nf = len(job.mesh.F)
        for view in sorted(job.views):
            v = job.views[view]
            t = np.asarray(raster.camera_basis(v.camera)[2], float)
            t = t / np.linalg.norm(t)
            fid = occ.scene(job.mesh.V).render(v.camera, v.shape, 1)['face_id']
            f = np.clip(fid, 0, nf - 1)
            hl = self.photo_highlights(job, view, cache)
            rows = {}
            for g, parts in self.GROUPS.items():
                m = self.photo_mask(job, view, parts, cache)
                cosv = np.abs(job.face_normals[f[m]] @ t)
                rows[g] = {'frame_px': int(m.sum()), 'achromatic_highlight_px': int((m & hl).sum()), 'print_form_px': int((m & ~hl).sum()), 'median_abs_cos_view': round(float(np.median(cosv)), 3) if m.any() else None, 'part_px': {p: int(self.photo_mask(job, view, (p,), cache).sum()) for p in parts}}
            n = max(sum((r['print_form_px'] for r in rows.values())), 1)
            for g, r in rows.items():
                r['share'] = round(r['print_form_px'] / n, 4)
                r['excluded'] = bool(r['share'] < MIN_PHOTO_GROUP_SHARE)
            out['photos'][IN.short_name(view)] = rows
        return out
