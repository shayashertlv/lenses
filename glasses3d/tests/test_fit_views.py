"""The WP3b driver's tables, reference-data checks and refusal rule (fast; the 12-view run itself is the CLI)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from glasses3d.g3d_host import cameras, fit_views
from glasses3d.g3d_host.camera import Camera

# PLAN.md §5, verbatim order (reference-0N follows the job's images[] order)
PLAN_S5 = {"ft1119": ["angled", "rear", "side", "top", "front", "elevated side"],
           "prada": ["side", "angled", "top", "front", "side", "rear"]}


def test_view_tables_follow_plan_section_5():
    for key, labels in PLAN_S5.items():
        prod = fit_views.PRODUCTS[key]
        assert list(prod.views.values()) == labels, key
        assert [n.split(".")[0] for n in prod.views] == [f"reference-0{i}" for i in range(1, 7)], key
        assert set(prod.n_refine) <= set(prod.views)
    assert fit_views.PRODUCTS["ft1119"].job == "d4b716a6afc445f89cbed8beddad4c37"
    assert fit_views.PRODUCTS["prada"].job == "8c82d986e6c14d26b4b38f3807712ea0"


def test_every_label_maps_to_known_views():
    used = {lab for labels in PLAN_S5.values() for lab in labels}
    assert used | {'rear_threequarter', 'bottom'} == set(fit_views.LABEL_CANDIDATES)
    for label, views in fit_views.LABEL_CANDIDATES.items():
        assert views and all(v in cameras.VIEWS for v in views), label
    assert fit_views.LABEL_CANDIDATES["side"] == ("right", "left")
    assert fit_views.LABEL_CANDIDATES["top"] == ("top", "topb")
    assert fit_views.LABEL_CANDIDATES["bottom"] == ("bottom", "bottomb")
    assert fit_views.LABEL_CANDIDATES["elevated side"] == cameras.TOP_OBLIQUE_VIEWS
    assert fit_views.CROSS_RECEIVERS == ("angled",)
    # the receivers' candidates are never cross sources, and no sign-fixed label's candidate seeds anything
    assert all(v in cameras.NOT_CROSS_SOURCES for lab in fit_views.CROSS_RECEIVERS
               for v in fit_views.LABEL_CANDIDATES[lab])
    assert all(v in cameras.NOT_CROSS_SOURCES for lab in fit_views.LABEL_PITCH_SIGN
               for v in fit_views.LABEL_CANDIDATES[lab])


def test_sign_check_refuses_a_camera_below_for_top_labels():
    below = Camera(0.0, -57.3, 0.0, 0.1, 1000.0, 0.0, 0.0)
    above = Camera(0.0, 59.9, 0.0, 0.1, 1000.0, 0.0, 0.0)
    assert "contradicts label 'top'" in fit_views.sign_check("top", below)
    assert fit_views.sign_check("top", above) is None
    assert fit_views.sign_check("elevated side", Camera(90.0, 0.0, 0.0, 0.1, 1, 0, 0)) is not None
    assert fit_views.sign_check("rear", below) is None and fit_views.sign_check("side", below) is None


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _fake_job(tmp_path: Path, product: str) -> tuple[Path, Path]:
    prod = fit_views.PRODUCTS[product]
    job = tmp_path / "job"
    (job / "images").mkdir(parents=True)
    images = []
    for i, name in enumerate(prod.views):
        data = f"photo {i}".encode()
        (job / "images" / name).write_bytes(data)
        images.append({"name": name, "sha256": _sha(data), "view": "unknown"})
    (job / "job.json").write_text(json.dumps({"id": prod.job, "images": images}))
    model = tmp_path / "model_src.glb"
    model.write_bytes(b"glTF fake")
    return job, model


def test_import_then_verify_and_fail_on_missing_or_changed_files(tmp_path):
    root = tmp_path / "reference"
    with pytest.raises(fit_views.ReferenceDataMissing):
        fit_views.reference_files("prada", root)
    job, model = _fake_job(tmp_path, "prada")
    man = fit_views.import_references("prada", job, model, root)
    assert man["files"]["reference-06.png"]["label"] == "rear"          # PLAN §5, not job.json's "unknown"
    files = fit_views.reference_files("prada", root)
    assert set(files) == set(fit_views.PRODUCTS["prada"].views) | {"model.glb"}
    fit_views.import_references("prada", job, model, root)              # idempotent on identical content
    (root / "prada" / "reference-03.png").write_bytes(b"edited")
    with pytest.raises(fit_views.ReferenceDataError, match="reference-03.png: differs"):
        fit_views.reference_files("prada", root)
    with pytest.raises(ValueError, match="refusing to overwrite"):
        fit_views.import_references("prada", job, model, root)
    (root / "prada" / "reference-03.png").unlink()
    with pytest.raises(fit_views.ReferenceDataError, match="reference-03.png: missing"):
        fit_views.reference_files("prada", root)


def test_import_checks_the_jobs_own_sha256(tmp_path):
    job, model = _fake_job(tmp_path, "ft1119")
    (job / "images" / "reference-02.png").write_bytes(b"not the photo the job recorded")
    with pytest.raises(ValueError, match="reference-02.png: sha256"):
        fit_views.import_references("ft1119", job, model, tmp_path / "reference")


def test_parse_candidates():
    got = fit_views._parse_candidates(["reference-06.png=top_oblique_left, top_oblique_right,left"])
    assert got == {"reference-06.png": ("top_oblique_left", "top_oblique_right", "left")}
    assert fit_views._parse_candidates(["reference-06.png=bottom"]) == {"reference-06.png": ("bottom",)}
    with pytest.raises(SystemExit):
        fit_views._parse_candidates(["reference-06.png=diagonal"])


@pytest.mark.skipif(not fit_views.manifest_path("prada").exists(), reason="Prada reference data never imported")
def test_prada_front_parts_exist_in_the_delivery():
    files = fit_views.reference_files("prada")
    mesh, pivot = fit_views.load_mesh("prada", files)
    assert len(mesh.skipped) == 7 and all(any(s in n for s in fit_views.PRODUCTS["prada"].skip) for n in mesh.skipped)
    piv_mm = mesh.frame.to_mm(pivot[None])[0]
    assert abs(piv_mm[0]) < 1e-6 and -6.0 < piv_mm[2] < 0.0                 # centred, at the front piece
