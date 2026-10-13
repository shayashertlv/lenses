"""Owner review material for the WP6 v3 rehearsal (WP6_V3 §7 item 4). Every output is a TOOL TEST and every image says
so (LABEL): the v3 rehearsal is a blind stand-in model playing Astra with the v3 tools on a copy of package_007, $0;
nothing here is Astra input or a deliverable.

Models, each sha-checked and opened as a byte copy in its own worker job on the WP6 v2 pose basis (wp6v2_owner_sheet's
open_job; the result sheet's path, wp6v2_result_sheet):
- ORIGINAL, PREFERRED (wp6v2_owner_sheet.MODELS);
- RUN 7: paid run facet-astra-007's best checkpoint (lab/package_007 c0007, revision 7);
- V3 REHEARSAL: a finish of the stand-in (review_20261012/v3/standin/pkg; its top triangle U = A, P, F, B one warp
  face), chosen by --head (HEADS): c0015, revision 19 (the default: its finish after STANDIN_REPORT's Addendum, P read
  at t 5.45, A-C and B-E declared ridges) -> result_v3_rehearsal2; or c0013, revision 15 (its first finish, P typed
  3.5 mm) -> result_v3_rehearsal, the 2026-10-13 build;
- the top triangle's two other readings, FOLD (U1 = A P B and U2 = P F B, flat, a line P-B) and FLAT (U one plane):
  each one facet_edit from the head's revision through the lab (Lab._edit: geometry.apply_edits -> fit_spec with the
  stored readings -> build -> temple_lab_build -> checkpoint) on a copy of the stand-in package (<out>/lab_copy), the
  same readings and declarations. Flat with the head's declarations is tried first; when the fit refuses it
  (E_FIT_SHAPE, recorded with the fit's own per-crease costs), the built flat leaves the costliest declaration it
  names to the fit, then the next, until the fit accepts (both heads: the B-E ridge alone).

Rendering: the job's rig studio_dome_v1 (the owner's choice "c", 2026-10-11). The photo pages (reference-0N.png,
black-*.png) use the existing sheet path: compare_to_photos with one crop per photo (the union of the models' temple
crops, cells <= 640 px), the original's frozen gains and photo stretch shared by every model. Every other image renders
the same way at its own box (the worker's render at the photo's crop camera and temple pose; the compare's gain,
backdrop and stretch arithmetic, as seeing's zoom). Each also gets a CLAY row (the worker's clay pass: one matte grey,
roughness 0.55, under the same rig), since no light reproduces the photos' facet tones (FINDINGS #4): clay shows the
faces by shape. Creases on a photo are the build's own model edges (build.model_edges, the lab's; run 7's from its
revision 7 rebuilt), drawn by seeing's compare overlay and zoom with their built angles.

Run from the repo root, in order (each step reads what the earlier wrote under OUT; add --head c0013 to every step for
the earlier build):
    glasses3d/.venv/Scripts/python.exe -m glasses3d.tests.proofs.wp6v3_owner_sheet options
        copy the stand-in package, build fold and flat -> options.json (receipts, fit lines, checkpoints)
    ... wp6v3_owner_sheet render      compares and renders -> render/, render.json
    ... wp6v3_owner_sheet sheets      the PNGs
    ... wp6v3_owner_sheet export      every head's GLB (the finish's lab export) -> viewer/ (HEADS' viewer files: c0015
                                      v3_rehearsal.glb, c0013 v3_rehearsal_c0013.glb), the chosen head first and default
    ... wp6v3_owner_sheet numbers     the checklist numbers -> numbers.json
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from glasses3d.agent.seeing import Seeing
from glasses3d.agent.temple_lab import geometry as G
from glasses3d.g3d.temple import build as BLD
from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import fit as FIT
from glasses3d.g3d.temple.spec import TempleError
from glasses3d.g3d_host import blender_camera as BC
from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host import pose as PO
from glasses3d.g3d_host.camera import project_mm
from glasses3d.tests.proofs import wp6v2_owner_sheet as OS

G3D = Path(__file__).resolve().parents[2]
DATA = G3D / "data"
W6 = DATA / "wp6v2"
STANDIN = W6 / "review_20261012" / "v3" / "standin" / "pkg"
PKG7 = W6 / "lab" / "package_007"
VIEWER = W6 / "viewer"
RIG = "studio_dome_v1"
LABEL = "v3 rehearsal: a stand-in model playing Astra with the v3 tools, $0"
RUN7_REV = 7                    # run 7's best: package_007 c0007, revision 7
# the stand-in's finishes (STANDIN_REPORT.md), each with its revision, output folder, checkpoint sha, the lab's own
# finish export (file, sha256) and its 3D viewer entry (key, label, file)
HEADS = {
    "c0015": {"revision": 19, "out": W6 / "result_v3_rehearsal2",       # the Addendum's finish (calls 32-40)
              "blend": "8bc6e09f640e1162d6c5e9f3298d79cd2981d0a5e3e9b6c1b861c1bc2700c357",
              "glb": ("head-71984b0902431083.glb", "b8a72136b85f8fb621165a163104ca7b2525bc97f86e27d65af23095168b90e9"),
              "viewer": ("v3rehearsal", "v3 rehearsal c0015 (stand-in tool test)", "v3_rehearsal.glb")},
    "c0013": {"revision": 15, "out": W6 / "result_v3_rehearsal",        # the first finish (call 31)
              "blend": "f5bfe2fbe0487a0f6af3bca777d841cbdd83ddabb956a843dad5e725e1304181",
              "glb": ("head-2f58b7c780bdafed.glb", "93f9abfffe9031d2b7e5980a8dc26413197ba236bdce3218b3f9dfdecff91954"),
              "viewer": ("v3rehearsal13", "v3 rehearsal c0013 (earlier finish)", "v3_rehearsal_c0013.glb")},
}
HEAD = "c0015"                  # the head the sheets show (use_head; --head)
HEAD_REV = HEADS[HEAD]["revision"]
OUT = HEADS[HEAD]["out"]
LABCOPY = OUT / "lab_copy"
MODELS = {
    "original": OS.MODELS["original"],
    "run7": ("ASTRA RUN 7", PKG7 / "checkpoints" / "c0007.blend",
             "3b4435b27313854367aec10c389087c9cad3b36d3d9311b03c1d3aaeed5924e1"),
    "v3": ("V3 REHEARSAL", STANDIN / "checkpoints" / f"{HEAD}.blend", HEADS[HEAD]["blend"]),
    "preferred": ("PREFERRED (10-10)",) + OS.MODELS["preferred"][1:],
}


def use_head(name: str) -> None:
    """Point the module at one of HEADS: HEAD, HEAD_REV, OUT, LABCOPY and MODELS["v3"]."""
    global HEAD, HEAD_REV, OUT, LABCOPY
    HEAD, HEAD_REV, OUT = name, HEADS[name]["revision"], HEADS[name]["out"]
    LABCOPY = OUT / "lab_copy"
    MODELS["v3"] = ("V3 REHEARSAL", STANDIN / "checkpoints" / f"{name}.blend", HEADS[name]["blend"])


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

# the top triangle's other two readings (WP6_V3 §2: the owner sheet shows warp, fold and flat), from the head's
# revision ({rev}); flat's "edits" are completed by flat_left (the declarations the fit refuses with a flat U)
OPTION_OBJECT = ("TOOL TEST by the coding agent on a copy (not Astra's, not the stand-in's): revision {rev}'s layout "
                 "and readings with the top band U built as {what}. Faces L (A B C), LL (A C H H0), RL (B E R70..R30 "
                 "C), RU (B F E) and the crease declarations as revision {rev}{left}.")
FLAT_U = {"op": "set_facet", "label": "U", "poly": ["A", "P", "F", "B"], "plane": {"mode": "flat"}, "soft": False}
OPTIONS = {
    "fold": {"title": "(b) FOLD: two flat faces, a line from the peak P to the logo corner B",
             "note": "tool test: U as two flat faces U1 = A P B, U2 = P F B (a line P-B), the same readings",
             "what": "a fold: two flat faces U1 = A P B and U2 = P F B with a line P-B (its angle left to the fit)",
             "left": ", L|U and RU|U as L|U1 and RU|U2",
             "edits": [{"op": "delete_facet", "label": "U"},
                       {"op": "set_facet", "label": "U1", "poly": ["A", "P", "B"], "plane": {"mode": "flat"},
                        "soft": False},
                       {"op": "set_facet", "label": "U2", "poly": ["P", "F", "B"], "plane": {"mode": "flat"},
                        "soft": False},
                       {"op": "set_crease", "facets": ["L", "U1"], "shape": "ridge"},
                       {"op": "set_crease", "facets": ["RU", "U2"], "shape": "ridge"}]},
    # with the head's declarations the fit refuses a flat U on both heads (E_FIT_SHAPE; c0013: the declared RL|RU
    # ridge B-E costs chi2 +4.7, readings-only -5.1 +- 3.4 deg; c0015: +6.4 in all, B-E +6.0, A-C +0.6); the refusal
    # is recorded and "flat" leaves the declarations flat_left names to the fit
    "flat_declared": {"title": "FLAT with the head's declarations",
                      "note": "tool test: U one flat face A P F B, the same readings and declarations",
                      "what": "one flat face A P F B (the fit moves its corners within their errors to make them one "
                              "plane)",
                      "left": "",
                      "edits": [FLAT_U]},
    "flat": {"title": "(c) FLAT: one plane, the fit spreading the misfit",
             "note": "tool test: U one flat face A P F B, the same readings; {pairs} left to the fit, since the fit "
                     "refuses the declaration with a flat U (E_FIT_SHAPE)",
             "what": "one flat face A P F B (the fit moves its corners within their errors to make them one plane)",
             "left": ", except {pairs}: not declared (with a flat U the fit refuses it, E_FIT_SHAPE)",
             "edits": [FLAT_U]},
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
        "TOOL TEST copy of the v3 blind stand-in's package (review_20261012/v3/standin/pkg), "
        f"{C._utc()[:10]}: the top triangle's fold and flat readings built from revision {HEAD_REV} ({HEAD}) through "
        "the lab for the owner sheet (tests/proofs/wp6v3_owner_sheet.py). Never Astra input, never a deliverable. "
        f"{n} JSON files repointed.\n", encoding="utf-8")


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


def shape_refusal(design: dict, readings: dict) -> dict | None:
    """The fit's E_FIT_SHAPE facts for a design (fit.fit_spec with the stored readings, as Lab._fit_build calls it; the
    lab's refusal keeps the message only): {dchi2, chi2_free, creases [{pair, declared, dchi2, free_deg,
    free_sd_deg}]} (dchi2 None: it cannot be built at all), or None when the fit accepts the design."""
    try:
        FIT.fit_spec(design, readings=readings)
    except TempleError as e:
        if e.code != "E_FIT_SHAPE":
            raise
        return {"dchi2": e.fields.get("dchi2"), "chi2_free": e.fields.get("chi2_free"),
                "creases": [{k: c.get(k) for k in ("pair", "declared", "dchi2", "free_deg", "free_sd_deg")}
                            for c in e.fields.get("creases") or []]}
    return None


def flat_left(lab, edits: list) -> tuple:
    """(the declared pairs flat leaves to the fit, the fit's refusal facts on the way): the head's revision with
    ``edits`` (U flat) and, while the fit refuses E_FIT_SHAPE, the costliest declaration it names deleted (one that
    cannot be built at all first), then the next, until the fit accepts."""
    base, readings = lab.design(HEAD_REV), lab.readings()
    left, refusals = [], []
    while True:
        design, _notes = G.apply_edits(base, edits + [{"op": "delete_crease", "facets": list(p)} for p in left],
                                       reset_layout=False, readings=readings, facts={})
        r = shape_refusal(design, readings)
        if r is None:
            return left, refusals
        refusals.append(r)
        named = sorted((c for c in r["creases"] if c["pair"] not in left),
                       key=lambda c: -(float("inf") if c["dchi2"] is None else c["dchi2"]))
        if not named:
            raise RuntimeError(f"flat: the fit refuses it with no declaration left to remove: {r}")
        left.append(named[0]["pair"])


def edge_name(facets: dict, pair) -> str:
    """'RL|RU (B-E)': a facet pair with the two corners its polygons share (build_facts' facets)."""
    a, b = (facets[f]["poly"] for f in pair)
    shared = [n for n in a if n in b]
    return "|".join(pair) + (f" ({'-'.join(shared)})" if len(shared) == 2 else "")


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
        fit = lab.fit_result(HEAD_REV)
        res["as_built"] = {"revision": HEAD_REV, "checkpoint": HEAD, "blend": str(MODELS["v3"][1]),
                           "crease_lines": LB.crease_lines(tb, fit, lab.raw_edges(HEAD_REV)),
                           "model_edges": lab.model_edges(HEAD_REV), "hinge": lab.spec(HEAD_REV)["hinge"],
                           "facts": build_facts(tb, fit, lab.spec(HEAD_REV)),
                           "P": {k: fit.nodes["P"].get(k) for k in ("source", "t", "sd_X", "X")}}
        left, refusals = flat_left(lab, OPTIONS["flat"]["edits"])
        pairs = ", ".join(edge_name(res["as_built"]["facts"]["facets"], p) for p in left)
        for name, o in OPTIONS.items():
            edits = o["edits"] + ([{"op": "delete_crease", "facets": list(p)} for p in left] if name == "flat" else [])
            said = name != "flat" or bool(left)          # a flat the fit accepts as declared leaves nothing out
            obj = OPTION_OBJECT.format(rev=HEAD_REV, what=o["what"], left=o["left"].format(pairs=pairs) if said else "")
            note = o["note"].format(pairs=pairs) if said else OPTIONS["flat_declared"]["note"]
            t0 = time.perf_counter()
            r = lab._edit(f"owner-{name}", HEAD_REV, False, edits, note, obj, True, ["B1"])
            rec = {k: v for k, v in r.items() if k not in ("images",)}
            rec["seconds"] = round(time.perf_counter() - t0, 1)
            rec["edits"] = edits
            if name == "flat_declared":
                rec["fit_refusal"] = refusals[0] if refusals else None
            if name == "flat":
                rec["left_to_fit"] = left
                rec["fit_refusals"] = refusals
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
    """The tool-test label every image carries (LABEL and the stand-in finish shown), white on dark red, wrapped to
    ``width``."""
    f = font(size, True)
    step = int(size * 1.35)
    lines = wrap(f"{LABEL}; its finish {HEAD}, revision {HEAD_REV}", f, width - 24)
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


def crease_text(creases: list) -> list:
    """One line per facet|facet crease of build_facts' creases, then the facet|base ones: 'L|U ridge 22.6 (21.3-33.1)'
    (the range along the edge when it spans 0.5 deg or more)."""
    rows = sorted(creases, key=lambda c: ("base" in c["pair"], c["pair"]))
    return [f"{c['pair']} {c['shape']} {c['deg']:.1f}" + (f" ({c['range'][0]:.1f}-{c['range'][1]:.1f})"
                                                          if c["range"][1] - c["range"][0] >= 0.5 else "")
            for c in rows]


def angled_sheet(R: dict, O: dict) -> Image.Image:
    """B2: a grid photo | Astra run 7 | v3 rehearsal at the whole glasses (gloss, clay, creases on the photo), then the
    close-up of the charted temple with each model's built crease angles beside it."""
    rows = R["rows"]
    rid = ANGLED[0]
    photo = img(rows["v3"]["angled"]["files"]["photo"])
    W = photo.width
    zooms = {n: next(z for z in R["creases"][n] if z["rid"] == ANGLED_ZOOM[0] and tuple(z["box"]) == ANGLED_ZOOM[1])
             for n in ("run7", "v3")}
    c7, cv = clay_rows([rows[n]["angled"]["files"]["clay"] for n in ("run7", "v3")])
    body = vstack([hstack([cell(photo, "PHOTO B2 (black angled)", W),
                           cell(img(rows["run7"]["angled"]["files"]["render"]),
                                "ASTRA RUN 7: gloss black render, same camera", W),
                           cell(img(rows["v3"]["angled"]["files"]["render"]),
                                "V3 REHEARSAL: gloss black render, same camera", W)]),
                   hstack([cell(photo, "PHOTO B2", W), cell(c7, "ASTRA RUN 7: clay", W),
                           cell(cv, "V3 REHEARSAL: clay", W)]),
                   hstack([cell(photo, "PHOTO B2", W),
                           cell(img(R["creases"]["run7_overlay"][rid]), "ASTRA RUN 7: its creases on the photo", W),
                           cell(img(R["creases"]["v3_overlay"][rid]), "V3 REHEARSAL: its creases on the photo", W)])],
                  10)
    trio = hstack([titled(["photo"], img(zooms["v3"]["panels"]["photo"]), None, 19),
                   titled(["Astra run 7's creases"], img(zooms["run7"]["panels"]["overlay"]), None, 19),
                   titled(["the v3 rehearsal's creases"], img(zooms["v3"]["panels"]["overlay"]), None, 19)])
    side = body.width - trio.width - 24
    angles = hstack([titled(["Astra run 7 (c0007)"] + crease_text(run7_facts()["creases"]), None, side // 2 - 8, 21),
                     titled([f"V3 rehearsal ({HEAD})"] + crease_text(O["as_built"]["facts"]["creases"]), None,
                            side // 2 - 8, 21)])
    close = hstack([trio, titled(["Built crease angles, degrees (the length-weighted mean; the range along the edge "
                                  "in brackets)"], angles, side, 22)], 24)
    notes = titled(["How to read the rows", CLAY_NOTE, EDGE_KEY], None, body.width, 22)
    title = ("Close-up of the charted temple, larger than native scale: each crease labelled with its built angle "
             "(degrees; - = valley; a-b = its range along the edge)")
    return vstack([banner(body.width, 26), notes, body, titled([title], close, body.width, 24)])


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
    misfit = fit_line(o["flat"]["facts"]["fit_lines"], "misfit beyond").split(": ", 1)[-1]
    head = titled([
        "The top triangle (face 4: the v3 rehearsal's face U = A, P, F, B) built three ways from the same readings",
        f"{AS_BUILT}. L|U ridge {ac['L|U']['deg']:.1f} deg, RU|U ridge {ac['RU|U']['deg']:.1f} deg; no line inside "
        f"the face. This is the stand-in's delivered head ({HEAD}, revision {HEAD_REV}).",
        f"{OPTIONS['fold']['title']}. The line P-B is a {fc['U1|U2']['shape']} of {fc['U1|U2']['deg']:.1f} deg; L|U1 "
        f"{fc['L|U1']['deg']:.1f}, RU|U2 {fc['RU|U2']['deg']:.1f}; every other face as built.",
        f"{OPTIONS['flat']['title']}. {flat_refusal_text(O)} To make A, P, F, B one plane the fit moves the "
        f"logo corner B off its reading ({misfit}); built, B sits {abs(bx):.2f} mm "
        f"{'further in' if bx < 0 else 'further out'} than in (a). Then L|U {lc['L|U']['deg']:.1f}, RU|U "
        f"{lc['RU|U']['deg']:.1f}, the logo's back diagonal L|RL {lc['L|RL']['deg']:.1f} (as built "
        f"{ac['L|RL']['deg']:.1f})" + "".join(
            f", {edge_name(O['as_built']['facts']['facets'], p)} a {lc['|'.join(p)]['deg']:.1f} deg "
            f"{lc['|'.join(p)]['shape']} (as built a {ac['|'.join(p)]['deg']:.1f} deg {ac['|'.join(p)]['shape']})"
            for p in o["flat"].get("left_to_fit", [["RL", "RU"]])) + ".",     # an options.json before left_to_fit
        f"(b) and (c) are the coding agent's builds through the lab on a copy of the stand-in's package (the same "
        f"readings, {p_text(O)}): tool tests, not the stand-in's or Astra's choice. The photo and every render use the "
        "same camera, the job's light and the photo's contrast."], None, W, 30)
    return vstack([banner(W, 30), head, body])


def fit_line(lines: list, start: str) -> str:
    """The fit report line that starts with ``start``, its wrapped continuations (indented entries) joined, spaces
    collapsed; "" when there is none."""
    out = None
    for ln in lines:
        if out is None:
            out = [ln] if ln.startswith(start) else None
        elif ln.startswith(" "):
            out.append(ln)
        else:
            break
    return " ".join(" ".join(out).split()) if out else ""


def flat_refusal_text(O: dict) -> str:
    """What the fit said to a flat U with the head's declarations (options' fit_refusal), and what flat left out."""
    o = O["options"]
    facets = O["as_built"]["facts"]["facets"]
    fr = o["flat_declared"].get("fit_refusal")
    left = [edge_name(facets, p) for p in o["flat"].get("left_to_fit") or []]
    if fr is None:
        return ("With the stand-in's declarations the fit accepts it." if o["flat_declared"].get("status") != "refused"
                else f"With the stand-in's declarations the fit refuses it ({o['flat_declared'].get('code')}).")
    costs = ", ".join(f"{edge_name(facets, c['pair'])} {c['declared']} " +
                      ("cannot be built" if c["dchi2"] is None else f"+{c['dchi2']:.1f}") for c in fr["creases"])
    total = "they cannot all be built" if fr["dchi2"] is None else f"chi2 +{fr['dchi2']:.1f} in all"
    return (f"With the stand-in's declarations the fit refuses it (E_FIT_SHAPE, {total}: {costs}), so "
            f"{' and '.join(left)} {'is' if len(left) == 1 else 'are'} left to the fit.")


def p_text(O: dict) -> str:
    """The peak P's thickness as the head has it: read or typed (options' as_built P)."""
    p = O["as_built"].get("P") or {}
    if p.get("source") == "typed":
        return f"P's typed {p['t']:.1f} mm thickness"
    if p.get("source") == "read":
        return f"P read, fitted at t {p['t']:.2f} mm"
    return "P as the head has it"


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
    for name, fn in (("faces_v3.png", lambda: faces_sheet(R)), ("angled_v3.png", lambda: angled_sheet(R, O)),
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
VIEWER_ENTRY = "  {{key: '{key}', label: '{label}', file: '{file}'}},\n"


def export() -> dict:
    """Every head's GLB (HEADS): the lab's own finish export of that checkpoint (head-<fingerprint>.glb under the
    stand-in package's work/exports; sha-checked and its fingerprint matched to the checkpoint's) copied to the 3D
    viewer as its HEADS file. The viewer lists them first, HEAD's first, and opens on HEAD's (an earlier v3 rehearsal
    entry is replaced; the other models stay)."""
    rows = [json.loads(ln) for ln in (STANDIN / "checkpoints" / "index.jsonl").read_text(encoding="utf-8").splitlines()
            if ln.strip()]
    out = {}
    for ck in [HEAD] + [h for h in HEADS if h != HEAD]:
        name, want = HEADS[ck]["glb"]
        src = STANDIN / "work" / "exports" / name
        if OS.sha(src) != want:
            raise RuntimeError(f"{src}: sha changed")
        fp = next(r["fingerprint"] for r in rows if r["id"] == ck)
        if not name.startswith(f"head-{fp[:16]}"):
            raise RuntimeError(f"{name} is not {ck}'s export (fingerprint {fp[:16]})")
        dst = VIEWER / HEADS[ck]["viewer"][2]
        shutil.copyfile(src, dst)
        if OS.sha(dst) != want:
            raise RuntimeError(f"{dst}: copy differs")
        out[ck] = {"glb": str(dst), "sha256": want, "revision": HEADS[ck]["revision"], "fingerprint": fp}
    page = VIEWER / "index.html"
    t = page.read_text(encoding="utf-8")
    t = re.sub(r"  \{key: '[^']*', label: '[^']*', file: 'v3_rehearsal[^']*\.glb'\},\n", "", t)
    entries = "".join(VIEWER_ENTRY.format(key=HEADS[ck]["viewer"][0], label=HEADS[ck]["viewer"][1],
                                          file=HEADS[ck]["viewer"][2]) for ck in out)
    t = t.replace("const MODELS = [\n", "const MODELS = [\n" + entries, 1)
    key = HEADS[HEAD]["viewer"][0]
    t = re.sub(r"currentKey = '[^']*'", f"currentKey = '{key}'", t, count=1)
    t = re.sub(r"show\('[^']*'\)\.catch", f"show('{key}').catch", t, count=1)
    if entries not in t or f"show('{key}').catch" not in t or f"currentKey = '{key}'" not in t:
        raise RuntimeError("viewer index.html: the model list or the default was not where expected")
    page.write_text(t, encoding="utf-8")
    return {"heads": out, "default": key}


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
    ap.add_argument("--head", choices=tuple(HEADS), default=HEAD, help="the stand-in finish shown (HEADS)")
    a = ap.parse_args()
    use_head(a.head)
    fn = {"options": options, "render": render, "sheets": sheets, "export": export, "numbers": numbers}[a.step]
    res = fn()
    print(json.dumps({"step": a.step, "seconds": res.get("seconds")}, indent=1))
