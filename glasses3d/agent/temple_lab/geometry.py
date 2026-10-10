"""Measured blank construction template and metric chart; no product facet constants."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple.spec import canonical


def section(vertices, triangles, z):
    tri = np.asarray(vertices, float)[np.asarray(triangles, int)]
    points = []
    for a, b in ((0, 1), (1, 2), (2, 0)):
        p, q = tri[:, a], tri[:, b]
        dz = q[:, 2] - p[:, 2]
        hit = (np.minimum(p[:, 2], q[:, 2]) <= z + 1e-8) & \
              (np.maximum(p[:, 2], q[:, 2]) >= z - 1e-8) & (abs(dz) > 1e-10)
        t = np.clip((z - p[hit, 2]) / dz[hit], 0, 1)
        points.extend(p[hit] + t[:, None] * (q[hit] - p[hit]))
    if not points:
        raise ValueError(f"no mesh cross-section at z={z}")
    return np.asarray(points)


def blank_template(vertices, triangles):
    """Cross-section envelopes, 2 mm sampling, linear front 45% base. Never applies it."""
    v = np.asarray(vertices, float)
    z0, zend = float(v[:, 2].max()), float(v[:, 2].min())
    length = z0 - zend
    if not 60 <= length <= 250 or v[:, 0].min() <= 0:
        raise ValueError("template requires a +X temple of length 60..250 mm")
    def valid_at(d):
        p = section(v, triangles, z0-d)
        return np.ptp(p[:, 1]) >= .300001 and np.ptp(p[:, 0]) >= 1.000001
    # A round tip can terminate at a single y coordinate. The builder requires a finite cap;
    # locate its first valid cross-section rather than silently expanding the original outline.
    limits = [0., length]
    for end, inward in ((0, 1.), (1, -1.)):
        if valid_at(limits[end]):
            continue
        bad, good = limits[end], limits[end]+inward*min(2., length/10)
        if not valid_at(good):
            raise ValueError("end needs more than 2mm trimming; template refused")
        for _ in range(40):
            mid = (bad+good)/2
            if valid_at(mid): good = mid
            else: bad = mid
        limits[end] = good
    ze = round(length * .45, 4)
    ds = np.unique(np.r_[np.linspace(*limits, int(np.ceil(length / 2)) + 1),
                         np.linspace(limits[1]-2., limits[1], 9), ze])
    sec = [section(v, triangles, z0 - d) for d in ds]
    inner = np.array([p[:, 0].min() for p in sec])
    thick = np.array([np.ptp(p[:, 0]) for p in sec])
    top = np.array([p[:, 1].max() for p in sec])
    bottom = np.array([p[:, 1].min() for p in sec])
    if (top - bottom < .3).any() or (thick < 1).any():
        raise ValueError("cross-section too thin for this template; no replacement was made")
    zone = ds <= ze
    base_in = np.polyval(np.polyfit(ds[zone], inner[zone], 1), ds)
    base_t = np.polyval(np.polyfit(ds[zone], thick[zone], 1), ds)
    diagnostics = {"sampling_mm_max": float(np.diff(ds).max()), "length_mm": length,
                   "cap_trim_mm": [limits[0], length-limits[1]],
                   "front_zone_mm": ze, "inner_linearization_max_mm": float(abs(base_in[zone]-inner[zone]).max()),
                   "thickness_linearization_max_mm": float(abs(base_t[zone]-thick[zone]).max()),
                   "warning": "Blank measured template, not the original mesh. Sampling/linearization and removal "
                              "of bevels can change the surface. Original scene remains the start checkpoint."}
    def profile(values, linear):
        return [[float(ds[0]), float(linear[0])], [ze, float(linear[zone][-1])]] + \
               [[float(d), float(y)] for d, y in zip(ds[~zone], values[~zone])]
    spec = canonical({"schema": "g3d.temple/1", "hinge": {"z_mm": z0,
                      "axis_xz_mm": [float(inner[0]), z0], "opening_deg": 0},
                      "outline": {"top": np.c_[ds, top].tolist(), "bottom": np.c_[ds, bottom].tolist(),
                                  "tip_round_mm": 0}, "inner": profile(inner, base_in),
                      "thickness": profile(thick, base_t), "zone_end_d": ze,
                      "nodes": {}, "facets": {}, "chamfers": [], "roll_mm": 0, "options": {}})
    build_temple(spec)
    return spec, diagnostics


def chart(spec, path):
    """Unambiguous metric side chart with its affine pixel transform, no hidden projection."""
    top, bot = np.asarray(spec["outline"]["top"]), np.asarray(spec["outline"]["bottom"])
    x0, x1 = top[0, 0], top[-1, 0]
    y0, y1 = bot[:, 1].min()-3, top[:, 1].max()+3
    scale = min(1100/(x1-x0), 430/(y1-y0))
    def xy(p):
        return (70+(p[0]-x0)*scale, 65+(y1-p[1])*scale)
    im = Image.new("RGB", (1240, 590), "#f6f5f0")
    d = ImageDraw.Draw(im)
    d.text((20, 15), "TEMPLE CHART: d back from hinge (mm); y up (mm); r outward from base (mm)", fill="black")
    for x in np.arange(np.ceil(x0/10)*10, x1+1, 10):
        a, b = xy((x, y0)), xy((x, y1))
        d.line([a, b], fill="#d1d4d6"); d.text((a[0], a[1]+5), f"{x:g}", fill="black")
    for y in np.arange(np.ceil(y0/10)*10, y1+1, 10):
        a, b = xy((x0, y)), xy((x1, y))
        d.line([a, b], fill="#d1d4d6"); d.text((25, a[1]), f"{y:g}", fill="black")
    d.polygon([xy(p) for p in np.vstack([top, bot[::-1]])], outline="black", width=3)
    d.line([xy((spec["zone_end_d"], y0)), xy((spec["zone_end_d"], y1))], fill="gray", width=2)
    colors = ["#a61b45", "#007780", "#955600", "#553ab6"]
    for i, (name, f) in enumerate(spec["facets"].items()):
        p = [spec["nodes"][n] for n in f["poly"]]
        d.line([xy(q) for q in p+[p[0]]], fill=colors[i % 4], width=3)
        c = np.mean(np.asarray(p)[:, :2], axis=0); d.text(xy(c), name, fill=colors[i % 4])
    for name, p in spec["nodes"].items():
        x, y = xy(p); d.ellipse((x-4, y-4, x+4, y+4), fill="red")
        d.text((x+6, y+3), f"{name} r={p[2]}", fill="black")
    d.text((20, 560), "Original photographs remain the evidence. This is a construction chart, not recovered depth.", fill="black")
    im.save(path)
    return {"scale_px_per_mm": scale, "pixel_origin": [70, 65], "d_at_origin": float(x0),
            "y_at_origin": float(y1), "formula": "d=x0+(px-70)/scale; y=y1-(py-65)/scale"}
