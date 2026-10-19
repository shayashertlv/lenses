"""Retain orientation alternatives that a silhouette fit cannot identify as physical truth.

These are diagnostics on the fitted mesh/pose. They neither change the installed camera nor
relax a camera check. A lower silhouette loss is reported as such, never as calibrated orientation.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np

from . import cameras


def profile_region(yaw: float) -> str:
    angle = abs((float(yaw) + 180.) % 360. - 180.)
    return "front_of_profile" if angle < 90. else "behind_profile" if angle > 90. else "profile"


def opposite_profile(view, cam):
    """Mirror-silhouette twin and the disjoint half of the existing signed side yaw window.

    For an X-symmetric orthographic object the twin is (180-yaw, -pitch), with a
    full-turn wrap for right views. Negating pitch alone does not cross the profile.
    """
    if view not in cameras.SIDE_VIEWS:
        raise ValueError("opposite-profile evidence requires a signed side view")
    prior = cameras.PRIOR_YAW[view]
    lo, hi = cameras.yaw_window(view, cam.yaw)
    twin = replace(cam, yaw=2. * prior - cam.yaw, pitch=-cam.pitch)
    # At exact profile keep both one-sided continuations: neither is yet represented.
    if cam.yaw == prior:
        return [(replace(twin, yaw=(lo + prior) / 2.), (lo, prior)),
                (replace(twin, yaw=(prior + hi) / 2.), (prior, hi))]
    return [(twin, (lo, prior) if cam.yaw > prior else (prior, hi))]


def profile_probes(view, cam, fg, scene, pivot, ignore_faces=None, *, off_axis=False):
    """Refine a reflected start while preventing it from silently returning across profile."""
    vf = cameras.ViewFit(view, fg, scene, pivot)
    vf.set_ignore(ignore_faces)
    if off_axis:
        vf.eye_limit = float(scene.max_radius)
    out = []
    for twin, window in opposite_profile(view, cam):
        eyes = {"eye_x": twin.eye_x, "eye_y": twin.eye_y} if off_axis else {}
        start = cameras.align(scene, vf.levels["fit"], twin.yaw, twin.pitch, twin.roll, twin.perspective, **eyes)
        probe, _ = vf.staged(vf.levels["fit"], start, cameras.STAGES_FIT, cameras.MAXFEV["twin"],
                             yaw_win=window)
        points = np.vstack([cameras.project(scene.hull, c) for c in (cam, probe)])
        final = cameras.Level(fg, cameras.FINAL_W, "orientation_final", margin=cameras.FINAL_ROI_MARGIN,
                              include_xyxy=(*points.min(0), *points.max(0)))
        final.ignore_faces = vf.ignore_faces
        final.required_hull = vf.levels["fit"].required_hull
        probe, _ = vf.staged(final, probe, cameras.STAGES_FINAL, cameras.MAXFEV["twin"], yaw_win=window)
        out.append((probe, window))
    return out, vf.evals


def evidence(best, results, fg, scene, pivot, ignore_faces=None, *, off_axis=False):
    """Serializable orientation report plus (record, scene, camera) tuples for native overlays.

    All losses share one final grid, even when opening candidates have different frozen poses.
    Side probes hold the selected opening fixed: they are not an independent joint pose search.
    """
    view = best["view"]
    if view not in cameras.SIDE_VIEWS + cameras.AXIAL_VIEWS:
        return None, []
    entries = []
    for index, result in enumerate(results):
        posed = result.get("posed_mesh")
        candidate_scene = posed.scene() if posed is not None else scene
        camera = result["camera"]
        record = {"id": f"view_{index + 1}", "view": result["view"],
                  "source": "installed" if result is best else "view_search",
                  "camera": camera.to_dict(),
                  "profile_region": profile_region(camera.yaw),
                  "theta_deg": dict(result.get("opening", {}).get("theta_deg", {})),
                  "overlay": None}
        entries.append((record, candidate_scene, camera))
    selected = next(entry for entry in entries if entry[0]["source"] == "installed")
    if view in cameras.SIDE_VIEWS:
        probes, evaluations = profile_probes(view, best["camera"], fg, selected[1], pivot,
                                             ignore_faces, off_axis=off_axis)
        best["fit"].evals += evaluations
        for index, (camera, window) in enumerate(probes):
            entries.append(({"id": f"profile_twin_{index + 1}", "view": view,
                             "source": "opposite_profile_probe", "camera": camera.to_dict(),
                             "profile_region": profile_region(camera.yaw), "yaw_window": list(window),
                             "theta_deg": dict(selected[0]["theta_deg"]), "overlay": None},
                            selected[1], camera))
    points = np.vstack([cameras.project(sc.hull, cam) for _, sc, cam in entries])
    level = cameras.Level(fg, cameras.FINAL_W, "orientation_common", margin=cameras.FINAL_ROI_MARGIN,
                          include_xyxy=(*points.min(0), *points.max(0)))
    level.ignore_faces = ignore_faces
    for record, candidate_scene, camera in entries:
        record["loss_common_level"] = float(level.loss(candidate_scene, camera))
        metrics = cameras.native_metrics(candidate_scene, camera, fg, cameras.matte_roi(fg), ignore_faces)
        record.update({key: value for key, value in metrics.items()
                       if key in ("iou", "contour_mean_px") or key.endswith("_lens_optional")})
        record["label_refusal"] = cameras.eye_side_refusal(record["view"], camera, pivot)
    preferred = min(entries, key=lambda entry: entry[0]["loss_common_level"])[0]
    report = {"schema": "g3d.camera_orientation/1", "status": "unresolved",
              "kind": "profile_front_back" if view in cameras.SIDE_VIEWS else "axial_front_back",
              "selected": selected[0]["id"], "preferred_by_silhouette": preferred["id"],
              "selection_changed": False, "evidence": "photo silhouette against current model",
              "note": "Orientation is unresolved: silhouette overlap alone does not establish which side of "
                      "profile or top/front-back orientation the photo shows. The installed camera is retained. "
                      "Inspect the alternative photo overlays and visible front/temple occlusions before using "
                      "this camera for geometry measurements. A lower silhouette loss is not calibrated pose evidence.",
              "pose_scope": "Each view candidate retains its fitted opening; opposite-profile probes hold the "
                            "installed opening fixed and do not re-estimate it.",
              "candidates": [record for record, _, _ in entries]}
    return report, entries
