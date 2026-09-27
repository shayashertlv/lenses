"""Independent evaluation of an asset as the wearer sees it, and the final status.

Protocol v2 (2026-09-26, after the owner's live verdicts). The evaluator judges what a wearer sees in the live
mirror: the exported GLB in the ACTUAL AR runtime, rendered on the harness's skin-toned stand-in for the face
(``solid`` fixture, the runtime's room lighting), at the mirror's own scale, in the three poses the wearer strikes
(front, turned 35 degrees, head rolled 25 degrees), beside the product photographs including the held-out view.
Shape evidence from the fitted photo cameras (photo | render | overlay) is shown as shape only. The evaluator does
not see EEVEE clay or textured previews, the asset-back inspection panel, or the author's rationale.

The v1 protocol showed the preview sheets and the asset-back panel on a checker fixture; on the first owner-judged
asset three of its five majors described those, not the asset. Its verdicts stay stored under ``evaluation/``.

Status: the evaluator's verdict is combined with the measured gates and the runtime flags; neither alone awards a
status, and ``accepted`` is awarded by a job only under a calibrated bar (``modeler.calibration``).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from bsa import archeck

from .observe import _label, match_tile, stack

STATUSES = ("accepted", "best_effort", "quality_unverified", "inconsistent_inputs", "execution_failed")
PROTOCOL = "modeler_evaluator_v2.2"   # v2 run 1 misread lens highlights and runtime-cut temples; v2.1 misread dark room reflections in a mirror lens

# Gates refit 2026-09-26 from the seven owner verdicts in the live try-on (data/modeler/calibration/owner_verdicts.jsonl):
# both owner accepts have lens outline <= 0.51 mm and front <= 0.51 mm; both Tripo rejects have lens outline >= 1.1 mm;
# both BSA rejects fail the runtime's temple continuity while beating every accept on silhouettes; the held-out contour
# rejected an owner-accepted asset at 1.24 mm and every reject scored better on it, so it is reported, not gated.
GATE_THRESHOLDS_MM = {"lens_outline_mean_mm": 0.8}
REPORT_ONLY_MM = {"front_contour_mean_mm": 1.0, "heldout_contour_mean_mm": 1.5}
PROVISIONAL_THRESHOLDS_MM = GATE_THRESHOLDS_MM | REPORT_ONLY_MM
THRESHOLD_PROVENANCE = ("gates refit 2026-09-26 from 10 owner verdicts (5 accept, 1 borderline, 4 reject): the visible lens outline (accepts "
                        "<= 0.75 mm, the Tripo rejects >= 1.1 mm) and runtime temple continuity are gates; the front contour is report-only "
                        "(the owner accepted the Oakley shield at 0.71 mm while rejected assets score 0.07-0.60) and so is the held-out contour")

# The wearer-proxy AR views: what the runtime shows a wearer, without the asset-back inspection panel.
WEARER_AR_VIEWS = ({"id": "front", "yaw_degrees": 0}, {"id": "angled", "yaw_degrees": 35}, {"id": "rolled", "roll_degrees": 25})
WEARER_BACKGROUND = "#cba68d"   # the harness's own skin-toned solid fixture

EVALUATOR_TASK = """You are the independent visual evaluator of a glasses asset for a live virtual try-on mirror.
The owner judges an asset by wearing it in the mirror at arm's length: the question is whether a wearer recognises
the product and sees nothing wrong. Judge exactly that. The AR renders are the actual runtime showing the asset in
the wearer's poses on a skin-toned stand-in for the face, at the mirror's own scale (the 'mirror' sheet) and
enlarged for inspection (the 'detail' sheet); compare them with the product photographs. Identity at mirror
distance: the outline and proportions of the front, the colour and finish of the frame, the lenses' tint, gradient
and transparency, temples present and complete to the ear, and hardware or branding where a wearer would see it.
Defects a wearer sees: wrong colour or finish, blotchy or noisy surfaces, jagged or torn edges, seams or halos
around the lenses, missing or truncated temples, wrong size relations, lenses that read opaque or absent. Do not
penalise cross-section shape, bevel width or millimetre-level silhouette detail that is invisible at that distance,
and do not judge colours or lens transparency from the photo-match sheet (an EEVEE preview: shape only).
Three things the runtime does that are NOT defects of the asset: (1) hard-edged patches on the lenses, bright
white ones on any lens and dark or coloured wedges with straight edges on a mirrored lens, are the runtime's room
(its light panels and dark walls) reflected in a smooth lens; they appear in the live mirror too and move with the
head, never count them as opaque lens material, as lens shading or as a defect; (2) the runtime cuts every temple at the ear
(the wearer's own ear and hair hide the rest), so temple tips, end pieces and anything behind the ear are never
visible by design, and only a temple that stops BEFORE the ear (the 'runtime' field reports that as a
temple_continuity_failure) is a defect; (3) a clear, untinted lens on the uniform stand-in shows only its edge and
its highlights, exactly as a clear lens does on a face, so rimless hardware legitimately appears to float, the lens is
not missing ('optical_meshes_detected' says how many lenses the runtime found), and tint or gradient are judged only
where the product has them. Severity
'major' means a wearer would notice it in the mirror; everything else is 'minor'. Be persuaded by nothing but the
images and the measurements."""

EVALUATOR_FORMAT = """Respond with ONE JSON object:
{"discrepancies": [{"part": "frame|lens_R|lens_L|temple_R|temple_L|bridge|hinge|logo|nose_pads|lenses|overall",
                    "view": "front|angled|rolled|back|left|right|held_out|overall", "description": "...", "severity": "major|minor"}],
 "identity_checklist": [{"feature": "<copied from the checklist>", "verdict": "present|partial|absent", "note": "..."}],
 "resemblance_0_10": {"front": n, "side": n, "angled_held_out": n, "materials_and_lenses": n, "mirror_overall": n},
 "runtime_notes": "<anything wrong in the AR renders: transparency, placement, truncated temples, artefacts>",
 "overall": "accept|reject", "summary": "<two or three sentences: would a wearer recognise the product and see nothing wrong?>"}"""


def validate_evaluation(d) -> dict:
    if not isinstance(d, dict):
        raise ValueError("evaluation must be a JSON object")
    disc = d.get("discrepancies", [])
    if not isinstance(disc, list):
        raise ValueError("discrepancies must be a list")
    clean = []
    for x in disc:
        if not isinstance(x, dict) or x.get("severity") not in ("major", "minor"):
            raise ValueError("each discrepancy needs part, view, description, severity major|minor")
        clean.append({"part": str(x.get("part", "overall"))[:40], "view": str(x.get("view", "overall"))[:20],
                      "description": str(x.get("description", ""))[:1000], "severity": x["severity"]})
    check = d.get("identity_checklist", [])
    if not isinstance(check, list):
        raise ValueError("identity_checklist must be a list")
    cl = []
    for x in check:
        if not isinstance(x, dict) or x.get("verdict") not in ("present", "partial", "absent"):
            raise ValueError("each checklist row needs feature and verdict present|partial|absent")
        cl.append({"feature": str(x.get("feature", ""))[:300], "verdict": x["verdict"], "note": str(x.get("note", ""))[:600]})
    res = d.get("resemblance_0_10", {})
    if not isinstance(res, dict):
        raise ValueError("resemblance_0_10 must be an object")
    res = {str(k)[:40]: float(v) for k, v in res.items() if isinstance(v, (int, float)) and 0 <= v <= 10}
    overall = d.get("overall")
    if overall not in ("accept", "reject"):
        raise ValueError("overall must be accept or reject")
    return {"discrepancies": clean, "identity_checklist": cl, "resemblance_0_10": res,
            "runtime_notes": str(d.get("runtime_notes", ""))[:2000], "overall": overall, "summary": str(d.get("summary", ""))[:2000]}


# --------------------------------------------------------------------------- wearer-proxy AR renders
def _hex_rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def content_box_solid(paths, color: str = WEARER_BACKGROUND, margin: int = 12, threshold: int = 14) -> tuple[int, int, int, int]:
    """Union bbox of the pixels that differ from the solid fixture over several renders."""
    box = None
    size = None
    rgb = np.array(_hex_rgb(color), int)
    for p in paths:
        im = np.asarray(Image.open(p).convert("RGB"))
        size = (im.shape[1], im.shape[0])
        diff = np.abs(im.astype(int) - rgb).max(-1) > threshold
        ys, xs = np.nonzero(diff)
        if not len(xs):
            continue
        b = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
        box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
    if box is None:
        return (0, 0) + (size or (720, 480))
    return (max(0, box[0] - margin), max(0, box[1] - margin), box[2] + margin, box[3] + margin)


def wearer_renders(asset_dir: Path, glb_path: Path, width_mm: float | None = None, *, force: bool = False) -> dict:
    """The asset in the actual runtime on the skin-toned fixture, wearer poses only. Cached under observe/ar_wearer/."""
    out = Path(asset_dir) / "observe" / "ar_wearer"
    cached = out / "archeck.json"
    if cached.exists() and not force:
        res = json.loads(cached.read_text(encoding="utf-8"))
        m = (res.get("models") or {}).get("candidate") or {}
        if m.get("renders") and all(Path(r).is_file() for r in m["renders"]):
            return m
    res = archeck.run({"candidate": Path(glb_path)}, out, ar_views=WEARER_AR_VIEWS, background="solid",
                      background_color=WEARER_BACKGROUND, width_mm={"candidate": float(width_mm)} if width_mm else None,
                      description="modeler evaluator v2: wearer-proxy AR view (solid skin-toned fixture, wearer poses)")
    return (res.get("models") or {}).get("candidate") or {}


def wearer_sheets(renders: list[str], out_dir: Path) -> tuple[Path | None, Path | None]:
    """(mirror sheet: the raw runtime frames at the mirror's scale; detail sheet: cropped to the asset, 480 px high)."""
    paths = [p for p in renders if p and Path(p).is_file()]
    if not paths:
        return None, None
    labels = [Path(p).stem.split("__")[-1] for p in paths]
    tiles = [_label(Image.open(p).convert("RGB"), f"{lab} (runtime, mirror scale)") for p, lab in zip(paths, labels)]
    mirror = Image.new("RGB", (sum(t.size[0] for t in tiles) + 8 * (len(tiles) - 1), max(t.size[1] for t in tiles)), (255, 255, 255))
    x = 0
    for t in tiles:
        mirror.paste(t, (x, 0))
        x += t.size[0] + 8
    mirror_path = Path(out_dir) / "sheet_wearer_mirror.png"
    mirror.save(mirror_path)
    box = content_box_solid(paths)
    dtiles = []
    for p, lab in zip(paths, labels):
        im = Image.open(p).convert("RGB").crop(box)
        im = im.resize((max(1, int(im.size[0] * 480 / max(im.size[1], 1))), 480), Image.LANCZOS)
        dtiles.append(_label(im, f"{lab} (runtime, enlarged)"))
    detail = Image.new("RGB", (sum(t.size[0] for t in dtiles) + 8 * (len(dtiles) - 1), 480), (255, 255, 255))
    x = 0
    for t in dtiles:
        detail.paste(t, (x, 0))
        x += t.size[0] + 8
    detail_path = Path(out_dir) / "sheet_wearer_detail.png"
    detail.save(detail_path)
    return mirror_path, detail_path


def heldout_match_sheet(cand_dir: Path, evidence: dict, evidence_dir: Path, out: Path) -> Path | None:
    """[held-out photo | render | overlay] from the held-out render the observer wrote under heldout/."""
    held = json.loads((cand_dir / "heldout" / "heldout.json").read_text(encoding="utf-8")) if (cand_dir / "heldout" / "heldout.json").exists() else {}
    tiles = []
    for vid, row in (held.get("renders") or {}).items():
        photo = Path(row["photo_crop"])
        render = Path(row["render"])
        if photo.is_file() and render.is_file():
            fg = np.load(row["fg_crop"])["fg"] if row.get("fg_crop") and Path(row["fg_crop"]).is_file() else None
            tiles.append(match_tile(photo, render, fg, f"{vid} (held out)"))
    if not tiles:
        return None
    stack(tiles).save(out)
    return out


# --------------------------------------------------------------------------- the package
def write_evaluator_package(job_dir: Path, asset_dir: Path, evidence: dict, checklist: list, out_dir: Path, *,
                            glb_path: Path | None = None, width_mm: float | None = None) -> tuple[dict, list[dict]]:
    """Everything the evaluator sees for one asset (a candidate or a baseline). Returns (request dict, images)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    asset_dir = Path(asset_dir)
    images = []
    for row in evidence["inputs"]:
        images.append({"id": f"photo_{row['id']}", "label": f"product photo, view {row['view']}" + (" (held out from the author)" if row["held_out"] else ""),
                       "path": str(row["path"])})
    obs = json.loads((asset_dir / "observe" / "observation.json").read_text(encoding="utf-8"))
    if glb_path is None:
        glb_path = asset_dir / "model.glb"
    if width_mm is None and obs.get("bbox_mm"):
        width_mm = float(obs["bbox_mm"][1][0] - obs["bbox_mm"][0][0])
    wearer = wearer_renders(asset_dir, Path(glb_path), width_mm) if Path(glb_path).is_file() else {}
    mirror, detail = wearer_sheets(wearer.get("renders") or [], out_dir)
    if mirror:
        images.append({"id": "wearer_mirror", "label": "the asset in the ACTUAL AR runtime, wearer poses front / turned 35 / rolled 25, on the "
                                                       "skin-toned stand-in for the face, at the mirror's own scale: what the wearer sees", "path": str(mirror)})
    if detail:
        images.append({"id": "wearer_detail", "label": "the same runtime renders enlarged for inspection (materials, lens tint and transparency, "
                                                       "edges, temples)", "path": str(detail)})
    pm = (obs.get("sheets") or {}).get("photo_match")
    if pm and Path(pm).is_file():
        images.append({"id": "photo_match_shape", "label": "SHAPE ONLY: the asset rendered from the fitted photo cameras beside each photo "
                                                          "(red photo edge, green render edge); an EEVEE preview whose colours and lens transparency are not evidence", "path": pm})
    hs = heldout_match_sheet(asset_dir, evidence, job_dir / "evidence", out_dir / "sheet_heldout_match.png")
    if hs:
        images.append({"id": "heldout_match_shape", "label": "SHAPE ONLY: the asset from the fitted HELD-OUT photo camera beside that photo", "path": str(hs)})
    held = json.loads((asset_dir / "heldout" / "heldout.json").read_text(encoding="utf-8")) if (asset_dir / "heldout" / "heldout.json").exists() else {}
    summary = obs.get("summary") or {}
    request = {"protocol": PROTOCOL, "task": EVALUATOR_TASK, "response_format": EVALUATOR_FORMAT,
               "identity_checklist": checklist, "product_notes": evidence.get("notes"),
               "runtime": {"runtime_compatible": summary.get("ar_runtime_compatible"), "optical_meshes_detected": summary.get("ar_optical_meshes"),
                           "temple_continuity_failure": summary.get("ar_continuity_failure"),
                           "note": "a continuity failure means the runtime cuts the temples short at the ear; the wearer sees truncated arms"},
               "measurements": {"note": "silhouette and lens-outline errors in mm at the nominal scale (shape evidence); lens_colour, when present, "
                                        "compares the lens core of the runtime's front render with the photo's lens core (hue_error 0 = same hue, "
                                        "saturation_ratio and value_ratio = render / photo) under different lighting",
                                "author_visible": summary, "held_out": held.get("summary"),
                                "per_view": {k: {kk: v.get(kk) for kk in ("view", "iou", "contour_mean_mm", "contour_p95_mm")} for k, v in (obs.get("views") or {}).items()}},
               "images": [{k: v for k, v in im.items() if k != "path"} | {"file": Path(im["path"]).name} for im in images]}
    return request, images


def write_package_files(out_dir: Path, request: dict, images: list[dict], role: str = "evaluator") -> Path:
    """The file layout an external agent answers: request.json, images/, README.md; the answer goes to response.json."""
    out_dir = Path(out_dir)
    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    for im in images:
        dst = img_dir / Path(im["path"]).name
        if not dst.exists() or Path(im["path"]).stat().st_mtime > dst.stat().st_mtime:
            shutil.copy2(im["path"], dst)
    request = dict(request, response_file="response.json")
    (out_dir / "request.json").write_text(json.dumps(request, indent=1), encoding="utf-8")
    (out_dir / "README.md").write_text(
        f"# {role} package\n\nRead request.json and every image in images/ (their labels are in request.json 'images').\n"
        f"Write your single answer as JSON to response.json in this folder. Read nothing outside this folder.\n", encoding="utf-8")
    return out_dir / "request.json"


# --------------------------------------------------------------------------- the status
def decide_status(*, candidate_valid: bool, metrics: dict | None, heldout: dict | None, evaluation: dict | None,
                  input_flags: list[str], protocol_calibrated: bool) -> dict:
    """The explicit status and the reasons. ``accepted`` needs a calibrated visual bar; until then the best a clean
    result can get is ``best_effort`` with its gates recorded. ``automatic_verdict`` is the job's own accept/reject."""
    reasons = []
    if not candidate_valid:
        return {"status": "execution_failed", "reasons": ["no candidate passed the contract and the AR renderer"], "provisional": {},
                "clean": False, "automatic_verdict": "reject"}
    provisional = {}
    m = metrics or {}
    h = (heldout or {}).get("summary", {}) if heldout else {}
    for key, limit in PROVISIONAL_THRESHOLDS_MM.items():
        val = h.get("mean_contour_mm_all_fit_views") if key == "heldout_contour_mean_mm" else m.get(key)
        gate = key in GATE_THRESHOLDS_MM
        if val is None:
            provisional[key] = {"value": None, "limit_mm": limit, "pass": None, "gate": gate}
        else:
            provisional[key] = {"value": round(float(val), 3), "limit_mm": limit, "pass": bool(val <= limit), "gate": gate}
    continuity = m.get("ar_continuity_failure")
    provisional["ar_temple_continuity"] = {"value": continuity, "limit_mm": None, "pass": not continuity, "gate": True}
    serious_input = [f for f in input_flags if f in ("mirror_iou_low", "lens_count_mismatch", "low_resolution")]
    if evaluation is None:
        return {"status": "quality_unverified", "reasons": ["the independent evaluation did not run or was invalid"],
                "provisional": provisional, "input_flags": input_flags, "clean": False, "automatic_verdict": "reject"}
    majors = [d for d in evaluation["discrepancies"] if d["severity"] == "major"]
    absent = [c for c in evaluation["identity_checklist"] if c["verdict"] == "absent"]
    gate_pass = all(v["pass"] for v in provisional.values() if v["gate"] and v["pass"] is not None)
    # The evaluator's OVERALL verdict is the judgement; its majors and absent features are the list of what the
    # wearer will notice. On the 2026-09-26 calibration set the owner accepted three assets whose evaluators listed
    # one major each (the owner's own notes: lens colour close, lens branding odd) while accepting overall, so majors
    # no longer block on their own.
    clean = evaluation["overall"] == "accept" and gate_pass
    if clean and protocol_calibrated:
        status = "accepted"
    elif serious_input and not clean:
        status = "inconsistent_inputs" if "lens_count_mismatch" in serious_input else "best_effort"
        reasons.append(f"input flags: {serious_input}")
    else:
        status = "best_effort"
    if clean and not protocol_calibrated:
        reasons.append("all gates passed and the evaluator accepted, but the visual bar is not calibrated against owner verdicts; not awarded 'accepted'")
    if majors:
        reasons.append(f"{len(majors)} major discrepancies reported by the evaluator")
    if absent:
        reasons.append(f"{len(absent)} identity features absent")
    if not gate_pass:
        failed = [k for k, v in provisional.items() if v["gate"] and v["pass"] is False]
        reasons.append(f"gate failed: {failed}")
    for k, v in provisional.items():
        if not v["gate"] and v["pass"] is False:
            reasons.append(f"{k} above its report-only level ({v['value']} > {v['limit_mm']} mm; not a gate)")
    if evaluation["overall"] == "reject":
        reasons.append("evaluator overall: reject")
    return {"status": status, "reasons": reasons, "provisional": provisional, "input_flags": input_flags,
            "evaluator_overall": evaluation["overall"], "major_discrepancies": len(majors), "absent_features": len(absent),
            "clean": clean, "automatic_verdict": "accept" if clean else "reject", "threshold_provenance": THRESHOLD_PROVENANCE}
