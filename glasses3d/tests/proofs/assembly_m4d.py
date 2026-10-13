"""Offline camera assembly proof on synthetic photos; never a product or owner verdict."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
from uuid import uuid4

import numpy as np
from PIL import Image

from glasses3d.agent import jobs, procs
from glasses3d.agent.journal import BlenderSession
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.agent.seeing import Seeing
from glasses3d.agent.tools import fit_workers, single_threaded_blas
from glasses3d.g3d_host import blender, compare as C, fitmesh, matte as M, raster
from glasses3d.tests.test_fit_views_synthetic import PPM, SHAPE, mesh_glb, photo, synthetic_parts


def run():
    disable_sdk_tracing()
    started = time.monotonic()
    out = blender.G3D / "data/assembly" / ("m4d-" + uuid4().hex[:8])
    source = out / "source"
    source.mkdir(parents=True)
    parts = synthetic_parts()
    glb = source / "model.glb"
    glb.write_bytes(mesh_glb(parts))
    mesh = fitmesh.load_fit_mesh(glb)
    views = {"front.png": {"kind": "front", "side": None},
             "rear.png": {"kind": "rear_threequarter", "side": "left", "matte_mode": "glossy_floor"},
             "blank.png": {"kind": "front", "side": None}}
    truth = {"front.png": (4., 12., 1., .12), "rear.png": (140., 10., 0., .12)}
    for name, (yaw, pitch, roll, perspective) in truth.items():
        cam = raster.view_camera(mesh.frame, yaw, pitch, PPM, SHAPE, roll=roll, perspective=perspective)
        mask = mesh.scene().render(cam, SHAPE)["mask"]
        assert not (mask[:12].any() or mask[-12:].any() or mask[:, :12].any() or mask[:, -12:].any())
        Image.fromarray(photo(mask)).save(source / name)
    Image.new("RGB", SHAPE[::-1], "white").save(source / "blank.png")
    procs.write_json_atomic(out / "views.json", views)
    root = jobs.create_job(images=source, views=out / "views.json", jobs_root=out / "jobs",
                          name="Offline camera assembly", description="Synthetic tool proof; not product output.")
    rows = jobs.load_job(root)["images"]
    images = {row["original_name"]: root / row["file"] for row in rows}
    hashes = {str(path): C.sha256_file(path) for path in [glb, *images.values()]}
    roles = {"Front": "frame", "Temple_R": "temple", "Temple_L": "temple"}
    procs.write_json_atomic(out / "parts.json", {"origin_mm": [0., 4., 2.], "parts": [
        {"name": n, "role": roles[n], "V": V.tolist(), "F": F.tolist()} for n, V, F in parts]})
    seed = out / "seed.blend"
    build = blender.run(None, blender.G3D / "tests/blender/wp2_synthetic_scene.py",
                        [out / "parts.json", seed], timeout_s=300)
    assert seed.is_file(), build.stdout[-1500:] + build.stderr[-1500:]
    session = BlenderSession(root, seed_blend=seed, calls_dir=root / "tools")
    try:
        with LoopbackOnly(), ProductionTransportTripwire(), single_threaded_blas():
            session.start()
            seeing = Seeing(session)
            head = dict(session.head)
            workers = fit_workers()
            fit = seeing.fit_cameras("mixed new-photo-set proof", images,
                {r["original_name"]: r["view_kind"] for r in rows}, front_parts=("Front",), workers=workers,
                candidates={r["original_name"]: tuple(r["fit_kinds"]) for r in rows},
                matte_modes={r["original_name"]: r.get("matte_mode") for r in rows})
            procs.write_json_atomic(out / "fit.json", fit)
            assert fit["status"] == "ok" and set(fit["usable_refs"]) == {"front", "rear"}, fit
            assert set(fit["unusable_refs"]) == {"blank"} and set(fit["refs"]) == {"front", "rear", "blank"}
            basis = C.load_basis(root)
            frozen = {str(p): C.sha256_file(p) for p in basis.dir.rglob("*") if p.is_file()}
            for name, (yaw, pitch, _, _) in truth.items():
                rid = C.ref_id(name)
                f, cam = basis.refs[rid]["fit"], basis.camera(rid)
                assert f["iou"] > .97 and f["contour_mean_px"] < 1.5, (rid, f)
                assert abs(cam.yaw - yaw) < 1.5 and abs(cam.pitch - pitch) < 1.5, (rid, cam)
            assert np.array_equal(basis.matte("rear"), M.matte(M.load_photo(images["rear.png"]), mode="glossy_floor"))
            compared = seeing.compare_to_photos(passes=(), basis=basis.id)
            assert compared["status"] == "ok" and set(compared["refs"]) == {"front", "rear"}, compared
            assert set(compared["unusable_refs"]) == {"blank"}
            refused = seeing.compare_to_photos(refs=["blank"], passes=(), basis=basis.id)
            assert refused["code"] == "E_UNUSABLE_CAMERA", refused
            failed = seeing.fit_cameras("all-failed diagnostic refit", images,
                {r["original_name"]: r["view_kind"] for r in rows}, refs=["blank"],
                front_parts=("Front",), workers=workers)
            assert failed["status"] == "refused" and failed["code"] == "E_FIT_ALL", failed
            assert C.load_basis(root).id == basis.id
            assert C.load_basis(root, failed["basis"]).usable_refs() == []
            assert all(C.sha256_file(p) == digest for p, digest in frozen.items())
            assert session.head == head and session.fingerprint() == head["fingerprint"]
        assert all(C.sha256_file(p) == digest for p, digest in hashes.items())
        result = {"status": "passed", "paid_calls": 0, "job": str(root), "workers": workers,
                  "fit": fit, "compare": compared, "explicit_failed_ref": refused, "all_failed": failed,
                  "source_files_unchanged": True, "old_basis_unchanged": True, "head_unchanged": True,
                  "seconds": time.monotonic() - started}
        procs.write_json_atomic(out / "proof.json", result)
        print(json.dumps({k: v for k, v in result.items() if k not in {"fit", "compare", "explicit_failed_ref", "all_failed"}}, indent=1))
        return result
    finally:
        session.close()


if __name__ == "__main__":
    os.environ["G3D_OFFLINE"] = "1"
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "OPENAI_ADMIN_KEY"):
        os.environ.pop(key, None)
    run()
