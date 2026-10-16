"""Exact Blender encoding of planned owner normals in verified normal spaces.

The intended normals are float32 corner data. The pure plan supplies all required
sharp fan boundaries before encoding. A read-only capture re-encodes on a private
mesh and compares both the normal-space flags and encoded shorts exactly. Decoded
vectors must also stay within conservative two-angle quantization roundoff. If
Blender's inverse encoder snaps near a basis boundary, a bounded deterministic
search of its two integer angles retains the same intended vectors and bound.
"""
from __future__ import annotations

import bpy
import numpy as np

INTENT_ATTRIBUTE = "g3d_owner_normal_intent"
QUANTIZATION_VECTOR_BOUND = float(8*np.pi/32767+64*np.finfo(np.float32).eps)


def _intent(mesh):
    attribute = mesh.attributes.get(INTENT_ATTRIBUTE)
    if attribute is None:
        return None
    if attribute.domain != "CORNER" or attribute.data_type != "FLOAT_VECTOR":
        raise ValueError("owner normal intent must be a corner FLOAT_VECTOR attribute")
    values = np.empty(len(attribute.data) * 3, dtype=np.float32)
    attribute.data.foreach_get("vector", values)
    return _validated(mesh, values.reshape(-1, 3))


def _validated(mesh, values):
    values = np.asarray(values, dtype=np.float32)
    if (values.shape != (len(mesh.loops), 3) or not np.isfinite(values).all()
            or np.any(np.linalg.norm(values, axis=1) <= 1e-10)):
        raise ValueError("owner normal intent needs one finite nonzero vector per corner")
    return values


def _encoded(mesh):
    attribute = mesh.attributes.get("custom_normal")
    if attribute is None:
        return None
    if attribute.domain != "CORNER" or attribute.data_type != "INT16_2D":
        raise ValueError("unsupported Blender custom normal encoding")
    values = np.empty(len(attribute.data) * 2, dtype=np.int32)
    attribute.data.foreach_get("value", values)
    return values


def _sharp(mesh):
    return [bool(edge.use_edge_sharp) for edge in mesh.edges]


def _error(mesh,intended):
    decoded=np.empty(len(mesh.loops)*3,dtype=np.float32)
    mesh.corner_normals.foreach_get('vector',decoded)
    difference=abs(decoded.reshape(-1,3).astype(float)-intended.astype(float))
    if not np.isfinite(difference).all():return float('inf')
    return float(difference.max()) if difference.size else 0.


def _encode(mesh,intended):
    """Encode on a private mesh, correcting only out-of-bound integer codes.

    Every search starts from Blender's setter. A finite-difference Jacobian and
    a 3-by-3 integer neighborhood improve the worst component error monotonically.
    Three bounded iterations may still fail; callers retain the original bound.
    No geometry, fan flags or intended direction is changed by this correction.
    """
    sharp=_sharp(mesh)
    mesh.normals_split_custom_set(intended.tolist());mesh.update()
    if _sharp(mesh)!=sharp:
        return False
    decoded=np.asarray([normal.vector[:] for normal in mesh.corner_normals],dtype=float)
    if not np.isfinite(decoded).all():
        raise ValueError('normal encoder produced nonfinite decoded vectors')
    errors=abs(decoded-intended).max(axis=1)
    attribute=mesh.attributes.get('custom_normal')
    if attribute is None:
        return True
    for loop in np.flatnonzero(errors>QUANTIZATION_VECTOR_BOUND):
        loop=int(loop);wanted=intended[loop].astype(float)
        current=np.asarray(attribute.data[loop].value,dtype=int)
        best=decoded[loop]

        def decode(code):
            attribute.data[loop].value=np.asarray(code,dtype=int).tolist()
            mesh.update()
            value=np.asarray(mesh.corner_normals[loop].vector,dtype=float)
            if not np.isfinite(value).all():
                raise ValueError('normal encoder produced nonfinite decoded vectors')
            return value

        for _ in range(3):
            columns=[]
            for axis in range(2):
                high=current.copy();low=current.copy()
                high[axis]=min(32767,high[axis]+16)
                low[axis]=max(-32767,low[axis]-16)
                columns.append((decode(high)-decode(low))/(high[axis]-low[axis]))
            jacobian=np.asarray(columns).T
            try:delta=np.linalg.lstsq(jacobian,wanted-best,rcond=None)[0]
            except np.linalg.LinAlgError:break
            if not np.isfinite(delta).all():break
            centre=np.clip(np.rint(current+delta),-32767,32767).astype(int)
            candidates=[(float(abs(best-wanted).max()),tuple(current),best)]
            for dx in range(-1,2):
                for dy in range(-1,2):
                    code=np.clip(centre+[dx,dy],-32767,32767).astype(int)
                    value=decode(code)
                    candidates.append((float(abs(value-wanted).max()),tuple(code),value))
            error,code,best=min(candidates,key=lambda row:(row[0],row[1]))
            current=np.asarray(code)
            if error<=QUANTIZATION_VECTOR_BOUND:break
        decode(current)
    return _sharp(mesh)==sharp


def write(mesh, intended_loop_normals):
    """Encode only into already planned normal fans; never restore flags afterward.

    Test a private copy first. Missing fan splits refuse before the source mesh
    changes, rather than silently averaging distinct selected/unselected normals.
    """
    intended = _validated(mesh, intended_loop_normals)
    attribute = mesh.attributes.get(INTENT_ATTRIBUTE)
    if attribute is not None and (attribute.domain != "CORNER" or attribute.data_type != "FLOAT_VECTOR"):
        raise ValueError("owner normal intent attribute name is already used by incompatible data")
    sharp=_sharp(mesh);temporary=mesh.copy()
    try:
        if not _encode(temporary,intended) or _sharp(temporary)!=sharp:
            raise ValueError('normal encoding needs additional planned sharp fan boundaries')
        if _error(temporary,intended)>QUANTIZATION_VECTOR_BOUND:
            raise ValueError('decoded planned normals exceed encoding quantization roundoff')
        encoded=_encoded(temporary)
        mesh.normals_split_custom_set(intended.tolist())
        if encoded is not None:
            mesh.attributes['custom_normal'].data.foreach_set('value',encoded)
    finally:
        bpy.data.meshes.remove(temporary)
    attribute = mesh.attributes.get(INTENT_ATTRIBUTE)
    if attribute is None:
        attribute = mesh.attributes.new(INTENT_ATTRIBUTE, "FLOAT_VECTOR", "CORNER")
    attribute.data.foreach_set("vector", intended.ravel())
    mesh.update()


def capture(mesh):
    """Return an exact encoding witness; temporary datablocks are always removed.

    Absence means a legacy/source mesh without a planned-normal witness. The
    result does not by itself grant edit authority: its intent still needs the
    host's exact planned-vector comparison.
    """
    intended = _intent(mesh)
    if intended is None:
        return None
    actual = _encoded(mesh)
    temporary = mesh.copy()
    try:
        sharp=_sharp(mesh)
        spaces_match=_encode(temporary,intended) and _sharp(temporary)==sharp
        expected = _encoded(temporary)
        vector_error=_error(mesh,intended)
        return {"intent_normals": intended.tolist(),
                "encoded_matches": spaces_match and actual is not None and expected is not None and np.array_equal(actual, expected),
                "normal_space_matches":spaces_match,
                "decoded_within_quantization":vector_error<=QUANTIZATION_VECTOR_BOUND,
                "quantization_vector_bound":float(QUANTIZATION_VECTOR_BOUND),
                "max_vector_error":vector_error}
    finally:
        bpy.data.meshes.remove(temporary)
