"""Synthetic FT1119 job for A0 (WP5_DESIGN §8): q001 with a KNOWN print, rendered at the six WP3b cameras.

Truth: the WP3b cameras; q001 with its Brow_Right front faces moved TRUE_PARALLAX_MM along camera 05's view axis
(a geometry error the anchor cannot see). Inputs handed to registration: q001 undisplaced, and view 01's camera
perturbed by +1 deg yaw and a 2 mm pivot shift (diagonal in the image).

The print is a 3D value-noise havana (amber blotches on dark brown; three rotated, incommensurate octaves) attached
to BODY points: a ray hit (face, barycentrics) on the true surface takes the colour of the same
barycentric point of q001's own triangle, so the print moves with the surface exactly as photoprint's samples do.
Other parts are a neutral grey (not acetate), the background is light grey; light shading 0.85 + 0.15 |cos|. Photos
are rendered at 2 x 2 supersampling. This module computes its own face classes and displacement (it does not use
photoprint's parallax code), so recovering the injected parallax tests that code too.
"""
from __future__ import annotations

import numpy as np

from glasses3d.g3d_host import cameras as C
from glasses3d.g3d_host import matte as matte_mod
from glasses3d.g3d_host import raster
from glasses3d.g3d_host.camera import Camera
from glasses3d.g3d_host.photoprint import inputs as IN

PRINT_PARTS = IN.FT1119_PARTS
PRINT_CELL_MM = 1.4
TRUE_PARALLAX_MM = 2.0
PERTURB_YAW_DEG = 1.0
PERTURB_PIVOT_MM = 2.0
BG = (238.0, 238.0, 238.0)
GREY = (150.0, 150.0, 150.0)
DARK = np.array([58.0, 34.0, 22.0])
AMBER = np.array([205.0, 128.0, 52.0])
SUPERSAMPLE = 2


class Print3D:
    """Three-octave value noise in model mm -> havana colours (sRGB 0..255). Each octave has its own random rotation
    and offset and the octave cells are incommensurate (1.3, 0.77, 0.47 mm), with a quintic fade: a single axis-aligned
    lattice made the 18-shift null biased (mean r 0.09, shifts landing on whole lattice periods)."""

    CELLS = (1.3, 0.77, 0.47)
    WEIGHTS = (0.55, 0.30, 0.15)

    def __init__(self, lo, hi, cell: float = PRINT_CELL_MM, seed: int = 11):
        rng = np.random.default_rng(seed)
        self.center = (np.asarray(lo, float) + np.asarray(hi, float)) / 2
        radius = float(np.linalg.norm(np.asarray(hi, float) - np.asarray(lo, float)) / 2) + 3.0
        k = cell / PRINT_CELL_MM
        self.octaves = []
        for c, w in zip(self.CELLS, self.WEIGHTS):
            c = c * k
            q, _ = np.linalg.qr(rng.standard_normal((3, 3)))
            n = int(np.ceil(2 * radius / c)) + 3
            self.octaves.append((c, w, q, rng.uniform(0, c, 3), rng.random((n, n, n)).astype(np.float32), radius))
        qh, _ = np.linalg.qr(rng.standard_normal((3, 3)))
        nh = int(np.ceil(2 * radius / (2.6 * k))) + 3
        self.hue = (2.6 * k, qh, rng.uniform(0, 2.6 * k, 3), rng.random((nh, nh, nh)).astype(np.float32), radius)

    @staticmethod
    def _noise(P, c, q, off, g, radius):
        x = ((P - 0.0) @ q.T + radius + off) / c
        i = np.floor(x).astype(int)
        f = x - i
        f = f * f * f * (f * (f * 6 - 15) + 10)            # quintic fade: no derivative creases at cell faces
        i = np.clip(i, 0, np.array(g.shape) - 2)
        out = 0.0
        for dx in (0, 1):
            for dy in (0, 1):
                for dz in (0, 1):
                    w = ((f[:, 0] if dx else 1 - f[:, 0]) * (f[:, 1] if dy else 1 - f[:, 1])
                         * (f[:, 2] if dz else 1 - f[:, 2]))
                    out = out + w * g[i[:, 0] + dx, i[:, 1] + dy, i[:, 2] + dz]
        return out

    def colour(self, P) -> np.ndarray:
        P = np.asarray(P, float) - self.center
        t = sum(w * self._noise(P, c, q, off, g, r) for c, w, q, off, g, r in self.octaves)
        s = np.clip((t - 0.40) / 0.20, 0, 1)
        s = s * s * (3 - 2 * s)
        h = self._noise(P, *self.hue)
        col = DARK[None] * (1 - s[:, None]) + AMBER[None] * s[:, None]
        col[:, 1] *= 0.9 + 0.2 * h
        col[:, 2] *= 0.8 + 0.4 * (1 - h)
        return col


def front_faces(job, part_name: str) -> np.ndarray:
    """Faces of a part whose normal is nearest +z among the six axes (computed here, independently)."""
    pid = job.part_ids([part_name])[0]
    faces = np.nonzero(job.mesh.part == pid)[0]
    V, F = job.mesh.V, job.mesh.F
    n = np.cross(V[F[faces, 1]] - V[F[faces, 0]], V[F[faces, 2]] - V[F[faces, 0]])
    n /= np.linalg.norm(n, axis=1, keepdims=True)
    axes = np.array([[0, 0, 1], [0, 0, -1], [0, 1, 0], [0, -1, 0], [1, 0, 0], [-1, 0, 0]], float)
    return faces[(n @ axes.T).argmax(1) == 0]


def true_vertices(job, axis05: np.ndarray, amount: float = TRUE_PARALLAX_MM) -> np.ndarray:
    V = job.mesh.V.copy()
    vi = np.unique(job.mesh.F[front_faces(job, "Brow_Right")])
    V[vi] += amount * axis05[None] / np.linalg.norm(axis05)
    return V


def render_photo(job, Vtrue, cam: Camera, shape, colour_fn) -> np.ndarray:
    """2 x 2 supersampled render: print parts via colour_fn(face, bary, body point), others grey, background."""
    H, W = shape
    k = SUPERSAMPLE
    cam2 = C.resized_camera(cam, k)
    scene = raster.RasterScene(Vtrue, job.mesh.F, job.frame)
    r = scene.render(cam2, (H * k, W * k), 1, want_bary=True, want_normal=True)
    img = np.empty((H * k, W * k, 3))
    img[:] = BG
    m = r["mask"]
    fid = r["face_id"][m].astype(np.int64)
    b = r["bary"][m].astype(np.float64)
    bary = np.stack([1 - b[:, 0] - b[:, 1], b[:, 0], b[:, 1]], 1)
    body = np.einsum("kj,kjd->kd", bary, job.mesh.V[job.mesh.F[fid]])
    toward = raster.camera_basis(cam2)[2]
    shade = 0.85 + 0.15 * np.abs(r["normal"][m].astype(np.float64) @ toward)
    col = colour_fn(fid, bary, body)
    img[m] = np.clip(col * shade[:, None], 0, 255)
    img = img.reshape(H, k, W, k, 3).mean((1, 3))
    return np.clip(np.rint(img), 0, 255).astype(np.uint8)


def make_print_colour(job, printer: Print3D):
    print_pids = set(job.part_ids(PRINT_PARTS))
    is_print = np.array([p in print_pids for p in range(len(job.mesh.names))])

    def colour(fid, bary, body):
        out = np.empty((len(fid), 3))
        out[:] = GREY
        pp = is_print[job.mesh.part[fid]]
        out[pp] = printer.colour(body[pp])
        return out
    return colour


def make_astra_colour(job):
    def colour(fid, bary, body):
        return job.astra.colours(job.mesh.part, fid, bary[:, 1:])
    return colour


def perturbed_camera(job, name: str, yaw_deg: float = PERTURB_YAW_DEG, pivot_mm: float = PERTURB_PIVOT_MM) -> Camera:
    from glasses3d.g3d_host.photoprint.pose import pack, unpack
    cam = job.views[name].camera
    x = pack(job, cam)
    ppm = job.ppm(name)
    x[0] += yaw_deg
    x[5] += pivot_mm * ppm / np.sqrt(2)
    x[6] += pivot_mm * ppm / np.sqrt(2)
    return unpack(job, x)


def synthetic_job(real_job=None, *, seed: int = 11):
    """(job handed to registration, truth dict)."""
    real = real_job or IN.load_job()
    axis05 = raster.camera_basis(real.views["reference-05.png"].camera)[2]
    Vt = true_vertices(real, axis05)
    pids = real.part_ids(PRINT_PARTS)
    vv = real.mesh.V[np.unique(real.mesh.F[np.isin(real.mesh.part, pids)])]
    printer = Print3D(vv.min(0), vv.max(0), seed=seed)
    colour = make_print_colour(real, printer)
    views = {}
    for n, v in real.views.items():
        img = render_photo(real, Vt, v.camera, v.shape, colour)
        views[n] = IN.View(name=n, kind=v.kind, image=img, matte=matte_mod.matte(img), camera=v.camera)
    job = IN.Job(mesh=real.mesh, frame=real.frame, pivot=real.pivot, views=views,
                 sources={"synthetic": "tests/photoprint/synth.py", "print_seed": seed}, astra=real.astra)
    true_cams = {n: v.camera for n, v in real.views.items()}
    start = dict(true_cams)
    start["reference-01.png"] = perturbed_camera(real, "reference-01.png")
    job = job.with_cameras(start)
    truth = {"cameras": true_cams, "V": Vt, "axis05": axis05, "printer": printer, "real": real}
    return job, truth


def fit_shape(img: np.ndarray, shape, fill=BG) -> np.ndarray:
    """Crop or pad (top-left aligned) a photo to another view's shape (a swapped photo must fit the target array)."""
    H, W = shape
    out = np.empty((H, W, 3), np.uint8)
    out[:] = np.asarray(fill, np.uint8)
    h, w = min(H, img.shape[0]), min(W, img.shape[1])
    out[:h, :w] = img[:h, :w]
    return out
