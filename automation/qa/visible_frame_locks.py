"""Bounded QA: lock only source triangles on raster-visible frame contours."""
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np
from scipy import ndimage

from reconstruction.camera import Camera, project
from reconstruction.mesh import TriangleMesh
from reconstruction.raster import rasterize
from reconstruction.surface_transfer import _chunks, _primitives

source, destination = map(Path, sys.argv[1:3])
started = time.monotonic()
raw = source.read_bytes()
doc, binary = _chunks(raw)
parts = [part for part in _primitives(doc, binary)
         if not doc['materials'][part['record']['material']].get('extensions', {}).get('LENSES_lens_appearance')]
vertex_offsets = np.cumsum([0] + [len(part['world']) for part in parts])
face_offsets = np.cumsum([0] + [len(part['faces']) for part in parts])
mesh = TriangleMesh(np.concatenate([part['world'] for part in parts]),
                    np.concatenate([part['faces'] + vertex_offsets[i] for i, part in enumerate(parts)]), []).normalized()
selected = np.zeros(len(mesh.faces), bool)
views = []
for yaw in (0, -40, 40, -80, 80, 180):
    for pitch in (0, 15):
        xy = project(mesh.vertices, Camera(yaw, pitch, 0, 0, 1, 0, 0))
        lo, hi = xy.min(axis=0), xy.max(axis=0)
        scale = 480 / max(hi-lo)
        center = 255.5 - (hi+lo)/2*scale
        camera = Camera(yaw, pitch, 0, 0, scale, *map(float, center))
        result = rasterize(mesh, camera, (512, 512))
        boundary = result.mask & ~ndimage.binary_erosion(result.mask)
        ids = np.unique(result.face_index[boundary])
        selected[ids] = True
        views.append({'yaw': yaw, 'pitch': pitch, 'boundary_pixels': int(boundary.sum()),
                      'visible_boundary_faces': len(ids), 'camera': camera.to_dict()})
        print(json.dumps(views[-1]), flush=True)
rows=[]
for i, part in enumerate(parts):
    ids = np.unique(part['faces'][selected[face_offsets[i]:face_offsets[i+1]]])
    rows.append({'mesh_index':part['mesh'], 'primitive_index':part['primitive'],
                 'source_vertex_ids':ids.tolist(), 'locked_source_vertices':len(ids)})
report={'method':'visible_frame_contour_vertex_locks_qa_v1',
        'source_sha256':hashlib.sha256(raw).hexdigest(), 'raster_size':512,
        'optical_geometry_ignored_as_occluder':True,'one_ring_expansion':False,
        'visible_boundary_faces':int(selected.sum()), 'views':views,'primitives':rows,
        'seconds':time.monotonic()-started}
destination.write_text(json.dumps(report,indent=2))
print(json.dumps({k:v for k,v in report.items() if k not in ('views','primitives')}),flush=True)
