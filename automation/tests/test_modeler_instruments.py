"""The measurement instruments behind the observation and the status (review of test-pilot-002, 2026-09-28).

Facts from the second paid run (Tom Ford crystal panto, owner: 'other than the lens color the model is perfect'):
- the lens-outline gate (0.8 mm) read 0.846 -> 1.969 mm over six revisions whose lens meshes were byte-identical from
  r0002: the 'Lens print' glyphs (part frame, in front of the lens) punched holes in the first-hit lens mask, and the
  temple extension at r0003 renormalised the NormFrame and moved the whole-silhouette front camera (pitch 15.7 -> 17.0);
- the back camera fell into a near-orthographic basin at r0004 (yaw 186, perspective 0.005, contour 3.24 mm vs 1.60)
  on a silhouette that had not changed, and every warm start kept it: the parent camera was never evaluated as it was;
- the crystal photo mattes cover a fraction of the silhouette, so contour/IoU numbers measure matte holes and the
  mirror_iou_low input flag is a matte artefact;
- ar_temple_continuity read measured: false for a freshly measured asset; rear_angled was silently never fitted;
- the evaluator saw the lens only over a skin-toned stand-in of the lens tint's own hue.
Nothing here runs Blender or the AR harness: tiny meshes, the host rasterizer, recorded harness stand-ins.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from bsa import archeck, raster
from bsa import cameras as bcam
from bsa.core import NormFrame, project_mm, px_per_mm
from modeler import evaluate as mevaluate
from modeler import observe
from reconstruction.camera import Camera

SHAPE = (240, 320)


# --------------------------------------------------------------------------- tiny meshes
def disc(cx, cy, z, r, n=96):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    V = np.vstack([[cx, cy, z], np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t), np.full(n, z)])])
    F = np.array([[0, 1 + i, 1 + (i + 1) % n] for i in range(n)])
    return V, F


def annulus(cx, cy, z, r0, r1, n=96):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    inner = np.column_stack([cx + r0 * np.cos(t), cy + r0 * np.sin(t), np.full(n, z)])
    outer = np.column_stack([cx + r1 * np.cos(t), cy + r1 * np.sin(t), np.full(n, z)])
    F = []
    for i in range(n):
        j = (i + 1) % n
        F += [[i, n + i, n + j], [i, n + j, j]]
    return np.vstack([inner, outer]), np.array(F)


def box(x0, x1, y0, y1, z0, z1):
    V = np.array([[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)], float)
    F = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return V, F


def obj(VF, part, materials=("Black",)):
    V, F = VF
    return {"V": np.asarray(V, float), "F": np.asarray(F, np.int64), "M": np.zeros(len(F), np.int64), "part": part, "materials": list(materials)}


MATERIALS = {"Black": {"kind": "acetate"}, "Crystal": {"kind": "translucent"}, "Ink": {"kind": "pbr"}, "Lens": {"kind": "lens"}}


def lens_objects(*, print_=False, rim=None, temple_len=40.0):
    """One lens disc (r 20 mm at z 0), optionally a small print in front of it and a rim ring over its edge, and a
    temple box behind: the part/material layout the modeler's exporter writes."""
    o = {"Flash lens R": obj(disc(30.0, 0.0, 0.0, 20.0), "lens_R", ("Lens",)),
         "Temple R": obj(box(48.0, 52.0, 5.0, 8.0, -temple_len, -2.0), "temple_R")}
    if print_:
        # the glyphs: three thin bars in front of the lens, well inside it (a lens_print decal)
        for k in range(3):
            o[f"Lens print {k}"] = obj(box(22.0 + 5 * k, 24.0 + 5 * k, 6.0, 12.0, 0.5, 0.8), "frame", ("Ink",))
    if rim is not None:
        o["Front"] = obj(annulus(30.0, 0.0, 1.0, 17.0, 24.0), "frame", (rim,))
    return o


def lens_scene(objects, frame=None):
    V, F, part = observe.mesh_of(objects)
    frame = frame or observe.norm_frame(V)
    return V, F, part, frame, raster.RasterScene(V, F, frame)


def evidence_for(cam, frame, r=20.0):
    t = np.linspace(0, 2 * np.pi, 180, endpoint=False)
    ring = np.column_stack([30.0 + r * np.cos(t), r * np.sin(t), np.zeros_like(t)])
    return {"lenses": [{"outline_px": project_mm(ring, cam, frame).tolist()}], "flags": []}


def front_cam(frame, perspective=0.0, ppm=4.0, yaw=0.0, pitch=0.0):
    return raster.view_camera(frame, yaw, pitch, ppm, SHAPE, perspective=perspective, center_mm=np.array([30.0, 0.0, 0.0]))


def metric(objects, cam=None, frame=None, **kw):
    V, F, part, frame, scene = lens_scene(objects, frame)
    cam = cam or front_cam(frame)
    ev = evidence_for(cam, frame)
    return observe.lens_front_metrics(scene, np.isin(part, observe.LENS_PARTS), cam, frame, ev, SHAPE,
                                      face_object=observe.face_objects(objects), see_through_faces=observe.see_through_faces(objects, MATERIALS), **kw)


# --------------------------------------------------------------------------- the lens outline
class TestLensOutlineMetric:
    def test_a_print_on_the_lens_does_not_move_the_lens_outline(self):
        """r0006: 1.969 mm as drawn, 1.494 without the print: the glyph edges were counted as lens outline."""
        base = metric(lens_objects())
        frame = observe.norm_frame(observe.mesh_of(lens_objects(print_=True))[0])
        printed = metric(lens_objects(print_=True), frame=frame)
        plain = metric(lens_objects(), frame=frame)
        assert printed["counted_as_lens"]["decal_pixels"] > 0
        assert printed["contour_mean_px"] == pytest.approx(plain["contour_mean_px"], abs=1e-9)
        assert printed["as_drawn"]["contour_mean_px"] > plain["contour_mean_px"] + 0.3, "the old rule: glyph holes count as outline"
        assert base["contour_mean_px"] < 1.0

    def test_the_lens_under_a_see_through_rim_counts_as_the_photo_shows_it(self):
        frame = observe.norm_frame(observe.mesh_of(lens_objects(rim="Crystal"))[0])
        crystal = metric(lens_objects(rim="Crystal"), frame=frame)
        opaque = metric(lens_objects(rim="Black"), frame=frame)
        assert crystal["counted_as_lens"]["see_through_pixels"] > 0
        assert crystal["contour_mean_px"] == pytest.approx(crystal["lens_only"]["contour_mean_px"], abs=1e-9)
        # an opaque rim hides the lens edge in the photo too: the first-hit rule stays
        assert opaque["counted_as_lens"]["see_through_pixels"] == 0
        assert opaque["contour_mean_px"] == pytest.approx(opaque["as_drawn"]["contour_mean_px"], abs=1e-9)
        assert opaque["contour_mean_px"] > crystal["contour_mean_px"] + 1.0

    def test_a_large_frame_part_over_the_lens_is_not_a_decal(self):
        o = lens_objects()
        o["Brow"] = obj(box(5.0, 55.0, 5.0, 26.0, 1.0, 3.0), "frame")      # covers the lens's top third and beyond
        m = metric(o)
        assert m["counted_as_lens"]["decal_pixels"] == 0

    def test_millimetres_are_taken_at_the_lens_centre(self):
        """scale/extent is the magnification at the NormFrame centre (mid-temple); the lens sits nearer the camera."""
        objects = lens_objects(temple_len=120.0)
        V, F, part, frame, scene = lens_scene(objects)
        cam = front_cam(frame, perspective=0.3)
        m = metric(objects, cam=cam, frame=frame)
        lens_centre = V[np.unique(F[np.isin(part, observe.LENS_PARTS)])].mean(0)
        ppm = bcam.px_per_mm_at(cam, frame, lens_centre)
        assert m["px_per_mm"] == pytest.approx(ppm) and m["px_per_mm_rule"] == "lens_centre"
        assert m["contour_mean_mm"] == pytest.approx(m["contour_mean_px"] / ppm)
        assert ppm > 1.05 * px_per_mm(cam, frame)
        # as drawn = the instrument before 2026-09-28 (same pixels rule and millimetre rule) for comparison
        assert m["as_drawn"]["contour_mean_mm"] == pytest.approx(m["as_drawn"]["contour_mean_px"] / px_per_mm(cam, frame))

    def test_the_projection_is_reported(self):
        objects = lens_objects()
        V, F, part, frame, scene = lens_scene(objects)
        cam = front_cam(frame, pitch=16.9)
        m = metric(objects, cam=cam, frame=frame)
        pr = m["projection"]
        assert pr["pitch_deg"] == pytest.approx(16.9)
        assert pr["cos_pitch"] == pytest.approx(np.cos(np.radians(16.9)), abs=1e-4)
        assert len(pr["lens_aspect_hw"]["photo"]) == 1 and len(pr["lens_aspect_hw"]["render"]) == 1


class TestLensCamera:
    def test_the_first_front_camera_is_frozen_while_the_front_piece_keeps_its_box(self):
        frame = NormFrame((0.0, 0.0, -40.0), 120.0)
        cam0 = Camera(0.4, 15.7, 0.1, 0.15, 900.0, 160.0, 120.0).to_dict()
        first = observe.lens_camera_for(None, cam0, frame, [[-60, -30, -9], [60, 12, 0]])
        assert first["origin"] == "fitted" and first["camera"] == cam0
        prev = {"camera": cam0, "frame": frame.to_dict(), "lens_camera": first}
        cam1 = Camera(0.7, 17.0, 0.6, 0.12, 950.0, 161.0, 118.0).to_dict()       # the temple-driven refit
        frame1 = NormFrame((0.0, 0.0, -45.0), 130.0)
        again = observe.lens_camera_for(prev, cam1, frame1, [[-59.2, -30, -9.3], [59.2, 12, 0]])
        assert again["origin"] == "carried" and again["camera"] == cam0 and again["frame"] == frame.to_dict()
        moved = observe.lens_camera_for(prev, cam1, frame1, [[-66, -30, -9], [66, 12, 0]])
        assert moved["origin"] == "fitted" and moved["camera"] == cam1 and moved["frame"] == frame1.to_dict()

    def test_a_temple_edit_leaves_the_lens_outline_where_it_was(self, tmp_path, monkeypatch):
        """Two revisions with identical lens and front, longer temples in the second, and a front refit that moves
        with the temples: the lens outline must not move (regression: r0002 1.311 -> r0003 1.918 with no lens edit)."""
        cams = iter([Camera(0.0, 0.0, 0.0, 0.0, 480.0, 160.0, 120.0), Camera(0.0, 1.5, 0.0, 0.0, 492.0, 158.0, 125.0)])
        obs_a = observe_front(tmp_path / "a", monkeypatch, lens_objects(print_=True), next(cams))
        obs_b = observe_front(tmp_path / "b", monkeypatch, lens_objects(print_=True, temple_len=70.0), next(cams),
                              previous=obs_a["views"])
        la, lb = obs_a["views"]["front"]["lens_outline"], obs_b["views"]["front"]["lens_outline"]
        assert lb["camera"]["origin"] == "carried"
        assert lb["contour_mean_mm"] == pytest.approx(la["contour_mean_mm"], abs=1e-6)
        assert lb["current_camera"]["contour_mean_mm"] != pytest.approx(la["contour_mean_mm"], abs=1e-3), "the refit alone would have moved it"


# --------------------------------------------------------------------------- observation plumbing (scripted fit)
def write_candidate(root: Path, objects: dict, materials: dict = MATERIALS) -> dict:
    build = root / "build"
    build.mkdir(parents=True, exist_ok=True)
    arrays, meta = {}, {}
    for name, o in objects.items():
        arrays[f"{name}__V"] = o["V"].astype(np.float32)
        arrays[f"{name}__F"] = o["F"].astype(np.int32)
        arrays[f"{name}__M"] = o["M"].astype(np.int32)
        meta[name] = {"object": name, "part": o["part"], "component": name, "materials": o["materials"]}
    np.savez_compressed(build / "parts.npz", **arrays)
    (build / "materials.json").write_text(json.dumps({"materials": materials, "objects": meta, "declarations": {}}), encoding="utf-8")
    return {"parts_npz": str(build / "parts.npz"), "materials_json": str(build / "materials.json"), "blend": None}


def write_front_evidence(root: Path, cam: Camera, frame: NormFrame, *, views=("front",), photo=None, fg=None, flags=()) -> tuple[dict, Path]:
    ev_dir = root / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    H, W = SHAPE
    if fg is None:
        fg = np.zeros(SHAPE, bool)
        fg[40:200, 60:260] = True
    arrays, inputs, ev_views = {}, [], {}
    for vid in views:
        p = ev_dir / f"{vid}.png"
        Image.fromarray(photo if photo is not None else np.where(fg[..., None], 40, 255).astype(np.uint8).repeat(3, -1)).save(p)
        arrays[f"fg_{vid}"] = fg
        inputs.append({"id": vid, "view": vid, "path": str(p), "held_out": False})
        ev_views[vid] = {"view": vid, "backdrop_rgb": [255, 255, 255]}
    np.savez_compressed(ev_dir / "masks.npz", **arrays)
    front = evidence_for(cam, frame)
    front["flags"] = list(flags)
    return {"views": ev_views, "held_out": {}, "inputs": inputs, "front": front}, ev_dir


def observe_front(root: Path, monkeypatch, objects: dict, cam: Camera, *, previous=None, views=("front",), photo=None, fg=None, flags=()):
    """observe_candidate on a scripted front fit that answers ``cam`` (no EEVEE, no AR)."""
    build = write_candidate(root, objects)
    V = observe.mesh_of(objects)[0]
    frame = observe.norm_frame(V)
    if previous:
        frame = NormFrame.from_dict(next(iter(previous.values()))["frame"])
    evidence, ev_dir = write_front_evidence(root, cam, frame, views=views, photo=photo, fg=fg, flags=flags)

    def fit_view(view, fg_, scene, frame_, pivot_mm, warm=None, **kw):
        out = {"camera": cam.to_dict(), "loss": 0.1, "px_per_mm": px_per_mm(cam, frame_), "roi": [0, 0, SHAPE[1], SHAPE[0]], "evaluations": 1,
               "seconds": 0.0, "iou": 0.9, "contour_mean_px": 1.0, "contour_p95_px": 2.0, "contour_mean_mm": 0.5, "contour_p95_mm": 1.0,
               "contour_mean_pct_width": 1.0, "photo_only_px": 3, "render_only_px": 4, "extent": {}}
        return out, fg_.copy()
    monkeypatch.setattr(observe, "fit_view", fit_view)
    return observe.observe_candidate(root / "cand", build, evidence, ev_dir, held_out_ids=set(), previous_cameras=previous, ar=False,
                                     glb_path=None, render_harness=lambda *a, **k: {"ok": True, "renders": []})


class TestStickyFrame:
    def test_a_compatible_parent_frame_is_kept(self, tmp_path, monkeypatch):
        cam = Camera(0.0, 0.0, 0.0, 0.0, 480.0, 160.0, 120.0)
        a = observe_front(tmp_path / "a", monkeypatch, lens_objects(), cam)
        b = observe_front(tmp_path / "b", monkeypatch, lens_objects(temple_len=48.0), cam, previous=a["views"])
        assert b["frame"] == a["frame"] and b["frame_origin"] == "parent"
        assert b["views"]["front"]["frame"] == b["frame"], "every view record carries its frame (the child's warm start needs it)"

    def test_an_incompatible_parent_frame_is_replaced(self, tmp_path, monkeypatch):
        cam = Camera(0.0, 0.0, 0.0, 0.0, 480.0, 160.0, 120.0)
        prev = {"front": {"view": "front", "camera": cam.to_dict(), "frame": {"center": [0.0, 0.0, 0.0], "extent": 400.0}}}
        b = observe.sticky_frame(prev, observe.mesh_of(lens_objects())[0])
        assert b[1] == "own" and b[0].extent < 100.0


# --------------------------------------------------------------------------- camera fits
class TestCameraFit:
    def test_warm_seeds_are_the_prior_seeds_nearest_the_warm_camera(self):
        seeds = bcam.prior_seeds_near("back", 180.06, 4.4, 12)
        assert {round(s[0]) for s in bcam.prior_seeds("back")[:12]} == {168, 172}, "the old seeds[:12]: never the prior yaw itself"
        assert seeds[0][:2] == (180.0, 0.0) and seeds[1][:2] == (180.0, 10.0)
        assert {round(s[1]) for s in seeds} == {0, 10} and len(seeds) == 12

    @pytest.mark.parametrize("warm,refit,expected", [
        ({"loss": 0.2848, "contour_mean_px": 10.3}, {"loss": 0.2905, "contour_mean_px": 20.8}, "warm"),    # r0004 back
        ({"loss": 0.2528, "contour_mean_px": 8.6}, {"loss": 0.2498, "contour_mean_px": 9.8}, "warm"),      # better loss, worse contour
        ({"loss": 0.2528, "contour_mean_px": 8.6}, {"loss": 0.2498, "contour_mean_px": 8.1}, "refit"),
        ({"loss": 0.30, "contour_mean_px": 8.6}, {"loss": 0.31, "contour_mean_px": 6.0}, "warm")])
    def test_the_refit_replaces_the_warm_camera_only_when_it_wins_loss_and_contour(self, warm, refit, expected):
        assert observe.choose_camera(warm, refit) == expected

    def test_an_unchanged_silhouette_reuses_the_parent_camera_without_a_fit(self, monkeypatch):
        V, F, part, frame, scene = lens_scene(lens_objects())
        cam = front_cam(frame, perspective=0.2)
        fg = scene.render(cam, SHAPE, 1, None)["mask"]
        roi = observe.fit_roi(fg)
        met = bcam.native_metrics(scene, cam, fg, roi)
        parent = {"camera": cam.to_dict(), "frame": frame.to_dict(), "roi": list(roi), "photo_only_px": met["photo_only_px"],
                  "render_only_px": met["render_only_px"], "contour_mean_px": met["contour_mean_px"]}

        def boom(*a, **k):
            raise AssertionError("no fit may run on an unchanged silhouette")
        monkeypatch.setattr(bcam.ViewFit, "run_starts", boom)
        out, mask = observe.fit_view("front", fg, scene, frame, np.array([30.0, 0.0, 0.0]), cam, warm_record=parent)
        assert out["camera"] == cam.to_dict() and out["camera_source"] == "reused_parent"
        assert out["camera_delta_vs_parent"]["yaw"] == 0.0

    def test_a_worse_refit_keeps_the_warm_camera_verbatim(self, monkeypatch):
        """r0004 back: the parent camera (perspective 0.224) was re-aligned at perspective 0.1 and lost."""
        V, F, part, frame, scene = lens_scene(lens_objects())
        truth = front_cam(frame, perspective=0.2)
        fg = scene.render(truth, SHAPE, 1, None)["mask"]
        fg = fg & ~np.pad(np.ones((4, 4), bool), ((100, SHAPE[0] - 104), (200, SHAPE[1] - 204)))   # the silhouette changed a little
        bad = Camera(truth.yaw + 6.0, truth.pitch, truth.roll, 0.005, truth.scale * 0.95, truth.center_x + 3, truth.center_y)
        monkeypatch.setattr(bcam.ViewFit, "run_starts", lambda self, seeds, n: None)
        monkeypatch.setattr(bcam.ViewFit, "refine", lambda self: {"camera": bad, "loss_final_level": 0.5, "loss_fit_level": 0.5, "origin": "prior"})
        parent = {"camera": truth.to_dict(), "frame": frame.to_dict(), "roi": list(observe.fit_roi(fg)), "photo_only_px": -1,
                  "render_only_px": -1, "contour_mean_px": -1.0}
        out, _ = observe.fit_view("front", fg, scene, frame, np.array([30.0, 0.0, 0.0]), truth, warm_record=parent)
        assert out["camera"] == truth.to_dict() and out["camera_source"] == "warm_kept"
        wc = out["warm_comparison"]
        assert wc["warm"]["loss"] < wc["refit"]["loss"] and wc["warm"]["contour_mean_px"] < wc["refit"]["contour_mean_px"]
        assert out["loss"] == pytest.approx(wc["warm"]["loss"], abs=1e-5)

    @pytest.mark.parametrize("view,rule", [("front", "front_piece_centre"), ("back", "front_piece_centre"), ("left", "projection_centre")])
    def test_view_millimetres_are_taken_at_the_front_piece(self, monkeypatch, view, rule):
        objects = lens_objects(temple_len=120.0)
        V, F, part, frame, scene = lens_scene(objects)
        cam = front_cam(frame, perspective=0.3)
        fg = scene.render(cam, SHAPE, 1, None)["mask"]
        monkeypatch.setattr(bcam.ViewFit, "run_starts", lambda self, seeds, n: None)
        monkeypatch.setattr(bcam.ViewFit, "refine", lambda self: {"camera": cam, "loss_final_level": 0.1, "loss_fit_level": 0.1, "origin": "prior"})
        pivot = observe.front_piece_centre(V, F, part)
        out, _ = observe.fit_view(view, fg, scene, frame, pivot, None)
        expected = bcam.px_per_mm_at(cam, frame, pivot) if rule == "front_piece_centre" else px_per_mm(cam, frame)
        assert out["px_per_mm_rule"] == rule and out["px_per_mm"] == pytest.approx(expected)
        assert out["contour_mean_mm"] == pytest.approx(out["contour_mean_px"] / expected)
        assert out["px_per_mm_projection_centre"] == pytest.approx(px_per_mm(cam, frame))

    def test_a_parent_in_another_frame_only_seeds_the_angles(self, monkeypatch):
        V, F, part, frame, scene = lens_scene(lens_objects())
        truth = front_cam(frame)
        fg = scene.render(truth, SHAPE, 1, None)["mask"]
        seen = {}
        monkeypatch.setattr(bcam.ViewFit, "run_starts", lambda self, seeds, n: seen.setdefault("seeds", seeds))
        monkeypatch.setattr(bcam.ViewFit, "refine", lambda self: {"camera": truth, "loss_final_level": 0.1, "loss_fit_level": 0.1, "origin": "warm"})
        parent = {"camera": truth.to_dict(), "frame": {"center": [1.0, 2.0, 3.0], "extent": 999.0}}
        out, _ = observe.fit_view("front", fg, scene, frame, np.array([30.0, 0.0, 0.0]), truth, warm_record=parent)
        assert out["camera_source"] == "refit" and "warm_comparison" not in out
        assert sum(1 for s in seen["seeds"] if s[3] == "warm") == len(observe.WARM_START_OFFSETS)


# --------------------------------------------------------------------------- matte coverage
def crystal_photo(opaque_share: float = 1.0):
    """A white-backdrop photo of a ring: its matte (fg) covers the dark share; the rest is faint warm crystal."""
    H, W = SHAPE
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.hypot(yy - 120, xx - 160)
    ring = (r > 50) & (r < 80)
    rgb = np.full((H, W, 3), 255, np.uint8)
    ang = (np.degrees(np.arctan2(yy - 120, xx - 160)) + 360) % 360
    dark = ring & (ang < 360 * opaque_share)
    rgb[ring & ~dark] = (236, 226, 208)          # clear crystal: a faint warm tint the contrast matte does not take
    rgb[ring & ~dark & ((xx + yy) % 7 == 0)] = (214, 204, 186)
    rgb[dark] = (40, 30, 25)
    return rgb, dark


class TestMatteCoverage:
    def test_an_opaque_frame_is_covered(self):
        from modeler import matte_coverage as mc
        rgb, fg = crystal_photo(1.0)
        c = mc.coverage_of(rgb, fg)
        assert c["coverage"] > 0.97 and c["reliable"] is True

    def test_a_crystal_frame_the_matte_misses_is_not_reliable(self):
        from modeler import matte_coverage as mc
        rgb, fg = crystal_photo(0.55)
        c = mc.coverage_of(rgb, fg)
        assert c["coverage"] < mc.COVERAGE_MIN and c["reliable"] is False
        assert c["complete_px"] > c["matte_px"]

    def test_the_mirror_iou_of_the_complete_silhouette(self):
        from modeler import matte_coverage as mc
        rgb, fg = crystal_photo(0.55)
        c = mc.coverage_of(rgb, fg, mirror=True)
        assert c["mirror_iou_complete"] > 0.94 > c["mirror_iou_matte"]

    def test_unreliable_views_mark_their_metrics_and_the_lens_gate(self, tmp_path, monkeypatch):
        rgb, fg = crystal_photo(0.55)
        objects = lens_objects()
        frame = observe.norm_frame(observe.mesh_of(objects)[0])
        cam = front_cam(frame)
        obs = observe_front(tmp_path, monkeypatch, objects, cam, photo=rgb, fg=fg)
        front = obs["views"]["front"]
        assert front["reliable"] is False and "matte" in front["reason"]
        s = obs["summary"]
        assert s["matte_coverage"]["front"]["reliable"] is False
        assert {"front_contour_mean_mm", "lens_outline_mean_mm", "mean_contour_mm_all_fit_views"} <= set(s["unreliable_metrics"])
        assert front["lens_outline"]["reliable"] is False
        assert s["mirror_iou"]["matte_artefact"] is True

    @pytest.mark.parametrize("reliable,complete,artefact", [(False, 0.9387, True), (True, 0.9387, False), (True, 0.958, True), (False, 0.86, False)])
    def test_mirror_iou_low_is_a_matte_artefact_when_filling_the_holes_makes_the_silhouette_symmetric(self, reliable, complete, artefact):
        """tomford-astra1's crystal front: matte 0.862, complete 0.939 (the complete silhouette under-fills clear crystal): the
        gain, not the 0.94 line, is the evidence on an unreliable matte."""
        front = {"view": "front", "contour_mean_mm": 1.0, "contour_p95_mm": 2.0, "iou": 0.8,
                 "matte_coverage": {"coverage": 0.8 if not reliable else 0.99, "reliable": reliable, "mirror_iou_matte": 0.8617, "mirror_iou_complete": complete}}
        if not reliable:
            front.update(reliable=False, reason="matte holes")
        assert observe.summarize({"front": front}, {}, None)["mirror_iou"]["matte_artefact"] is artefact

    def test_the_intake_rim_guard_alone_makes_the_lens_outline_unreliable(self, tmp_path, monkeypatch):
        objects = lens_objects()
        frame = observe.norm_frame(observe.mesh_of(objects)[0])
        obs = observe_front(tmp_path, monkeypatch, objects, front_cam(frame), flags=("rim_invisible_to_matte",))
        assert obs["views"]["front"].get("reliable", True) is True
        assert obs["views"]["front"]["lens_outline"]["reliable"] is False
        assert "rim_invisible_to_matte" in obs["summary"]["unreliable_metrics"]["lens_outline_mean_mm"]

    def test_rear_angled_is_listed_as_unsupported(self, tmp_path, monkeypatch):
        objects = lens_objects()
        frame = observe.norm_frame(observe.mesh_of(objects)[0])
        obs = observe_front(tmp_path, monkeypatch, objects, front_cam(frame), views=("front", "rear_angled"))
        assert obs["summary"]["fit_views_unsupported"] == {"rear_angled": observe.UNSUPPORTED_VIEW_REASON}


# --------------------------------------------------------------------------- the status
def _eval(overall="accept"):
    return {"discrepancies": [], "identity_checklist": [], "resemblance_0_10": {}, "runtime_notes": "", "overall": overall, "summary": ""}


class TestDecideStatus:
    def test_an_unreliable_lens_outline_never_fails_the_gate(self):
        m = {"lens_outline_mean_mm": 1.969, "front_contour_mean_mm": 1.267, "ar_continuity_failure": None, "ar_continuity_measured": True,
             "unreliable_metrics": {"lens_outline_mean_mm": "front matte covers 79% of the silhouette"}}
        st = mevaluate.decide_status(candidate_valid=True, metrics=m, heldout=None, evaluation=_eval(), input_flags=[], protocol_calibrated=True)
        p = st["provisional"]["lens_outline_mean_mm"]
        assert p["gate"] is False and p["reliable"] is False and p["pass"] is False and "79%" in p["reason"]
        assert st["automatic_verdict"] == "accept"
        assert any("lens_outline_mean_mm" in r and "unreliable" in r for r in st["reasons"])

    def test_a_reliable_lens_outline_still_gates(self):
        m = {"lens_outline_mean_mm": 1.2, "ar_continuity_failure": None, "ar_continuity_measured": True}
        st = mevaluate.decide_status(candidate_valid=True, metrics=m, heldout=None, evaluation=_eval(), input_flags=[], protocol_calibrated=True)
        assert st["automatic_verdict"] == "reject" and st["provisional"]["lens_outline_mean_mm"]["gate"] is True

    def test_an_unreliable_missing_gate_metric_is_not_unmeasured(self):
        m = {"ar_continuity_failure": None, "unreliable_metrics": {"lens_outline_mean_mm": "no reliable front matte"}}
        st = mevaluate.decide_status(candidate_valid=True, metrics=m, heldout=None, evaluation=_eval(), input_flags=[], protocol_calibrated=True)
        assert st["automatic_verdict"] == "accept"

    def test_mirror_iou_low_from_a_matte_artefact_is_informational(self):
        m = {"lens_outline_mean_mm": 0.3, "ar_continuity_failure": None, "mirror_iou": {"matte": 0.844, "complete": 0.958, "matte_artefact": True}}
        st = mevaluate.decide_status(candidate_valid=True, metrics=m, heldout=None, evaluation=_eval("reject"), input_flags=["mirror_iou_low"],
                                     protocol_calibrated=True)
        assert not any(r.startswith("input flags") for r in st["reasons"])
        assert any("mirror_iou_low" in r and "matte artefact" in r for r in st["reasons"])
        st2 = mevaluate.decide_status(candidate_valid=True, metrics={"lens_outline_mean_mm": 0.3}, heldout=None, evaluation=_eval("reject"),
                                      input_flags=["mirror_iou_low"], protocol_calibrated=True)
        assert st2["reasons"][0] == "input flags: ['mirror_iou_low']"

    @pytest.mark.parametrize("summary,measured", [({"ar_continuity_failure": None, "ar_continuity_measured": True}, True),
                                                  ({"ar_continuity_failure": None}, False),
                                                  ({"ar_continuity_failure": {"side": "L"}}, True)])
    def test_continuity_is_measured_when_the_harness_reported_it(self, summary, measured):
        st = mevaluate.decide_status(candidate_valid=True, metrics={"lens_outline_mean_mm": 0.3, **summary}, heldout=None, evaluation=_eval(),
                                     input_flags=[], protocol_calibrated=True)
        assert st["provisional"]["ar_temple_continuity"]["measured"] is measured


class TestArcheckContinuityKey:
    def test_the_report_says_whether_continuity_was_measured(self, tmp_path):
        rows = [{"id": "a", "status": "runtime_compatible", "continuity_failure": None, "renders": []},
                {"id": "b", "status": "runtime_compatible", "renders": []}]
        (tmp_path / "report.json").write_text(json.dumps({"status": "inspected", "cases": rows}), encoding="utf-8")
        res = archeck.parse_report(tmp_path, {"a": "A", "b": "B"})
        assert res["models"]["A"]["continuity_measured"] is True and res["models"]["B"]["continuity_measured"] is False


# --------------------------------------------------------------------------- the AR pose sweep and side view
class TestPoseSweep:
    def test_the_sweep_and_the_side_view(self):
        from modeler import pose_sweep as ps
        yaws = sorted({v["yaw_degrees"] for v in ps.POSE_SWEEP_VIEWS})
        pitches = sorted({v["pitch_degrees"] for v in ps.POSE_SWEEP_VIEWS})
        assert yaws == [-9, -6, -3, 0, 3, 6, 9] and pitches == [-4, 0]
        import re
        assert all(re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", v["id"]) for v in list(ps.POSE_SWEEP_VIEWS) + list(observe.AR_VIEWS))
        side = [v for v in observe.AR_VIEWS if v["id"] == "side"]
        assert side and 60 <= side[0]["yaw_degrees"] <= 80

    def test_a_flat_lens_slab_flips_its_reflection_between_neighbouring_poses(self):
        """Measured on the harness (720x480, checker): delivered r0006 (flat lens) jumps 0.109 of the front piece between
        yaw 3-degree neighbours; a base-6 lens 0.020; the owner-accepted assets at most 0.045 (miu)."""
        from modeler import pose_sweep as ps
        delivered = {"pm4": [(7063, 22790), (6187, 23055), (3691, 23191), (2652, 23273), (4845, 23204), (7074, 23048), (6090, 22791)]}
        base6 = {"pm4": [(1937, 22790), (2034, 23055), (2419, 23191), (2443, 23273), (2103, 23204), (1918, 23048), (1845, 22791)]}
        d = ps.reflection_stats(delivered)
        b = ps.reflection_stats(base6)
        assert d["flag"] == "lens_reflection_slab" and b["flag"] is None
        assert d["max_jump_px"] == 2496 and d["max_jump_share"] == pytest.approx(0.1085, abs=1e-3)
        assert d["max_saturated_share"] == pytest.approx(7063 / 22790, abs=1e-3)

    def test_the_observation_carries_the_sweep_sheet_and_the_metric(self, tmp_path, monkeypatch):
        from modeler import pose_sweep as ps
        calls = []

        def run(models, out, **kw):
            calls.append([v["id"] for v in kw.get("ar_views", ())])
            out = Path(out)
            out.mkdir(parents=True, exist_ok=True)
            files = []
            for v in kw["ar_views"]:
                img = archeck.checker_background(720, 480)
                img[200:280, 250:470] = (150, 130, 110)
                p = out / f"candidate__actual-ar__{v['id']}.png"
                Image.fromarray(img).save(p)
                files.append({"view": v["id"], "path": str(p), "sha256": "x"})
            return {"models": {"candidate": {"runtime_compatible": True, "renders": [f["path"] for f in files], "render_files": files}}}
        monkeypatch.setattr(observe.archeck, "run", run)
        monkeypatch.setattr(ps, "front_piece_saturation", lambda glb, out_dir, views: {v: (100, 20000) for v in views})
        objects = lens_objects()
        frame = observe.norm_frame(observe.mesh_of(objects)[0])
        build = write_candidate(tmp_path, objects)
        evidence, ev_dir = write_front_evidence(tmp_path, front_cam(frame), frame)
        glb = tmp_path / "model.glb"
        glb.write_bytes(b"glTF")
        from modeler import see_through, lens_colour
        monkeypatch.setattr(see_through, "render_fixtures", lambda *a, **k: {"status": "not_applicable"})
        monkeypatch.setattr(lens_colour, "lens_colour_metric", lambda *a, **k: {"status": "not_applicable"})
        monkeypatch.setattr(observe, "fit_view", lambda view, fg, scene, frame_, pivot, warm=None, **kw: (
            {"camera": front_cam(frame_).to_dict(), "loss": 0.1, "px_per_mm": 4.0, "roi": [0, 0, 320, 240], "evaluations": 1, "seconds": 0.0,
             "iou": 0.9, "contour_mean_px": 1.0, "contour_p95_px": 2.0, "contour_mean_mm": 0.25, "contour_p95_mm": 0.5, "contour_mean_pct_width": 1.0,
             "photo_only_px": 3, "render_only_px": 4, "extent": {}}, fg.copy()))
        obs = observe.observe_candidate(tmp_path / "cand", build, evidence, ev_dir, held_out_ids=set(), ar=True, glb_path=glb,
                                        render_harness=lambda *a, **k: {"ok": True, "renders": []})
        assert calls and set(calls[0]) == {v["id"] for v in observe.AR_VIEWS} | {v["id"] for v in ps.POSE_SWEEP_VIEWS}
        assert "pose_sweep" in obs["sheets"] and "ar" in obs["sheets"]
        lr = obs["summary"]["lens_reflection"]
        assert lr["flag"] is None and lr["max_jump_px"] == 0 and lr["max_saturated_share"] == pytest.approx(0.005)
        with Image.open(obs["sheets"]["ar"]) as ar_sheet:
            ar_width = ar_sheet.size[0]
        assert ar_width < 2200, "the AR sheet is a grid of its five views, not a 5-wide strip of sweep renders"


# --------------------------------------------------------------------------- the lens over the photo's backdrop
class TestLensBackdropSheet:
    def test_the_sheet_sits_beside_the_photos_lens(self, tmp_path):
        photo = np.full((200, 300, 3), 255, np.uint8)
        photo[60:140, 40:140] = (213, 196, 175)
        pp = tmp_path / "front.png"
        Image.fromarray(photo).save(pp)
        over = np.full((480, 720, 3), 255, np.uint8)
        over[200:280, 250:470] = (233, 229, 219)
        op = tmp_path / "over.png"
        Image.fromarray(over).save(op)
        evidence = {"views": {"front": {"view": "front", "author_photo": {"path": str(pp), "crop_xyxy": [0, 0, 300, 200], "scale": 1.0, "size": [300, 200]}}},
                    "front": {"lenses": [{"outline_px": [[40, 60], [140, 60], [140, 140], [40, 140]]}]}}
        lc = {"render_over_backdrop": str(op), "backdrop_rgb": [255, 255, 255], "photo_rgb": [213, 196, 175], "predicted_rgb": [233, 229, 219]}
        out = observe.lens_backdrop_sheet(evidence, lc, tmp_path / "sheet.png")
        a = np.asarray(Image.open(out).convert("RGB")).astype(int)
        assert (np.abs(a - (213, 196, 175)).sum(-1) < 6).any() and (np.abs(a - (233, 229, 219)).sum(-1) < 6).any()

    def test_the_evaluator_package_shows_it(self, tmp_path, monkeypatch):
        asset = tmp_path / "asset"
        (asset / "observe").mkdir(parents=True)
        sheet = asset / "observe" / "sheet_lens_backdrop.png"
        Image.new("RGB", (40, 20), (200, 180, 160)).save(sheet)
        (asset / "observe" / "observation.json").write_text(json.dumps({"sheets": {"lens_backdrop": str(sheet)}, "summary": {}, "bbox_mm": None}), encoding="utf-8")
        monkeypatch.setattr(mevaluate, "wearer_renders", lambda *a, **k: {})
        monkeypatch.setattr(mevaluate, "_package_image_name", lambda im: im["id"] + ".png")
        request, images = mevaluate.write_evaluator_package(tmp_path, asset, {"inputs": []}, [], tmp_path / "pkg", glb_path=tmp_path / "none.glb")
        ids = [im["id"] for im in images]
        assert "lens_backdrop" in ids
        assert "lens_backdrop" in mevaluate.EVALUATOR_TASK
