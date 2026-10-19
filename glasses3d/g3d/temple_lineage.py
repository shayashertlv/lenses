"""Build-time temple names bound to exact raw connectivity, independent of future builders.

Coordinates are deliberately absent: an owner may move vertices while retaining their
identity. Checkpoint hashes authenticate the stored record; its own digest detects
damage, and the topology/spec bindings prevent reuse on a different construction.
"""
from __future__ import annotations

import hashlib
import json
import math
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

import numpy as np
from . import temple_archive

SCHEMA = "g3d.temple_lineage/1"
_PRODUCERS = ContextVar("temple_lineage_producers", default=None)


@contextmanager
def alias_context(root):
    """Authenticate source files before the worker arms its snippet I/O guard."""
    errors = []
    producers = list(temple_archive.archived_builders(root, errors=errors))
    token = _PRODUCERS.set((producers, errors))
    try:
        yield
    finally:
        _PRODUCERS.reset(token)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def topology_digest(vertex_count, polygons):
    return digest({"vertex_count": vertex_count, "polygons": polygons})


def canonical_spec(metadata):
    value = metadata["canonical_spec"]
    value = json.loads(value) if isinstance(value, str) else value
    if not isinstance(value, dict) or metadata.get("side") not in ("+x", "-x"):
        raise ValueError("Invalid stored temple spec or side")
    if metadata.get("spec_sha256") != digest(value):
        raise ValueError("Stored temple spec hash differs from its canonical spec")
    return value


def declared_constraints(metadata):
    """Keep the hashed spec's physical floor even when its names cannot be recovered."""
    spec = canonical_spec(metadata)
    minimum = spec.get("options", {}).get("t_min_mm")
    hinge = spec.get("hinge")
    if (isinstance(minimum, bool) or not isinstance(minimum, (int, float)) or
            not math.isfinite(minimum) or minimum <= 0 or not isinstance(hinge, dict)):
        raise ValueError("Invalid declared temple constraints")
    return {"spec_sha256": metadata["spec_sha256"], "options": {"t_min_mm": minimum}, "hinge": hinge}


def match_sources(tb, side, vertex_count, actual):
    """Accept only the producer's polygons or its exact ordered checked triangles."""
    if vertex_count != len(tb.V):
        raise ValueError("Stored specialist topology differs from the current raw mesh; no guessed aliases")
    _, polygons, triangles = tb.world(side)
    checked = {}
    for triangle, pi in zip(triangles.tolist(), tb.tri_poly):
        checked.setdefault(int(pi), []).append(triangle)
    sources, cursor = [], 0
    for pi, polygon in enumerate(polygons):
        if cursor < len(actual) and actual[cursor] == polygon:
            sources.append(pi)
            cursor += 1
        else:
            expected = checked.get(pi, [])
            if not expected or actual[cursor:cursor + len(expected)] != expected:
                raise ValueError("Stored specialist topology differs from the current raw mesh; no guessed aliases")
            sources.extend([pi] * len(expected))
            cursor += len(expected)
    if cursor != len(actual):
        raise ValueError("Stored specialist topology differs from the current raw mesh; no guessed aliases")
    return sources


def freeze(tb, side, vertex_count, polygons, *, sources=None):
    """Freeze the writer's final polygon lineage, including UV splits and checked ears.

Only the producing writer supplies `sources`; historical readers must establish it
with match_sources against the authenticated builder, never geometric proximity.
    """
    if side not in (-1, 1):
        raise ValueError("Invalid temple side")
    if sources is None:
        sources = match_sources(tb, side, vertex_count, polygons)
    if (len(sources) != len(polygons) or vertex_count < len(tb.V) or
            any(type(i) is not int or not 0 <= i < len(tb.polys) for i in sources)):
        raise ValueError("Invalid producer polygon lineage")
    facets = {}
    for pi, source in enumerate(sources):
        if tb.poly_kind[source] in ("outer", "roll"):
            facets.setdefault(tb.poly_region[source].split("/")[0], []).append(pi)
    points = {}
    for name, node in tb.spec.nodes.items():
        ids = np.flatnonzero(np.linalg.norm(tb.V[:tb.n2, :2] - np.asarray(node[:2]), axis=1) <= 1e-6)
        if len(ids) == 1:
            points[name] = int(ids[0])
    record = {"schema": SCHEMA, "spec_sha256": tb.spec.sha256, "side": "+x" if side > 0 else "-x",
              "vertex_count": vertex_count, "topology_sha256": topology_digest(vertex_count, polygons),
              "facets": facets, "points": points, "hinge": dict(tb.spec.hinge), "options": dict(tb.spec.options)}
    return {**record, "sha256": digest(record)}


def verified(record, metadata, raw):
    """Read frozen authority without importing, parsing with, or invoking today's builder."""
    spec = canonical_spec(metadata)
    if not isinstance(record, dict):
        raise ValueError("Invalid frozen temple lineage")
    body = {key: value for key, value in record.items() if key != "sha256"}
    if record.get("schema") != SCHEMA or record.get("sha256") != digest(body):
        raise ValueError("Frozen temple lineage digest differs")
    count, polygons = len(raw["vertices_local"]), raw["polygons"]
    if (record.get("spec_sha256") != metadata["spec_sha256"] or record.get("side") != metadata["side"] or
            record.get("vertex_count") != count or record.get("topology_sha256") != topology_digest(count, polygons)):
        raise ValueError("Frozen temple lineage differs from the stored spec or current raw mesh; no guessed aliases")
    facets, points = record.get("facets"), record.get("points")
    if not isinstance(facets, dict) or not isinstance(points, dict):
        raise ValueError("Invalid frozen temple names")
    used = set()
    for name, ids in facets.items():
        if (not isinstance(name, str) or not name or not isinstance(ids, list) or
                any(type(i) is not int or not 0 <= i < len(polygons) for i in ids) or
                len(set(ids)) != len(ids) or used.intersection(ids)):
            raise ValueError("Invalid frozen facet indices")
        used.update(ids)
    if any(not isinstance(name, str) or not name or type(i) is not int or not 0 <= i < count
           for name, i in points.items()):
        raise ValueError("Invalid frozen corner indices")
    if not isinstance(record.get("hinge"), dict) or not isinstance(record.get("options"), dict):
        raise ValueError("Invalid frozen temple frame")
    if record["options"] != spec.get("options"):
        raise ValueError("Frozen temple options differ from its declared spec")
    declared_hinge = dict(spec.get("hinge", {}))
    # The producer resolves a missing axis for the closed frame; the resolved
    # frame is frozen, while every explicitly declared field remains binding.
    if declared_hinge.get("axis_xz_mm") is None:
        declared_hinge["axis_xz_mm"] = record["hinge"].get("axis_xz_mm")
    if record["hinge"] != declared_hinge:
        raise ValueError("Frozen temple hinge differs from its declared spec")
    return {"status": "verified_topology", "basis": "Frozen producer aliases; exact stored spec, side and raw polygon connectivity; current positions",
            "stored_spec_sha256": record["spec_sha256"], "facets": facets, "points": points,
            "historical_coordinates_used": False, "hinge": record["hinge"], "options": record["options"],
            "lineage_sha256": record["sha256"]}


@lru_cache(maxsize=16)
def _rebuild(builder, canonical):
    return builder(json.loads(canonical))


def aliases(metadata, raw, root=None):
    """Use frozen lineage first; legacy meshes require a verified run-source archive.

An invalid frozen record is a refusal, not permission to replace its authority.
Legacy recovery accepts only unanimous matching archived builds and uses none of
their coordinates for display or editing.
    """
    spec = canonical_spec(metadata)
    if "lineage" in metadata:
        return verified(metadata["lineage"], metadata, raw)
    context = _PRODUCERS.get() if root is None else None
    if root is None and context is None:
        raise ValueError("No authenticated producer lineage or run source archive; no guessed aliases")
    errors, matches = list(context[1]) if context is not None else [], []
    producers = context[0] if context is not None else temple_archive.archived_builders(root, errors=errors)
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"), allow_nan=False)
    for builder, provenance in producers:
        try:
            tb = _rebuild(builder, canonical)
            if tb.spec.sha256 != metadata["spec_sha256"]:
                raise ValueError("Archived builder canonical spec differs")
            record = freeze(tb, 1 if metadata["side"] == "+x" else -1,
                            len(raw["vertices_local"]), raw["polygons"])
            result = verified(record, metadata, raw)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, IndexError) as error:
            errors.append(str(error))
            continue
        matches.append((result, provenance))
    if not matches:
        detail = "; ".join(dict.fromkeys(str(e) for e in errors))[:800]
        raise ValueError("No authenticated producer matches the current raw mesh; no guessed aliases" +
                         (": " + detail if detail else ""))
    result = matches[0][0]
    if any(row[0] != result for row in matches[1:]):
        raise ValueError("Authenticated producer aliases disagree; no guessed aliases")
    return {**result, "basis": "Authenticated run-source builder; exact stored spec, vertex count and ordered polygons/checked triangles; current positions",
            "lineage_provenance": [row[1] for row in matches]}
