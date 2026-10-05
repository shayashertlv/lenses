"""$0 probe part 2: (a) brow registration error of fitted silhouette cameras; (b) project the FRONT photo onto
quality-001's brow UVs (visibility-tested), then re-render through the HELD-OUT angled camera and compare with the
angled photo, against quality-001's own hand-typed pigment texture through the same camera."""
import sys, json, pickle, io
sys.path.insert(0, r"C:/Users/Shay/PycharmProjects/lenses/automation")
import numpy as np
from PIL import Image
from scipy import ndimage
from bsa import raster
from bsa.core import NormFrame, project_mm, px_per_mm
from bsa import texture as T
from glbload import load, split

from pathlib import Path as _Path
_G3D = _Path(__file__).resolve().parents[3]          # glasses3d/
sys.path.insert(0, str(_Path(__file__).resolve().parent))
HERE = str(_G3D / "data/reference/projection") + "/"
J = r"C:/Users/Shay/PycharmProjects/lenses/automation/data/blender_agent/studio/jobs/d4b716a6afc445f89cbed8beddad4c37/images/"
fits = json.load(open(HERE + "fits.json"))
frame = NormFrame(tuple(fits["frame"]["center"]), fits["frame"]["extent"])

doc, binc, prims = load(HERE + "q001.glb")
skip = ("inner_brand_print", "TOM_FORD", "medallion")
prims = [p for p in prims if not any(s in p["node"] for s in skip)]
Vs, Fs, part, UVc, off = [], [], [], [], 0
names = []
for i, p in enumerate(prims):
    Vs.append(p["V"] * 1000.0); Fs.append(p["F"] + off); off += len(p["V"])
    part.append(np.full(len(p["F"]), i)); names.append(p["node"].split(".")[0])
    UVc.append(p["UV"][p["F"]] if p["UV"] is not None else np.zeros((len(p["F"]), 3, 2)))
V = np.vstack(Vs); F = np.vstack(Fs); part = np.concatenate(part); UVc = np.concatenate(UVc)
assert fits["names"] == [p["node"] for p in prims]
scene = raster.get_scene(V, F, frame)
BROW = [names.index("Brow_Left"), names.index("Brow_Right")]
TEMPLE = [names.index("Temple_Left"), names.index("Temple_Right")]
is_brow_face = np.isin(part, BROW)

def cam_of(view):
    return pickle.load(open(HERE + f"cam_{view}.pkl", "rb"))

def photo(name):
    return np.asarray(Image.open(J + name).convert("RGB"))

def acetate_mask(img):
    f = img.astype(float); lum = f.mean(2); sat = f.max(2) - f.min(2)
    return ((lum < 170) & (sat > 25)) | (lum < 90)

report = {}
# ---------------------------------------------------------------- (a) brow registration per fitted photo
for name, view in (("reference-05.png", "front"), ("reference-01.png", "angled"), ("reference-06.png", "left")):
    cam = cam_of(view); img = photo(name); H, W = img.shape[:2]
    r = scene.render(cam, (H, W), 1)
    fid = r["face_id"]; hit = fid >= 0
    pid = np.full(fid.shape, -1); pid[hit] = part[fid[hit]]
    rbrow = np.isin(pid, BROW); rtemple = np.isin(pid, TEMPLE)
    pmask = acetate_mask(img)
    # columns where the rendered brow is the only acetate (no rendered temple in the column)
    cols = [x for x in range(W) if rbrow[:, x].sum() >= 4 and not rtemple[:, x].any()]
    top, bot = [], []
    for x in cols:
        ys = np.nonzero(rbrow[:, x])[0]; y0, y1 = ys.min(), ys.max()
        band = np.zeros(H, bool); band[max(0, y0 - 12):min(H, y1 + 13)] = True
        py = np.nonzero(pmask[:, x] & band)[0]
        if len(py) < 4: continue
        top.append(py.min() - y0); bot.append(py.max() - y1)
    top, bot = np.array(top, float), np.array(bot, float)
    ppm = px_per_mm(cam, frame)
    # symmetric boundary distance between rendered brow and photo acetate inside the brow columns' band
    colmask = np.zeros((H, W), bool); colmask[:, cols] = True
    near = ndimage.binary_dilation(rbrow, iterations=12) & colmask
    pm = pmask & near
    e_r = rbrow & ~ndimage.binary_erosion(rbrow); e_p = pm & ~ndimage.binary_erosion(pm)
    d_to_p = ndimage.distance_transform_edt(~e_p); d_to_r = ndimage.distance_transform_edt(~e_r)
    sym = np.concatenate([d_to_p[e_r & colmask], d_to_r[e_p]])
    report[view] = {"photo": name, "px_per_mm": round(ppm, 3), "columns": len(top),
                    "brow_top_edge_err_px": {"median_signed": float(np.median(top)), "median_abs": float(np.median(np.abs(top))), "p90_abs": float(np.percentile(np.abs(top), 90))},
                    "brow_bottom_edge_err_px": {"median_signed": float(np.median(bot)), "median_abs": float(np.median(np.abs(bot))), "p90_abs": float(np.percentile(np.abs(bot), 90))},
                    "brow_boundary_symmetric_px": {"mean": float(sym.mean()), "p90": float(np.percentile(sym, 90))},
                    "rendered_brow_height_px_median": float(np.median([rbrow[:, x].sum() for x in cols])) if cols else None,
                    "camera": fits["fits"][name]["camera"], "fit_metrics": {k: fits["fits"][name]["metrics"].get(k) for k in ("iou", "contour_mean_px", "outer_contour_mean_px")}}
    # overlay: photo with rendered brow edge (cyan) and temple edge (magenta)
    ov = img.copy()
    ov[e_r] = (0, 255, 255)
    et = rtemple & ~ndimage.binary_erosion(rtemple); ov[et] = (255, 0, 255)
    Image.fromarray(ov).save(HERE + f"overlay_brow_{view}.png")
    print(view, json.dumps(report[view]), flush=True)

# ---------------------------------------------------------------- (b) projection bake from the FRONT photo only
TEX = 1024
camF = cam_of("front"); imgF = photo("reference-05.png"); HF, WF = imgF.shape[:2]
brow_faces = np.nonzero(is_brow_face)[0]
# quality-001's brow UV is PLANAR: front and back faces share texels (measured overlap 2.0). Rasterize the faces
# turned toward the front camera LAST so they own the shared texels (later triangles win).
_e1 = V[F[brow_faces, 1]] - V[F[brow_faces, 0]]; _e2 = V[F[brow_faces, 2]] - V[F[brow_faces, 0]]
_c = np.cross(_e1, _e2) @ raster.camera_basis(camF)[2]
brow_faces = brow_faces[np.argsort(_c, kind="stable")]
uvtri = UVc[brow_faces] * TEX                       # glTF uv: u right, v DOWN (image rows) -> texel coords directly
fid_t, bary_t = T.rasterize_triangles(uvtri, (TEX, TEX))
used = fid_t >= 0
gf = brow_faces[fid_t[used]]
P = np.einsum("kj,kjd->kd", bary_t[used].astype(float), V[F[gf]])
# outward normal per face (geometric)
e1 = V[F[gf, 1]] - V[F[gf, 0]]; e2 = V[F[gf, 2]] - V[F[gf, 0]]
N = np.cross(e1, e2); N /= np.linalg.norm(N, axis=1, keepdims=True) + 1e-12
vis, uvp = T.visible_points(P, N, camF, frame, scene, (HF, WF), min_cos=0.15, tol_mm=0.5)
amask = acetate_mask(imgF)
ui = np.clip(np.round(uvp[:, 0]).astype(int), 0, WF - 1); vi = np.clip(np.round(uvp[:, 1]).astype(int), 0, HF - 1)
on_acetate = amask[vi, ui]
good = vis & on_acetate
col = T.sample_bilinear(imgF, uvp[:, 0], uvp[:, 1])
tex = np.zeros((TEX, TEX, 3), np.float32); valid = np.zeros((TEX, TEX), bool)
ii, jj = np.nonzero(used)
tex[ii[good], jj[good]] = col[good]; valid[ii[good], jj[good]] = True
# unseen texels (back/underside, rejected): nearest valid texel (stand-in for mirroring/synthesis)
filled, dist = T.nearest_fill(tex, valid)
filled[~used] = 0
Image.fromarray(np.clip(filled, 0, 255).astype(np.uint8)).save(HERE + "baked_front_brow.png")
report["bake"] = {"texture": TEX, "brow_texels": int(used.sum()), "visible_from_front": int(vis.sum()),
                  "visible_and_on_acetate": int(good.sum()), "share_projected": round(float(good.sum() / used.sum()), 3),
                  "rejected_visible_but_not_acetate_px": int((vis & ~on_acetate).sum())}
print("bake", report["bake"], flush=True)

# existing quality-001 brow texture (Astra's hand-typed field)
d, b = split(HERE + "q001.glb")
mat_i = [i for i, m in enumerate(d["materials"]) if m["name"].startswith("Front source havana")][0]
tex_i = d["materials"][mat_i]["pbrMetallicRoughness"]["baseColorTexture"]["index"]
img_i = d["textures"][tex_i]["source"]; bv = d["bufferViews"][d["images"][img_i]["bufferView"]]
astra_tex = np.asarray(Image.open(io.BytesIO(b[bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]])).convert("RGB"))
Image.fromarray(astra_tex).save(HERE + "astra_brow_texture.png")

# ---------------------------------------------------------------- render the brow through the HELD-OUT angled camera
def render_tex(cam, shape, texture):
    r = scene.render(cam, shape, 1, want_bary=True)
    fid = r["face_id"]; hit = fid >= 0
    out = np.zeros((*shape, 3), np.float32); m = np.zeros(shape, bool)
    sel = hit.copy(); sel[hit] = is_brow_face[fid[hit]]
    f = fid[sel]; bb = r["bary"][sel].astype(float)
    w = np.stack([1 - bb[:, 0] - bb[:, 1], bb[:, 0], bb[:, 1]], 1)
    uv = np.einsum("kj,kjd->kd", w, UVc[f])
    th, tw = texture.shape[:2]
    out[sel] = T.sample_bilinear(texture, uv[:, 0] * tw - 0.5, uv[:, 1] * th - 0.5)
    m[sel] = True
    return out, m

def compare(view, name):
    cam = cam_of(view); img = photo(name); H, W = img.shape[:2]
    a, m = render_tex(cam, (H, W), astra_tex.astype(np.float32))
    p, _ = render_tex(cam, (H, W), filled)
    am = acetate_mask(img)
    use = ndimage.binary_erosion(m, iterations=2) & ndimage.binary_erosion(am, iterations=1)
    def lum(x): return x.astype(float).mean(-1)
    ph = ndimage.gaussian_filter(lum(img), 1.0);
    res = {"pixels": int(use.sum())}
    for key, ren in (("astra_hand_typed", a), ("front_photo_projection", p)):
        rl = ndimage.gaussian_filter(lum(ren), 1.0)
        r_ = np.corrcoef(rl[use], ph[use])[0, 1]
        g, o, _ = T.quantile_gain(ren[use], img[use].astype(float))
        adj = np.clip(T.apply_gain(ren[use], g, o), 0, 255)
        de = T.de00(adj, img[use].astype(float))
        res[key] = {"luminance_pearson_r": round(float(r_), 3), "de00_median_after_gain": round(float(np.median(de)), 2),
                    "de00_p90_after_gain": round(float(np.percentile(de, 90)), 2)}
    # side-by-side crop
    ys, xs = np.nonzero(m); y0, y1, x0, x1 = max(ys.min() - 8, 0), ys.max() + 8, max(xs.min() - 8, 0), xs.max() + 8
    def lay(x, mm):
        o = img.copy().astype(np.float32); o[mm] = x[mm]; return o[y0:y1, x0:x1]
    sheet = np.concatenate([img[y0:y1, x0:x1], lay(p, m), lay(a, m)], 0).clip(0, 255).astype(np.uint8)
    Image.fromarray(sheet).resize((sheet.shape[1] * 2, sheet.shape[0] * 2), Image.NEAREST).save(HERE + f"heldout_{view}_photo_proj_astra.png")
    return res

report["heldout_angled"] = compare("angled", "reference-01.png")
print("heldout angled", report["heldout_angled"], flush=True)
report["self_front"] = compare("front", "reference-05.png")
print("self front", report["self_front"], flush=True)
json.dump(report, open(HERE + "probe_report.json", "w"), indent=1, default=float)
