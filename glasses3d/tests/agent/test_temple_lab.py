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
from types import SimpleNamespace

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
from glasses3d.g3d.temple.spec import canonical, parse, spec_sha256  # noqa: E402
from glasses3d.g3d_host.camera import Camera, NormFrame  # noqa: E402

# the pinned §4.2 union (tools.edits_schema()): change it only together with the tool layer and the prompt
EDITS_SCHEMA_SHA256 = "2abfc92d4b74eb201b7a5cf0322fda8bb16ccdbd5e322d74be8fac74a3f7e954"
PHOTO_IDS = ["01", "02", "03", "04", "05", "06"]
KINDS = {"01": "side", "02": "angled", "03": "top", "04": "front", "05": "side", "06": "rear"}
DEFAULT = ["01", "02", "05"]
disable_sdk_tracing()                  # the SDK's error path would build a trace exporter (a network client)


# --------------------------------------------------------------------------------------------- fakes
class FakeBasis:
    """Six target photos (small synthetic images) with orthographic cameras."""

    def __init__(self):
        self.frame = NormFrame((0.0, 0.0, 0.0), 200.0)
        self.refs = {f"reference-{p}": {"view_kind": KINDS[p], "camera": None} for p in PHOTO_IDS}

    def colourway(self, rid):
        return "target"

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
            vk = KINDS[rid.split("-")[1]]
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


def make_lab(root: Path, spec=None, **session) -> Lab:
    """A prepared lab: revision 0 = spec (default prada_default) applied on the fake original and compared at every
    photo by the prepare step (``session``: FakeSession attributes for that apply, e.g. blender_lines)."""
    (root / "inputs").mkdir(parents=True, exist_ok=True)
    (root / "inputs" / "lab.json").write_text(json.dumps({"schema": LAB_SCHEMA, "rig": "studio_dome_v1",
                                                          "assets": []}), encoding="utf-8")
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
    assert d["max_dx_mm"] > 0.5 and d["outline_mm"] == 0.0 and G.distinct_heads(d)
    same = G.geometry_delta(a, build_temple(FX.prada_default()))
    assert same["value_mm"] == 0.0 and not G.distinct_heads(same)
    flip = FX.prada_default()
    flip["creases"] = [c for c in flip["creases"] if c["facets"] != ["F4", "base"]]
    assert not G.distinct_heads(G.geometry_delta(a, build_temple(flip)))      # a declaration alone: no change
    assert G.spread_triple(["A", "B", "C"], {("A", "B"): 0.4, ("A", "C"): 0.9, ("B", "C"): 0.5}) == ["A", "B", "C"]
    assert G.spread_triple(["A", "B", "C"], {("A", "B"): 0.2, ("A", "C"): 0.9, ("B", "C"): 0.5}) is None


def test_topology_signature_is_geometry_not_labels():
    """A pure rename of facets and nodes is no change (facets_changed / creases_changed False, not distinct); a facet
    whose corner moves in (d, y), or a facet removed, is a topology change."""
    a = build_temple(FX.prada_default())
    renamed = rename(FX.prada_default(), {"F1": "G1", "F2": "G2", "F3": "Q7", "F4": "Z"}, {"A": "N1", "B": "N2",
                                                                                         "C": "N3"})
    assert renamed != canonical(FX.prada_default()) and spec_sha256(renamed) != spec_sha256(FX.prada_default())
    b = build_temple(renamed)
    d = G.geometry_delta(a, b)
    assert d["value_mm"] == 0.0 and not d["facets_changed"] and not d["creases_changed"] and not G.distinct_heads(d)
    m = G.facet_matching(a, b)
    assert m == {"F1": "G1", "F2": "G2", "F3": "Q7", "F4": "Z"}
    assert G.built_creases(a, m) == G.built_creases(b)
    assert frozenset({"F4", "base"}) in G.built_creases(a)              # a crease against the base keeps "base"
    moved = canonical(FX.prada_default())
    moved["nodes"]["C"][1] += 0.5                                       # C's y: the facets around C move
    d = G.geometry_delta(a, build_temple(moved))
    assert d["facets_changed"] and d["creases_changed"] is None and G.distinct_heads(d)


def _with_free_node(spec: dict, name: str, d: float, y: float) -> dict:
    s = copy.deepcopy(canonical(spec))
    s["nodes"][name] = [d, y, "base"]
    return canonical(s)


@pytest.mark.parametrize("dd, dy, distinct", [(0.05, 0.0, False), (0.0, -0.05, False), (0.02, 0.04, False),
                                              (0.0, 0.19, False), (0.3, 0.0, True), (0.0, 0.3, True),
                                              (0.0, 0.21, True)])
def test_head_distinctness_is_a_tolerance_not_a_rounding(dd, dy, distinct):
    """§4.1 precondition 1 (p3f_review lab defect 1): a node nudged by less than HEAD_DISTINCT_MM in (d, y) (its X
    change far below 0.2 mm) is no new head, whatever 0.01 mm rounding of the nodes would say; a move of
    HEAD_DISTINCT_MM or more is. C is a corner of F1, F2 and F4."""
    a = build_temple(FX.prada_default())
    moved = canonical(FX.prada_default())
    moved["nodes"]["C"][0] += dd
    moved["nodes"]["C"][1] += dy
    d = G.geometry_delta(a, build_temple(moved))
    if not distinct:
        assert d["max_dx_mm"] < G.HEAD_DISTINCT_MM and d["outline_mm"] == 0.0
        assert not d["facets_changed"] and not d["creases_changed"]
    assert G.distinct_heads(d) is distinct, d


def test_head_distinctness_ignores_labels_order_and_edge_nodes():
    """A rename plus a rotated, reversed polygon is the same layout; so is a node added on a facet's edge (it shapes
    nothing); a node added 0.3 mm off the edge is a new facet shape; deleting a facet is a new facet set."""
    a = build_temple(FX.prada_default())
    s = rename(FX.prada_default(), {"F1": "X1", "F2": "X2"}, {"A": "N1", "C": "N3"})
    s["facets"]["X1"]["poly"] = list(reversed(s["facets"]["X1"]["poly"]))
    s["facets"]["X2"]["poly"] = s["facets"]["X2"]["poly"][1:] + s["facets"]["X2"]["poly"][:1]
    d = G.geometry_delta(a, build_temple(canonical(s)))
    assert not G.distinct_heads(d), d
    poly = canonical(FX.prada_default())["facets"]["F4"]["poly"]
    nodes = canonical(FX.prada_default())["nodes"]
    p, q = (np.asarray(nodes[n][:2], float) for n in poly[:2])
    mid = 0.5 * (p + q)
    normal = np.array([-(q - p)[1], (q - p)[0]]) / np.linalg.norm(q - p)
    for offset, distinct in ((0.0, False), (0.3, True)):
        x = mid + offset * normal
        e = _with_free_node(FX.prada_default(), "M", float(x[0]), float(x[1]))
        e["facets"]["F4"]["poly"] = [poly[0], "M"] + poly[1:]
        P = G.facet_polygon(parse(e), "F4")
        assert G.same_facet(G.facet_polygon(a.spec, "F4"), P) is not distinct
    gone = canonical(FX.prada_default())
    del gone["facets"]["F3"]
    gone["creases"] = [c for c in gone["creases"] if "F3" not in c["facets"]]
    assert G.facet_matching(a, SimpleNamespace(spec=parse(gone))) is None


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
    assert enum == PHOTO_IDS
    finish = next(t for t in T.registry(lab, L.RunSettings(effort="max", finish_k=6)) if t.name == "facet_finish")
    assert "at least 6" in finish.description


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
    assert body["status"] == "refused" and [m["precondition"] for m in body["missing"]] == [1, 2]
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
        assert body["status"] == "refused" and [m["precondition"] for m in body["missing"]] == [1, 2]
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
