"""WP5 photo print, Tom Ford FT1119: the owner's "every angle x every option" comparison, rendered by arview.

    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.photoprint.all_angles [--out DIR] [--workers N]
        [--stage all|prepare|capture|measure|sheets|summary] [--camera-source bake|registration]

The four options (verified by sha256, and B / C by their capture reports and the level each print was made with):
Astra's q001 as delivered ("Astra's texture (now)"), the tool-test print at the delivered 'photo_angles' level
("A (new paint)": the production print), and the calibration's owner candidates 'near_18deg' (the brief's "B (darker)")
and 'all_pairs_chroma' (the brief's "C (more colourful)"). The column labels and the legend are built from the measured
differences against A (``option_differences`` / ``option_labels``, fix round 2): an option not visibly different from A
on any row is labelled '(almost the same as A)', so the owner is never shown two identical columns under two names.
Owner-facing text is words only (no degrees, no millimetres); row titles carry height words that must match the
measured view (``check_title_height``).

Owner sheets (plain words only):
1. ``all_angles_photos.png``: one row per photo whose silhouette lines up (IoU >= IOU_MIN), in the order 05 front,
   01 angled, 03 wearer's right side, 06 wearer's left side from above, 04 top, 02 back: the photo | the four options
   rendered by arview's own renderer from THAT photo's camera (``arview capture --cameras <json> --no-face``: the
   try-on's materials, environment, key light and tone mapping, no face, the authored size), on a flat background taken
   from the photo, every tile cut with the same box. A photo row under IOU_MIN does not go on that sheet (fix round 1:
   the brief's rule); it goes on ``all_angles_photos_shape_differs.png`` with a plain note under it, built from the
   measurement (which parts the mismatch is on, near / far arm, the model's tip plates), so the main session can show
   or drop it.
   Cameras (``--camera-source``): 'bake' (default, fix round 1) = the WP3b cameras of ``data/wp3b/ft1119/cameras.json``
   that the bake painted with (sha256 checked against the bake's own input record) and the temples at rest, as the bake
   painted them; 'registration' = step 0's registered pose (``ft1119_strip_null/registration.json`` fits.all.pose) and
   its fitted temple hinge angles (the first build's choice; photo 01's camera differs by 0.35 deg roll and ~3 px), drawn
   on a posed COPY of each GLB (``pose_glb``: the temples' vertices rotated about their hinge pivots by
   ``pose.hinge_matrix``, a pure glTF edit that appends rotated POSITION / NORMAL accessors).
   The camera of a photo (``g3d_host.camera.Camera``: NormFrame-normalised points to native pixels) is the pinhole
   camera at toward / perspective, focal length scale / perspective px, principal point (center_x, center_y) and the
   roll folded into the camera's right / up axes (``photo_camera``, checked against ``Camera.project`` on every vertex).
   Before the sheet: each render's part-id silhouette (holes filled) against the photo's matte (``matte.matte``, the
   one the cameras were fitted on) in the tile: IoU, centroid offset and boundary distance per row and option, a left /
   right check (the +x and -x parts' rendered centroids against the camera's own projection of those parts), a camera
   check (arview's silhouette against a raster of the cameras' fit mesh at the same camera and temples) and where the
   mismatch lies (``mismatch_parts``: model-only and photo-only pixels by front / near arm / far arm).
2. ``all_angles_tryon.png`` (+ ``all_angles_tryon_2.png``): one row per try-on pose of ``--poses all`` plus the worn
   portrait, the four options captured fresh on the skin background (``arview capture --poses all --backgrounds skin``),
   one crop per row (the union of the four part-id silhouettes), and a photo first where one was taken within 20 deg of
   that pose, else an empty tile saying how far the nearest photo is. The angle is between the VIEWING directions: from
   the glasses' front (the centre of the front group's box) to each camera's centre, in the glasses' frame (fix round
   1: the try-on camera sees the glasses 11-17 deg off its axis, from below, so the head rotation alone put photos 05 /
   01 at 18 deg from 'straight on' / 'turned left' when they are 30 deg away). Notes under rows and a footer name the
   try-on's own effects (lens glare, arms cut where the head would hide them, the close camera, the model's materials).

Every capture runs in a process pool of ``os.cpu_count() - 2`` workers, each opted out of EcoQoS, the node process too.
Writes only under ``--out`` (default ``data/wp5/all_angles``); the bake, the tool test and the calibration are inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from glasses3d.g3d_host import fitmesh
from glasses3d.g3d_host import raster
from glasses3d.g3d_host.camera import Camera, NormFrame, project_mm
from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.g3d_host.photoprint import register as REG
from glasses3d.g3d_host.photoprint.pose import hinge_matrix
from glasses3d.g3d_host.photoprint.procs import default_workers, opt_out_ecoqos, pool_map
from glasses3d.tests.photoprint import ar_calibrate as AC

REPO = Path(__file__).resolve().parents[3]
G3D = REPO / "glasses3d"
ARVIEW = G3D / "arview"
WP5 = G3D / "data" / "wp5"
OUT = WP5 / "all_angles"
REGISTRATION = WP5 / "ft1119_strip_null" / "registration.json"
CALIBRATION = WP5 / "ar_calibration" / "calibration.json"
BAKE = WP5 / "ft1119_bake"
RADIANCE_BAKE = WP5 / "ft1119_bake_radiance_s6b"
ASTRA_JOB = REPO / "automation/data/blender_agent/studio/jobs/3b8a1c7ae8114868b49b298b681f1b29"

# (key, the brief's name). The sheets show the labels ``option_labels`` builds from the measured differences (fix round
# 2: C measured almost the same as A everywhere, so 'more colourful' would have named a difference nobody can see).
OPTIONS = (("astra", "Astra's texture (now)"), ("A", "A (new paint)"), ("B", "B (darker)"), ("C", "C (more colourful)"))
# an option counts as visibly different from A when, on some row, at least VISIBLE_SHARE of the frame's pixels differ by
# more than VISIBLE_LEVELS / 255 in a channel, or the mean CIE76 colour difference reaches JND_DE (a just-noticeable step)
VISIBLE_LEVELS = 6
VISIBLE_SHARE = 0.05
JND_DE = 2.3
CAMERA_SOURCES = ("bake", "registration")
WP3B_CAMERAS = G3D / "data/wp3b/ft1119/cameras.json"
EXPECTED_SHA_PREFIX = {"astra": "af6e9d10", "A": "17d60488"}
CANDIDATE_OF = {"B": "near_18deg", "C": "all_pairs_chroma"}
EXPECTED_LEVEL = {"B": {"gain": 0.831, "gamma": 1.168, "chroma_power": 0.455},
                  "C": {"gain": 0.931, "gamma": 1.080, "sat": 1.378, "chroma_power": 0.615},
                  "A": {"gain": 0.931, "gamma": 1.080, "chroma_power": 0.455}}

# photo rows in the owner's order: (short id, file stem, plain row title). The height words in a title must match the
# measured viewing direction (``height_word``; checked in ``measure`` and ``sheets_tryon``): fix round 2 named photo 05
# 'from the front, a little above' (it sees the glasses from 19 deg above, which is why it pairs with the try-on's 'from
# a little above' and not with 'straight on'), and 01 / 06 / the try-on's pitch +20 rows likewise.
PHOTO_ROWS = (("05", "front", "from the front, a little above"),
              ("01", "angled", "from an angle, wearer's left side, a little above"),
              ("03", "right_side", "wearer's right side"),
              ("06", "left_side_from_above", "wearer's left side, a little above"),
              ("04", "top", "from the top"),
              ("02", "back", "from the back"))
TRYON_ROWS = (("front", "straight on"),
              ("yaw_p30", "turned, wearer's right arm near"),
              ("yaw_m30", "turned, wearer's left arm near"),
              ("yaw_p45", "turned further, wearer's right arm near"),
              ("yaw_m45", "turned further, wearer's left arm near"),
              ("pitch_p20", "from a little above"),
              ("pitch_m20", "from below"),
              ("yaw_p30_pitch_p20", "turned, wearer's right arm near, from a little above"),
              ("yaw_p30_pitch_m20", "turned, wearer's right arm near, from below"),
              ("yaw_m30_pitch_p20", "turned, wearer's left arm near, from a little above"),
              ("yaw_m30_pitch_m20", "turned, wearer's left arm near, from below"),
              ("portrait", "on a face"))
HEIGHT_WORDS = ("a little above", "from the top", "below")
TRYON_PHOTO_MAX_DEG = 20.0
FAR_AWAY_RATIO = 3.0               # a photo taken from this many times the try-on camera's distance is 'much farther away'
# what the try-on itself draws, the same in all four options (the runtime and the model's materials, not the paint)
TRYON_FOOTER = (
    "Same in all four, and not the paint: the white patches on the lenses and on the inner curve of the brow are the try-on's "
    "light reflecting; the arms stop where the head would hide them, so in some turned rows the far arm shows only as a small "
    "dark block near the far lens; the faint curved shade on the skin background is the shadow of the head; the metal looks "
    "silver and the lenses cream because those are the model's own materials; the try-on camera is close, so the near side "
    "of the glasses looks bigger than in your photos, which were taken from far away.")
PHOTOS_FOOTER = ("Same in all four, and not the paint: the metal looks silver and the lenses cream because those are the "
                 "model's own materials (gold and beige in your photos).")
# added only on a sheet that carries photo 05 (the row where the previous round measured it), naming that row
PHOTOS_FOOTER_EDGE_ON = " Where an arm is seen edge-on, as in the {ordinal} row, it mirrors the light and looks grey."
EDGE_ON_ROW = "05"
ORDINALS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth"}
IOU_MIN = 0.8
TILE_MAX_PX = 1280                 # arview never renders wider (MAX_RENDER_WIDTH); tiles are rendered at the crop
CROP_MARGIN = 0.06                 # of the larger box side, plus CROP_PAD px
CROP_PAD = 10
SILHOUETTE_LEVEL = 40              # part-id: the background is black, every part colour is >= 102 levels from it
FONT = "C:/Windows/Fonts/arial.ttf"
FONT_B = "C:/Windows/Fonts/arialbd.ttf"


def sha256_file(path: Path) -> str:
    return IN.sha256_file(path)


def _rel(p: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(REPO)).replace("\\", "/")
    except ValueError:
        return str(p)


def log(msg: str) -> None:
    print(f"[all_angles {time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ----------------------------------------------------------------------------- inputs
def glb_paths() -> dict[str, Path]:
    cal = json.loads(CALIBRATION.read_text(encoding="utf-8"))
    out = {"astra": ASTRA_JOB / "original" / "model.glb", "A": WP5 / "tooltest" / "q001_painted_TOOLTEST.glb"}
    for k, cand in CANDIDATE_OF.items():
        out[k] = REPO / cal["candidates"][cand]["glb"].replace("\\", "/")
    return out


def _glb_image(doc: dict, binc: bytes, k: int) -> bytes:
    v = doc["bufferViews"][doc["images"][k]["bufferView"]]
    o = v.get("byteOffset", 0)
    return binc[o:o + v["byteLength"]]


def _structure(doc: dict, binc: bytes) -> tuple[str, str]:
    """sha256 of the JSON without the image bufferViews' placement, and of the non-image binary payloads in order: two
    GLBs whose prints differ but whose geometry, materials and everything else agree have equal pairs."""
    img_views = {im["bufferView"] for im in doc.get("images", []) if "bufferView" in im}
    d = json.loads(json.dumps(doc))
    for i, v in enumerate(d["bufferViews"]):
        v.pop("byteOffset", None)
        if i in img_views:
            v.pop("byteLength", None)
    d["buffers"][0].pop("byteLength", None)
    d.pop("asset", None)                       # generator / tool-test note only
    pay = []
    for i, v in enumerate(doc["bufferViews"]):
        if i not in img_views:
            o = v.get("byteOffset", 0)
            pay.append(hashlib.sha256(binc[o:o + v["byteLength"]]).hexdigest())
    return (hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest(),
            hashlib.sha256("".join(pay).encode()).hexdigest())


def verify_inputs() -> dict:
    """sha256 of every option; B and C identified from their capture reports (input path + sha256), the calibration's
    record of the print each was made with, and that print recomputed from the radiance bake with the candidate's level
    (``ar_calibrate.levelled_png``, the code path that made it) byte for byte. After the owner choice, A is verified
    against its preserved pre_owner_B bake; these frozen comparison options do not change with production."""
    cal = json.loads(CALIBRATION.read_text(encoding="utf-8"))
    paths = glb_paths()
    rec: dict = {"calibration": {"path": _rel(CALIBRATION), "sha256": sha256_file(CALIBRATION)},
                 "radiance_bake": _rel(RADIANCE_BAKE), "production_bake": _rel(BAKE), "options": {}}
    docA, binA = AC._chunks(paths["A"].read_bytes())
    pngA = _glb_image(docA, binA, AC.print_image_index(docA))
    prod = np.asarray(Image.open(BAKE / "print.png").convert("RGB"))
    a_bake = BAKE / "pre_owner_B" if "production_selection" in cal else BAKE
    a_reference = np.asarray(Image.open(a_bake / "print.png").convert("RGB"))
    rec["option_A_reference_bake"] = _rel(a_bake)
    imgA = np.asarray(Image.open(io.BytesIO(pngA)).convert("RGB"))
    structA = _structure(docA, binA)
    for key, label in OPTIONS:
        p = paths[key]
        sha = sha256_file(p)
        r = {"brief_name": label, "path": _rel(p), "sha256": sha}
        if key in EXPECTED_SHA_PREFIX and not sha.startswith(EXPECTED_SHA_PREFIX[key]):
            raise SystemExit(f"{key}: {p} has sha256 {sha[:12]}, expected {EXPECTED_SHA_PREFIX[key]}")
        if key == "astra":
            r["what"] = "Astra's delivered q001 (studio job 3b8a1c7a original/model.glb), read-only"
            r["same_bytes_as"] = _rel(G3D / "data/reference/projection/q001.glb") if sha256_file(
                G3D / "data/reference/projection/q001.glb") == sha else None
        else:
            doc, binc = AC._chunks(p.read_bytes())
            k = AC.print_image_index(doc)
            png = _glb_image(doc, binc, k)
            r["print_png_sha256"] = hashlib.sha256(png).hexdigest()
            r["geometry_and_materials_equal_A"] = _structure(doc, binc) == structA
            # the GLB's own label (asset.extras.g3d_tool_test); it must name the level of the print it carries
            note = str(((doc.get("asset") or {}).get("extras") or {}).get("g3d_tool_test") or "")
            r["asset_note"] = note
            if key == "A":
                lv = cal["candidates"]["photo_angles"]["level"]
                rec_png = np.asarray(Image.open(io.BytesIO(AC.levelled_png(RADIANCE_BAKE, lv))).convert("RGB"))
                r.update({"what": "tool-test copy of q001 carrying option A's frozen photo_angles print",
                          "level": lv, "level_name": "photo_angles",
                          "print_equals_production_print_png": bool(np.array_equal(imgA, prod)),
                          "print_equals_option_A_reference_png": bool(np.array_equal(imgA, a_reference)),
                          "print_equals_radiance_bake_levelled_with_photo_angles": bool(np.array_equal(imgA, rec_png))})
                if not (r["print_equals_option_A_reference_png"] and r["print_equals_radiance_bake_levelled_with_photo_angles"]):
                    raise SystemExit("A's print is not its preserved reference at the photo_angles level")
            else:
                cand = cal["candidates"][CANDIDATE_OF[key]]
                it = next(e for e in cal["iterations"] if e.get("tag") == cand["tag"])
                reports = {}
                for kind in ("capture", "sheet_capture"):
                    rp = json.loads((REPO / cand[kind].replace("\\", "/") / "report.json").read_text(encoding="utf-8"))
                    reports[kind] = {"report": _rel(REPO / cand[kind].replace("\\", "/") / "report.json"),
                                     "input_path": rp["input"]["path"], "input_sha256": rp["input"]["sha256"],
                                     "matches": Path(rp["input"]["path"]).resolve() == p.resolve() and rp["input"]["sha256"] == sha}
                recomputed = hashlib.sha256(AC.levelled_png(RADIANCE_BAKE, cand["level"])).hexdigest()
                r.update({"what": f"calibration candidate '{CANDIDATE_OF[key]}' (tag {cand['tag']}): the production radiance "
                                  "print levelled with the candidate's level, in a copy of the tool-test GLB",
                          "level_name": CANDIDATE_OF[key], "level": cand["level"], "tag": cand["tag"], "capture_reports": reports,
                          "calibration_record_png_sha256": it.get("png_sha256"),
                          "calibration_record_level_equal": it["level"] == cand["level"],
                          "recomputed_png_sha256": recomputed,
                          "identified": bool(all(x["matches"] for x in reports.values())
                                             and it.get("png_sha256") == r["print_png_sha256"] == recomputed
                                             and it["level"] == cand["level"] and r["geometry_and_materials_equal_A"])})
                lvl = cand["level"]
                r["asset_note_names_its_level"] = (f"gain {lvl['gain']:.3f}" in note and f"gamma {lvl['gamma']:.3f}" in note)
                if not r["asset_note_names_its_level"]:
                    r["asset_note_stale"] = (
                        "the GLB's label (asset.extras.g3d_tool_test, written before this run by the calibration from an "
                        "older tool-test copy) does not describe its print: the print is the s6b radiance bake at level "
                        f"'{CANDIDATE_OF[key]}' (proved above: recomputed byte for byte). The file is a read-only input here; "
                        "ar_calibrate.evaluate copies the source GLB's label through swap_image and never relabels.")
                exp = EXPECTED_LEVEL[key]
                for name, value in exp.items():
                    if abs(cand["level"][name] - value) > 6e-4:
                        raise SystemExit(f"{key}: level {name} {cand['level'][name]} is not the expected {value}")
                if not r["identified"]:
                    raise SystemExit(f"{key}: the candidate GLB {p} could not be identified with certainty: {json.dumps(r)[:800]}")
        rec["options"][key] = r
    return rec


# ----------------------------------------------------------------------------- cameras
def photo_camera(cam: Camera, frame: NormFrame) -> tuple[np.ndarray, float]:
    """The pinhole equivalent of ``cam``: (view, focal px). view: row-major 4x4, model metres to camera metres (camera
    looking down -Z, +Y up); u = center_x + f X / -Z, v = center_y - f Y / -Z (native photo px, pixel centres)."""
    if not cam.perspective > 0:
        raise ValueError("an orthographic photo camera has no pinhole equivalent")
    right, up, toward = raster.camera_basis(cam)
    cr, sr = math.cos(math.radians(cam.roll)), math.sin(math.radians(cam.roll))
    Rc = np.stack([cr * right - sr * up, sr * right + cr * up, toward])
    c_m = np.asarray(frame.center, float) / 1000.0
    t = -Rc @ c_m - np.array([0.0, 0.0, frame.extent / (1000.0 * cam.perspective)])
    view = np.eye(4)
    view[:3, :3] = Rc
    view[:3, 3] = t
    return view, cam.scale / cam.perspective


def project_view(view: np.ndarray, f: float, cx: float, cy: float, P_m: np.ndarray) -> np.ndarray:
    Q = P_m @ view[:3, :3].T + view[:3, 3]
    return np.column_stack([cx + f * Q[:, 0] / -Q[:, 2], cy - f * Q[:, 1] / -Q[:, 2]])


def crop_box(matte: np.ndarray, uv: np.ndarray) -> tuple[int, int, int, int]:
    """(x0, y0, w, h) native px: the matte's box united with the model's projected box, plus a margin."""
    ys, xs = np.nonzero(matte)
    x0, x1 = min(xs.min(), uv[:, 0].min()), max(xs.max() + 1, uv[:, 0].max() + 1)
    y0, y1 = min(ys.min(), uv[:, 1].min()), max(ys.max() + 1, uv[:, 1].max() + 1)
    m = CROP_MARGIN * max(x1 - x0, y1 - y0) + CROP_PAD
    x0, y0, x1, y1 = math.floor(x0 - m), math.floor(y0 - m), math.ceil(x1 + m), math.ceil(y1 + m)
    return int(x0), int(y0), int(x1 - x0), int(y1 - y0)


def tile_size(w: int, h: int) -> tuple[int, int]:
    k = min(TILE_MAX_PX / w, TILE_MAX_PX / h, 3.0)
    return max(64, int(round(w * k))), max(64, int(round(h * k)))


def background_hex(img: np.ndarray, matte: np.ndarray, crop) -> tuple[str, list[int]]:
    x0, y0, w, h = crop
    H, W = matte.shape
    far = ~ndimage.binary_dilation(matte, iterations=25)
    sub = np.zeros_like(matte)
    sub[max(0, y0):min(H, y0 + h), max(0, x0):min(W, x0 + w)] = True
    px = img[far & sub]
    rgb = [int(round(v)) for v in np.median(px, axis=0)]
    return "#%02x%02x%02x" % tuple(rgb), rgb


def crop_resize(arr: np.ndarray, crop, size, fill, resample) -> np.ndarray:
    """The crop box (may leave the image: padded with ``fill``) resampled to ``size`` (PIL box resize, edge convention)."""
    x0, y0, w, h = crop
    H, W = arr.shape[:2]
    pl, pt, pr, pb = max(0, -x0), max(0, -y0), max(0, x0 + w - W), max(0, y0 + h - H)
    if arr.ndim == 3:
        canvas = np.empty((H + pt + pb, W + pl + pr, arr.shape[2]), arr.dtype)
        canvas[:] = np.asarray(fill, arr.dtype)
    else:
        canvas = np.full((H + pt + pb, W + pl + pr), fill, arr.dtype)
    canvas[pt:pt + H, pl:pl + W] = arr
    im = Image.fromarray(canvas)
    return np.asarray(im.resize(size, resample, box=(x0 + pl, y0 + pt, x0 + pl + w, y0 + pt + h)))


def load_pose(job) -> tuple:
    reg = json.loads(REGISTRATION.read_text(encoding="utf-8"))
    d = reg["fits"]["all"]["pose"]
    return REG.pose_from_json(job, d), reg["groups"], d, reg


def photo_cameras(job, source: str) -> tuple[dict, dict, dict]:
    """(vname -> Camera, (vname, group) -> hinge deg, record of the source).

    'bake': the WP3b cameras the production bake painted with (its ``bake.json`` inputs.sources names
    ``data/wp3b/ft1119/cameras.json`` and its sha256, checked here) and the temples at rest, as the bake painted them
    (the bake's registration used no registered pose on FT1119: ``registration.json`` registered_used is empty, checked).
    'registration': step 0's registered pose (fits.all.pose) and its fitted temple hinge angles."""
    if source not in CAMERA_SOURCES:
        raise ValueError(f"camera source {source!r}: one of {CAMERA_SOURCES}")
    if source == "registration":
        pose, _, pose_json, _ = load_pose(job)
        return (dict(pose.cameras), {k: float(a) for k, a in pose.hinges.items() if a != 0.0},
                {"source": "registration", "file": _rel(REGISTRATION), "sha256": sha256_file(REGISTRATION), "fit": "fits.all.pose",
                 "hinges": pose_json["hinges"]})
    bake = json.loads((BAKE / "bake.json").read_text(encoding="utf-8"))
    src = bake["inputs"]["sources"]
    sha = sha256_file(WP3B_CAMERAS)
    if Path(src["cameras_json"]).resolve() != WP3B_CAMERAS.resolve() or src["cameras_sha256"] != sha:
        raise SystemExit(f"the bake's cameras ({src['cameras_json']}, {src['cameras_sha256'][:12]}) are not {WP3B_CAMERAS} ({sha[:12]})")
    breg = json.loads((BAKE / "registration.json").read_text(encoding="utf-8"))
    if breg.get("registered_used"):
        raise SystemExit("the bake used a registered pose for some region: its cameras are not the WP3b cameras alone")
    wp3b = json.loads(WP3B_CAMERAS.read_text(encoding="utf-8"))
    cams = {v: Camera.from_dict(wp3b["images"][v]["camera"]) for v in job.views if v in wp3b["images"]}
    return cams, {}, {"source": "bake", "file": _rel(WP3B_CAMERAS), "sha256": sha,
                      "evidence": "ft1119_bake/bake.json inputs.sources.cameras_json + cameras_sha256 name this file and sha256; "
                                  "ft1119_bake/registration.json registered_used is empty (no registered pose painted any region)",
                      "temples": "at rest (the bake painted the temples at rest)"}


def front_centre_m(job, groups: list[dict]) -> np.ndarray:
    """The glasses' front in model metres: the centre of the box of the front group's fit-mesh vertices (the point the
    viewing directions are measured from)."""
    parts = set(next(g for g in groups if g["name"] == "front")["parts"])
    pids = [i for i, n in enumerate(job.mesh.names) if n in parts]
    V = job.mesh.V[np.unique(job.mesh.F[np.isin(job.mesh.part, pids)])]
    return (V.min(0) + V.max(0)) / 2000.0


def view_toward_tryon(asset_to_world, front_m) -> tuple[np.ndarray, float]:
    """(unit direction from the glasses' front to the try-on camera, in the glasses' frame; distance in metres). The
    try-on camera sits at the scene origin; ``asset_to_world`` (column-major) maps asset metres to scene centimetres."""
    M = np.asarray(asset_to_world, float).reshape(4, 4).T
    R = M[:3, :3] / np.cbrt(np.linalg.det(M[:3, :3]))
    f_cam = M[:3, :3] @ np.asarray(front_m, float) + M[:3, 3]
    d = R.T @ (-f_cam)
    return d / np.linalg.norm(d), float(np.linalg.norm(f_cam)) / 100.0


def view_toward_photo(view_row_major, front_m) -> tuple[np.ndarray, float]:
    """(unit direction from the glasses' front to a photo camera's centre, model frame; distance in metres)."""
    V = np.asarray(view_row_major, float).reshape(4, 4)
    C = -V[:3, :3].T @ V[:3, 3]
    d = C - np.asarray(front_m, float)
    return d / np.linalg.norm(d), float(np.linalg.norm(d))


def angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.degrees(np.arccos(np.clip(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)), -1.0, 1.0))))


def node_groups(glb: Path, job, groups: list[dict]) -> dict[str, str]:
    """GLB mesh node (name before '.') -> hinge group name. The fit mesh's parts by the registration's group lists; the
    decals the cameras were fitted without (``model.skip``) by the group of the nearest fit-mesh part."""
    doc, binc = AC._chunks(glb.read_bytes())
    part_group = {p: g["name"] for g in groups for p in g["parts"]}
    names = job.mesh.names
    cent = []
    for i, n in enumerate(names):
        vi = np.unique(job.mesh.F[job.mesh.part == i])
        cent.append((n, job.mesh.V[vi]))
    out = {}
    for node in doc["nodes"]:
        if "mesh" not in node:
            continue
        base = str(node.get("name", "")).split(".")[0]
        if base in part_group:
            out[base] = part_group[base]
            continue
        P = np.concatenate([_accessor(doc, binc, pr["attributes"]["POSITION"])
                            for pr in doc["meshes"][node["mesh"]]["primitives"]]) * 1000.0
        best = min(cent, key=lambda nv: float(np.min(np.linalg.norm(nv[1][::7, None] - P[None, ::7], axis=2))))
        if best[0] not in part_group:
            # fix round 2: never a silent 'front' (the near / far arm words of the owner note depend on this mapping)
            raise ValueError(f"node {node.get('name')!r}: its nearest fit-mesh part {best[0]!r} is in no hinge group")
        out[base] = part_group[best[0]]
    return out


_COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def _accessor(doc: dict, binc: bytes, ai: int) -> np.ndarray:
    a = doc["accessors"][ai]
    if "sparse" in a or a["componentType"] != 5126:
        raise ValueError(f"accessor {ai}: only dense float accessors are posed")
    n = _COUNT[a["type"]]
    bv = doc["bufferViews"][a["bufferView"]]
    off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = bv.get("byteStride", 4 * n)
    return np.ndarray((a["count"], n), dtype="<f4", buffer=binc, offset=off, strides=(stride, 4)).astype(np.float64)


_TRANSFORM_KEYS = {"matrix": [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0], "translation": [0, 0, 0],
                   "rotation": [0, 0, 0, 1.0], "scale": [1.0, 1.0, 1.0]}


def _node_transformed(node: dict) -> bool:
    return any(k in node and not np.allclose(node[k], ident) for k, ident in _TRANSFORM_KEYS.items())


def pose_glb(src: Path, dst: Path, moves: dict[str, tuple], node_group: dict[str, str]) -> dict:
    """``dst`` = ``src`` with every mesh node of a moved group rotated: POSITION (p - pivot) R^T + pivot, NORMAL and
    TANGENT.xyz times R^T, written as NEW accessors appended to the binary chunk; everything else byte for byte.
    ``moves``: group -> (R 3x3, pivot metres, alpha deg).

    Sharing (fix round 1): a moved node whose mesh another node also uses gets its OWN copy of the mesh entry (appended),
    so the other node keeps drawing the unmoved geometry; every rotation reads an ORIGINAL accessor from the source's
    binary chunk (one rotated accessor per (accessor, group), reused). The rotation is in the model frame, so a moved
    node (or any ancestor) with its own transform is refused rather than posed wrongly."""
    doc, binc = AC._chunks(src.read_bytes())
    out = bytearray(binc)
    n_acc0 = len(doc["accessors"])
    parent = {c: i for i, n in enumerate(doc["nodes"]) for c in n.get("children", [])}
    users: dict[int, int] = {}
    for n in doc["nodes"]:
        if "mesh" in n:
            users[n["mesh"]] = users.get(n["mesh"], 0) + 1
    rotated: dict[tuple[int, str], int] = {}
    moved, copied = [], []
    for ni, node in enumerate(doc["nodes"]):
        if "mesh" not in node:
            continue
        g = node_group.get(str(node.get("name", "")).split(".")[0])
        if g not in moves:
            continue
        j = ni
        while j is not None:
            if _node_transformed(doc["nodes"][j]):
                raise ValueError(f"{node.get('name')}: node {doc['nodes'][j].get('name', j)} has its own transform; "
                                 "only nodes in the model frame are posed")
            j = parent.get(j)
        R, piv = moves[g][0], moves[g][1]
        mi = node["mesh"]
        if users[mi] > 1:
            doc["meshes"].append(json.loads(json.dumps(doc["meshes"][mi])))
            users[mi] -= 1
            node["mesh"] = len(doc["meshes"]) - 1
            copied.append(node.get("name"))
        for pr in doc["meshes"][node["mesh"]]["primitives"]:
            if pr.get("targets"):
                raise ValueError(f"{node.get('name')}: morph targets are not posed")
            for attr in ("POSITION", "NORMAL", "TANGENT"):
                if attr not in pr["attributes"]:
                    continue
                ai = pr["attributes"][attr]
                if (ai, g) in rotated:
                    pr["attributes"][attr] = rotated[(ai, g)]
                    continue
                if ai >= n_acc0:
                    raise AssertionError(f"{node.get('name')}: {attr} would be read from an appended accessor")
                a = _accessor(doc, binc, ai)
                if attr == "POSITION":
                    b = (a - piv) @ R.T + piv
                elif attr == "NORMAL":
                    b = a @ R.T
                else:
                    b = np.column_stack([a[:, :3] @ R.T, a[:, 3]])
                data = b.astype("<f4").tobytes()
                out += b"\x00" * ((-len(out)) % 4)
                doc["bufferViews"].append({"buffer": 0, "byteOffset": len(out), "byteLength": len(data), "target": 34962})
                out += data
                acc = {"bufferView": len(doc["bufferViews"]) - 1, "componentType": 5126, "count": int(len(b)),
                       "type": doc["accessors"][ai]["type"]}
                if attr == "POSITION":
                    acc["min"] = [float(x) for x in b.astype("<f4").min(0)]
                    acc["max"] = [float(x) for x in b.astype("<f4").max(0)]
                doc["accessors"].append(acc)
                rotated[(ai, g)] = pr["attributes"][attr] = len(doc["accessors"]) - 1
            moved.append(node.get("name"))
    out += b"\x00" * ((-len(out)) % 4)
    doc["buffers"][0]["byteLength"] = len(out)
    doc.setdefault("asset", {}).setdefault("extras", {})["g3d_all_angles_posed"] = (
        "COPY for the all-angles owner sheet: temples rotated about their hinge pivots by the photo's fitted hinge angles "
        f"({', '.join(f'{g} opened {m[2]:+.4f} deg (pose.hinge_matrix)' for g, m in moves.items())}); not a model.")
    js = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    js += b" " * ((-len(js)) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(out), 0x004E4942) + bytes(out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)
    return {"nodes": sorted(set(moved)), "meshes_copied_for": sorted(set(copied)), "sha256": sha256_file(dst)}


def prepare(out: Path, camera_source: str = "bake") -> dict:
    """Inputs, cameras, crops, backgrounds and the hinge-posed copies (when the camera source has hinge angles); writes
    cameras/*.json."""
    inputs = verify_inputs()
    log("inputs verified: " + ", ".join(f"{k} {v['sha256'][:12]}" for k, v in inputs["options"].items()))
    job = IN.load_job(IN.FT1119['cameras'], with_astra=False)
    _, groups, _, _ = load_pose(job)          # the hinge groups (parts, sides, pivots) of step 0's registration
    cams, all_hinges, cam_source = photo_cameras(job, camera_source)
    paths = glb_paths()
    gby = {g["name"]: g for g in groups}
    ng = node_groups(paths["A"], job, groups)
    for key in ("astra", "B", "C"):
        if node_groups(paths[key], job, groups) != ng:
            raise SystemExit(f"{key}: its nodes do not map to the hinge groups as A's do")
    wp3b = json.loads((G3D / "data/wp3b/ft1119/cameras.json").read_text(encoding="utf-8"))
    rows, cam_files = {}, {}
    (out / "cameras").mkdir(parents=True, exist_ok=True)
    rest_cams = []
    for short, stem, title in PHOTO_ROWS:
        vname = f"reference-{short}.png"
        view = job.views[vname]
        cam = cams[vname]
        hinges = {g: a for (v, g), a in all_hinges.items() if v == vname and a != 0.0}
        V = job.mesh.V.copy()
        moves = {}
        for gname, alpha in sorted(hinges.items()):
            g = gby[gname]
            R = hinge_matrix(alpha, g["side"])
            piv = np.asarray(g["hinge_pivot_mm"], float)
            pids = [i for i, n in enumerate(job.mesh.names) if n in set(g["parts"])]
            vi = np.unique(job.mesh.F[np.isin(job.mesh.part, pids)])
            V[vi] = (V[vi] - piv) @ R.T + piv
            moves[gname] = (R, piv / 1000.0, float(alpha))
        uv_cam = project_mm(V, cam, job.frame)
        viewm, f = photo_camera(cam, job.frame)
        uv_pin = project_view(viewm, f, cam.center_x, cam.center_y, V / 1000.0)
        err = float(np.abs(uv_pin - uv_cam).max())
        if err > 1e-6:
            raise SystemExit(f"{vname}: the pinhole camera disagrees with Camera.project by {err} px")
        crop = crop_box(view.matte, uv_cam)
        size = tile_size(crop[2], crop[3])
        kx, ky = size[0] / crop[2], size[1] / crop[3]
        bg_hex, bg_rgb = background_hex(view.image, view.matte, crop)
        Q = (V / 1000.0) @ viewm[:3, :3].T + viewm[:3, 3]
        d = -Q[:, 2]
        spec = {"id": f"photo_{short}", "width": size[0], "height": size[1], "background": bg_hex,
                "view": [float(x) for x in viewm.reshape(-1)], "fx": f * kx, "fy": f * ky,
                "cx": (cam.center_x + 0.5 - crop[0]) * kx - 0.5, "cy": (cam.center_y + 0.5 - crop[1]) * ky - 0.5,
                "near": float(max(1e-3, 0.5 * d.min())), "far": float(2.0 * d.max()),
                "meta": {"photo": vname, "crop_native_xywh": list(crop)}}
        # the tile camera must equal the photo camera mapped into the tile (pixel-centre convention)
        Qt = (V / 1000.0) @ viewm[:3, :3].T + viewm[:3, 3]
        uv_tile = np.column_stack([spec["cx"] + spec["fx"] * Qt[:, 0] / -Qt[:, 2], spec["cy"] - spec["fy"] * Qt[:, 1] / -Qt[:, 2]])
        uv_back = np.column_stack([(uv_tile[:, 0] + 0.5) / kx - 0.5 + crop[0], (uv_tile[:, 1] + 0.5) / ky - 0.5 + crop[1]])
        err_t = float(np.abs(uv_back - uv_cam).max())
        if err_t > 1e-6:
            raise SystemExit(f"{vname}: the tile camera disagrees with the photo camera by {err_t} px")
        rec = {"photo": vname, "title": title, "stem": stem, "view_kind": view.kind,
               "photo_path": _rel(Path(job.sources["images"][vname]["path"])), "photo_sha256": view.sha256,
               "photo_shape_hw": list(view.shape),
               "camera": cam.to_dict(), "camera_source": cam_source["source"],
               "camera_equals_bake_wp3b": cam.to_dict() == wp3b["images"][vname]["camera"],
               "bake_wp3b_camera": wp3b["images"][vname]["camera"],
               "pinhole": {"view_row_major_m": spec["view"], "focal_px_native": f, "principal_px_native": [cam.center_x, cam.center_y],
                           "max_abs_px_vs_Camera_project": err},
               "crop_native_xywh": list(crop), "tile_px": list(size), "tile_scale": [kx, ky],
               "background": {"hex": bg_hex, "rgb": bg_rgb, "rule": "median photo colour outside the matte dilated 25 px, in the crop"},
               "hinges_deg": {g: float(a) for g, a in hinges.items()},
               "hinge_posing": ("posed copies (temples rotated about their hinge pivots by the fitted angles)" if hinges
                                else ("rest state: the temples as the bake painted them (the model's)" if camera_source == "bake"
                                      else "rest state: the fitted pose holds no hinge angle for this photo (its temples are the model's)"))}
        if hinges:
            posed = {}
            for key, _ in OPTIONS:
                dst = out / "glbs" / f"{key}_posed_{short}.glb"
                info = pose_glb(paths[key], dst, moves, ng)
                fm = fitmesh.load_fit_mesh(dst, skip=tuple(wp3b["model"]["skip"]), frame=job.frame)
                rest = fitmesh.load_fit_mesh(paths[key], skip=tuple(wp3b["model"]["skip"]), frame=job.frame)
                if fm.names != job.mesh.names or rest.names != job.mesh.names or len(fm.V) != len(rest.V):
                    raise SystemExit(f"{dst}: the posed copy's parts differ")
                # its own rest mesh (the painted copies split vertices at UV seams) rotated as the Deformer rotates S
                Vr = rest.V.copy()
                for gname, (R, piv_m, _a) in moves.items():
                    pids = [i for i, n in enumerate(rest.names) if n in set(gby[gname]["parts"])]
                    vi = np.unique(rest.F[np.isin(rest.part, pids)])
                    Vr[vi] = (Vr[vi] - piv_m * 1000.0) @ R.T + piv_m * 1000.0
                dv = float(np.abs(fm.V - Vr).max())
                if dv > 1e-3:
                    raise SystemExit(f"{dst}: posed vertices are {dv} mm from the hinge rotation of the rest mesh")
                posed[key] = {"path": _rel(dst), "sha256": info["sha256"], "nodes_rotated": info["nodes"],
                              "max_abs_mm_vs_pose_hinge_matrix": dv, "source_sha256": inputs["options"][key]["sha256"]}
            rec["posed_glbs"] = posed
            spec_file = out / "cameras" / f"photo_{short}.json"
            spec_file.write_text(json.dumps({"schema": "arview.cameras/1", "cameras": [spec]}, indent=1), encoding="utf-8")
            cam_files[short] = spec_file
        else:
            rest_cams.append(spec)
        rec["camera_spec"] = {k: v for k, v in spec.items() if k != "view"}
        rows[short] = rec
    rest_file = out / "cameras" / "photos_rest.json"
    rest_file.write_text(json.dumps({"schema": "arview.cameras/1", "cameras": rest_cams}, indent=1), encoding="utf-8")
    groups_rec = {g["name"]: {k: g[k] for k in ("side", "hinge_pivot_mm", "hinge_axis", "parts")} for g in groups}
    return {"inputs": inputs, "rows": rows, "cameras_files": {"rest": _rel(rest_file), **{k: _rel(v) for k, v in cam_files.items()}},
            "hinge_groups": groups_rec, "node_groups": ng, "cameras": cam_source,
            "front_centre_m": [float(x) for x in front_centre_m(job, groups)],
            "front_centre_rule": "centre of the box of the front group's fit-mesh vertices (viewing directions start here)",
            "pose": {"groups_source": _rel(REGISTRATION), "groups_source_sha256": sha256_file(REGISTRATION),
                     "parallax_note": "the pose's parallax is a sampling correction of the bake (pose.py: the GLB keeps S); it is not drawn"}}


# ----------------------------------------------------------------------------- captures
def run_capture(glb: str, out: str, args: list) -> dict:
    """One ``arview capture`` (node opted out of EcoQoS; this pool worker is too). Never kills a run that is progressing:
    the deadline is far beyond any capture (each step has its own 60 s stall deadline inside the capture)."""
    cmd = ["node", "bin/arview.mjs", "capture", glb, *args, "--out", out]
    t = time.time()
    p = subprocess.Popen(cmd, cwd=str(ARVIEW), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace")
    eco = opt_out_ecoqos(p.pid)
    so, se = p.communicate(timeout=1800)
    Path(out + ".log").write_text(" ".join(cmd) + "\n" + so[-20000:] + "\n--- stderr ---\n" + se[-20000:], encoding="utf-8")
    return {"command": "node bin/arview.mjs capture " + " ".join(f'"{c}"' if " " in c else c for c in cmd[3:]),
            "cwd": _rel(ARVIEW), "exit": p.returncode, "seconds": round(time.time() - t, 1), "node_ecoqos_opt_out": eco,
            "out": _rel(Path(out)), "summary": so.strip().splitlines()[-1] if so.strip() else None}


def capture_jobs(out: Path, prep: dict) -> list[tuple]:
    paths = glb_paths()
    jobs = []
    for key, _ in OPTIONS:
        jobs.append((str(paths[key]), str(out / "captures" / f"{key}_rest"),
                     ["--cameras", str(REPO / prep["cameras_files"]["rest"]), "--no-face"]))
        for short, rec in prep["rows"].items():
            if "posed_glbs" in rec:
                jobs.append((str(REPO / rec["posed_glbs"][key]["path"]), str(out / "captures" / f"{key}_posed_{short}"),
                             ["--cameras", str(REPO / prep["cameras_files"][short]), "--no-face"]))
        jobs.append((str(paths[key]), str(out / "tryon" / key), ["--poses", "all", "--backgrounds", "skin"]))
    return jobs


def capture_all(out: Path, prep: dict, workers: int | None) -> list[dict]:
    jobs = capture_jobs(out, prep)
    for _, o, _ in jobs:
        if Path(o).exists() and any(Path(o).iterdir()):
            raise SystemExit(f"{o} is not empty: captures always go to fresh directories")
    log(f"{len(jobs)} captures in a pool of {default_workers(len(jobs)) if workers is None else workers} workers")
    res = pool_map(run_capture, jobs, workers=workers if workers else default_workers(len(jobs)), tag="all_angles captures")
    for r in res:
        log(f"capture {r['out']}: exit {r['exit']} in {r['seconds']} s")
        if r["exit"] not in (0, 3):
            raise SystemExit(f"capture failed: {r}")
        if r["exit"] == 3:
            # captured, but the capture's own spec check failed or could not verify: kept, and carried into the summary
            r["warning"] = "exit 3: the images were captured but the capture's spec check failed or could not verify (see its report.json)"
            log(f"WARNING capture {r['out']}: {r['warning']}")
    return res


def _warning_source(text: str) -> str:
    if "degenerate" in text:
        return "the model's own lens triangles"
    if "FIT SCALE" in text:
        return "the worn portrait's live fit size (the try-on's auto-fit)"
    return "other"


def _warning_key(text: str) -> str:
    """A warning with node-name suffixes ('.001') and numbers blanked, to find the same warning in another capture."""
    import re
    return re.sub(r"\d+(\.\d+)?", "#", re.sub(r"\.\d{3}\b", "", text))


def capture_warnings(state: dict, root: Path = REPO, before: Path | None = None) -> list[dict]:
    """Every capture that did not come back clean (fix round 2: the first version listed only exits other than 0, so 8
    reports with status 'captured_with_warnings' were summarised as 'no warnings'): its exit, its report's status and
    each warning with where it comes from and whether the capture made before this work (``before``: a report.json
    directory, default ``byte_identity/before1`` under the run) carried the same warning."""
    out = []
    before_keys = set()
    bp = (before if before is not None else OUT / "byte_identity" / "before1") / "report.json"
    if bp.is_file():
        before_keys = {_warning_key(str(w)) for w in json.loads(bp.read_text(encoding="utf-8")).get("warnings") or []}
    for c in state.get("captures") or []:
        rp = root / c["out"] / "report.json"
        r = json.loads(rp.read_text(encoding="utf-8")) if rp.is_file() else {}
        status = r.get("status", "no report.json")
        warns = [str(w) for w in r.get("warnings") or []]
        if c["exit"] != 0 or status != "captured" or warns:
            out.append({"out": c["out"], "exit": c["exit"], "status": status, "exit_warning": c.get("warning"),
                        "warnings": [{"text": w, "source": _warning_source(w), "also_before_this_work": _warning_key(w) in before_keys}
                                     for w in warns]})
    return out


# ----------------------------------------------------------------------------- measure
def silhouette(png: Path) -> np.ndarray:
    a = np.asarray(Image.open(png).convert("RGB"))
    return ndimage.binary_fill_holes(a.max(axis=2) > SILHOUETTE_LEVEL)


def capture_dir(out: Path, key: str, short: str, rec: dict) -> Path:
    return out / "captures" / (f"{key}_posed_{short}" if "posed_glbs" in rec else f"{key}_rest")


def side_check(report: dict, png: Path, rec: dict, glb: Path) -> dict:
    """Left / right: the rendered centroids of the x < 0 and x > 0 parts (part-id palette sides, decided by geometry)
    against the camera's own projection of those parts' vertices."""
    pal = report.get("id_palette") or []
    a = np.asarray(Image.open(png).convert("RGB")).astype(int)
    spec = rec["camera_spec"]
    view = np.asarray(rec["pinhole"]["view_row_major_m"], float).reshape(4, 4)
    doc, binc = AC._chunks(glb.read_bytes())
    pts = {"left": [], "right": []}
    lens_names = {e.get("name") for e in pal if e.get("class") == "lens"}
    for node in doc["nodes"]:
        if "mesh" not in node or node.get("name") in lens_names:
            continue
        P = np.concatenate([_accessor(doc, binc, pr["attributes"]["POSITION"]) for pr in doc["meshes"][node["mesh"]]["primitives"]])
        if P[:, 0].max() <= 0:
            pts["left"].append(P)
        elif P[:, 0].min() >= 0:
            pts["right"].append(P)
    exp, got = {}, {}
    for side in ("left", "right"):
        P = np.concatenate(pts[side])
        Q = P @ view[:3, :3].T + view[:3, 3]
        uv = np.column_stack([spec["cx"] + spec["fx"] * Q[:, 0] / -Q[:, 2], spec["cy"] - spec["fy"] * Q[:, 1] / -Q[:, 2]])
        exp[side] = uv.mean(0)
        m = np.zeros(a.shape[:2], bool)
        for e in pal:
            # every lens draws the one lens colour (part-id), so lenses say nothing about a side
            if e.get("side") == side and e.get("class") != "lens":
                m |= (np.abs(a - np.asarray(e["rgb"])).max(axis=2) <= max(4, int(e.get("decode_tolerance", 8))))
        ys, xs = np.nonzero(m)
        got[side] = np.array([xs.mean(), ys.mean()]) if len(xs) else None
    de = exp["right"] - exp["left"]
    if got["left"] is None or got["right"] is None or np.linalg.norm(de) < 5:
        return {"verdict": "not decidable", "expected_right_minus_left_px": de.round(1).tolist()}
    dg = got["right"] - got["left"]
    return {"verdict": "ok" if float(de @ dg) > 0 else "SWAPPED", "expected_right_minus_left_px": de.round(1).tolist(),
            "rendered_right_minus_left_px": dg.round(1).tolist()}


def _edge(mask: np.ndarray) -> np.ndarray:
    return mask & ~ndimage.binary_erosion(mask)


def boundary_mm(render: np.ndarray, photo: np.ndarray, k: float, ppm: float) -> dict:
    """Symmetric boundary distance between the two silhouettes (tile px -> native px -> mm at the front pivot)."""
    er, ep = _edge(render), _edge(photo)
    d_to_p = ndimage.distance_transform_edt(~ep)[er]
    d_to_r = ndimage.distance_transform_edt(~er)[ep]
    both = np.concatenate([d_to_p, d_to_r]) / k / ppm
    return {"mean_mm": round(float(both.mean()), 2), "p90_mm": round(float(np.percentile(both, 90)), 2),
            "render_to_photo_mean_mm": round(float(d_to_p.mean() / k / ppm), 2),
            "photo_to_render_mean_mm": round(float(d_to_r.mean() / k / ppm), 2)}


REFINE_ROUND_EVALS = 1500         # one Powell round
REFINE_MAX_EVALS = 6000           # all rounds
REFINE_MIN_GAIN = 1e-4            # a converged round that gains less IoU than this ends the search
REFINE_NEAR_BOUND = 0.05
REFINE_SEARCH = "powell restarted from its own result until a round converges with gain < 1e-4 (fix round 2)"
# the diagnostic search beyond the pose model's bounds (``wide=True``): yaw / pitch, magnification and temple half-ranges
WIDE_ANG_DEG, WIDE_MAG_PCT, WIDE_HINGE_DEG = 10.0, 10.0, 15.0


def refine_check(short: str, cam_dict: dict, hinges_deg: dict | None = None, wide: bool = False) -> dict:
    """Diagnostic for a row under IOU_MIN: the best silhouette IoU any nearby camera reaches, starting from the row's
    camera (``cam_dict``) and temple angles, with yaw / pitch within 6 deg, roll within 4 deg, the front pivot within
    3 mm, magnification within 4 %, perspective within 0.3 and both temples opened or folded within the pose model's own
    bound (6 deg, ``pose.HINGE_BOUND_DEG``); raster renders of q001 (the four options share its geometry) at stride 2,
    Powell, then the winner at full resolution. A refinement that cannot reach IOU_MIN says the gap is the model's shape
    against the photo, not the camera.
    ``wide`` (fix round 2, diagnostic only): yaw / pitch within WIDE_ANG_DEG, magnification within WIDE_MAG_PCT and the
    temples within WIDE_HINGE_DEG, beyond what the pose model admits: how much of the gap a looser camera could hide."""
    from scipy import optimize
    from glasses3d.g3d_host.photoprint.pose import HINGE_BOUND_DEG, pack, unpack
    opt_out_ecoqos(None)
    job = IN.load_job(IN.FT1119['cameras'], with_astra=False)
    _, groups, _, _ = load_pose(job)
    gby = {g["name"]: g for g in groups}
    vname = f"reference-{short}.png"
    view, cam0 = job.views[vname], Camera.from_dict(cam_dict)
    h0 = [float((hinges_deg or {}).get(g, 0.0)) for g in ("temple-x", "temple+x")]
    fg = view.matte
    H, W = fg.shape
    gv = {}
    for gname in ("temple-x", "temple+x"):
        pids = [i for i, n in enumerate(job.mesh.names) if n in set(gby[gname]["parts"])]
        gv[gname] = np.unique(job.mesh.F[np.isin(job.mesh.part, pids)])
    ys, xs = np.nonzero(fg)
    mg = int(0.15 * (xs.max() - xs.min()))
    roi = (max(0, xs.min() - mg), max(0, ys.min() - mg), min(W, xs.max() + mg), min(H, ys.max() + mg))
    ref2 = raster.downsample_mask(fg, 2, roi) >= 0.5

    def iou(cam, h, stride, roi_, ref):
        V = job.mesh.V.copy()
        for gname, a in zip(("temple-x", "temple+x"), h):
            if a:
                R, piv = hinge_matrix(a, gby[gname]["side"]), np.asarray(gby[gname]["hinge_pivot_mm"])
                V[gv[gname]] = (V[gv[gname]] - piv) @ R.T + piv
        try:
            r = raster.get_scene(V, job.mesh.F, job.frame).render(cam, (H, W), stride, roi_)
        except ValueError:
            return 0.0
        m = ndimage.binary_fill_holes(r["mask"])
        return float((m & ref).sum() / max(1, (m | ref).sum()))

    x0 = pack(job, cam0)
    ppm = job.ppm(vname, cam0)
    free = [0, 1, 2, 4, 5, 6, 3]
    ang, mag, hb = (WIDE_ANG_DEG, WIDE_MAG_PCT, WIDE_HINGE_DEG) if wide else (6.0, 4.0, HINGE_BOUND_DEG)
    bounds = [(x0[0] - ang, x0[0] + ang), (x0[1] - ang, x0[1] + ang), (x0[2] - 4, x0[2] + 4), (x0[4] - mag, x0[4] + mag),
              (x0[5] - 3 * ppm, x0[5] + 3 * ppm), (x0[6] - 3 * ppm, x0[6] + 3 * ppm), (max(0.0, x0[3] - 3), x0[3] + 3),
              (-hb, hb), (-hb, hb)]

    def cam_of(p):
        x = x0.copy()
        for i, k in enumerate(free):
            x[k] = p[i]
        return unpack(job, x)

    p0 = np.array([x0[k] for k in free] + h0)
    t = time.time()
    # fix round 2: Powell restarted from its own result until a round converges and gains nothing (the first version
    # stopped row 03 at its 900-evaluation cap); every round and whether it converged is recorded
    fun = lambda p: 1.0 - iou(cam_of(p), tuple(p[7:]), 2, roi, ref2)
    x, fx, rounds, total = p0.copy(), fun(p0), [], 0
    while total < REFINE_MAX_EVALS:
        cap = min(REFINE_ROUND_EVALS, REFINE_MAX_EVALS - total)
        r = optimize.minimize(fun, x, method="Powell", bounds=bounds, options={"maxfev": cap, "xtol": 1e-3, "ftol": 1e-5})
        total += int(r.nfev)
        converged = bool(r.success) and int(r.nfev) < cap
        gain = float(fx - r.fun)
        rounds.append({"evaluations": int(r.nfev), "iou_stride2": round(1.0 - float(r.fun), 5), "converged": converged,
                       "message": str(r.message)})
        if r.fun < fx:
            x, fx = r.x.copy(), float(r.fun)
        if converged and gain < REFINE_MIN_GAIN:
            break
    cam1 = cam_of(x)
    full = (0, 0, W, H)
    names = ["yaw_deg", "pitch_deg", "roll_deg", "magnification_pct", "pivot_u_px", "pivot_v_px", "perspective_x10",
             "hinge_temple-x_deg", "hinge_temple+x_deg"]
    at_bound = [n for i, n in enumerate(names)
                if min(x[i] - bounds[i][0], bounds[i][1] - x[i]) <= REFINE_NEAR_BOUND * (bounds[i][1] - bounds[i][0])]
    return {"photo": vname, "iou_fitted_camera_full_res": round(iou(cam0, tuple(h0), 1, full, fg), 4),
            "iou_best_refined_full_res": round(iou(cam1, tuple(x[7:]), 1, full, fg), 4),
            "refined_minus_fitted": {n: round(float(x[i] - p0[i]), 3) for i, n in enumerate(names)},
            "refined_values": {n: round(float(x[i]), 3) for i, n in enumerate(names)},
            "at_or_near_a_bound": at_bound, "near_bound_rule": f"within {REFINE_NEAR_BOUND:.0%} of the parameter's range of a bound",
            "refined_camera": cam1.to_dict(), "evaluations": total, "rounds": rounds,
            "converged": bool(rounds and rounds[-1]["converged"]), "max_evaluations": REFINE_MAX_EVALS,
            "search": REFINE_SEARCH, "seconds": round(time.time() - t, 1), "wide": wide,
            "bounds": (f"{'WIDE, beyond the pose model: ' if wide else ''}yaw/pitch +-{ang:g} deg, roll +-4 deg, pivot +-3 mm, "
                       f"magnification +-{mag:g} %, perspective +-0.3, hinges +-{hb:g} deg"),
            "renderer": "g3d_host.raster (q001 rest geometry, the cameras' fit mesh), stride 2 search, full-resolution verdict"}


# where a silhouette mismatch lies: the front, the +x arm (wearer's left) or the -x arm (wearer's right)
ROLE_FRONT, ROLE_PLUS, ROLE_MINUS = 1, 2, 3
NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def part_roles(report: dict, png: Path, node_group: dict[str, str]) -> np.ndarray:
    """Per pixel of a part-id render: 0 (background / undecoded), ROLE_FRONT, ROLE_PLUS (the +x hinge group) or
    ROLE_MINUS. The lenses all draw one colour (front); every other part by its node's hinge group (``node_groups``:
    decals included)."""
    a = np.asarray(Image.open(png).convert("RGB")).astype(int)
    lab = np.zeros(a.shape[:2], np.uint8)
    for e in report.get("id_palette") or []:
        base = str(e.get("name", "")).split(".")[0]
        if e.get("class") != "lens" and base not in node_group:
            # fix round 2: an unmapped part would have counted as 'front' and silently changed the near / far arm words
            raise ValueError(f"part-id palette name {e.get('name')!r} maps to no hinge group (node_groups has {sorted(node_group)})")
        g = "front" if e.get("class") == "lens" else node_group[base]
        if g not in ("front", "temple+x", "temple-x"):
            raise ValueError(f"part-id palette name {e.get('name')!r}: unknown hinge group {g!r}")
        role = ROLE_FRONT if g == "front" else (ROLE_PLUS if g == "temple+x" else ROLE_MINUS)
        m = np.abs(a - np.asarray(e["rgb"])).max(axis=2) <= max(4, int(e.get("decode_tolerance", 8)))
        lab[m & (lab == 0)] = role
    return lab


def view_side(direction: np.ndarray) -> dict:
    """Where a camera sits around the glasses (``direction``: unit, from the front to the camera, model frame): azimuth
    (0 = in front, +90 = the wearer's left), elevation, whether it is a side view (35-145 deg off the front or back) and
    which arm is near."""
    az = math.degrees(math.atan2(direction[0], direction[2]))
    return {"azimuth_deg": round(az, 1), "elevation_deg": round(math.degrees(math.asin(np.clip(direction[1], -1, 1))), 1),
            "side_view": bool(35.0 <= abs(az) <= 145.0), "near_role": ROLE_PLUS if direction[0] > 0 else ROLE_MINUS}


def height_word(elevation_deg: float) -> str | None:
    """The height words a row title must carry for a view this far above (+) or below (-) the glasses' front."""
    if elevation_deg >= 45.0:
        return "from the top"
    if elevation_deg >= 10.0:
        return "a little above"
    if elevation_deg <= -10.0:
        return "below"
    return None


def check_title_height(title: str, elevation_deg: float) -> None:
    """A row title says 'a little above' / 'from the top' / 'below' exactly when the measured view does (fix round 2:
    photo 05 was 'from the front' while it sees the glasses from 19 deg above)."""
    want = height_word(elevation_deg)
    have = [w for w in HEIGHT_WORDS if w in title]
    if have != ([want] if want else []):
        raise SystemExit(f"row title {title!r} says {have or 'no height'} but the view is {elevation_deg:+.1f} deg "
                         f"({want or 'no height word'})")


def role_names(side: dict) -> dict[int, str]:
    if side["side_view"]:
        near = side["near_role"]
        return {ROLE_FRONT: "the front", near: "the near arm", ROLE_PLUS + ROLE_MINUS - near: "the far arm"}
    return {ROLE_FRONT: "the front", ROLE_PLUS: "the wearer's left arm", ROLE_MINUS: "the wearer's right arm"}


def mismatch_parts(lab: np.ndarray, m: np.ndarray, matte: np.ndarray, k: float, ppm: float, names: dict[int, str]) -> dict:
    """Where the render's silhouette ``m`` and the photo's ``matte`` disagree, by part role. Every pixel takes the role of
    the nearest decoded part-id pixel (anti-aliased edges and filled holes inside the render; for a photo-only pixel,
    the nearest rendered part). Per role: model-only and photo-only pixels and their shares, the share of the role's
    rendered pixels the photo does not have (hidden in the photo), the mean distance of its render edge to the photo's
    edge in mm (tile px / ``k`` -> native px / ``ppm``), and centroids (tile px) for above / below words."""
    known = lab > 0
    if not known.any():
        return {"roles": {}, "model_only_px": int((m & ~matte).sum()), "photo_only_px": int((matte & ~m).sum())}
    idx = ndimage.distance_transform_edt(~known, return_distances=False, return_indices=True)
    full = lab[idx[0], idx[1]]
    mo, po = m & ~matte, matte & ~m
    mis = mo | po
    er, ep = _edge(m), _edge(matte)
    d_to_p = ndimage.distance_transform_edt(~ep)
    roles = {}
    for r, name in names.items():
        rm = full == r
        rendered = int((m & rm).sum())
        e = er & rm
        roles[name] = {"role": r, "rendered_px": rendered, "model_only_px": int((mo & rm).sum()),
                       "photo_only_px_nearest": int((po & rm).sum()),
                       "model_only_share": round(float((mo & rm).sum() / max(1, mo.sum())), 3),
                       "photo_only_share": round(float((po & rm).sum() / max(1, po.sum())), 3),
                       "mismatch_share": round(float((mis & rm).sum() / max(1, mis.sum())), 3),
                       "rendered_hidden_in_photo": round(float((mo & rm).sum() / max(1, rendered)), 3),
                       "edge_to_photo_mean_mm": round(float(d_to_p[e].mean() / k / ppm), 2) if e.any() else None,
                       "model_only_centroid_y": float(np.nonzero(mo & rm)[0].mean()) if (mo & rm).any() else None,
                       "rendered_centroid_y": float(np.nonzero(m & rm)[0].mean()) if rendered else None}
    return {"roles": roles, "model_only_px": int(mo.sum()), "photo_only_px": int(po.sum())}


def tip_plates(glb: Path) -> dict:
    """The model's arm-tip plates (mesh nodes named '*medallion*') and the side (x sign) each sits on."""
    doc, binc = AC._chunks(glb.read_bytes())
    out = {"+x": False, "-x": False, "nodes": []}
    for node in doc["nodes"]:
        if "mesh" in node and "medallion" in str(node.get("name", "")).lower():
            P = np.concatenate([_accessor(doc, binc, pr["attributes"]["POSITION"]) for pr in doc["meshes"][node["mesh"]]["primitives"]])
            s = "+x" if P[:, 0].mean() > 0 else "-x"
            out[s] = True
            out["nodes"].append({"name": node.get("name"), "side": s})
    return out


OFF_CLEARLY_MM = 2.0               # an edge this far from the photo's edge on average is 'clearly off', else 'slightly off'


def shape_note(mis: dict, side: dict, plates: dict, refined_iou: float | None) -> str:
    """The plain note under a photo row whose outline does not line up, built from the measurement only, in words (no
    numbers: the owner sheets carry plain labels): whether a bounded refinement of the camera lined it up, which part
    the mismatch is on (a far arm the photo hides, or the parts with the largest share and whether they are slightly or
    clearly off), and a missing tip plate on the near arm. ``refined_iou``: the refinement check's full-resolution IoU
    (None = not run)."""
    names = role_names(side)
    roles = mis["roles"]
    if refined_iou is None:
        out = ["The model's outline does not line up with this photo, so judge the paint here, not the shape."]
    elif refined_iou < IOU_MIN:
        out = ["The model's outline does not line up with this photo, even with the camera adjusted, so judge the paint here, "
               "not the shape."]
    else:
        out = ["The model's outline lines up with this photo only when the camera is moved a little from where the paint was "
               "made from, so judge the paint here, not the shape."]
    far = names.get(ROLE_PLUS + ROLE_MINUS - side["near_role"]) if side["side_view"] else None
    near = names.get(side["near_role"]) if side["side_view"] else None
    far_hidden = bool(far and roles[far]["model_only_share"] >= 0.3 and roles[far]["rendered_hidden_in_photo"] >= 0.5
                      and roles[far]["model_only_centroid_y"] is not None and roles[near]["rendered_centroid_y"] is not None)
    if far_hidden:
        rel = "below" if roles[far]["model_only_centroid_y"] > roles[near]["rendered_centroid_y"] else "above"
        out.append(f"The model shows its far arm {rel} the near arm; in your photo the far arm is hidden.")
    order = [n for n in sorted(roles, key=lambda n: -roles[n]["mismatch_share"]) if not (far_hidden and n == far)]
    rest = [n for n in order if roles[n]["mismatch_share"] >= 0.25][:2] or order[:1]
    if rest:
        mm = [roles[n]["edge_to_photo_mean_mm"] for n in rest if roles[n]["edge_to_photo_mean_mm"] is not None]
        arms = {n for n in names.values() if n != "the front"}
        where = "both arms" if set(rest) == arms else " and ".join(rest)
        verb = "sits" if len(rest) == 1 else "sit"
        off = (f", which {verb} {'clearly' if max(mm) >= OFF_CLEARLY_MM else 'slightly'} off." if mm else ".")
        out.append(("The rest of the difference" if far_hidden else "Most of the difference") + f" is along {where}" + off)
    if side["side_view"]:
        near_sign = "+x" if side["near_role"] == ROLE_PLUS else "-x"
        far_sign = "-x" if near_sign == "+x" else "+x"
        if plates.get(far_sign) and not plates.get(near_sign):
            # 'metal plate' (fix round 2): the model's plate renders silvery like the rest of its metal; gold is the photo's
            out.append(f"The model has the metal plate at the arm's tip only on the wearer's {'left' if far_sign == '+x' else 'right'} "
                       "arm, so this arm's tip is plain.")
    return " ".join(out)


def raster_check(job, rec: dict, gby: dict, m_tile: np.ndarray) -> dict:
    """arview's part-id silhouette against a raster of the cameras' fit mesh (``g3d_host.raster``) at the SAME camera and
    temple angles, mapped into the tile like the photo: says whether arview draws the camera it was given."""
    cam = Camera.from_dict(rec["camera"])
    V = job.mesh.V.copy()
    for gname, alpha in rec["hinges_deg"].items():
        g = gby[gname]
        R, piv = hinge_matrix(alpha, g["side"]), np.asarray(g["hinge_pivot_mm"], float)
        pids = [i for i, n in enumerate(job.mesh.names) if n in set(g["parts"])]
        vi = np.unique(job.mesh.F[np.isin(job.mesh.part, pids)])
        V[vi] = (V[vi] - piv) @ R.T + piv
    H, W = job.views[rec["photo"]].shape[:2]
    x0, y0, w, h = rec["crop_native_xywh"]
    roi = (max(0, x0), max(0, y0), min(W, x0 + w), min(H, y0 + h))
    r = raster.get_scene(V, job.mesh.F, job.frame).render(cam, (H, W), 1, roi)
    native = np.zeros((H, W), np.uint8)
    native[roi[1]:roi[3], roi[0]:roi[2]] = r["mask"].astype(np.uint8) * 255
    t = ndimage.binary_fill_holes(crop_resize(native, rec["crop_native_xywh"], tuple(rec["tile_px"]), 0, Image.BILINEAR) >= 128)
    inter, union = int((t & m_tile).sum()), int((t | m_tile).sum())
    return {"iou_arview_vs_raster": round(inter / max(1, union), 4), "raster_only_px": int((t & ~m_tile).sum()),
            "arview_only_px": int((m_tile & ~t).sum()),
            "rule": "raster of the fit mesh (decals skipped, as the cameras were fitted) at the row's camera and temples, native px, "
                    "box-resized into the tile; arview's silhouette is A's part-id (holes filled)"}


def measure(out: Path, prep: dict, previous: dict | None = None, workers: int | None = None) -> dict:
    job = IN.load_job(IN.FT1119['cameras'], with_astra=False)
    _, groups, _, _ = load_pose(job)
    gby = {g["name"]: g for g in groups}
    paths = glb_paths()
    front = np.asarray(prep["front_centre_m"], float)
    plates = tip_plates(paths["A"])
    res = {}
    for short, stem, title in PHOTO_ROWS:
        rec = prep["rows"][short]
        view = job.views[rec["photo"]]
        crop, size = rec["crop_native_xywh"], tuple(rec["tile_px"])
        matte = crop_resize(view.matte.astype(np.uint8) * 255, crop, size, 0, Image.BILINEAR) >= 128
        yp, xp = np.nonzero(matte)
        kx, ky = rec["tile_scale"]
        cam = Camera.from_dict(rec["camera"])
        ppm = job.ppm(rec["photo"], cam)
        d_photo, dist = view_toward_photo(rec["pinhole"]["view_row_major_m"], front)
        side = view_side(d_photo)
        check_title_height(title, side["elevation_deg"])
        names = role_names(side)
        row = {}
        for key, _ in OPTIONS:
            d = capture_dir(out, key, short, rec)
            png = d / f"photo_{short}__part-id.png"
            report = json.loads((d / "report.json").read_text(encoding="utf-8"))
            m = silhouette(png)
            inter, union = int((m & matte).sum()), int((m | matte).sum())
            ys, xs = np.nonzero(m)
            off = [(xs.mean() - xp.mean()) / kx, (ys.mean() - yp.mean()) / ky]
            glb = REPO / rec["posed_glbs"][key]["path"] if "posed_glbs" in rec else paths[key]
            row[key] = {"iou": round(inter / union, 4), "centroid_offset_native_px": [round(float(o), 1) for o in off],
                        "centroid_offset_native_px_norm": round(float(np.hypot(*off)), 1),
                        "boundary": boundary_mm(m, matte, (kx + ky) / 2, ppm),
                        "render_only_px": int((m & ~matte).sum()), "photo_only_px": int((matte & ~m).sum()),
                        "left_right": side_check(report, png, rec, glb), "capture_status": report["status"],
                        "mismatch": mismatch_parts(part_roles(report, png, prep["node_groups"]), m, matte, (kx + ky) / 2, ppm, names),
                        "rendered_sha256": report["runtime"].get("rendered_sha256") if isinstance(report.get("runtime"), dict) else None}
            if key == "A":
                row_cam = raster_check(job, rec, gby, m)
            ov = np.asarray(Image.open(d / f"photo_{short}__beauty.png").convert("RGB")).astype(float) * 0.55 + 110
            ov[m & ~matte] = [40, 90, 255]
            ov[matte & ~m] = [255, 40, 40]
            (out / "overlays").mkdir(parents=True, exist_ok=True)
            Image.fromarray(np.clip(ov, 0, 255).astype(np.uint8)).save(out / "overlays" / f"{short}_{key}.png")
        log(f"row {short}: IoU " + ", ".join(f"{k} {v['iou']:.3f}" for k, v in row.items())
            + "; left/right " + ", ".join(v["left_right"]["verdict"] for v in row.values())
            + f"; arview vs raster at the same camera {row_cam['iou_arview_vs_raster']:.3f}")
        row["camera_check"] = row_cam
        row["view"] = {**side, "camera_distance_m": round(dist, 3), "near_arm": names.get(side["near_role"]) if side["side_view"] else None}
        res[short] = row
    low = [s for s, row in res.items() if any(row[k]["iou"] < IOU_MIN for k, _ in OPTIONS)]
    # per row under IOU_MIN: the refinement check within the pose model's bounds (it decides the owner note) and the
    # diagnostic beyond them (summary only), in one pool; a check already made for the same camera, temples and search
    # is reused (it reads no capture)
    prev = (previous or {})
    todo = []
    for s in low:
        for wide, key in ((False, "refinement_check"), (True, "wide_search_check")):
            chk = (prev.get(s) or {}).get(key)
            if (chk and chk.get("fitted_camera") == prep["rows"][s]["camera"] and chk.get("search") == REFINE_SEARCH
                    and bool(chk.get("wide")) == wide and (chk.get("hinges_deg") or {}) == prep["rows"][s]["hinges_deg"]):
                res[s][key] = chk
                log(f"row {s}: {key} reused (same camera, temples and search)")
            else:
                todo.append((s, wide, key))
    if todo:
        log(f"rows under IoU {IOU_MIN}: {low}; {len(todo)} refinement checks run in a pool (several minutes; progress every 30 s)")
        jobs = [(s, prep["rows"][s]["camera"], prep["rows"][s]["hinges_deg"], wide) for s, wide, _ in todo]
        for (s, wide, key), chk in zip(todo, pool_map(refine_check, jobs, workers=workers if workers is not None else default_workers(len(jobs)), tag="all_angles refine check")):
            chk["fitted_camera"] = prep["rows"][s]["camera"]
            chk["hinges_deg"] = prep["rows"][s]["hinges_deg"]
            res[s][key] = chk
            log(f"row {s}: {key}: fitted camera IoU {chk['iou_fitted_camera_full_res']}, best "
                + ("camera + hinges beyond the pose model's bounds (diagnostic)" if wide else "nearby camera + hinges")
                + f" {chk['iou_best_refined_full_res']}")
    for s, row in res.items():
        if min(row[k]["iou"] for k, _ in OPTIONS) < IOU_MIN:
            chk = row.get("refinement_check") or {}
            row["sheet_note"] = shape_note(row["A"]["mismatch"], row["view"], plates, chk.get("iou_best_refined_full_res"))
    res["_tip_plates"] = plates
    return res


# ----------------------------------------------------------------------------- how different the options look
_SRGB_TO_XYZ = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])
_D65 = np.array([0.95047, 1.0, 1.08883])


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB 0..255 (..., 3) -> CIE L*a*b* (D65)."""
    c = np.asarray(rgb, float) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    t = (lin @ _SRGB_TO_XYZ.T) / _D65
    e = (6.0 / 29.0) ** 3
    f = np.where(t > e, np.cbrt(np.maximum(t, e)), t / (3 * (6.0 / 29.0) ** 2) + 4.0 / 29.0)
    return np.stack([116.0 * f[..., 1] - 16.0, 500.0 * (f[..., 0] - f[..., 1]), 200.0 * (f[..., 1] - f[..., 2])], axis=-1)


def frame_pixels(report: dict, png: Path) -> np.ndarray:
    """The pixels of a part-id render that decode to a non-lens part (the frame: the print is on it, the lenses carry none)."""
    a = np.asarray(Image.open(png).convert("RGB")).astype(int)
    m = np.zeros(a.shape[:2], bool)
    for e in report.get("id_palette") or []:
        if e.get("class") != "lens":
            m |= np.abs(a - np.asarray(e["rgb"])).max(axis=2) <= max(4, int(e.get("decode_tolerance", 8)))
    return m


def difference_stats(ref: np.ndarray, img: np.ndarray, mask: np.ndarray) -> dict:
    """How ``img`` differs from ``ref`` (both sRGB uint8) on ``mask``: the share of pixels off by more than VISIBLE_LEVELS
    in some channel, the CIE76 colour difference and the mean L* and C*ab change."""
    a, b = ref[mask].astype(float), img[mask].astype(float)
    d = np.abs(b - a).max(axis=1)
    la, lb = srgb_to_lab(a), srgb_to_lab(b)
    de = np.linalg.norm(lb - la, axis=1)
    return {"px": int(mask.sum()), "mean_levels": round(float(d.mean()), 2), "p99_levels": round(float(np.percentile(d, 99)), 1),
            "share_over_levels": round(float((d > VISIBLE_LEVELS).mean()), 4), "mean_dE76": round(float(de.mean()), 2),
            "p90_dE76": round(float(np.percentile(de, 90)), 2), "mean_dL": round(float((lb[:, 0] - la[:, 0]).mean()), 2),
            "mean_dC": round(float((np.hypot(lb[:, 1], lb[:, 2]) - np.hypot(la[:, 1], la[:, 2])).mean()), 2)}


def option_differences(out: Path, prep: dict) -> dict:
    """B and C against A on the frame pixels of every photo row and every try-on pose (the four share one geometry, so
    one mask per row: A's part-id), and per option whether the difference is visible (VISIBLE_SHARE / JND_DE)."""
    rows = {}
    for short, _, _ in PHOTO_ROWS:
        rec = prep["rows"][short]
        dA = capture_dir(out, "A", short, rec)
        mask = frame_pixels(json.loads((dA / "report.json").read_text(encoding="utf-8")), dA / f"photo_{short}__part-id.png")
        ims = {k: np.asarray(Image.open(capture_dir(out, k, short, rec) / f"photo_{short}__beauty.png").convert("RGB")) for k in ("A", "B", "C")}
        rows[f"photo_{short}"] = {k: difference_stats(ims["A"], ims[k], mask) for k in ("B", "C")}
    rep = json.loads((out / "tryon" / "A" / "report.json").read_text(encoding="utf-8"))
    for pid, _ in TRYON_ROWS:
        if pid == "portrait":
            continue
        mask = frame_pixels(rep, out / "tryon" / "A" / f"{pid}__part-id.png")
        ims = {k: np.asarray(Image.open(out / "tryon" / k / f"{pid}__skin__beauty.png").convert("RGB")) for k in ("A", "B", "C")}
        rows[f"tryon_{pid}"] = {k: difference_stats(ims["A"], ims[k], mask) for k in ("B", "C")}
    per = {}
    for k in ("B", "C"):
        st = [r[k] for r in rows.values()]
        per[k] = {"max_share_over_levels": max(s["share_over_levels"] for s in st), "max_mean_dE76": max(s["mean_dE76"] for s in st),
                  "mean_dL_range": [min(s["mean_dL"] for s in st), max(s["mean_dL"] for s in st)],
                  "mean_dC_range": [min(s["mean_dC"] for s in st), max(s["mean_dC"] for s in st)]}
        per[k]["visible"] = bool(per[k]["max_share_over_levels"] >= VISIBLE_SHARE or per[k]["max_mean_dE76"] >= JND_DE)
    return {"rows": rows, "per_option": per,
            "rule": (f"frame pixels (part-id, lenses excluded) of A's render; visible = on some row at least {VISIBLE_SHARE:.0%} of "
                     f"them differ from A by more than {VISIBLE_LEVELS}/255 in a channel, or the mean CIE76 difference is at least "
                     f"{JND_DE} (a just-noticeable step)")}


def option_labels(diffs: dict) -> dict:
    """The column labels and the legend line, from the measured differences: an option visibly different from A is
    'darker' when it is darker on every row (or 'more colourful' when it gains chroma on every row); one that is not
    visibly different says so ('almost the same as A'), with what little changes."""
    labels = {"astra": "Astra's texture (now)", "A": "A (new paint)"}
    phrases, why = [], {}
    for k in ("B", "C"):
        p = diffs["per_option"][k]
        darker, richer = p["mean_dL_range"][1] < 0, p["mean_dC_range"][0] > 0
        if p["visible"] and darker:
            labels[k], ph = f"{k} (darker)", f"{k} is A made darker"
        elif p["visible"] and richer:
            labels[k], ph = f"{k} (more colourful)", f"{k} is A made more colourful"
        elif p["visible"]:
            labels[k], ph = f"{k} (a different shade of A)", f"{k} is A in a different shade"
        else:
            labels[k] = f"{k} (almost the same as A)"
            ph = (f"{k} is A with slightly " + ("richer colour" if richer else "darker colour" if darker else "different colour")
                  + ", so little that it looks almost the same as A")
        phrases.append(ph)
        why[k] = {"visible": p["visible"], "darker_on_every_row": darker, "more_chroma_on_every_row": richer}
    legend = "A is the new paint made from your photos; " + "; ".join(phrases) + ". Astra's texture is what the glasses have now."
    return {"labels": labels, "legend": legend, "why": why}


def byte_identity_problems(bi: dict) -> list[str]:
    """What is wrong with the try-on byte-identity record (fix round 2: nothing used to check it, while arview's README
    cites it as the proof that the face path's images are unchanged)."""
    out = []
    if not bi.get("before_runs_identical"):
        out.append("the two try-on captures made before the --cameras mode differ from each other: the byte-identity proof has no baseline")
    if not bi.get("after_identical_to_before"):
        out.append(f"the fresh try-on capture of A differs from the captures made before the --cameras mode in "
                   f"{len(bi.get('differing_files') or [])} image(s): arview's face path changed what it draws")
    return out


# ----------------------------------------------------------------------------- sheets
def _font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _text_center(draw, box, text, font, fill=(30, 30, 30)):
    x0, y0, x1, y1 = box
    lines = text.split("\n")
    hs = [draw.textbbox((0, 0), ln, font=font) for ln in lines]
    total = sum(b[3] - b[1] for b in hs) + 6 * (len(lines) - 1)
    y = y0 + (y1 - y0 - total) / 2
    for ln, b in zip(lines, hs):
        w = b[2] - b[0]
        draw.text((x0 + (x1 - x0 - w) / 2 - b[0], y - b[1]), ln, font=font, fill=fill)
        y += b[3] - b[1] + 6


def _wrap(draw, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= width or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def _measure_draw() -> ImageDraw.ImageDraw:
    return ImageDraw.Draw(Image.new("RGB", (8, 8)))


def compose_row(title: str, tiles: list, headers: list[str], tile_w: int, label_w: int, header: bool = True,
                captions: list | None = None, empty_texts: list | None = None, note: str | None = None) -> Image.Image:
    """One row image: a title column, then the tiles (each scaled to ``tile_w``), headers above when ``header``.
    ``captions``: text under a tile (may hold '\\n'); ``empty_texts``: text inside an empty (None) tile; ``note``: a
    wrapped line under the whole row (what the owner must know to read it)."""
    k = max(1.0, tile_w / 560.0)
    label_w = int(round(label_w * k))
    gap, head_h = 12, (int(54 * k) if header else 0)
    hs = [round(t.height * tile_w / t.width) if t is not None else 0 for t in tiles]
    th = max(h for h in hs if h) if any(hs) else 200
    fb, fr, fc = _font(FONT_B, int(26 * k)), _font(FONT_B, int(24 * k)), _font(FONT, int(21 * k))
    fn = _font(FONT, int(23 * k))
    n_cap = max((c.count("\n") + 1 for c in captions or [] if c), default=0)
    cap_h = (int(27 * k) * n_cap + int(8 * k)) if n_cap else 0
    W = label_w + len(tiles) * (tile_w + gap) + gap
    note_txt = _wrap(_measure_draw(), note, fn, W - label_w - 2 * gap) if note else None
    note_h = ((note_txt.count("\n") + 1) * (int(23 * k) + 8) + gap) if note_txt else 0
    H = head_h + th + cap_h + note_h + 2 * gap
    im = Image.new("RGB", (W, H), (255, 255, 255))
    dr = ImageDraw.Draw(im)
    _text_center(dr, (8, head_h, label_w - 8, head_h + th), _wrap(dr, title, fr, label_w - 24), fr)
    for i, t in enumerate(tiles):
        x = label_w + gap + i * (tile_w + gap)
        if header:
            _text_center(dr, (x, 6, x + tile_w, head_h), headers[i], fb)
        if t is None:
            box = (x, head_h + gap, x + tile_w - 1, head_h + gap + th - 1)
            dr.rectangle(box, outline=(225, 225, 225), fill=(250, 250, 250))
            if empty_texts and empty_texts[i]:
                _text_center(dr, box, "\n".join(_wrap(dr, ln, fc, tile_w - 48) for ln in empty_texts[i].split("\n")), fc, (125, 125, 125))
        else:
            r = t.resize((tile_w, hs[i]), Image.LANCZOS)
            im.paste(r, (x, head_h + gap + (th - hs[i]) // 2))
        if captions and captions[i]:
            _text_center(dr, (x, head_h + gap + th + 2, x + tile_w, head_h + gap + th + cap_h), captions[i], fc, (90, 90, 90))
    if note_txt:
        y = head_h + gap + th + cap_h + gap // 2
        for ln in note_txt.split("\n"):
            dr.text((label_w + gap, y), ln, font=fn, fill=(150, 60, 20))
            y += int(23 * k) + 8
    return im


def stack(rows: list[Image.Image], title: str | None = None, subtitles: tuple | list = (), footer: str | None = None) -> Image.Image:
    """The rows one under another, a title and subtitle lines (wrapped, centred) above, a footer (wrapped) below."""
    W = max(r.width for r in rows)
    md = _measure_draw()
    fsub, ffoot = _font(FONT, 25), _font(FONT, 24)
    subs = [_wrap(md, s, fsub, W - 120) for s in subtitles if s]
    sub_hs = [(s.count("\n") + 1) * 33 + 6 for s in subs]
    foot = _wrap(md, footer, ffoot, W - 80) if footer else None
    foot_h = ((foot.count("\n") + 1) * 32 + 34) if foot else 0
    th = (66 if title else 0) + sum(sub_hs) + (10 if subs else 0)
    H = th + sum(r.height for r in rows) + 6 * (len(rows) - 1) + foot_h
    im = Image.new("RGB", (W, H), (255, 255, 255))
    dr = ImageDraw.Draw(im)
    y = 0
    if title:
        _text_center(dr, (0, 0, W, 66), title, _font(FONT_B, 34))
        y = 62
    for s, h in zip(subs, sub_hs):
        _text_center(dr, (0, y, W, y + h), s, fsub, (70, 70, 70))
        y += h
    y = th
    for r in rows:
        im.paste(r, (0, y))
        y += r.height
        if r is not rows[-1]:
            dr.line((20, y + 2, W - 20, y + 2), fill=(215, 215, 215), width=2)
        y += 6
    if foot:
        dr.line((20, y + 4, W - 20, y + 4), fill=(215, 215, 215), width=2)
        y += 18
        for ln in foot.split("\n"):
            dr.text((40, y), ln, font=ffoot, fill=(80, 80, 80))
            y += 32
    return im


def photo_tile(view, crop, size, bg) -> Image.Image:
    return Image.fromarray(crop_resize(view.image, crop, size, bg, Image.LANCZOS))


def photos_footer(shorts: list[str]) -> str:
    """The footer of a photo sheet holding the rows ``shorts`` (in order): the model's materials, and the edge-on arm
    only on a sheet that shows the row it was measured on, named by its place (fix round 2: the shape-differs sheet's
    footer pointed at a 'front row' it does not have)."""
    if EDGE_ON_ROW in shorts:
        return PHOTOS_FOOTER + PHOTOS_FOOTER_EDGE_ON.format(ordinal=ORDINALS[shorts.index(EDGE_ON_ROW) + 1])
    return PHOTOS_FOOTER


def sheets_photos(out: Path, prep: dict, sil: dict, labels: dict) -> dict:
    """``all_angles_photos.png``: the rows that line up (IoU >= IOU_MIN). ``all_angles_photos_shape_differs.png``: the
    rows that do not, each with its plain note (``shape_note``) under it; not on the main sheet (the brief's rule).
    ``labels``: ``option_labels`` (column labels and legend from the measured differences)."""
    job = IN.load_job(IN.FT1119['cameras'], with_astra=False)
    headers = ["your photo"] + [labels["labels"][k] for k, _ in OPTIONS]
    legend = labels["legend"]
    ok_rows, low_rows, files, placement = [], [], {}, {}
    (out / "rows").mkdir(parents=True, exist_ok=True)
    for short, stem, title in PHOTO_ROWS:
        rec = prep["rows"][short]
        view = job.views[rec["photo"]]
        crop, size = rec["crop_native_xywh"], tuple(rec["tile_px"])
        tiles = [photo_tile(view, crop, size, rec["background"]["rgb"])]
        for key, _ in OPTIONS:
            tiles.append(Image.open(capture_dir(out, key, short, rec) / f"photo_{short}__beauty.png").convert("RGB"))
        for t in tiles:
            if t.size != size:
                raise SystemExit(f"row {short}: a tile is {t.size}, not {size}")
        low = min(sil[short][k]["iou"] for k, _ in OPTIONS) < IOU_MIN
        note = sil[short].get("sheet_note") if low else None
        if low and not note:
            raise SystemExit(f"row {short} is under IoU {IOU_MIN} but has no sheet note")
        row_big = compose_row(title, tiles, headers, tile_w=size[0], label_w=250, note=note)
        p = out / "rows" / f"{short}_{stem}.png"
        row_big.save(p)
        files[short] = _rel(p)
        (low_rows if low else ok_rows).append((short, title, tiles, note))
    result = {"rows": files, "headers": headers, "legend": legend, "footers": {}}
    if ok_rows:
        small = [compose_row(t, tl, headers, tile_w=560, label_w=250, header=(j == 0), note=n) for j, (_, t, tl, n) in enumerate(ok_rows)]
        foot = photos_footer([s for s, *_ in ok_rows])
        sheet = stack(small, "Tom Ford glasses: your photos beside the four textures",
                      ["Each picture in a row is seen from the same spot your photo was taken from, with no face, cut the same way.",
                       legend], foot)
        sp = out / "all_angles_photos.png"
        sheet.save(sp)
        result.update({"sheet": _rel(sp), "size": list(sheet.size)})
        result["footers"]["all_angles_photos.png"] = foot
        placement.update({s: "all_angles_photos.png" for s, *_ in ok_rows})
    if low_rows:
        n = len(low_rows)
        small = [compose_row(t, tl, headers, tile_w=560, label_w=250, header=(j == 0), note=nt) for j, (_, t, tl, nt) in enumerate(low_rows)]
        foot = photos_footer([s for s, *_ in low_rows])
        sheet = stack(small, f"Tom Ford glasses: {NUMBER_WORDS.get(n, str(n))} more photo{'s' if n > 1 else ''}, where the model's "
                             "shape does not line up",
                      ["Seen from the same spot as your photo, like the main sheet. Here the model's outline differs from your photo, "
                       "so judge the paint, not the shape; the note under each row says where.", legend], foot)
        sp = out / "all_angles_photos_shape_differs.png"
        sheet.save(sp)
        result.update({"sheet_shape_differs": _rel(sp), "size_shape_differs": list(sheet.size)})
        result["footers"]["all_angles_photos_shape_differs.png"] = foot
        placement.update({s: "all_angles_photos_shape_differs.png" for s, *_ in low_rows})
    result["placement"] = placement
    result["rule"] = (f"a photo row goes on all_angles_photos.png only when its silhouette IoU is at least {IOU_MIN}; the others go on "
                      "all_angles_photos_shape_differs.png with a note under the row, built from the measurement")
    return result


def _axis_toward_camera(asset_to_world) -> np.ndarray:
    """The try-on camera's optical axis toward the camera, in the glasses' frame (R^T (0, 0, 1), scale removed):
    ORIENTATION ONLY, the convention of the first build and of ar_calibrate's trade-off pairs; recorded for comparison,
    never used to pair photos (the glasses sit 11-17 deg off that axis)."""
    M = np.asarray(asset_to_world, float).reshape(4, 4).T
    R = M[:3, :3] / np.cbrt(np.linalg.det(M[:3, :3]))
    d = R.T @ np.array([0.0, 0.0, 1.0])
    return d / np.linalg.norm(d)


def _pose_matrices(report: dict) -> dict:
    """Per try-on pose (and the worn portrait): its asset_to_world matrix."""
    out = {p["id"]: p["asset_to_world"] for p in report.get("poses") or [] if p.get("asset_to_world")}
    por = report.get("portrait") or {}
    if por.get("asset_to_world"):
        out["portrait"] = por["asset_to_world"]
    return out


def _relation_parts(photo: np.ndarray, pose: np.ndarray, near: bool = True) -> list[str]:
    """Plain words for how the photo's viewing direction differs from the pose's (elevation vs side-to-side). ``near``:
    the photo is shown beside the pose, so a height difference is 'a little' higher / lower; for the closest photo of
    an empty cell (over TRYON_PHOTO_MAX_DEG away) it is plain 'higher' / 'lower'."""
    elev = math.degrees(math.asin(np.clip(photo[1], -1, 1))) - math.degrees(math.asin(np.clip(pose[1], -1, 1)))
    azim = math.degrees(math.atan2(photo[0], photo[2])) - math.degrees(math.atan2(pose[0], pose[2]))
    az_photo, az_pose = math.degrees(math.atan2(photo[0], photo[2])), math.degrees(math.atan2(pose[0], pose[2]))
    parts = []
    if abs(elev) >= 5:
        parts.append(("a little " if near else "") + ("higher" if elev > 0 else "lower"))
    if abs(azim) >= 5:
        same_side = abs(az_photo) < 5 or np.sign(az_photo) == np.sign(az_pose)
        if abs(az_pose) >= 10 and same_side and abs(abs(az_photo) - abs(az_pose)) >= 5:
            # a turned pose: the photo is less turned (more from the front) or more turned (more from the side)
            parts.append("more from the front" if abs(az_photo) < abs(az_pose) else "more from the side")
        else:
            parts.append("more from the wearer's left" if azim > 0 else "more from the wearer's right")
    return parts


def _difference_words(photo: np.ndarray, pose: np.ndarray) -> str:
    parts = _relation_parts(photo, pose)
    return "your photo, taken " + " and ".join(parts) if parts else "your photo, about the same angle"


def tryon_empty_text(pose_title: str, pose_dir: np.ndarray, photo_title: str, photo_dir: np.ndarray) -> str:
    """The text of an empty photo cell, in words only (fix round 2: no degrees on the owner sheets): no photo near this
    pose, the closest photo by its row title and how it was taken, and, when the pose's own title has no height word,
    whether the try-on sees the glasses from below here (its straight-on and turned views do, slightly: that is why the
    photo 'from the front, a little above' pairs with 'from a little above' and not with 'straight on')."""
    parts = _relation_parts(photo_dir, pose_dir, near=False)
    lines = ["No photo from near this angle.",
             f"The closest is '{photo_title}'" + (", taken " + " and ".join(parts) if parts else "") + "."]
    el = math.degrees(math.asin(np.clip(pose_dir[1], -1, 1)))
    if not any(w in pose_title for w in HEIGHT_WORDS) and el <= -5.0:
        lines.append("Here the try-on sees the glasses from " + ("a little below." if el > -10.0 else "below."))
    return "\n".join(lines)


def _box_of(mask: np.ndarray):
    ys, xs = np.nonzero(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def tryon_row_notes(direction: np.ndarray) -> dict:
    """What the try-on draws in one pose that could be read as paint, from the pose's viewing direction: a straight view
    from above (elevation >= 10 deg, within 35 deg of the front) shows short arms (the head hides the rest) whose tops
    catch the light. (A far arm cut to a small block shows in several turned rows; the footer names it, since the part-id
    render draws it joined to the frame and cannot single it out.)"""
    side = view_side(direction)
    above = side["elevation_deg"] >= 10.0 and abs(side["azimuth_deg"]) < 35.0
    return {"elevation_deg": side["elevation_deg"], "azimuth_deg": side["azimuth_deg"],
            "note": ("Seen from above, the arms look short because the head hides the rest, and the light on their tops makes "
                     "them look grey.") if above else None}


def sheets_tryon(out: Path, prep: dict, labels: dict) -> dict:
    job = IN.load_job(IN.FT1119['cameras'], with_astra=False)
    front = np.asarray(prep["front_centre_m"], float)
    reports = {k: json.loads((out / "tryon" / k / "report.json").read_text(encoding="utf-8")) for k, _ in OPTIONS}
    mats = _pose_matrices(reports["A"])
    views = {pid: view_toward_tryon(m, front) for pid, m in mats.items()}
    for k in ("astra", "B", "C"):
        for pid, m in _pose_matrices(reports[k]).items():
            if pid in views and angle_deg(views[pid][0], view_toward_tryon(m, front)[0]) > 0.5:
                log(f"note: try-on pose {pid} differs by more than 0.5 deg between A and {k} (fit scale differs)")
    photo_views = {short: view_toward_photo(prep["rows"][short]["pinhole"]["view_row_major_m"], front) for short, _, _ in PHOTO_ROWS}
    photo_toward = {short: raster.camera_basis(Camera.from_dict(prep["rows"][short]["camera"]))[2] for short, _, _ in PHOTO_ROWS}
    _, groups, _, _ = load_pose(job)
    fparts = set(next(g for g in groups if g["name"] == "front")["parts"])
    fpids = [i for i, n in enumerate(job.mesh.names) if n in fparts]
    front_alt = job.mesh.V[np.unique(job.mesh.F[np.isin(job.mesh.part, fpids)])].mean(0) / 1000.0
    views_alt = {pid: view_toward_tryon(m, front_alt)[0] for pid, m in mats.items()}
    photo_alt = {short: view_toward_photo(prep["rows"][short]["pinhole"]["view_row_major_m"], front_alt)[0] for short, _, _ in PHOTO_ROWS}
    photo_title = {short: title for short, _, title in PHOTO_ROWS}
    headers = ["your photo"] + [labels["labels"][k] for k, _ in OPTIONS]
    rows_small, files, rec_rows = [], {}, {}
    (out / "rows_tryon").mkdir(parents=True, exist_ok=True)
    for i, (pid, title) in enumerate(TRYON_ROWS):
        if pid not in views:
            raise SystemExit(f"try-on pose {pid}: no asset_to_world in A's report")
        vdir, vdist = views[pid]
        if pid != "portrait":
            check_title_height(title, view_side(vdir)["elevation_deg"])
        angles = {s: angle_deg(vdir, pv[0]) for s, pv in photo_views.items()}
        orient = {s: angle_deg(_axis_toward_camera(mats[pid]), t) for s, t in photo_toward.items()}
        best = min(angles, key=angles.get)
        use = best if angles[best] <= TRYON_PHOTO_MAX_DEG else None
        angles_alt = {s: angle_deg(views_alt[pid], d) for s, d in photo_alt.items()}
        best_alt = min(angles_alt, key=angles_alt.get)
        use_alt = best_alt if angles_alt[best_alt] <= TRYON_PHOTO_MAX_DEG else None
        if use_alt != use:
            log(f"WARNING try-on pose {pid}: the photo shown depends on the front point ({use} vs {use_alt} with the vertex mean)")
        if pid == "portrait":
            files_k = {k: out / "tryon" / k / "portrait__a__eyes.png" for k, _ in OPTIONS}
            crops = {k: next((im.get("crop") for im in reports[k]["images"] if im["file"] == "portrait__a__eyes.png"), None) for k, _ in OPTIONS}
            if len({json.dumps(c, sort_keys=True) for c in crops.values()}) != 1:
                raise SystemExit(f"the portrait eye crops differ between the options: {crops}")
            tiles = [Image.open(f).convert("RGB") for f in files_k.values()]
            crop_rec = {"source": "portrait__a__eyes.png (the capture's own eye crop, identical for the four)", "crop": crops["A"]}
            row_notes = {"note": None, "rule": "the portrait has no part-id render; no row note"}
        else:
            boxes = [_box_of(silhouette(out / "tryon" / k / f"{pid}__part-id.png")) for k, _ in OPTIONS]
            full = Image.open(out / "tryon" / "A" / f"{pid}__skin__beauty.png")
            m = 24
            box = (max(0, min(b[0] for b in boxes) - m), max(0, min(b[1] for b in boxes) - m),
                   min(full.width, max(b[2] for b in boxes) + m), min(full.height, max(b[3] for b in boxes) + m))
            tiles = [Image.open(out / "tryon" / k / f"{pid}__skin__beauty.png").convert("RGB").crop(box) for k, _ in OPTIONS]
            crop_rec = {"source": f"{pid}__skin__beauty.png", "box_xyxy": list(box)}
            row_notes = tryon_row_notes(vdir)
        caption, empty = None, None
        if use is not None:
            prec = prep["rows"][use]
            view = job.views[prec["photo"]]
            ys, xs = np.nonzero(view.matte)
            bw, bh = xs.max() + 1 - xs.min(), ys.max() + 1 - ys.min()
            mm = int(0.08 * max(bw, bh))
            pcrop = (int(xs.min()) - mm, int(ys.min()) - mm, int(bw) + 2 * mm, int(bh) + 2 * mm)
            ptile = photo_tile(view, pcrop, (pcrop[2], pcrop[3]), prec["background"]["rgb"])
            words = _difference_words(photo_views[use][0], vdir)
            if photo_views[use][1] >= FAR_AWAY_RATIO * vdist:
                words += "\nfrom much farther away than the try-on camera"
            caption = [words] + [None] * len(OPTIONS)
        else:
            ptile = None
            empty = [tryon_empty_text(title, vdir, photo_title[best], photo_views[best][0])] + [None] * len(OPTIONS)
        all_tiles = [ptile] + tiles
        tw = tiles[0].width
        row_big = compose_row(title, all_tiles, headers, tile_w=tw, label_w=260, captions=caption, empty_texts=empty, note=row_notes.get("note"))
        p = out / "rows_tryon" / f"{i + 1:02d}_{pid}.png"
        row_big.save(p)
        files[pid] = _rel(p)
        rows_small.append((title, all_tiles, caption, empty, row_notes.get("note")))
        rec_rows[pid] = {"title": title, "crop": crop_rec, "photo": (f"reference-{use}.png" if use else None),
                         "photo_angle_deg": round(angles[use], 1) if use else None,
                         "nearest_photo": f"reference-{best}.png", "nearest_photo_angle_deg": round(angles[best], 1),
                         "angles_to_photos_deg": {f"reference-{s}.png": round(a, 1) for s, a in angles.items()},
                         "orientation_only_angles_deg": {f"reference-{s}.png": round(a, 1) for s, a in orient.items()},
                         "angles_to_photos_deg_vertex_mean_front": {f"reference-{s}.png": round(a, 1) for s, a in angles_alt.items()},
                         "same_photo_with_vertex_mean_front": use_alt == use,
                         "tryon_view": {**{k: v for k, v in view_side(vdir).items() if k != "near_role"}, "camera_distance_m": round(vdist, 3)},
                         "photo_camera_distance_m": round(photo_views[use][1], 3) if use else None,
                         "caption": caption[0] if caption else None, "empty_text": empty[0] if empty else None,
                         "row_notes": row_notes}
    half = (len(rows_small) + 1) // 2
    pages = [rows_small[:half], rows_small[half:]]
    small = [[compose_row(t, tl, headers, tile_w=500, label_w=260, header=(j == 0), captions=c, empty_texts=e, note=n)
              for j, (t, tl, c, e, n) in enumerate(pg)] for pg in pages]
    title = "Tom Ford glasses in the try-on: the four textures at every try-on angle"
    sub = ["As the try-on shows them, on the skin background. Your photo is shown only where one was taken from about the "
           "same direction; otherwise the box names the closest photo and how it was taken.", labels["legend"]]
    p1, p2 = out / "all_angles_tryon.png", out / "all_angles_tryon_2.png"
    s1 = stack(small[0], title + " (first page)", sub, TRYON_FOOTER)
    s2 = stack(small[1], title + " (second page)", sub, TRYON_FOOTER)
    s1.save(p1)
    s2.save(p2)
    return {"sheets": [_rel(p1), _rel(p2)], "sizes": [list(s1.size), list(s2.size)], "rows": files, "row_details": rec_rows,
            "headers": headers, "subtitles": sub,
            "photo_rule": (f"a photo is shown only when it was taken within {TRYON_PHOTO_MAX_DEG:.0f} deg of the pose: the angle between "
                           "the VIEWING directions from the glasses' front (prepare.front_centre_m) to the try-on camera's centre and to "
                           "the photo camera's centre, in the glasses' frame (fix round 1; the first build used the optical axes, "
                           "orientation only, kept as orientation_only_angles_deg); otherwise the cell says how far the nearest photo is"),
            "footer": TRYON_FOOTER, "front_alt_m": [float(x) for x in front_alt],
            "front_alt_rule": "the front group's fit-mesh vertex mean: the pairing is decided again from it and must not change"}


# ----------------------------------------------------------------------------- byte identity
def byte_identity(out: Path) -> dict:
    """The try-on capture of A on the changed arview against the two captures made with the same command BEFORE the
    --cameras mode existed (``byte_identity/before1``, ``before2``)."""
    def imgs(d):
        r = json.loads((d / "report.json").read_text(encoding="utf-8"))
        return {i["file"]: i["sha256"] for i in r["images"]}, r
    b1, r1 = imgs(out / "byte_identity" / "before1")
    b2, _ = imgs(out / "byte_identity" / "before2")
    a, ra = imgs(out / "tryon" / "A")
    return {"command": "node bin/arview.mjs capture <A> --poses all --backgrounds skin --out <dir>",
            "before": [_rel(out / "byte_identity" / "before1"), _rel(out / "byte_identity" / "before2")],
            "after": _rel(out / "tryon" / "A"), "images": len(a),
            "before_runs_identical": b1 == b2, "after_identical_to_before": a == b1,
            "differing_files": sorted(k for k in set(a) | set(b1) if a.get(k) != b1.get(k)),
            "arview_commit_before": r1["tool"].get("arview_commit"), "arview_dirty_before": r1["tool"].get("arview_dirty"),
            "arview_dirty_after": ra["tool"].get("arview_dirty")}


FIX_ROUND_1 = [
    {"finding": "try-on rows 'straight on' / 'turned, wearer's left arm near' showed photos 05 / 01 that are ~30 deg away (major)",
     "resolution": "photos are paired by VIEWING direction from the glasses' front to each camera centre (view_toward_tryon / "
                   "view_toward_photo); those cells are now empty and say how far the nearest photo is; subtitle rewritten; the "
                   "orientation-only angles stay in row_details for comparison"},
    {"finding": "photo rows 03 / 06 under IoU 0.8 shipped on the owner sheet with no mark (major)",
     "resolution": "the main photo sheet carries only rows at or above 0.8; rows 03 / 06 are on all_angles_photos_shape_differs.png "
                   "with a plain note under each, built from the measurement (mismatch_parts, tip_plates, refinement check)"},
    {"finding": "row 01 used the registration's refined camera + hinges, not the bake's WP3b camera (minor)",
     "resolution": "--camera-source bake (default): every row uses the WP3b camera the bake painted with (sha256 checked against "
                   "bake.json) and the temples at rest; row 01 IoU 0.865 -> 0.869; 'registration' keeps the old choice"},
    {"finding": "B / C GLB asset notes say 'step-4 print' (minor)",
     "resolution": "read-only inputs: recorded per option as asset_note / asset_note_stale; the cause is ar_calibrate.evaluate "
                   "copying the source label through swap_image"},
    {"finding": "medallion only on +x, silver/cream materials, second arm in row 03 (minor)",
     "resolution": "row 03's sheet note names the far arm and the plain tip; both sheets' footers name the model's materials"},
    {"finding": "try-on effects read as paint: glare, silver inner brow, cut far arm block, head shadow, close camera, from-above "
                "stubs (minor)",
     "resolution": "try-on footer names each; the straight 'from above' row has its own note; captions say the photo was taken "
                   "from much farther away"},
    {"finding": "column 'A' undescribed (minor)", "resolution": "'A (new paint)' plus a legend line on every sheet"},
    {"finding": "pose_glb mutates a shared mesh / reads appended accessors (minor)",
     "resolution": "a moved node with a shared mesh gets its own mesh copy; rotations read only original accessors (reused per "
                   "accessor and group); transformed nodes are refused; three new tests"},
    {"finding": "camera-mode report said templeEndState 'fixed cap' and verticalFovDegrees 63 (minor)",
     "resolution": "renderer.ts: templeEndStateText says 'authored (explicit camera ...)' and cameraState reports the explicit "
                   "projection's field of view; face path text unchanged (100/100 images byte-identical); unit test asserts both"},
    {"finding": "summary notes compared different cameras and typed 'Where:' strings by hand (minor)",
     "resolution": "camera_check rasters the fit mesh at the SAME camera (IoU 0.984-0.993); notes are built from measurements only"},
    {"finding": "capture exit 3 accepted silently (minor)", "resolution": "logged as WARNING and listed in summary.capture_warnings"},
    {"finding": "no tests for node_groups / side_check (minor)", "resolution": "tests added (14 in test_all_angles.py)"},
    {"finding": "ragged pale blotches on the brow tops of A / B / C (minor; paint)",
     "resolution": "NOT fixed: it is in the production print (read-only here); see problems_left"},
    {"finding": "photo tiles are small on the sheet; photo tiles 05 / 06 / 01 are upscaled (minor)",
     "resolution": "unchanged: the row images (rows/, 1280 px tiles) are the close-up; the photos' own resolution limits their tiles"},
]

FIX_ROUND_2 = [
    {"finding": "column 'C (more colourful)' cannot be told apart from A (major)",
     "resolution": "the column labels and the legend are built from the measured differences on every photo row and try-on pose "
                   "(option_differences / option_labels; summary.option_columns, option_labels_why, option_differences_per_option): an "
                   "option not visibly different from A is labelled '(almost the same as A)' and the legend says what little changes; "
                   "the option itself (C = the brief's all_pairs_chroma candidate) is unchanged"},
    {"finding": "the shape-differs sheet's footer points at a 'front row' it does not have (minor, two verifiers)",
     "resolution": "photos_footer: the edge-on sentence only on a sheet showing photo 05, naming that row by its place"},
    {"finding": "'no capture warnings' while all 8 reports say captured_with_warnings (minor)",
     "resolution": "capture_warnings reads every report.json: status and each warning with its source and whether the capture made "
                   "before this work had it; summary.capture_warnings_text says it in one line"},
    {"finding": "row 03's note calls the model's tip plate 'gold' (minor)", "resolution": "'the metal plate'"},
    {"finding": "production print blotches on the brow tops (minor; paint)", "resolution": "NOT fixed: read-only input; problems_left"},
    {"finding": "B / C embedded asset notes stale (minor)", "resolution": "NOT fixed: read-only inputs; recorded as before"},
    {"finding": "numbers in owner-facing text (degrees, mm) (minor)",
     "resolution": "empty try-on cells name the closest photo and how it was taken in words (tryon_empty_text); the subtitle says "
                   "'about the same direction'; the shape notes say 'slightly off' / 'clearly off'"},
    {"finding": "photo 05 'from the front' pairs with the try-on's 'from above', not 'straight on' (minor)",
     "resolution": "photo 05 is 'from the front, a little above' (01 and 06 likewise, the try-on's pitch +20 rows 'from a little above'); "
                   "every title's height words are checked against the measured view (check_title_height); the straight-on cell says "
                   "the try-on sees the glasses from a little below there"},
    {"finding": "shape_note / summary text fixed whatever the refinement found; a capped local search reported as global (minor)",
     "resolution": "shape_note has three openings by the refinement result; refinement_sentence reports the search (rounds, "
                   "converged or capped, parameters at a bound) and claims only a local result; the search now restarts Powell until "
                   "a round converges with no gain (REFINE_SEARCH); a diagnostic search beyond the pose model's bounds "
                   "(refine_check(wide=True), silhouettes.<row>.wide_search_check, wide_sentence) says how much of the gap a looser "
                   "camera could hide"},
    {"finding": "nothing enforces the try-on byte-identity verdict (minor)",
     "resolution": "byte_identity_problems: logged as WARNING and listed in summary.problems; summary.byte_identity_ok"},
    {"finding": "unmapped GLB / palette names silently counted as 'front' (minor)",
     "resolution": "node_groups and part_roles raise on a name with no hinge group"},
]


def capture_warnings_text(cw: list[dict], n_captures: int) -> str:
    """One line on the captures' warnings (``capture_warnings``)."""
    if not cw:
        return "every capture came back clean (status 'captured', no warnings, exit 0)"
    n_w = sum(len(c["warnings"]) for c in cw)
    new_w = sum(1 for c in cw for w in c["warnings"] if not w["also_before_this_work"])
    statuses = sorted({c["status"] for c in cw})
    text = (f"{len(cw)} of {n_captures} captures came back with status {' / '.join(statuses)}: {n_w} warnings, from "
            + "; ".join(sorted({w['source'] for c in cw for w in c['warnings']})) + "; "
            + ("every one was also in the capture made before this work" if not new_w
               else f"{new_w} were NOT in the capture made before this work"))
    bad = [c for c in cw if c["exit"] != 0]
    return text + ("; every exit was 0" if not bad else f"; {len(bad)} exited with a code other than 0")


def refinement_sentence(chk: dict, short: str) -> str:
    """The summary's sentence on a row's refinement check, from its result (fix round 2: the first version said 'no
    admissible camera ... brings the silhouette to 0.8' whatever the result, and claimed a global result from a local
    search that had stopped at its evaluation cap)."""
    x = chk["iou_best_refined_full_res"]
    how = (f"Powell from this camera, {chk['evaluations']} evaluations in {len(chk.get('rounds') or [1])} round(s), "
           + ("converged" if chk.get("converged") else "stopped at its evaluation cap without converging"))
    bound = chk.get("at_or_near_a_bound") or []
    if bound:
        how += f"; at or near a bound: {', '.join(bound)} ({chk.get('near_bound_rule', '')})"
    where = f"overlays/{short}_A.png: blue = model only, red = photo only"
    if x < IOU_MIN:
        return (f"UNDER {IOU_MIN}: a bounded local search of the camera and both temple angles ({chk['bounds']}; {how}) reaches "
                f"{x:.3f}, still under {IOU_MIN}. It found no nearby camera or temple opening that lines the model up with this photo "
                "(a local search, not a proof that none exists); with the camera check above, the gap is the model's shape against "
                f"this photo, not the comparison ({where}).")
    return (f"UNDER {IOU_MIN} at this camera, but a bounded local search of the camera and both temple angles ({chk['bounds']}; "
            f"{how}) reaches {x:.3f}, at or above {IOU_MIN}: part of the gap is the camera, not only the model's shape ({where}).")


def wide_sentence(wchk: dict) -> str:
    """The summary's sentence on the diagnostic search beyond the pose model's bounds (``refine_check(wide=True)``)."""
    x = wchk["iou_best_refined_full_res"]
    big = {n: v for n, v in wchk["refined_minus_fitted"].items() if abs(v) >= 3.0}
    return (f"Diagnostic only, beyond the pose model's bounds ({wchk['bounds']}; {wchk['evaluations']} evaluations, "
            + ("converged" if wchk.get("converged") else "stopped at its evaluation cap") + f"): {x:.3f}, "
            + ("still under" if x < IOU_MIN else "at or above") + f" {IOU_MIN}"
            + (", by moving " + ", ".join(f"{n} {v:+.1f}" for n, v in big.items()) if big else "")
            + (f"; at or near a bound: {', '.join(wchk['at_or_near_a_bound'])}" if wchk.get("at_or_near_a_bound") else "")
            + ". Values outside the admissible ranges describe no real camera or temple state; this only bounds how much of the "
              "gap a looser camera could hide.")


def summary(out: Path, state: dict) -> dict:
    """Per-row notes (numbers; the owner-facing note for a row under IOU_MIN is ``silhouettes[row].sheet_note``), the
    IoU verdicts, capture warnings and an index of every file written. Every sentence is built from the measurement."""
    wp3b = json.loads(WP3B_CAMERAS.read_text(encoding="utf-8"))
    sp = state["sheet_photos"]
    rows = {}
    for short, stem, title in PHOTO_ROWS:
        rec, sil = state["prepare"]["rows"][short], state["silhouettes"][short]
        ious = {k: sil[k]["iou"] for k, _ in OPTIONS}
        cc = sil["camera_check"]
        note = [f"IoU {min(ious.values()):.3f} against the photo's matte (all four options share one geometry, so one silhouette).",
                f"arview draws the camera it was given: its silhouette against a raster of the cameras' fit mesh at the same camera and "
                f"temples scores IoU {cc['iou_arview_vs_raster']:.3f} ({cc['raster_only_px']} raster-only px, {cc['arview_only_px']} "
                "arview-only px)."]
        bake_iou = wp3b["images"][rec["photo"]]["iou"]
        if rec["camera_equals_bake_wp3b"] and not rec["hinges_deg"]:
            note.append(f"The camera is the bake's own WP3b camera with the temples at rest, as the bake painted; the bake's fit of that "
                        f"camera scored {bake_iou:.3f} against the photo (raster, WP3b cameras.json).")
        else:
            note.append(f"The camera is NOT the bake's WP3b camera ({rec['camera_source']}"
                        + (", temples posed " + ", ".join(f"{g} {a:+.4f} deg" for g, a in rec["hinges_deg"].items()) if rec["hinges_deg"] else "")
                        + f"); the WP3b camera's own score {bake_iou:.3f} belongs to a different camera.")
        note.append(f"Boundary mean {sil['A']['boundary']['mean_mm']} mm, p90 {sil['A']['boundary']['p90_mm']} mm; centroid offset "
                    f"{sil['A']['centroid_offset_native_px']} native px; left/right {sil['A']['left_right']['verdict']}.")
        mis = sil["A"]["mismatch"]["roles"]
        note.append("Mismatch by part (share of model-only / photo-only px; render edge to photo edge): " + "; ".join(
            f"{n} {v['model_only_share']:.0%} / {v['photo_only_share']:.0%}, {v['edge_to_photo_mean_mm']} mm" for n, v in mis.items()) + ".")
        chk = sil.get("refinement_check")
        if chk:
            note.append(refinement_sentence(chk, short))
        wchk = sil.get("wide_search_check")
        if chk and wchk:
            note.append(wide_sentence(wchk))
        note.append(f"Sheet: {sp['placement'][short]}" + (f", with the note: \"{sil['sheet_note']}\"" if sil.get("sheet_note") else "") + ".")
        rows[short] = {"photo": rec["photo"], "title": title, "iou": ious, "iou_ok": min(ious.values()) >= IOU_MIN,
                       "left_right_ok": all(sil[k]["left_right"]["verdict"] == "ok" for k, _ in OPTIONS),
                       "camera_check_iou": cc["iou_arview_vs_raster"], "sheet": sp["placement"][short],
                       "sheet_note": sil.get("sheet_note"), "note": " ".join(note)}
    files = {"sheet_photos": sp.get("sheet"), "sheet_photos_shape_differs": sp.get("sheet_shape_differs"), "rows_photos": sp["rows"],
             "sheets_tryon": state["sheet_tryon"]["sheets"], "rows_tryon": state["sheet_tryon"]["rows"],
             "cameras": state["prepare"]["cameras_files"],
             "posed_glbs": {s: {k: v["path"] for k, v in r["posed_glbs"].items()} for s, r in state["prepare"]["rows"].items() if "posed_glbs" in r},
             "overlays": sorted(_rel(p_) for p_ in (out / "overlays").glob("*.png")),
             "captures": sorted(c["out"] for c in state["captures"]),
             "byte_identity": state["byte_identity"]["before"] + [state["byte_identity"]["after"]]}
    stale = {k: v["asset_note_stale"] for k, v in state["prepare"]["inputs"]["options"].items() if v.get("asset_note_stale")}
    labels = option_labels(state["option_differences"])
    bi_problems = byte_identity_problems(state["byte_identity"])
    cw = capture_warnings(state, before=out / "byte_identity" / "before1")
    new_w = [w["text"] for c in cw for w in c["warnings"] if not w["also_before_this_work"]]
    problems = [f"byte identity: {p}" for p in bi_problems] + [f"capture {c['out']}: exit {c['exit']}" for c in cw if c["exit"] != 0]
    problems += [f"capture warning not seen before this work: {t}" for t in new_w]
    return {"rows": rows, "rows_under_iou_min": [s for s, r in rows.items() if not r["iou_ok"]], "iou_min": IOU_MIN,
            "camera_source": state["prepare"]["cameras"]["source"], "capture_warnings": cw,
            "capture_warnings_text": capture_warnings_text(cw, len(state["captures"])),
            "byte_identity_ok": not bi_problems, "problems": problems,
            "asset_notes_stale": stale, "files": files, "option_columns": labels["labels"],
            "option_columns_brief_names": {k: lab for k, lab in OPTIONS}, "options_legend": labels["legend"],
            "option_labels_why": labels["why"], "option_differences_per_option": state["option_differences"]["per_option"],
            "photo_row_order": [f"reference-{s}.png" for s, _, _ in PHOTO_ROWS],
            "tryon_photo_pairing_robust_to_front_point": all(r.get("same_photo_with_vertex_mean_front", True)
                                                             for r in state["sheet_tryon"]["row_details"].values()),
            "fix_round_1": FIX_ROUND_1, "fix_round_2": FIX_ROUND_2}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--stage", default="all", choices=("all", "prepare", "capture", "measure", "sheets", "summary"))
    ap.add_argument("--camera-source", default="bake", choices=CAMERA_SOURCES,
                    help="bake: the WP3b cameras the bake painted with, temples at rest (default); registration: step 0's "
                         "registered pose and hinge angles")
    a = ap.parse_args(argv)
    if a.workers is not None and a.workers < 1:
        ap.error("--workers must be positive")
    opt_out_ecoqos(None)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    state_p = out / "all_angles.json"
    state = json.loads(state_p.read_text(encoding="utf-8")) if state_p.is_file() and a.stage != "all" else {}
    t0 = time.time()
    if a.stage in ("all", "prepare"):
        state["prepare"] = prepare(out, a.camera_source)
        state_p.write_text(json.dumps(state, indent=1), encoding="utf-8")
    if a.stage in ("all", "capture"):
        state["captures"] = capture_all(out, state["prepare"], a.workers)
        state_p.write_text(json.dumps(state, indent=1), encoding="utf-8")
    if a.stage in ("all", "measure"):
        state["silhouettes"] = measure(out, state["prepare"], state.get("silhouettes"), workers=a.workers)
        state["option_differences"] = option_differences(out, state["prepare"])
        log("option differences against A (max share of frame px over 6/255, max mean dE76): " + ", ".join(
            f"{k} {v['max_share_over_levels']:.3f} / {v['max_mean_dE76']:.2f} ({'visible' if v['visible'] else 'NOT visible'})"
            for k, v in state["option_differences"]["per_option"].items()))
        state["byte_identity"] = byte_identity(out)
        for p in byte_identity_problems(state["byte_identity"]):
            log(f"WARNING byte identity: {p}")
        state_p.write_text(json.dumps(state, indent=1), encoding="utf-8")
    if a.stage in ("all", "sheets"):
        labels = option_labels(state["option_differences"])
        state["sheet_photos"] = sheets_photos(out, state["prepare"], state["silhouettes"], labels)
        state["sheet_tryon"] = sheets_tryon(out, state["prepare"], labels)
        state_p.write_text(json.dumps(state, indent=1), encoding="utf-8")
    if a.stage in ("all", "summary"):
        state["summary"] = summary(out, state)
        state_p.write_text(json.dumps(state, indent=1), encoding="utf-8")
    log(f"done in {time.time() - t0:.0f} s -> {state_p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
