"""Comparison-bound visible crease choices. No appearance score and no bypass of builder checks."""
import copy
import hashlib
import json
import math
import time
import numpy as np
from scipy.spatial import cKDTree

from glasses3d.agent.procs import write_json_atomic
from glasses3d.agent.tools import arview, look
from glasses3d.g3d.temple import build as B, fit as FIT
from glasses3d.g3d.temple.spec import VISIBLE_ANGLE_TOL_DEG
from glasses3d.g3d_host import compare as C
from . import geometry as G, state as S, crease_ar as AR
from .recovery import restore_head

SCHEMA = "g3d.crease_angle_comparison/1"
MIN_ALTERNATIVES, MAX_ALTERNATIVES = 3, 5
ANGLE_TOLERANCE_DEG = VISIBLE_ANGLE_TOL_DEG
COMPARE_WORK_DEADLINE_S = 360


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def geometry_digest(tb):
    return digest({"vertices": tb.V.tolist(), "polygons": [list(map(int, p)) for p in tb.polys], "regions": tb.poly_region,
                   "normals": tb.normals.tolist()})


def geometry_effects(before, after):
    """Name every changed builder surface, including closure/ramp regions; vertex distance is not wall thickness."""
    def groups(tb):
        out = {}
        for poly, region, kind in zip(tb.polys, tb.poly_region, tb.poly_kind):
            out.setdefault((region, kind), []).extend(tb.V[poly].tolist())
        return {key: np.unique(np.asarray(points), axis=0) for key, points in out.items()}
    a, b = groups(before), groups(after)
    rows = []
    for region, kind in sorted(set(a) | set(b)):
        av, bv = a.get((region, kind)), b.get((region, kind))
        same = av is not None and bv is not None and np.array_equal(av, bv)
        distance = None if av is None or bv is None else max(float(cKDTree(av).query(bv)[0].max()),
                                                            float(cKDTree(bv).query(av)[0].max()))
        rows.append({"region": region, "surface": kind, "unchanged": same,
                     "before_vertices": len(av) if av is not None else 0,
                     "after_vertices": len(bv) if bv is not None else 0,
                     "max_nearest_vertex_mm": distance})
    return {**G.geometry_delta(before, after), "surfaces": rows,
            "distance_method": "Symmetric nearest-vertex distance by builder region and surface; full thickness is reported separately.",
            "decal_carry": "Per-object movement, affected regions and any crease warnings are in export.build.sides."}


def binding(lab, parent):
    lab.verify_inputs()
    head = lab.head_record()
    if head is None:
        raise S.LabRefusal("E_STATE", "an applied revision is required")
    # Re-read the frozen evidence, not the cached Basis: edits to cameras, mattes, or its catalog must invalidate.
    bs = C.Basis(lab.basis.dir, lab.root)
    lab._basis = bs  # AR photo rows, projection and masks must consume exactly the verified camera catalog.
    return {"parent_revision": parent, "design_sha256": G.design_sha256(lab.design(parent)),
            "parent_fit_sha256": digest(lab.fit_record(parent)),
            "head_revision": head["revision"], "head_fingerprint": lab.s.head["fingerprint"],
            "start_fingerprint": lab.state["start_fingerprint"], "readings_sha256": digest(lab.readings()),
            "lab_manifest_sha256": arview.sha256(lab.root / "inputs/lab.json"),
            "assets": [{"path": a["path"], "sha256": arview.sha256(lab.root / "inputs" / a["path"])}
                       for a in lab.manifest.get("assets", [])],
            "basis_sha256": arview.sha256(bs.dir / "basis.json"),
            "cameras_sha256": arview.sha256(bs.dir / C.CAMERAS_JSON),
            "cameras": {rid: {"camera": vars(bs.camera(rid)), "pose": bs.pose(rid),
                              "photo_sha256": arview.sha256(bs.photo_path(rid))}
                        for rid in bs.usable_refs()}}


def declaration(base, pair, moving_facet, shape, degrees, comparison_id, comparison_sha256, reason):
    design = copy.deepcopy(base)
    design["creases"] = [c for c in design.get("creases", []) if tuple(sorted(c["facets"])) != tuple(sorted(pair))]
    design["creases"].append({"facets": list(pair), "shape": shape, "visible_angle": {
        "degrees": float(degrees), "moving_facet": moving_facet, "comparison_id": comparison_id,
        "comparison_sha256": comparison_sha256, "reason": reason}})
    return design


def actual_angle(tb, pair):
    rec = next((r for r in tb.tables["creases"] if r.get("kind") == "crease" and
                set(r["regions"]) == set(pair)), None)
    if rec is None:
        raise ValueError("the built model has no requested crease")
    return float(rec["mean_dihedral_deg"])


def comparison_summary(baseline, candidates, baseline_fit, pair):
    """Flat, short metric lines survive the generic text limiter without hiding any candidate behind sampling."""
    def number(value, *, sign=False):
        return f"{value:+.3g}" if sign and value is not None else f"{value:.3g}" if value is not None else "n/a"
    measured = next((c for c in (baseline_fit or {}).get("creases", []) if set(c["pair"]) == set(pair)), {})
    degree = measured.get("free_deg") if measured.get("free_deg") is not None else measured.get("deg")
    sd = measured.get("free_sd_deg") if measured.get("free_sd_deg") is not None else measured.get("sd_deg")
    state = {"open": "unresolved", "certain": "sign-certain", "flat": "flat within error"}.get(measured.get("state"), "unavailable")
    out = {"baseline": f"built {number(baseline.get('built_degrees'))}°; measurement {number(degree, sign=True)}±{number(sd)}° {state}; /baseline"}
    for index, item in enumerate(candidates):
        meta = (item.get("fit") or {}).get("model", {}).get("visible_angles", {})
        choices = meta.get("choices", [])
        moved = sorted({r["node"] for c in choices for r in c.get("changed_corners", [])})
        names = ",".join(moved)
        if len(names) > 14:
            names = moved[0][:9] + f"…+{len(moved)-1}" if moved else "none"
        residuals = [abs(r["residual_sd"]) for c in choices for r in c.get("reading_residuals", {}).values()
                     if r.get("residual_sd") is not None]
        surfaces = item.get("affected_geometry", {}).get("surfaces")
        changed = str(sum(not s["unchanged"] for s in surfaces)) if surfaces is not None else "n/a"
        out[item["label"]] = (f"built/requested {number(item.get('built_degrees'))}/{number(item['degrees'])}° {item['status']}; "
            f"wall{number(item.get('thickness', {}).get('min_mm'))}mm; localΔχ²{number(meta.get('local_cost_change'), sign=True)}; "
            f"max|r|{number(max(residuals) if residuals else None)}sd; moved {names or 'none'}; surfaces{changed}; /alternatives/{index}")
    for start in range(0, len(candidates), 3):
        out[f"exact_requested_degrees_{start//3+1}"] = "; ".join(
            f"{c['label']}={c['degrees']!r}" for c in candidates[start:start+3])
    out["details"] = "Pointers start at full_report /result; full readings, checks, geometry and decal movement remain there."
    out["metric_scope"] = "Wall=min thickness; local cost/residual=all visible choices; surfaces=changed region/surface groups."
    return out


def compare(lab, call_id, parent_revision, facets, moving_facet, shape, angles_deg, photos):
    parent = S.revision(lab.state, parent_revision)["id"]
    pair = G._pair(facets, 0)
    if "base" in pair or moving_facet not in pair or shape not in ("ridge", "valley"):
        raise S.LabRefusal("E_ARGS", "choose two facets, one moving facet of that pair, and ridge or valley")
    if (not isinstance(angles_deg, list) or not MIN_ALTERNATIVES <= len(angles_deg) <= MAX_ALTERNATIVES or
        any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or
            not FIT.MIN_CREASE_DEG <= x < 180 for x in angles_deg) or len(set(angles_deg)) != len(angles_deg)):
        raise S.LabRefusal("E_ARGS", f"choose {MIN_ALTERNATIVES}..{MAX_ALTERNATIVES} distinct absolute dihedral angles >= {FIT.MIN_CREASE_DEG:g} and <180 degrees")
    photos = lab._photos_arg(photos) or [lab.pid(r) for r in lab.basis.usable_refs()]
    validate_design(lab, lab.design(parent))
    source = binding(lab, parent)
    comparison_id = lab._next("crease")
    directory = lab.work / "crease_angles" / comparison_id
    directory.mkdir(parents=True, exist_ok=False)
    baseline_tb = lab.build(parent)
    base = lab.design(parent)
    rows = AR.photo_rows(lab, photos, directory)
    candidates, builds = [], {"baseline": baseline_tb}
    specs = [{"label": "baseline", "spec": lab.spec(parent)}]
    deadline = time.monotonic()+COMPARE_WORK_DEADLINE_S
    for index, degree in enumerate(angles_deg):
        label = f"angle-{index:02d}"
        preview = declaration(base, pair, moving_facet, shape, degree, comparison_id, "0"*64,
                              "Comparison preview only; no visual selection has been made.")
        fit, tb = lab._fit_build(preview, call_id, validate_visible=False)
        item = {"label": label, "degrees": degree}
        if tb is None:
            item.update(status="refused", refusal=fit)
        else:
            actual = actual_angle(tb, pair)
            if abs(actual-degree) > ANGLE_TOLERANCE_DEG:
                item.update(status="refused", refusal={"code": "E_ANGLE", "message": "built angle differs from request", "built_degrees": actual})
            else:
                item.update(status="built", built_degrees=actual, geometry_sha256=geometry_digest(tb),
                    receipt=lab._receipt(tb, fit, B.model_edges(tb)), hard_checks=tb.checks,
                    thickness=tb.tables["thickness"], fit=fit.to_dict(),
                    affected_geometry=geometry_effects(baseline_tb, tb))
                builds[label] = tb
                specs.append({"label": label, "spec": fit.spec})
        candidates.append(item)
    # Use the same original decal/material baseline as Lab._apply. Always restore the current head.
    before = dict(lab.s.head)
    try:
        restored = lab.s.restore_checkpoint(lab.state["start_checkpoint"], call_id=call_id+"-export-base")
        if restored.get("status") != "ok":
            raise ValueError("could not restore the private lab baseline for export")
        exported = lab.s.call_read("facet_crease_compare", "temple_variants_export", {
            "variants": specs, "out_dir": str(lab.root / "work/exports" / comparison_id)},
            deadline_s=max(1, deadline-time.monotonic()), call_id=call_id+"-exports")
    except BaseException as original:
        restore_head(lab.s, before, call_id+"-head-restore", original=original)
        raise
    else:
        restore_head(lab.s, before, call_id+"-head-restore")
    result = exported.get("result") or {}
    if exported.get("status") != "ok" or result.get("status") != "ok" or not result.get("unchanged"):
        raise ValueError("read-only candidate export did not complete unchanged")
    baseline = {"label": "baseline", "status": "baseline", "built_degrees": actual_angle(baseline_tb, pair)}
    for item in [baseline]+candidates:
        if item.get("status") == "refused":
            continue
        export = result["variants"].get(item["label"]) or {}
        if export.get("status") != "exported":
            item.update(status="refused", refusal=export)
            continue
        item["export"] = export
        try:
            cap = AR.capture(lab, export, rows, directory / item["label"], deadline)
        except (ValueError, OSError) as exc:
            item.update(status="capture_failed", refusal={"message": str(exc)})
            continue
        item["capture"] = cap
        item["status"] = "eligible" if cap.get("spec", {}).get("ok") is True else "hard_checks_failed"
    if not baseline.get("capture"):
        raise ValueError("baseline capture failed; no comparison is selectable")
    images, shown = AR.make_sheets(lab, comparison_id, moving_facet, rows, baseline, candidates, builds, directory,
                                  facets=pair)
    if binding(lab, parent) != source:
        raise S.LabRefusal("E_COMPARISON_STALE", "comparison inputs changed during capture")
    record = {"schema": SCHEMA, "comparison_id": comparison_id, "source_binding": source,
        "parent_revision": parent, "facets": list(pair), "moving_facet": moving_facet, "shape": shape,
        "angle_tolerance_deg": ANGLE_TOLERANCE_DEG, "baseline": baseline, "alternatives": candidates,
        "photos": shown, "images": images, "export_context": {"decal_source": lab.state["start_checkpoint"],
            "baseline_fingerprint": lab.state["start_fingerprint"], "head_restored": True},
        "informational_only": "No likeness gate. Refused or unrendered alternatives cannot be selected."}
    fit_record = lab.fit_record(parent) or {}
    record["summary"] = comparison_summary(baseline, candidates, fit_record.get("fit"), pair)
    path = directory / "comparison.json"
    write_json_atomic(path, record)
    with lab.state_lock:
        lab.state.setdefault("crease_comparisons", {})[comparison_id] = {
            "file": str(path.relative_to(lab.root)), "sha256": arview.sha256(path), "call_id": call_id,
            "delivered": False, "unseen": call_id in lab._unseen}
    lab.save()
    return {"status": "ok", **record, "comparison_sha256": arview.sha256(path)}


def evidence(lab, comparison_id):
    saved = lab.state.get("crease_comparisons", {}).get(comparison_id)
    if not saved or not saved.get("delivered") or saved.get("unseen"):
        raise S.LabRefusal("E_COMPARISON_UNSEEN", "the completed comparison must reach you before choosing its angle")
    path = arview.confined(lab.root, saved["file"])
    if arview.sha256(path) != saved["sha256"]:
        raise S.LabRefusal("E_COMPARISON_CHANGED", "comparison manifest bytes changed")
    rec = json.loads(path.read_text(encoding="utf8"))
    for item in [rec["baseline"]]+rec["alternatives"]:
        if not item.get("export"):
            continue
        export = item["export"]
        if arview.sha256(arview.confined(lab.root, export["path"])) != export["sha256"]:
            raise S.LabRefusal("E_COMPARISON_CHANGED", "comparison GLB bytes changed")
        if item.get("capture"):
            look.verified_report(lab.root, {**item["capture"], "export_sha256": export["sha256"]}, export["sha256"])
    for row in rec["photos"]:
        if arview.sha256(arview.confined(lab.root, row["photo_path"])) != row["photo_sha256"]:
            raise S.LabRefusal("E_COMPARISON_CHANGED", "comparison photo pixels changed")
        for image in row.get("sheets", []):
            if arview.sha256(arview.confined(lab.root, image["path"])) != image["sha256"]:
                raise S.LabRefusal("E_COMPARISON_CHANGED", "comparison sheet pixels changed")
    return rec, saved["sha256"]


def choose(lab, call_id, comparison_id, degrees, reason, apply, photos):
    rec, sha = evidence(lab, comparison_id)
    if binding(lab, rec["parent_revision"]) != rec["source_binding"]:
        raise S.LabRefusal("E_COMPARISON_STALE", "design, head, readings, photos, materials or frozen cameras changed; compare again")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise S.LabRefusal("E_ARGS", "give the visual reason in 1..1000 characters")
    item = next((c for c in rec["alternatives"] if c["degrees"] == degrees and c["status"] == "eligible"), None)
    if not item or not any(r.get("visible_pixels_by_variant", {}).get(item["label"], 0) for r in rec["photos"]):
        raise S.LabRefusal("E_COMPARISON_CHOICE", "choose one exact angle whose hard checks passed and whose named face was shown")
    parent = rec["parent_revision"]
    validate_design(lab, lab.design(parent))
    design = declaration(lab.design(parent), rec["facets"], rec["moving_facet"], rec["shape"], degrees,
                         comparison_id, sha, reason.strip())
    fit, tb = lab._fit_build(design, call_id, validate_visible=False)
    if tb is None:
        return fit
    if geometry_digest(tb) != item["geometry_sha256"]:
        raise S.LabRefusal("E_COMPARISON_CHANGED", "selected angle no longer builds the compared geometry")
    rid, _ = lab._register(design, parent=parent, kind="edit", label="visible angle", note=reason,
                           call_id=call_id, fit=fit)
    lab._cache_build(rid, tb)
    lab.save()
    out = lab._edit(call_id, rid, False, [], reason, None, apply, photos)
    return {**out, "visible_angle": {"comparison_id": comparison_id, "comparison_sha256": sha,
                                     "degrees": degrees, "reason": reason, "source_parent": parent}}


def validate_design(lab, design):
    """Every choice keeps its own evidence; one complete compared candidate must anchor the current design.

    A later whole-model comparison can include earlier choices. Generic edits cannot silently reuse that chain.
    Applying an unchanged chosen revision changes only the head, not its immutable inputs or comparison.
    """
    has_choices, anchored = False, False
    for crease in design.get("creases", []):
        v = crease.get("visible_angle")
        if not v:
            continue
        has_choices = True
        rec, sha = evidence(lab, v["comparison_id"])
        if (sha != v["comparison_sha256"] or set(rec["facets"]) != set(crease["facets"]) or
            rec["shape"] != crease["shape"] or rec["moving_facet"] != v["moving_facet"] or
            not any(c["degrees"] == v["degrees"] and c["status"] == "eligible" for c in rec["alternatives"])):
            raise S.LabRefusal("E_COMPARISON_CHOICE", "visible declaration does not match its completed comparison")
        expected = declaration(lab.design(rec["parent_revision"]), rec["facets"], rec["moving_facet"],
                               rec["shape"], v["degrees"], v["comparison_id"], sha, v["reason"])
        anchored |= G.design_sha256(design) == G.design_sha256(expected)
        current = binding(lab, rec["parent_revision"])
        # Applying/reapplying that exact revision changes the head. Its immutable source and external evidence do not.
        source = rec["source_binding"]
        ignored = {"head_revision", "head_fingerprint"}
        if {k: x for k, x in current.items() if k not in ignored} != {k: x for k, x in source.items() if k not in ignored}:
            raise S.LabRefusal("E_COMPARISON_STALE", "readings, assets or frozen cameras changed after this visible-angle comparison; compare again")
    if has_choices and not anchored:
        raise S.LabRefusal("E_COMPARISON_STALE", "the design changed after these visible-angle comparisons; remove the stale declaration and compare again")
