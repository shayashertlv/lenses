"""Offline-first initial geometry, with explicitly injected provider execution.

``resolve_initial_model`` owns one output directory/immutable request. It copies
an existing static GLB exactly, imports a saved provider artifact, or advances a
provider task by at most one submit/retrieve/download cycle. Completion means
an initial hypothesis is available, never that photographs were reconstructed
accurately. This module neither reads credentials nor constructs an HTTP client.

Meshy wire contract checked against https://docs.meshy.ai/en/api/multi-image-to-3d
on 2026-09-21: 1--4 JPEG/PNG images; first image is primary for Meshy 7.1.
Native preparation is deliberately not imported: parts_prepare requires layout,
classifies/joins parts and may reconstruct lenses, remove walls and fit temples.
Those edits need explicit hypothesis/fallback lineage, not hidden initialization.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile
import uuid
from typing import Protocol
from urllib.parse import urlsplit

import numpy as np

from .atomic_files import replace_with_retry
from .mesh import load_glb


MAX_MODEL_BYTES = 2 * 1024**3
MESHY_ENDPOINT = "https://api.meshy.ai/openapi/v1/multi-image-to-3d"
MESHY_SPLIT_ENDPOINT = "https://api.meshy.ai/openapi/v1/print/split"
MESHY_SPLIT_SETTINGS = {"mode": "by_parts", "layout": "assembled", "target_formats": ["glb"]}
# Generic eyewear vocabulary handed to the provider split as naming hints. The
# grouping stage reads geometry only; these names never become semantic rules.
EYEWEAR_SPLIT_PARTS = ("frame front", "left lens", "right lens", "left temple", "right temple", "nose pads")
MESHY_DEFAULT_SETTINGS = {"ai_model": "meshy-7.1", "should_texture": True, "enable_pbr": True,
                          "texture_resolution": "4k", "should_remesh": False, "target_formats": ["glb"]}
_TASK_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class InitializerBackend(Protocol):
    """Explicit caller-owned execution. submit MUST NOT retry an uncertain POST.

    retrieve returns {task_id, status: pending|succeeded|failed, ...}; download
    returns exact GLB bytes. Retrieval/download may be retried for the same task.
    Backend errors must not embed credentials; reports store only exception type.
    """

    provider: str

    def submit(self, request: dict) -> str: ...
    def retrieve(self, task_id: str) -> dict: ...
    def download(self, task: dict) -> bytes: ...


class MeshyTransport(Protocol):
    """Injected transport owns authentication, bounded timeouts and size limits.

    post_json must send at most once, with no automatic POST retry. download_public
    MUST omit API credentials and validate every redirect against public HTTPS
    (including resolved addresses), enforcing max_bytes during streaming. This
    module intentionally provides no default implementation or environment lookup.
    """

    def post_json(self, url: str, payload: dict) -> dict: ...
    def get_json(self, url: str) -> dict: ...
    def download_public(self, url: str, *, max_bytes: int) -> bytes: ...


class MeshyBackend:
    """Meshy 7.1 adapter for caller-supplied, explicitly authorized transports.

    The optional split transport is a second, separately reserved paid task
    (print/split of the finished generation into named parts). One transport
    instance never carries two POSTs, so a split needs its own instance.
    """

    provider = "meshy"

    def __init__(self, transport: MeshyTransport, split_transport: MeshyTransport | None = None):
        if split_transport is transport and transport is not None:
            raise ValueError("The split needs its own transport instance")
        self.transport, self.split_transport = transport, split_transport

    def bind_output(self, output: Path, request_sha256: str):
        if hasattr(self.transport, "bind_output"):
            self.transport.bind_output(output / "transport", request_sha256)

    def bind_split_output(self, output: Path, request_sha256: str):
        if self.split_transport is None:
            raise ValueError("No split transport was authorized")
        if hasattr(self.split_transport, "bind_output"):
            self.split_transport.bind_output(output / "transport", request_sha256)

    def preflight(self, request: dict):
        if hasattr(self.transport, "check_submission"):
            self.transport.check_submission(request["settings"])

    def preflight_split(self, request: dict):
        if self.split_transport is None:
            raise ValueError("No split transport was authorized")
        if hasattr(self.split_transport, "check_submission"):
            self.split_transport.check_submission(_split_payload(request))

    def submit_split(self, request: dict) -> str:
        if self.split_transport is None:
            raise ValueError("No split transport was authorized")
        return _task_id(self.split_transport.post_json(MESHY_SPLIT_ENDPOINT, _split_payload(request)).get("result"))

    def retrieve_split(self, task_id: str) -> dict:
        if self.split_transport is None:
            raise ValueError("No split transport was authorized")
        return _observe(self.split_transport, MESHY_SPLIT_ENDPOINT, task_id, extra=("part_count",))

    def download_split(self, task: dict) -> bytes:
        if self.split_transport is None:
            raise ValueError("No split transport was authorized")
        if task.get("status") != "succeeded":
            raise ValueError("Only a successful task has a downloadable model")
        return self.split_transport.download_public(_public_https(task.get("glb_url")), max_bytes=MAX_MODEL_BYTES)
        for photo in request["selection"]["selected"]:
            if Path(photo["path"]).stat().st_size > 20 * 1024**2:
                raise ValueError("Selected image exceeds the 20 MiB provider input limit")

    def submit(self, request: dict) -> str:
        selected = request["selection"]["selected"]
        if not 1 <= len(selected) <= 4:
            raise ValueError("Meshy requires one to four selected images")
        payload = deepcopy(request["settings"])
        images = []
        for photo in selected:
            if Path(photo["path"]).stat().st_size > 20 * 1024**2:
                raise ValueError("Selected image exceeds the 20 MiB provider input limit")
            raw = Path(photo["path"]).read_bytes()
            if len(raw) > 20 * 1024**2:
                raise ValueError("Selected image exceeds the 20 MiB provider input limit")
            if _hash(raw) != photo["sha256"]:
                raise ValueError("Selected photo bytes changed before submission")
            mime = "image/png" if raw.startswith(b"\x89PNG\r\n\x1a\n") else (
                "image/jpeg" if raw.startswith(b"\xff\xd8\xff") else None)
            if mime is None:
                raise ValueError("Normalize provider images to PNG or JPEG first")
            images.append(f"data:{mime};base64," + base64.b64encode(raw).decode("ascii"))
        payload["image_urls"] = images
        return _task_id(self.transport.post_json(MESHY_ENDPOINT, payload).get("result"))

    def retrieve(self, task_id: str) -> dict:
        return _observe(self.transport, MESHY_ENDPOINT, task_id)

    def download(self, task: dict) -> bytes:
        if task.get("status") != "succeeded":
            raise ValueError("Only a successful task has a downloadable model")
        return self.transport.download_public(_public_https(task.get("glb_url")), max_bytes=MAX_MODEL_BYTES)


def _observe(transport, endpoint: str, task_id: str, *, extra=()) -> dict:
    task_id = _task_id(task_id)
    task = transport.get_json(f"{endpoint}/{task_id}")
    if task.get("id") != task_id:
        raise ValueError("Provider returned a different task")
    status = task.get("status")
    observation = {"task_id": task_id, "provider_status": status, "provider_response": deepcopy(task)}
    for key in ("progress", "consumed_credits", *extra):
        if key in task:
            observation[key] = deepcopy(task[key])
    if getattr(transport, "last_receipt", None) is not None:
        observation["transport_receipt"] = deepcopy(transport.last_receipt)
    if status in {"PENDING", "QUEUED", "IN_PROGRESS"}:
        return dict(observation, status="pending")
    if status in {"FAILED", "CANCELED", "CANCELLED", "EXPIRED"}:
        return dict(observation, status="failed")
    if status != "SUCCEEDED":
        raise ValueError("Unknown provider task status")
    return dict(observation, status="succeeded",
                glb_url=_public_https((task.get("model_urls") or {}).get("glb")))


def _split_payload(request: dict) -> dict:
    parts = request.get("parts")
    if not isinstance(parts, list) or not 1 <= len(parts) <= 10 or any(
            not isinstance(p, str) or not p.strip() or "," in p for p in parts) or len(set(parts)) != len(parts):
        raise ValueError("A split names one to ten distinct comma-free parts")
    return {**MESHY_SPLIT_SETTINGS, "input_task_id": _task_id(request.get("input_task_id")), "prompt": ", ".join(parts)}


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


def _task_id(value) -> str:
    if not isinstance(value, str) or not _TASK_ID.fullmatch(value):
        raise ValueError("Invalid provider task id")
    return value


def _public_https(value) -> str:
    if not isinstance(value, str):
        raise ValueError("Missing public HTTPS model URL")
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Model download requires public HTTPS without credentials")
    if host.lower() == "localhost" or host.lower().endswith((".localhost", ".local")):
        raise ValueError("Private download host is unsupported")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Private download address is unsupported")
    return value


def _write_exclusive(path: Path, raw: bytes) -> None:
    # A partial reservation is intentionally not erased on failure: its existence
    # must still prevent a second paid POST after a crash or disk error.
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _bind_json(path: Path, value: dict) -> None:
    raw = _json_bytes(value)
    try:
        _write_exclusive(path, raw)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise ValueError(f"Immutable initializer receipt differs: {path.name}")


def _write_report(path: Path, value: dict) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(_json_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())
    try:
        replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _resolve_path(value, base_dir: Path) -> Path:
    if not isinstance(value, (str, Path)) or not str(value):
        raise ValueError("Expected a local file path")
    path = Path(value)
    return (path if path.is_absolute() else base_dir / path).resolve()


def _check_pin(raw: bytes, pin) -> str:
    digest = _hash(raw)
    if pin is not None and (not isinstance(pin, str) or not _SHA.fullmatch(pin) or digest != pin):
        raise ValueError("Input SHA-256 does not match pinned bytes")
    return digest


def _photo_ledger(photos: list[dict], base_dir: Path, *, missing_provider_pins=None) -> list[dict]:
    if not isinstance(photos, list):
        raise ValueError("photos must be a list")
    result, ids = [], set()
    for index, photo in enumerate(photos):
        if not isinstance(photo, dict):
            raise ValueError("Every photo must be a dictionary")
        entry = deepcopy(photo)
        identifier = entry.get("id", f"photo-{index + 1}")
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            raise ValueError("Photo ids must be nonempty and unique")
        ids.add(identifier)
        path = _resolve_path(entry.get("path"), base_dir)
        if not path.exists() and identifier in (missing_provider_pins or {}):
            saved = missing_provider_pins[identifier]
            if str(path) != saved['path'] or entry.get('sha256') not in (None, saved['sha256']):
                raise ValueError('Missing provider photo differs from its pinned request')
            digest, size = saved['sha256'], saved['bytes']
        else:
            raw = path.read_bytes()
            digest, size = _check_pin(raw, entry.get("sha256")), len(raw)
        entry.update(id=identifier, path=str(path), sha256=digest, bytes=size)
        view = entry.get("view", "unknown")
        if view is None:
            view = "unknown"
        if not isinstance(view, str):
            raise ValueError("Photo view must be a string or null")
        entry.update(view=view, input_index=index)
        _json_bytes(entry)
        result.append(entry)
    return result


def _provider_snapshot_path(output, digest):
    if not isinstance(digest, str) or not _SHA.fullmatch(digest):
        raise ValueError('Invalid provider photo digest')
    return output / 'provider_photos' / (digest + '.image')


def _missing_provider_pins(output):
    """Recover identity from the old request; never manufacture missing pixels.

    The request is subsequently rebound byte-for-byte and task receipts verify
    its hash. Legacy requests without snapshots may still GET a known task.
    """
    request_path = output / 'request.json'
    if not request_path.is_file():
        return {}, set()
    saved = json.loads(request_path.read_bytes())
    provider_ids = set(saved.get('provider_only_photos', []))
    rows = [r for r in [*saved.get('selection', {}).get('selected', []),
                       *saved.get('selection', {}).get('unused', [])] if r['id'] in provider_ids]
    known = any((output / name).is_file() for name in
                ('task_receipt.json', 'submit_request.json', 'generation_receipt.json', 'artifact_receipt.json'))
    receipt_path = output / 'provider_photos.json'
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_bytes())
        if receipt.get('request_sha256') != _hash(_json_bytes(saved)):
            raise ValueError('Provider photo snapshot receipt belongs to another request')
        legacy_missing = set(receipt.get('legacy_unsnapshotted_photo_ids', []))
    else:
        legacy_missing = provider_ids if known else set()
    pins = {}
    for row in rows:
        snapshot = _provider_snapshot_path(output, row['sha256'])
        if snapshot.is_file():
            raw = snapshot.read_bytes()
            _check_pin(raw, row['sha256'])
            if len(raw) != row['bytes']:
                raise ValueError('Provider photo snapshot size differs')
            pins[row['id']] = row
        elif known and row['id'] in legacy_missing:
            pins[row['id']] = row
    return pins, legacy_missing


def _snapshot_provider_photos(output, photos, request_sha, legacy_missing):
    """Pin exact extra-photo bytes before submission; keep old request identity."""
    receipt_path = output / 'provider_photos.json'
    old = json.loads(receipt_path.read_bytes()) if receipt_path.is_file() else None
    unavailable, records, paths = [], [], {}
    for row in photos:
        destination = _provider_snapshot_path(output, row['sha256'])
        if destination.is_file():
            _check_pin(destination.read_bytes(), row['sha256'])
        elif Path(row['path']).is_file():
            raw = Path(row['path']).read_bytes()
            _check_pin(raw, row['sha256'])
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                _write_exclusive(destination, raw)
            except FileExistsError:
                _check_pin(destination.read_bytes(), row['sha256'])
        elif row['id'] in legacy_missing:
            unavailable.append(row['id'])
        else:
            raise ValueError('Provider photo snapshot and original are missing')
        if destination.is_file():
            paths[row['id']] = str(destination)
        records.append({'photo_id': row['id'], 'source_path': row['path'], 'sha256': row['sha256'],
                        'bytes': row['bytes'], 'snapshot': destination.relative_to(output).as_posix()})
    receipt = {'schema_version': 1, 'method': 'exact_provider_photo_snapshots_v1',
               'request_sha256': request_sha, 'photos': records,
               'legacy_unsnapshotted_photo_ids': old['legacy_unsnapshotted_photo_ids'] if old else unavailable}
    _bind_json(receipt_path, receipt)
    return paths, unavailable


def _selection_with_provider_snapshots(selection, snapshot_paths, provider_ids):
    selection = deepcopy(selection)
    for row in [*selection['selected'], *selection['unused']]:
        if row['id'] in snapshot_paths:
            row['source_path'], row['path'] = row['path'], snapshot_paths[row['id']]
            row['source_availability'] = 'owned_exact_snapshot'
        elif row['id'] in provider_ids:
            row['source_availability'] = 'missing_legacy_unsnapshotted_input'
    return selection


_VIEWS = {"front": (0, 0), "back": (180, 0), "left": (-90, 0), "right": (90, 0),
          "front_left": (-45, 0), "front_right": (45, 0), "back_left": (-135, 0),
          "back_right": (135, 0), "top": (0, 90), "bottom": (0, -90)}


def _direction(photo: dict):
    # These are declared labels/angles, never camera estimates from pixels.
    angle = photo.get("yaw_degrees")
    elevation = photo.get("elevation_degrees", 0)
    if angle is None:
        pair = _VIEWS.get(photo["view"].lower().replace("-", "_").replace(" ", "_"))
        if pair is None:
            return None
        angle, elevation = pair
    if isinstance(angle, bool) or isinstance(elevation, bool) or not all(
            isinstance(v, (int, float)) and math.isfinite(v) for v in (angle, elevation)) or abs(elevation) > 90:
        raise ValueError("Declared view angles must be finite, with elevation in [-90,90]")
    yaw, pitch = math.radians(angle), math.radians(elevation)
    return (math.sin(yaw)*math.cos(pitch), math.sin(pitch), math.cos(yaw)*math.cos(pitch))


def select_meshy_views(photos: list[dict]) -> dict:
    """Select <=4 pinned ledger entries; every unused photo remains for fitting.

    Prefer the declared frontmost direction as primary, then greedily maximize
    angular separation. Unknown-angle photos fill slots before redundant known
    directions. Equal scores use input order; exact-byte duplicates are never
    sent twice. Direction diversity is only a declared-view heuristic.
    """
    if not photos:
        raise ValueError("Meshy initialization needs at least one photo")
    directions = [_direction(p) for p in photos]
    unique, seen, duplicates = [], set(), set()
    for index, photo in enumerate(photos):
        if photo["sha256"] in seen:
            duplicates.add(index)
        else:
            unique.append(index)
            seen.add(photo["sha256"])
    known = [i for i in unique if directions[i] is not None]
    first = max(known, key=lambda i: directions[i][2]) if known else unique[0]
    chosen = [first]
    reasons = {first: "declared_frontmost_primary" if known else "unknown_angle_primary_by_input_order"}
    while len(chosen) < min(4, len(unique)):
        remaining = [i for i in unique if i not in chosen]
        known_chosen = [directions[i] for i in chosen if directions[i] is not None]
        scores = {i: min(1 - sum(a*b for a, b in zip(directions[i], old)) for old in known_chosen)
                  for i in remaining if directions[i] is not None and known_chosen}
        diverse = [i for i in scores if scores[i] > 1e-12]
        unknown = [i for i in remaining if directions[i] is None]
        if diverse:
            pick = max(diverse, key=scores.get)
            reason = "declared_angular_diversity"
        elif unknown:
            pick, reason = unknown[0], "unknown_angle_by_input_order"
        else:
            pick, reason = remaining[0], "redundant_direction_by_input_order"
        chosen.append(pick)
        reasons[pick] = reason
    return {"policy": "declared_direction_farthest_v1", "diversity_verified_from_pixels": False,
            "primary_is_declared_front": photos[first]["view"].lower() == "front",
            "selected": [dict(photos[i], selection_reason=reasons[i]) for i in chosen],
            "unused": [dict(photo, unused_reason="duplicate_bytes" if i in duplicates else "provider_view_limit")
                       for i, photo in enumerate(photos) if i not in chosen],
            "all_photos_retained_for_fitting": True}


def _settings(overrides) -> dict:
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        raise ValueError("Meshy settings must be a dictionary")
    allowed = set(MESHY_DEFAULT_SETTINGS) | {"geometry_resolution", "image_enhancement", "remove_lighting"}
    if set(overrides) - allowed:
        raise ValueError("Unsupported Meshy settings; image inputs are owned by the selection ledger")
    result = {**MESHY_DEFAULT_SETTINGS, **deepcopy(overrides)}
    if result["ai_model"] != "meshy-7.1" or result["target_formats"] != ["glb"]:
        raise ValueError("This adapter pins meshy-7.1 and GLB output")
    if result["texture_resolution"] not in {"2k", "4k", "8k"} or result.get("geometry_resolution", "standard") not in {"standard", "2k"}:
        raise ValueError("Unsupported provider resolution")
    for key in ("should_texture", "enable_pbr", "should_remesh", "image_enhancement", "remove_lighting"):
        if key in result and type(result[key]) is not bool:
            raise ValueError(f"{key} must be boolean")
    return result


def _validate_glb(raw: bytes, directory: Path) -> dict:
    """Validate the evaluator's bounded static triangle profile, not AR quality."""
    if not isinstance(raw, bytes) or not 28 <= len(raw) <= MAX_MODEL_BYTES:
        raise ValueError("Expected bounded GLB bytes")
    if struct.unpack_from("<4sII", raw) != (b"glTF", 2, len(raw)):
        raise ValueError("Expected an intact GLB version 2")
    offset, chunks = 12, []
    while offset < len(raw):
        if offset + 8 > len(raw):
            raise ValueError("Truncated GLB chunk")
        size, kind = struct.unpack_from("<II", raw, offset)
        offset += 8
        if size % 4 or offset + size > len(raw):
            raise ValueError("Invalid GLB chunk extent/alignment")
        chunks.append((kind, raw[offset:offset+size]))
        offset += size
    if [c[0] for c in chunks] != [0x4E4F534A, 0x004E4942]:
        raise ValueError("Expected a self-contained JSON/BIN GLB")
    doc = json.loads(chunks[0][1])
    if doc.get("asset", {}).get("version") != "2.0":
        raise ValueError("Expected glTF 2.0 metadata")
    if doc.get("skins") or doc.get("animations") or any("skin" in n for n in doc.get("nodes", [])):
        raise ValueError("Bake animated/skinned geometry before initialization")
    for mesh in doc.get("meshes", []):
        if mesh.get("weights") or any(p.get("targets") for p in mesh.get("primitives", [])):
            raise ValueError("Bake morph targets before initialization")
    for node in doc.get("nodes", []):
        if "EXT_mesh_gpu_instancing" in node.get("extensions", {}):
            raise ValueError("Bake GPU instancing before initialization")
    if any("EXT_meshopt_compression" in view.get("extensions", {}) for view in doc.get("bufferViews", [])):
        raise ValueError("Decode meshopt compression before initialization")
    for image in doc.get("images", []):
        if "uri" in image and not image["uri"].startswith("data:"):
            raise ValueError("External image resources must be embedded before copying")
    # Validate exactly the bytes that will be copied, not a second source read.
    with tempfile.NamedTemporaryFile(dir=directory, suffix=".glb", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(raw)
    try:
        mesh = load_glb(temporary)
        if not np.isfinite(mesh.vertices).all() or not len(mesh.faces):
            raise ValueError("Static model has invalid world geometry")
        return {"profile": "embedded_static_triangle_geometry_v1", "vertices": len(mesh.vertices),
                "triangles": len(mesh.faces), "parts": len(mesh.parts),
                "materials_and_optical_semantics_validated": False, "topology_validated": False}
    except (KeyError, IndexError, TypeError, struct.error) as error:
        raise ValueError("Unsupported or malformed static GLB") from error
    finally:
        temporary.unlink(missing_ok=True)


def recover_initializer_task(output: Path, task_id: str, *, request_sha256: str, phase: str = "generation") -> None:
    """Explicit operator reconciliation after uncertain submission; never POST.

    The caller must establish the provider task belongs to this exact request.
    The hash prevents accidental cross-job association; it cannot prove that
    external assertion. Preserve this manual intervention in the receipt.
    ``phase`` names the generation directory or its ``split`` subdirectory.
    """
    if phase not in ("generation", "split"):
        raise ValueError("phase must be generation or split")
    output = Path(output) if phase == "generation" else Path(output) / "split"
    request = json.loads((output / "request.json").read_text("utf-8"))
    if _hash(_json_bytes(request)) != request_sha256:
        raise ValueError("Recovered task request hash mismatch")
    reservation = json.loads((output / "submit_request.json").read_text("utf-8"))
    if reservation.get("request_sha256") != request_sha256:
        raise ValueError("Recovered task reservation mismatch")
    _bind_json(output / "task_receipt.json", {"task_id": _task_id(task_id), "request_sha256": request_sha256,
                                           "association": "explicit_operator_reconciliation"})


def _provider_task(folder: Path, request_sha: str, *, available: bool, bind, allow_submit: bool,
                   preflight, submit, retrieve, download):
    """Advance one paid provider task by at most one submit/retrieve/download cycle.

    Returns ``("complete", {raw, task, task_id})`` or a non-complete status with
    its report fields. Reservation, task, observation and terminal receipts live
    in ``folder``; a reservation without a receipt stays uncertain forever.
    """
    from .meshy_transport import ExpiredDownloadError
    folder.mkdir(parents=True, exist_ok=True)
    task_receipt, reservation = folder / "task_receipt.json", folder / "submit_request.json"
    task_id = None
    if task_receipt.exists():
        saved = json.loads(task_receipt.read_text("utf-8"))
        if saved.get("request_sha256") != request_sha:
            raise ValueError("Saved provider task request mismatch")
        task_id = _task_id(saved.get("task_id"))
    elif reservation.exists():
        return "submission_uncertain", {"recovery": "Reconcile provider task explicitly; never resubmit this request."}
    if not available:
        return "awaiting_backend", {"task_id": task_id}
    bind()
    if task_id is None:
        if not allow_submit:
            return "awaiting_submission_authorization", {}
        # Reject configuration/budget/input errors before reserving a paid
        # request. A rejected preflight has no uncertain provider outcome.
        try:
            preflight()
        except Exception as error:
            return "submission_not_authorized", {"error_type": type(error).__name__}
        try:
            _write_exclusive(reservation, _json_bytes({"request_sha256": request_sha, "provider": "meshy"}))
        except FileExistsError:
            return "submission_uncertain", {"recovery": "Another caller reserved submission; do not resubmit."}
        try:
            task_id = _task_id(submit())
            _bind_json(task_receipt, {"request_sha256": request_sha, "task_id": task_id, "association": "submit_response"})
        except Exception as error:
            return "submission_uncertain", {"error_type": type(error).__name__}
    terminal_path = folder / "terminal_task.json"
    try:
        if terminal_path.exists():
            task = json.loads(terminal_path.read_text("utf-8"))
        else:
            task = retrieve(task_id)
            _write_exclusive(folder / f"task-observation-{uuid.uuid4().hex}.json", _json_bytes(task))
        if task.get("task_id") != task_id or task.get("status") not in {"pending", "succeeded", "failed"}:
            raise ValueError("Backend task identity/status mismatch")
        if task["status"] == "pending":
            return "pending", {"task_id": task_id, "provider_observation": task}
        _bind_json(terminal_path, task)
        if task["status"] == "failed":
            return "provider_failed", {"task_id": task_id, "provider_observation": task}
    except Exception as error:
        return "poll_error", {"task_id": task_id, "error_type": type(error).__name__}
    try:
        raw = download(deepcopy(task))
    except Exception as error:
        if not isinstance(error, ExpiredDownloadError):
            return "download_error", {"task_id": task_id, "error_type": type(error).__name__}
        # A signed URL can expire while terminal task metadata remains valid.
        # Preserve that first terminal receipt and GET only the known task;
        # never submit a replacement, even if the refresh/download fails.
        try:
            refreshed = retrieve(task_id)
            _write_exclusive(folder / f"task-refresh-{uuid.uuid4().hex}.json", _json_bytes({
                "reason": "explicit_expired_download_response", "prior_terminal_sha256": _hash(terminal_path.read_bytes()),
                "request_sha256": request_sha, "task": refreshed}))
            if refreshed.get("task_id") != task_id or refreshed.get("status") != "succeeded":
                raise ValueError("Refreshed task identity/status mismatch")
            raw = download(deepcopy(refreshed))
            task = refreshed
        except Exception as refresh_error:
            return "download_error", {"task_id": task_id, "error_type": type(refresh_error).__name__,
                                      "url_refresh_attempted": True}
    return "complete", {"raw": raw, "task": task, "task_id": task_id}


def _split_request(initializer: dict) -> dict | None:
    if "split" not in initializer:
        return None
    split = initializer["split"]
    if split is None:
        split = {}
    if not isinstance(split, dict) or set(split) - {"parts"}:
        raise ValueError("split accepts only an optional parts list")
    parts = split.get("parts", list(EYEWEAR_SPLIT_PARTS))
    if not isinstance(parts, list) or not 1 <= len(parts) <= 10 or any(
            not isinstance(p, str) or not p.strip() or "," in p or len(p) > 40 for p in parts) or len(set(parts)) != len(parts):
        raise ValueError("split.parts names one to ten distinct comma-free parts")
    return {"settings": dict(MESHY_SPLIT_SETTINGS), "parts": list(parts),
            "part_names_are_provider_hints": True, "semantic_identity": "unverified"}


def resolve_initial_model(initializer: dict, base_dir: Path, output: Path, photos: list[dict], *,
                          backend: InitializerBackend | None = None, allow_submit: bool = False) -> dict:
    """Resolve one immutable initializer request, offline unless backend is passed.

    ``output`` is a stage directory. Photos are {id?,path,sha256?,view?,...},
    with paths relative to base_dir. Existing source: {kind:'existing_glb',path,
    sha256?}. Meshy: {kind:'meshy',settings?:dict,cached_glb?:{path,sha256?},
    provider_views?:[photo...],split?:{parts?:[...]}}. ``provider_views`` are
    extra photographs offered to the provider only; they are never fitting
    inputs. ``split`` adds a second paid task that cuts the finished generation
    into named closed parts; the generation is retained as ``generated.glb`` and
    the split parts become ``initial.glb``. A cache import is an unverified-origin
    artifact, not a provider/photo claim, and cannot be split.

    Reports expose status, phase, selected/unused/all_photos and, once complete,
    model:'initial.glb', sha256 and source_sha256. Existing receipts pin all input
    hashes/settings. Changing the request requires a different output directory.
    Missing backend returns awaiting_backend. allow_submit must be explicitly
    True for the first POST of each phase; it never authorizes a repeated POST.
    Unknown submit outcome returns submission_uncertain until explicitly
    reconciled. Retrieval/download failures may resume the same saved task.
    """
    if not isinstance(initializer, dict) or type(allow_submit) is not bool:
        raise ValueError("Expected initializer dictionary and boolean allow_submit")
    kind = initializer.get("kind")
    allowed = {"kind", "path", "sha256"} if kind == "existing_glb" else {"kind", "settings", "cached_glb", "cached_generation", "provider_views", "split"}
    if kind not in {"existing_glb", "meshy"} or set(initializer) - allowed:
        raise ValueError("Unsupported initializer kind or fields")
    base_dir, output = Path(base_dir).resolve(), Path(output).resolve()
    provider_views = initializer.get("provider_views") or []
    if not isinstance(provider_views, list) or any(not isinstance(v, dict) for v in provider_views):
        raise ValueError("provider_views must be a list of photo dictionaries")
    extras = [{**v, "id": v.get("id", f"provider-view-{i + 1}")} for i, v in enumerate(provider_views)]
    missing_pins, legacy_missing = _missing_provider_pins(output) if extras else ({}, set())
    combined = _photo_ledger([*photos, *extras], base_dir, missing_provider_pins=missing_pins)
    ledger, provider_only = combined[:len(photos)], combined[len(photos):]
    for entry in ledger:
        entry["fitting_input"] = True
    for entry in provider_only:
        entry["fitting_input"] = False
    request = {"schema_version": 1, "kind": kind, "all_photos": ledger}
    split = _split_request(initializer) if kind == "meshy" else None
    source_raw = None
    if kind == "existing_glb" or "cached_glb" in initializer:
        source = initializer if kind == "existing_glb" else initializer["cached_glb"]
        if not isinstance(source, dict) or (kind == "meshy" and set(source) - {"path", "sha256"}):
            raise ValueError("cached_glb must contain path and optional sha256")
        if split is not None:
            raise ValueError("A cached artifact has no provider generation task to split")
        cached_generation = initializer.get("cached_generation")
        if cached_generation is not None:
            if kind != "meshy" or not isinstance(cached_generation, dict) or set(cached_generation) - {"path", "sha256"}:
                raise ValueError("cached_generation must contain path and optional sha256 beside a cached split")
            generation_path = _resolve_path(cached_generation.get("path"), base_dir)
            if generation_path.stat().st_size > MAX_MODEL_BYTES:
                raise ValueError("Cached generation exceeds size limit")
            generation_raw = generation_path.read_bytes()
            request["cached_generation"] = {"path": str(generation_path), "sha256": _check_pin(generation_raw, cached_generation.get("sha256"))}
        source_path = _resolve_path(source.get("path"), base_dir)
        if source_path.stat().st_size > MAX_MODEL_BYTES:
            raise ValueError("Source model exceeds size limit")
        source_raw = source_path.read_bytes()
        request["source"] = {"path": str(source_path), "sha256": _check_pin(source_raw, source.get("sha256"))}
        if source_path == output / "initial.glb":
            raise ValueError("Initializer source and destination must differ")
    elif "cached_generation" in initializer:
        raise ValueError("cached_generation needs a cached split")
    if kind == "meshy":
        request.update(settings=_settings(initializer.get("settings")), selection=select_meshy_views(combined),
                       provider_only_photos=[e["id"] for e in provider_only])
        if split is not None:
            request["split"] = split
    else:
        if provider_only:
            raise ValueError("provider_views need a provider initializer")
        request["selection"] = {"selected": [], "unused": [dict(p, unused_reason="existing_model_no_provider_input") for p in ledger],
                                "all_photos_retained_for_fitting": True}
    output.mkdir(parents=True, exist_ok=True)
    _bind_json(output / "request.json", request)
    request_sha = _hash(_json_bytes(request))
    execution_request = deepcopy(request)
    unavailable_provider_photos = []
    if provider_only:
        snapshot_paths, unavailable_provider_photos = _snapshot_provider_photos(output, provider_only, request_sha, legacy_missing)
        execution_request['selection'] = _selection_with_provider_snapshots(
            request['selection'], snapshot_paths, {row['id'] for row in provider_only})
    report = {"schema_version": 1, "kind": kind, "request_sha256": request_sha, "all_photos": ledger,
              **execution_request["selection"], "quality_verdict": "unmeasured", "photo_reconstruction_verified": False,
              "native_preparation_applied": False, "scale_and_semantics_verified": False}
    if provider_only:
        report["provider_only_photos"] = [e["id"] for e in provider_only]
        report['provider_photo_snapshots'] = {'receipt': 'provider_photos.json',
            'sha256': _hash((output / 'provider_photos.json').read_bytes()),
            'unavailable_legacy_photos': unavailable_provider_photos}

    def finish(status, **fields):
        result = dict(report, status=status, **fields)
        _write_report(output / "initializer-report.json", result)
        return result

    artifact_receipt = output / "artifact_receipt.json"
    if artifact_receipt.exists():
        saved = json.loads(artifact_receipt.read_text("utf-8"))
        if saved.get("request_sha256") != request_sha or saved.get("model") != "initial.glb":
            raise ValueError("Saved artifact receipt does not belong to this request")
        raw = (output / "initial.glb").read_bytes()
        _check_pin(raw, saved["sha256"])
        return finish("complete", **{k: v for k, v in saved.items() if k != "request_sha256"})

    task_id, provider_observation = None, None
    provenance = "existing_glb_copy" if kind == "existing_glb" else "local_cache_import_origin_unverified"
    generation = None
    if request.get("cached_generation"):
        # A cached textured generation beside a cached split: retained as the
        # appearance source with unverified origin, exactly like the split.
        generation_raw = Path(request["cached_generation"]["path"]).read_bytes()
        _check_pin(generation_raw, request["cached_generation"]["sha256"])
        destination = output / "generated.glb"
        if destination.exists():
            _check_pin(destination.read_bytes(), request["cached_generation"]["sha256"])
        else:
            _write_exclusive(destination, generation_raw)
        generation = {"model": "generated.glb", "sha256": request["cached_generation"]["sha256"], "bytes": len(generation_raw),
                      "task_id": None, "provenance": "local_cache_import_origin_unverified",
                      "role": "textured_generation_retained_as_appearance_source"}
    if source_raw is None:
        if backend is not None and backend.provider != kind:
            raise ValueError("Backend provider differs from initializer")
        generation_receipt = output / "generation_receipt.json"
        if generation_receipt.exists():
            saved = json.loads(generation_receipt.read_text("utf-8"))
            if saved.get("request_sha256") != request_sha:
                raise ValueError("Saved generation receipt does not belong to this request")
            source_raw = (output / "generated.glb").read_bytes()
            _check_pin(source_raw, saved["sha256"])
            task_id, provider_observation = saved["task_id"], saved.get("provider_observation")
            generation = {k: v for k, v in saved.items() if k != "request_sha256"}
        else:
            status, fields = _provider_task(
                output, request_sha, available=backend is not None,
                bind=lambda: backend.bind_output(output, request_sha) if hasattr(backend, "bind_output") else None,
                allow_submit=allow_submit,
                preflight=lambda: backend.preflight(deepcopy(execution_request)) if hasattr(backend, "preflight") else None,
                submit=lambda: backend.submit(deepcopy(execution_request)), retrieve=lambda t: backend.retrieve(t),
                download=lambda t: backend.download(t))
            if status != "complete":
                return finish(status, phase="generation", **fields)
            source_raw, task_id = fields["raw"], fields["task_id"]
            provider_observation = deepcopy(fields["task"])
        provenance = "provider_initial_hypothesis"
    if split is not None:
        if generation is None:
            try:
                validation = _validate_glb(source_raw, output)
            except (ValueError, UnicodeError, KeyError, IndexError, TypeError, AttributeError, OverflowError) as error:
                return finish("artifact_invalid", phase="generation", task_id=task_id, error_type=type(error).__name__)
            generation = {"model": "generated.glb", "sha256": _hash(source_raw), "bytes": len(source_raw),
                          "task_id": task_id, "provider_observation": provider_observation, "validation": validation,
                          "role": "textured_generation_retained_as_appearance_source"}
            destination = output / "generated.glb"
            if destination.exists():
                _check_pin(destination.read_bytes(), generation["sha256"])
            else:
                _write_exclusive(destination, source_raw)
            _bind_json(output / "generation_receipt.json", {"request_sha256": request_sha, **generation})
        split_folder = output / "split"
        split_folder.mkdir(exist_ok=True)
        split_request = {"input_task_id": task_id, **request["split"]}
        _bind_json(split_folder / "request.json", split_request)
        split_sha = _hash(_json_bytes(split_request))
        capable = backend is not None and all(hasattr(backend, name) for name in
                                              ("submit_split", "retrieve_split", "download_split", "preflight_split"))
        available = capable and getattr(backend, "split_transport", None) is not None
        status, fields = _provider_task(
            split_folder, split_sha, available=available,
            bind=lambda: backend.bind_split_output(split_folder, split_sha) if hasattr(backend, "bind_split_output") else None,
            allow_submit=allow_submit, preflight=lambda: backend.preflight_split(deepcopy(split_request)),
            submit=lambda: backend.submit_split(deepcopy(split_request)), retrieve=lambda t: backend.retrieve_split(t),
            download=lambda t: backend.download_split(t))
        if status != "complete":
            return finish(status, phase="split", generation=generation, **fields)
        source_raw = fields["raw"]
        provenance = "provider_split_parts_hypothesis"
        split_observation, split_task_id = deepcopy(fields["task"]), fields["task_id"]

    try:
        validation = _validate_glb(source_raw, output)
    except (ValueError, UnicodeError, KeyError, IndexError, TypeError, AttributeError, OverflowError) as error:
        return finish("artifact_invalid", phase="split" if split is not None else "generation",
                      task_id=task_id, error_type=type(error).__name__)
    digest = _hash(source_raw)
    destination = output / "initial.glb"
    if destination.exists():
        _check_pin(destination.read_bytes(), digest)
    else:
        _write_exclusive(destination, source_raw)
    saved = {"request_sha256": request_sha, "model": "initial.glb", "sha256": digest,
             "source_sha256": digest, "bytes": len(source_raw), "exact_source_bytes_preserved": True,
             "provenance": provenance, "task_id": task_id, "validation": validation, "phase": "complete"}
    if provider_observation is not None:
        saved["provider_observation"] = provider_observation
    if split is not None:
        saved.update(generation=generation, split_task_id=split_task_id, provider_split_observation=split_observation,
                     appearance_source="generated.glb", part_names="provider hints only; identity unverified")
    elif generation is not None:
        saved.update(generation=generation, appearance_source="generated.glb")
    _bind_json(artifact_receipt, saved)
    return finish("complete", **{k: v for k, v in saved.items() if k != "request_sha256"})
