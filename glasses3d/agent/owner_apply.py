"""Apply an already shown, sealed owner choice locally, without an inference round."""
from __future__ import annotations

import html
from pathlib import Path

from . import delivery, edit_capabilities, edits, owner_edits, procs
from .job_scene import JobScene


def _choice(root, doc):
    binding = doc["understanding"]["amount_preview"]
    preview = edits.read(edits.confined(root, binding["preview_receipt"]))
    choice = next(row for row in preview["options"] if row["id"] == doc["selected_preview_option"])
    # All evidence was verified by edits.load; resolve copies by hash, never by the
    # original preview's absolute paths (which can point at another job).
    paths = {}
    for name, digest in preview["evidence_files"].items():
        if name.endswith(".png"):
            paths.setdefault(digest, edits.confined(root, name))
    images = []
    for image in [*choice.get("full_pairs", []), *choice.get("target_details", [])]:
        path = paths.get(image["sha256"])
        if path is None or edits.sha(path) != image["sha256"]:
            raise ValueError("selected preview image is missing or changed")
        images.append({**image, "path": str(path)})
    if not images:
        raise ValueError("selected preview has no photo/before/after evidence")
    return choice, images


def _page(root, report):
    esc = html.escape
    label = "TOOL TEST on a copy" if report["tool_test"] else "Owner-selected edit on a copy"
    body = ["<!doctype html><meta charset='utf-8'><title>Applied preview</title>",
            "<style>body{font:18px system-ui;margin:2em;max-width:1800px}img{max-width:100%}</style>",
            f"<h1>{label}: {esc(report['option']['id'])}</h1>",
            "<p>Exact shown operations applied. 0 API requests; $0. "
            "Visual acceptance remains the owner's decision.</p>",
            "<p>The photo-first comparisons are the sealed selected preview; the applied model "
            "has the exact same verified scene fingerprint.</p>"]
    for image in report["images"]:
        relative = Path("../..") / Path(image["path"]).relative_to(root)
        body.append(f"<figure><figcaption>{esc(image.get('view', 'Photo / before / selected result'))}</figcaption>"
                    f"<a href='{esc(relative.as_posix())}'><img src='{esc(relative.as_posix())}'></a></figure>")
    path = root / "work/owner_edit/applied-preview.html"
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def apply_preview(edit_root, confirmation_text, option_id, *, destination=None, tool_test=False):
    """Confirm an exact option and apply it to a fresh copy; never invoke a paid runner.

    tool_test labels the owner's authorization to demonstrate an option. It does
    not claim they selected that amount as the final product or accepted its look.
    """
    if not isinstance(option_id, str) or not option_id.strip():
        raise ValueError("an exact shown preview option is required")
    root = edits.create_edit(edit_root, confirmation_text, option_id=option_id, destination=destination, record=False)
    doc = edits.load(root)
    if not doc.get("selected_preview"):
        raise ValueError("offline apply requires an existing sealed rendered option")
    edit_capabilities.require_round(root, doc)
    choice, images = _choice(root, doc)
    scene = JobScene(root)
    try:
        scene.start()
        source_head = dict(scene.s.head)
        if (source_head["fingerprint"] != doc["source"]["fingerprint"] or
                scene.s.fingerprint() != doc["source"]["fingerprint"]):
            raise ValueError("offline apply must start from the exact unchanged selected source")
        operation_ids = [op["id"] for op in doc["selected_operations"]]
        applied = owner_edits.apply(scene, operation_ids, "host-preview-apply")
        if applied.get("status") != "ok":
            raise ValueError("selected preview apply refused: " + str(applied))
        if scene.s.fingerprint() != doc["selected_preview"]["fingerprint"]:
            raise ValueError("applied scene differs from the exact selected preview")
        scope = owner_edits.ScopeGuard(scene.owner_edit_authority).verify(scene)
        if not scope.get("ok"):
            raise ValueError("selected preview failed scope verification: " + str(scope))
        exported = scene.seeing.export_head("host-preview-export")
        if exported.get("status") != "exported" or not exported.get("validate_spec", {}).get("ok"):
            raise ValueError("selected preview failed strict export validation")
        if exported.get("fingerprint") != doc["selected_preview"]["fingerprint"]:
            raise ValueError("export does not belong to the exact selected preview")
        if not exported.get("print_check", {}).get("ok"):
            raise ValueError("selected preview failed retained print verification")
        if exported["sha256"] != choice["export"]["sha256"]:
            raise ValueError("applied export bytes differ from the shown preview")
        # A concurrent modification of copied authority/evidence cannot be reported
        # as successful just because an earlier load passed.
        final_doc = edits.load(root)
        if final_doc != doc:
            raise ValueError("selected preview authority changed during application")
        edit_capabilities.require_round(root, final_doc)
        report = {"schema": "g3d.owner_applied_preview/1", "status": "applied", "tool_test": bool(tool_test),
                  "visual_acceptance": False, "api_requests": 0, "settled_usd": "0.000000",
                  "request_text": doc["request_text"], "confirmation_text": confirmation_text,
                  "option": doc["selected_preview"], "source_head": source_head,
                  "checkpoint": scene.s.head, "exact_preview_fingerprint": True,
                  "apply_receipt": applied, "scope_check": scope, "export": exported,
                  "preview_export_sha256": choice["export"]["sha256"],
                  "export_matches_preview_bytes": exported["sha256"] == choice["export"]["sha256"],
                  "images": images, "image_provenance": "sealed selected preview, exact applied fingerprint",
                  "round_path": str(root)}
        basis_id = doc["source"]["basis"]
        report["source_basis"] = {"basis": basis_id,
            "basis_sha256": edits.sha(edits.confined(root, Path("cameras") / basis_id / "basis.json"))}
        from .tools import arview
        ar = arview.capture(root, exported, "host-preview-ar")
        exit_head = scene.exit_checkpoint()
        published = delivery.deliver_owner_applied(root, exported, exit_head, ar, report)
        path = root / "work/owner_edit/applied-preview.json"
        procs.write_json_atomic(path, report)
        page = _page(root, report)
        if destination is None:
            edits.record_round(edit_root, root)
        return {**report, "report": str(path), "review_page": str(page), "delivery": published}
    finally:
        scene.close()
