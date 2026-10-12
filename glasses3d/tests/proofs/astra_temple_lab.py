"""T5 (plan/WP6_FACETS.md §5): the $0 scripted rehearsal of a facet run. SCRIPTED OFFLINE TOOL TEST: the edits
below are arbitrary controls, never Astra or product output.

A fresh package (temple_lab.cli.prepare: a byte copy of the original Prada scene, the six target photos, the three
black photos B1-B3, the pose basis with B1-B3 (colourway photos, each with a camera transported from the target photo
of its pose: B1 from 05, B2 from 02, B3 from 04), revision 0 applied) runs through agent/loop.run_astra with the pinned
Agents SDK, the real Blender worker and the seven facet tools, while api.openai.com is a MockTransport
(agent/replay.ScriptedOpenAI) whose replies are scripted
calls built from the live lab state; LoopbackOnly and the production-transport tripwire are active throughout. The
run settings are exactly a paid run's (tools.run_settings: required tool choice kept, final only on an accepted
facet_finish with K 4, the context guard at 240k counted input); only the approval is a synthetic OFFLINE ONLY record.

The script passes every tool and checks: the missing approval is refused before any request; every request has
tool_choice required; images of a tool output reach the next request; a facet_edit without the due judgement is
refused E_JUDGEMENT; H1's facet_edit compare takes a black photo (the default set plus B1); a simulated stuck call
(facet_inspect made to outlast a shortened SDK timeout) answers E_TIMEOUT with the footer and makes the next call
E_BUSY; a facet_edit timed out the same way still applies in its thread, and its compare (never shown) demands no
judgement of the next action; an early facet_finish is refused E_FINISH and the run goes on; one scripted count above
the guard threshold makes the next request replace the superseded compare images by stubs; facet_variants at 05 and
B1 leaves the worker's scene fingerprint unchanged, renders 05's panels native and B1's gloss_black, and every render
panel of both sheets carries that model's own crease lines (computed for every panel, depth-tested on the head's
scene, drawn in the overlay colours: counted per panel on the sheet); the top photo's compare text carries its outline
line (information only); after one more distinct head (judging compare_all), the accepted "stuck" finish ends the run
(status completed, no further request) with an export that validates; the §4.5 text budgets hold on every real
output; instructions-sent.txt is byte for byte the bound instructions; no network. The first request carries every
photo (6 target + 3 black, the black ones role colourway with their basis images); photo_image shows a black photo
(B2); photo_compare on B2 and B3 (points_px on B2, one on the temple, one off it) shows photo / overlay / gloss_black
and no native row, and the job's gloss_black gain still comes from black-side (seeing.gain_view_rank: a transported
camera never displaces a fitted gain view). It records every tool output's text and image sizes (the estimate's
input), where the job's gloss_black gain came from, and each variant sheet's path, pass and per-panel crease lines.

Run 5's inward edge (a crease flipped ridge -> valley by applying a variant, unseen; a solved corner; no way to read t
from a photo) is rehearsed with the same step count up to the context guard: photo_compare's depths read t from a
pixel (compare_points: a B2 reading the default set leaves out comes under "depths", at the model's own t;
compare_black: B2 at H3's crease corner Q, the projection of t = model + DEPTH_DT and the same pixel DEPTH_OFF_PX off
its depth line, each the exact inverse: t, the model's t, px per mm of t against a finite difference, miss, the charted
temple, in the text and the compare record); the "sunk" variant (H1's R below the base) builds F1|base a valley where
H1 builds a ridge, so its variant item and the facet_edit that applies it with no edits ("diff": "no change") state
crease_vs_head and diff_vs_head against H1; every built facet_edit states crease_vs_head exactly when a facet pair both
builds have changed shape against the head it replaced (checked on the builds' own crease tables), and diff_vs_head
exactly when its parent is not that head; one more facet_inspect on the roof H3 (after the guard) prints its solved
nodes as "solve -> value" (the build's node table), while no receipt lists them as solved corners (outside the
outline: they bind nothing). After the guard, one scripted head (HM, edit_margins: a crown whose corner B is solved
from its plane and a logo that takes B, run 5's mechanism as a TOOL TEST layout) makes a receipt carry "margins" (the
crease's margin with its solved corner, exactly lab.margins_summary of the build) and "solved" (B's t), so the cost
estimate (cli estimate, priced from these outputs) prices a facet_edit with them.

Writes <out>/rehearsal.json (and <out>-run.json, the process receipt).
Run: glasses3d/.venv/Scripts/python.exe -m glasses3d.tests.proofs.astra_temple_lab rehearse --out DIR [--rig RIG]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import io
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
from glasses3d.agent.loop import run_astra
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.agent.session_inputs import image_identity
from glasses3d.agent.temple_lab import geometry as G
from glasses3d.agent.temple_lab import lab as LB
from glasses3d.agent.temple_lab import sheets as SH
from glasses3d.agent.temple_lab import state as S
from glasses3d.agent.temple_lab.cli import DEFAULT_RIG, make_spec, prepare
from glasses3d.agent.temple_lab.lab import read, sha, write_json_atomic
from glasses3d.agent.temple_lab.tools import CONTEXT_GUARD_TOKENS, TOOL_NAMES, run_settings
from glasses3d.g3d.temple.spec import parse
from glasses3d.g3d_host import pose as PO
from glasses3d.g3d_host.camera import project_mm
from glasses3d.g3d_host.compare import RIDGE_RGB, VALLEY_RGB

RUN_ID = "offline-rehearsal"
CAP = "100"
LABEL = "SCRIPTED OFFLINE TOOL TEST, NOT ASTRA OUTPUT"
SLOW_INSPECT_S = 8.0           # the simulated stuck call: facet_inspect sleeps this long ...
SHORT_TIMEOUT_S = 3.0          # ... under this SDK timeout, so the next call finds the scene lock held
SLOW_EDIT_S = 5.0              # the timed-out apply: facet_edit starts this late under the same short timeout
TEXT_BUDGET = {"facet_edit": 1500, "facet_variants": 1500, "facet_restore": 1000}      # §4.5
GUARD_COUNT = CONTEXT_GUARD_TOKENS + 1_000      # one scripted count above the guard: it fires on the next request
CROP_05 = [300, 150, 900, 450]
POINTS_05 = [[600.0, 300.0], [700.0, 320.0]]
# native px of black-angled (1920 x 1280): on the temple (charted d 30.8 at revision 0 by the B2/B3 cameras' own
# $0 lab check) and the corner, off it
POINTS_B2 = [[1456.0, 615.0], [5.0, 5.0]]
BLACK_IDS = ["B1", "B2", "B3"]                    # cli.COLOURWAY_PHOTOS: each a colourway photo with a camera
BLACK_REFS = ["black-side", "black-angled", "black-front"]
# H1's facet_edit compare: the default set (compare_points') plus this black photo, which facet_variants then takes (it
# needs each photo compared first); never fewer photos than the default, since the estimate prices facet_edit by the
# mean of these outputs
EDIT_H1_BLACK = "B1"
VARIANT_PHOTOS = ["05", "B1"]      # a target photo (native panels) and a black one (gloss_black panels)
VARIANT_PASSES = {"05": ["native_linear"], "B1": ["gloss_black"]}
BLACK_COMPARE = ["B2", "B3"]
BLACK_ROWS = {"photo", "overlay", "gloss_black"}  # a variant-colourway photo's compare rows (no native row)
FOOTER = re.compile(r"\nsettled \$\d+\.\d\d / cap \$\d+\.\d\d · context \S+ tokens$")
CONTROL = "SCRIPTED CONTROL, not a visual judgement"
# depth readings (photo_compare depths): TOOL TEST pixels, each the projection of a chart point at a chosen t
DEPTH_PHOTO = "B2"                 # the black angled photo (run 5's photo agent read B there at ~7.4-8.0 px per mm)
DEPTH_AT_R0 = (30.0, 0.0)          # compare_points (revision 0): this (d, y) at the model's own t
DEPTH_NODE = "Q"                   # compare_black (H3): this crease corner ...
DEPTH_DT = 1.0                     # ... at the model's t + DEPTH_DT (an arbitrary control thickness) ...
DEPTH_OFF_PX = 5.0                 # ... and the same pixel moved this far off its depth line
DEPTH_RE = re.compile(r"d (\S+) y (\S+): t (\S+) \(model (\S+)\) (\S+) px/mm, miss (\S+) px (charted|mirrored)")
CORNER_ALSO = "B1"                 # compare_black's corner reading: Q at the model's t + DEPTH_DT in B2 and this photo,
CORNER_DD = 2.0                    # given this many mm off in d (a one-photo reading would take it as exact)
EDIT_H2_VARIANT = "sunk"           # the variant edit_h2 applies with no edits: F1|base ridge (H1) -> valley
FLIP_H2 = [("F1|base", "ridge", "valley")]
# the facet_edit steps that build
EDITS_BUILT = ["edit_h1", "edit_h2", "edit_margins", "edit_h3", "edit_h4", "edit_h6"]
HM_SOLVED = "B"                    # the margins head's corner solved from the crown's plane (an end of crown|logo)


# --------------------------------------------------------------------------------------------- scripted edits
def t_base(spec: dict, d: float, y: float, dt: float = 0.0) -> float:
    return round(float(parse(spec).base_t(d, y)) + dt, 3)


def set_node(name: str, d: float, y: float, t) -> dict:
    """t: a number (mm), "base" or None (solve)."""
    mode = "mm" if isinstance(t, float) else ("base" if t == "base" else "solve")
    return {"op": "set_node", "name": name, "d_mm": d, "y_mm": y,
            "t": {"mode": mode, "mm": t if mode == "mm" else None}}


def facet(label: str, poly: list, crease=None, rise=None) -> dict:
    plane = {"mode": "crease", "crease": crease, "rise_per_mm": rise} if crease else \
        {"mode": "nodes", "crease": None, "rise_per_mm": None}
    return {"op": "set_facet", "label": label, "poly": poly, "plane": plane, "fade": None}


def head_edits(name: str, r0: dict) -> tuple[list, bool]:
    """The scripted heads (edits, reset_layout) on revision 0's chains: H1 one raised triangle; HM a crown A P R B
    whose corner B is solved (from the crown's plane) and a logo A B C that takes B, so the crown|logo crease has a
    solved corner that sets it (its margin in the receipt; the crown's given corners span a wide triangle: the first
    try, A at (3, 8), tilted 7.1 deg per mm and added a W_CONDITIONING line, t5_wip6c); H3 a two-facet roof with a
    declared ridge (a new layout); H4 the roof with steeper sides; H5 between them (the timed-out apply); H6 steeper
    still (the third distinct head of the stuck finish)."""
    if name == "HM":
        return [set_node("A", 3.0, -3.0, t_base(r0, 3.0, -3.0, 0.8)),
                set_node("P", 30.0, 14.0, t_base(r0, 30.0, 14.0, 1.2)),
                set_node("R", 78.0, 1.5, t_base(r0, 78.0, 1.5, 0.6)), set_node(HM_SOLVED, 45.0, -2.0, None),
                set_node("C", 20.0, -13.0, t_base(r0, 20.0, -13.0, 0.5)),
                facet("crown", ["A", "P", "R", HM_SOLVED]), facet("logo", ["A", HM_SOLVED, "C"])], True
    if name == "H1":
        return [set_node("P", 10.0, 4.0, "base"), set_node("Q", 40.0, 4.0, "base"),
                set_node("R", 25.0, -12.0, t_base(r0, 25.0, -12.0, 0.8)), facet("F1", ["P", "R", "Q"])], False
    rise = {"H3": -0.12, "H4": -0.2, "H5": -0.16, "H6": -0.28}[name]
    return [set_node("P", 8.0, 1.0, t_base(r0, 8.0, 1.0, 0.8)), set_node("Q", 45.0, 0.0, t_base(r0, 45.0, 0.0, 0.8)),
            set_node("PT", 8.0, 30.0, None), set_node("QT", 45.0, 30.0, None),
            set_node("PB", 8.0, -30.0, None), set_node("QB", 45.0, -30.0, None),
            facet("U", ["P", "Q", "QT", "PT"], ["P", "Q"], rise), facet("D", ["Q", "P", "PB", "QB"], ["P", "Q"], rise),
            {"op": "set_crease", "facets": ["D", "U"], "shape": "ridge"}], True


def variant_edits(r0: dict) -> list:
    """Three depths of H1's node R (its parent is H1's revision): "sunk" sets R below the base, so F1|base is built a
    valley where H1 (R 0.8 mm proud) builds a ridge (8.0° against 11.4° on the Prada original): a crease flip against
    the head, as run 5's applied variant; deep and deeper keep the ridge."""
    return [{"label": lb, "edits": [set_node("R", 25.0, -12.0, t_base(r0, 25.0, -12.0, dt))]}
            for lb, dt in (("sunk", -0.8), ("deep", 1.4), ("deeper", 2.0))]


def built_flips(ref, tb) -> list[tuple[str, str, str]]:
    """The facet pairs both builds have whose built crease (the build's own crease table, kind "crease") is a ridge in
    one and a valley in the other: [(pair, shape in ref, shape in tb)] (the rehearsal's own reading of the tables)."""
    def shapes(x):
        return {"|".join(c["regions"]): c["shape"] for c in x.tables["creases"] if c["kind"] == "crease"}
    a, b = shapes(ref), shapes(tb)
    return [(k, a[k], b[k]) for k in sorted(set(a) & set(b)) if a[k] != b[k] and "flat" not in (a[k], b[k])]


def flips_stated(text, flips: list, prefix: str = "") -> bool:
    """crease_vs_head states exactly these flips (absent when there are none)."""
    if not flips:
        return text is None
    return isinstance(text, str) and text.startswith(prefix) and text.count(" -> ") == len(flips) and all(
        re.search(rf"(^|; |: ){re.escape(k)} {a} [\d.]+° -> {b} [\d.]+°", text) for k, a, b in flips)


def depth_rows(text: str) -> list[dict]:
    """The depth readings of a compare text (lab.depth_text)."""
    return [{"d": float(m[1]), "y": float(m[2]), "t": float(m[3]), "model": float(m[4]), "ppm": float(m[5]),
             "miss": float(m[6]), "side": m[7]} for m in DEPTH_RE.finditer(str(text))]


def judgement(lab, best: str | None = None, overall: str = "mixed") -> dict:
    c = S.seen_compares(lab.state)[-1]
    return {"compare_id": c["compare_id"], "versus_compare_id": None, "overall": overall,
            "photos": [{"photo": p, "verdict": overall, "largest_difference": CONTROL, "at_d_mm": None,
                        "at_y_mm": None} for p in c["photos"]],
            "best_checkpoint": best or lab.s.head["id"]}


# --------------------------------------------------------------------------------------------- request bodies
def _parts(item) -> list:
    out = item.get("output")
    return out if isinstance(out, list) else [{"type": "input_text", "text": out}] if isinstance(out, str) else []


def _image(url: str) -> tuple[str, tuple[int, int]]:
    raw = base64.b64decode(url.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as im:
        return hashlib.sha256(raw).hexdigest(), im.size


def call_records(creates: list, images_index: dict) -> list:
    """Every tool call in order, from the first request that carries its output: name, arguments, the output's text
    and images (size and the lab's kind), the request index it appeared in."""
    seen, out = set(), []
    for n, body in enumerate(creates):
        items = [i for i in body.get("input", []) if isinstance(i, dict)]
        calls = {i["call_id"]: i for i in items if i.get("type") == "function_call"}
        for item in items:
            if item.get("type") != "function_call_output" or item["call_id"] in seen:
                continue
            seen.add(item["call_id"])
            fc = calls.get(item["call_id"], {})
            text = "".join(p.get("text", "") for p in _parts(item) if p.get("type") == "input_text")
            imgs = []
            for p in _parts(item):
                if p.get("type") == "input_image":
                    digest, (w, h) = _image(p["image_url"])
                    imgs.append([w, h, (images_index.get(digest) or {}).get("kind", "unknown"), p.get("detail"),
                                 digest])
            try:
                body_json, _end = json.JSONDecoder().raw_decode(text)
            except ValueError:
                body_json = {"status": "sdk_text"}
            out.append({"call_id": item["call_id"], "tool": fc.get("name"), "request": n,
                        "args_chars": len(fc.get("arguments") or ""), "text_chars": len(text), "text": text,
                        "status": body_json.get("status"), "code": body_json.get("code"), "body": body_json,
                        "images": imgs})
    return out


def first_request(body: dict) -> dict:
    """The first request's text characters (input, instructions, tool schemas; images stripped) and image sizes."""
    sizes = []

    def strip(v):
        if isinstance(v, dict):
            if v.get("type") == "input_image":
                sizes.append(list(_image(v["image_url"])[1]))
                return {"type": "input_image"}
            return {k: strip(x) for k, x in v.items()}
        if isinstance(v, list):
            return [strip(x) for x in v]
        return v
    kept = strip({k: body.get(k) for k in ("input", "instructions", "tools")})
    return {"text_chars": len(json.dumps(kept, ensure_ascii=False, separators=(",", ":"))), "image_sizes": sizes}


def sheet_crease_px(path, layout, shape, n_panels: int) -> list[int]:
    """The crease-colour pixels (exactly compare.RIDGE_RGB or VALLEY_RGB) inside each panel image of a variant sheet,
    in panel order (photo, head, variants), the cells placed as sheets.variant_sheet places them when every panel is
    an image of the same ``shape`` (H, W) (no refused panel)."""
    cols, pw, _ph = layout
    h, w = shape
    a = np.asarray(Image.open(path).convert("RGB"))
    hit = np.zeros(a.shape[:2], bool)
    for rgb in (RIDGE_RGB, VALLEY_RGB):
        hit |= np.all(a == np.asarray(rgb, np.uint8), axis=-1)
    out = []
    for n in range(n_panels):
        r, c = divmod(n, cols)
        x = SH.GAP_PX + c * (pw + SH.GAP_PX)
        y = SH.TITLE_PX + SH.GAP_PX + r * (h + SH.LABEL_PX + SH.GAP_PX) + SH.LABEL_PX
        out.append(int(hit[y:y + h, x:x + w].sum()))
    return out


# --------------------------------------------------------------------------------------------- the rehearsal
def rehearse(root, rig: str = DEFAULT_RIG) -> dict:
    root = Path(root).resolve()
    disable_sdk_tracing()
    t0 = time.perf_counter()
    prepared = prepare(root, rig=rig)
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
    tools = {t.name: t for t in spec.tools}
    timeout0 = tools["facet_inspect"].timeout_seconds
    edit_timeout0 = tools["facet_edit"].timeout_seconds
    st: dict = {"fingerprints": [], "variant_views": [], "edit_heads": {}, "depths": {}}
    slow = {"armed": False, "done": threading.Event()}
    slow_edit = {"armed": False, "done": threading.Event()}
    inspect0, variants0, edit0, views0 = lab.inspect, lab.variants, lab.edit, lab._variant_views

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
        """The worker views of a facet_variants render (each photo's pass) and each sheet's layout (observed only)."""
        views, comp = views0(*args, **kwargs)
        st["variant_views"].append({
            "passes": {v["name"]: list(v.get("passes") or []) for v in views},
            "photos": {pid: {"layout": list(c["layout"]), "shape": list(c["shape"]), "what": c.get("what")}
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
    lab.inspect, lab.variants, lab.edit, lab._variant_views = inspect, variants, edit_fn, variant_views

    def settle(done: threading.Event, timeout_s: float) -> None:
        """A timed-out call's thread has finished and released the scene lock."""
        done.wait(timeout_s)
        t = time.perf_counter()
        while lab.lock.locked() and time.perf_counter() - t < 30:
            time.sleep(0.05)

    def edit(parent, edits, reset=False, judged=None, photos=None):
        return {"parent_revision": parent, "reset_layout": reset, "edits": edits, "judgement": judged,
                "note": CONTROL, "apply": True, "photos": photos}

    def variant_rev(label):
        return next(i["revision"] for i in lab.state["variants"][-1]["items"] if i["label"] == label)

    def head_rev():
        return lab.head_record()["revision"]

    def depth_control(pid: str, d: float, y: float, dt: float, off_px: float = 0.0, side: int = 1) -> dict:
        """A TOOL TEST depth reading of the head: the native pixel where photo pid shows the point (d, y, t = the
        model's t there + dt) of the side's temple, placed as the lab builds it (the spec's hinge opening) and opened
        by the photo's pose, moved off_px perpendicular to the image of its depth line; and what the reading must
        return: that t (the exact inverse), the model's t, the px per mm of t (a finite difference) and off_px."""
        rev = head_rev()
        tb, rid = lab.build(rev), lab.rid(pid)
        cam, frame, pose = lab.basis.camera(rid), lab.basis.frame, lab.basis.pose(rid)
        h = tb.spec.hinge
        deg = float(h.get("opening_deg") or 0.0)
        opening = (deg, (abs(float(h["axis_xz_mm"][0])), float(h["axis_xz_mm"][1]))) if deg else None
        x_in = float(np.asarray(tb.spec.inner(d), float))
        model = float(G.surface_at(tb, np.array([[d, y]], float))[0][0]) - x_in
        t = model + dt

        def uv(tt):
            return project_mm(PO.chart_to_posed([[d, y, x_in + tt]], side, float(h["z_mm"]), pose, opening), cam,
                              frame)[0]
        e = uv(t + 0.01) - uv(t - 0.01)
        at = uv(t) + off_px * np.array([-e[1], e[0]]) / float(np.hypot(*e))
        return {"arg": {"photo": pid, "d_mm": d, "y_mm": y, "uv": [round(float(at[0]), 3), round(float(at[1]), 3)]},
                "expect": {"photo": pid, "revision": rev, "side": side, "d_mm": d, "y_mm": y, "t_mm": t,
                           "model_t_mm": model, "px_per_mm": float(np.hypot(*e)) / 0.02, "miss_px": off_px}}

    def compare_points():
        """The default set with points_px at 05 and a depth reading at B2, which that set leaves out (its reading
        comes under "depths"): the model's own surface point, so t reads the model's t."""
        c = depth_control(DEPTH_PHOTO, *DEPTH_AT_R0, 0.0)
        st["depths"]["compare_points"] = [c["expect"]]
        return R.tool_call("photo_compare", {"photos": None, "crop": None, "points_px": [
            {"photo": "05", "points": POINTS_05}], "depths": [c["arg"]]})

    def compare_black():
        """B2 and B3, points_px at B2, two depth readings at B2 of H3's crease corner Q: on its depth line at the
        model's t + DEPTH_DT and DEPTH_OFF_PX off it."""
        d, y = (float(v) for v in lab.spec(head_rev())["nodes"][DEPTH_NODE][:2])
        rows = [depth_control(DEPTH_PHOTO, d, y, DEPTH_DT, off_px=o) for o in (0.0, DEPTH_OFF_PX)]
        st["depths"]["compare_black"] = [r["expect"] for r in rows]
        also = depth_control(CORNER_ALSO, d, y, DEPTH_DT)               # the same point in B1 (charted, square-on)
        corner = {**rows[0]["arg"], "d_mm": d + CORNER_DD, "also": [{"photo": CORNER_ALSO, "uv": also["arg"]["uv"]}]}
        st["corner"] = {"d_mm": d, "y_mm": y, "t_mm": rows[0]["expect"]["t_mm"], "arg": corner}
        return R.tool_call("photo_compare", {"photos": BLACK_COMPARE, "crop": None, "points_px": [
            {"photo": "B2", "points": POINTS_B2}], "depths": [r["arg"] for r in rows] + [corner]})

    def inspect_roof():
        """facet_inspect on H3 (a roof whose PT, QT, PB, QB are solved): after the context guard."""
        st["roof_revision"] = head_rev()
        return R.tool_call("facet_inspect", {})

    def arm_slow():
        slow["armed"] = True
        tools["facet_inspect"].timeout_seconds = SHORT_TIMEOUT_S
        return R.tool_call("facet_inspect", {})

    def busy():
        tools["facet_inspect"].timeout_seconds = timeout0
        return R.tool_call("photo_image", {"photo": "05", "crop": None})

    def edit_h1():
        """H1, judging compare_points; its compare shows that compare's photos (the default set) and a black photo."""
        st["default_photos"] = list(S.seen_compares(lab.state)[-1]["photos"])
        st["h1_photos"] = st["default_photos"] + [EDIT_H1_BLACK]
        return R.tool_call("facet_edit", edit(0, head_edits("H1", r0)[0], judged=judgement(lab),
                                              photos=st["h1_photos"]))

    def variants_call():
        st["h1"], st["h1_revision"] = lab.s.head["id"], head_rev()
        return R.tool_call("facet_variants", {"parent_revision": st["h1_revision"], "variants": variant_edits(r0),
                                              "photos": VARIANT_PHOTOS, "judgement": judgement(lab, best=st["h1"])},
                           counted=GUARD_COUNT)

    def edit_margins():
        """HM (after the context guard), judging edit_h2's compare: its receipt carries margins and solved."""
        edits, reset = head_edits("HM", r0)
        return R.tool_call("facet_edit", edit(head_rev(), edits, reset, judged=judgement(lab)))

    def edit_h3():
        settle(slow["done"], 60)
        st["h2"] = lab.s.head["id"]
        edits, reset = head_edits("H3", r0)
        return R.tool_call("facet_edit", edit(head_rev(), edits, reset))

    def edit_h4():
        st["h3"] = lab.s.head["id"]
        st["h3_revision"] = head_rev()
        edits, _ = head_edits("H4", r0)
        return R.tool_call("facet_edit", edit(head_rev(), edits[-3:-1], judged=judgement(lab, best=st["h3"])))

    def edit_slow():
        """H5 under a 3 s SDK timeout, starting 5 s late: E_TIMEOUT, then its thread applies and compares."""
        slow_edit["armed"] = True
        tools["facet_edit"].timeout_seconds = SHORT_TIMEOUT_S
        edits, _ = head_edits("H5", r0)
        return R.tool_call("facet_edit", edit(head_rev(), edits[-3:-1], judged=judgement(lab, best=st["h3"],
                                                                                         overall="worse")))

    def edit_h6():
        """A third distinct head; its judgement covers compare_all (H3), so the stuck finish's last three judgements
        are on H4, H3 and H6."""
        edits, _ = head_edits("H6", r0)
        return R.tool_call("facet_edit", edit(head_rev(), edits[-3:-1], judged=judgement(lab, best=st["h3"],
                                                                                         overall="same")))

    def restore_h3():
        """After the timed-out apply has finished: no judgement is due (its compare was never shown)."""
        settle(slow_edit["done"], 120)
        tools["facet_edit"].timeout_seconds = edit_timeout0
        st["unseen"] = [c["compare_id"] for c in lab.state["compares"] if c.get("unseen")]
        st["h5_revision"] = head_rev()
        return R.tool_call("facet_restore", {"checkpoint": st["h3"], "judgement": None})

    steps = [
        ("inspect", lambda: R.tool_call("facet_inspect", {})),
        ("photo_image", lambda: R.tool_call("photo_image", {"photo": "05", "crop": CROP_05})),
        ("photo_black", lambda: R.tool_call("photo_image", {"photo": "B2", "crop": None})),
        ("compare_points", compare_points),
        ("edit_unjudged", lambda: R.tool_call("facet_edit", edit(0, head_edits("H1", r0)[0]))),
        ("edit_h1", edit_h1),
        ("variants", variants_call),
        ("edit_h2", lambda: R.tool_call("facet_edit", edit(variant_rev(EDIT_H2_VARIANT), []))),
        ("edit_margins", edit_margins),
        ("finish_early", lambda: R.tool_call("facet_finish", {"status": "matched", "best_checkpoint": lab.s.head["id"],
                                                              "summary": CONTROL, "judgement": judgement(lab)})),
        ("inspect_slow", arm_slow),
        ("busy", busy),
        ("edit_h3", edit_h3),
        ("inspect_roof", inspect_roof),
        ("edit_h4", edit_h4),
        ("edit_slow", edit_slow),
        ("restore_h3", restore_h3),
        ("compare_black", compare_black),
        ("compare_all", lambda: R.tool_call("photo_compare", {"photos": lab.photo_ids(), "crop": "temple",
                                                              "points_px": None, "depths": None})),
        ("edit_h6", edit_h6),
        ("finish", lambda: R.tool_call("facet_finish", {"status": "stuck", "best_checkpoint": st["h3"],
                                                        "summary": CONTROL, "judgement": judgement(
                                                            lab, best=st["h3"], overall="worse")})),
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
    lab.inspect, lab.variants, lab.edit, lab._variant_views = inspect0, variants0, edit0, views0
    records = call_records(transport.creates, lab.state["images"])
    if len(records) == len(transport.creates) - 1:          # the accepted finish: its output is the final output
        fc = next(o for o in transport.replies[len(transport.creates) - 1].output if o["type"] == "function_call")
        text = result.get("final_output") or ""
        try:
            body_json = json.JSONDecoder().raw_decode(text)[0]
        except ValueError:
            body_json = {}
        records.append({"call_id": None, "tool": fc["name"], "request": None, "args_chars": len(fc["arguments"]),
                        "text_chars": len(text), "text": text, "status": body_json.get("status"),
                        "code": body_json.get("code"), "body": body_json, "images": []})
    by = {names[i]: rec for i, rec in enumerate(records)}
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
    first = transport.creates[0]
    first_content = [x for item in first["input"] for x in (item.get("content") or []) if isinstance(x, dict)]
    image_hashes = [image_identity(x["image_url"])[0] for x in first_content if x.get("type") == "input_image"]
    guard_req = next((n for n, body in enumerate(transport.creates)
                      if any(p.get("type") == "input_text" and p.get("text", "").startswith("[image removed")
                             for i in body["input"] if isinstance(i, dict) and i.get("type") == "function_call_output"
                             for p in _parts(i))), None)
    stubbed = sorted({rec["call_id"] for rec in records for body in transport.creates[guard_req:guard_req + 1]
                      for i in body["input"] if isinstance(i, dict) and i.get("call_id") == rec["call_id"]
                      and i.get("type") == "function_call_output"
                      for p in _parts(i) if p.get("type") == "input_text" and p["text"].startswith("[image removed")}) \
        if guard_req is not None else []
    expected_images = {"inspect": 2, "photo_image": 1, "photo_black": 1,
                       "compare_points": len(by["compare_points"]["body"]["photos"]),
                       "edit_h1": 2 + len(by["edit_h1"]["body"].get("photos") or {}), "variants": len(VARIANT_PHOTOS),
                       "compare_black": len(BLACK_COMPARE), "inspect_roof": 2,
                       "edit_margins": 2 + len(by["edit_margins"]["body"].get("photos") or {})}
    cw_refs = lab.colourway_refs()
    black_view = {pid: lab.basis.refs[ref].get("view_kind") for pid, ref in cw_refs.items()}

    def black_text_ok(text, pid) -> bool:
        return str(text).startswith(f"{black_view.get(pid)} black: rows photo/overlay/gloss-black IoU ")
    black_id = by["compare_black"]["body"].get("compare_id")
    black_cmp = next((c for c in lab.state["compares"] if c["compare_id"] == black_id), None)
    black_report = read(black_cmp["report"]) if black_cmp else {}
    black_rows = {pid: sorted(((black_report.get("photos") or {}).get(cw_refs.get(pid)) or {}).get("rows") or {})
                  for pid in BLACK_COMPARE}
    black_texts = {pid: str((by["compare_black"]["body"].get("photos") or {}).get(pid)) for pid in BLACK_COMPARE}
    gloss_from = (black_report.get("gains") or {}).get("gloss_black", {}).get("from")
    h1_texts = by["edit_h1"]["body"].get("photos") or {}
    # facet_variants at a target and a black photo: each photo's pass, the state record's crease lines per panel and
    # the crease-colour pixels inside each panel of the sheet the call returned
    vbody = by["variants"]["body"]
    vid = vbody.get("variants_id")
    vrec = next((v for v in lab.state["variants"] if v["id"] == vid), {})
    vviews = st["variant_views"][-1] if st["variant_views"] else {"passes": {}, "photos": {}}
    labels = [v["label"] for v in variant_edits(r0)]
    models = ["head"] + labels
    sheets = {}
    for pid in VARIANT_PHOTOS:
        path = lab.root / "work" / "temple_lab" / "variants" / str(vid) / f"sheet-{pid}.png"
        geo = vviews["photos"].get(pid)
        px = sheet_crease_px(path, geo["layout"], geo["shape"], 1 + len(models)) if geo and path.is_file() else None
        lines = (vrec.get("creases") or {}).get(pid) or {}
        sheets[pid] = {"path": str(path), "what": (geo or {}).get("what"), "record_models": sorted(lines),
                       "photo_panel_crease_px": px[0] if px else None,
                       "panels": {m: {"lines": lines.get(m), "crease_px": px[1 + i] if px else None}
                                  for i, m in enumerate(models)}}

    def sheet_ok(s) -> bool:
        panels = s["panels"].values()
        return (s["record_models"] == sorted(models)
                and all(isinstance(p["lines"], int) and isinstance(p["crease_px"], int)
                        and (p["lines"] > 0) == (p["crease_px"] > 0) for p in panels)
                and s["panels"]["head"]["lines"] > 0
                and len({p["crease_px"] for p in panels}) > 1)            # each panel draws its own model's lines

    # depth readings: the compare record (rounded to 0.001) and the text Astra read, against each control's inverse
    def near(a, b, tol) -> bool:
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= tol

    def depth_check(step: str, text) -> dict:
        exp = st["depths"].get(step) or []
        cid = by[step]["body"].get("compare_id")
        got = next((c.get("depths") or [] for c in lab.state["compares"] if c["compare_id"] == cid), [])
        got = [g for g in got if not g.get("corner")]                     # corner readings: corner_check
        txt = depth_rows(text)
        ok = bool(exp) and len(got) == len(txt) == len(exp) and all(
            g.get("photo") == e["photo"] and g.get("revision") == e["revision"] and g.get("side") == e["side"]
            and near(g.get("t_mm"), e["t_mm"], 0.005) and near(g.get("model_t_mm"), e["model_t_mm"], 0.005)
            and near(g.get("px_per_mm"), e["px_per_mm"], 0.01 * e["px_per_mm"])
            and near(g.get("miss_px"), e["miss_px"], 0.01)
            and x["d"] == e["d_mm"] and x["y"] == e["y_mm"] and near(x["t"], e["t_mm"], 0.006)
            and near(x["model"], e["model_t_mm"], 0.006)
            and near(x["ppm"], e["px_per_mm"], 0.06 + 0.01 * e["px_per_mm"]) and near(x["miss"], e["miss_px"], 0.06)
            and x["side"] == {1: "charted", -1: "mirrored"}[e["side"]]
            for e, g, x in zip(exp, got, txt))
        return {"ok": ok, "expected": exp, "recorded": got, "text": text}
    cp_body = by["compare_points"]["body"]
    cp_photos = cp_body.get("photos") or {}
    depths_points = depth_check("compare_points", (cp_body.get("depths") or {}).get(DEPTH_PHOTO))
    depths_black = depth_check("compare_black", black_texts["B2"].split(" | depths ", 1)[1]
                               if " | depths " in black_texts["B2"] else "")
    # the corner reading (also): the point found from B2 and B1 although its d was given 2 mm off, zero residuals
    cb_id = by["compare_black"]["body"].get("compare_id")
    corner_rec = next((r for c in lab.state["compares"] if c["compare_id"] == cb_id for r in (c.get("depths") or [])
                       if r.get("corner")), None)
    exp_c = st.get("corner") or {}
    cr = (corner_rec or {}).get("corner") or {}
    corner_ok = bool(cr) and near(cr.get("d_mm"), exp_c.get("d_mm"), 0.01) and near(cr.get("y_mm"),
                exp_c.get("y_mm"), 0.01) and near(cr.get("t_mm"), exp_c.get("t_mm"), 0.01) \
        and max(cr.get("res_px") or [99.0]) < 0.05 and (corner_rec or {}).get("also") == exp_c["arg"]["also"] \
        and f" with {CORNER_ALSO}: t " in black_texts["B2"] and "; the point the pixels show is at d " in black_texts["B2"]
    # crease shapes against the head: every built facet_edit (crease_vs_head exactly on the builds' own flips against
    # the head it faced, diff_vs_head exactly when its parent is not that head) and every variant item (against H1)
    edits_vs_head = []
    for i, r in enumerate(records):
        b = r["body"]
        if r["tool"] != "facet_edit" or r["status"] not in ("applied", "built"):
            continue
        head = st["edit_heads"].get(r["call_id"])
        flips = built_flips(lab.build(head), lab.build(b["revision"])) if head is not None else None
        edits_vs_head.append({
            "step": names[i], "head": head, "parent": b.get("parent"), "revision": b.get("revision"), "flips": flips,
            "crease_vs_head": b.get("crease_vs_head"), "diff_vs_head": b.get("diff_vs_head"),
            "ok": head is not None and flips_stated(b.get("crease_vs_head"), flips, f"r{head}: ")
                  and ("diff_vs_head" in b) == (b.get("parent") != head)})
    v_items = {v["label"]: v for v in vbody.get("variants") or []}
    v_head = vbody.get("head_revision")
    variant_flips = {lb: built_flips(lab.build(v_head), lab.build(v["revision"])) for lb, v in v_items.items()
                     if v_head is not None and "revision" in v}
    b2 = by["edit_h2"]["body"]
    h2_head = st["edit_heads"].get(by["edit_h2"]["call_id"])
    sunk = v_items.get(EDIT_H2_VARIANT, {}).get("revision")
    # facet_inspect on the roof H3: its solved nodes as "solve -> value" (the build's node table); they lie outside the
    # outline (y +-30 mm), so no receipt lists them as solved corners
    roof = st.get("roof_revision")
    ins = by["inspect_roof"]["body"]
    roof_nodes = lab.spec(roof)["nodes"] if roof is not None else {}
    roof_table = lab.build(roof).tables["nodes"] if roof is not None else {}
    roof_solved = sorted(n for n, (_d, _y, t) in roof_nodes.items() if t is None)
    shown = (ins.get("spec") or {}).get("nodes") or {}
    h3_receipt = by["edit_h3"]["body"].get("receipt") or {}
    roof_ok = (ins.get("status") == "ok" and roof is not None and roof == st.get("h3_revision")
               and (ins.get("head") or {}).get("revision") == roof and bool(roof_solved)
               and all((shown.get(n) or [None] * 3)[2] == f"solve -> {float(roof_table[n]['t']):.2f}"
                       for n in roof_solved)
               and all(isinstance((shown.get(n) or [None] * 3)[2], float) for n in roof_nodes if n not in roof_solved)
               and "solved" not in h3_receipt
               and not any("solved from" in ln for ln in (h3_receipt.get("lines") or [])
                           + ((ins.get("receipt") or {}).get("lines") or [])))
    h2_vs_head = None if None in (h2_head, sunk) else \
        f"r{h2_head}: " + G.diff_text(G.diff_specs(lab.spec(h2_head), lab.spec(sunk)))
    applied_flip_ok = (b2.get("status") == "applied" and b2.get("diff") == "no change" and sunk is not None
                       and b2.get("parent") == b2.get("revision") == sunk and h2_head == st.get("h1_revision")
                       and b2.get("diff_vs_head") == h2_vs_head and "~node R t " in str(h2_vs_head)
                       and built_flips(lab.build(h2_head), lab.build(sunk)) == FLIP_H2
                       and flips_stated(b2.get("crease_vs_head"), FLIP_H2, f"r{h2_head}: ")
                       and list(b2).index("crease_vs_head") < list(b2).index("receipt"))
    # the margins head: its receipt's margins are exactly the host's summary of its build against the head it faced
    # (B, solved from the crown, sets crown|logo), and its solved names B
    hm = by["edit_margins"]["body"]
    hm_head, hm_rev = st["edit_heads"].get(by["edit_margins"]["call_id"]), hm.get("revision")
    hm_receipt = hm.get("receipt") or {}
    hm_expect = None if None in (hm_head, hm_rev) else LB.margins_summary(
        LB.crease_margins(lab.build(hm_rev)), LB.MARGIN_ITEMS_MAX,
        [k for k, *_ in LB.flips(lab.build(hm_head), lab.build(hm_rev))])
    margins_ok = (hm.get("status") == "applied" and hm_expect is not None and hm_receipt.get("margins") == hm_expect
                  and hm_expect.startswith(f"{HM_SOLVED} solved: crown|logo ")
                  and str(hm_receipt.get("solved")).startswith(f"{HM_SOLVED} ")
                  and by["edit_margins"]["text_chars"] <= TEXT_BUDGET["facet_edit"])
    rows = lab.manifest["photos"]
    roles = [r.get("role", "target") for r in rows]
    final = json.loads((result.get("final_output") or "{}").split("\n", 1)[0] or "{}")
    request = read(root / "runs" / RUN_ID / "approval-request.json")["request"]
    sent = root / "runs" / RUN_ID / "instructions-sent.txt"
    over_budget = [(names[i], r["tool"], r["text_chars"]) for i, r in enumerate(records)
                   if r["tool"] in TEXT_BUDGET and r["text_chars"] > TEXT_BUDGET[r["tool"]]]
    slow_edit_call = by["edit_slow"]["call_id"]
    checks = {
        "completed_by_accepted_finish": result["status"] == "completed" and final.get("status") == "accepted",
        "no_request_after_the_accepted_finish": len(transport.creates) == len(steps),
        "missing_approval_refused_before_network": missing_approval_refused and not blocked_network.attempts
                                                   and not blocked_transport.constructions,
        "no_external_network": not network.attempts and not tripwire.constructions,
        "no_api_contract_violations": not transport.violations and not transport.unexpected,
        "every_tool_called": {r["tool"] for r in records} == set(TOOL_NAMES),
        "tool_choice_required_on_every_request": all(b.get("tool_choice") == "required" for b in transport.creates),
        "images_reach_the_next_request": all(
            sum(1 for im in by[n]["images"] if im[3] == "high") == k and by[n]["request"] == names.index(n) + 1
            for n, k in expected_images.items()),
        "e_judgement": by["edit_unjudged"]["code"] == "E_JUDGEMENT",
        "sdk_timeout_then_e_busy": by["inspect_slow"]["code"] == "E_TIMEOUT" and "timed out" in by["inspect_slow"][
            "text"] and bool(FOOTER.search(by["inspect_slow"]["text"])) and by["busy"]["code"] == "E_BUSY",
        "timed_out_apply_needs_no_judgement": by["edit_slow"]["code"] == "E_TIMEOUT"
                                              and by["restore_h3"]["status"] == "ok"
                                              and st.get("h5_revision") not in (None, st.get("h3_revision"))
                                              and [c["compare_id"] for c in lab.state["compares"]
                                                   if c["call_id"] == slow_edit_call] == st.get("unseen"),
        "text_budget_on_every_real_output": not over_budget,
        "instructions_sent_is_the_bound_text": sha(sent) == request["instructions_sha256"]
                                               and b"\r" not in sent.read_bytes(),
        "refused_finish_keeps_the_run_going": by["finish_early"]["code"] == "E_FINISH"
                                              and len(transport.creates) > names.index("finish_early") + 1,
        "context_guard_fired": bool((result.get("context_guard") or {}).get("epoch"))
                               and guard_req == names.index("variants") + 1
                               and stubbed == [by["compare_points"]["call_id"]],
        "variants_left_the_scene_unchanged": bool(st["fingerprints"]) and all(a == b for a, b in st["fingerprints"])
                                             and by["variants"]["body"].get("spread_ok") is True,
        "export_validates": by["finish"]["body"].get("validate_spec", {}).get("ok") is True,
        "top_photo_compare_has_the_outline_line": "| outline: common " in str(
            (by["compare_all"]["body"].get("photos") or {}).get("03")) and ">1 mm: d" in str(
            (by["compare_all"]["body"].get("photos") or {}).get("03")),
        "stuck_finish_accepted": by["finish"]["body"].get("finish") == "stuck",
        "every_result_has_the_footer": all(FOOTER.search(r["text"]) for r in records if r["status"] != "sdk_text"),
        "first_request_carries_every_photo": image_hashes == [p["sha256"] for p in rows]
                                             and roles == ["target"] * 6 + ["colourway"] * 3
                                             and [r["id"] for r in rows[6:]] == BLACK_IDS
                                             and [r.get("basis_ref") for r in rows[6:]] == BLACK_REFS
                                             and lab.compare_ids() == lab.photo_ids() + BLACK_IDS
                                             and lab.image_ids() == lab.compare_ids(),
        "photo_image_shows_a_black_photo": by["photo_black"]["status"] == "ok"
                                           and by["photo_black"]["body"].get("photo") == "B2"
                                           and by["photo_black"]["body"].get("colour") == "black",
        "edit_compare_takes_a_black_photo": by["edit_h1"]["status"] == "applied"
                                            and list(h1_texts) == st.get("h1_photos")
                                            and st.get("default_photos") == list(
                                                by["compare_points"]["body"].get("photos") or {})
                                            and EDIT_H1_BLACK not in st["default_photos"]
                                            and black_text_ok(h1_texts.get(EDIT_H1_BLACK), EDIT_H1_BLACK)
                                            and not any("black" in str(h1_texts.get(p))
                                                        for p in st["default_photos"]),
        "variants_at_a_black_photo_render_gloss_black": by["variants"]["status"] == "ok"
                                                        and vrec.get("photos") == VARIANT_PHOTOS
                                                        and vrec.get("rendered") == labels
                                                        and vviews["passes"] == VARIANT_PASSES
                                                        and str(sheets["B1"]["what"]).startswith("gloss-black render")
                                                        and str(sheets["05"]["what"]).startswith("native render"),
        "variant_sheets_draw_each_models_crease_lines": vrec.get("crease_occlusion") == "scene"
                                                        and "render" not in vbody
                                                        and all(sheet_ok(s) for s in sheets.values()),
        "black_compare_is_gloss_without_native": by["compare_black"]["status"] == "ok"
                                                 and black_cmp is not None and black_cmp["photos"] == BLACK_COMPARE
                                                 and all(set(black_rows[p]) == BLACK_ROWS for p in BLACK_COMPARE)
                                                 and all(black_text_ok(black_texts[p], p) for p in BLACK_COMPARE),
        "black_points_charted": "| points (1456,615) d " in black_texts["B2"]
                                and "; (5,5) no temple hit" in black_texts["B2"]
                                and "| points " not in black_texts["B3"],
        "gloss_gain_still_from_black_side": str(gloss_from).startswith("the dark variant photo black-side "),
        "depth_reading_outside_the_default_set": depths_points["ok"] and list(cp_body.get("depths") or {}) == [
            DEPTH_PHOTO] and DEPTH_PHOTO not in cp_photos and not any(" | depths " in str(v)
                                                                      for v in cp_photos.values()),
        "black_depths_read_t_px_per_mm_and_miss": depths_black["ok"] and len(depths_black["expected"]) == 2
                                                  and " | depths " not in black_texts["B3"],
        "corner_reading_from_two_photos": corner_ok,
        "applied_variant_states_the_flip_against_the_head": applied_flip_ok,
        "crease_vs_head_exactly_on_flips": all(e["ok"] for e in edits_vs_head)
                                           and [e["step"] for e in edits_vs_head] == EDITS_BUILT
                                           and [e["step"] for e in edits_vs_head if e["flips"]] == ["edit_h2"],
        "variant_items_state_their_flips": sorted(variant_flips) == sorted(labels)
                                           and all(flips_stated(v_items[lb].get("crease_vs_head"), f)
                                                   for lb, f in variant_flips.items())
                                           and [lb for lb in labels if variant_flips[lb]] == [EDIT_H2_VARIANT],
        "inspect_prints_solved_values": roof_ok,
        "edit_receipt_carries_margins": margins_ok,
        "exit_checkpoint_verified": (result.get("exit_checkpoint") or {}).get("verified") is True,
        "worker_refuses_a_path_spec": path_refusal["status"] == "refused",
        "worker_failed_build_leaves_the_scene": native_rollback,
    }
    durations = [round(b - a, 2) for a, b in zip(stamps, stamps[1:])]
    report = {"label": LABEL, "checks": checks, "passed": all(checks.values()), "rig": rig, "paid_calls": 0,
              "prepared": prepared, "seconds": {"prepare": round(t_prepare, 1), "run": round(t_run, 1)},
              "result": {k: result.get(k) for k in ("status", "error", "requests", "turns_used", "tool_calls",
                                                    "finish_notes", "context_guard", "exit_checkpoint", "settings",
                                                    "final_output")},
              "steps": [{"step": names[i], "tool": r["tool"], "status": r["status"], "code": r["code"],
                         "text_chars": r["text_chars"], "images": [im[:3] for im in r["images"]],
                         "seconds_until_next_request": durations[i] if i < len(durations) else None}
                        for i, r in enumerate(records)],
              "context_guard": {"request": guard_req, "stubbed_call_ids": stubbed},
              "black_compare": {"compare_id": black_id, "rows": black_rows, "texts": black_texts,
                                "gloss_black_gain_from": gloss_from},
              "variants_sheets": {"variants_id": vid, "passes": vviews["passes"],
                                  "crease_occlusion": vrec.get("crease_occlusion"), "sheets": sheets},
              "depth_readings": {"compare_points": depths_points, "compare_black": depths_black,
                                 "corner": {"expected": exp_c, "recorded": corner_rec}},
              "crease_vs_head": {"edits": edits_vs_head,
                                 "variants": {lb: {"revision": v.get("revision"), "flips": variant_flips.get(lb),
                                                   "crease_vs_head": v.get("crease_vs_head"),
                                                   "solved": v.get("solved")} for lb, v in v_items.items()}},
              "inspect_roof": {"revision": roof, "solved": roof_solved, "nodes": shown},
              "margins_head": {"revision": hm_rev, "head": hm_head, "receipt": hm_receipt, "expected": hm_expect},
              "text_budget": {"limits": TEXT_BUDGET, "over": over_budget,
                              "largest": {t: max((r["text_chars"] for r in records if r["tool"] == t), default=None)
                                          for t in TEXT_BUDGET}},
              "unseen_compares": st.get("unseen"),
              "variants_fingerprints": st["fingerprints"], "violations": transport.violations,
              "network_attempts": network.attempts, "transport_constructions": tripwire.constructions,
              "request_count": len(transport.creates), "first_request": first_request(first),
              "tool_outputs": [{"tool": r["tool"], "status": r["status"], "text_chars": r["text_chars"],
                                "args_chars": r["args_chars"], "images": [im[:3] for im in r["images"]]}
                               for r in records],
              "outputs": {names[i]: r["body"] for i, r in enumerate(records)},
              "native_refusals": [path_refusal, bad_refusal]}
    write_json_atomic(root / "rehearsal.json", report)
    if not report["passed"]:
        raise AssertionError({k: v for k, v in checks.items() if not v})
    return {"passed": True, "report": str(root / "rehearsal.json"), "paid_calls": 0}


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
