"""T5b (plan/WP6_FACETS.md §5): TOOL TEST of the facet edits' expressivity. The coding agent, acting as a tool user
(direct calls of the registered facet tools, no model), starts from revision 0 of a fresh package and reaches the
owner-preferred layout: facets F1-F4 with the same built crease shapes as the preferred spec cfc11127 converted with
g3d.temple.convert.v1_to_v2 (snap 0, exact). Never given to Astra; never a product result.

Stages, each one facet_edit (apply false) on revision 0, until the build has the preferred's facet set and crease
shapes (and no other crease):
  A  the layout only: every preferred node at the same chart (d, y) and absolute X (t converted to revision 0's
     inner face), the four facets with the same planes, the five crease shapes declared;
  B  A + the outline (top, bottom) spliced to the preferred's over the facet zone;
  C  B + the base (top, bottom) spliced to the preferred's absolute base surface over the facet zone.
The first matching stage is applied (facet_edit with no edits) and compared at every photo; its op count is the
result (target <= 40). Ops a stage cannot express are listed (hinge, chamfers and options have no edit op).

Writes data/wp6v2/lab/t5b/{report.json, TOOL_TEST_*.png (compare images, chart, section strip, labelled), job/}.
Run: glasses3d/.venv/Scripts/python.exe -m glasses3d.tests.proofs.wp6v2_expressivity [--rig photo_env]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import time
from pathlib import Path

from agents.tool_context import ToolContext
from PIL import Image, ImageDraw

from glasses3d.agent.offline import disable_sdk_tracing
from glasses3d.agent.temple_lab import geometry as G
from glasses3d.agent.temple_lab import state as S
from glasses3d.agent.temple_lab.cli import DEFAULT_RIG, LAB_DATA, prepare
from glasses3d.agent.temple_lab.lab import Lab, read, sha, write_json_atomic
from glasses3d.agent.temple_lab.tools import registry
from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple.convert import v1_to_v2
from glasses3d.g3d.temple.spec import canonical, parse, spec_sha256
from glasses3d.g3d_host import compare as C

G3D = Path(__file__).resolve().parents[2]
PREFERRED = G3D / "data/wp6/pose_arm_resume_20261010/distal_all_views/before/spec.json"
PREFERRED_SHA = "cfc11127d085c4a642f4a77fb2d6daae676470ffabe699af55b16fc49ec908ce"
OUT = LAB_DATA / "t5b"
ZONE_D = 85.0                  # the facet zone (the preferred's facets end at d 76.5-79.0)
BASE_STEP = 5.0                # base splice samples
OPS_TARGET = 40
LABEL = "TOOL TEST (scripted by the coding agent, never given to Astra): preferred layout from revision 0"
NOTE = "TOOL TEST: the preferred layout reached from revision 0 with facet_edit ops"


def call(tool, args: dict, call_id: str):
    ctx = ToolContext(context=None, tool_name=tool.name, tool_call_id=call_id, tool_arguments=json.dumps(args))
    out = asyncio.run(tool.on_invoke_tool(ctx, json.dumps(args)))
    if isinstance(out, str):
        raise RuntimeError(f"{tool.name}: {out}")
    head, footer = out[0].text.rsplit("\n", 1)
    return json.loads(head), out[1:]


def creases_of(tb) -> dict:
    return {"|".join(c["regions"]): c["shape"] for c in tb.tables["creases"] if c["kind"] == "crease"}


def node_edit(name, d, y, t) -> dict:
    mode = "mm" if isinstance(t, float) else ("base" if t == "base" else "solve")
    return {"op": "set_node", "name": name, "d_mm": round(d, 4), "y_mm": round(y, 4),
            "t": {"mode": mode, "mm": round(t, 4) if mode == "mm" else None}}


def stage_edits(r0: dict, pref: dict, target_creases: dict, stage: str) -> list:
    """The edits of a stage (module doc) on revision 0."""
    p0, pp = parse(r0), parse(pref)
    dz = r0["hinge"]["z_mm"] - pref["hinge"]["z_mm"]           # same world z: d_r0 = d_pref + dz
    edits = []
    if stage in ("B", "C"):
        for chain, pts in (("outline.top", pref["outline"]["top"]), ("outline.bottom", pref["outline"]["bottom"])):
            knots = [[round(d + dz, 4), round(v, 4)] for d, v in pts if d <= ZONE_D]
            edits.append({"op": "splice_chain", "chain": chain, "d_from_mm": min(0.0, knots[0][0]),
                          "d_to_mm": knots[-1][0], "points": knots})
    if stage == "C":
        ds = [i * BASE_STEP for i in range(int(ZONE_D / BASE_STEP) + 1)]
        for chain, tab in (("base.top", pp.t_top), ("base.bottom", pp.t_bot)):
            pts = [[round(d + dz, 4), round(float(pp.inner(d) + tab(d) - p0.inner(d + dz)), 4)] for d in ds]
            edits.append({"op": "splice_chain", "chain": chain, "d_from_mm": min(0.0, pts[0][0]),
                          "d_to_mm": pts[-1][0], "points": pts})
    for name, (d, y, t) in pref["nodes"].items():
        if isinstance(t, float):
            t = float(pp.inner(d) + t - p0.inner(d + dz))         # the same absolute X on revision 0's inner face
        edits.append(node_edit(name, d + dz, y, t))
    for label, f in pref["facets"].items():
        pl = f["plane"]
        plane = {"mode": "nodes", "crease": None, "rise_per_mm": None} if pl == "nodes" else \
            {"mode": "crease", "crease": list(pl["crease"]), "rise_per_mm": pl["rise_per_mm"]}
        fade = None if f["fade"] is None else {"d0_mm": f["fade"]["d0"] + dz, "d1_mm": f["fade"]["d1"] + dz}
        edits.append({"op": "set_facet", "label": label, "poly": list(f["poly"]), "plane": plane, "fade": fade})
    for pair, shape in target_creases.items():
        edits.append({"op": "set_crease", "facets": pair.split("|"), "shape": shape})
    return edits


def labelled(src: Path, dst: Path, title: str) -> str:
    im = Image.open(src).convert("RGB")
    band = Image.new("RGB", (im.width, 30), (255, 236, 160))
    ImageDraw.Draw(band).text((8, 7), title[:int(im.width / 7)], fill=(30, 30, 30), font=C._font(15))
    out = Image.new("RGB", (im.width, im.height + 30), (255, 255, 255))
    out.paste(band, (0, 0))
    out.paste(im, (0, 30))
    out.save(dst)
    return dst.name


def save_images(images, tag: str, titles: list) -> list:
    out = []
    for k, (im, title) in enumerate(zip(images, titles)):
        raw = base64.b64decode(im.image_url.split(",", 1)[1])
        tmp = OUT / f"_{tag}_{k}.png"
        Image.open(io.BytesIO(raw)).save(tmp)
        out.append(labelled(tmp, OUT / f"TOOL_TEST_{tag}_{k:02d}_{title.split(':')[0].replace(' ', '_')}.png",
                            f"{LABEL} | {title}"))
        tmp.unlink()
    return out


def main(rig: str = DEFAULT_RIG) -> dict:
    disable_sdk_tracing()
    if sha(PREFERRED) != PREFERRED_SHA:
        raise RuntimeError("the preferred spec's sha256 changed")
    if OUT.exists():
        raise RuntimeError(f"{OUT} exists; remove it first")
    OUT.mkdir(parents=True)
    t0 = time.perf_counter()
    prep = prepare(OUT / "job", rig=rig)
    t_prep = time.perf_counter() - t0
    pref = canonical(v1_to_v2(read(PREFERRED), snap_mm=0.0))
    tb_pref = build_temple(pref)
    target = creases_of(tb_pref)
    lab = Lab(OUT / "job")
    lab.start()
    report = {"label": LABEL, "rig": rig, "prepared": prep, "preferred": {
        "path": str(PREFERRED), "sha256": PREFERRED_SHA, "v2_sha256": spec_sha256(pref),
        "facets": sorted(pref["facets"]), "creases": target}, "stages": [], "paid_calls": 0}
    try:
        tl = {t.name: t for t in registry(lab)}
        r0 = lab.spec(0)
        chosen = None
        for stage in ("A", "B", "C"):
            edits = stage_edits(r0, pref, target, stage)
            t1 = time.perf_counter()
            body, _imgs = call(tl["facet_edit"], {"parent_revision": 0, "reset_layout": False, "edits": edits,
                                                  "judgement": None, "note": NOTE, "apply": False, "photos": None},
                               f"t5b-{stage}")
            row = {"stage": stage, "ops": len(edits), "ops_by_kind": {}, "status": body["status"],
                   "code": body.get("code"), "message": body.get("message"),
                   "seconds": round(time.perf_counter() - t1, 2)}
            for e in edits:
                row["ops_by_kind"][e["op"]] = row["ops_by_kind"].get(e["op"], 0) + 1
            if body["status"] == "built":
                tb = lab.build(body["revision"])
                built = creases_of(tb)
                row.update(revision=body["revision"], spec=body["spec"], receipt_lines=body["receipt"]["lines"],
                           facets=sorted(tb.spec.facets), creases=built,
                           same_facets=sorted(tb.spec.facets) == sorted(pref["facets"]), same_creases=built == target,
                           vs_preferred=G.geometry_delta(tb, tb_pref),
                           thickness_mm=[round(float(tb.thickness.min()), 2), round(float(tb.thickness.max()), 2)])
            report["stages"].append(row)
            if row.get("same_facets") and row.get("same_creases"):
                chosen = row
                break
        report["result"] = {"reached": chosen is not None, "stage": chosen and chosen["stage"],
                            "ops": chosen and chosen["ops"],
                            "within_target": bool(chosen and chosen["ops"] <= OPS_TARGET),
                            "ops_target": OPS_TARGET,
                            "not_expressible": {"hinge": "no op (revision 0 keeps the original's hinge)",
                                                "chamfers": "no op (the preferred has none, revision 0 none)",
                                                "options": "no op (snap_mm: the preferred 0, revision 0 the default "
                                                           "0.05; it does not change the layout)"},
                            "missing_ops": []}
        images = []
        if chosen:
            body, imgs = call(tl["facet_edit"], {"parent_revision": chosen["revision"], "reset_layout": False,
                                                 "edits": [], "judgement": None, "note": NOTE, "apply": True,
                                                 "photos": None}, "t5b-apply")
            report["apply"] = {k: body.get(k) for k in ("status", "revision", "checkpoint", "compare_id", "receipt",
                                                         "blender", "photos")}
            if body["status"] == "applied":
                titles = ["chart", "section strip"] + [f"photo {p}: compare" for p in body["photos"]]
                images += save_images(imgs, "apply", titles)
                cmp_all, imgs = call(tl["photo_compare"], {"photos": lab.photo_ids(), "crop": "temple",
                                                           "points_px": None}, "t5b-compare-all")
                report["compare_all"] = {k: cmp_all.get(k) for k in ("status", "compare_id", "photos")}
                images += save_images(imgs, "all", [f"photo {p}: compare" for p in cmp_all["photos"]])
            report["head_creases"] = creases_of(lab.build(S.head_by_fingerprint(
                lab.state, lab.s.head["fingerprint"])["revision"]))
        report["images"] = images
        report["seconds"] = {"prepare": round(t_prep, 1), "total": round(time.perf_counter() - t0, 1)}
    finally:
        lab.close()
    write_json_atomic(OUT / "report.json", report)
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rig", default=DEFAULT_RIG)
    r = main(ap.parse_args().rig)
    print(json.dumps({"result": r["result"], "stages": [{k: s.get(k) for k in ("stage", "ops", "status", "code",
                                                                                "same_facets", "same_creases")}
                                                         for s in r["stages"]], "images": r["images"]}, indent=1))
