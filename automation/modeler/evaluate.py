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

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from bsa import archeck
from bsa.core import sha256_file

from .observe import _label, match_tile, stack
from .paths import AR

# 'inconsistent_inputs' was removed 2026-09-29: it hung on an input flag 'lens_count_mismatch' that nothing produced
# (bsa.intake's per-view flags never carried it), so the status was unreachable (PLAN_2026-09-27_astra_intake.md section 4)
STATUSES = ("accepted", "best_effort", "quality_unverified", "execution_failed")
PROTOCOL = "modeler_evaluator_v2.2"   # v2 run 1 misread lens highlights and runtime-cut temples; v2.1 misread dark room reflections in a mirror lens

# Gates refit 2026-09-26 from the seven owner verdicts in the live try-on (data/modeler/calibration/owner_verdicts.jsonl):
# both owner accepts have lens outline <= 0.51 mm and front <= 0.51 mm; both Tripo rejects have lens outline >= 1.1 mm;
# both BSA rejects fail the runtime's temple continuity while beating every accept on silhouettes; the held-out contour
# rejected an owner-accepted asset at 1.24 mm and every reject scored better on it, so it is reported, not gated.
# Recomputed 2026-09-28 under the lens-outline rule of that date (prints on the lens and see-through rims counted as lens,
# millimetres at the lens centre instead of the NormFrame centre) with each calibration asset's stored front camera:
# accepts 0.108-0.439 mm (were 0.115-0.747; rayban 0.741 -> 0.407, the oakleys 0.747/0.732 -> 0.439/0.432: prints and a x1.07-1.20
# millimetre bias), the Tripo rejects 1.010 / 1.131 (were 1.105 / 1.215), the BSA rejects 0.074 / 0.629 (they fail continuity):
# 0.8 still separates every row as before, with a wider margin on the accept side. The view contours moved to the front piece
# centre too (front x0.83-0.97 of the old mm, back x1.03-1.15, held-out angled x0.86-0.98): front accepts <= 0.664 (oakley)
# against the 1.0 report-only level, held-out 0.49-1.43 against 1.5. (fix7-instr/calib_recompute.py; the vb-run1 row stores 0.458
# while its observation.json reproduces 0.141 as drawn: that row's metrics came from another observation.)
GATE_THRESHOLDS_MM = {"lens_outline_mean_mm": 0.8}
REPORT_ONLY_MM = {"front_contour_mean_mm": 1.0, "heldout_contour_mean_mm": 1.5}
PROVISIONAL_THRESHOLDS_MM = GATE_THRESHOLDS_MM | REPORT_ONLY_MM
THRESHOLD_PROVENANCE = ("gates refit 2026-09-26 from 10 owner verdicts (5 accept, 1 borderline, 4 reject): the visible lens outline (accepts "
                        "<= 0.75 mm, the Tripo rejects >= 1.1 mm) and runtime temple continuity are gates; the front contour is report-only "
                        "(the owner accepted the Oakley shield at 0.71 mm while rejected assets score 0.07-0.60) and so is the held-out contour; "
                        "recomputed 2026-09-28 under the lens rule of that date (prints and see-through rims count as lens, mm at the lens "
                        "centre): accepts <= 0.44 mm, the Tripo rejects >= 1.01 mm, every row gated as before")

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
where the product has them. A tinted lens is judged on the 'lens_backdrop' image when it is present (the runtime's
lens over the front photo's own backdrop beside the photo's lenses): on the skin-toned stand-in a light brown tint is
indistinguishable from a clear lens. Severity
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


def ar_runtime_digest() -> str:
    """The AR runtime the wearer renders ran in: the pipeline's own ``ar_runtime`` fingerprint (ar/src, package.json,
    the harness pages and driver) when ``bsa.pipeline`` imports; else sha256 over the sorted files of ar/src/render
    and ar/src/eyewear, so a cache key can always be formed."""
    try:
        from bsa.pipeline import ar_runtime_digest as pipeline_digest
        return pipeline_digest()
    except Exception:  # noqa: BLE001 - the fallback fingerprints the renderer and eyewear sources directly
        h = hashlib.sha256()
        for sub in ("render", "eyewear"):
            for f in sorted((AR / "src" / sub).rglob("*")):
                if f.is_file():
                    h.update(str(f.relative_to(AR)).replace("\\", "/").encode())
                    h.update(sha256_file(f).encode())
        return h.hexdigest()


def wearer_cache_key(glb_path: Path, width_mm: float | None, *, runtime_digest: str | None = None) -> dict:
    """What a wearer render depends on: the exact asset bytes, the AR runtime, the poses and the fixture."""
    return {"asset_sha256": sha256_file(glb_path), "ar_runtime_digest": runtime_digest or ar_runtime_digest(),
            "views": [dict(v) for v in WEARER_AR_VIEWS], "background": "solid", "background_color": WEARER_BACKGROUND,
            "width_mm": float(width_mm) if width_mm else None}


def _wearer_cache_valid(res: dict, key: dict) -> bool:
    """A cached render is reused only when its recorded key equals the computed one and every render file is present
    with its recorded sha256; a cache without a key (written before 2026-09-27) is legacy and re-rendered."""
    if res.get("cache_key") != key:
        return False
    m = (res.get("models") or {}).get("candidate") or {}
    renders, digests = m.get("renders") or [], res.get("render_sha256") or {}
    if not renders:
        return False
    return all(Path(r).is_file() and digests.get(r) and digests[r] == sha256_file(Path(r)) for r in renders)


def wearer_renders(asset_dir: Path, glb_path: Path, width_mm: float | None = None, *, force: bool = False) -> dict:
    """The asset in the actual runtime on the skin-toned fixture, wearer poses only. Cached under observe/ar_wearer/,
    bound to the asset bytes, the AR runtime digest and the render settings (``cache_key`` in archeck.json) plus a
    sha256 per render file; anything else re-renders (until 2026-09-27 the cache was existence-only, so a render of
    other bytes was reused for a new asset)."""
    out = Path(asset_dir) / "observe" / "ar_wearer"
    cached = out / "archeck.json"
    key = wearer_cache_key(Path(glb_path), width_mm)
    if cached.exists() and not force:
        try:
            res = json.loads(cached.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            res = {}
        if _wearer_cache_valid(res, key):
            return (res.get("models") or {}).get("candidate") or {}
    res = archeck.run({"candidate": Path(glb_path)}, out, ar_views=WEARER_AR_VIEWS, background="solid",
                      background_color=WEARER_BACKGROUND, width_mm={"candidate": float(width_mm)} if width_mm else None,
                      description="modeler evaluator v2: wearer-proxy AR view (solid skin-toned fixture, wearer poses)")
    m = (res.get("models") or {}).get("candidate") or {}
    res["cache_key"] = key
    res["render_sha256"] = {r: sha256_file(Path(r)) for r in (m.get("renders") or []) if Path(r).is_file()}
    out.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    return m


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
    lb = (obs.get("sheets") or {}).get("lens_backdrop")
    if lb and Path(lb).is_file():
        images.append({"id": "lens_backdrop", "label": "LENS TINT: the runtime's lens over the front photo's own backdrop, beside the photo's lenses "
                                                        "and the two lens-core colours; judge the tint here, not on the skin-toned stand-in", "path": lb})
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
               "measurements": {"note": "silhouette and lens-outline errors in mm at the nominal scale (shape evidence); a metric named in "
                                        "unreliable_metrics (a view with reliable: false) was measured on a photo matte that misses much of the "
                                        "silhouette (clear or crystal material) and measures matte holes, not the asset; lens_colour, when present, "
                                        "is the runtime's lens predicted over the front photo's own backdrop from two solid-fixture renders against "
                                        "the photo's lens core (basis two_fixture_fit): match and flags (too_light, too_pale, ...) are the verdict, "
                                        "saturation_ratio and value_ratio = prediction / photo; lens_reflection flags a flat lens whose room "
                                        "reflection flips between poses a few degrees apart (lens_reflection_slab)",
                                "author_visible": summary, "held_out": held.get("summary"),
                                "per_view": {k: {kk: v.get(kk) for kk in ("view", "iou", "contour_mean_mm", "contour_p95_mm", "reliable")} for k, v in (obs.get("views") or {}).items()}},
               "images": [{k: v for k, v in im.items() if k != "path"} | {"file": _package_image_name(im)} for im in images]}
    return request, images


def _package_image_name(im: dict) -> str:
    from . import author as mauthor
    return mauthor.package_image_name(im)


def write_package_files(out_dir: Path, request: dict, images: list[dict], role: str = "evaluator") -> Path:
    """The file layout an external agent answers: request.json, images/, README.md; the answer goes to response.json."""
    out_dir = Path(out_dir)
    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    from . import author as mauthor
    for im in images:
        # identity-aware names (<id>__<sha12><suffix>): two sheets with one basename cannot collapse into one file,
        # and a copy is skipped only when its bytes already match (until 2026-09-27: basename + mtime)
        dst = img_dir / mauthor.package_image_name(im)
        if not dst.exists() or hashlib.sha256(dst.read_bytes()).hexdigest() != mauthor.image_sha256(im):
            shutil.copy2(im["path"], dst)
    request = dict(request, response_file="response.json")
    (out_dir / "request.json").write_text(json.dumps(request, indent=1), encoding="utf-8")
    (out_dir / "README.md").write_text(
        f"# {role} package\n\nRead request.json and every image in images/ (their labels are in request.json 'images').\n"
        f"Write your single answer as JSON to response.json in this folder. Read nothing outside this folder.\n", encoding="utf-8")
    return out_dir / "request.json"


# --------------------------------------------------------------------------- the status
def decide_status(*, candidate_valid: bool, metrics: dict | None, heldout: dict | None, evaluation: dict | None,
                  input_flags: list[str], protocol_calibrated: bool, tags: tuple[str, ...] | list[str] = (),
                  uncovered_tags: tuple[str, ...] | list[str] = (), gate_overrides: dict | None = None) -> dict:
    """The explicit status and the reasons. ``accepted`` needs a calibrated visual bar AND owner verdicts covering every
    tag of the asset (``modeler.tags``: kind of glasses, translucent, mirrored); until then the best a clean result can
    get is ``best_effort`` with its gates recorded. ``automatic_verdict`` is the job's own ``accept`` / ``reject`` /
    ``unmeasured``: a gate whose metric is missing (value None) can never pass, so with an evaluator accept and no
    definite failure the verdict is ``unmeasured`` and the status ``best_effort`` (until 2026-09-27 a missing gate
    metric was skipped and the asset accepted); a failed gate or an evaluator reject stays a ``reject``."""
    reasons = []
    tags, uncovered_tags = list(tags), list(uncovered_tags)
    tagged = {"tags": tags, "uncovered_tags": uncovered_tags}
    if not candidate_valid:
        return {"status": "execution_failed", "reasons": ["no candidate passed the contract and the AR renderer"], "provisional": {},
                "clean": False, "automatic_verdict": "reject", **tagged}
    provisional = {}
    m = metrics or {}
    h = (heldout or {}).get("summary", {}) if heldout else {}
    overrides = gate_overrides or {}
    # the observation's own reliability verdicts (modeler.observe.summarize): a metric measured on a photo matte that covers
    # too little of the silhouette, or a lens outline under a front whose rim the matte cannot see, is reported, never gated
    unreliable = dict(m.get("unreliable_metrics") or {})
    held_unreliable = (h.get("unreliable_metrics") or {}).get("mean_contour_mm_all_fit_views")
    if held_unreliable:
        unreliable["heldout_contour_mean_mm"] = held_unreliable
    for key, limit in PROVISIONAL_THRESHOLDS_MM.items():
        val = h.get("mean_contour_mm_all_fit_views") if key == "heldout_contour_mean_mm" else m.get(key)
        # a per-job override (the intake review found the measured reference untrustworthy) makes a gate report-only
        override = overrides.get(key) if isinstance(overrides.get(key), dict) else None
        gate = key in GATE_THRESHOLDS_MM and not (override and override.get("mode") == "report_only") and not unreliable.get(key)
        if val is None:
            provisional[key] = {"value": None, "limit_mm": limit, "pass": None, "gate": gate}
        else:
            provisional[key] = {"value": round(float(val), 3), "limit_mm": limit, "pass": bool(val <= limit), "gate": gate}
        if override:
            provisional[key]["override"] = override
        if unreliable.get(key):
            provisional[key]["reliable"] = False
            provisional[key]["reason"] = str(unreliable[key])
    continuity = m.get("ar_continuity_failure")
    # an absent flag is a legacy harness report written before the runtime reported continuity: every owner-accepted
    # calibration asset is such a record, so it is not gated on absence (that would silently revoke the calibrated state);
    # the record says so ("measured": False) instead of pretending a measurement. ``ar_continuity_measured`` (the key's
    # presence in the harness row, bsa.archeck.parse_report) tells a measured pass (null) from a legacy record; until
    # 2026-09-28 a fresh pass read measured: False
    measured = m["ar_continuity_measured"] if isinstance(m.get("ar_continuity_measured"), bool) else continuity is not None
    provisional["ar_temple_continuity"] = {"value": continuity, "limit_mm": None, "pass": not continuity, "gate": True, "measured": bool(measured or continuity)}
    serious_input = [f for f in input_flags if f in ("mirror_iou_low", "low_resolution")]
    mirror = m.get("mirror_iou") if isinstance(m.get("mirror_iou"), dict) else {}
    informational = []
    if "mirror_iou_low" in serious_input and mirror.get("matte_artefact"):
        # the photo is symmetric, its matte is not (a crystal rim the matte misses): test-pilot-002 front 0.844 vs complete 0.958
        serious_input.remove("mirror_iou_low")
        informational.append(f"input flag mirror_iou_low is a matte artefact (mirror IoU {mirror.get('matte')} on the matte, {mirror.get('complete')} "
                             f"on the complete silhouette; flag threshold {mirror.get('threshold', 0.94)}): informational")
    if evaluation is None:
        return {"status": "quality_unverified", "reasons": ["the independent evaluation did not run or was invalid"],
                "provisional": provisional, "input_flags": input_flags, "clean": False, "automatic_verdict": "reject", **tagged}
    majors = [d for d in evaluation["discrepancies"] if d["severity"] == "major"]
    absent = [c for c in evaluation["identity_checklist"] if c["verdict"] == "absent"]
    # a gate without a measurement is not a passed gate: the required evidence is missing (fail closed)
    unmeasured = [k for k, v in provisional.items() if v["gate"] and v["pass"] is None]
    gate_failed = [k for k, v in provisional.items() if v["gate"] and v["pass"] is False]
    gate_pass = not unmeasured and not gate_failed
    # The evaluator's OVERALL verdict is the judgement; its majors and absent features are the list of what the
    # wearer will notice. On the 2026-09-26 calibration set the owner accepted three assets whose evaluators listed
    # one major each (the owner's own notes: lens colour close, lens branding odd) while accepting overall, so majors
    # no longer block on their own.
    clean = evaluation["overall"] == "accept" and gate_pass
    calibrated_for_asset = protocol_calibrated and not uncovered_tags
    if clean and calibrated_for_asset:
        status = "accepted"
    elif serious_input and not clean:
        status = "best_effort"
        reasons.append(f"input flags: {serious_input}")
    else:
        status = "best_effort"
    reasons += informational
    if clean and not protocol_calibrated:
        reasons.append("all gates passed and the evaluator accepted, but the visual bar is not calibrated against owner verdicts; not awarded 'accepted'")
    elif clean and uncovered_tags:
        reasons.append(f"all gates passed and the evaluator accepted, but the visual bar has no owner verdicts covering {uncovered_tags} "
                       "(calibration coverage by kind); not awarded 'accepted'")
    if majors:
        reasons.append(f"{len(majors)} major discrepancies reported by the evaluator")
    if absent:
        reasons.append(f"{len(absent)} identity features absent")
    for k in unmeasured:
        reasons.append(f"required gate metric {k} is unmeasured")
    if gate_failed:
        reasons.append(f"gate failed: {gate_failed}")
    for k, v in provisional.items():
        if v.get("reliable") is False:
            reasons.append(f"{k} is unreliable, reported only ({v['value']} mm against {v['limit_mm']}): {v['reason']}")
        elif not v["gate"] and v["pass"] is False:
            reasons.append(f"{k} above its report-only level ({v['value']} > {v['limit_mm']} mm; not a gate)")
    if evaluation["overall"] == "reject":
        reasons.append("evaluator overall: reject")
    # a definite failure is a reject whatever else is missing; missing evidence alone is 'unmeasured', never an accept
    if clean:
        verdict = "accept"
    elif gate_failed or evaluation["overall"] == "reject":
        verdict = "reject"
    else:
        verdict = "unmeasured"
    return {"status": status, "reasons": reasons, "provisional": provisional, "input_flags": input_flags,
            "evaluator_overall": evaluation["overall"], "major_discrepancies": len(majors), "absent_features": len(absent),
            "clean": clean, "automatic_verdict": verdict, "threshold_provenance": THRESHOLD_PROVENANCE, **tagged}
