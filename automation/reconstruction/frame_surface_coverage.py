"""Whole-frame surface coverage from angularly diverse photo observations.

The denominator contains every non-optical triangle, including untextured and
unmapped surfaces. Atlas texel density cannot increase a surface's weight.
"""
from dataclasses import asdict, dataclass
from itertools import combinations
import math

import numpy as np


@dataclass(frozen=True)
class FrameSurfaceSamplingPolicy:
    maximum_points: int = 32768
    seed: int = 718293
    minimum_separation_degrees: float = 8.
    sampling_tail_probability: float = .01

    def __post_init__(self):
        if type(self.maximum_points) is not int or not 256 <= self.maximum_points <= 262144:
            raise ValueError('Frame surface point budget must be 256 through 262144')
        if type(self.seed) is not int or not 0 <= self.seed < 2**32:
            raise ValueError('Frame surface seed must be an unsigned 32-bit integer')
        if not np.isfinite(self.minimum_separation_degrees) or not 0 < self.minimum_separation_degrees < 90:
            raise ValueError('Frame surface separation must be between zero and 90 degrees')
        if not np.isfinite(self.sampling_tail_probability) or not 0 < self.sampling_tail_probability <= .05:
            raise ValueError('Frame surface sampling tail must be positive and at most .05')


def _frame_area(mesh):
    triangles = mesh.vertices[mesh.faces]
    area = np.linalg.norm(np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0]),axis=1)/2
    if not np.isfinite(area).all():
        raise ValueError('Frame coverage requires finite source geometry')
    frame = np.ones(len(mesh.faces),bool)
    for part in mesh.parts:
        if part.get('has_lens_appearance_extension',False) or part.get('transmission',0)>0:
            frame[part['face_start']:part['face_start']+part['face_count']]=False
    return triangles,area,frame


def sample_frame_surface(mesh, *, policy=FrameSurfaceSamplingPolicy()):
    """Fixed-budget area strata, independent of texture resolution/triangle count.

    Triangle centroids are spatially ordered, then the complete frame area is
    split into equal-weight strata. Independent, reproducibly seeded draws pick
    a face in each stratum and a uniform barycentric point on that face. No face
    is declared observed from a vertex/texel count. Missing materials stay in
    the sampling domain and subsequently receive invalid observation flags.
    """
    triangles,area,frame = _frame_area(mesh)
    ids = np.flatnonzero(frame & (area > 0)); total = float(area[ids].sum())
    if not len(ids):
        return {'source_faces':np.empty(0,np.int64),'barycentric':np.empty((0,3)),
                'world':np.empty((0,3)),'source_triangles':np.empty((0,3,3)),
                'area_weights':np.empty(0),'frame_area':total,'policy':asdict(policy)}
    centroids = triangles[ids].mean(axis=1)
    order = np.lexsort((centroids[:,2],centroids[:,1],centroids[:,0]))
    ids = ids[order]; cumulative = np.cumsum(area[ids]); cumulative[-1]=total
    rng = np.random.default_rng(policy.seed); count=policy.maximum_points
    mass = (np.arange(count)+rng.random(count))*total/count
    sampled = ids[np.searchsorted(cumulative,mass,side='right')]
    uv = rng.random((count,2)); root=np.sqrt(uv[:,0])
    bary = np.column_stack((1-root,root*(1-uv[:,1]),root*uv[:,1]))
    xyz = triangles[sampled]
    return {'source_faces':sampled,'barycentric':bary,'world':np.einsum('ni,nij->nj',bary,xyz),
            'source_triangles':xyz,'area_weights':np.full(count,total/count),
            'frame_area':total,'policy':asdict(policy)}


def assess_sampled_frame_surface_coverage(mesh, sampling, valid, directions):
    """Estimate guarded three-view area with a disclosed sampling allowance.

    The lower estimate deducts a one-sided Hoeffding sampling allowance for the
    independent equal-weight strata. This is conditional on the photo/camera
    instrument; it does not bound segmentation or registration systematic error.
    The predeclared seed makes the sampling design reproducible, not exact.
    """
    policy=FrameSurfaceSamplingPolicy(**sampling['policy'])
    triangles,area,frame=_frame_area(mesh); total=float(area[frame].sum())
    ids=np.asarray(sampling['source_faces']);bary=np.asarray(sampling['barycentric'])
    weights=np.asarray(sampling['area_weights']);valid=np.asarray(valid);directions=np.asarray(directions,float)
    count=len(ids)
    if (ids.ndim!=1 or ids.dtype.kind not in 'iu' or np.any(ids<0) or np.any(ids>=len(frame))
            or not frame[ids].all() or bary.shape!=(count,3) or not np.isfinite(bary).all()
            or np.any(bary<0) or not np.allclose(bary.sum(axis=1),1.)
            or weights.shape!=(count,) or not np.isfinite(weights).all()
            or (count and (count!=policy.maximum_points or not np.allclose(weights,total/count,rtol=1e-12,atol=0)))
            or valid.ndim!=2 or valid.dtype!=np.bool_ or valid.shape[1]!=count
            or directions.shape!=(*valid.shape,3) or not np.isfinite(directions).all()
            or (total>0 and count==0)):
        raise ValueError('Invalid complete-area source-bound surface sampling')
    expected=sample_frame_surface(mesh,policy=policy)
    if not np.array_equal(ids,expected['source_faces']) or not np.array_equal(bary,expected['barycentric']):
        raise ValueError('Frame coverage samples differ from their fixed complete-area recipe')
    supported=_diverse(valid,directions,policy.minimum_separation_degrees)
    fraction=float(supported.mean()) if count else None
    allowance=math.sqrt(math.log(1/policy.sampling_tail_probability)/(2*count)) if count else None
    lower=max(0.,fraction-allowance) if count else None
    upper=min(1.,fraction+allowance) if count else None
    return {'schema_version':1,'method':'guarded_area_stratified_frame_sampling_v2','policy':asdict(policy),
        'denominator_complete':True,'frame_area':total,'frame_triangles':int(frame.sum()),
        'observed_area':total*lower if count else 0.,'observed_fraction':lower,
        'estimated_observed_area':total*fraction if count else 0.,'estimated_observed_fraction':fraction,
        'sampling_fraction_allowance':allowance,'sampling_fraction_interval':[lower,upper],
        'samples':count,'angular_supported_samples':int(supported.sum()),
        'frame_triangles_with_supported_samples':int(len(np.unique(ids[supported]))),
        'scope':'Complete non-optical triangle area; independent area samples observed through guarded posed photo rasters. Missing UV/material support remains uncovered.',
        'sampling_scope':'Predeclared seeded independent area strata; one-sided Hoeffding allowance conditional on the sampling model, not an exact geometric coverage certificate.',
        'limitations':['Camera and image-membership systematic errors are not bounded by the numerical sampling allowance.',
                       'The finite sampling design is reproducible but cannot prove visibility at every unsampled surface point.',
                       'Three-view angular diversity is an engineering coverage criterion, not semantic part identity.']}


@dataclass(frozen=True)
class FrameCoveragePolicy:
    subdivisions: int = 4
    minimum_separation_degrees: float = 8.

    def __post_init__(self):
        if type(self.subdivisions) is not int or not 2 <= self.subdivisions <= 16:
            raise ValueError('Frame coverage subdivisions must be 2 through 16')
        if not np.isfinite(self.minimum_separation_degrees) or not 0 < self.minimum_separation_degrees < 90:
            raise ValueError('Frame coverage separation must be between zero and 90 degrees')


def _subtriangles(count):
    triangles = []
    for i in range(count):
        for j in range(count - i):
            triangles.append(np.array([[i, j], [i + 1, j], [i, j + 1]], float) / count)
            if i + j < count - 1:
                triangles.append(np.array([[i + 1, j], [i + 1, j + 1], [i, j + 1]], float) / count)
    return np.asarray(triangles)


def _diverse(valid, directions, separation):
    norms = np.linalg.norm(directions, axis=2)
    usable = valid & (norms > 1e-12)
    unit = directions / np.maximum(norms[..., None], 1e-12)
    threshold = np.cos(np.radians(separation))
    observed = np.zeros(valid.shape[1], bool)
    for a, b, c in combinations(range(len(valid)), 3):
        candidate = usable[a] & usable[b] & usable[c] & ~observed
        if not candidate.any():
            continue
        observed |= candidate & (np.sum(unit[a] * unit[b], axis=1) <= threshold) & (
            np.sum(unit[a] * unit[c], axis=1) <= threshold) & (
            np.sum(unit[b] * unit[c], axis=1) <= threshold)
    return observed


def assess_frame_surface_coverage(mesh, material_track_evidence, *, policy=FrameCoveragePolicy()):
    """Return area coverage supported throughout fixed barycentric subdivisions.

    Each evidence record contains source_faces [N], barycentric [N,3], valid
    [V,N], and directions [V,N,3] in one canonical coordinate frame. A covered
    cell needs three mutually separated views for samples near each of its
    three corners AND near its center. This deliberately leaves sparse and
    unobserved cells uncovered; it is not an interpolation of atlas hit counts.
    """
    triangles = mesh.vertices[mesh.faces]
    area = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1) / 2
    if not np.isfinite(area).all():
        raise ValueError('Frame coverage requires finite source geometry')
    frame = np.ones(len(mesh.faces), bool)
    for part in mesh.parts:
        if part.get('has_lens_appearance_extension', False) or part.get('transmission', 0) > 0:
            frame[part['face_start']:part['face_start'] + part['face_count']] = False
    count = policy.subdivisions ** 2
    bits = np.zeros((len(mesh.faces), count), np.uint8)
    sampled = np.zeros(len(mesh.faces), bool)
    supported_tracks = total_tracks = 0
    cells = _subtriangles(policy.subdivisions)
    for evidence in material_track_evidence:
        ids = np.asarray(evidence['source_faces'])
        bary = np.asarray(evidence['barycentric'], float)
        valid = np.asarray(evidence['valid'])
        directions = np.asarray(evidence['directions'], float)
        if (ids.ndim != 1 or ids.dtype.kind not in 'iu' or np.any(ids < 0) or np.any(ids >= len(frame))
                or bary.shape != (len(ids), 3) or not np.isfinite(bary).all()
                or np.any(bary < -1e-7) or np.any(bary > 1 + 1e-7)
                or not np.allclose(bary.sum(axis=1), 1., atol=1e-7)
                or valid.dtype != np.bool_ or valid.ndim != 2 or valid.shape[1] != len(ids)
                or directions.shape != (*valid.shape, 3) or not np.isfinite(directions).all()):
            raise ValueError('Malformed source-bound frame coverage tracks')
        total_tracks += len(ids)
        diverse = _diverse(valid, directions, policy.minimum_separation_degrees) & frame[ids]
        supported_tracks += int(diverse.sum())
        ids, xy = ids[diverse], bary[diverse, :2]
        sampled[ids] = True
        for ordinal, cell in enumerate(cells):
            matrix = np.column_stack((cell[0] - cell[2], cell[1] - cell[2]))
            first = (xy - cell[2]) @ np.linalg.inv(matrix).T
            local = np.column_stack((first, 1 - first.sum(axis=1)))
            inside = np.all(local >= -1e-7, axis=1)
            for corner in range(3):
                selected = inside & (local[:, corner] >= .70)
                np.bitwise_or.at(bits[:, ordinal], ids[selected], np.uint8(1 << corner))
            center = inside & np.all(local >= .20, axis=1)
            np.bitwise_or.at(bits[:, ordinal], ids[center], np.uint8(8))
    covered_cells = np.count_nonzero(bits == 15, axis=1)
    total = float(area[frame].sum())
    observed = float(np.sum(area[frame] * covered_cells[frame] / count))
    return {'schema_version': 1, 'method': 'angular_multiview_barycentric_frame_area_v1',
        'policy': asdict(policy), 'denominator_complete': True,
        'frame_area': total, 'observed_area': observed,
        'observed_fraction': observed / total if total > 0 else None,
        'frame_triangles': int(frame.sum()), 'frame_triangles_with_supported_samples': int(np.count_nonzero(frame & sampled)),
        'covered_subtriangles': int(covered_cells[frame].sum()), 'subtriangles_per_face': count,
        'tracks': total_tracks, 'angular_supported_tracks': supported_tracks,
        'scope': 'Whole non-optical triangle area, weighted once per spatial cell; missing UV and unobserved surfaces count as uncovered.',
        'limitations': ['Finite spatial sampling cannot establish visibility between sample locations.',
                       'Three-view angular diversity is an engineering coverage criterion, not semantic part identity.']}
