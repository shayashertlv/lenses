"""Shared contract for every BSA stage: products, coordinate frames, cameras, artifacts.

Coordinate conventions (see DESIGN.md):
- MODEL frame: millimetres, +X to the viewer's right in the front photo, +Y up,
  +Z toward the front-photo camera (the lenses face +Z). Built in the canonical
  generator's frame, then exported in metres with the bridge underside at the origin.
- PIXEL frame: (u right, v down) in the NATIVE resolution of each photo.
- Cameras are reconstruction.camera.Camera values expressed in the NORMALIZED frame
  of the product (NormFrame): project(frame.to_norm(V_mm), camera) -> native pixels.
  Every mesh (generator, constructed, delivered candidate, controls) is normalized
  with the SAME NormFrame. Never call TriangleMesh.normalized() on a candidate.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from reconstruction.camera import Camera, project  # noqa: F401  (re-exported)

AUTOMATION = Path(__file__).resolve().parents[1]
DATA = AUTOMATION / "data"
# run artifacts; ``BSA_DATA_DIR`` moves them (a scratch or reproducibility run, a hermetic test) - inherited by the
# gate subprocess. The ground truth is an INPUT and never moves with it.
BSA_DATA = Path(os.environ["BSA_DATA_DIR"]).resolve() if os.environ.get("BSA_DATA_DIR") else DATA / "bsa"
GROUND_TRUTH = DATA / "bsa" / "ground_truth"
DETECTOR_WEIGHTS = DATA / "models" / "glasses-detector-v1"

VIEWS = ("front", "back", "left", "right", "angled")
FIT_VIEWS = ("front", "back", "left", "right")   # may shape or select anything
HELD_OUT_VIEWS = ("angled",)                       # never used for any fit or choice; judge only

DEFAULT_FRONT_WIDTH_MM = 140.0


@dataclass(frozen=True)
class Product:
    """One cached product: its photos and the cached generator/candidate assets (no paid calls)."""
    id: str
    photos_dir: Path
    generation_glb: Path            # raw Tripo H3.1 multiview textured generation (~1.9M triangles)
    candidate_glb: Path | None      # previous route's delivered candidate (baseline competitor)
    kind: str                       # informative only; never used by an algorithm
    front_width_mm: float = DEFAULT_FRONT_WIDTH_MM

    def photo_path(self, view: str) -> Path:
        return self.photos_dir / f"{view}.jpg"


_SEG = DATA / "segmented-pipeline-v1" / "jobs"
_PC = DATA / "provider-comparison-v1"
PRODUCTS: dict[str, Product] = {
    "miu": Product("miu", _PC / "inputs" / "miu", _PC / "runs" / "miu-tripo" / "artifacts" / "model.glb",
                   _SEG / "miu" / "candidate.glb", "gold thin-metal rimless, clear lenses"),
    "oakley": Product("oakley", _PC / "inputs" / "oakley", _PC / "runs" / "oakley-tripo" / "artifacts" / "model.glb",
                      _SEG / "oakley" / "candidate.glb", "sport shield, green/purple mirror"),
    "rayban": Product("rayban", _SEG / "rayban" / "inputs", _SEG / "rayban" / "providers" / "generation" / "artifacts" / "model.glb",
                      _SEG / "rayban" / "candidate.glb", "tortoise acetate full-rim, dark tint"),
    "vb": Product("vb", _SEG / "vb" / "inputs", _SEG / "vb" / "providers" / "generation" / "artifacts" / "model.glb",
                  _SEG / "vb" / "candidate.glb", "navy acetate full-rim, brown gradient"),
    "invu": Product("invu", _SEG / "invu" / "inputs", _SEG / "invu" / "providers" / "generation" / "artifacts" / "model.glb",
                    _SEG / "invu" / "candidate.glb", "sport shield, blue mirror"),
}


def load_photo(product: Product, view: str) -> np.ndarray:
    """Native-resolution 8-bit RGB (EXIF orientation is absent in this corpus)."""
    return np.asarray(Image.open(product.photo_path(view)).convert("RGB"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class NormFrame:
    """Maps MODEL millimetres to the normalized frame the cameras live in."""
    center: tuple[float, float, float]
    extent: float

    def to_norm(self, v_mm: np.ndarray) -> np.ndarray:
        return (np.asarray(v_mm, float) - np.asarray(self.center)) / self.extent

    def to_mm(self, v_norm: np.ndarray) -> np.ndarray:
        return np.asarray(v_norm, float) * self.extent + np.asarray(self.center)

    def to_dict(self) -> dict:
        return {"center": list(self.center), "extent": self.extent}

    @staticmethod
    def from_dict(d: dict) -> "NormFrame":
        return NormFrame(tuple(float(x) for x in d["center"]), float(d["extent"]))


def camera_from_dict(d: dict) -> Camera:
    return Camera(**{k: float(d[k]) for k in ("yaw", "pitch", "roll", "perspective", "scale", "center_x", "center_y")})


def px_per_mm(camera: Camera, frame: NormFrame) -> float:
    """Image pixels per model millimetre near the camera's projection centre."""
    return camera.scale / frame.extent


def project_mm(v_mm: np.ndarray, camera: Camera, frame: NormFrame) -> np.ndarray:
    return project(frame.to_norm(v_mm), camera)


@dataclass
class StageDir:
    """Artifact folder of one stage for one product: result.json + arrays.npz + sheets."""
    root: Path

    def __post_init__(self):
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def result_path(self) -> Path:
        return self.root / "result.json"

    def done(self) -> bool:
        return self.result_path.exists()

    def save(self, result: dict, arrays: dict[str, np.ndarray] | None = None) -> None:
        if arrays:
            np.savez_compressed(self.root / "arrays.npz", **arrays)
        tmp = self.result_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, indent=1, default=_json_default))
        tmp.replace(self.result_path)

    def load(self) -> tuple[dict, dict[str, np.ndarray]]:
        result = json.loads(self.result_path.read_text())
        arrays_path = self.root / "arrays.npz"
        arrays = dict(np.load(arrays_path, allow_pickle=False)) if arrays_path.exists() else {}
        return result, arrays


def _json_default(o: Any):
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    if hasattr(o, "to_dict"):
        return o.to_dict()
    raise TypeError(f"Not JSON serializable: {type(o)}")


def run_dir(run: str, product: str) -> Path:
    return BSA_DATA / "runs" / run / product


def stage_dir(run: str, product: str, stage: str) -> StageDir:
    return StageDir(run_dir(run, product) / stage)


STAGES = ("s0_intake", "s1_generator", "s2_front", "s3_cameras", "s4_depth", "s5_temples",
          "s6_assembly", "s7_texture", "s8_lens", "s9_export", "s10_gate")
