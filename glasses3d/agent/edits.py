"""Owner edit rounds: immutable source and words, fresh conversations, separate approvals.

This module is local-only. Creating a round never starts a worker or a provider client.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4

from . import brief, delivery, jobs, procs
from .journal import CheckpointIndex

SCHEMA = "g3d.owner_edit/1"
UNDERSTANDING_SCHEMA = "g3d.owner_understanding/1"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def confined(root, relative):
    root = Path(root).resolve()
    path = root / relative
    if path.is_symlink() or path.resolve() != path.absolute() or not path.resolve().is_relative_to(root):
        raise ValueError("edit artifact must stay inside its job without filesystem links")
    return path


def tree_hashes(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): sha(confined(root, p.relative_to(root)))
            for p in sorted(root.rglob("*")) if p.is_file()}


def load(root):
    root = Path(root).resolve()
    path = root / "inputs/owner-edit.json"
    if not path.exists():
        return None
    doc = read(path)
    if doc.get("schema") != SCHEMA or doc.get("phase") not in ("understand", "edit"):
        raise ValueError("unsupported owner edit round")
    if not isinstance(doc.get("request_text"), str) or not doc["request_text"].strip():
        raise ValueError("owner edit request is missing")
    for name, digest in doc["immutable_files"].items():
        if sha(confined(root, name)) != digest:
            raise ValueError(f"owner edit input changed: {name}")
    if doc["phase"] == "edit":
        if not isinstance(doc.get("confirmation_text"), str) or not doc["confirmation_text"].strip():
            raise ValueError("edit requires the owner's verbatim confirmation or correction")
        understanding = read(root / "inputs/understanding.json")
        if understanding.get("schema") != UNDERSTANDING_SCHEMA:
            raise ValueError("unsupported understanding artifact")
        doc["understanding"] = understanding
    return doc


def brief_suffix(doc):
    text = "\n\nOwner's edit request (verbatim):\n" + doc["request_text"]
    text += "\n\nEdit phase: " + doc["phase"]
    text += ("\nThe owner's explicit requested feature target outranks photo readings and earlier choices. "
             "All physical hard checks remain. Keep unrequested geometry unchanged and report adjacent effects.")
    if doc["phase"] == "edit":
        text += "\n\nOwner's confirmation or correction (verbatim):\n" + doc["confirmation_text"]
        understanding = doc["understanding"]
        shown = {key: understanding[key] for key in
                 ("schema", "source_fingerprint", "items", "limitations", "map_id", "map_sha256", "capture_receipts",
                  "following_markings", "legend")
                 if key in understanding}
        text += ("\n\nNumbered understanding and exact target references (full immutable authority is in "
                 "inputs/understanding.json; tools resolve the shown support hashes):\n"
                 + json.dumps(shown, ensure_ascii=False))
    return text


def _copy_inputs(source, target):
    jobs.load_job(source)
    target.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(source / "job.json", target / "job.json")
    for name in ("images", "reference_images", "inputs"):
        folder = source / name
        if folder.exists():
            tree_hashes(folder)  # reject linked files before copying
            shutil.copytree(folder, target / name)
    (target / "inputs").mkdir(exist_ok=True)


def _refresh_brief(root, doc):
    """Keep an optional stored brief consistent with this round, without changing the source."""
    path = root / "inputs/brief.txt"
    if not path.exists():
        return
    original = root / "inputs/source-brief.txt"
    if not original.exists():
        shutil.copyfile(path, original)
    path.write_text(brief.build_brief(jobs.load_job(root)) + brief_suffix(doc), encoding="utf-8", newline="")
    doc["immutable_files"]["inputs/brief.txt"] = sha(path)
    doc["immutable_files"]["inputs/source-brief.txt"] = sha(original)


def _source_basis(source, report, supplied=None, *, source_runtime=None):
    from glasses3d.g3d_host.compare import Basis, load_basis
    runtime = Path(source_runtime).resolve() if source_runtime else source
    candidates = []
    if report.get("look_comparison"):
        candidates.append(report["look_comparison"])
    # Draft delivery predates look packaging; its run's tool receipts retain the exact exported comparison.
    for path in ([] if candidates else sorted((source / "runs" / report["run_id"] / "tools").glob("*/result.json"), reverse=True)):
        for output in read(path).get("result", []):
            if output.get("type") != "text":
                continue
            try:
                body = json.JSONDecoder().raw_decode(output["text"])[0]
            except (ValueError, KeyError):
                continue
            if body.get("full_report"):
                receipt = body["full_report"]
                path = confined(runtime, receipt["path"])
                if not path.is_relative_to(runtime / "work/tool_reports") or sha(path) != receipt["sha256"]:
                    raise ValueError("delivery full report bytes changed")
                body = read(path)["result"]
            for value in (body, body.get("look_comparison", {})):
                if value.get("export_sha256") == report["glb_sha256"] and value.get("basis"):
                    candidates.append(value)
            if candidates:
                break
        if candidates:
            break
    for candidate in candidates:
        if supplied is not None and candidate.get("basis") != supplied:
            continue
        basis = (Basis(confined(runtime / "cameras", candidate["basis"]), source)
                 if source_runtime else load_basis(source, candidate["basis"]))
        if candidate.get("basis_sha256") != sha(basis.dir / "basis.json"):
            raise ValueError("delivery camera basis changed")
        return basis
    raise ValueError("no hash-bound frozen camera evidence for this delivery; inspect the delivery's run evidence")


def create_understand(source, request_text, *, delivery_id=None, basis=None, destination=None, source_runtime=None):
    """Copy the chosen verified delivery checkpoint, never the job's potentially newer head."""
    source = Path(source).resolve()
    if not isinstance(request_text, str) or not request_text.strip():
        raise ValueError("owner request must be nonempty text")
    saved = delivery.load_delivery(source, delivery_id)
    report = saved["report_data"]
    runtime = Path(source_runtime).resolve() if source_runtime else source
    row = CheckpointIndex(runtime / "checkpoints").get(report["checkpoint"])
    if row is None or row["fingerprint"] != report["fingerprint"]:
        raise ValueError("the delivery's exact editable checkpoint is unavailable")
    blend = confined(runtime / "checkpoints", row["file"])
    if sha(blend) != row["sha256"]:
        raise ValueError("the delivery checkpoint bytes changed")
    frozen = _source_basis(source, report, basis, **({"source_runtime": runtime} if source_runtime else {}))
    edit_id = "edit-" + uuid4().hex[:10]
    edit_root = Path(destination).resolve() if destination else source / "edits" / edit_id
    if edit_root.exists():
        raise ValueError("edit destination already exists")
    root = edit_root / "round-001-understand"
    _copy_inputs(source, root)
    shutil.copyfile(blend, root / "inputs/seed.blend")
    shutil.copytree(frozen.dir, root / "cameras" / frozen.id)
    doc = {"schema": SCHEMA, "phase": "understand", "edit_id": edit_id, "round": 1,
           "request_text": request_text, "source": {"job": str(source), "delivery_id": saved["delivery_id"],
           "checkpoint": row["id"], "fingerprint": row["fingerprint"], "checkpoint_sha256": row["sha256"],
           "export_sha256": saved["glb_sha256"], "basis": frozen.id},
           "immutable_files": {"job.json": sha(root / "job.json"), "inputs/seed.blend": row["sha256"],
             **{"cameras/" + frozen.id + "/" + p: digest for p, digest in tree_hashes(frozen.dir).items()}}}
    _refresh_brief(root, doc)
    procs.write_json_atomic(root / "inputs/owner-edit.json", doc)
    procs.write_json_atomic(edit_root / "edit.json", {"schema": SCHEMA, "edit_id": edit_id,
        "source_job": str(source), "rounds": [root.name]})
    return root


def create_edit(edit_root, confirmation_text, *, correction=False):
    """The second round requires a delivered, unchanged understanding. It is never implicitly approved."""
    edit_root = Path(edit_root).resolve()
    if not isinstance(confirmation_text, str) or not confirmation_text.strip():
        raise ValueError("owner confirmation or correction must be nonempty text")
    index = read(edit_root / "edit.json")
    previous = confined(edit_root, index["rounds"][-1])
    doc = load(previous)
    if doc["phase"] != "understand":
        raise ValueError("start another job edit from the latest delivery for a further edit round")
    artifact = previous / "work/owner_edit/understanding.json"
    understanding = read(artifact)
    shown = read(artifact.with_name("understanding-delivery.json"))
    state_path = previous / "work/job_scene/state.json"
    unseen = read(state_path).get("unseen", []) if state_path.exists() else []
    if (understanding.get("status") != "awaiting_owner_confirmation" or not shown.get("delivered") or
            shown.get("sha256") != sha(artifact) or shown.get("call_id") != understanding.get("call_id") or
            shown.get("call_id") in unseen):
        raise ValueError("understanding must be shown to the owner before an edit round")
    if understanding["source_fingerprint"] != doc["source"]["fingerprint"]:
        raise ValueError("understanding is not of the selected delivery")
    root = edit_root / "round-002-edit"
    _copy_inputs(previous, root)
    shutil.copytree(previous / "cameras", root / "cameras")
    # Maps/captures retain their relative paths; original bytes become approval-bound inputs too.
    if (previous / "work/model_maps").exists():
        shutil.copytree(previous / "work/model_maps", root / "work/model_maps")
    for name, digest in understanding["evidence_files"].items():
        src = confined(previous, name)
        if sha(src) != digest:
            raise ValueError("understanding evidence changed")
        dst = confined(root, name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            shutil.copyfile(src, dst)
    shutil.copyfile(artifact, root / "inputs/understanding.json")
    new = copy.deepcopy(doc)
    new.update(phase="edit", round=2, confirmation_text=confirmation_text, correction=bool(correction),
               understanding_sha256=sha(artifact))
    new["immutable_files"].update(understanding["evidence_files"])
    new["immutable_files"]["inputs/understanding.json"] = sha(artifact)
    _refresh_brief(root, {**new, "understanding": understanding})
    procs.write_json_atomic(root / "inputs/owner-edit.json", new)
    index["rounds"].append(root.name)
    procs.write_json_atomic(edit_root / "edit.json", index)
    return root


def resolve_round(source, edit_id, round_name=None):
    edit_root = confined(Path(source).resolve() / "edits", edit_id)
    index = read(edit_root / "edit.json")
    name = round_name or index["rounds"][-1]
    if name not in index["rounds"]:
        raise ValueError("unknown edit round")
    return confined(edit_root, name)


class Authority:
    """Host-owned confirmed selectors. A tool may interpret words but cannot grant itself more scope."""

    def __init__(self, root):
        self.root = Path(root).resolve()

    def resolve(self, operation_ids=None, current_head=None):
        doc = load(self.root)
        if not doc or doc["phase"] != "edit":
            raise ValueError("owner confirmation is required before edits")
        understanding = doc["understanding"]
        operations = understanding["operations"]
        requested = [op["id"] for op in operations] if operation_ids is None else operation_ids
        if len(set(requested)) != len(requested) or set(requested) - {op["id"] for op in operations}:
            raise ValueError("operation ids must name distinct confirmed items")
        snapshot = read(confined(self.root, understanding["snapshot_path"]))
        receipts = self.receipts()
        return {"authority_sha256": sha(self.root / "inputs/owner-edit.json"),
                "round_id": doc["edit_id"] + ":" + str(doc["round"]),
                "source_fingerprint": doc["source"]["fingerprint"], "snapshot": snapshot,
                "operations": [copy.deepcopy(op) for op in operations if op["id"] in requested],
                "all_operations": copy.deepcopy(operations),
                "applied_operation_ids": [op for row in receipts for op in row.get("operation_ids", [])],
                "receipts": receipts, "correction": doc.get("correction", False),
                "request_text": doc["request_text"], "confirmation_text": doc["confirmation_text"],
                "confirmation_sha256": hashlib.sha256(doc["confirmation_text"].encode()).hexdigest()}

    def receipts(self):
        path = self.root / "work/owner_edit/applied.json"
        return read(path) if path.exists() else []

    def record_applied(self, receipt):
        rows = self.receipts()
        rows.append(copy.deepcopy(receipt))
        procs.write_json_atomic(self.root / "work/owner_edit/applied.json", rows)


def understanding_page(root):
    """Local owner-readable stop page; the text and pixels are the exact tool artifacts."""
    import html
    root = Path(root).resolve()
    doc = read(root / "work/owner_edit/understanding.json")
    esc = html.escape
    title = "scripted by Codex, not Astra" if doc.get("scripted") else "Astra: understand"
    body = [f"<h1>{esc(title)}</h1><p>The model is unchanged. Confirm or correct these numbered changes. "
            "The edit is a separate round with its own estimate and approval.</p>",
            f"<h2>Owner's edit request (verbatim)</h2><pre>{esc(doc['request_text'])}</pre>"]
    labels = {row['target_id']: row['label'] for row in doc.get('legend', [])}
    for item in doc["items"]:
        body += [f"<h2>{item['number']}. {esc(item['label'])}</h2>",
                 f"<p>{esc(item['interpretation'])}</p><p>{esc(item['direction'])}; {esc(item['amount'])}</p>",
                 f"<p>Targets: {esc(', '.join(labels.get(key, key) for key in item['target_ids']))}</p>"]
    if doc.get('following_markings'):
        body.append('<h2>Markings that follow the edited surface</h2><ul>' + ''.join(
            f"<li>{esc(row['object_name'])} follows {esc(row['surface_object_name'])}, keeping its distance from the surface.</li>"
            for row in doc['following_markings']) + '</ul>')
    if doc.get('legend'):
        body.append('<h2>Label legend</h2><ul>' + ''.join(
            f"<li>{esc(row['label'])} = {esc(row['description'])}</li>" for row in doc['legend']) + '</ul>')
    body.append("<h2>Limitations and neighbouring effects</h2><ul>" +
                "".join(f"<li>{esc(value)}</li>" for value in doc["limitations"]) + "</ul>")
    seen = set()
    for reference in doc["capture_receipts"]:
        capture = read(confined(root, reference))
        for row in capture["views"]:
            path = confined(root, row["full_pair"])
            if path in seen:
                continue
            seen.add(path)
            rel = Path("..") / ".." / path.relative_to(root)
            body.append(f"<figure><figcaption>{esc(row['ref'])}: photo first, labelled model second. "
                        f"Click for native pixels.</figcaption><a href='{esc(rel.as_posix())}'>"
                        f"<img src='{esc(rel.as_posix())}'></a></figure>")
    path = root / "work/owner_edit/understanding.html"
    path.write_text("<!doctype html><meta charset='utf-8'><title>Owner edit: understand</title>"
                   "<style>body{font:18px system-ui;margin:2em;max-width:1600px}img{max-width:100%}"
                   "pre{white-space:pre-wrap}figure{margin:2em 0}</style>" + "\n".join(body), encoding="utf-8")
    return path


def review_understanding(root, port):
    import socket
    import sys
    root = Path(root).resolve()
    page = understanding_page(root)
    if port == 0:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
    child = procs.spawn_detached([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
                                  "--directory", str(root)], cwd=root,
                                 log_path=root / "work/owner_edit/review.log",
                                 status_path=root / "work/owner_edit/review-status.json")
    return {"status": "understanding_review", "url": f"http://127.0.0.1:{port}/" + page.relative_to(root).as_posix(),
            "geometry_changed": False, **child.record()}
