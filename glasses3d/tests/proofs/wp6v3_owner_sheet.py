"""Owner review material for the WP6 v3 rehearsal (WP6_V3 §7 item 4). Every output is a TOOL TEST and every image says
so (LABEL): the v3 rehearsal is a blind stand-in model playing Astra with the v3 tools on a copy of package_007, $0;
nothing here is Astra input or a deliverable.

Models, each sha-checked and opened as a byte copy in its own worker job on the WP6 v2 pose basis (wp6v2_owner_sheet's
open_job; the result sheet's path, wp6v2_result_sheet):
- ORIGINAL, PREFERRED (wp6v2_owner_sheet.MODELS);
- RUN 7: paid run facet-astra-007's best checkpoint (lab/package_007 c0007, revision 7);
- V3 REHEARSAL: the stand-in's finish (review_20261012/v3/standin/pkg c0013, revision 15: its top triangle U = A, P, F,
  B one warp face);
- the top triangle's two other readings, FOLD (U1 = A P B and U2 = P F B, flat, a line P-B) and FLAT (U one plane):
  each one facet_edit from revision 15 through the lab (Lab._edit: geometry.apply_edits -> fit_spec with the stored
  readings -> build -> temple_lab_build -> checkpoint) on a copy of the stand-in package (<out>/lab_copy), the same
  readings and declarations (flat: the fit refuses it with them, E_FIT_SHAPE on the B-E ridge, recorded; the built
  flat leaves that one crease to the fit).

Rendering: the job's rig studio_dome_v1 (the owner's choice "c", 2026-10-11). The photo pages (reference-0N.png,
black-*.png) use the existing sheet path: compare_to_photos with one crop per photo (the union of the models' temple
crops, cells <= 640 px), the original's frozen gains and photo stretch shared by every model. Every other image renders
the same way at its own box (the worker's render at the photo's crop camera and temple pose; the compare's gain,
backdrop and stretch arithmetic, as seeing's zoom). Each also gets a CLAY row (the worker's clay pass: one matte grey,
roughness 0.55, under the same rig), since no light reproduces the photos' facet tones (FINDINGS #4): clay shows the
faces by shape. Creases on a photo are the build's own model edges (build.model_edges, the lab's; run 7's from its
revision 7 rebuilt), drawn by seeing's compare overlay and zoom with their built angles.

Run from the repo root, in order (each step reads what the earlier wrote under OUT):
    glasses3d/.venv/Scripts/python.exe -m glasses3d.tests.proofs.wp6v3_owner_sheet options
        copy the stand-in package, build fold and flat -> options.json (receipts, fit lines, checkpoints)
    ... wp6v3_owner_sheet render      compares and renders -> render/, render.json
    ... wp6v3_owner_sheet sheets      the PNGs
    ... wp6v3_owner_sheet export      the v3 head's GLB (the finish's lab export) -> viewer/v3_rehearsal.glb
    ... wp6v3_owner_sheet numbers     the checklist numbers -> numbers.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from glasses3d.agent.seeing import Seeing
from glasses3d.g3d.temple import build as BLD
from glasses3d.g3d.temple import build_temple
from glasses3d.g3d_host import blender_camera as BC
from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host import pose as PO
from glasses3d.g3d_host.camera import project_mm
from glasses3d.tests.proofs import wp6v2_owner_sheet as OS

G3D = Path(__file__).resolve().parents[2]
DATA = G3D / "data"
W6 = DATA / "wp6v2"
OUT = W6 / "result_v3_rehearsal"
STANDIN = W6 / "review_20261012" / "v3" / "standin" / "pkg"
PKG7 = W6 / "lab" / "package_007"
LABCOPY = OUT / "lab_copy"
VIEWER = W6 / "viewer"
RIG = "studio_dome_v1"
LABEL = "v3 rehearsal: a stand-in model playing Astra with the v3 tools, $0"
HEAD_REV = 15                   # the stand-in's finish: c0013, revision 15 (STANDIN_REPORT.md)
RUN7_REV = 7                    # run 7's best: package_007 c0007, revision 7
V3_GLB = ("head-2f58b7c780bdafed.glb", "93f9abfffe9031d2b7e5980a8dc26413197ba236bdce3218b3f9dfdecff91954")
MODELS = {
    "original": OS.MODELS["original"],
    "run7": ("ASTRA RUN 7", PKG7 / "checkpoints" / "c0007.blend",
             "3b4435b27313854367aec10c389087c9cad3b36d3d9311b03c1d3aaeed5924e1"),
    "v3": ("V3 REHEARSAL", STANDIN / "checkpoints" / "c0013.blend",
           "f5bfe2fbe0487a0f6af3bca777d841cbdd83ddabb956a843dad5e725e1304181"),
    "preferred": ("PREFERRED (10-10)",) + OS.MODELS["preferred"][1:],
}
PAGE_MODELS = ("original", "run7", "v3", "preferred")
PHOTOS = ("reference-01", "reference-02", "reference-03", "reference-04", "reference-05", "reference-06",
          "black-side", "black-angled", "black-front")
PID = {"black-side": "B1", "black-angled": "B2", "black-front": "B3"}
# the boxes (native px) of the sheets that are not photo pages; k = their render scale
FACES = ("black-side", (200, 0, 1480, 445), 1.0)            # faces_run7.png's view of B1
ANGLED = ("black-angled", (180, 390, 1750, 920), 0.7)       # angled_run7.png's view of B2
ANGLED_ZOOM = ("black-angled", (1290, 450, 1740, 840))      # B2's charted temple block (labelled creases)
TOP_ZOOMS = (("black-side", (280, 50, 1000, 230)),          # the top triangle U and the logo's top edge
             ("black-angled", (1290, 470, 1730, 700)))
# face numbers: (number, face, {corner: weight} of the point marked, colour); the logo's mark sits left of its lettering
FACE_MARKS = (("1", "L", {"A": 0.65, "B": 0.10, "C": 0.25}, (35, 144, 220)),
              ("2", "RU", {"B": 1 / 3, "F": 1 / 3, "E": 1 / 3}, (232, 128, 20)),
              ("3", "RL", {"B": 1 / 3, "E": 1 / 3, "C": 1 / 3}, (40, 160, 70)),
              ("4", "U", {"A": 0.25, "P": 0.25, "F": 0.25, "B": 0.25}, (140, 60, 200)))
FACE_NAMES = {"1": "logo triangle", "2": "upper-back triangle", "3": "lower-back triangle", "4": "top triangle"}

# the top triangle's other two readings (WP6_V3 §2: the owner sheet shows warp, fold and flat), from revision 15
OPTION_OBJECT = ("TOOL TEST by the coding agent on a copy (not Astra's, not the stand-in's): revision 15's layout and "
                 "readings with the top band U built as {what}. Faces L (A B C), LL (A C H H0), RL (B E R70..R30 C), "
                 "RU (B F E) as revision 15; edges L|U and RU|U declared ridges as revision 15.")
OPTIONS = {
    "fold": {"title": "(b) FOLD: two flat faces, a line from the peak P to the logo corner B",
             "note": "tool test: U as two flat faces U1 = A P B, U2 = P F B (a line P-B), the same readings",
             "object": OPTION_OBJECT.format(what="a fold: two flat faces U1 = A P B and U2 = P F B with a line P-B "
                                                 "(its angle left to the fit)"),
             "edits": [{"op": "delete_facet", "label": "U"},
                       {"op": "set_facet", "label": "U1", "poly": ["A", "P", "B"], "plane": {"mode": "flat"},
                        "soft": False},
                       {"op": "set_facet", "label": "U2", "poly": ["P", "F", "B"], "plane": {"mode": "flat"},
                        "soft": False},
                       {"op": "set_crease", "facets": ["L", "U1"], "shape": "ridge"},
                       {"op": "set_crease", "facets": ["RU", "U2"], "shape": "ridge"}]},
    # with revision 15's declarations the fit refuses a flat U (E_FIT_SHAPE: the declared RL|RU ridge B-E then costs
    # chi2 +4.7, readings-only -5.1 +- 3.4 deg); the refusal is recorded and "flat" leaves that one crease to the fit
    "flat_declared": {"title": "FLAT with revision 15's declarations",
                      "note": "tool test: U one flat face A P F B, the same readings and declarations",
                      "object": OPTION_OBJECT.format(what="one flat face A P F B (the fit moves its corners within "
                                                          "their errors to make them one plane)"),
                      "edits": [{"op": "set_facet", "label": "U", "poly": ["A", "P", "F", "B"],
                                 "plane": {"mode": "flat"}, "soft": False}]},
    "flat": {"title": "(c) FLAT: one plane, the fit spreading the misfit",
             "note": "tool test: U one flat face A P F B, the same readings; RL|RU (B-E) left to the fit, since the "
                     "fit refuses its declared ridge with a flat U (E_FIT_SHAPE)",
             "object": OPTION_OBJECT.format(what="one flat face A P F B (the fit moves its corners within their "
                                                 "errors to make them one plane); the RL|RU edge B-E is not declared "
                                                 "(with a flat U the fit refuses its ridge, E_FIT_SHAPE)"),
             "edits": [{"op": "set_facet", "label": "U", "poly": ["A", "P", "F", "B"], "plane": {"mode": "flat"},
                        "soft": False},
                       {"op": "delete_crease", "facets": ["RL", "RU"]}]},
}
BUILT_OPTIONS = ("fold", "flat")
AS_BUILT = "(a) AS BUILT: one warp face, its lower edge bending at the logo corner B"
F_BOLD = "arialbd.ttf"
F_REG = "arial.ttf"


def font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype(F_BOLD if bold else F_REG, size)
    except OSError:
        return C._font(size)


def jdump(path: Path, doc) -> None:
    C.write_json(path, doc)


def jload(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha_check(models: dict) -> None:
    for name, (_l, path, want) in models.items():
        got = OS.sha(path)
        if got != want:
            raise RuntimeError(f"{name}: {path} sha {got[:12]} != {want[:12]}")


# ------------------------------------------------------------------------------------------------- options
def copy_package() -> None:
    """A byte copy of the stand-in package at LABCOPY, its JSON path strings repointed (as the stand-in's drive.py
    repointed package_007's): the stand-in's own folder is evidence and never written."""
    if LABCOPY.exists():
        shutil.rmtree(LABCOPY, onerror=OS._rw)
    shutil.copytree(STANDIN, LABCOPY)
    old = [str(STANDIN), str(STANDIN).replace("\\", "\\\\"), str(STANDIN).replace("\\", "/")]
    new = [str(LABCOPY), str(LABCOPY).replace("\\", "\\\\"), str(LABCOPY).replace("\\", "/")]
    n = 0
    for p in LABCOPY.rglob("*.json*"):
        t = p.read_text(encoding="utf-8")
        u = t
        for a, b in zip(old[::-1], new[::-1]):
            u = u.replace(a, b)
        if u != t:
            n += 1
            p.write_text(u, encoding="utf-8")
    (LABCOPY / "TOOL_TEST.txt").write_text(
        "TOOL TEST copy of the v3 blind stand-in's package (review_20261012/v3/standin/pkg), 2026-10-13: the top "
        "triangle's fold and flat readings built from revision 15 through the lab for the owner sheet "
        f"(tests/proofs/wp6v3_owner_sheet.py). Never Astra input, never a deliverable. {n} JSON files repointed.\n",
        encoding="utf-8")


def crease_record(tb, fit) -> list:
    """The build's creases (tables) with the fit's angle and sd where it has the pair."""
    fits = {tuple(c["pair"]): c for c in (fit.creases if fit is not None else []) if c.get("deg") is not None}
    out = []
    for c in tb.tables["creases"]:
        if c["kind"] != "crease":
            continue
        f = fits.get(tuple(c["regions"]))
        out.append({"pair": "|".join(c["regions"]), "shape": c["shape"], "deg": round(c["mean_dihedral_deg"], 2),
                    "range": [round(v, 2) for v in c["range_deg"]], "declared": c.get("declared"),
                    "fit": None if f is None else {"deg": round(f["deg"], 2), "sd": round(f["sd_deg"], 2),
                                                   "certain": f.get("certain")}})
    return out


def sharp_share(tb) -> dict:
    """{pair: [sharp edges, edges]} of every facet|facet crease of a build (build.crease_edges against tb.sharp)."""
    sharp = {tuple(sorted(e)) for e in tb.sharp}
    out: dict = {}
    for a, b, pair, _deg, _ridge, p1, p2 in BLD.crease_edges(tb):
        if "base" in pair or not BLD.face_edge(tb, p1, p2):
            continue
        key = "|".join(pair)
        rec = out.setdefault(key, [0, 0])
        rec[0] += int(tuple(sorted((a, b))) in sharp)
        rec[1] += 1
    return out


def build_facts(tb, fit, spec: dict) -> dict:
    """What the checklist reads off a build: creases, sharp shares, fades, the border ramps, each facet's corners
    with (d, y, X), the fit's face lines and warnings."""
    nodes = {n: v for n, v in tb.tables["nodes"].items()}
    facets = {}
    for lab, f in spec["facets"].items():
        facets[lab] = {"poly": list(f["poly"]), "plane": f["plane"] if isinstance(f["plane"], str) else "coef",
                       "fade": f.get("fade"), "soft": bool(f.get("soft")),
                       "corners": {n: [round(float(nodes[n]["d"]), 3), round(float(nodes[n]["y"]), 3),
                                       round(float(nodes[n]["X"]), 3)] for n in f["poly"] if n in nodes}}
    out = {"creases": crease_record(tb, fit), "sharp": sharp_share(tb), "facets": facets,
           "border_ramps": tb.tables.get("border_ramps"), "warnings": [str(w) for w in tb.warnings]}
    if fit is not None:
        out["fit_lines"] = fit.report_lines(limit=40)
        out["fit_faces"] = fit.faces
    return out


def options() -> dict:
    """Build fold and flat from revision HEAD_REV on a copy of the stand-in package (module doc)."""
    from glasses3d.agent.temple_lab import lab as LB
    sha_check({"v3": MODELS["v3"]})
    OUT.mkdir(parents=True, exist_ok=True)
    copy_package()
    lab = LB.Lab(LABCOPY)
    lab.start()
    res = {"label": LABEL, "created": C._utc(), "package": str(LABCOPY), "parent_revision": HEAD_REV, "options": {}}
    try:
        res["head"] = {"checkpoint": lab.s.head["id"], "fingerprint": lab.s.head["fingerprint"]}
        tb = lab.build(HEAD_REV)
        res["as_built"] = {"revision": HEAD_REV, "checkpoint": "c0013", "blend": str(MODELS["v3"][1]),
                           "crease_lines": LB.crease_lines(tb, lab.fit_result(HEAD_REV), lab.raw_edges(HEAD_REV)),
                           "model_edges": lab.model_edges(HEAD_REV), "hinge": lab.spec(HEAD_REV)["hinge"],
                           "facts": build_facts(tb, lab.fit_result(HEAD_REV), lab.spec(HEAD_REV))}
        for name, o in OPTIONS.items():
            t0 = time.perf_counter()
            r = lab._edit(f"owner-{name}", HEAD_REV, False, o["edits"], o["note"], o["object"], True, ["B1"])
            rec = {k: v for k, v in r.items() if k not in ("images",)}
            rec["seconds"] = round(time.perf_counter() - t0, 1)
            if r.get("status") == "applied":
                rid = r["revision"]
                ck = r["checkpoint"]
                blend = LABCOPY / "checkpoints" / f"{ck}.blend"
                rec.update(blend=str(blend), sha256=OS.sha(blend),
                           crease_lines=LB.crease_lines(lab.build(rid), lab.fit_result(rid), lab.raw_edges(rid)),
                           model_edges=lab.model_edges(rid), hinge=lab.spec(rid)["hinge"],
                           facts=build_facts(lab.build(rid), lab.fit_result(rid), lab.spec(rid)))
            res["options"][name] = rec
            print(name, r.get("status"), r.get("revision"), r.get("checkpoint"), r.get("code"), r.get("message"),
                  flush=True)
    finally:
        lab.close()
    jdump(OUT / "options.json", res)
    return res


# ------------------------------------------------------------------------------------------------- render
def open_jobs(models: dict, photos: dict) -> dict:
    OS.OUT = OUT / "render"
    OS.OUT.mkdir(parents=True, exist_ok=True)
    jobs = {}
    for name, (_l, path, _w) in models.items():
        jobs[name] = OS.open_job(name, path, photos)
    return jobs


def head_mesh(see) -> tuple:
    bs = C.load_basis(see.root)
    exp = see.export_head()
    mesh = see.fit_mesh(exp["path"], exp["sha256"], skip=bs.skip, frame=bs.frame)
    return bs, mesh, PO.part_roles(exp["path"])


def crop_cam(bs, rid: str, box, k: float):
    """The crop camera and shape of a native box at k (seeing._matched_body's arithmetic)."""
    x0, y0, x1, y1 = box
    cam = BC.crop_camera(bs.camera(rid), x0, y0, k)
    return cam, (max(1, round((y1 - y0) * k)), max(1, round((x1 - x0) * k)))


def render_views(see, tag: str, items: list, gains: dict) -> dict:
    """The worker's render of ``items`` [(name, rid, box, k, passes)] at each photo's crop camera and temple pose
    under RIG with the gains' light frame (seeing._matched_body's view), into the job's work/renders/<call>:
    {name: {pass: file}}."""
    bs = C.load_basis(see.root)
    cid = see.s.new_call_id("owner_sheet_render", None)
    out_dir = see._prepare_work_dir(see.work / "renders" / cid)
    origin = np.asarray(see.bounds(cid)["origin_mm"] or (0.0, 0.0, 0.0), float)
    views = []
    for name, rid, box, k, passes in items:
        cam, shape = crop_cam(bs, rid, box, k)
        views.append({"name": name, "camera": BC.to_blender(cam, bs.frame, origin, shape), "passes": list(passes),
                      "pose": bs.pose(rid)})
    rr = see.s.call_read("owner_sheet_render", "render", {
        "out_dir": str(out_dir), "views": views, **see._render_params(RIG, None, gains.get("light_frame"))},
        deadline_s=900.0, call_id=cid)
    if rr["status"] != "ok":
        raise RuntimeError(f"{tag}: render failed: {rr.get('message')}")
    return {v["name"]: {p: f["path"] for p, f in v["files"].items()} for v in rr["result"]["views"]}


def shown_rows(bs, rid: str, box, k: float, files: dict, gains: dict, stretch) -> dict:
    """The rows one render shows, as seeing's zoom makes them (_zoom_photo): photo (the box on the crop camera's grid,
    the compare's stretch), render (native_linear for a target photo, gloss_black for another colour, the job's
    gains and the photo's backdrop), clay (RGBA: clay_rows stretches a row alike and puts it on white) and geometry
    (the normal pass, light-free) when rendered."""
    cam, shape = crop_cam(bs, rid, box, k)
    photo, matte = bs.photo(rid), bs.matte(rid)
    lo, hi = (float(v) for v in stretch)
    bg = C.photo_background(photo, matte)
    out = {"photo": Image.fromarray(C.apply_stretch(C.resample_box(photo, box, k, shape), lo, hi))}
    cw = bs.colourway(rid)
    if "native_linear" in files:
        gain = gains["colourways"]["target"]["native_linear"]["gain"]
        nat = C.read_linear(files["native_linear"])
        out["render"] = Image.fromarray(C.apply_stretch(C.composite_render(nat[0], nat[1], gain, bg), lo, hi))
    if "gloss_black" in files:
        gl_rec = gains["gloss_black"]
        gcw = gains["colourways"].get(cw) or gains["colourways"].get("target")
        rlo, rhi = (lo, hi) if gcw.get("dark") else tuple(gl_rec["stretch"])
        gl = C.read_linear(files["gloss_black"])
        out["render"] = Image.fromarray(C.apply_stretch(C.composite_render(gl[0], gl[1], gl_rec["gain"], bg), rlo,
                                                        rhi))
    if "clay" in files:
        out["clay"] = Image.open(files["clay"]).convert("RGBA")       # shown with clay_rows' shared stretch
    if "normal" in files:
        nrm = np.asarray(Image.open(files["normal"]).convert("RGBA"))
        out["geometry"] = Image.fromarray(C.geometry_view(nrm))
    return out


def save_rows(rows: dict, stem: Path) -> dict:
    stem.parent.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, im in rows.items():
        p = stem.parent / f"{stem.name}_{name}.png"
        (im if im.mode == "RGBA" else im.convert("RGB")).save(p)
        out[name] = str(p)
    return out


def face_marks(bs, rid: str, box, k: float, built: dict) -> list:
    """Where faces 1-4 sit in a box: each face's FACE_MARKS point (a weighted mean of the v3 rehearsal's built
    corners, chart d, y, X: build_facts' facets) posed at the photo (pose.chart_to_posed, side +1, the spec's
    hinge) and projected by the photo's camera: [(number, u, v)] in the box's image px at k."""
    nodes = {n: xyz for f in built["facts"]["facets"].values() for n, xyz in f["corners"].items()}
    hinge = built["hinge"]
    opening = (float(hinge["opening_deg"]), (abs(float(hinge["axis_xz_mm"][0])), float(hinge["axis_xz_mm"][1]))) \
        if hinge.get("opening_deg") else None
    out = []
    for num, _face, weights, _rgb in FACE_MARKS:
        ch = sum(w * np.array([float(v) for v in nodes[n]]) for n, w in weights.items())
        P = PO.chart_to_posed([ch], 1, float(hinge["z_mm"]), bs.pose(rid), opening)
        u, v = project_mm(P, bs.camera(rid), bs.frame)[0]
        out.append((num, (u - box[0] + 0.5) * k - 0.5, (v - box[1] + 0.5) * k - 0.5))
    return out


def run7_edges() -> dict:
    """Run 7's model edges as the lab draws them (build.model_edges of its revision 7 rebuilt by the current builder,
    lab.capped_edges) and its hinge: the creases of the c0007 head."""
    from glasses3d.agent.temple_lab import lab as LB
    spec = jload(PKG7 / "work" / "temple_lab" / "revisions" / f"r{RUN7_REV:04d}.json")
    return {"model_edges": LB.capped_edges(BLD.model_edges(build_temple(spec))), "hinge": spec["hinge"]}


def edges_arg(opt: dict) -> dict:
    return {"model_edges": opt["model_edges"], "opening": opt["hinge"], "hinge_z_mm": float(opt["hinge"]["z_mm"])}


def render() -> dict:
    """Every compare and render the sheets use (module doc) -> OUT/render.json."""
    t0 = time.perf_counter()
    opts = jload(OUT / "options.json")
    models = dict(MODELS)
    for name in BUILT_OPTIONS:
        o = opts["options"][name]
        if o.get("status") != "applied":
            raise RuntimeError(f"option {name} was not built: {o.get('code')} {o.get('message')}")
        models[name] = (name.upper(), Path(o["blend"]), o["sha256"])
    sha_check(models)
    doc = jload(OS.BASIS_JSON)
    photos = {n: Path(r["image"]) for n, r in doc["images"].items()}
    jobs = open_jobs(models, photos)
    out = {"label": LABEL, "created": C._utc(), "rig": RIG, "inputs": {n: {"label": l, "path": str(p), "sha256": w}
                                                                       for n, (l, p, w) in models.items()},
           "basis": {"path": str(OS.BASIS_JSON), "sha256": OS.sha(OS.BASIS_JSON)}}
    try:
        # 1. photo pages: one crop per photo (the union of the page models' temple crops), the original's gains
        boxes = {}
        heads = {}
        for name, (_s, see) in jobs.items():
            heads[name] = head_mesh(see)
            if name not in PAGE_MODELS:
                continue
            bs, mesh, roles = heads[name]
            for rid in PHOTOS:
                g = Seeing._matched_geometry(bs, rid, mesh, roles)
                boxes.setdefault(rid, []).append(C.crop_box(g["temple"], bs.matte(rid)))
        crops = {}
        for rid, bl in boxes.items():
            box = OS.union_box(bl)
            crops[rid] = {"box": list(box), "k": min(1.0, OS.CELL_MAX_PX / max(box[2] - box[0], box[3] - box[1]))}
        r = jobs["original"][1].compare_to_photos(list(PHOTOS), passes=("native_linear",), rig=RIG, boxes=crops)
        assert r["status"] == "ok", r
        reports = {"original": jload(r["report"])}
        gains = reports["original"]["gains"]
        for rid in PHOTOS:
            crops[rid]["stretch"] = reports["original"]["photos"][rid]["stretch"]
        for name in PAGE_MODELS[1:]:
            r = jobs[name][1].compare_to_photos(list(PHOTOS), passes=("native_linear",), rig=RIG, boxes=crops,
                                                gains=gains)
            assert r["status"] == "ok", r
            reports[name] = jload(r["report"])
        out["crops"] = crops
        out["gains"] = gains
        out["pages"] = {rid: {name: {"rows": reports[name]["photos"][rid]["rows"],
                                     "temple_iou": reports[name]["photos"][rid]["temple_iou"],
                                     "outline_offsets_mm": reports[name]["photos"][rid].get("outline_offsets_mm"),
                                     "outer_edge_offsets_mm": reports[name]["photos"][rid].get("outer_edge_offsets_mm"),
                                     "view_kind": reports[name]["photos"][rid]["view_kind"]}
                              for name in PAGE_MODELS} for rid in PHOTOS}
        # 2. clay at the page crops; the faces / angled boxes; the top-triangle zooms
        rows: dict = {}
        for name in models:
            items = []
            if name in PAGE_MODELS:
                items += [(f"page-{rid}", rid, crops[rid]["box"], crops[rid]["k"], ["clay"]) for rid in PHOTOS]
            if name in ("run7", "v3"):
                for tag, (rid, box, k) in (("faces", FACES), ("angled", ANGLED)):
                    items.append((tag, rid, box, k, ["gloss_black", "clay"]))
            if name in ("v3",) + BUILT_OPTIONS:
                for rid, box in TOP_ZOOMS:
                    k = C.zoom_scale(box[2] - box[0], box[3] - box[1])
                    items.append((f"top-{rid}", rid, box, k, ["gloss_black", "clay", "normal"]))
            files = render_views(jobs[name][1], name, items, gains)
            bs = heads[name][0]
            for vname, rid, box, k, _p in items:
                stretch = crops[rid]["stretch"]
                rws = shown_rows(bs, rid, box, k, files[vname], gains, stretch)
                rows.setdefault(name, {})[vname] = {"rid": rid, "box": list(box), "k": k,
                                                    "files": save_rows(rws, OUT / "render" / "rows" / name / vname)}
        out["rows"] = rows
        # 3. the build's creases on the photo (seeing's zoom with the model edges, labelled with their angles; and
        # the compare's overlay row at the faces and angled boxes): run 7's from its revision 7 rebuilt (run7_edges)
        creases: dict = {}
        edge_src = {"run7": run7_edges(), "v3": opts["as_built"],
                    **{n: opts["options"][n] for n in BUILT_OPTIONS}}
        for name, src in edge_src.items():
            zoom = [] if name == "run7" else [{"rid": rid, "box": list(box), "geometry": False}
                                              for rid, box in TOP_ZOOMS]
            if name in ("run7", "v3"):
                zoom.append({"rid": ANGLED_ZOOM[0], "box": list(ANGLED_ZOOM[1]), "geometry": False})
            r = jobs[name][1].compare_to_photos([], passes=("native_linear",), rig=RIG, boxes=crops, gains=gains,
                                                zoom=zoom, photo_names=PID, **edges_arg(src))
            assert r["status"] == "ok", r
            creases[name] = [{"rid": z["rid"], "box": q["box"], "panels": z["panels"], "text": z["text"]}
                             for z, q in zip(r["zoom"], zoom)]
            if name in ("run7", "v3"):
                bx = {rid: {"box": list(box), "k": k, "stretch": crops[rid]["stretch"]}
                      for rid, box, k in (FACES, ANGLED)}
                r = jobs[name][1].compare_to_photos(list(bx), passes=("native_linear",), rig=RIG, boxes=bx,
                                                    gains=gains, photo_names=PID, **edges_arg(src))
                assert r["status"] == "ok", r
                creases[f"{name}_overlay"] = {rid: r["photos"][rid]["rows"]["overlay"] for rid in bx}
        out["creases"] = creases
        # 4. face numbers at B1 (the v3 rehearsal's fitted corners)
        rid, box, k = FACES
        out["face_marks"] = face_marks(heads["v3"][0], rid, box, k, opts["as_built"])
        for name, (s, _see) in jobs.items():
            assert s.fingerprint() == s.head["fingerprint"], f"{name}: the renders moved the scene"
    finally:
        for s, _see in jobs.values():
            s.close()
    out["seconds"] = round(time.perf_counter() - t0, 1)
    jdump(OUT / "render.json", out)
    return out


# ------------------------------------------------------------------------------------------------- sheets
INK, MUTED, BANNER_BG = (12, 12, 12), (70, 70, 70), (150, 20, 20)
CLAY_PCT = (1.0, 99.0)          # the clay rows' shared stretch: these luma percentiles of the row's model pixels


def text_w(text: str, f) -> int:
    return int(ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(text, font=f))


def wrap(text: str, f, width: int) -> list:
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if text_w(t, f) <= width or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    return lines + ([cur] if cur else [])


def banner(width: int, size: int = 22) -> Image.Image:
    """The tool-test label every image carries (LABEL), white on dark red, wrapped to ``width``."""
    f = font(size, True)
    step = int(size * 1.35)
    lines = wrap(LABEL, f, width - 24)
    im = Image.new("RGB", (width, 14 + step * len(lines)), BANNER_BG)
    dr = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        dr.text((12, 8 + step * i), ln, fill=(255, 255, 255), font=f)
    return im


def titled(lines: list, im: Image.Image | None, width: int | None = None, size: int = 22) -> Image.Image:
    """``im`` (or nothing) under its title lines: the first bold at ``size`` px, the rest regular at size - 3, each
    wrapped to the width."""
    width = width or im.width
    rows = []
    for i, t in enumerate(lines):
        f = font(size, True) if i == 0 else font(size - 3)
        rows += [(ln, f, INK if i == 0 else MUTED) for ln in wrap(t, f, width - 16)]
    step = int(size * 1.3)
    h = 6 + step * len(rows)
    ih = im.height + 10 if im is not None else 4
    out = Image.new("RGB", (max(width, im.width if im is not None else 0), h + ih), (255, 255, 255))
    dr = ImageDraw.Draw(out)
    for i, (ln, f, col) in enumerate(rows):
        dr.text((8, 4 + step * i), ln, fill=col, font=f)
    if im is not None:
        out.paste(im.convert("RGB"), (0, h))
    return out


def vstack(parts: list, gap: int = 6) -> Image.Image:
    W = max(p.width for p in parts)
    out = Image.new("RGB", (W, sum(p.height for p in parts) + gap * (len(parts) - 1)), (255, 255, 255))
    y = 0
    for p in parts:
        out.paste(p.convert("RGB"), (0, y))
        y += p.height + gap
    return out


def hstack(parts: list, gap: int = 8) -> Image.Image:
    H = max(p.height for p in parts)
    out = Image.new("RGB", (sum(p.width for p in parts) + gap * (len(parts) - 1), H), (255, 255, 255))
    x = 0
    for p in parts:
        out.paste(p.convert("RGB"), (x, 0))
        x += p.width + gap
    return out


def marked(im: Image.Image, marks: list, r: int = 15) -> Image.Image:
    """Numbered discs (FACE_MARKS' colours) at [(number, u, v)]."""
    im = im.convert("RGB").copy()
    dr = ImageDraw.Draw(im)
    f = font(20, True)
    cols = {num: rgb for num, _f, _w, rgb in FACE_MARKS}
    for num, u, v in marks:
        dr.ellipse((u - r - 2, v - r - 2, u + r + 2, v + r + 2), fill=(255, 255, 255))
        dr.ellipse((u - r, v - r, u + r, v + r), fill=cols[num])
        dr.text((u - text_w(num, f) / 2, v - 12), num, fill=(255, 255, 255), font=f)
    return im


def legend_marks() -> Image.Image:
    f = font(19)
    items = [(num, FACE_NAMES[num]) for num, *_ in FACE_MARKS]
    im = Image.new("RGB", (sum(36 + text_w(t, f) + 18 for _n, t in items) + 8, 34), (255, 255, 255))
    x = 8
    for num, t in items:
        im.paste(marked(Image.new("RGB", (30, 30), (255, 255, 255)), [(num, 15, 15)], r=12), (x, 2))
        x += 34
        ImageDraw.Draw(im).text((x, 6), t, fill=INK, font=f)
        x += text_w(t, f) + 18
    return im


def img(path) -> Image.Image:
    return Image.open(path).convert("RGB")


def clay_rows(paths: list) -> list:
    """Clay renders (RGBA) shown alike: one luma stretch for the whole row (CLAY_PCT of the pooled model pixels, the
    rule compare.apply_stretch applies to photos and renders), composited on white, so the faces' small differences
    in grey show and every model of the row gets the same curve."""
    ims = [np.asarray(Image.open(p).convert("RGBA")) for p in paths]
    lum = np.concatenate([(a[..., :3].astype(np.float64) @ C.LUMA)[a[..., 3] > 127] for a in ims])
    lo, hi = (float(v) for v in np.percentile(lum, CLAY_PCT))
    out = []
    for a in ims:
        rgb = C.apply_stretch(a[..., :3], lo, hi).astype(np.float64)
        al = a[..., 3:4].astype(np.float64) / 255.0
        out.append(Image.fromarray(np.clip(rgb * al + 255.0 * (1 - al), 0, 255).astype(np.uint8)))
    return out


CLAY_NOTE = "clay: one matte grey under the same light, the faces by their shape without reflections; contrast " \
            "stretched alike for every model in the sheet"


EDGE_KEY = ("orange ridge = outward, blue valley = inward, grey = nearly flat; magenta = the model's outline; green "
            "grid = d every 10 mm, y every 5 mm")


def faces_sheet(R: dict) -> Image.Image:
    rows = R["rows"]
    marks = [tuple(m) for m in R["face_marks"]]
    rid = FACES[0]
    photo = img(rows["v3"]["faces"]["files"]["photo"])
    W = photo.width
    c7, cv = clay_rows([rows[n]["faces"]["files"]["clay"] for n in ("run7", "v3")])
    blocks = [titled(["REAL GLASSES (black photo B1)"], vstack([legend_marks(), marked(photo, marks)], 2), W),
              titled(["ASTRA RUN 7 (gloss black render, same camera)"],
                     marked(img(rows["run7"]["faces"]["files"]["render"]), marks), W),
              titled(["V3 REHEARSAL (gloss black render, same camera)"],
                     marked(img(rows["v3"]["faces"]["files"]["render"]), marks), W),
              titled(["ASTRA RUN 7: clay", CLAY_NOTE], marked(c7, marks), W),
              titled(["V3 REHEARSAL: clay"], marked(cv, marks), W),
              titled(["ASTRA RUN 7's creases on the photo", EDGE_KEY], img(R["creases"]["run7_overlay"][rid]), W),
              titled(["V3 REHEARSAL's creases on the photo"], img(R["creases"]["v3_overlay"][rid]), W),
              titled(["The numbers sit at the same pixels in every panel: points inside the v3 rehearsal's faces L, "
                      "RU, RL and U, projected by B1's camera."], None, W, 19)]
    return vstack([banner(W)] + blocks)


def angled_sheet(R: dict) -> Image.Image:
    rows = R["rows"]
    rid = ANGLED[0]
    photo = img(rows["v3"]["angled"]["files"]["photo"])
    W = photo.width
    zooms = {n: next(z for z in R["creases"][n] if z["rid"] == ANGLED_ZOOM[0] and tuple(z["box"]) == ANGLED_ZOOM[1])
             for n in ("run7", "v3")}
    c7, cv = clay_rows([rows[n]["angled"]["files"]["clay"] for n in ("run7", "v3")])
    trio = hstack([titled(["photo"], img(zooms["v3"]["panels"]["photo"]), None, 19),
                   titled(["Astra run 7's creases"], img(zooms["run7"]["panels"]["overlay"]), None, 19),
                   titled(["the v3 rehearsal's creases"], img(zooms["v3"]["panels"]["overlay"]), None, 19)])
    blocks = [titled(["REAL GLASSES (black angled photo B2)"], photo, W),
              titled(["ASTRA RUN 7 (gloss black render, same camera)"], img(rows["run7"]["angled"]["files"]["render"]),
                     W),
              titled(["V3 REHEARSAL (gloss black render, same camera)"], img(rows["v3"]["angled"]["files"]["render"]),
                     W),
              titled(["ASTRA RUN 7: clay", CLAY_NOTE], c7, W),
              titled(["V3 REHEARSAL: clay"], cv, W),
              titled(["ASTRA RUN 7's creases on the photo", EDGE_KEY], img(R["creases"]["run7_overlay"][rid]), W),
              titled(["V3 REHEARSAL's creases on the photo"], img(R["creases"]["v3_overlay"][rid]), W),
              titled(["Close-up of the charted temple, larger than native scale: each crease labelled with its built "
                      "angle (degrees; - = valley; a-b = its range along the edge)"], trio, W)]
    return vstack([banner(max(trio.width, W))] + blocks)


def cell(im: Image.Image, label: str, width: int) -> Image.Image:
    return titled([label], im, width, 20)


def options_sheet(R: dict, O: dict) -> Image.Image:
    rows = R["rows"]
    cols = [("v3", AS_BUILT), ("fold", OPTIONS["fold"]["title"]), ("flat", OPTIONS["flat"]["title"])]
    crease = {name: {(z["rid"], tuple(z["box"])): z["panels"]["overlay"] for z in R["creases"][name]}
              for name, _t in cols}
    blocks = []
    for rid, box in TOP_ZOOMS:
        tag = f"top-{rid}"
        ph = img(rows["v3"][tag]["files"]["photo"])
        w = ph.width
        k = rows["v3"][tag]["k"]
        clays = clay_rows([rows[name][tag]["files"]["clay"] for name, _t in cols])
        grid = []
        for what, key in (("gloss black render", "render"), ("clay (stretched alike)", "clay"),
                          ("its creases on the photo, angle in degrees", None),
                          ("geometry: surface direction as colour, no light", "geometry")):
            line = [cell(ph, f"PHOTO {PID[rid]}", w)]
            for i, (name, title) in enumerate(cols):
                if key is None:
                    im = img(crease[name][(rid, tuple(box))])
                elif key == "clay":
                    im = clays[i]
                else:
                    im = img(rows[name][tag]["files"][key])
                line.append(cell(im, f"{title.split(':')[0]}: {what}", w))
            grid.append(hstack(line))
        blocks.append(titled([f"{PID[rid]} at {k:.2f} x native scale (the renders are made at this scale)"],
                             vstack(grid), None, 26))
    body = vstack(blocks, 24)
    W = body.width
    o = O["options"]
    fc = {c["pair"]: c for c in o["fold"]["facts"]["creases"]}
    lc = {c["pair"]: c for c in o["flat"]["facts"]["creases"]}
    ac = {c["pair"]: c for c in O["as_built"]["facts"]["creases"]}
    bx = o["flat"]["facts"]["facets"]["L"]["corners"]["B"][2] - O["as_built"]["facts"]["facets"]["L"]["corners"]["B"][2]
    misfit = next((ln.split(": ", 1)[1] for ln in o["flat"]["facts"]["fit_lines"] if ln.startswith("misfit beyond")),
                  "")
    head = titled([
        "The top triangle (face 4: the v3 rehearsal's face U = A, P, F, B) built three ways from the same readings",
        f"{AS_BUILT}. L|U ridge {ac['L|U']['deg']:.1f} deg, RU|U ridge {ac['RU|U']['deg']:.1f} deg; no line inside "
        "the face. This is the stand-in's delivered head.",
        f"{OPTIONS['fold']['title']}. The line P-B is a ridge of {fc['U1|U2']['deg']:.1f} deg; L|U1 "
        f"{fc['L|U1']['deg']:.1f}, RU|U2 {fc['RU|U2']['deg']:.1f}; every other face as built.",
        f"{OPTIONS['flat']['title']}. With the stand-in's declarations the fit refuses it (E_FIT_SHAPE: the declared "
        "B-E ridge would cost chi2 +4.7), so B-E is left to the fit. To make A, P, F, B one plane the fit moves the "
        f"logo corner B off its reading ({misfit}); built, B sits {abs(bx):.2f} mm "
        f"{'further in' if bx < 0 else 'further out'} than in (a). Then L|U {lc['L|U']['deg']:.1f}, RU|U "
        f"{lc['RU|U']['deg']:.1f}, the logo's back diagonal L|RL {lc['L|RL']['deg']:.1f} (as built "
        f"{ac['L|RL']['deg']:.1f}), B-E a {lc['RL|RU']['deg']:.1f} deg {lc['RL|RU']['shape']} (as built a "
        f"{ac['RL|RU']['deg']:.1f} deg ridge).",
        "(b) and (c) are the coding agent's builds through the lab on a copy of the stand-in's package (the same "
        "readings, P's typed 3.5 mm thickness): tool tests, not the stand-in's or Astra's choice. The photo and every "
        "render use the same camera, the job's light and the photo's contrast."], None, W, 30)
    return vstack([banner(W, 30), head, body])


def page_sheet(R: dict, rid: str) -> Image.Image:
    pg = R["pages"][rid]
    pid = PID.get(rid, rid[-2:])
    key = "native_linear" if rid.startswith("reference-") else "gloss_black"
    rowname = "native" if key == "native_linear" else "gloss black"
    labels = {"original": "ORIGINAL", "run7": "ASTRA RUN 7", "v3": "V3 REHEARSAL", "preferred": "PREFERRED (10-10)"}
    clays = clay_rows([R["rows"][name][f"page-{rid}"]["files"]["clay"] for name in PAGE_MODELS])
    cells = []
    photo = Image.open(pg["original"]["rows"]["photo"])
    for row in (rowname, "clay"):
        cells.append(OS.label_cell(photo, f"PHOTO {pid}"))
        for i, name in enumerate(PAGE_MODELS):
            im = Image.open(pg[name]["rows"][key]) if row != "clay" else clays[i]
            cells.append(OS.label_cell(im, f"{labels[name]}: {row}"))
    g = C.grid(cells, len(PAGE_MODELS) + 1, max_px=5200, gap=8).convert("RGB")
    title = [f"Photo {pid} ({pg['original']['view_kind']}): photo | original | Astra run 7 | v3 rehearsal | preferred, "
             "the same camera and temple pose, light C (studio_dome_v1)",
             f"Row 1: {rowname} render, matched to the photo's brightness and contrast. Row 2: {CLAY_NOTE}."]
    return vstack([banner(g.width, 26), titled(title, None, g.width, 26), g])


def sheets() -> dict:
    R = jload(OUT / "render.json")
    O = jload(OUT / "options.json")
    made = {}
    for name, fn in (("faces_v3.png", lambda: faces_sheet(R)), ("angled_v3.png", lambda: angled_sheet(R)),
                     ("top_triangle_options.png", lambda: options_sheet(R, O))):
        im = fn()
        im.save(OUT / name)
        made[name] = list(im.size)
    for rid in PHOTOS:
        im = page_sheet(R, rid)
        p = OUT / f"{rid}.png"
        im.save(p)
        made[p.name] = list(im.size)
    print(json.dumps(made, indent=1))
    return {"made": made}


# ------------------------------------------------------------------------------------------------- export
VIEWER_ENTRY = "  {key: 'v3rehearsal', label: 'v3 rehearsal (stand-in tool test)', file: 'v3_rehearsal.glb'},\n"


def export() -> dict:
    """The v3 rehearsal's head GLB: the lab's own export of c0013 (facet_finish's, head-<fingerprint>.glb under the
    stand-in package's work/exports; sha-checked and its fingerprint matched to c0013's) copied to the 3D viewer, which
    lists it first and opens on it (the other models stay)."""
    src = STANDIN / "work" / "exports" / V3_GLB[0]
    if OS.sha(src) != V3_GLB[1]:
        raise RuntimeError(f"{src}: sha changed")
    rows = [json.loads(ln) for ln in (STANDIN / "checkpoints" / "index.jsonl").read_text(encoding="utf-8").splitlines()
            if ln.strip()]
    fp = next(r["fingerprint"] for r in rows if r["id"] == "c0013")
    if not V3_GLB[0].startswith(f"head-{fp[:16]}"):
        raise RuntimeError(f"{V3_GLB[0]} is not c0013's export (fingerprint {fp[:16]})")
    dst = VIEWER / "v3_rehearsal.glb"
    shutil.copyfile(src, dst)
    if OS.sha(dst) != V3_GLB[1]:
        raise RuntimeError(f"{dst}: copy differs")
    page = VIEWER / "index.html"
    t = page.read_text(encoding="utf-8")
    if "v3_rehearsal.glb" not in t:
        t = t.replace("const MODELS = [\n", "const MODELS = [\n" + VIEWER_ENTRY, 1)
    t = t.replace("currentKey = 'run7'", "currentKey = 'v3rehearsal'").replace("show('run7')", "show('v3rehearsal')")
    if "v3_rehearsal.glb" not in t or "show('v3rehearsal')" not in t:
        raise RuntimeError("viewer index.html: the model list or the default was not where expected")
    page.write_text(t, encoding="utf-8")
    return {"glb": str(dst), "sha256": V3_GLB[1], "checkpoint": "c0013", "fingerprint": fp}


# ------------------------------------------------------------------------------------------------- numbers
def run7_facts() -> dict:
    """Run 7's revision 7 (a v2 spec: no fit file) built by the current builder (every v2 spec builds bit-identically,
    WP6_V3 §2), with its creases, sharp shares, fades and ramps."""
    spec = jload(PKG7 / "work" / "temple_lab" / "revisions" / f"r{RUN7_REV:04d}.json")
    tb = build_temple(spec)
    return build_facts(tb, None, spec)


def top_tip(facts: dict, labels) -> dict:
    """The top triangle's tip: the corner of the given facets furthest back (largest d)."""
    best = None
    for lab in labels:
        for n, (d, y, X) in facts["facets"][lab]["corners"].items():
            if best is None or d > best["d"]:
                best = {"node": n, "d": d, "y": y, "X": X}
    return best


def outline_rows(R: dict, name: str) -> dict:
    """The side views' outline offsets (photo minus model, mm) of one model at 01, 05, B1: per photo and temple the
    stations with a number, top, top_silhouette (in a view from above, the top face's far edge: seeing's
    top_far_edge, here as measured, not less its median) and bottom, and the largest |offset| among them."""
    out = {}
    for rid in ("reference-01", "reference-05", "black-side"):
        rec = (R["pages"][rid][name] or {}).get("outline_offsets_mm") or {}
        for side, o in rec.items():
            for edge in ("top", "top_silhouette", "bottom"):
                st = [(s["d_mm"], s["offset_mm"]) for s in (o or {}).get(edge) or [] if s.get("offset_mm") is not None]
                if st:
                    out[f"{PID.get(rid, rid[-2:])} {side} {edge}"] = {
                        "stations": st, "max_abs": max(abs(v) for _d, v in st),
                        "at": max(st, key=lambda s: abs(s[1]))[0]}
    return out


def numbers() -> dict:
    O = jload(OUT / "options.json")
    R = jload(OUT / "render.json")
    r7 = run7_facts()
    doc = {"label": LABEL, "created": C._utc(),
           "run7": {"facts": r7, "tip": top_tip(r7, ["crown"]), "outline": outline_rows(R, "run7")},
           "v3": {"facts": O["as_built"]["facts"], "crease_lines": O["as_built"]["crease_lines"],
                  "tip": top_tip(O["as_built"]["facts"], ["U"]), "outline": outline_rows(R, "v3")},
           "fold": {"facts": O["options"]["fold"]["facts"], "crease_lines": O["options"]["fold"]["crease_lines"],
                    "tip": top_tip(O["options"]["fold"]["facts"], ["U1", "U2"])},
           "flat": {"facts": O["options"]["flat"]["facts"], "crease_lines": O["options"]["flat"]["crease_lines"],
                    "tip": top_tip(O["options"]["flat"]["facts"], ["U"]),
                    "refused_with_declarations": {k: O["options"]["flat_declared"].get(k)
                                                  for k in ("code", "message")}},
           "temple_iou": {rid: {n: round(R["pages"][rid][n]["temple_iou"], 3) for n in PAGE_MODELS} for rid in PHOTOS}}
    jdump(OUT / "numbers.json", doc)
    for name in ("run7", "v3", "fold", "flat"):
        d = doc[name]
        print(f"== {name}: tip {d['tip']}")
        for c in d["facts"]["creases"]:
            print(f"   {c['pair']:10s} {c['shape']:6s} {c['deg']:6.2f} {c['range']} declared {c['declared']} fit "
                  f"{c['fit']}")
        print("   sharp", d["facts"]["sharp"])
        print("   fades", {k: v["fade"] for k, v in d["facts"]["facets"].items() if v["fade"]})
        print("   ramps", d["facts"]["border_ramps"])
        for k, v in (d.get("outline") or {}).items():
            print(f"   outline {k}: max |{v['max_abs']:.2f}| at d {v['at']}: {v['stations']}")
    return doc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=("options", "render", "sheets", "export", "numbers"))
    a = ap.parse_args()
    fn = {"options": options, "render": render, "sheets": sheets, "export": export, "numbers": numbers}[a.step]
    res = fn()
    print(json.dumps({"step": a.step, "seconds": res.get("seconds")}, indent=1))
