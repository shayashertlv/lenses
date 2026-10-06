"""The receipt of g3d.faceted_temple (WP6_DESIGN.md §3 "Receipt"). numpy only.

    facet table   label, area, normal, lean against the inner face, defining points, conditioning, planarity
    crease table  regions, dihedral, ridge / valley, length
    thickness     min / max and where
    stations      every 5 mm: width, height, x_in, x_out, outer tilt, bulge (vbulge per station)
    geometry      the §5 numbers on the tool's own mesh (single-plane share with the spec's facet planes, vbulge,
                  pbulge, inner flatness, SLAB / INNER_NOT_FLAT / BULGE_SANITY)
    checks        closed, Euler, volume, planarity, self-intersection; warnings; budget
"""
from __future__ import annotations

import time

import numpy as np

from . import CORE_VERSION, SCHEMA
from . import measure as MS
from .spec import plain

RECEIPT_STATION_STEP_MM = 5.0


def facet_planes_chart(tb) -> list:
    """[(label, unit normal, offset)] of every facet's outer plane in the chart (n . x = c)."""
    out = []
    for label in sorted(tb.spec.facets):
        ids = [i for i, (k, rg) in enumerate(zip(tb.poly_kind, tb.poly_region)) if k == "outer" and rg == label]
        if not ids:
            continue
        n = tb.normals[ids[0]]
        c = float(n @ tb.V[tb.polys[ids[0]][0]])
        out.append((label, n, c))
    return out


def geometry_numbers(tb, photo_creases: int | None = None) -> dict:
    m = MS.measure_temple(tb.V, tb.tris, zone_end=tb.spec.zone_end, d_last=tb.spec.d1,
                          facet_planes=facet_planes_chart(tb), photo_creases=photo_creases,
                          station_step=RECEIPT_STATION_STEP_MM, with_facets=False)
    m.pop("facets", None)
    return m


def receipt(tb, *, core_ms: float | None = None, photo_creases: int | None = None) -> dict:
    t0 = time.perf_counter()
    geo = geometry_numbers(tb, photo_creases)
    out = {"tool": "g3d.faceted_temple", "schema": SCHEMA, "core_version": CORE_VERSION,
           "spec_sha256": tb.spec.sha256, "canonical_spec": tb.spec.canonical,
           "facets": tb.tables["facets"], "creases": tb.tables["creases"], "thickness": tb.tables["thickness"],
           "nodes_solved": tb.tables["nodes_solved"], "arrangement": tb.tables["arrangement"],
           "stations": geo.pop("stations"), "geometry": geo, "checks": tb.checks,
           "warnings": tb.warnings, "sharp_edges": len(tb.sharp),
           "mesh": {"vertices": int(len(tb.V)), "polygons": len(tb.polys), "triangles": int(len(tb.tris))},
           "budget": {"core_ms": core_ms, "measure_ms": 1000.0 * (time.perf_counter() - t0)}}
    return plain(out)
