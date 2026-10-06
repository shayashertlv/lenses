"""Inputs of WP5 step 0: the model, the photos, their mattes and WP3b cameras, and Astra's own texture.

Ported from the scratch `wp5lib.py` module globals (GLB, CAMS_JSON, JOB, MESH, SCENE, PIVOT, FACE_N, CAMS0, IMGS,
MATTES, VALID, ppm_local, ppm_pivot, astra_colours); PORT_REVIEW section 10. Everything the scratch kept in module
globals lives on a ``Job``; nothing is read at import time.

``load_job`` reads a WP3b ``cameras.json`` (``g3d_host.fit_views``): the GLB path and sha256, the skip list (thin
decals the cameras were fitted without), the NormFrame, the front pivot, and per image its path, sha256, view kind and
camera. Hashes are checked (a changed photo or model refuses). Mattes are recomputed with ``matte.matte`` defaults, the
matte the WP3b cameras were fitted on.

Conventions (g3d_host.camera): model millimetres; cameras map NormFrame-normalised points to native pixels whose
integer coordinates are pixel centres; colours sRGB 0..255.
"""
from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass, field, replace
from functools import cached_property
from pathlib import Path

import numpy as np
from PIL import Image

from .. import fitmesh, raster
from .. import glb as glbmod
from .. import matte as matte_mod
from .. import texture_utils as T
from ..camera import Camera, NormFrame
from . import signals as S

REPO = Path(__file__).resolve().parents[3]
FT1119 = {
    "cameras": REPO / "glasses3d/data/wp3b/ft1119/cameras.json",
    "glb": REPO / "glasses3d/data/reference/projection/q001.glb",
    "images": REPO / "automation/data/blender_agent/studio/jobs/d4b716a6afc445f89cbed8beddad4c37/images",
}
FT1119_PARTS = ("Brow_Left", "Brow_Right", "Temple_Left", "Temple_Right")   # the acetate (print) parts of q001


class InputError(ValueError):
    """The inputs do not match what the WP3b cameras were fitted on (hash, shape, missing file)."""


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_array(a: np.ndarray) -> str:
    a = np.ascontiguousarray(a)
    h = hashlib.sha256()
    h.update(str((a.shape, a.dtype.str)).encode())
    h.update(a.tobytes())
    return h.hexdigest()


def short_name(name: str) -> str:
    """'reference-01.png' -> '01' (other names: the file stem)."""
    stem = Path(name).stem
    return stem[len("reference-"):] if stem.startswith("reference-") else stem


@dataclass(eq=False)
class View:
    name: str
    kind: str
    image: np.ndarray            # (H, W, 3) uint8 sRGB
    matte: np.ndarray            # (H, W) bool
    camera: Camera               # the WP3b camera (registration starts here)
    sha256: str | None = None

    @property
    def short(self) -> str:
        return short_name(self.name)

    @property
    def shape(self) -> tuple[int, int]:
        return self.image.shape[:2]

    @cached_property
    def acetate(self) -> np.ndarray:
        return S.acetate_mask(self.image)

    @cached_property
    def valid(self) -> np.ndarray:
        return S.valid_mask(self.image, self.matte)


@dataclass(eq=False)
class AstraTexture:
    """Astra's own base-colour textures on the model's UVs: per face its three corner UVs and per part its texture."""
    uv: np.ndarray                         # (M, 3, 2) corner UVs (glTF convention: v down, 0..1)
    tex_of_part: np.ndarray                # (n_parts,) texture id or -1
    textures: dict[int, np.ndarray]        # id -> (h, w, 3) float32 sRGB
    factor_of_part: np.ndarray             # (n_parts, 3) baseColorFactor RGB (linear) used where a part has no texture

    def colours(self, part: np.ndarray, face: np.ndarray, bary: np.ndarray) -> np.ndarray:
        """Base colour (sRGB 0..255) at ray hits: face ids and open3d barycentrics (b0, b1) -> (K, 3)."""
        b = np.asarray(bary, np.float64)
        w = np.stack([1 - b[:, 0] - b[:, 1], b[:, 0], b[:, 1]], 1)
        uv = np.einsum("kj,kjd->kd", w, self.uv[face])
        out = np.zeros((len(face), 3))
        pf = part[face]
        tid = self.tex_of_part[pf]
        for t in np.unique(tid):
            s = tid == t
            if t < 0:
                out[s] = T.linear_to_srgb(self.factor_of_part[pf[s]])
                continue
            tex = self.textures[int(t)]
            th, tw = tex.shape[:2]
            out[s] = T.sample_bilinear(tex, uv[s, 0] * tw - 0.5, uv[s, 1] * th - 0.5)
        return out


def load_astra_texture(glb_path: str | Path, mesh: fitmesh.FitMesh) -> AstraTexture:
    g = glbmod.load(glb_path)
    prims = [p for p in g.primitives if p.node_name not in set(mesh.skipped) and p.mode == 4 and len(p.triangles)]
    if [p.node_name for p in prims] != mesh.node_names:
        raise InputError("GLB primitives do not match the fit mesh (order or skip list changed)")
    uv = np.concatenate([(p.uv0[p.triangles] if p.uv0 is not None else np.zeros((len(p.triangles), 3, 2)))
                         for p in prims])
    tex_of = np.full(len(prims), -1)
    fac = np.ones((len(prims), 3))
    textures: dict[int, np.ndarray] = {}
    for i, p in enumerate(prims):
        m = g.material(p.material_index) or {}
        pbr = m.get("pbrMetallicRoughness") or {}
        fac[i] = np.asarray(pbr.get("baseColorFactor", [1, 1, 1, 1])[:3], float)
        bct = pbr.get("baseColorTexture")
        if bct is None:
            continue
        src = g.doc["textures"][bct["index"]]["source"]
        tex_of[i] = src
        if src not in textures:
            data = g.image_bytes(src)
            if data is None:
                raise InputError(f"{glb_path}: image {src} is missing")
            rgb = np.asarray(Image.open(io.BytesIO(data)).convert("RGB")).astype(np.float32)
            # the base colour is texture x factor (linear light)
            textures[src] = rgb
    for i in range(len(prims)):
        t = tex_of[i]
        if t >= 0 and not np.allclose(fac[i], 1.0):
            lin = T.srgb_to_linear(textures[t]) * fac[i]
            key = 10_000 + i                       # a part-specific copy when the factor tints the texture
            textures[key] = T.linear_to_srgb(lin).astype(np.float32)
            tex_of[i] = key
    return AstraTexture(uv=uv, tex_of_part=tex_of, textures=textures, factor_of_part=fac)


@dataclass(eq=False)
class Job:
    mesh: fitmesh.FitMesh
    frame: NormFrame
    pivot: np.ndarray                      # normalised front pivot (cameras.ViewFit's)
    views: dict[str, View]
    sources: dict = field(default_factory=dict)
    astra: AstraTexture | None = None

    @property
    def V(self) -> np.ndarray:
        return self.mesh.V

    @property
    def F(self) -> np.ndarray:
        return self.mesh.F

    @property
    def part(self) -> np.ndarray:
        return self.mesh.part

    @cached_property
    def face_normals(self) -> np.ndarray:
        return face_normals(self.mesh.V, self.mesh.F)

    @cached_property
    def face_area(self) -> np.ndarray:
        V, F = self.mesh.V, self.mesh.F
        return 0.5 * np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1)

    @property
    def pivot_mm(self) -> np.ndarray:
        return self.frame.to_mm(self.pivot[None])[0]

    def ppm(self, view: str, cam: Camera | None = None) -> float:
        """Local px/mm at the front pivot through ``cam`` (default: the view's WP3b camera)."""
        return ppm_local(cam or self.views[view].camera, self.frame, self.pivot_mm[None])[0]

    def scene(self, V: np.ndarray | None = None) -> raster.RasterScene:
        return raster.get_scene(self.mesh.V if V is None else V, self.mesh.F, self.frame)

    def with_images(self, images: dict[str, np.ndarray], mattes: dict[str, np.ndarray] | None = None) -> "Job":
        """A copy with some photos replaced (tests: swapped or substituted photos); mattes recomputed unless given."""
        views = {}
        for n, v in self.views.items():
            if n in images:
                img = np.asarray(images[n], np.uint8)
                m = (mattes or {}).get(n)
                views[n] = replace(v, image=img, matte=matte_mod.matte(img) if m is None else np.asarray(m, bool),
                                   sha256=None)
            else:
                views[n] = v
        return replace(self, views=views)

    def with_cameras(self, cams: dict[str, Camera]) -> "Job":
        """A copy whose start (WP3b) cameras are replaced for some views."""
        return replace(self, views={n: (replace(v, camera=cams[n]) if n in cams else v) for n, v in self.views.items()})

    def part_ids(self, names) -> list[int]:
        return self.mesh.part_ids(names)


def face_normals(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    return n / (np.linalg.norm(n, axis=1, keepdims=True) + 1e-12)


def ppm_local(cam: Camera, frame: NormFrame, P_mm: np.ndarray) -> np.ndarray:
    """Local image px per model mm at points (perspective shrinks with depth along the view axis)."""
    toward = raster.camera_basis(cam)[2]
    zn = frame.to_norm(np.asarray(P_mm, float).reshape(-1, 3)) @ toward
    return cam.scale / frame.extent / (1.0 - cam.perspective * zn)


def load_job(cameras_json: str | Path = FT1119["cameras"], *, images_dir: str | Path | None = None,
             glb: str | Path | None = None, check_hashes: bool = True, with_astra: bool = True) -> Job:
    """The job WP3b fitted: model, photos (hash-checked), recomputed mattes and the fitted cameras."""
    cj = Path(cameras_json)
    doc = json.loads(cj.read_text(encoding="utf-8"))
    model = doc["model"]
    glb_path = Path(glb) if glb is not None else REPO / Path(str(model["path"]).replace("\\", "/"))
    if not glb_path.is_file():
        raise InputError(f"model {glb_path} not found")
    glb_sha = sha256_file(glb_path)
    if check_hashes and model.get("sha256") and glb_sha != model["sha256"]:
        raise InputError(f"{glb_path}: sha256 {glb_sha[:12]} differs from the cameras' model {model['sha256'][:12]}")
    frame = NormFrame.from_dict(model["frame"])
    mesh = fitmesh.load_fit_mesh(glb_path, skip=tuple(model.get("skip", ())), frame=frame)
    pivot = frame.to_norm(np.asarray(model["front_pivot_mm"], float)[None])[0]
    views: dict[str, View] = {}
    srcs = {"cameras_json": str(cj), "cameras_sha256": sha256_file(cj), "glb": str(glb_path), "glb_sha256": glb_sha,
            "images": {}}
    for name, rec in doc["images"].items():
        if rec.get("status", "ok") != "ok" or not rec.get("camera"):
            continue
        p = (Path(images_dir) / name) if images_dir is not None else REPO / Path(str(rec["image"]).replace("\\", "/"))
        if not p.is_file():
            raise InputError(f"photo {p} not found")
        sha = sha256_file(p)
        if check_hashes and rec.get("image_sha256") and sha != rec["image_sha256"]:
            raise InputError(f"{p}: sha256 {sha[:12]} differs from the one its camera was fitted on")
        img = matte_mod.load_photo(p)
        if rec.get("shape") and list(img.shape[:2]) != list(rec["shape"]):
            raise InputError(f"{p}: shape {img.shape[:2]} differs from the fitted {rec['shape']}")
        views[name] = View(name=name, kind=str(rec.get("view_kind", "")), image=img, matte=matte_mod.matte(img),
                           camera=Camera.from_dict(rec["camera"]), sha256=sha)
        srcs["images"][name] = {"path": str(p), "sha256": sha, "view_kind": rec.get("view_kind")}
    if len(views) < 2:
        raise InputError("fewer than two fitted photos")
    job = Job(mesh=mesh, frame=frame, pivot=pivot, views=views, sources=srcs)
    if with_astra:
        job.astra = load_astra_texture(glb_path, mesh)
    return job
