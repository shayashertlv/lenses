"""WP5 step 4 fix (host): the metal / achromatic-highlight colour model and the hardware margin (photo donors only).

Diagnosis (FT1119 bake 7ac7c8f, photo 02, the rear view): the per-pixel acetate test (``signals.acetate_mask``:
luminance < 170 and max - min > 25, or luminance < 90) passes the SHADED parts of the gold hinge (e.g. (139, 105, 80),
(170, 134, 99), (128, 104, 78)) and of the pad arms, and q001's hinge geometry (``Temple_hinge`` / ``Front_hinge``,
4-5 mm across) covers only the barrel's left end of the photo's hinge, whose plate extends about 5 mm further onto the
endpiece back (rendered there as Brow print). Those pixels were painted onto the endpiece backs and then copied by the
fill; the pad-arm rivet (a dark disc on the brow back, right at the projected end of q001's ``Pad_stalk``) and the
bright pad-arm flecks at the brows' nose-side ends came in the same way. Two rules make such pixels holes FOR THAT VIEW
(``texels.view_samples``; registration and step 0 keep their own masks):

- **Colour model** (``pigment_arm`` / ``pale_mask``; sampled > 8 mm from hardware, whose photo counterparts are
  bigger than the model's; a bin whose mode jumps > 0.08 over the previous one is highlight-dominated and takes the
  previous bin's arm; the threshold is capped at B/R 0.85): the havana's pigment colours form one arm in (luminance, B/R)
  per photo: above luminance 90 (where the acetate test's dark branch ends) the amber phase gets SATURATED as it gets
  brighter (B/R falls, 0.5 -> 0.38 on 02 and 03), while a white highlight adds equal amounts to every channel and gold
  reflects with B/R 0.55-0.8: both lie ABOVE the arm. The arm is fitted per photo on its own print pixels (rendered
  print, eroded 2 px, > 2 mm from any other part, acetate, not clipped), per 10-level luminance bin: its centre is the
  bin's MODE of B/R (robust to the highlight plume, which is sparser than the arm), its spread the MAD of the pixels
  BELOW the mode (the uncontaminated side). A pixel at luminance >= 90 is ``pale`` (metal or highlight colour) when its
  B/R exceeds the centre by more than max(3 spreads, 0.06). The fit adapts to each photo's colour grading: on 06 the
  honey phase is desaturated (B/R 0.55-0.62 at luminance 130-150) and stays print, while its glints (B/R > 0.85) go.
- **Hardware margin** (``hardware_faces``): pixels within ``HARDWARE_MARGIN_MM`` (2.5 mm at the view's px/mm at the
  front pivot) of a rendered SMALL hardware part (diagonal < 10 mm and at least 1 mm thick: hinges, pad mounts and
  stalks; 02's pad-arm rivet sits 1-3 mm from q001's stalk end) and within ``HARDWARE_MARGIN_LARGE_MM`` (1.0 mm) of
  any other (the bridge, the T-trim's 0.7 mm bar, arm and return, screws; 2.5 mm around the T-trim cost 10-15 % of
  photo 05's brow-front donors) are holes: a metallic (>= 0.5) part that is not print and not a lens and is
  small (3D bounding-box diagonal <= ``HARDWARE_MAX_DIAG_MM``: hinges, screws, pad mounts and stalks, the bridge, the
  T-trim; q001's eyewire, 60 mm, keeps the general 0.5 mm occluder margin, since a 1.5 mm band along it would remove
  the brow's whole lower edge), plus metallic decals (q001's tip medallion).
- The pale mask and every BRIGHT (luminance >= 150) non-acetate pixel inside the matte are dilated by
  ``EDGE_DILATE_MM`` (0.15 mm): the
  anti-aliased transition pixels between a highlight (or metal, or the lens) and the print are mixtures (1 px at the
  FT1119 photos' 4.8-8 px/mm; 0.2 mm made 2 px at 8 px/mm and cost 14 % of photo 02's donors).

- **Thin lines** (``thin_lines``, fix 4): glints along curved edges (thin, achromatic, brighter than their masked
  opening) anywhere, and dark outlines (thin, darker than their masked closing) within 1 mm of the silhouette.
- **Dark blobs** (``dark_blobs``): round near-black neutral blobs on print (02's pad-arm rivets, about 5 mm from q001's
  pad stalks, so no margin reaches them).
- Small acetate-test islands (<= 2 mm2) enclosed by the mask go with it (a hinge barrel's shaded interior).

**Correction round (2026-10-06, task item 2: brow-front coverage; measured on photo 05's view of the brow fronts,
per texel the first failing donor rule, numbers in PORT_REVIEW section 10):**
- the LARGE-hardware margin (1.0 mm around the T-trim and the bridge) is off (``HARDWARE_MARGIN_LARGE_MM`` = 0): it
  cost 4.7 % of the +x brow front, where the photo shows acetate print right up to the T; the occluder margin
  (photometric since this round, ``texels``) and the bicubic footprint (2.5 px from bright non-acetate pixels) keep
  the T's own pixels out. They did NOT keep its anti-aliased edge out (fix round 1 review: a column of bright outliers
  3.6 px beside the -x T's stroke, whose light edge pixels pass the acetate colour test): the footprint's 'edges'
  rule now also keeps 2.5 px from those light edge pixels (``texels.footprint_holes``). Small hardware (hinges, pad mounts and stalks, whose shaded
  gold passes the acetate test) keeps its 2.5 mm.
- thin lines are no longer dilated by a blanket pixel: a glint's 1-px neighbour joins it only if it is itself brighter
  than its masked opening by >= half the line contrast (its shoulder); dark lines keep the 1-px dilation. The blanket
  dilation tripled a 1-px glint and took dark print beside it (the flagged neighbours' luminance p10 was 28-42, darker
  than the ok print's median 50).

The tool's own numbers are reported (``ViewSamples.reasons``: 'pale', 'hardware_margin', 'edge_halo', 'thin_line',
'dark_blob', 'enclosed'); the proof measured contamination independently from the written print.png
(``tests/photoprint/step4_sheets``: metal-coloured share per zone near the hardware; deleted on 2026-10-13, see git tag
review-20261013-pre-cleanup).

WP5 brow-top diagnosis (2026-10-07): the colour arm also flags isolated pixels in smooth amber patches. Dilating
these seeds then filling them carves ragged holes around the retained honey cores. A small colour outlier alone is
therefore insufficient evidence for deletion. ``supported_pale`` retains the old mask near hardware, on coherent
areas, on neutral colours, or where a local achromatic light increment supports it. The existing physical scales
and contrast limits are reused; no product coordinates or photo names enter this rule. Step-4 diagnostic mode
retains the original mask. The report counts unsupported colour-only seeds before their halo can erase print.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

from . import signals as S

PALE_LUM_MIN = 90.0              # the acetate test's dark branch ends here; below it B/R is noise on near-black pixels
PALE_LUM_MAX = 170.0
ARM_BIN = 10.0
ARM_MIN_PX = 150
ARM_BR_STEP = 0.02
PALE_K = 3.0
ARM_JUMP = 0.08
PALE_MIN_MARGIN = 0.06
EDGE_DILATE_MM = 0.15
HARDWARE_MARGIN_MM = 2.5        # small hardware (diagonal < HARDWARE_SMALL_MM): hinges, screws, pad mounts / stalks
HARDWARE_MARGIN_LARGE_MM = 0.0  # larger hardware (the bridge, the T-trim): off since the correction round (was 1.0)
HARDWARE_SMALL_MM = 10.0
HARDWARE_THIN_MM = 1.0          # a small part thinner than this (the T-trim's 0.7 mm bar, a screw) gets the 1.0 mm margin
HARDWARE_MAX_DIAG_MM = 25.0
SAMPLE_ERODE_PX = 2
SAMPLE_AWAY_MM = 2.0
SAMPLE_AWAY_HARDWARE_MM = 8.0    # the photo's hardware is bigger than the model's (02's hinge plate: ~5 mm beyond q001's)
PALE_MAX_THRESHOLD = 0.85        # B/R above this at luminance >= 90 is a highlight (or silver) whatever the arm
HALO_LUM_MIN = 150.0             # non-acetate pixels this bright (highlights, metal, the lens) spread a halo
ENCLOSED_MAX_MM2 = 2.0
LINE_HALF_MM = 0.4               # thin lines: narrower than 2 x this survive a masked opening / closing
LINE_CONTRAST = 25.0             # a bright line: luminance >= 25 levels over its opening ...
LINE_ACHROMATIC = 0.6            # ... with a blue increase >= 0.6 x the red one (white light adds to every channel)
DARK_CONTRAST = 30.0             # a dark line: >= 30 levels under its closing ...
DARK_EDGE_MM = 1.0               # ... within 1 mm of the photo's silhouette or of another part (an outline, not print)
LINE_ELONGATION = 3.0            # components: sqrt of the coordinate-covariance eigenvalue ratio
LINE_MIN_MM = 1.0                # ... and at least this long (4 sigma along the major axis)
DARK_BLOB_CLOSE_MM = 1.0         # dark compact blobs (rivets, screw heads the model lacks): >= DARK_BLOB_CONTRAST
DARK_BLOB_CONTRAST = 20.0        # levels under the masked closing at this radius, ...
DARK_BLOB_MM2 = (0.4, 5.0)       # ... area in this range, ...
DARK_BLOB_ELONGATION = 2.0       # ... round (elongation < 2), ...
DARK_BLOB_RB = 10.0              # ... neutral (mean R - B < 10), ...
DARK_BLOB_LUM = 45.0             # ... near-black (mean luminance < 45) ...
DARK_BLOB_ON_PRINT = 0.9         # ... and lying on rendered print; dilated DARK_BLOB_DILATE_MM (the rivet's ring)
DARK_BLOB_DILATE_MM = 1.0        # the detected core is the rivet's dark centre; its ring and outline reach ~1.4 mm


def blue_ratio(img) -> np.ndarray:
    f = np.asarray(img, np.float64)
    return f[..., 2] / np.maximum(f[..., 0], 1.0)


def pigment_arm(image, print_px, other_px, matte, ppm: float, hardware_px=None) -> dict:
    """Per-photo pigment arm: {'centres' lum bin centres, 'centre' B/R mode, 'spread', 'threshold', 'counts',
    'fitted' (bins with >= ARM_MIN_PX samples)}; see the module docstring."""
    img = np.asarray(image, np.float64)
    sup = ndimage.binary_erosion(np.asarray(print_px, bool), iterations=SAMPLE_ERODE_PX)
    sup &= ndimage.binary_erosion(np.asarray(matte, bool), iterations=SAMPLE_ERODE_PX)
    if np.asarray(other_px, bool).any():
        sup &= ndimage.distance_transform_edt(~np.asarray(other_px, bool)) > SAMPLE_AWAY_MM * ppm
    if hardware_px is not None and np.asarray(hardware_px, bool).any():
        sup &= ndimage.distance_transform_edt(~np.asarray(hardware_px, bool)) > SAMPLE_AWAY_HARDWARE_MM * ppm
    sup &= S.acetate_mask(img) & ~(img >= 250).any(-1)
    lum = img.mean(-1)[sup]
    br = blue_ratio(img)[sup]
    edges = np.arange(PALE_LUM_MIN, PALE_LUM_MAX + 1e-9, ARM_BIN)
    centres = (edges[:-1] + edges[1:]) / 2
    cen = np.full(len(centres), np.nan)
    spr = np.full(len(centres), np.nan)
    cnt = np.zeros(len(centres), np.int64)
    hb = np.arange(0.0, 1.5 + ARM_BR_STEP, ARM_BR_STEP)
    for k in range(len(centres)):
        m = (lum >= edges[k]) & (lum < edges[k + 1])
        cnt[k] = int(m.sum())
        if cnt[k] < ARM_MIN_PX:
            continue
        x = br[m]
        h, _ = np.histogram(np.clip(x, 0, 1.5 - 1e-9), bins=hb)
        h = ndimage.uniform_filter1d(h.astype(float), 3, mode="nearest")
        mode = float((hb[int(np.argmax(h))] + hb[int(np.argmax(h)) + 1]) / 2)
        below = mode - x[x <= mode]
        s = float(1.4826 * np.median(below)) if len(below) >= 20 else ARM_BR_STEP
        cen[k], spr[k] = mode, max(s, ARM_BR_STEP / 2)
    fitted = np.isfinite(cen)
    # continuity prior: the pigment arm is smooth in luminance. A bin whose mode jumps above the previous fitted bin's
    # by more than ARM_JUMP is dominated by highlights (photo 02's bright bins are the temple inner faces' sheen
    # stripe: 1,092 sheen vs 69 amber pixels at luminance >= 110), so it takes the previous bin's centre and spread
    jumped = []
    last = None
    for k in range(len(centres)):
        if not fitted[k]:
            continue
        if last is not None and cen[k] > cen[last] + ARM_JUMP:
            cen[k], spr[k] = cen[last], spr[last]
            jumped.append(float(centres[k]))
        last = k
    thr = np.full(len(centres), np.nan)
    thr[fitted] = np.minimum(cen[fitted] + np.maximum(PALE_K * spr[fitted], PALE_MIN_MARGIN), PALE_MAX_THRESHOLD)
    if fitted.any():
        idx = np.nonzero(fitted)[0]
        for k in range(len(centres)):
            if not fitted[k]:
                thr[k] = thr[idx[np.argmin(np.abs(idx - k))]]          # nearest fitted bin
    else:
        thr[:] = PALE_MAX_THRESHOLD
    return {"centres": centres.tolist(), "centre": [None if not np.isfinite(c) else round(float(c), 4) for c in cen],
            "spread": [None if not np.isfinite(c) else round(float(c), 4) for c in spr],
            "threshold": [None if not np.isfinite(c) else round(float(c), 4) for c in thr],
            "counts": cnt.tolist(), "fitted": int(fitted.sum()), "samples": int(sup.sum()), "jumped_bins": jumped}


def pale_mask(image, arm: dict) -> np.ndarray:
    """Metal / achromatic-highlight colours: acetate-test pixels at luminance >= 90 whose B/R lies above the photo's
    pigment arm (``pigment_arm``). Without a fitted arm nothing is flagged."""
    img = np.asarray(image, np.float64)
    lum = img.mean(-1)
    thr = np.interp(lum, np.asarray(arm["centres"], float), np.asarray(arm["threshold"], float))
    return S.acetate_mask(img) & (lum >= PALE_LUM_MIN) & (blue_ratio(img) > thr)


def supported_pale(image, candidate, inside, hardware, ppm: float) -> np.ndarray:
    """Confirm small colour-only outliers before dilating them into photo holes.

    Keep candidates within the pigment fit's hardware exclusion distance (8 mm), in connected areas larger than
    the existing enclosed-island limit (2 mm2), or above the absolute neutral-colour limit. Otherwise require the
    same additive-white signature used for glint shoulders: a local luminance rise >= 12.5 and blue rise >= 0.6 of
    the red rise, measured over a 0.4 mm masked opening. All thresholds come from the existing donor rules.
    """
    img = np.asarray(image, float)
    M = np.asarray(inside, bool)
    cand = np.asarray(candidate, bool) & M
    hw = np.asarray(hardware, bool)
    near = (ndimage.distance_transform_edt(~hw) <= SAMPLE_AWAY_HARDWARE_MM * ppm
            if hw.any() else np.zeros_like(M))
    comp, _ = ndimage.label(cand, structure=np.ones((3, 3), bool))
    size = np.bincount(comp.ravel())
    size[0] = 0
    coherent = size[comp] > ENCLOSED_MAX_MM2 * ppm * ppm
    fp = _disk(max(1, int(round(LINE_HALF_MM * ppm))))
    L = img.mean(-1)
    dL = L - _masked_open(L, M, fp)
    dR = img[..., 0] - _masked_open(img[..., 0], M, fp)
    dB = img[..., 2] - _masked_open(img[..., 2], M, fp)
    light = (dL >= LINE_CONTRAST / 2) & (dB >= LINE_ACHROMATIC * np.maximum(dR, 1e-6))
    return cand & (near | coherent | (blue_ratio(img) > PALE_MAX_THRESHOLD) | light)


def hardware_faces(job, print_part_ids, decals: dict | None = None) -> np.ndarray:
    """(n_faces + n_decal_faces + 1,) uint8: 0 = not hardware, 1 = large hardware (diagonal >= HARDWARE_SMALL_MM),
    2 = small hardware (see the module docstring); the trailing entry is 0 (face id -1)."""
    from .. import glb as glbmod
    from .project import job_glb
    g = glbmod.load(job_glb(job))
    metallic = {}
    lens = {}
    from .. import lens_id
    for p in g.primitives:
        m = g.material(p.material_index) or {}
        pbr = m.get("pbrMetallicRoughness") or {}
        d, _w = lens_id.decide_explicit([("material", m.get("extras")), ("node", None)])
        if d is None:
            d = lens_id.decide_name(m.get("name"), p.node_name)
        metallic[p.node_name] = float(pbr.get("metallicFactor", 1.0))
        lens[p.node_name] = bool(d.is_lens) if d is not None else False
    nf = len(job.mesh.F)
    nd = 0 if decals is None else len(decals["F"])
    out = np.zeros(nf + nd + 1, np.uint8)
    pp = set(int(x) for x in print_part_ids)
    for i, nn in enumerate(job.mesh.node_names):
        if i in pp or lens.get(nn, False) or metallic.get(nn, 0.0) < 0.5:
            continue
        m = job.mesh.part == i
        if not m.any():
            continue
        V = job.mesh.V[np.unique(job.mesh.F[m])]
        ext = V.max(0) - V.min(0)
        diag = float(np.linalg.norm(ext))
        if diag <= HARDWARE_MAX_DIAG_MM:
            out[:nf][m] = 2 if (diag < HARDWARE_SMALL_MM and float(ext.min()) >= HARDWARE_THIN_MM) else 1
    if nd:
        for k, nm in enumerate(decals["names"]):
            if decals["metallic"][k] >= 0.5:
                out[nf:nf + nd][decals["prim"] == k] = 2
    return out


def _disk(r: int) -> np.ndarray:
    y, x = np.mgrid[-r:r + 1, -r:r + 1]
    return (x * x + y * y) <= r * r + 0.5


def _masked_open(x, M, fp):
    e = ndimage.grey_erosion(np.where(M, x, 1e6), footprint=fp)
    return ndimage.grey_dilation(np.where(M, e, -1e6), footprint=fp)


def _masked_close(x, M, fp):
    d = ndimage.grey_dilation(np.where(M, x, -1e6), footprint=fp)
    return ndimage.grey_erosion(np.where(M, d, 1e6), footprint=fp)


def _line_components(cand: np.ndarray, ppm: float) -> np.ndarray:
    """The connected components of ``cand`` that are LINES: elongation >= LINE_ELONGATION, length >= LINE_MIN_MM."""
    lab, n = ndimage.label(cand, structure=np.ones((3, 3), bool))
    if n == 0:
        return np.zeros_like(cand)
    idx = np.arange(1, n + 1)
    yy, xx = np.indices(cand.shape)
    cnt = ndimage.sum(cand, lab, idx)
    mx, my = ndimage.sum(xx, lab, idx) / cnt, ndimage.sum(yy, lab, idx) / cnt
    cxx = ndimage.sum(xx * xx, lab, idx) / cnt - mx ** 2 + 1 / 12
    cyy = ndimage.sum(yy * yy, lab, idx) / cnt - my ** 2 + 1 / 12
    cxy = ndimage.sum(xx * yy, lab, idx) / cnt - mx * my
    tr, det = cxx + cyy, cxx * cyy - cxy ** 2
    disc = np.sqrt(np.maximum(tr * tr / 4 - det, 0))
    l1, l2 = tr / 2 + disc, np.maximum(tr / 2 - disc, 1e-6)
    keep = idx[(np.sqrt(l1 / l2) >= LINE_ELONGATION) & (4 * np.sqrt(l1) >= LINE_MIN_MM * ppm)]
    return np.isin(lab, keep)


def _components(mask: np.ndarray):
    lab, n = ndimage.label(mask, structure=np.ones((3, 3), bool))
    idx = np.arange(1, n + 1)
    if not n:
        return lab, idx, {}
    yy, xx = np.indices(mask.shape)
    cnt = ndimage.sum(mask, lab, idx)
    mx, my = ndimage.sum(xx, lab, idx) / cnt, ndimage.sum(yy, lab, idx) / cnt
    cxx = ndimage.sum(xx * xx, lab, idx) / cnt - mx ** 2 + 1 / 12
    cyy = ndimage.sum(yy * yy, lab, idx) / cnt - my ** 2 + 1 / 12
    cxy = ndimage.sum(xx * yy, lab, idx) / cnt - mx * my
    tr, det = cxx + cyy, cxx * cyy - cxy ** 2
    disc = np.sqrt(np.maximum(tr * tr / 4 - det, 0))
    l1, l2 = tr / 2 + disc, np.maximum(tr / 2 - disc, 1e-6)
    return lab, idx, {"count": cnt, "mx": mx, "my": my, "elong": np.sqrt(l1 / l2), "l1": l1}


def dark_blobs(image, inside, print_px, ppm: float) -> dict:
    """Dark compact blobs ON print (step 4 fix 1): rivets and screw heads the photo shows where the model has no part.
    Diagnosis: photo 02's two pad-arm rivets (near-black neutral discs, (8, 4, 4)-(34, 29, 28), on the brow backs at
    px (636, 378) and (807, 377)) lie about 5 mm from q001's pad stalks, so no hardware margin reaches them, and their
    colour is the havana's own dark phase (R - B 4-7 against the dark havana's median 6-10), so no colour rule
    separates them; what does is SHAPE: a round near-black neutral blob >= 20 levels under its 1 mm masked closing,
    0.4-5 mm2, on rendered print. On the six FT1119 photos exactly these two components qualify (of 196-677 candidate
    components per photo). Returns {'mask' (dilated 1.0 mm: the rivet's ring and outer outline), 'blobs': [...]} (reported to
    Astra with the foreign blobs)."""
    img = np.asarray(image, np.float64)
    M = np.asarray(inside, bool)
    L = img.mean(-1)
    fp = _disk(max(2, int(round(DARK_BLOB_CLOSE_MM * ppm))))
    cand = M & ((_masked_close(L, M, fp) - L) >= DARK_BLOB_CONTRAST)
    lab, idx, st = _components(cand)
    mask = np.zeros(cand.shape, bool)
    if not len(idx):
        return {"mask": mask, "blobs": []}
    area = st["count"] / ppm ** 2
    rb = ndimage.mean(img[..., 0] - img[..., 2], lab, idx)
    ml = ndimage.mean(L, lab, idx)
    onp = ndimage.mean(np.asarray(print_px, float), lab, idx)
    keep = ((area >= DARK_BLOB_MM2[0]) & (area <= DARK_BLOB_MM2[1]) & (st["elong"] < DARK_BLOB_ELONGATION)
            & (rb < DARK_BLOB_RB) & (ml < DARK_BLOB_LUM) & (onp >= DARK_BLOB_ON_PRINT))
    sel = idx[keep]
    blobs = [{"kind": "dark (rivet or screw head)", "area_mm2": round(float(area[i - 1]), 2),
              "centre_px": [round(float(st["mx"][i - 1]), 1), round(float(st["my"][i - 1]), 1)],
              "mean_rgb": [int(round(x)) for x in img[lab == i].mean(0)]} for i in sel]
    if len(sel):
        mask = ndimage.binary_dilation(np.isin(lab, sel), iterations=max(1, int(round(DARK_BLOB_DILATE_MM * ppm))))
    return {"mask": mask & M, "blobs": blobs}


def thin_lines(image, inside, edge, ppm: float) -> dict:
    """Glints and outlines (step 4 fix 4): {'bright', 'dark'} masks of THIN lines in the photo, ``inside`` = the matte
    eroded 1 px, ``edge`` = pixels within DARK_EDGE_MM of the silhouette or of another part. A specular glint along a
    curved edge is a thin line brighter than its masked opening (radius LINE_HALF_MM) by >= 25 levels whose blue rises
    with its red (white light; an amber fleck raises red only); an outline is a thin line darker than its masked
    closing by >= 30 levels near an edge (inside the print, a thin dark gap between honey streaks is print and stays).
    Only line-shaped components count (elongation >= 3, length >= 1 mm): print dots and blotches are not lines.
    Diagnosis (FT1119 bake 7ac7c8f): the light lines along the curved edges sat below luminance 90 over the dark
    phase, where the pale rule does not look, so they were painted and copied along the strips by the fill."""
    img = np.asarray(image, np.float64)
    M = np.asarray(inside, bool)
    fp = _disk(max(1, int(round(LINE_HALF_MM * ppm))))
    L = img.mean(-1)
    tw = L - _masked_open(L, M, fp)
    dR = img[..., 0] - _masked_open(img[..., 0], M, fp)
    dB = img[..., 2] - _masked_open(img[..., 2], M, fp)
    bright = M & (tw >= LINE_CONTRAST) & (dB >= LINE_ACHROMATIC * np.maximum(dR, 1e-6))
    dark = M & np.asarray(edge, bool) & ((_masked_close(L, M, fp) - L) >= DARK_CONTRAST)
    shoulder = M & (tw >= LINE_CONTRAST / 2) & (dB >= LINE_ACHROMATIC * np.maximum(dR, 1e-6))
    return {"bright": _line_components(bright, ppm), "dark": _line_components(dark, ppm), "shoulder": shoulder}


def donor_holes(image, matte, face_id, is_print, is_hardware, ppm: float, *, arm=None, lines: bool = True,
                rules: str = "correction") -> dict:
    """Per-photo donor holes (image space): {'pale', 'hardware', 'halo', 'mask', 'arm'}. ``face_id`` = the first-hit
    face per pixel of the whole scene (-1 background), ``is_print`` / ``is_hardware`` per face id (trailing False)."""
    img = np.asarray(image, np.float64)
    fid = np.asarray(face_id)
    pr = (fid >= 0) & is_print[fid]
    other = (fid >= 0) & ~is_print[fid]
    hwk = np.where(fid >= 0, np.asarray(is_hardware)[np.maximum(fid, 0)], 0)
    hw = hwk > 0
    if arm is None:
        arm = pigment_arm(img, pr, other, matte, ppm)
    pale_candidate = pale_mask(img, arm)
    inside = ndimage.binary_erosion(np.asarray(matte, bool), iterations=1)
    pale = (pale_candidate if rules == "step4" else supported_pale(img, pale_candidate, inside, hw, ppm))
    non = inside & ~S.acetate_mask(img)
    lum = img.mean(-1)
    it = max(1, int(round(EDGE_DILATE_MM * ppm)))
    halo = ndimage.binary_dilation(pale | (non & (lum >= HALO_LUM_MIN)), iterations=it)
    hard = np.zeros(fid.shape, bool)
    if hw.any():
        small = hwk == 2
        if small.any():
            hard |= ndimage.distance_transform_edt(~small) <= HARDWARE_MARGIN_MM * ppm
        large = hwk == 1
        large_mm = 1.0 if rules == "step4" else HARDWARE_MARGIN_LARGE_MM
        if large.any() and large_mm > 0:
            hard |= ndimage.distance_transform_edt(~large) <= large_mm * ppm
    tl = {"bright": np.zeros(fid.shape, bool), "dark": np.zeros(fid.shape, bool), "shoulder": np.zeros(fid.shape, bool)}
    if lines:
        outside = ~np.asarray(matte, bool) | other
        edge = ndimage.distance_transform_edt(~outside) <= DARK_EDGE_MM * ppm
        tl = thin_lines(img, inside, edge, ppm)
    # correction round: a glint grows only into its own shoulder (neighbours >= half its contrast over their opening)
    if rules == "step4":                       # the step-4 blanket 1-px dilation (before/after measurements)
        line = ndimage.binary_dilation(tl["bright"] | tl["dark"], iterations=1) & inside
    else:
        line = (tl["bright"] | (ndimage.binary_dilation(tl["bright"], iterations=1) & tl["shoulder"])
                | ndimage.binary_dilation(tl["dark"], iterations=1)) & inside
    db = dark_blobs(img, inside, pr, ppm)
    mask = pale | halo | hard | line | db["mask"]
    # small acetate-test islands enclosed by the mask (a hinge barrel's shaded interior, a screw slot) go with it
    enclosed = ndimage.binary_fill_holes(mask) & ~mask
    if enclosed.any():
        lab, n = ndimage.label(enclosed)
        area = ndimage.sum(enclosed, lab, np.arange(1, n + 1))
        enclosed = np.isin(lab, np.nonzero(area <= ENCLOSED_MAX_MM2 * ppm * ppm)[0] + 1)
    mask |= enclosed
    return {"pale": pale, "pale_colour_only": pale_candidate & inside & ~pale,
            "hardware": hard, "halo": halo & ~pale & ~non, "lines": line & ~pale & ~hard,
            "dark_blob": db["mask"], "dark_blobs": db["blobs"],
            "bright_lines": tl["bright"], "dark_lines": tl["dark"],
            "enclosed": enclosed, "mask": mask, "arm": arm}
