"""T5 for WP6 v3 (plan/WP6_V3.md §7.2; plan/WP6_FACETS.md §5 T5): the $0 scripted rehearsal of a v3 facet run.
SCRIPTED OFFLINE TOOL TEST: every pick, layout, edit and judgement below is an arbitrary control, never Astra or
product output.

A fresh package (temple_lab.cli.prepare: a byte copy of the original Prada scene, the six target photos, the three
black photos B1-B3 with their transported cameras, the pose basis, revision 0 fitted and applied) runs through
agent/loop.run_astra with the pinned Agents SDK, the real Blender worker and the seven v3 tools (tools.TOOL_NAMES),
while api.openai.com is a MockTransport (agent/replay.ScriptedOpenAI) whose replies are scripted calls built from the
live lab state; LoopbackOnly and the production-transport tripwire are active throughout. The run settings are exactly
a paid run's (tools.run_settings: required tool choice kept, final only on an accepted facet_finish with K 4, the
context guard at 240k counted input); only the approval is a synthetic OFFLINE ONLY record.

The controls (tests/proofs/wp6v3_rehearsal_checks.py): six chart points CTRL (d, y, t above revision 0's base surface)
picked in the photos by projecting them through the lab's own cameras, so every reading has an exact answer: A, H, K
as corners in B1 B2 05 02 (two directions: B1 is 05's camera moved, B2 02's), the tip E as a crossing of the traced
images of the chart segments E-H and E-K (shallow in the side views beside their slide line), C and X1 as corners in
two photos. The layout: crown A H E K (warp: K lies off the plane of A H E), logo A K C (soft border), all from the
readings; crown|logo builds a certain ridge, so declaring it a valley is refused E_FIT_SHAPE.

The script (23 requests, one tool call each) and what it checks:
    inspect          facet_inspect of revision 0: one chart image, the v3 node columns (fitted_at)
    photo_image      a 05 crop at scale 2, enhanced: k 2, the stretch
    photo_black      B2 whole: a black photo, its colour
    read             photo_read A H K E C X1: each reading within READ_TOL_MM of its control with a zero residual, E a
                     crossing with W_SHALLOW_CROSSING and W_ALONG_LINE, one pick sheet a reading (kind reading) of the
                     size its photos make
    edit_no_object   the layout without an object note: E_OBJECT, nothing registered
    edit_h1          H1 = the layout from the readings (warp crown, soft logo) with the object note: applied, the fit's
                     warp line, a soft crease line, compared at the default set
    compare_zoom     B1 and B2 in full, a zoom at B1 with the geometry view and one at 05 without, points_px at B2 (one
                     on the temple at a control (d, y), one off it): black rows (photo / gloss-black render /
                     overlay), the lab's photo ids in the images' titles (photo_names), the zoom texts and grids
    edit_unjudged    E_JUDGEMENT naming that compare
    edit_fit_refused crown|logo declared valley, judging the zoom compare (its zoomed photo 05 included): E_FIT_SHAPE
                     (stage fit, the readings against it), "judgement of cmpN stored"
    reread           A read again from B1 and B2 with B2's pick 2 px off, X1 dropped: replaced, dropped, and the head's
                     node A holding the earlier reading (head_nodes)
    inspect_stale    the head's node A row "read (replaced since)", the readings table
    edit_h2          H2 = H1 with A set from the new reading and the bottom outline offset
    variants         of H2 at 05 (native panels) and B1 (gloss_black panels): flat crown, K typed 1 mm out, the tail
                     outline, and the refused valley arm; the scene unchanged, each model's edges drawn, the spread;
                     its count is scripted above the guard threshold
    edit_h3          the flat arm applied with no edits (the guard's request: its decision is checked, below)
    reapply_h1       H1 applied again with no edits, after the guard's epoch (v3 has no restore): its default compare
                     hits seeing's compare cache, so its panels are the very bytes the guard stubbed in edit_h1's
                     output, and they must reach the next request as images (review 2026-10-12, P1, v3p3-verify-14:
                     context guard /1 stubbed them by bytes; /2 scopes each epoch's choice to the outputs present then)
    finish_early     matched, judging reapply_h1's compare: E_FINISH naming exactly preconditions 1 and 3, the run goes
                     on
    inspect_slow     facet_inspect made to outlast a shortened SDK timeout: E_TIMEOUT with the footer ...
    busy             ... so the next call answers E_BUSY at once
    edit_slow        H4 under the same short timeout: E_TIMEOUT; its thread still applies and compares (unseen)
    back_to_h2       H2 again with no edits, compared at every photo with a camera (needs no judgement: the unseen
                     compare demands none): side views' outline offsets, black rows, no top-view outline line
    edit_h5          H5 = H2 with C typed 2 mm out of its reading (judging back_to_h2's compare): logo|base flips
                     valley -> ridge (crease_vs_head states it), the fit weighs the typed C against its reading
                     (W_FIT_OFF_READING)
    variants_h5      two arms of H5 at 05 (precondition 5's variants since the latest head)
    finish           stuck with H2: accepted, exported, validated; no request after it
and across the run: every tool called, tool_choice required on every request, each output's images in the next
request, every result's footer, no retired v2 phrase in any output, every photo in the first request (6 target + 3
black), instructions-sent.txt byte for byte the bound text, crease_vs_head exactly where the builds flip a crease and
diff_vs_head exactly where the parent is not the head, no network, the worker's own refusals.

The context guard's decision (wp6v3_rehearsal_checks module doc): compare panels, zoom grids and variant sheets of a
superseded result (older than the latest 2, not a compare of best_checkpoint's revision) may be stubbed, and a pick
sheet only once its reading was read again or dropped; photo views, charts and the stored readings' pick sheets stay.
At edit_h3's request the guard's real choice must equal expected_stubs on the state it chose from, with every class
present (an old compare panel, an old zoom grid, the re-read and the dropped reading's sheets stubbed; the stored
readings' sheets, the photo views, the charts, the latest results and best's compare kept), and every later request
must carry the same stubs and no stub in any output that arrived after it.

Writes <out>/rehearsal.json (and <out>-run.json, the process receipt); the cost estimate (cli estimate) prices its
tool_outputs ([W, H, cli.record_kind], each facet_variants call with its "arms") and its first request's serialized
bytes (first_request "bytes", from the run's events.jsonl).
Run: glasses3d/.venv/Scripts/python.exe -m glasses3d.tests.proofs.astra_temple_lab rehearse --out DIR [--rig RIG]
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
import time
import traceback

import numpy as np
from PIL import Image

from glasses3d.agent import approvals as A
from glasses3d.agent import replay as R
from glasses3d.agent.loop import REMOVED_PHRASES, run_astra
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.agent.temple_lab import geometry as G
from glasses3d.agent.temple_lab import lab as LB
from glasses3d.agent.temple_lab import readings as RD
from glasses3d.agent.temple_lab import state as S
from glasses3d.tests.fixtures.temple_lab_prada import prepare_kwargs
from glasses3d.agent.temple_lab.cli import DEFAULT_RIG, RETIRED_PHRASES, make_spec, prepare, record_kind
from glasses3d.agent.temple_lab.lab import read, sha, write_json_atomic
from glasses3d.agent.temple_lab.tools import CONTEXT_GUARD_TOKENS, TOOL_NAMES, run_settings
from glasses3d.g3d_host.compare import RIDGE_RGB, VALLEY_RGB
from glasses3d.tests.proofs import wp6v3_rehearsal_checks as CH

RUN_ID = "offline-rehearsal"
CAP = "100"
LABEL = "SCRIPTED OFFLINE TOOL TEST, NOT ASTRA OUTPUT"
SLOW_INSPECT_S = 8.0           # the simulated stuck call: facet_inspect sleeps this long ...
SHORT_TIMEOUT_S = 3.0          # ... under this SDK timeout, so the next call finds the scene lock held
SLOW_EDIT_S = 5.0              # the timed-out apply: facet_edit starts this late under the same short timeout
GUARD_COUNT = CONTEXT_GUARD_TOKENS + 1_000      # one scripted count above the guard: it fires on the next request
FOOTER = re.compile(r"\nsettled \$\d+\.\d\d / cap \$\d+\.\d\d · context \S+ tokens$")
CONTROL = "SCRIPTED CONTROL, not a visual judgement"
# the control points: (d, y, t mm above revision 0's base surface at (d, y)); K stands 1.1-1.5 mm off the plane of
# A, H, E (a warp crown, 0.44 mm off one plane in the fit) and C low, so crown|logo builds a certain ~10 deg ridge
# (host pre-check on revision 0: readings-only +9.3 +- 2.8 deg)
CTRL = {"A": (5.0, 6.0, 0.6), "H": (30.0, 10.5, 0.9), "E": (68.0, 7.5, 0.5), "K": (44.0, 4.0, 2.0),
        "C": (20.0, -8.0, 0.2), "X1": (55.0, 2.0, 0.3)}
FOUR, TWO = ["B1", "B2", "05", "02"], ["B1", "B2"]
READ_PHOTOS = {"A": FOUR, "H": FOUR, "K": FOUR, "E": FOUR, "C": TWO, "X1": ["05", "02"]}
CROSSING = {"E": ("H", "K")}          # E read as the crossing of the traced segments toward H and K
TRACE_S = (0.04, 0.12, 0.2, 0.28, 0.36)   # the traced points, as fractions of the way toward each of those corners
REREAD = ("A", TWO, {"B2": (2.0, 0.0)})   # A read again from B1 and B2, B2's pick 2 px right (another feature)
DROP = "X1"
READ_TOL_MM = 0.005            # a control reading's d, y, X against its chart point (the picks are rounded to 0.001 px)
READ_RES_PX = 0.05             # ... and its residuals
CROP_05, SCALE_05 = [380, 250, 740, 430], 2       # photo_image: a 360 x 180 crop at k 2 (720 x 360 <= IMAGE_MAX)
COMPARE_PHOTOS = ["B1", "B2"]                      # compare_zoom: two black photos in full ...
ZOOMS = (("B1", (56.0, 5.75), (380, 220), True), ("05", (25.0, 5.0), (300, 200), False))   # ... zooms (photo, d y,
#                                                    box w h, geometry), centred on the head's surface at (d, y)
POINT_B2 = (35.0, -4.0)        # points_px at B2: the head's surface point at this (d, y) (the plain arm) ...
OFF_B2 = [5.0, 5.0]            # ... and a pixel off the temple
POINT_TOL_MM = 0.3             # a charted point against the control's (d, y); its t against the head's t there
VARIANT_PHOTOS = ["05", "B1"]  # a target photo (native panels) and a black one (gloss_black panels)
VARIANT_PASSES = {"05": ["native_linear"], "B1": ["gloss_black"]}
H5_VARIANT_PHOTOS = ["05"]
BLACK_IDS, BLACK_REFS = ["B1", "B2", "B3"], ["black-side", "black-angled", "black-front"]
BLACK_ROWS = {"photo", "overlay", "gloss_black"}   # a colourway photo's compare rows (no native row)
SIDE_TEXT = {"01": "outline of the mirrored temple", "05": "outline of the charted temple",
             "B1": "outline of the charted temple"}  # the side views' outline offsets (seeing._outline_text)
OBJECT_NOTE = (
    "SCRIPTED CONTROL (offline tool test). Two faces on the outer side of the temple, from near the hinge to d 68, the "
    "plain arm around and behind them. crown (A H E K): the top face, one face to its tip E with no line inside it; "
    "its corners need not lie on one plane, so it is a warp. logo (A K C): the face below it, flat, its borders with "
    "the plain arm soft. Edges: crown|logo A-K a ridge; crown|base A-H-E-K and logo|base A-C-K. Vertices read from "
    "two directions: A, H, K in B1 B2 05 02, the tip E as the crossing of its two edges (shallow in the side views), "
    "C in B1 B2. Uncertain: E along its edges.")


# --------------------------------------------------------------------------------------------- scripted edits
def node_read(name: str) -> dict:
    return {"op": "set_node", "name": name, "d_mm": None, "y_mm": None, "t": {"mode": "reading", "mm": None}}


def node_mm(name: str, d: float, y: float, t: float) -> dict:
    return {"op": "set_node", "name": name, "d_mm": d, "y_mm": y, "t": {"mode": "mm", "mm": t}}


def facet(label: str, poly: list, mode: str = "flat", soft: bool = False) -> dict:
    return {"op": "set_facet", "label": label, "poly": poly, "plane": {"mode": mode}, "soft": soft}


def offset(chain: str, a: float, b: float, delta: float, taper: float) -> dict:
    return {"op": "offset_chain", "chain": chain, "d_from_mm": a, "d_to_mm": b, "delta_mm": delta, "taper_mm": taper}


CROWN, LOGO = ["A", "H", "E", "K"], ["A", "K", "C"]
H1_EDITS = [node_read(n) for n in ("A", "H", "K", "E", "C")] + [facet("crown", CROWN, "warp"),
                                                                facet("logo", LOGO, soft=True)]
VALLEY = {"op": "set_crease", "facets": ["crown", "logo"], "shape": "valley"}
H2_EDITS = [node_read("A"), offset("outline.bottom", 100.0, 115.0, -0.8, 5.0)]
H4_EDITS = [offset("outline.bottom", 118.0, 128.0, 0.7, 4.0)]
C_UP_MM = 2.0                  # H5 types C this far out of its reading: logo|base flips valley -> ridge (host pre-check
#                                on H2: readings-only +4.3 +- 2.0 deg, certain; 1.5 mm already flips it, 1.0 does not)


def arms(readings: dict, labels=("flat", "kdeep", "tail", "valley")) -> list:
    """The variant arms (each edits the parent): flat crown; K typed 1 mm further out than its reading (the fit weighs
    it against the reading: W_FIT_OFF_READING); the tail's top outline 0.6 mm out; crown|logo declared a valley
    (refused E_FIT_SHAPE). Host pre-check on H2: pairwise 0.84-1.08 mm between the first three."""
    k = readings["K"]
    all_ = {"flat": [facet("crown", CROWN, "flat")],
            "kdeep": [node_mm("K", round(float(k["d"]), 3), round(float(k["y"]), 3), round(float(k["t"]) + 1.0, 3))],
            "tail": [offset("outline.top", 90.0, 110.0, 0.6, 5.0)],
            "valley": [VALLEY]}
    return [{"label": lb, "edits": all_[lb]} for lb in labels]


def crease_px(path) -> int:
    """Pixels of exactly the ridge or valley colour on an image (the crease lines drawn on a variant page)."""
    a = np.asarray(Image.open(path).convert("RGB"))
    hit = np.zeros(a.shape[:2], bool)
    for rgb in (RIDGE_RGB, VALLEY_RGB):
        hit |= np.all(a == np.asarray(rgb, np.uint8), axis=-1)
    return int(hit.sum())


POINTS_RE = re.compile(r"\| points \((\S+),(\S+)\) d (\S+) y (\S+) t (\S+); \((\S+),(\S+)\) no temple hit")


# --------------------------------------------------------------------------------------------- the rehearsal
def rehearse(root, rig: str = DEFAULT_RIG) -> dict:
    root = Path(root).resolve()
    disable_sdk_tracing()
    t0 = time.perf_counter()
    prepared = prepare(root, rig=rig, **prepare_kwargs())
    t_prepare = time.perf_counter() - t0
    settings = run_settings(effort="max", max_turns=30)
    estimate = {"label": "SYNTHETIC OFFLINE CONTROL ONLY", "text": "no estimate: offline rehearsal"}
    spec, lab = make_spec(root, RUN_ID, settings, CAP, write_request=True, estimate=estimate)
    with LoopbackOnly() as blocked_network, ProductionTransportTripwire() as blocked_transport:
        try:
            asyncio.run(run_astra(spec, scene=lab))
        except A.ApprovalRefused:
            missing_approval_refused = True
        else:
            missing_approval_refused = False
    # a synthetic approval ONLY for this MockTransport proof, never in a package meant for a paid run
    spec.offline_api_key = "sk-offline-temple-lab-proof"
    os.environ["G3D_OFFLINE"] = "1"
    A.write_approval(root / "runs" / RUN_ID, max_usd=CAP,
                     owner_words="SYNTHETIC OFFLINE CONTROL ONLY; no authorization for a paid call",
                     recorded_by="offline proof over MockTransport")
    r0 = lab.spec(0)
    ctrl = {n: CH.chart_point(r0, *v) for n, v in CTRL.items()}
    tools = {t.name: t for t in spec.tools}
    timeout0 = tools["facet_inspect"].timeout_seconds
    edit_timeout0 = tools["facet_edit"].timeout_seconds
    st: dict = {"fingerprints": [], "variant_views": [], "edit_heads": {}}
    slow = {"armed": False, "done": threading.Event()}
    slow_edit = {"armed": False, "done": threading.Event()}
    inspect0, variants0, edit0, views0, stubs0 = (lab.inspect, lab.variants, lab.edit, lab._variant_views,
                                                  lab.context_stubs)

    def inspect():
        if not slow["armed"]:
            return inspect0()
        slow["armed"] = False
        try:
            time.sleep(SLOW_INSPECT_S)
            return inspect0()
        finally:
            slow["done"].set()

    def variants(*args, **kwargs):
        before = lab.s.fingerprint()
        out = variants0(*args, **kwargs)
        st["fingerprints"].append([before, lab.s.fingerprint()])
        return out

    def variant_views(*args, **kwargs):
        """The worker views of a facet_variants render (each photo's pass) and each photo's panel box (observed)."""
        views, comp = views0(*args, **kwargs)
        st["variant_views"].append({
            "passes": {v["name"]: list(v.get("passes") or []) for v in views},
            "photos": {pid: {k: (list(c[k]) if isinstance(c[k], tuple) else c[k]) for k in ("box", "k", "what", "shows")}
                       for pid, c in comp["photos"].items()}})
        return views, comp

    def edit_fn(*args, **kwargs):
        head = lab.head_record()                         # the head this edit's crease_vs_head / diff_vs_head face
        st["edit_heads"][args[0]] = head["revision"] if head else None
        if not slow_edit["armed"]:
            return edit0(*args, **kwargs)
        slow_edit["armed"] = False
        try:
            time.sleep(SLOW_EDIT_S)
            return edit0(*args, **kwargs)
        finally:
            slow_edit["done"].set()

    def context_stubs(shas):
        """The guard's one choice, with a snapshot of the state it chose from (for expected_stubs)."""
        with lab.state_lock:
            s_ = lab.state
            best = S.head_by_fingerprint(s_, lab.fingerprint_of(s_.get("best_checkpoint")))
            snap = {"shas": list(shas), "images": json.loads(json.dumps(s_["images"])),
                    "results": [[c["compare_id"], c["seq"]] for c in S.seen_compares(s_)]
                               + [[v["id"], v["seq"]] for v in S.seen_variants(s_)],
                    "best_compares": [c["compare_id"] for c in s_["compares"]
                                      if best is not None and c["revision"] == best["revision"]],
                    "stored": {n: r.get("id") for n, r in (s_.get("readings") or {}).items()},
                    "best_checkpoint": s_.get("best_checkpoint"), "best_revision": best["revision"] if best else None}
        out = stubs0(shas)
        st["guard"] = {"snapshot": snap, "chosen": dict(out)}
        return out
    lab.inspect, lab.variants, lab.edit, lab._variant_views = inspect, variants, edit_fn, variant_views
    lab.context_stubs = context_stubs            # the loop binds scene.context_stubs when the run starts

    def settle(done: threading.Event, timeout_s: float) -> None:
        """A timed-out call's thread has finished and released the scene lock."""
        done.wait(timeout_s)
        t = time.perf_counter()
        while lab.lock.locked() and time.perf_counter() - t < 30:
            time.sleep(0.05)

    def head_rev() -> int:
        return lab.head_record()["revision"]

    def head_spec() -> dict:
        return lab.spec(head_rev())

    def judgement(best: str | None = None, overall: str = "mixed") -> dict:
        c = S.seen_compares(lab.state)[-1]
        return {"compare_id": c["compare_id"], "versus_compare_id": None, "overall": overall,
                "photos": [{"photo": p, "verdict": overall,
                            "differences": [{"what": CONTROL, "d_mm": 40.0, "y_mm": 0.0}]} for p in c["photos"]],
                "best_checkpoint": best or lab.s.head["id"]}

    def edit(parent, edits, *, judged=None, obj=None, photos=None) -> dict:
        return {"parent_revision": parent, "reset_layout": False, "edits": edits, "judgement": judged,
                "note": CONTROL, "object": obj, "apply": True, "photos": photos}

    def point(name, photos, shift=None) -> dict:
        if name in CROSSING:
            return CH.crossing_point(lab, r0, name, ctrl[name], [ctrl[n] for n in CROSSING[name]], photos, TRACE_S)
        return CH.corner_point(lab, r0, name, ctrl[name], photos, shift)

    def surface_point(spec_, d, y) -> np.ndarray:
        tb = lab.build(head_rev())
        return np.array([d, y, float(G.surface_at(tb, np.array([[d, y]], float))[0][0])])

    def step_read():
        return R.tool_call("photo_read", {"points": [point(n, READ_PHOTOS[n]) for n in CTRL], "drop": []})

    def step_edit_no_object():
        st["readings_read"] = json.loads(json.dumps(lab.readings()))          # the first readings, as stored
        st["revisions_before_object"] = len(lab.state["revisions"])
        return R.tool_call("facet_edit", edit(0, H1_EDITS))

    def step_compare_zoom():
        st["h1"], st["h1_ck"] = head_rev(), lab.s.head["id"]
        sp = head_spec()
        zooms = [{"photo": pid, "box": CH.box_around(lab, sp, pid, surface_point(sp, *dy), *wh), "geometry": geo}
                 for pid, dy, wh, geo in ZOOMS]
        p = surface_point(sp, *POINT_B2)
        on = [round(float(v), 1) for v in CH.pixel(lab, sp, "B2", p)]
        st["point_b2"] = {"uv": on, "d": POINT_B2[0], "y": POINT_B2[1],
                          "t_model": float(p[2]) - float(np.interp(POINT_B2[0], *np.asarray(sp["inner"], float).T))}
        st["zooms"] = zooms
        return R.tool_call("photo_compare", {"photos": COMPARE_PHOTOS, "crop": None, "zoom": zooms,
                                             "points_px": [{"photo": "B2", "points": [on, OFF_B2]}]})

    def step_edit_unjudged():
        st["zoom_compare"] = S.seen_compares(lab.state)[-1]["compare_id"]
        return R.tool_call("facet_edit", edit(st["h1"], [VALLEY]))

    def step_reread():
        name, photos, shift = REREAD
        return R.tool_call("photo_read", {"points": [point(name, photos, shift)], "drop": [DROP]})

    def step_edit_h2():
        return R.tool_call("facet_edit", edit(st["h1"], H2_EDITS))

    def step_variants():
        st["h2"], st["h2_ck"] = head_rev(), lab.s.head["id"]
        return R.tool_call("facet_variants", {"parent_revision": st["h2"], "variants": arms(lab.readings()),
                                              "photos": VARIANT_PHOTOS, "judgement": judgement(best=st["h2_ck"])},
                           counted=GUARD_COUNT)

    def variant_rev(label: str, n: int = -1) -> int | None:
        return next((i["revision"] for v in lab.state["variants"][n:][:1] for i in v["items"] if i["label"] == label),
                    None)

    def step_edit_h3():
        st["flat"] = variant_rev("flat")
        return R.tool_call("facet_edit", edit(st["flat"], []))

    def step_reapply_h1():
        """H1 again after the guard's epoch, judging edit_h3's compare: the compare cache returns edit_h1's panels."""
        st["h3"], st["h3_ck"] = head_rev(), lab.s.head["id"]
        return R.tool_call("facet_edit", edit(st["h1"], [], judged=judgement(best=st["h2_ck"])))

    def step_finish_early():
        return R.tool_call("facet_finish", {"status": "matched", "best_checkpoint": st["h3_ck"], "summary": CONTROL,
                                            "judgement": judgement(best=st["h2_ck"], overall="worse")})

    def step_inspect_slow():
        slow["armed"] = True
        tools["facet_inspect"].timeout_seconds = SHORT_TIMEOUT_S
        return R.tool_call("facet_inspect", {})

    def step_busy():
        tools["facet_inspect"].timeout_seconds = timeout0
        return R.tool_call("photo_image", {"photo": "05", "crop": None, "enhance": False, "scale": 1})

    def step_edit_slow():
        """H4 under a 3 s SDK timeout, starting 5 s late: E_TIMEOUT, then its thread applies and compares."""
        settle(slow["done"], 60)
        slow_edit["armed"] = True
        tools["facet_edit"].timeout_seconds = SHORT_TIMEOUT_S
        return R.tool_call("facet_edit", edit(st["h3"], H4_EDITS))

    def step_back_to_h2():
        """After the timed-out apply has finished: no judgement is due (its compare was never shown)."""
        settle(slow_edit["done"], 120)
        tools["facet_edit"].timeout_seconds = edit_timeout0
        st["unseen"] = [c["compare_id"] for c in lab.state["compares"] if c.get("unseen")]
        st["h4"] = head_rev()
        return R.tool_call("facet_edit", edit(st["h2"], [], photos=lab.compare_ids()))

    def step_edit_h5():
        st["h2_all_ck"] = lab.s.head["id"]
        c = lab.readings()["C"]
        up = node_mm("C", round(float(c["d"]), 3), round(float(c["y"]), 3), round(float(c["t"]) + C_UP_MM, 3))
        return R.tool_call("facet_edit", edit(st["h2"], [up], judged=judgement(best=st["h2_all_ck"],
                                                                              overall="same")))

    def step_variants_h5():
        st["h5"] = head_rev()
        return R.tool_call("facet_variants", {
            "parent_revision": st["h5"], "variants": arms(lab.readings(), ("flat", "kdeep")),
            "photos": H5_VARIANT_PHOTOS, "judgement": judgement(best=st["h2_all_ck"], overall="worse")})

    steps = [
        ("inspect", lambda: R.tool_call("facet_inspect", {})),
        ("photo_image", lambda: R.tool_call("photo_image", {"photo": "05", "crop": CROP_05, "enhance": True,
                                                            "scale": SCALE_05})),
        ("photo_black", lambda: R.tool_call("photo_image", {"photo": "B2", "crop": None, "enhance": False,
                                                            "scale": 1})),
        ("read", step_read),
        ("edit_no_object", step_edit_no_object),
        ("edit_h1", lambda: R.tool_call("facet_edit", edit(0, H1_EDITS, obj=OBJECT_NOTE))),
        ("compare_zoom", step_compare_zoom),
        ("edit_unjudged", step_edit_unjudged),
        ("edit_fit_refused", lambda: R.tool_call("facet_edit", edit(st["h1"], [VALLEY], judged=judgement(
            best=st["h1_ck"])))),
        ("reread", step_reread),
        ("inspect_stale", lambda: R.tool_call("facet_inspect", {})),
        ("edit_h2", step_edit_h2),
        ("variants", step_variants),
        ("edit_h3", step_edit_h3),
        ("reapply_h1", step_reapply_h1),
        ("finish_early", step_finish_early),
        ("inspect_slow", step_inspect_slow),
        ("busy", step_busy),
        ("edit_slow", step_edit_slow),
        ("back_to_h2", step_back_to_h2),
        ("edit_h5", step_edit_h5),
        ("variants_h5", step_variants_h5),
        ("finish", lambda: R.tool_call("facet_finish", {"status": "stuck", "best_checkpoint": st["h2_all_ck"],
                                                        "summary": CONTROL, "judgement": None})),
    ]
    names = [n for n, _ in steps]
    transport = R.ScriptedOpenAI([])
    stamps: list = []

    def handler(request):
        if request.url.path == R.COUNT_PATH and len(transport.replies) == len(transport.counts) \
                and len(transport.replies) < len(steps):
            transport.replies.append(steps[len(transport.replies)][1]())
        if request.url.path == R.CREATE_PATH:
            stamps.append(time.perf_counter())
        return transport.handle(request)
    spec.offline_handler = handler
    t1 = time.perf_counter()
    with LoopbackOnly() as network, ProductionTransportTripwire() as tripwire:
        result = asyncio.run(run_astra(spec, scene=lab))
    t_run = time.perf_counter() - t1
    lab.inspect, lab.variants, lab.edit, lab._variant_views, lab.context_stubs = (inspect0, variants0, edit0, views0,
                                                                                  stubs0)
    records = CH.call_records(transport.creates, lab.state["images"], record_kind)
    if len(records) == len(transport.creates) - 1:          # the accepted finish: its output is the final output
        fc = next(o for o in transport.replies[len(transport.creates) - 1].output if o["type"] == "function_call")
        text = result.get("final_output") or ""
        try:
            body_json = json.JSONDecoder().raw_decode(text)[0]
        except ValueError:
            body_json = {}
        records.append({"call_id": None, "tool": fc["name"], "request": None, "args_chars": len(fc["arguments"]),
                        "text_chars": len(text), "text": text, "status": body_json.get("status"),
                        "code": body_json.get("code"), "body": body_json, "images": [], "stubs": []})
    by = {n: {"call_id": None, "tool": None, "request": None, "args_chars": 0, "text_chars": 0, "text": "",
              "status": None, "code": None, "body": {}, "images": [], "stubs": []} for n in names}
    by.update({names[i]: rec for i, rec in enumerate(records)})       # a step the run never reached keeps the blank
    # the worker's own refusals of malformed lab specs (outside the loop, after it)
    lab.start()
    try:
        fp_before = lab.s.fingerprint()
        path_refusal = lab.s.call_mutating("control", "temple_lab_build", {"spec": "arbitrary-path.json"},
                                           deadline_s=30)
        bad_refusal = lab.s.call_mutating("control", "temple_lab_build", {"spec": {"schema": "bad"}}, deadline_s=30)
        native_rollback = bad_refusal["status"] == "refused" and lab.s.fingerprint() == fp_before
    finally:
        lab.close()
    lab.verify_inputs()
    try:
        checks, detail = check_run(lab, st, by, names, records, transport, result, ctrl)
        checks["checks_ran"] = True
    except Exception:  # noqa: BLE001 - a run that ended early still writes its report, with the traceback
        checks, detail = {"checks_ran": False}, {"check_error": traceback.format_exc()}
    rows = lab.manifest["photos"]
    roles = [r.get("role", "target") for r in rows]
    request = read(root / "runs" / RUN_ID / "approval-request.json")["request"]
    sent = root / "runs" / RUN_ID / "instructions-sent.txt"
    final = json.loads((result.get("final_output") or "{}").split("\n", 1)[0] or "{}")
    checks.update({
        "completed_by_accepted_finish": result["status"] == "completed" and final.get("status") == "accepted",
        "no_request_after_the_accepted_finish": len(transport.creates) == len(steps),
        "missing_approval_refused_before_network": missing_approval_refused and not blocked_network.attempts
                                                   and not blocked_transport.constructions,
        "no_external_network": not network.attempts and not tripwire.constructions,
        "no_api_contract_violations": not transport.violations and not transport.unexpected,
        "every_tool_called": {r["tool"] for r in records} == set(TOOL_NAMES),
        "tool_choice_required_on_every_request": all(b.get("tool_choice") == "required" for b in transport.creates),
        "instructions_sent_is_the_bound_text": sha(sent) == request["instructions_sha256"]
                                               and b"\r" not in sent.read_bytes(),
        "first_request_carries_every_photo": CH.first_request_images(transport.creates[0]) == [p["sha256"] for p
                                                                                               in rows]
                                             and roles == ["target"] * 6 + ["colourway"] * 3
                                             and [r["id"] for r in rows[6:]] == BLACK_IDS
                                             and [r.get("basis_ref") for r in rows[6:]] == BLACK_REFS
                                             and lab.compare_ids() == lab.photo_ids() + BLACK_IDS
                                             and lab.image_ids() == lab.compare_ids(),
        "exit_checkpoint_verified": (result.get("exit_checkpoint") or {}).get("verified") is True,
        "worker_refuses_a_path_spec": path_refusal["status"] == "refused",
        "worker_failed_build_leaves_the_scene": native_rollback,
    })
    durations = [round(b - a, 2) for a, b in zip(stamps, stamps[1:])]
    report = {"label": LABEL, "checks": checks, "passed": all(checks.values()), "rig": rig, "paid_calls": 0,
              "prepared": prepared, "seconds": {"prepare": round(t_prepare, 1), "run": round(t_run, 1)},
              "controls": {n: [float(v) for v in p] for n, p in ctrl.items()},
              "result": {k: result.get(k) for k in ("status", "error", "requests", "turns_used", "tool_calls",
                                                    "finish_notes", "context_guard", "exit_checkpoint", "settings",
                                                    "final_output")},
              "steps": [{"step": names[i], "tool": r["tool"], "status": r["status"], "code": r["code"],
                         "request": r["request"], "text_chars": r["text_chars"], "args_chars": r["args_chars"],
                         "images": [im[:3] for im in r["images"]], "stubs_when_first_sent": len(r["stubs"]),
                         "seconds_until_next_request": durations[i] if i < len(durations) else None}
                        for i, r in enumerate(records)],
              **detail,
              "variants_fingerprints": st["fingerprints"], "violations": transport.violations,
              "network_attempts": network.attempts, "transport_constructions": tripwire.constructions,
              "request_count": len(transport.creates),
              "first_request": {**CH.first_request(transport.creates[0]), **first_request_bytes(root)},
              "text_chars": {t: {"largest": max((r["text_chars"] for r in records if r["tool"] == t), default=None),
                                 "mean": round(float(np.mean([r["text_chars"] for r in records if r["tool"] == t])))
                                 if any(r["tool"] == t for r in records) else None} for t in TOOL_NAMES},
              "tool_outputs": [{"tool": r["tool"], "status": r["status"], "text_chars": r["text_chars"],
                                "args_chars": r["args_chars"], "images": [im[:3] for im in r["images"]],
                                **({"arms": len(r["body"]["variants"])} if r["tool"] == "facet_variants"
                                   and isinstance(r["body"].get("variants"), list) else {})}
                               for r in records],
              "outputs": {names[i]: r["body"] for i, r in enumerate(records)},
              "native_refusals": [path_refusal, bad_refusal]}
    write_json_atomic(root / "rehearsal.json", report)
    if not report["passed"]:
        raise AssertionError({k: v for k, v in checks.items() if not v})
    return {"passed": True, "report": str(root / "rehearsal.json"), "paid_calls": 0}


def first_request_bytes(root: Path) -> dict:
    """{"bytes": the first request's serialized bytes} from the run's events.jsonl (ReviewLog's api_request event), or
    {} when it is not there (the estimate then uses its bytes model)."""
    try:
        for line in (root / "runs" / RUN_ID / "events.jsonl").read_text(encoding="utf-8").splitlines():
            ev = json.loads(line)
            if ev.get("event") == "api_request" and ev.get("request_seq") == 1:
                return {"bytes": int(ev["serialized_bytes"])}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {}


# --------------------------------------------------------------------------------------------- the checks
def check_run(lab, st: dict, by: dict, names: list, records: list, transport, result, ctrl: dict):
    """The rehearsal's checks of the tools' outputs and the lab state (module doc): (checks {name: bool}, detail for
    the report)."""
    checks, detail = {}, {}
    images = lab.state["images"]

    def near(a, b, tol) -> bool:
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(float(a) - float(b)) <= tol

    def kinds_of(step) -> list:
        return [im[2] for im in by[step]["images"]]

    # every result: the footer; no retired v2 phrase in any text Astra read from a tool
    checks["every_result_has_the_footer"] = all(FOOTER.search(r["text"]) for r in records if r["status"] != "sdk_text")
    banned = [p.lower() for p in tuple(RETIRED_PHRASES) + tuple(REMOVED_PHRASES)]
    hits = sorted({(names[i], p) for i, r in enumerate(records) for p in banned if p in r["text"].lower()})
    checks["no_retired_phrase_in_any_output"] = not hits
    detail["retired_phrase_hits"] = hits
    # images: each output's images in the request right after its call, all detail high
    pages = {}
    for step in ("variants", "variants_h5"):
        vid = by[step]["body"].get("variants_id")
        pages[step] = sorted(str(p) for p in (lab.root / "work" / "temple_lab" / "variants" / str(vid)).glob(
            "sheet-*.png")) if vid else []
    expect = {"inspect": 1, "photo_image": 1, "photo_black": 1, "read": len(CTRL), "reread": 1, "inspect_stale": 1,
              "compare_zoom": len(COMPARE_PHOTOS) + len(ZOOMS), "variants": len(pages["variants"]),
              "variants_h5": len(pages["variants_h5"])}
    for step in ("edit_h1", "edit_h2", "edit_h3", "reapply_h1", "back_to_h2", "edit_h5"):
        expect[step] = 1 + len(by[step]["body"].get("photos") or {})
    reach = {n: [sum(1 for im in by[n]["images"] if im[3] == "high"), k, by[n]["request"], names.index(n) + 1]
             for n, k in expect.items()}
    checks["images_reach_the_next_request"] = all(a == b and c == d for a, b, c, d in reach.values()) \
        and all(k > 0 for k in expect.values())
    detail["images_reach"] = reach
    # facet_inspect of revision 0
    ins = by["inspect"]["body"]
    checks["inspect_shows_revision_0"] = ins.get("status") == "ok" and (ins.get("head") or {}).get("revision") == 0 \
        and kinds_of("inspect") == ["chart"] and "fitted_at" in ins["spec"]["nodes"]["columns"] \
        and ins.get("readings", {}).get("rows") == {} and set(ins.get("photos") or {}) == set(lab.compare_ids())
    # photo_image: a crop at k 2, enhanced; a black photo whole
    pi = by["photo_image"]["body"]
    tr = pi.get("transform") or {}
    w, h = (CROP_05[2] - CROP_05[0]) * SCALE_05, (CROP_05[3] - CROP_05[1]) * SCALE_05
    checks["photo_image_crop_scale_enhance"] = pi.get("status") == "ok" and tr.get("box") == CROP_05 \
        and tr.get("k") == SCALE_05 and "stretch" in tr and by["photo_image"]["images"][0][:2] == [w, h]
    pb = by["photo_black"]["body"]
    checks["photo_image_shows_a_black_photo"] = pb.get("status") == "ok" and pb.get("photo") == "B2" \
        and pb.get("colour") == "black"
    # photo_read: each reading at its control, exact picks, one pick sheet a reading of its photos' size
    first = st.get("readings_read") or {}
    rbody = by["read"]["body"]
    sheet_recs = [images.get(im[4]) or {} for im in by["read"]["images"]]
    rows = {}
    for n, p in ctrl.items():
        rec = first.get(n) or {}
        rows[n] = {"err_mm": [round(float(rec.get(k, np.nan)) - float(v), 4) for k, v in zip(("d", "y", "X"), p)],
                   "res_px": rec.get("res_px"), "kind": rec.get("kind"), "photos": rec.get("photos"),
                   "sd": [rec.get("sd_d"), rec.get("sd_y"), rec.get("sd_X")],
                   "warnings": [w_["code"] for w_ in rec.get("warnings") or []],
                   "crossing_deg": rec.get("crossing_deg")}
    n_px = {n: (2 * RD.PANEL_W + 3 * RD.GAP_PX, RD.TITLE_PX + len(READ_PHOTOS[n]) * (RD.PANEL_H + RD.LABEL_PX
                                                                                     + RD.GAP_PX) + RD.GAP_PX)
            for n in CTRL}
    checks["readings_recover_the_controls"] = rbody.get("status") == "ok" and set(first) == set(CTRL) and all(
        all(near(e, 0.0, READ_TOL_MM) for e in r["err_mm"]) and max(r["res_px"] or [99]) < READ_RES_PX
        and r["photos"] == READ_PHOTOS[n] and r["kind"] == ("crossing" if n in CROSSING else "corner")
        for n, r in rows.items())
    checks["readings_texts_and_pick_sheets"] = list(rbody.get("readings") or {}) == list(CTRL) and all(
        rbody["readings"][n] == RD.reading_text(first[n]) for n in CTRL) and [
        (s.get("kind"), s.get("reading")) for s in sheet_recs] == [("reading", n) for n in CTRL] and [
        tuple(im[:2]) for im in by["read"]["images"]] == [n_px[n] for n in CTRL]
    e = rows["E"]
    checks["crossing_states_its_limits"] = {"W_ALONG_LINE", "W_SHALLOW_CROSSING"} <= set(e["warnings"]) \
        and len(e["crossing_deg"] or []) == len(READ_PHOTOS["E"]) and "slide line" in rbody["readings"]["E"] \
        and all(rows[n]["crossing_deg"] is None for n in CTRL if n not in CROSSING)
    detail["readings"] = rows
    # E_OBJECT on the first facet_edit without a note: nothing registered
    eo = by["edit_no_object"]["body"]
    checks["e_object_on_the_first_edit"] = eo.get("code") == "E_OBJECT" and eo.get("message") == S.OBJECT_REFUSAL \
        and "latest_object" not in eo and st.get("revisions_before_object") == 1 \
        and lab.state["revisions"][1]["call_id"] != by["edit_no_object"]["call_id"]
    # H1: the layout from the readings, warp crown, soft logo, the object note
    b1 = by["edit_h1"]["body"]
    h1 = st.get("h1")
    d1 = lab.design(h1) if h1 is not None else {"nodes": {}, "facets": {}}
    read_ids = {n: (t.get("read") or {}).get("id") for n, (_d, _y, t) in d1["nodes"].items() if G.is_read(t)}
    rc1 = b1.get("receipt") or {}
    checks["edit_builds_readings_warp_soft_with_the_note"] = b1.get("status") == "applied" \
        and b1.get("object") == "recorded" and b1.get("revision") == h1 \
        and read_ids == {n: first[n]["id"] for n in ("A", "H", "K", "E", "C")} \
        and d1["facets"]["crown"]["plane"] == "warp" and d1["facets"]["logo"].get("soft") is True \
        and any(ln.startswith("face crown warp") for ln in rc1.get("fit") or []) \
        and any(ln.startswith("logo|base") and " soft" in ln for ln in rc1.get("creases") or []) \
        and any(ln.startswith("crown|logo ridge") for ln in rc1.get("creases") or []) \
        and bool(b1.get("compare_id")) and kinds_of("edit_h1")[0] == "chart" \
        and set(kinds_of("edit_h1")[1:]) == {"compare"} and S.object_text(lab.state) == OBJECT_NOTE
    # the zoom compare: B1, B2 in full (black rows), two zooms (geometry at B1 only), points at B2
    cz = by["compare_zoom"]["body"]
    zid = cz.get("compare_id")
    zrec = S.compare(lab.state, zid) or {}
    zimg = [images.get(im[4]) or {} for im in by["compare_zoom"]["images"]]
    ztexts = cz.get("zoom") or []
    m = POINTS_RE.search(str((cz.get("photos") or {}).get("B2")))
    pt = st.get("point_b2") or {}
    zreport = read(zrec["report"]) if zrec.get("report") else {}
    # the images are titled with the lab's photo ids (photo_names; v3p3-fix-seeing: "black-side photo")
    znames = [((zreport.get("photos") or {}).get(lab.rid(p)) or {}).get("name") for p in COMPARE_PHOTOS] + \
        [z.get("name") for z in zreport.get("zoom") or []]
    checks["compare_zoom_geometry_and_points"] = cz.get("status") == "ok" and list(cz.get("photos") or {}) == \
        COMPARE_PHOTOS and zrec.get("photos") == COMPARE_PHOTOS + ["05"] and zrec.get("stacked") == COMPARE_PHOTOS \
        and znames == COMPARE_PHOTOS + ["B1", "05"] \
        and [z.get("zoom", False) for z in zimg] == [False, False, True, True] \
        and kinds_of("compare_zoom") == ["compare", "compare", "zoom", "zoom"] \
        and len(ztexts) == 2 and ztexts[0].startswith("zoom 1 on B1 (side, black)") \
        and ztexts[1].startswith("zoom 2 on 05 (side, target)") and "geometry colours" in ztexts[0] \
        and "geometry colours" not in ztexts[1] and "crown|logo" in ztexts[0] \
        and m is not None and near(float(m[3]), pt.get("d"), POINT_TOL_MM) \
        and near(float(m[4]), pt.get("y"), POINT_TOL_MM) and near(float(m[5]), pt.get("t_model"), POINT_TOL_MM)
    detail["compare_zoom"] = {"compare_id": zid, "zooms": st.get("zooms"), "point_b2": pt,
                              "charted": list(m.groups()) if m else None, "zoom_texts": ztexts, "names": znames}
    black_ok = {pid: str((cz.get("photos") or {}).get(pid)).startswith(
        f"{lab.basis.refs[lab.rid(pid)].get('view_kind')} black (rows photo / gloss-black render / overlay)")
        for pid in COMPARE_PHOTOS}
    zrows = {pid: sorted(((zreport.get("photos") or {}).get(lab.rid(pid)) or {}).get("rows") or {})
             for pid in COMPARE_PHOTOS}
    gloss_from = (zreport.get("gains") or {}).get("gloss_black", {}).get("from")
    checks["black_compare_is_gloss_without_native"] = all(black_ok.values()) and all(
        set(r) == BLACK_ROWS for r in zrows.values())
    checks["gloss_gain_still_from_black_side"] = str(gloss_from).startswith("the dark variant photo black-side ")
    detail["black_compare"] = {"rows": zrows, "gloss_black_gain_from": gloss_from}
    # E_JUDGEMENT; the fit refusal judging the zoom compare (its zoomed photo included)
    ej = by["edit_unjudged"]["body"]
    checks["e_judgement"] = ej.get("code") == "E_JUDGEMENT" and ej.get("compare_id") == zid
    ef = by["edit_fit_refused"]["body"]
    jz = next((j for j in lab.state["judgements"] if j["compare_id"] == zid), {})
    checks["fit_refusal_e_fit_shape"] = ef.get("status") == "refused" and ef.get("code") == "E_FIT_SHAPE" \
        and ef.get("stage") == "fit" and "crown|logo declared valley" in str(ef.get("message")) \
        and "against the readings of" in str(ef.get("message")) and ef.get("judgement") == f"judgement of {zid} stored" \
        and [p["photo"] for p in jz.get("photos") or []] == COMPARE_PHOTOS + ["05"] \
        and len(lab.state["revisions"]) > (h1 or 0) and all(r["call_id"] != by["edit_fit_refused"]["call_id"]
                                                            for r in lab.state["revisions"])
    # the re-read and the drop; the head's node keeps the reading it was set from
    rr = by["reread"]["body"]
    new_a = lab.readings().get("A") or {}
    checks["reread_replaces_drops_and_names_the_held_node"] = rr.get("status") == "ok" \
        and str((rr.get("replaced") or {}).get("A")).startswith("was X ") \
        and f"({first['A']['id']})" in str((rr.get("replaced") or {}).get("A")) and rr.get("dropped") == [DROP] \
        and f"r{h1}: node A holds the earlier reading A" in str(rr.get("head_nodes")) \
        and rr.get("stored") == sorted(n for n in CTRL if n != DROP) \
        and abs(float(new_a.get("X", 0.0)) - float(first["A"]["X"])) > 0.01 and new_a.get("photos") == REREAD[1]
    si = by["inspect_stale"]["body"]
    srows = (si.get("spec") or {}).get("nodes", {}).get("rows") or {}
    checks["inspect_states_a_held_earlier_reading"] = si.get("status") == "ok" \
        and (si.get("head") or {}).get("revision") == h1 and (srows.get("A") or [None] * 3)[2] == \
        "read (replaced since)" and all((srows.get(n) or [None] * 3)[2] == "read" for n in ("H", "K", "E", "C")) \
        and set((si.get("readings") or {}).get("rows") or {}) == {n for n in CTRL if n != DROP} \
        and si.get("object") == OBJECT_NOTE
    # facet_variants of H2 at a target and a black photo
    vb = by["variants"]["body"]
    vrec = next((v for v in lab.state["variants"] if v["id"] == vb.get("variants_id")), {})
    vviews = st["variant_views"][0] if st["variant_views"] else {"passes": {}, "photos": {}}
    items = {i["label"]: i for i in vb.get("variants") or []}
    edges = vrec.get("edges") or {}
    page_px = [crease_px(p) for p in pages["variants"]]
    checks["variants_at_a_target_and_a_black_photo"] = vb.get("status") == "ok" and vrec.get("photos") == \
        VARIANT_PHOTOS and vviews["passes"] == VARIANT_PASSES and vrec.get("rendered") == ["flat", "kdeep", "tail"] \
        and str((vviews["photos"].get("B1") or {}).get("what")).startswith("gloss-black render") \
        and str((vviews["photos"].get("05") or {}).get("what")).startswith("native render") \
        and vb.get("spread_ok") is True and "render" not in vb
    checks["variant_arm_fit_refusal_and_fit_lines"] = items.get("valley", {}).get("code") == "E_FIT_SHAPE" \
        and items["valley"].get("stage") == "fit" and items["valley"].get("revision") is None \
        and all(isinstance(items.get(lb, {}).get("fit"), list) and items[lb]["fit"] for lb in ("flat", "kdeep", "tail")) \
        and any("W_FACE_NOT_FLAT" in ln for ln in items["flat"]["fit"]) \
        and any("W_FIT_OFF_READING" in ln for ln in items["kdeep"]["fit"])
    checks["variant_pages_draw_each_models_edges"] = vrec.get("edge_occlusion") == "scene" and set(edges) == \
        set(VARIANT_PHOTOS) and all(set(edges[p]) == {"head", "flat", "kdeep", "tail"} and all(
            isinstance(v, int) and v > 0 for v in edges[p].values()) for p in VARIANT_PHOTOS) \
        and bool(page_px) and all(px > 0 for px in page_px)
    checks["variants_left_the_scene_unchanged"] = len(st["fingerprints"]) == 2 and all(
        a == b for a, b in st["fingerprints"])
    detail["variants"] = {"variants_id": vb.get("variants_id"), "passes": vviews["passes"],
                          "photos": vviews["photos"], "edges": edges, "pages": pages["variants"],
                          "page_crease_px": page_px, "items": vb.get("variants"), "pairwise_mm": vb.get("pairwise_mm")}
    # the guard: fired once at edit_h3's request, its choice = the decision on the state it chose from, every class of
    # the decision present, the same stubs on every later request
    guard_req = next((n for n, body in enumerate(transport.creates) if CH.stubbed_in(body)), None)
    g = st.get("guard") or {}
    snap = g.get("snapshot") or {}
    expected = CH.expected_stubs(snap) if snap else {}
    chosen = g.get("chosen") or {}
    sent = CH.stubbed_in(transport.creates[guard_req]) if guard_req is not None else {}
    want_per_call = {}
    for r in records:
        k = sum(1 for im in r["images"] if im[4] in expected)
        if k and r["request"] is not None and r["request"] < (guard_req or 0):
            want_per_call[r["call_id"]] = k
    kept = [im for r in records if r["request"] is not None and r["request"] < (guard_req or 0)
            for im in r["images"] if im[4] not in expected]
    why = list(expected.values())
    classes = {"old compare panel": "superseded model" in why,
               "old zoom grid": "superseded model (zoom grid)" in why,
               "re-read pick sheet": "pick sheet of a reading read again" in why,
               "dropped pick sheet": "pick sheet of a reading dropped" in why,
               "stored pick sheets kept": sum(1 for im in kept if im[2] == "reading") == len(CTRL) - 1,
               "photo views kept": sum(1 for im in kept if im[2] == "photo") == 2,
               "charts kept": sum(1 for im in kept if im[2] == "chart") >= 4,
               "latest results and best's compare kept": all(
                   im[4] not in expected for n in ("edit_h2", "variants") for im in by[n]["images"]
                   if im[2] != "chart")}
    later = [CH.stubbed_in(b) for b in transport.creates[(guard_req or 0) + 1:]]
    checks["context_guard_fired_once_at_the_guard_request"] = bool((result.get("context_guard") or {}).get("epoch")) \
        and guard_req == names.index("variants") + 1 and CH.KEEP_LATEST == LB.CONTEXT_KEEP_LATEST
    checks["context_guard_decision_holds"] = bool(expected) and set(chosen) == set(expected) and all(classes.values()) \
        and {cid: len(v) for cid, v in sent.items()} == want_per_call \
        and all(all(s.get(cid) == v for cid, v in sent.items()) for s in later) \
        and all(set(s) <= set(sent) for s in later)              # no output that arrived after the epoch is stubbed
    # the re-applied H1 (after the epoch): its compare panels are the bytes the guard stubbed in edit_h1's output (the
    # compare cache), and they reach the next request as images (review 2026-10-12, P1, v3p3-verify-14)
    ra, ra_body = by["reapply_h1"], by["reapply_h1"]["body"]
    ra_panels = [im[4] for im in ra["images"] if im[2] == "compare"]
    h1_panels = [im[4] for im in by["edit_h1"]["images"] if im[2] == "compare"]
    checks["reapplied_stubbed_revision_arrives_as_images"] = ra_body.get("status") == "applied" \
        and ra_body.get("revision") == st.get("h1") and bool(ra_panels) and set(ra_panels) <= set(expected) \
        and set(ra_panels) <= set(h1_panels) and not ra["stubs"] and ra["request"] is not None \
        and ra["request"] > (guard_req or 1 << 30) and len(ra["images"]) == 1 + len(ra_body.get("photos") or {}) \
        and all(ra["call_id"] not in s for s in later)
    detail["reapply_h1"] = {"revision": ra_body.get("revision"), "compare_id": ra_body.get("compare_id"),
                            "panels": len(ra_panels), "panels_stubbed_at_the_epoch": sum(1 for d in ra_panels
                                                                                         if d in expected),
                            "stubs_when_sent": len(ra["stubs"]), "request": ra["request"]}
    detail["context_guard"] = {"request": guard_req, "best_checkpoint": snap.get("best_checkpoint"),
                               "best_revision": snap.get("best_revision"), "results": snap.get("results"),
                               "expected": {k[:12]: (snap["images"].get(k) or {}) | {"why": v} for k, v in
                                            expected.items()} if snap else {},
                               "chosen_texts": sorted(chosen.values()), "per_call": want_per_call, "classes": classes,
                               "kept_kinds": sorted({im[2] for im in kept})}
    # the applied variant arm (no edits) and the crease / diff statements against the head
    b3 = by["edit_h3"]["body"]
    checks["applied_variant_is_its_revision"] = b3.get("status") == "applied" and b3.get("diff") == "no change" \
        and b3.get("parent") == b3.get("revision") == st.get("flat") \
        and str(b3.get("diff_vs_head")).startswith(f"r{st.get('h2')}: ")
    vs = []
    for i, r in enumerate(records):
        b = r["body"]
        if r["tool"] != "facet_edit" or r["status"] not in ("applied", "built"):
            continue
        head = st["edit_heads"].get(r["call_id"])
        flips = CH.built_flips(lab.build(head), lab.build(b["revision"])) if head is not None else None
        vs.append({"step": names[i], "head": head, "parent": b.get("parent"), "revision": b.get("revision"),
                   "flips": flips, "crease_vs_head": b.get("crease_vs_head"), "diff_vs_head": b.get("diff_vs_head"),
                   "ok": head is not None and CH.flips_stated(b.get("crease_vs_head"), flips, f"r{head}: ")
                   and ("diff_vs_head" in b) == (b.get("parent") != head)})
    checks["crease_vs_head_exactly_on_flips"] = all(v["ok"] for v in vs) and [v["step"] for v in vs] == [
        "edit_h1", "edit_h2", "edit_h3", "reapply_h1", "back_to_h2", "edit_h5"] and [(v["step"], v["flips"]) for v in vs
                                                                       if v["flips"]] == [
        ("edit_h5", [("logo|base", "valley", "ridge")])]
    # each built variant arm states its flips against the head (crease_vs_head, the head not named)
    arm_flips = []
    for step in ("variants", "variants_h5"):
        vbody = by[step]["body"]
        hr = vbody.get("head_revision")
        for it in vbody.get("variants") or []:
            if it.get("revision") is not None and "same_as" not in it and hr is not None:
                f = CH.built_flips(lab.build(hr), lab.build(it["revision"]))
                arm_flips.append({"step": step, "label": it["label"], "flips": f,
                                  "crease_vs_head": it.get("crease_vs_head"),
                                  "ok": CH.flips_stated(it.get("crease_vs_head"), f)})
    checks["variant_items_state_their_flips"] = len(arm_flips) == 5 and all(a["ok"] for a in arm_flips)
    detail["crease_vs_head"] = {"edits": vs, "variant_arms": arm_flips}
    # the refused finish, the timeout and E_BUSY, the timed-out apply
    fe = by["finish_early"]["body"]
    checks["refused_finish_names_its_preconditions"] = fe.get("code") == "E_FINISH" and [
        x["precondition"] for x in fe.get("missing") or []] == [1, 3] and by["finish_early"]["request"] is not None
    checks["sdk_timeout_then_e_busy"] = by["inspect_slow"]["code"] == "E_TIMEOUT" \
        and "timed out" in by["inspect_slow"]["text"] and bool(FOOTER.search(by["inspect_slow"]["text"])) \
        and by["busy"]["code"] == "E_BUSY"
    slow_call = by["edit_slow"]["call_id"]
    bh = by["back_to_h2"]["body"]
    checks["timed_out_apply_needs_no_judgement"] = by["edit_slow"]["code"] == "E_TIMEOUT" \
        and bh.get("status") == "applied" and st.get("h4") not in (None, st.get("h3")) \
        and [c["compare_id"] for c in lab.state["compares"] if c["call_id"] == slow_call] == st.get("unseen") \
        and bool(st.get("unseen")) and bh.get("diff") == "no change" and bh.get("revision") == st.get("h2") \
        and str(bh.get("diff_vs_head")).startswith(f"r{st.get('h4')}: ")
    # H2 at every photo with a camera: the side views' outline offsets, black rows, no top-view outline line
    texts = bh.get("photos") or {}
    checks["every_photo_compare_texts"] = list(texts) == lab.compare_ids() \
        and all(SIDE_TEXT[p] in str(texts.get(p)) for p in SIDE_TEXT) \
        and all(str(texts.get(p)).startswith(f"{lab.basis.refs[lab.rid(p)].get('view_kind')} black (rows photo / "
                                             f"gloss-black render / overlay)") for p in BLACK_IDS) \
        and all("rows photo / render / overlay" in str(texts.get(p)) for p in lab.photo_ids()) \
        and "outline:" not in str(texts.get("03")) and "outline of the" not in str(texts.get("03"))
    detail["every_photo_texts"] = texts
    # the last head and the stuck finish
    b5 = by["edit_h5"]["body"]
    v5 = by["variants_h5"]["body"]
    v5rec = next((v for v in lab.state["variants"] if v["id"] == v5.get("variants_id")), {})
    fin = by["finish"]["body"]
    chain = S.distinct_compared_heads(lab.state, lab.distinct)
    checks["typed_value_against_its_reading"] = b5.get("status") == "applied" and any(
        ln.startswith("W_FIT_OFF_READING") and "C" in ln for ln in (b5.get("receipt") or {}).get("fit") or [])
    checks["stuck_finish_accepted_and_exported"] = b5.get("status") == "applied" \
        and v5.get("status") == "ok" and bool(v5rec.get("rendered")) and fin.get("status") == "accepted" \
        and fin.get("finish") == "stuck" and fin.get("revision") == st.get("h2") \
        and (fin.get("validate_spec") or {}).get("ok") is True and len(chain) >= 4 \
        and [j["compare_revision"] for j in S.last_judgements(lab.state)] == [st.get("h1"), st.get("h2"),
                                                                              b5.get("revision")]
    detail["finish"] = {"distinct_heads": chain, "early_missing": fe.get("missing"), "accepted": fin,
                        "heads": {k: st.get(k) for k in ("h1", "h2", "flat", "h3", "h4", "h5")},
                        "unseen_compares": st.get("unseen")}
    return checks, detail


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["rehearse"])
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--rig", default=DEFAULT_RIG)
    a = p.parse_args()
    receipt = a.out.parent / (a.out.name + "-run.json")
    rec = {"label": "OFFLINE TOOL TEST", "pid": os.getpid(), "started": datetime.now(timezone.utc).isoformat(),
           "action": a.action, "out": str(a.out), "rig": a.rig, "paid_calls": 0}
    write_json_atomic(receipt, rec)
    try:
        rec["result"] = rehearse(a.out, a.rig)
        rec["exit_code"] = 0
    except BaseException:
        rec["exit_code"] = 1
        rec["traceback"] = traceback.format_exc()
        raise
    finally:
        rec["ended"] = datetime.now(timezone.utc).isoformat()
        write_json_atomic(receipt, rec)


if __name__ == "__main__":
    main()
