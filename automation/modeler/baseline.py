"""Measure an external GLB (a previous route's asset) with the job's own protocol, for a fair comparison.

    python -m modeler.baseline --job data/modeler/jobs/<name> --glb <asset.glb> --name bsa-m3

Same photos, same camera-fit procedure, same metrics, same AR harness as the job's candidates; no Blender renders
(there is no program). Results land in <job>/baselines/<name>/ and never enter the author's packages.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from reconstruction.mesh import load_glb_bytes

from .observe import observe_candidate


def parts_from_glb(glb: Path, out_dir: Path) -> tuple[Path, Path]:
    """Write parts.npz + materials.json (the harness's export format) from a GLB: metres -> mm, parts by declared
    role / node name (lens_R, lens_L, frame, temple_R, temple_L; unknown -> frame)."""
    mesh = load_glb_bytes(Path(glb).read_bytes())
    V = mesh.vertices * 1000.0
    arrays, meta = {}, {}
    for i, p in enumerate(mesh.parts or []):
        name = str(p.get("name") or f"part{i}")
        role = p.get("declared_role") or ("lens" if (p.get("transmission") or 0) > 0 or p.get("has_lens_appearance_extension") else None)
        part = name if name in ("frame", "temple_R", "temple_L", "lens_R", "lens_L", "lens_C") else None
        if part is None:
            if role == "lens":
                part = "lens_R" if V[mesh.faces[p["face_start"]:p["face_start"] + p["face_count"]]].reshape(-1, 3)[:, 0].mean() > 0 else "lens_L"
            elif role == "temple" or name.lower().startswith("temple"):
                part = "temple_R" if "R" in name[-2:] else "temple_L"
            else:
                part = "frame"
        F = mesh.faces[p["face_start"]:p["face_start"] + p["face_count"]]
        used = np.unique(F)
        remap = -np.ones(len(V), np.int64)
        remap[used] = np.arange(len(used))
        key = f"{name}_{i}".replace("/", "_")
        arrays[f"{key}__V"] = V[used].astype(np.float32)
        arrays[f"{key}__F"] = remap[F].astype(np.int32)
        arrays[f"{key}__M"] = np.zeros(len(F), np.int32)
        meta[key] = {"object": key, "part": part, "component": name, "materials": [None]}
    out_dir.mkdir(parents=True, exist_ok=True)
    npz = out_dir / "parts.npz"
    np.savez_compressed(npz, **arrays)
    mats = out_dir / "materials.json"
    mats.write_text(json.dumps({"materials": {}, "objects": meta, "declarations": {}}, indent=1), encoding="utf-8")
    return npz, mats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job", type=Path, required=True)
    ap.add_argument("--glb", type=Path, required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--no-ar", action="store_true")
    args = ap.parse_args(argv)
    job = args.job
    evidence = json.loads((job / "evidence" / "evidence.json").read_text(encoding="utf-8"))
    out = job / "baselines" / args.name
    npz, mats = parts_from_glb(args.glb, out / "build")
    build = {"parts_npz": str(npz), "materials_json": str(mats), "blend": None}
    obs = observe_candidate(out, build, evidence, job / "evidence", held_out_ids=set(evidence["held_out"]), ar=not args.no_ar,
                            glb_path=args.glb)
    held = json.loads((out / "heldout" / "heldout.json").read_text(encoding="utf-8"))
    summary = {"name": args.name, "glb": str(args.glb), "summary": obs["summary"], "held_out": held["summary"],
               "per_view": {k: {kk: v.get(kk) for kk in ("view", "iou", "contour_mean_mm", "contour_p95_mm")} for k, v in obs["views"].items()}}
    (out / "baseline.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
