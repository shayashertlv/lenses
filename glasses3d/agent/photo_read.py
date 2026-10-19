"""Read fixed or explicitly posed world points from a frozen photo basis, without a model surface.

Uses the facet reader's pixel/crossing model and numerical primitives. Uncertainty is
conditional on calibration; residuals never enlarge errors to make observations agree.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import itertools
import math
from pathlib import Path
import re

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.stats import chi2

from glasses3d.g3d_host import compare as C, pose as PO
from glasses3d.g3d_host.camera import project_mm
from .procs import write_json_atomic
from .temple_lab import readings as R

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,47}$")
UNCERTAINTY = (
    "Errors are conditional on the frozen calibration: pick/crossing error plus the shared 1 px camera floor, "
    "and a separate angular term: 0.3 degrees for fixed points or observed openings, 10 degrees when a temple opening "
    "is unmeasured. These are stated assumptions from each photo's provenance, not fitted confidence intervals. "
    "They do not certify camera accuracy or photo correspondence. "
    "A two-photo residual cannot detect a slide along the epipolar line."
)


def posed_points(points, basis, rid, temple_side=None, angle_delta=0.0):
    """Neutral world XYZ to the explicit side's frozen photo pose; fixed parts stay fixed."""
    points = np.asarray(points, float)
    if temple_side is None:
        return points
    poses = PO.parse_pose(basis.pose(rid))
    if not poses:
        return points  # no known hinge axis: use camera yaw sensitivity instead
    if temple_side not in poses:
        raise ValueError(f"{rid}: frozen pose has no temple side {temple_side:+d}")
    theta, axis = poses[temple_side]
    return PO.rot_about_hinge(points, temple_side, theta + angle_delta, axis)


def world_matrix(basis, rid, temple_side=None, angle_delta=0.0):
    """Analytic projective matrix for world XYZ, including an explicitly requested temple pose."""
    camera = basis.camera(rid)
    has_pose = temple_side is not None and bool(PO.parse_pose(basis.pose(rid)))
    if angle_delta and not has_pose:
        camera = replace(camera, yaw=camera.yaw + angle_delta)
    # R's chart is (-worldZ, worldY, worldX) when side=+1 and hinge_z=0.
    chart = np.array([[0, 0, -1, 0], [0, 1, 0, 0], [1, 0, 0, 0], [0, 0, 0, 1]], float)
    matrix = R.camera_matrix(camera, basis.frame, 1, 0.0) @ chart
    if has_pose:
        points = posed_points(np.vstack([np.zeros(3), np.eye(3)]), basis, rid, temple_side, angle_delta)
        affine = np.eye(4)
        affine[:3, :3], affine[:3, 3] = (points[1:] - points[0]).T, points[0]
        matrix = matrix @ affine
    return matrix


def _pixel_covariance(basis, parsed, matrices, point, temple_side):
    sources = [R.camera_source(basis, r["rid"]) for r in parsed]
    maps = []
    for row, matrix, source in zip(parsed, matrices, sources):
        source_matrix = world_matrix(basis, source, temple_side)
        maps.append(np.eye(2) if source == row["rid"] else
                    R._jac_px(matrix, point) @ np.linalg.pinv(R._jac_px(source_matrix, point)))
    covariance = np.zeros((2 * len(parsed), 2 * len(parsed)))
    for i, row in enumerate(parsed):
        own = R.transport_px(basis, row["rid"])
        covariance[2*i:2*i+2, 2*i:2*i+2] += row["cov"] + np.eye(2) * own**2
        for j in range(len(parsed)):
            if sources[i] == sources[j]:
                covariance[2*i:2*i+2, 2*j:2*j+2] += R.CAMERA_PX_SD**2 * maps[i] @ maps[j].T
    return covariance, sources


def _solve(basis, parsed, temple_side):
    matrices = [world_matrix(basis, r["rid"], temple_side) for r in parsed]
    pixels = [r["uv"] for r in parsed]
    point = R._dlt(matrices, pixels)
    reference = np.asarray(basis.pivot_mm, float)
    condition = R._cond(matrices, point if point is not None and np.isfinite(point).all() else reference)
    if not np.isfinite(condition) or condition > R.COND_MAX:
        raise ValueError(R.one_line([r["photo"] for r in parsed], condition))
    if point is None or not np.isfinite(point).all() or min(R._project(h, point)[1] for h in matrices) <= R.NEAR:
        raise ValueError("these pixels do not triangulate to a finite point in front of every camera")
    covariance, sources = _pixel_covariance(basis, parsed, matrices, point, temple_side)
    inverse = np.linalg.inv(np.linalg.cholesky(covariance))
    point = R._solve(matrices, pixels, inverse, point)[0]
    covariance, sources = _pixel_covariance(basis, parsed, matrices, point, temple_side)
    inverse = np.linalg.inv(np.linalg.cholesky(covariance))
    point = R._solve(matrices, pixels, inverse, point)[0]
    condition = R._cond(matrices, point)
    if not np.isfinite(condition) or condition > R.COND_MAX:
        raise ValueError(R.one_line([r["photo"] for r in parsed], condition))
    if not np.isfinite(point).all() or min(R._project(h, point)[1] for h in matrices) <= R.NEAR:
        raise ValueError("the fitted point crosses a camera near plane")
    fitted = [R._project(h, point)[0] for h in matrices]
    for row, uv in zip(parsed, fitted):
        direct = project_mm(posed_points([point], basis, row["rid"], temple_side),
                            basis.camera(row["rid"]), basis.frame)[0]
        if np.max(np.abs(direct - uv)) > R.SELF_CHECK_PX:
            raise RuntimeError("photo_read world projection differs from project_mm")
    jacobian = np.vstack([R._jac_px(h, point) for h in matrices])
    weighted = inverse @ jacobian
    cov_pick = np.linalg.inv(weighted.T @ weighted)
    angle_evidence = {r['rid']: (R.opening_uncertainty(basis, r['rid'], temple_side) if temple_side is not None else
                     {'sd_deg': R.THETA_SD_DEG, 'status': 'fixed_point_camera_yaw_assumption'}) for r in parsed}
    source_sd = {source: max(angle_evidence[r['rid']]['sd_deg'] for i,r in enumerate(parsed) if sources[i] == source)
                 for source in dict.fromkeys(sources)}
    shifts, pixel_shifts = {}, []
    for source in dict.fromkeys(sources):
        moved, shifted_pixels = [], []
        for sign in (1, -1):
            hm = [world_matrix(basis, r["rid"], temple_side,
                               sign * source_sd[source] if sources[i] == source else 0)
                  for i, r in enumerate(parsed)]
            moved.append(R._solve(hm, pixels, inverse, point)[0])
            shifted_pixels.append(np.concatenate([R._project(h, point)[0] for h in hm]))
        shifts[source] = (moved[0] - moved[1]) / 2
        pixel_shifts.append((shifted_pixels[0] - shifted_pixels[1]) / 2)
    cov_angle = sum((np.outer(v, v) for v in shifts.values()), start=np.zeros((3, 3)))
    residual_covariance = covariance + sum((np.outer(v, v) for v in pixel_shifts), start=np.zeros_like(covariance))
    residual_vectors = [q - uv for q, uv in zip(fitted, pixels)]
    whitening = np.linalg.inv(np.linalg.cholesky(residual_covariance))
    residual = whitening @ np.concatenate(residual_vectors)
    jw = whitening @ jacobian
    chi = max(float(residual @ residual - residual @ jw @ np.linalg.solve(jw.T @ jw, jw.T @ residual)), 0.0)
    dof = 2 * len(parsed) - 3
    threshold = float(chi2.ppf(1 - R.RESIDUAL_P, dof))
    return {"xyz_mm": point.tolist(), "sd_xyz_mm": np.sqrt(np.diag(cov_pick + cov_angle)).tolist(),
            "cov_xyz_mm2": (cov_pick + cov_angle).tolist(), "cov_pick_camera_floor_mm2": cov_pick.tolist(),
            "cov_angular_mm2": cov_angle.tolist(), "angular_shift_mm": {k: v.tolist() for k, v in shifts.items()},
            "angular_sd_deg": source_sd, "opening_uncertainty": angle_evidence, "camera_sources": sources,
            "condition": condition, "condition_limit": R.COND_MAX,
            "uv_fit": [q.tolist() for q in fitted], "residual_px": [float(np.linalg.norm(v)) for v in residual_vectors],
            "residual_sd": [math.sqrt(max(float(v @ np.linalg.solve(residual_covariance[2*i:2*i+2, 2*i:2*i+2], v)), 0))
                            for i, v in enumerate(residual_vectors)],
            "chi2": chi, "dof": dof, "chi2_limit": threshold,
            "consistent_with_stated_error": chi <= threshold}, matrices


def read_point(basis, name, picks, *, kind="corner", temple_side=None):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("reading name: a letter followed by at most 47 letters, digits, _ or -")
    if temple_side is not None and (isinstance(temple_side, bool) or temple_side not in (-1, 1)):
        raise ValueError("temple_side must be null (fixed part), +1 or -1")
    parsed = R._parse_picks(basis, picks, kind, None)
    if len({r["rid"] for r in parsed}) != len(parsed):
        raise ValueError("a photo may occur only once, including aliases")
    for row in parsed:
        basis.photo(row["rid"])  # verify the actual pixels before measuring
    result, matrices = _solve(basis, parsed, temple_side)
    warnings = []
    if not result["consistent_with_stated_error"]:
        warnings.append({"code": "W_RESIDUAL", "message": "These marked pixels are not one point within their stated error. "
                         "Check feature correspondence and calibration; the reported covariance has not been enlarged.",
                         "chi2": result["chi2"], "dof": result["dof"], "limit": result["chi2_limit"]})
    jac = [R._jac_px(h, result["xyz_mm"]) for h in matrices]
    los = [np.linalg.svd(j)[2][-1] for j in jac]
    lines = []
    for i, row in enumerate(parsed):
        directions = []
        for j, other in enumerate(parsed):
            if i == j or abs(float(los[i] @ los[j])) >= math.cos(math.radians(R.SAME_DIR_DEG)):
                continue
            direction = jac[i] @ los[j]
            unit, angle = R._line_dir(direction)
            directions.append({"to": [other["photo"]], "deg": angle})
            if kind == "crossing":
                near = [k + 1 for k, edge in enumerate(row["lines"])
                        if R._angle_deg(unit, edge["t"]) < R.EPIPOLAR_WARN_DEG]
                if near:
                    warnings.append({"code": "W_ALONG_LINE", "photo": row["photo"], "to": other["photo"],
                                     "edges": near, "message": "An edge lies along the epipolar direction; a slide "
                                     "along it is weakly constrained by this photo pair."})
        lines.append(directions)
        if kind == "crossing" and row["crossing_deg"] < R.SHALLOW_DEG:
            warnings.append({"code": "W_SHALLOW_CROSSING", "photo": row["photo"], "degrees": row["crossing_deg"],
                             "message": "Shallow crossing: position error grows along the traced edges."})
    pairs = []
    if len(parsed) > 2:
        for indices in itertools.combinations(range(len(parsed)), 2):
            subset = [parsed[i] for i in indices]
            try:
                pair, _ = _solve(basis, subset, temple_side)
                pairs.append({"status": "ok", "photos": [r["photo"] for r in subset], **pair})
            except (ValueError, np.linalg.LinAlgError) as exc:
                pairs.append({"status": "refused", "photos": [r["photo"] for r in subset], "message": str(exc)})
    disagreement = []
    for a, b in itertools.combinations([p for p in pairs if p["status"] == "ok"], 2):
        delta = np.asarray(a["xyz_mm"]) - b["xyz_mm"]
        # SD sums bound each axis difference even when pairs share photos/camera errors.
        bound = np.asarray(a["sd_xyz_mm"]) + b["sd_xyz_mm"]
        ratio = float(np.max(np.abs(delta) / np.maximum(bound, 1e-12)))
        disagreement.append({"pairs": [a["photos"], b["photos"]], "distance_mm": float(np.linalg.norm(delta)),
                             "axis_difference_mm": delta.tolist(), "axis_sd_sum_bound_mm": bound.tolist(),
                             "max_axis_error_ratio": ratio})
    if any(d["max_axis_error_ratio"] > 3 for d in disagreement):
        warnings.append({"code": "W_PAIR_DISAGREEMENT", "message": "Photo pairs disagree by more than three times "
                         "their summed conditional axis errors. A small two-photo residual does not validate calibration.",
                         "worst": max(disagreement, key=lambda d: d["max_axis_error_ratio"])})
    return {"status": "ok", "name": name, "kind": kind, "temple_side": temple_side,
            "coordinate_space": "neutral world millimetres: +Y up, +Z forward", "uncertainty_note": UNCERTAINTY,
            **result, "photos": [r["photo"] for r in parsed], "rids": [r["rid"] for r in parsed],
            "uv": [r["uv"].tolist() for r in parsed], "uv_cov": [r["cov"].tolist() for r in parsed],
            "crossing_deg": [r["crossing_deg"] for r in parsed] if kind == "crossing" else None,
            "lines": lines, "warnings": warnings, "pairs": pairs, "pair_disagreement": disagreement,
            "picks": [{"photo": r["photo"], "uv": r["uv_in"], "edges": r["edges_in"], "sd_px": r["sd_px"]} for r in parsed]}


def pick_sheet(basis, record, path):
    n = len(record["photos"])
    width = 2 * R.PANEL_W + 3 * R.GAP_PX
    sheet = Image.new("RGB", (width, 65 + n * (R.PANEL_H + R.LABEL_PX + R.GAP_PX)), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=13)
    draw.text((4, 3), f"{record['name']}: XYZ {np.round(record['xyz_mm'], 2).tolist()} mm; "
                     f"conditional SD {np.round(record['sd_xyz_mm'], 2).tolist()} mm", fill="black", font=font)
    draw.text((4, 23), "Clean photo left; pick green, traced edges cyan, fitted point magenta, epipolar lines orange.", fill="black", font=font)
    draw.text((4, 43), "Calibration accuracy is not certified. " + ", ".join(sorted({w['code'] for w in record['warnings']})), fill="black", font=font)
    for i, rid in enumerate(record["rids"]):
        clean, marked, box = R._panel(basis.photo(rid), record["picks"][i], record["uv"][i], record["uv_fit"][i],
                                      record["uv_cov"][i], record["lines"][i])
        y = 65 + i * (R.PANEL_H + R.LABEL_PX + R.GAP_PX)
        for col, panel in enumerate((clean, marked)):
            sheet.paste(panel, (R.GAP_PX + col * (R.PANEL_W + R.GAP_PX) + (R.PANEL_W-panel.width)//2,
                               y + (R.PANEL_H-panel.height)//2))
        draw.text((4, y + R.PANEL_H), f"{record['photos'][i]}: {box}; residual {record['residual_px'][i]:.2f} px", fill="black", font=font)
    sheet.save(path)


def measure(root, call_id, points, basis_id=None):
    root = Path(root).resolve()
    basis = C.load_basis(root, basis_id)
    if not isinstance(points, list) or not 1 <= len(points) <= 8:
        raise ValueError("points must contain 1..8 named readings")
    if len({p.get("name") for p in points}) != len(points):
        raise ValueError("reading names must be distinct within one call")
    target = (root / "work/photo_read" / hashlib.sha256(str(call_id).encode()).hexdigest()[:24]).resolve()
    if not target.is_relative_to(root / "work"):
        raise ValueError("photo_read output must stay inside the job's work directory")
    target.mkdir(parents=True, exist_ok=False)
    records, images = [], []
    for index, point in enumerate(points):
        try:
            record = read_point(basis, point["name"], point["picks"], kind=point.get("kind", "corner"),
                                temple_side=point.get("temple_side"))
            path = target / f"reading-{index+1:02d}.png"
            pick_sheet(basis, record, path)
            images.append({"path": path.relative_to(root).as_posix(), "view": point["name"], "kind": "photo_read"})
        except (ValueError, np.linalg.LinAlgError) as exc:
            record = {"status": "refused", "code": "E_PHOTO_READ", "name": point.get("name"), "message": str(exc)}
        records.append(record)
    count_ok = sum(r["status"] == "ok" for r in records)
    result = {"schema": "g3d.photo_read/1", "status": "ok" if count_ok == len(records) else "partial" if count_ok else "refused",
              "call_id": call_id, "basis": basis.id, "basis_sha256": C.sha256_file(basis.dir / "basis.json"),
              "cameras_sha256": C.sha256_file(basis.dir / "cameras.json"), "model_sha256": (basis.meta.get("model") or {}).get("sha256"),
              "photo_sha256": {rid: row["photo_sha256"] for rid, row in basis.refs.items()},
              "uncertainty_note": UNCERTAINTY, "readings": records, "images": images}
    report = target / "report.json"
    write_json_atomic(report, result)
    return {**result, "report_path": report.relative_to(root).as_posix(), "report_sha256": C.sha256_file(report)}
