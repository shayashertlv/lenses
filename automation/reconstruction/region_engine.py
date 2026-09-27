"""Offline SAM2.1 Base Plus region proposals, never semantic component evidence.

Weights and implementation are recorded, not fetched. Dependencies/model are lazy;
one image embedding serves every prompt in a predict call. Three alternatives,
including empty or identical masks, are preserved in decoder order. The model's
predicted quality is not calibrated lens/frame confidence or boundary accuracy.

The local Ultralytics adapter temporarily blocks Python socket connections and
sets offline environment flags while running, restoring both on exit. Its lock
serializes engine calls; unrelated threads should not do network work during a
call. This is an offline guard, not a security sandbox for untrusted packages.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import socket
import tempfile
import threading
import types

import numpy as np


_LOCK = threading.RLock()
_ENVIRONMENT = {"YOLO_OFFLINE": "true", "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
_LIMITATIONS = [
    "Masks are image-conditioned region proposals without lens/frame/component labels.",
    "Predicted quality is a model score, not semantic confidence or calibrated boundary accuracy.",
    "A prompt may select background, shadows, reflections, or multiple physical components.",
    "No physical optical properties, hidden geometry, or reconstruction acceptance are established.",
]


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return _stream_sha(stream)


def _stream_sha(stream) -> str:
    digest = hashlib.sha256()
    while block := stream.read(4 * 1024 * 1024):
        digest.update(block)
    return digest.hexdigest()


def _dependency_pins() -> dict:
    """Pin installed Ultralytics Python/config sources without importing it.

    This includes SAM architecture modules and shared preprocessing/inference
    utilities, not just the adapter entry points. Native dependency binaries are
    identified by package versions, not claimed to have a binary content pin.
    """
    try:
        distribution = importlib.metadata.distribution("ultralytics")
    except importlib.metadata.PackageNotFoundError:
        return {"status": "not_installed"}
    manifest = {}
    for relative in sorted(distribution.files or [], key=str):
        name = relative.as_posix()
        if name.startswith("ultralytics/") and relative.suffix in (".py", ".yaml", ".yml"):
            manifest[name] = _sha(Path(distribution.locate_file(relative)))
    if not manifest:
        raise RuntimeError("Ultralytics installation lacks a source inventory")
    entries = ("ultralytics/models/sam/build.py", "ultralytics/models/sam/predict.py", "ultralytics/utils/ops.py")
    if not all(name in manifest for name in entries):
        raise RuntimeError("Ultralytics installation lacks required inference sources")
    return {"status": "pinned", "manifest_sha256": hashlib.sha256(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "source_files": len(manifest), "entry_points": {name: manifest[name] for name in entries},
            "scope": "all distribution-listed ultralytics Python and YAML sources; native binaries version-identified only"}


@contextmanager
def _offline(runtime: Path):
    """Restore every process-global change even when an import or inference fails."""
    def blocked(*_args, **_kwargs):
        raise RuntimeError("Network access is disabled for OfflineSAM2RegionEngine")

    environment = {**_ENVIRONMENT, "YOLO_CONFIG_DIR": str(runtime)}
    previous = {key: os.environ.get(key) for key in environment}
    targets = [(socket, "create_connection"), (socket, "getaddrinfo"),
               (socket.socket, "connect"), (socket.socket, "connect_ex"), (socket.socket, "sendto")]
    originals = [(owner, name, getattr(owner, name)) for owner, name in targets]
    try:
        os.environ.update(environment)
        for owner, name, _ in originals:
            setattr(owner, name, blocked)
        yield
    finally:
        for owner, name, original in originals:
            setattr(owner, name, original)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _prompts(value: list[dict], width: int, height: int) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise ValueError("prompts must be a nonempty list")
    result = []
    for prompt in value:
        if not isinstance(prompt, dict) or set(prompt) - {"bbox_xyxy", "points_xy", "point_labels"}:
            raise ValueError("A prompt only accepts bbox_xyxy, points_xy, and point_labels")
        box = prompt.get("bbox_xyxy")
        if not isinstance(box, list) or len(box) != 4:
            raise ValueError("bbox_xyxy must contain four coordinates")
        x0, y0, x1, y1 = [_number(v, "bbox_xyxy") for v in box]
        # Exclusive upper pixel-center coordinates become SAM's inclusive corners.
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height and x1 - x0 >= 1 and y1 - y0 >= 1):
            raise ValueError("bbox_xyxy must lie inside the image and span at least one pixel per axis")
        points, labels = prompt.get("points_xy", []), prompt.get("point_labels", [])
        if not isinstance(points, list) or not isinstance(labels, list) or len(points) != len(labels):
            raise ValueError("points_xy and point_labels must be equally sized lists")
        checked = []
        for point, label in zip(points, labels, strict=True):
            if not isinstance(point, list) or len(point) != 2 or type(label) is not int or label not in (0, 1):
                raise ValueError("Each point requires two coordinates and an integer label 0 or 1")
            x, y = [_number(v, "points_xy") for v in point]
            if not (0 <= x <= width - 1 and 0 <= y <= height - 1):
                raise ValueError("Point coordinates must be pixel centers inside the image")
            checked.append([x, y])
        result.append({"bbox_xyxy": [x0, y0, x1, y1], "points_xy": checked, "point_labels": list(labels)})
    return result


def _checked_output(raw, count: int, shape: tuple[int, int]) -> list[list[dict]]:
    if not isinstance(raw, list) or len(raw) != count:
        raise RuntimeError("SAM2 output does not match the prompt count")
    result = []
    for candidates in raw:
        if not isinstance(candidates, list) or len(candidates) != 3:
            raise RuntimeError("SAM2 must preserve exactly three mask alternatives per prompt")
        checked = []
        for candidate in candidates:
            if not isinstance(candidate, dict) or set(candidate) != {"mask", "predicted_quality"}:
                raise RuntimeError("Invalid SAM2 candidate schema")
            mask, score = candidate["mask"], candidate["predicted_quality"]
            if not isinstance(mask, np.ndarray) or mask.dtype != np.bool_ or mask.shape != shape:
                raise RuntimeError("SAM2 masks must be bool arrays on the original image grid")
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
                raise RuntimeError("SAM2 predicted quality must be finite and in [0,1]")
            checked.append({"mask": mask.copy(), "predicted_quality": float(score)})
        result.append(checked)
    return result


def _clone(function, replacements):
    """Change builder dependencies in a private globals dictionary, not its module."""
    cloned = types.FunctionType(function.__code__, {**function.__globals__, **replacements},
                                function.__name__, function.__defaults__, function.__closure__)
    cloned.__kwdefaults__ = function.__kwdefaults__
    return cloned


def _strict_checkpoint(model, stream, torch):
    saved = torch.load(stream, map_location="cpu", weights_only=True)
    if not isinstance(saved, dict):
        raise ValueError("Checkpoint must contain a tensor state dictionary")
    state = saved.get("model", saved)
    if not isinstance(state, dict) or not state or any(not isinstance(key, str) or not torch.is_tensor(value)
                                                    for key, value in state.items()):
        raise ValueError("Checkpoint model state must be a nonempty named tensor dictionary")
    if any(not bool(torch.isfinite(value).all()) for value in state.values()):
        raise ValueError("Checkpoint contains nonfinite model parameters")
    loaded = model.load_state_dict(state, strict=True)
    if loaded.missing_keys or loaded.unexpected_keys:
        raise ValueError("Checkpoint state did not match the SAM2.1 Base Plus architecture exactly")
    return len(state)


class OfflineSAM2RegionEngine:
    """Pinned local SAM2.1 Base Plus adapter; no default checkpoint discovery.

    Coordinates use the original RGB image: integer (x,y) denotes a pixel center,
    and bbox right/bottom are exclusive. Points carry explicit 0/1 labels. Boxes
    span at least one pixel and are not silently clipped. Calls retain no image
    embedding afterward, but reuse the loaded model. Runtime writes (if needed
    by Ultralytics) go to runtime_dir or an owned temporary directory.
    """

    def __init__(self, weights: Path, *, expected_sha256: str | None = None, runtime_dir: Path | None = None):
        if not isinstance(weights, Path):
            raise TypeError("weights must be an explicit local pathlib.Path")
        self.weights = weights.resolve(strict=True)
        if not self.weights.is_file() or self.weights.stat().st_size == 0:
            raise ValueError("weights must be a nonempty local file")
        if expected_sha256 is not None and (not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)):
            raise ValueError("expected_sha256 must be 64 lowercase hexadecimal characters")
        self._sha256 = _sha(self.weights)
        if expected_sha256 is not None and expected_sha256 != self._sha256:
            raise ValueError("SAM2 weights SHA-256 does not match expected_sha256")
        if runtime_dir is not None and not isinstance(runtime_dir, Path):
            raise TypeError("runtime_dir must be a pathlib.Path")
        self._temporary = tempfile.TemporaryDirectory(prefix="lenses-sam2-") if runtime_dir is None else None
        self.runtime_dir = (Path(self._temporary.name) if self._temporary else runtime_dir).resolve()
        if self.runtime_dir.exists() and not self.runtime_dir.is_dir():
            raise ValueError("runtime_dir must be a directory")
        self._expected = expected_sha256
        self._predictor = None
        self._torch = None
        self._state_keys = None
        self._source_hashes = {}

    def _verify(self):
        if _sha(self.weights) != self._sha256:
            raise ValueError("SAM2 weights changed after engine construction")

    def describe(self) -> dict:
        """Stable engine identity, equal before/after inference and across instances.

        Runtime paths, loaded state and per-instance activity belong to receipt().
        Changed source/weight bytes still change or invalidate this identity.
        """
        self._verify()
        packages = {}
        for name in ("numpy", "torch", "torchvision", "ultralytics", "opencv-python"):
            try:
                packages[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                packages[name] = None
        result = {"schema_version": 1, "engine": "offline_sam2_1_base_plus_v1",
                  "weights": {"path": str(self.weights), "sha256": self._sha256,
                              "expected_sha256": self._expected, "bytes": self.weights.stat().st_size,
                              "load_policy": "torch.load(weights_only=True); load_state_dict(strict=True)"},
                  "code": {"adapter_sha256": _sha(Path(__file__)), "dependency_sources": _dependency_pins()},
                  "packages": packages, "runtime": {"python": platform.python_version(), "device": "cpu",
                              "dtype": "float32", "threads": 4, "image_size": 1024,
                              "network": "blocked_during_engine_calls", "image_embedding": "once_per_predict_call",
                              "alternatives_per_prompt": 3, "bbox_adapter": "exclusive_upper_minus_one",
                              "mask_postprocessing": "native_SAM_threshold_and_resize_only"},
                  "semantic_identity": "unmeasured", "limitations": list(_LIMITATIONS)}
        return json.loads(json.dumps(result, allow_nan=False))

    def receipt(self) -> dict:
        """Mutable execution details; deliberately excluded from resume identity."""
        return {"schema_version": 1, "model_loaded": self._predictor is not None,
                "state_keys": self._state_keys, "runtime_directory": str(self.runtime_dir),
                "loaded_dependency_sources": dict(self._source_hashes), "semantic_identity": "unmeasured"}

    def _load(self):
        if self._predictor is not None:
            return
        import torch
        import ultralytics.models.sam.build as build
        import ultralytics.models.sam.predict as prediction
        from ultralytics.utils import ops

        distribution = importlib.metadata.distribution("ultralytics")
        for relative, module in (("ultralytics/models/sam/build.py", build),
                                 ("ultralytics/models/sam/predict.py", prediction), ("ultralytics/utils/ops.py", ops)):
            if Path(module.__file__).resolve() != Path(distribution.locate_file(relative)).resolve():
                raise RuntimeError("Imported Ultralytics source differs from the pinned installed distribution")

        def local_loader(model, _architecture_marker):
            # Hash and deserialize the same open file; a path replacement cannot
            # substitute unverified bytes between a separate hash and load.
            with self.weights.open("rb") as stream:
                if _stream_sha(stream) != self._sha256:
                    raise ValueError("SAM2 weights changed before loading")
                stream.seek(0)
                self._state_keys = _strict_checkpoint(model, stream, torch)
                stream.seek(0)
                if _stream_sha(stream) != self._sha256:
                    raise ValueError("SAM2 weights changed during loading")
            return model

        builder = _clone(build._build_sam2, {"_load_checkpoint": local_loader})
        base_plus = _clone(build.build_sam2_b, {"_build_sam2": builder})
        # This marker selects SAM2.1 architecture flags, independent of filename;
        # the private loader always uses the verified local file above.
        model = base_plus(checkpoint="sam2.1_b.pt")
        predictor = prediction.SAM2Predictor(overrides={"device": "cpu", "imgsz": 1024, "half": False,
                  "conf": 0.0, "save": False, "verbose": False, "project": str(self.runtime_dir), "name": "engine"})
        predictor.setup_model(model=model, verbose=False)
        self._source_hashes = {name: _sha(Path(module.__file__)) for name, module in
                              (("sam.build", build), ("sam.predict", prediction), ("utils.ops", ops))}
        self._predictor, self._torch = predictor, torch

    def _run(self, image, prompts):
        predictor = self._predictor
        predictor.reset_image()
        try:
            predictor.set_image(image[..., ::-1].copy())  # Ultralytics consumes BGR.
            result = []
            for prompt in prompts:
                x0, y0, x1, y1 = prompt["bbox_xyxy"]
                points = prompt["points_xy"]
                masks, boxes = predictor.inference_features(predictor.features, image.shape[:2],
                    bboxes=[[x0, y0, x1 - 1, y1 - 1]], points=[points] if points else None,
                    labels=[prompt["point_labels"]] if points else None, multimask_output=True)
                if masks is None:
                    raise RuntimeError("SAM2 returned no masks")
                raw_masks, scores = masks.detach().cpu().numpy(), boxes[:, 4].detach().cpu().numpy()
                if len(raw_masks) != len(scores):
                    raise RuntimeError("SAM2 mask and score counts differ")
                result.append([{"mask": mask, "predicted_quality": float(score)}
                               for mask, score in zip(raw_masks, scores, strict=True)])
            return result
        finally:
            predictor.reset_image()

    def predict(self, image: np.ndarray, prompts: list[dict]) -> list[list[dict]]:
        if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3 or min(image.shape[:2]) <= 0:
            raise ValueError("image must be a nonempty uint8 RGB HWC array")
        checked = _prompts(prompts, image.shape[1], image.shape[0])
        with _LOCK:
            self._verify()
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
            with _offline(self.runtime_dir):
                import torch
                previous_threads = torch.get_num_threads()
                try:
                    torch.set_num_threads(4)
                    self._load()
                    with torch.inference_mode():
                        raw = self._run(np.ascontiguousarray(image), checked)
                    self._verify()
                    return _checked_output(raw, len(checked), image.shape[:2])
                finally:
                    torch.set_num_threads(previous_threads)
