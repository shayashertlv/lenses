"""Exact Blender encoding of planned owner normals in verified normal spaces.

The intended normals are float32 corner data. The pure plan supplies all required
sharp fan boundaries before encoding. A local shading write preserves every raw
short outside its declared influence. A read-only capture re-encodes managed rows
on a private mesh and compares their encoded shorts exactly; unmanaged rows must
decode to their exact retained intent. The host independently verifies the mask
and exact source codes/vectors outside each operation's influence. Decoded managed
vectors must also stay within conservative two-angle quantization roundoff. If
Blender's inverse encoder snaps near a basis boundary, a bounded deterministic
search of its two integer angles retains the same intended vectors and bound.
"""
from __future__ import annotations

import bpy
import numpy as np

from .owner_normal_plan import MANAGED_ATTRIBUTE, AUTO_ATTRIBUTE

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
    if attribute.domain != "CORNER" or attribute.data_type not in ('INT16_2D', 'FLOAT_VECTOR'):
        raise ValueError("unsupported Blender custom normal encoding")
    field, width, dtype = _storage(attribute.data_type)
    values = np.empty(len(attribute.data) * width, dtype=dtype)
    attribute.data.foreach_get(field, values)
    return values


def _storage(data_type):
    if data_type == 'INT16_2D':
        return 'value', 2, np.int32
    if data_type == 'FLOAT_VECTOR':
        return 'vector', 3, np.float32
    raise ValueError('unsupported Blender custom normal encoding')


def _sharp(mesh):
    return [bool(edge.use_edge_sharp) for edge in mesh.edges]


def _managed(mesh):
    attribute = mesh.attributes.get(MANAGED_ATTRIBUTE)
    if attribute is None:
        # Earlier complete encodings had an intent layer but no partial mask.
        return np.full(len(mesh.loops), mesh.attributes.get(INTENT_ATTRIBUTE) is not None, dtype=bool)
    if attribute.domain != 'CORNER' or attribute.data_type != 'BOOLEAN':
        raise ValueError('owner normal encoding mask must be a corner BOOLEAN attribute')
    values = np.empty(len(mesh.loops), dtype=bool)
    attribute.data.foreach_get('value', values)
    return values


def _decoded(mesh):
    values = np.empty(len(mesh.loops) * 3, dtype=np.float32)
    mesh.corner_normals.foreach_get('vector', values)
    return values.reshape(-1, 3)


def _error(mesh,intended):
    decoded=np.empty(len(mesh.loops)*3,dtype=np.float32)
    mesh.corner_normals.foreach_get('vector',decoded)
    difference=abs(decoded.reshape(-1,3).astype(float)-intended.astype(float))
    if not np.isfinite(difference).all():return float('inf')
    return float(difference.max()) if difference.size else 0.


def _encode_short(mesh,intended):
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


def _encode(mesh, intended):
    """Retain the selected storage format, including exact float normal layers.

    Adding short corner normals changes Blender's automatic POINT-normal domain
    and can perturb untouched vectors even with zero codes. FLOAT_VECTOR storage
    retains those captured float32 vectors exactly without a short-normal fan.
    The same finite-vector and decoded-error bounds remain enforced by the caller.
    """
    if not hasattr(mesh, 'attributes'):
        return _encode_short(mesh, intended)
    attribute = mesh.attributes.get('custom_normal')
    if attribute is None or attribute.data_type == 'INT16_2D':
        return _encode_short(mesh, intended)
    if attribute is not None and (attribute.data_type != 'FLOAT_VECTOR' or attribute.domain != 'CORNER'):
        raise ValueError('unsupported Blender custom normal encoding')
    # Blender's free-vector storage does not share the short encoder's fans.
    # Its actual decoded values are checked with the unchanged bound below.
    attribute = mesh.attributes.get('custom_normal')
    if attribute is None:
        attribute = mesh.attributes.new('custom_normal', 'FLOAT_VECTOR', 'CORNER')
    attribute.data.foreach_set('vector', intended.ravel())
    mesh.update()
    return True


def write(mesh, intended_loop_normals, *, source=None, influence_loop_ids=None,
          encoding_loop_ids=None):
    """Encode only into already planned normal fans; never restore flags afterward.

    Test a private copy first. Missing fan splits refuse before the source mesh
    changes, rather than silently averaging distinct selected/unselected normals.
    """
    intended = _validated(mesh, intended_loop_normals)
    attribute = mesh.attributes.get(INTENT_ATTRIBUTE)
    if attribute is not None and (attribute.domain != "CORNER" or attribute.data_type != "FLOAT_VECTOR"):
        raise ValueError("owner normal intent attribute name is already used by incompatible data")
    if source is not None:
        from .owner_normal_plan import managed_loops
        managed_loops(source)
    prior_managed = _managed(mesh)
    influence = np.ones(len(mesh.loops), dtype=bool)
    if influence_loop_ids is not None:
        if source is None or len(source['corner_normals']) != len(mesh.loops):
            raise ValueError('a partial normal encoding requires its exact unchanged source topology')
        if any(type(i) is not int or not 0 <= i < len(mesh.loops) for i in influence_loop_ids):
            raise ValueError('normal influence contains an invalid corner')
        influence[:] = False
        influence[list(influence_loop_ids)] = True
    encoding = influence.copy()
    if encoding_loop_ids is not None:
        if any(type(i) is not int or not 0 <= i < len(mesh.loops) for i in encoding_loop_ids):
            raise ValueError('normal encoding mask contains an invalid corner')
        encoding[:] = False
        encoding[list(encoding_loop_ids)] = True
        if np.any(encoding & ~influence):
            raise ValueError('normal encoding mask exceeds the declared influence')
    managed = prior_managed | encoding
    previous = _encoded(mesh)
    source_layers = {row['name']: row for row in (source or {}).get('attributes', [])}
    source_storage = next((row for row in (source or {}).get('normal_attributes', [])
                           if row['name'] == 'custom_normal'), None)
    automatic = (mesh.attributes.get(AUTO_ATTRIBUTE) is not None or AUTO_ATTRIBUTE in source_layers or
                 influence_loop_ids is not None and previous is None)
    sharp=_sharp(mesh);temporary=mesh.copy()
    try:
        if previous is None and (influence_loop_ids is not None or
                source_storage is not None and source_storage['data_type'] == 'FLOAT_VECTOR'):
            # Only source-backed partial writes need a new exact representation.
            # The direct whole-mesh encoder retains its existing short format.
            temporary.attributes.new('custom_normal', 'FLOAT_VECTOR', 'CORNER')
        if not _encode(temporary,intended) or _sharp(temporary)!=sharp:
            raise ValueError('normal encoding needs additional planned sharp fan boundaries')
        encoded=_encoded(temporary)
        if encoded is None:
            raise ValueError('normal encoder did not produce custom normal storage')
        storage = temporary.attributes['custom_normal'].data_type
        field, width, dtype = _storage(storage)
        if previous is None:
            # Source automatic normals had no custom layer. Their exact implicit
            # FLOAT_VECTOR values are the captured vectors, never zero directions.
            previous = (np.asarray(source['corner_normals'], dtype=dtype).ravel() if width == 3 and source is not None
                        else np.zeros(len(mesh.loops) * width, dtype=dtype))
        if influence_loop_ids is not None:
            # Do not round-trip source shorts through their lossy decoded
            # vectors. Their normal spaces must also remain exactly unchanged.
            encoded.reshape(-1, width)[~encoding] = previous.reshape(-1, width)[~encoding]
            temporary.attributes['custom_normal'].data.foreach_set(field, encoded)
            temporary.update()
            if not np.array_equal(_decoded(temporary)[~influence],
                                  np.asarray(source['corner_normals'], dtype=np.float32)[~influence]):
                raise ValueError('normal encoding changed a fan outside the declared influence')
        if _error(temporary,intended)>QUANTIZATION_VECTOR_BOUND:
            raise ValueError('decoded planned normals exceed encoding quantization roundoff')
        if not np.array_equal(_decoded(temporary)[~managed], intended[~managed]):
            raise ValueError('unmanaged normal intent must equal its exact source vector')
        attribute = mesh.attributes.get('custom_normal')
        if attribute is None:
            attribute = mesh.attributes.new('custom_normal', storage, 'CORNER')
        if attribute.data_type != storage or attribute.domain != 'CORNER':
            raise ValueError('normal encoding changed the source storage format')
        attribute.data.foreach_set(field, encoded)
    finally:
        bpy.data.meshes.remove(temporary)
    attribute = mesh.attributes.get(INTENT_ATTRIBUTE)
    if attribute is None:
        attribute = mesh.attributes.new(INTENT_ATTRIBUTE, "FLOAT_VECTOR", "CORNER")
    attribute.data.foreach_set("vector", intended.ravel())
    legacy_full = bool((source or {}).get('normal_intent')) and MANAGED_ATTRIBUTE not in source_layers
    if not legacy_full or automatic:
        attribute = mesh.attributes.get(MANAGED_ATTRIBUTE)
        if attribute is None:
            attribute = mesh.attributes.new(MANAGED_ATTRIBUTE, 'BOOLEAN', 'CORNER')
        attribute.data.foreach_set('value', managed)
    if automatic:
        attribute = mesh.attributes.get(AUTO_ATTRIBUTE)
        if attribute is None:
            attribute = mesh.attributes.new(AUTO_ATTRIBUTE, 'BOOLEAN', 'CORNER')
        if attribute.domain != 'CORNER' or attribute.data_type != 'BOOLEAN':
            raise ValueError('owner automatic normal marker must be a corner BOOLEAN attribute')
        attribute.data.foreach_set('value', managed)
    mesh.update()


def capture(mesh):
    """Return an exact encoding witness; temporary datablocks are always removed.

    Absence means a legacy/source mesh without a planned-normal witness. The
    result does not by itself grant edit authority: its intent still needs the
    host's exact planned-vector comparison.
    """
    intended = _intent(mesh)
    if intended is None:
        if mesh.attributes.get(MANAGED_ATTRIBUTE) is not None or mesh.attributes.get(AUTO_ATTRIBUTE) is not None:
            raise ValueError('normal encoding mask has no authored intent')
        return None
    managed = _managed(mesh)
    automatic = mesh.attributes.get(AUTO_ATTRIBUTE)
    if automatic is not None:
        if (automatic.domain != 'CORNER' or automatic.data_type != 'BOOLEAN'
                or mesh.attributes.get(MANAGED_ATTRIBUTE) is None
                or mesh.attributes.get('custom_normal') is None
                or mesh.attributes['custom_normal'].data_type != 'FLOAT_VECTOR'):
            raise ValueError('owner automatic normal marker needs exact free normal storage and a mask')
        values = np.empty(len(mesh.loops), dtype=bool)
        automatic.data.foreach_get('value', values)
        if not np.array_equal(values, managed):
            raise ValueError('owner automatic normal marker differs from its encoding mask')
    actual = _encoded(mesh)
    attribute = mesh.attributes.get('custom_normal')
    width = _storage(attribute.data_type)[1] if attribute is not None else 0
    temporary = mesh.copy()
    try:
        sharp=_sharp(mesh)
        spaces_match=_encode(temporary,intended) and _sharp(temporary)==sharp
        expected = _encoded(temporary)
        vector_error=_error(mesh,intended)
        unmanaged_matches = np.array_equal(_decoded(mesh)[~managed], intended[~managed])
        return {"intent_normals": intended.tolist(),
                "encoded_matches": spaces_match and unmanaged_matches and actual is not None and expected is not None
                    and np.array_equal(actual.reshape(-1, width)[managed], expected.reshape(-1, width)[managed]),
                "normal_space_matches":spaces_match,
                "decoded_within_quantization":vector_error<=QUANTIZATION_VECTOR_BOUND,
                "managed_loop_ids":np.flatnonzero(managed).tolist(),
                "unmanaged_matches":bool(unmanaged_matches),
                "quantization_vector_bound":float(QUANTIZATION_VECTOR_BOUND),
                "max_vector_error":vector_error}
    finally:
        bpy.data.meshes.remove(temporary)
