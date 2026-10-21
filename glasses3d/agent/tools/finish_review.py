"""Two-call visual review receipt, bound to current immutable evidence; no likeness score."""
from pydantic import BaseModel, ConfigDict

from ...g3d_host import compare as C
from .. import differences
from . import arview
from .look_panels import DETAIL_KINDS


class LegacyPhotoDifference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str
    differences: str


class PhotoDifference(LegacyPhotoDifference):
    difference_ids: list[str] | None = None


def evidence(root, head, export):
    """Independently verify the frozen basis and its actual photo/camera bytes."""
    result = {"checkpoint": head["id"], "fingerprint": head["fingerprint"],
              "export_sha256": export["sha256"]}
    try:
        basis = C.load_basis(root)
    except C.NoBasis as error:
        return {**result, "basis": None, "basis_sha256": None, "cameras_sha256": None,
                "photos": [], "unavailable": str(error)}
    refs = sorted(basis.usable_refs(), key=lambda rid: basis.refs[rid].get("view_kind") != "front")
    return {**result, "basis": basis.id, "basis_sha256": arview.sha256(basis.dir / "basis.json"),
            "cameras_sha256": basis.meta["cameras_json_sha256"],
            "unusable_refs": basis.unusable_refs(),
            "unavailable": ("No usable camera for owner photos: " + ", ".join(
                f"{rid} ({row['image']}): {row.get('refusal') or row.get('message') or row.get('status')}"
                for rid, row in basis.unusable_refs().items())) if basis.unusable_refs() else None,
            "photos": [{"ref": rid, "sha256": basis.refs[rid]["photo_sha256"]} for rid in refs]}


def _capture_complete(binding, look):
    if not binding["photos"]:
        return None
    if look.get("status") != "ok":
        return "Usable photo cameras exist, but their comparison evidence was not captured."
    for key in ("basis", "basis_sha256", "cameras_sha256", "export_sha256"):
        if look.get(key) != binding[key]:
            return f"Comparison evidence has a different {key}."
    rows = look.get("views") or []
    expected = {item["ref"]: item["sha256"] for item in binding["photos"]}
    if len(rows) != len(expected) or {row.get("ref") for row in rows} != set(expected):
        return "Comparison evidence must include every usable photo exactly once."
    if not look.get("worn_front_path"):
        return "The same-export worn front view is missing."
    for row in rows:
        if row.get("photo_sha256") != expected[row["ref"]]:
            return f"Photo bytes differ for {row['ref']}."
        if not any(image.get("kind") == "look_full_pair" for image in row.get("images", [])):
            return f"The full photo/drawing pair is missing for {row['ref']}."
        details = row.get("details") or []
        if len(details) != len(DETAIL_KINDS) or {item.get("kind") for item in details} != set(DETAIL_KINDS):
            return f"Native detail evidence or explicit absence reports are missing for {row['ref']}."
        if any(item.get("status") not in {"visible", "unavailable"} or
               item["status"] == "visible" and not item.get("images") or
               item["status"] == "unavailable" and not item.get("reason") for item in details):
            return f"Native detail evidence is incomplete for {row['ref']}."
    required_paths = {str(image["path"]) for row in rows for image in row["images"]}
    required_paths.update(str(image["path"]) for row in rows for detail in row["details"]
                          for image in detail.get("images", []))
    required_paths.add(str(look["worn_front_path"]))
    delivered_paths = {str(image["path"]) for image in look.get("images", [])}
    delivered_paths.update(str(part["path"]) for image in look.get("images", [])
                           if image.get("kind") == "look_composed" for part in image.get("parts", []))
    if not required_paths <= delivered_paths:
        return "Some comparison panels or the worn portrait are not included in the tool output."
    return None


def apply(scene, result, review_call_id, per_photo, difference_updates=None):
    """Add the procedure after existing export/AR checks, before output is encoded."""
    if result["failures"]:
        scene.finish_review = None
        return
    try:
        binding = evidence(scene.root, scene.s.head, result["export"])
        error = _capture_complete(binding, result["look_comparison"])
    except (OSError, ValueError, KeyError, TypeError) as problem:
        error, binding = f"Cannot verify photo review evidence: {problem}", None
    if error:
        scene.finish_review = None
        result.update(status="refused", code="E_FINISH_REVIEW", message=error)
        result["failures"].append({"check": "photo_review_evidence", "code": "E_FINISH_REVIEW", "message": error})
        return
    prior = getattr(scene, "finish_review", None)
    unseen = getattr(scene, "_unseen", getattr(scene, "unseen", ()))
    matches = (prior and prior.get("binding") == binding and prior.get("call_id") not in unseen
               and prior.get("call_id") != result["call_id"])
    refs = [item["ref"] for item in binding["photos"]]
    result["review"] = {"required_refs": refs, "binding": binding,
                        "appearance_is_information": True,
                        "unavailable": binding.get("unavailable")}
    decision = differences.enabled(scene)
    rows = [item.model_dump(exclude_none=True) if isinstance(item, LegacyPhotoDifference) else item
            for item in (per_photo or [])]
    valid_rows = all(isinstance(row, dict) and {"ref", "differences"} <= set(row)
                     and set(row) <= ({"ref", "differences", "difference_ids"} if decision else {"ref", "differences"})
                     and isinstance(row["ref"], str) and isinstance(row["differences"], str)
                     and row["differences"].strip()
                     and (row.get("difference_ids") is None or isinstance(row["difference_ids"], list)
                          and all(isinstance(key, str) for key in row["difference_ids"])) for row in rows)
    if decision and per_photo is not None and (not valid_rows or len({row["ref"] for row in rows}) != len(rows)):
        result.update(status="refused", code="E_FINISH_REVIEW", message="Provide distinct per_photo refs and nonblank differences.")
        result["failures"].append({"check": "photo_review", "code": "E_FINISH_REVIEW", "message": result["message"]})
        return
    if not matches or review_call_id is None:
        if decision and difference_updates is not None:
            result.update(status="refused", code="E_DIFFERENCES",
                          message="Inline difference_updates require a delivered, current first finish review_call_id.")
            result["failures"].append({"check": "differences", "code": "E_DIFFERENCES", "message": result["message"]})
            if not matches:
                scene.finish_review = None
            return
        scene.finish_review = None
        inspection = ("Judge lens colour on the worn, on-face AR images against the owner photos first. "
                      "Use every full photo/drawing pair and native detail for shape, see-through and measurements; "
                      "a better photo-background colour number does not overrule the worn result. "
                      "Report disagreements and explain the kept choice. " if decision else
                      "Inspect every full photo/drawing pair and native detail, plus the worn front. ")
        result.update(status="review_required", code="REVIEW_REQUIRED", message=
            inspection + "Then call finish again "
            "with this call's review_call_id and one nonblank differences entry (or 'none') per required ref. "
            "If there are no usable photo cameras, return an empty per_photo list and acknowledge the unavailable evidence.")
        result["review"]["review_call_id"] = result["call_id"]
        if decision:
            try:
                differences.prepare_finish(scene, result, rows, result["call_id"])
            except ValueError as problem:
                result.update(status="refused", code="E_DIFFERENCES", message=str(problem))
                result["failures"].append({"check": "differences", "code": "E_DIFFERENCES", "message": str(problem)})
            else:
                result["message"] += " Record actionable differences with record_difference; open items prevent acceptance."
        return
    valid = (review_call_id == prior["call_id"] and per_photo is not None and
             len(rows) == len(refs) and valid_rows and
             len({row["ref"] for row in rows}) == len(rows) and {row["ref"] for row in rows} == set(refs))
    result["review"]["review_call_id"] = prior["call_id"]
    if not valid:
        result.update(status="refused", code="E_FINISH_REVIEW", message=
            "Use the delivered review_call_id and exactly one nonblank differences entry (or 'none') for each required ref; "
            "no missing, duplicate or extra photos. With no usable cameras explicitly pass [].")
        result["failures"].append({"check": "photo_review", "code": "E_FINISH_REVIEW", "message": result["message"]})
        return
    result["review"]["per_photo"] = rows
    if decision:
        try:
            open_ids = differences.prepare_finish(scene, result, rows, prior["call_id"], updates=difference_updates)
            stale_ids = []
            for item in result["differences"]:
                if item["status"] != "open":
                    try:
                        differences.validate_evidence(scene, item["evidence_ref"], item["refs"],
                                                      current_export=result["export"])
                    except (ValueError, OSError, KeyError, TypeError):
                        stale_ids.append(item["id"])
            if open_ids or stale_ids:
                result["open_difference_ids"], result["stale_difference_ids"] = open_ids, stale_ids
                raise ValueError("Resolve open differences and support resolved items with a current comparison: "
                                 + ", ".join(open_ids + stale_ids))
        except ValueError as problem:
            result.update(status="refused", code="E_DIFFERENCES", message=str(problem))
            if hasattr(problem, "invalid_difference_ids"):
                result["invalid_difference_ids"] = problem.invalid_difference_ids
            result["failures"].append({"check": "differences", "code": "E_DIFFERENCES", "message": str(problem)})


def delivered(scene, result):
    """Called only after images and lossless bounded output succeed."""
    unseen = getattr(scene, "_unseen", getattr(scene, "unseen", ()))
    if result["call_id"] in unseen:
        scene.finish_review = None
    elif result.get("status") == "review_required":
        scene.finish_review = {"call_id": result["call_id"], "binding": result["review"]["binding"]}
    if hasattr(scene, "save"):
        scene.save()
