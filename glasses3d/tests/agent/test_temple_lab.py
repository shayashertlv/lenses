"""The facet lab and its seven tools (plan/WP6_FACETS.md §4.1-4.5) with a fake scene and a fake compare: no Blender,
no network, no model. The geometry is real (g3d.temple /2 core on the fixtures; prada_default is the Prada-layout
fixture the text budgets are asserted on); the Blender side of facet_variants is test_wp2_worker's
test_temple_variants_render_builds_renders_and_leaves_the_scene_unchanged.
"""
import asyncio
import copy
import hashlib
import json
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
from glasses3d.agent.temple_lab import geometry as G  # noqa: E402
from glasses3d.agent.temple_lab import sheets as SH  # noqa: E402
from glasses3d.agent.temple_lab import state as S  # noqa: E402
from glasses3d.agent.temple_lab import tools as T  # noqa: E402
from glasses3d.agent.temple_lab.lab import LAB_SCHEMA, Lab  # noqa: E402
from glasses3d.g3d.temple import build_temple  # noqa: E402
from glasses3d.g3d.temple import fixtures as FX  # noqa: E402
from glasses3d.g3d.temple import report as RP  # noqa: E402
from glasses3d.g3d.temple.spec import TempleError, canonical, parse, spec_sha256  # noqa: E402
from glasses3d.g3d_host.camera import Camera, NormFrame  # noqa: E402

# the pinned §4.2 union (tools.edits_schema()): change it only together with the tool layer and the prompt
EDITS_SCHEMA_SHA256 = "d2a21805361de8da2cc4262b75f02c01404fd6add36edaec12ad9e88302a942d"
PHOTO_IDS = ["01", "02", "03", "04", "05", "06"]
BLACK_IDS = ["B1", "B2", "B3"]          # B1: a calibrated colourway photo (basis black-side); B2, B3: reference only
KINDS = {"01": "side", "02": "angled", "03": "top", "04": "front", "05": "side", "06": "rear"}
DEFAULT = ["01", "02", "05"]
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


class FakeIndex:
    def __init__(self, rows):
        self.rows = rows

    def get(self, cid):
        return next((r for r in reversed(self.rows) if r["id"] == cid), None)


class FakeSession:
    """An in-memory checkpoint chain: a temple_lab_build checkpoints a fingerprint named by the spec sha and answers
    the core receipt lines plus ``blender_lines`` (the Blender layer's own) with the decal move and hinge leaf gap;
    a variant render writes float renders whose brightness depends on the variant (labels in ``refuse_variants`` are
    refused by "Blender"; ``render_error`` fails the whole render)."""

    def __init__(self, root):
        self.root = Path(root)
        self.ck_dir = self.root / "checkpoints"
        self.rows = [{"id": "c0000", "fingerprint": "fp-original", "file": "c0000.blend", "sha256": "0" * 64}]
        self.index = FakeIndex(self.rows)
        self.worker = None
        self.calls = []
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
        built = {x["label"]: {"status": "refused", "code": "E_RELIEF_STEP", "message": "injected"}
                 if x["label"] in self.refuse_variants else {"status": "ok", "overrides": {}}
                 for x in params["variants"]}
        views = []
        for v in params["views"]:
            res = v["camera"]["resolution"]
            for k, name in enumerate(["head"] + [lb for lb, b in built.items() if b["status"] == "ok"]):
                p = out / f"{v['name']}--{name}_native_linear.npz"
                a = np.zeros((res[1], res[0]), np.float16)
                a[res[1] // 4:3 * res[1] // 4, :] = 1.0
                with open(p, "wb") as f:
                    np.savez_compressed(f, rgb=np.full((res[1], res[0], 3), 0.2 + 0.1 * k, np.float16), alpha=a)
                views.append({"name": f"{v['name']}--{name}", "files": {"native_linear": {"path": str(p)}}})
        return {"status": "ok", "call_id": call_id, "result": {"status": "ok", "variants": built,
                                                               "render": {"views": views}, "unchanged": True}}


class FakeSeeing:
    """compare_to_photos writes a stacked image and a report.json per call (the fields the lab reads; ``records``
    replaces a photo's synthetic record, e.g. by one recorded on the real lab). ``slow_s`` sleeps that long first."""

    def __init__(self, session, root):
        self.s, self.root = session, Path(root)
        self.calls = []
        self.records: dict = {}
        self.slow_s = 0.0

    def bounds(self, call_id):
        return {"origin_mm": [0.0, 0.0, 0.0]}

    def _render_params(self, rig, env, light_frame):
        return {"rig": rig, "light_frame": light_frame, "engine": "auto"}

    def export_head(self, call_id=None):
        if self.slow_s:
            time.sleep(self.slow_s)
        p = self.root / "work" / "exports" / f"head-{self.s.head['fingerprint']}.glb"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"glTF fake")
        return {"status": "exported", "path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "fingerprint": self.s.head["fingerprint"], "checkpoint": self.s.head["id"]}

    def compare_to_photos(self, refs=None, passes=(), rig=None, crop="temple", points_px=None, hinge_z_mm=None,
                          call_id=None, render_deadline_s=None):
        self.calls.append({"refs": refs, "passes": list(passes), "rig": rig, "crop": crop, "points_px": points_px,
                           "hinge_z_mm": hinge_z_mm, "call_id": call_id, "render_deadline_s": render_deadline_s})
        if self.slow_s:
            time.sleep(self.slow_s)
        rids = [f"reference-{p}" for p in DEFAULT] if refs == "default" else list(refs)
        d = self.root / "work" / "compare" / call_id
        d.mkdir(parents=True, exist_ok=True)
        photos, rep = {}, {}
        for i, rid in enumerate(rids):
            img = d / f"{rid}.png"
            tint = hashlib.sha256(f"{call_id}{rid}".encode()).digest()
            Image.new("RGB", (300, 400), tuple(tint[:3])).save(img)
            vk = "side" if rid == "black-side" else KINDS[rid.split("-")[1]]
            offs = {s: [{"d_mm": float(dd), "offset_mm": round(0.1 * (dd % 7) - 0.3, 2)} for dd in range(10, 131, 10)]
                    for s in ("+1", "-1")} if vk in ("top", "rear") else None
            rep[rid] = {"view_kind": vk, "temple_iou": 0.912, "px_per_mm": 8.17,
                        "crop": {"box": [20, 30, 300, 210], "k": 0.493}, "outer_edge_offsets_mm": offs,
                        "start_offsets_mm": offs, "stretch": [60.0, 240.0], "background": [255, 255, 255],
                        **self.records.get(rid, {})}
            photos[rid] = {"image": str(img), "text": "x", "cached": False}
            if points_px and rid in points_px:
                photos[rid]["points"] = [{"uv": [u, v], "hit": True, "side": 1, "d_mm": 20.0, "y_mm": 1.5,
                                          "X_mm": 70.0} for u, v in points_px[rid]]
        gains = {"colourways": {"target": {"native_linear": {"gain": [0.9, 0.95, 1.1]}}}, "gloss_black": {"gain": 3.0},
                 "light_frame": {"center": [0, 0, 0], "radius": 80.0}}
        (d / "report.json").write_text(json.dumps({"photos": rep, "gains": gains, "environment": None}),
                                       encoding="utf-8")
        return {"status": "ok", "compare_id": call_id, "checkpoint": self.s.head["id"], "photos": photos,
                "report": str(d / "report.json")}


def lab_photos(root: Path) -> list:
    """lab.json's photo rows as cli.prepare writes them: the six targets, B1 (the basis' black-side) and the
    reference-only B2, B3 (synthetic images under inputs/images)."""
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


def make_lab(root: Path, spec=None, **session) -> Lab:
    """A prepared lab: revision 0 = spec (default prada_default) applied on the fake original and compared at every
    target photo by the prepare step (``session``: FakeSession attributes for that apply, e.g. blender_lines)."""
    (root / "inputs").mkdir(parents=True, exist_ok=True)
    (root / "inputs" / "lab.json").write_text(json.dumps({"schema": LAB_SCHEMA, "rig": "studio_dome_v1",
                                                          "assets": [], "photos": lab_photos(root)}),
                                              encoding="utf-8")
    s = FakeSession(root)
    for k, v in session.items():
        setattr(s, k, v)
    lab = Lab(root, session=s, seeing=FakeSeeing(s, root))
    lab._basis = FakeBasis()
    lab.state["start_checkpoint"], lab.state["start_fingerprint"] = "c0000", "fp-original"
    lab.state["original"] = {"footprints": {"decals": [], "hinge": []}, "chart_mesh": None}
    rid, _ = lab._register(spec or FX.prada_default(), parent=None, kind="template", label="revision 0")
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
    c = S.seen_compares(lab.state)[-1]
    return {"compare_id": c["compare_id"], "versus_compare_id": None, "overall": overall,
            "photos": [{"photo": p, "verdict": overall, "largest_difference": "upper facet too dark at the logo",
                        "at_d_mm": 30.0, "at_y_mm": 5.0} for p in c["photos"]],
            "best_checkpoint": best or lab.s.head["id"]}


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


# --------------------------------------------------------------------------------------------- edits union
def test_edits_schema_is_pinned_and_strict():
    schema = T.edits_schema()
    digest = hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert digest == EDITS_SCHEMA_SHA256, digest
    members = schema["properties"]["edits"]["items"]["anyOf"]
    ops = [schema["$defs"][m["$ref"].split("/")[-1]]["properties"]["op"]["const"] for m in members]
    assert ops == ["set_node", "delete_node", "set_facet", "delete_facet", "set_crease", "delete_crease",
                   "splice_chain", "offset_chain", "set_scalar"]
    for d in schema["$defs"].values():
        assert d["additionalProperties"] is False and set(d["required"]) == set(d["properties"])
    assert "discriminator" not in json.dumps(schema) and '"title"' not in json.dumps(schema)


@pytest.mark.parametrize("name", sorted(FX.FIXTURES))
def test_edits_round_trip_every_fixture(name):
    """From another parent (the slab's chains, no layout; the target's chamfers, which §4.2 has no op for) the
    edits of spec_to_edits rebuild the spec exactly."""
    spec = FX.FIXTURES[name]()
    parent = {**canonical(FX.slab()), "chamfers": canonical(spec)["chamfers"]}
    out, notes = G.apply_edits(parent, G.spec_to_edits(spec), reset_layout=True)
    assert out == canonical(spec) and notes == []
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
        ([{"op": "set_facet", "label": "F9", "poly": ["A", "B", "Q"],
           "plane": {"mode": "nodes", "crease": None, "rise_per_mm": None}, "fade": None}], 0, "does not exist"),
        ([{"op": "set_facet", "label": "F9", "poly": ["A", "B", "C"],
           "plane": {"mode": "crease", "crease": ["A", "B"], "rise_per_mm": None}, "fade": None}], 0, "rise"),
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
    assert notes and all("deleted with facet" in n for n in notes)
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
    0.09 mm coplanar sliver inserted into it, and a facet proud at one end and sunk at the other moved across the
    old majority flip of its facet|base crease (0.0016 mm of t) are no new head."""
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

    def balance(dt):                                      # proud 0.3 mm at d 40, sunk 0.3 mm (+ dt) at d 50
        b = on_slab([_node("E0", 40.0, -3.0, FX.T_ZONE + 0.3), _node("E1", 50.0, -3.0, FX.T_ZONE - 0.3 + dt),
                     _node("E2", 50.0, 1.0, None), _node("E3", 40.0, 1.0, None),
                     _facet("E", ["E0", "E1", "E2", "E3"], ("E0", "E1"), 0.0)])
        return b, next(c["shape"] for c in b.tables["creases"] if c["regions"] == ["E", "base"])
    lo, hi = -0.1, 0.1
    assert balance(lo)[1] != balance(hi)[1]
    while hi - lo > 0.002:                                # bisect the table's majority flip
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


# --------------------------------------------------------------------------------------------- tools
def test_registry_seven_strict_tools_timeouts_and_contract(lab):
    tools = T.registry(lab)
    assert tuple(t.name for t in tools) == T.TOOL_NAMES
    for t in tools:
        assert t.strict_json_schema and t.params_json_schema["additionalProperties"] is False
        assert t.timeout_seconds == L.HARD_TIMEOUT_S[t.name] + L.SDK_TIMEOUT_MARGIN_S
    text = "Tools: " + ", ".join(T.TOOL_NAMES) + "."
    assert L.contract_problems(L.ToolContract(frozenset(T.TOOL_NAMES)), instructions=text, brief="b", tools=tools) == []
    enum = tools[1].params_json_schema["properties"]["photo"]["enum"]
    assert enum == PHOTO_IDS + BLACK_IDS                              # photo_image: every photo
    by = {t.name: t.params_json_schema for t in tools}
    assert enums(by["photo_compare"]["properties"]["photos"]) == [PHOTO_IDS + ["B1"]]   # the photos with a camera
    assert enums(by["facet_edit"]["properties"]["photos"]) == [PHOTO_IDS + ["B1"]]
    assert enums(by["facet_variants"]["properties"]["photos"]) == [PHOTO_IDS]            # target material only
    assert PHOTO_IDS + ["B1"] in enums(by["facet_edit"]) and PHOTO_IDS + BLACK_IDS not in enums(by["facet_edit"])
    finish = next(t for t in T.registry(lab, L.RunSettings(effort="max", finish_k=6)) if t.name == "facet_finish")
    assert "at least 6" in finish.description and "at every target photo" in finish.description


def test_facet_inspect_echoes_the_spec_once_with_station_table(lab):
    out = call(tools_of(lab)["facet_inspect"], {})
    body, footer, images, text = parsed(out)
    assert body["status"] == "ok" and body["head"]["revision"] == 0
    assert body["spec"]["stations"]["columns"] == ["d", "y_top", "y_bot", "x_in", "t_top", "t_bot"]
    assert set(body["spec"]["nodes"]) >= {"A", "B", "C"} and body["spec"]["facets"]["F1"]["poly"]
    assert body["receipt"]["lines"][0].startswith("F1|F2 ridge")
    assert footer == "settled n/a / cap n/a · context n/a tokens"
    assert [i.detail for i in images] == ["high", "high"]
    assert len(text) <= 6000, len(text)
    for im in images:
        raw = __import__("base64").b64decode(im.image_url.split(",", 1)[1])
        assert S.load(lab.state_path, "x")["images"][hashlib.sha256(raw).hexdigest()]["kind"] in ("chart", "sections")


def test_photo_image_native_crop_and_refusal(lab):
    t = tools_of(lab)["photo_image"]
    body, _f, images, _t = parsed(call(t, {"photo": "05", "crop": [10, 20, 110, 70]}))
    assert body["transform"]["box"] == [10, 20, 110, 70] and body["transform"]["k"] == 1.0
    assert len(images) == 1
    body, *_ = parsed(call(t, {"photo": "05", "crop": [0, 0, 999, 10]}))
    assert body["status"] == "refused" and body["code"] == "E_ARGS"
    out = call(t, {"photo": "07", "crop": None})                       # not in the enum: the SDK's schema refusal
    assert isinstance(out, str) and "Invalid JSON input for tool photo_image" in out


def test_photo_compare_returns_compare_id_and_charts_points(lab):
    t = tools_of(lab)["photo_compare"]
    body, _f, images, text = parsed(call(t, {"photos": ["05", "03"], "crop": None,
                                             "points_px": [{"photo": "05", "points": [[100.0, 120.0]]}]}))
    assert body["status"] == "ok" and body["compare_id"].startswith("cmp") and len(images) == 2
    assert lab.seeing.calls[-1]["passes"] == ["native_linear", "gloss_black"]
    assert lab.seeing.calls[-1]["rig"] == "studio_dome_v1" and lab.seeing.calls[-1]["hinge_z_mm"] == -10.6
    assert "d 20.00 y 1.50 t " in body["photos"]["05"]
    assert "+1 [" in body["photos"]["03"] and "start [" in body["photos"]["03"]
    assert S.pending_compare(lab.state)["compare_id"] == body["compare_id"]
    body, *_ = parsed(call(t, {"photos": ["05"], "crop": None, "points_px": [{"photo": "03", "points": [[1, 1]]}]}))
    assert body["status"] == "refused" and body["code"] == "E_ARGS"


def test_black_photos_image_compare_judge_and_refusals(lab):
    """The black photos: B1 (a calibrated colourway photo) is shown and compared (seeing gets its basis reference,
    the text names its rows) and judged like any photo; B2 and B3 (no camera) are photo_image only, and every
    compare path refuses them with a message naming photo_image; facet_variants takes target photos only; finish
    precondition 3 and the default compare set stay the six target photos."""
    assert lab.photo_ids() == PHOTO_IDS and lab.compare_ids() == PHOTO_IDS + ["B1"]
    assert lab.image_ids() == PHOTO_IDS + BLACK_IDS and lab.pid("black-side") == "B1" and lab.rid("B1") == "black-side"
    tl = tools_of(lab)
    for pid, view in (("B2", "angled"), ("B3", "front"), ("B1", "side")):
        body, _f, images, _t = parsed(call(tl["photo_image"], {"photo": pid, "crop": [0, 0, 64, 40]}))
        assert body["status"] == "ok" and body["view"] == view and body["colour"] == "black" and len(images) == 1
    body, *_ = parsed(call(tl["photo_image"], {"photo": "05", "crop": None}))
    assert "colour" not in body
    body, _f, images, _t = parsed(call(tl["photo_compare"], {"photos": ["B1", "05"], "crop": None, "points_px": [
        {"photo": "B1", "points": [[100.0, 120.0]]}]}))
    assert body["status"] == "ok" and list(body["photos"]) == ["B1", "05"] and len(images) == 2
    assert lab.seeing.calls[-1]["refs"] == ["black-side", "reference-05"]
    assert set(lab.seeing.calls[-1]["points_px"]) == {"black-side"}
    assert body["photos"]["B1"].startswith("side black: rows photo/overlay/gloss-black IoU ")
    assert "d 20.00 y 1.50 t " in body["photos"]["B1"] and "black" not in body["photos"]["05"]
    assert S.pending_compare(lab.state)["photos"] == ["B1", "05"]
    j = judgement(lab)
    assert [p["photo"] for p in j["photos"]] == ["B1", "05"]
    body, *_ = parsed(call(tl["facet_edit"], {"parent_revision": 0, "reset_layout": False, "judgement": j,
                                              "edits": [set_t("C", FX.prada_default(), 0.5)], "note": "x",
                                              "apply": True, "photos": ["B1"]}))
    assert body["status"] == "applied" and list(body["photos"]) == ["B1"] and lab.state["judgements"][-1]["photos"]
    # B2 / B3: outside the compare enums (the SDK refuses the arguments), and the lab names photo_image
    out = call(tl["photo_compare"], {"photos": ["B2"], "crop": None, "points_px": None})
    assert isinstance(out, str) and "Invalid JSON input for tool photo_compare" in out
    for fn in (lambda: lab.compare("x", ["B2"]), lambda: lab.compare("x", ["05"], None, [{"photo": "B3",
                                                                                        "points": [[1, 1]]}])):
        with pytest.raises(S.LabRefusal, match="photo_image only") as e:
            fn()
        assert e.value.code == "E_ARGS"
    out = call(tl["facet_variants"], {"parent_revision": 0, "photos": ["B1"], "judgement": judgement(lab),
                                      "variants": [{"label": "A", "edits": []}, {"label": "B", "edits": []}]})
    assert isinstance(out, str) and "Invalid JSON input for tool facet_variants" in out
    with pytest.raises(S.LabRefusal, match="not a target photo"):
        lab.variants("x", 0, [{"label": "A", "edits": []}, {"label": "B", "edits": []}], ["B1"], judgement(lab))
    body, *_ = parsed(call(tl["facet_inspect"], {}))
    assert body["photos"]["B1"] == "side, black, photo_compare" and body["photos"]["B2"] == \
        "angled, black, photo_image only" and body["photos"]["05"] == "side"
    # the default set and finish precondition 3 are the target photos
    parsed(call(tl["photo_compare"], {"photos": None, "crop": None, "points_px": None}))
    assert lab.seeing.calls[-1]["refs"] == "default"
    miss = S.finish_missing(lab.state, status="matched", best_revision=1, k=1, distinct=lab.distinct,
                            target_photos=lab.photo_ids())
    assert next(m for m in miss if m["precondition"] == 3)["photos"] == ["03", "04", "06"]     # B1 never needed
    # a reference-only photo whose bytes changed is refused
    (lab.root / "inputs" / "images" / "black-front.png").write_bytes(b"changed")
    body, *_ = parsed(call(tl["photo_image"], {"photo": "B3", "crop": None}))
    assert body["status"] == "refused" and body["code"] == "E_STATE"


def test_facet_edit_refusals_return_at_once_and_change_nothing(lab):
    t = tools_of(lab)["facet_edit"]
    n_rev, head = len(lab.state["revisions"]), lab.s.head["id"]
    body, *_ = parsed(call(t, {"parent_revision": 0, "reset_layout": False, "judgement": None, "note": "x",
                               "apply": True, "photos": None,
                               "edits": [set_t("C", FX.prada_default(), 0.2), {"op": "delete_node", "name": "B"}]}))
    assert body["status"] == "refused" and body["edit"] == 1 and body["code"] == "E_EDIT"
    body, *_ = parsed(call(t, {"parent_revision": 0, "reset_layout": False, "judgement": None, "note": "x",
                               "apply": True, "photos": None, "edits": [set_t("A", FX.prada_default(), 5.0)]}))
    assert body["status"] == "refused" and body["stage"] == "build" and body["code"].startswith("E_")
    body, *_ = parsed(call(t, {"parent_revision": 9, "reset_layout": False, "judgement": None, "note": "x",
                               "apply": True, "photos": None, "edits": []}))
    assert body["code"] == "E_REVISION"
    assert len(lab.state["revisions"]) == n_rev and lab.s.head["id"] == head


def test_facet_edit_applies_compares_and_fits_the_text_budget(lab):
    t = tools_of(lab)["facet_edit"]
    edits = [set_t("C", FX.prada_default(), 0.6), {"op": "set_scalar", "key": "roll_mm", "value": 0.3}]
    body, footer, images, text = parsed(call(t, {"parent_revision": 0, "reset_layout": False, "edits": edits,
                                                 "judgement": None, "note": "deeper C, rolled edges", "apply": True,
                                                 "photos": None}, "call_e1"))
    assert body["status"] == "applied" and body["revision"] == 1 and body["parent"] == 0
    assert body["diff"].startswith("~node C t 8.0->8.6") and "roll_mm 0.0->0.3" in body["diff"]
    assert body["receipt"]["lines"][0].startswith("F1|F2 ridge") and body["blender"]["decal_max_move_mm"] == 0.61
    assert body["compare_id"] and list(body["photos"]) == DEFAULT
    assert len(images) == 2 + 3 and footer.startswith("settled ")
    assert len(text) <= 1500, len(text)                                   # §4.5 on the Prada-layout fixture
    head = S.head_by_fingerprint(lab.state, lab.s.head["fingerprint"])
    assert head["revision"] == 1 and lab.state["events"][-1]["tool"] == "facet_edit"
    # the top and rear photos carry the offsets: still within the budget
    body, _f, _i, text = parsed(call(t, {"parent_revision": 1, "reset_layout": False, "judgement": judgement(lab),
                                         "edits": [set_t("C", FX.prada_default(), 0.9)], "note": "x", "apply": True,
                                         "photos": ["03", "06"]}))
    assert body["status"] == "applied" and len(text) <= 1500, len(text)


# Recorded on the real lab (T5 rehearsal 2026-10-11, data/wp6v2/lab/t5): the compare of the two-facet roof edit H3
# (cmp012; view, temple IoU, px/mm, crop box and k, outer-edge offsets photo minus model at d 10..130 every 10 mm) and
# the Blender layer's own lines of its apply and of prepare's revision 0 (lab_calls 0002, 0011). Its facet_edit text
# was 2,006 characters before the §4.5 trim.
T5_PHOTOS = {
    "01": ("side", 0.870, 8.171, [0, 50, 1298, 647], 0.3836, None),
    "02": ("angled", 0.879, 8.436, [303, 426, 1860, 890], 0.4935, None),
    "03": ("top", 0.817, 7.387, [88, 0, 1360, 895], 0.2559,
           {"+1": [-0.66, 1.21, 2.85, 1.92, -0.41, -0.42, -1.2, -0.99, -0.72, -0.87, -0.29, None, None],
            "-1": [-0.8, 1.07, 2.71, 1.99, -0.54, -0.56, -0.99, -1.0, -0.57, -0.87, -0.3, None, None]}),
    "04": ("front", 0.890, 7.320, [0, 35, 1190, 373], 0.6775, None),      # T5's compare_all (cmp017)
    "05": ("side", 0.877, 7.473, [49, 30, 1226, 572], 0.4225, None),
    "06": ("rear", 0.782, 6.572, [154, 47, 1364, 543], 0.4617,
           {"+1": [None, None, -0.94, -1.56, -0.47, -3.56, -3.39, -3.22, -3.36, -3.65, -3.03, -0.9, 0.0],
            "-1": [None, None, -0.94, -1.56, -0.62, -3.71, -3.39, -3.22, -3.21, -3.65, -2.57, -0.75, 0.15]}),
}
T5_PREPARE_LINES = ["W_ARM_NORMAL_SPREAD: smooth outer edges bend up to 61.5 deg (> 30.0)",
                    "W_HINGE_LEAF: hinge leaf Hinge right | temple mounting leaf embedded 0.84 mm",
                    "W_HINGE_LEAF: hinge leaf Hinge left | temple mounting leaf embedded 0.84 mm"]
T5_EDIT_LINES = ["W_ARM_NORMAL_SPREAD: smooth outer edges bend up to 61.5 deg (> 30.0)",
                 "W_DECAL_SPANS_CREASE: decal Temple right | PRADA gold hallmark spans D|U",
                 "W_HINGE_LEAF: hinge leaf Hinge right | temple mounting leaf embedded 0.84 mm",
                 "W_DECAL_SPANS_CREASE: decal Temple left | PRADA gold hallmark spans D|U",
                 "W_HINGE_LEAF: hinge leaf Hinge left | temple mounting leaf embedded 0.84 mm"]
T5_GAP_MM = -0.836


def t5_records() -> dict:
    out = {}
    for pid, (kind, iou, ppm, box, k, offs) in T5_PHOTOS.items():
        rows = None if offs is None else {side: [{"d_mm": 10.0 * (i + 1), "offset_mm": v} for i, v in enumerate(vals)]
                                          for side, vals in offs.items()}
        out[f"reference-{pid}"] = {"view_kind": kind, "temple_iou": iou, "px_per_mm": ppm,
                                   "crop": {"box": box, "k": k}, "outer_edge_offsets_mm": rows,
                                   "start_offsets_mm": rows}
    return out


def roof_edits(r0: dict, rise: float) -> list:
    """T5's scripted two-facet roof (H3 / H4) on revision 0's chains: four W_BORDER_RAMP runs."""
    from glasses3d.g3d.temple.spec import parse
    ps = parse(r0)

    def node(name, d, y, t):
        mode = "mm" if isinstance(t, float) else "solve"
        return {"op": "set_node", "name": name, "d_mm": d, "y_mm": y,
                "t": {"mode": mode, "mm": t if mode == "mm" else None}}

    def facet(label, poly):
        return {"op": "set_facet", "label": label, "poly": poly, "fade": None,
                "plane": {"mode": "crease", "crease": ["P", "Q"], "rise_per_mm": rise}}
    return [node("P", 8.0, 1.0, round(float(ps.base_t(8.0, 1.0)) + 0.8, 3)),
            node("Q", 45.0, 0.0, round(float(ps.base_t(45.0, 0.0)) + 0.8, 3)),
            node("PT", 8.0, 30.0, None), node("QT", 45.0, 30.0, None), node("PB", 8.0, -30.0, None),
            node("QB", 45.0, -30.0, None), facet("U", ["P", "Q", "QT", "PT"]), facet("D", ["Q", "P", "PB", "QB"]),
            {"op": "set_crease", "facets": ["D", "U"], "shape": "ridge"}]


class WorstFooter:
    """The longest footer a facet run prints (a two-digit spend, a three-digit cap, a six-digit context)."""

    def footer(self):
        return L.footer_text(99.99, 100.0, 999_999)


@pytest.mark.parametrize("photos", [PHOTO_IDS, ["01", "02", "03", "05", "06"], ["03", "06"]])
def test_facet_edit_text_budget_on_recorded_t5_outputs(tmp_path, photos):
    """§4.5 on the real T5 numbers: the H3 roof edit's facet_edit text with the recorded compare records (all six
    photos, the default five, top and rear), Blender lines, revision-0 warnings and the longest footer is <= 1500
    characters; the border ramps are one line; the warnings inherited from revision 0 (W_ARM_NORMAL_SPREAD,
    W_HINGE_LEAF, the leaf gap) are left out; the layout reset_layout cleared is counted, not named."""
    lab = make_lab(tmp_path, blender_lines=T5_PREPARE_LINES, gap_mm=T5_GAP_MM)
    lab.seeing.records = t5_records()
    lab.s.blender_lines = T5_EDIT_LINES
    body, footer, images, text = parsed(call(tools_of(lab)["facet_edit"], {
        "parent_revision": 0, "reset_layout": True, "edits": roof_edits(lab.spec(0), -0.12), "judgement": None,
        "note": "TOOL TEST: the T5 roof", "apply": True, "photos": photos}, context=WorstFooter()))
    assert body["status"] == "applied", body
    assert footer == WorstFooter().footer() and list(body["photos"]) == photos
    assert len(text) <= 1500, (len(text), text)
    assert "-4 facets" in body["diff"] and "-5 creases" in body["diff"] and "F1|F2" not in body["diff"]
    lines = body["receipt"]["lines"]
    ramps = [ln for ln in lines if ln.startswith("W_BORDER_RAMP")]
    assert len(ramps) == 1 and ramps[0].count(";") >= 1, lines
    blob = json.dumps(body, ensure_ascii=False)
    assert "W_ARM_NORMAL_SPREAD" not in blob and "W_HINGE_LEAF" not in blob and "hinge_leaf_gap_mm" not in blob
    assert body["blender"]["lines"] == ["W_DECAL_SPANS_CREASE: decal Temple right | PRADA gold hallmark spans D|U (x2)"]
    assert "+1 [-0.7 +1.2 +2.9 +1.9" in body["photos"]["03"] and "d 10..110/10" in body["photos"]["03"]
    if "06" in photos:
        assert "d 30..130/10:" in body["photos"]["06"] and " na" not in body["photos"]["06"]


def test_t5_recorded_rehearsal_text_budget():
    """§4.5 asserted on the latest recorded T5 rehearsal (data/wp6v2/lab/t5/rehearsal.json) when it exists."""
    path = Path(__file__).resolve().parents[2] / "data" / "wp6v2" / "lab" / "t5" / "rehearsal.json"
    if not path.is_file():
        pytest.skip("no recorded T5 rehearsal")
    rec = json.loads(path.read_text(encoding="utf-8"))
    budget = {"facet_edit": 1500, "facet_variants": 1500, "facet_restore": 1000}
    over = [(s["step"], s["text_chars"]) for s in rec["steps"] if s["tool"] in budget
            and s["text_chars"] > budget[s["tool"]]]
    assert not over, over


def test_inherited_warnings_reported_once_and_shown_again_when_they_change(tmp_path):
    lab = make_lab(tmp_path, blender_lines=T5_PREPARE_LINES, gap_mm=T5_GAP_MM)
    assert lab.state["inherited"]["blender"] == T5_PREPARE_LINES and lab.state["inherited"]["hinge_leaf_gap_mm"] == \
        pytest.approx(-0.84)
    tl = tools_of(lab)
    body, *_ = parsed(call(tl["facet_inspect"], {}))
    inh = body["inherited_from_revision_0"]
    assert inh[0] == T5_PREPARE_LINES[0] and inh[1] == T5_PREPARE_LINES[1] + " (x2)"
    assert inh[2].startswith("hinge_leaf_gap_mm -0.84")
    lab.s.blender_lines, lab.s.gap_mm = T5_PREPARE_LINES[:1] + [ln.replace("0.84", "0.86") for ln in
                                                                T5_PREPARE_LINES[1:]], -0.858
    body, *_ = parsed(call(tl["facet_edit"], {"parent_revision": 0, "reset_layout": False, "judgement": None,
                                              "edits": [set_t("C", FX.prada_default(), 0.5)], "note": "x",
                                              "apply": True, "photos": ["05"]}))
    assert "lines" not in body["blender"] and "hinge_leaf_gap_mm" not in body["blender"]       # within tolerance
    lab.s.blender_lines, lab.s.gap_mm = [ln.replace("0.84", "1.50").replace("61.5", "75.0")
                                         for ln in T5_PREPARE_LINES], -1.5
    body, *_ = parsed(call(tl["facet_edit"], {"parent_revision": 0, "reset_layout": False,
                                              "judgement": judgement(lab), "edits": [set_t("C", FX.prada_default(),
                                                                                           0.9)],
                                              "note": "x", "apply": True, "photos": ["05"]}))
    assert body["blender"]["hinge_leaf_gap_mm"] == -1.5
    assert body["blender"]["lines"] == ["W_ARM_NORMAL_SPREAD: smooth outer edges bend up to 75.0 deg (> 30.0)",
                                        "W_HINGE_LEAF: hinge leaf Hinge right | temple mounting leaf embedded 1.50 mm "
                                        "(x2)"]
    from glasses3d.agent.temple_lab.lab import same_warning
    ref = "W_ARM_NORMAL_SPREAD: smooth outer edges bend up to 61.5 deg (> 30.0)"
    assert same_warning(ref.replace("61.5", "64.0"), ref) and not same_warning(ref.replace("61.5", "68.0"), ref)
    assert not same_warning(ref.replace("smooth", "sharp"), ref)


def test_judgement_rule(lab):
    tl = tools_of(lab)
    body, *_ = parsed(call(tl["facet_edit"], {"parent_revision": 0, "reset_layout": False, "judgement": None,
                                              "edits": [set_t("C", FX.prada_default(), 0.5)], "note": "x",
                                              "apply": True, "photos": None}))
    cid = body["compare_id"]
    base = {"parent_revision": 1, "reset_layout": False, "edits": [], "note": "x", "apply": False, "photos": None}
    body, *_ = parsed(call(tl["facet_edit"], {**base, "judgement": None}))
    assert body["code"] == "E_JUDGEMENT" and cid in body["message"]
    wrong = judgement(lab)
    wrong["compare_id"] = "cmp001"
    assert parsed(call(tl["facet_edit"], {**base, "judgement": wrong}))[0]["code"] == "E_JUDGEMENT"
    short = judgement(lab)
    short["photos"] = short["photos"][:1]
    body, *_ = parsed(call(tl["facet_restore"], {"checkpoint": lab.s.head["id"], "judgement": short}))
    assert body["code"] == "E_JUDGEMENT" and body["missing"] == ["02", "05"]
    bad_best = judgement(lab, best="c9999")
    assert parsed(call(tl["facet_variants"], {"parent_revision": 1, "photos": ["05"], "judgement": bad_best,
                                              "variants": [{"label": "A", "edits": []}, {"label": "B", "edits": []}]}
                       ))[0]["code"] == "E_JUDGEMENT"
    assert not lab.state["judgements"]
    body, *_ = parsed(call(tl["facet_edit"], {**base, "judgement": judgement(lab, overall="better")}))
    assert body["status"] == "built" and lab.state["judgements"][-1]["compare_id"] == cid
    assert lab.state["best_checkpoint"] == lab.s.head["id"] and S.pending_compare(lab.state) is None
    # photo_compare and facet_inspect are not action tools: no judgement needed before them
    assert parsed(call(tl["photo_compare"], {"photos": ["05"], "crop": None, "points_px": None}))[0]["status"] == "ok"
    assert parsed(call(tl["facet_inspect"], {}))[0]["status"] == "ok"


def test_facet_variants_sheets_revisions_and_unchanged_scene(lab):
    tl = tools_of(lab)
    spec = FX.prada_default()
    head_fp = lab.s.head["fingerprint"]
    variants = [{"label": "A", "edits": [set_t("C", spec, -1.0)]}, {"label": "B", "edits": [set_t("C", spec, 0.5)]},
                {"label": "C", "edits": [set_t("C", spec, 1.5)]}, {"label": "Z", "edits": [set_t("A", spec, 5.0)]}]
    body, _f, images, text = parsed(call(tl["facet_variants"], {"parent_revision": 0, "variants": variants,
                                                                "photos": ["05", "03"], "judgement": None}))
    assert body["status"] == "ok" and body["spread_ok"] is True and len(images) == 2
    by = {v["label"]: v for v in body["variants"]}
    assert by["Z"]["revision"] is None and by["Z"]["code"].startswith("E_")
    assert all(by[x]["revision"] is not None and by[x]["vs_head_mm"] > 0.3 for x in "ABC")
    assert lab.state["revisions"][by["B"]["revision"]]["kind"] == "variant"
    assert lab.s.read_fingerprints == [head_fp] and lab.s.head["fingerprint"] == head_fp
    assert len(text) <= 1500, len(text)
    for im in images:
        raw = __import__("base64").b64decode(im.image_url.split(",", 1)[1])
        sheet = Image.open(__import__("io").BytesIO(raw))
        assert sheet.width <= SH.SHEET_W and sheet.height <= SH.SHEET_H
    # a variant becomes the head through facet_edit with no edits
    body, *_ = parsed(call(tl["facet_edit"], {"parent_revision": by["B"]["revision"], "reset_layout": False,
                                              "edits": [], "judgement": None, "note": "apply B", "apply": True,
                                              "photos": ["05"]}))
    assert body["status"] == "applied" and body["revision"] == by["B"]["revision"] and body["diff"] == "no change"


def test_variant_spread_counts_only_rendered_variants(lab):
    """Precondition 2's spread is over the variants rendered at every photo: one refused by Blender leaves two (no
    spread); a failed render leaves none; three rendered ones count."""
    tl = tools_of(lab)
    spec = FX.prada_default()
    variants = [{"label": lb, "edits": [set_t("C", spec, dt)]} for lb, dt in (("A", -1.0), ("B", 0.0), ("C", 1.0))]
    args = {"parent_revision": 0, "variants": variants, "photos": ["05", "02"], "judgement": None}
    lab.s.refuse_variants = {"B"}
    body, _f, images, _t = parsed(call(tl["facet_variants"], args))
    assert body["status"] == "ok" and body["spread_ok"] is False and len(images) == 2
    assert all(v["revision"] is not None for v in body["variants"])       # all three built on the host
    assert body["render"]["blender_refused"]["B"].startswith("E_RELIEF_STEP")
    assert lab.state["variants"][-1]["rendered"] == ["A", "C"] and lab.state["variants"][-1]["spread"] is None
    lab.s.refuse_variants, lab.s.render_error = set(), True
    body, _f, images, _t = parsed(call(tl["facet_variants"], args))
    assert body["spread_ok"] is False and images == [] and body["render"]["status"] == "error"
    assert lab.state["variants"][-1]["rendered"] == []
    assert [m["precondition"] for m in S.finish_missing(lab.state, status="matched", best_revision=0, k=0,
                                                        distinct=lambda a, b: True, target_photos=[])] == [2]
    lab.s.render_error = False
    body, *_ = parsed(call(tl["facet_variants"], args))
    assert body["spread_ok"] is True and lab.state["variants"][-1]["spread"] == ["A", "B", "C"]
    assert lab.s.calls[-1][2] <= L.HARD_TIMEOUT_S["facet_variants"]       # the render's worker deadline


def test_variant_sheet_layout_and_refused_panel(tmp_path):
    img = Image.new("RGB", (600, 180), (200, 180, 160))
    panels = [("photo 05", img, None), ("head", img, None)] + [(f"V{i}", img, None) for i in range(5)] + \
             [("Z: refused E_RELIEF_STEP", None, "Z refused E_RELIEF_STEP: facet F2 meets F1 with a step")]
    sheet = SH.variant_sheet(panels, "variants")
    assert sheet.size[0] <= 1280 and sheet.size[1] <= 900
    cols, pw, ph = SH.variant_layout(8)
    assert cols == 2 and pw <= 640 and ph > 100
    sheet.save(tmp_path / "sheet.png")


LONG = {"N": "N" + "n" * 23, "S": "S" + "s" * 23}       # the longest facet names the edits allow (24 characters)


def test_variant_refusals_keep_the_whole_message(lab, monkeypatch):
    """facet_variants returns a core refusal whole (E_FADE_CLIFF with 24-character facet names is longer than a
    receipt line) and cuts a longer text only at a clause end, never mid-sentence (the reviewer's defect 4,
    2026-10-11: "...or end the cr")."""
    import glasses3d.agent.temple_lab.lab as LB
    with pytest.raises(TempleError) as e:
        build_temple(rename(FX.refusal_cases()["E_FADE_CLIFF"], LONG, {}))
    cliff = e.value
    assert cliff.code == "E_FADE_CLIFF" and LB.LINE_MAX < len(cliff.message) <= LB.REFUSAL_MAX
    real = LB.build_temple

    def refusing(spec):
        try:
            return real(spec)
        except TempleError:
            raise cliff from None
    monkeypatch.setattr(LB, "build_temple", refusing)
    spec = FX.prada_default()
    variants = [{"label": "A", "edits": [set_t("C", spec, -1.0)]}, {"label": "B", "edits": [set_t("C", spec, 0.5)]},
                {"label": "Z", "edits": [set_t("A", spec, 5.0)]}]
    body, *_ = parsed(call(tools_of(lab)["facet_variants"], {"parent_revision": 0, "variants": variants,
                                                             "photos": ["05"], "judgement": None}))
    z = next(v for v in body["variants"] if v["label"] == "Z")
    assert z["code"] == "E_FADE_CLIFF" and z["message"] == cliff.message
    assert z["message"].endswith(f"fade {LONG['S']} the same way, start the fade where the crease ends, or end the "
                                  f"crease at a node")
    text = "the first clause; the second clause. " * 30
    out = LB.brief(text)
    assert len(out) <= LB.REFUSAL_MAX and out.endswith(" ...") and out[:-4].endswith("clause")
    assert LB.brief("one " * 200).endswith("one ...") and LB.brief(cliff.message) == cliff.message


def test_fade_warning_lines_fit_a_receipt_line():
    """W_FADE_HOLD and W_FADE_INERT with 24-character facet names fit LINE_MAX whole, W_FADE_HOLD with 3 characters to
    spare (3-digit d, a 2-digit hold), so the receipt never cuts their advice; W_FADE_INERT counts the facets past
    its budget ("+N more")."""
    import glasses3d.agent.temple_lab.lab as LB
    tb = build_temple(rename(FX.fade_one_side(proud=3.0, edge=0.0), LONG, {}))
    lines = {ln.split(":", 1)[0]: ln for ln in RP.warning_lines(tb)}
    assert len(lines["W_FADE_HOLD"]) + 3 <= LB.LINE_MAX and lines["W_FADE_HOLD"].endswith("(>= 10; > 25 is refused)")
    assert lines["W_FADE_INERT"].endswith(f"{LONG['N']} (held by less-faded neighbours)")
    s = canonical(FX.pyramid())
    for f in s["facets"].values():
        f["fade"] = {"d0": 110.0, "d1": 130.0}                         # beyond every facet: three inert fades
    s = rename(s, {k: k + "x" * 22 for k in s["facets"]}, {})
    w = [x for x in build_temple(s).warnings if x["code"] == "W_FADE_INERT"]
    assert len(w) == 1 and len(w[0]["facets"]) == 3 and w[0]["message"].endswith("; +1 more")
    assert len(f"W_FADE_INERT: {w[0]['message']}") <= LB.LINE_MAX


def test_facet_restore(lab):
    tl = tools_of(lab)
    first = lab.s.head["id"]
    call(tl["facet_edit"], {"parent_revision": 0, "reset_layout": False, "edits": [set_t("C", FX.prada_default(), 1)],
                            "judgement": None, "note": "x", "apply": True, "photos": None})
    body, *_ = parsed(call(tl["facet_restore"], {"checkpoint": "c0000", "judgement": judgement(lab)}))
    assert body["code"] == "E_ARGS"                                       # the original holds no applied revision
    body, footer, _i, text = parsed(call(tl["facet_restore"], {"checkpoint": first, "judgement": None}))
    assert body["status"] == "ok" and body["revision"] == 0 and body["restored_from"] == first
    assert S.head_by_fingerprint(lab.state, lab.s.head["fingerprint"])["revision"] == 0
    assert len(text) <= 1000 and lab.state["events"][-1]["tool"] == "facet_restore"


def _distinct_heads_and_variants(lab, tl):
    spec = FX.prada_default()
    cps = []
    for k, dt in enumerate((0.5, 1.0, 1.5)):
        body, *_ = parsed(call(tl["facet_edit"], {"parent_revision": 0, "reset_layout": False,
                                                  "edits": [set_t("C", spec, dt)], "note": "x", "apply": True,
                                                  "photos": None, "judgement": judgement(lab) if k else None}))
        cps.append(body["checkpoint"])
    variants = [{"label": lb, "edits": [set_t("C", spec, dt)]} for lb, dt in (("A", -1.0), ("B", 0.0), ("C", 1.0))]
    body, *_ = parsed(call(tl["facet_variants"], {"parent_revision": 0, "variants": variants, "photos": ["05"],
                                                  "judgement": judgement(lab, best=cps[0])}))
    assert body["spread_ok"]
    return cps


def test_finish_refused_names_preconditions_then_accepted(lab, monkeypatch):
    import glasses3d.g3d_host.spec as GS
    monkeypatch.setattr(GS, "validate_spec", lambda p: {"ok": True, "errors": [], "warnings": ["w1"],
                                                        "live_site_would_fail": []})
    tl = tools_of(lab, finish_k=3)
    body, *_ = parsed(call(tl["facet_finish"], {"status": "matched", "best_checkpoint": lab.s.head["id"],
                                                "summary": "s", "judgement": None}))
    assert body["status"] == "refused" and [m["precondition"] for m in body["missing"]] == [1, 2, 3]
    assert body["missing"][2]["photos"] == PHOTO_IDS                   # prepare's compares were never shown to Astra
    assert S.DIFFERENT_ATTEMPT in body["missing"][0]["message"]
    cps = _distinct_heads_and_variants(lab, tl)
    best = cps[0]
    body, *_ = parsed(call(tl["facet_finish"], {"status": "matched", "best_checkpoint": best, "summary": "s",
                                                "judgement": None}))
    assert body["status"] == "refused" and [m["precondition"] for m in body["missing"]] == [3]
    assert set(body["missing"][0]["photos"]) == {"03", "04", "06"}
    parsed(call(tl["facet_restore"], {"checkpoint": best, "judgement": None}))
    parsed(call(tl["photo_compare"], {"photos": PHOTO_IDS, "crop": None, "points_px": None}))
    body, *_ = parsed(call(tl["facet_finish"], {"status": "stuck", "best_checkpoint": best, "summary": "s",
                                                "judgement": judgement(lab, best=best, overall="better")}))
    assert body["status"] == "refused" and [m["precondition"] for m in body["missing"]] == [4]
    body, footer, images, text = parsed(call(tl["facet_finish"], {"status": "matched", "best_checkpoint": best,
                                                                  "summary": "s", "judgement": None}))
    assert body["status"] == "accepted" and body["validate_spec"]["ok"] is True and body["revision"] == 1
    assert lab.state["finish"]["checkpoint"] == lab.s.head["id"] and len(text) <= 1500
    assert L.tool_result_status(call(tl["facet_finish"], {"status": "matched", "best_checkpoint": best,
                                                          "summary": "s", "judgement": None})) == "accepted"


def test_stuck_needs_three_distinct_heads_none_better(lab):
    st = {"judgements": [{"compare_revision": r, "overall": o, "compare_id": f"c{r}"}
                         for r, o in ((1, "same"), (2, "worse"), (3, "mixed"))], "compares": [], "variants": [{
                             "spread": ["A", "B", "C"]}], "revisions": []}
    miss = S.finish_missing(st, status="stuck", best_revision=1, k=0, distinct=lambda a, b: True, target_photos=[])
    assert miss == []
    st["judgements"][1]["compare_revision"] = 1
    miss = S.finish_missing(st, status="stuck", best_revision=1, k=0, distinct=lambda a, b: True, target_photos=[])
    assert [m["precondition"] for m in miss] == [4]


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
                            status="matched", best_revision=1, k=4, distinct=lambda a, b: True, target_photos=[])
    assert [m["precondition"] for m in miss] == [1] and "2 of 4" in miss[0]["message"]


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
    for k, (parent, edits) in enumerate(steps):
        body, *_ = parsed(call(tl["facet_edit"], {"parent_revision": parent, "reset_layout": parent == 0 and k == 4,
                                                  "edits": edits, "note": "x", "apply": True, "photos": None,
                                                  "judgement": judgement(lab) if k else None}))
        assert body["status"] == "applied", body
    revs = [c["revision"] for c in S.seen_compares(lab.state)]
    assert revs == [1, 0, 1, 0, 2] and lab.state["revisions"][2]["sha256"] != lab.state["revisions"][1]["sha256"]
    assert not G.distinct_heads(lab.delta(1, 2))                       # the rename: no change by geometry
    body, *_ = parsed(call(tl["facet_finish"], {"status": "matched", "best_checkpoint": lab.s.head["id"],
                                                "summary": "s", "judgement": judgement(lab)}))
    pre1 = next(m for m in body["missing"] if m["precondition"] == 1)
    assert "2 of 3" in pre1["message"] and "revisions [1, 0]" in pre1["message"]


def test_stuck_uses_the_one_head_rule(lab, monkeypatch):
    """Precondition 4 counts heads with the same rule as precondition 1 (Lab.distinct, the built temples): judgements
    on revision 1, revision 0 and a rename of revision 1 cover 2 distinct heads, not 3; one more real change makes
    the last 3 judgements cover 3."""
    import glasses3d.g3d_host.spec as GS
    monkeypatch.setattr(GS, "validate_spec", lambda p: {"ok": True, "errors": [], "warnings": [],
                                                        "live_site_would_fail": []})
    tl = tools_of(lab, finish_k=2)
    spec = FX.prada_default()
    renamed = rename(canonical(spec), {"F1": "G1", "F2": "G2", "F3": "G3", "F4": "G4"}, {"C": "Cx", "A": "Ax"})
    renamed["nodes"]["Cx"][2] += 1.0                                        # revision 1's geometry, other names
    for k, (edits, reset) in enumerate([([set_t("C", spec, 1.0)], False), ([], False),
                                        (G.spec_to_edits(renamed), True)]):
        body, *_ = parsed(call(tl["facet_edit"], {"parent_revision": 0, "reset_layout": reset, "edits": edits,
                                                  "note": "x", "apply": True, "photos": None,
                                                  "judgement": judgement(lab) if k else None}))
        assert body["status"] == "applied", body
    assert [c["revision"] for c in S.seen_compares(lab.state)] == [1, 0, 2]
    calls = []
    distinct0 = lab.distinct
    lab.distinct = lambda ra, rb: calls.append((ra, rb)) or distinct0(ra, rb)
    body, *_ = parsed(call(tl["facet_finish"], {"status": "stuck", "best_checkpoint": lab.s.head["id"], "summary": "s",
                                                "judgement": judgement(lab)}))
    pre4 = next(m for m in body["missing"] if m["precondition"] == 4)
    assert "cover 2 distinct heads" in pre4["message"] and (1, 2) in calls     # (1, 2): only precondition 4 asks it
    assert [j["compare_revision"] for j in lab.state["judgements"]] == [1, 0, 2]
    body, *_ = parsed(call(tl["facet_edit"], {"parent_revision": 0, "reset_layout": False, "note": "x", "apply": True,
                                              "edits": [set_t("C", spec, 2.0)], "photos": None, "judgement": None}))
    body, *_ = parsed(call(tl["facet_finish"], {"status": "stuck", "best_checkpoint": lab.s.head["id"], "summary": "s",
                                                "judgement": judgement(lab)}))
    assert body["status"] == "refused" and 4 not in [m["precondition"] for m in body["missing"]], body


def test_precondition_3_counts_only_compares_astra_saw():
    """pf_review defect 3: prepare's compares and a timed-out call's compare (never shown) cover no photo."""
    st = {"compares": [{"by": "prepare", "revision": 1, "photos": ["01", "02"]},
                       {"by": "astra", "revision": 1, "photos": ["03"], "unseen": True},
                       {"by": "astra", "revision": 1, "photos": ["04"]}],
          "variants": [{"spread": ["A", "B", "C"]}], "judgements": []}
    miss = S.finish_missing(st, status="matched", best_revision=1, k=1, distinct=lambda a, b: True,
                            target_photos=["01", "02", "03", "04"])
    assert [m["precondition"] for m in miss] == [3] and miss[0]["photos"] == ["01", "02", "03"]


def test_the_state_lock_serialises_mark_unseen_and_save(lab, monkeypatch):
    """pf_review defect 4: mark_unseen (on the event-loop thread, when the SDK times a call out) waits while the
    worker thread saves the state; it never adds keys under json.dumps. The lock is re-entrant (a save inside a
    mutation)."""
    body, *_ = parsed(call(tools_of(lab)["photo_compare"], {"photos": ["05"], "crop": None, "points_px": None},
                           "call_r"))
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
    body, *_ = parsed(call(tl["photo_image"], {"photo": "05", "crop": None}))
    assert time.perf_counter() - t0 < 2.0
    assert body["status"] == "busy" and body["code"] == "E_BUSY" and body["running"]["tool"] == "facet_inspect"
    gate.set()
    th.join(10)
    assert parsed(call(tl["photo_image"], {"photo": "05", "crop": None}))[0]["status"] == "ok"


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
    out = sdk_call(tl["facet_edit"], {"parent_revision": 0, "reset_layout": False, "judgement": None, "note": "x",
                                      "edits": [set_t("C", FX.prada_default(), 0.7)], "apply": True,
                                      "photos": ["05"]}, "call_slow", 0.3, context=Status())
    assert isinstance(out, str)                                        # asyncio.run waited for the worker thread
    body, footer = json.loads(out.rsplit("\n", 1)[0]), out.rsplit("\n", 1)[1]
    assert body["status"] == "timeout" and body["code"] == "E_TIMEOUT" and "timed out after 0.3 s" in body["message"]
    assert footer == "settled $2.50 / cap $25.00 · context 31000 tokens"
    lab.seeing.slow_s = 0.0
    made = [c for c in lab.state["compares"] if c["call_id"] == "call_slow"]
    assert len(made) == 1 and made[0]["unseen"] is True and made[0]["judged"] is True
    assert S.pending_compare(lab.state) is None and S.seen_compares(lab.state) == []
    assert lab.head_record()["revision"] == 1                          # the apply happened
    body, *_ = parsed(call(tl["facet_edit"], {"parent_revision": 1, "reset_layout": False, "judgement": None,
                                              "edits": [set_t("C", FX.prada_default(), 1.4)], "note": "x",
                                              "apply": True, "photos": ["05"]}))
    assert body["status"] == "applied"                                 # no E_JUDGEMENT for the unseen compare


def test_timed_out_variants_never_meet_precondition_2(lab):
    """p3f_review lab defect 2: a facet_variants call the SDK times out still renders in its thread, but Astra never
    saw its sheets: the result is recorded unseen and finish precondition 2 stays missing (both orders: the SDK
    gives up before or after the result is recorded); a later call that returns counts."""
    tl = tools_of(lab)
    spec = FX.prada_default()
    variants = [{"label": lb, "edits": [set_t("C", spec, dt)]} for lb, dt in (("A", -1.0), ("B", 0.0), ("C", 1.0))]
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
                                                            distinct=lambda a, b: True, target_photos=[])]
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
    parsed(call(tl["photo_compare"], {"photos": PHOTO_IDS, "crop": None, "points_px": None}))
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
    photo_compare and facet_edit's compares (native_linear and gloss_black) and facet_variants' renders."""
    from glasses3d.agent.seeing import MATCHED_RIG
    from glasses3d.agent.temple_lab import cli
    assert cli.DEFAULT_RIG == MATCHED_RIG == lab.rig == "studio_dome_v1"
    tl = tools_of(lab)
    parsed(call(tl["photo_compare"], {"photos": ["05"], "crop": None, "points_px": None}))
    assert lab.seeing.calls[-1]["rig"] == "studio_dome_v1"
    assert lab.seeing.calls[-1]["passes"] == ["native_linear", "gloss_black"]
    parsed(call(tl["facet_edit"], {"parent_revision": 0, "reset_layout": False, "judgement": judgement(lab),
                                   "edits": [set_t("C", FX.prada_default(), 0.5)], "note": "x", "apply": True,
                                   "photos": ["05"]}))
    assert lab.seeing.calls[-1]["rig"] == "studio_dome_v1"
    spec = FX.prada_default()
    parsed(call(tl["facet_variants"], {"parent_revision": 0, "photos": ["05"], "judgement": judgement(lab),
                                       "variants": [{"label": lb, "edits": [set_t("C", spec, dt)]}
                                                    for lb, dt in (("A", -1.0), ("B", 1.0))]}))
    assert lab.s.read_params[-1]["rig"] == "studio_dome_v1"


def test_mark_unseen_after_the_compare_was_recorded(lab):
    """The other order: the call's compare is recorded first, then the SDK gives up on the call."""
    body, *_ = parsed(call(tools_of(lab)["photo_compare"], {"photos": ["05"], "crop": None, "points_px": None},
                           "call_late"))
    assert S.pending_compare(lab.state)["compare_id"] == body["compare_id"]
    lab.mark_unseen("call_late")
    assert S.pending_compare(lab.state) is None and S.compare(lab.state, body["compare_id"])["unseen"] is True


def test_compare_render_deadline_is_the_tool_hard_timeout(lab):
    assert lab.seeing.calls[-1]["render_deadline_s"] is None              # prepare's compare: seeing's default
    tl = tools_of(lab)
    parsed(call(tl["photo_compare"], {"photos": ["05"], "crop": None, "points_px": None}))
    assert lab.seeing.calls[-1]["render_deadline_s"] == L.hard_timeout_s("photo_compare") < \
        L.sdk_timeout_s("photo_compare")
    parsed(call(tl["facet_edit"], {"parent_revision": 0, "reset_layout": False, "judgement": judgement(lab),
                                   "edits": [set_t("C", FX.prada_default(), 0.5)], "note": "x", "apply": True,
                                   "photos": ["05"]}))
    assert lab.seeing.calls[-1]["render_deadline_s"] == L.hard_timeout_s("facet_edit") < L.sdk_timeout_s("facet_edit")


def test_the_scene_lock_is_the_labs_and_teardown_waits_for_it(lab, monkeypatch):
    """tools.py uses Lab.lock; exit_checkpoint and close wait for a call still holding it (a timed-out one), and
    give up after LOCK_WAIT_S with an error (close still closes the worker)."""
    import glasses3d.agent.temple_lab.lab as LB
    tl = tools_of(lab)
    lab.s.ck_dir.mkdir(parents=True, exist_ok=True)
    (lab.s.ck_dir / lab.s.head["file"]).write_bytes(b"")
    lab.s.head["sha256"] = hashlib.sha256(b"").hexdigest()
    assert lab.lock.acquire(timeout=1)
    try:
        body, *_ = parsed(call(tl["photo_image"], {"photo": "05", "crop": None}))
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
    body, *_ = parsed(call(tl["facet_edit"], {"parent_revision": 0, "reset_layout": False,
                                              "edits": [set_t("C", FX.prada_default(), 1)], "judgement": None,
                                              "note": "x", "apply": True, "photos": None}))
    assert body["status"] == "refused" and body["stage"] == "blender" and body["code"] == "E_MULTI_MATERIAL"
    assert lab.s.head["fingerprint"] == lab.s.index.get(head)["fingerprint"]
    lab.s.fail_build = "raise"
    with pytest.raises(WorkerFailed):
        call(tl["facet_edit"], {"parent_revision": 0, "reset_layout": False,
                                "edits": [set_t("C", FX.prada_default(), 2)], "judgement": None, "note": "x",
                                "apply": True, "photos": None})
    assert lab.s.head["fingerprint"] == lab.s.index.get(head)["fingerprint"]


def test_footer_from_the_run_context(lab):
    class Status:
        def footer(self):
            return L.footer_text(1.234, 20.0, 123456)
    body, footer, *_ = parsed(call(tools_of(lab)["photo_image"], {"photo": "05", "crop": None}, context=Status()))
    assert footer == "settled $1.23 / cap $20.00 · context 123456 tokens"


def test_context_stubs_keep_latest_two_and_best(lab):
    tl = tools_of(lab)
    spec = FX.prada_default()
    shas = []
    for k, dt in enumerate((0.5, 1.0, 1.5, 2.0)):
        out = call(tl["facet_edit"], {"parent_revision": 0, "reset_layout": False, "edits": [set_t("C", spec, dt)],
                                      "note": "x", "apply": True, "photos": ["05"],
                                      "judgement": judgement(lab, best=lab.state["heads"][1]["checkpoint"]
                                                             if k > 1 else None) if k else None})
        shas.append([hashlib.sha256(__import__("base64").b64decode(i.image_url.split(",", 1)[1])).hexdigest()
                     for i in out[1:]])
    stubs = lab.context_stubs([s for row in shas for s in row])
    compares = [row[-1] for row in shas]                                  # each edit's compare image (photo 05)
    charts = [s for row in shas for s in row[:-1]]
    assert compares[1] in stubs and compares[0] not in stubs           # compares[0] = best_checkpoint's revision
    assert compares[2] not in stubs and compares[3] not in stubs      # the latest two
    assert not set(charts) & set(stubs)                                 # charts and strips are never stubbed
    assert "superseded" in stubs[compares[1]]


# --------------------------------------------------------------------------------------------- real Blender
REPO = Path(__file__).resolve().parents[3]
SCENE = REPO / "automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend"
SCENE_SHA = "56274debb901fb28dea1f93f0a6b6ae8401501f7145573f8dedb68c7d2436c06"
BASIS = REPO / "glasses3d/data/wp6v2/basis/cameras.json"
REFERENCE = REPO / "glasses3d/data/reference/prada"


def real_job(root: Path, rig: str = "studio_dome_v1") -> Path:
    """A facet-lab job on a byte copy of the Prada original with the six target photos and the pose basis."""
    import shutil
    from glasses3d.g3d_host import compare as C
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
    C.import_cameras_json(root, root / "inputs" / "cameras.json", photos, reason="facet lab test")
    (root / "inputs" / "lab.json").write_text(json.dumps({"schema": LAB_SCHEMA, "rig": rig, "assets": [
        {"path": "scene.blend", "sha256": SCENE_SHA}]}), encoding="utf-8")
    return root


@pytest.mark.slow
def test_real_lab_prepare_edit_variants_restore(tmp_path):
    """On real Blender (a copy of Prada, studio_dome_v1): prepare makes revision 0 from the original's export and
    compares the original and revision 0 at all six photos; facet_edit applies a facet and compares; facet_variants
    renders without changing the scene; facet_restore returns to revision 0; facet_finish names what is missing."""
    from glasses3d.g3d_host import blender
    if blender.blender_exe() is None or not SCENE.exists() or not BASIS.exists():
        pytest.skip("Blender 5.2, the Prada scene or the pose basis is not available")
    from glasses3d.g3d.temple.spec import parse
    root = real_job(tmp_path / "job")
    lab = Lab(root)
    try:
        prep = lab.prepare()
    finally:
        lab.close()
    assert prep["status"] == "prepared" and len(lab.state["compares"]) == 2
    assert all(c["photos"] == PHOTO_IDS for c in lab.state["compares"]) and lab.is_clean()
    lab = Lab(root)
    lab.start()
    try:
        tl = tools_of(lab)
        r0 = lab.spec(0)
        t_r = float(parse(r0).base_t(25.0, -12.0)) + 0.8
        node = {"op": "set_node", "name": "R", "d_mm": 25.0, "y_mm": -12.0, "t": {"mode": "mm", "mm": t_r}}
        edits = [{"op": "set_node", "name": "P", "d_mm": 10.0, "y_mm": 6.0, "t": {"mode": "base", "mm": None}},
                 {"op": "set_node", "name": "Q", "d_mm": 40.0, "y_mm": 6.0, "t": {"mode": "base", "mm": None}}, node,
                 {"op": "set_facet", "label": "F1", "poly": ["P", "R", "Q"],
                  "plane": {"mode": "nodes", "crease": None, "rise_per_mm": None}, "fade": None}]
        r0_checkpoint = lab.s.head["id"]
        reads, call_read = [], lab.s.call_read

        def recording_read(tool, method, params, *, deadline_s, call_id):
            reads.append((method, deadline_s))
            return call_read(tool, method, params, deadline_s=deadline_s, call_id=call_id)
        lab.s.call_read = recording_read
        body, _f, images, text = parsed(call(tl["facet_edit"], {"parent_revision": 0, "reset_layout": False,
                                                                "edits": edits, "judgement": None, "note": "test",
                                                                "apply": True, "photos": ["05", "03"]}, "c1"))
        assert body["status"] == "applied" and body["compare_id"] and len(images) == 4, body
        assert len(text) <= 1500
        assert ("render", L.hard_timeout_s("facet_edit")) in reads, reads      # the compare's renders, capped
        fp = lab.s.head["fingerprint"]
        variants = [{"label": lb, "edits": [{**node, "t": {"mode": "mm", "mm": t_r + dt}}]}
                    for lb, dt in (("A", -0.5), ("B", 0.5))]
        body, _f, images, _t = parsed(call(tl["facet_variants"], {"parent_revision": body["revision"],
                                                                  "variants": variants, "photos": ["05"],
                                                                  "judgement": judgement(lab)}, "c2"))
        assert body["status"] == "ok" and len(images) == 1 and lab.s.head["fingerprint"] == fp
        assert lab.s.fingerprint() == fp                         # the worker's scene, not just the index
        body, *_ = parsed(call(tl["facet_restore"], {"checkpoint": r0_checkpoint, "judgement": None}, "c3"))
        assert body["status"] == "ok" and body["revision"] == 0
        body, *_ = parsed(call(tl["facet_finish"], {"status": "matched", "best_checkpoint": r0_checkpoint,
                                                    "summary": "test", "judgement": None}, "c4"))
        assert body["status"] == "refused" and [m["precondition"] for m in body["missing"]] == [1, 2, 3]
        assert body["missing"][2]["photos"] == PHOTO_IDS                # prepare's compares were never shown to Astra
        assert lab.exit_checkpoint()["verified"] is True
    finally:
        lab.close()


def test_facet_run_settings():
    s = T.run_settings(effort="max", max_turns=None, finish_k=5)
    assert s.behavior() == {"tool_choice": "required", "reset_tool_choice": False,
                            "finish": {"tool": "facet_finish", "behavior": L.FINISH_BEHAVIOR, "k": 5},
                            "context_guard": {"input_tokens": 240_000, "filter": L.CONTEXT_GUARD}}


def test_resume_rollback_ignores_prepare_and_finds_the_lost_apply(lab):
    """loop.plan_rollback over the lab journal: prepare's apply is never rolled back; an apply whose turn was not
    recorded in the session is."""
    assert L.plan_rollback([], lab.journal_entries()) is None and lab.is_clean()
    before = lab.s.head["id"]
    call(tools_of(lab)["facet_edit"], {"parent_revision": 0, "reset_layout": False, "judgement": None, "note": "x",
                                       "edits": [set_t("C", FX.prada_default(), 0.4)], "apply": True,
                                       "photos": None}, "call_lost")
    plan = L.plan_rollback([], lab.journal_entries())
    assert plan["call_id"] == "call_lost" and plan["checkpoint_before"] == before and not lab.is_clean()
    recorded = [{"type": "function_call", "call_id": "call_lost"},
                {"type": "function_call_output", "call_id": "call_lost", "output": "x"}]
    assert L.plan_rollback(recorded, lab.journal_entries()) is None
