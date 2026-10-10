"""g3d_host/lighting.py (the photo_env environment, WP6_FACETS §3.2 amended 2026-10-10).

Host only (always run):
* the lobes are a partition of unity (equal weights = a uniform world), the floor term is a horizon step, the
  equirect preview puts the camera at the centre column;
* NNLS with the smoothness penalty recovers known non-negative weights from a synthetic basis, never returns a
  negative weight (even when the least-squares answer is negative), weighs every photo the same, and the
  leave-one-photo-out CV picks a lambda from the declared grid;
* the Gauss-Newton right-hand side, the white-balance hue and the masked cell samples (nothing leaks in from
  outside the mask).

Real worker on a copy of the Prada scene (Blender 5.2 + data; skipped, or failed under G3D_REQUIRE_DATA=1):
* recovery: the scene rendered under known lobe weights at four photo cameras is fitted back from differential
  basis renders (two re-linearised steps): the weights come back and the direct render of the fit reproduces the
  target;
* the job's fitted environment (Seeing.photo_env, one iteration at a reduced crop size for test time) is frozen in
  work/compare/env-<basis>.json with its weights, CV table, residuals and photo shas, and the next call reuses it
  without rendering."""
from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d_host import lighting as L

G3D = Path(__file__).resolve().parents[1]
PRADA_SCENE = G3D.parent / "automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend"
BASIS = G3D / "data" / "wp6v2" / "basis" / "cameras.json"
REQUIRE_DATA = os.environ.get("G3D_REQUIRE_DATA") == "1"


def need(ok: bool, why: str) -> None:
    if not ok:
        (pytest.fail if REQUIRE_DATA else pytest.skip)(why)


# ------------------------------------------------------------------------------------------------ host only
def test_lobes_partition_of_unity_and_floor():
    rng = np.random.default_rng(0)
    e, c = rng.uniform(-90, 90, 500), rng.uniform(-1, 1, 500)
    M = L.lobe_matrix(e, c)
    assert M.shape == (500, L.N_LOBES) and (M >= 0).all()
    np.testing.assert_allclose(M[:, :-1].sum(1), 1.0, atol=1e-12)               # sectors x tents: uniform world
    assert L.LOBES[0] == "front-90" and L.LOBES[-1] == "floor" and len(L.LOBES) == 19
    np.testing.assert_allclose(L.floor_term([-30.0, -1.0, -0.5, 0.0, 10.0]), [1, 1, 0.5, 0, 0])
    w = np.zeros(L.N_LOBES)
    w[L.LOBES.index("front+12")] = 1.0
    img = L.env_image(w, 360, 180)
    row = int(round((90 - 12) / 180 * 180 - 0.5))
    assert np.argmax(img[row]) in (179, 180) and img[row, 0] == 0.0              # toward the camera = centre column
    assert L.smoothness().shape == (3 * 5 + 2 * 6, L.N_LOBES)


def test_nnls_recovers_known_weights_and_stays_non_negative():
    rng = np.random.default_rng(1)
    A = rng.uniform(0.0, 1.0, (600, L.N_LOBES))
    w_true = rng.uniform(0.0, 2.0, L.N_LOBES)
    w_true[[0, 5, 11]] = 0.0
    groups = np.repeat(["p1", "p2", "p3"], 200)
    fit = L.fit_env(A, A @ w_true, groups, lam=1e-9)
    np.testing.assert_allclose(fit["weights"], w_true, atol=1e-5)
    assert all(v["rel_rms"] < 1e-6 for v in fit["residuals"].values())
    b = A @ w_true - 3.0 * A[:, 4]                                             # least squares wants w[4] < 0
    w_ls = np.linalg.lstsq(A, b, rcond=None)[0]
    assert w_ls[4] < -0.5
    w = np.asarray(L.fit_env(A, b, groups, lam=1e-3)["weights"])
    assert (w >= 0).all() and w[4] == 0.0
    # every photo weighs the same: 10x more rows of one photo does not move the answer toward it
    A2 = np.vstack([A[:200]] * 10 + [A[200:]])
    g2 = np.concatenate([np.repeat("p1", 2000), groups[200:]])
    b2 = A2 @ w_true
    b2[:2000] *= 1.1                                                            # p1 disagrees by 10 %
    w2 = np.asarray(L.fit_env(A2, b2, g2, lam=1e-9)["weights"])
    w1 = np.asarray(L.fit_env(A, np.concatenate([b2[:200], b2[2000:]]), groups, lam=1e-9)["weights"])
    np.testing.assert_allclose(w2, w1, rtol=1e-6, atol=1e-6)


def test_cv_lambda_picks_from_the_grid():
    rng = np.random.default_rng(2)
    A = rng.uniform(0.0, 1.0, (300, L.N_LOBES))
    w = np.full(L.N_LOBES, 0.8)
    b = A @ w + rng.normal(0, 0.05, 300)
    groups = np.repeat(["a", "b", "c"], 100)
    lam, table = L.cv_lambda(A, b, groups)
    assert lam in L.LAMBDA_GRID and [t["lambda"] for t in table] == list(L.LAMBDA_GRID)
    assert all(set(t["held_out"]) == {"a", "b", "c"} for t in table)
    assert min(table, key=lambda t: t["cv_rel_rms"])["cv_rel_rms"] == pytest.approx(
        next(t for t in table if t["lambda"] == lam)["cv_rel_rms"])
    assert L.cv_lambda(A, b, np.repeat("a", 300)) == (L.LAMBDA_GRID[2], [])


def test_gauss_newton_white_balance_and_cells():
    rng = np.random.default_rng(3)
    B, w0, w = rng.uniform(0, 1, (50, 5)), rng.uniform(0, 1, 5), rng.uniform(0, 1, 5)
    f0 = B @ w0 + 0.3
    y = f0 + B @ (w - w0)
    np.testing.assert_allclose(B @ w, L.gauss_newton_rhs(y, f0, B, w0), atol=1e-12)
    g = np.array([0.5, 0.6, 0.9])
    ch = L.chroma(g)
    assert float(ch @ L.LUMA) == pytest.approx(1.0)
    lin = np.full((4, 4, 3), 0.2) * ch
    np.testing.assert_allclose(L.balanced_luminance(lin, ch), 0.2)
    img = np.zeros((80, 80))
    img[:, 40:] = 100.0                                                         # outside the mask: must not leak
    mask = np.zeros((80, 80), bool)
    mask[10:70, 5:38] = True
    pts, (vals,) = L.cell_samples([img + 1.0 * mask], mask, sigma_px=6.0, step_px=4.0)
    assert len(pts) and mask[pts[:, 0], pts[:, 1]].all()
    np.testing.assert_allclose(vals, 1.0, atol=1e-9)
    assert L.weights_sha(w) == L.weights_sha(list(w)) and len(L.weights_sha(w)) == 16


# ------------------------------------------------------------------------------------------------ real worker
def _rw(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


@pytest.fixture(scope="module")
def prada(tmp_path_factory):
    from glasses3d.g3d_host import blender
    if blender.blender_exe() is None:
        pytest.skip("Blender 5.2 not installed")
    need(PRADA_SCENE.is_file(), f"Prada scene missing: {PRADA_SCENE}")
    need(BASIS.is_file(), "the WP6 v2 basis is missing (tests/proofs/wp6v2_basis.py makes it)")
    root = tmp_path_factory.mktemp("lighting")
    seed = root / "seed.blend"
    shutil.copy2(PRADA_SCENE, seed)
    os.chmod(seed, stat.S_IWRITE | stat.S_IREAD)
    return {"root": root, "seed": seed, "doc": json.loads(BASIS.read_text(encoding="utf-8"))}


def test_known_weights_are_recovered_from_renders(prada):
    from glasses3d.agent.worker_client import WorkerClient
    from glasses3d.g3d_host import blender_camera as BC
    from glasses3d.g3d_host import compare as C
    from glasses3d.g3d_host.camera import Camera, NormFrame
    root, doc = prada["root"] / "recover", prada["doc"]
    (root / "work").mkdir(parents=True)
    shutil.copy2(prada["seed"], root / "seed.blend")
    frame = NormFrame.from_dict(doc["model"]["frame"])
    cams = {}
    for ref in ("reference-04", "reference-05", "reference-06", "reference-03"):
        rec = next(r for n, r in doc["images"].items() if Path(n).stem == ref)
        H, W = rec["shape"]
        k = 320 / max(H, W)
        cams[ref] = BC.to_blender(BC.crop_camera(Camera.from_dict(rec["camera"]), 0, 0, k), frame, (0, 0, 0),
                                  (round(H * k), round(W * k)))
    w_true = np.array([0.3, 0.5, 1.4, 1.1, 0.8, 0.7, 0.4, 0.5, 0.7, 1.0, 0.6, 0.5,
                       0.6, 0.4, 0.5, 1.5, 1.8, 0.9, 0.5])
    w = WorkerClient(root, start_blend=root / "seed.blend", selftest=True).start()
    try:
        def render(envs: dict, tag: str) -> dict:
            views = [{"name": f"{ref}--{n}", "camera": cam, "passes": ["native_linear"],
                      "env": {"weights": [float(x) for x in wt]}}
                     for ref, cam in cams.items() for n, wt in envs.items()]
            r = w.call("render", {"out_dir": str(root / "work" / tag), "views": views, "rig": "photo_env",
                                  "linear_format": "float", "engine": "eevee"}, deadline_s=600)
            assert r["unchanged"] and r["env_lobes"] == L.LOBES
            return {tuple(v["name"].split("--")): C.read_linear(v["files"]["native_linear"]["path"])
                    for v in r["views"]}
        target = render({"t": w_true}, "target")
        masks = {ref: C.erode_px(target[(ref, "t")][1] > 0.999, 2) for ref in cams}

        def cells(imgs: dict, name: str) -> np.ndarray:
            return np.concatenate([L.cell_samples([imgs[(ref, name)][0] @ L.LUMA], masks[ref], 1.0, 3.0)[1][0]
                                   for ref in cams])
        groups = np.concatenate([[ref] * len(L.cell_samples([masks[ref] * 1.0], masks[ref], 1.0, 3.0)[0])
                                 for ref in cams])
        y = cells(target, "t")
        w0 = np.full(L.N_LOBES, 0.8)
        base = render({"u": w0}, "base")
        f0 = cells(base, "u")
        for it in range(2):
            delta = 0.5 * float(np.mean(w0))
            basis = render({f"L{i:02d}": w0 + delta * np.eye(L.N_LOBES)[i] for i in range(L.N_LOBES)}, f"basis{it}")
            B = np.column_stack([(cells(basis, f"L{i:02d}") - f0) / delta for i in range(L.N_LOBES)])
            fit = L.fit_env(B, L.gauss_newton_rhs(y, f0, B, w0), groups, lam=1e-4)
            w0 = np.asarray(fit["weights"])
            f0 = cells(render({"w": w0}, f"direct{it}"), "w")
        res = L.residual_stats(f0, y, groups)
    finally:
        w.close()
    assert (w0 >= 0).all()
    assert max(v["rel_rms"] for v in res.values()) < 0.01, res               # the fit reproduces the target
    err = np.abs(w0 - w_true)
    print("recovered weights |error|:", dict(zip(L.LOBES, np.round(err, 3))), "residuals", res)
    assert np.median(err) < 0.1 and float(np.mean(err)) < 0.15, err


def test_job_environment_is_frozen_and_reused(prada, monkeypatch):
    from glasses3d.agent import seeing as SG
    from glasses3d.agent.journal import BlenderSession
    from glasses3d.g3d_host import compare as C
    monkeypatch.setattr(SG, "ENV_ITERATIONS", 1)
    monkeypatch.setattr(SG, "ENV_FIT_MAX_PX", 320)
    root = prada["root"] / "job"
    root.mkdir()
    shutil.copy2(prada["seed"], root / "seed.blend")
    doc = prada["doc"]
    C.import_cameras_json(root, BASIS, {n: Path(r["image"]) for n, r in doc["images"].items()}, reason="lighting")
    s = BlenderSession(root, seed_blend=root / "seed.blend", calls_dir=root / "tools")
    s.start()
    try:
        see = SG.Seeing(s)
        r = see.photo_env()
        assert r["status"] == "ok", r
        rec = json.loads(Path(r["path"]).read_text(encoding="utf-8"))
        assert rec["schema"] == L.SCHEMA and rec["lobes"] == L.LOBES and len(rec["weights"]) == L.N_LOBES
        assert min(rec["weights"]) >= 0 and rec["lambda"] in L.LAMBDA_GRID and len(rec["cv"]) == len(L.LAMBDA_GRID)
        cal = {c["photo"] for c in rec["calibration"]}
        assert cal <= {"reference-02", "reference-03", "reference-04", "reference-06"} and "reference-04" in cal
        bs = C.load_basis(root)
        for c in rec["calibration"]:
            assert c["photo_sha256"] == bs.refs[c["photo"]]["photo_sha256"] and c["erode_native_px"] >= 12
        it = rec["iterations"][0]
        assert set(it["direct_residuals"]) == cal and it["direct_mean_rel_rms"] < 0.2
        assert rec["fit_on"].startswith("front acetate")
        renders = sorted((root / "work" / "compare" / f"env-{bs.id}-fit").rglob("*.npz"))
        again = see.photo_env()
        assert again["status"] == "ok" and again["weights"] == r["weights"]
        assert sorted((root / "work" / "compare" / f"env-{bs.id}-fit").rglob("*.npz")) == renders
        assert s.fingerprint() == s.head["fingerprint"]
    finally:
        s.close()
        shutil.rmtree(root / "checkpoints", onerror=_rw)
