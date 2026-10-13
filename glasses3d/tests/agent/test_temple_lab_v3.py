"""WP6 v3 lab tools (plan/WP6_V3.md §6; phase-2 interfaces I3 geometry.apply_edits, I4 state, I5 tools.registry): the
edits on design specs (set_node t mode reading, set_facet flat / warp and soft), the design canonical form and diffs
with read dicts, the strict schemas (the edits union pinned by sha), the judgement's up-to-3 differences per photo, the
object note and its rule (E_OBJECT), every finish precondition passing and failing, and the registry's tools and the
Lab methods they call. No Blender, no network, no model.

Readings are real readings.read_point records on the synthetic rig of tests/agent/test_temple_readings.py (perspective
cameras around a Prada-sized temple pair; points projected with the lab's own model), so set_node mode reading is
tested on what photo_read stores; fit.read_payload (track fit2) turns them into the node's t.
"""
import asyncio
import copy
import hashlib
import inspect
import json
import math
import threading
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

pytest.importorskip("agents")
from agents.tool_context import ToolContext  # noqa: E402

from glasses3d.agent import loop as L  # noqa: E402
from glasses3d.agent import seeing as SEE  # noqa: E402
from glasses3d.agent.offline import disable_sdk_tracing  # noqa: E402
from glasses3d.agent.temple_lab import geometry as G  # noqa: E402
from glasses3d.agent.temple_lab import readings as RD  # noqa: E402
from glasses3d.agent.temple_lab import state as S  # noqa: E402
from glasses3d.agent.temple_lab import tools as T  # noqa: E402
from glasses3d.g3d.temple import build_temple  # noqa: E402
from glasses3d.g3d.temple import fit as FIT  # noqa: E402
from glasses3d.g3d.temple import fixtures as FX  # noqa: E402
from glasses3d.g3d.temple.spec import DEFAULT_OPTIONS, WARP_MIN_CORNERS, canonical, spec_sha256  # noqa: E402
from glasses3d.tests.agent.test_temple_readings import SynthBasis, corner_picks, point  # noqa: E402

# the pinned v3 edits union (tools.edits_schema()): change it only together with the tool layer and the prompt
# (re-pinned 2026-10-12, v3p2-lab-e1-fix: descriptions only, the name rule, typed t's sd, set_crease shape, chains,
# set_scalar; the structure is unchanged; re-pinned 2026-10-12, v3p3-fix-lab: set_node gained outline (top | bottom
# | none | null, e2e P1-1), the solve and chain texts state what the code does (owner P2-3, e2e P1-1), and the schema
# is the SDK's strict form, as Astra sees it; re-pinned 2026-10-13, v3-standin-fix: descriptions only, set_node outline
# says what each value does (the blind stand-in read "a corner on that edge" as unclear) and set_facet poly that a
# facet is clipped to the outline, so a corner may lie beyond it; re-pinned 2026-10-13, v3-standin-fix-review:
# descriptions only, set_node outline says where the bend is stated: facet_edit's receipt, a variant's notes)
EDITS_SCHEMA_SHA256 = "7066d267aaf610947d24f418e6414625dbef9a3d48aaf762e7c8342dc1261eed"
READ_AT = {"Q": (40.0, 0.0, 6.4), "R": (40.0, 8.0, 7.4)}      # synthetic reading points (d, y, t) on the slab
REMEDY_WORDS = ("apply", "restore", "then ", "photo_compare", "first", "should", "try", "instead")
HERE = Path(__file__).resolve()
disable_sdk_tracing()                  # the SDK's error path would build a trace exporter (a network client)


# --------------------------------------------------------------------------------------------- helpers
@pytest.fixture(scope="module")
def slab_tb():
    return build_temple(FX.slab())


@pytest.fixture(scope="module")
def recs(slab_tb):
    """Two stored readings, each a corner read in 05 + 02 on the synthetic rig (photo_read's records)."""
    bs = SynthBasis()
    return {name: RD.read_point(slab_tb, bs, name, corner_picks(bs, point(slab_tb, d, y, t), ["05", "02"]),
                                kind="corner") for name, (d, y, t) in READ_AT.items()}


def node(name, d, y, mode, mm=None) -> dict:
    """set_node as Astra sends it (the strict schema requires outline; null: as before the edit)."""
    return {"op": "set_node", "name": name, "d_mm": d, "y_mm": y, "t": {"mode": mode, "mm": mm}, "outline": None}


def facet(label, poly, mode="flat", soft=False) -> dict:
    return {"op": "set_facet", "label": label, "poly": list(poly), "plane": {"mode": mode}, "soft": soft}


def quad(mode="flat", soft=False, read=()) -> list:
    """A four-corner face on the slab (d 20-40, y 0-8); the corners named in ``read`` take their stored reading."""
    typed = {"P": (20.0, 0.0, 6.0), "Q": (40.0, 0.0, 6.4), "R": (40.0, 8.0, 7.4), "S": (20.0, 8.0, 6.2)}
    out = [node(n, None, None, "reading") if n in read else node(n, d, y, "mm", t) for n, (d, y, t) in typed.items()]
    return out + [facet("crown", ["P", "Q", "R", "S"], mode, soft)]


def refused(spec, edits, **kw) -> G.EditError:
    with pytest.raises(G.EditError) as info:
        G.apply_edits(spec, edits, **kw)
    return info.value


def walk(schema):
    """Every dict inside a JSON schema."""
    if isinstance(schema, dict):
        yield schema
        for v in schema.values():
            yield from walk(v)
    elif isinstance(schema, list):
        for v in schema:
            yield from walk(v)


def strict(schema) -> list:
    """The objects of a schema that are not strict (additionalProperties false, every property required)."""
    return [d for d in walk(schema) if d.get("type") == "object" and "properties" in d and
            (d.get("additionalProperties") is not False or set(d.get("required", [])) != set(d["properties"]))]


# --------------------------------------------------------------------------------------------- I3: reading mode
def test_reading_mode_places_the_node_at_its_reading(recs):
    """t mode reading takes the stored reading of the node's name; d_mm / y_mm null are the reading's (d, y), given
    numbers within 0.5 mm are the node's, one null coordinate is the reading's."""
    q = recs["Q"]
    payload = json.loads(json.dumps(FIT.read_payload(q)))
    design, notes = G.apply_edits(FX.slab(), [node("Q", None, None, "reading")], readings=recs)
    assert design["nodes"]["Q"] == [q["d"], q["y"], payload] and notes == []
    assert payload["read"]["X"] == q["X"] and set(payload) == {"read"}
    design, _ = G.apply_edits(FX.slab(), [node("Q", q["d"] + 0.3, q["y"] - 0.3, "reading")], readings=recs)
    assert design["nodes"]["Q"] == [q["d"] + 0.3, q["y"] - 0.3, payload]
    design, _ = G.apply_edits(FX.slab(), [node("Q", q["d"] - 0.49, None, "reading")], readings=recs)
    assert design["nodes"]["Q"] == [q["d"] - 0.49, q["y"], payload]
    assert G.READ_POSITION_MM == FIT.OFF_POSITION_MM == 0.5          # the fit's own position fact line


@pytest.mark.parametrize("edit, use, words", [
    (node("Z", None, None, "reading"), True, "there is no reading named Z: read it with photo_read first"),
    (node("Q", None, None, "reading"), False, "there is no reading named Q: read it with photo_read first"),
    (node("Q", None, None, "reading", 6.0), True, "t.mode 'reading' takes t.mm null"),
    (node("Q", None, None, "mm", 6.0), True, "needs d_mm and y_mm"),
    (node("Q", 40.0, None, "base"), True, "needs d_mm and y_mm"),
    (node("Q", 40.0, 0.0, "typed", 6.0), True, "t.mode must be reading, mm, base or solve"),
])
def test_reading_mode_refusals_name_the_edit(recs, edit, use, words):
    err = refused(FX.slab(), [node("P", 20.0, 0.0, "mm", 6.0), edit], readings=recs if use else None)
    assert err.index == 1 and err.code == "E_EDIT" and words in err.message, err.message


def test_a_reading_used_away_from_where_it_was_read_is_refused_naming_both(recs):
    q = recs["Q"]
    spec = canonical(FX.slab())
    err = refused(spec, [node("Q", q["d"] + 0.51, None, "reading")], readings=recs)
    assert err.index == 0 and f"d {q['d'] + 0.51:.2f}, y {q['y']:.2f}" in err.message
    assert f"read, d {q['d']:.2f}, y {q['y']:.2f}" in err.message and "0.51 mm" in err.message
    assert err.fields == {"node": "Q", "at": [q["d"] + 0.51, q["y"]], "read_at": [q["d"], q["y"]]}
    assert not any(w in err.message for w in ("should", "try", "instead"))
    assert spec == canonical(FX.slab())                                # the input is never changed


def test_a_read_design_fits_and_builds(recs):
    """apply_edits -> design (read dicts) -> fit.fit_spec -> a pure /2 spec the builder takes; the read corners are
    observed as readings (a warp through 4 corners holds them exactly)."""
    design, _ = G.apply_edits(FX.slab(), quad("warp", soft=True, read=("Q", "R")), readings=recs)
    assert G.canonical_design(design) == design
    fr = FIT.fit_spec(design, readings=recs)
    assert fr.nodes["Q"]["source"] == fr.nodes["R"]["source"] == "read"
    assert fr.nodes["Q"]["X"] == pytest.approx(recs["Q"]["X"], abs=1e-6)
    assert fr.spec["facets"]["crown"]["plane"] == "warp" and fr.spec["facets"]["crown"]["soft"] is True
    assert all(not isinstance(v[2], dict) for v in fr.spec["nodes"].values())
    build_temple(fr.spec)


def test_design_canonical_form_and_sha(recs):
    """A design without readings is its canonical spec (same sha); a read t is kept with float numbers; the sha
    follows the reading."""
    plain, _ = G.apply_edits(FX.slab(), quad())
    assert plain == canonical(plain) and G.design_sha256(plain) == spec_sha256(plain)
    read, _ = G.apply_edits(FX.slab(), quad(read=("Q",)), readings=recs)
    again = copy.deepcopy(read)
    again["nodes"]["Q"][2]["read"]["d"] = int(again["nodes"]["Q"][2]["read"]["d"])        # 40 (an int) = 40.0
    assert G.canonical_design(again) == read and G.design_sha256(again) == G.design_sha256(read)
    other = copy.deepcopy(read)
    other["nodes"]["Q"][2]["read"]["X"] += 0.01
    assert G.design_sha256(other) != G.design_sha256(read) != G.design_sha256(plain)
    bad = copy.deepcopy(read)
    bad["nodes"]["Q"][2] = {"X": 1.0}
    with pytest.raises(Exception) as info:
        G.canonical_design(bad)
    assert getattr(info.value, "code", None) == "E_SPEC"


# --------------------------------------------------------------------------------------------- I3: warp and soft
def test_set_facet_flat_warp_and_soft():
    design, _ = G.apply_edits(FX.slab(), quad("warp", soft=True))
    assert design["facets"]["crown"] == {"poly": ["P", "Q", "R", "S"], "plane": "warp", "fade": None, "soft": True}
    design, _ = G.apply_edits(design, [facet("crown", ["P", "Q", "R", "S"], "flat", False)])
    assert design["facets"]["crown"] == {"poly": ["P", "Q", "R", "S"], "plane": "nodes", "fade": None}
    fitted = FIT.fit_spec(G.apply_edits(FX.slab(), quad("warp"))[0])
    assert fitted.spec["facets"]["crown"]["plane"] == "warp"


@pytest.mark.parametrize("edit, words", [
    (facet("tri", ["P", "Q", "R"], "warp"), f"a warp facet needs >= {WARP_MIN_CORNERS} corners"),
    (facet("crown", ["P", "Q", "R", "S"], "curved"), "plane.mode must be flat or warp"),
    ({**facet("crown", ["P", "Q", "R", "S"]), "soft": "yes"}, "soft must be true or false"),
    ({**facet("crown", ["P", "Q", "R", "S"]), "plane": {"mode": "flat", "crease": ["P", "Q"], "rise_per_mm": None}},
     "takes no crease and no rise_per_mm"),
    ({**facet("crown", ["P", "Q", "R", "S"]), "plane": {"mode": "crease", "crease": ["P", "Q"],
                                                        "rise_per_mm": None}}, "needs rise_per_mm"),
])
def test_set_facet_refusals(edit, words):
    err = refused(FX.slab(), quad()[:4] + [edit])
    assert err.index == 4 and words in err.message, err.message


def test_internal_callers_keep_the_v2_shapes_the_tool_union_refuses():
    """apply_edits still takes v2's plane mode nodes / crease + rise and fades (fixtures, v2 tests, old revisions);
    the strict union Astra uses has none of them."""
    v2 = {"op": "set_facet", "label": "crown", "poly": ["P", "Q", "R", "S"],
          "plane": {"mode": "nodes", "crease": None, "rise_per_mm": None}, "fade": {"d0_mm": 25.0, "d1_mm": 35.0}}
    design, _ = G.apply_edits(FX.slab(), quad()[:4] + [v2])
    assert design["facets"]["crown"] == {"poly": ["P", "Q", "R", "S"], "plane": "nodes",
                                         "fade": {"d0": 25.0, "d1": 35.0}}
    for bad in (v2, {**facet("crown", "PQRS"), "fade": None}, {**facet("crown", "PQRS"), "plane": {"mode": "nodes"}},
                {**facet("crown", "PQRS"), "plane": {"mode": "crease"}}, {k: v for k, v in facet("c", "PQRS").items()
                                                                          if k != "soft"},
                {**node("Q", 1.0, 1.0, "mm", 1.0), "t": {"mode": "reading", "mm": None, "sd": 1.0}}):
        with pytest.raises(ValidationError):
            T.EditList.model_validate({"edits": [bad]})


# --------------------------------------------------------------------------------------------- I3: diffs, round trip
def test_diff_texts_print_readings_warp_soft_and_coef(recs, slab_tb):
    typed, _ = G.apply_edits(FX.slab(), quad())
    read, _ = G.apply_edits(typed, [node("Q", None, None, "reading"), facet("crown", "PQRS", "warp", True)],
                            readings=recs)
    q = recs["Q"]
    text = G.diff_text(G.diff_specs(typed, read))
    assert text == f"~node Q t 6.4->read {q['t']:.2f} +- {q['sd_X']:.2f}; ~facet crown flat->warp soft", text
    assert G.read_sd(read["nodes"]["Q"][2]) == pytest.approx(q["sd_X"], abs=1e-3)       # pick and camera parts
    assert G.diff_text(G.diff_specs(read, typed)) == \
        f"~node Q t read {q['t']:.2f} +- {q['sd_X']:.2f}->6.4; ~facet crown warp->flat not soft"
    assert G.diff_text(G.diff_specs(FX.slab(), read)) == \
        f"+node P, Q t read {q['t']:.2f} +- {q['sd_X']:.2f}, R, S; +facet crown warp soft"     # "t": v3p3-clean-lab
    # the same reading name read again from other photos: the node's t changes, the text says so
    bs = SynthBasis()
    again = {"Q": RD.read_point(slab_tb, bs, "Q", corner_picks(bs, point(slab_tb, 40.0, 0.0, 6.4), ["05", "03"]),
                                kind="corner")}
    reread, _ = G.apply_edits(read, [node("Q", None, None, "reading")], readings=again)
    assert reread["nodes"]["Q"][2] != read["nodes"]["Q"][2]
    assert G.diff_text(G.diff_specs(read, reread)).startswith("~node Q t ")
    # a polygon listed from another start or the other way round is no change; a new corner is "poly"
    turned, _ = G.apply_edits(read, [facet("crown", ["R", "Q", "P", "S"], "warp", True)])
    assert G.diff_specs(read, turned) == {}
    # fitted planes: coef numbers
    a = canonical(typed)
    a["facets"]["crown"]["plane"] = {"coef": [1.0, 0.1, 0.2]}
    b = copy.deepcopy(a)
    b["facets"]["crown"]["plane"]["coef"][0] = 1.5
    assert G.diff_text(G.diff_specs(a, b)) == "~facet crown coef"
    assert G.diff_text(G.diff_specs(typed, a)) == "~facet crown flat->coef"


def test_chain_diffs_print_the_true_changed_span():
    """diff_specs compares a chain's two versions as lines over d (geometry.chain_changes), never knot by knot
    (v3p3-clean-lab; session2 printed "outline.top d 0.0-136.0" for a splice of d 0-77, "77.0-131.0" for a change over
    77-117 and "122.0-134.0" for one over 122-136, the knots paired by index): the span runs from the last knot where
    the lines agree to the next one where they agree again (a splice changes the segment to the next kept knot), an
    offset_chain's change is its taper's span, an end knot moved is in it, two separate changes are two spans, the
    largest move is in mm, a changed d range is said, and a knot added on the line changes nothing."""
    base = canonical(FX.prada_default())
    top = base["outline"]["top"]
    after = min(p[0] for p in top if p[0] > 77.0)

    def chains(edits):
        return G.diff_specs(base, G.apply_edits(base, edits)[0]).get("chains")
    # a splice of d 0-77 with fewer knots: the change ends at the first knot kept after 77
    pts = [[top[0][0], top[0][1] + 0.5], [40.0, 13.0], [77.0, float(np.interp(77.0, *np.asarray(top).T)) + 0.3]]
    new = G.apply_edits(base, [{"op": "splice_chain", "chain": "outline.top", "d_from_mm": 0.0, "d_to_mm": 77.0,
                                "points": pts}])[0]["outline"]["top"]
    dd = np.linspace(top[0][0], top[-1][0], 136001)                       # dense samples: an independent reference
    gap = np.abs(np.interp(dd, *np.asarray(new).T) - np.interp(dd, *np.asarray(top).T))
    assert dd[gap > 1e-9].max() < after and dd[gap > 1e-9].max() > 77.0      # the change runs on past the splice
    assert chains([{"op": "splice_chain", "chain": "outline.top", "d_from_mm": 0.0, "d_to_mm": 77.0,
                    "points": pts}]) == [f"outline.top d {top[0][0]:.1f}-{after:.1f} (up to {gap.max():.2f} mm)"]
    # two tapered offsets that touch (session2 step 07: -1.3 mm over 84-96, taper 7; +0.85 over 108-112, taper 5)
    off = [{"op": "offset_chain", "chain": "outline.bottom", "d_from_mm": 84.0, "d_to_mm": 96.0, "delta_mm": -1.3,
            "taper_mm": 7.0},
           {"op": "offset_chain", "chain": "outline.bottom", "d_from_mm": 108.0, "d_to_mm": 112.0, "delta_mm": 0.85,
            "taper_mm": 5.0}]
    assert chains(off) == ["outline.bottom d 77.0-117.0 (up to 1.30 mm)"]
    # an offset over the end knot (step 11: -3.5 mm over 128-136, taper 6): the end knot is in the span
    end = base["outline"]["bottom"][-1][0]
    assert chains([{"op": "offset_chain", "chain": "outline.bottom", "d_from_mm": 128.0, "d_to_mm": end,
                    "delta_mm": -3.5, "taper_mm": 6.0}]) == [f"outline.bottom d 122.0-{end:.1f} (up to 3.50 mm)"]
    # two separate changes are two spans
    two = [{"op": "offset_chain", "chain": "inner", "d_from_mm": 20.0, "d_to_mm": 30.0, "delta_mm": 0.2,
            "taper_mm": 2.0},
           {"op": "offset_chain", "chain": "inner", "d_from_mm": 90.0, "d_to_mm": 95.0, "delta_mm": -0.004,
            "taper_mm": 1.0}]
    assert chains(two) == ["inner d 18.0-32.0, 89.0-96.0 (up to 0.20 mm)"]
    assert chains(two[1:]) == ["inner d 89.0-96.0 (under 0.01 mm)"]
    # knots on the old line only (a zero offset inserts its breakpoints): no shape change
    assert chains([{**off[0], "delta_mm": 0.0}]) == ["outline.bottom knots only (no shape change)"]
    # a shortened chain: its end range is said
    bot = base["outline"]["bottom"]
    cut = [p for p in bot if p[0] <= 130.0]
    spans, mm = G.chain_changes(bot, cut)
    assert spans == [(cut[-1][0], bot[-1][0])] and mm == 0.0
    assert G.chain_change_text(bot, cut) == (f"d {cut[-1][0]:.1f}-{bot[-1][0]:.1f} (its d range {bot[0][0]:.1f}-"
                                             f"{bot[-1][0]:.1f} -> {cut[0][0]:.1f}-{cut[-1][0]:.1f})")
    assert G.chain_changes(bot, bot) == ([], 0.0)


def test_spec_to_edits_round_trips_a_v3_design(recs):
    design, _ = G.apply_edits(FX.slab(), quad("warp", soft=True, read=("Q", "R")) +
                              [{"op": "set_crease", "facets": ["crown", "base"], "shape": "ridge"}], readings=recs)
    edits = G.spec_to_edits(design)
    validated = T.EditList.model_validate({"edits": edits})              # the strict union accepts them
    assert [e.model_dump() for e in validated.edits] == edits
    out, notes = G.apply_edits(FX.wedge(), edits, reset_layout=True, readings=recs)
    assert out == design and notes == []
    assert [e["t"]["mode"] for e in edits if e["op"] == "set_node"] == ["mm", "reading", "reading", "mm"]
    fitted = canonical(design | {"nodes": {n: v[:2] + [6.0] for n, v in design["nodes"].items()}})
    fitted["facets"]["crown"]["plane"] = {"coef": [1.0, 0.1, 0.2]}
    assert [e["plane"] for e in G.spec_to_edits(fitted) if e["op"] == "set_facet"] == [{"mode": "flat"}]


# --------------------------------------------------------------------------------------------- I5: strict schemas
def test_edits_schema_is_pinned_and_strict():
    schema = T.edits_schema()
    digest = hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert digest == EDITS_SCHEMA_SHA256, digest
    members = schema["properties"]["edits"]["items"]["anyOf"]
    defs = schema["$defs"]
    ops = [defs[m["$ref"].split("/")[-1]]["properties"]["op"]["const"] for m in members]
    assert ops == ["set_node", "delete_node", "set_facet", "delete_facet", "set_crease", "delete_crease",
                   "splice_chain", "offset_chain", "set_scalar"]
    assert not strict(schema) and "discriminator" not in json.dumps(schema) and '"title"' not in json.dumps(schema)
    assert defs["Thickness"]["properties"]["mode"]["enum"] == ["reading", "mm", "base", "solve"]
    outline = defs["SetNode"]["properties"]["outline"]                      # v3p3 e2e P1-1: required, nullable
    assert {"enum": ["top", "bottom", "none"], "type": "string"} in outline["anyOf"] and {"type": "null"} in \
        outline["anyOf"] and "outline" in defs["SetNode"]["required"] and "default" not in outline
    assert defs["Plane"]["properties"] == {"mode": defs["Plane"]["properties"]["mode"]}
    assert defs["Plane"]["properties"]["mode"]["enum"] == ["flat", "warp"]
    assert set(defs["SetFacet"]["properties"]) == {"op", "label", "poly", "plane", "soft"}
    assert {"type": "null"} in defs["SetNode"]["properties"]["d_mm"]["anyOf"]
    assert "fade" not in json.dumps(schema) and "rise_per_mm" not in json.dumps(schema)


class FakeLab:
    """The Lab surface tools.registry uses (I5): photo ids, the lock, and each method recording its arguments."""

    def __init__(self, result=None):
        self.lock, self.running, self.calls, self.unseen = threading.Lock(), None, [], []
        self.result = result or {"status": "ok"}

    def photo_ids(self):
        return ["01", "02", "05"]

    def compare_ids(self):
        return ["01", "02", "05", "B1"]

    def image_ids(self):
        return ["01", "02", "05", "B1", "R1"]

    def variant_photos_max(self):
        return 4

    def mark_unseen(self, call_id):
        self.unseen.append(call_id)

    def _rec(self, name, *args):
        self.calls.append((name, args))
        if isinstance(self.result, Exception):
            raise self.result
        return dict(self.result)

    def inspect(self):
        return self._rec("inspect")

    def photo(self, *args):
        return self._rec("photo", *args)

    def read(self, *args):
        return self._rec("read", *args)

    def compare(self, *args):
        return self._rec("compare", *args)

    def edit(self, *args):
        return self._rec("edit", *args)

    def variants(self, *args):
        return self._rec("variants", *args)

    def finish(self, *args):
        return self._rec("finish", *args)


@pytest.fixture
def timeouts(monkeypatch):
    """loop.HARD_TIMEOUT_S names photo_read (its owner's table; test_loop_facet pins every TOOL_NAMES entry): the
    real value when present, else 30 s so the registry tests here test this file's tools while the table lacks it.
    test_the_real_registry_builds (no fixture) fails until the loop owner adds the entry: tools.registry raises
    KeyError 'photo_read' without it, so no run can start (review 2026-10-12)."""
    monkeypatch.setitem(L.HARD_TIMEOUT_S, "photo_read", L.HARD_TIMEOUT_S.get("photo_read", 30))


def test_the_real_registry_builds():
    """Every tool of the registry has a hard timeout in loop.HARD_TIMEOUT_S, so tools.registry builds with the real
    table (no fixture). A blocker in loop.py (not this track's file) while it fails."""
    missing = [name for name in T.TOOL_NAMES if name not in L.HARD_TIMEOUT_S]
    assert missing == [], f"loop.HARD_TIMEOUT_S has no entry for {missing}"
    assert tuple(t.name for t in T.registry(FakeLab())) == T.TOOL_NAMES


def tools_of(lab, k=None):
    settings = L.RunSettings(effort="max", finish_k=k) if k else None
    return {t.name: t for t in T.registry(lab, settings)}


def call(tool, args: dict, call_id: str = "call_x"):
    ctx = ToolContext(context=None, tool_name=tool.name, tool_call_id=call_id, tool_arguments=json.dumps(args))
    return asyncio.run(tool.on_invoke_tool(ctx, json.dumps(args)))


def body(out) -> dict:
    return json.loads(out[0].text.rsplit("\n", 1)[0])


def judgement_args(photos=("01", "05"), n=1) -> dict:
    return {"compare_id": "cmp002", "versus_compare_id": None, "overall": "mixed",
            "photos": [{"photo": p, "verdict": "worse", "differences": [{"what": f"crown tip {i}", "d_mm": 76.0,
                                                                          "y_mm": 9.0} for i in range(n)]}
                       for p in photos], "best_checkpoint": "c0001"}


def test_tool_schemas_are_strict_with_the_v3_limits(timeouts):
    tools = tools_of(FakeLab())
    assert tuple(tools) == T.TOOL_NAMES == ("facet_inspect", "photo_image", "photo_read", "photo_compare",
                                            "facet_edit", "facet_variants", "facet_finish")
    for t in tools.values():
        assert not strict(t.params_json_schema), t.name
        assert '"title"' not in json.dumps(t.params_json_schema)
    p = {name: t.params_json_schema for name, t in tools.items()}
    assert set(p["photo_image"]["properties"]) == {"photo", "crop", "enhance", "scale"}
    assert p["photo_image"]["properties"]["scale"] | {"description": None} == {
        "type": "integer", "minimum": 1, "maximum": S.SCALE_MAX, "description": None}
    assert p["photo_image"]["properties"]["photo"]["enum"] == ["01", "02", "05", "B1", "R1"]
    read = p["photo_read"]
    assert set(read["properties"]) == {"points", "drop"} and read["properties"]["points"]["maxItems"] == 8
    pick = read["$defs"]["ReadPick"]["properties"]
    assert read["$defs"]["ReadPoint"]["properties"]["picks"]["minItems"] == RD.PHOTOS_MIN == 2
    assert read["$defs"]["ReadPoint"]["properties"]["picks"]["maxItems"] == RD.PHOTOS_MAX == 4
    assert read["$defs"]["ReadPoint"]["properties"]["kind"]["enum"] == ["corner", "crossing"]
    assert pick["photo"]["enum"] == ["01", "02", "05", "B1"]
    edges = next(a for a in pick["edges"]["anyOf"] if a.get("type") == "array")
    assert (edges["minItems"], edges["maxItems"]) == (2, 2)
    assert (edges["items"]["minItems"], edges["items"]["maxItems"]) == (RD.EDGE_POINTS_MIN, RD.EDGE_POINTS_MAX)
    cmp = p["photo_compare"]
    assert set(cmp["properties"]) == {"photos", "crop", "zoom", "points_px"}
    zoom = next(a for a in cmp["properties"]["zoom"]["anyOf"] if a.get("type") == "array")
    assert zoom["maxItems"] == S.ZOOM_MAX == SEE.ZOOM_MAX == 3
    assert set(cmp["$defs"]["Zoom"]["properties"]) == {"photo", "box", "geometry"}
    edit = p["facet_edit"]
    assert list(edit["properties"]) == ["parent_revision", "reset_layout", "edits", "judgement", "note", "object",
                                        "apply", "photos"]
    obj = next(a for a in edit["properties"]["object"]["anyOf"] if a.get("type") == "string")
    assert obj["maxLength"] == S.OBJECT_MAX == 3000
    pj = edit["$defs"]["PhotoJudgement"]["properties"]
    assert set(pj) == {"photo", "verdict", "differences"} and pj["differences"]["maxItems"] == 3
    assert set(edit["$defs"]["Difference"]["properties"]) == {"what", "d_mm", "y_mm"}
    assert set(p["facet_variants"]["properties"]) == {"parent_revision", "variants", "photos", "judgement"}
    assert set(p["facet_finish"]["properties"]) == {"status", "best_checkpoint", "summary", "judgement"}
    M = T.models(["01", "02", "05"], ["01", "02", "05", "B1"])
    with pytest.raises(ValidationError):
        M["Judgement"].model_validate(judgement_args(n=4))
    with pytest.raises(ValidationError):
        M["Zoom"].model_validate({"photo": "R1", "box": [0, 0, 10, 10], "geometry": True})
    with pytest.raises(ValidationError):
        M["ReadPoint"].model_validate({"name": "K", "kind": "corner",
                                       "picks": [{"photo": "05", "uv": [1.0, 2.0], "edges": None, "sd_px": None}]})


def tool_texts(tools: dict) -> list:
    return [t.description for t in tools.values()] + [d for t in tools.values()
                                                      for _p, d in L.schema_descriptions(t.params_json_schema)]


def test_tool_descriptions_are_short_facts(timeouts):
    """No phrase the run's contract refuses (cli.tool_contract: loop.REMOVED_PHRASES and cli.RETIRED_PHRASES, the
    whole list, not a part of it), and no v2 word (restore, depth, fade) in any tool or parameter text."""
    from glasses3d.agent.temple_lab import cli
    banned = tuple(cli.tool_contract().removed_phrases)
    assert set(L.REMOVED_PHRASES) | set(cli.RETIRED_PHRASES) <= set(banned)
    tools = tools_of(FakeLab(), k=5)
    for text in tool_texts(tools):
        assert not any(w in text.lower() for w in banned), text
        assert "restore" not in text and "depth" not in text and "fade" not in text, text
    assert all(len(t.description) <= 1200 for t in tools.values())
    fin = " ".join(tools["facet_finish"].description.split())
    assert "at least 5 distinct applied heads were compared in full" in fin and "(4) an object note exists" in fin
    assert "(5) for stuck, a facet_variants result with a rendered variant was shown after the latest applied " \
           "head" in fin
    assert S.DIFFERENT_ATTEMPT in fin and "every photo with a camera (a zoom alone does not count)" in fin
    assert 'the last 3 judgements cover 3 distinct heads with none "better" (one judgement per compare' in fin


def test_description_numbers_are_the_codes(timeouts):
    """Every number a tool text states is the constant the code enforces (review 2026-10-12: the variant counts,
    target photos, spread, zoom and variant caps, station step were retyped literals), and each scale names its cap
    (a text that promised native pixels where the code shrinks the image)."""
    from glasses3d.agent.temple_lab import lab as LAB
    from glasses3d.agent.temple_lab import sheets as SH
    from glasses3d.g3d_host import compare as C
    tools = tools_of(FakeLab())

    def params(name):
        return {path.split(".")[-1]: " ".join(text.split())
                for path, text in L.schema_descriptions(tools[name].params_json_schema)}
    flat = {name: " ".join(t.description.split()) for name, t in tools.items()}
    assert f"(at most {S.READ_POINTS_MAX})" in params("photo_read")["points"]
    assert f"(at most {S.READ_DROP_MAX})" in params("photo_read")["drop"]
    assert f"{RD.PHOTOS_MIN} to {RD.PHOTOS_MAX} photos" in flat["photo_read"]
    assert f"At most {S.ZOOM_MAX} boxes" in params("photo_compare")["zoom"]
    assert f"at least {SEE.ZOOM_MIN_PX} px a side" in params("photo_compare")["box"]
    assert f"at most {S.OBJECT_MAX} characters" in params("facet_edit")["object"]
    assert f"at most {LAB.NOTE_MAX} characters" in params("facet_edit")["note"]
    assert f"(at most {G.EDITS_MAX})" in params("facet_edit")["edits"]
    assert f"up to {S.DIFFERENCES_MAX} differences" in json.dumps(tools["facet_edit"].params_json_schema)
    # photo_image: k = min(scale, IMAGE_MAX / long side) (sheets.photo_view), stated with its cap
    assert f"1 to {S.SCALE_MAX}: the crop enlarged up to this many times, its long side at most {SH.IMAGE_MAX} px" \
        in params("photo_image")["scale"]
    assert f"k = min(scale, {SH.IMAGE_MAX} / its long side in native px)" in flat["photo_image"]
    assert "below native scale even at scale 1" in flat["photo_image"]
    # zoom panels: compare.zoom_scale; side-view stations: seeing.GRID_STATION_MM
    zw, zh = C.ZOOM_PANEL_PX
    assert f"k = min({C.ZOOM_MAX_K:g}, {zw} / box width, {zh} / box height)" in flat["photo_compare"]
    assert f"native or larger for a box up to {zw} x {zh} native px, smaller beyond" in flat["photo_compare"]
    assert C.zoom_scale(zw, zh) >= 1.0 > C.zoom_scale(zw + 1, zh) and C.zoom_scale(10, 10) == C.ZOOM_MAX_K
    assert f"outline offsets every {SEE.GRID_STATION_MM:g} mm" in flat["photo_compare"]
    # facet_variants: the lab's counts and targets, sheets.variant_scale's cap and cell, the spread
    var = tools["facet_variants"].params_json_schema["properties"]["variants"]
    assert (var["minItems"], var["maxItems"]) == (LAB.VARIANTS_MIN, LAB.VARIANTS_MAX) == (2, 4)   # v3-final: 6 -> 4
    assert f"Build {LAB.VARIANTS_MIN} to {LAB.VARIANTS_MAX} variants" in flat["facet_variants"]
    assert f"{LAB.VARIANTS_MIN} to {LAB.VARIANTS_MAX} labelled edit lists" in params("facet_variants")["variants"]
    assert f"at most {LAB.VARIANT_TARGETS_MAX} of them target photos" in params("facet_variants")["photos"]
    assert f"at k up to {SH.VARIANT_K_MAX:g} (native or larger when that part fits a {SH.CELL_W} x {SH.CELL_H_MAX} " \
           f"px panel, smaller beyond" in flat["facet_variants"]
    assert SH.variant_scale(SH.CELL_W, SH.CELL_H_MAX) >= 1.0 > SH.variant_scale(SH.CELL_W + 1, SH.CELL_H_MAX)
    assert f"({S.SPREAD_VARIANTS} rendered variants pairwise >= {G.VARIANT_DISTINCT_MM:g} mm apart)" in \
        flat["facet_variants"]
    assert S.SPREAD_MM == G.VARIANT_DISTINCT_MM
    assert len(G.spread_triple(["a", "b", "c"], {("a", "b"): 1.0, ("a", "c"): 1.0, ("b", "c"): 1.0})) == \
        S.SPREAD_VARIANTS
    fin = " ".join(tools["facet_finish"].description.split())
    assert f"at least {S.SPREAD_VARIANTS} variants pairwise at least {G.VARIANT_DISTINCT_MM:g} mm apart" in fin
    assert f"{S.SPREAD_VARIANTS} variants pairwise >= {G.VARIANT_DISTINCT_MM:g} mm apart" in \
        S.finish_missing(S.new_state("rig"), status="matched", best_revision=0, k=1, distinct=lambda a, b: True,
                         camera_photos=[], has_object=True)[1]["message"]
    # the edits union: reading position, typed t's sd, warp corners, soft band, a declaration, names, scalars
    schema = json.dumps(T.edits_schema())
    assert f"within {G.READ_POSITION_MM:g} mm" in schema and f"({WARP_MIN_CORNERS} or more corners)" in schema
    assert "a 6 mm band, smooth where it folds under 25 deg" in schema
    assert f"an observation of sd {FIT.TYPED_SD_MM:g} mm" in schema
    snap = DEFAULT_OPTIONS["snap_mm"]                                     # set_node outline, the chains (e2e P1-1)
    assert f"a node within {snap:g} mm of an outline edge lies on it" in schema and snap == G.snap_of(FX.slab())
    assert f"a node on it, within {snap:g} mm, moves with it in y, a node set from a reading at most " \
           f"{G.READ_POSITION_MM:g} mm from where it was read" in schema
    assert f"a weak prior of sd {FIT.PRIOR_SD_MM:g} mm toward the plain arm" in schema      # solve (owner P2-3)
    assert f"folding at least {FIT.MIN_CREASE_DEG:g} deg, or refuses E_FIT_SHAPE" in schema
    assert f"chi2 more than {FIT.ACCEPT_DCHI2:g} above" in schema
    assert f"mm, 0 to {G.SCALAR_LIMIT_MM:g}" in schema
    assert schema.count(f"{G.NAME_RULE}; not 'base'") == 2                  # SetNode.name, SetFacet.label
    assert G.NAME_RULE == f"a letter, then up to {G.NAME_MAX - 1} letters, digits, _ or -" and G.NAME_MAX == RD.NAME_MAX
    assert G.NAME_RE.match("a" * G.NAME_MAX) and not G.NAME_RE.match("a" * (G.NAME_MAX + 1))
    assert f"the node it is for: {G.NAME_RULE}; not 'base'" in json.dumps(tools["photo_read"].params_json_schema)
    assert f"a short name: {G.NAME_RULE}, not ending in _" in json.dumps(tools["facet_variants"].params_json_schema)


def test_tool_texts_state_what_the_code_does(timeouts):
    """The facts the review of 2026-10-12 found missing or wrong: the default photo set, the variant sheet's temple and
    region, a reading as a snapshot, the object rule on any parent, a refused call's note, a judgement when none is
    due, the crease declaration and the chains."""
    tools = tools_of(FakeLab())
    flat = {name: " ".join(t.description.split()) for name, t in tools.items()}
    params = {name: {p: " ".join(x.split()) for p, x in L.schema_descriptions(t.params_json_schema)}
              for name, t in tools.items()}
    target = (f"null for the target photos whose temple shows at least {SEE.TEMPLE_ROI_MIN_PX:,} native px (the "
              "result's left_out names the others; a photo of another colour only when named)")   # v3p3 astra P3-9:
    assert target in params["photo_compare"]["$.properties.photos"]       # "null for the target photos" left out 04
    assert target in params["facet_edit"]["$.properties.photos"]
    assert "default set" not in json.dumps([flat, params])
    assert "on the temple whose outer face that photo sees (charted or mirrored)" in flat["facet_variants"]
    assert "the part where the arms differ from the head (else the faceted zone)" in flat["facet_variants"]
    assert "charted temple's faceted zone" not in flat["facet_variants"]
    assert "A node keeps the reading it was set from: replacing or dropping the stored reading does not change the " \
           "node; set_node with t mode reading sets a node from the stored reading of its name." in flat["photo_read"]
    assert "differs from the layout the latest note was written for, whatever the parent" in flat["facet_edit"]
    assert 'a note in a refused call is kept only when the result says "object": "recorded"' in flat["facet_edit"]
    for name in ("facet_edit", "facet_variants", "facet_finish"):
        assert params[name]["$.properties.judgement"] == T.JUDGEMENT_TEXT
    assert "count a photo as compared only when it was shown in full, not by a zoom alone" in flat["photo_compare"]
    edits = params["facet_edit"]
    assert edits["$.$defs.SetCrease.properties.facets"] == "two facet labels, or a facet and 'base' (the plain arm)"
    assert edits["$.$defs.DeleteCrease.properties.facets"] == edits["$.$defs.SetCrease.properties.facets"]
    assert edits["$.$defs.SpliceChain.properties.chain"] == edits["$.$defs.OffsetChain.properties.chain"] == \
        T.CHAIN_TEXT
    assert all(c in T.CHAIN_TEXT for c in G.CHAINS)
    assert "roll_mm" in edits["$.$defs.SetScalar.properties.key"] and \
        "tip_round_mm" in edits["$.$defs.SetScalar.properties.key"]
    # v3p3-fix-lab: solve is what the fit does (owner P2-3: "set by the faces and readings around it" was false on a
    # warp and hid that its own reading is dropped); the outline relation (e2e P1-1); the variant and edit texts
    mode = edits["$.$defs.Thickness.properties.mode"]
    assert "a stored reading of its name is not used (W_FIT_OFF_READING says how far it lies)" in mode
    assert "the faces and readings around it" not in mode
    assert edits["$.$defs.SetNode.properties.outline"] == " ".join(T.OUTLINE_TEXT.split())
    # v3-standin-fix: each value as what it does (the blind stand-in's "a corner on that edge" left the notch at E)
    outline = T.OUTLINE_TEXT
    assert "top / bottom: the node is kept on that edge: the edge's knot is moved to the node's (d, y)" in outline
    assert "where the node lies off the edge's line the edge bends there (the result gives the bend in mm and deg: " \
           "facet_edit's receipt in outline_bends, a variant in its notes)" in outline       # v3-standin-fix-review
    assert "none: a free node: the outline keeps its shape (a knot the node held stays where it was) and the node " \
           "may lie inside, on or beyond the outline" in outline
    assert "null: as before this edit (a node on an edge stays on it, its knot moving with it; a new node is free)" \
        in outline and "a corner on that edge" not in outline
    assert "the facet is clipped to the outline, so a corner may lie beyond it" in \
        edits["$.$defs.SetFacet.properties.poly"]
    assert "receipt's deleted_creases" in edits["$.properties.edits"] and "outline_bends" in edits["$.properties.edits"]
    assert "a facet|base one any plain arm between a straight side and a curved outline" in flat["facet_edit"]
    assert "so for the top it prints the arm's top height on that face's far edge, less its median over the stations" \
        in flat["photo_compare"]                                          # what seeing's text now says (v3-standin-fix)
    assert "an arm that builds the head's own design is no variant" in flat["facet_variants"]
    assert "how far its ridges and valleys lie from the head's (mm)" in flat["facet_variants"]
    assert "crease_vs_head (ridge <-> valley) and edges_vs_head (a crease that appears or goes flat)" in \
        flat["facet_edit"]
    assert "the stored readings (one no node is named for: against its nearest node)" in flat["facet_inspect"]


def test_the_prompt_states_the_finish_rule_finish_checks():
    """The prompt's FINISHING clauses (instructions.txt, the coordinator's) are facet_finish's own (FINISH_DOC, from
    "(1)" to "the run continues.", K substituted): the code checks that a facet_variants result was SHOWN after the
    latest applied head (a variants sheet has no compare_id, so no judgement can name it), counts full compares only
    for (1) and (3), and one judgement per compare for (5). Fails while the prompt says otherwise (review 2026-10-12:
    the prompt's (5) "a facet_variants call was judged" is a condition no tool can meet)."""
    raw = (HERE.parents[2] / "agent" / "temple_lab" / "instructions.txt").read_text(encoding="utf-8")
    k = 4
    text = " ".join(raw.replace("{K}", str(k)).split())
    doc = " ".join(T.FINISH_DOC.format(k=k, different=S.DIFFERENT_ATTEMPT, last=S.STUCK_LAST,
                                       spread=S.SPREAD_VARIANTS, spread_mm=G.VARIANT_DISTINCT_MM,
                                       judgement=T.JUDGEMENT_TEXT).split())

    def clauses(t: str) -> str:
        i, j = t.index("(1) "), t.index("the run continues.")
        return t[i:j]
    assert clauses(text) == clauses(doc)


def test_the_instructions_name_exactly_the_registry():
    """The prompt (instructions.txt) names every registered tool and no retired one."""
    text = (HERE.parents[2] / "agent" / "temple_lab" / "instructions.txt").read_text(encoding="utf-8")
    assert all(name in text for name in T.TOOL_NAMES)
    assert "facet_restore" not in text


# --------------------------------------------------------------------------------------------- I5: the Lab calls
def tool_calls() -> list:
    """(tool, its arguments, (the Lab method, the positional arguments after self)) for every tool (I5)."""
    picks = [{"photo": "05", "uv": [10.0, 20.0], "edges": None, "sd_px": None},
             {"photo": "B1", "uv": None, "sd_px": 1.5,
              "edges": [[[1.0, 2.0], [5.0, 6.0]], [[9.0, 1.0], [7.0, 3.0], [5.0, 5.0]]]}]
    edits = quad("warp", True)
    zoom = [{"photo": "B1", "box": [10, 20, 110, 90], "geometry": True}]
    points = [{"photo": "05", "points": [[1.0, 2.0]]}]
    variants = [{"label": "a", "edits": []}, {"label": "b", "edits": edits[:1]}]
    return [
        ("facet_inspect", {}, ("inspect", ())),
        ("photo_image", {"photo": "R1", "crop": [0, 0, 40, 30], "enhance": True, "scale": 3},
         ("photo", ("R1", [0, 0, 40, 30], True, 3))),
        ("photo_read", {"points": [{"name": "K", "kind": "corner", "picks": picks}], "drop": ["E"]},
         ("read", ("c1", [{"name": "K", "kind": "corner", "picks": picks}], ["E"]))),
        ("photo_compare", {"photos": ["05", "B1"], "crop": None, "zoom": zoom, "points_px": points},
         ("compare", ("c1", ["05", "B1"], None, zoom, points))),
        ("facet_edit", {"parent_revision": 3, "reset_layout": False, "edits": edits, "judgement": judgement_args(),
                        "note": "warp the crown", "object": "crown: one face", "apply": True, "photos": None},
         ("edit", ("c1", 3, False, edits, judgement_args(), "warp the crown", "crown: one face", True, None))),
        ("facet_variants", {"parent_revision": 2, "variants": variants, "photos": ["05"], "judgement": None},
         ("variants", ("c1", 2, variants, ["05"], None))),
        ("facet_finish", {"status": "stuck", "best_checkpoint": "c0003", "summary": "s", "judgement": None},
         ("finish", ("c1", "stuck", "c0003", "s", None, 4))),
    ]


def test_each_tool_calls_its_lab_method(timeouts):
    lab = FakeLab()
    tools = tools_of(lab, k=4)
    for name, args, want in tool_calls():
        out = call(tools[name], args, call_id="c1")
        assert body(out) == {"status": "ok"}, (name, out)
        assert lab.calls[-1] == want, name
    assert len(lab.calls) == len(T.TOOL_NAMES)


def test_the_calls_bind_to_the_real_lab_methods():
    """The positional calls land on the real Lab's parameters of the same role (I6's signatures)."""
    from glasses3d.agent.temple_lab.lab import Lab
    bound = {method: inspect.signature(getattr(Lab, method)).bind(None, *args).arguments
             for _tool, _args, (method, args) in tool_calls()}
    assert bound["photo"]["enhance"] is True and bound["photo"]["scale"] == 3
    assert bound["read"]["points"][0]["name"] == "K" and bound["read"]["drop"] == ["E"]
    assert bound["compare"]["zoom"][0]["geometry"] is True and bound["compare"]["points"][0]["photo"] == "05"
    assert bound["edit"]["object"] == "crown: one face" and bound["edit"]["apply"] is True
    assert bound["edit"]["note"] == "warp the crown" and bound["edit"]["photos"] is None
    assert bound["variants"]["photos"] == ["05"] and bound["finish"]["k"] == 4


def test_busy_and_refusals_reach_astra_as_results(timeouts):
    lab = FakeLab(result=S.LabRefusal("E_OBJECT", S.OBJECT_REFUSAL))
    tools = tools_of(lab)
    out = body(call(tools["facet_edit"], {"parent_revision": 0, "reset_layout": False, "edits": [],
                                          "judgement": None, "note": "n", "object": None, "apply": False,
                                          "photos": None}))
    assert out == {"status": "refused", "code": "E_OBJECT",
                   "message": "write the object note (facet_edit object) first"}
    lab.result = G.EditError("there is no reading named Q: read it with photo_read first", 2)
    out = body(call(tools["facet_edit"], {"parent_revision": 0, "reset_layout": False, "edits": [],
                                          "judgement": None, "note": "n", "object": None, "apply": False,
                                          "photos": None}))
    assert out["code"] == "E_EDIT" and out["edit"] == 2
    lab.result = ValueError("picks must name 2..4 photos with a camera")
    out = body(call(tools["photo_read"], {"points": [], "drop": []}))
    assert out["code"] == "E_LAB_ARGS" and "2..4 photos" in out["message"]
    lab.lock.acquire()
    try:
        lab.running = {"tool": "photo_compare", "call_id": "x", "t0": 0.0}
        out = body(call(tools["facet_inspect"], {}))
        assert out["code"] == "E_BUSY" and out["running"]["tool"] == "photo_compare"
    finally:
        lab.lock.release()


# --------------------------------------------------------------------------------------------- I4: judgements
def judged_state() -> dict:
    st = S.new_state("rig")
    st["heads"] = [{"checkpoint": "c0001", "fingerprint": "fp1", "revision": 1, "call_id": "e1", "seq": 1}]
    st["compares"] = [{"compare_id": "cmp002", "checkpoint": "c0001", "fingerprint": "fp1", "revision": 1,
                       "photos": ["01", "05"], "by": "astra", "call_id": "e1", "judged": False, "seq": 2}]
    return st


FP = {"c0001": "fp1"}.get


def test_a_judgement_names_up_to_three_differences_per_photo():
    st = judged_state()
    for n in (0, 1, 3):
        rec = S.check_judgement(st, judgement_args(n=n), fingerprint_of=FP)
        assert [len(p["differences"]) for p in rec["photos"]] == [n, n]
    rec = S.check_judgement(st, judgement_args(n=3), fingerprint_of=FP)
    assert rec["photos"][0]["differences"][2] == {"what": "crown tip 2", "d_mm": 76.0, "y_mm": 9.0}
    S.record_judgement(st, rec, call_id="e2", tool="facet_edit")
    assert st["compares"][0]["judged"] and st["judgements"][-1]["photos"] == rec["photos"]
    assert S.pending_compare(st) is None


@pytest.mark.parametrize("change, words", [
    (lambda j: j["photos"][0]["differences"].append(dict(j["photos"][0]["differences"][0])), "at most 3"),
    (lambda j: j["photos"][0]["differences"][0].update(what=" "), "what must be 1..200"),
    (lambda j: j["photos"][0]["differences"][0].update(what="x" * 201), "what must be 1..200"),
    (lambda j: j["photos"][0]["differences"][0].update(d_mm=float("nan")), "d_mm must be a finite number"),
    (lambda j: j["photos"][0]["differences"][0].update(y_mm=None), "y_mm must be a finite number"),
    (lambda j: j["photos"][0].update(differences=None), "differences must list at most 3"),
    (lambda j: j["photos"].append(dict(j["photos"][0])), "is repeated"),
    (lambda j: j["photos"].pop(), "misses ['05']"),
    (lambda j: j["photos"][0].update(verdict="fine"), "verdict must be one of"),
    (lambda j: j.update(best_checkpoint="c9"), "best_checkpoint 'c9'"),
])
def test_judgement_limits_are_refused(change, words):
    j = judgement_args(n=3)
    change(j)
    with pytest.raises(S.LabRefusal) as info:
        S.check_judgement(judged_state(), j, fingerprint_of=FP)
    assert info.value.code == "E_JUDGEMENT" and words in info.value.message, info.value.message


# --------------------------------------------------------------------------------------------- I4: the object note
def test_object_note_is_stored_and_capped():
    st = S.new_state("rig")
    assert S.object_text(st) is None
    S.record_object(st, "crown: one face, A-H-K-E", "e1", spec=canonical(FX.prada_default()))
    S.record_object(st, "x" * S.OBJECT_MAX, "e2")
    assert S.object_text(st) == "x" * 3000 and [o["call_id"] for o in st["objects"]] == ["e1", "e2"]
    assert st["objects"][0]["layout"] == S.layout_key(FX.prada_default()) and st["objects"][1]["layout"] is None
    for bad in ("x" * 3001, " ", None, 5):
        with pytest.raises(S.LabRefusal) as info:
            S.record_object(st, bad, "e3")
        assert info.value.code == "E_ARGS"
    assert len(st["objects"]) == 2


def test_layout_changes_are_facets_or_node_names():
    a = canonical(FX.prada_default())
    same = copy.deepcopy(a)
    same["nodes"]["C"][0] += 1.0                                            # a node move
    same["nodes"]["C"][2] = None                                            # a value
    same["facets"]["F2"]["plane"] = "warp"                                  # a plane mode
    same["facets"]["F3"]["soft"] = True                                     # a soft border
    same["facets"]["F2"]["poly"] = ["C", "B", "A"]                          # the same face the other way round
    same["facets"]["F4"]["poly"] = same["facets"]["F4"]["poly"][2:] + same["facets"]["F4"]["poly"][:2]
    same["creases"] = []                                                    # declarations
    assert not S.layout_changed(a, same) and S.layout_key(a) == S.layout_key(same)
    for change in (lambda s: s["nodes"].update(Z=[1.0, 1.0, 5.0]), lambda s: s["nodes"].pop("H_lo"),
                   lambda s: s["facets"].update(F5=s["facets"].pop("F4")),
                   lambda s: s["facets"]["F2"].update(poly=["A", "C", "B", "K"]),
                   lambda s: s["facets"].pop("F2")):
        b = copy.deepcopy(a)
        change(b)
        assert S.layout_changed(a, b)
    assert S.layout_changed(None, a)


def test_the_object_rule():
    """E_OBJECT: no note yet -> due; a note in the call -> never; a layout change -> due unless the latest note was
    written with that layout (an earlier call that made it); no layout change -> not due once a note exists."""
    a = canonical(FX.prada_default())
    b = copy.deepcopy(a)
    b["facets"].pop("F2")
    st = S.new_state("rig")
    assert S.object_due(st, a, a) and S.object_due(st, None, a)            # the first facet_edit
    assert not S.object_due(st, a, b, "crown and logo") and S.object_due(st, a, b, "  ")
    with pytest.raises(S.LabRefusal) as info:
        S.check_object(st, a, a, None)
    assert (info.value.code, info.value.message) == ("E_OBJECT", "write the object note (facet_edit object) first")
    S.record_object(st, "four faces", "e1", spec=a)
    assert not S.object_due(st, a, a)                                      # no layout change
    assert S.object_due(st, a, b)                                          # a layout change, the note is for a
    S.record_object(st, "three faces", "e2", spec=b)                       # e.g. a call whose build was refused
    assert not S.object_due(st, a, b)
    S.check_object(st, a, b, None)
    S.record_object(st, "a note with no design", "e3")
    assert S.object_due(st, a, b) and not S.object_due(st, b, b)


def test_the_object_rule_follows_the_latest_note_not_the_parent():
    """v3 has no restore tool: applying a facet_variants arm or going back to an older revision is facet_edit(parent
    = that revision, edits []), whose parent has the new design's layout. The note is due whenever the design's layout
    is not the one the latest note was written for (review 2026-10-12: a four-face note, then a three-face arm applied,
    passed, and facet_inspect showed the four-face note for the three-face head). E_OBJECT states the fact."""
    a = canonical(FX.prada_default())
    b = copy.deepcopy(a)
    b["facets"].pop("F2")
    b["nodes"]["M"] = [60.0, 5.0, None]
    st = S.new_state("rig")
    S.record_object(st, "four faces", "e1", spec=a)
    assert S.object_due(st, b, b)                                          # a variant arm with layout b applied
    assert S.object_due(st, b, b, None) and not S.object_due(st, b, b, "three faces")
    with pytest.raises(S.LabRefusal) as info:
        S.check_object(st, b, b, None)
    assert (info.value.code, info.value.message) == ("E_OBJECT", S.OBJECT_REFUSAL)
    assert info.value.to_dict()["latest_object"] == ("the latest object note (of call e1) was written for another "
                                                     "layout; this design: +node M; -facet F2")   # an older entry
    entry = S.record_object(st, "three faces", "e2", spec=b, parent=3)
    assert entry["parent"] == 3
    assert S.object_origin(entry) == "given in a facet_edit of revision 3 that made no revision"   # refused fit
    entry["revision"] = 4                                                  # the lab: the call's design became r4
    assert not S.object_due(st, b, b)
    assert S.object_due(st, a, a)                                          # back to the four-face revision
    with pytest.raises(S.LabRefusal) as info:
        S.check_object(st, a, a)
    assert info.value.fields == {"latest_object": "the latest object note (given with revision 4) was written for "
                                                  "another layout; this design: -node M; +facet F2"}
    assert "e2" not in json.dumps(info.value.fields)       # the SDK call id names nothing Astra can map (P3-5)
    S.record_object(st, "four faces again", "e3", spec=a)
    assert not S.object_due(st, b, a)                                      # a value edit of b's parent lands on a
    c = copy.deepcopy(a)
    c["facets"]["F2"]["poly"] = c["facets"]["F2"]["poly"][:-1] + ["M"]
    c["nodes"]["M"] = [60.0, 5.0, None]
    assert S.layout_diff(S.layout_key(a), S.layout_key(c)) == "+node M; ~facet F2 poly"
    assert S.layout_diff(S.layout_key(a), S.layout_key(a)) == ""
    many = {"nodes": [f"N{i}" for i in range(9)], "facets": {}}
    assert S.layout_diff(None, many) == "+node N0, N1, N2, N3, N4, +4 more"
    S.record_object(st, "a note with no design", "e4", parent=5)           # layout None: the parent decides
    with pytest.raises(S.LabRefusal) as info:
        S.check_object(st, a, c)
    assert info.value.fields["latest_object"] == ("the latest object note (given in a facet_edit of revision 5 that "
                                                  "made no revision) has no design; this design's layout differs from "
                                                  "its parent's: +node M; ~facet F2 poly")
    assert not any(w in info.value.fields["latest_object"] for w in REMEDY_WORDS)


# --------------------------------------------------------------------------------------------- I4: judged once
def test_a_judgement_when_none_is_due_is_not_stored():
    """One judgement per compare: a judgement given when the latest seen compare is judged already is not stored
    again (None), on any tool. Two routes made duplicates (review 2026-10-12): a refused facet_edit retried with the
    judgement its first try had stored (package_003: cmp004 twice), and a judgement passed on facet_variants to make
    a variants call "judged"; three copies filled finish precondition 5's last 3 with one head."""
    st = judged_state()
    rec = S.check_judgement(st, judgement_args(), fingerprint_of=FP)
    assert S.judgement_not_due(st, judgement_args()) is None              # due: not this case
    S.record_judgement(st, rec, call_id="e2", tool="facet_edit")
    for tool in ("facet_edit", "facet_variants", "facet_finish"):         # the retry, a variants call, a finish
        again = S.check_judgement(st, judgement_args(), fingerprint_of=FP)
        assert again is None
        S.record_judgement(st, again, call_id="e3", tool=tool)
    assert [j["compare_id"] for j in st["judgements"]] == ["cmp002"]
    # v3-standin-fix: the earlier judgement by its tool and its own words, never the SDK's call id ("call s25")
    assert S.judgement_not_due(st, judgement_args()) == (
        "no judgement was due: the latest compare, cmp002, was judged already by the judgement a facet_edit call "
        "carried (overall: mixed; best_checkpoint: c0001); this judgement of cmp002 was not stored (one judgement per "
        "compare; the next compare makes one due)")
    st2 = copy.deepcopy(st)
    st2["judgements"][0].update(tool="facet_variants", overall="same")
    text = S.judgement_not_due(st2, {**judgement_args(), "compare_id": "cmp001"})
    assert text.startswith("no judgement was due: the latest compare, cmp002, was judged already by the judgement a "
                           "facet_variants call carried (overall: same;")
    assert "this judgement of cmp001 was not stored" in text and "call e2" not in text
    assert S.judgement_not_due(st, None) is None
    other = {**judgement_args(), "compare_id": "cmp001", "overall": "better"}    # any judgement when none is due
    assert S.check_judgement(st, other, fingerprint_of=FP) is None
    st["compares"].append({**st["compares"][0], "compare_id": "cmp003", "judged": False, "seq": 3})
    with pytest.raises(S.LabRefusal) as info:                              # a new compare is due again
        S.check_judgement(st, judgement_args(), fingerprint_of=FP)
    assert info.value.code == "E_JUDGEMENT" and "cmp003" in info.value.message
    with pytest.raises(S.LabRefusal):
        S.check_judgement(S.new_state("rig"), judgement_args(), fingerprint_of=FP)    # no compare at all
    assert [j["compare_id"] for j in S.last_judgements({"judgements": [{"compare_id": c} for c in
                                                                       ("a", "b", "b", "c", "c", "c")]})] == \
        ["a", "b", "c"]


# --------------------------------------------------------------------------------------------- I3: readings in use
def test_a_node_keeps_the_reading_it_was_set_from(recs, slab_tb):
    """apply_edits copies a reading into the design; replacing or dropping the stored reading leaves the node's t (the
    revision's fit and build never change under it). stale_reads names those nodes for the lab (review 2026-10-12:
    Q re-read 2.4 sd away left the head's Q and its fit report unchanged, with nothing saying so)."""
    design, _ = G.apply_edits(FX.slab(), quad(read=("Q", "R")), readings=recs)
    assert G.stale_reads(design, recs) == {}
    assert G.stale_reads(FX.slab(), recs) == {}                            # no read nodes
    bs = SynthBasis()
    again = {**recs, "Q": RD.read_point(slab_tb, bs, "Q", corner_picks(bs, point(slab_tb, 40.0, 0.0, 7.9),
                                                                         ["05", "02"]), kind="corner")}
    assert G.stale_reads(design, again) == {"Q": "replaced"}
    assert G.stale_reads(design, {"Q": recs["Q"]}) == {"R": "dropped"}
    roundtrip = json.loads(json.dumps(design))                             # a revision file read back
    assert G.stale_reads(roundtrip, json.loads(json.dumps(recs))) == {}
    reread, _ = G.apply_edits(design, [node("Q", None, None, "reading")], readings=again)
    assert G.stale_reads(reread, again) == {} and reread["nodes"]["Q"][2] != design["nodes"]["Q"][2]
    # the same picks read again (e.g. after an E_TIMEOUT): only the record id differs, so it is not "replaced" (v3p3
    # e2e P3-1: "node K holds the earlier reading K" at X +0.00 invited a set_node that made a second revision with
    # the head's fitted spec); other numbers still are
    same = {**recs, "Q": {**recs["Q"], "id": "read009-Q", "call_id": "call_again"}}
    with_ids = G.apply_edits(FX.slab(), quad(read=("Q", "R")), readings={n: {**r, "id": f"read001-{n}"}
                                                                          for n, r in recs.items()})[0]
    assert G.stale_reads(with_ids, same) == {} and G.stale_reads(with_ids, again) == {"Q": "replaced"}


# --------------------------------------------------------------------------------------------- I3: the outline relation
TOP_D = 66.0                         # between the slab's top knots at d 60 and 70


@pytest.fixture(scope="module")
def edge_recs(slab_tb):
    """Readings of a corner on the slab's top outline edge, 0.2 mm below it at d 66 ("T"), and the same corner read
    again 0.12 mm lower and 0.05 mm back ("T2", stored under the name T for a re-read)."""
    bs = SynthBasis()
    yt = G._interp(canonical(FX.slab())["outline"]["top"], TOP_D)
    out = {}
    for key, (d, y) in {"T": (TOP_D, yt - 0.2), "T2": (TOP_D + 0.05, yt - 0.32)}.items():
        out[key] = RD.read_point(slab_tb, bs, "T", corner_picks(bs, point(slab_tb, d, y, 6.0), ["05", "02"]),
                                 kind="corner")
    return out


def knots_near(spec, d, chain="top", mm=0.3) -> list:
    return [p for p in G.canonical_design(spec)["outline"][chain] if abs(p[0] - d) <= mm]


def turn_at(spec, d, chain="top") -> float:
    """How far an outline chain turns at its knot at d (deg): the slope angles of the segments beside it, apart."""
    P = G.canonical_design(spec)["outline"][chain]
    k = next(j for j, p in enumerate(P) if abs(p[0] - d) < 1e-9)
    into = math.degrees(math.atan2(P[k][1] - P[k - 1][1], P[k][0] - P[k - 1][0]))
    out = math.degrees(math.atan2(P[k + 1][1] - P[k][1], P[k + 1][0] - P[k][0]))
    return abs(out - into)


def test_set_node_outline_puts_the_edges_knot_at_a_corner_and_keeps_it(edge_recs):
    """v3p3 e2e P1-1 (verify-0): a corner read on the outline stayed on the built outline only while a knot sat at it,
    and set_node changed the node without the knot. set_node outline top / bottom puts that chain's knot at the node
    (a note and the diff say so); null keeps the edge the node lay on before the edit, so a re-read carries the knot
    (the old one goes, one knot at the new reading); none leaves the chains; a new node with null leaves them; an
    edge change moves the knot to the other chain; a d outside the outline is refused, naming the node."""
    recs = {"T": edge_recs["T"]}
    t = edge_recs["T"]
    slab = canonical(FX.slab())
    first = node("T", None, None, "reading") | {"outline": None}
    plain, notes = G.apply_edits(slab, [first], readings=recs)            # a new node, null: the chains stay
    assert plain["outline"] == slab["outline"] and notes == []
    assert G.outline_edge(plain, t["d"], t["y"]) is None                   # 0.2 mm off: not on the edge
    on, notes = G.apply_edits(slab, [first | {"outline": "top"}], readings=recs)
    assert [t["d"], t["y"]] in on["outline"]["top"] and on["outline"]["bottom"] == slab["outline"]["bottom"]
    was = G._interp(slab["outline"]["top"], t["d"])                        # T lies 0.2 mm under the edge's line
    assert notes == [f"outline.top has its knot at node T, d {t['d']:.2f} y {t['y']:.2f} (outline top)",
                     f"outline.top bends at node T: its knot at d {t['d']:.2f} y {t['y']:.2f} lies "
                     f"{was - t['y']:.2f} mm below the line the edge ran on (y {was:.2f} there); between its knots at "
                     f"d 60.00 and 70.00 the edge now turns {turn_at(on, t['d']):.0f}° at the knot (it ran straight "
                     "there)"]
    assert G.outline_edge(on, t["d"], t["y"]) == "top"
    assert "outline.top d 60.0-70.0 (up to 0.20 mm)" in G.diff_text(G.diff_specs(slab, on))
    # a re-read 0.12 mm lower, set by mode reading with outline null: the knot moves with the corner
    t2 = edge_recs["T2"]
    reread, notes = G.apply_edits(on, [first], readings={"T": t2})
    assert knots_near(reread, TOP_D) == [[t2["d"], t2["y"]]]
    assert notes[0] == (f"outline.top has its knot at node T, d {t2['d']:.2f} y {t2['y']:.2f} (as before this edit: "
                        "it lay on that edge)")
    assert len(notes) == 2 and notes[1].startswith("outline.top bends at node T: its knot at d 66.05 y")
    kept, notes = G.apply_edits(on, [first | {"outline": "none"}], readings={"T": t2})    # none: the knot stays
    assert knots_near(kept, TOP_D) == [[t["d"], t["y"]]] and notes == []
    moved, notes = G.apply_edits(on, [node("T", 66.0, -1.0, "mm", 6.0) | {"outline": "bottom"}])
    assert knots_near(moved, TOP_D) == [] and [66.0, -1.0] in moved["outline"]["bottom"]      # left the top edge
    # the cap knot keeps its d: a corner on it moves it in y only, one off it adds a knot beside it
    cap = slab["outline"]["top"][0]
    capped, _ = G.apply_edits(slab, [node("A", cap[0], cap[1], "mm", 5.0)])
    capped2, _ = G.apply_edits(capped, [node("A", 0.0, cap[1] + 0.2, "mm", 5.0)])
    assert capped2["outline"]["top"][0] == [0.0, cap[1] + 0.2]
    capped3, _ = G.apply_edits(capped, [node("A", 0.4, cap[1] + 0.2, "mm", 5.0)])
    assert capped3["outline"]["top"][:2] == [cap, [0.4, cap[1] + 0.2]]
    err = refused(slab, [node("Z", -2.0, 10.0, "mm", 5.0) | {"outline": "top"}])
    assert err.index == 0 and "node Z at d -2.00 lies outside the outline's d range 0.00-136.50" in err.message
    assert refused(slab, [node("Z", 5.0, 10.0, "mm", 5.0) | {"outline": "left"}]).message == \
        "outline must be top, bottom, none or null"


def test_chain_edits_carry_a_corner_on_the_edge_within_its_reading(edge_recs):
    """v3p3 e2e P1-1: offset_chain / splice_chain moved the knot and left the corner (an offset across a corner was
    refused E_SHORT_EDGE or built a false border). A node on an outline edge before the edit moves with it in y (a
    note each); a node holding a reading at most READ_POSITION_MM from where it was read, else EditError with the
    range the edge may move there (facts, no remedy); a node off the edge, and the other chains, are left alone."""
    recs = {"T": edge_recs["T"]}
    t = edge_recs["T"]
    on, _ = G.apply_edits(canonical(FX.slab()), [node("T", None, None, "reading") | {"outline": "top"},
                                                 node("B", 40.0, 0.0, "mm", 6.0)], readings=recs)
    up, notes = G.apply_edits(on, [{"op": "offset_chain", "chain": "outline.top", "d_from_mm": 60.0, "d_to_mm": 70.0,
                                    "delta_mm": 0.3, "taper_mm": 2.0}])
    assert up["nodes"]["T"][:2] == [t["d"], pytest.approx(t["y"] + 0.3)] and up["nodes"]["B"] == on["nodes"]["B"]
    assert notes == [f"node T moved with outline.top to y {t['y'] + 0.3:.2f} at d {t['d']:.2f} (+0.30 mm; it lies "
                     "on that edge)"]
    assert G.outline_edge(up, *up["nodes"]["T"][:2]) == "top"
    err = refused(on, [{"op": "offset_chain", "chain": "outline.top", "d_from_mm": 60.0, "d_to_mm": 70.0,
                        "delta_mm": 0.6, "taper_mm": 0.0}])
    assert err.index == 0 and err.fields["node"] == "T" and err.fields["chain"] == "outline.top"
    assert "moves node T, which lies on that edge" in err.message and "0.600 mm from where reading T was read" in \
        err.message and "the edge may move there from -0.500 to +0.500 mm with T on it" in err.message
    # the edge's room is counted from where the edge was at the node's d (a node within snap_mm beside the line lands
    # on it): session2's C, 0.002 mm under the bottom line, read "0.50 mm ... from -0.50 to +0.50" for +0.50 refused
    beside = copy.deepcopy(on)
    next(p for p in beside["outline"]["top"] if p[0] == t["d"])[1] += 0.002          # the edge 0.002 mm above T
    assert G.outline_edge(beside, *beside["nodes"]["T"][:2]) == "top"
    err = refused(beside, [{"op": "offset_chain", "chain": "outline.top", "d_from_mm": 60.0, "d_to_mm": 70.0,
                            "delta_mm": 0.5, "taper_mm": 0.0}])
    assert "0.502 mm from where reading T was read" in err.message and "from -0.502 to +0.498 mm" in err.message
    assert not any(w in err.message for w in REMEDY_WORDS)
    pts = [[60.0, 12.42], [TOP_D, t["y"] - 0.1], [70.0, 10.74]]           # a splice through the corner 0.1 lower
    spliced, notes = G.apply_edits(on, [{"op": "splice_chain", "chain": "outline.top", "d_from_mm": 60.0,
                                         "d_to_mm": 70.0, "points": pts}])
    assert spliced["nodes"]["T"][1] == pytest.approx(t["y"] - 0.1, abs=1e-3) and len(notes) == 1
    other, notes = G.apply_edits(on, [{"op": "offset_chain", "chain": "outline.bottom", "d_from_mm": 60.0,
                                       "d_to_mm": 70.0, "delta_mm": 0.6, "taper_mm": 0.0}])
    assert other["nodes"] == on["nodes"] and notes == []                    # T is on the top edge, not the bottom


# --------------------------------------------------------------------------------------------- the blind stand-in
# (data/wp6v2/review_20261012/v3/standin/STANDIN_REPORT.md, 2026-10-13, v3-standin-fix): what the tools said or did not
# say. Every expected number below is computed here from the slab's own chains, not read back from the code.
def test_an_outline_knot_off_the_edge_line_states_the_bend_it_makes():
    """Stand-in rev 1: E set with outline bottom 0.97 mm below the bottom edge's line and 0.15 mm behind revision 0's
    knot at d 76.00 cut a notch (a 111° turn) into the outline under a note that said only "outline.bottom has its
    knot at node E". A knot off the edge's line now states its bend (mm off the line, the knots beside it, the turn
    there and what it was), handed to the lab's receipt as facts["outline_bends"]; a knot within snap_mm of the line
    bends nothing; an end knot names its neighbour; none leaves the outline."""
    slab = canonical(FX.slab())
    bot = slab["outline"]["bottom"]                                       # knots at d 70 (-2.12) and 80 (-0.24)
    line = G._interp(bot, 70.15)
    facts = {}
    out, notes = G.apply_edits(slab, [node("E", 70.15, line - 0.97, "mm", 5.0) | {"outline": "bottom"}], facts=facts)
    y = line - 0.97
    turn = turn_at(out, 70.15, "bottom")
    assert turn > 90.0                                                     # a notch, as the stand-in's E
    bend = (f"outline.bottom bends at node E: its knot at d 70.15 y {y:.2f} lies 0.97 mm below the line the edge ran "
            f"on (y {line:.2f} there); between its knots at d 70.00 and 80.00 the edge now turns {turn:.0f}° at the "
            "knot (it ran straight there)")
    assert notes == [f"outline.bottom has its knot at node E, d 70.15 y {y:.2f} (outline bottom)", bend]
    assert facts == {"outline_bends": [bend], "deleted_creases": []}
    # on the line within snap_mm: a knot, no bend
    on_line = G._interp(bot, 75.0) + 0.03
    _o, notes = G.apply_edits(slab, [node("F", 75.0, on_line, "mm", 5.0) | {"outline": "bottom"}], facts=facts)
    assert notes == [f"outline.bottom has its knot at node F, d 75.00 y {on_line:.2f} (outline bottom)"]
    assert facts["outline_bends"] == []
    # the cap knot keeps its d and takes y: its bend names the neighbour, no turn (a cap)
    cap = slab["outline"]["bottom"][0]
    _o, notes = G.apply_edits(slab, [node("H", 0.0, cap[1] + 0.5, "mm", 5.0) | {"outline": "bottom"}])
    assert notes[1] == (f"outline.bottom bends at node H: its knot at d 0.00 y {cap[1] + 0.5:.2f} lies 0.50 mm above "
                        f"the line the edge ran on (y {cap[1]:.2f} there); it is the edge's end knot, its neighbour "
                        "at d 2.00")
    # an existing knot moved: the turn it had there is said
    moved, notes = G.apply_edits(out, [node("E", 70.15, y + 0.3, "mm", 5.0)], facts=facts)   # null: stays on it
    assert notes[1].endswith(f"the edge now turns {turn_at(moved, 70.15, 'bottom'):.0f}° at the knot (it turned "
                             f"{turn:.0f}° there)") and facts["outline_bends"] == [notes[1]]
    # none: a free node, the outline as it was, no note for a typed corner
    free, notes = G.apply_edits(slab, [node("E", 70.15, line - 0.97, "mm", 5.0) | {"outline": "none"}])
    assert free["outline"] == slab["outline"] and notes == []


def test_bend_and_beyond_notes_tell_the_revision_after_the_last_edit(slab_tb):
    """v3-standin-fix-review: the bend and beyond notes were told at their set_node, so a later edit of the same list
    left them false of the revision. The stand-in's rev 7 (call 22) set E free 0.07 mm below the bottom edge (edits[6])
    and then spliced the edge through E (edits[7]): the note called E "a corner beyond the outline" in a revision where
    it lies on the bottom edge. Each is now told of the design after the last edit, in its set_node's place: none when
    a later edit put the corner on the outline or moved the bend's knot away, the final chain's neighbours when a later
    knot went in beside it."""
    bs = SynthBasis()
    slab = canonical(FX.slab())
    bot = slab["outline"]["bottom"]                                       # knots at d 70 (-2.12) and 80 (-0.24)
    yb = G._interp(bot, 75.0)
    rec = RD.read_point(slab_tb, bs, "E", corner_picks(bs, point(slab_tb, 75.0, yb - 0.8, 5.0), ["05", "02"]),
                        kind="corner")
    free, notes = G.apply_edits(slab, [node("E", None, None, "reading")], readings={"E": rec})
    d, y = free["nodes"]["E"][:2]
    assert len(notes) == 1 and notes[0].startswith(f"node E at d {d:.2f} y {y:.2f} lies ")       # beyond, alone
    splice = {"op": "splice_chain", "chain": "outline.bottom", "d_from_mm": d - 1.0, "d_to_mm": d + 1.0,
              "points": [[d, y]]}                                          # the edge through E, as the stand-in's
    facts = {}
    spliced, notes = G.apply_edits(slab, [node("E", None, None, "reading"), splice], readings={"E": rec},
                                   facts=facts)
    assert G.outline_edge(spliced, d, y) == "bottom" and G.beyond_text(spliced, "E", d, y) is None
    assert notes == [] and facts == {"outline_bends": [], "deleted_creases": []}
    # a bend whose knot a later splice replaced (E rides the edge to the new chain): no bend
    line = G._interp(bot, 70.15)
    notch = node("E", 70.15, line - 0.97, "mm", 5.0) | {"outline": "bottom"}
    flat = {"op": "splice_chain", "chain": "outline.bottom", "d_from_mm": 69.0, "d_to_mm": 81.0,
            "points": [[75.0, G._interp(bot, 75.0)]]}
    out, notes = G.apply_edits(slab, [notch, flat], facts=facts)
    assert out["nodes"]["E"][1] != pytest.approx(line - 0.97) and facts["outline_bends"] == []
    assert not any("bends at node" in n for n in notes) and notes[0].startswith("outline.bottom has its knot at node E")
    # a knot a later set_node put beside E's (on the notched edge's line: no bend of its own): the bend names the final
    # neighbours and the final turn
    notched = G.apply_edits(slab, [notch])[0]["outline"]["bottom"]
    beside = node("G", 72.0, G._interp(notched, 72.0), "mm", 5.0) | {"outline": "bottom"}
    out, notes = G.apply_edits(slab, [notch, beside], facts=facts)
    assert facts["outline_bends"] == [f"outline.bottom bends at node E: its knot at d 70.15 y {line - 0.97:.2f} lies "
                                      f"0.97 mm below the line the edge ran on (y {line:.2f} there); between its knots "
                                      f"at d 70.00 and 72.00 the edge now turns {turn_at(out, 70.15, 'bottom'):.0f}° "
                                      "at the knot (it ran straight there)"]
    assert notes[1] == facts["outline_bends"][0]                          # in its set_node's place


def test_a_read_corner_beyond_the_outline_is_a_free_corner_and_says_so(slab_tb):
    """Stand-in: A read at d -0.27 and E 0.97 mm below the model's bottom edge ("outside the model's outline"), with
    nothing saying what such a corner does; outline top refused A with no word on the free alternative. A read corner
    set free (outline none / null) beyond the outline is kept where it was read, with a note; top / bottom outside
    the outline's d range is refused naming the free node; a typed virtual corner (the Prada fixture's A_hi) needs no
    note."""
    bs = SynthBasis()
    slab = canonical(FX.slab())
    yb = G._interp(slab["outline"]["bottom"], 75.0)
    rec = RD.read_point(slab_tb, bs, "E", corner_picks(bs, point(slab_tb, 75.0, yb - 0.8, 5.0), ["05", "02"]),
                        kind="corner")
    design, notes = G.apply_edits(slab, [node("E", None, None, "reading")], readings={"E": rec})
    d, y = design["nodes"]["E"][:2]
    assert design["outline"] == slab["outline"] and y < yb - 0.5
    assert notes == [f"node E at d {d:.2f} y {y:.2f} lies {G._interp(slab['outline']['bottom'], d) - y:.2f} mm below "
                     "the bottom outline: a corner beyond the outline (its facets are clipped to the outline)"]
    assert G.beyond_text(slab, "A", -0.27, 10.4) == ("node A at d -0.27 y 10.40 lies 0.27 mm in front of the outline's "
                                                     "d range 0.00-136.50: a corner beyond the outline (its facets are "
                                                     "clipped to the outline)")
    assert G.beyond_text(slab, "A", 0.0, 10.4) is None                    # inside or on: no note
    err = refused(slab, [node("A", -0.27, 10.4, "mm", 5.0) | {"outline": "top"}])
    assert "lies outside the outline's d range 0.00-136.50, so it cannot lie on the top edge; a free node (outline " \
           "none) may lie beyond the outline, which clips its facets" in err.message
    _d, notes = G.apply_edits(slab, [node("A", -0.27, 10.4, "mm", 5.0) | {"outline": "none"}])
    assert notes == []                                                    # typed: the layout's own virtual corner


def test_deleting_a_facet_states_its_deleted_crease_declarations():
    """Stand-in call 26: delete_facet U dropped L|U and RU|U, said only as "crease L|U deleted with facet U" among the
    notes. One note names every declaration deleted (with the facet and the edit index), which the same list
    declared again and which the design no longer declares; facts["deleted_creases"] hands it to the receipt."""
    base = FX.prada_default()                                              # F1|F2, F2|F4 ridges, F4|base valley
    redo = canonical(base)["facets"]["F4"]
    facts = {}
    edits = [{"op": "delete_facet", "label": "F4"},
             {"op": "set_facet", "label": "F4", "poly": redo["poly"], "plane": {"mode": "flat"}, "soft": False},
             {"op": "set_crease", "facets": ["F4", "F2"], "shape": "ridge"}]
    spec, notes = G.apply_edits(base, edits, facts=facts)
    text = ("deleted crease declarations: F2|F4 ridge, F4|base valley (with facet F4, edits[0]); declared again in "
            "this edit list: F2|F4 ridge; not declared now: F4|base")
    assert notes == [text] and facts["deleted_creases"] == [text]
    assert {tuple(c["facets"]) for c in spec["creases"]} == {("F1", "F2"), ("F1", "base"), ("F2", "F3"),
                                                              ("F2", "F4")}
    _s, notes = G.apply_edits(base, edits + [{"op": "set_crease", "facets": ["F4", "base"], "shape": "ridge"}])
    assert notes == ["deleted crease declarations: F2|F4 ridge, F4|base valley (with facet F4, edits[0]); declared "
                     "again in this edit list: F2|F4 ridge, F4|base ridge"]          # declared again as a ridge
    facts = {}
    _s, notes = G.apply_edits(base, [{"op": "set_scalar", "key": "roll_mm", "value": 0.0}], facts=facts)
    assert notes == [] and facts == {"outline_bends": [], "deleted_creases": []}


def _strip_design(virtual: bool) -> dict:
    """A facet RL on the slab with C and E on the bottom outline (d 20 and 60) and B inside; ``virtual``: RL's bottom
    side runs through two corners beyond the outline instead (the prompt's rule for a face that reaches it)."""
    bot = canonical(FX.slab())["outline"]["bottom"]
    nodes = [node("B", 40.0, 5.0, "mm", 6.0) | {"outline": None},
             node("C", 20.0, G._interp(bot, 20.0), "mm", 6.0) | {"outline": "bottom"},
             node("E", 60.0, G._interp(bot, 60.0), "mm", 6.0) | {"outline": "bottom"}]
    poly = ["B", "E", "C"]
    if virtual:
        nodes += [node("V1", 30.0, -20.0, "solve"), node("V2", 50.0, -20.0, "solve")]
        poly = ["B", "E", "V2", "V1", "C"]
    return G.apply_edits(FX.slab(), nodes + [facet("RL", poly)])[0]


def test_a_face_that_stops_at_a_chord_names_the_plain_arm_it_leaves_and_virtual_corners_close_it():
    """Stand-in rev 1: RL's corners C and E sat on the bottom outline, which ran up to 4.9 mm below the chord C-E; the
    receipt said "RL|base valley 8.7° (sunk 1.62 mm: a 39.1° ramp at d 21.9-56.4)" and nothing named the strip of
    plain arm the B1 render showed. geometry.outline_strips names it (side, edge, d range, width: the largest gap
    between the chord and the outline, here at a knot of the outline, computed from the chains); corners beyond the
    outline build with no refusal and leave none; a side with a corner inside the arm is never a strip."""
    bot = canonical(FX.slab())["outline"]["bottom"]
    chord = lambda d: G._interp(bot, 20.0) + (G._interp(bot, 60.0) - G._interp(bot, 20.0)) * (d - 20.0) / 40.0
    gaps = {d: chord(d) - y for d, y in bot if 20.0 < d < 60.0}            # piecewise linear: largest at a knot
    widest = max(gaps, key=gaps.get)
    assert gaps[widest] > 0.9
    tb = build_temple(FIT.fit_spec(_strip_design(False)).spec)
    strips = G.outline_strips(tb)
    assert list(strips) == ["RL"] and len(strips["RL"]) == 1
    s = strips["RL"][0]
    assert s["edge"] == "bottom" and s["side"] == ["E", "C"] and s["width_mm"] == pytest.approx(gaps[widest], abs=1e-3)
    assert 20.0 < s["d"][0] < widest < s["d"][1] < 60.0
    assert G.strip_text("RL", strips["RL"]) == (f"RL leaves plain arm up to {gaps[widest]:.2f} mm wide between its "
                                                f"straight side E-C and the curved bottom outline at d "
                                                f"{s['d'][0]:.1f}-{s['d'][1]:.1f}")
    assert G.labels_at(tb, [[widest, (chord(widest) + G._interp(bot, widest)) / 2], [40.0, 0.0], [40.0, 50.0]]) == \
        ["base", "RL", None]
    # the corners beyond the outline: built (no refusal), RL meets the curved outline, no strip
    vt = build_temple(FIT.fit_spec(_strip_design(True)).spec)
    assert G.outline_strips(vt) == {}
    assert G.labels_at(vt, [[widest, G._interp(bot, widest) + 0.2]]) == ["RL"]
    assert G.strip_text("RL", []) == ""


def test_arguments_the_parameters_refuse_name_each_problem_by_path(timeouts):
    """Stand-in call 11: a probe edit came back as the SDK's "Invalid JSON input for tool facet_edit" with no field or
    path (the SDK drops the validation text unless tool data is logged). The tools answer E_ARGS with each problem by
    path, nothing done (the lab is not called): one line for an edit whose op names no edit (not one per union
    member), a known op's own errors only, the same inside a variant's edits, a photo id outside the enum, and text
    that is not JSON."""
    lab = FakeLab()
    tools = tools_of(lab)
    ops = "set_node, delete_node, set_facet, delete_facet, set_crease, delete_crease, splice_chain, offset_chain, " \
          "set_scalar"
    args = {"parent_revision": 0, "reset_layout": False, "judgement": None, "note": "x", "object": None,
            "apply": False, "photos": None,
            "edits": [{"op": "help"},
                      {"op": "set_node", "name": "K", "d_mm": 1.0, "y_mm": 2.0, "t": {"mode": "typed", "mm": 5.0},
                       "outline": None},
                      {"op": "delete_node"}]}
    out = call(tools["facet_edit"], args)
    assert isinstance(out, str) and lab.calls == []
    head, footer = out.rsplit("\n", 1)
    b = json.loads(head)
    assert (b["status"], b["code"], b["tool"]) == ("refused", "E_ARGS", "facet_edit") and footer == L.footer_text()
    assert b["message"] == "the arguments do not match facet_edit's parameters; nothing was done"
    assert b["errors"] == [f"edits[0]: op 'help' is not one of {ops}",
                           "edits[1].t.mode: Input should be 'reading', 'mm', 'base' or 'solve'",
                           "edits[2].name: Field required"]
    var = {"parent_revision": 0, "photos": ["05"], "judgement": None,
           "variants": [{"label": "a", "edits": [{"op": "set_scalar", "key": "roll_mm", "value": 0.0},
                                                 {"op": "nope"}]}, {"label": "b", "edits": []}]}
    b = json.loads(call(tools["facet_variants"], var).rsplit("\n", 1)[0])
    assert b["code"] == "E_ARGS" and b["errors"] == [f"variants[0].edits[1]: op 'nope' is not one of {ops}"]
    b = json.loads(call(tools["photo_image"], {"photo": "07", "crop": None, "enhance": False, "scale": 1})
                   .rsplit("\n", 1)[0])
    assert b["errors"] == ["photo: Input should be '01', '02', '05', 'B1' or 'R1'"] and lab.calls == []
    ctx = ToolContext(context=None, tool_name="facet_edit", tool_call_id="c", tool_arguments="{not json")
    b = json.loads(asyncio.run(tools["facet_edit"].on_invoke_tool(ctx, "{not json")).rsplit("\n", 1)[0])
    assert b["code"] == "E_ARGS" and b["errors"][0].startswith("the arguments are not JSON: Expecting property name")
    assert T.schema_errors(T.EditList, json.dumps({"edits": []})) is None          # valid: not this refusal
    many = {"edits": [{"op": f"x{i}"} for i in range(T.ARGS_ERRORS_MAX + 3)]}
    lines = T.schema_errors(T.EditList, json.dumps(many))
    assert len(lines) == T.ARGS_ERRORS_MAX + 1 and lines[-1] == "+3 more"
    # v3-standin-fix-review: an edit with no op, one that is not an object, a non-text op ("op None is not one of")
    lines = T.schema_errors(T.EditList, json.dumps({"edits": [{"name": "K"}, "set_node", {"op": 5}]}))
    assert lines == [f"edits[0]: no op is given; op is one of {ops}",
                     f"edits[1]: an edit is an object with an op, one of {ops}", f"edits[2]: op 5 is not one of {ops}"]


# --------------------------------------------------------------------------------------------- I4: finish
PHOTOS = ["01", "02", "05"]
CAMERA = PHOTOS + ["B1"]


def finish_state() -> dict:
    """Four applied heads (revisions 1-4, seq 10 r), each compared at every photo with a camera (seq 10 r + 1), a
    variants call with a spread after the latest head (seq 45), three judgements on heads 2-4, none better."""
    st = S.new_state("rig")
    st["heads"] = [{"checkpoint": f"c{r}", "fingerprint": f"fp{r}", "revision": r, "call_id": f"e{r}", "seq": 10 * r}
                   for r in (1, 2, 3, 4)]
    st["compares"] = [{"compare_id": f"cmp{10 * r + 1}", "checkpoint": f"c{r}", "fingerprint": f"fp{r}",
                       "revision": r, "photos": list(CAMERA), "by": "astra", "call_id": f"e{r}", "judged": True,
                       "seq": 10 * r + 1} for r in (1, 2, 3, 4)]
    st["variants"] = [{"id": "var045", "call_id": "v1", "parent": 4, "photos": ["05"], "seq": 45,
                       "items": [], "pairwise": {}, "rendered": ["a", "b", "c"], "spread": ["a", "b", "c"]}]
    st["judgements"] = [{"call_id": f"j{r}", "tool": "facet_edit", "compare_id": f"cmp{10 * r + 1}",
                         "versus_compare_id": None, "overall": "same", "photos": [], "best_checkpoint": "c4",
                         "revision": 4, "compare_revision": r} for r in (2, 3, 4)]
    st["seq"] = 45
    return st


def missing(st, status="stuck", k=4, best=4, photos=CAMERA, has_object=True, distinct=lambda a, b: a != b) -> dict:
    out = S.finish_missing(st, status=status, best_revision=best, k=k, distinct=distinct, camera_photos=photos,
                           has_object=has_object)
    for m in out:
        assert not any(w in m["message"] for w in REMEDY_WORDS), m          # facts only, no remedy
    return {m["precondition"]: m for m in out}


def test_finish_passes_when_every_precondition_holds():
    assert missing(finish_state()) == {} and missing(finish_state(), status="matched") == {}


def test_finish_precondition_1_distinct_heads():
    assert set(missing(finish_state(), k=5)) == {1}
    m = missing(finish_state(), k=4, distinct=lambda a, b: abs(a - b) > 1)  # 1, 3 distinct; 2, 4 not
    assert set(m) == {1, 5} and "2 of 4 distinct applied heads compared in full (revisions [1, 3])" in \
        m[1]["message"]


def test_finish_counts_full_compares_only():
    """Preconditions 1 and 3 count a photo only where a compare showed it in full (``stacked``); a zoom-only compare
    (photos = the zoomed photos, stacked []) does not count (review 2026-10-12: revision 4 shown at B1 only in a
    40 x 40 px zoom met precondition 3 for B1). A record without ``stacked`` counts its photos."""
    st = finish_state()
    for c in st["compares"]:
        c["stacked"] = list(c["photos"])
    st["compares"][-1]["photos"] = st["compares"][-1]["stacked"] = list(PHOTOS)    # revision 4 in full at 01 02 05
    zoom = {"compare_id": "cmp042", "checkpoint": "c4", "fingerprint": "fp4", "revision": 4, "photos": ["B1"],
            "stacked": [], "zoom": [{"photo": "B1", "box": [900, 400, 940, 440], "geometry": False}], "by": "astra",
            "call_id": "z1", "judged": True, "seq": 42}
    st["compares"].append(zoom)
    m = missing(st, status="matched")
    assert set(m) == {3} and m[3]["photos"] == ["B1"]
    assert m[3]["message"] == "no full compare of revision 4 (best_checkpoint's) was shown at photos ['B1'] (a zoom " \
                              "alone does not count)"
    zoom["stacked"] = ["B1"]                                                # the same compare with B1 in full
    assert missing(st, status="matched") == {}
    zoom.pop("stacked")                                                     # a record without stacked: its photos
    assert missing(st, status="matched") == {}
    # precondition 1: a head shown only in a zoom is not compared
    st = finish_state()
    for c in st["compares"]:
        c["stacked"] = list(c["photos"])
    st["compares"][0]["stacked"] = []
    m = missing(st, status="matched")
    assert set(m) == {1} and "3 of 4 distinct applied heads compared in full (revisions [2, 3, 4])" in \
        m[1]["message"]
    assert S.shown_in_full({"photos": ["01", "B1"], "stacked": ["01"]}) == ["01"]
    assert S.shown_in_full({"photos": ["01", "B1"]}) == ["01", "B1"]


def test_finish_precondition_2_a_seen_spread():
    st = finish_state()
    st["variants"][0]["spread"] = None
    assert set(missing(st, status="matched")) == {2}
    st = finish_state()
    st["variants"][0]["unseen"] = True
    assert set(missing(st, status="matched")) == {2}


def test_finish_precondition_3_every_photo_with_a_camera():
    st = finish_state()
    st["compares"][-1]["photos"] = list(PHOTOS)                             # revision 4 never shown at B1
    m = missing(st, status="matched")
    assert set(m) == {3} and m[3]["photos"] == ["B1"]
    assert m[3]["message"] == "no full compare of revision 4 (best_checkpoint's) was shown at photos ['B1'] (a zoom " \
                              "alone does not count)"
    assert missing(st, status="matched", photos=PHOTOS) == {}
    st["compares"][-1]["unseen"] = True
    assert set(missing(st, status="matched", photos=PHOTOS)) == {1, 3}


def test_finish_precondition_4_an_object_note():
    m = missing(finish_state(), status="matched", has_object=False)
    assert set(m) == {4} and m[4]["message"] == "there is no object note"


@pytest.mark.parametrize("change", [
    lambda st: st["variants"][0].update(seq=35),                            # before the latest head (seq 40)
    lambda st: st["variants"][0].update(unseen=True),
    lambda st: st["variants"][0].update(rendered=[]),                       # nothing Astra could judge
    lambda st: st["heads"].append({"checkpoint": "c5", "fingerprint": "fp5", "revision": 2, "call_id": "e5",
                                   "seq": 50}),                             # a later head (back to revision 2)
])
def test_finish_precondition_5_variants_since_the_latest_head(change):
    st = finish_state()
    change(st)
    m = missing(st)
    assert set(m) == ({2, 5} if st["variants"][0].get("unseen") else {5})
    assert "no facet_variants result with a rendered variant was shown since the latest applied head" in \
        m[5]["message"]
    assert set(missing(st, status="matched")) <= {2}


@pytest.mark.parametrize("change, words", [
    (lambda st: st["judgements"][1].update(overall="better"), "['cmp31'] said better"),
    (lambda st: st["judgements"][1].update(compare_revision=2), "cover 2 distinct heads"),
    (lambda st: st["judgements"].pop(0), "the last 2 judgements (one per compare) cover 2 distinct heads"),
    # a state written before check_judgement stored one per compare: head 4's judged three times (package_003's
    # state.json holds cmp004 twice) is one judgement, so the last 3 per compare are heads 2, 3, 4 ...
    (lambda st: st["judgements"].insert(0, {**st["judgements"][0], "compare_id": "cmp11", "compare_revision": 1,
                                            "overall": "better"}) or
     st["judgements"].extend([dict(st["judgements"][-1]), dict(st["judgements"][-1])]), None),
    # ... and head 4 judged three times with nothing before it is one head
    (lambda st: st["judgements"].__setitem__(slice(None), [dict(st["judgements"][-1]) for _ in range(3)]),
     "the last 1 judgements (one per compare) cover 1 distinct heads"),
])
def test_finish_precondition_5_the_last_judgements(change, words):
    st = finish_state()
    change(st)
    m = missing(st)
    if words is None:
        assert m == {}
    else:
        assert set(m) == {5} and words in m[5]["message"], m[5]["message"]
    assert missing(st, status="matched") == {}


def test_finish_orders_a_head_without_seq_by_its_own_compare():
    """A head recorded without seq lies just before the compare its call made (facet_edit applies, then compares)."""
    st = finish_state()
    for h in st["heads"]:
        h.pop("seq")
    assert S.head_seq(st, st["heads"][-1]) == 40.5 and missing(st) == {}
    st["variants"][0]["seq"] = 40                                           # before head 4's compare (41)
    assert set(missing(st)) == {5}
    st["compares"] = [c for c in st["compares"] if c["call_id"] != "e4"]
    assert S.head_seq(st, st["heads"][-1]) == 0.0


def test_new_state_and_load(tmp_path):
    st = S.new_state("rig")
    assert st["schema"] == "g3d.temple_lab_state/3" and st["readings"] == {} and st["objects"] == []
    p = tmp_path / "state.json"
    S.save(p, st)
    assert S.load(p, "rig") == st
    p.write_text(json.dumps({**st, "schema": "g3d.temple_lab_state/2"}), encoding="utf-8")
    with pytest.raises(ValueError, match="is not g3d.temple_lab_state/3"):
        S.load(p, "rig")


def test_constants_shared_with_the_lab_and_seeing():
    assert S.READ_POINTS_MAX == 8 and S.ZOOM_MAX == SEE.ZOOM_MAX and S.DIFFERENCES_MAX == 3
    assert math.isclose(G.READ_POSITION_MM, FIT.OFF_POSITION_MM)
    assert np.isfinite(G.HEAD_DISTINCT_MM)
