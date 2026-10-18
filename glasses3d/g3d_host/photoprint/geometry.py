"""Read-only winding diagnostics for surfaces whose normals control photo visibility."""
from __future__ import annotations

import numpy as np

from ...g3d.seat import geom


def winding_report(job, part_ids) -> dict:
    """Report inward closed components and inconsistent edges without repairing geometry.

    Exact position welding only removes exported UV/normal splits. Open or nonmanifold components have no
    reliable volume orientation and are explicitly marked undetermined; they are never called inward.
    """
    rows, warnings, objects = [], [], {}
    node_ids = job.mesh.node_indices
    for pid in part_ids:
        identity = node_ids[pid] if len(node_ids) == len(job.mesh.names) else job.mesh.node_names[pid]
        objects.setdefault(identity, []).append(pid)
    for pids in objects.values():
        faces = job.F[np.isin(job.part, pids)]
        if not len(faces):
            continue
        V, inverse = geom.weld_exact(job.V[np.unique(faces)])
        _, local = np.unique(faces, return_inverse=True)
        F = inverse[local.reshape(-1, 3)]
        _, labels = geom.components(F, len(V))
        components = []
        for label in np.unique(labels):
            T = F[labels == label]
            topo = geom.topology(T, len(V))
            oriented = topo['closed'] and not topo['misoriented_edges']
            # Shift the integration origin close to the component to avoid cancellation far from the origin.
            Q = V - V[np.unique(T)].mean(0)
            volume = geom.signed_volume(Q, T) if oriented else None
            state = ('inward' if volume < 0 else 'outward' if volume > 0 else 'undetermined') if oriented else 'undetermined'
            components.append({'faces': len(T), **topo, 'signed_volume_mm3': volume, 'orientation': state})
        inward = sum(c['faces'] for c in components if c['orientation'] == 'inward')
        misoriented = sum(c['misoriented_edges'] for c in components)
        row = {'part': job.mesh.names[pids[0]], 'primitives': list(map(int, pids)), 'faces': len(faces),
               'inward_faces': inward, 'inward_face_share': inward / len(faces),
               'misoriented_edges': misoriented, 'components': components}
        rows.append(row)
        if inward or misoriented:
            warnings.append(f"{row['part']} (primitives {pids}): print winding has {inward}/{len(faces)} faces in "
                            f"inward closed components and {misoriented} inconsistent edges; photo visibility and "
                            "facing classes use these normals, so fill can hide missing photo projection. Repair the mesh.")
    return {'parts': rows, 'warnings': warnings, 'geometry_changed': False}
