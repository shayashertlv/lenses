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


# ----------------------------------------------------------------------------- the null where it binds (fix round 1)
STRIP_PERIOD_MM = 8.0          # the gate's strip-null period (gate.STRIP_PERIOD_MM; restated here on purpose)
STRIP_ALPHA = 0.6              # weight of the periodic component
STRIP_BAND = (0.12, 3.0)       # shading band along the strip: 1 + a cos(2 pi y / period_mm)


def _fade(f):
    return f * f * f * (f * (f * 6 - 15) + 10)


class StripPrint:
    """A havana ELONGATED ALONG THE STRIP of the Brow_Right front, laid on the surface: real FT1119 blotches run along
    the strip and give the gate's strip null high maxima (0.41-0.87), which A0's isotropic print never does
    (0.15-0.29).

    Surface coordinates computed HERE (not the gate's): c = |x|, beta = the relative height across the front at that
    c (0..1, from Brow_Right's +z-facing vertices binned every 0.5 mm along c), h = beta x the front's median height.
    t = 0.5 + (alpha (p - 0.5) + (1 - alpha) (q - 0.5)) / sqrt(alpha^2 + (1 - alpha)^2): p = two octaves of 2D value
    noise PERIODIC in c with the null's own 8 mm period (cells 8/3 x 0.8 and 8/5 x 0.47 mm, indices wrapped in c),
    q = two octaves elongated along c but NOT periodic (2.9 x 0.85 and 1.7 x 0.5 mm); havana as Print3D; a shading band
    constant along the strip, 1 + 0.12 cos(2 pi h / 3 mm), body-fixed (the worst case for the null: in every photo).

    ``colour(P, shift_c_mm)`` = the print at (c + shift, beta): a photo of it is a WRONG correspondence that still
    correlates through p at zero shift (above the 0.45 floor, residual ~0); decoys shifted by multiples of 8 mm along
    the strip keep p too, so only the decoy rule can refuse it. The true correspondence correlates through p and q and
    must clear the decoys by 0.15 (the null's power where it binds). Measured on the way (fix round 1): with p periodic
    in 3D x instead (cells also along z), the brow's curvature broke p under the null's surface shift (decoy max
    0.30) and the 8 mm-wrong photo REGISTERED (peak 0.64): the null only discounts structure that repeats along its own
    surface parameterisation. Fix round 2 keeps that variant (``periodic='x3d'``: p = two octaves of 3D value noise in
    model mm, periodic in x with the 8 mm period, cells 8/3 x 0.8 x 0.8 and 8/5 x 0.47 x 0.47 mm) as a KNOWN FALSE
    POSITIVE of the gate (A0 reports it; tests/photoprint/test_register_synthetic.py pins it as xfail(strict))."""

    BIN_MM = 0.5
    PERIODIC = ("surface", "x3d")

    def __init__(self, job, part: str = "Brow_Right", seed: int = 23, alpha: float = STRIP_ALPHA, band=STRIP_BAND,
                 periodic: str = "surface"):
        if periodic not in self.PERIODIC:
            raise ValueError(f"periodic must be one of {self.PERIODIC}")
        rng = np.random.default_rng(seed)
        self.periodic = periodic
        self.alpha = float(alpha)
        self.band = band
        f = front_faces(job, part)
        P = job.mesh.V[np.unique(job.mesh.F[f])]
        c = np.abs(P[:, 0])
        self.c_lo = float(c.min()) - 1.0
        nb = int(np.ceil((c.max() + 1.0 - self.c_lo) / self.BIN_MM)) + 1
        k = np.clip(((c - self.c_lo) / self.BIN_MM).astype(int), 0, nb - 1)
        lo, hi = np.full(nb, np.inf), np.full(nb, -np.inf)
        np.minimum.at(lo, k, P[:, 1])
        np.maximum.at(hi, k, P[:, 1])
        good = np.isfinite(lo)
        x = np.arange(nb)
        self.ylo = np.interp(x, x[good], lo[good])
        self.yhi = np.interp(x, x[good], hi[good])
        self.nb = nb
        self.height = float(np.median(self.yhi - self.ylo))
        span_c = float(c.max() - self.c_lo) + 4 * STRIP_PERIOD_MM
        self.per = []
        for cx, ch, w in ((STRIP_PERIOD_MM / 3, 0.8, 0.65), (STRIP_PERIOD_MM / 5, 0.47, 0.35)):
            nx = int(round(STRIP_PERIOD_MM / cx))
            ny = int(np.ceil(self.height / ch)) + 4
            self.per.append((np.array([cx, ch]), w, nx, rng.random((nx, ny)).astype(np.float32)))
        self.non = []
        for cx, ch, w in ((2.9, 0.85, 0.65), (1.7, 0.5, 0.35)):
            n = (int(np.ceil(span_c / cx)) + 3, int(np.ceil(self.height / ch)) + 4)
            self.non.append((np.array([cx, ch]), w, rng.random(n).astype(np.float32)))
        # the 3D-x periodic variant (fix round 2: a known false positive of the strip null, kept as a tripwire)
        self.lo3 = P.min(0) - 2.0
        ext = P.max(0) + 2.0 - self.lo3
        self.per3 = []
        for cx, ch, w in ((STRIP_PERIOD_MM / 3, 0.8, 0.65), (STRIP_PERIOD_MM / 5, 0.47, 0.35)):
            nx = int(round(STRIP_PERIOD_MM / cx))
            n = (nx, int(np.ceil(ext[1] / ch)) + 4, int(np.ceil(ext[2] / ch)) + 4)
            self.per3.append((np.array([cx, ch, ch]), w, nx, rng.random(n).astype(np.float32)))

    @staticmethod
    def _lattice3(X, cell, grid, wrap_x: int):
        x = X / cell
        i = np.floor(x).astype(int)
        f = _fade(x - i)
        out = 0.0
        for dx in (0, 1):
            ix = (i[:, 0] + dx) % wrap_x
            for dy in (0, 1):
                iy = np.clip(i[:, 1] + dy, 0, grid.shape[1] - 1)
                for dz in (0, 1):
                    iz = np.clip(i[:, 2] + dz, 0, grid.shape[2] - 1)
                    w = ((f[:, 0] if dx else 1 - f[:, 0]) * (f[:, 1] if dy else 1 - f[:, 1])
                         * (f[:, 2] if dz else 1 - f[:, 2]))
                    out = out + w * grid[ix, iy, iz]
        return out

    @staticmethod
    def _lattice(X, cell, grid, wrap_x: int | None = None):
        x = X / cell
        i = np.floor(x).astype(int)
        f = _fade(x - i)
        out = 0.0
        for dx in (0, 1):
            ix = (i[:, 0] + dx) % wrap_x if wrap_x else np.clip(i[:, 0] + dx, 0, grid.shape[0] - 1)
            for dy in (0, 1):
                iy = np.clip(i[:, 1] + dy, 0, grid.shape[1] - 1)
                w = (f[:, 0] if dx else 1 - f[:, 0]) * (f[:, 1] if dy else 1 - f[:, 1])
                out = out + w * grid[ix, iy]
        return out

    def coords(self, P) -> tuple[np.ndarray, np.ndarray]:
        P = np.asarray(P, float)
        c = np.abs(P[:, 0])
        k = np.clip(((c - self.c_lo) / self.BIN_MM).astype(int), 0, self.nb - 1)
        beta = np.clip((P[:, 1] - self.ylo[k]) / np.maximum(self.yhi[k] - self.ylo[k], 0.2), 0.0, 1.0)
        return c, beta

    def colour(self, P, shift_c_mm: float = 0.0) -> np.ndarray:
        c, beta = self.coords(P)
        h = beta * self.height + 1.0
        cc = c - self.c_lo + float(shift_c_mm) + STRIP_PERIOD_MM
        Xn = np.stack([cc, h], 1)
        if self.periodic == "x3d":
            P = np.asarray(P, float)
            X3 = np.stack([np.mod(np.abs(P[:, 0]) - self.lo3[0] + float(shift_c_mm) + STRIP_PERIOD_MM,
                                  STRIP_PERIOD_MM), P[:, 1] - self.lo3[1], P[:, 2] - self.lo3[2]], 1)
            p = sum(w * self._lattice3(X3, cell, g, nx) for cell, w, nx, g in self.per3)
        else:
            Xp = np.stack([np.mod(cc, STRIP_PERIOD_MM), h], 1)
            p = sum(w * self._lattice(Xp, cell, g, wrap_x=nx) for cell, w, nx, g in self.per)
        q = sum(w * self._lattice(Xn, cell, g) for cell, w, g in self.non)
        a = self.alpha
        t = 0.5 + (a * (p - 0.5) + (1 - a) * (q - 0.5)) / np.hypot(a, 1 - a)
        s_ = np.clip((t - 0.40) / 0.20, 0, 1)
        s_ = s_ * s_ * (3 - 2 * s_)
        col = DARK[None] * (1 - s_[:, None]) + AMBER[None] * s_[:, None]
        amp, per_h = self.band
        return col * (1.0 + amp * np.cos(2 * np.pi * h / per_h))[:, None]


def strip_print_images(job, truth, views, *, shift_x_mm: dict | None = None, seed: int = 23,
                       alpha: float = STRIP_ALPHA, periodic: str = "surface", part: str = "Brow_Right") -> dict:
    """Photos of the StripPrint on the TRUE geometry at the TRUE cameras for ``views`` ({view: uint8 image});
    ``shift_x_mm`` = {view: mm} renders that view's print shifted along the strip (a wrong correspondence). Body
    points are q001's own (the print moves with the surface, as ``make_print_colour``). ``periodic``: 'surface' (the
    null's own parameterisation) or 'x3d' (the known false positive, see ``StripPrint``); ``part`` = the brow whose
    front carries the strip print (the rest of the print parts stay StripPrint-coloured by the same field)."""
    real = truth["real"]
    sp = StripPrint(real, part=part, seed=seed, alpha=alpha, periodic=periodic)
    print_pids = set(real.part_ids(PRINT_PARTS))
    is_print = np.array([p_ in print_pids for p_ in range(len(real.mesh.names))])
    out = {}
    for v in views:
        dx = float((shift_x_mm or {}).get(v, 0.0))

        def colour(fid, bary, body, dx=dx):
            c = np.empty((len(fid), 3))
            c[:] = GREY
            pp = is_print[real.mesh.part[fid]]
            c[pp] = sp.colour(body[pp], dx)
            return c
        out[v] = render_photo(real, truth["V"], truth["cameras"][v], real.views[v].shape, colour)
    return out


def shifted_print_images(job, truth, views, shift_x_mm: float, seed: int = 11) -> dict:
    """Photos of A0's own isotropic print (Print3D, same seed) with every print point shifted by ``shift_x_mm`` along
    model x before colouring: a WRONG correspondence of the ordinary havana (fix round 2, false-registration sweep)."""
    real = truth["real"]
    printer = truth["printer"]
    print_pids = set(real.part_ids(PRINT_PARTS))
    is_print = np.array([p_ in print_pids for p_ in range(len(real.mesh.names))])
    sh = np.array([float(shift_x_mm), 0.0, 0.0])

    def colour(fid, bary, body):
        c = np.empty((len(fid), 3))
        c[:] = GREY
        pp = is_print[real.mesh.part[fid]]
        c[pp] = printer.colour(body[pp] + sh[None])
        return c
    return {v: render_photo(real, truth["V"], truth["cameras"][v], real.views[v].shape, colour) for v in views}


def fit_shape(img: np.ndarray, shape, fill=BG) -> np.ndarray:
    """Crop or pad (top-left aligned) a photo to another view's shape (a swapped photo must fit the target array)."""
    H, W = shape
    out = np.empty((H, W, 3), np.uint8)
    out[:] = np.asarray(fill, np.uint8)
    h, w = min(H, img.shape[0]), min(W, img.shape[1])
    out[:h, :w] = img[:h, :w]
    return out
