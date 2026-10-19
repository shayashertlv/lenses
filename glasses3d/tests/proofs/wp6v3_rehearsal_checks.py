"""Helpers of the v3 T5 rehearsal (tests/proofs/astra_temple_lab.py): the TOOL TEST controls (pixel picks made by
projecting chart points through the lab's own cameras, so a reading has an exact answer), the request-body readers
(every tool call's output as the next request carried it; the first request), the crease-flip reading of two builds,
and a historical context-filter oracle (expected_stubs). Host only: no Blender, no network.

Controls (SCRIPTED OFFLINE TOOL TEST, never Astra or product output): a control point is (d, y, dt): the chart point
(d, y, X = revision 0's base surface X + dt) of the charted (+X) temple. Its pick in a photo is that point placed as
the lab builds it (the spec's hinge opening) on the temple whose outer face the camera sees there (readings._facing),
opened by the photo's temple pose and projected by its camera (camera.project_mm): the inverse photo_read must
recover. A crossing's two traced edges are the projections of points on the chart segments from the point toward two
other control points (a straight chart line is a straight image line under the pinhole), so the two image lines
cross exactly at the point's pixel, at whatever angle the photo shows them (shallow in the side views when both
segments run near the temple's length: W_SHALLOW_CROSSING, and W_ALONG_LINE beside their slide line).

Historical context-filter decision (retired; WP6_V3 §6, review task v3p3-t5): at its one epoch the
guard may replace an old tool image by a text stub only where the image shows a SUPERSEDED MODEL or backs NO STORED
READING:
- compare panels, zoom grids and variant sheets (images of a model: a render, the model's outline and its own crease
  lines) of a result older than the latest CONTEXT_KEEP_LATEST compare / variants results and not a compare of
  best_checkpoint's revision. A zoom's photo panels are evidence, but of the photo, which photo_image or a new zoom
  shows again at native scale for one cheap call; its render and edge panels are a superseded model's, judged when
  shown (the judgement's text stays in the context). Kept: the latest results and every compare of the best revision
  (what Astra compares a new head against);
- a pick sheet only once its reading was read again or dropped: the sheet of a STORED reading is the evidence of which
  feature each pick is on (FINDINGS §0.6: Astra misread which feature a pick was on; v3 P3), and it stays for the run.
  The replaced reading's numbers stay in its photo_read text;
- never: photo_image views (photo evidence), charts (each revision's layout and labels, about 600 tokens), the first
  request's photos (the loop never touches them).
expected_stubs retains that old oracle for saved evidence. The current rehearsal tests append-only history
and fresh-conversation renewal; no live lab or loop rewrites these images.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re

import numpy as np
from PIL import Image

from glasses3d.agent.temple_lab import readings as RD
from glasses3d.g3d.temple.spec import parse
from glasses3d.g3d_host import pose as PO
from glasses3d.g3d_host.camera import project_mm

KEEP_LATEST = 2                   # frozen historical oracle; no live lab setting
MODEL_KINDS = ("compare", "variants")     # Lab._image kinds of images that show a model (zoom grids: compare + zoom)
STUB_RE = re.compile(r"^\[image removed by the host to save context: ")


# --------------------------------------------------------------------------------------------- control picks
def opening(hinge: dict) -> tuple | None:
    """(deg, (|axis x|, axis z)) of a spec hinge's opening (pose.chart_to_posed's form); None at opening 0."""
    deg = float(hinge.get("opening_deg") or 0.0)
    return (deg, (abs(float(hinge["axis_xz_mm"][0])), float(hinge["axis_xz_mm"][1]))) if deg else None


def chart_point(spec: dict, d: float, y: float, dt: float) -> np.ndarray:
    """The control chart point (d, y, X): ``spec``'s base surface X at (d, y) plus dt mm."""
    return np.array([d, y, float(parse(spec).x_base(d, y)) + dt], float)


def pixel(lab, spec: dict, pid: str, p) -> np.ndarray:
    """Native pixel of chart point p in photo pid (module doc: the temple the camera sees there, the spec's hinge)."""
    bs, h = lab.basis, spec["hinge"]
    rid = lab.rid(pid)
    cam, frame, pose = bs.camera(rid), bs.frame, bs.pose(rid)
    op, z = opening(h), float(h["z_mm"])
    side = RD._facing(cam, frame, pose, np.asarray(p, float), z, op)[0]
    return project_mm(PO.chart_to_posed([np.asarray(p, float)], side, z, pose, op), cam, frame)[0]


def _px(uv) -> list:
    return [round(float(v), 3) for v in uv]


def corner_point(lab, spec: dict, name: str, p, photos: list, shift: dict | None = None) -> dict:
    """photo_read's point for a corner: one pick per photo at p's pixel (``shift`` {photo: [du, dv]} moves a pick off
    it: a pick on another feature)."""
    picks = []
    for pid in photos:
        uv = pixel(lab, spec, pid, p) + np.asarray((shift or {}).get(pid, (0.0, 0.0)), float)
        picks.append({"photo": pid, "uv": _px(uv), "edges": None, "sd_px": None})
    return {"name": name, "kind": "corner", "picks": picks}


def crossing_point(lab, spec: dict, name: str, p, toward: list, photos: list, along: tuple) -> dict:
    """photo_read's point for a crossing: per photo two traced edges, the projections of p + s (q - p) for s in
    ``along`` on the segments toward each of the two chart points ``toward`` (module doc)."""
    p = np.asarray(p, float)
    picks = []
    for pid in photos:
        edges = [[_px(pixel(lab, spec, pid, p + s * (np.asarray(q, float) - p))) for s in along] for q in toward]
        picks.append({"photo": pid, "uv": None, "edges": edges, "sd_px": None})
    return {"name": name, "kind": "crossing", "picks": picks}


def box_around(lab, spec: dict, pid: str, p, w: int, h: int) -> list[int]:
    """A native box [x0, y0, x1, y1] of w x h px centred on p's pixel, moved inside the photo."""
    H, W = lab.basis.shape(lab.rid(pid))
    u, v = pixel(lab, spec, pid, p)
    x0 = int(min(max(round(u - w / 2), 0), W - w))
    y0 = int(min(max(round(v - h / 2), 0), H - h))
    return [x0, y0, x0 + w, y0 + h]


# --------------------------------------------------------------------------------------------- request bodies
def _parts(item) -> list:
    out = item.get("output")
    return out if isinstance(out, list) else [{"type": "input_text", "text": out}] if isinstance(out, str) else []


def image_meta(url: str) -> tuple[str, tuple[int, int]]:
    """(sha256 of the decoded bytes, (W, H)) of a data-URL image."""
    raw = base64.b64decode(url.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as im:
        return hashlib.sha256(raw).hexdigest(), im.size


def call_records(creates: list, images_index: dict, record_kind) -> list:
    """Every tool call in order, from the first request that carries its output: name, arguments, the output's text
    and images ([W, H, record_kind of the lab's index entry, detail, sha256]; "unknown" for an image the lab did not
    register), the request index it appeared in, its stubs (text parts the context guard put in place of images)."""
    seen, out = set(), []
    for n, body in enumerate(creates):
        items = [i for i in body.get("input", []) if isinstance(i, dict)]
        calls = {i["call_id"]: i for i in items if i.get("type") == "function_call"}
        for item in items:
            if item.get("type") != "function_call_output" or item["call_id"] in seen:
                continue
            seen.add(item["call_id"])
            fc = calls.get(item["call_id"], {})
            texts = [p.get("text", "") for p in _parts(item) if p.get("type") == "input_text"]
            text = "".join(t for t in texts if not STUB_RE.match(t))
            imgs = []
            for p in _parts(item):
                if p.get("type") == "input_image":
                    digest, (w, h) = image_meta(p["image_url"])
                    rec = images_index.get(digest)
                    imgs.append([w, h, record_kind(rec) if rec else "unknown", p.get("detail"), digest])
            try:
                body_json, _end = json.JSONDecoder().raw_decode(text)
            except ValueError:
                body_json = {"status": "sdk_text"}
            out.append({"call_id": item["call_id"], "tool": fc.get("name"), "request": n,
                        "args_chars": len(fc.get("arguments") or ""), "text_chars": len(text), "text": text,
                        "status": body_json.get("status"), "code": body_json.get("code"), "body": body_json,
                        "images": imgs, "stubs": [t for t in texts if STUB_RE.match(t)]})
    return out


def first_request(body: dict) -> dict:
    """The first request's text characters (input, instructions, tool schemas; images stripped) and image sizes."""
    sizes = []

    def strip(v):
        if isinstance(v, dict):
            if v.get("type") == "input_image":
                sizes.append(list(image_meta(v["image_url"])[1]))
                return {"type": "input_image"}
            return {k: strip(x) for k, x in v.items()}
        if isinstance(v, list):
            return [strip(x) for x in v]
        return v
    kept = strip({k: body.get(k) for k in ("input", "instructions", "tools")})
    return {"text_chars": len(json.dumps(kept, ensure_ascii=False, separators=(",", ":"))), "image_sizes": sizes}


def first_request_images(body: dict) -> list[str]:
    """sha256 of the first request's own images (the user message: the photos), in order."""
    content = [x for item in body["input"] for x in (item.get("content") or []) if isinstance(x, dict)]
    return [image_meta(x["image_url"])[0] for x in content if x.get("type") == "input_image"]


def stubbed_in(body: dict) -> dict:
    """{call_id: [stub texts]} of the function_call_outputs of one request whose images the guard replaced."""
    out = {}
    for i in body.get("input", []):
        if isinstance(i, dict) and i.get("type") == "function_call_output":
            stubs = [p["text"] for p in _parts(i) if p.get("type") == "input_text" and STUB_RE.match(p["text"])]
            if stubs:
                out[i["call_id"]] = stubs
    return out


# --------------------------------------------------------------------------------------------- creases
def built_flips(ref, tb) -> list[tuple[str, str, str]]:
    """The facet pairs both builds have whose built crease (the build's own crease table, kind "crease") is a ridge
    in one and a valley in the other: [(pair, shape in ref, shape in tb)] (the rehearsal's own reading of the tables;
    lab.crease_changes states them)."""
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


# --------------------------------------------------------------------------------------------- the guard's decision
def expected_stubs(snapshot: dict) -> dict:
    """The guard's decision (module doc) on a snapshot taken when it chose: {sha256: why} of the images it may stub
    among ``snapshot["shas"]`` (the tool-output images of that request). ``snapshot``: {shas, images (the lab's image
    index: {sha: {kind, ref, photo?, revision?, zoom?, reading?}}), results [[ref, seq]] (the compare and variants
    results Astra was shown), best_compares [compare ids of best_checkpoint's revision], stored {reading name: its
    stored id}}."""
    latest = {ref for ref, _seq in sorted(snapshot["results"], key=lambda r: r[1])[-KEEP_LATEST:]}
    keep = latest | set(snapshot["best_compares"])
    out = {}
    for sha in snapshot["shas"]:
        rec = snapshot["images"].get(sha)
        if rec is None:
            continue
        if rec["kind"] in MODEL_KINDS and rec["ref"] not in keep:
            out[sha] = "superseded model" + (" (zoom grid)" if rec.get("zoom") else "")
        elif rec["kind"] == "reading" and snapshot["stored"].get(rec.get("reading")) != \
                f"{rec['ref']}-{rec.get('reading')}":
            out[sha] = "pick sheet of a reading " + ("read again" if rec.get("reading") in snapshot["stored"]
                                                     else "dropped")
    return out
