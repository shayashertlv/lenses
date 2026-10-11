"""T5 (plan/WP6_FACETS.md §5): the $0 scripted rehearsal of a facet run. SCRIPTED OFFLINE TOOL TEST: the edits
below are arbitrary controls, never Astra or product output.

A fresh package (temple_lab.cli.prepare: a byte copy of the original Prada scene, the six target photos, the three
black photos B1-B3, the pose basis with B1, revision 0 applied) runs through agent/loop.run_astra with the pinned
Agents SDK, the real Blender worker and the seven facet tools, while api.openai.com is a MockTransport
(agent/replay.ScriptedOpenAI) whose replies are scripted
calls built from the live lab state; LoopbackOnly and the production-transport tripwire are active throughout. The
run settings are exactly a paid run's (tools.run_settings: required tool choice kept, final only on an accepted
facet_finish with K 4, the context guard at 240k counted input); only the approval is a synthetic OFFLINE ONLY record.

The script passes every tool and checks: the missing approval is refused before any request; every request has
tool_choice required; images of a tool output reach the next request; a facet_edit without the due judgement is
refused E_JUDGEMENT; a simulated stuck call (facet_inspect made to outlast a shortened SDK timeout) answers E_TIMEOUT
with the footer and makes the next call E_BUSY; a facet_edit timed out the same way still applies in its thread, and
its compare (never shown) demands no judgement of the next action; an early facet_finish is refused E_FINISH and the
run goes on; one scripted count above the guard threshold makes the next request replace the superseded compare images
by stubs; facet_variants leaves the worker's scene fingerprint unchanged; the accepted facet_finish ends the run
(status completed, no further request) with an export that validates; the §4.5 text budgets hold on every real
output; instructions-sent.txt is byte for byte the bound instructions; no network. The first request carries every
photo (6 target + 3 black); photo_image shows the reference-only B2; photo_compare on B1 (with points_px) shows photo /
overlay / gloss_black and no native row. It records every tool output's text and image sizes (the estimate's input)
and where the job's gloss_black gain came from.

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

from PIL import Image

from glasses3d.agent import approvals as A
from glasses3d.agent import replay as R
from glasses3d.agent.loop import run_astra
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire, disable_sdk_tracing
from glasses3d.agent.session_inputs import image_identity
from glasses3d.agent.temple_lab import state as S
from glasses3d.agent.temple_lab.cli import DEFAULT_RIG, make_spec, prepare
from glasses3d.agent.temple_lab.lab import read, sha, write_json_atomic
from glasses3d.agent.temple_lab.tools import CONTEXT_GUARD_TOKENS, TOOL_NAMES, run_settings
from glasses3d.g3d.temple.spec import parse

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
POINTS_B1 = [[700.0, 230.0], [1000.0, 220.0]]   # native px of black-side (1607 x 554), on the temple
BLACK_ROWS = {"photo", "overlay", "gloss_black"}  # a variant-colourway photo's compare rows (no native row)
FOOTER = re.compile(r"\nsettled \$\d+\.\d\d / cap \$\d+\.\d\d · context \S+ tokens$")
CONTROL = "SCRIPTED CONTROL, not a visual judgement"


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
    """The scripted heads (edits, reset_layout) on revision 0's chains: H1 one raised triangle; H3 a two-facet roof
    with a declared ridge (a new layout); H4 the roof with steeper sides; H5 between them (the timed-out apply)."""
    if name == "H1":
        return [set_node("P", 10.0, 4.0, "base"), set_node("Q", 40.0, 4.0, "base"),
                set_node("R", 25.0, -12.0, t_base(r0, 25.0, -12.0, 0.8)), facet("F1", ["P", "R", "Q"])], False
    rise = {"H3": -0.12, "H4": -0.2, "H5": -0.16}[name]
    return [set_node("P", 8.0, 1.0, t_base(r0, 8.0, 1.0, 0.8)), set_node("Q", 45.0, 0.0, t_base(r0, 45.0, 0.0, 0.8)),
            set_node("PT", 8.0, 30.0, None), set_node("QT", 45.0, 30.0, None),
            set_node("PB", 8.0, -30.0, None), set_node("QB", 45.0, -30.0, None),
            facet("U", ["P", "Q", "QT", "PT"], ["P", "Q"], rise), facet("D", ["Q", "P", "PB", "QB"], ["P", "Q"], rise),
            {"op": "set_crease", "facets": ["D", "U"], "shape": "ridge"}], True


def variant_edits(r0: dict) -> list:
    """Three depths of H1's node R (its parent is H1's revision)."""
    return [{"label": lb, "edits": [set_node("R", 25.0, -12.0, t_base(r0, 25.0, -12.0, dt))]}
            for lb, dt in (("shallow", 0.2), ("deep", 1.4), ("deeper", 2.0))]


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
    st: dict = {"fingerprints": []}
    slow = {"armed": False, "done": threading.Event()}
    slow_edit = {"armed": False, "done": threading.Event()}
    inspect0, variants0, edit0 = lab.inspect, lab.variants, lab.edit

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

    def edit_fn(*args, **kwargs):
        if not slow_edit["armed"]:
            return edit0(*args, **kwargs)
        slow_edit["armed"] = False
        try:
            time.sleep(SLOW_EDIT_S)
            return edit0(*args, **kwargs)
        finally:
            slow_edit["done"].set()
    lab.inspect, lab.variants, lab.edit = inspect, variants, edit_fn

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

    def arm_slow():
        slow["armed"] = True
        tools["facet_inspect"].timeout_seconds = SHORT_TIMEOUT_S
        return R.tool_call("facet_inspect", {})

    def busy():
        tools["facet_inspect"].timeout_seconds = timeout0
        return R.tool_call("photo_image", {"photo": "05", "crop": None})

    def variants_call():
        st["h1"], st["h1_revision"] = lab.s.head["id"], head_rev()
        return R.tool_call("facet_variants", {"parent_revision": st["h1_revision"], "variants": variant_edits(r0),
                                              "photos": ["05", "02"], "judgement": judgement(lab, best=st["h1"])},
                           counted=GUARD_COUNT)

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
        ("compare_points", lambda: R.tool_call("photo_compare", {"photos": None, "crop": None, "points_px": [
            {"photo": "05", "points": POINTS_05}]})),
        ("edit_unjudged", lambda: R.tool_call("facet_edit", edit(0, head_edits("H1", r0)[0]))),
        ("edit_h1", lambda: R.tool_call("facet_edit", edit(0, head_edits("H1", r0)[0], judged=judgement(lab)))),
        ("variants", variants_call),
        ("edit_h2", lambda: R.tool_call("facet_edit", edit(variant_rev("deep"), []))),
        ("finish_early", lambda: R.tool_call("facet_finish", {"status": "matched", "best_checkpoint": lab.s.head["id"],
                                                              "summary": CONTROL, "judgement": judgement(lab)})),
        ("inspect_slow", arm_slow),
        ("busy", busy),
        ("edit_h3", edit_h3),
        ("edit_h4", edit_h4),
        ("edit_slow", edit_slow),
        ("restore_h3", restore_h3),
        ("compare_black", lambda: R.tool_call("photo_compare", {"photos": ["B1"], "crop": None, "points_px": [
            {"photo": "B1", "points": POINTS_B1}]})),
        ("compare_all", lambda: R.tool_call("photo_compare", {"photos": lab.photo_ids(), "crop": "temple",
                                                              "points_px": None})),
        ("finish", lambda: R.tool_call("facet_finish", {"status": "matched", "best_checkpoint": st["h3"],
                                                        "summary": CONTROL, "judgement": judgement(
                                                            lab, best=st["h3"], overall="same")})),
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
    lab.inspect, lab.variants, lab.edit = inspect0, variants0, edit0
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
                       "edit_h1": 2 + len(by["edit_h1"]["body"].get("photos") or {}), "variants": 2,
                       "compare_black": 1}
    black_id = by["compare_black"]["body"].get("compare_id")
    black_cmp = next((c for c in lab.state["compares"] if c["compare_id"] == black_id), None)
    black_report = read(black_cmp["report"]) if black_cmp else {}
    black_rows = sorted(((black_report.get("photos") or {}).get("black-side") or {}).get("rows") or {})
    gloss_from = (black_report.get("gains") or {}).get("gloss_black", {}).get("from")
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
        "every_result_has_the_footer": all(FOOTER.search(r["text"]) for r in records if r["status"] != "sdk_text"),
        "first_request_carries_every_photo": image_hashes == [p["sha256"] for p in rows]
                                             and roles == ["target"] * 6 + ["colourway", "reference", "reference"]
                                             and [r["id"] for r in rows[6:]] == ["B1", "B2", "B3"],
        "photo_image_shows_the_reference_only_black_photo": by["photo_black"]["status"] == "ok"
                                                            and by["photo_black"]["body"].get("colour") == "black",
        "black_compare_is_gloss_without_native": by["compare_black"]["status"] == "ok"
                                                 and black_cmp is not None and black_cmp["photos"] == ["B1"]
                                                 and set(black_rows) == BLACK_ROWS
                                                 and str((by["compare_black"]["body"].get("photos") or {}).get(
                                                     "B1")).startswith("side black: rows photo/overlay/gloss-black"),
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
              "black_compare": {"compare_id": black_id, "rows": black_rows, "gloss_black_gain_from": gloss_from},
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
