"""Host seating check on an exported GLB (WP4_DESIGN.md §8 'Geometric (binding)').

    python -m glasses3d.g3d_host.seat_check <model.glb> [--frame-type metal] [--range -56.2,-11.6] [--out r.json]

GLB -> nodes -> Open3DBackend -> the same numpy core as the Blender tool and the Blender check (glasses3d/g3d/seat/,
the check itself in g3d/seat/check.py), so the tool and the two checks cannot disagree. Input: positions in metres x 1000 (world mm, asset frame: +Y up, +Z front, x = 0 at the
bridge underside); exact weld per node; lenses from lens_id.classify, then body recognition (closed, >= 0.3 mm
effective thickness); frame parts = closed components of `partRole` frame nodes (all non-lens nodes when the file
carries no roles) whose box lies within 6 mm of the lens rim, minus markings; frame type from the lens node's extras
`g3d_seat`, else the argument, else inferred and warned. Hidden x-ranges (browline) from the argument, else
`g3d_hidden_x_ranges_mm` on the lens node.

Components (§3 'Lens body', §8):
- lens nodes: a closed component >= 0.3 mm thick is a body; any other component is a rider when it lies on a body
  (`rider_of`: Prada's etch), else the file FAILS (E_OPEN / E_THIN: a lens that is not closed is never silently
  dropped while the other lens passes); a lens node that yields nothing usable fails (E_EMPTY);
- frame nodes: every closed component is a frame solid (a 0.6 mm nylor cord has 2V/A 0.29 mm, a 0.55 mm wire 0.27:
  the 0.3 mm body threshold is for lenses), except a MARKING: closed, thinner than MARKING_MAX_MM and lying on a lens
  (Oakley's OAKLEY / PRIZM decals, 2V/A 0.002-0.003); open frame components are riders when they lie on a lens
  (INVU's print); one within 6 mm of a rim is listed as an open part AND fails the file (E_OPEN_PART): it cannot be
  measured as a solid, and silently dropping it would turn the stations it holds EXPOSED, which semi_rimless grades
  as visible by design (a swept channel with uncapped ends would pass a lens it fails when closed);
- skipped primitives (compressed, undecodable, not triangles): a lens primitive fails the file (E_UNREADABLE, or
  E_EMPTY when its node has no other lens geometry); an undecoded frame surface within 6 mm of a rim, or of unknown
  position, fails it too (E_UNREADABLE_PART). The lens such a frame problem names is not graded.

Frame type: declared (`g3d_seat`, else the argument) or inferred: > 20% EXPOSED -> semi_rimless; ENCLOSED majority ->
acetate; else metal; NO ENCLOSED/LIPPED station -> unknown (not graded, FAIL). An inferred type grades the lens
provisionally (`graded_pass`) but never passes it: `pass` and the file's `ok` need a declared type (until the
exporter carries `g3d_seat`, pass frame_type=..., or blender.export_and_validate(..., seating={...})).

`check_glb` never raises: a broken input comes back as {"ok": False, "error": ...}.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

import numpy as np

from glasses3d.g3d.seat import check as CK
from glasses3d.g3d.seat import rules, stations
from glasses3d.g3d.seat.intervals import FAR_MM, NumpyBackend
from glasses3d.g3d_host import lens_id
from glasses3d.g3d_host.glb import Glb, load


class Open3DBackend:
    """open3d RaycastingScene (Embree) line intervals. Coordinates are centred on the parts' box before the float32
    conversion and lines start just outside that box (equivalent to the 400 mm cast, with less float32 error)."""

    def __init__(self, parts):
        import open3d as o3d
        self._o3d = o3d
        self.n_parts = len(parts)
        self.scene = o3d.t.geometry.RaycastingScene()
        self.gid_part: dict[int, int] = {}
        boxes = [np.asarray(V, np.float64) for V, T in parts if len(T)]
        if boxes:
            allv = np.concatenate(boxes)
            lo, hi = allv.min(0), allv.max(0)
        else:
            lo = hi = np.zeros(3)
        self.centre = 0.5 * (lo + hi)
        self.far = float(min(FAR_MM, np.linalg.norm(hi - lo) + 10.0))
        for i, (V, T) in enumerate(parts):
            T = np.asarray(T, np.int64).reshape(-1, 3)
            if len(T) == 0:
                continue
            Vc = (np.asarray(V, np.float64) - self.centre).astype(np.float32)
            gid = self.scene.add_triangles(o3d.core.Tensor(Vc), o3d.core.Tensor(T.astype(np.uint32)))
            self.gid_part[int(gid)] = i

    def hits(self, origins, dirs):
        O = np.asarray(origins, np.float64).reshape(-1, 3) - self.centre
        D = np.asarray(dirs, np.float64).reshape(-1, 3)
        if len(O) == 0 or not self.gid_part:
            z = np.zeros(0, np.int64)
            return z, z, np.zeros(0)
        # a line whose origin lies far outside the box: start `far` before its closest approach to the centre
        back = self.far + np.maximum(0.0, (O * D).sum(1))
        rays = np.c_[O - back[:, None] * D, D].astype(np.float32)
        r = self.scene.list_intersections(self._o3d.core.Tensor(rays))
        li = r["ray_ids"].numpy().astype(np.int64)
        t = r["t_hit"].numpy().astype(np.float64) - back[li]
        g = r["geometry_ids"].numpy().astype(np.int64)
        lut = np.full(max(self.gid_part) + 1, -1, np.int64)
        for k, v in self.gid_part.items():
            lut[k] = v
        return li, lut[g], t


def make_backend(kind: str, parts):
    if kind == "numpy":
        return NumpyBackend(parts)
    if kind == "open3d":
        return Open3DBackend(parts)
    raise ValueError(f"backend {kind!r}: numpy or open3d")


# ------------------------------------------------------------------------------------------------- GLB reading
def _role(node: dict, mesh: dict, mat: dict | None) -> str | None:
    for ex in ((mat or {}).get("extras"), mesh.get("extras"), node.get("extras")):
        if isinstance(ex, dict):
            v = ex.get("partRole")
            if isinstance(v, str) and v.strip():
                return v.strip().lower()
    return None


def _primitive_refs(g: Glb) -> list[lens_id.PrimitiveRef]:
    refs = []
    for i, p in enumerate(g.primitives):
        mat = g.material(p.material_index)
        node = g.nodes[p.node_index] if isinstance(g.nodes[p.node_index], dict) else {}
        mesh = g.meshes[p.mesh_index] if isinstance(g.meshes[p.mesh_index], dict) else {}
        refs.append(lens_id.PrimitiveRef(key=str(i), node_name=p.node_name, node_extras=node.get("extras"),
                                         mesh_extras=mesh.get("extras"), material_name=mat.get("name") if mat else None,
                                         material_extras=mat.get("extras") if mat else None, material=mat))
    return refs


def _translucent(mat: dict | None) -> bool:
    """§4: transmission > 0.05, alpha < 0.98 or a volume."""
    if not isinstance(mat, dict):
        return False
    ext = mat.get("extensions") or {}
    try:
        if float((ext.get("KHR_materials_transmission") or {}).get("transmissionFactor", 0.0)) > 0.05:
            return True
        alpha = float(((mat.get("pbrMetallicRoughness") or {}).get("baseColorFactor") or [1, 1, 1, 1])[3])
    except (TypeError, ValueError, IndexError):
        return False
    return alpha < 0.98 or "KHR_materials_volume" in ext


def world_normals(normals: np.ndarray, world: np.ndarray) -> np.ndarray:
    """Unit world-space normals: the inverse transpose of the node matrix (rows: n @ M^-1, as positions go p @ M^T)."""
    n = np.asarray(normals, np.float64) @ np.linalg.inv(np.asarray(world, np.float64)[:3, :3])
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-30)


TRIANGLE_MODES = CK.TRIANGLE_MODES


def read_nodes(g: Glb) -> tuple[list[dict], list[str], list[dict]]:
    """Per mesh node: name, world mm positions, triangles, world normals (or None), roles, lens flag, materials.
    Also every SKIPPED primitive (compressed, undecodable, or no triangles) with its lens decision, role, mode and
    world mm box (None when unknown), so collect() can fail a file whose lens or nearby frame part was not read."""
    decisions, warnings = lens_id.classify(_primitive_refs(g))
    by_node: dict[int, dict] = {}
    skipped: list[dict] = []
    for i, p in enumerate(g.primitives):
        node = g.nodes[p.node_index] if isinstance(g.nodes[p.node_index], dict) else {}
        mesh = g.meshes[p.mesh_index] if isinstance(g.meshes[p.mesh_index], dict) else {}
        mat = g.material(p.material_index)
        if p.error or p.compressed or len(p.triangles) == 0:
            why = p.error or p.compressed or f"no triangles (mode {p.mode})"
            warnings.append(f"{p.node_name}: primitive {p.primitive_index} skipped ({why})")
            if p.bounds is not None:
                box = np.asarray(p.bounds, np.float64) * 1000.0
            elif len(p.positions):
                box = np.stack([p.positions.min(0), p.positions.max(0)]) * 1000.0
            else:
                box = None
            skipped.append({"node_index": p.node_index, "node": p.node_name, "primitive": p.primitive_index,
                            "why": why, "lens": bool(decisions[str(i)].is_lens), "role": _role(node, mesh, mat),
                            "mode": p.mode, "unread_surface": bool(p.error or p.compressed), "box": box})
            continue
        d = by_node.setdefault(p.node_index, {"name": p.node_name, "V": [], "T": [], "N": [], "roles": set(),
                                              "lens": False, "translucent": False, "extras": node.get("extras") or {},
                                              "n": 0})
        nrm = None
        if p.normals is not None and len(p.normals) == len(p.positions):
            nrm = world_normals(p.normals, p.world)
        d["V"].append(p.positions * 1000.0)
        d["T"].append(p.triangles + d["n"])
        d["N"].append(nrm if nrm is not None else np.full_like(p.positions, np.nan))
        d["n"] += len(p.positions)
        role = _role(node, mesh, mat)
        if role:
            d["roles"].add(role)
        d["lens"] |= decisions[str(i)].is_lens
        d["translucent"] |= _translucent(mat)
    out = []
    for ni in sorted(by_node):
        d = by_node[ni]
        out.append({"node": ni, "name": d["name"], "V": np.concatenate(d["V"]), "T": np.concatenate(d["T"]),
                    "N": np.concatenate(d["N"]), "roles": sorted(d["roles"]), "lens": d["lens"],
                    "translucent": d["translucent"], "extras": d["extras"]})
    return out, warnings, skipped


# ------------------------------------------------------------------------------------- shared core (g3d/seat/check)
RIM_BOX_PAD_MM = CK.RIM_BOX_PAD_MM
RIDER_SURFACE_MM, RIDER_SHARE, RIDER_SAMPLES = CK.RIDER_SURFACE_MM, CK.RIDER_SHARE, CK.RIDER_SAMPLES
EXPOSED_INFER_SHARE = CK.EXPOSED_INFER_SHARE
MARKING_MAX_MM = CK.MARKING_MAX_MM
rim_split = CK.rim_split
infer_frame_type = CK.infer_frame_type
rim_box = CK.rim_box
parts_near = CK.parts_near


def factory(kind: str):
    return lambda parts: make_backend(kind, parts)


def rider_of(comp: stations.Component, bodies: list, backend_kind: str, cache: dict | None = None) -> int:
    return CK.rider_of(comp, bodies, factory(backend_kind), cache)


def collect(g: Glb, backend_kind: str = "open3d") -> dict:
    """Read a GLB into the check's inputs (g3d/seat/check.collect)."""
    nodes, warnings, skipped = read_nodes(g)
    return CK.collect(nodes, skipped, warnings, factory(backend_kind))


def check_glb(glb, frame_type: str | None = None, lower_edge: str | None = None,
              hidden_x_range_mm=None, backend: str = "open3d", stations_table: bool = False) -> dict:
    try:
        return _check(glb, frame_type, lower_edge, hidden_x_range_mm, backend, stations_table)
    except Exception as exc:                                  # a check never raises (§8 runs on every export)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc(limit=8)}


def _check(glb, frame_type, lower_edge, hidden_x_range_mm, backend_kind, stations_table) -> dict:
    g = glb if isinstance(glb, Glb) else load(glb)
    nodes, warnings, skipped = read_nodes(g)
    rep = CK.check_nodes(nodes, skipped, warnings, factory(backend_kind), frame_type, lower_edge,
                         hidden_x_range_mm, stations_table, backend_kind)
    return {"glb": str(getattr(g, "path", "")), **rep}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="WP4 seating check on a GLB")
    ap.add_argument("glb")
    ap.add_argument("--frame-type", choices=rules.FRAME_TYPE_NAMES)
    ap.add_argument("--lower-edge", choices=("plain", "nylor"))
    ap.add_argument("--range", action="append", default=None,
                    help="hidden x-range in asset mm, repeatable; write --range=-56.2,-11.6 when x0 is negative")
    ap.add_argument("--backend", default="open3d", choices=("open3d", "numpy"))
    ap.add_argument("--stations", action="store_true", help="include the per-station table")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    ranges = [tuple(float(v) for v in s.split(",")) for s in a.range] if a.range else None
    rep = check_glb(a.glb, a.frame_type, a.lower_edge, ranges, a.backend, a.stations)
    text = json.dumps(rep, indent=1)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
