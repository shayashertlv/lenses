"""WP2 stage B, host only: camera bases and comparison metrics (g3d_host/compare.py, WP2_DESIGN §6).

* importing the frozen WP3b Prada cameras.json freezes basis-01 (mattes once, ROI checked, photos sha-checked); a
  basis is never overwritten, a changed matte or photo is refused, the next import is basis-02;
* the host metrics on the Studio delivery GLB (the model the basis was fitted to) reproduce cameras.json exactly for
  all six references (the Blender half of the chain is in test_wp2_seeing.py);
* deltas, sheets and the parts image behave as documented.
Data-dependent (Prada WP3b cameras and reference photos): skipped, or failed under G3D_REQUIRE_DATA=1."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host import fitmesh

G3D = Path(__file__).resolve().parents[1]
CAMERAS_JSON = G3D / "data" / "wp3b" / "prada" / "cameras.json"
PHOTOS = G3D / "data" / "reference" / "prada"
REQUIRE_DATA = os.environ.get("G3D_REQUIRE_DATA") == "1"


def need(ok: bool, why: str) -> None:
    if not ok:
        (pytest.fail if REQUIRE_DATA else pytest.skip)(why)


@pytest.fixture(scope="module")
def imported(tmp_path_factory):
    need(CAMERAS_JSON.is_file() and (PHOTOS / "model.glb").is_file(), "WP3b Prada data missing")
    root = tmp_path_factory.mktemp("wp2compare")
    doc = json.loads(CAMERAS_JSON.read_text(encoding="utf-8"))
    photos = {n: PHOTOS / n for n in doc["images"]}
    b = C.import_cameras_json(root, CAMERAS_JSON, photos, reason="frozen WP3b basis")
    return {"root": root, "doc": doc, "photos": photos, "basis": b}


def test_import_freezes_basis_01(imported):
    b, doc = imported["basis"], imported["doc"]
    assert b.id == "basis-01" and C.list_bases(imported["root"]) == ["basis-01"]
    assert b.meta["schema"] == C.BASIS_SCHEMA and b.meta["notes"] == [] and b.meta["reason"] == "frozen WP3b basis"
    assert b.meta["cameras_json_sha256"] == C.sha256_file(CAMERAS_JSON)
    assert b.skip == ("hallmark", "etch", "ribbon") and len(b.front_parts) == 3
    assert list(b.refs) == [f"reference-0{i}" for i in range(1, 7)]
    for name, rec in doc["images"].items():
        r = b.refs[C.ref_id(name)]
        assert r["camera"] == rec["camera"] and r["roi"] == rec["roi"] and r["shape"] == rec["shape"]
        assert r["photo_sha256"] == rec["image_sha256"] and r["fit"]["iou"] == rec["iou"]
        assert b.matte(C.ref_id(name)).sum() == r["fg_px"] == rec["matte"]["foreground_px"]
        assert (b.dir / r["overlay"]).is_file()


def test_host_metrics_reproduce_cameras_json_on_the_fitted_glb(imported):
    b, doc = imported["basis"], imported["doc"]
    mesh = fitmesh.load_fit_mesh(PHOTOS / "model.glb", skip=b.skip, frame=b.frame)
    for name, rec in doc["images"].items():
        m, masks = C.reference_metrics(mesh, b, C.ref_id(name))
        a = m["all"]
        assert a["iou"] == rec["iou"] and a["contour_mean_px"] == rec["contour_mean_px"], name
        assert a["contour_p90_px"] == rec["contour_p90_px"] and a["contour_p95_px"] == rec["contour_p95_px"], name
        assert a["contour_mean_mm"] == pytest.approx(rec["contour_mean_mm"], rel=1e-12)
        assert a["model_only_px"] == rec["render_only_px"] and a["photo_only_px"] == rec["photo_only_px"]
        assert m["px_per_mm"] == pytest.approx(rec["px_per_mm_at_front_pivot"], rel=1e-12)
        assert 0.5 < m["front"]["iou"] <= 1.0 and masks["mask"].shape == tuple(rec["shape"])


def test_a_basis_is_frozen(imported, tmp_path):
    root, photos = imported["root"], imported["photos"]
    with pytest.raises(FileExistsError):
        C.import_cameras_json(root, CAMERAS_JSON, photos, reason="again", basis_id="basis-01")
    b2 = C.import_cameras_json(root, CAMERAS_JSON, photos, reason="again")
    assert b2.id == "basis-02" and C.load_basis(root).id == "basis-02" and C.load_basis(root, "basis-01").id == \
        "basis-01"
    p = b2.dir / b2.refs["reference-01"]["matte"]
    Image.fromarray(np.zeros_like(np.asarray(Image.open(p)))).save(p)
    with pytest.raises(ValueError, match="matte differs"):
        C.load_basis(root, "basis-02").matte("reference-01")
    bad = dict(photos)
    bad["reference-01.webp"] = photos["reference-02.webp"]
    with pytest.raises(ValueError, match="sha256"):
        C.import_cameras_json(tmp_path, CAMERAS_JSON, bad, reason="wrong photo")
    with pytest.raises(C.NoBasis, match="fit_cameras"):
        C.load_basis(tmp_path / "empty")
    shutil.rmtree(b2.dir)


def test_deltas_sheets_and_parts_image(imported):
    cur = {"all": {"iou": 0.8, "contour_mean_mm": 1.0, "contour_p90_mm": 2.0, "iou_full_image": 0.79}}
    prev = {"all": {"iou": 0.9, "contour_mean_mm": 0.5, "contour_p90_mm": 2.5, "iou_full_image": 0.9}}
    d = C.deltas(cur, prev)
    assert d["all"]["iou"] == pytest.approx(-0.1) and d["all"]["contour_p90_mm"] == pytest.approx(-0.5)
    assert C.deltas(cur, None) is None
    tall, wide = Image.new("RGBA", (300, 900), (1, 2, 3, 255)), Image.new("RGBA", (900, 120), (1, 2, 3, 255))
    g = C.grid([wide, wide, tall, tall], 2, max_px=10_000, gap=6)
    assert g.size == (900 + 900 + 18, 120 + 900 + 18)                       # rows as tall as their own tallest
    s = C.contact_sheet([("a", [tall, tall]), ("b", [wide])] * 4)
    assert max(s.size) <= 2048
    b = imported["basis"]
    mesh = fitmesh.load_fit_mesh(PHOTOS / "model.glb", frame=b.frame)
    img, legend = C.parts_image(mesh, b.camera("reference-04"), b.shape("reference-04"))
    assert img.size == tuple(reversed(b.shape("reference-04"))) and len(legend) >= 5
    assert any(n.startswith("Front") for n in legend)
    box, names = C.part_box(mesh, b.camera("reference-01"), b.shape("reference-01"), "temple right")
    assert names and box[2] > box[0] and box[3] > box[1]
    with pytest.raises(ValueError, match="no part matches"):
        C.part_box(mesh, b.camera("reference-01"), b.shape("reference-01"), "nonexistent")
