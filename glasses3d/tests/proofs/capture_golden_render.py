"""Capture the photo_v1 golden renders that tests/test_render_dome.py test_photo_v1_pixels_are_unchanged compares
against, with the PRE-CHANGE g3d/render.py.

The g3d package at ``--commit`` (default 62b25e4, the last commit before the WP6 v2 render changes) is extracted with
``git archive`` into ``<out>/src_<commit>`` so the working tree's render.py cannot leak in; Blender opens a byte copy
of the accepted Prada scene (sha 56274deb...) and calls that snapshot's ``render.render`` (photo_v1, EEVEE, the
default 16 samples) on the six WP3b reference cameras (data/wp3b/prada/cameras.json) scaled to <= 640 px, passes
native, clay and normal.

Writes data/wp6v2/golden_photo_v1/golden_<tag>.json ({render, files {"<view>/<pass>": {path, shape}}, views}) and the
PNGs under job_<tag>/work/r. Two captures of the same code differ only by EEVEE's run-to-run noise (<= 14 px).
Run: glasses3d/.venv/Scripts/python.exe -m glasses3d.tests.proofs.capture_golden_render [--tag a] [--commit 62b25e4]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import numpy as np
from PIL import Image

from glasses3d.g3d_host import blender
from glasses3d.g3d_host import blender_camera as BC
from glasses3d.g3d_host.camera import Camera, NormFrame

G3D = Path(__file__).resolve().parents[2]
REPO = G3D.parent
OUT = G3D / "data" / "wp6v2" / "golden_photo_v1"
SCENE = REPO / "automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend"
SCENE_SHA = "56274debb901fb28dea1f93f0a6b6ae8401501f7145573f8dedb68c7d2436c06"
CAMS = G3D / "data" / "wp3b" / "prada" / "cameras.json"
LIGHT = {"center": [0, -5, -70], "radius": 90.0}
MAX_PX = 640

RENDER_BPY = r'''
import json, sys, traceback
argv = sys.argv[sys.argv.index("--") + 1:]
src, params_path, root, out_json = argv
sys.path.insert(0, src)
from glasses3d.g3d import render as R
res = {"module": R.__file__}
try:
    res["render"] = R.render(json.load(open(params_path, encoding="utf-8")), root)
    res["status"] = "ok"
except Exception:
    res["status"] = "failed"
    res["traceback"] = traceback.format_exc()
json.dump(res, open(out_json, "w", encoding="utf-8"))
'''


def _rw(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def snapshot(commit: str) -> Path:
    dst = OUT / f"src_{commit}"
    if dst.exists():
        shutil.rmtree(dst, onerror=_rw)
    tar = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", commit, "glasses3d/__init__.py",
                          "glasses3d/g3d"], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(dst, filter="data")
    return dst


def main(tag: str, commit: str) -> dict:
    root = OUT / f"job_{tag}"
    if root.exists():
        shutil.rmtree(root, onerror=_rw)
    (root / "work").mkdir(parents=True)
    seed = root / "seed.blend"
    shutil.copy2(SCENE, seed)
    os.chmod(seed, stat.S_IWRITE | stat.S_IREAD)
    if hashlib.sha256(seed.read_bytes()).hexdigest() != SCENE_SHA:
        raise RuntimeError("the Prada scene copy is not 56274deb...")
    src = snapshot(commit)
    doc = json.loads(CAMS.read_text(encoding="utf-8"))
    frame = NormFrame.from_dict(doc["model"]["frame"])
    views = []
    for name, rec in doc["images"].items():
        H, W = rec["shape"]
        k = min(1.0, MAX_PX / max(H, W))
        cam = BC.crop_camera(Camera.from_dict(rec["camera"]), 0, 0, k)
        shape = (round(H * k), round(W * k))
        views.append({"name": Path(name).stem, "camera": BC.to_blender(cam, frame, (0, 0, 0), shape),
                      "passes": ["native", "clay", "normal"]})
    params = {"out_dir": str(root / "work" / "r"), "views": views, "engine": "eevee", "light_frame": LIGHT}
    (root / "params.json").write_text(json.dumps(params), encoding="utf-8")
    script = root / "render_bpy.py"
    script.write_text(RENDER_BPY, encoding="utf-8")
    out_json = root / "result.json"
    proc = blender.run(seed, script, [src, root / "params.json", root, out_json], timeout_s=900)
    if not out_json.is_file():
        raise RuntimeError(f"the capture wrote nothing: {proc.stderr[-2000:]}")
    res = json.loads(out_json.read_text(encoding="utf-8"))
    if res["status"] != "ok":
        raise RuntimeError(res.get("traceback"))
    if not Path(res["module"]).resolve().is_relative_to(src.resolve()):
        raise RuntimeError(f"the capture imported {res['module']}, not the {commit} snapshot")
    r = res["render"]
    files = {}
    for v in r["views"]:
        for ps, f in v["files"].items():
            files[f"{v['name']}/{ps}"] = {"path": f["path"], "shape": list(np.asarray(Image.open(f["path"])).shape)}
    golden = {"render": {k: v for k, v in r.items() if k != "views"}, "files": files, "views": views,
              "commit": commit, "module": res["module"]}
    (OUT / f"golden_{tag}.json").write_text(json.dumps(golden, indent=1), encoding="utf-8")
    return {"unchanged": r["unchanged"], "engine": r["engine_final"], "files": len(files), "commit": commit}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="a")
    ap.add_argument("--commit", default="62b25e4")
    a = ap.parse_args()
    print(json.dumps(main(a.tag, a.commit)))
