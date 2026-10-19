"""Offline coverage of an owner's exact edit request by executable operations.

This is a declared mapping check, not a language model or a keyword classifier. A
caller supplies one labelled item per requested change, including exact text spans.
The check verifies complete textual coverage and availability. It cannot establish
that a human's interpretation is correct; the labelled rendered understanding and
owner confirmation are still required before any geometry is edited.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

SCHEMA = "g3d.edit_capabilities/1"
MAPPING_SCHEMA = "g3d.edit_item_map/1"
RELATIVE_PATH = "inputs/edit-capabilities.json"
NOTICE = ("Explicit mapping only: this check does not understand natural language or certify the interpretation. "
          "Measure the selected source and record its cause before choosing operations; keywords are not a diagnosis. "
          "Each requested change must have its own exact-text item. Labelled selections, rendered amount previews, "
          "owner confirmation, and all physical hard checks remain required.")


def diagnosis_errors(value):
    """Require inspectable, source-bound measurements, not a keyword-to-tool guess."""
    required = {"source_fingerprint", "cause", "measurements", "evidence", "operation_rationale"}
    if not isinstance(value, dict) or set(value) != required:
        return ["Each item needs a source-bound diagnosis before operation mapping."]
    errors = []
    fingerprint = value["source_fingerprint"]
    if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
        errors.append("Diagnosis needs the actual source fingerprint.")
    if any(not isinstance(value[k], str) or not value[k].strip() for k in ("cause", "operation_rationale")):
        errors.append("Diagnosis needs a measured cause and operation rationale.")
    measurements = value["measurements"]
    if not isinstance(measurements, list) or not measurements:
        errors.append("Diagnosis needs measurements with numbers and units.")
    else:
        for row in measurements:
            if (not isinstance(row, dict) or set(row) != {"name", "value", "unit"}
                    or any(not isinstance(row[k], str) or not row[k].strip() for k in ("name", "unit"))
                    or type(row["value"]) not in (int, float) or not math.isfinite(row["value"])):
                errors.append("Every diagnosis measurement needs a name, finite numeric value and unit.")
    evidence = value["evidence"]
    if not isinstance(evidence, list) or not evidence:
        errors.append("Diagnosis needs hashed local measurement evidence.")
    else:
        for row in evidence:
            if not isinstance(row, dict) or set(row) != {"path", "sha256"} or not isinstance(row["path"], str):
                errors.append("Invalid diagnosis evidence reference.")
                continue
            path = Path(row["path"])
            if (not path.is_absolute() or not path.is_file()
                    or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]):
                errors.append("Diagnosis evidence changed or is missing.")
    return errors


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def text_digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def catalog():
    """Read the operation set used by the executor; bind its real implementation bytes."""
    from glasses3d.g3d import owner_mesh
    package = Path(__file__).resolve().parents[1]
    kinds = sorted(owner_mesh.OPERATION_KINDS)
    paths = [*package.glob("g3d/owner_*.py"), package / "agent/owner_edits.py", package / "agent/edits.py",
             package / "agent/tools/owner_edit.py", package / "agent/tools/owner_preview.py", package / "agent/owner_apply.py",
             package / "agent/owner_captures.py", package / "agent/owner_choices.py", Path(__file__).resolve()]
    implementation = {p.relative_to(package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(set(paths))}
    body = {"operation_kinds": kinds, "implementation_sha256": implementation}
    return {**body, "version_sha256": digest(body)}


def check(request_text, mapping=None):
    """Return a $0 diagnostic; incomplete/ambiguous mappings never become ready."""
    if not isinstance(request_text, str) or not request_text.strip():
        raise ValueError("edit coverage needs the owner's exact nonempty request text")
    available = catalog()
    errors, items = [], []
    if mapping is None:
        errors.append({"code": "mapping_required", "message": "Supply an explicit item map; no operation is inferred from keywords."})
    elif (not isinstance(mapping, dict) or set(mapping) != {"schema", "request_text", "items"}
          or mapping.get("schema") != MAPPING_SCHEMA):
        errors.append({"code": "invalid_mapping", "message": "Mapping needs schema, request_text and items only."})
    elif mapping["request_text"] != request_text:
        errors.append({"code": "request_changed", "message": "Mapping request_text differs from the verbatim request."})
    elif not isinstance(mapping["items"], list) or not mapping["items"]:
        errors.append({"code": "mapping_required", "message": "Every requested change needs its own labelled item."})
    else:
        covered, identifiers = set(), set()
        for number, item in enumerate(mapping["items"], 1):
            issue = lambda code, message: errors.append({"code": code, "item": number, "message": message})
            if not isinstance(item, dict) or set(item) not in ({"id", "label", "start", "end", "text", "operation_kinds"},
                    {"id", "label", "start", "end", "text", "operation_kinds", "diagnosis"}):
                issue("invalid_item", "Each item needs id, label, start, end, text, operation_kinds and diagnosis only.")
                continue
            for message in diagnosis_errors(item.get("diagnosis")):
                issue("diagnosis_required", message)
            if (not isinstance(item["id"], str) or not item["id"].strip() or item["id"] in identifiers
                    or not isinstance(item["label"], str) or not item["label"].strip()):
                issue("invalid_label", "Items need distinct nonempty ids and plain-words labels.")
                continue
            identifiers.add(item["id"])
            start, end = item["start"], item["end"]
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(request_text)
                    or not isinstance(item["text"], str) or not item["text"].strip()
                    or request_text[start:end] != item["text"]):
                issue("ambiguous_span", "start/end must identify this exact nonempty text in the verbatim request.")
                continue
            span = set(range(start, end))
            if covered.intersection(span):
                issue("overlapping_items", "Request items must not overlap; repeated text needs distinct exact spans.")
            covered.update(span)
            kinds = item["operation_kinds"]
            if (not isinstance(kinds, list) or not kinds or any(not isinstance(k, str) for k in kinds)
                    or len(set(kinds)) != len(kinds)):
                issue("unmapped_item", "This item needs distinct executable operation kinds.")
                continue
            unknown = sorted(set(kinds) - set(available["operation_kinds"]))
            if unknown:
                issue("unsupported_operation", "Unavailable operations: " + ", ".join(unknown) + ". Build them before a paid edit round.")
            items.append(item)
        missing = [i for i, char in enumerate(request_text) if not char.isspace() and i not in covered]
        if missing:
            errors.append({"code": "unmapped_text", "message": "Some request text has no item mapping.",
                           "character_offsets": missing})
    body = {"schema": SCHEMA, "status": "ready" if not errors else "needs_mapping",
            "cost_usd": "0", "api_calls": 0, "notice": NOTICE, "request_text": request_text,
            "request_sha256": text_digest(request_text), "catalog": available, "items": items,
            "diagnostics": errors}
    return {**body, "record_sha256": digest(body)}


def require_ready(report, request_text):
    """Recompute the saved declaration against current code; do not trust its status."""
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        raise ValueError("edit capability dry run is missing or invalid; run job edit-check first")
    current = check(request_text, {"schema": MAPPING_SCHEMA, "request_text": report.get("request_text"),
                                   "items": report.get("items")})
    if current["status"] != "ready":
        raise ValueError("edit capability dry run refuses: " + "; ".join(row["message"] for row in current["diagnostics"]))
    if current != report:
        raise ValueError("edit capability dry run is stale or changed; repeat job edit-check with the current request and implementation")
    return current


def write_report(path, report):
    """Save only caller-selected local evidence; this does not create an estimate."""
    from . import procs
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    procs.write_json_atomic(path, report)
    return path


def install(root, report):
    """Bind a ready report as approval-hashed round input; never modify a delivery."""
    from . import edits, procs
    root = Path(root).resolve()
    doc = edits.load(root)
    if doc is None:
        raise ValueError("coverage can be installed only in an owner-edit round")
    require_ready(report, doc["request_text"])
    if any(item["diagnosis"]["source_fingerprint"] != doc["source"]["fingerprint"] for item in report["items"]):
        raise ValueError("diagnosis is not of the selected source model")
    path = write_report(edits.confined(root, RELATIVE_PATH), report)
    doc["capability_mapping"] = {key: report[key] for key in ("request_sha256", "items", "notice")}
    doc["immutable_files"][RELATIVE_PATH] = edits.sha(path)
    edits._refresh_brief(root, doc)
    # Persist only the original metadata plus updated input hashes; load() derives
    # the mapping/selected-operations views from their own immutable files.
    metadata = edits.read(root / "inputs/owner-edit.json")
    metadata["immutable_files"] = doc["immutable_files"]
    procs.write_json_atomic(root / "inputs/owner-edit.json", metadata)
    return path


def require_round(root, owner_edit=None):
    """Verify UNDERSTAND coverage or all actually confirmed EDIT operation kinds."""
    from . import edits
    root = Path(root).resolve()
    doc = owner_edit if owner_edit is not None else edits.load(root)
    if doc is None:
        return None
    if doc.get("owner_capture_items"):
        from .owner_captures import load_round_captures
        captures = load_round_captures(root, doc)
        if doc["phase"] == "understand":
            return {"schema": "g3d.owner_capture_admission/1", "status": "unresolved",
                    "item_ids": [row["item_id"] for row in captures], "mutation_authority": False}
        # Capture intake has no fabricated operation mapping. Confirmed EDIT is
        # still checked against the complete, sealed executable understanding.
        understanding = doc["understanding"]
        edits.require_executable(understanding)
        if edits.blocked_items(understanding):
            raise ValueError("every requested item must be executable before a paid edit; understanding still has blocked items")
        understood = understanding.get("items", [])
        if ([item.get("item_id") for item in understood] != [row["item_id"] for row in captures]
                or [item.get("number") for item in understood] != list(range(1, len(captures) + 1))):
            raise ValueError("confirmed understanding must retain every capture item in its original order")
        operations = doc.get("selected_operations", understanding.get("operations", []))
        selected_by_id = {op.get("id"): op for op in operations}
        if not operations or None in selected_by_id or len(selected_by_id) != len(operations):
            raise ValueError("confirmed operations need distinct identities")
        accounted = set()
        for item in understood:
            proposed = json.loads(item["operation_json"])
            group = proposed if isinstance(proposed, list) else [proposed]
            ids = [op.get("id") for op in group]
            if (not ids or len(set(ids)) != len(ids) or accounted.intersection(ids)
                    or any(identity not in selected_by_id for identity in ids)):
                raise ValueError("each capture item needs its own distinct confirmed operations")
            if any(selected_by_id[op["id"]].get("kind") != op.get("kind") for op in group):
                raise ValueError("selected operation kinds differ from the confirmed capture item")
            accounted.update(ids)
        if accounted != set(selected_by_id) or {op.get("kind") for op in operations} - set(catalog()["operation_kinds"]):
            raise ValueError("confirmed capture operations are unavailable or include an unrequested item")
        return {"schema": "g3d.owner_capture_admission/1", "status": "confirmed",
                "item_ids": [row["item_id"] for row in captures], "mutation_authority": True}
    path = edits.confined(root, RELATIVE_PATH)
    if not path.is_file():
        raise ValueError("edit capability dry run is missing; run job edit-check before estimate, approval or paid UNDERSTAND/EDIT")
    report = require_ready(edits.read(path), doc["request_text"])
    source = doc.get("source") or edits.load(root)["source"]
    if any(item["diagnosis"]["source_fingerprint"] != source["fingerprint"] for item in report["items"]):
        raise ValueError("diagnosis is not of the selected source model")
    if doc["phase"] == "edit":
        understanding = doc["understanding"]
        if edits.blocked_items(understanding):
            raise ValueError("every requested item must be executable before a paid edit; understanding still has blocked items")
        operations = doc.get("selected_operations", understanding.get("operations", []))
        understood_items = understanding.get("items", [])
        if ([item.get("number") for item in understood_items] != list(range(1, len(report["items"]) + 1))):
            raise ValueError("confirmed understanding must have one numbered item for every dry-run item, in the same order")
        selected_by_id = {op.get("id"): op for op in operations}
        if None in selected_by_id or len(selected_by_id) != len(operations):
            raise ValueError("confirmed operations need distinct identities")
        accounted_ids = set()
        for declared, understood in zip(report["items"], understood_items):
            try:
                proposed = json.loads(understood["operation_json"])
                group = proposed if isinstance(proposed, list) else [proposed]
                ids = [op["id"] for op in group]
                selected_kinds = {selected_by_id[identity]["kind"] for identity in ids}
            except (ValueError, KeyError, TypeError):
                raise ValueError("every confirmed understanding item needs its exact selected operations") from None
            if not ids or accounted_ids.intersection(ids) or len(set(ids)) != len(ids):
                raise ValueError("each requested item needs its own distinct confirmed operations")
            accounted_ids.update(ids)
            if selected_kinds != set(declared["operation_kinds"]):
                raise ValueError(f"confirmed operations for dry-run item {declared['id']} differ from its mapping")
        if accounted_ids != set(selected_by_id):
            raise ValueError("confirmed operations include an item outside the complete dry-run mapping")
        actual = {op.get("kind") for op in operations}
        expected = {kind for item in report["items"] for kind in item["operation_kinds"]}
        if actual != expected:
            raise ValueError("confirmed operation kinds differ from the complete dry-run mapping; repeat job edit-check for the confirmed operations")
    return report
