"""The facet lab, WP6 v3 (plan/WP6_V3.md §6, phase-2 interface I6) with a fake scene and a fake compare: no Blender,
no network, no model. The geometry is real (the g3d.temple /2 core and fit.py on the fixtures; prada_default is the
Prada-layout fixture), and so are the readings (readings.read_point on the synthetic rig of
tests/agent/test_temple_readings.py). The tools' schemas and their mapping onto the Lab methods are
tests/agent/test_temple_lab_v3.py's (track lab-e1); the Blender side of facet_variants is test_wp2_worker's
test_temple_variants_render_builds_renders_and_leaves_the_scene_unchanged.
"""
import asyncio
import copy
import hashlib
import json
import re
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

pytest.importorskip("agents")
from agents.tool_context import ToolContext  # noqa: E402

from glasses3d.agent import loop as L  # noqa: E402
from glasses3d.agent.journal import WorkerFailed  # noqa: E402
from glasses3d.agent.offline import disable_sdk_tracing  # noqa: E402
from glasses3d.agent.seeing import MATCHED_RIG, Seeing  # noqa: E402
from glasses3d.agent.temple_lab import geometry as G  # noqa: E402
from glasses3d.agent.temple_lab import lab as LB  # noqa: E402
from glasses3d.agent.temple_lab import readings as RD  # noqa: E402
from glasses3d.agent.temple_lab import sheets as SH  # noqa: E402
from glasses3d.agent.temple_lab import state as S  # noqa: E402
from glasses3d.agent.temple_lab import tools as T  # noqa: E402
from glasses3d.agent.temple_lab.lab import LAB_SCHEMA, Lab, render_safe_name  # noqa: E402
from glasses3d.g3d.temple import build as BLD  # noqa: E402
from glasses3d.g3d.temple import build_temple  # noqa: E402
from glasses3d.g3d.temple import fit as FIT  # noqa: E402
from glasses3d.g3d.temple import fixtures as FX  # noqa: E402
from glasses3d.g3d.temple import report as RP  # noqa: E402
from glasses3d.g3d.temple.spec import TempleError, canonical, parse, spec_sha256  # noqa: E402
from glasses3d.g3d_host import compare as GC  # noqa: E402
from glasses3d.g3d_host import pose as PO  # noqa: E402
from glasses3d.g3d_host.camera import Camera, NormFrame  # noqa: E402
from glasses3d.tests.agent.test_temple_readings import SynthBasis, corner_picks, point  # noqa: E402

PHOTO_IDS = ["01", "02", "03", "04", "05", "06"]
BLACK_IDS = ["B1", "B2", "B3"]          # B1: a calibrated colourway photo (basis black-side); B2, B3: synthetic
#                                        reference-only photos (the generic reference role; lab_photos)
CAMERA_IDS = PHOTO_IDS + ["B1"]         # the photos with a camera (Lab.compare_ids)
KINDS = {"01": "side", "02": "angled", "03": "top", "04": "front", "05": "side", "06": "rear"}
DEFAULT = ["01", "02", "05"]
OBJECT = "TOOL TEST: faces F1-F4 of the fixture; F1|F2 a ridge (the readings), F4 ends at E; nothing uncertain"
REMEDY_WORDS = ("solved corners", "rise_per_mm", "solve instead", "one solved", "meets it", "do not redeclare",
                "crease plane can hold", "facet_restore", "restore it")     # v2 refusal notes and removed tools
disable_sdk_tracing()                  # the SDK's error path would build a trace exporter (a network client)


# --------------------------------------------------------------------------------------------- fakes
class FakeBasis:
    """Six target photos and the variant-colourway black-side (small synthetic images) with orthographic cameras."""

    def __init__(self):
        self.frame = NormFrame((0.0, 0.0, 0.0), 200.0)
        self.refs = {f"reference-{p}": {"view_kind": KINDS[p], "camera": None} for p in PHOTO_IDS}
        self.refs["black-side"] = {"view_kind": "side", "camera": None, "colourway": "variant"}

    def colourway(self, rid):
        return self.refs[rid].get("colourway") or "target"

    def camera(self, rid):
        return Camera(90.0, 0.0, 0.0, 0.0, 400.0, 160.0, 120.0)

    def shape(self, rid):
        return 240, 320

    def photo(self, rid):
        y, x = np.mgrid[0:240, 0:320]
        return np.stack([(x % 256), (y % 256), np.full_like(x, 200)], -1).astype(np.uint8)

    def matte(self, rid):
        m = np.zeros((240, 320), bool)
        m[80:160, 40:280] = True
        return m

    def pose(self, rid):
        return {"+1": {"theta_deg": 2.0, "axis_xz_mm": [64.6, -10.1]},
                "-1": {"theta_deg": 2.0, "axis_xz_mm": [-64.6, -10.1]}}


class ReadBasis(FakeBasis):
    """FakeBasis on the readings tests' synthetic rig (test_temple_readings.SynthBasis: perspective cameras around the
    Prada-sized temple pair, 640 x 480 photos; 05 square-on, 02 angled, black-side = 05's camera moved): photo_read
    reads real points here."""

    def __init__(self):
        super().__init__()
        self.synth = SynthBasis()
        self.frame = self.synth.frame
        for rid in self.refs:
            self.refs[rid].update(self.synth.refs[rid])

    def camera(self, rid):
        return self.synth.camera(rid)

    def shape(self, rid):
        return self.synth.shape(rid)

    def pose(self, rid):
        return self.synth.pose(rid)

    def photo(self, rid):
        return self.synth.photo(rid)

    def matte(self, rid):
        m = np.zeros((480, 640), bool)
        m[150:330, 60:600] = True
        return m


class FakeIndex:
    def __init__(self, rows):
        self.rows = rows

    def get(self, cid):
        return next((r for r in reversed(self.rows) if r["id"] == cid), None)


class FakeSession:
    """An in-memory checkpoint chain: a temple_lab_build checkpoints a fingerprint named by the spec sha and answers
    the core receipt lines plus ``blender_lines`` (the Blender layer's own) with the decal move and hinge leaf gap
    (``built`` keeps every spec it was given: the fitted spec, I6); a variant render writes float renders whose
    brightness depends on the variant (labels in ``refuse_variants`` are refused by "Blender"; ``render_error`` fails
    the whole render)."""

    def __init__(self, root):
        self.root = Path(root)
        self.ck_dir = self.root / "checkpoints"
        self.rows = [{"id": "c0000", "fingerprint": "fp-original", "file": "c0000.blend", "sha256": "0" * 64}]
        self.index = FakeIndex(self.rows)
        self.worker = None
        self.calls = []
        self.built = []
        self.fail_build = None
        self.read_fingerprints = []
        self.blender_lines = ["W_DECAL_SPANS_CREASE: decal PRADA spans F1|F2"]
        self.gap_mm, self.move_mm = 0.036, 0.61
        self.refuse_variants: set = set()
        self.render_error = False
        self.closed = 0
        self.read_params = []

    @property
    def head(self):
        return self.rows[-1]

    def _new(self, fp, kind):
        row = {"id": f"c{len(self.rows):04d}", "fingerprint": fp, "file": "x.blend", "sha256": "0" * 64, "kind": kind}
        self.rows.append(row)
        return row

    def start(self):
        return {"head": self.head}

    def close(self):
        self.closed += 1

    def fingerprint(self):
        return self.head["fingerprint"]

    def restore_checkpoint(self, cid, call_id=None):
        self.calls.append(("restore", cid))
        target = self.index.get(cid)
        if target is None:
            return {"status": "error", "message": f"no checkpoint {cid!r}"}
        if target["fingerprint"] == self.head["fingerprint"]:
            return {"status": "ok", "checkpoint": self.head["id"], "changed": False}
        row = self._new(target["fingerprint"], "restore")
        return {"status": "ok", "checkpoint": row["id"], "restored_from": cid, "changed": True}

    def call_mutating(self, tool, method, params, *, deadline_s, label="", call_id=None):
        self.calls.append((method, call_id))
        if self.fail_build == "raise":
            raise WorkerFailed("injected worker failure", self.head)
        if self.fail_build:
            return {"status": "refused", "call_id": call_id, "result": {"status": "refused", "code": self.fail_build,
                                                                        "message": "injected"}}
        self.built.append(params["spec"])
        row = self._new("fp-" + spec_sha256(params["spec"])[:12], "save")
        core = RP.receipt(build_temple(params["spec"]))["lines"]          # the Blender layer repeats the core's
        return {"status": "ok", "call_id": call_id, "checkpoint": row["id"], "result": {
            "status": "ok", "lines": core + list(self.blender_lines),
            "sides": {"+x": {"decals": [{"max_move_mm": self.move_mm}],
                             "hinge": {"leaves": [{"gap_mm": self.gap_mm}]}}}}}

    def call_read(self, tool, method, params, *, deadline_s, call_id):
        assert method == "temple_variants_render"
        self.calls.append((method, call_id, deadline_s))
        self.read_params.append(params)
        self.read_fingerprints.append(self.head["fingerprint"])
        if self.render_error:
            return {"status": "error", "call_id": call_id, "message": "injected render failure"}
        out = Path(params["out_dir"])
        out.mkdir(parents=True, exist_ok=True)
        # keyed and named by the label's render safe name, as the worker (g3d/worker.py temple_variants_render)
        built = {render_safe_name(x["label"]): {"status": "refused", "code": "E_RELIEF_STEP", "message": "injected"}
                 if x["label"] in self.refuse_variants else {"status": "ok", "overrides": {}}
                 for x in params["variants"]}
        views = []
        for v in params["views"]:
            res = v["camera"]["resolution"]
            for k, name in enumerate(["head"] + [lb for lb, b in built.items() if b["status"] == "ok"]):
                files = {}
                for ps in v.get("passes") or params.get("passes") or ["native_linear"]:   # a view's own passes win
                    p = out / f"{v['name']}--{name}_{ps}.npz"
                    a = np.zeros((res[1], res[0]), np.float16)
                    a[res[1] // 4:3 * res[1] // 4, :] = 1.0
                    level = (0.2 + 0.1 * k) * (0.1 if ps == "gloss_black" else 1.0)
                    with open(p, "wb") as f:
                        np.savez_compressed(f, rgb=np.full((res[1], res[0], 3), level, np.float16), alpha=a)
                    files[ps] = {"path": str(p)}
                views.append({"name": f"{v['name']}--{name}", "files": files})
        return {"status": "ok", "call_id": call_id, "result": {"status": "ok", "variants": built,
                                                               "render": {"views": views}, "unchanged": True}}


def side_outline(facing: bool) -> dict:
    """seeing's outline_offsets_mm record of one temple in a side view from above (seeing._side_outline): the top not
    measured (its face is in view), the bottom measured every 10 mm with one covered station."""
    rows = [{"d_mm": float(d), "offset_mm": None, "reason": "top face in view", "px_per_mm": 7.5} for d in (10, 20, 30)]
    bottom = [{"d_mm": 10.0, "offset_mm": 0.12, "reason": None, "px_per_mm": 7.5},
              {"d_mm": 20.0, "offset_mm": None, "reason": "covered", "px_per_mm": 7.5},
              {"d_mm": 30.0, "offset_mm": -0.43, "reason": None, "px_per_mm": 7.5}]
    return {"facing": facing, "elevation_deg": 14.2, "mm_per_px": [0.13, 0.14], "top": rows, "bottom": bottom}


class FakeSeeing:
    """compare_to_photos writes a stacked image per photo and per zoom and a report.json per call (the fields the lab
    reads: side views carry seeing's outline_offsets_mm, the charted temple facing, the mirrored seen from inside; top
    and rear photos the v2 outer-edge offsets, which the v3 lab never prints; ``records`` replaces a photo's synthetic
    record). Every call's arguments are kept (zoom, model_edges, opening, photo_names); a zoom's text names its photo
    as photo_names says, as seeing's does. ``slow_s`` sleeps that long first.
    export_head writes ``scene_glb`` (default: the box glasses of tests/glbfab, part roles on the nodes; the lab's
    variant edges take their occluder from it through fit_mesh)."""

    def __init__(self, session, root):
        self.s, self.root = session, Path(root)
        self.calls = []
        self.records: dict = {}
        self.slow_s = 0.0
        from glasses3d.tests import glbfab
        self.scene_glb = glbfab.build(role_where="node")

    def bounds(self, call_id):
        return {"origin_mm": [0.0, 0.0, 0.0]}

    def _render_params(self, rig, env, light_frame):
        return {"rig": rig, "light_frame": light_frame, "engine": "auto"}

    def export_head(self, call_id=None):
        if self.slow_s:
            time.sleep(self.slow_s)
        p = self.root / "work" / "exports" / f"head-{self.s.head['fingerprint']}.glb"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.scene_glb)
        return {"status": "exported", "path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "fingerprint": self.s.head["fingerprint"], "checkpoint": self.s.head["id"]}

    def fit_mesh(self, glb, sha, *, skip=(), frame=None):
        from glasses3d.g3d_host import fitmesh
        return fitmesh.load_fit_mesh(glb, skip=tuple(skip), frame=frame)

    def compare_to_photos(self, refs=None, passes=(), rig=None, crop="temple", points_px=None, hinge_z_mm=None,
                          call_id=None, render_deadline_s=None, zoom=None, model_edges=None, opening=None,
                          photo_names=None):
        self.calls.append({"refs": refs, "passes": list(passes), "rig": rig, "crop": crop, "points_px": points_px,
                           "hinge_z_mm": hinge_z_mm, "call_id": call_id, "render_deadline_s": render_deadline_s,
                           "zoom": zoom, "model_edges": model_edges, "opening": opening, "photo_names": photo_names})
        names = photo_names or {}
        if self.slow_s:
            time.sleep(self.slow_s)
        rids = [f"reference-{p}" for p in DEFAULT] if refs == "default" else list(refs)
        d = self.root / "work" / "compare" / call_id
        d.mkdir(parents=True, exist_ok=True)
        photos, rep = {}, {}
        for rid in rids:
            img = d / f"{rid}.png"
            tint = hashlib.sha256(f"{call_id}{rid}".encode()).digest()
            Image.new("RGB", (300, 400), tuple(tint[:3])).save(img)
            vk = "side" if rid == "black-side" else KINDS[rid.split("-")[1]]
            offs = {s: [{"d_mm": float(dd), "offset_mm": round(0.1 * (dd % 7) - 0.3, 2)} for dd in range(10, 131, 10)]
                    for s in ("+1", "-1")} if vk in ("top", "rear") else None
            black = {"stretch": [21.0, 144.0], "gloss": {"gain": 0.83, "stretch": [21.0, 144.0],
                                                         "stretch_is_photo": True}} if rid == "black-side" else {}
            rep[rid] = {"view_kind": vk, "temple_iou": 0.912, "px_per_mm": 8.17,
                        "crop": {"box": [20, 30, 300, 210], "k": 0.493}, "outer_edge_offsets_mm": offs,
                        "start_offsets_mm": offs, "stretch": [60.0, 240.0], "background": [255, 255, 255],
                        "outline_offsets_mm": {"charted": side_outline(True), "mirrored": side_outline(False)}
                        if vk == "side" else None,
                        "model_edges": None if model_edges is None else [
                            {"pair": e["pair"], "shape": e["shape"], "runs_px": [[[10.0, 10.0], [20.0, 12.0]]]}
                            for e in model_edges],
                        **black, **self.records.get(rid, {})}
            photos[rid] = {"image": str(img), "text": "x", "cached": False}
            if points_px and rid in points_px:
                photos[rid]["points"] = [{"uv": [u, v], "hit": True, "side": 1, "d_mm": 20.0, "y_mm": 1.5,
                                          "X_mm": 70.0} for u, v in points_px[rid]]
        zooms = []
        for n, z in enumerate(zoom or [], 1):
            img = d / f"zoom{n}-{z['rid']}.png"
            Image.new("RGB", (200, 120), (n * 40, 90, 90)).save(img)
            x0, y0, x1, y1 = z["box"]
            cw = "variant" if z["rid"] == "black-side" else "target"         # seeing's word for another colourway
            text = (f"zoom {n} on {names.get(z['rid'], z['rid'])} (side, {cw}): native box [{x0:g},{y0:g},{x1:g},"
                    f"{y1:g}] at k 2.00: 16.34 "
                    f"px/mm in this image (8.17 native)\npanels: photo | render / overlay | "
                    f"{'geometry' if z['geometry'] else 'render with edges'}\nmodel edges shown (deg; - = valley): "
                    f"F1|F2 14\ncompare_id {call_id}.")
            zooms.append({"rid": z["rid"], "image": str(img), "text": text, "panels": {}, "cached": False})
        gains = {"colourways": {"target": {"native_linear": {"gain": [0.9, 0.95, 1.1]}}},
                 "gloss_black": {"gain": 3.0, "stretch": [0.0, 128.0]},
                 "light_frame": {"center": [0, 0, 0], "radius": 80.0}}
        (d / "report.json").write_text(json.dumps({"photos": rep, "gains": gains, "environment": None,
                                                   "zoom": [{"rid": z["rid"]} for z in zooms]}), encoding="utf-8")
        return {"status": "ok", "compare_id": call_id, "checkpoint": self.s.head["id"], "photos": photos,
                "zoom": zooms, "report": str(d / "report.json")}


def lab_photos(root: Path) -> list:
    """lab.json's photo rows in cli.prepare's format: the six targets, B1 (the basis' black-side) and the
    reference-only B2, B3 (synthetic images under inputs/images): a generic test of the reference role (no camera,
    photo_image only). The Prada job's own B2, B3 are colourway rows with transported cameras since 2026-10-11
    (test_temple_lab_cli)."""
    rows = [{"id": p, "path": f"images/reference-{p}.png", "view": KINDS[p], "role": "target"} for p in PHOTO_IDS]
    rows.append({"id": "B1", "path": "images/black-side.png", "view": "side", "role": "colourway", "colour": "black",
                 "basis_ref": "black-side"})
    (root / "inputs" / "images").mkdir(parents=True, exist_ok=True)
    for pid, view, size in (("B2", "angled", (192, 128)), ("B3", "front", (180, 120))):
        p = root / "inputs" / "images" / f"black-{view}.png"
        Image.new("RGB", size, (12, 12, 14 + len(view))).save(p)
        rows.append({"id": pid, "path": f"images/black-{view}.png", "view": view, "role": "reference",
                     "colour": "black", "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    return rows


def make_lab(root: Path, spec=None, basis=None, **session) -> Lab:
    """A prepared lab, as Lab.prepare makes it: revision 0 = spec (default prada_default) through the fit, applied on
    the fake original (the worker gets the fitted spec) and compared at every target photo by the prepare step
    (``session``: FakeSession attributes for that apply, e.g. blender_lines; ``basis``: default FakeBasis)."""
    (root / "inputs").mkdir(parents=True, exist_ok=True)
    (root / "inputs" / "lab.json").write_text(json.dumps({"schema": LAB_SCHEMA, "rig": "studio_dome_v1",
                                                          "assets": [], "photos": lab_photos(root)}),
                                              encoding="utf-8")
    s = FakeSession(root)
    for k, v in session.items():
        setattr(s, k, v)
    lab = Lab(root, session=s, seeing=FakeSeeing(s, root))
    lab._basis = basis or FakeBasis()
    lab.state["start_checkpoint"], lab.state["start_fingerprint"] = "c0000", "fp-original"
    lab.state["original"] = {"footprints": {"decals": [], "hinge": []}}
    design = spec or FX.prada_default()
    rid, _ = lab._register(design, parent=None, kind="template", label="revision 0", fit=FIT.fit_spec(design))
    assert lab._apply("prepare-r0", rid, tool="prepare")["status"] == "ok"
    assert lab._compare("prepare-r0", lab.photo_ids(), by="prepare")["status"] == "ok"
    lab.save()
    return lab


@pytest.fixture
def lab(tmp_path):
    return make_lab(tmp_path)


def call(tool, args: dict, call_id: str = "call_x", context=None):
    ctx = ToolContext(context=context, tool_name=tool.name, tool_call_id=call_id, tool_arguments=json.dumps(args))
    return asyncio.run(tool.on_invoke_tool(ctx, json.dumps(args)))


def parsed(out):
    text = out[0].text
    head, footer = text.rsplit("\n", 1)
    return json.loads(head), footer, out[1:], text


def tools_of(lab, **settings):
    return {t.name: t for t in T.registry(lab, L.RunSettings(effort="max", **settings) if settings else None)}


def judgement(lab, best=None, overall="mixed"):
    """The judgement of the latest compare Astra saw (one entry per photo, one difference each, state v3's form)."""
    c = S.seen_compares(lab.state)[-1]
    return {"compare_id": c["compare_id"], "versus_compare_id": None, "overall": overall,
            "photos": [{"photo": p, "verdict": overall, "differences": [
                {"what": "upper facet too dark at the logo", "d_mm": 30.0, "y_mm": 5.0}]} for p in c["photos"]],
            "best_checkpoint": best or lab.s.head["id"]}


def edit_args(parent=0, edits=(), *, judge=None, note="x", obj=None, apply=True, photos=None, reset=False) -> dict:
    """facet_edit's arguments (I5: every one named)."""
    return {"parent_revision": parent, "reset_layout": reset, "edits": list(edits), "judgement": judge, "note": note,
            "object": obj, "apply": apply, "photos": photos}


def compare_args(photos=None, crop=None, zoom=None, points=None) -> dict:
    return {"photos": photos, "crop": crop, "zoom": zoom, "points_px": points}


def enums(schema) -> list:
    """Every "enum" list inside a JSON schema (the photo-id Literals), in order, $defs included."""
    out = []
    if isinstance(schema, dict):
        if isinstance(schema.get("enum"), list):
            out.append(schema["enum"])
        for k, v in schema.items():
            if k != "enum":
                out += enums(v)
    elif isinstance(schema, list):
        for v in schema:
            out += enums(v)
    return out


def set_t(name, spec, dt):
    d, y, t = canonical(spec)["nodes"][name]
    return {"op": "set_node", "name": name, "d_mm": d, "y_mm": y, "t": {"mode": "mm", "mm": float(t) + dt}}


def rename(spec: dict, facets: dict, nodes: dict) -> dict:
    """The same spec with facets and nodes renamed (a pure relabelling, the geometry unchanged)."""
    s = copy.deepcopy(canonical(spec))

    def n(x):
        return nodes.get(x, x)
    s["nodes"] = {n(k): v for k, v in s["nodes"].items()}
    out = {}
    for label, f in s["facets"].items():
        f["poly"] = [n(x) for x in f["poly"]]
        if isinstance(f["plane"], dict) and f["plane"].get("crease"):
            f["plane"]["crease"] = [n(x) for x in f["plane"]["crease"]]
        out[facets.get(label, label)] = f
    s["facets"] = out
    for c in s["creases"]:
        c["facets"] = [facets.get(x, x) for x in c["facets"]]
    return canonical(s)


def no_remedy(text: str) -> bool:
    """True when ``text`` carries none of v2's remedy phrases or removed tools' names (WP6_V3 P6)."""
    return not any(w in text for w in REMEDY_WORDS)


# --------------------------------------------------------------------------------------------- edits union
@pytest.mark.parametrize("name", sorted(FX.FIXTURES))
def test_edits_round_trip_every_fixture(name):
    """From another parent (the slab's chains, no layout; the target's chamfers, which the edits have no op for) the
    edits of spec_to_edits rebuild the spec exactly; the strict v3 union takes them unless the fixture has a v2 crease
    + rise plane (internal callers only, geometry module doc; WP6_V3 §6 offers Astra none)."""
    spec = FX.FIXTURES[name]()
    parent = {**canonical(FX.slab()), "chamfers": canonical(spec)["chamfers"]}
    out, notes = G.apply_edits(parent, G.spec_to_edits(spec), reset_layout=True)
    assert out == canonical(spec) and notes == []
    v2 = any(isinstance(f["plane"], dict) for f in canonical(spec)["facets"].values())
    if v2:
        with pytest.raises(Exception):
            T.EditList.model_validate({"edits": G.spec_to_edits(spec)})
    else:
        validated = T.EditList.model_validate({"edits": G.spec_to_edits(spec)})      # the strict models accept them
        assert [e.model_dump() for e in validated.edits] == G.spec_to_edits(spec)


def test_edit_refusals_name_the_edit_index():
    base = FX.prada_default()
    cases = [
        ([{"op": "delete_node", "name": "C"}], 0, "facet"),
        ([{"op": "set_node", "name": "Z", "d_mm": 1, "y_mm": 1, "t": {"mode": "mm", "mm": None}}], 0, "t.mm"),
        ([{"op": "set_node", "name": "Z", "d_mm": 1, "y_mm": 1, "t": {"mode": "base", "mm": 2.0}}], 0, "null"),
        ([{"op": "set_node", "name": "Z", "d_mm": 1, "y_mm": 1, "t": {"mode": "mm", "mm": -1.0}}], 0, "negative"),
        ([set_t("C", base, 0.1), {"op": "delete_facet", "label": "NOPE"}], 1, "no facet"),
        ([{"op": "set_facet", "label": "F9", "poly": ["A", "B", "Q"], "plane": {"mode": "flat"}, "soft": False}], 0,
         "does not exist"),
        ([{"op": "set_facet", "label": "F9", "poly": ["A", "B", "C"], "plane": {"mode": "warp"}, "soft": False}], 0,
         "warp facet needs"),
        ([{"op": "set_crease", "facets": ["F1", "F7"], "shape": "ridge"}], 0, "F7"),
        ([{"op": "delete_crease", "facets": ["F1", "F3"]}], 0, "no declared crease"),
        ([{"op": "splice_chain", "chain": "inner", "d_from_mm": 5, "d_to_mm": 1, "points": []}], 0, "d_from"),
        ([{"op": "set_scalar", "key": "roll_mm", "value": -1}], 0, "value"),
        ([{"op": "set_node", "name": "base", "d_mm": 1, "y_mm": 1, "t": {"mode": "base", "mm": None}}], 0, "names"),
    ]
    for edits, index, words in cases:
        with pytest.raises(G.EditError) as info:
            G.apply_edits(base, edits)
        assert info.value.index == index and info.value.code == "E_EDIT" and words in info.value.message, edits


def test_delete_node_with_its_facets_in_one_call_and_crease_cascade():
    base = FX.prada_default()
    gone = [f for f, rec in canonical(base)["facets"].items() if "C" in rec["poly"]]
    edits = [{"op": "delete_node", "name": "C"}] + [{"op": "delete_facet", "label": f} for f in gone]
    spec, notes = G.apply_edits(base, edits)
    assert "C" not in spec["nodes"] and not set(gone) & set(spec["facets"])
    left, items, lost = list(canonical(base)["creases"]), [], []     # v3-standin-fix: each delete takes what is left
    for i, f in enumerate(gone):
        took = [x for x in left if f in x["facets"]]
        left = [x for x in left if f not in x["facets"]]
        if took:
            items.append(", ".join(f"{'|'.join(x['facets'])} {x['shape']}" for x in took) + f" (with facet {f}, "
                         f"edits[{i + 1}])")
            lost += ["|".join(x["facets"]) for x in took]
    assert len(lost) >= 2 and notes == ["deleted crease declarations: " + "; ".join(items) + "; not declared now: "
                                        + ", ".join(lost)]
    assert all(not set(c["facets"]) & set(gone) for c in spec["creases"])


def test_chain_offset_with_taper_and_splice():
    base = canonical(FX.prada_default())
    spec, _ = G.apply_edits(base, [{"op": "offset_chain", "chain": "base.top", "d_from_mm": 20.0, "d_to_mm": 30.0,
                                    "delta_mm": 0.5, "taper_mm": 5.0}])
    pts = np.asarray(spec["base"]["top"])
    old = np.asarray(base["base"]["top"])

    def at(p, d):
        return float(np.interp(d, p[:, 0], p[:, 1]))
    assert at(pts, 25.0) == pytest.approx(at(old, 25.0) + 0.5)
    assert at(pts, 17.5) == pytest.approx(at(old, 17.5) + 0.25)
    assert at(pts, 10.0) == pytest.approx(at(old, 10.0)) and at(pts, 40.0) == pytest.approx(at(old, 40.0))
    spec, _ = G.apply_edits(base, [{"op": "splice_chain", "chain": "outline.top", "d_from_mm": 20.0,
                                    "d_to_mm": 40.0, "points": [[25.0, 15.0], [35.0, 15.5]]}])
    top = spec["outline"]["top"]
    assert [p for p in top if 20 <= p[0] <= 40] == [[25.0, 15.0], [35.0, 15.5]]
    build_temple(spec)


def test_geometry_delta_distinct_rules():
    a = build_temple(FX.prada_default())
    deeper = FX.prada_default()
    deeper["nodes"]["C"][2] += 1.0
    b = build_temple(deeper)
    d = G.geometry_delta(a, b)
    assert d["surface_mm"] > 0.5 and d["outline_mm"] == 0.0 and G.distinct_heads(d)
    same = G.geometry_delta(a, build_temple(FX.prada_default()))
    assert same["value_mm"] == 0.0 and same["crease_mm"] == 0.0 and same["crease_new"] == []
    assert not G.distinct_heads(same)
    flip = FX.prada_default()
    flip["creases"] = [c for c in flip["creases"] if c["facets"] != ["F4", "base"]]
    assert not G.distinct_heads(G.geometry_delta(a, build_temple(flip)))      # a declaration alone: no change
    assert G.spread_triple(["A", "B", "C"], {("A", "B"): 0.4, ("A", "C"): 0.9, ("B", "C"): 0.5}) == ["A", "B", "C"]
    assert G.spread_triple(["A", "B", "C"], {("A", "B"): 0.2, ("A", "C"): 0.9, ("B", "C"): 0.5}) is None


# the slab fixture's base is one plane over d 0-65 (fixtures module doc): a facet whose crease nodes are "base" and
# whose rise follows the base is flush with it
SLAB = FX.slab()
SLAB_DXB = float(parse(SLAB).x_base(41.0, 0.0) - parse(SLAB).x_base(40.0, 0.0))     # the base's dX per mm of d


def _node(name, d, y, t="base"):
    """set_node; t "base", None (solve) or a thickness in mm."""
    tt = {"mode": "base", "mm": None} if t == "base" else {"mode": "solve", "mm": None} if t is None else \
        {"mode": "mm", "mm": float(t)}
    return {"op": "set_node", "name": name, "d_mm": float(d), "y_mm": float(y), "t": tt}


def _facet(label, poly, crease, rise):
    return {"op": "set_facet", "label": label, "poly": list(poly), "fade": None,
            "plane": {"mode": "crease", "crease": list(crease), "rise_per_mm": float(rise)}}


def _rect(label, d0, y0, w, h, t="base", along_d=True):
    """A w x h facet at (d0, y0) parallel to the base: its crease is the bottom edge (along_d) or the d0 edge, at t."""
    p = [f"{label}{k}" for k in range(4)]
    if along_d:
        return [_node(p[0], d0, y0, t), _node(p[1], d0 + w, y0, t), _node(p[2], d0 + w, y0 + h, None),
                _node(p[3], d0, y0 + h, None), _facet(label, p, (p[0], p[1]), 0.0)]
    return [_node(p[0], d0, y0, t), _node(p[1], d0 + w, y0, None), _node(p[2], d0 + w, y0 + h, None),
            _node(p[3], d0, y0 + h, t), _facet(label, p, (p[3], p[0]), SLAB_DXB)]


def _roof(h, yc=0.0, w=3.0):
    """Two facets meeting along y = yc (d 40-60), their outer edges w mm away on the base: the crease h mm above the
    base is a ridge (h < 0: a valley) of 2 x atan(h / w)."""
    return [_node("c0", 40.0, yc, FX.T_ZONE + h), _node("c1", 60.0, yc, FX.T_ZONE + h), _node("u0", 40.0, yc + w, None),
            _node("u1", 60.0, yc + w, None), _node("l0", 40.0, yc - w, None), _node("l1", 60.0, yc - w, None),
            _facet("U", ["c0", "c1", "u1", "u0"], ("c0", "c1"), -h / w),
            _facet("D", ["l0", "l1", "c1", "c0"], ("c0", "c1"), -h / w)]


def on_slab(edits):
    return build_temple(G.apply_edits(SLAB, edits)[0])


def _chain(builds) -> list:
    """Precondition 1's count over these heads in order (state.unique_heads with distinct_heads)."""
    return S.unique_heads(list(range(len(builds))),
                          lambda i, j: G.distinct_heads(G.geometry_delta(builds[i], builds[j])))


def test_crease_pieces_are_the_built_folds_never_the_table():
    """The folds over the border band agree with the build's own crease table on the Prada fixture (every declared
    ridge folds > 2 deg up, the F4|base valley down); the crease set reads the geometry only, so a rewritten crease
    table (a "flip" or a lost crease with the same geometry) is no change."""
    a = build_temple(FX.prada_default())
    p = G.crease_pieces(a)
    L = np.hypot(*(p["seg"][:, 1] - p["seg"][:, 0]).T)
    f = np.nan_to_num(p["fold"])
    ridge, valley = L[f >= 2.0].sum(), L[f <= -2.0].sum()
    table = {tuple(c["regions"]): c for c in a.tables["creases"] if c["kind"] == "crease"}
    assert table[("F4", "base")]["shape"] == "valley" and ridge > 120.0 and 19.0 < valley < 20.5
    assert L[np.isnan(p["fold"])].sum() < 0.1 * L.sum()                 # unmeasurable only at the outline
    b = build_temple(FX.prada_default())
    b.tables = copy.deepcopy(b.tables)
    next(c for c in b.tables["creases"] if c["regions"] == ["F1", "F2"])["shape"] = "valley"
    b.tables["creases"] = [r for r in b.tables["creases"] if r["regions"] != ["F2", "F3"]]
    d = G.geometry_delta(a, b)
    assert d["value_mm"] == 0.0 and d["crease_mm"] == 0.0 and d["crease_new"] == [] and not G.distinct_heads(d)


def test_a_pure_rename_is_no_change():
    """A pure rename of facets and nodes builds the same temple: the same surface and the same crease sets."""
    a = build_temple(FX.prada_default())
    renamed = rename(FX.prada_default(), {"F1": "G1", "F2": "G2", "F3": "Q7", "F4": "Z"}, {"A": "N1", "B": "N2",
                                                                                         "C": "N3"})
    assert renamed != canonical(FX.prada_default()) and spec_sha256(renamed) != spec_sha256(FX.prada_default())
    d = G.geometry_delta(a, build_temple(renamed))
    assert d["value_mm"] == 0.0 and d["crease_mm"] == 0.0 and d["crease_new"] == [] and not G.distinct_heads(d)
    assert d["creases"]["ridge"]["length_mm"][0] == d["creases"]["ridge"]["length_mm"][1] > 100.0


def _moved(spec: dict, name: str, dd: float = 0.0, dy: float = 0.0, dt: float = 0.0) -> dict:
    s = copy.deepcopy(canonical(spec))
    s["nodes"][name][0] += dd
    s["nodes"][name][1] += dy
    if dt:
        s["nodes"][name][2] += dt
    return canonical(s)


@pytest.mark.parametrize("mm", [0.0, 1.0, 2.0, 3.0])
def test_a_corner_outside_the_outline_moves_nothing_built(mm):
    """pf_review defect 1: K_hi is a corner of F1 above the outline; moving it 0 / 1 / 2 / 3 mm moves nothing built
    (same mesh), so it is never a new head (a declared polygon corner outside the outline counts for nothing)."""
    base = canonical(FX.prada_default())
    assert base["nodes"]["K_hi"][1] > max(y for _d, y in base["outline"]["top"])
    a, b = build_temple(base), build_temple(_moved(base, "K_hi", dy=mm))
    assert np.array_equal(a.V, b.V)
    d = G.geometry_delta(a, b)
    assert d["value_mm"] == 0.0 and d["crease_mm"] == 0.0 and d["crease_new"] == []
    assert G.distinct_heads(d) is False


@pytest.mark.parametrize("name, dd, dy", [("C", 0.05, 0.0), ("C", 0.0, -0.05), ("C", 0.02, 0.04), ("B", 0.05, 0.0),
                                          ("B", 0.0, 0.1)])
def test_a_node_nudge_that_moves_surface_and_creases_less_than_0_2_mm_is_no_new_head(name, dd, dy):
    """§4.1 by the built temple: a node moved in (d, y) whose built surface (normal distance) and visible creases move
    < HEAD_DISTINCT_MM is no new head."""
    a = build_temple(FX.prada_default())
    d = G.geometry_delta(a, build_temple(_moved(FX.prada_default(), name, dd, dy)))
    assert d["surface_mm"] < 0.05 and d["outline_mm"] == 0.0 and d["crease_mm"] < G.HEAD_DISTINCT_MM
    assert d["crease_new"] == [] and G.distinct_heads(d) is False, d


@pytest.mark.parametrize("name, dd, dy", [("C", 0.3, 0.0), ("C", 0.0, 0.3), ("B", 0.0, 0.3)])
def test_a_crease_move_that_hardly_moves_the_surface_is_reported_but_no_new_head(name, dd, dy):
    """The same node 0.3 mm away moves a ridge's end by >= 0.2 mm but the surface < 0.2 mm: the crease distance is
    reported, and only the surface / outline decides (crease thresholds had knife edges, 2026-10-11)."""
    a = build_temple(FX.prada_default())
    d = G.geometry_delta(a, build_temple(_moved(FX.prada_default(), name, dd, dy)))
    assert d["value_mm"] < G.HEAD_DISTINCT_MM <= d["crease_mm"] and not G.distinct_heads(d), d


@pytest.mark.parametrize("dt, distinct", [(0.05, False), (0.15, False), (0.3, True), (1.0, True)])
def test_a_thickness_change_at_a_node_counts_from_0_2_mm(dt, distinct):
    a = build_temple(FX.prada_default())
    d = G.geometry_delta(a, build_temple(_moved(FX.prada_default(), "C", dt=dt)))
    assert (d["surface_mm"] >= G.HEAD_DISTINCT_MM) is distinct and d["crease_mm"] < G.HEAD_DISTINCT_MM
    assert G.distinct_heads(d) is distinct, d


def test_rename_reorder_and_a_node_on_an_edge_are_no_new_head():
    """A rename plus a rotated, reversed polygon is the same built temple; so is a solved node added on a facet's
    edge (it shapes nothing)."""
    a = build_temple(FX.prada_default())
    s = rename(FX.prada_default(), {"F1": "X1", "F2": "X2"}, {"A": "N1", "C": "N3"})
    s["facets"]["X1"]["poly"] = list(reversed(s["facets"]["X1"]["poly"]))
    s["facets"]["X2"]["poly"] = s["facets"]["X2"]["poly"][1:] + s["facets"]["X2"]["poly"][:1]
    d = G.geometry_delta(a, build_temple(canonical(s)))
    assert not G.distinct_heads(d) and d["value_mm"] == 0.0 and d["crease_mm"] == 0.0, d
    e = copy.deepcopy(canonical(FX.prada_default()))
    poly = e["facets"]["F4"]["poly"]
    p, q = (np.asarray(e["nodes"][n][:2], float) for n in poly[:2])
    e["nodes"]["M"] = [*(0.5 * (p + q)).tolist(), None]
    e["facets"]["F4"]["poly"] = [poly[0], "M"] + poly[1:]
    d = G.geometry_delta(a, build_temple(canonical(e)))
    assert not G.distinct_heads(d) and d["max_dx_mm"] < 1e-6 and d["crease_mm"] < 0.02, d


def test_deleting_a_proud_facet_is_a_new_head():
    a = build_temple(FX.prada_default())
    gone = canonical(FX.prada_default())
    del gone["facets"]["F3"]
    gone["creases"] = [c for c in gone["creases"] if "F3" not in c["facets"]]
    assert G.distinct_heads(G.geometry_delta(a, build_temple(gone)))
    proud = on_slab(_rect("F", 40.0, -2.0, 10.0, 4.0, FX.T_ZONE + 0.5))
    d = G.geometry_delta(proud, build_temple(SLAB))
    assert d["surface_mm"] > 0.45 and d["crease_new"] == ["ridge"] and G.distinct_heads(d)


def test_layout_only_changes_with_the_same_surface_are_no_new_head():
    """gf_review defect 1a: facets flush with the base (a hop of 3 mm, a 0.0067 mm apex move across the old 1 mm^2
    cut, a shared edge 1.03 -> 0.97 mm) and coplanar splits of a 0.5 mm proud facet change no surface and no visible
    crease: revision 0 plus any number of them counts 1 head."""
    slab = build_temple(SLAB)
    hops = [on_slab(_rect("F", 40.0, y0, 2.0, 6.0, along_d=False)) for y0 in (-4.0, -1.0, 2.0)]
    assert _chain([slab] + hops) == [0]

    def tri(w):                                          # a flush triangle of area 3 w mm^2
        return on_slab([_node("T0", 40.0, -3.0), _node("T1", 40.0, 3.0), _node("T2", 40.0 + w, 0.0, None),
                        _facet("T", ["T0", "T1", "T2"], ("T1", "T0"), SLAB_DXB)])

    def adjacent(L):                                     # two flush facets sharing an edge L mm long
        return on_slab(_rect("F1", 40.0, -3.0, 2.5, 6.0, along_d=False) +
                       _rect("F2", 42.5, 3.0 - L, 2.5, 6.0, along_d=False))
    assert _chain([slab, tri(0.3367), tri(0.33), adjacent(1.03), adjacent(0.97)]) == [0]
    t = FX.T_ZONE + 0.5
    whole = on_slab(_rect("F1", 40.0, -2.0, 10.0, 4.0, t))
    splits = []
    for ys in (-1.0, 0.0, 1.0):                          # F1 cut into two coplanar strips at y = ys
        splits.append(on_slab([_node("A0", 40.0, -2.0, t), _node("A1", 50.0, -2.0, t), _node("M1", 50.0, ys, t),
                               _node("M0", 40.0, ys, t), _node("A2", 50.0, 2.0, None), _node("A3", 40.0, 2.0, None),
                               _facet("F1a", ["A0", "A1", "M1", "M0"], ("A0", "A1"), 0.0),
                               _facet("F1b", ["M0", "M1", "A2", "A3"], ("M0", "M1"), 0.0)]))
    assert _chain([whole] + splits) == [0]
    d = G.geometry_delta(whole, splits[0])
    assert d["value_mm"] < 1e-3 and d["crease_mm"] == 0.0


def test_hairlines_slivers_and_a_balance_point_are_no_new_head():
    """gf_review defect 1b: a 0.001 mm gap opened in a real 5.7 deg valley between two 0.5 mm proud facets, a
    0.09 mm coplanar sliver inserted into it, and a facet whose border with the base folds as a ridge along one part
    and a valley along another moved across its table's majority flip (a warp facet, its fold changing sign along
    the border, one corner 0.002 mm; v2's flat facet proud at one end and sunk at the other no longer flips: the v3
    builder judges a facet|base border by its fold against the base, v3-builder-fix P2-3) are no new head."""
    t = FX.T_ZONE + 0.5

    def two(gap=0.0, sliver=0.0):
        """F1 [40, 50] x [-3, 1], 0.5 mm proud and flat; F2 from y = 1 + gap + sliver to 5, rising 0.1 / mm from
        F1's level (a 5.7 deg valley with F1); a sliver S coplanar with F1 fills [1, 1 + sliver]."""
        e = [_node("A0", 40.0, -3.0, t), _node("A1", 50.0, -3.0, t), _node("A2", 50.0, 1.0, t),
             _node("A3", 40.0, 1.0, t), _facet("F1", ["A0", "A1", "A2", "A3"], ("A0", "A1"), 0.0)]
        lo = ["A3", "A2"]
        if sliver:
            e += [_node("S2", 50.0, 1.0 + sliver, t), _node("S3", 40.0, 1.0 + sliver, t),
                  _facet("S", ["A3", "A2", "S2", "S3"], ("A3", "A2"), 0.0)]
            lo = ["S3", "S2"]
        if gap:
            e += [_node("G0", 40.0, 1.0 + gap, t), _node("G1", 50.0, 1.0 + gap, t)]
            lo = ["G0", "G1"]
        return on_slab(e + [_node("B2", 50.0, 5.0, None), _node("B3", 40.0, 5.0, None),
                            _facet("F2", [lo[0], lo[1], "B2", "B3"], (lo[0], lo[1]), 0.1)])
    shared = two()
    assert [(c["shape"], round(c["mean_dihedral_deg"], 1)) for c in shared.tables["creases"]
            if c["regions"] == ["F1", "F2"]] == [("valley", 5.7)]
    for other in (two(gap=0.001), two(sliver=0.09)):
        d = G.geometry_delta(shared, other)
        assert d["creases"]["valley"]["mm"] is not None and d["crease_mm"] < 0.1 and not G.distinct_heads(d), d

    def balance(dt):                                      # a warp twisted 1 mm: E2 +1 (+ dt), E3 -1 mm
        b = on_slab([_node("E0", 40.0, -3.0, FX.T_ZONE), _node("E1", 50.0, -3.0, FX.T_ZONE),
                     _node("E2", 50.0, 1.0, FX.T_ZONE + 1.0 + dt), _node("E3", 40.0, 1.0, FX.T_ZONE - 1.0),
                     {"op": "set_facet", "label": "E", "poly": ["E0", "E1", "E2", "E3"], "plane": {"mode": "warp"},
                      "soft": False}])
        return b, next(c["shape"] for c in b.tables["creases"] if c["regions"] == ["E", "base"])
    lo, hi = -0.004, 0.004
    assert balance(lo)[1] != balance(hi)[1]
    while hi - lo > 0.0005:                               # bisect the table's majority flip
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if balance(mid)[1] == balance(lo)[1] else (lo, mid)
    (a, sa), (b, sb) = balance(lo), balance(hi)
    d = G.geometry_delta(a, b)
    assert sa != sb and d["crease_mm"] == 0.0 and not G.distinct_heads(d), d


def test_crease_changes_are_reported_and_only_the_surface_decides():
    """Crease thresholds had knife edges (a 0.05 mm thickness nudge could make a 2 deg fold appear), so the crease
    comparison is reported but never decides (2026-10-11). Faint changes that move the surface < 0.2 mm - a crease
    crossing 2 deg, a 3 deg ridge flipped to a valley, a new 3 deg crease, a crease moved 0.5 mm - are no new head;
    the same flip on a 0.3 mm roof moves the surface >= 0.2 mm and is one."""
    def proud(deg):
        return on_slab(_rect("K", 40.0, -3.0, 10.0, 6.0, FX.T_ZONE + 2.0 * np.tan(np.radians(deg))))
    d = G.geometry_delta(proud(0.9), proud(2.1))
    assert d["value_mm"] < 0.05 and d["crease_new"] == ["ridge"] and not G.distinct_heads(d), d
    ridge = on_slab(_roof(0.08))
    assert [(c["shape"], round(c["mean_dihedral_deg"], 2)) for c in ridge.tables["creases"]
            if c["regions"] == ["D", "U"]] == [("ridge", 3.05)]
    flip = G.geometry_delta(ridge, on_slab(_roof(-0.08)))
    assert flip["value_mm"] < G.HEAD_DISTINCT_MM and flip["creases"]["ridge"]["mm"] >= 1.0
    assert not G.distinct_heads(flip)
    new = G.geometry_delta(build_temple(SLAB), ridge)
    assert new["value_mm"] < G.HEAD_DISTINCT_MM and "ridge" in new["crease_new"] and not G.distinct_heads(new)
    moved = G.geometry_delta(ridge, on_slab(_roof(0.08, yc=0.5)))
    assert abs(moved["crease_mm"] - 0.5) < 0.02 and not G.distinct_heads(moved)
    big = G.geometry_delta(on_slab(_roof(0.3)), on_slab(_roof(-0.3)))
    assert big["value_mm"] >= G.HEAD_DISTINCT_MM and G.distinct_heads(big), big


def test_the_roll_band_does_not_magnify_an_outline_move():
    """gf_review defect 2: on a rolled temple (roll 0.6 mm) outline.bottom moved 0.04 / 0.08 / 0.12 mm moves X by up
    to 0.36 mm in the nearly vertical roll band but the surface along its normal by ~0.1 mm: one head, and the
    variant spread (value_mm) is the outline's move. The roll itself (0 -> 0.6 mm) moves the corner 0.25 mm."""
    rolled = copy.deepcopy(SLAB)
    rolled["roll_mm"] = 0.6
    heads = [build_temple(rolled)] + [
        build_temple(G.apply_edits(rolled, [{"op": "offset_chain", "chain": "outline.bottom", "d_from_mm": 0.0,
                                             "d_to_mm": 140.0, "delta_mm": -x, "taper_mm": 0.0}])[0])
        for x in (0.04, 0.08, 0.12)]
    d = G.geometry_delta(heads[0], heads[3])
    assert d["max_dx_mm"] > 0.3 and d["surface_mm"] < 0.15 and d["value_mm"] == d["outline_mm"] == 0.12
    assert not G.distinct_heads(d) and _chain(heads) == [0]
    roll = G.geometry_delta(build_temple(SLAB), heads[0])
    assert 0.2 <= roll["surface_mm"] < 0.3 and G.distinct_heads(roll)


def test_the_head_comparison_is_fast():
    """The comparison runs on every finish; < 0.5 s per pair (about 0.05 s on the Prada fixture, 0.15 s on the
    preferred layout with a 0.4 mm roll)."""
    for spec, node in ((FX.prada_default(), "C"), (FX.rolled(), "C")):
        a = build_temple(spec)
        b = build_temple(_moved(spec, node, dt=0.3))
        G.geometry_delta(a, b)
        t0 = time.perf_counter()
        G.geometry_delta(a, b)
        assert time.perf_counter() - t0 < 0.5


def test_distinct_heads_chain_counts_unique_heads_only():
    """Precondition 1 counts a head only when it is distinct from EVERY head counted before it: alternating two
    revisions A, B, A, B is 2 heads, not 4; a head near any counted one never counts."""
    def st(revs):
        return {"compares": [{"by": "astra", "revision": r, "photos": ["05"]} for r in revs]}
    assert S.distinct_compared_heads(st((1, 2, 1, 2)), lambda a, b: True) == [1, 2]
    near = {frozenset((1, 2)): False}
    assert S.distinct_compared_heads(st((1, 1, 2, 3, 2)), lambda a, b: near.get(frozenset((a, b)), True)) == [1, 3]
    near = {frozenset((1, 3)): False}                       # 3 is near 1 (not near 2): it never counts
    assert S.distinct_compared_heads(st((1, 2, 3, 4)), lambda a, b: near.get(frozenset((a, b)), True)) == [1, 2, 4]
    assert S.distinct_compared_heads(st((1, 2, 3, 4)), lambda a, b: True, limit=2) == [1, 2]
    unseen = st((1, 2, 3))
    unseen["compares"][1]["unseen"] = True                  # a compare Astra never saw counts nothing
    assert S.distinct_compared_heads(unseen, lambda a, b: True) == [1, 3]
    miss = S.finish_missing({**st((1, 2, 1, 2)), "variants": [{"spread": ["A", "B", "C"]}], "judgements": []},
                            status="matched", best_revision=1, k=4, distinct=lambda a, b: True, camera_photos=[],
                            has_object=True)
    assert [m["precondition"] for m in miss] == [1] and "2 of 4" in miss[0]["message"]


# --------------------------------------------------------------------------------------------- revisions and the fit
def test_registry_names_the_v3_tools_and_the_labs_photo_ids(lab):
    """The registry over the real Lab (its schemas are test_temple_lab_v3's): the seven v3 tools, photo_image takes
    every photo, photo_read / photo_compare / facet_edit / facet_variants the photos with a camera, the variants at
    most 3 targets + 1 black; facet_finish names the precondition count K."""
    tools = T.registry(lab)
    assert tuple(t.name for t in tools) == T.TOOL_NAMES == (
        "facet_inspect", "photo_image", "photo_read", "photo_compare", "facet_edit", "facet_variants", "facet_finish")
    by = {t.name: t.params_json_schema for t in tools}
    assert enums(by["photo_image"]["properties"]["photo"]) == [PHOTO_IDS + BLACK_IDS]
    for name in ("photo_read", "photo_compare", "facet_edit", "facet_variants"):
        assert CAMERA_IDS in enums(by[name]) and PHOTO_IDS + BLACK_IDS not in enums(by[name]), name
    assert by["facet_variants"]["properties"]["photos"]["maxItems"] == 4
    finish = next(t for t in T.registry(lab, L.RunSettings(effort="max", finish_k=6)) if t.name == "facet_finish")
    assert "at least 6" in finish.description and "object note" in finish.description


def test_revision_zero_is_fitted_and_the_worker_builds_the_fitted_spec(lab):
    """I6: a revision is its design (revisions/rNNNN.json) and its fit (rNNNN.fit.json: the FitResult and the fitted
    spec); Lab.design / Lab.spec / Lab.build; the worker gets the fitted spec, never the design. A pure /2 design keeps
    its spec sha as its key."""
    rec = lab.state["revisions"][0]
    design, spec = lab.design(0), lab.spec(0)
    assert rec["file"] == "revisions/r0000.json" and rec["fit_file"] == "revisions/r0000.fit.json"
    assert design == canonical(FX.prada_default()) and rec["sha256"] == spec_sha256(FX.prada_default())
    fr = json.loads((lab.work / rec["fit_file"]).read_text(encoding="utf-8"))
    assert fr["schema"] == LB.FIT_RECORD_SCHEMA and fr["design_sha256"] == rec["sha256"]
    assert fr["spec_sha256"] == rec["spec_sha256"] == spec_sha256(spec) and fr["fit"]["spec"] == spec
    assert spec["facets"]["F2"]["plane"] == {"coef": spec["facets"]["F2"]["plane"]["coef"]}      # flat: its plane
    assert all(isinstance(v[2], float) for n, v in spec["nodes"].items() if n in ("A", "B", "C", "K"))
    assert spec["nodes"]["K"][2] == pytest.approx(5.0, abs=0.01)                    # solved by the fit (W_FIT_SOLVED)
    assert lab.s.built == [spec] and lab.build(0).spec.sha256 == spec_sha256(spec)
    assert isinstance(lab.fit_result(0), FIT.FitResult) and lab.fit_result(0).chi2 == pytest.approx(0.0, abs=1e-9)
    assert lab.state["heads"][0]["seq"] == 0                                     # the order precondition 5 reads


def test_a_design_is_its_revision_and_a_new_reading_a_new_design(tmp_path):
    """The same design is the same revision (a variant applied with no edits); a design whose reading was read again
    (other numbers) is a new revision; the design file keeps the read dict, the fit file its numbers."""
    lab = make_lab(tmp_path, basis=ReadBasis())
    read_c(lab, 9.0)
    edits = [{"op": "set_node", "name": "C", "d_mm": None, "y_mm": None, "t": {"mode": "reading", "mm": None}}]
    b1 = lab.edit("e1", 0, False, edits, None, "x", OBJECT, False, None)
    b2 = lab.edit("e2", 0, False, edits, None, "x", None, False, None)
    assert b1["status"] == b2["status"] == "built" and b1["revision"] == b2["revision"] == 1
    assert b2["note"] == "this design is revision 1 already"
    t = lab.design(1)["nodes"]["C"][2]
    assert G.is_read(t) and t["read"]["X"] == pytest.approx(lab.readings()["C"]["X"], abs=1e-6)
    assert isinstance(lab.spec(1)["nodes"]["C"][2], float)
    read_c(lab, 9.4)
    b3 = lab.edit("e3", 0, False, edits, None, "x", None, False, None)
    assert b3["revision"] == 2 and lab.state["revisions"][2]["sha256"] != lab.state["revisions"][1]["sha256"]
    assert lab.spec(2)["nodes"]["C"][2] == pytest.approx(lab.spec(1)["nodes"]["C"][2] + 0.4, abs=0.05)


# --------------------------------------------------------------------------------------------- readings
def picks_at(lab, name, d, y, t, photos=("05", "02")) -> dict:
    """A corner reading of the head's chart point (d, y, t) in ``photos`` (exact projections on the synthetic rig)."""
    tb = lab.build(lab.head_record()["revision"])
    return {"name": name, "kind": "corner", "picks": corner_picks(lab.basis.synth, point(tb, d, y, t), list(photos))}


def read_c(lab, t: float) -> dict:
    """Read node C of the Prada fixture (d 22.6, y -16.3) at thickness t in 05 + 02."""
    return lab.read("read-c", [picks_at(lab, "C", 22.6, -16.3, t)], [])


def test_photo_read_stores_replaces_and_drops_by_name_with_a_history(tmp_path):
    """photo_read (I6): readings.read_point on the head's build, stored by name in state["readings"] (a reading of the
    same name replaces the stored one; drop removes one), every change in state["reading_log"] with the call and the
    replaced record; each reading returns readings.reading_text and one pick sheet, registered like every image."""
    lab = make_lab(tmp_path, basis=ReadBasis())
    out = lab.read("call_r1", [picks_at(lab, "Q", 40.0, 2.0, 6.0), picks_at(lab, "R", 50.0, 4.0, 7.0)], [])
    assert out["status"] == "ok" and out["stored"] == ["Q", "R"] and list(out["readings"]) == ["Q", "R"]
    q = lab.readings()["Q"]
    assert (q["d"], q["y"], q["t"]) == (pytest.approx(40.0, abs=1e-3), pytest.approx(2.0, abs=1e-3),
                                        pytest.approx(6.0, abs=1e-3))
    assert q["call_id"] == "call_r1" and q["id"] == f"{out['read_id']}-Q" and q["revision"] == 0
    assert out["readings"]["Q"] == RD.reading_text(q)
    assert len(out["images"]) == 2
    first = [hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in out["images"]]
    for p, digest in zip(out["images"], first):
        rec = lab.state["images"][digest]
        assert rec["kind"] == "reading" and rec["ref"] == out["read_id"] and Path(p).is_relative_to(lab.work)
    assert lab.context_stubs(first) == {}                                # both readings are the stored ones
    q0 = dict(lab.readings()["Q"])
    out = lab.read("call_r2", [picks_at(lab, "Q", 40.0, 2.0, 6.5)], ["R"])
    assert list(out["replaced"]) == ["Q"] and out["dropped"] == ["R"] and out["stored"] == ["Q"]
    assert lab.readings()["Q"]["t"] == pytest.approx(6.5, abs=1e-3)
    # the replaced reading's change is printed (review2-lab-e2-2 P3-10): the old one, then X, d, y moves
    assert out["replaced"]["Q"] == LB.reading_change(q0, lab.readings()["Q"])
    assert out["replaced"]["Q"].startswith(f"was X {RD.fixed(q0['X'], 2)} +- {RD.fixed(q0['sd_X'], 2)} at d 40.00 "
                                           f"y 2.00 ({q0['id']}); X +0.50 mm (+") \
        and out["replaced"]["Q"].endswith(" sd of the difference), d +0.00, y +0.00")
    assert "head_nodes" not in out                                      # no node of the head holds Q or R
    second = hashlib.sha256(Path(out["images"][0]).read_bytes()).hexdigest()
    stubs = lab.context_stubs(first + [second])                         # the context guard: superseded sheets go
    assert set(stubs) == set(first) and "read again since" in stubs[first[0]] and "dropped since" in stubs[first[1]]
    log = lab.state["reading_log"]
    assert [(e["action"], e["name"], e["call_id"]) for e in log] == [
        ("read", "Q", "call_r1"), ("read", "R", "call_r1"), ("dropped", "R", "call_r2"), ("replaced", "Q", "call_r2")]
    assert log[3]["was"]["t"] == pytest.approx(6.0, abs=1e-3) and log[2]["was"]["name"] == "R"
    out = lab.read("call_r3", [], ["Q"])
    assert out["stored"] == [] and "images" in out and out["images"] == [] and not lab.is_clean()
    assert second in lab.context_stubs([second])


def test_photo_read_refusals_store_nothing(tmp_path):
    """All or none: readings' ValueErrors (one direction seen twice, a pick outside the photo), a photo without a
    camera (named for photo_image), an unknown name to drop, a name read and dropped, repeated names and an empty call
    are E_ARGS, and nothing is stored."""
    lab = make_lab(tmp_path, basis=ReadBasis())
    good = picks_at(lab, "Q", 40.0, 2.0, 6.0)
    cases = [([good, picks_at(lab, "R", 40.0, 2.0, 6.0, ("05", "B1"))], [], "along nearly one line"),
             ([good, {"name": "R", "kind": "corner", "picks": [{"photo": "05", "uv": [900.0, 10.0], "edges": None,
                                                                "sd_px": None}, good["picks"][1]]}], [],
              "inside the native 640 x 480 photo 05"),
             ([{**good, "picks": [{**good["picks"][0], "photo": "B2"}, good["picks"][1]]}], [], "photo_image only"),
             ([good], ["Z"], "no reading named 'Z'"),
             ([good], ["Q"], "both read and dropped"),
             ([good, good], [], "repeat a name"),
             ([], [], "name points to read")]
    for points, drop, words in cases:
        with pytest.raises(S.LabRefusal, match=re.escape(words)) as e:
            lab.read("x", points, drop)
        assert e.value.code == "E_ARGS"
        assert lab.readings() == {} and not lab.state.get("reading_log")
    assert lab.is_clean()


def test_a_replaced_or_dropped_reading_is_named_on_the_heads_nodes(tmp_path):
    """A node keeps the reading it was set from (geometry.stale_reads): photo_read names the head's nodes that hold a
    reading it replaced or dropped (with the reading's id and X), and facet_inspect's node rows mark them (review2-
    lab-e2-1 P2-1, review2-lab-e2-2 P2-1: a re-read 0.5 mm out and a dropped reading left the head's nodes, fit and
    receipts on the old ones with nothing saying so); set_node mode reading again makes them current."""
    lab = make_lab(tmp_path, basis=ReadBasis())
    tb0 = lab.build(0)
    tB = float(tb0.spec.base_t(46.8, 11.8))                              # B is "base": read on the arm
    lab.read("r1", [picks_at(lab, "C", 22.6, -16.3, 9.0), picks_at(lab, "B", 46.8, 11.8, tB)], [])
    old = {n: dict(r) for n, r in lab.readings().items()}
    reading = [{"op": "set_node", "name": n, "d_mm": None, "y_mm": None, "t": {"mode": "reading", "mm": None}}
               for n in ("B", "C")]
    body = lab.edit("e1", 0, False, reading, None, "x", OBJECT, True, ["05"])
    assert body["status"] == "applied" and body["revision"] == 1
    assert [r[2] for n, r in lab.inspect()["spec"]["nodes"]["rows"].items() if n in ("B", "C")] == ["read", "read"]
    out = lab.read("r2", [picks_at(lab, "C", 22.6, -16.3, 9.5)], ["B"])
    assert out["head_nodes"] == (f"head r1: node B holds reading B ({old['B']['id']}, X {RD.fixed(old['B']['X'], 2)}), "
                                 f"now dropped; node C holds the earlier reading C ({old['C']['id']}, X "
                                 f"{RD.fixed(old['C']['X'], 2)})")
    rows = lab.inspect()["spec"]["nodes"]["rows"]
    assert rows["C"][2] == "read (replaced since)" and rows["B"][2] == "read (dropped since)"
    assert rows["C"][3] == pytest.approx(9.0, abs=0.05)                  # the head still holds the old reading
    assert "head_nodes" not in lab.read("r3", [picks_at(lab, "Q", 40.0, 2.0, 6.0)], [])       # no node Q
    body = lab.edit("e2", 1, False, reading[1:], judgement(lab), "x", None, True, ["05"])
    assert body["status"] == "applied"
    assert lab.inspect()["spec"]["nodes"]["rows"]["C"][2] == "read"


def test_a_reading_no_node_is_named_for_is_stated_against_its_nearest_node_jointly(tmp_path):
    """v3p3 owner P1-2 as verify-7 corrected it: A2 (the logo's front corner, read beside the crown vertex A) printed
    only "outside the model's outline", and no text related it to A; an X-only "+0.77 mm (2.7 sd)" would be false
    precision (the two differ by chi2 18.1 for 3 dof, almost all of it in d). A stored reading no node is named for is
    stated against the node nearest it as built: the distance, and against the reading that node holds, each axis in
    sd of the difference (both pick parts and the camera shifts' difference), chi2 for 3 dof and that reading's X at
    its (d, y); in photo_read ("no_node", only near a read node), facet_inspect ("unused_readings", all) and a
    facet_edit receipt when it changed against the parent."""
    lab = make_lab(tmp_path, basis=ReadBasis())
    read_c(lab, 9.0)
    edits = [{"op": "set_node", "name": "C", "d_mm": None, "y_mm": None, "t": {"mode": "reading", "mm": None}}]
    assert lab.edit("e1", 0, False, edits, None, "x", OBJECT, True, ["05"])["status"] == "applied"
    out = lab.read("r2", [picks_at(lab, "C2", 23.6, -16.1, 9.3), picks_at(lab, "Q", 40.0, 2.0, 6.0)], [])
    c, c2 = lab.readings()["C"], lab.readings()["C2"]
    D = np.array([c2[k] - c[k] for k in ("d", "y", "X")])
    cov = np.array(c["cov_pick"]) + np.array(c2["cov_pick"])
    for f in set(c["camera_shift"]) | set(c2["camera_shift"]):
        s = np.array(c2["camera_shift"].get(f, [0.0] * 3)) - np.array(c["camera_shift"].get(f, [0.0] * 3))
        cov += np.outer(s, s)
    z = D / np.sqrt(np.diag(cov))
    chi2 = float(D @ np.linalg.solve(cov, D))
    x_at, sd_at = RD.conditional_x(c, c2["d"], c2["y"])
    fn = lab.fit_result(1).nodes["C"]
    dist = float(np.hypot(fn["d"] - c2["d"], fn["y"] - c2["y"]))
    want = (f"C2: no node C2; {dist:.2f} mm from node C as built; against the reading node C holds ({c['id']}): d "
            f"{RD.signed(D[0], 2)} ({RD.signed(z[0], 1)} sd), y {RD.signed(D[1], 2)} ({RD.signed(z[1], 1)} sd), X "
            f"{RD.signed(D[2], 2)} mm ({RD.signed(z[2], 1)} sd), chi2 {chi2:.1f} for 3 dof; at C2's d, y it gives X "
            f"{RD.fixed(x_at, 2)} +- {RD.fixed(sd_at, 2)} (C2 {RD.signed(c2['X'] - x_at, 2)} mm)")
    assert out["no_node"] == [want]                         # Q lies far from every read node: not listed here
    ins = lab.inspect()["unused_readings"]
    assert ins[0] == want and ins[1].startswith("Q: no node Q; ") and ins[1].endswith(
        "mm from node B as built (node B holds no reading: the plain arm's t)"), ins
    assert not any(w in json.dumps(ins) for w in REMEDY_WORDS)
    body = lab.edit("e2", 1, False, [set_t("A", FX.prada_default(), 0.2)], judgement(lab), "x", None, False, None)
    assert body["status"] == "built" and "unused_readings" not in body          # unchanged against the parent
    typed = [{"op": "set_node", "name": "C", "d_mm": 22.6, "y_mm": -16.3, "t": {"mode": "mm", "mm": 9.0}}]
    body = lab.edit("e3", 1, False, typed, None, "x", None, False, None)         # C typed: it holds no reading now
    assert body["unused_readings"][0].startswith("C2: no node C2; ") and body["unused_readings"][0].endswith(
        "mm from node C as built (node C holds no reading: typed t 9.00 mm)"), body["unused_readings"]


def test_finish_and_judgements_name_best_checkpoints_own_revision(tmp_path):
    """v3p3 e2e P3-1: two designs with one fitted spec (a node set again from a re-read with the same picks: only the
    reading's id differs) build one scene, so their checkpoints share a fingerprint; resolved by fingerprint, the
    earlier checkpoint named the later revision and the finish was refused for want of its compares. A checkpoint
    names the head applied AT it (state.head_of_checkpoint); the same-numbers re-read is not "replaced"."""
    lab = make_lab(tmp_path, basis=ReadBasis())
    read_c(lab, 9.0)
    edits = [{"op": "set_node", "name": "C", "d_mm": None, "y_mm": None, "t": {"mode": "reading", "mm": None}}]
    body = lab.edit("e1", 0, False, edits, None, "x", OBJECT, True, CAMERA_IDS)
    assert body["status"] == "applied"
    best, r1 = body["checkpoint"], body["revision"]
    out = lab.read("r2", [picks_at(lab, "C", 22.6, -16.3, 9.0)], [])   # the same picks again (e.g. after E_TIMEOUT)
    assert "head_nodes" not in out                                      # the same numbers: not "replaced"
    body = lab.edit("e2", r1, False, edits, judgement(lab, best=best), "x", None, True, ["05"])
    r2 = body["revision"]
    assert r2 != r1 and spec_sha256(lab.spec(r2)) == spec_sha256(lab.spec(r1))
    fps = [h["fingerprint"] for h in lab.state["heads"] if h["revision"] in (r1, r2)]
    assert len(fps) == 2 and fps[0] == fps[1] and lab.s.head["id"] != best
    assert S.head_of_checkpoint(lab.state, best, lab.fingerprint_of)["revision"] == r1
    assert S.head_by_fingerprint(lab.state, lab.fingerprint_of(best))["revision"] == r2      # the old rule's answer
    rec = S.check_judgement(lab.state, judgement(lab, best=best), fingerprint_of=lab.fingerprint_of)
    assert rec["revision"] == r1
    body = lab.finish("f1", "matched", best, "s", judgement(lab, best=best), 1)
    assert body["status"] == "refused" and 3 not in [m["precondition"] for m in body["missing"]], body


def test_readings_of_a_photo_read_the_sdk_timed_out_are_not_kept(tmp_path):
    """v3p3 safety P3-2: a photo_read the SDK timed out still stored its readings, and a later set_node mode reading
    used one Astra never saw. mark_unseen undoes what that call stored (a read removed, a replaced or dropped one put
    back; the log entries marked unseen); a call marked unseen before it stores keeps nothing."""
    lab = make_lab(tmp_path, basis=ReadBasis())
    lab.read("c1", [picks_at(lab, "Q", 40.0, 2.0, 6.0), picks_at(lab, "R", 50.0, 4.0, 7.0)], [])
    before = json.loads(json.dumps(lab.readings()))
    lab.read("c2", [picks_at(lab, "Q", 40.0, 2.0, 6.5), picks_at(lab, "S", 30.0, 0.0, 6.0)], ["R"])
    assert sorted(lab.readings()) == ["Q", "S"]
    lab.mark_unseen("c2")                                  # the SDK gave up after the readings were stored
    assert json.loads(json.dumps(lab.readings())) == before
    assert all(e["unseen"] for e in lab.state["reading_log"] if e["call_id"] == "c2")
    lab.mark_unseen("c3")                                  # ... and before: nothing is stored
    out = lab.read("c3", [picks_at(lab, "T", 35.0, 1.0, 6.0)], ["Q"])
    assert out["status"] == "ok" and json.loads(json.dumps(lab.readings())) == before
    assert lab.state["reading_log"][-1] == {"call_id": "c3", "id": out["read_id"], "action": "unseen",
                                            "names": ["T"], "drop": ["Q"]}


def test_pick_sheets_of_names_that_differ_only_in_case_are_two_files(tmp_path):
    """Reading names are case-sensitive ("Q" and "q" are two nodes); on a case-insensitive file system their pick
    sheets were one file, the second overwriting the first (review2-lab-e2-1 P2-2): each sheet's file carries its
    index in the call, so both are kept, sent and indexed."""
    lab = make_lab(tmp_path, basis=ReadBasis())
    out = lab.read("r1", [picks_at(lab, "Q", 40.0, 2.0, 6.0), picks_at(lab, "q", 50.0, 4.0, 7.0)], [])
    paths = [Path(p) for p in out["images"]]
    assert len({p.name.lower() for p in paths}) == 2 and all(p.is_file() for p in paths)
    digests = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    assert len(set(digests)) == 2 and [lab.state["images"][d]["reading"] for d in digests] == ["Q", "q"]
    assert lab.context_stubs(digests) == {} and out["stored"] == ["Q", "q"]


# --------------------------------------------------------------------------------------------- facet_edit
def test_facet_edit_fits_readings_and_builds_the_fitted_spec(tmp_path):
    """The edit flow (I6): apply_edits(readings) -> design (C's t its reading) -> fit_spec -> the fitted spec, which is
    the revision's spec, the build and what the worker applies; the receipt's fit lines count the reading; the
    compare is of the fitted head (its model edges)."""
    lab = make_lab(tmp_path, basis=ReadBasis())
    read_c(lab, 9.0)
    edits = [{"op": "set_node", "name": "C", "d_mm": None, "y_mm": None, "t": {"mode": "reading", "mm": None}}]
    body, *_ = parsed(call(tools_of(lab)["facet_edit"], edit_args(0, edits, obj=OBJECT, photos=["05"])))
    assert body["status"] == "applied", body
    rid = body["revision"]
    spec = lab.spec(rid)
    fit = lab.fit_result(rid)
    assert fit.nodes["C"]["source"] == "read" and spec["nodes"]["C"][2] == pytest.approx(9.0, abs=0.05)
    assert lab.s.built[-1] == spec and lab.build(rid).spec.sha256 == spec_sha256(spec)
    assert body["receipt"]["fit"][0].startswith("fit: chi2 ") and "from 1 readings" in body["receipt"]["fit"][0]
    assert body["diff"].startswith("~node C t 8.0->read ")
    assert lab.seeing.calls[-1]["model_edges"] == LB.capped_edges(BLD.model_edges(lab.build(rid)))


def test_facet_edit_receipt_order_and_crease_lines(lab):
    """The receipt (WP6_V3 §6): fit lines first (the fit report without its crease lines), then one line per built
    crease (the builder's shape and angle, then the fit's angle +- sd and certain / open), then the builder's
    warnings; then solved / crease_vs_head / diff_vs_head against the head. No margins block, no lean, no remedy text.
    The crease line is report.crease_line plus the fit's part, cut at LINE_MAX (the fit's part first)."""
    body = lab.edit("e1", 0, False, [set_t("A", FX.prada_default(), 0.6)], None, "x", OBJECT, False, None)
    assert body["status"] == "built", body
    rc = body["receipt"]
    assert list(rc)[:2] == ["fit", "creases"] and "margins" not in rc and "lean_deg" not in rc
    tb, fit = lab.build(body["revision"]), lab.fit_result(body["revision"])
    assert rc["fit"] == fit.report_lines(limit=LB.FIT_LINES_MAX, creases=False)
    assert rc["creases"] == LB.crease_lines(tb, fit, BLD.model_edges(tb)) == LB.crease_lines(tb, fit)
    line = next(ln for ln in rc["creases"] if ln.startswith("F2|F4 "))
    assert line.startswith(RP.crease_line(next(c for c in tb.tables["creases"] if c["regions"] == ["F2", "F4"])))
    assert re.search(r"; fit \+\d+\.\d \+- \d+\.\d°, certain$", line), line        # ° since v3p3-clean-lab
    border = next(ln for ln in rc["creases"] if ln.startswith("F4|base "))
    assert border.count("; border fit -") == 1 and "fit fold" not in border        # "fold": the bracket (v3p3 P3-9)
    keys = list(body)
    assert keys.index("receipt") < keys.index("solved") and "crease_vs_head" not in body
    assert "H_lo t 4.24 mm (head 5.75), K t 4.82 mm (head 5.00)" in body["solved"]      # t in mm (v3p3-clean-lab)
    assert no_remedy(json.dumps(body))
    f = {"deg": 16.12, "sd_deg": 3.8, "certain": True, "declared": "ridge", "dchi2": 2.4, "free_deg": 11.0,
         "free_sd_deg": 2.0, "held": False}
    assert LB.fit_crease_text(f, False) == "fit +16.1 +- 3.8°, certain (readings-only +11.0 +- 2.0°, cost chi2 +2.4)"
    assert LB.fit_crease_text({**f, "held": True, "deg": 2.5, "dchi2": 0.0}, True) == \
        "border fit +2.5° (held at the declared minimum), certain"
    assert LB.fit_crease_text({**f, "deg": -0.01, "certain": False, "declared": None}, False) == \
        "fit +0.0 +- 3.8°, open"


def test_crease_lines_keep_the_fits_part_and_say_where_a_border_folds_both_ways():
    """compose drops the highest-ranked parts first and never the rank-0 ones (the builder's shape and angle, the
    fit's angle, sd and certain / open); a 170-character cut of the tail lost the fit's whole part (review2-lab-e2-2
    P3-8). A facet|base border that folds both ways prints the table's shares over its face edges and the signed
    extremes of its model edges, so the builder's "ridge" and the fit's valley fold read as one border (review2-lab-
    e2-1 P2-4, the lab's part); the ramp's d is in the step clause."""
    parts = [("h" * 150, 0), ("; fit fold -2.3 +- 1.1", 0), (" at d 27.0 y 4.0", 3), (", certain", 0),
             (", the thickness bound holds A (the sd is without it)", 4),
             (" (readings-only -2.0 +- 1.0, cost +2.4)", 1)]
    assert LB.compose(parts, 300) == "".join(t for t, _r in parts)
    assert LB.compose(parts, 260) == "h" * 150 + "; fit fold -2.3 +- 1.1 at d 27.0 y 4.0, certain (readings-only " \
                                              "-2.0 +- 1.0, cost +2.4) ..."
    assert LB.compose(parts, 230) == "h" * 150 + "; fit fold -2.3 +- 1.1, certain (readings-only -2.0 +- 1.0, " \
                                              "cost +2.4) ..."
    assert LB.compose(parts, 200) == "h" * 150 + "; fit fold -2.3 +- 1.1, certain ..."
    assert LB.compose([("h" * 300, 0), ("; fit +1.0 +- 0.5", 0)], 260) == "h" * 260
    c = {"regions": ["shoulder", "base"], "kind": "crease", "shape": "ridge", "mean_dihedral_deg": 6.2,
         "warp_beside": True, "range_deg": [4.1, 9.8], "soft": True, "declared": "ridge",
         "step": {"height_mm": 0.8, "slope_deg": 21.8, "d_mm": [75.5, 76.8]},
         "ridge_share": 0.53, "valley_share": 0.47, "face_length_mm": 64.2, "length_mm": 70.0}
    f = {"pair": ["shoulder", "base"], "deg": -6.2, "sd_deg": 1.7, "at": [25.0, 6.0], "certain": True, "held": False,
         "declared": "ridge", "dchi2": 3.1, "free_deg": -6.0, "free_sd_deg": 1.7, "borders": 1, "bound": ["K"]}

    class Build:
        tables = {"creases": [c]}

    class Fit:
        creases = [f]
    edges = [{"pair": "shoulder|base", "shape": "flat", "deg": 0.6, "range": [-4.38, 9.8]}]
    line = LB.crease_lines(Build, Fit, edges)[0]
    assert line == ("shoulder|base ridge 6.2° (4.1-9.8) soft (proud 0.80 mm: a 21.8° ramp at d 75.5-76.8) declared "
                    "ridge, ridge along 53 % and valley along 47 % of its 64.2 mm (-4.4 to +9.8°); border fit -6.2 "
                    "+- 1.7°, certain (readings-only -6.0 +- 1.7°, cost chi2 +3.1) ..."), line   # the bound and (with
    #                       the units, v3p3-clean-lab) the place went, in compose's order ("border fit": v3p3 P3-9)
    assert len(line) <= LB.CREASE_LINE_MAX
    assert LB.mixed_text(c, (0.5, 9.8)) == ", ridge along 53 % and valley along 47 % of its 64.2 mm"   # one sign
    assert LB.mixed_text({**c, "valley_share": 0.03, "ridge_share": 0.97}, (-4.4, 9.8)) == ""          # < 5 %


def test_a_ramp_is_printed_once_and_snaps_are_explained(lab):
    """A facet standing 1 mm proud of the arm (a 26.6° ramp over the 2 mm border band): its W_BORDER_RAMP run is the
    crease line's step, printed there with its d and not again in the warnings (review2-lab-e2-2 P3-10); the spec's
    knot snaps are a sentence with the largest move, not a bare count."""
    z = FX.T_ZONE

    def node(n, d, y):
        return {"op": "set_node", "name": n, "d_mm": d, "y_mm": y, "t": {"mode": "mm", "mm": z + 1.0}}
    edits = [node("P0", 40.0, -2.0), node("P1", 60.0, -2.0), node("P2", 60.0, 2.0), node("P3", 40.03, 2.0),
             {"op": "set_facet", "label": "P", "poly": ["P0", "P1", "P2", "P3"], "plane": {"mode": "flat"},
              "soft": False}]
    design = G.apply_edits(SLAB, edits)[0]
    fit = FIT.fit_spec(design)
    tb = build_temple(fit.spec)
    assert [w["code"] for w in tb.warnings] == ["W_BORDER_RAMP"]
    rc = lab._receipt(tb, fit, BLD.model_edges(tb))
    assert rc["creases"][0].startswith("P|base flat 0.0° (proud 1.00 mm: a 26.6° ramp at d 40.0-60.0) undeclared; "
                                       "border fit +0.0 +- ")
    assert "warnings" not in rc
    assert rc["snaps"] == "1 knot or node value moved up to 0.03 mm onto a shared d or the outline edge (the spec's " \
                          "canonical form)"
    tb.warnings = tb.warnings + [{**tb.warnings[0], "d_mm": [62.0, 70.0], "height_mm": 0.5, "slope_deg": 14.0}]
    assert lab._receipt(tb, fit)["warnings"] == [                                                 # another run
        "W_BORDER_RAMP (steeper than 8°): P proud 0.50 mm: a 14.0° ramp at d 62.0-70.0"]


def test_receipt_texts_carry_units_and_words(lab):
    """v3p3-clean-lab (session2's receipts read as Astra would): the thickness has its unit and says which is the
    least ("2.78 at d 0.0 y 10.1 .. 7.88 at ..." had none); a border ramp under the W_BORDER_RAMP angle is in the
    crease line's step words, proud or sunk, in mm, with its d ("shoulder +0.81 7.7° d 75.5-76.7" had neither unit
    nor words), a point d printed once."""
    z = FX.T_ZONE

    def node(n, d, y, dt):
        return {"op": "set_node", "name": n, "d_mm": d, "y_mm": y, "t": {"mode": "mm", "mm": z + dt}}
    edits = [node("P0", 40.0, -2.0, 0.2), node("P1", 60.0, -2.0, 0.2), node("P2", 60.0, 2.0, 0.2),
             node("P3", 40.0, 2.0, 0.2),
             {"op": "set_facet", "label": "P", "poly": ["P0", "P1", "P2", "P3"], "plane": {"mode": "flat"},
              "soft": False}]
    design = G.apply_edits(SLAB, edits)[0]
    fit = FIT.fit_spec(design)
    tb = build_temple(fit.spec)
    assert not any(w["code"] == "W_BORDER_RAMP" for w in tb.warnings)
    rc = lab._receipt(tb, fit)
    ramp = next(r for r in RP.receipt(tb)["border_ramps"] if r["facet"] == "P")
    assert rc["border_ramps"] == f"P proud 0.20 mm: a {ramp['slope_deg']:.1f}° ramp at d 40.0-60.0", rc["border_ramps"]
    assert 5.0 < ramp["slope_deg"] < 8.0
    th = RP.receipt(tb)["thickness"]
    assert rc["thickness"] == (f"min {th['min_mm']:.2f} mm at d {th['min_at'][0]:.1f} y {th['min_at'][1]:.1f}; max "
                               f"{th['max_mm']:.2f} mm at d {th['max_at'][0]:.1f} y {th['max_at'][1]:.1f}")
    assert LB.ramp_text("F4", -0.42, 12.34, [55.0, 55.04]) == "F4 sunk 0.42 mm: a 12.3° ramp at d 55.0"


def test_fit_and_build_refusals_return_their_facts_only(lab, monkeypatch):
    """A declaration the readings and typed values contradict is the fit's E_FIT_SHAPE (stage fit; its facts:
    readings-only angle, cost, the values most against it); a builder refusal is the builder's message (stage
    build); neither has a remedy added (WP6_V3 P6), and neither makes a revision."""
    n = len(lab.state["revisions"])
    body = lab.edit("e1", 0, False, [{"op": "set_crease", "facets": ["F2", "F4"], "shape": "valley"}], None, "x",
                    OBJECT, False, None)
    assert body["status"] == "refused" and body["code"] == "E_FIT_SHAPE" and body["stage"] == "fit"
    assert body["message"].startswith("the declared crease shapes: they cost chi2 +") and "readings-only +6.6" in \
        body["message"] and no_remedy(body["message"]) and len(body["message"]) <= LB.FIT_REFUSAL_MAX
    real = LB.build_temple

    def refusing(spec):
        raise TempleError("E_TOO_THIN", "the temple is 0.41 mm thick at d 12.0 y 3.0 (t_min 0.8)", facet="F2")
    monkeypatch.setattr(LB, "build_temple", refusing)
    body = lab.edit("e2", 0, False, [set_t("C", FX.prada_default(), 0.5)], None, "x", None, True, None)
    monkeypatch.setattr(LB, "build_temple", real)
    assert body == {"status": "refused", "code": "E_TOO_THIN", "stage": "build", "edit": None,
                    "message": "the temple is 0.41 mm thick at d 12.0 y 3.0 (t_min 0.8)", "fields": {"facet": "F2"},
                    "parent": 0}
    assert len(lab.state["revisions"]) == n and lab.s.built == [lab.spec(0)]


def _corner_off_top(outline, dy=0.2):
    """A facet U-V-T behind the Prada fixture's facets with T dy mm off the top outline at d 66 (between its knots at
    d 60 and 70) and a steep edge U-T: the parse finds U-T crossing the top edge 0.002 mm from T's d (E_SHORT_EDGE,
    session2's first layout at node E, v3p3 e2e P1-1)."""
    yt = G._interp(canonical(FX.prada_default())["outline"]["top"], 66.0)
    nodes = [("T", 66.0, yt + dy, 5.5, outline), ("U", 65.9, 2.0, 5.0, None), ("V", 75.0, 4.0, 5.0, None)]
    return yt, [{"op": "set_node", "name": n, "d_mm": d, "y_mm": y, "t": {"mode": "mm", "mm": t}, "outline": o}
                for n, d, y, t, o in nodes] + [{"op": "set_facet", "label": "Fx", "poly": ["U", "V", "T"],
                                                 "plane": {"mode": "flat"}, "soft": False}]


def test_a_corner_off_the_outline_is_refused_with_its_facts_and_built_on_it_with_outline(lab):
    """v3p3 e2e P1-1 and P3-2 (verify-0 p7: the first Prada layout refused E_SHORT_EDGE at node E, 0.20 mm above the
    top outline, at stage "fit", with one remedy that looped when read as a move in d): the parse's refusal inside the
    fit is stage "spec" with its fields, and the lab states each node it names against the nearer outline edge
    (outline_facts: how far, no knot within snap_mm, set_node outline puts the knot there); with set_node outline top
    the same design builds (a note and the diff name the knot), and a variant arm says the same facts."""
    yt, edits = _corner_off_top(None)
    body = lab.edit("e1", 0, False, edits, None, "x", OBJECT, False, None)
    assert (body["status"], body["code"], body["stage"]) == ("refused", "E_SHORT_EDGE", "spec"), body
    assert body["fields"]["refs"] == ["node T"] and "at" in body["fields"]
    assert body["outline"] == [f"node T at d 66.00, y {yt + 0.2:.2f} lies 0.20 mm above the top edge, with no knot "
                               "of it within 0.05 mm; set_node outline top puts that edge's knot at the node"]
    assert not any(w in json.dumps(body["outline"]) for w in REMEDY_WORDS)
    _yt, on = _corner_off_top("top")
    body = lab.edit("e2", 0, False, on, None, "x", None, False, None)
    assert body["status"] == "built", body
    assert body["notes"][0] == f"outline.top has its knot at node T, d 66.00 y {yt + 0.2:.2f} (outline top)"
    assert "outline.top d 60.0-70.0 (up to 0.20 mm)" in body["diff"]
    assert G.outline_edge(lab.design(body["revision"]), 66.0, yt + 0.2) == "top"
    variants = [{"label": "off", "edits": edits}, {"label": "on", "edits": on}]
    body = lab.variants("v1", 0, variants, ["05"], None)
    by = {v["label"]: v for v in body["variants"]}
    assert (by["off"]["code"], by["off"]["stage"]) == ("E_SHORT_EDGE", "spec") and by["off"]["outline"][0].startswith(
        "node T at d 66.00")
    assert by["on"]["revision"] is not None and by["on"]["notes"][0].startswith("outline.top has its knot at node T")
    assert LB.outline_facts(canonical(FX.prada_default()), ["node Z", "a station", "node A"]) == [
        "node A at d 0.00, y 10.50 lies 0.00 mm below the top edge; set_node outline top puts that edge's knot at "
        "the node"]                                                    # A sits on the cap knot: no "no knot"


def test_the_receipt_states_deleted_crease_declarations_and_outline_bends(lab):
    """The 2026-10-13 blind stand-in (v3-standin-fix): delete_facet U dropped its declared creases with only a note
    ("crease L|U deleted with facet U"), and E's outline knot 0.97 mm off the bottom edge's line cut a notch under a
    note that said only where the knot was. facet_edit's receipt states both on lines of their own, first
    (deleted_creases: what went, what this list declared again, what the design no longer declares; outline_bends:
    mm off the line, the knots beside it, the turn), taken out of notes, which keep the knot note; a variant arm keeps
    them in its notes (it has no receipt)."""
    spec = canonical(FX.prada_default())
    bot = spec["outline"]["bottom"]
    d, y_line = spec["nodes"]["E"][0], G._interp(bot, spec["nodes"]["E"][0])        # E rides the bottom edge
    assert G.outline_edge(spec, d, spec["nodes"]["E"][1]) == "bottom"
    edits = [{"op": "delete_facet", "label": "F4"},
             {"op": "set_facet", "label": "F4", "poly": spec["facets"]["F4"]["poly"], "plane": {"mode": "flat"},
              "soft": False},
             {"op": "set_crease", "facets": ["F2", "F4"], "shape": "ridge"},
             {"op": "set_node", "name": "E", "d_mm": d, "y_mm": y_line - 0.6, "t": {"mode": "base", "mm": None},
              "outline": "bottom"}]
    body = lab.edit("e1", 0, False, edits, None, "x", OBJECT, False, None)
    assert body["status"] == "built", body
    rc = body["receipt"]
    assert list(rc)[:2] == ["deleted_creases", "outline_bends"]
    assert rc["deleted_creases"] == ("deleted crease declarations: F2|F4 ridge, F4|base valley (with facet F4, "
                                     "edits[0]); declared again in this edit list: F2|F4 ridge; not declared now: "
                                     "F4|base")
    after = G.canonical_design(lab.design(body["revision"]))["outline"]["bottom"]
    k = next(j for j, p in enumerate(after) if abs(p[0] - d) < 1e-9)
    lo, hi = after[k - 1][0], after[k + 1][0]
    turn = abs(np.degrees(np.arctan2(after[k + 1][1] - after[k][1], hi - d))
               - np.degrees(np.arctan2(after[k][1] - after[k - 1][1], d - lo)))
    assert rc["outline_bends"] == [f"outline.bottom bends at node E: its knot at d {d:.2f} y {y_line - 0.6:.2f} lies "
                                   f"0.60 mm below the line the edge ran on (y {y_line:.2f} there); between its knots at "
                                   f"d {lo:.2f} and {hi:.2f} the edge now turns {turn:.0f}° at the knot (it ran "
                                   "straight there)"]
    assert body["notes"] == [f"outline.bottom has its knot at node E, d {d:.2f} y {y_line - 0.6:.2f} (outline bottom)"]
    out = lab.variants("v1", 0, [{"label": "a", "edits": edits}, {"label": "b", "edits": []}], ["05"], None)
    arm = next(v for v in out["variants"] if v["label"] == "a")
    assert arm["notes"] == body["notes"][:1] + rc["outline_bends"] + [rc["deleted_creases"]]


def _strip_spec(virtual: bool) -> dict:
    """A facet RL on the slab with C and E on the bottom outline (d 20, 60) and B inside, or its bottom side through
    two corners beyond the outline (``virtual``)."""
    bot = canonical(FX.slab())["outline"]["bottom"]
    nodes = {"B": (40.0, 5.0, 6.0), "C": (20.0, G._interp(bot, 20.0), 6.0), "E": (60.0, G._interp(bot, 60.0), 6.0)}
    poly = ["B", "E", "C"]
    edits = [{"op": "set_node", "name": n, "d_mm": d, "y_mm": y, "t": {"mode": "mm", "mm": t}, "outline": None}
             for n, (d, y, t) in nodes.items()]
    if virtual:
        edits += [{"op": "set_node", "name": n, "d_mm": d, "y_mm": -20.0, "t": {"mode": "solve", "mm": None},
                   "outline": None} for n, d in (("V1", 30.0), ("V2", 50.0))]
        poly = ["B", "E", "V2", "V1", "C"]
    edits.append({"op": "set_facet", "label": "RL", "poly": poly, "plane": {"mode": "flat"}, "soft": False})
    return G.apply_edits(FX.slab(), edits)[0]


def test_crease_lines_name_the_plain_arm_a_face_leaves_along_the_outline(tmp_path):
    """The stand-in's rev 1: "RL|base valley 8.7° (sunk 1.62 mm: a 39.1° ramp at d 21.9-56.4)" was a strip of plain
    arm up to 4.9 mm wide under RL's chord C-E, found only in the render. The facet|base crease line (facet_inspect,
    facet_edit) and a variant arm's creases name it with geometry.strip_text, the fit's part kept whole beside it; a
    face whose bottom side runs through corners beyond the outline (no refusal) meets the outline and names none."""
    lab = make_lab(tmp_path, spec=_strip_spec(False))
    tb = lab.build(0)
    strips = G.outline_strips(tb)
    want = G.strip_text("RL", strips["RL"])
    assert want.startswith("RL leaves plain arm up to 1.0") and "between its straight side E-C and the curved bottom" \
        " outline at d 2" in want
    line = next(c for c in lab.inspect()["receipt"]["creases"] if c.startswith("RL|base"))
    assert f"; {want}" in line and line.endswith(("certain", "open", "flat within 2 sd")), line
    assert len(line) <= LB.CREASE_LINE_MAX + len(want) + 2
    body = lab.edit("e1", 0, False, [], None, "x", OBJECT, False, None)    # facet_edit's receipt: the same line
    assert next(c for c in body["receipt"]["creases"] if c.startswith("RL|base")) == line
    virtual = _strip_spec(True)
    body = lab.edit("e2", 0, True, G.spec_to_edits(virtual), None, "x", OBJECT, False, None)
    assert body["status"] == "built", body
    assert not any("leaves plain arm" in c for c in body["receipt"]["creases"])
    out = lab.variants("v1", 0, [{"label": "chord", "edits": [set_t("B", lab.design(0), 0.4)]},
                                 {"label": "beyond", "edits": G.spec_to_edits(virtual)}], ["05"], None)
    by = {v["label"]: v for v in out["variants"]}
    assert "RL|base" in by["chord"]["creases"] and "(RL leaves plain arm up to" in by["chord"]["creases"]
    assert "leaves plain arm" not in by["beyond"]["creases"]


def test_variant_arms_state_their_creases_move_ramps_and_edges_against_the_head(lab):
    """v3p3 astra P2-2 (verify-3: vs_head_mm 0.60 for an arm whose tip edge met the outline 7.9 mm back, its valleys
    8.37 mm from the head's), owner P3-1 (a 3.2 mm proud shoulder read "shoulder|base ridge 9.7°") and astra P3-4
    (a crease that appears was named nowhere): each arm with a model of its own says how far its ridges and valleys
    lie from the head's (crease_shift; vs_head_mm stays the surface / outline measure), a facet|base step of 0.5 mm
    or more in its crease string, and the creases that appear or go flat (edges_vs_head); facet_edit says the latter
    against the head too, while crease_vs_head stays the ridge <-> valley flips."""
    spec = FX.prada_default()
    z = FX.T_ZONE
    proud = [{"op": "set_node", "name": n, "d_mm": d, "y_mm": y, "t": {"mode": "mm", "mm": z + 1.0}, "outline": None}
             for n, d, y in (("P0", 66.0, -1.0), ("P1", 74.0, -1.0), ("P2", 74.0, 3.0), ("P3", 66.0, 3.0))]
    proud.append({"op": "set_facet", "label": "P", "poly": ["P0", "P1", "P2", "P3"], "plane": {"mode": "flat"},
                  "soft": False})
    body = lab.variants("v1", 0, [{"label": "proud", "edits": proud}, {"label": "deep", "edits": [set_t("C", spec,
                                                                                                         1.0)]}],
                        ["05"], None)
    by = {v["label"]: v for v in body["variants"]}
    for lb in ("proud", "deep"):
        d = lab.delta(0, by[lb]["revision"])
        assert by[lb]["vs_head_mm"] == round(d["value_mm"], 2)
        assert by[lb].get("creases_vs_head_mm") == (LB.crease_shift(d, True) or None)
        edges = LB.edge_changes(lab.build(0), lab.build(by[lb]["revision"]))
        assert by[lb].get("edges_vs_head") == ("; ".join(edges) or None)
    assert "valley: none here" not in by["deep"]["creases_vs_head_mm"] and "ridge " in by["deep"]["creases_vs_head_mm"]
    tb = lab.build(by["proud"]["revision"])
    steps = [c for c in tb.tables["creases"] if c["kind"] == "crease" and c.get("step")
             and abs(c["step"]["height_mm"]) >= LB.RAMP_BRIEF_MM]
    assert steps and [c["regions"] for c in steps] == [["P", "base"]]       # the facet stands 1 mm proud
    st = steps[0]["step"]
    assert f"P|base {steps[0]['shape']} " in by["proud"]["creases"] and \
        f" ({RP.ramp_clause(st['height_mm'], st['slope_deg'], st.get('d_mm'))})" in by["proud"]["creases"]
    assert re.search(r"P\|base \w+ [\d.]+° \(proud \d\.\d\d mm: a [\d.]+° ramp at d [\d.]+-[\d.]+\)",
                     by["proud"]["creases"]), by["proud"]["creases"]
    # crease_shift's words on a delta of its own (Lab.delta orders the pair by revision: head_first)
    delta = {"creases": {"ridge": {"mm": 0.104, "length_mm": [80.0, 81.0]},
                         "valley": {"mm": None, "length_mm": [0.0, 12.34]}}}
    assert LB.crease_shift(delta) == "ridge 0.10, valley: none in the head (12.3 mm here)"
    assert LB.crease_shift(delta, head_first=False) == "ridge 0.10, valley: none here (the head's 12.3 mm)"
    assert LB.crease_shift({"creases": {"ridge": {"mm": 0.0, "length_mm": [0.0, 0.0]},
                                        "valley": {"mm": 8.3676, "length_mm": [20.0, 21.0]}}}) == "valley 8.37"


def test_edge_changes_name_a_crease_that_appears_or_goes_flat():
    """edge_changes (v3p3 astra P3-4): pairs both builds have that are flat in the head and a ridge or valley here, or
    the other way round, in report.crease_line's angles (deg_round: a flat 1.97 prints 1.9); flips stay
    crease_changes' (the T5 rehearsal checks crease_vs_head states exactly them)."""
    def build(**shapes):
        class Tb:
            tables = {"creases": [{"kind": "crease", "regions": list(k.split("_")), "shape": s, "mean_dihedral_deg": v}
                                  for k, (s, v) in shapes.items()]}
        return Tb
    head = build(shoulder_base=("flat", 1.23), lower_shoulder=("valley", 4.3), crown_logo=("ridge", 17.3),
                 logo_nose=("valley", 3.6))
    arm = build(shoulder_base=("ridge", 9.7), lower_shoulder=("flat", 1.97), crown_logo=("valley", 2.1),
                logo_nose=("valley", 3.1))
    assert LB.edge_changes(head, arm) == ["lower|shoulder valley 4.3° -> flat 1.9° (the edge goes flat)",
                                          "shoulder|base flat 1.2° -> ridge 9.7° (an edge appears)"]
    assert LB.crease_changes(head, arm) == ["crown|logo ridge 17.3° -> valley 2.1°"]


def test_a_crease_on_a_solved_corner_names_that_corners_reading():
    """v3p3 owner P2-3 (verify-12): mode solve drops the node's own reading; a flat crown carried its plane 12 mm into
    K (-6.8 sd off K's reading), a declared crown|logo ridge was then held at the 2.5° minimum and an undeclared one
    flipped to a valley, with nothing tying either to K. A crease whose shared corner the fit solved while a stored
    reading of its name lies off it names that corner with the first clause of its W_FIT_OFF_READING (in the crease
    line, rank 1, and in crease_vs_head); none for a read corner, a facet|base pair or no warning."""
    from types import SimpleNamespace
    tb = SimpleNamespace(spec=SimpleNamespace(facets={"crown": {"poly": ["A", "K", "E", "H"]},
                                                      "logo": {"poly": ["A", "C", "K"]},
                                                      "lower": {"poly": ["C", "D", "K"]}}),
                         tables={"creases": [{"kind": "crease", "regions": ["crown", "logo"], "shape": "ridge",
                                              "mean_dihedral_deg": 2.5, "declared": "ridge"}]})
    msg = "solved K 6.15: its reading 7.72 +- 0.29 (-5.4 sd)"
    fit = SimpleNamespace(nodes={"K": {"source": "solve"}, "A": {"source": "read"}, "C": {"source": "read"}},
                          warnings=[{"code": "W_FIT_OFF_READING", "node": "K", "kind": "solve", "message": msg},
                                    {"code": "W_FIT_OFF_READING", "node": "A", "kind": "position",
                                     "message": "node A at d 1.00, y 10.00: its reading at d 0.40, y 10.13 (0.61 mm "
                                                "away)"}],
                          creases=[{"pair": ["crown", "logo"], "deg": 2.5, "sd_deg": 5.3, "held": True,
                                    "declared": "ridge", "state": "open", "dchi2": 0.7, "free_deg": -2.1,
                                    "free_sd_deg": 5.3}])
    assert LB.solved_corners_text(tb, fit, ("crown", "logo")) == f" (corner K: {msg})"
    assert LB.solved_corners_text(tb, fit, ("crown", "base")) == "" and LB.solved_corners_text(tb, None,
                                                                                                ("crown", "logo")) == ""
    line = LB.crease_lines(tb, fit)[0]
    assert line == f"crown|logo ridge 2.5° declared ridge; fit +2.5° (held at the declared minimum), open " \
                   f"(readings-only -2.1 +- 5.3°, cost chi2 +0.7) (corner K: {msg})", line
    head = SimpleNamespace(tables={"creases": [{"kind": "crease", "regions": ["crown", "logo"], "shape": "valley",
                                                "mean_dihedral_deg": 2.1}]})
    assert LB.crease_changes(head, tb, fit) == [f"crown|logo valley 2.1° -> ridge 2.5° (corner K: {msg})"]
    fit.nodes["K"]["source"] = "read"                                    # a read corner: the fit weighs its reading
    assert LB.solved_corners_text(tb, fit, ("crown", "logo")) == ""


def test_the_object_note_rule_in_facet_edit(lab):
    """E_OBJECT (state.check_object, WP6_V3 §6): a facet_edit without a note while none exists is refused, before any
    fit or build; a note is recorded with its design's layout (facet_inspect shows it); a later change of t needs
    none; a new layout needs a new one; a note too long is E_ARGS."""
    tl = tools_of(lab)
    n = len(lab.state["revisions"])
    body, *_ = parsed(call(tl["facet_edit"], edit_args(0, [set_t("C", FX.prada_default(), 0.5)], apply=False)))
    assert body == {"status": "refused", "code": "E_OBJECT", "message": S.OBJECT_REFUSAL}
    assert len(lab.state["revisions"]) == n and S.object_text(lab.state) is None
    body, *_ = parsed(call(tl["facet_edit"], edit_args(0, [set_t("C", FX.prada_default(), 0.5)], obj=OBJECT,
                                                       apply=False)))
    assert body["status"] == "built" and body["object"] == "recorded" and S.object_text(lab.state) == OBJECT
    body, *_ = parsed(call(tl["facet_edit"], edit_args(body["revision"], [set_t("C", FX.prada_default(), 0.9)],
                                                       apply=False)))
    assert body["status"] == "built" and "object" not in body
    split = [{"op": "set_node", "name": "M", "d_mm": 30.0, "y_mm": 0.0, "t": {"mode": "solve", "mm": None}}]
    body, *_ = parsed(call(tl["facet_edit"], edit_args(0, split, apply=False)))
    assert body["code"] == "E_OBJECT"
    with pytest.raises(S.LabRefusal, match="object must be text of 1..3000 characters") as e:
        lab.edit("x", 0, False, split, None, "x", "x" * (S.OBJECT_MAX + 1), False, None)
    assert e.value.code == "E_ARGS"
    assert lab.inspect()["object"] == OBJECT


def test_a_judgement_given_is_stated_when_not_stored_or_when_the_call_is_refused(lab):
    """A judgement given is recorded before the call can be refused; a retry's judgement (none due) is not stored,
    and the result says so (state.judgement_not_due), refusals raised (E_OBJECT, E_STATE) or returned (E_EDIT) say
    "judgement of cmpX stored" (review2-lab-e2-1 P3-1, review2-lab-e2-2 P3-5: a resent judgement with another
    best_checkpoint was dropped silently); a call with no judgement, or a built one with its judgement stored, says
    nothing."""
    tl = tools_of(lab)
    spec = FX.prada_default()
    c1 = parsed(call(tl["photo_compare"], compare_args(["05"])))[0]["compare_id"]
    first = judgement(lab)
    body = parsed(call(tl["facet_edit"], edit_args(0, [set_t("C", spec, 0.5)], judge=first, apply=False)))[0]
    assert body["code"] == "E_OBJECT" and body["judgement"] == f"judgement of {c1} stored"
    again = {**first, "overall": "better"}
    body = parsed(call(tl["facet_edit"], edit_args(0, [set_t("C", spec, 0.5)], judge=again, obj=OBJECT, apply=False),
                       "call_retry"))[0]
    assert body["status"] == "built" and body["judgement"] == (
        f"no judgement was due: the latest compare, {c1}, was judged already by the judgement a facet_edit call "
        f"carried (overall: mixed; best_checkpoint: {first['best_checkpoint']}); this judgement of {c1} was not "
        "stored (one judgement per compare; the next compare makes one due)")
    assert [j["overall"] for j in lab.state["judgements"]] == ["mixed"]
    c2 = parsed(call(tl["photo_compare"], compare_args(["05"])))[0]["compare_id"]
    body = parsed(call(tl["facet_edit"], edit_args(0, [{"op": "delete_node", "name": "Z"}], judge=judgement(lab),
                                                   apply=False)))[0]
    assert body["code"] == "E_EDIT" and body["judgement"] == f"judgement of {c2} stored"
    c3 = parsed(call(tl["photo_compare"], compare_args(["05"])))[0]["compare_id"]
    two = [{"label": "A", "edits": []}, {"label": "B", "edits": []}]
    body = parsed(call(tl["facet_variants"], {"parent_revision": 0, "photos": ["B1"], "judgement": judgement(lab),
                                              "variants": two}))[0]
    assert body["code"] == "E_STATE" and body["judgement"] == f"judgement of {c3} stored"
    assert "a full photo_compare of it, or photos without it" in body["message"]          # both ways out
    body = parsed(call(tl["facet_edit"], edit_args(0, [set_t("C", spec, 0.7)], apply=False)))[0]
    assert body["status"] == "built" and "judgement" not in body


def test_a_fit_or_build_that_raises_is_the_calls_refusal_and_inspect_names_the_notes_layout(lab, monkeypatch):
    """Any exception of the fit or the build other than a TempleError is this call's refusal with its type and text
    (E_FIT / E_BUILD at stage fit / build), the object note given is still "recorded", the state keeps it in
    host_errors, and in facet_variants only that arm is refused (review2-lab-e2-1 P3-2: the SDK's generic error lost
    the result). facet_inspect says when the latest note was written for another layout than the head's (review2-
    lab-e2-1 P3-3, review2-lab-e2-2 P3-1)."""
    tl = tools_of(lab)
    spec = FX.prada_default()
    real = FIT.fit_spec

    def failing(design, readings=None):
        if "M" in design["nodes"]:
            raise RuntimeError("nnls: no convergence")
        return real(design, readings=readings)
    monkeypatch.setattr(LB.FIT, "fit_spec", failing)
    split = [{"op": "set_node", "name": "M", "d_mm": 30.0, "y_mm": 0.0, "t": {"mode": "solve", "mm": None}}]
    body = parsed(call(tl["facet_edit"], edit_args(0, split, obj="NOTE: a layout with M", apply=False)))[0]
    assert body == {"status": "refused", "code": "E_FIT", "stage": "fit", "edit": None,
                    "message": "the fit raised RuntimeError: nnls: no convergence", "parent": 0, "object": "recorded"}
    assert lab.state["host_errors"][-1] == {"call_id": "call_x", "stage": "fit", "message": body["message"]}
    ins = lab.inspect()
    assert ins["object"] == "NOTE: a layout with M" and ins["object_layout"] == \
        "the note (given in a facet_edit of revision 0 that made no revision) was written for another layout; the " \
        "head: -node M"                                       # the revision, never the SDK call id (v3p3 P3-5)
    variants = [{"label": "A", "edits": [set_t("C", spec, 0.5)]}, {"label": "M", "edits": split}]
    body = parsed(call(tl["facet_variants"], {"parent_revision": 0, "variants": variants, "photos": ["05"],
                                              "judgement": None}))[0]
    by = {v["label"]: v for v in body["variants"]}
    assert (by["M"]["stage"], by["M"]["code"]) == ("fit", "E_FIT") and by["A"]["revision"] is not None
    assert lab.state["variants"][-1]["rendered"] == ["A"]
    monkeypatch.setattr(LB, "build_temple", lambda s: (_ for _ in ()).throw(np.linalg.LinAlgError("singular")))
    body = lab.edit("e3", 0, False, [set_t("C", spec, 0.9)], None, "x", OBJECT, False, None)
    assert (body["code"], body["stage"], body["object"]) == ("E_BUILD", "build", "recorded")
    assert body["message"] == "the build raised LinAlgError: singular"
    assert "object_layout" not in lab.inspect()                     # the latest note is for the head's layout


def test_a_typed_t_off_its_reading_is_a_fact_in_the_fit_lines(tmp_path):
    """W_FIT_OFF_READING: a t typed on a node that has a stored reading of its name, more than 2 sd from it, is
    printed as a fact in the receipt's fit lines ("typed C 7.00: its reading 9.00 +- 0.58 (-3.4 sd)"); 1.7 sd off
    is not."""
    lab = make_lab(tmp_path, basis=ReadBasis())
    read_c(lab, 9.0)
    sd = lab.readings()["C"]["sd_X"]
    assert 0.4 < sd < 0.8
    body = lab.edit("e1", 0, False, [set_t("C", FX.prada_default(), -1.0)], None, "x", OBJECT, False, None)
    line = next(ln for ln in body["receipt"]["fit"] if ln.startswith("W_FIT_OFF_READING"))
    assert line == f"W_FIT_OFF_READING: typed C 7.00: its reading 9.00 +- {sd:.2f} ({-2.0 / sd:+.1f} sd)", line
    body = lab.edit("e2", 0, False, [set_t("C", FX.prada_default(), 0.0)], None, "x", None, False, None)
    assert not any(ln.startswith("W_FIT_OFF_READING") for ln in body["receipt"]["fit"])
    assert no_remedy(line)


def test_solved_moves_flips_and_diff_against_the_head(lab):
    """Against the CURRENT head: solved (fitted nodes with no reading or typed value whose t moved >= 0.1 mm),
    crease_vs_head (a crease built with the other shape) and diff_vs_head (the parent is not the head), in that
    order after the receipt."""
    spec = FX.prada_default()
    keep = [{"op": "delete_crease", "facets": ["F2", "F4"]}, {"op": "delete_crease", "facets": ["F4", "base"]},
            {"op": "delete_crease", "facets": ["F2", "F3"]}]
    body = lab.edit("e1", 0, False, keep, None, "x", OBJECT, True, ["05"])
    assert body["status"] == "applied" and "solved" not in body and "crease_vs_head" not in body
    r1 = body["revision"]
    body = lab.edit("e2", r1, False, [set_t("C", spec, -4.0)], judgement(lab), "x", None, False, None)
    assert body["status"] == "built", body
    assert body["crease_vs_head"] == "r1: F2|F3 ridge 8.7° -> valley 3.5°", body["crease_vs_head"]
    body = lab.edit("e3", 0, False, [set_t("A", spec, 0.6)], None, "x", None, False, None)
    keys = list(body)
    assert keys.index("solved") < keys.index("diff_vs_head") and body["diff_vs_head"].startswith("r1: ~node A t ")
    assert "+crease F2|F3 ridge" in body["diff_vs_head"]
    assert "K t 4.82 mm (head 5.00)" in body["solved"]


# --------------------------------------------------------------------------------------------- photo_compare
def test_photo_compare_sends_the_heads_model_edges_and_prints_v3_texts(lab):
    """photo_compare (I6 / WP6_V3 §5-6): seeing gets passes native_linear, the head's model edges (build.model_edges by
    identity), its hinge_z and opening; per photo the lab's text: view, IoU, px/mm, box and k, a side view's outline
    offsets for the temple whose outer face it sees (seeing's text: bottom measured, top not), the charted points; no
    top / rear offsets, no start column, no outline line, no camera note."""
    t = tools_of(lab)["photo_compare"]
    body, _f, images, text = parsed(call(t, compare_args(["05", "03", "06"], points=[
        {"photo": "05", "points": [[100.0, 120.0]]}])))
    assert body["status"] == "ok" and body["compare_id"].startswith("cmp") and len(images) == 3
    c = lab.seeing.calls[-1]
    assert c["passes"] == ["native_linear"] and c["rig"] == MATCHED_RIG and c["hinge_z_mm"] == -10.6
    assert c["opening"] == lab.spec(0)["hinge"] and c["zoom"] is None
    assert c["model_edges"] == LB.capped_edges(BLD.model_edges(lab.build(0))) and c["model_edges"]
    t05 = body["photos"]["05"]
    assert t05.startswith("side (rows photo / render / overlay); temple IoU 0.912, 4.03 px/mm in this image (8.17 "
                          "native), box [20,30,300,210] k 0.493 | outline of the charted temple, photo minus model")
    assert "bottom (+ = the photo's edge lower) at d: 10 +0.1, 20 covered, 30 -0.4" in t05 and "mirrored" not in t05
    assert "| points (100,120) d 20.00 y 1.50 t " in t05
    for pid in ("03", "06"):
        assert body["photos"][pid] == f"{KINDS[pid]} (rows photo / render / overlay); temple IoU 0.912, 4.03 px/mm " \
                                      "in this image (8.17 native), box [20,30,300,210] k 0.493"
    for gone in ("offsets", "start", "outline: common", GC.CAMERA_NOTE[:30], "camera error", "depths"):
        assert gone not in text, gone
    rec = lab.state["compares"][-1]
    assert rec["photos"] == rec["stacked"] == ["05", "03", "06"] and "outline" not in rec
    assert S.pending_compare(lab.state)["compare_id"] == body["compare_id"]


def test_zooms_are_checked_shown_and_judged(lab):
    """zoom [{photo, box, geometry}] (<= 3): photo ids become basis references for seeing, boxes are native px inside
    the photo; each zoom's text is seeing's naming the lab's photo id (its trailing compare_id line left out: the result
    carries it); photos [] with a zoom is a zoom-only compare whose zoomed photos are judged, and precondition 3 is
    left to the full compares ("stacked"). Every compare passes seeing photo_names = every photo with a camera by the
    lab's id (v3p3-fix-seeing: the images were titled "reference-05 photo", "black-side photo"), and the lab no longer
    rewrites seeing's zoom text."""
    tl = tools_of(lab)
    z = [{"photo": "05", "box": [40, 60, 200, 140], "geometry": True}, {"photo": "B1", "box": [0, 0, 64, 40],
                                                                        "geometry": False}]
    body, _f, images, _t = parsed(call(tl["photo_compare"], compare_args(["05"], zoom=z)))
    assert body["status"] == "ok" and len(images) == 3 and list(body["photos"]) == ["05"]
    assert lab.seeing.calls[-1]["zoom"] == [{"rid": "reference-05", "box": [40.0, 60.0, 200.0, 140.0],
                                             "geometry": True},
                                            {"rid": "black-side", "box": [0.0, 0.0, 64.0, 40.0], "geometry": False}]
    names = {f"reference-{p}": p for p in PHOTO_IDS} | {"black-side": "B1"}
    assert lab.photo_names() == names and all(c["photo_names"] == names for c in lab.seeing.calls)
    assert body["zoom"][0].startswith("zoom 1 on 05 (side, target): native box [40,60,200,140] at k 2.00")
    assert body["zoom"][1].startswith("zoom 2 on B1 (") and "compare_id" not in " ".join(body["zoom"])
    rec = lab.state["compares"][-1]
    assert rec["photos"] == ["05", "B1"] and rec["stacked"] == ["05"] and rec["zoom"][1]["photo"] == "B1"
    assert [p["photo"] for p in judgement(lab)["photos"]] == ["05", "B1"]
    body, *_ = parsed(call(tl["facet_edit"], edit_args(0, [set_t("C", FX.prada_default(), 0.4)], obj=OBJECT,
                                                       apply=False, judge=judgement(lab))))
    assert body["status"] == "built"
    body, _f, images, _t = parsed(call(tl["photo_compare"], compare_args([], zoom=z[:1])))
    assert body["status"] == "ok" and body["photos"] == {} and len(images) == 1
    assert lab.seeing.calls[-1]["refs"] == [] and lab.state["compares"][-1]["photos"] == ["05"]
    for zoom, words in (([{"photo": "05", "box": [0, 0, 999, 10], "geometry": False}],
                         "inside the native 320 x 240 photo 05"),
                        ([{"photo": "05", "box": [10, 10, 14, 40], "geometry": False}], "at least 8 x 8"),
                        ([{"photo": "B2", "box": [0, 0, 20, 20], "geometry": False}], "photo_image only"),
                        ([{"photo": "05", "box": [0, 0, 20, 20], "geometry": False}] * 4, "at most 3")):
        with pytest.raises(S.LabRefusal, match=re.escape(words)):
            lab.compare("x", ["05"], None, zoom, None)
    with pytest.raises(S.LabRefusal, match="shows nothing"):
        lab.compare("x", [], None, None, None)


def test_compare_states_points_not_charted_the_default_set_backdrop_and_colour(lab):
    """points_px are charted on the photos a compare shows in full: points on a photo shown in a zoom only, or not
    shown, are named in "points_not_charted" and the compare (its zooms) is still made (review2-lab-e2-2 P3-4: a
    zoom-only compare was refused with a false message and its zooms lost); with photos null the set is seeing's,
    chosen at compare time, so points need named photos (E_ARGS, both ways out; review2-lab-e2-1 P3-4: they were
    dropped silently), and the result names the target photos the default set left out (review2-lab-e2-2 P3-3). A
    render whose model shows >= 10 % in the backdrop colour says so (review2-lab-e2-2 P3-2); a zoom of a photo of
    another colour names its colour, not seeing's "variant" (P3-10)."""
    tl = tools_of(lab)
    body = parsed(call(tl["photo_compare"], compare_args()))[0]
    assert body["left_out"] == "03, 04, 06: the default set is the target photos whose temple shows at least 40,000 " \
                               "native px"
    with pytest.raises(S.LabRefusal, match=re.escape("name the photos, or points_px null")) as e:
        lab.compare("x", None, None, None, [{"photo": "05", "points": [[1.0, 2.0]]}])
    assert e.value.code == "E_ARGS"
    z = [{"photo": "05", "box": [40, 60, 200, 140], "geometry": False}]
    body, _f, images, _t = parsed(call(tl["photo_compare"], compare_args([], zoom=z, points=[
        {"photo": "05", "points": [[100.0, 120.0]]}])))
    assert body["status"] == "ok" and len(images) == 1 and lab.seeing.calls[-1]["points_px"] is None
    assert body["points_not_charted"] == "points_px are charted on the photos a compare shows in full: 05 (shown in " \
                                         "a zoom only)"
    body = parsed(call(tl["photo_compare"], compare_args(["05"], points=[{"photo": "05", "points": [[100.0, 120.0]]},
                                                                         {"photo": "02", "points": [[5.0, 5.0]]}])))[0]
    assert "| points (100,120) d 20.00" in body["photos"]["05"] and "left_out" not in body
    assert body["points_not_charted"].endswith(": 02 (not in this compare)")
    assert list(lab.seeing.calls[-1]["points_px"]) == ["reference-05"]
    lab.seeing.records["reference-03"] = {"render_backdrop_share": {"native_linear": 0.515}}
    lab.seeing.records["reference-01"] = {"render_backdrop_share": {"native_linear": 0.08}}
    lab.seeing.records["reference-04"] = {"render_backdrop_share": {"native_linear": 0.15}}
    body = parsed(call(tl["photo_compare"], compare_args(["03", "01", "04"])))[0]
    assert body["photos"]["03"].endswith(" | render: 52% of the model's pixels show in the backdrop's colour (the "
                                         "outline marks the model's edge)")
    assert "backdrop" not in body["photos"]["01"]
    # the clause is explained once a result (v3p3 astra P3-6: repeated in every such photo of every compare)
    assert body["photos"]["04"].endswith(" | render: 15% of the model's pixels show in the backdrop's colour")
    body = parsed(call(tl["photo_compare"], compare_args(["05"], zoom=[{"photo": "B1", "box": [0, 0, 64, 40],
                                                                        "geometry": False}])))[0]
    assert body["zoom"][0].startswith("zoom 1 on B1 (side, black): native box [0,0,64,40]")


def test_variant_arm_fit_lines_name_each_warning_once_and_the_solved_moves():
    """fit_brief: the fit and misfit lines, each warning code with its subjects once (a node's value and position
    W_FIT_OFF_READING are one item), and the solved nodes that moved against the head's fit (review2-lab-e2-2 P3-9:
    "W_FIT_OFF_READING L, W_FIT_OFF_READING L" and no "K 6.53 (head 7.72)")."""
    class Fit:
        warnings = [{"code": "W_FIT_PRIOR", "node": "K"}, {"code": "W_FIT_OFF_READING", "node": "K"},
                    {"code": "W_FIT_OFF_READING", "node": "L"}, {"code": "W_FIT_OFF_READING", "node": "L"},
                    {"code": "W_FACE_NOT_FLAT", "face": "crown"}]
        nodes = {"K": {"source": "solve", "t": 6.53}, "L": {"source": "read", "t": 4.6}, "M": {"source": "solve",
                                                                                         "t": 3.0}}

        @staticmethod
        def report_lines(limit=12, creases=True):
            return ["fit: chi2 9.1, dof 4", "camera turn per fit: 05 +0.10 +- 0.90", "misfit beyond 1 sd: L +0.40 mm"]
    assert LB.fit_brief(Fit, {"K": {"t": 7.72}, "M": {"t": 3.02}}) == [
        "fit: chi2 9.1, dof 4", "misfit beyond 1 sd: L +0.40 mm",
        "warnings: W_FIT_PRIOR K; W_FIT_OFF_READING K, L; W_FACE_NOT_FLAT crown", "solved: K t 6.53 mm (head 7.72)"]
    assert LB.fit_brief(Fit)[-1].startswith("warnings: ")

    # v3p3 astra P2-2 (verify-3): a bracket of a typed t is where its distance from the reading decides; the arm read
    # "W_FIT_OFF_READING K" where facet_edit said "-4.5 sd", "W_FIT_OFF_READING E" where it said "(8.27 mm away)", and
    # "W_FACE_NOT_FLAT crown" with no chi2. Each such warning keeps its message's first clause (first_clause: a "; "
    # inside brackets closes the bracket), one line each; the others stay code and subject
    class Facts(Fit):
        warnings = [{"code": "W_FIT_SOLVED", "node": "L0", "message": "node L0: no reading or typed value; X 70.57"},
                    {"code": "W_FIT_OFF_READING", "node": "K", "kind": "typed",
                     "message": "typed K 6.40: its reading 7.72 +- 0.29 (-4.5 sd)"},
                    {"code": "W_FIT_OFF_READING", "node": "E", "kind": "position",
                     "message": "node E at d 85.00, y 8.66: its reading at d 76.75 +- 1.24, y 9.16 +- 0.32 (8.27 mm "
                                "away; its X carried +0.10 mm along the faces to the node)"},
                    {"code": "W_FACE_NOT_FLAT", "face": "crown",
                     "message": "face crown: its corners do not fit one flat face (chi2 10.5 for 1 extra corner, 3.2 "
                                "sd): each corner from the plane of the other three: A -5.28 +- 1.63 mm, K +2.01 +- "
                                "0.62, E -3.34 +- 1.03, H (others nearly in line); the flat fit: E +0.41 mm (1.1 sd); "
                                "ways (unranked): warp (one face, no line), a fold (two faces)"}]
    out = LB.fit_brief(Facts, {"K": {"t": 7.72}, "M": {"t": 3.02}})
    assert out[2:] == [
        "warnings: W_FIT_SOLVED L0",
        "W_FIT_OFF_READING: typed K 6.40: its reading 7.72 +- 0.29 (-4.5 sd)",
        "W_FIT_OFF_READING: node E at d 85.00, y 8.66: its reading at d 76.75 +- 1.24, y 9.16 +- 0.32 (8.27 mm away)",
        "W_FACE_NOT_FLAT: face crown: its corners do not fit one flat face (chi2 10.5 for 1 extra corner, 3.2 sd): "
        "each corner from the plane of the other three: A -5.28 +- 1.63 mm, K +2.01 +- 0.62, E -3.34 +- 1.03, H "
        "(others nearly in line)", "solved: K t 6.53 mm (head 7.72)"], out
    assert all(len(ln) <= LB.BRIEF_FACT_MAX + len("W_FACE_NOT_FLAT: ") for ln in out)
    assert LB.first_clause("a, b, c, d", 8) == "a, b ..." and LB.first_clause("x (y; z) w; v") == "x (y)"
    # fit.py's newer wording has no ", " before its corner list: the cut falls at a ": ", never inside a number
    newer = ("face crown: its corners do not fit one flat face (chi2 10.5 for 1 extra corner): each corner 3.2 sd from "
             "the plane of the other three (with one extra corner the readings cannot say which corner is off): A "
             "-5.28 +- 1.63 mm, K +2.01 +- 0.62, E -3.34 +- 1.03; the flat fit: E (d, y) 2.99 mm")
    assert LB.first_clause(newer) == newer.split(": A ")[0] + " ...", LB.first_clause(newer)
    assert LB.first_clause("p (q r s t u v w x", 12) == "p (q r) ..."                 # an open bracket is closed
    # a solved node the head does not have, and one the head's fit splits per facet (no single t): said so, in mm
    # (session2: "L0 4.64 (new)", no quantity and no unit; v3p3-clean-lab)
    assert LB.solved_moves(Fit, {"K": {"t": None}}) == "K t 6.53 mm (the head's has no single t), M t 3.00 mm (not " \
                                                       "in the head)"


def test_variant_arm_fit_lines_keep_where_the_fit_put_read_corners():
    """v3-integrate-2 (interface fit2 -> lab): the joint fit moves read corners along their readings and says so in
    its fit lines; a variant arm's fit_brief keeps those lines with their wrapped continuations (session2's flat-crown
    arm moved the tip E 2.90 mm forward, 2.8 sd, while its misfit line read only "E +0.41 mm (1.1 sd)"), and the
    fit's camera line (v3-final: the misfit line says "after the camera turn", and the arm printed no turn), and
    still leaves out the settling notes and the face lines."""
    class Fit:
        warnings = [{"code": "W_FACE_NOT_FLAT", "face": "crown"}]
        nodes = {}

        @staticmethod
        def report_lines(limit=12, creases=True):
            return ["fit: chi2 10.5, dof 1",
                    "camera turn of fit 02: 0.3 sd (in sd of that camera fit's theta error; one turn for all of its "
                    "readings)", "camera turn per fit: 02 +0.31 +- 1.00",
                    "corners the fit placed off the design (fitted - design, d y mm; fitted - read in its sd): E "
                    "-2.90 +0.90 (2.8 sd),", "  K +0.08 -0.04 (0.6 sd)",
                    "corners held at their move limit (4 sd of the reading along a direction): E",
                    "kept at the design position (the fitted one lay where the builder would snap it): D",
                    "the readings-only fit did not settle in 30 passes: its last pass still moved a corner 0.002 mm",
                    "misfit beyond 1 sd (fitted - read, after the camera turn): E +0.41 mm",
                    "face crown flat (A K E H): chi2 10.5", "W_FACE_NOT_FLAT: face crown: ...", "  ways (unranked)",
                    "at the thickness bound: K"]
    lines = Fit.report_lines()
    out = LB.fit_brief(Fit)
    assert out[:7] == ["fit: chi2 10.5, dof 1", lines[1], lines[3], "  K +0.08 -0.04 (0.6 sd)", lines[5], lines[6],
                       lines[8]]                    # fit.py's camera line kept; an older "camera turn per fit" not
    assert out[7:] == ["at the thickness bound: K", "warnings: W_FACE_NOT_FLAT crown"]    # e2e P3-5: an arm held at
    #                                                                                      t_min reads as one
    assert "camera turn of" in LB.BRIEF_FIT_LINES and "at the thickness bound" in LB.BRIEF_FIT_LINES


def test_variant_crease_brief_never_rounds_a_flat_crease_to_2():
    """v3-integrate-2 (interface builder2 -> lab): a variant arm's crease string rounds as report.crease_line does
    (build.deg_round): a flat crease at 1.97 prints "flat 1.9°", never 2.0 next to its shape word "flat" (< 2 deg).
    The shape is a word and the angle has its unit (v3p3-clean-lab: session2's "crown|logo r14.5" coded the shape
    in a letter no text explained); chamfers are not creases."""
    class Tb:
        tables = {"creases": [{"kind": "crease", "regions": ["L", "R"], "shape": "flat", "mean_dihedral_deg": 1.97},
                              {"kind": "crease", "regions": ["F", "base"], "shape": "valley",
                               "mean_dihedral_deg": 5.04},
                              {"kind": "chamfer", "regions": ["F", "G"], "shape": "ridge", "mean_dihedral_deg": 9.0}]}
    assert LB.Lab._crease_brief(Tb) == "L|R flat 1.9°, F|base valley 5.0°"


def test_crease_lines_print_the_fits_state_in_its_words():
    """v3-integrate-2 (interface fit2 -> lab): a crease the fit calls "flat" (|angle| + 2 sd < MIN_CREASE_DEG: no
    declaration builds within the readings) prints fit.STATE_TEXT's "flat within 2 sd", never "open", which would
    invite a declaration the fit then refuses; certain and open are unchanged; a record without state falls back to
    certain."""
    base = {"deg": 0.4, "sd_deg": 0.5, "held": False, "declared": None}
    texts = {s: LB.fit_crease_text({**base, "state": s, "certain": s == "certain"}, False)
             for s in ("certain", "open", "flat")}
    assert texts == {"certain": "fit +0.4 +- 0.5°, certain", "open": "fit +0.4 +- 0.5°, open",
                     "flat": "fit +0.4 +- 0.5°, flat within 2 sd"}
    assert LB.fit_crease_text({**base, "certain": True}, False).endswith(", certain")
    assert LB.fit_crease_text({**base, "certain": False}, False).endswith(", open")


def test_model_edges_are_capped_for_seeing():
    """seeing takes at most MODEL_EDGES_MAX edges of at most EDGE_POINTS_MAX points: the longest runs are kept in
    their order, a longer run is thinned evenly with its deg_at; the builder's own edges pass unchanged."""
    from glasses3d.agent import seeing as SEE
    edges = BLD.model_edges(build_temple(FX.prada_default()))
    assert edges and LB.capped_edges(edges) == edges
    many = [{"pair": f"F{i}|base", "shape": "flat", "deg": 0.0, "pts": [[0, 0, 0], [i + 1.0, 0, 0]],
             "deg_at": [0.0, 0.0]} for i in range(SEE.MODEL_EDGES_MAX + 5)]
    kept = LB.capped_edges(many)
    assert len(kept) == SEE.MODEL_EDGES_MAX and kept == many[5:]
    long = {"pair": "A|B", "shape": "ridge", "deg": 5.0, "pts": [[float(i), 0.0, 0.0] for i in range(9000)],
            "deg_at": [5.0] * 9000}
    one = LB.capped_edges([long])[0]
    assert len(one["pts"]) == len(one["deg_at"]) <= SEE.EDGE_POINTS_MAX and one["pts"][-1] == long["pts"][-1]


def test_black_photos_image_compare_judge_and_refusals(lab):
    """The black photos: B1 (a calibrated colourway photo) is shown and compared (seeing gets its basis reference,
    the text names its rows) and judged like any photo; B2 and B3 (no camera) are photo_image only, and every
    compare path refuses them with a message naming photo_image; finish precondition 3 covers every photo with a
    camera (B1 included); the default compare set stays seeing's."""
    assert lab.photo_ids() == PHOTO_IDS and lab.compare_ids() == CAMERA_IDS
    assert lab.image_ids() == PHOTO_IDS + BLACK_IDS and lab.pid("black-side") == "B1" and lab.rid("B1") == "black-side"
    tl = tools_of(lab)
    for pid, view in (("B2", "angled"), ("B3", "front"), ("B1", "side")):
        body, _f, images, _t = parsed(call(tl["photo_image"], {"photo": pid, "crop": [0, 0, 64, 40], "enhance": False,
                                                               "scale": 1}))
        assert body["status"] == "ok" and body["view"] == view and body["colour"] == "black" and len(images) == 1
    body, _f, images, _t = parsed(call(tl["photo_compare"], compare_args(["B1", "05"], points=[
        {"photo": "B1", "points": [[100.0, 120.0]]}])))
    assert body["status"] == "ok" and list(body["photos"]) == ["B1", "05"] and len(images) == 2
    assert lab.seeing.calls[-1]["refs"] == ["black-side", "reference-05"]
    assert body["photos"]["B1"].startswith("side black (rows photo / gloss-black render / overlay); temple IoU ")
    assert "d 20.00 y 1.50 t " in body["photos"]["B1"] and "black" not in body["photos"]["05"]
    for fn in (lambda: lab.compare("x", ["B2"]), lambda: lab.compare("x", ["05"], None, None, [
            {"photo": "B3", "points": [[1, 1]]}])):
        with pytest.raises(S.LabRefusal, match="photo_image only") as e:
            fn()
        assert e.value.code == "E_ARGS"
    with pytest.raises(S.LabRefusal, match="photo_image only"):
        lab.variants("x", 0, [{"label": "A", "edits": []}, {"label": "B", "edits": []}], ["B3"], judgement(lab))
    body, *_ = parsed(call(tl["facet_inspect"], {}))
    assert body["photos"]["B1"] == "side, black, camera" and body["photos"]["B2"] == \
        "angled, black, photo_image only" and body["photos"]["05"] == "side, camera"  # v2's "photo_compare" wording;
    #                                         every photo with a camera says so (v3p3 astra P3-9: only B1-B3 did)
    parsed(call(tl["photo_compare"], compare_args()))
    assert lab.seeing.calls[-1]["refs"] == "default"
    miss = S.finish_missing(lab.state, status="matched", best_revision=0, k=1, distinct=lab.distinct,
                            camera_photos=lab.compare_ids(), has_object=False)
    assert next(m for m in miss if m["precondition"] == 3)["photos"] == ["03", "04", "06"]
    (lab.root / "inputs" / "images" / "black-front.png").write_bytes(b"changed")
    body, *_ = parsed(call(tl["photo_image"], {"photo": "B3", "crop": None, "enhance": False, "scale": 1}))
    assert body["status"] == "refused" and body["code"] == "E_STATE"


def test_photo_image_native_crop_enhance_scale_and_refusals(lab):
    """photo_image: a native crop (k 1), a small crop scaled up to 3 x on the exact pixel grid (the transform maps
    back), enhance = a contrast stretch of the crop (its luma range printed); refusals E_ARGS."""
    t = tools_of(lab)["photo_image"]
    body, _f, images, _t = parsed(call(t, {"photo": "05", "crop": [10, 20, 110, 70], "enhance": False, "scale": 1}))
    assert body["transform"]["box"] == [10, 20, 110, 70] and body["transform"]["k"] == 1.0 and len(images) == 1
    body, _f, images, _t = parsed(call(t, {"photo": "05", "crop": [10, 20, 50, 40], "enhance": True, "scale": 3}))
    tr = body["transform"]
    assert tr["k"] == 3.0 and tr["stretch"][0] < tr["stretch"][1]
    raw = __import__("base64").b64decode(images[0].image_url.split(",", 1)[1])
    im = Image.open(__import__("io").BytesIO(raw))
    assert im.size == (120, 60)
    native = lab.basis.photo("reference-05")[20:40, 10:50]
    shown = np.asarray(SH.photo_view(lab.basis.photo("reference-05"), [10, 20, 50, 40], scale=3.0)[0])
    assert np.abs(shown[1::3, 1::3].astype(int) - native.astype(int)).max() <= 1      # pixel centres: native values
    im_big, tr_big = SH.photo_view(np.zeros((900, 1500, 3), np.uint8), None, scale=3.0)
    assert max(im_big.size) <= SH.IMAGE_MAX and tr_big["k"] < 1.0                     # never past the long-side cap
    for args, words in (({"crop": [0, 0, 999, 10]}, "inside the 320 x 240"), ({"scale": 4}, "scale must be 1..3"),
                        ({"enhance": "yes"}, "enhance must be")):
        with pytest.raises(S.LabRefusal, match=re.escape(words)):
            lab.photo(**{"photo": "05", "crop": None, "enhance": False, "scale": 1, **args})
    out = call(t, {"photo": "07", "crop": None, "enhance": False, "scale": 1})      # not in the enum: the SDK refuses
    assert isinstance(out, str) and json.loads(out.rsplit("\n", 1)[0])["errors"] == [   # by path (v3-standin-fix)
        "photo: Input should be '01', '02', '03', '04', '05', '06', 'B1', 'B2' or 'B3'"]


# --------------------------------------------------------------------------------------------- facet_inspect
def test_facet_inspect_shows_design_fit_readings_object_and_the_chart(tmp_path):
    """facet_inspect (WP6_V3 §6): per node its design t (a number, "base", "solve", "read") and fitted value (t, X,
    source, residual mm and sd, sd_X); the readings table (refreshed on the head); the object note; the receipt (fit
    lines, merged crease lines, builder warnings); the chart image only (no section strip); no lean, no inherited
    warnings."""
    lab = make_lab(tmp_path, basis=ReadBasis())
    read_c(lab, 9.0)
    edits = [{"op": "set_node", "name": "C", "d_mm": None, "y_mm": None, "t": {"mode": "reading", "mm": None}}]
    lab.edit("e1", 0, False, edits, None, "x", OBJECT, True, ["05"])
    body, footer, images, text = parsed(call(tools_of(lab)["facet_inspect"], {}))
    assert body["status"] == "ok" and body["head"]["revision"] == 1
    nodes = body["spec"]["nodes"]
    assert nodes["columns"] == ["d", "y", "design", "t", "X", "source", "residual_mm", "residual_sd", "sd_X",
                                "fitted_at"]          # fitted_at: v3-integrate-2 (the fit moves read corners)
    c, a, k, b = (nodes["rows"][n] for n in ("C", "A", "K", "B"))
    assert c[2] == "read" and c[5] == "read" and c[3] == pytest.approx(9.0, abs=0.05)
    assert all(r[9] is None for r in nodes["rows"].values())                   # no corner moved here
    assert a[2] == 4.83 and a[5] == "typed" and k[2] == "solve" and k[5] == "solve" and b[2] == "base"
    assert body["readings"]["columns"][:6] == ["kind", "d", "y", "X", "sd_X", "t"]
    row = body["readings"]["rows"]["C"]
    assert row[0] == "corner" and row[1:3] == [22.6, -16.3] and row[5] == pytest.approx(9.0, abs=0.006)
    assert all(v == round(v, 2) for v in row[1:6])                        # 0.01 mm, as the node rows (P3-10)
    mt = RD.model_t(lab.build(1), 22.6, -16.3)          # C read below the head's bottom outline: no model t there,
    assert mt is None and row[6] == RD.OUTSIDE_MODEL     # in words (v3p3-clean-lab; it was null)
    # a solved node (K) and a base node (B) have no reading: their residual columns say so, never a number against
    # the fit's weak prior (session2's L0 printed 0.21 mm, 0.0 sd; v3p3-clean-lab)
    assert k[6:8] == [LB.NO_RESIDUAL["solve"], None] and b[6:8] == [LB.NO_RESIDUAL["base"], None]
    assert isinstance(c[6], float) and isinstance(a[6], float)
    assert body["object"] == OBJECT and body["spec"]["facets"]["F2"] == {"poly": ["A", "C", "B"], "plane": "flat"}
    rc = body["receipt"]
    assert rc["fit"][0].startswith("fit: chi2 ") and rc["creases"][0].startswith("F1|F2 ridge ")
    assert "inherited_from_revision_0" not in body and "lean_deg" not in text and "margins" not in rc
    assert [i.detail for i in images] == ["high"] and footer == "settled n/a / cap n/a · context n/a tokens"
    raw = __import__("base64").b64decode(images[0].image_url.split(",", 1)[1])
    assert S.load(lab.state_path, "x")["images"][hashlib.sha256(raw).hexdigest()]["kind"] == "chart"
    assert len(text) <= 9000, len(text)


def test_facet_inspect_says_where_the_fit_moved_a_read_corner(tmp_path, monkeypatch):
    """v3-integrate-2 (interface fit2 -> lab): the joint fit moves a read corner along its reading (FitResult.nodes
    d, y, move_mm) and its t and X are there; the node row keeps the design's (d, y) and adds fitted_at = the fitted
    [d, y] from fit.MOVE_SHOWN_MM of move (session2's flat-crown head: E designed at d 76.75, fitted at 73.85)."""
    lab = make_lab(tmp_path, basis=ReadBasis())
    read_c(lab, 9.0)
    edits = [{"op": "set_node", "name": "C", "d_mm": None, "y_mm": None, "t": {"mode": "reading", "mm": None}}]
    lab.edit("e1", 0, False, edits, None, "x", OBJECT, True, ["05"])
    real = lab._fit_now

    def moved(rid):
        fr = real(rid)
        c = fr.nodes["C"]
        c.update(d=c["d"] - 2.9, y=c["y"] + 0.9, move_mm=[-2.9, 0.9])
        fr.nodes["A"]["move_mm"] = [0.01, 0.0]                      # under MOVE_SHOWN_MM: not shown
        return fr
    monkeypatch.setattr(lab, "_fit_now", moved)
    rows = lab.inspect()["spec"]["nodes"]["rows"]
    d, y = rows["C"][0], rows["C"][1]
    assert rows["C"][9] == [round(d - 2.9, 2), round(y + 0.9, 2)] and rows["A"][9] is None


def test_refusal_messages_are_cut_only_at_a_clause_end():
    """lab.brief (a builder refusal within REFUSAL_MAX, a fit refusal within FIT_REFUSAL_MAX): whole when it fits, else
    cut at its last clause end and marked " ...", never mid-sentence (the reviewer's defect 4, 2026-10-11: "...or end
    the cr")."""
    text = "the first clause; the second clause. " * 30
    out = LB.brief(text)
    assert len(out) <= LB.REFUSAL_MAX and out.endswith(" ...") and out[:-4].endswith("clause")
    assert LB.brief("one " * 200).endswith("one ...") and LB.brief("short; whole") == "short; whole"
    assert LB.brief(text, LB.FIT_REFUSAL_MAX) == text
    assert len(LB.brief(text * 2, LB.FIT_REFUSAL_MAX)) <= LB.FIT_REFUSAL_MAX < len(text * 2)


def test_receipt_groups_ramps_in_one_line_and_leaves_out_conditioning(lab):
    """The builder's warnings in a receipt: every W_BORDER_RAMP in one line last, W_CONDITIONING left out (a fitted
    spec's coef planes never raise it; WP6_V3 §6 removes it from Astra's view), the others whole."""
    tb = copy.copy(build_temple(FX.prada_default()))
    cond = {"code": "W_CONDITIONING", "facet": "F2", "deg_per_mm": 7.14, "message": "facet F2 tilts 7.1 deg per mm"}
    ramp = {"code": "W_BORDER_RAMP", "facet": "F4", "height_mm": -0.42, "slope_deg": 12.3, "d_mm": [40.0, 55.0],
            "message": "x"}
    tb.warnings = [cond, {"code": "W_FLAT_CREASE", "message": "crease F2|F3 is flat"}, ramp]
    assert lab._receipt(tb, None)["warnings"] == [
        "W_FLAT_CREASE: crease F2|F3 is flat",
        "W_BORDER_RAMP (steeper than 8°): F4 sunk 0.42 mm: a 12.3° ramp at d 40.0-55.0"]   # words: v3p3-clean-lab
    # a W_FLAT_CREASE whose pair's crease line already says "flat" is not repeated (v3p3 astra P3-1, P3-6: every
    # receipt from session2's step 06 printed "bends only 1.23 deg" beside "shoulder|base flat 1.2° soft")
    tb.tables = copy.deepcopy(tb.tables)
    pair = next(c for c in tb.tables["creases"] if c["kind"] == "crease" and c["regions"] == ["F2", "F3"])
    pair.update(shape="flat", mean_dihedral_deg=1.23)
    flat = {"code": "W_FLAT_CREASE", "regions": ["F2", "F3"], "message": "crease F2|F3 bends only 1.23 deg (< 2.0)"}
    rc = lab._receipt(tb, None)
    assert "F2|F3 flat 1.2° " in " ".join(rc["creases"])
    tb.warnings = [cond, flat, ramp]
    assert lab._receipt(tb, None)["warnings"] == [
        "W_BORDER_RAMP (steeper than 8°): F4 sunk 0.42 mm: a 12.3° ramp at d 40.0-55.0"]


def test_inherited_warnings_stay_out_of_receipts_until_they_change(tmp_path):
    """Revision 0's warnings (its core and Blender lines at prepare: the same words, numbers aside) are never news in a
    later receipt (v3p3-clean-lab: session2 step 11 printed W_ARM_NORMAL_SPREAD as news, with no d, because the tip
    edit IMPROVED it, 61.5 -> 49.9): left out while their numbers stay within MATERIAL_ABS / MATERIAL_REL, and once
    they move listed under the Blender receipt's inherited_changed with revision 0's numbers, never in its news
    lines (a design change: they were news lines before); a warning revision 0 did not have is news; facet_inspect
    no longer lists them (WP6_V3 §6)."""
    prep = ["W_ARM_NORMAL_SPREAD: smooth outer edges bend up to 61.5 deg (> 30.0)",
            "W_HINGE_LEAF: hinge leaf Hinge right | temple mounting leaf embedded 0.84 mm",
            "W_HINGE_LEAF: hinge leaf Hinge left | temple mounting leaf embedded 0.84 mm"]
    lab = make_lab(tmp_path, blender_lines=prep, gap_mm=-0.836)
    lab.state["inherited"] = lab._inherited_of(0, {"lines": prep, "sides": {"+x": {"hinge": {"leaves": [
        {"gap_mm": -0.836}]}}}})
    assert "inherited_from_revision_0" not in lab.inspect()
    lab.s.blender_lines, lab.s.gap_mm = prep[:1] + [ln.replace("0.84", "0.86") for ln in prep[1:]], -0.858
    body = lab.edit("e1", 0, False, [set_t("C", FX.prada_default(), 0.5)], None, "x", OBJECT, True, ["05"])
    assert "lines" not in body["blender"] and "hinge_leaf_gap_mm" not in body["blender"]       # within tolerance
    lab.s.blender_lines, lab.s.gap_mm = [ln.replace("0.84", "1.50").replace("61.5", "75.0") for ln in prep], -1.5
    body = lab.edit("e2", 0, False, [set_t("C", FX.prada_default(), 0.9)], judgement(lab), "x", None, True, ["05"])
    assert body["blender"]["hinge_leaf_gap_mm"] == -1.5 and "lines" not in body["blender"]
    assert body["blender"]["inherited_changed"] == [
        "W_ARM_NORMAL_SPREAD: smooth outer edges bend up to 75.0 deg (> 30.0); revision 0 had it with 61.5",
        "W_HINGE_LEAF: hinge leaf Hinge right | temple mounting leaf embedded 1.50 mm (x2); revision 0 had it with 0.84"]
    lab.s.blender_lines = prep + ["W_KNUCKLE_PENETRATION: knuckle Hinge right enters the temple by 0.40 mm"]
    body = lab.edit("e3", 0, False, [set_t("C", FX.prada_default(), 0.3)], judgement(lab), "x", None, True, ["05"])
    assert body["blender"]["lines"] == ["W_KNUCKLE_PENETRATION: knuckle Hinge right enters the temple by 0.40 mm"]
    assert "inherited_changed" not in body["blender"]                     # revision 0's own numbers again
    ref = prep[0]
    assert LB.same_warning(ref.replace("61.5", "64.0"), ref) and not LB.same_warning(ref.replace("61.5", "68.0"), ref)
    assert LB.inherited_ref(ref.replace("61.5", "49.9"), prep) == ref and LB.inherited_ref(prep[1], prep) == prep[1]
    assert LB.inherited_ref("W_ARM_NORMAL_SPREAD: smooth outer edges bend up to 49.9 deg", prep) is None


def test_an_inherited_arm_spread_that_changes_says_where(tmp_path):
    """v3p3-clean-lab, the core path: revision 0 bends its smooth outer edges 57.8 deg (> 30) where the fixture's base
    drops 3.4 mm over d 80-82 (W_ARM_NORMAL_SPREAD, inherited at prepare). An edit easing the drop to d 80-83 (47.6
    deg) lists it under the receipt's inherited_changed with its place (arm_spread_at: the builder's message gives
    only the angle; session2's had no d) and revision 0's number, never under warnings; revision 0's own design lists
    nothing; a W_ARM_NORMAL_SPREAD that revision 0 did not have is news, with its place; an angle no smooth edge
    folds has no place (never a wrong one)."""
    def drop(run):
        return [[0.0, 5.0], [65.0, 5.0], [80.0, 5.0], [80.0 + run, 1.6], [136.5, 1.6]]

    def splice(run):
        return [{"op": "splice_chain", "chain": c, "d_from_mm": 80.0, "d_to_mm": 83.0,
                 "points": [[80.0, 5.0], [80.0 + run, 1.6]]} for c in ("base.top", "base.bottom")]
    lab = make_lab(tmp_path / "a", spec=FX.base_spec(base={"top": drop(2.0), "bottom": drop(2.0)}))
    assert "W_ARM_NORMAL_SPREAD: smooth outer edges bend up to 57.8 deg (> 30.0)" in lab.state["inherited"]["core"]
    rc = lab.edit("e1", 0, False, splice(3.0), None, "x", OBJECT, False, None)["receipt"]
    assert not any(ln.startswith("W_ARM_NORMAL_SPREAD") for ln in rc.get("warnings", []))
    assert rc["inherited_changed"] == ["W_ARM_NORMAL_SPREAD: smooth outer edges bend up to 47.6 deg (> 30.0) at d "
                                       "80.0 y 4.4 (over 30° along d 80.0-83.0); revision 0 had it with 57.8"]
    rc = lab.edit("e2", 0, False, [], None, "x", None, False, None)["receipt"]
    assert "inherited_changed" not in rc and not any("W_ARM" in ln for ln in rc.get("warnings", []))
    lab = make_lab(tmp_path / "b", spec=FX.base_spec())
    assert lab.state["inherited"]["core"] == []
    body = lab.edit("e1", 0, False, splice(2.0), None, "x", OBJECT, False, None)
    w = next(w for w in lab.build(body["revision"]).warnings if w["code"] == "W_ARM_NORMAL_SPREAD")
    where = LB.arm_spread_at(lab.build(body["revision"]), w["deg"])
    assert re.fullmatch(r" at d 8[0-2]\.\d y -?\d+\.\d \(over 30° along d 80\.0-82\.0\)", where), where
    assert body["receipt"]["warnings"] == [f"W_ARM_NORMAL_SPREAD: {w['message']}{where}"]
    assert "inherited_changed" not in body["receipt"]
    tb = build_temple(FX.base_spec(base={"top": drop(2.0), "bottom": drop(2.0)}))
    deg = next(w["deg"] for w in tb.warnings if w["code"] == "W_ARM_NORMAL_SPREAD")
    assert LB.arm_spread_at(tb, deg).startswith(" at d 80.0 ") and LB.arm_spread_at(tb, deg + 1.0) == ""


# --------------------------------------------------------------------------------------------- procedure
def test_judgement_rule(lab):
    tl = tools_of(lab)
    body, *_ = parsed(call(tl["facet_edit"], edit_args(0, [set_t("C", FX.prada_default(), 0.5)], obj=OBJECT)))
    cid = body["compare_id"]
    base = edit_args(1, apply=False)
    body, *_ = parsed(call(tl["facet_edit"], base))
    assert body["code"] == "E_JUDGEMENT" and cid in body["message"]
    wrong = judgement(lab)
    wrong["compare_id"] = "cmp001"
    assert parsed(call(tl["facet_edit"], {**base, "judgement": wrong}))[0]["code"] == "E_JUDGEMENT"
    short = judgement(lab)
    short["photos"] = short["photos"][:1]
    body, *_ = parsed(call(tl["facet_edit"], {**base, "judgement": short}))
    assert body["code"] == "E_JUDGEMENT" and body["missing"] == ["02", "05"]
    bad_best = judgement(lab, best="c9999")
    assert parsed(call(tl["facet_variants"], {"parent_revision": 1, "photos": ["05"], "judgement": bad_best,
                                              "variants": [{"label": "A", "edits": []}, {"label": "B", "edits": []}]}
                       ))[0]["code"] == "E_JUDGEMENT"
    assert not lab.state["judgements"]
    body, *_ = parsed(call(tl["facet_edit"], {**base, "judgement": judgement(lab, overall="better")}))
    assert body["status"] == "built" and lab.state["judgements"][-1]["compare_id"] == cid
    assert lab.state["best_checkpoint"] == lab.s.head["id"] and S.pending_compare(lab.state) is None
    # photo_compare, photo_read and facet_inspect are not action tools: no judgement needed before them
    assert parsed(call(tl["photo_compare"], compare_args(["05"])))[0]["status"] == "ok"
    assert parsed(call(tl["facet_inspect"], {}))[0]["status"] == "ok"


def test_facet_edit_refusals_return_at_once_and_change_nothing(lab):
    t = tools_of(lab)["facet_edit"]
    n_rev, head = len(lab.state["revisions"]), lab.s.head["id"]
    body, *_ = parsed(call(t, edit_args(0, [set_t("C", FX.prada_default(), 0.2), {"op": "delete_node", "name": "B"}],
                                        obj=OBJECT)))
    assert body["status"] == "refused" and body["edit"] == 1 and body["code"] == "E_EDIT"
    body, *_ = parsed(call(t, edit_args(0, [{"op": "set_node", "name": "C", "d_mm": None, "y_mm": None,
                                             "t": {"mode": "reading", "mm": None}}], obj=OBJECT)))
    assert body["code"] == "E_EDIT" and "there is no reading named C: read it with photo_read first" in body["message"]
    body, *_ = parsed(call(t, edit_args(9)))
    assert body["code"] == "E_REVISION"
    assert len(lab.state["revisions"]) == n_rev and lab.s.head["id"] == head


def test_facet_edit_applies_compares_and_fits_the_text_budget(lab):
    """An applied edit: the revision, the Blender layer's news, a compare of the default set with the fitted head's
    model edges, the chart and the compare images; the text stays within 3,000 characters on the Prada fixture with
    the default photos (v2's 1,500 had no fit lines and no outline text; the fit report is at most 12 lines of 140)."""
    t = tools_of(lab)["facet_edit"]
    edits = [set_t("C", FX.prada_default(), 0.6), {"op": "set_scalar", "key": "roll_mm", "value": 0.3}]
    body, footer, images, text = parsed(call(t, edit_args(0, edits, obj=OBJECT, note="deeper C, rolled edges"),
                                             "call_e1"))
    assert body["status"] == "applied" and body["revision"] == 1 and body["parent"] == 0
    assert body["diff"].startswith("~node C t 8.0->8.6") and "roll_mm 0.0->0.3" in body["diff"]
    assert body["receipt"]["creases"][0].startswith("F1|F2 ridge") and body["blender"]["decal_max_move_mm"] == 0.61
    assert body["compare_id"] and list(body["photos"]) == DEFAULT
    assert len(images) == 1 + 3 and footer.startswith("settled ")
    assert len(text) <= 3000, len(text)
    head = S.head_by_fingerprint(lab.state, lab.s.head["fingerprint"])
    assert head["revision"] == 1 and lab.state["events"][-1]["tool"] == "facet_edit"


# --------------------------------------------------------------------------------------------- facet_variants
def test_facet_variants_fit_each_arm_and_name_refusals(lab):
    """Each arm is fitted and built as facet_edit's: a built arm is a revision with its fit lines (short), creases and
    distance from the head; an arm the fit refuses (E_FIT_SHAPE) or apply_edits refuses (E_EDIT) carries its stage,
    code and facts; the scene never changes; a built variant becomes the head through facet_edit with no edits. At
    most VARIANTS_MAX 4 arms a call (v3-final: ONE sheet per photo holds the head and every arm; a fifth is E_ARGS)."""
    tl = tools_of(lab)
    spec = FX.prada_default()
    head_fp = lab.s.head["fingerprint"]
    variants = [{"label": "A", "edits": [set_t("C", spec, -1.0)]}, {"label": "B", "edits": [set_t("C", spec, 0.5)]},
                {"label": "C", "edits": [set_t("C", spec, 1.5)]},
                {"label": "V", "edits": [{"op": "set_crease", "facets": ["F2", "F4"], "shape": "valley"}]}]
    z_arm = {"label": "Z", "edits": [{"op": "delete_node", "name": "B"}]}
    parsed(call(tl["photo_compare"], compare_args(["05", "03"])))
    with pytest.raises(S.LabRefusal, match=re.escape("variants must be 2..4 {label, edits}")) as e:
        lab._variants("x", 0, variants + [z_arm], ["05", "03"])
    assert e.value.code == "E_ARGS" and LB.VARIANTS_MAX == 4
    body, _f, images, text = parsed(call(tl["facet_variants"], {"parent_revision": 0, "variants": variants,
                                                                "photos": ["05", "03"], "judgement": judgement(lab)}))
    assert body["status"] == "ok" and body["spread_ok"] is True and len(images) == 2      # one sheet a photo
    by = {v["label"]: v for v in body["variants"]}
    assert (by["V"]["revision"], by["V"]["stage"], by["V"]["code"]) == (None, "fit", "E_FIT_SHAPE")
    assert no_remedy(by["V"]["message"]) and "readings-only" in by["V"]["message"]
    for x in "ABC":
        v = by[x]
        assert v["revision"] is not None and v["vs_head_mm"] > 0.3 and v["fit"][0].startswith("fit: chi2 ")
        assert v["creases"].startswith("F1|F2 r") and lab.state["revisions"][v["revision"]]["fit_file"]
    assert lab.state["revisions"][by["B"]["revision"]]["kind"] == "variant"
    assert lab.s.read_fingerprints == [head_fp] and lab.s.head["fingerprint"] == head_fp
    assert [v["spec"]["nodes"]["C"][2] for v in lab.s.read_params[-1]["variants"]] == \
        [lab.spec(by[x]["revision"])["nodes"]["C"][2] for x in "ABC"]                  # the worker gets fitted specs
    assert len(text) <= 3000, len(text)
    zb, *_ = parsed(call(tl["facet_variants"], {"parent_revision": 0, "variants": [variants[1], z_arm],
                                                "photos": ["05"], "judgement": None}))
    z = {v["label"]: v for v in zb["variants"]}["Z"]
    assert zb["status"] == "ok" and (z["stage"], z["code"], z["edit"]) == ("edit", "E_EDIT", 0)
    assert lab.s.head["fingerprint"] == head_fp
    body, *_ = parsed(call(tl["facet_edit"], edit_args(by["B"]["revision"], obj=OBJECT, photos=["05"])))
    assert body["status"] == "applied" and body["revision"] == by["B"]["revision"] and body["diff"] == "no change"


def test_variant_pages_crop_to_the_charted_temple_with_clean_and_lined_panels(lab, monkeypatch):
    """facet_variants' sheets (WP6_V3 §6, scorecard #29): per photo the box is the charted temple's faceted zone
    (inside the photo, smaller than the compare's crop of both temples), at sheets.variant_scale (<= 2) for the models
    DRAWN; a photo row (the photo | the photo with the head's edges) and per model drawn a clean panel beside the lined
    one (the photo outline and that model's model edges); an arm refused or equal to the head has no panels and is
    named on the sheet's notes line, and an arm equal to the head is not rendered again (review2-lab-e2-1 P2-3: such
    arms took a page slot each) and is no rendered variant, so it never makes a spread (v3p3 e2e P3-4: "same" stood in
    for the head beside two real arms); ONE sheet per photo within SHEET_W x SHEET_H (v3p3-clean-
    lab: session2's 3-arm call on 3 photos returned 8 pages; the cost estimate prices one a photo), its title with k
    and px/mm; the record counts each model's edges drawn per photo."""
    rows_seen = []
    real = SH.variant_sheet

    def sheet(photo_row, rows, title, notes=""):
        rows_seen.append((photo_row, rows, title, notes))
        return real(photo_row, rows, title, notes)
    monkeypatch.setattr(SH, "variant_sheet", sheet)
    tl = tools_of(lab)
    parsed(call(tl["photo_compare"], compare_args(["05", "B1"])))
    spec = FX.prada_default()
    variants = [{"label": "deep", "edits": [set_t("C", spec, 1.0)]}, {"label": "shallow", "edits": [set_t("C", spec,
                                                                                                         -1.0)]},
                {"label": "Z", "edits": [{"op": "delete_node", "name": "B"}]}, {"label": "same", "edits": []}]
    body, _f, images, _t = parsed(call(tl["facet_variants"], {"parent_revision": 0, "variants": variants,
                                                              "photos": ["05", "B1"], "judgement": judgement(lab)}))
    assert body["status"] == "ok" and "render" not in body, body
    by = {v["label"]: v for v in body["variants"]}
    assert by["same"] == {"label": "same", "revision": 0, "design": by["same"]["design"], "vs_head_mm": 0.0,
                          "same_as": "head"}
    assert [v["label"] for v in lab.s.read_params[-1]["variants"]] == ["deep", "shallow"]   # not rendered again
    views = {v["name"]: v for v in lab.s.read_params[-1]["views"]}
    assert views["B1"]["passes"] == ["gloss_black"] and views["05"]["passes"] == ["native_linear"]
    assert len(rows_seen) == 2
    for photo_row, rows, title, notes in rows_seen:
        assert photo_row[0].startswith("photo ") and photo_row[2] == "photo + the head's edges"
        assert [r[0].split(":")[0].split(" ")[0] for r in rows] == ["head", "deep", "shallow"]
        for label, clean, lined, note in rows:
            assert clean.size == lined.size and np.asarray(clean).tolist() != np.asarray(lined).tolist(), label
        assert notes == "no panels of their own (facts in the text): Z refused E_EDIT; same = head"
        assert "box [" in title and "(where the arms differ) k " in title and "legend" not in title
        assert re.search(r" k \d\.\d\d, \d+\.\d px/mm$", title), title
    rec = lab.state["variants"][-1]
    assert rec["rendered"] == ["deep", "shallow"] and rec["spread"] is None and body["spread_ok"] is False
    assert rec["edge_occlusion"] == "scene" and set(rec["edges"]) == {"05", "B1"}
    assert all(n is not None for row in rec["edges"].values() for n in row.values())
    assert set(rec["edges"]["05"]) == {"head", "deep", "shallow"}
    models = {"head": lab.build(0), "deep": lab.build(by["deep"]["revision"]),
              "shallow": lab.build(by["shallow"]["revision"])}
    for pid in ("05", "B1"):
        c = lab._variant_views([pid], models, "x", pairs=3)[1]["photos"][pid]
        assert c["k"] == SH.variant_scale(c["box"][2] - c["box"][0], c["box"][3] - c["box"][1], 3)
    assert len(images) == 2 and sorted(Path(r["file"]).name for r in lab.state["images"].values()
                                       if r["kind"] == "variants") == ["sheet-05.png", "sheet-B1.png"]
    c = lab._variant_views(["05"], {"head": lab.build(0)}, "x")[1]["photos"]["05"]
    x0, y0, x1, y1 = c["box"]
    assert 0 <= x0 < x1 <= 320 and 0 <= y0 < y1 <= 240 and c["k"] <= SH.VARIANT_K_MAX
    assert c["k"] == SH.variant_scale(x1 - x0, y1 - y0, 1) and c["side"] == 1 and c["shows"] == "the faceted zone"
    for p in images:
        raw = __import__("base64").b64decode(p.image_url.split(",", 1)[1])
        page = Image.open(__import__("io").BytesIO(raw))
        assert page.width <= SH.SHEET_W and page.height <= SH.SHEET_H


def test_variant_sheet_is_one_page_at_the_largest_scale_that_fits():
    """sheets (v3p3-clean-lab, a design change: pages per photo became ONE sheet per photo; session2's 3-arm call on 3
    photos returned 8 pages, B2's box at k 1.16 holding one model a page, each repeating the photo pair, while the cost
    estimate priced one sheet a photo). variant_scale: the largest k <= 2 at which the photo pair and every model pair
    fit one SHEET_W x SHEET_H sheet in some layout (no grid step above it fits); native for a CELL_W x CELL_H_MAX box
    with one model; session2's step-17 boxes with the head and 3 arms: B1 0.43, 05 0.48, B2 0.72 (the docstring's
    numbers). variant_layout: side by side when any side-by-side layout fits, else stacked; the smallest sheet.
    variant_sheet: one image within the sheet whatever the panel size (larger panels are shrunk), every model's
    panels on it, the photo pair first; the header's title (no "page i/n"), the legend on its own line (it was cut on
    900 px pages, review2-lab-e2-1 P3-6), the notes on the next; a narrow pair's label takes two lines."""
    def fits(w, h, k, models):
        return SH.variant_layout(max(1, round(w * k)), max(1, round(h * k)), models + 1) is not None
    assert SH.variant_scale(100, 40) == SH.VARIANT_K_MAX and SH.variant_scale(100, 40, 7) == SH.VARIANT_K_MAX
    assert SH.variant_scale(SH.CELL_W, SH.CELL_H_MAX) >= 1.0 > SH.variant_scale(SH.CELL_W + 1, SH.CELL_H_MAX)
    assert SH.variant_scale(1150, 320, 4) == pytest.approx(0.433, abs=0.001)          # session2 B1, head + 3 arms
    assert SH.variant_scale(1026, 286, 4) == pytest.approx(0.484, abs=0.001)          # 05
    assert SH.variant_scale(439, 329, 4) == pytest.approx(0.719, abs=0.001)           # B2 (it was 1.16 on 4 pages)
    assert SH.variant_scale(1150, 320, 1 + LB.VARIANTS_MAX) == pytest.approx(0.380, abs=0.001)  # B1, head + 4 arms
    for w, h in ((1150, 320), (439, 329), (1026, 286), (634, SH.CELL_H_MAX), (67, 382), (3000, 2000), (120, 40)):
        for m in (1, 2, 4, 7):
            k = SH.variant_scale(w, h, m)
            assert 0.0 < k <= SH.VARIANT_K_MAX and fits(w, h, k, m), (w, h, m, k)
            assert not any(fits(w, h, g / 100.0, m) for g in range(int(k * 100) + 1, 201)), (w, h, m, k)
            assert m == 1 or k <= SH.variant_scale(w, h, m - 1) + 1e-9                 # more models, never larger
    assert SH.variant_layout(421, 371, 2) == (False, 1)                       # side by side when it fits
    assert SH.variant_layout(1000, 150, 2) == (True, 1)                        # a pair 2004 px wide: stacked
    assert SH.variant_layout(1000, 400, 2) is None and SH.variant_layout(120, 40, 8)[0] is False
    assert SH._shape(67, 382, False)[2] == SH.LABEL_PX2 and SH._shape(400, 200, False)[2] == SH.LABEL_PX
    colours = [(30 * i + 20, 200 - 20 * i, 90) for i in range(8)]
    for w, h in ((634, SH.CELL_H_MAX), (421, 371), (300, 120), (120, 40), (67, 382), (1150, 320)):
        imgs = [Image.new("RGB", (w, h), c) for c in colours]
        rows = [(f"V{i}: r{i}, 0.52 mm from head", imgs[i + 2], imgs[i + 2], "") for i in range(6)]
        page = SH.variant_sheet(("photo 05", imgs[0], "photo + the head's edges", imgs[1]), rows, "T" * 260,
                                "Z refused")
        assert page.width <= SH.SHEET_W and page.height <= SH.SHEET_H, (w, h, page.size)
        a = np.asarray(page).reshape(-1, 3)
        seen = {tuple(c) for c in np.unique(a, axis=0)}
        assert all(c in seen for c in colours), (w, h)                         # every model's panels are on it
    dr = __import__("PIL.ImageDraw", fromlist=["x"]).Draw(Image.new("RGB", (8, 8)))
    lines = SH.header_lines(dr, "variants var003 of r0 at 05 (side): " + "x " * 200, "Z refused E_EDIT", 900, False)
    assert lines[0].startswith("variants var003") and len(lines) == SH.HEADER_LINES
    assert lines[2] == SH.edge_legend(False) and lines[3] == "Z refused E_EDIT"
    assert SH.header_lines(dr, "t", "", 900, True)[:3] == ["t", "", SH.edge_legend(True)]


def test_variant_region_is_where_the_arms_differ():
    """The variant panels show the (d, y) box where an arm's surface differs from the head's (or its outline: a chain
    edit), widened; arms equal to the head show the faceted zone; a temple without facets shows whole."""
    s = FX.prada_default()
    head = build_temple(s)
    deep, flat = copy.deepcopy(s), copy.deepcopy(s)
    deep["nodes"]["C"][2] += 1.0
    flat["nodes"]["C"][2] -= 1.0
    P, what = Lab._variant_region({"head": head, "A": build_temple(deep), "B": build_temple(flat)})
    assert what == "where the arms differ" and P[:, 0].max() < 60.0 and P.shape[1] == 3 and np.isfinite(P).all()
    P, what = Lab._variant_region({"head": head, "A": build_temple(s)})
    assert what == "the faceted zone" and 60.0 < P[:, 0].max() < 70.0
    arm, _ = G.apply_edits(s, [{"op": "offset_chain", "chain": "outline.bottom", "d_from_mm": 100.0, "d_to_mm": 110.0,
                                "delta_mm": -0.5, "taper_mm": 2.0}])
    P, what = Lab._variant_region({"head": head, "C": build_temple(arm)})
    assert what == "where the arms differ" and 90.0 < P[:, 0].min() and P[:, 0].max() < 120.0
    P, what = Lab._variant_region({"head": build_temple(FX.slab())})
    assert what == "the whole temple" and P[:, 0].max() > 130.0


def _head_with(mesh, roles, tb):
    """The box-glasses fixture's export with its two temple bodies replaced by tb's (as the worker builds the spec
    into both temples)."""
    from dataclasses import replace
    bodies = [PO.temple_body(mesh, s, roles) for s in (1, -1)]
    keep = ~np.isin(mesh.part, bodies)
    Vs, Fs, Ps, n = [mesh.V], [mesh.F[keep]], [mesh.part[keep]], len(mesh.V)
    for s, b in zip((1, -1), bodies):
        W, _polys, tris = tb.world(s)
        Vs.append(W)
        Fs.append(np.asarray(tris, np.int64) + n)
        Ps.append(np.full(len(tris), b, mesh.part.dtype))
        n += len(W)
    return replace(mesh, V=np.concatenate(Vs), F=np.concatenate(Fs), part=np.concatenate(Ps))


def _run_mask(edges, shape) -> np.ndarray:
    """The visible runs of drawn model edges, 1 px wide, as a boolean image (to count pixels)."""
    from PIL import ImageDraw
    im = Image.new("L", (shape[1], shape[0]), 0)
    dr = ImageDraw.Draw(im)
    for e in edges:
        for run in e["runs"]:
            dr.line([tuple(map(float, q[:2])) for q in run], fill=255, width=1)
    return np.asarray(im) > 0


def test_variant_edges_are_the_compare_rule_on_the_fixture(tmp_path):
    """Lab._variant_edges (the head's export through the lab's seeing, its temple bodies swapped for the model's, the
    edges posed by pose.chart_to_posed and depth-tested by compare.edge_runs_posed) draws exactly seeing's model edges
    of an export whose temple bodies ARE that model (Seeing._edges_posed + _edges_drawn on the posed scene, the
    compare overlay's rule): the box-glasses fixture with the Prada-layout temples built in, six camera angles, an
    asymmetric pose; the same call with the pose's sides swapped draws other runs. Fixture data only."""
    from glasses3d.g3d_host import fitmesh, raster
    lab = make_lab(tmp_path)
    bs, tb = lab.basis, lab.build(0)
    exp = lab.seeing.export_head("fixture")
    mesh = fitmesh.load_fit_mesh(exp["path"], frame=bs.frame)
    roles = PO.part_roles(exp["path"])
    head = _head_with(mesh, roles, tb)

    def pose(a, b):                                       # opening degrees of the +X and -X temples
        return {"+1": {"theta_deg": a, "axis_xz_mm": [64.6, -10.1]},
                "-1": {"theta_deg": b, "axis_xz_mm": [-64.6, -10.1]}}
    shape = (360, 640)
    views = {"side": (90.0, 13.6, 0.25), "angled": (28.2, 11.4, 0.26), "top": (0.3, 59.9, 0.29),
             "mirrored-side": (-90.0, 0.0, 0.0), "rear": (180.0, 8.8, 0.07), "front": (0.0, 5.0, 0.2)}
    photos = {name: {"pose": pose(9.0, 3.0), "shape": shape,
                     "ccam": raster.view_camera(bs.frame, yaw, pitch, 3.0, shape, perspective=persp,
                                                center_mm=np.array([0.0, 0.0, -60.0]))}
              for name, (yaw, pitch, persp) in views.items()}
    got, info = lab._variant_edges(photos, {"head": tb}, "fixture")
    swapped, _ = lab._variant_edges({k: {**c, "pose": pose(3.0, 9.0)} for k, c in photos.items()}, {"head": tb},
                                    "fixture")
    assert info == {"occlusion": "scene"}
    edges = LB.capped_edges(BLD.model_edges(tb))
    shown = 0
    for name, c in photos.items():
        mp = PO.posed(head, c["pose"], roles)
        ref = Seeing._edges_drawn(Seeing._edges_posed([{**e, "pts": np.asarray(e["pts"], float),
                                                        "deg_at": np.asarray(e["deg_at"], float)} for e in edges],
                                                      c["pose"], tb.spec.hinge["z_mm"], None),
                                  c["ccam"], bs.frame, mp.scene(), shape)
        mine = got[(name, "head")]
        assert len(mine) == len(ref) == 2 * len(edges)
        for a, b in zip(mine, ref):
            assert (a["pair"], a["side"]) == (b["pair"], b["side"]) and len(a["runs"]) == len(b["runs"]), name
            for ra, rb in zip(a["runs"], b["runs"]):
                assert np.allclose(np.asarray(ra, float)[:, :2], np.asarray(rb, float)[:, :2], atol=1e-6), name
            shown += sum(len(r) for r in a["runs"])
        a, b = _run_mask(mine, shape), _run_mask(swapped[(name, "head")], shape)
        assert a.sum() > 40 and (a != b).sum() > 0.3 * a.sum(), (name, int(a.sum()), int((a != b).sum()))
    assert shown > 1000


def test_variant_spread_counts_only_rendered_variants(lab):
    """Precondition 2's spread is over the variants rendered at every photo: one refused by Blender leaves two (no
    spread); a failed render leaves none; three rendered ones count. (B is C + 0.3 mm: an arm equal to the head is
    not rendered again, so Blender could not refuse it.)"""
    tl = tools_of(lab)
    parsed(call(tl["photo_compare"], compare_args(["05", "02"])))
    spec = FX.prada_default()
    variants = [{"label": lb, "edits": [set_t("C", spec, dt)]} for lb, dt in (("A", -1.0), ("B", 0.3), ("C", 1.0))]
    args = {"parent_revision": 0, "variants": variants, "photos": ["05", "02"], "judgement": judgement(lab)}
    lab.s.refuse_variants = {"B"}
    body, _f, images, _t = parsed(call(tl["facet_variants"], args))
    assert body["status"] == "ok" and body["spread_ok"] is False and len(images) >= 2
    assert all(v["revision"] is not None for v in body["variants"])       # all three built on the host
    assert body["render"]["blender_refused"]["B"].startswith("E_RELIEF_STEP")
    assert lab.state["variants"][-1]["rendered"] == ["A", "C"] and lab.state["variants"][-1]["spread"] is None
    lab.s.refuse_variants, lab.s.render_error = set(), True
    args["judgement"] = None
    body, _f, images, _t = parsed(call(tl["facet_variants"], args))
    assert body["spread_ok"] is False and images == [] and body["render"]["status"] == "error"
    assert lab.state["variants"][-1]["rendered"] == []
    assert [m["precondition"] for m in S.finish_missing(lab.state, status="matched", best_revision=0, k=0,
                                                        distinct=lambda a, b: True, camera_photos=[],
                                                        has_object=True)] == [2]
    lab.s.render_error = False
    body, *_ = parsed(call(tl["facet_variants"], args))
    assert body["spread_ok"] is True and lab.state["variants"][-1]["spread"] == ["A", "B", "C"]
    assert lab.s.calls[-1][2] <= L.HARD_TIMEOUT_S["facet_variants"] - L.FACET_VARIANTS_HOST_MARGIN_S   # worker


def test_variant_photo_limits_labels_and_a_lab_without_black_photos(lab):
    """At most 3 target photos per call, a 4th only of another colour; labels must differ ignoring case and never be
    'head'; every photo needs a full compare first (a zoom-only compare is not one); a lab without photos of another
    colour takes 3. Edges degrade to the temples' own depth (named in the text) when the head's scene cannot be
    read."""
    tl = tools_of(lab)
    two = [{"label": "A", "edits": []}, {"label": "B", "edits": []}]
    with pytest.raises(S.LabRefusal, match="at most 3 target photos"):
        lab.variants("x", 0, two, ["01", "02", "03", "05"], None)
    out = call(tl["facet_variants"], {"parent_revision": 0, "variants": two, "judgement": None,
                                      "photos": ["01", "02", "03", "B1", "05"]})
    b = json.loads(out.rsplit("\n", 1)[0])                                        # by path (v3-standin-fix)
    assert isinstance(out, str) and b["code"] == "E_ARGS" and b["errors"] == ["photos: List should have at most 4 "
                                                                              "items after validation, not 5"]
    for labels in (["Head", "B"], ["a", "A"]):
        with pytest.raises(S.LabRefusal, match="ignoring case"):
            lab.variants("x", 0, [{"label": lb, "edits": []} for lb in labels], ["05"], None)
    with pytest.raises(S.LabRefusal, match="never compared in full") as e:
        lab.variants("x", 0, two, ["05", "B1"], None)                    # B1 needs a compare for its gloss record
    assert e.value.message.endswith("a full photo_compare of it, or photos without it")   # both ways out (P3-10)
    lab.compare("z", [], None, [{"photo": "B1", "box": [0, 0, 40, 40], "geometry": False}], None)
    with pytest.raises(S.LabRefusal, match="never compared in full"):
        lab.variants("x", 0, two, ["05", "B1"], judgement(lab))
    lab.seeing.scene_glb = b"glTF fake"
    spec = FX.prada_default()
    two = [{"label": "A", "edits": [set_t("C", spec, 1)]}, {"label": "B", "edits": []}]
    body, _f, images, _t = parsed(call(tl["facet_variants"], {"parent_revision": 0, "judgement": None,
                                                             "photos": ["05"], "variants": two}))
    assert body["status"] == "ok" and len(images) >= 1
    assert body["render"]["edges"].startswith("depth-tested on the temples only")
    assert lab.state["variants"][-1]["edges"]["05"]["A"] > 0
    lab.manifest["photos"] = [r for r in lab.manifest["photos"] if r.get("role") != "colourway"]
    assert lab.variant_photos_max() == 3
    schema = {t.name: t.params_json_schema for t in T.registry(lab)}["facet_variants"]
    assert schema["properties"]["photos"]["maxItems"] == 3 and enums(schema["properties"]["photos"]) == [PHOTO_IDS]


def test_variant_labels_are_their_render_file_names(lab):
    """The worker names a variant's render files by g3d/render.py safe_name(label) and the lab looks them up by the
    label, so a label that is not its own safe name ('deep_', 'A_') is refused E_ARGS before any build or render; the
    distinct-ignoring-case rule runs on the safe names; a label ending in '-' is its own safe name and renders.
    lab.render_safe_name is render.py's rule: that module imports bpy, so its NAME_RE and safe_name are read from the
    source here and compared on sample names."""
    import ast
    src = (Path(LB.__file__).resolve().parents[2] / "g3d" / "render.py").read_text(encoding="utf-8")
    keep = [n for n in ast.parse(src).body
            if (isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "NAME_RE" for t in n.targets))
            or (isinstance(n, ast.FunctionDef) and n.name == "safe_name")]
    assert len(keep) == 2
    ns = {"re": re, "RenderRefused": ValueError}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "g3d/render.py", "exec"), ns)       # noqa: S102
    assert ns["NAME_RE"].pattern == LB.RENDER_NAME_RE.pattern
    for name in ("deep", "deep_", "A_", "a__", "_a_", "a.b", "x y", "Zz-9_x", "C_fwd", "deep-", "N" * 90, "é"):
        try:
            want = ns["safe_name"](name)
        except ValueError:                          # no usable character: render.py refuses, the lab's gives ""
            want = ""
        assert render_safe_name(name) == want, name
    spec = FX.prada_default()
    n_rev, n_calls, seq = len(lab.state["revisions"]), len(lab.s.calls), lab.state["seq"]
    for labels in (["deep_", "deep"], ["a", "A_"]):
        with pytest.raises(S.LabRefusal, match="not ending in _") as e:
            lab.variants("x", 0, [{"label": lb, "edits": [set_t("C", spec, 0.5)]} for lb in labels], ["05"], None)
        assert e.value.code == "E_ARGS"
    body, *_ = parsed(call(tools_of(lab)["facet_variants"], {"parent_revision": 0, "photos": ["05"], "judgement": None,
                                                             "variants": [{"label": "deep_", "edits": []},
                                                                          {"label": "B", "edits": []}]}))
    assert body["status"] == "refused" and body["code"] == "E_ARGS" and "not ending in _" in body["message"]
    assert len(lab.state["revisions"]) == n_rev and len(lab.s.calls) == n_calls and lab.state["seq"] == seq
    with pytest.raises(S.LabRefusal, match="ignoring case"):
        lab.variants("x", 0, [{"label": lb, "edits": []} for lb in ("Deep-", "deep-")], ["05"], None)
    dash = [{"label": "deep-", "edits": [set_t("C", spec, 1.0)]}, {"label": "B", "edits": []}]
    body, *_ = parsed(call(tools_of(lab)["facet_variants"], {"parent_revision": 0, "photos": ["05"], "judgement": None,
                                                             "variants": dash}))
    assert body["status"] == "ok" and "render" not in body and lab.state["variants"][-1]["rendered"] == ["deep-"]
    assert body["variants"][1]["same_as"] == "head"          # B builds the head: shown, no variant (v3p3 e2e P3-4)


def test_arms_that_all_build_the_head_open_no_stuck_finish(lab):
    """v3p3 safety P3-1: an arm whose design is the head's is marked same_as "head" and its model is on the sheet, so
    it counted as rendered, and a facet_variants call whose arms all equal the head (empty edit lists, or edits the
    fit maps back to the head's design) met finish precondition 5 with no alternative shown. Such an arm is no
    rendered variant: the call records none, precondition 5 stays missing; one real arm meets it."""
    tl = tools_of(lab)
    parsed(call(tl["photo_compare"], compare_args(["05"])))
    spec = FX.prada_default()
    noop = [{"label": "same", "edits": []}, {"label": "again", "edits": [set_t("C", spec, 0.0)]}]
    body, *_ = parsed(call(tl["facet_variants"], {"parent_revision": 0, "variants": noop, "photos": ["05"],
                                                  "judgement": judgement(lab)}))
    assert [v.get("same_as") for v in body["variants"]] == ["head", "head"] and body["spread_ok"] is False
    assert lab.state["variants"][-1]["rendered"] == [] and S.variants_since_head(lab.state) == []
    missing = S.finish_missing(lab.state, status="stuck", best_revision=0, k=0, distinct=lab.distinct,
                               camera_photos=[], has_object=True)
    assert any(m["precondition"] == 5 and "no facet_variants result with a rendered variant" in m["message"]
               for m in missing)
    body, *_ = parsed(call(tl["facet_variants"], {"parent_revision": 0, "variants": noop[:1] + [
        {"label": "deep", "edits": [set_t("C", spec, 1.0)]}], "photos": ["05"], "judgement": None}))
    assert lab.state["variants"][-1]["rendered"] == ["deep"] and len(S.variants_since_head(lab.state)) == 1


def test_a_dead_worker_in_the_variant_edge_export_stays_fatal(lab, monkeypatch):
    """_variant_edges re-raises journal.WorkerFailed from the head's export (the worker died and was not recovered),
    so the run ends failed_worker as from any tool; any other export failure only degrades the edges to the temples'
    own depth (test_variant_photo_limits_labels_and_a_lab_without_black_photos)."""
    def dead(call_id=None):
        raise WorkerFailed("injected: the worker died during the head's export", lab.s.head)
    monkeypatch.setattr(lab.seeing, "export_head", dead)
    two = [{"label": "A", "edits": [set_t("C", FX.prada_default(), 1)]}, {"label": "B", "edits": []}]
    with pytest.raises(WorkerFailed):
        call(tools_of(lab)["facet_variants"], {"parent_revision": 0, "judgement": None, "photos": ["05"],
                                               "variants": two})


# --------------------------------------------------------------------------------------------- facet_finish
def _distinct_heads_and_variants(lab, tl, obj=OBJECT):
    """Three applied heads (C + 0.5 / 1.0 / 1.5 mm, the first with the object note) compared at the default set, then
    a facet_variants call with a spread: [the three checkpoints]."""
    spec = FX.prada_default()
    cps = []
    for k, dt in enumerate((0.5, 1.0, 1.5)):
        body, *_ = parsed(call(tl["facet_edit"], edit_args(0, [set_t("C", spec, dt)], obj=obj if not k else None,
                                                           judge=judgement(lab) if k else None)))
        assert body["status"] == "applied", body
        cps.append(body["checkpoint"])
    variants = [{"label": lb, "edits": [set_t("C", spec, dt)]} for lb, dt in (("A", -1.0), ("B", 0.0), ("C", 1.0))]
    body, *_ = parsed(call(tl["facet_variants"], {"parent_revision": 0, "variants": variants, "photos": ["05"],
                                                  "judgement": judgement(lab, best=cps[0])}))
    assert body["spread_ok"]
    return cps


def test_finish_preconditions_through_the_lab(lab, monkeypatch):
    """facet_finish passes the lab's facts to state.finish_missing: camera_photos = every photo with a camera (B1
    too), has_object = a note exists; refused with the missing ones named (no removed tool in any message), then
    accepted once they hold; a finish needs no restore tool: best_checkpoint's revision is restored by the finish
    itself."""
    import glasses3d.g3d_host.spec as GS
    monkeypatch.setattr(GS, "validate_spec", lambda p: {"ok": True, "errors": [], "warnings": ["w1"],
                                                        "live_site_would_fail": []})
    tl = tools_of(lab, finish_k=3)
    body, *_ = parsed(call(tl["facet_finish"], {"status": "matched", "best_checkpoint": lab.s.head["id"],
                                                "summary": "s", "judgement": None}))
    assert body["status"] == "refused" and [m["precondition"] for m in body["missing"]] == [1, 2, 3, 4]
    assert body["missing"][2]["photos"] == CAMERA_IDS                   # prepare's compares were never shown to Astra
    assert no_remedy(json.dumps(body)) and S.DIFFERENT_ATTEMPT in body["missing"][0]["message"]
    cps = _distinct_heads_and_variants(lab, tl)
    best = cps[0]
    body, *_ = parsed(call(tl["facet_finish"], {"status": "matched", "best_checkpoint": best, "summary": "s",
                                                "judgement": None}))
    assert body["status"] == "refused" and [m["precondition"] for m in body["missing"]] == [3]
    assert set(body["missing"][0]["photos"]) == {"03", "04", "06", "B1"}
    first = lab.state["heads"][1]["revision"]                         # best's revision, applied again with no edits
    parsed(call(tl["facet_edit"], edit_args(first, judge=judgement(lab, best=best), photos=CAMERA_IDS)))
    body, *_ = parsed(call(tl["facet_finish"], {"status": "stuck", "best_checkpoint": best, "summary": "s",
                                                "judgement": judgement(lab, best=best, overall="better")}))
    assert body["status"] == "refused" and [m["precondition"] for m in body["missing"]] == [5]
    body, footer, images, text = parsed(call(tl["facet_finish"], {"status": "matched", "best_checkpoint": best,
                                                                  "summary": "s", "judgement": None}))
    assert body["status"] == "accepted" and body["validate_spec"]["ok"] is True and body["revision"] == first
    assert lab.state["finish"]["checkpoint"] == lab.s.head["id"] and len(text) <= 1500
    assert L.tool_result_status(call(tl["facet_finish"], {"status": "matched", "best_checkpoint": best,
                                                          "summary": "s", "judgement": None})) == "accepted"


def test_finish_counts_a_renamed_or_repeated_head_once(lab, monkeypatch):
    """On the lab: A, B, A, B and a pure rename of B (same geometry, other facet and node names) are 2 heads."""
    import glasses3d.g3d_host.spec as GS
    monkeypatch.setattr(GS, "validate_spec", lambda p: {"ok": True, "errors": [], "warnings": [],
                                                        "live_site_would_fail": []})
    tl = tools_of(lab, finish_k=3)
    spec = FX.prada_default()
    renamed = rename(canonical(spec), {"F1": "G1", "F2": "G2", "F3": "G3", "F4": "G4"}, {"C": "Cx", "A": "Ax"})
    renamed["nodes"]["Cx"][2] += 1.0                                        # = B's C + 1 mm, under new names
    steps = [(0, [set_t("C", spec, 1.0)]), (0, []), (1, []), (0, []), (0, G.spec_to_edits(renamed))]
    for k, (parent, edits) in enumerate(steps):          # the Lab method (the rename's v2 crease plane is an
        body = lab.edit(f"e{k}", parent, parent == 0 and k == 4, edits, judgement(lab) if k else None, "x",  # edit
                        OBJECT if k in (0, 4) else None, True, None)          # the strict union leaves to internal
        assert body["status"] == "applied", body                            # callers, geometry module doc)
    revs = [c["revision"] for c in S.seen_compares(lab.state)]
    assert revs == [1, 0, 1, 0, 2] and lab.state["revisions"][2]["sha256"] != lab.state["revisions"][1]["sha256"]
    assert not G.distinct_heads(lab.delta(1, 2))                       # the rename: no change by geometry
    body, *_ = parsed(call(tl["facet_finish"], {"status": "matched", "best_checkpoint": lab.s.head["id"],
                                                "summary": "s", "judgement": judgement(lab)}))
    pre1 = next(m for m in body["missing"] if m["precondition"] == 1)
    assert "2 of 3" in pre1["message"] and "revisions [1, 0]" in pre1["message"]


def test_stuck_uses_the_one_head_rule(lab, monkeypatch):
    """Precondition 5 counts heads with the same rule as precondition 1 (Lab.distinct, the built temples): judgements
    on revision 1, revision 0 and a rename of revision 1 cover 2 distinct heads, not 3; one more real change (and a
    facet_variants result after it) makes the last 3 judgements cover 3."""
    import glasses3d.g3d_host.spec as GS
    monkeypatch.setattr(GS, "validate_spec", lambda p: {"ok": True, "errors": [], "warnings": [],
                                                        "live_site_would_fail": []})
    tl = tools_of(lab, finish_k=2)
    spec = FX.prada_default()
    renamed = rename(canonical(spec), {"F1": "G1", "F2": "G2", "F3": "G3", "F4": "G4"}, {"C": "Cx", "A": "Ax"})
    renamed["nodes"]["Cx"][2] += 1.0                                        # revision 1's geometry, other names
    for k, (edits, reset) in enumerate([([set_t("C", spec, 1.0)], False), ([], False),
                                        (G.spec_to_edits(renamed), True)]):     # the Lab method, as above
        body = lab.edit(f"e{k}", 0, reset, edits, judgement(lab) if k else None, "x",
                        OBJECT if k in (0, 2) else None, True, None)
        assert body["status"] == "applied", body
    assert [c["revision"] for c in S.seen_compares(lab.state)] == [1, 0, 2]
    calls = []
    distinct0 = lab.distinct
    lab.distinct = lambda ra, rb: calls.append((ra, rb)) or distinct0(ra, rb)
    body, *_ = parsed(call(tl["facet_finish"], {"status": "stuck", "best_checkpoint": lab.s.head["id"], "summary": "s",
                                                "judgement": judgement(lab)}))
    pre5 = next(m for m in body["missing"] if m["precondition"] == 5)
    assert "cover 2 distinct heads" in pre5["message"] and (1, 2) in calls     # (1, 2): only precondition 5 asks it
    assert [j["compare_revision"] for j in lab.state["judgements"]] == [1, 0, 2]
    body, *_ = parsed(call(tl["facet_edit"], edit_args(0, [set_t("C", spec, 2.0)])))
    assert body["code"] == "E_OBJECT"                    # the latest note describes the renamed layout (state rule)
    body, *_ = parsed(call(tl["facet_edit"], edit_args(0, [set_t("C", spec, 2.0)], obj=OBJECT)))
    assert body["status"] == "applied", body
    variants = [{"label": lb, "edits": [set_t("C", spec, dt)]} for lb, dt in (("A", -1.0), ("B", 0.0), ("C", 1.0))]
    body, *_ = parsed(call(tl["facet_variants"], {"parent_revision": 0, "variants": variants, "photos": ["05"],
                                                  "judgement": judgement(lab)}))
    assert body["status"] == "ok" and lab.state["variants"][-1]["rendered"], body
    body, *_ = parsed(call(tl["facet_finish"], {"status": "stuck", "best_checkpoint": lab.s.head["id"], "summary": "s",
                                                "judgement": None}))
    assert body["status"] == "refused" and 5 not in [m["precondition"] for m in body["missing"]], body


def test_is_clean_until_a_reading_a_note_or_a_compare(tmp_path):
    """A paid run starts only from a clean package: a stored reading (or its log), an object note or Astra's compare
    each makes it not clean."""
    lab = make_lab(tmp_path, basis=ReadBasis())
    assert lab.is_clean()
    read_c(lab, 9.0)
    assert not lab.is_clean()
    lab.read("drop", [], ["C"])
    assert lab.readings() == {} and not lab.is_clean()                 # the log keeps the reading
    lab2 = make_lab(tmp_path / "b")
    S.record_object(lab2.state, OBJECT, "c1")
    assert not lab2.is_clean()


# --------------------------------------------------------------------------------------------- plumbing
def test_the_state_lock_serialises_mark_unseen_and_save(lab, monkeypatch):
    """mark_unseen (on the event-loop thread, when the SDK times a call out) waits while the worker thread saves the
    state; it never adds keys under json.dumps. The lock is re-entrant (a save inside a mutation)."""
    body, *_ = parsed(call(tools_of(lab)["photo_compare"], compare_args(["05"]), "call_r"))
    entered, gate, done = threading.Event(), threading.Event(), threading.Event()
    save0 = S.save

    def slow_save(path, state):
        entered.set()
        gate.wait(10)
        return save0(path, state)
    monkeypatch.setattr(S, "save", slow_save)
    saver = threading.Thread(target=lab.save)
    saver.start()
    assert entered.wait(10)
    marker = threading.Thread(target=lambda: (lab.mark_unseen("call_r"), done.set()))
    marker.start()
    time.sleep(0.3)
    assert not done.is_set() and "unseen" not in S.compare(lab.state, body["compare_id"])
    gate.set()
    saver.join(10)
    marker.join(10)
    assert done.is_set() and S.compare(lab.state, body["compare_id"])["unseen"] is True
    monkeypatch.setattr(S, "save", save0)
    with lab.state_lock:
        lab.save()
    assert json.loads(lab.state_path.read_text(encoding="utf-8"))["compares"][-1]["unseen"] is True


def test_busy_lock_returns_at_once(lab):
    tl = tools_of(lab)
    gate, entered = threading.Event(), threading.Event()
    original = lab.inspect

    def slow():
        entered.set()
        gate.wait(10)
        return original()
    lab.inspect = slow
    th = threading.Thread(target=lambda: call(tl["facet_inspect"], {}))
    th.start()
    assert entered.wait(10)
    t0 = time.perf_counter()
    body, *_ = parsed(call(tl["photo_image"], {"photo": "05", "crop": None, "enhance": False, "scale": 1}))
    assert time.perf_counter() - t0 < 2.0
    assert body["status"] == "busy" and body["code"] == "E_BUSY" and body["running"]["tool"] == "facet_inspect"
    gate.set()
    th.join(10)
    assert parsed(call(tl["photo_image"], {"photo": "05", "crop": None, "enhance": False, "scale": 1}))[0]["status"] \
        == "ok"


def sdk_call(tool, args: dict, call_id: str, timeout_s: float, context=None):
    """The SDK's own invocation with its timeout (agents.tool.invoke_function_tool, as the runner calls a tool)."""
    from agents.tool import invoke_function_tool
    tool.timeout_seconds = timeout_s
    ctx = ToolContext(context=context, tool_name=tool.name, tool_call_id=call_id, tool_arguments=json.dumps(args))
    return asyncio.run(invoke_function_tool(function_tool=tool, context=ctx, arguments=json.dumps(args)))


def test_sdk_timeout_text_has_the_footer_and_the_unseen_compare_needs_no_judgement(lab):
    """A facet_edit the SDK times out answers E_TIMEOUT with the money / context footer; its thread still applies
    and compares, and that compare (never shown) is recorded unseen: the next action needs no judgement of it."""
    class Status:
        def footer(self):
            return L.footer_text(2.5, 25.0, 31_000)
    tl = tools_of(lab)
    lab.seeing.slow_s = 1.5
    out = sdk_call(tl["facet_edit"], edit_args(0, [set_t("C", FX.prada_default(), 0.7)], obj=OBJECT, photos=["05"]),
                   "call_slow", 0.3, context=Status())
    assert isinstance(out, str)                                        # asyncio.run waited for the worker thread
    body, footer = json.loads(out.rsplit("\n", 1)[0]), out.rsplit("\n", 1)[1]
    assert body["status"] == "timeout" and body["code"] == "E_TIMEOUT" and "timed out after 0.3 s" in body["message"]
    assert footer == "settled $2.50 / cap $25.00 · context 31000 tokens"
    lab.seeing.slow_s = 0.0
    made = [c for c in lab.state["compares"] if c["call_id"] == "call_slow"]
    assert len(made) == 1 and made[0]["unseen"] is True and made[0]["judged"] is True
    assert S.pending_compare(lab.state) is None and S.seen_compares(lab.state) == []
    assert lab.head_record()["revision"] == 1                          # the apply happened
    body, *_ = parsed(call(tl["facet_edit"], edit_args(1, [set_t("C", FX.prada_default(), 1.4)], photos=["05"])))
    assert body["status"] == "applied"                                 # no E_JUDGEMENT for the unseen compare


def test_timed_out_variants_never_meet_precondition_2(lab):
    """A facet_variants call the SDK times out still renders in its thread, but Astra never saw its sheets: the result
    is recorded unseen and finish precondition 2 stays missing (both orders: the SDK gives up before or after the
    result is recorded); a later call that returns counts. (B is C + 0.3 mm: an arm equal to the head, C + 0.0 here,
    is no variant, v3p3 e2e P3-4.)"""
    tl = tools_of(lab)
    spec = FX.prada_default()
    variants = [{"label": lb, "edits": [set_t("C", spec, dt)]} for lb, dt in (("A", -1.0), ("B", 0.3), ("C", 1.0))]
    args = {"parent_revision": 0, "variants": variants, "photos": ["05"], "judgement": None}
    read0 = lab.s.call_read

    def slow_read(*a, **kw):
        time.sleep(1.5)
        return read0(*a, **kw)
    lab.s.call_read = slow_read
    out = sdk_call(tl["facet_variants"], args, "call_var_slow", 0.3)
    lab.s.call_read = read0
    assert json.loads(out.rsplit("\n", 1)[0])["code"] == "E_TIMEOUT"
    rec = lab.state["variants"][-1]
    assert rec["call_id"] == "call_var_slow" and rec["spread"] == ["A", "B", "C"] and rec["unseen"] is True
    assert S.seen_variants(lab.state) == []

    def missing():
        return [m["precondition"] for m in S.finish_missing(lab.state, status="matched", best_revision=0, k=0,
                                                            distinct=lambda a, b: True, camera_photos=[],
                                                            has_object=True)]
    assert missing() == [2]
    body, *_ = parsed(call(tools_of(lab)["facet_variants"], args, "call_var_late"))
    assert body["spread_ok"] is True and missing() == []
    lab.mark_unseen("call_var_late")                                    # the SDK gave up after the result
    assert missing() == [2] and S.seen_variants(lab.state) == []
    body, *_ = parsed(call(tools_of(lab)["facet_variants"], args, "call_var_ok"))
    assert body["spread_ok"] is True and missing() == []


def test_timed_out_finish_never_records_the_finish(lab, monkeypatch):
    """A facet_finish the SDK times out (its restore and export still run in the thread) answers E_TIMEOUT and the
    run goes on: its accepted finish is kept as finish_unseen, never as the run's finish (both orders)."""
    import glasses3d.g3d_host.spec as GS
    monkeypatch.setattr(GS, "validate_spec", lambda p: {"ok": True, "errors": [], "warnings": [],
                                                        "live_site_would_fail": []})
    tl = tools_of(lab, finish_k=3)
    cps = _distinct_heads_and_variants(lab, tl)
    parsed(call(tl["photo_compare"], compare_args(CAMERA_IDS)))
    args = {"status": "matched", "best_checkpoint": cps[-1], "summary": "s", "judgement": judgement(lab, cps[-1])}
    lab.seeing.slow_s = 1.5
    out = sdk_call(tl["facet_finish"], args, "call_fin_slow", 0.3)
    lab.seeing.slow_s = 0.0
    assert json.loads(out.rsplit("\n", 1)[0])["code"] == "E_TIMEOUT"
    assert lab.state["finish"] is None and [f["call_id"] for f in lab.state["finish_unseen"]] == ["call_fin_slow"]
    args["judgement"] = None
    body, *_ = parsed(call(tools_of(lab, finish_k=3)["facet_finish"], args, "call_fin_late"))
    assert body["status"] == "accepted" and lab.state["finish"]["call_id"] == "call_fin_late"
    lab.mark_unseen("call_fin_late")                                    # the SDK gave up after the record
    assert lab.state["finish"] is None and len(lab.state["finish_unseen"]) == 2


def test_the_job_rig_lights_every_compare_and_variant_render(lab):
    """The lab's rig (lab.json; studio_dome_v1, the owner's choice of 2026-10-11, is the package default) lights
    photo_compare and facet_edit's compares (native_linear: a photo of another colour is seeing's gloss_black) and
    facet_variants' renders."""
    from glasses3d.agent.temple_lab import cli
    assert cli.DEFAULT_RIG == MATCHED_RIG == lab.rig == "studio_dome_v1"
    tl = tools_of(lab)
    parsed(call(tl["photo_compare"], compare_args(["05"])))
    assert lab.seeing.calls[-1]["rig"] == "studio_dome_v1" and lab.seeing.calls[-1]["passes"] == ["native_linear"]
    parsed(call(tl["facet_edit"], edit_args(0, [set_t("C", FX.prada_default(), 0.5)], obj=OBJECT,
                                            judge=judgement(lab), photos=["05"])))
    assert lab.seeing.calls[-1]["rig"] == "studio_dome_v1"
    spec = FX.prada_default()
    parsed(call(tl["facet_variants"], {"parent_revision": 0, "photos": ["05"], "judgement": judgement(lab),
                                       "variants": [{"label": lb, "edits": [set_t("C", spec, dt)]}
                                                    for lb, dt in (("A", -1.0), ("B", 1.0))]}))
    assert lab.s.read_params[-1]["rig"] == "studio_dome_v1"


def test_mark_unseen_after_the_compare_was_recorded(lab):
    """The other order: the call's compare is recorded first, then the SDK gives up on the call."""
    body, *_ = parsed(call(tools_of(lab)["photo_compare"], compare_args(["05"]), "call_late"))
    assert S.pending_compare(lab.state)["compare_id"] == body["compare_id"]
    lab.mark_unseen("call_late")
    assert S.pending_compare(lab.state) is None and S.compare(lab.state, body["compare_id"])["unseen"] is True


def test_compare_render_deadline_is_the_tool_hard_timeout(lab):
    assert lab.seeing.calls[-1]["render_deadline_s"] is None              # prepare's compare: seeing's default
    tl = tools_of(lab)
    parsed(call(tl["photo_compare"], compare_args(["05"])))
    assert lab.seeing.calls[-1]["render_deadline_s"] == L.hard_timeout_s("photo_compare") < \
        L.sdk_timeout_s("photo_compare")
    parsed(call(tl["facet_edit"], edit_args(0, [set_t("C", FX.prada_default(), 0.5)], obj=OBJECT,
                                            judge=judgement(lab), photos=["05"])))
    assert lab.seeing.calls[-1]["render_deadline_s"] == L.hard_timeout_s("facet_edit") < L.sdk_timeout_s("facet_edit")


def test_the_scene_lock_is_the_labs_and_teardown_waits_for_it(lab, monkeypatch):
    """tools.py uses Lab.lock; exit_checkpoint and close wait for a call still holding it (a timed-out one), and
    give up after LOCK_WAIT_S with an error (close still closes the worker)."""
    tl = tools_of(lab)
    lab.s.ck_dir.mkdir(parents=True, exist_ok=True)
    (lab.s.ck_dir / lab.s.head["file"]).write_bytes(b"")
    lab.s.head["sha256"] = hashlib.sha256(b"").hexdigest()
    assert lab.lock.acquire(timeout=1)
    try:
        body, *_ = parsed(call(tl["photo_image"], {"photo": "05", "crop": None, "enhance": False, "scale": 1}))
        assert body["code"] == "E_BUSY"
        threading.Timer(0.4, lab.lock.release).start()
        t0 = time.perf_counter()
        assert lab.exit_checkpoint()["verified"] is True and time.perf_counter() - t0 >= 0.3
    finally:
        if lab.lock.locked():
            lab.lock.release()
    monkeypatch.setattr(LB, "LOCK_WAIT_S", 0.1)
    lab.lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="still held the scene"):
            lab.exit_checkpoint()
        with pytest.raises(RuntimeError, match="closed under it"):
            lab.close()
        assert lab.s.closed == 1
    finally:
        lab.lock.release()
    lab.close()
    assert lab.s.closed == 2 and not lab.lock.locked()


def test_worker_failure_stays_fatal_and_build_failure_rolls_back(lab):
    tl = tools_of(lab)
    head = lab.s.head["id"]
    lab.s.fail_build = "E_MULTI_MATERIAL"
    body, *_ = parsed(call(tl["facet_edit"], edit_args(0, [set_t("C", FX.prada_default(), 1)], obj=OBJECT)))
    assert body["status"] == "refused" and body["stage"] == "blender" and body["code"] == "E_MULTI_MATERIAL"
    assert lab.s.head["fingerprint"] == lab.s.index.get(head)["fingerprint"]
    lab.s.fail_build = "raise"
    with pytest.raises(WorkerFailed):
        call(tl["facet_edit"], edit_args(0, [set_t("C", FX.prada_default(), 2)]))
    assert lab.s.head["fingerprint"] == lab.s.index.get(head)["fingerprint"]


def test_footer_from_the_run_context(lab):
    class Status:
        def footer(self):
            return L.footer_text(1.234, 20.0, 123456)
    body, footer, *_ = parsed(call(tools_of(lab)["photo_image"], {"photo": "05", "crop": None, "enhance": False,
                                                                  "scale": 1}, context=Status()))
    assert footer == "settled $1.23 / cap $20.00 · context 123456 tokens"


def test_context_stubs_keep_latest_two_and_best(lab):
    tl = tools_of(lab)
    spec = FX.prada_default()
    shas = []
    for k, dt in enumerate((0.5, 1.0, 1.5, 2.0)):
        out = call(tl["facet_edit"], edit_args(0, [set_t("C", spec, dt)], obj=OBJECT if not k else None,
                                               photos=["05"], judge=judgement(
                                                   lab, best=lab.state["heads"][1]["checkpoint"] if k > 1 else None)
                                               if k else None))
        shas.append([hashlib.sha256(__import__("base64").b64decode(i.image_url.split(",", 1)[1])).hexdigest()
                     for i in out[1:]])
    stubs = lab.context_stubs([s for row in shas for s in row])
    compares = [row[-1] for row in shas]                                  # each edit's compare image (photo 05)
    charts = [s for row in shas for s in row[:-1]]
    assert compares[1] in stubs and compares[0] not in stubs           # compares[0] = best_checkpoint's revision
    assert compares[2] not in stubs and compares[3] not in stubs      # the latest two
    assert not set(charts) & set(stubs)                                 # charts are never stubbed
    assert "superseded" in stubs[compares[1]]


def test_resume_rollback_ignores_prepare_and_finds_the_lost_apply(lab):
    """loop.plan_rollback over the lab journal: prepare's apply is never rolled back; an apply whose turn was not
    recorded in the session is."""
    assert L.plan_rollback([], lab.journal_entries()) is None and lab.is_clean()
    before = lab.s.head["id"]
    call(tools_of(lab)["facet_edit"], edit_args(0, [set_t("C", FX.prada_default(), 0.4)], obj=OBJECT), "call_lost")
    plan = L.plan_rollback([], lab.journal_entries())
    assert plan["call_id"] == "call_lost" and plan["checkpoint_before"] == before and not lab.is_clean()
    recorded = [{"type": "function_call", "call_id": "call_lost"},
                {"type": "function_call_output", "call_id": "call_lost", "output": "x"}]
    assert L.plan_rollback(recorded, lab.journal_entries()) is None


def test_facet_run_settings():
    s = T.run_settings(effort="max", max_turns=None, finish_k=5)
    assert s.behavior() == {"tool_choice": "required", "reset_tool_choice": False,
                            "finish": {"tool": "facet_finish", "behavior": L.FINISH_BEHAVIOR, "k": 5},
                            "context_guard": {"input_tokens": 240_000, "filter": L.CONTEXT_GUARD}}


# --------------------------------------------------------------------------------------------- real Blender
REPO = Path(__file__).resolve().parents[3]
SCENE = REPO / "automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend"
SCENE_SHA = "56274debb901fb28dea1f93f0a6b6ae8401501f7145573f8dedb68c7d2436c06"
BASIS = REPO / "glasses3d/data/wp6v2/basis/cameras.json"
REFERENCE = REPO / "glasses3d/data/reference/prada"


def real_job(root: Path, rig: str = "studio_dome_v1") -> Path:
    """A facet-lab job on a byte copy of the Prada original with the six target photos and the pose basis."""
    import shutil
    (root / "inputs" / "images").mkdir(parents=True)
    shutil.copy2(SCENE, root / "inputs" / "scene.blend")
    assert hashlib.sha256((root / "inputs" / "scene.blend").read_bytes()).hexdigest() == SCENE_SHA
    doc = json.loads(BASIS.read_text(encoding="utf-8"))
    doc["images"] = {n: r for n, r in doc["images"].items() if r.get("colourway") == "target"}
    photos = {}
    for name, rec in doc["images"].items():
        photos[name] = root / "inputs" / "images" / name
        shutil.copy2(REFERENCE / name, photos[name])
        assert hashlib.sha256(photos[name].read_bytes()).hexdigest() == rec["image_sha256"]
    (root / "inputs" / "cameras.json").write_text(json.dumps(doc), encoding="utf-8")
    GC.import_cameras_json(root, root / "inputs" / "cameras.json", photos, reason="facet lab test")
    (root / "inputs" / "lab.json").write_text(json.dumps({"schema": LAB_SCHEMA, "rig": rig, "assets": [
        {"path": "scene.blend", "sha256": SCENE_SHA}]}), encoding="utf-8")
    return root


@pytest.mark.slow
def test_real_lab_prepare_edit_variants_finish(tmp_path):
    """On real Blender (a copy of Prada, studio_dome_v1): prepare fits revision 0 (the original's template) and
    compares the original and revision 0 at all six photos; facet_edit fits and applies a facet (the worker builds
    the fitted spec) and compares it with its model edges; facet_variants renders without changing the scene;
    facet_finish names what is missing."""
    from glasses3d.g3d_host import blender
    if blender.blender_exe() is None or not SCENE.exists() or not BASIS.exists():
        pytest.skip("Blender 5.2, the Prada scene or the pose basis is not available")
    root = real_job(tmp_path / "job")
    lab = Lab(root)
    try:
        prep = lab.prepare()
    finally:
        lab.close()
    assert prep["status"] == "prepared" and len(lab.state["compares"]) == 2 and lab.is_clean()
    assert all(c["photos"] == PHOTO_IDS for c in lab.state["compares"])
    assert lab.state["revisions"][0]["fit_file"] and lab.spec(0)["facets"] == {}
    lab = Lab(root)
    lab.start()
    try:
        tl = tools_of(lab)
        t_r = float(parse(lab.spec(0)).base_t(25.0, -12.0)) + 0.8
        node = {"op": "set_node", "name": "R", "d_mm": 25.0, "y_mm": -12.0, "t": {"mode": "mm", "mm": t_r}}
        edits = [{"op": "set_node", "name": "P", "d_mm": 10.0, "y_mm": 6.0, "t": {"mode": "base", "mm": None}},
                 {"op": "set_node", "name": "Q", "d_mm": 40.0, "y_mm": 6.0, "t": {"mode": "base", "mm": None}}, node,
                 {"op": "set_facet", "label": "F1", "poly": ["P", "R", "Q"], "plane": {"mode": "flat"},
                  "soft": False}]
        r0_checkpoint = lab.s.head["id"]
        body, _f, images, text = parsed(call(tl["facet_edit"], edit_args(0, edits, obj=OBJECT, note="test",
                                                                         photos=["05", "03"]), "c1"))
        assert body["status"] == "applied" and body["compare_id"] and len(images) == 3, body
        rep = json.loads(Path(lab.state["compares"][-1]["report"]).read_text(encoding="utf-8"))
        assert rep["model_edges"]["edges"] == len(lab.model_edges(body["revision"]))
        fp = lab.s.head["fingerprint"]
        variants = [{"label": lb, "edits": [{**node, "t": {"mode": "mm", "mm": t_r + dt}}]}
                    for lb, dt in (("A", -0.5), ("B", 0.5))]
        body, _f, images, _t = parsed(call(tl["facet_variants"], {"parent_revision": body["revision"],
                                                                  "variants": variants, "photos": ["05"],
                                                                  "judgement": judgement(lab)}, "c2"))
        assert body["status"] == "ok" and len(images) >= 1 and lab.s.head["fingerprint"] == fp
        assert lab.s.fingerprint() == fp                         # the worker's scene, not just the index
        body, *_ = parsed(call(tl["facet_finish"], {"status": "matched", "best_checkpoint": r0_checkpoint,
                                                    "summary": "test", "judgement": None}, "c4"))
        assert body["status"] == "refused" and [m["precondition"] for m in body["missing"]] == [1, 2, 3]
        assert body["missing"][2]["photos"] == PHOTO_IDS                # prepare's compares were never shown to Astra
        assert lab.exit_checkpoint()["verified"] is True
    finally:
        lab.close()
