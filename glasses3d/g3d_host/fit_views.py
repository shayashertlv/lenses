"""WP3b driver: fit one camera to EVERY reference photo of a model and report the silhouette registration (PLAN.md §4
WP3b $0 proof: per-view IoU and contour error in px and in mm, for all 6 FT1119 and all 6 Prada references).

Entry points
------------
- ``fit_views(glb_path, images, views, out_dir, ...)``: the generic driver. ``images`` = {name: photo path},
  ``views`` = {name: view kind}. Writes ``out_dir/cameras.json`` (one record per photo, ``CAMERAS_JSON``) and one
  ``overlay_<name stem>.png`` per photo (the photo with the fitted render's silhouette edge in magenta and the photo
  matte's edge in green). Every photo is fitted and recorded; none is dropped (PLAN §0.3).
- ``fit_product(product, ...)``: the same on a product of ``PRODUCTS`` whose reference data was imported into
  ``glasses3d/data/reference/<product>/`` (sha256-checked ``manifest.json``; ``import`` copies it in once from a job
  folder named on the command line). Nothing here reads ``automation/`` otherwise.

View kinds (``VIEW_KINDS``, ``view_candidates``): the PLAN §5 labels, where an ambiguous label expands to every view
it can mean and ``cameras.fit_best`` keeps the candidate with the lowest loss on ONE common final level ('side' ->
right, left; 'top' -> top, topb; 'bottom' -> bottom, bottomb;
'elevated side' / 'top-oblique' -> top_oblique_left, top_oblique_right; 'rear' ->
back), or any exact ``cameras.VIEWS`` name (left, right, back, topb, top_oblique_left, ...) for a single candidate.
'top' and 'bottom' always mean labels (both front/back candidates); pass ``candidates`` to force one view.

Schedule (bsa ``fit_all``'s order, re-cut for per-photo kinds):

1. every photo that is not an 'angled' photo, in parallel worker processes: one ``cameras.fit_best`` over the kind's
   candidates (each fitted from its prior, ``n_refine`` = ``cameras.N_COARSE_POWELL`` unless overridden);
2. the 'angled' photos, which also receive ``cameras.cross_seeds`` from step 1's winners (``cameras.NOT_CROSS_SOURCES``
   (angled, top/bottom variants, top_oblique_*, rear_threequarter_*) are never sources).

Every fit reports its sign margin (``ViewFit.pitch_twin``). A sign-fixed kind (every candidate puts the camera ABOVE
the glasses: 'top', 'elevated side') whose winner has pitch <= 0 is recorded with ``status: refused`` (its camera is
still reported): projecting such a camera would paint photo colours onto undersides.
Bottom views require negative pitch. Fits below ``MIN_USABLE_IOU`` are ``unusable`` camera evidence, retained with
their overlays and metrics; this is not a model-likeness or finish gate. Transparent lenses use the same optional
IoU as the optimizer; raw IoU is always reported separately.
mm = px / (px per mm at the front pivot, ``cameras.px_per_mm_at``), bsa's ``contour_mean_mm`` convention. Contour
statistics are symmetric (``cameras.contour_stats``): mean = mean of the two directed means, pNN = the larger of the
two directed NNth percentiles; both directed means are reported too (render -> photo large: the model shows something
the photo does not, e.g. a far temple; photo -> render large: the matte holds something the model lacks).

Usage (from the repository root):
    glasses3d/.venv/Scripts/python -m glasses3d.g3d_host.fit_views views --glb model.glb --out-dir DIR
        --image angled=photos/reference-01.png --image "elevated side=photos/reference-06.png" ...
        [--skip inner_brand_print TOM_FORD] [--front-parts Brow_Left ...] [--n-refine 3 reference-02.png=2]
        [--candidates reference-06.png=top_oblique_left,left] [--workers N]
    glasses3d/.venv/Scripts/python -m glasses3d.g3d_host.fit_views import ft1119 --job-dir <job> --model <glb>
    glasses3d/.venv/Scripts/python -m glasses3d.g3d_host.fit_views fit ft1119 [--only reference-06.png]
        [--candidates reference-06.png=top_oblique_left,top_oblique_right,left] [--out file.json] [--out-dir DIR]
``views`` names each photo by its file name; ``fit`` writes ``cameras.json`` and the overlays into ``--out-dir``
(default ``data/out/wp3b/<product>/``) and, with ``--out``, a copy of the JSON at that path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from . import cameras, fitmesh, hinge_axes, hinge_fit, pose
from . import matte as M
from .camera import CAMERA_FIELDS, Camera

G3D = Path(__file__).resolve().parents[1]
REFERENCE = G3D / "data" / "reference"
OUT = G3D / "data" / "out" / "wp3b"
MANIFEST = "manifest.json"
MODEL = "model.glb"
CAMERAS_JSON = "cameras.json"
CONTOUR_PERCENTILES = (90, 95)
MIN_USABLE_IOU = 0.5  # at least half the effective foreground union overlaps; a camera-status label, not a finish gate

# PLAN §5 labels -> candidate views. fit_best keeps the lowest common final-level loss among them.
LABEL_CANDIDATES: dict[str, tuple[str, ...]] = {
    "front": ("front",),
    "rear": ("back",),
    "rear_threequarter": ("rear_threequarter_left", "rear_threequarter_right"),
    "side": ("right", "left"),                        # the label does not say which side
    "top": ("top", "topb"),                           # seen over the front or over the back; camera above either way
    "bottom": ("bottom", "bottomb"),                  # camera below, seen under the front or under the back
    "elevated side": ("top_oblique_left", "top_oblique_right"),
    "angled": ("angled",),
}
LABEL_PITCH_SIGN = {"top": 1, "bottom": -1, "elevated side": 1}
CROSS_RECEIVERS = ("angled",)                         # bsa fit_all: the angled view only receives cross seeds
# every accepted view kind: the labels, every exact cameras view ('top' stays the label), and spelling aliases
VIEW_KINDS: dict[str, tuple[str, ...]] = {
    **LABEL_CANDIDATES,
    **{v: (v,) for v in cameras.VIEWS if v not in LABEL_CANDIDATES},
    "top oblique": cameras.TOP_OBLIQUE_VIEWS,
    "rear three quarter": ("rear_threequarter_left", "rear_threequarter_right"),
}


def _norm_kind(kind: str) -> str:
    return " ".join(str(kind).strip().lower().replace("_", " ").replace("-", " ").split())


_KIND_TABLE = {_norm_kind(k): v for k, v in VIEW_KINDS.items()}


def view_candidates(kind: str) -> tuple[str, ...]:
    """The cameras views a view kind can mean (case, '_' and '-' are ignored: 'Elevated_Side' = 'elevated side')."""
    try:
        return _KIND_TABLE[_norm_kind(kind)]
    except KeyError:
        raise ValueError(f"unknown view kind {kind!r}; known: {', '.join(VIEW_KINDS)}") from None


def kind_pitch_sign(kind: str) -> int | None:
    """Required camera side of the horizontal plane: +1 above, -1 below, otherwise unspecified."""
    above = cameras.TOP_VIEWS + cameras.TOP_OBLIQUE_VIEWS
    candidates = view_candidates(kind)
    if all(v in above for v in candidates):
        return 1
    return -1 if all(v in cameras.BOTTOM_VIEWS for v in candidates) else None


@dataclass(frozen=True)
class Product:
    key: str
    name: str
    job: str                                          # studio job id the photos come from (PLAN §5)
    model_source: str                                 # where model.glb came from (provenance only)
    views: dict[str, str]                             # photo -> PLAN §5 label (job.json labels are ignored, §0.3)
    skip: tuple[str, ...] = ()                        # fitmesh skip substrings (thin surface decals)
    front_parts: tuple[str, ...] | None = None        # pivot parts (None: the geometric front piece)
    n_refine: dict[str, int] = field(default_factory=dict)   # per-photo override of cameras.N_COARSE_POWELL


PRADA_SKIP = ("hallmark", "etch", "ribbon")           # gold temple hallmarks/ribbon and the lens etch: surface decals
PRADA_FRONT_PARTS = ("Front | continuous milled acetate.001", "Lens left | closed 49 x 36 mm.001",
                     "Lens right | closed 49 x 36 mm.001")
# make_uv_test.py fitted FT1119 02/03/04 with 2 starts; keeping 2 keeps the reproduced prototype cameras for 02/03
# (tests/test_cameras_repro.py) as the cameras WP5 projects with; 04 also gets the top prior's low band, which lands in
# the prototype's basin (within 0.06-0.21 deg, tested) but not bit-identically
FT1119_N_REFINE = {"reference-02.png": 2, "reference-03.png": 2, "reference-04.png": 2}

PRODUCTS: dict[str, Product] = {
    "ft1119": Product(
        key="ft1119", name="Tom Ford FT1119-D 52A", job="d4b716a6afc445f89cbed8beddad4c37",
        model_source="studio job 3b8a1c7ae8114868b49b298b681f1b29 original/model.glb (quality-001, = prototype q001.glb)",
        views={"reference-01.png": "angled", "reference-02.png": "rear", "reference-03.png": "side",
               "reference-04.png": "top", "reference-05.png": "front", "reference-06.png": "elevated side"},
        skip=fitmesh.PROTOTYPE_SKIP,
        front_parts=("Brow_Left.001", "Brow_Right.001", "Eyewire_Left.001", "Eyewire_Right.001",
                     "Lens_Left.001", "Lens_Right.001", "Saddle_bridge.001"),
        n_refine=FT1119_N_REFINE),
    "prada": Product(
        key="prada", name="Prada PRS 17WS 21D5J2 49M", job="8c82d986e6c14d26b4b38f3807712ea0",
        model_source="studio job 8c82d986e6c14d26b4b38f3807712ea0 agent/ar/prada-pr17ws-delivery-3e3ac97b/model.glb",
        views={"reference-01.webp": "side", "reference-02.webp": "angled", "reference-03.png": "top",
               "reference-04.png": "front", "reference-05.png": "side", "reference-06.png": "rear"},
        skip=PRADA_SKIP, front_parts=PRADA_FRONT_PARTS),
}


# ----------------------------------------------------------------------------- reference data
class ReferenceDataMissing(FileNotFoundError):
    """The product's reference data was never imported (no manifest)."""


class ReferenceDataError(RuntimeError):
    """The manifest exists but a file is missing or differs from it."""


def reference_dir(product: str, root: Path | None = None) -> Path:
    return (REFERENCE if root is None else Path(root)) / product


def manifest_path(product: str, root: Path | None = None) -> Path:
    return reference_dir(product, root) / MANIFEST


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def reference_files(product: str, root: Path | None = None) -> dict[str, Path]:
    """Every manifest file of the product (photos and ``model.glb``), each checked against its sha256 and size.
    Raises ``ReferenceDataMissing`` without a manifest and ``ReferenceDataError`` for a missing or changed file."""
    mp = manifest_path(product, root)
    if not mp.exists():
        raise ReferenceDataMissing(f"{mp} not found: run `python -m glasses3d.g3d_host.fit_views import {product} ...`")
    man = json.loads(mp.read_text(encoding="utf-8"))
    out, bad = {}, []
    for name, rec in man["files"].items():
        p = mp.parent / name
        if not p.exists():
            bad.append(f"{name}: missing")
        elif p.stat().st_size != rec["bytes"] or sha256_file(p) != rec["sha256"]:
            bad.append(f"{name}: differs from the manifest (sha256 {rec['sha256'][:12]}...)")
        out[name] = p
    if bad:
        raise ReferenceDataError(f"{mp.parent}: " + "; ".join(bad))
    if product in PRODUCTS:
        want = set(PRODUCTS[product].views) | {MODEL}
        if set(out) != want:
            raise ReferenceDataError(f"{mp}: files {sorted(out)} != the product table's {sorted(want)}")
    return out


def import_references(product: str, job_dir: Path, model: Path, root: Path | None = None) -> dict:
    """Copy the product's photos (from ``job_dir/images``, each checked against the job's own sha256) and the model
    into ``data/reference/<product>/`` and write the manifest. Never overwrites a file with different content."""
    prod = PRODUCTS[product]
    job_dir, model = Path(job_dir), Path(model)
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    if job.get("id") not in (None, prod.job):
        raise ValueError(f"{job_dir}: job id {job.get('id')} is not {prod.job}")
    job_sha = {im["name"]: im.get("sha256") for im in job.get("images", [])}
    dst = reference_dir(product, root)
    dst.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, label in prod.views.items():
        src = job_dir / "images" / name
        sha = sha256_file(src)
        if job_sha.get(name) != sha:
            raise ValueError(f"{src}: sha256 {sha} != job.json's {job_sha.get(name)}")
        files[name] = _copy_checked(src, dst / name, sha) | {"label": label, "source": f"job {prod.job} images/{name}"}
    msha = sha256_file(model)
    files[MODEL] = _copy_checked(model, dst / MODEL, msha) | {"source": prod.model_source}
    man = {"product": product, "name": prod.name, "job": prod.job,
           "imported_utc": _utc(),
           "note": "labels are PLAN.md §5 (job.json view labels ignored, PLAN §0.3); every photo is authoritative",
           "files": files}
    (dst / MANIFEST).write_text(json.dumps(man, indent=1), encoding="utf-8")
    return man


def _copy_checked(src: Path, dst: Path, sha: str) -> dict:
    if dst.exists():
        if sha256_file(dst) != sha:
            raise ValueError(f"{dst} exists with different content; refusing to overwrite")
    else:
        shutil.copyfile(src, dst)
        if sha256_file(dst) != sha:
            raise ValueError(f"{dst}: copy differs from {src}")
    return {"sha256": sha, "bytes": dst.stat().st_size}


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_mesh(product: str, files: dict[str, Path]) -> tuple[fitmesh.FitMesh, np.ndarray]:
    """The product's fit mesh (table skip list) and its normalized front pivot (table front parts)."""
    prod = PRODUCTS[product]
    mesh = fitmesh.load_fit_mesh(files[MODEL], skip=prod.skip)
    return mesh, mesh.front_pivot(prod.front_parts)


# ----------------------------------------------------------------------------- measurement helpers
def sign_check(kind: str, cam: Camera, *, pivot_norm=None) -> str | None:
    """Refusal text when the camera's pitch sign contradicts the view kind (``kind_pitch_sign``), else None."""
    s = kind_pitch_sign(kind)
    if s is not None and cam.pitch * s <= 0:
        return (f"pitch {cam.pitch:.2f} contradicts label {kind!r} (the camera must be "
                f"{'above' if s > 0 else 'below'} the glasses)")
    if (cam.eye_x or cam.eye_y) and cam.perspective > 0:
        refusals = [cameras.eye_side_refusal(view,cam,pivot_norm) for view in view_candidates(kind)]
        if all(refusals):
            return f"off-axis eye contradicts label {kind!r}: " + "; ".join(refusals)
    return None


def camera_usability(metrics: dict) -> dict:
    """Label registration, without changing the fit objective, metric values or delivery quality gates.

    Optional-lens IoU is authoritative when present: a transparent lens's raw filled silhouette can legitimately
    disagree with the open-frame matte. Non-finite metrics never certify a usable camera.
    """
    metric = "iou_lens_optional" if "iou_lens_optional" in metrics else "iou"
    value = float(metrics[metric])
    usable = bool(np.isfinite(value) and value >= MIN_USABLE_IOU)
    return {"metric": metric, "value": value if np.isfinite(value) else None,
            "minimum_iou": MIN_USABLE_IOU, "usable": usable,
            "reason": None if usable else
            f"{metric} {value:.4f} is below the usable camera overlap {MIN_USABLE_IOU:.2f} or is non-finite; "
            "camera, matte, overlay and candidate evidence are retained; this is not a finish gate"}


def contour_summary(render: np.ndarray, photo: np.ndarray, percentiles=CONTOUR_PERCENTILES) -> dict:
    """``cameras.contour_stats`` with more percentiles and both directed means (same arithmetic: the mean of the two
    directed means; pNN = the larger of the two directed NNth percentiles). inf when either mask has no edge."""
    er, ep = cameras._edge(render), cameras._edge(photo)
    if not er.any() or not ep.any():
        return {"mean": float("inf"), "render_to_photo_mean": float("inf"), "photo_to_render_mean": float("inf"),
                **{f"p{q}": float("inf") for q in percentiles}}
    dr = cameras._dist_to(ep)[er]           # render contour -> photo contour
    dp = cameras._dist_to(er)[ep]           # photo contour -> render contour
    return {"mean": float((dr.mean() + dp.mean()) / 2), "render_to_photo_mean": float(dr.mean()),
            "photo_to_render_mean": float(dp.mean()),
            **{f"p{q}": float(max(np.percentile(dr, q), np.percentile(dp, q))) for q in percentiles}}


def overlay_image(img: np.ndarray, fg: np.ndarray, render: np.ndarray, thickness: int | None = None) -> np.ndarray:
    """The photo with the matte edge (green) and the rendered silhouette edge (magenta, on top). Edges are thickened
    with the image size (1 px per ~700 px of the longer side) so they survive a downscaled view."""
    t = max(1, round(max(img.shape[:2]) / 700)) if thickness is None else int(thickness)
    ov = np.array(img[..., :3], dtype=np.uint8, copy=True)

    def thick(mask, n):
        e = cameras._edge(mask)
        return ndimage.binary_dilation(e, iterations=n) if n > 0 and e.any() else e
    ov[thick(fg, (t - 1) // 2)] = (0, 200, 0)
    ov[thick(render, t - 1)] = (255, 0, 255)
    return ov


def _cam(c: Camera) -> dict:
    return {k: float(v) for k, v in c.to_dict().items()}


def rigid_fit_marker(model_sha256: str, photo_sha256: str, camera: dict) -> dict:
    """Provenance for this fitter's fixed authored mesh; zero is a delta, not a measured opening angle."""
    camera_sha = hashlib.sha256(json.dumps(camera, sort_keys=True, separators=(",", ":"),
                                          allow_nan=False).encode("utf-8")).hexdigest()
    return {"schema": "g3d.rigid_camera_fit/1", "source_model_sha256": model_sha256,
            "photo_sha256": photo_sha256, "camera_sha256": camera_sha,
            "temple_delta_deg": {"+1": 0.0, "-1": 0.0}}


def articulated_fit_marker(model_sha256, photo_sha256, camera, theta, axes, observed, axis_source, initial_theta):
    """Bind a fitted pose to the exact authored model, photo, camera and axes."""
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode("utf-8")).hexdigest()
    return {"mode": "articulated_fit", "schema": "g3d.articulated_camera_fit/1",
            "opening_calibrated": any(observed.values()),
            "meaning": "per-photo opening delta from the captured authored GLB; unobserved sides retain input",
            "source_model_sha256": model_sha256, "photo_sha256": photo_sha256,
            "camera_sha256": digest(camera), "axes_sha256": digest(axes), "theta_sha256": digest(theta),
            "axis_source": axis_source, "input_theta_deg": initial_theta}


def _candidate_record(r: dict, ppm: float) -> dict:
    m = r["metrics"]
    return {"view": r["view"], "camera": _cam(r["camera"]), "loss": r["loss"], "loss_common_level": r.get("common_loss"),
            "iou": m["iou"], "contour_mean_px": m["contour_mean_px"], "px_per_mm_at_front_pivot": ppm,
            "contour_mean_mm": m["contour_mean_px"] / ppm, "sign": r["sign"],
            "winner_origin": r["refine"]["origin"], "evaluations": r["fit"].evals,
            **({"theta_deg":r['opening']['theta_deg'], "theta_observed":r['opening']['theta_observed'],
                "hinge_axes":"shared top-level hinge_axis_xz_mm",
                "opening_selection":r['opening']['opening_fit']['selected'],
                "opening_common_losses":r['opening']['opening_fit']['common_losses']}
               if 'opening' in r else {}),
            **{k: v for k, v in m.items() if k.endswith('_lens_optional') or k == 'lens_pixels_optional'},
            **({"off_axis": r["refine"]["off_axis"]} if "off_axis" in r["refine"] else {})}


def lens_loss_policy(glb_path, mesh: fitmesh.FitMesh) -> tuple[dict, list[int]]:
    """Use the seating check's construction evidence, and only the actual lens primitives of exact nodes.

    This changes the camera objective only; it never changes a seating verdict or the reported raw silhouette.
    Unknown construction, ambiguous duplicate node names, and failed checks retain the full silhouette.
    """
    from . import seat_check
    report = seat_check.check_glb(glb_path)
    construction = [{key: row.get(key) for key in ('node', 'frame_type', 'frame_type_source', 'provisional')}
                    for row in report.get('lenses', [])]
    by_node = {}
    for row in construction:
        by_node.setdefault(row['node'], []).append(row)
    identities = {}
    for name, node in zip(mesh.names, mesh.node_indices):
        identities.setdefault(name, set()).add(node)
    optional_nodes = {name for name, rows in by_node.items()
                      if len(identities.get(name, ())) == 1
                      and all(row['frame_type'] in ('rimless', 'semi_rimless') for row in rows)}
    optional = [i for i, (name, lens) in enumerate(zip(mesh.names, mesh.lens_parts))
                if lens and name in optional_nodes]
    actual_nodes = sorted({mesh.names[i] for i in optional})
    policy = {'mode': 'optional' if optional else 'full', 'optional_nodes': actual_nodes,
              'optional_faces': int(np.isin(mesh.part, optional).sum()), 'construction': construction}
    if report.get('error'):
        policy['note'] = f"construction check unavailable; full silhouette retained: {report['error']}"
    return policy, optional


def _model_record(glb_path, mesh, pivot, skip, front_parts):
    return {"path": str(glb_path), "sha256": sha256_file(Path(glb_path)), "skip": list(skip),
            "skipped": mesh.skipped, "front_parts": None if front_parts is None else list(front_parts),
            "front_pivot_mm": [float(x) for x in mesh.frame.to_mm(pivot[None])[0]],
            "frame": mesh.frame.to_dict(), "parts": len(mesh.names), "faces": int(len(mesh.F))}


def _fit_context(glb_path, skip, front_parts, mesh=None, pivot=None):
    mesh = mesh if mesh is not None else fitmesh.load_fit_mesh(glb_path, skip=skip)
    pivot = pivot if pivot is not None else mesh.front_pivot(front_parts)
    policy, optional = lens_loss_policy(glb_path, mesh)
    model = _model_record(glb_path, mesh, pivot, skip, front_parts)
    roles = pose.part_roles(glb_path)
    opening = hinge_axes.derive_axes(glb_path, model['sha256'], mesh=mesh, roles=roles)
    return {'model': model, 'lens_loss': policy, 'optional_parts': optional,
            'opening': opening, 'part_roles': roles}


def _opening_candidates(mesh, context, fg, pivot, results, ignore_faces, off_axis, initial_theta):
    """Keep rigid and articulated candidates on ONE final grid, preserving existing camera gates."""
    axes, roles = context['opening']['hinge_axis_xz_mm'], context['part_roles']
    alternatives = []
    initial_mesh = pose.posed(mesh, {k: {"theta_deg": initial_theta.get(k, 0.), "axis_xz_mm": v}
                                   for k, v in axes.items()}, roles)
    for result in results:
        proposal = hinge_fit.refine(mesh, roles, axes, fg, result['camera'], result['view'], pivot,
                                    initial_theta=initial_theta, ignore_faces=ignore_faces, off_axis=off_axis)
        alternatives.append((result, proposal))
    points = []
    for result, proposal in alternatives:
        for candidate_mesh, cam in ((initial_mesh, result['camera']), (proposal['mesh'], proposal['camera'])):
            points.append(cameras.project(candidate_mesh.scene().hull, cam))
    points = np.vstack(points)
    level = cameras.Level(fg, cameras.FINAL_W, 'opening_common', margin=cameras.FINAL_ROI_MARGIN,
                          include_xyxy=(*points.min(0), *points.max(0)))
    level.ignore_faces = ignore_faces
    for result, proposal in alternatives:
        rigid_loss = level.loss(initial_mesh.scene(), result['camera'])
        articulated_loss = level.loss(proposal['mesh'].scene(), proposal['camera'])
        kept = articulated_loss < rigid_loss
        proposal['opening_fit'].update(common_losses={'input_pose': float(rigid_loss),
                                                      'articulated': float(articulated_loss)},
                                      selected='articulated' if kept else 'input_pose')
        if kept:
            result['camera'], result['posed_mesh'] = proposal['camera'], proposal['mesh']
            vf = cameras.ViewFit(result['view'], fg, proposal['mesh'].scene(), pivot)
            vf.set_ignore(ignore_faces)
            if off_axis:
                vf.eye_limit = float(np.linalg.norm(mesh.frame.to_norm(mesh.V), axis=1).max())
            result['sign'] = vf.pitch_twin(result['camera'])
            result['fit'].evals += vf.evals
        else:
            result['posed_mesh'] = initial_mesh
            proposal['theta_deg'] = {k: initial_theta.get(k, 0.) for k in axes}
            proposal['theta_observed'] = {k: False for k in axes}
            visible = initial_mesh.scene().render(result['camera'], fg.shape)['face_id']
            tags = pose.side_of_faces(mesh, roles)
            for key, row in proposal['opening_fit']['per_side'].items():
                sensitivity = {name:row.pop(name) for name in
                    ('camera_coupling','independent_px_per_degree','one_pixel_sensitivity_deg','sensitivity_note')
                    if name in row}
                row.update(proposed_deg=row['theta_deg'], theta_deg=proposal['theta_deg'][key],
                           proposed_visible_pixels_final=row.get('visible_pixels_final'),
                           visible_pixels_final=int(np.count_nonzero((visible>=0) & (tags[np.maximum(visible,0)]==int(key)))),
                           proposal_sensitivity=sensitivity, identifiability='unobserved',
                           observed=False, status='input_pose_selected_on_common_loss')
        result['opening'] = proposal
        result['common_loss'] = result['loss'] = float(min(rigid_loss, articulated_loss))
        metrics = cameras.native_metrics(result['posed_mesh'].scene(), result['camera'], fg,
                                          cameras.matte_roi(fg), ignore_faces)
        result['metrics'] = {k:v for k,v in metrics.items() if k not in ('mask', 'face_id')}
    return min(results, key=lambda r:r['common_loss']), results


def _matte_mode(requested: str | None, context: dict) -> str:
    """Resolve absence from actual optional lens faces; an explicit photo mode always wins."""
    if requested is not None:
        if not isinstance(requested, str) or requested not in M.MODES:
            raise ValueError(f'unknown matte mode {requested!r}; known: {M.MODES}')
        return requested
    return ('open_frame' if context.get('optional_parts') and context.get('lens_loss', {}).get('mode') == 'optional'
            else 'default')


# ----------------------------------------------------------------------------- one photo
def fit_image(glb_path: str | Path, image_path: str | Path, view_kind: str, *, skip=(), front_parts=None,
              candidates: tuple[str, ...] | None = None, n_refine: int | None = None,
              cross_winners: dict[str, dict] | None = None, overlay_path: str | Path | None = None,
              matte_mode: str | None = None, _model_context: dict | None = None, off_axis: bool = False,
              initial_theta: dict | None = None, fit_openings: bool = True) -> dict:
    """Fit one photo to the model (runs in a worker process; returns plain data). ``candidates`` overrides the kind's
    views; ``cross_winners`` = {view: camera dict} of already fitted photos, used as cross seeds for a
    ``CROSS_RECEIVERS`` candidate. An absent ``matte_mode`` uses open_frame only for actual optional lens faces;
    explicit modes are authoritative. The record stores the resolved mode so frozen evidence uses the same matte.
    Fits use the validated seven-parameter search by default; ``off_axis=True`` adds a bounded lateral-eye
    continuation. Better silhouette agreement does not certify camera-parameter accuracy. The record's ``model``
    entry describes the mesh (``fit_views`` lifts it out)."""
    t0 = time.perf_counter()
    cands = tuple(candidates) if candidates else view_candidates(view_kind)
    bad = [v for v in cands if v not in cameras.VIEWS]
    if not cands or bad:
        raise ValueError(f"candidates {cands}: unknown views {bad}; known {cameras.VIEWS}")
    nr = cameras.N_COARSE_POWELL if n_refine is None else int(n_refine)
    mesh = fitmesh.load_fit_mesh(glb_path, skip=tuple(skip))
    pivot = mesh.front_pivot(None if front_parts is None else tuple(front_parts))
    context = _model_context or _fit_context(glb_path, skip, front_parts, mesh, pivot)
    model = _model_record(glb_path, mesh, pivot, skip, front_parts)
    if model != context['model']:
        raise ValueError('the model changed since shared camera-fit preparation')
    matte_mode = _matte_mode(matte_mode, context)
    ignore_faces = np.isin(mesh.part, context['optional_parts']) if context['optional_parts'] else None
    axes = ((context.get('opening') or {}).get('hinge_axis_xz_mm') or {}) if fit_openings else {}
    initial_theta = {f'{int(k):+d}': float(v) for k,v in (initial_theta or {}).items()}
    if set(initial_theta)-set(axes):
        raise ValueError('initial openings require recognized, signed hinge axes')
    initial_mesh = pose.posed(mesh, {k:{'theta_deg':initial_theta.get(k, 0.),'axis_xz_mm':v}
                                   for k,v in axes.items()}, context.get('part_roles') or {})
    scene = initial_mesh.scene()
    photo_sha = sha256_file(Path(image_path))
    img = M.load_photo(image_path)
    fg = M.matte(img, mode=matte_mode)
    if not fg.any():
        raise ValueError(f"{image_path}: the matte is empty (no foreground against the border background model)")
    cross, n_cross, sources = None, 0, []
    receivers = [v for v in cands if v in CROSS_RECEIVERS]
    if receivers and cross_winners:
        winners = {v: Camera.from_dict(c) for v, c in cross_winners.items()}
        cross = {v: cameras.cross_seeds(v, winners) for v in receivers}
        n_cross = sum(len(cameras.wrap_seeds(v, s)) for v, s in cross.items())
        sources = sorted(winners)
    best, results = cameras.fit_best(fg, scene, pivot, [(v, None) for v in cands], nr, cross=cross,
                                     ignore_faces=ignore_faces, **({"off_axis": True} if off_axis else {}))
    if axes:
        best, results = _opening_candidates(mesh, context, fg, pivot, results, ignore_faces, off_axis, initial_theta)
        scene = best['posed_mesh'].scene()
    pivot_mm = mesh.frame.to_mm(pivot[None])[0]
    ppm = {id(r): cameras.px_per_mm_at(r["camera"], mesh.frame, pivot_mm) for r in results}
    cam, m, p = best["camera"], best["metrics"], ppm[id(best)]
    roi = cameras.matte_roi(fg)                       # the ROI native_metrics measured in (fit_view)
    x0, y0, x1, y1 = roi
    cs = contour_summary(scene.render(cam, fg.shape, 1, roi)["mask"], fg[y0:y1, x0:x1])
    refusal = sign_check(view_kind, cam, pivot_norm=pivot)  # orientation AND the actual eye respect the label
    usability = camera_usability(m)
    flags = [f for f in ((best["sign"] or {}).get("flag"),) if f]
    if refusal:
        flags.append("label_sign_contradicted")
    if not usability["usable"]:
        flags.append("insufficient_camera_overlap")
    if (best["refine"].get("off_axis") or {}).get("candidate_at_eye_bound"):
        flags.append("off_axis_candidate_at_domain_bound")
    rec = {
        "image": str(image_path), "image_sha256": photo_sha, "shape": list(fg.shape),
        "view_kind": view_kind, "candidates": list(cands), "n_refine": nr,
        "status": "refused" if refusal else "ok" if usability["usable"] else "unusable",
        "refusal": refusal or usability["reason"], "flags": flags, "usability": usability,
        "code": "E_VIEW_SIGN" if refusal else None if usability["usable"] else "E_CAMERA_UNUSABLE",
        "view": best["view"], "winner_origin": best["refine"]["origin"], "camera": _cam(cam),
        "loss": best["loss"], "loss_common_level": best["common_loss"],
        "iou": m["iou"], "contour_mean_px": m["contour_mean_px"], "contour_p90_px": cs["p90"],
        "contour_p95_px": m["contour_p95_px"],
        "contour_render_to_photo_mean_px": cs["render_to_photo_mean"],
        "contour_photo_to_render_mean_px": cs["photo_to_render_mean"],
        "outer_contour_mean_px": m["outer_contour_mean_px"], "outer_contour_p95_px": m["outer_contour_p95_px"],
        "px_per_mm_at_front_pivot": p, "contour_mean_mm": m["contour_mean_px"] / p, "contour_p90_mm": cs["p90"] / p,
        "contour_p95_mm": m["contour_p95_px"] / p, "outer_contour_mean_mm": m["outer_contour_mean_px"] / p,
        "roi": [int(v) for v in roi], "photo_only_px": m["photo_only_px"], "render_only_px": m["render_only_px"],
        "extent": m["extent"], "sign": best["sign"],
        "all_candidates": [_candidate_record(r, ppm[id(r)]) for r in results],
        "cross_seeds": n_cross, "cross_sources": sources,
        "matte": {"foreground_px": int(fg.sum()), "shape": list(fg.shape)},
        "matte_mode": matte_mode, "lens_loss": context['lens_loss'],
        **{k: v for k, v in m.items() if k.endswith('_lens_optional') or k == 'lens_pixels_optional'},
        "overlay": None if overlay_path is None else str(overlay_path),
        "model": model,
    }
    if off_axis:
        rec["off_axis"] = dict(best["refine"]["off_axis"])
        if axes:
            limit = float(np.linalg.norm(mesh.frame.to_norm(mesh.V), axis=1).max())
            rec['off_axis'].update(camera_after_opening_fit=rec['camera'],
                opening_fit_eye_domain=[-limit,limit],
                opening_fit_at_eye_bound=any(abs(v) >= limit*(1-1e-5) for v in (cam.eye_x,cam.eye_y)))
            if rec['off_axis']['opening_fit_at_eye_bound']:
                flags.append('opening_fit_candidate_at_eye_bound')
    if sha256_file(Path(image_path)) != photo_sha or sha256_file(Path(glb_path)) != model['sha256']:
        raise ValueError('photo or model changed during fitting')
    if axes:
        opening = best['opening']
        rec.update(theta_deg=opening['theta_deg'], theta_observed=opening['theta_observed'],
                   hinge_axis_xz_mm=axes, opening_fit=opening['opening_fit'])
        rec['opening_fit']['unavailable'] = context['opening']['unavailable']
        rec['pose_provenance'] = articulated_fit_marker(model['sha256'], photo_sha, rec['camera'],
            rec['theta_deg'], axes, rec['theta_observed'], context['opening']['axis_source'],
            {k:initial_theta.get(k, 0.) for k in axes})
    elif context.get('opening') and fit_openings:
        rec['opening_fit'] = {'schema':'g3d.opening_fit/1', 'status':'unavailable',
                              'unavailable':context['opening']['unavailable'],
                              'note':'Rigid camera retained; no unrecognized hinge angle was invented.'}
    if rec["status"] == "ok" and not axes:
        rec["rigid_fit"] = rigid_fit_marker(rec["model"]["sha256"], rec["image_sha256"], rec["camera"])
    if overlay_path is not None:
        op = Path(overlay_path)
        op.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(overlay_image(img, fg, scene.render(cam, fg.shape, 1)["mask"])).save(op)
    rec["seconds"] = time.perf_counter() - t0
    return rec


# ----------------------------------------------------------------------------- every photo of a model
def _run(job):
    args, kwargs = job
    try:
        return fit_image(*args, **kwargs)
    except Exception as exc:
        return _failed_photo(args[1], args[2], kwargs, exc)


def _failed_photo(image_path, kind, kwargs, exc):
    """Keep every attempted photo, including decodable empty mattes and wholly unreadable inputs."""
    mode = _matte_mode(kwargs.get('matte_mode'), kwargs.get('_model_context') or {})
    row = {'image': str(image_path), 'image_sha256': None, 'view_kind': kind,
           'candidates': list(kwargs.get('candidates') or view_candidates(kind)), 'n_refine': kwargs.get('n_refine'),
           'status': 'failed', 'code': 'E_PHOTO_FIT', 'refusal': f'{type(exc).__name__}: {exc}',
           'error': {'type': type(exc).__name__, 'message': str(exc)}, 'flags': [], 'camera': None, 'view': None,
           'shape': None, 'matte_mode': mode, 'matte': None, 'roi': None, 'overlay': None,
           'lens_loss': (kwargs.get('_model_context') or {}).get('lens_loss')}
    try:
        row['image_sha256'] = sha256_file(Path(image_path))
        image = M.load_photo(image_path)
        row['shape'] = list(image.shape[:2])
        mask = M.matte(image, mode=mode)
        row['matte'] = {'foreground_px': int(mask.sum()), 'shape': list(mask.shape)}
        if mask.any():
            row['roi'] = [int(v) for v in cameras.matte_roi(mask)]
    except Exception:
        pass  # The recorded original failure remains authoritative; unavailable evidence is explicit None.
    return row


def _map(jobs, workers):
    if not jobs:
        return []
    if workers <= 1 or len(jobs) == 1:
        return [_run(j) for j in jobs]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_run, jobs))


def _per_name(value, names, what: str) -> dict:
    """An int (every name) or {name: value} -> {name: value or None}; unknown names raise."""
    if value is None:
        return {n: None for n in names}
    if isinstance(value, dict):
        unknown = sorted(set(value) - set(names))
        if unknown:
            raise ValueError(f"{what}: unknown images {unknown}; known: {list(names)}")
        return {n: value.get(n) for n in names}
    return {n: value for n in names}


def fit_views(glb_path: str | Path, images: dict[str, str | Path], views: dict[str, str], out_dir: str | Path, *,
              skip=(), front_parts=None, candidates: dict[str, tuple[str, ...]] | None = None,
              n_refine: int | dict[str, int] | None = None, workers: int | None = None, log=print,
              matte_modes: str | dict[str, str | None] | None = None, off_axis: bool = False,
              initial_openings: dict[str, dict] | None = None) -> dict:
    """Fit a camera for every photo in ``images`` (name -> path) against the model, each with the candidates of its
    view kind in ``views`` (name -> kind, ``view_candidates``), on the schedule above. Writes
    ``out_dir/cameras.json`` and ``out_dir/overlay_<name stem>.png`` per photo; returns the cameras.json content.

    ``skip`` / ``front_parts`` go to ``fitmesh.load_fit_mesh`` / ``FitMesh.front_pivot`` (None: the geometric front
    piece); ``candidates`` ({name: views}) overrides a photo's kind; ``n_refine`` is an int or {name: int}.
    ``matte_modes`` may provide one mode or per-photo modes; absent entries resolve from construction, and every
    result records the concrete mode used. An explicit default retains the historical hole-filled matte.
    ``off_axis=True`` explicitly adds a lateral-eye continuation to the validated seven-parameter default."""
    t0 = time.perf_counter()
    glb_path = Path(glb_path)
    images = {str(n): Path(p) for n, p in images.items()}
    names = list(images)
    if not names:
        raise ValueError("no images")
    if set(views) != set(names):
        raise ValueError(f"views {sorted(views)} != images {sorted(names)}: every image needs exactly one view kind")
    if not glb_path.is_file():
        raise FileNotFoundError(f"not found: {glb_path}")
    cands = {n: (tuple(c) if c else None) for n, c in _per_name(candidates, names, "candidates").items()}
    nref = _per_name(n_refine, names, "n_refine")
    modes = _per_name(matte_modes, names, 'matte_modes')
    openings = _per_name(initial_openings, names, 'initial_openings')
    for name, mode in modes.items():
        if mode is not None and (not isinstance(mode, str) or mode not in M.MODES):
            raise ValueError(f'{name}: unknown matte mode {mode!r}; known: {M.MODES}')
    stems = [Path(n).stem for n in names]
    if len(set(stems)) != len(stems):
        raise ValueError(f"image names {names} share a stem: their overlays would collide")
    for n in names:                                    # fail fast on a bad kind or candidate list
        for v in cands[n] or view_candidates(views[n]):
            if v not in cameras.VIEWS:
                raise ValueError(f"{n}: unknown view {v!r}; known {cameras.VIEWS}")
    context = _fit_context(glb_path, tuple(skip), None if front_parts is None else tuple(front_parts))
    modes = {name: _matte_mode(mode, context) for name, mode in modes.items()}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def receives(n):
        return any(v in CROSS_RECEIVERS for v in (cands[n] or view_candidates(views[n])))

    def job(n, winners):
        return ((glb_path, images[n], views[n]),
                {"skip": tuple(skip), "front_parts": None if front_parts is None else tuple(front_parts),
                 "candidates": cands[n], "n_refine": nref[n], "cross_winners": winners,
                 "matte_mode": modes[n], "_model_context": context,
                 "off_axis": off_axis,
                 "initial_theta": openings[n],
                 "overlay_path": out_dir / f"overlay_{Path(n).stem}.png"})

    first = [n for n in names if not receives(n)]
    second = [n for n in names if receives(n)]
    nw = workers or min(8, max(1, len(first)))
    recs = dict(zip(first, _map([job(n, None) for n in first], nw)))
    winners: dict[str, dict] = {}
    for n in first:                                    # cross sources: ok fits whose chosen view may seed others
        r = recs[n]
        if r["status"] == "ok" and r["view"] not in cameras.NOT_CROSS_SOURCES:
            winners.setdefault(r["view"], r["camera"])  # first photo wins a duplicate view
    recs.update(zip(second, _map([job(n, winners or None) for n in second], min(nw, max(1, len(second))))))
    models = [recs[n].pop("model") for n in names if 'model' in recs[n]]
    if any(mo != context['model'] for mo in models) or sha256_file(glb_path) != context['model']['sha256']:
        raise RuntimeError("the worker processes loaded different meshes from one GLB")
    res = {"created_utc": _utc(), "model": context['model'], "images": {n: recs[n] for n in names},
           "cross_winners": winners,
           "policy": {"view_kinds": {k: list(v) for k, v in VIEW_KINDS.items()},
                      "cross_receivers": list(CROSS_RECEIVERS), "not_cross_sources": list(cameras.NOT_CROSS_SOURCES),
                      "n_coarse_powell": cameras.N_COARSE_POWELL, "pitch_bounds": cameras.PITCH_BOUNDS,
                      "pitch_windows": cameras.PITCH_WINDOWS,
                      "roll": {"default_bounds": cameras.ROLL_BOUNDS, "axial_views": cameras.AXIAL_VIEWS,
                               "axial_seeds": cameras.AXIAL_ROLL_SEEDS,
                               "axial_window": "nearest 45-degree chart + default bounds; output canonicalized to [-180,180)"},
                      "camera_usability": {"minimum_iou": MIN_USABLE_IOU,
                                           "metric": "iou_lens_optional when present, otherwise iou",
                                           "scope": "camera evidence status, not a finish gate"},
                      "choice": "lowest loss on one common final level among the kind's candidates (fit_best)",
                      "mm": "px / px_per_mm_at(camera, frame, front pivot)",
                      "contour": "symmetric: mean of the two directed means; pNN = max of the two directed pNN",
                      "roi": "cameras.matte_roi(matte): matte bbox + 15 % of its width + 8 px (x0, y0, x1, y1)"},
           "seconds": time.perf_counter() - t0}
    if off_axis:
        res["policy"]["off_axis"] = {"mode": "legacy_search_then_off_axis_continuation",
                                   "eye_domain": "each coordinate +/- normalized model sphere radius",
                                   "existing_bounds_changed": False, "legacy_candidate_retained": True}
    (out_dir / CAMERAS_JSON).write_text(json.dumps(res, indent=1, default=_json), encoding="utf-8")
    for n in names:
        log(_row(n, res["images"][n]))
    return res


def _row(name: str, r: dict) -> str:
    if r.get('status') == 'failed':
        return f"{name:18s} {r['view_kind']:13s} -> failed {r.get('code')}: {r.get('refusal')}"
    c = r["camera"]
    return (f"{name:18s} {r['view_kind']:13s} -> {r['view']:17s} {r['status']:7s} IoU {r['iou']:.4f} "
            f"contour {r['contour_mean_px']:6.2f} px = {r['contour_mean_mm']:5.2f} mm (p90 {r['contour_p90_mm']:5.2f} mm, "
            f"{r['px_per_mm_at_front_pivot']:.2f} px/mm) yaw {c['yaw']:7.2f} pitch {c['pitch']:6.2f} "
            f"roll {c['roll']:7.2f} "
            f"sign margin {_margin(r)} {r['flags'] or ''}")


def _margin(r: dict) -> str:
    s = r.get("sign") or {}
    return f"{s['margin']:+.4f}" if "margin" in s else "n/a"


def _json(o):
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(type(o).__name__)


# ----------------------------------------------------------------------------- one product of the table
def fit_product(product: str, only: list[str] | None = None, candidates: dict[str, tuple[str, ...]] | None = None,
                workers: int | None = None, out_dir: Path | None = None, root: Path | None = None,
                log=print) -> dict:
    """``fit_views`` on a product's sha256-checked reference data with the product table's mesh settings."""
    prod = PRODUCTS[product]
    files = reference_files(product, root)            # fail fast on missing/changed data
    unknown = sorted(set(only or ()) - set(prod.views)) + sorted(set(candidates or {}) - set(prod.views))
    if unknown:
        raise ValueError(f"{product}: unknown references {unknown}; known: {list(prod.views)}")
    names = [n for n in prod.views if not only or n in only]
    res = fit_views(files[MODEL], {n: files[n] for n in names}, {n: prod.views[n] for n in names},
                    OUT / product if out_dir is None else out_dir, skip=prod.skip, front_parts=prod.front_parts,
                    candidates={n: c for n, c in (candidates or {}).items() if n in names},
                    n_refine={n: k for n, k in prod.n_refine.items() if n in names}, workers=workers, log=log)
    return {"product": product, "name": prod.name, **res}


# ----------------------------------------------------------------------------- CLI
def _parse_candidates(items: list[str] | None) -> dict[str, tuple[str, ...]]:
    out = {}
    for it in items or ():
        name, _, views = it.partition("=")
        vs = tuple(v.strip() for v in views.split(",") if v.strip())
        bad = [v for v in vs if v not in cameras.VIEWS]
        if not vs or bad:
            raise SystemExit(f"--candidates {it!r}: unknown views {bad or '(none given)'}; known {cameras.VIEWS}")
        out[name] = vs
    return out


def _parse_images(items: list[str]) -> tuple[dict[str, Path], dict[str, str]]:
    """``KIND=PATH`` items -> ({file name: path}, {file name: kind})."""
    images, views = {}, {}
    for it in items:
        kind, sep, path = it.partition("=")
        if not sep or not kind.strip() or not path.strip():
            raise SystemExit(f"--image {it!r}: expected KIND=PATH")
        try:
            view_candidates(kind)
        except ValueError as e:
            raise SystemExit(f"--image {it!r}: {e}") from None
        p = Path(path.strip())
        if p.name in images:
            raise SystemExit(f"--image {it!r}: a second image named {p.name!r}")
        images[p.name], views[p.name] = p, kind.strip()
    return images, views


def _parse_n_refine(items: list[str] | None, names: list[str]) -> int | dict[str, int] | None:
    """``N`` (every image) and/or ``NAME=N`` items -> None, an int, or {name: int} for every name."""
    default, per = None, {}
    for it in items or ():
        name, sep, n = it.rpartition("=")
        try:
            k = int(n)
        except ValueError:
            raise SystemExit(f"--n-refine {it!r}: expected N or NAME=N") from None
        if k < 1:
            raise SystemExit(f"--n-refine {it!r}: N must be >= 1")
        if sep and name not in names:
            raise SystemExit(f"--n-refine {it!r}: no image named {name!r}; images: {names}")
        if sep:
            per[name] = k
        else:
            default = k
    if not per:
        return default
    d = cameras.N_COARSE_POWELL if default is None else default
    return {n: per.get(n, d) for n in names}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m glasses3d.g3d_host.fit_views", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("views", help="fit every photo given on the command line to one GLB")
    v.add_argument("--glb", required=True, type=Path)
    v.add_argument("--image", required=True, action="append", metavar="KIND=PATH",
                   help=f"repeat per photo; KIND is one of: {', '.join(VIEW_KINDS)}")
    v.add_argument("--out-dir", required=True, type=Path)
    v.add_argument("--skip", nargs="*", default=[], help="skip parts whose node name contains any of these")
    v.add_argument("--front-parts", nargs="*", help="pivot parts (default: the geometric front piece)")
    v.add_argument("--candidates", nargs="*", help="NAME=view1,view2 (overrides that photo's kind)")
    v.add_argument("--n-refine", nargs="*", help="N (every photo) and/or NAME=N")
    v.add_argument("--workers", type=int)
    a = sub.add_parser("import", help="copy a product's photos and model into data/reference/<product>/")
    a.add_argument("product", choices=sorted(PRODUCTS))
    a.add_argument("--job-dir", required=True, type=Path)
    a.add_argument("--model", required=True, type=Path)
    f = sub.add_parser("fit", help="fit every reference photo of an imported product")
    f.add_argument("product", choices=sorted(PRODUCTS))
    f.add_argument("--only", nargs="*")
    f.add_argument("--candidates", nargs="*", help="reference=view1,view2 (overrides the label's candidates)")
    f.add_argument("--workers", type=int)
    f.add_argument("--out", type=Path, help="also write the cameras JSON here")
    f.add_argument("--out-dir", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "import":
        man = import_references(args.product, args.job_dir, args.model)
        print(json.dumps({k: {"sha256": v["sha256"], "label": v.get("label")} for k, v in man["files"].items()},
                         indent=1))
        return 0
    if args.cmd == "views":
        images, views = _parse_images(args.image)
        nr = _parse_n_refine(args.n_refine, list(images))
        res = fit_views(args.glb, images, views, args.out_dir, skip=tuple(args.skip),
                        front_parts=None if args.front_parts is None else tuple(args.front_parts),
                        candidates=_parse_candidates(args.candidates), n_refine=nr, workers=args.workers)
        print("wrote", args.out_dir / CAMERAS_JSON)
        print(f"{len(res['images'])} images in {res['seconds']:.0f} s")
        return 0
    out_dir = args.out_dir or OUT / args.product
    res = fit_product(args.product, only=args.only, candidates=_parse_candidates(args.candidates),
                      workers=args.workers, out_dir=out_dir)
    print("wrote", out_dir / CAMERAS_JSON)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(res, indent=1, default=_json), encoding="utf-8")
        print("wrote", args.out)
    print(f"{len(res['images'])} references in {res['seconds']:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
