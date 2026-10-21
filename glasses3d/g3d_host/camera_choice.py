"""Choose frozen orientation candidates, rechecking their exact pose without optimizing it."""
from __future__ import annotations

from copy import deepcopy
import math

import numpy as np

from . import cameras, fit_views, pose
from .camera import CAMERA_FIELDS, EYE_FIELDS, Camera
from .hinge_domain import OpeningDomain


def opening_uncertainty(record):
    """Keep photographic articulation distinct from the camera's orientation family."""
    opening = record.get("opening_fit") or {}
    observed = record.get("theta_observed") or {}
    return {"status": "unavailable" if not record.get("hinge_axis_xz_mm") else
            "observed" if observed and all(observed.values()) else "not_fully_observed",
            "theta_deg": record.get("theta_deg"), "theta_observed": record.get("theta_observed"),
            "unavailable": opening.get("unavailable"),
            "note": "Photographic arm opening is separate from camera orientation. Choosing an orientation "
                    "retains that candidate's arm pose; it does not measure an unobserved opening. "
                    "Visible arms below are full-scene model occlusions, not proof of the photo's opening."}


def select(record, candidate_id, mesh, roles, foreground, pivot, model_sha, photo_sha):
    """Return a checked replacement fit row and native overlay mask; never run a fit.

    The caller supplies a verified immutable basis and an identical current model. An alternative's
    camera and articulation are inseparable. Every visibility/overlap measurement uses the entire
    posed scene, including objects occluding an arm, and the frozen optional-lens policy.
    """
    row = deepcopy(record)
    orientation = row.get("camera_ambiguity") or {}
    matches = [r for r in orientation.get("candidates", []) if r.get("id") == candidate_id]
    if len(matches) != 1:
        raise ValueError(f"unknown or repeated orientation candidate {candidate_id!r}")
    candidate = matches[0]
    view = candidate.get("view")
    if view not in (row.get("candidates") or fit_views.view_candidates(row["view_kind"])):
        raise ValueError("orientation candidate contradicts the fitted view labels")
    values = candidate.get("camera") or {}
    if any(isinstance(values.get(k, 0.), bool) or not math.isfinite(float(values.get(k, 0.)))
           for k in CAMERA_FIELDS + EYE_FIELDS):
        raise ValueError("orientation camera fields must be finite numbers")
    cam = Camera.from_dict(values)
    if cam.scale <= 0 or cam.perspective < 0:
        raise ValueError("orientation camera needs positive scale and nonnegative perspective")
    lo, hi = cameras.yaw_window(view, cam.yaw)
    if not lo - 1e-8 <= cam.yaw <= hi + 1e-8:
        raise ValueError("orientation candidate fails unchanged view-label yaw domain")
    refusal = (candidate.get("label_refusal") or fit_views.sign_check(row["view_kind"], cam, pivot_norm=pivot)
               or cameras.eye_side_refusal(view, cam, pivot))
    if refusal:
        raise ValueError(f"orientation candidate fails unchanged view-label check: {refusal}")
    axes = row.get("hinge_axis_xz_mm") or {}
    theta = candidate.get("theta_deg") or {}
    if set(theta) != set(axes):
        raise ValueError("orientation candidate lacks its exact photographic arm pose")
    if axes:
        parsed = {k: {"theta_deg": t, "axis_xz_mm": axes[k]} for k, t in theta.items()}
        pose.parse_pose(parsed)
        domain = OpeningDomain(mesh, roles, axes)
        if set(domain.sides) != set(axes) or not domain.valid(theta):
            raise ValueError("orientation candidate fails unchanged physical opening checks")
        posed = pose.posed(mesh, parsed, roles)
    else:
        posed = mesh
    scene = posed.scene()
    # Projection checks all vertices, not only the front hull, before rasterization.
    cameras.project(mesh.frame.to_norm(posed.V), cam)
    policy = row.get("lens_loss") or {}
    optional = set(policy.get("optional_nodes") or ()) if policy.get("mode") == "optional" else set()
    optional_parts = [i for i, name in enumerate(mesh.names) if name in optional and mesh.lens_parts[i]]
    ignore = np.isin(mesh.part, optional_parts) if optional_parts else None
    roi = cameras.matte_roi(foreground)
    metrics = cameras.native_metrics(scene, cam, foreground, roi, ignore)
    usable = fit_views.camera_usability(metrics)
    if not usable["usable"]:
        raise ValueError(f"orientation candidate fails unchanged camera gate: {usable['reason']}")
    full = scene.render(cam, foreground.shape)
    ppm = cameras.px_per_mm_at(cam, mesh.frame, mesh.frame.to_mm(np.asarray(pivot)[None])[0])
    contour = fit_views.contour_summary(metrics["mask"], foreground[roi[1]:roi[3], roi[0]:roi[2]])
    old_camera, old_theta = row.get("camera"), row.get("theta_deg")
    row.update({k: v for k, v in metrics.items() if k not in ("mask", "face_id")})
    row.update(camera=cam.to_dict(), view=view, status="ok", code=None, refusal=None, usability=usable,
               winner_origin="explicit_frozen_orientation_choice", roi=list(roi),
               px_per_mm_at_front_pivot=ppm, contour_mean_mm=metrics["contour_mean_px"] / ppm,
               contour_p90_px=contour["p90"], contour_p90_mm=contour["p90"] / ppm,
               contour_p95_mm=metrics["contour_p95_px"] / ppm,
               outer_contour_mean_mm=metrics["outer_contour_mean_px"] / ppm,
               contour_render_to_photo_mean_px=contour["render_to_photo_mean"],
               contour_photo_to_render_mean_px=contour["photo_to_render_mean"],
               loss=candidate.get("loss_common_level"), loss_common_level=candidate.get("loss_common_level"),
               sign=None)
    # Search-bound diagnostics refer to the old winner, and are retained as provenance below.
    previous_flags = row.get("flags") or []
    old_selection_flags = {"camera_orientation_unresolved", "camera_orientation_unavailable",
                           "label_sign_contradicted", "insufficient_camera_overlap",
                           "off_axis_candidate_at_domain_bound", "opening_fit_candidate_at_eye_bound"}
    row["flags"] = [f for f in previous_flags if f not in old_selection_flags]
    if "camera_orientation_chosen_not_calibrated" not in row["flags"]:
        row["flags"].append("camera_orientation_chosen_not_calibrated")
    orientation.update(status="chosen", selected=candidate_id, selection_changed=old_camera != cam.to_dict(),
                       choice={"candidate": candidate_id, "previous_camera": old_camera,
                               "previous_flags": previous_flags, "checked_without_refit": True,
                               "checks": ["frozen_model_and_photos", "view_label_and_eye", "near_plane",
                                          "physical_arm_opening", "whole_scene_occlusion", "camera_usability"]},
                       note="Astra explicitly chose this saved alternative after inspecting photo evidence. "
                            "All camera gates passed without optimization. This choice is not an independent "
                            "camera calibration and does not establish photographic arm opening.")
    row["camera_ambiguity"] = orientation
    row["candidate_selection"] = {"schema": "g3d.camera_candidate_selection/1", "changed": old_camera != cam.to_dict(),
                                  "reason": "explicit_validated_frozen_orientation_choice",
                                  "selected": deepcopy(candidate)}
    if row.get("off_axis") is not None:
        row["off_axis"] = {"previous_fit": row["off_axis"], "selected_camera": cam.to_dict(),
                           "selected_by": "explicit_frozen_orientation_choice"}
    tags = pose.side_of_faces(mesh, roles)
    visibility = {}
    for side in (1, -1):
        ids = full["face_id"]
        visible = (ids >= 0) & (tags[np.maximum(ids, 0)] == side)
        visibility[f"{side:+d}"] = {"visible_model_pixels": int(visible.sum()),
                                   "photo_foreground_supported_pixels": int((visible & foreground).sum())}
    if axes:
        # An old angle measurement was conditional on another camera. Never certify it at the new one.
        same = old_camera == cam.to_dict() and old_theta == theta
        observed = row.get("theta_observed") if same else {k: False for k in axes}
        previous_opening = deepcopy(row.get("opening_fit"))
        row.update(theta_deg=theta, theta_observed=observed,
                   opening_fit={"schema": "g3d.opening_fit/1", "status": "retained_candidate_pose_not_refitted",
                                "previous_fit": previous_opening, "visibility": visibility,
                                "note": "Opening is retained, not newly measured; camera-conditioned observations "
                                        "are cleared when the orientation changes."})
        provenance = row.get("pose_provenance") or {}
        row["pose_provenance"] = fit_views.articulated_fit_marker(model_sha, photo_sha, row["camera"], theta,
            axes, observed, provenance.get("axis_source"), provenance.get("input_theta_deg"))
        row.pop("rigid_fit", None)
    else:
        row["rigid_fit"] = fit_views.rigid_fit_marker(model_sha, photo_sha, row["camera"])
    orientation["photographic_arm_opening"] = {**opening_uncertainty(row), "visibility": visibility}
    return row, full["mask"]
