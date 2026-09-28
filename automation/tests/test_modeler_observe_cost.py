"""The cost of an observation without a change of measurement (modeler.observe, modeler/blender/harness.py).

No Blender runs here. The observation is exercised with a fake render harness that records the job it receives, a
scripted camera fit (the BSA multi-start fit is the host's own instrument and is measured elsewhere), and a tiny
box mesh; the Blender harness's render loop runs against a fake ``bpy`` so the per-view EEVEE settings it applies can
be read back. Facts from the first paid run (test-pilot-001): 11 EEVEE renders at 32 samples took 543 s of a
12-minute build; no metric reads those renders (IoU, contour mm and the lens outline come from the host rasterizer,
the wearer views from the AR renderer); the held-out render was made even for revisions that had failed the contract.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
from unittest import mock

import numpy as np
from PIL import Image
import pytest

from modeler import observe
from modeler.paths import BLENDER_DIR
from reconstruction.camera import Camera

# a short root for the harness output (Windows path limit): LENSES_TEST_TMP when set, else the system temp folder;
# created by the fixture, never at import
LAG_ROOT = Path(os.environ.get("LENSES_TEST_TMP") or tempfile.gettempdir()) / "lag-observe-cost"
FRONT_ID, HELD_ID = "img0001", "img0009"


def png_size(path) -> tuple[int, int]:
    """An image's size with its file closed (an open handle keeps Windows from removing the test folder)."""
    with Image.open(path) as im:
        return im.size


@pytest.fixture
def tmp():
    LAG_ROOT.mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(dir=LAG_ROOT))
    yield d
    shutil.rmtree(d, ignore_errors=True)


# --------------------------------------------------------------------------- a candidate without Blender
def write_box_candidate(root: Path) -> dict:
    """parts.npz + materials.json of one closed box named front_plate (part frame), as the harness exports them."""
    V = np.array([[x, y, z] for x in (-70.0, 70.0) for y in (-25.0, 25.0) for z in (-3.0, 3.0)], np.float32)
    F = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]], np.int32)
    build = root / "build"
    build.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(build / "parts.npz", front_plate__V=V, front_plate__F=F, front_plate__M=np.zeros(len(F), np.int32))
    (build / "materials.json").write_text(json.dumps({"materials": {}, "objects": {"front_plate": {"object": "front_plate", "part": "frame", "component": "front", "materials": []}},
                                                      "declarations": {}}), encoding="utf-8")
    (build / "candidate.blend").write_bytes(b"BLENDER-not-really")
    return {"parts_npz": str(build / "parts.npz"), "materials_json": str(build / "materials.json"), "blend": str(build / "candidate.blend")}


def write_evidence(root: Path) -> tuple[dict, Path]:
    """One author-visible front photo and one sealed held-out angled photo, with their mattes and the author crop."""
    ev_dir = root / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    H, W = 60, 90
    fg = np.zeros((H, W), bool)
    fg[15:45, 10:80] = True
    np.savez_compressed(ev_dir / "masks.npz", **{f"fg_{FRONT_ID}": fg, f"fg_{HELD_ID}": fg})
    photos = {}
    for vid in (FRONT_ID, HELD_ID):
        p = ev_dir / f"{vid}.jpg"
        Image.fromarray(np.full((H, W, 3), 200, np.uint8)).save(p, quality=90)
        photos[vid] = p
    author = {"path": str(photos[FRONT_ID]), "crop_xyxy": [0, 0, W, H], "scale": 1.0, "size": [W, H]}
    evidence = {"product_id": "unit", "views": {FRONT_ID: {"view": "front", "author_photo": author}}, "held_out": {HELD_ID: {"view": "angled"}},
                "inputs": [{"id": FRONT_ID, "view": "front", "path": str(photos[FRONT_ID]), "held_out": False},
                           {"id": HELD_ID, "view": "angled", "path": str(photos[HELD_ID]), "held_out": True}],
                "front": None}
    return evidence, ev_dir


def scripted_fit(calls: list):
    """A stand-in for observe.fit_view: records the view ids it was asked to fit and answers a fixed frozen camera."""
    def fit_view(view, fg, scene, frame, pivot_mm, warm=None):
        calls.append(view)
        cam = Camera(yaw=0.0 if view == "front" else 35.0, pitch=0.0, roll=0.0, perspective=0.0, scale=60.0, center_x=fg.shape[1] / 2, center_y=fg.shape[0] / 2)
        out = {"camera": cam.to_dict(), "loss": 0.1, "px_per_mm": 0.5, "roi": [0, 0, fg.shape[1], fg.shape[0]], "evaluations": 1, "seconds": 0.0,
               "iou": 0.9, "contour_mean_px": 1.0, "contour_p95_px": 2.0, "contour_mean_mm": 2.0, "contour_p95_mm": 4.0, "contour_mean_pct_width": 1.0,
               "photo_only_px": 3, "render_only_px": 4, "extent": [0, 0, 1, 1]}
        return out, fg.copy()
    return fit_view


class RecordingHarness:
    """A ``worker.run_harness`` stand-in: keeps every job, writes a flat PNG per requested render like the real harness."""
    def __init__(self):
        self.jobs: list[dict] = []

    def __call__(self, job: dict, out_dir: Path, *, time_limit_s: int = 300) -> dict:
        self.jobs.append(json.loads(json.dumps(job)))
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        for spec in job["renders"]:
            p = out_dir / f"{spec['id']}.png"
            Image.new("RGBA", (int(spec["width"]), int(spec["height"])), (90, 90, 90, 255)).save(p)
            rows.append({"id": spec["id"], "path": str(p), "kind": spec["kind"], "width": spec["width"], "height": spec["height"], "camera": spec["camera"], "error": None, "seconds": 0.0})
        return {"ok": True, "renders": rows, "error": None, "process": {"returncode": 0, "seconds": 0.0, "error": None}}


def observe_with(root: Path, monkeypatch, *, heldout: bool | None) -> tuple[dict, RecordingHarness, list, Path]:
    build = write_box_candidate(root)
    evidence, ev_dir = write_evidence(root)
    calls: list = []
    monkeypatch.setattr(observe, "fit_view", scripted_fit(calls))
    harness = RecordingHarness()
    cand = root / "cand"
    kwargs = dict(held_out_ids=set(evidence["held_out"]), ar=False, glb_path=None, render_harness=harness)
    if heldout is not None:
        kwargs["heldout"] = heldout
    obs = observe.observe_candidate(cand, build, evidence, ev_dir, **kwargs)
    return obs, harness, calls, cand


# --------------------------------------------------------------------------- the observation's render job
class TestObservationRenderJob:
    def test_the_render_job_asks_for_16_samples(self, tmp, monkeypatch):
        obs, harness, _, _ = observe_with(tmp, monkeypatch, heldout=None)
        assert len(harness.jobs) == 1
        job = harness.jobs[0]
        assert job["samples"] == 16 == observe.RENDER_SAMPLES and job["mode"] == "render_only"
        assert obs["render_result"]["ok"] is True

    def test_canonical_specs_are_540_by_360_and_keep_their_views(self):
        specs = observe.canonical_render_specs()
        assert [s["id"] for s in specs] == [v["id"] for v in observe.CANONICAL_VIEWS]
        assert all((s["width"], s["height"]) == (540, 360) == observe.CANONICAL_RENDER_SIZE for s in specs)
        assert {s["kind"] for s in specs} == {"clay", "textured"} and all(s["transparent"] is True for s in specs)
        assert [s["camera"]["yaw"] for s in specs] == [v["yaw"] for v in observe.CANONICAL_VIEWS]
        assert observe.canonical_render_specs(width=720, height=480)[0]["width"] == 720        # the size stays a parameter

    def test_540_by_360_lands_at_the_same_size_on_the_sheets_as_720_by_480(self, tmp):
        """The grid thumbnails to its cell (360 clay / 480 textured): both sizes show as 360x240 and 480x320."""
        for w, h in ((720, 480), (540, 360)):
            p = tmp / f"r_{w}.png"
            Image.new("RGBA", (w, h), (10, 10, 10, 255)).save(p)
            for cell in (360, 480):
                im = Image.open(p).convert("RGBA")
                im.thumbnail((cell, cell), Image.LANCZOS)
                assert im.size == (cell, int(cell * 2 / 3)), (w, h, cell, im.size)
        clay = observe.grid([("a", tmp / "r_540.png")] * 3, cols=3)
        assert clay.size == (3 * 368, 368)
        tex = observe.grid([("a", tmp / "r_540.png")] * 2, cols=2, cell=480)
        assert tex.size == (2 * 488, 488)

    def test_the_sheets_are_still_made_from_the_smaller_renders(self, tmp, monkeypatch):
        obs, harness, _, cand = observe_with(tmp, monkeypatch, heldout=None)
        for key in ("clay", "textured", "photo_match"):
            assert key in obs["sheets"] and Path(obs["sheets"][key]).is_file(), key
        assert png_size(obs["sheets"]["clay"]) == (3 * 368, 2 * 368)   # 5 clay views on a 3-column grid of 360 px cells
        assert png_size(obs["sheets"]["textured"]) == (2 * 488, 488)


# --------------------------------------------------------------------------- heldout=False
class TestHeldoutSkip:
    def test_default_observes_the_held_out_view_and_seals_its_render(self, tmp, monkeypatch):
        obs, harness, calls, cand = observe_with(tmp, monkeypatch, heldout=None)
        assert calls == ["front", "angled"]
        ids = [r["id"] for r in harness.jobs[0]["renders"]]
        assert f"heldout_{HELD_ID}" in ids and f"match_{FRONT_ID}" in ids
        held_spec = next(r for r in harness.jobs[0]["renders"] if r["id"] == f"heldout_{HELD_ID}")
        assert held_spec["kind"] == "textured" and held_spec["camera"]["type"] == "photo"
        assert "heldout_skipped" not in obs                                   # nothing else changes when heldout=True
        assert f"heldout_{HELD_ID}" not in obs["renders"]                      # never author-visible
        assert (cand / "heldout" / f"heldout_{HELD_ID}.png").is_file() and not (cand / "observe" / "renders" / f"heldout_{HELD_ID}.png").exists()
        held = json.loads((cand / "heldout" / "heldout.json").read_text(encoding="utf-8"))
        assert set(held["views"]) == {HELD_ID} and held["views"][HELD_ID]["view"] == "angled" and "heldout_skipped" not in held
        assert held["summary"]["mean_contour_mm_all_fit_views"] == 2.0 and HELD_ID in held["renders"]
        assert HELD_ID not in obs["views"] and set(obs["views"]) == {FRONT_ID}

    def test_heldout_false_skips_the_fit_and_the_render_and_records_it(self, tmp, monkeypatch):
        obs, harness, calls, cand = observe_with(tmp, monkeypatch, heldout=False)
        assert calls == ["front"]                                               # no held-out camera fit
        ids = [r["id"] for r in harness.jobs[0]["renders"]]
        assert not any(i.startswith("heldout_") for i in ids), ids
        assert f"match_{FRONT_ID}" in ids and all(v["id"] in ids for v in observe.CANONICAL_VIEWS)
        assert obs["heldout_skipped"] is True
        assert json.loads((cand / "observe" / "observation.json").read_text(encoding="utf-8"))["heldout_skipped"] is True
        held = json.loads((cand / "heldout" / "heldout.json").read_text(encoding="utf-8"))
        assert held["views"] == {} and held["renders"] == {} and held["heldout_skipped"] is True
        assert held["summary"].get("mean_contour_mm_all_fit_views") is None     # an unmeasured gate, never a number
        assert not list((cand / "heldout").glob("*.png")) and not list((cand / "heldout").glob("*.jpg"))
        assert harness.jobs[0]["samples"] == 16

    def test_the_author_visible_observation_is_identical_either_way(self, monkeypatch):
        LAG_ROOT.mkdir(parents=True, exist_ok=True)
        a, b = Path(tempfile.mkdtemp(dir=LAG_ROOT)), Path(tempfile.mkdtemp(dir=LAG_ROOT))
        try:
            obs_t, h_t, _, _ = observe_with(a, monkeypatch, heldout=True)
            obs_f, h_f, _, _ = observe_with(b, monkeypatch, heldout=False)
            visible = [r for r in h_t.jobs[0]["renders"] if not r["id"].startswith("heldout_")]
            assert visible == h_f.jobs[0]["renders"]
            assert h_t.jobs[0]["samples"] == h_f.jobs[0]["samples"]
            strip = lambda o: {k: v for k, v in o.items() if k not in ("seconds", "sheets", "renders", "heldout_skipped")}   # noqa: E731 - paths differ per folder
            assert strip(obs_t) == strip(obs_f)
            assert obs_t["views"] == obs_f["views"] and obs_t["summary"] == obs_f["summary"]
            assert sorted(obs_t["sheets"]) == sorted(obs_f["sheets"]) and sorted(obs_t["renders"]) == sorted(obs_f["renders"])
        finally:
            shutil.rmtree(a, ignore_errors=True)
            shutil.rmtree(b, ignore_errors=True)

    def test_heldout_is_a_keyword_with_a_true_default(self):
        import inspect
        p = inspect.signature(observe.observe_candidate).parameters["heldout"]
        assert p.default is True and p.kind is inspect.Parameter.KEYWORD_ONLY


# --------------------------------------------------------------------------- summary.temple_see_through (author RULE 59)
def observe_with_see_through(root: Path, monkeypatch, result: dict) -> dict:
    """observe_candidate with the AR check on: the harness, the lens colour metric and the see-through metric faked,
    the last answering ``result`` (its render paths are written as small PNGs first)."""
    from modeler import lens_colour, see_through
    for r in (result, result.get("temple_see_through") or {}):
        for k, p in (r.get("renders") or {}).items():
            Path(p).parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (40, 30), (200, 150, 120) if k == "skin" else (60, 80, 110)).save(p)
    glb = root / "model.glb"
    glb.write_bytes(b"glTF-not-really")
    monkeypatch.setattr(observe.archeck, "run", lambda *a, **kw: {"models": {"candidate": {"runtime_compatible": True, "renders": []}}})
    monkeypatch.setattr(lens_colour, "lens_colour_metric", lambda *a, **kw: {"status": "not_applicable"})
    monkeypatch.setattr(see_through, "frame_see_through_metric", lambda *a, **kw: json.loads(json.dumps(result)))
    build = write_box_candidate(root)
    evidence, ev_dir = write_evidence(root)
    monkeypatch.setattr(observe, "fit_view", scripted_fit([]))
    return observe.observe_candidate(root / "cand", build, evidence, ev_dir, held_out_ids=set(evidence["held_out"]), ar=True, glb_path=glb,
                                     render_harness=RecordingHarness())


def temple_result(root: Path, status: str = "measured") -> dict:
    t = {"status": status, "view": "angled", "translucent_materials": ["crystal"], "near_temple": "temple_L", "clip_zm": -0.115}
    if status == "measured":
        t.update({"see_through": 0.796, "per_channel": [0.796, 0.796, 0.795], "temple_rgb_on_skin": [199.4, 166.1, 139.1],
                  "temple_rgb_on_blue": [84.1, 96.9, 114.5], "temple_pixels": 757, "bare_fixture_pixels": 370,
                  "renders": {k: str(root / f"ar_see_through_{k}" / "angled.png") for k in ("skin", "blue")}})
    return t


class TestTempleSeeThroughSummary:
    def test_a_measured_temple_is_summarised_beside_a_measured_front(self, tmp, monkeypatch):
        result = {"status": "measured", "see_through": 0.71, "per_channel": [0.7, 0.71, 0.72], "frame_rgb_on_skin": [190.0, 160.0, 140.0],
                  "frame_pixels": 900, "renders": {k: str(tmp / f"ar_see_through_{k}" / "front.png") for k in ("skin", "blue")},
                  "translucent_materials": ["crystal"], "temple_see_through": temple_result(tmp)}
        obs = observe_with_see_through(tmp, monkeypatch, result)
        s = obs["summary"]
        assert s["frame_see_through"]["see_through"] == 0.71
        assert s["temple_see_through"] == {"see_through": 0.796, "per_channel": [0.796, 0.796, 0.795], "temple_rgb_on_skin": [199.4, 166.1, 139.1],
                                           "temple_pixels": 757, "view": "angled", "near_temple": "temple_L"}
        assert png_size(obs["sheets"]["see_through"]) == (40, 4 * 30 + 3 * 8)   # two front tiles, then the two temple tiles
        saved = json.loads((tmp / "cand" / "observe" / "observation.json").read_text(encoding="utf-8"))
        assert saved["summary"]["temple_see_through"]["see_through"] == 0.796

    @pytest.mark.parametrize("front_status", ["no_frame_pixels", "no_frame_projection"])
    def test_the_temple_is_summarised_when_the_front_has_no_number(self, tmp, monkeypatch, front_status):
        result = {"status": front_status, "frame_pixels": 12, "translucent_materials": ["crystal"], "temple_see_through": temple_result(tmp)}
        obs = observe_with_see_through(tmp, monkeypatch, result)
        assert obs["summary"]["frame_see_through"] == {"status": front_status, "error": None}
        assert obs["summary"]["temple_see_through"]["see_through"] == 0.796
        assert png_size(obs["sheets"]["see_through"]) == (40, 2 * 30 + 8)      # the temple tiles alone

    def test_a_failed_temple_is_summarised_as_its_status(self, tmp, monkeypatch):
        result = {"status": "no_frame_pixels", "frame_pixels": 12, "translucent_materials": ["crystal"],
                  "temple_see_through": dict(temple_result(tmp, "failed"), error="ValueError: x")}
        obs = observe_with_see_through(tmp, monkeypatch, result)
        assert obs["summary"]["temple_see_through"] == {"status": "failed", "error": "ValueError: x"}
        assert "see_through" not in obs["sheets"]

    def test_opaque_temples_leave_the_key_absent(self, tmp, monkeypatch):
        result = {"status": "no_frame_pixels", "frame_pixels": 12, "translucent_materials": ["crystal"],
                  "temple_see_through": {"status": "not_applicable"}}
        obs = observe_with_see_through(tmp, monkeypatch, result)
        assert "temple_see_through" not in obs["summary"]
        assert obs["summary"]["frame_see_through"]["status"] == "no_frame_pixels"

    def test_unmeasurable_translucent_temples_are_summarised_as_their_status(self, tmp, monkeypatch):
        result = {"status": "no_frame_pixels", "frame_pixels": 12, "translucent_materials": ["crystal"],
                  "temple_see_through": {"status": "no_temple_pixels", "temple_pixels": 3}}
        obs = observe_with_see_through(tmp, monkeypatch, result)
        assert obs["summary"]["temple_see_through"] == {"status": "no_temple_pixels"}
        assert obs["summary"]["frame_see_through"]["status"] == "no_frame_pixels"


# --------------------------------------------------------------------------- summary.lens_colour (author RULE: lens colour)
FLASH_LENS = {"kind": "lens", "base_color_linear": [0.75, 0.725, 0.67], "roughness": 0.055, "transmission": 1.0,
              "lens": {"transmission_top_rgb": [0.75, 0.725, 0.67], "transmission_bottom_rgb": [0.75, 0.725, 0.67], "profile": "smooth",
                       "reflectance_rgb": [0.23, 0.2, 0.15], "mirror": True, "angular": None, "roughness": 0.055}}


class ArRecorder:
    """An ``archeck.run`` stand-in: records each call's background and views, writes one front render per call (a lens-coloured
    box on the fixture colour, or on the checker for the default call) and answers a compatible row listing it."""
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, models, out, **kw):
        from bsa import archeck
        self.calls.append({"out": Path(out).name, "background": kw.get("background", "checker"), "colour": kw.get("background_color"),
                           "views": [v["id"] for v in kw.get("ar_views", ())]})
        Path(out).mkdir(parents=True, exist_ok=True)
        if kw.get("background") == "solid":
            c = kw["background_color"].lstrip("#")
            img = np.broadcast_to(np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], np.uint8), (480, 720, 3)).copy()
        else:
            img = archeck.checker_background(720, 480).astype(np.uint8)
        img[200:280, 250:470] = (150, 130, 110)
        p = Path(out) / "candidate__actual-ar__front.png"
        Image.fromarray(img).save(p)
        return {"models": {"candidate": {"runtime_compatible": True, "renders": [str(p)]}}}


def observe_lens(root: Path, monkeypatch, lens_result: dict | None = None, materials: dict | None = None):
    """observe_candidate with the AR check on for a candidate whose materials carry ``materials`` (a flash lens by default):
    ``archeck.run`` recorded, the lens colour metric's arguments captured (it answers ``lens_result``)."""
    from modeler import lens_colour
    build = write_box_candidate(root)
    mj = Path(build["materials_json"])
    meta = json.loads(mj.read_text(encoding="utf-8"))
    meta["materials"] = {"Flash": FLASH_LENS} if materials is None else materials
    mj.write_text(json.dumps(meta), encoding="utf-8")
    glb = root / "model.glb"
    glb.write_bytes(b"glTF-not-really")
    ar = ArRecorder()
    monkeypatch.setattr(observe.archeck, "run", ar)
    seen: list = []

    def metric(*a, **kw):
        seen.append((a, kw))
        return json.loads(json.dumps(lens_result or {"status": "no_photo_lens"}))
    monkeypatch.setattr(lens_colour, "lens_colour_metric", metric)
    evidence, ev_dir = write_evidence(root)
    monkeypatch.setattr(observe, "fit_view", scripted_fit([]))
    obs = observe.observe_candidate(root / "cand", build, evidence, ev_dir, held_out_ids=set(evidence["held_out"]), ar=True, glb_path=glb,
                                    render_harness=RecordingHarness())
    return obs, ar, seen


def measured_lens(root: Path, **over) -> dict:
    p = root / "over_backdrop.png"
    img = np.full((480, 720, 3), 255, np.uint8)
    img[200:280, 250:470] = (212, 195, 174)
    Image.fromarray(img).save(p)
    d = {"status": "measured", "basis": "two_fixture_fit", "hue_error": 0.02, "saturation_ratio": 0.35, "value_ratio": 1.1,
         "photo_hsv": [0.092, 0.178, 0.836], "predicted_hsv": [0.113, 0.062, 0.916], "mirrored": True, "match": False,
         "flags": ["too_light", "too_pale"], "photo_rgb": [213.2, 196.1, 175.2], "predicted_rgb": [233.9, 229.2, 219.6],
         "backdrop_rgb": [255.0, 255.0, 255.0], "lens_transmission_effective": [0.745, 0.718, 0.664],
         "lens_reflection": [0.077, 0.067, 0.049], "lens_transmission_recommended": [0.59, 0.486, 0.38],
         "lens_transmission_upper_bound": [0.667, 0.553, 0.43], "lens_env_intensity_recommended": None,
         "lens_env_intensity_note": "not recommended for a transmissive lens", "render_over_backdrop": str(p)}
    d.update(over)
    return d


class TestLensColourFixtures:
    def test_every_lens_gets_the_two_solid_fixture_renders(self, tmp, monkeypatch):
        # an opaque-framed product (no translucent material): the fixtures used to run only for crystal fronts, and the lens
        # colour came from the checker render
        obs, ar, seen = observe_lens(tmp, monkeypatch)
        solid = [c for c in ar.calls if c["background"] == "solid"]
        assert [c["colour"] for c in solid] == ["#cba68d", "#3a4f6e"]
        assert all(c["views"] == ["front"] for c in solid), "opaque temples: the front view only"
        assert [c["background"] for c in ar.calls].count("checker") == 1, "the checker render stays, for the AR sheet"
        (glb, rendered, evidence, materials_json), _ = seen[0]
        assert rendered["status"] == "rendered"
        assert sorted(rendered["dirs"]) == ["blue", "skin"]
        assert all(Path(d).name.startswith("ar_see_through_") for d in rendered["dirs"].values())
        assert obs["frame_see_through"] == {"status": "not_applicable"}

    def test_no_lens_and_no_translucent_material_costs_no_fixture_render(self, tmp, monkeypatch):
        obs, ar, seen = observe_lens(tmp, monkeypatch, materials={"Black": {"kind": "acetate", "base_color_linear": [0.02, 0.02, 0.02]}})
        assert [c["background"] for c in ar.calls] == ["checker"]
        assert seen and seen[0][0][1]["status"] == "not_applicable"

    def test_the_summary_carries_the_prediction_and_the_recommendation(self, tmp, monkeypatch):
        obs, _, _ = observe_lens(tmp, monkeypatch, measured_lens(tmp))
        lc = obs["summary"]["lens_colour"]
        assert (lc["value_ratio"], lc["saturation_ratio"], lc["match"], lc["flags"]) == (1.1, 0.35, False, ["too_light", "too_pale"])
        assert lc["lens_transmission_recommended"] == [0.59, 0.486, 0.38]
        assert lc["lens_transmission_upper_bound"] == [0.667, 0.553, 0.43]
        assert lc["backdrop_rgb"] == [255.0, 255.0, 255.0]
        assert lc["basis"] == "two_fixture_fit"
        assert "lens_env_intensity_recommended" not in obs["summary"], "under-determined for a transmissive lens: absent"

    def test_the_lens_backdrop_sheet_shows_the_lens_over_the_photo_backdrop_beside_the_photo(self, tmp, monkeypatch):
        # its own sheet since 2026-09-28 (the final evaluator gets it too); until then a row stacked under the AR sheet
        obs, _, _ = observe_lens(tmp, monkeypatch, measured_lens(tmp))
        with Image.open(obs["sheets"]["lens_backdrop"]) as sheet:
            assert sheet.size[1] == 360
            row = np.asarray(sheet.convert("RGB"))
        assert (np.abs(row.astype(int) - (212, 195, 174)).sum(-1) < 6).any(), "the lens over the photo's backdrop is on the sheet"
        assert obs["lens_colour"]["sheet"] == "lens_backdrop"
        assert png_size(obs["sheets"]["ar"])[1] == observe.AR_SHEET_TILE_HEIGHT, "the AR sheet keeps its runtime views only"

    def test_a_failed_fixture_render_is_recorded_for_the_lens(self, tmp, monkeypatch):
        from modeler import see_through
        monkeypatch.setattr(see_through, "render_fixtures", lambda *a, **kw: {"status": "render_failed", "fixture": "skin", "error": "boom"})
        obs, _, seen = observe_lens(tmp, monkeypatch)
        assert seen[0][0][1] == {"status": "render_failed", "fixture": "skin", "error": "boom"}


# --------------------------------------------------------------------------- summary.appearance (modeler.appearance)
GOLD = {"kind": "metal", "base_color_linear": [0.7011, 0.5271, 0.2307], "metallic": 1.0, "roughness": 0.23}


def appearance_record(sheet: Path) -> dict:
    return {"status": "measured", "reliable": True, "reason": None, "flags": ["metal_hue_off:Gold"], "harness_seconds": 17.1, "seconds": 22.0,
            "materials": {"Gold": {"role": "metal", "status": "measured", "photo": {"hue": 0.09, "saturation": 0.24, "rgb": [183, 164, 142], "pixels": 900},
                                   "render": {"hue": 0.117, "saturation": 0.36, "rgb": [186, 167, 118], "pixels": 90},
                                   "deltas": {"hue": -0.026, "saturation_ratio": 0.66, "per_view": {"front": {"hue": -0.026}}},
                                   "recommended": {"base_color_srgb": [218, 178, 132]}, "flags": ["metal_hue_off"], "views": ["front"]}},
            "hardware_area": {"endpiece": {"photo_share": 0.125, "render_share": 0.22, "ratio": 1.65, "flag": "hardware_heavy", "views": {"front": 1.45}}},
            "sheet": str(sheet)}


class TestAppearanceInTheObservation:
    """Every metal and crystal material is measured against the author photos (modeler.appearance): the observation calls it
    once per build after the fixture runs, with the author-visible views only, and carries its compact summary (the shared
    interface summary.appearance) and its 'material_match' sheet."""

    def test_the_record_reaches_the_summary_and_the_sheet(self, tmp, monkeypatch):
        from modeler import appearance
        sheet = tmp / "material_match.png"
        Image.new("RGB", (20, 10), (200, 180, 150)).save(sheet)
        seen = []

        def metric(obs_dir, glb, **kw):
            seen.append((obs_dir, glb, kw))
            return json.loads(json.dumps(appearance_record(sheet)))
        monkeypatch.setattr(appearance, "appearance_metric", metric)
        obs, ar, _ = observe_lens(tmp, monkeypatch, materials={"Flash": FLASH_LENS, "Gold": GOLD})
        assert len(seen) == 1
        obs_dir, glb, kw = seen[0]
        assert Path(obs_dir) == tmp / "cand" / "observe" and Path(glb).name == "model.glb"
        assert set(kw["views"]) == {FRONT_ID}, "author-visible views only: the held-out view never reaches the instrument"
        assert kw["materials"]["Gold"]["kind"] == "metal" and kw["evidence"]["product_id"] == "unit"
        assert kw["width_mm"] == 140.0 and kw["sheet_png"] == tmp / "cand" / "observe" / "sheet_material_match.png"
        s = obs["summary"]["appearance"]
        assert s == appearance.summary_of(appearance_record(sheet))
        assert s["materials"]["Gold"]["deltas"] == {"hue": -0.026, "saturation_ratio": 0.66}
        assert s["hardware_area"]["endpiece"]["flag"] == "hardware_heavy" and s["reliable"] is True
        assert obs["sheets"]["material_match"] == str(sheet)
        assert obs["appearance"]["harness_seconds"] == 17.1
        saved = json.loads((tmp / "cand" / "observe" / "observation.json").read_text(encoding="utf-8"))
        assert saved["summary"]["appearance"]["flags"] == ["metal_hue_off:Gold"]

    def test_no_metal_or_crystal_leaves_the_key_absent_and_costs_no_harness_run(self, tmp, monkeypatch):
        obs, ar, _ = observe_lens(tmp, monkeypatch)                  # a lens only: the real instrument, not applicable
        assert "appearance" not in obs["summary"] and "material_match" not in obs["sheets"]
        assert obs["appearance"]["status"] == "not_applicable"
        assert [c["background"] for c in ar.calls] == ["checker", "solid", "solid"], "the checker run and the two lens fixtures only"

    def test_a_failure_is_recorded_never_fatal(self, tmp, monkeypatch):
        from modeler import appearance

        def boom(*a, **kw):
            raise RuntimeError("boom")
        monkeypatch.setattr(appearance, "appearance_metric", boom)
        obs, _, _ = observe_lens(tmp, monkeypatch, materials={"Flash": FLASH_LENS, "Gold": GOLD})
        s = obs["summary"]["appearance"]
        assert s["reliable"] is False and s["status"] == "failed" and "boom" in s["reason"]
        assert "lens_colour" in obs and obs["summary"]["ar_runtime_compatible"] is not None

    def test_without_the_ar_check_nothing_is_measured(self, tmp, monkeypatch):
        from modeler import appearance
        monkeypatch.setattr(appearance, "appearance_metric", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not run")))
        obs, _, _, _ = observe_with(tmp, monkeypatch, heldout=None)
        assert obs["appearance"] is None and "appearance" not in obs["summary"]


# --------------------------------------------------------------------------- the Blender harness's per-view EEVEE settings
class FakeEevee:
    """Only the two EEVEE properties the harness touches; anything else it might set is a bug this fake would raise on."""
    __slots__ = ("taa_render_samples", "use_raytracing")

    def __init__(self):
        self.taa_render_samples = None
        self.use_raytracing = None


def load_harness_with_fake_bpy():
    """modeler/blender/harness.py imported against a fake bpy/mathutils/glasses_lib: nothing of Blender is needed to read
    which settings its render loop applies. Returns (module, scene, render calls)."""
    scene = mock.MagicMock(name="scene")
    scene.eevee = FakeEevee()
    calls = []
    bpy = mock.MagicMock(name="bpy")
    bpy.context.scene = scene
    bpy.app.version_string = "5.2.0 (fake)"

    def render(write_still=True):
        calls.append({"path": scene.render.filepath, "raytracing": scene.eevee.use_raytracing, "samples": scene.eevee.taa_render_samples,
                      "size": (scene.render.resolution_x, scene.render.resolution_y), "engine": scene.render.engine})
    bpy.ops.render.render.side_effect = render
    mathutils = types.ModuleType("mathutils")
    mathutils.Matrix = mock.MagicMock(name="Matrix")
    mathutils.Vector = mock.MagicMock(name="Vector")
    gl = types.ModuleType("glasses_lib")
    gl.objects = lambda: []
    saved = {k: sys.modules.get(k) for k in ("bpy", "mathutils", "glasses_lib")}
    sys.modules.update({"bpy": bpy, "mathutils": mathutils, "glasses_lib": gl})
    try:
        spec = importlib.util.spec_from_file_location("lenses_modeler_harness_under_test", BLENDER_DIR / "harness.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    mod._fake = {"bpy": bpy, "gl": gl}       # keep the fakes alive for the module's late imports
    return mod, scene, calls


def orbit_spec(vid: str, kind: str, yaw: float = 0.0) -> dict:
    return {"id": vid, "kind": kind, "width": 64, "height": 48, "transparent": True,
            "camera": {"type": "orbit", "yaw": yaw, "pitch": 0, "roll": 0, "ortho": True, "px_per_mm": 2.0, "target": [0.0, 0.0, 0.0]}}


class TestHarnessRaytracingPerKind:
    def test_raytracing_only_on_textured_views(self, tmp):
        mod, scene, calls = load_harness_with_fake_bpy()
        sys.modules["glasses_lib"] = mod._fake["gl"]          # render_views imports it by name
        try:
            no_kind = orbit_spec("no_kind", "textured")
            no_kind.pop("kind")
            specs = [orbit_spec("clay_front", "clay"), orbit_spec("tex_front", "textured"), orbit_spec("clay_right", "clay", -90),
                     orbit_spec("tex_three_quarter", "textured", 35), orbit_spec("match_img0001", "textured"), no_kind]
            rows = mod.render_views(specs, str(tmp), samples=16)
        finally:
            sys.modules.pop("glasses_lib", None)
        assert [r["id"] for r in rows] == [s["id"] for s in specs] and all(r["error"] is None for r in rows)
        assert [c["raytracing"] for c in calls] == [False, True, False, True, True, True]     # a missing kind is textured, as before
        assert all(c["samples"] == 16 for c in calls) and all(c["engine"] == "BLENDER_EEVEE" for c in calls)
        assert all(c["size"] == (64, 48) for c in calls)
        assert [Path(c["path"]).name for c in calls] == [f"{s['id']}.png" for s in specs]

    def test_the_flag_is_reset_per_view_not_once(self, tmp):
        """A textured view after a clay one must trace again: the setting is applied inside the loop, per spec."""
        mod, scene, calls = load_harness_with_fake_bpy()
        sys.modules["glasses_lib"] = mod._fake["gl"]
        try:
            mod.render_views([orbit_spec("t1", "textured"), orbit_spec("c1", "clay"), orbit_spec("t2", "textured")], str(tmp), samples=4)
        finally:
            sys.modules.pop("glasses_lib", None)
        assert [c["raytracing"] for c in calls] == [True, False, True] and all(c["samples"] == 4 for c in calls)

    def test_a_version_without_the_property_is_left_alone(self):
        mod, scene, _ = load_harness_with_fake_bpy()

        class NoRaytracing:
            __slots__ = ("taa_render_samples",)
        scene.eevee = NoRaytracing()
        mod.set_raytracing(scene, True)                    # no AttributeError, nothing set
        assert not hasattr(scene.eevee, "use_raytracing")

    def test_harness_default_samples_is_16(self):
        import inspect
        mod, _, _ = load_harness_with_fake_bpy()
        assert inspect.signature(mod.render_views).parameters["samples"].default == 16
        src = (BLENDER_DIR / "harness.py").read_text(encoding="utf-8")
        assert 'int(job.get("samples", 16))' in src
