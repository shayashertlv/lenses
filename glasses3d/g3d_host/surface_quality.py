"""Surface evidence from an immutable GLB; no geometry or acceptance rules change."""
from . import glb
from ..g3d.surface_quality import analyse
import numpy as np


def report(path):
    model = glb.load(path)
    rows = []
    for p in model.primitives:
        if p.error or p.compressed or not len(p.triangles):
            continue
        normals = None
        if p.normals is not None:
            normals = p.normals @ np.linalg.inv(p.world[:3, :3])
            normals = normals[p.triangles]
        role = (model.nodes[p.node_index].get("extras") or {}).get("partRole", "")
        rows.append({"object": p.node_name, "role": role, "primitive": p.primitive_index,
                     **analyse(p.positions*1000, p.triangles, normals)})
    return {"schema": "g3d.surface_quality/1", "informational_only": True, "objects": rows,
            "facet_names": "Named facet measurements, when present, come from the native export receipt; GLB strips builder provenance.",
            "light": "Review room plus side_studio at 0.8; lens reflection multiplier remains fixed."}
