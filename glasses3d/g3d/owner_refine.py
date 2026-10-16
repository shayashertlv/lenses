"""Local interpolating samples for selected curved mesh refinement.

An edge uses cubic endpoint tangent controls (PN interpolation); its midpoint
keeps both original endpoints. A triangle centre is the cubic PN patch centre.
For larger polygons the same symmetric pairwise tangent correction supplies a
centre. Triangle refinement should use four children where all edges split:
six centre-fan children introduce unnecessarily thin triangles on narrow rings.
New normals use a quadratic PN field with shared endpoint/chord controls. The
field is continuous across shared edges; its derivatives need not be continuous.
"""
import numpy as np


def vertex_normals(vertices, polygons, faces, corner_normals):
    """One shared direction from the selected loops at each source vertex."""
    result=np.zeros_like(vertices,dtype=float)
    start=np.cumsum([0]+[len(p) for p in polygons])
    for i in faces:
        for vertex,normal in zip(polygons[i],corner_normals[start[i]:start[i+1]]):
            result[vertex]+=normal
    lengths=np.linalg.norm(result,axis=1)
    ids=sorted({v for i in faces for v in polygons[i]})
    if not np.isfinite(result[ids]).all() or np.any(lengths[ids]<1e-8):
        raise ValueError('curved refinement needs consistent nonzero selected shading normals')
    result[ids]/=lengths[ids,None]
    return result


def mean_normal(normals):
    """Refuse cancelling tangent directions before any coordinate can become NaN."""
    normal=np.asarray(normals,float).mean(0)
    length=np.linalg.norm(normal)
    if not np.isfinite(normal).all() or not np.isfinite(length) or length<1e-8:
        raise ValueError('interpolated shading normals cancel; repair their orientation before refinement')
    return normal/length


def sample(points,normals,strength):
    """Original-sample interpolation; no global fit residual is introduced."""
    points,normals=np.asarray(points,float),np.asarray(normals,float)
    delta=np.zeros(3)
    for a in range(len(points)):
        for b in range(a+1,len(points)):
            edge=points[b]-points[a]
            delta+=np.dot(edge,normals[b])*normals[b]-np.dot(edge,normals[a])*normals[a]
    return points.mean(0)+strength*delta/(2*len(points)**2)


def sample_normal(points,normals,strength):
    """Quadratic PN normal at an edge midpoint or symmetric polygon centre.

    Endpoint normals stay unchanged. Each quadratic edge control depends only
    on its two endpoints, so adjacent faces share exactly the same edge normal.
    A polygon centre uses equal barycentric weights; for a triangle this is the
    standard quadratic triangular Bezier field. Strength blends with the
    original normalized linear field before a final normalization.
    """
    points,normals=np.asarray(points,float),np.asarray(normals,float)
    linear=mean_normal(normals)
    total=normals.sum(0)
    for a in range(len(points)):
        for b in range(a+1,len(points)):
            edge=points[b]-points[a]
            length2=np.dot(edge,edge)
            if not np.isfinite(length2) or length2<1e-16:
                raise ValueError('quadratic shading interpolation needs distinct finite endpoints')
            pair=normals[a]+normals[b]
            control=pair-2*np.dot(edge,pair)/length2*edge
            total+=2*mean_normal(control[None,:])
    quadratic=mean_normal(total[None,:])
    return mean_normal(((1-strength)*linear+strength*quadratic)[None,:])
