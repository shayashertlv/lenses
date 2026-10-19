"""One separately approved full-tool edit of an immutable, unpreviewed owner item.

Creating a round performs local input checks/copies only. It never grants an approval,
starts Blender, or calls a model. Full-tool authority belongs to its subsequent approval.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import shutil
from uuid import uuid4

from . import delivery, edits, jobs, owner_captures, procs
from .journal import CheckpointIndex

SCHEMA = "g3d.full_edit_item/1"
RELATIVE_PATH = "inputs/full-edit-item.json"
UNDERSTANDING_PATH = "work/owner_edit/understanding.json"
BASELINE_PATH = "inputs/fallback-source-snapshot.json"
ALLOWED_DISPOSITIONS = {"cannot_preview", "needs_clarification", "unsupported"}

INSTRUCTIONS = """Full edit of one owner item (full_edit_item).
This is an edit of an existing delivered model, not a new product build. Start from the
exact delivered checkpoint supplied here. Change only what the selected owner's item
asks; keep all other geometry, materials, UVs and transforms as they are. The complete
normal modeling toolset is available, including Blender code and specialist tools.
The owner's product photos still decide the look beyond the explicitly requested change.
The capture is a picture of the current model with the owner's marks, not a product
photo. Read the clean and annotated capture, its labels, camera, surface anchors and
footprint together with the owner's words verbatim and the sealed understanding.
Source mark resolutions identify source polygons, not permanent indices after a topology
change. Background and ambiguous correspondence remain unresolved; do not invent targets.
Use the ordinary model map to inspect actual targets. Preserve every existing physical
hard refusal. Finish through the ordinary finish review, with a short report of what
changed and anything not finished in the finish summary. The host measures geometry,
materials, UVs, transforms and changes outside the marked region as side effects for
the owner to judge by eye; these measurements are reports, not additional quality gates.
"""

SPECIALIST_INSTRUCTIONS = """This specialist belongs to a full_edit_item round editing an existing delivered model.
Work only on the selected owner's item below; preserve unrequested geometry, materials,
UVs and transforms. Product photos decide the look beyond the explicit request. Attached
owner captures show the current source model with the owner's marks, not product photos.
Read the owner's exact words and sealed understanding with those captures. Source mark
resolutions may become stale after geometry or topology changes; unresolved stays unresolved.
Keep this stage's existing physical checks and finish procedure. Report what changed and
anything not finished. Changes outside the item are measured for the owner's judgement.
"""


def _source(doc):
    """Recheck the selected delivery, never follow its possibly newer current pointer."""
    source = doc["source"]
    root = Path(source["job"]).resolve()
    saved = delivery.load_delivery(root, source["delivery_id"])
    report = saved["report_data"]
    if (saved["glb_sha256"] != source["export_sha256"]
            or report["fingerprint"] != source["fingerprint"]
            or report["checkpoint"] != source["checkpoint"]):
        raise ValueError("The selected source delivery changed; create a new understanding.")
    model = Path(saved["model"])
    if edits.sha(model) != source["export_sha256"]:
        raise ValueError("The selected source export bytes changed.")
    row = CheckpointIndex(root / "checkpoints").get(source["checkpoint"])
    if (row is None or row["fingerprint"] != source["fingerprint"]
            or row["sha256"] != source["checkpoint_sha256"]):
        raise ValueError("The selected source checkpoint fingerprint changed or is unavailable.")
    if edits.sha(edits.confined(root / "checkpoints", row["file"])) != source["checkpoint_sha256"]:
        raise ValueError("The selected source checkpoint bytes changed.")
    jobs.load_job(root)
    return saved


def _shown_understanding(root, item_id):
    doc = edits.load(root)
    if not doc or doc["phase"] != "understand" or not doc.get("owner_capture_items"):
        raise ValueError("A delivered capture UNDERSTAND round is required.")
    records = owner_captures.load_round_captures(root, doc)
    selected = [row for row in records if row["item_id"] == item_id]
    if len(selected) != 1:
        raise ValueError("The item must name one original capture item in this understanding.")
    artifact = edits.confined(root, UNDERSTANDING_PATH)
    shown_path = artifact.with_name("understanding-delivery.json")
    if not artifact.is_file() or not shown_path.is_file():
        raise ValueError("The understanding must be delivered before a full item edit.")
    understanding, shown = edits.read(artifact), edits.read(shown_path)
    state_path = edits.confined(root, "work/job_scene/state.json")
    unseen = edits.read(state_path).get("unseen", []) if state_path.exists() else []
    if (understanding.get("schema") != edits.UNDERSTANDING_SCHEMA
            or understanding.get("status") not in {"diagnostic_only", "awaiting_owner_confirmation"}
            or shown.get("delivered") is not True or shown.get("sha256") != edits.sha(artifact)
            or not understanding.get("call_id") or shown.get("call_id") != understanding["call_id"]
            or shown["call_id"] in unseen):
        raise ValueError("The delivered understanding changed or has not been shown to the owner.")
    if (understanding.get("source_fingerprint") != doc["source"]["fingerprint"]
            or understanding.get("request_text") != doc["request_text"]):
        raise ValueError("The understanding describes a different source or owner's request.")
    items = understanding.get("items", [])
    if [row.get("item_id") for row in items] != [row["item_id"] for row in records]:
        raise ValueError("The understanding item identity or order changed.")
    item = next(row for row in items if row["item_id"] == item_id)
    if item.get("disposition") == "previewed" or item.get("disposition") == "executable":
        raise ValueError("This item already has a previewed edit; select its shown option.")
    if item.get("disposition") not in ALLOWED_DISPOSITIONS:
        raise ValueError("The item has no recognized unpreviewable disposition.")
    if not isinstance(item.get("interpretation"), str) or not item["interpretation"].strip():
        raise ValueError("The understanding is missing its reading of this item.")
    if not isinstance(item.get("reason"), str) or not item["reason"].strip():
        raise ValueError("The unpreviewable item needs its recorded reason.")
    files = understanding.get("evidence_files", {})
    for name, digest in files.items():
        if edits.sha(edits.confined(root, name)) != digest:
            raise ValueError("The delivered understanding evidence changed.")
    snapshot_name = understanding.get("snapshot_path")
    if not isinstance(snapshot_name, str) or snapshot_name not in files:
        raise ValueError("The understanding has no bound source snapshot for measuring side effects.")
    snapshot = edits.read(edits.confined(root, snapshot_name))
    if snapshot.get("schema") != "g3d.mesh_snapshot/1" or not isinstance(snapshot.get("objects"), list):
        raise ValueError("The understanding source snapshot has an unsupported format.")
    if snapshot.get("fingerprint") != doc["source"]["fingerprint"]:
        raise ValueError("The understanding snapshot describes a different source fingerprint.")
    _source(doc)
    return doc, understanding, selected[0], item, snapshot


def capture_context(doc):
    """The explicit C1 capture-loader context; there is no restricted owner-edit phase."""
    return doc


def _mark_resolutions(record, snapshot, source_sha):
    from .tools import model_map_geometry as geometry
    resolutions = geometry.resolve_capture_marks(snapshot, record["source_glb"], record["payload"])
    for row in resolutions:
        row.update(item_id=record["item_id"], source_glb_sha256=source_sha,
                   metadata_sha256=record["metadata_sha256"])
    return resolutions


def instructions_suffix(doc=None):
    return "\n\n" + INSTRUCTIONS


def brief_suffix(doc):
    return ("\n\nFull edit of selected owner item " + doc["item_id"]
            + "\nOwner's words (verbatim):\n" + doc["owner_words"]
            + "\n\nSealed understanding SHA-256: " + doc["understanding_sha256"]
            + "\nUnderstanding's reading of this item (not additional owner instructions):\n"
            + json.dumps(doc["item_interpretation"], ensure_ascii=False)
            + "\n\nSource mark resolutions (evidence only; unresolved stays unresolved):\n"
            + json.dumps(doc["owner_mark_resolutions"], ensure_ascii=False)
            + "\nOnly this item is authorized by the separately approved full edit.")


def specialist_suffix(doc):
    return "\n\n" + SPECIALIST_INSTRUCTIONS + brief_suffix(doc)


def load(root):
    root = Path(root).resolve()
    path = edits.confined(root, RELATIVE_PATH)
    if not path.exists():
        return None
    doc = edits.read(path)
    if (doc.get("schema") != SCHEMA or doc.get("mode") != "full_edit_item"
            or doc.get("origin") != "full_edit_item" or not re.fullmatch(r"I[1-9][0-9]{0,8}", str(doc.get("item_id", "")))):
        raise ValueError("Unsupported full item edit round.")
    if (root / "inputs/owner-edit.json").exists():
        raise ValueError("A full item edit cannot also be a restricted owner edit round.")
    if doc.get("baseline_snapshot") != BASELINE_PATH:
        raise ValueError("The full item edit source snapshot path changed.")
    for name, digest in doc["immutable_files"].items():
        if edits.sha(edits.confined(root, name)) != digest:
            raise ValueError(f"Full item edit input changed: {name}")
    jobs.load_job(root)
    records = owner_captures.load_round_captures(root, capture_context(doc))
    if (len(records) != 1 or records[0]["item_id"] != doc["item_id"]
            or records[0]["request_text"] != doc["owner_words"]):
        raise ValueError("The full item edit's capture or owner's words changed.")
    if edits.sha(root / "inputs/seed.blend") != doc["source"]["checkpoint_sha256"]:
        raise ValueError("The full item edit seed differs from the selected source checkpoint.")
    previous = Path(doc["understand_round"]).resolve()
    original, understanding, record, item, snapshot = _shown_understanding(previous, doc["item_id"])
    if (edits.sha(previous / UNDERSTANDING_PATH) != doc["understanding_sha256"]
            or original["source"] != doc["source"] or item != doc["item_interpretation"]
            or record["request_text"] != doc["owner_words"]
            or edits.sha(root / "inputs/fallback-understanding.json") != doc["understanding_sha256"]
            or edits.read(root / doc["baseline_snapshot"]) != snapshot):
        raise ValueError("The source understanding or selected item changed; prepare a new full edit.")
    for key in ("metadata_sha256", "viewer_sha256", "annotated_sha256"):
        if records[0][key] != record[key]:
            raise ValueError("The selected capture differs from the delivered understanding.")
    if doc["owner_mark_resolutions"] != _mark_resolutions(record, snapshot, doc["source"]["export_sha256"]):
        raise ValueError("The full item edit source mark resolutions changed.")
    return doc


def create_full_edit_item(round_root, item_id, destination=None) -> Path:
    """Copy one unpreviewed capture item from a delivered UNDERSTAND; approval is separate."""
    previous = Path(round_root).resolve()
    doc, understanding, record, item, snapshot = _shown_understanding(previous, item_id)
    root = Path(destination).resolve() if destination is not None else edits.RUNTIME_ROOT / ("f" + uuid4().hex[:10])
    source = Path(doc["source"]["job"]).resolve()
    if root.exists() or root.is_relative_to(previous) or root.is_relative_to(source):
        raise ValueError("The fallback destination must be a new directory outside its source and understanding.")
    # Copy no prior round, approvals, ledgers, preview choices or unrelated captures.
    root.mkdir(parents=True)
    shutil.copyfile(previous / "job.json", root / "job.json")
    for folder in ("images", "reference_images", "cameras"):
        if (previous / folder).exists():
            edits.tree_hashes(previous / folder)
            shutil.copytree(previous / folder, root / folder)
    inputs = root / "inputs"
    inputs.mkdir()
    shutil.copyfile(previous / "inputs/seed.blend", inputs / "seed.blend")
    from .print_freshness import carry_evidence
    carry_evidence(previous, root)
    copied_row = {key: record[key] for key in ("item_id", "capture_id", "labels", "request_text", "capture_path")}
    folder = (root / copied_row["capture_path"]).parent
    folder.mkdir(parents=True)
    for name in ("capture.json", "viewer.png", "annotated.png"):
        shutil.copyfile(record["capture_dir"] / name, folder / name)
    shutil.copyfile(record["source_glb"], root / owner_captures.SOURCE_PATH)
    procs.write_json_atomic(root / owner_captures.ITEMS_PATH, [copied_row])
    shutil.copyfile(previous / UNDERSTANDING_PATH, inputs / "fallback-understanding.json")
    shutil.copyfile((previous / UNDERSTANDING_PATH).with_name("understanding-delivery.json"),
                    inputs / "fallback-understanding-delivery.json")
    shutil.copyfile(previous / understanding["snapshot_path"], root / BASELINE_PATH)
    resolutions = _mark_resolutions(record, snapshot, doc["source"]["export_sha256"])
    new = {"schema": SCHEMA, "mode": "full_edit_item", "origin": "full_edit_item", "item_id": item_id,
           "understand_round": str(previous), "source": copy.deepcopy(doc["source"]),
           "understanding_sha256": edits.sha(previous / UNDERSTANDING_PATH),
           "item_interpretation": copy.deepcopy(item), "owner_words": record["request_text"],
           "request_text": owner_captures._request_text([copied_row]), "owner_capture_items": [copied_row],
           "baseline_snapshot": BASELINE_PATH, "owner_mark_resolutions": resolutions,
           "scripted": bool(understanding.get("scripted")), "immutable_files": edits.tree_hashes(root)}
    procs.write_json_atomic(root / RELATIVE_PATH, new)
    load(root)  # Recheck copied bytes and the original bindings before returning a requestable round.
    return root
