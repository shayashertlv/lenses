"""agent/seeing.py gain views (WP6_FACETS §3.3): which photo of each colourway sets the job's gains.

* gain_view_rank = the record's model fit IoU, or -1 without one; gain_views picks the highest per colourway, ties
  keeping the basis order;
* a colourway photo whose camera was transported by image similarity has no fit IoU (even with a near-perfect
  photo-to-photo transport IoU in its fit_status, and with a model_check block beside it, which is not the fit IoU)
  and never displaces one with a fit IoU, so adding the transported black photos (black-angled, black-front) changes
  no gain: black-side stays the black gain view and reference-04 the target one; with no fitted photo in its colourway
  a transported one still serves;
* on the real WP6 v2 basis (tests/proofs/wp6v2_basis.py's check job, skipped when absent): the gain views are
  reference-04 and black-side, and B2 / B3 are variant-colourway photos with a pose, a supplied matte, the source
  photo's theta and a camera that differs from the source's by a similarity only (yaw, pitch, perspective kept);
  their supplied matte is the white-background one and black-side's the dark-studio one (review 2026-10-11 P1: the
  dark matte drops B3's lit temple tops); each twin's record carries model_check {iou, temple_iou, against} and no
  fit IoU, its registration matte is the candidate that agrees best with the source matte (B3: the white-background
  matte, about a pixel), and its temple IoU on the original model is within TWIN_TEMPLE_IOU_SLACK of its source
  photo's (report.json).
No Blender: gain_views only builds the worker views.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from glasses3d.agent.seeing import GAIN_VIEW_MAX_PX, Seeing
from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host.camera import Camera, NormFrame

G3D = Path(__file__).resolve().parents[1]
CHECK_JOB = G3D / "data" / "wp6v2" / "basis" / "check_job"
CAM = {"yaw": 90.0, "pitch": 10.0, "roll": 0.0, "perspective": 0.2, "scale": 1000.0, "center_x": 600.0,
       "center_y": 300.0}


class FakeBasis:
    """The Basis surface gain_views reads: refs (fit, colourway, shape, camera), frame, pose."""

    is_usable = C.Basis.is_usable
    usable_refs = C.Basis.usable_refs
    unusable_refs = C.Basis.unusable_refs

    def __init__(self, refs: dict):
        self.refs = refs
        self.frame = NormFrame((0.0, -4.0, -72.0), 151.0)

    def colourway(self, rid):
        return self.refs[rid].get("colourway") or "target"

    def camera(self, rid):
        return Camera.from_dict(self.refs[rid]["camera"])

    def shape(self, rid):
        return tuple(self.refs[rid]["shape"])

    def pose(self, rid):
        return {"+1": {"theta_deg": 0.0, "axis_xz_mm": [64.6, -10.1]},
                "-1": {"theta_deg": 0.0, "axis_xz_mm": [-64.6, -10.1]}}


def ref(iou=None, colourway="target", shape=(700, 1200), **extra):
    return {"camera": CAM, "shape": list(shape), "colourway": colourway,
            "fit": {"status": "ok", "iou": iou, "contour_mean_px": None}, **extra}


def picked(bs) -> dict:
    geo, views = Seeing.gain_views(bs, (0.0, 0.0, 0.0))
    assert [v["name"] for v in views] == [f"gain-{g['rid']}" for g in geo.values()]
    return {cw: g["rid"] for cw, g in geo.items()}


MODEL_CHECK = {"iou": 0.9771, "temple_iou": 0.886, "against": "original model, white-background matte"}


def test_rank_is_the_model_fit_iou_or_minus_one():
    assert Seeing.gain_view_rank(ref(0.883)) == 0.883
    assert Seeing.gain_view_rank(ref(None)) == -1.0 and Seeing.gain_view_rank({}) == -1.0
    assert Seeing.gain_view_rank(ref(0.0)) == 0.0                               # a measured 0 still beats none
    assert Seeing.gain_view_rank(ref(None, model_check=MODEL_CHECK)) == -1.0     # a model check is not the fit IoU


def test_transported_black_photos_change_no_gain_view():
    """The WP6 v2 set-up: B2 / B3 carry a transport IoU (in fit_status) and a model check above black-side's fit IoU,
    never a fit IoU."""
    refs = {f"reference-0{i}": ref(iou) for i, iou in enumerate((0.873, 0.926, 0.880, 0.969, 0.879, 0.922), 1)}
    refs["black-side"] = ref(0.883, "variant", (554, 1607), fit_status="camera transported from 05")
    before = picked(FakeBasis(dict(refs)))
    assert before == {"target": "reference-04", "variant": "black-side"}
    for rid in ("black-angled", "black-front"):
        refs[rid] = ref(None, "variant", (1280, 1920), fit_status=f"camera transported from 02 by image similarity "
                                                                    f"(contour 0.82 px, IoU 0.992)",
                        model_check=MODEL_CHECK)
    assert picked(FakeBasis(refs)) == before
    order = {k: refs[k] for k in ("black-angled", "black-front", *[k for k in refs if not k.startswith("black-a")
                                                                   and k != "black-front"])}
    assert picked(FakeBasis(order)) == before                                  # whatever the basis order


def test_a_transported_photo_serves_when_its_colourway_has_no_fitted_one_and_ties_keep_order():
    refs = {"reference-01": ref(0.9), "black-angled": ref(None, "variant", (1280, 1920)),
            "black-front": ref(None, "variant", (1280, 1920))}
    geo, _ = Seeing.gain_views(FakeBasis(refs), (0.0, 0.0, 0.0))
    assert geo["variant"]["rid"] == "black-angled"                             # the first of equals
    assert geo["variant"]["k"] == pytest.approx(GAIN_VIEW_MAX_PX / 1920)          # scaled to <= GAIN_VIEW_MAX_PX
    assert geo["variant"]["shape"] == (round(1280 * GAIN_VIEW_MAX_PX / 1920), GAIN_VIEW_MAX_PX)
    refs["reference-02"] = ref(0.9)
    assert picked(FakeBasis(refs))["target"] == "reference-01"


def test_real_basis_gain_views_and_the_black_cameras():
    if not (CHECK_JOB / C.CAMERAS_DIR).is_dir():
        pytest.skip("the WP6 v2 basis check job is missing (tests/proofs/wp6v2_basis.py makes it)")
    from glasses3d.tests.proofs import wp6v2_basis as WB
    bs = C.load_basis(CHECK_JOB)
    assert picked(bs) == {"target": "reference-04", "variant": "black-side"}
    assert [r for r in bs.refs if bs.colourway(r) == "variant"] == ["black-side", "black-angled", "black-front"]
    assert bs.refs["black-side"]["matte_note"] == WB.MATTES["dark_studio"][1]     # its floor reflection: dark matte
    doc = json.loads((bs.dir / "cameras.json").read_text(encoding="utf-8"))
    report = json.loads((CHECK_JOB.parent / "report.json").read_text(encoding="utf-8"))
    assert report["gain_views"] == {"target": "reference-04", "variant": "black-side"}
    for rid, src in (("black-angled", "reference-02"), ("black-front", "reference-04")):
        rec, s = bs.refs[rid], bs.refs[src]
        assert rec["matte_mode"] == "supplied" and rec["view_kind"] == s["view_kind"] and bs.pose(rid)
        assert rec["matte_note"] == WB.MATTES["white_background"][1]
        assert rec["theta_deg"] == s["theta_deg"] and rec["theta_observed"] == {"+1": False, "-1": False}
        assert rec["fit_status"].startswith(f"camera transported from {src[-2:]} by image similarity")
        assert rec["fit"]["iou"] is None                                       # no fit IoU: never the gain view
        for k in ("yaw", "pitch", "perspective"):
            assert rec["camera"][k] == s["camera"][k]
        full = next(r for n, r in doc["images"].items() if C.ref_id(n) == rid)
        t = full["transport"]
        assert t["source"] == bs.refs[src]["image"] and t["termination"] == "parameter_step_converged"
        assert rec["camera"]["scale"] == pytest.approx(s["camera"]["scale"] * t["scale"])
        # the registration matte: the candidate whose contour agrees best with the source matte
        cands = t["registration_candidates"]
        assert set(cands) == {"white_background", "dark_studio"}
        best = min(cands, key=lambda n: cands[n]["mean_bidirectional_px"])
        assert t["target_matte"].startswith(WB.matte_label(best))
        assert full["camera"] == cands[best]["camera"]
        assert t["mean_bidirectional_px"] == cands[best]["mean_bidirectional_px"] <= 1.5
        assert t["mask_iou_after_warp"] >= 0.98
        # the $0 model check: beside the transport, never the top-level fit IoU
        assert "iou" not in full and full["model_check"] == report["model_checks"][rid]
        assert full["model_check"]["against"] == "original model, white-background matte"
        assert set(full["model_check"]) == {"iou", "temple_iou", "against"}
        assert all(0.0 < full["model_check"][k] <= 1.0 for k in ("iou", "temple_iou"))
        assert full["model_check"]["temple_iou"] >= \
            report["model_checks"][src]["temple_iou"] - WB.TWIN_TEMPLE_IOU_SLACK
    # B3 (review 2026-10-11 P1): 04's matte keeps the temple tops, so B3 registers onto the white-background matte
    front = next(r for n, r in doc["images"].items() if C.ref_id(n) == "black-front")["transport"]
    assert front["target_matte"].startswith("white-background (the supplied matte")
