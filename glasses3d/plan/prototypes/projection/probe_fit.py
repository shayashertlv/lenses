"""$0 feasibility probe: fit BSA silhouette cameras of the real FT1119 photos to a COPY of quality-001,
then measure brow registration error. Read-only on the repo (imports bsa/ from automation)."""
import sys, json, time, pickle
sys.path.insert(0, r"C:/Users/Shay/PycharmProjects/lenses/automation")
import numpy as np
from PIL import Image
from scipy import ndimage
from bsa import raster, cameras as bcam
from bsa.core import NormFrame, project_mm, px_per_mm
from glbload import load

from pathlib import Path as _Path
_G3D = _Path(__file__).resolve().parents[3]          # glasses3d/
sys.path.insert(0, str(_Path(__file__).resolve().parent))
HERE = str(_G3D / "data/reference/projection") + "/"
J = r"C:/Users/Shay/PycharmProjects/lenses/automation/data/blender_agent/studio/jobs/d4b716a6afc445f89cbed8beddad4c37/images/"

doc, binc, prims = load(HERE + "q001.glb")
skip = ("inner_brand_print", "TOM_FORD", "medallion")
prims = [p for p in prims if not any(s in p["node"] for s in skip)]
Vs, Fs, part, off = [], [], [], 0
names = []
for i, p in enumerate(prims):
    Vs.append(p["V"] * 1000.0); Fs.append(p["F"] + off); off += len(p["V"])
    part.append(np.full(len(p["F"]), i)); names.append(p["node"])
V = np.vstack(Vs); F = np.vstack(Fs); part = np.concatenate(part)
lo, hi = V.min(0), V.max(0)
frame = NormFrame(tuple(float(x) for x in (lo + hi) / 2), float((hi - lo).max()))
scene = raster.get_scene(V, F, frame)
front_names = [n for n in names if n.split(".")[0] in ("Brow_Left", "Brow_Right", "Eyewire_Left", "Eyewire_Right", "Lens_Left", "Lens_Right", "Saddle_bridge")]
fp = np.isin(part, [names.index(n) for n in front_names])
fpV = V[np.unique(F[fp])]
pivot_mm = (fpV.min(0) + fpV.max(0)) / 2


def matte(path):
    im = np.asarray(Image.open(path).convert("RGB")).astype(int)
    fg = (255 - im.min(2)) > 14
    fg = ndimage.binary_closing(fg, iterations=2)
    fg = ndimage.binary_fill_holes(fg)
    lab, n = ndimage.label(fg)
    if n > 1:
        sizes = ndimage.sum(fg, lab, range(1, n + 1))
        keep = np.isin(lab, 1 + np.nonzero(sizes > 200)[0])
        fg = keep
    return fg


out = {}
for name, view in (("reference-05.png", "front"), ("reference-01.png", "angled"), ("reference-06.png", "left")):
    t0 = time.time()
    fg = matte(J + name)
    fit = bcam.ViewFit(view, fg, scene, pivot=frame.to_norm(pivot_mm[None])[0])
    seeds = bcam.prior_seeds(view)
    fit.run_starts(seeds, 2)
    res = fit.refine()
    cam = res["camera"]
    ys, xs = np.nonzero(fg); w = xs.max()-xs.min()+1; mm_ = int(0.15*w)+8
    roi = (int(max(0, xs.min()-mm_)), int(max(0, ys.min()-mm_)), int(min(fg.shape[1], xs.max()+mm_+1)), int(min(fg.shape[0], ys.max()+mm_+1)))
    pickle.dump(cam, open(HERE + f"cam_{view}.pkl", "wb"))
    m = bcam.native_metrics(scene, cam, fg, roi)
    out[name] = {"view": view, "camera": [cam.yaw, cam.pitch, cam.roll, cam.perspective, cam.scale, cam.center_x, cam.center_y],
                 "px_per_mm": px_per_mm(cam, frame), "loss": res["loss_final_level"],
                 "metrics": {k: (float(v) if np.isscalar(v) else None) for k, v in m.items()},
                 "seconds": round(time.time() - t0, 1)}
    print(name, json.dumps(out[name], default=float), flush=True)
    np.save(HERE + f"fg_{view}.npy", fg)

json.dump({"names": names, "frame": frame.to_dict(), "fits": out}, open(HERE + "fits.json", "w"), indent=1, default=float)
