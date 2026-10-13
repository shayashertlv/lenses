"""Immutable, content-bound job deliveries; mutable compatibility copies are never verdict identities."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
from uuid import uuid4

from . import procs

SCHEMA = "g3d.delivery/1"
POINTER_SCHEMA = "g3d.delivery_current/1"
REPORT_SCHEMA = "g3d.deliverable/1"
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,23}-[0-9a-f]{64}")


class DeliveryError(ValueError):
    """A finished run or immutable delivery is incomplete, stale or inconsistent."""


def _json_bytes(value) -> bytes:
    try:
        return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DeliveryError(f"delivery contains invalid JSON data: {exc}") from exc


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inside(root: Path, path) -> Path:
    if not isinstance(path, (str, Path)) or not str(path):
        raise DeliveryError("delivery file path is missing")
    target = (root / path).resolve()
    if target == root or not target.is_relative_to(root):
        raise DeliveryError("delivery file path escapes its directory")
    return target


def _output_path(root: Path, path) -> Path:
    """A mutable output may never alias an immutable snapshot, even through an in-job link."""
    target = _inside(root, path)
    if target != (root / path).absolute():
        raise DeliveryError("delivery output path contains a filesystem link")
    return target


def _relative(value: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise DeliveryError("delivery manifest needs confined relative file paths")
    return value


def _read(root: Path, path, expected: str | None = None) -> tuple[Path, bytes]:
    target = _inside(root, path)
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise DeliveryError(f"delivery file is missing or unreadable: {target.name}") from exc
    if expected is not None and (not isinstance(expected, str) or not _DIGEST.fullmatch(expected)
                                 or _sha(data) != expected):
        raise DeliveryError(f"delivery file hash changed: {target.name}")
    return target, data


def _document(data: bytes, what: str) -> dict:
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise DeliveryError(f"{what} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise DeliveryError(f"{what} must be an object")
    return value


def _capture_files(root: Path, ar: dict, glb_sha: str) -> tuple[dict, list, list, list]:
    """Recheck both the renderer's raw images and the tool images actually shown to Astra."""
    if (not isinstance(ar, dict) or ar.get("status") != "ok" or ar.get("exit_code") != 0
            or ar.get("export_sha256") != glb_sha):
        raise DeliveryError("delivery requires a successful AR capture of the finished export")
    report_path, report_bytes = _read(root, ar.get("report"), ar.get("report_sha256", ""))
    capture = _document(report_bytes, "AR report")
    inputs, request = capture.get("input"), capture.get("request")
    if (capture.get("schema") != "arview.capture/1" or not isinstance(inputs, dict) or inputs.get("sha256") != glb_sha
            or capture.get("exit_code") != 0 or not isinstance(request, dict) or request.get("fixed_scale") is not True):
        raise DeliveryError("AR report does not describe a successful fixed-scale capture of this GLB")
    records = capture.get("images")
    if not isinstance(records, list) or not records:
        raise DeliveryError("AR report has no capture images")
    raw, names = [], set()
    for rec in records:
        if not isinstance(rec, dict):
            raise DeliveryError("invalid AR capture image record")
        name = _relative(rec.get("file"))
        if name.casefold() in names or name.casefold() == "report.json":
            raise DeliveryError("AR capture image names collide")
        names.add(name.casefold())
        _, data = _read(report_path.parent, name, rec.get("sha256", ""))
        raw.append((name, data))
    images = ar.get("images")
    sheets = ar.get("sheets")
    if not isinstance(images, list) or not images or not isinstance(sheets, list) or not sheets:
        raise DeliveryError("AR contact sheets or their image receipts are missing")
    shown, source_hashes = [], {}
    for rec in images:
        if not isinstance(rec, dict):
            raise DeliveryError("invalid AR tool image record")
        image_path, data = _read(root, rec.get("path"), rec.get("sha256", ""))
        source = rec.get("source_path", rec.get("path"))
        source_sha = rec.get("source_sha256", rec.get("sha256", ""))
        source_path, source_data = _read(root, source, source_sha)
        source_hashes[source_path] = _sha(source_data)
        shown.append((rec, image_path, data, source_path))
    contact, seen = [], set()
    for value in sheets:
        path = _inside(root, value)
        if path in seen:
            raise DeliveryError("AR contact sheet is repeated")
        seen.add(path)
        if path not in source_hashes:
            raise DeliveryError("AR contact sheet has no matching hashed image receipt")
        _, data = _read(root, path, source_hashes[path])
        contact.append((path, data))
    return {"path": report_path, "bytes": report_bytes, "data": capture}, raw, shown, contact


def _snapshot_id(manifest: dict) -> str:
    core = {key: value for key, value in manifest.items() if key != "id"}
    run_id = core.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise DeliveryError("delivery run id is missing")
    label = re.sub(r"[^A-Za-z0-9_.-]", "_", run_id).strip("._-")[:24] or "run"
    return label + "-" + _sha(_json_bytes(core))


def _prepare(root: Path, scene, result: dict) -> tuple[dict, dict[str, bytes]]:
    finished = scene.finish
    if not isinstance(finished, dict) or finished.get("status") != "accepted":
        raise DeliveryError("delivery requires an accepted finish")
    checkpoint = finished.get("checkpoint")
    exit_head = result.get("exit_checkpoint") or {}
    if not checkpoint or exit_head.get("verified") is not True or exit_head.get("id") != checkpoint:
        raise DeliveryError("delivery requires the finished checkpoint to be verified at shutdown")
    exp = finished.get("export") or {}
    if not isinstance(exp, dict):
        raise DeliveryError("finished export record is invalid")
    validation = exp.get("validate_spec") or {}
    seating = exp.get("seat_check")
    printing = exp.get("print_check")
    if (exp.get("status") != "exported" or exp.get("checkpoint") != checkpoint or exp.get("drift")
            or not finished.get("fingerprint") or exp.get("fingerprint") != finished["fingerprint"]
            or not isinstance(validation, dict) or validation.get("ok") is not True
            or validation.get("sha256") != exp.get("sha256") or exp.get("validation_sha256") != exp.get("sha256")
            or not isinstance(seating, dict) or seating.get("ok") is not True
            or not isinstance(printing, dict) or printing.get("ok") is not True
            or printing.get("glb_sha256") != exp.get("sha256")):
        raise DeliveryError("finished export is invalid or does not match the accepted checkpoint")
    _, model = _read(root, exp.get("path"), exp.get("sha256", ""))
    ar = scene.last_ar
    capture, raw, shown, contact = _capture_files(root, ar, exp["sha256"])
    files = {"model.glb": model, "arview/report.json": capture["bytes"]}
    for name, data in raw:
        files["arview/" + name] = data
    sheet_paths = {}
    for index, (path, data) in enumerate(contact, 1):
        suffix = path.suffix.lower()
        if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
            raise DeliveryError("unsupported AR contact sheet format")
        name = f"contact-sheets/{index:02d}{suffix}"
        files[name], sheet_paths[path] = data, name
    image_records = []
    for index, (record, path, data, source) in enumerate(shown, 1):
        name = sheet_paths.get(path)
        if name is None:
            suffix = path.suffix.lower()
            if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
                raise DeliveryError("unsupported AR tool image format")
            name = f"tool-images/{index:02d}{suffix}"
            files[name] = data
        if source not in sheet_paths:
            raise DeliveryError("AR tool image does not describe a contact sheet")
        image_records.append({**copy.deepcopy(record), "path": name, "source_path": sheet_paths[source],
                              "source_sha256": _sha(files[sheet_paths[source]])})
    ar_record = copy.deepcopy(ar)
    ar_record.pop("log", None)  # process logs are run evidence, not needed to inspect this immutable delivery
    ar_record.update(report="arview/report.json", images=image_records, sheets=list(sheet_paths.values()))
    report = {"schema": REPORT_SCHEMA, "job_id": root.name, "run_id": result.get("run_id"),
              "checkpoint": checkpoint, "exit_checkpoint": copy.deepcopy(exit_head),
              "fingerprint": finished["fingerprint"], "glb_sha256": exp["sha256"],
              "validation_sha256": exp["validation_sha256"], "validate_spec": copy.deepcopy(validation),
              "seating": copy.deepcopy(seating), "print_check": copy.deepcopy(printing),
              "arview": ar_record, "cost": copy.deepcopy(result.get("budget")),
              "summary": finished.get("summary", "")}
    if "path" in report["validate_spec"]:
        report["validate_spec"]["path"] = "model.glb"
    if "glb" in report["seating"]:
        report["seating"]["glb"] = "model.glb"
    files["report.json"] = _json_bytes(report)
    manifest = {"schema": SCHEMA, "job_id": root.name, "run_id": result.get("run_id"),
                "glb_sha256": exp["sha256"], "model": "model.glb", "report": "report.json",
                "contact_sheets": list(sheet_paths.values()),
                "files": {name: {"sha256": _sha(data), "bytes": len(data)} for name, data in sorted(files.items())}}
    manifest["id"] = _snapshot_id(manifest)
    return manifest, files


def _resolved_report(directory: Path, report: dict) -> dict:
    report = copy.deepcopy(report)
    ar = report["arview"]
    ar["report"] = str(_inside(directory, ar["report"]))
    ar["sheets"] = [str(_inside(directory, path)) for path in ar["sheets"]]
    for rec in ar["images"]:
        for field in ("path", "source_path"):
            rec[field] = str(_inside(directory, rec[field]))
    if "path" in report["validate_spec"]:
        report["validate_spec"]["path"] = str(directory / "model.glb")
    if isinstance(report.get("seating"), dict) and "glb" in report["seating"]:
        report["seating"]["glb"] = str(directory / "model.glb")
    return report


def load_delivery(root, delivery_id: str | None = None) -> dict:
    """Load a verified immutable snapshot, resolving current exactly once when no id is supplied.

    Pre-M4 snapshots may have seating=None. Their recorded evidence and identities remain immutable; only new
    publication requires a passing all-frame seating report.
    """
    root = Path(root).resolve()
    pointer = None
    if delivery_id is None:
        _, data = _read(root, "deliveries/current.json")
        pointer = _document(data, "delivery pointer")
        if pointer.get("schema") != POINTER_SCHEMA:
            raise DeliveryError("invalid current delivery pointer")
        delivery_id = pointer.get("id")
    if not isinstance(delivery_id, str) or not _ID.fullmatch(delivery_id):
        raise DeliveryError("invalid delivery id")
    directory = _output_path(root, Path("deliveries") / delivery_id)
    manifest_path, manifest_bytes = _read(directory, "manifest.json",
                                         pointer.get("manifest_sha256", "") if pointer else None)
    manifest = _document(manifest_bytes, "delivery manifest")
    if (manifest_bytes != _json_bytes(manifest) or manifest.get("schema") != SCHEMA or manifest.get("id") != delivery_id
            or manifest.get("job_id") != root.name or _snapshot_id(manifest) != delivery_id
            or manifest.get("model") != "model.glb" or manifest.get("report") != "report.json"):
        raise DeliveryError("delivery manifest identity changed")
    records = manifest.get("files")
    if not isinstance(records, dict) or not {"model.glb", "report.json", "arview/report.json"} <= records.keys():
        raise DeliveryError("delivery manifest is incomplete")
    contents, folded = {}, set()
    for name, rec in records.items():
        _relative(name)
        if name.casefold() in folded or name.casefold() == "manifest.json" or not isinstance(rec, dict):
            raise DeliveryError("delivery manifest contains colliding file records")
        folded.add(name.casefold())
        _, data = _read(directory, name, rec.get("sha256", ""))
        if type(rec.get("bytes")) is not int or len(data) != rec["bytes"]:
            raise DeliveryError("delivery file size changed")
        contents[name] = data
    disk_files = set()
    for path in directory.rglob("*"):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise DeliveryError("delivery contains a filesystem link")
        if path.is_file():
            disk_files.add(path.relative_to(directory).as_posix())
    if disk_files != set(records) | {"manifest.json"}:
        raise DeliveryError("delivery contains unlisted or missing files")
    sha = _sha(contents["model.glb"])
    report = _document(contents["report.json"], "delivery report")
    exit_head = report.get("exit_checkpoint") or {}
    validation, seating = report.get("validate_spec"), report.get("seating")
    printing = report.get("print_check")
    if (manifest.get("glb_sha256") != sha or (pointer and pointer.get("glb_sha256") != sha)
            or report.get("schema") != REPORT_SCHEMA or report.get("job_id") != root.name
            or report.get("run_id") != manifest.get("run_id") or report.get("glb_sha256") != sha
            or report.get("validation_sha256") != sha or not isinstance(validation, dict)
            or validation.get("ok") is not True or validation.get("sha256") != sha
            or not report.get("checkpoint") or not report.get("fingerprint") or not isinstance(exit_head, dict)
            or exit_head.get("verified") is not True or exit_head.get("id") != report.get("checkpoint")
            or (seating is not None and (not isinstance(seating, dict) or seating.get("ok") is not True))
            or (printing is not None and (not isinstance(printing, dict) or printing.get("ok") is not True
                                         or printing.get("glb_sha256") != sha))):
        raise DeliveryError("delivery report does not bind the validated finished model")
    _capture_files(directory, report.get("arview"), sha)
    sheets = manifest.get("contact_sheets")
    if sheets != report["arview"]["sheets"] or not sheets or any(path not in records for path in sheets):
        raise DeliveryError("delivery contact sheets disagree with the report")
    resolved = _resolved_report(directory, report)
    return {"status": "delivered", "id": delivery_id, "delivery_id": delivery_id, "glb_sha256": sha,
            "model": str(directory / "model.glb"), "report": str(directory / "report.json"),
            "contact_sheet": str(directory / sheets[0]), "contact_sheets": [str(directory / p) for p in sheets],
            "manifest": str(manifest_path), "manifest_sha256": _sha(manifest_bytes), "report_data": resolved}


def _write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def _compatibility(root: Path, loaded: dict) -> None:
    """Publish a convenience copy; immutable APIs never consult these replaceable files."""
    dest = _output_path(root, "deliverable")
    dest.mkdir(exist_ok=True)
    for name, data in (("model.glb", Path(loaded["model"]).read_bytes()),
                       ("report.json", _json_bytes({**loaded["report_data"], "delivery_id": loaded["id"],
                                                    "manifest_sha256": loaded["manifest_sha256"]}))):
        path = _output_path(root, dest / name)
        temp = _inside(root, dest / f".{name}.{uuid4().hex}.tmp")
        _write_file(temp, data)
        procs.replace_with_retry(temp, path)


def deliver_finished(root, scene, result) -> dict:
    """Snapshot a successfully finished run. A rerun cannot overwrite a previous snapshot's bytes."""
    if result.get("status") != "completed" or not scene.finish:
        return {"status": "not_finished", "run_status": result.get("status")}
    root = Path(root).resolve()
    manifest, files = _prepare(root, scene, result)
    deliveries = _output_path(root, "deliveries")
    deliveries.mkdir(exist_ok=True)
    dest = _output_path(root, deliveries / manifest["id"])
    if not dest.exists():
        stage = _inside(root, deliveries / f".pending-{uuid4().hex}")
        stage.mkdir()
        try:
            for name, data in files.items():
                _write_file(_inside(stage, name), data)
            _write_file(stage / "manifest.json", _json_bytes(manifest))
            try:
                os.rename(stage, dest)
            except FileExistsError:  # another identical delivery won; verification below decides whether it is valid
                pass
        finally:
            if stage.exists():
                # This is our freshly generated staging directory, resolved and confined before removing it.
                safe = _inside(deliveries, stage)
                shutil.rmtree(safe)
    loaded = load_delivery(root, manifest["id"])
    if loaded["manifest_sha256"] != _sha(_json_bytes(manifest)):
        raise DeliveryError("existing immutable delivery differs from this finished run")
    _compatibility(root, loaded)
    procs.write_json_atomic(_output_path(root, "deliveries/current.json"),
                           {"schema": POINTER_SCHEMA, "id": loaded["id"], "glb_sha256": loaded["glb_sha256"],
                            "manifest_sha256": loaded["manifest_sha256"]})
    return {**loaded, "snapshot_model": loaded["model"], "snapshot_report": loaded["report"],
            "model": str(root / "deliverable/model.glb"), "report": str(root / "deliverable/report.json")}
