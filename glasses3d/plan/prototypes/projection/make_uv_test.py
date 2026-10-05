"""Build a test 'UV map' for the owner's image-model experiment: the FT1119 brows and temples of quality-001 unrolled
into straight strips, every texel coloured from the best of the six reference images (all treated as real).
Deliberately NOT cleaned: view seams, baked highlights/shading, holes where no image sees the surface, the metal T.
Read-only on the repo; writes only into this folder."""
import sys, json, pickle, math
sys.path.insert(0, r"C:/Users/Shay/PycharmProjects/lenses/automation")
from pathlib import Path as _Path
_G3D = _Path(__file__).resolve().parents[3]          # glasses3d/
sys.path.insert(0, str(_Path(__file__).resolve().parent))
OLD = str(_G3D / "data/reference/projection") + "/"
sys.path.insert(0, OLD)
import numpy as np
from PIL import Image
from scipy import ndimage
from scipy.spatial import ConvexHull
from bsa import raster, cameras as bcam, texture as T
from bsa.core import NormFrame, px_per_mm
from glbload import load

HERE = str(_G3D / "data/reference/projection/rerun") + "/"
import os as _os; _os.makedirs(HERE, exist_ok=True)
J = r"C:/Users/Shay/PycharmProjects/lenses/automation/data/blender_agent/studio/jobs/d4b716a6afc445f89cbed8beddad4c37/images/"
PX_MM = 10.0          # strip resolution (the photos carry ~4.7 px/mm)

fits = json.load(open(OLD + "fits.json"))
frame = NormFrame(tuple(fits["frame"]["center"]), fits["frame"]["extent"])
doc, binc, prims = load(OLD + "q001.glb")
skip = ("inner_brand_print", "TOM_FORD", "medallion")
prims = [p for p in prims if not any(s in p["node"] for s in skip)]
Vs, Fs, part, off, names = [], [], [], 0, []
for i, p in enumerate(prims):
    Vs.append(p["V"] * 1000.0); Fs.append(p["F"] + off); off += len(p["V"])
    part.append(np.full(len(p["F"]), i)); names.append(p["node"].split(".")[0])
V = np.vstack(Vs); F = np.vstack(Fs); part = np.concatenate(part)
scene = raster.get_scene(V, F, frame)
front_names = ("Brow_Left", "Brow_Right", "Eyewire_Left", "Eyewire_Right", "Lens_Left", "Lens_Right", "Saddle_bridge")
fp = np.isin(part, [names.index(n) for n in front_names])
fpV = V[np.unique(F[fp])]
pivot = frame.to_norm(((fpV.min(0) + fpV.max(0)) / 2)[None])[0]


def photo(name):
    return np.asarray(Image.open(J + name).convert("RGB"))


def matte(img):
    """Foreground = differs from a quadratic background fitted to the image border (handles grey gradients)."""
    f = img.astype(float); H, W = f.shape[:2]
    b = np.zeros((H, W), bool); b[:10, :] = b[-10:, :] = b[:, :10] = b[:, -10:] = True
    yy, xx = np.mgrid[0:H, 0:W]; x, y = xx / W, yy / H
    X = np.stack([np.ones_like(x), x, y, x * x, y * y, x * y], -1).reshape(-1, 6)
    bg = np.zeros_like(f)
    for c in range(3):
        coef, *_ = np.linalg.lstsq(X[b.ravel()], f[..., c][b], rcond=None)
        bg[..., c] = (X @ coef).reshape(H, W)
    fg = np.abs(f - bg).max(2) > 18
    fg = ndimage.binary_opening(fg, iterations=1)
    fg = ndimage.binary_closing(fg, iterations=2)
    fg = ndimage.binary_fill_holes(fg)
    lab, n = ndimage.label(fg)
    if n > 1:
        sizes = ndimage.sum(fg, lab, range(1, n + 1))
        fg = np.isin(lab, 1 + np.nonzero(sizes > 300)[0])
    return fg


# ------------------------------------------------------------------ cameras for all six references
cams, report = {}, {"cameras": {}}
for name, view in (("reference-05.png", "front"), ("reference-01.png", "angled"), ("reference-06.png", "left")):
    cams[name] = pickle.load(open(OLD + f"cam_{view}.pkl", "rb"))
    report["cameras"][name] = {"view": view, "source": "earlier probe", "iou": fits["fits"][name]["metrics"].get("iou")}

bcam.PRIOR_YAW["top"] = 0.0
bcam.PRIOR_YAW["topb"] = 180.0
bcam.PITCH_BOUNDS = (-95.0, 95.0)


def fit(name, view, seeds):
    img = photo(name); fg = matte(img)
    Image.fromarray((fg * 255).astype(np.uint8)).save(HERE + f"matte_{name}")
    vf = bcam.ViewFit(view, fg, scene, pivot=pivot)
    vf.run_starts(seeds, 2)
    res = vf.refine()
    cam = res["camera"]
    ys, xs = np.nonzero(fg); w = xs.max() - xs.min() + 1; m = int(0.15 * w) + 8
    roi = (int(max(0, xs.min() - m)), int(max(0, ys.min() - m)), int(min(fg.shape[1], xs.max() + m + 1)), int(min(fg.shape[0], ys.max() + m + 1)))
    met = bcam.native_metrics(scene, cam, fg, roi)
    return cam, res["loss_final_level"], met


for name, cands in (("reference-02.png", [("back", None)]),
                    ("reference-03.png", [("right", None), ("left", None)]),
                    ("reference-04.png", [("top", [(y, p, 0.0, "top") for y in (-6.0, 0.0, 6.0) for p in (70.0, 80.0, 88.0, -70.0, -80.0, -88.0)]),
                                          ("topb", [(180.0 + y, p, 0.0, "topb") for y in (-6.0, 0.0, 6.0) for p in (70.0, 80.0, 88.0, -70.0, -80.0, -88.0)])])):
    import os
    if os.path.exists(HERE + f"cam_{name}.pkl"):
        cams[name] = pickle.load(open(HERE + f"cam_{name}.pkl", "rb")); continue
    best = None
    for view, seeds in cands:
        cam, loss, met = fit(name, view, seeds if seeds is not None else bcam.prior_seeds(view))
        print(name, view, round(loss, 4), {k: met.get(k) for k in ("iou", "contour_mean_px")}, flush=True)
        if best is None or loss < best[1]:
            best = (cam, loss, met, view)
    cams[name] = best[0]
    report["cameras"][name] = {"view": best[3], "loss": best[1], "iou": best[2].get("iou"), "contour_mean_px": best[2].get("contour_mean_px")}
    pickle.dump(best[0], open(HERE + f"cam_{name}.pkl", "wb"))

# overlay check per photo: rendered silhouette edge on the photo
for name, cam in cams.items():
    img = photo(name); H, W = img.shape[:2]
    r = scene.render(cam, (H, W), 1); hit = r["face_id"] >= 0
    e = hit & ~ndimage.binary_erosion(hit)
    ov = img.copy(); ov[e] = (255, 0, 255)
    Image.fromarray(ov).save(HERE + f"overlay_{name}")

# ------------------------------------------------------------------ unroll each part into a strip
STRIPS = ["Brow_Left", "Brow_Right", "Temple_Left", "Temple_Right"]
imgs = {n: photo(n) for n in cams}
mattes = {n: ndimage.binary_erosion(matte(imgs[n]), iterations=1) for n in cams}
ppm = {n: px_per_mm(cams[n], frame) for n in cams}
order = list(cams)
strip_imgs, strip_src, strip_used = [], [], []
report["strips"] = {}
for pname in STRIPS:
    pid = names.index(pname)
    faces = np.nonzero(part == pid)[0]
    vid = np.unique(F[faces]); P0 = V[vid]
    mu = P0.mean(0)
    a1 = np.linalg.svd(P0 - mu, full_matrices=False)[2][0]
    if pname.startswith("Brow"):
        ref = np.array([0.0, 0.0, 1.0])                       # front (+Z) in the middle of the strip
    else:
        ref = np.array([np.sign(mu[0]) or 1.0, 0.0, 0.0])     # outward side in the middle of the strip
    e1 = ref - (ref @ a1) * a1; e1 /= np.linalg.norm(e1)
    # read the strip like a photo of the outer face: up = +Y, s grows to the viewer's right
    right = np.cross(-e1, np.array([0.0, 1.0, 0.0]))
    if a1 @ right < 0: a1 = -a1
    e2 = np.cross(a1, e1)
    if e2[1] > 0: e2 = -e2                                    # +t goes DOWN the outer face (image rows)
    s_all = (V - mu) @ a1
    sv = s_all[vid]
    edges = np.arange(sv.min(), sv.max() + 3.0, 3.0)
    centers, svals, perims = [], [], []
    for k in range(len(edges) - 1):
        sel = (sv >= edges[k]) & (sv < edges[k + 1])
        if sel.sum() < 6: continue
        Q = P0[sel]; qe1 = (Q - mu) @ e1; qe2 = (Q - mu) @ e2
        cb = mu + 0.5 * (qe1.min() + qe1.max()) * e1 + 0.5 * (qe2.min() + qe2.max()) * e2
        sm = 0.5 * (edges[k] + edges[k + 1]); centers.append(cb + sm * a1); svals.append(sm)
        q2 = np.c_[(Q - Q.mean(0)) @ e1, (Q - Q.mean(0)) @ e2]
        try: perims.append(ConvexHull(q2).area)
        except Exception: pass
    centers = np.array(centers); svals = np.array(svals)
    perim = float(np.median(perims))
    deg = int(min(5, max(1, len(svals) // 4)))
    coefs = [np.polyfit(svals, centers[:, d], deg) for d in range(3)]
    def center_at(s):
        s = np.clip(s, svals.min(), svals.max())
        return np.stack([np.polyval(coefs[d], s) for d in range(3)], -1)
    # midpoint-subdivide long triangles (Astra's lofts are long thin faces) so (s, angle) interpolates sanely
    TP = V[F[faces]].copy(); par = faces.copy()
    for _ in range(8):
        el = np.linalg.norm(TP - np.roll(TP, -1, 1), axis=2).max(1)
        big = el > 1.2
        if not big.any(): break
        a, b, c = TP[big, 0], TP[big, 1], TP[big, 2]
        ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
        new = np.concatenate([np.stack([a, ab, ca], 1), np.stack([ab, b, bc], 1), np.stack([ca, bc, c], 1), np.stack([ab, bc, ca], 1)])
        TP = np.concatenate([TP[~big], new]); par = np.concatenate([par[~big], np.tile(par[big], 4)])
    flat = TP.reshape(-1, 3)
    s_c = (flat - mu) @ a1
    d = flat - center_at(s_c); d -= (d @ a1)[:, None] * a1
    t_c = np.arctan2(d @ e2, d @ e1)                          # 0 = front/outward, +/-pi = the hidden cut
    Ws = int(math.ceil((sv.max() - sv.min()) * PX_MM)) + 4
    Hs = int(math.ceil(perim * PX_MM)) + 4
    tri_s = ((s_c - sv.min()) * PX_MM + 2).reshape(-1, 3)
    tri_t = t_c.reshape(-1, 3).copy()
    wrap = tri_t.max(1) - tri_t.min(1) > math.pi
    tri_t[wrap] = np.where(tri_t[wrap] < 0, tri_t[wrap] + 2 * math.pi, tri_t[wrap])
    vv = (tri_t + math.pi) / (2 * math.pi) * (Hs - 4) + 2
    tri = np.stack([tri_s, vv], -1)
    tri_all = np.concatenate([tri, tri[wrap] - np.array([0.0, Hs - 4])], 0)
    TPa = np.concatenate([TP, TP[wrap]]); para = np.concatenate([par, par[wrap]])
    fid, bary = T.rasterize_triangles(tri_all, (Hs, Ws))
    used = fid >= 0
    Pt = np.einsum("kj,kjd->kd", bary[used].astype(float), TPa[fid[used]])
    gf = para[fid[used]]
    e1f = V[F[gf, 1]] - V[F[gf, 0]]; e2f = V[F[gf, 2]] - V[F[gf, 0]]
    N = np.cross(e1f, e2f); N /= np.linalg.norm(N, axis=1, keepdims=True) + 1e-12
    best_score = np.full(len(Pt), -1.0); best_col = np.zeros((len(Pt), 3)); best_src = np.full(len(Pt), -1)
    for k, n in enumerate(order):
        H, W = imgs[n].shape[:2]
        vis, uvp = T.visible_points(Pt, N, cams[n], frame, scene, (H, W), min_cos=0.2, tol_mm=0.5)
        ui = np.clip(np.round(uvp[:, 0]).astype(int), 0, W - 1); vi = np.clip(np.round(uvp[:, 1]).astype(int), 0, H - 1)
        ok = vis & mattes[n][vi, ui]
        cosv = N @ raster.camera_basis(cams[n])[2]
        score = np.where(ok, cosv * ppm[n], -1.0)
        better = score > best_score
        if better.any():
            best_col[better] = T.sample_bilinear(imgs[n], uvp[better, 0], uvp[better, 1])
            best_src[better] = k; best_score[better] = score[better]
    img = np.full((Hs, Ws, 3), 128.0); src = np.full((Hs, Ws), -1)
    ii, jj = np.nonzero(used)
    got = best_src >= 0
    img[ii[got], jj[got]] = best_col[got]; src[ii, jj] = best_src
    strip_imgs.append(img); strip_src.append(src); strip_used.append(used)
    cov = {order[k]: round(float((best_src == k).sum() / len(best_src)), 3) for k in range(len(order))}
    report["strips"][pname] = {"size_px": [Ws, Hs], "length_mm": round(float(sv.max() - sv.min()), 1),
                               "around_mm": round(perim, 1), "seen_by_any_image": round(float(got.mean()), 3),
                               "share_per_image": cov}
    print(pname, report["strips"][pname], flush=True)

# ------------------------------------------------------------------ stack into one atlas + repaint mask
GAP = 24
Wa = max(s.shape[1] for s in strip_imgs); Ha = sum(s.shape[0] for s in strip_imgs) + GAP * (len(strip_imgs) + 1)
atlas = np.full((Ha, Wa, 3), 255.0); mask = np.zeros((Ha, Wa), bool); srcmap = np.full((Ha, Wa, 3), 255, np.uint8)
pal = np.array([(230, 25, 75), (60, 180, 75), (0, 130, 200), (245, 130, 48), (145, 30, 180), (70, 240, 240)], np.uint8)
y = GAP; layout = []
for name_, img, src, used in zip(STRIPS, strip_imgs, strip_src, strip_used):
    Hs, Ws = src.shape
    atlas[y:y + Hs, :Ws] = np.where(used[..., None], img, 255.0)
    hole = used & (src < 0)
    seam = np.zeros_like(used)
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        a = src; b = np.roll(np.roll(src, dy, 0), dx, 1)
        seam |= (a != b) & (a >= 0) & (b >= 0)
    seam = ndimage.binary_dilation(seam, iterations=3) & used
    f = img; lum = f.mean(2); sat = f.max(2) - f.min(2)
    shiny = used & (src >= 0) & (lum > 175) & (sat < 60)
    shiny = ndimage.binary_dilation(shiny, iterations=2) & used
    mask[y:y + Hs, :Ws] = hole | seam | shiny
    sm = np.full((Hs, Ws, 3), 255, np.uint8); sm[used] = (128, 128, 128); okk = used & (src >= 0); sm[okk] = pal[src[okk]]
    srcmap[y:y + Hs, :Ws] = sm
    layout.append({"part": name_, "y0": y, "height": Hs, "width": Ws})
    y += Hs + GAP
Image.fromarray(atlas.clip(0, 255).astype(np.uint8)).save(HERE + "ft1119_uv_test.png")
Image.fromarray((mask * 255).astype(np.uint8)).save(HERE + "ft1119_uv_test_mask.png")
Image.fromarray(srcmap).save(HERE + "ft1119_uv_sources.png")
report["layout"] = layout; report["source_colours"] = {order[k]: pal[k].tolist() for k in range(len(order))}
json.dump(report, open(HERE + "report.json", "w"), indent=1, default=float)
print(json.dumps(report["layout"]))
