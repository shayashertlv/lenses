"""Deterministic, selected surface edits. Pure planning; no Blender or provider calls.

These operations require a rendered, owner-selected preview. Topology refinement
inserts shared internal edge midpoints inside the selection. Its default uses
local cubic tangent samples and four-child triangles; boundary triangles and
larger polygons use centre fans. Original vertices and boundary edges stay exact.
Explicit relax_interior also moves original interior vertices toward the fit,
with a smooth distance falloff near the patch boundary and fixed points. The
boundary, fixed points and opposite surface remain unchanged.
"""
from __future__ import annotations

import copy
import math
import numpy as np
from . import owner_extended, owner_health, owner_refine

OPERATION_KINDS = frozenset({'flatten_face', 'repair_shading', 'refine_curved_surface'}) | owner_extended.OPERATION_KINDS | owner_health.OPERATION_KINDS


def active(operations):
    # One deterministic shading policy applies to standalone and mixed edits.
    return any(op['kind'] in OPERATION_KINDS | {'translate_region', 'rotate_region', 'taper_region'} for op in operations)


def error(message, code='E_OWNER_SURFACE', **facts):
    from .owner_mesh import OwnerMeshError
    raise OwnerMeshError(code, message, **facts)


def split_edge_values(values, splits):
    """Carry one authored edge value to both deterministically planned children."""
    result=dict(values)
    for row in splits:
        a,b=row['edge'];mid=row['vertex_id'];key=tuple(sorted((a,b)))
        if key not in result:continue
        value=result.pop(key)
        result[tuple(sorted((a,mid)))]=value
        result[tuple(sorted((mid,b)))]=value
    return result


def amount(params, required):
    if set(params) != set(required):
        error('surface parameters must be exactly '+', '.join(required))
    strength = params['strength']
    if isinstance(strength, bool) or not isinstance(strength, (float, int)) or not math.isfinite(strength) or not 0 < strength <= 1:
        error('strength must be finite and greater than zero through one')
    return float(strength)


def selected(obj, operation):
    from . import owner_mesh as M
    weights, _ = M.scope_vertices(operation['scope'], obj)
    support = {i for i, w in weights.items() if w == 1}
    faces = [i for i, p in enumerate(obj['polygons']) if set(p) <= support]
    if not faces:
        error('select complete faces with weight one; the operation cannot enlarge its shown support')
    return faces


def orientation_sign(obj):
    """World winding reverses under reflection; physical outside does not."""
    return -1 if np.linalg.det(np.asarray(obj.get('matrix_world', np.eye(4)))[:3, :3]) < 0 else 1


def representation_tolerance(points):
    """Eight float32 coordinate ULPs, solely for represented-plane predicates."""
    scale = max(float(np.max(np.abs(points))), np.finfo(np.float32).tiny)
    return float(abs(np.spacing(np.float32(scale)))) * 8


def face_vectors(vertices, polygons, orientation=1):
    out = []
    for poly in polygons:
        points = vertices[poly]
        points = points-points[0]
        vector = np.sum(np.cross(points, np.roll(points, -1, axis=0)), axis=0)*orientation
        length = np.linalg.norm(vector)
        if length <= 1e-10:
            error('selected surface contains a degenerate face; clean the mesh first')
        out.append(vector / length)
    return np.asarray(out)


def _chart(vertices, polygons, faces, orientation=1):
    ids = sorted({v for i in faces for v in polygons[i]})
    points = vertices[ids]
    center = points.mean(0)
    _, singular, axes = np.linalg.svd(points-center, full_matrices=False)
    if len(singular) < 2 or singular[1] <= 1e-8:
        error('selected surface does not span a plane')
    n = axes[2]
    outward = face_vectors(vertices, [polygons[i] for i in faces], orientation).mean(0)
    if n @ outward < 0:
        n = -n
    u = axes[0]; v = np.cross(n, u)
    basis = np.array([u, v, n])
    return center, basis, ids


def _quadratic(vertices, polygons, faces, orientation=1):
    center, basis, ids = _chart(vertices, polygons, faces, orientation)
    xyz = (vertices[ids]-center) @ basis.T
    x, y, z = xyz.T
    design = np.c_[np.ones(len(x)), x, y, x*x, x*y, y*y]
    if np.linalg.matrix_rank(design) < 6:
        error('curved refinement needs at least six independent surface samples')
    coef = np.linalg.lstsq(design, z, rcond=None)[0]
    residual = abs(design @ coef-z)
    # The fit is reported, not a likeness gate. The proposed surface is shown in previews.
    def project(points):
        local = (points-center) @ basis.T
        x, y = local[:, :2].T
        local[:, 2] = np.c_[np.ones(len(x)), x, y, x*x, x*y, y*y] @ coef
        return local @ basis+center
    def normals(points):
        local = (points-center) @ basis.T
        x, y = local[:, :2].T
        local_n = np.c_[-coef[1]-2*coef[3]*x-coef[4]*y,
                        -coef[2]-coef[4]*x-2*coef[5]*y, np.ones(len(x))]
        normal = local_n @ basis
        return normal/np.linalg.norm(normal, axis=1)[:, None]
    return project, normals, float(residual.max())


def _normal_blend(old, target, strength):
    mixed = (1-strength)*old+strength*target
    length = np.linalg.norm(mixed, axis=1)
    if np.any(length <= 1e-8):
        error('normal repair would cross a reversed normal')
    return mixed/length[:, None]


def _relax_falloff(vertices, ids, boundary_edges, fixed_ids, *, points=None, width=None):
    """Smoothly fade displacement over 10% of the frozen chart's largest span.

    Distance is measured to complete boundary segments, not just their sampled
    endpoints: a narrow triangle beside a long fixed edge must receive the same
    protection as one beside a densely sampled edge. Additional fixed points
    receive the same falloff. The cubic has zero derivative at both ends.
    """
    points=vertices[ids] if points is None else np.asarray(points)
    width=float(np.ptp(vertices[ids],axis=0).max())*.1 if width is None else width
    if width<=1e-12:error('relaxation needs a nonzero patch span')
    distance=np.full(len(points),np.inf)
    for a,b in boundary_edges:
        edge=vertices[b]-vertices[a];length2=float(edge@edge)
        if length2<=1e-24:continue
        along=np.clip((points-vertices[a])@edge/length2,0,1)
        distance=np.minimum(distance,np.linalg.norm(points-vertices[a]-along[:,None]*edge,axis=1))
    for i in fixed_ids:
        distance=np.minimum(distance,np.linalg.norm(points-vertices[i],axis=1))
    t=np.minimum(distance/width,1.)
    return t*t*(3-2*t),width


def authored_normals(obj):
    witness=obj.get('normal_intent')
    if witness and all(witness.get(k) for k in ('encoded_matches','normal_space_matches','decoded_within_quantization')):
        return np.asarray(witness['intent_normals'],float)
    return np.asarray(obj['corner_normals'],float)


def rigid_rotation_normals(obj,operation):
    """Use the confirmed rotation itself, avoiding host/Blender SVD differences."""
    matrix=np.asarray(obj.get('matrix_world',np.eye(4)))[:3,:3]
    normals=authored_normals(obj)@np.linalg.inv(matrix)
    line=np.asarray(operation['params']['axis_points_mm'],float)
    axis=line[1]-line[0];axis/=np.linalg.norm(axis)
    angle=math.radians(operation['params']['delta_deg']);c,s=math.cos(angle),math.sin(angle)
    normals=(normals*c+np.cross(axis,normals)*s+(normals@axis)[:,None]*axis*(1-c))@matrix
    normals/=np.linalg.norm(normals,axis=1)[:,None]
    return normals.astype(np.float32).astype(float).tolist()


def transport_normals(obj, after_local):
    """Fit every polygon vertex, preserving rigid motion without a sliver frame.

    Float32 translations may perturb edge vectors by a few ULPs. Recognize that
    representation error before fitting; no physical acceptance gate uses it.
    """
    before=np.asarray(obj['vertices_local'],float);after=np.asarray(after_local,float)
    if np.array_equal(before,after):return copy.deepcopy(obj['corner_normals'])
    normals=authored_normals(obj).copy();cursor=0
    for poly in obj['polygons']:
        a,b=before[poly],after[poly]
        if np.array_equal(a,b):cursor+=len(poly);continue
        tol=max(representation_tolerance(a),representation_tolerance(b))
        delta=b-a
        if np.max(abs(delta-delta.mean(0)))<=tol:
            cursor+=len(poly);continue
        # Use explicit three-component arithmetic and accurately summed normal
        # equations. Host and Blender ship different LAPACK implementations;
        # SVD/lstsq can encode different float32 values near exact zero. The
        # full polygon supplies the frame, never its possibly sliver first fan.
        def dot(x,y):return math.fsum(float(i)*float(j) for i,j in zip(x,y))
        def cross(x,y):return np.array([x[1]*y[2]-x[2]*y[1],x[2]*y[0]-x[0]*y[2],x[0]*y[1]-x[1]*y[0]])
        def length(x):return math.sqrt(dot(x,x))
        aa=a-np.array([math.fsum(a[:,i])/len(a) for i in range(3)])
        bb=b-np.array([math.fsum(b[:,i])/len(b) for i in range(3)])
        crosses=[cross(x,y) for x,y in zip(aa,np.roll(aa,-1,axis=0))]
        source_normal=np.array([math.fsum(row[i] for row in crosses) for i in range(3)])
        extent=max(length(row) for row in aa)
        if length(source_normal)<=tol*extent:
            error('surface-following cannot resolve the motion of a degenerate face')
        source_normal/=length(source_normal)
        tangent=max((row-dot(row,source_normal)*source_normal for row in aa),key=lambda row:dot(row,row))
        tangent/=length(tangent);bitangent=cross(source_normal,tangent)
        uv=[(dot(row,tangent),dot(row,bitangent)) for row in aa]
        a00=math.fsum(u*u for u,v in uv);a01=math.fsum(u*v for u,v in uv);a11=math.fsum(v*v for u,v in uv)
        determinant=a00*a11-a01*a01
        if determinant<=tol*tol*max(a00,a11):
            error('surface-following cannot resolve the motion of a degenerate face')
        rhs0=np.array([math.fsum(uv[j][0]*bb[j,i] for j in range(len(uv))) for i in range(3)])
        rhs1=np.array([math.fsum(uv[j][1]*bb[j,i] for j in range(len(uv))) for i in range(3)])
        mapped_u=(a11*rhs0-a01*rhs1)/determinant
        mapped_v=(a00*rhs1-a01*rhs0)/determinant
        mapped_normal=cross(mapped_u,mapped_v);area=length(mapped_normal)
        if area<=np.finfo(float).eps*length(mapped_u)*length(mapped_v):
            error('surface-following collapses a previously nondegenerate marking face')
        mapped_normal/=area
        dual_u=cross(mapped_v,mapped_normal);dual_v=cross(mapped_normal,mapped_u)
        dual_n=cross(mapped_u,mapped_v);volume=dot(mapped_u,dual_u)
        values=[]
        for value in normals[cursor:cursor+len(poly)]:
            moved=(dot(value,tangent)*dual_u+dot(value,bitangent)*dual_v+dot(value,source_normal)*dual_n)/volume
            values.append(moved/length(moved))
        values=np.asarray(values)
        original=normals[cursor:cursor+len(poly)]
        directions=original/np.linalg.norm(original,axis=1)[:,None]
        unchanged=np.max(abs(values-directions),axis=1)<=64*np.finfo(float).eps
        values[unchanged]=original[unchanged]
        normals[cursor:cursor+len(poly)]=values.astype(np.float32).astype(float)
        cursor+=len(poly)
    return normals.tolist()


def preserve_minimum_thickness(source, target, faces, *, preserve_original=True, minimum_mm=None):
    """Exact opposing graph surfaces; a positive total volume cannot hide crossings.

    Curved-patch changes must prove that the original minimum wall remains. The
    chart is selected from the source only, never searched until an edit passes.
    Generic thinning requires an explicit positive minimum_mm. Existing temple
    checks retain their declared t_min, even when a lower explicit floor is given.
    """
    from . import owner_mesh as M, owner_thickness
    if source.get('aliases', {}).get('status') == 'verified_topology' and source.get('role') == 'temple':
        from .temple.spec import DEFAULT_OPTIONS
        declared=source['aliases'].get('options',{}).get('t_min_mm',DEFAULT_OPTIONS['t_min_mm'])
        try:explicit=owner_thickness.minimum_floor(minimum_mm,preserve_original=True)
        except ValueError as exc:error(str(exc),'E_TOO_THIN')
        required=max(float(declared),float(explicit))
        vertices=np.asarray(target['vertices_world_mm'])
        check=M.paired_thickness(target,vertices,required,source)
        if check is None:
            tri=np.asarray(target['triangles'],int)
            if orientation_sign(target)<0:tri=tri[:,::-1]
            check=M.graph_thickness(vertices,tri,source['side'],required)
        return {'method': 'existing verified temple chart and declared minimum retained','declared_thickness':check}
    try:floor=owner_thickness.minimum_floor(minimum_mm,preserve_original=preserve_original)
    except ValueError as exc:error(str(exc),'E_TOO_THIN')
    constraints=M.temple_constraints(source)
    if constraints is not None:
        floor=max(floor,owner_thickness.minimum_floor(constraints['options']['t_min_mm'],preserve_original=True))
        minimum_mm=float(floor)
    from . import owner_intersections
    crossing=owner_intersections.no_new_crossings(source,target)
    center, basis, _ = _chart(np.asarray(source['vertices_world_mm']), source['polygons'], faces)
    orientation = -1 if np.linalg.det(np.asarray(source.get('matrix_world', np.eye(4)))[:3,:3]) < 0 else 1
    if orientation < 0:
        basis[1:] *= -1
    chart_normalizations=[];chart_cache={};chart_walls={};chart_caps={}
    def chart(obj):
        if id(obj) in chart_cache:return chart_cache[id(obj)]
        xyz = (np.asarray(obj['vertices_world_mm'])-center) @ basis.T
        walls,caps=owner_thickness.wall_polygons(xyz,obj['polygons'],wall_angle,position_error,with_caps=True)
        parents=(list(range(len(obj['polygons']))) if obj is source or obj['polygons']==source['polygons'] else
                 obj.get('_surface_polygon_parents',list(range(len(obj['polygons'])))))
        walls={i for i in walls if parents[i] in source_walls}
        chart_walls[id(obj)]=walls
        chart_caps[id(obj)]=caps
        normalized=owner_thickness.vertical_chart(xyz,obj['polygons'],wall_angle,walls=walls,surface_vertices=caps)
        chart_normalizations.append(float(np.max(np.linalg.norm(normalized[:,:2]-xyz[:,:2],axis=1))))
        if chart_normalizations[-1]>position_error:
            error('chart normalization exceeds float32 representation uncertainty','E_OWNER_GRAPH')
        xyz=normalized
        chart_cache[id(obj)]=np.c_[xyz[:,2], xyz[:,1], -xyz[:,0]]
        return chart_cache[id(obj)]
    # Near-vertical float32 walls are classified by normal angle, never by an
    # absolute projected area. This is representation error, not a wall allowance.
    raw=np.asarray(source['vertices_world_mm'])
    span=max(float(np.ptp(raw,axis=0).max()),1.)
    wall_angle=8*np.finfo(np.float32).eps*max(float(np.max(abs(raw))),span)/span
    position_error=wall_angle*span
    source_walls=owner_thickness.wall_polygons((raw-center)@basis.T,source['polygons'],wall_angle,position_error)
    def triangles(obj):
        rows=np.asarray(obj['triangles'],int)
        pids=obj.get('triangle_polygons')
        if pids is None:
            fans=[[p[0],p[k],p[k+1]] for p in obj['polygons'] for k in range(1,len(p)-1)]
            if obj['triangles']!=fans:error('wall proof requires actual triangle-to-polygon correspondence','E_OWNER_GRAPH')
            pids=[i for i,p in enumerate(obj['polygons']) for _ in range(len(p)-2)]
        chart(obj)
        # A wall parent may contain a real, narrow bevel. Only rim triangles
        # whose every vertex also belongs to retained graph surfaces can be
        # omitted; a wall-only shoulder must keep its exact projected domain.
        rows=rows[[i not in chart_walls[id(obj)] or not set(row).issubset(chart_caps[id(obj)])
                   for i,row in zip(pids,rows)]]
        return rows if orientation>0 else rows[:,::-1]
    def measure(obj,footprint=None):
        coordinates=chart(obj)
        try:
            M.graph_thickness(coordinates,triangles(obj),1,1e-12,footprint=footprint,wall_angle=wall_angle,position_error=position_error)
        except M.OwnerMeshError as exc:
            if exc.code!='E_TOO_THIN':raise
            # Coverage has already passed. Exact evaluation distinguishes a
            # genuine crossing from a zero-thickness common rim.
        return owner_thickness.minimum(coordinates,triangles(obj),footprint=footprint,wall_angle=wall_angle,position_error=position_error)
    try:
        baseline,before=owner_thickness.source_certificate(chart(source),triangles(source),wall_angle=wall_angle,position_error=position_error)
    except M.OwnerMeshError as exc:
        if exc.code!='E_OWNER_GRAPH':raise
        try:
            return {**owner_thickness.local_patches(source,target,faces,preserve_original=preserve_original,minimum_mm=minimum_mm),
                    'no_new_crossings':crossing}
        except ValueError as local_error:
            raise M.OwnerMeshError('E_OWNER_GRAPH',str(local_error)) from local_error
    def xy_chart(obj):
        coordinates=chart(obj)
        return np.c_[-coordinates[:,2],coordinates[:,1],coordinates[:,0]]
    footprint=owner_thickness.changed_footprint(source,target,xy_chart(source),xy_chart(target))
    if footprint is None:
        actual,after=baseline,{**before,'unchanged_tessellation':True,'scope':'whole_target'}
    elif floor>baseline:
        # An unchanged region is certified only to the old source minimum. A
        # higher requested floor needs the whole target, including that region.
        actual,after=measure(target)
        after={**after,'scope':'whole_target'}
    else:
        actual,after=measure(target,footprint)
        after={**after,'scope':'edited_footprint'}
    if baseline<0:
        error('source has a true crossing of its opposing surfaces','E_TOO_THIN')
    required=max(baseline if preserve_original else 0,floor)
    if actual<required:
        raise M.OwnerMeshError('E_TOO_THIN','the operation reduces graph thickness below its required minimum',
                              before=before,after=after)
    return {'method':'exact opposite-surface graph intersections in the frozen source chart',
            'before':before,'after':after,'original_minimum_preserved':actual>=baseline,
            'unchanged_remainder_proven':True,
            'no_new_crossings':crossing,
            'representation_position_bound_mm':float(position_error),
            'maximum_chart_projection_normalization_mm':max(chart_normalizations,default=0.),
            'edited_footprint_mm':None if footprint is None else [v.tolist() for v in footprint],
            'global_minimum_lower_bound_mm':float(min(baseline,actual) if after['scope']=='edited_footprint' else actual),
            'required_minimum_mm':float(required),'required_minimum_exact':str(required)}


def plan_object(obj, operation):
    """Return complete selected-object arrays, with exact unchanged original support."""
    from . import owner_mesh as M
    result = copy.deepcopy(obj)
    kind, params = operation['kind'], operation['params']
    if kind not in OPERATION_KINDS:
        error('unknown surface operation')
    ev = obj.get('evaluated', {})
    users = obj.get('shared_mesh_users', 1)
    if (not ev.get('topology_matches_source') or not ev.get('positions_match_source') or
            (len(users) if isinstance(users, list) else users) != 1 or obj.get('unsupported_edit_state') or
            kind!='material_adjust' and obj.get('unsupported_normal_edit_state')):
        error('surface edits need a unique editable mesh without unsupported state')
    if operation['kind'] in owner_extended.OPERATION_KINDS:
        return owner_extended.plan_object(obj,operation)
    if operation['kind'] in owner_health.OPERATION_KINDS:
        return owner_health.plan_object(obj,operation)
    required = ('strength', 'mode') if kind == 'repair_shading' else ('strength',)
    if kind == 'flatten_face' and 'keep_boundary' in params:
        if type(params['keep_boundary']) is not bool:
            error('keep_boundary must be a boolean')
        required = ('strength', 'keep_boundary')
    if kind == 'refine_curved_surface' and 'relax_interior' in params:
        if type(params['relax_interior']) is not bool:error('relax_interior must be a boolean')
        required = ('strength','relax_interior')
    strength = amount(params, required)
    faces = selected(obj, operation)
    vertices = np.asarray(obj['vertices_world_mm'], float).copy()
    polygons = copy.deepcopy(obj['polygons'])
    matrix = np.asarray(obj.get('matrix_world', np.eye(4)), float)
    old_normals = np.asarray(obj['corner_normals'], float) @ np.linalg.inv(matrix[:3, :3])
    old_normals /= np.maximum(np.linalg.norm(old_normals, axis=1)[:, None], 1e-30)
    normals = old_normals.copy()
    loop_start = np.cumsum([0]+[len(p) for p in polygons])
    target_loops = np.concatenate([np.arange(loop_start[i], loop_start[i+1]) for i in faces])
    smooth = list(obj.get('polygon_smooth', [False]*len(polygons)))
    reports = {'selected_polygon_ids': faces, 'strength': strength}
    if kind == 'flatten_face':
        center, basis, ids = _chart(vertices, polygons, faces, orientation_sign(obj))
        edges = {}
        for i in faces:
            for a, b in zip(polygons[i], polygons[i][1:]+polygons[i][:1]):
                key = tuple(sorted((a, b))); edges[key] = edges.get(key, 0)+1
        boundary = {v for edge, count in edges.items() if count == 1 for v in edge}
        keep_boundary = params.get('keep_boundary', True)
        fixed = set(operation['scope'].get('fixed_vertex_ids', [])) | (boundary if keep_boundary else set())
        # A nonplanar boundary cannot become a plane while staying fixed. Refuse honestly.
        border = sorted(fixed.intersection(ids))
        if len(border) >= 3:
            border_center = vertices[border].mean(0)
            _, _, axes = np.linalg.svd(vertices[border]-border_center, full_matrices=False)
            plane_normal = axes[-1]
            if plane_normal @ basis[-1] < 0: plane_normal = -plane_normal
            residual = abs((vertices[border]-border_center) @ plane_normal)
            if float(residual.max()) > M.POSITION_TOL_MM:
                error('the selected boundary is nonplanar; flattening while keeping it fixed is impossible')
            center, basis[-1] = border_center, plane_normal
        distance = (vertices[ids]-center) @ basis[-1]
        moving = [i for i in ids if i not in fixed]
        if moving:
            vertices[moving] -= strength*((vertices[moving]-center) @ basis[-1])[:, None]*basis[-1]
        reports.update(plane_point_mm=center.tolist(), plane_normal=basis[-1].tolist(),
                       maximum_residual_before_mm=float(abs(distance).max()),
                       maximum_residual_after_mm=float(abs((vertices[ids]-center) @ basis[-1]).max()),
                       fixed_boundary_vertex_ids=sorted(boundary) if keep_boundary else [],
                       boundary_movement_explicitly_selected=not keep_boundary)
        targets = np.repeat(basis[-1][None, :], len(target_loops), axis=0)
        normals[target_loops] = _normal_blend(normals[target_loops], targets, strength)
    elif kind == 'repair_shading':
        mode = params['mode']
        if mode not in ('flat', 'smooth'):
            error('repair_shading mode is flat or smooth')
        vectors = {i: n for i, n in zip(faces, face_vectors(vertices, [polygons[i] for i in faces], orientation_sign(obj)))}
        averaged = np.zeros_like(vertices)
        for i in faces:
            averaged[polygons[i]] += vectors[i]
        lens = np.linalg.norm(averaged, axis=1)
        averaged /= np.maximum(lens, 1e-30)[:, None]
        for i in faces:
            start, end = loop_start[i:i+2]
            targets = (np.repeat(vectors[i][None, :], end-start, axis=0) if mode == 'flat'
                       else averaged[polygons[i]])
            normals[start:end] = _normal_blend(old_normals[start:end], targets, strength)
            smooth[i] = True  # explicit split loop normals retain flat facet borders
        reports['geometry_unchanged'] = True
    else:
        if obj.get('aliases', {}).get('status') == 'verified_topology':
            error('curved refinement cannot discard a verified temple thickness chart; use the facet tools')
        if obj.get('attributes') is None:
            error('refinement needs a fresh snapshot with complete mesh attributes')
        # Attributes whose meanings cannot be interpolated require dedicated operations.
        supported = {'position', '.edge_verts', '.corner_vert', '.corner_edge', 'material_index',
                     'sharp_face', 'sharp_edge', 'custom_normal', 'g3d_owner_normal_intent', '.select_vert', '.select_edge', '.select_poly',
                     *[u['name'] for u in obj.get('uv_layers', [])]}
        unknown = [a['name'] for a in obj['attributes'] if a['name'] not in supported]
        if unknown:
            error('refinement cannot safely interpolate attributes: '+', '.join(unknown))
        use_pn=not params.get('relax_interior',False)
        try:
            project, curved_normals, residual = _quadratic(vertices, polygons, faces, orientation_sign(obj))
        except M.OwnerMeshError as exc:
            if not use_pn:raise
            # Local tangent interpolation does not depend on a global fit. A
            # small or rank-deficient patch still receives every geometric and
            # thickness check; only this informational diagnostic is absent.
            project=curved_normals=residual=None
            reports['quadric_fit_unavailable_reason']=str(exc)
        try:
            sample_normals=owner_refine.vertex_normals(vertices,polygons,faces,old_normals) if use_pn else None
        except ValueError as exc:error(str(exc))
        def interpolated_normal(ids):
            try:return owner_refine.sample_normal(vertices[ids],sample_normals[ids],strength)
            except ValueError as exc:error(str(exc))
        counts={}
        for i in faces:
            for a,b in zip(polygons[i],polygons[i][1:]+polygons[i][:1]):
                key=tuple(sorted((a,b)));counts[key]=counts.get(key,0)+1
        boundary={v for edge,count in counts.items() if count==1 for v in edge}
        relax_fade=lambda points:np.ones(len(points))
        if params.get('relax_interior',False):
            fixed=boundary|set(operation['scope'].get('fixed_vertex_ids',[]))
            patch_ids=sorted({v for i in faces for v in polygons[i]})
            boundary_edges=[edge for edge,count in counts.items() if count==1]
            frozen=vertices.copy()
            fade_center,fade_basis,_=_chart(frozen,polygons,faces,orientation_sign(obj))
            fade_chart=(frozen-fade_center)@fade_basis[:2].T
            weights,width=_relax_falloff(fade_chart,patch_ids,boundary_edges,fixed)
            def relax_fade(points):
                chart=(points-fade_center)@fade_basis[:2].T
                return _relax_falloff(fade_chart,patch_ids,boundary_edges,fixed,points=chart,width=width)[0]
            weights=dict(zip(patch_ids,weights))
            movable=sorted(set(patch_ids)-fixed)
            if movable:
                fade=np.asarray([weights[i] for i in movable])
                vertices[movable]+=strength*fade[:,None]*(project(vertices[movable])-vertices[movable])
            reports['relaxation_falloff']={'method':'cubic smoothstep frozen-chart distance to fixed boundary segments and points',
                'width_mm':width,'patch_span_fraction':.1,
                'vertex_weights':[{'vertex_id':i,'weight':float(weights[i])} for i in patch_ids]}
        # Split shared selected edges once, with one common curved midpoint.
        # Patch boundary edges stay intact, so neighbouring faces never acquire
        # a T-junction. Face centres alone leave the original long optical edges
        # untouched no matter how often refinement is repeated.
        midpoints={}
        for edge,count in sorted(counts.items()):
            if count!=2:continue
            point=vertices[list(edge)].mean(0)
            # Preserve the endpoint residuals from the fitted quadric. Snapping
            # only new samples to the fit can create a ridge between unchanged
            # source samples whenever the input is not exactly quadratic.
            if use_pn:
                point=owner_refine.sample(vertices[list(edge)],sample_normals[list(edge)],strength)
                normal=interpolated_normal(list(edge))
                sample_normals=np.vstack([sample_normals,normal])
            else:
                point+=strength*relax_fade(point[None,:])[0]*(project(point[None,:])[0]-project(vertices[list(edge)]).mean(0))
            midpoints[edge]=len(vertices)
            vertices=np.vstack([vertices,point])
        new_polys, new_normals, new_smooth, materials, uv_rows = [], [], [], [], [[] for _ in obj.get('uv_layers', [])]
        material = obj.get('polygon_material', [0]*len(polygons))
        parents = []
        for i, poly in enumerate(polygons):
            start, end = loop_start[i:i+2]
            if i not in faces:
                new_polys.append(poly); new_normals.extend(old_normals[start:end]); new_smooth.append(smooth[i]); materials.append(material[i]); parents.append(i)
                for j, uv in enumerate(obj.get('uv_layers', [])): uv_rows[j].extend(uv['uv'][start:end])
                continue
            four_children=use_pn and len(poly)==3 and all(tuple(sorted((a,b))) in midpoints
                for a,b in zip(poly,poly[1:]+poly[:1]))
            if not four_children:
                mid = vertices[poly].mean(0)
                if use_pn:
                    mid=owner_refine.sample(vertices[poly],sample_normals[poly],strength)
                    normal=interpolated_normal(poly)
                    sample_normals=np.vstack([sample_normals,normal])
                else:
                    mid += strength*relax_fade(mid[None,:])[0]*(project(mid[None,:])[0]-project(vertices[poly]).mean(0))
                index = len(vertices); vertices = np.vstack([vertices, mid])
            ring=[];ring_uv=[[] for _ in uv_rows]
            for j, (a, b) in enumerate(zip(poly, poly[1:]+poly[:1])):
                ring.append(a)
                extra=midpoints.get(tuple(sorted((a,b))))
                if extra is not None:ring.append(extra)
                for k,uv in enumerate(obj.get('uv_layers',[])):
                    values=np.asarray(uv['uv'][start:end],float)
                    ring_uv[k].append(values[j])
                    if extra is not None:ring_uv[k].append((values[j]+values[(j+1)%len(poly)])/2)
            if four_children:
                # Four similar triangles avoid the thin six-child centre fan
                # that amplifies curved interpolation errors on narrow rings.
                for child in ((0,1,5),(1,2,3),(5,3,4),(1,3,5)):
                    tri=[ring[j] for j in child]
                    new_polys.append(tri);new_normals.extend(sample_normals[tri]);new_smooth.append(True)
                    materials.append(material[i]);parents.append(i)
                    for k in range(len(uv_rows)):uv_rows[k].extend(ring_uv[k][j].tolist() for j in child)
            else:
                for j,(a,b) in enumerate(zip(ring,ring[1:]+ring[:1])):
                    tri=[a,b,index]
                    new_polys.append(tri); new_normals.extend(sample_normals[tri] if use_pn else curved_normals(vertices[tri])); new_smooth.append(True); materials.append(material[i]); parents.append(i)
                    for k, uv in enumerate(obj.get('uv_layers', [])):
                        values = np.asarray(uv['uv'][start:end], float)
                        uv_rows[k].extend([ring_uv[k][j].tolist(), ring_uv[k][(j+1)%len(ring)].tolist(), values.mean(0).tolist()])
        polygons, normals, smooth = new_polys, np.asarray(new_normals), new_smooth
        result['polygon_material'] = materials
        result['_surface_polygon_parents'] = parents
        result['_surface_edge_splits']=[{'edge':list(edge),'vertex_id':mid} for edge,mid in midpoints.items()]
        result['uv_layers'] = [{**uv, 'uv': values} for uv, values in zip(obj.get('uv_layers', []), uv_rows)]
        reports.update(quadric_fit_max_residual_mm=residual, added_vertices=len(vertices)-len(obj['vertices_world_mm']),
                       original_vertices_unchanged=not params.get('relax_interior',False),
                       boundary_edges_unchanged=True, split_internal_edges=len(midpoints),
                       insertion_policy=('local cubic tangent samples with shared quadratic PN normals; four-child interior triangles' if use_pn
                           else 'fitted curvature with interpolated source residuals'),
                       fixed_boundary_vertex_ids=sorted(boundary), polygon_parents=parents,
                       relax_interior=params.get('relax_interior',False))
    matrix = np.asarray(obj.get('matrix_world', np.eye(4)), float)
    # origin translation cancels for source displacement and new barycentric vertices.
    origin = np.asarray(obj['vertices_world_mm'][0])-np.asarray(obj['vertices_local'][0]) @ matrix[:3, :3].T-matrix[:3, 3]
    local = ((vertices-origin-matrix[:3, 3]) @ np.linalg.inv(matrix[:3, :3]).T).astype(np.float32).astype(float)
    count = len(obj['vertices_local'])
    unchanged = np.all(vertices[:count] == np.asarray(obj['vertices_world_mm']), axis=1)
    local[:count][unchanged] = np.asarray(obj['vertices_local'])[unchanged]
    vertices = local @ matrix[:3, :3].T+matrix[:3, 3]+origin
    vertices[:count][unchanged] = np.asarray(obj['vertices_world_mm'])[unchanged]
    normals = normals @ matrix[:3, :3]
    normals /= np.maximum(np.linalg.norm(normals, axis=1)[:, None], 1e-30)
    if kind=='flatten_face':
        # Explicit boundary movement also rotates incident neighbouring faces.
        # Carry their authored normals through that motion instead of freezing
        # stale world directions while the face itself changes orientation.
        transported=np.asarray(transport_normals(obj,local))
        neighbour=np.ones(len(normals),dtype=bool);neighbour[target_loops]=False
        normals[neighbour]=transported[neighbour]
    elif kind=='repair_shading':
        untouched=np.ones(len(normals),dtype=bool);untouched[target_loops]=False
        normals[untouched]=np.asarray(obj['corner_normals'])[untouched]
    else:
        cursor=0
        for parent,poly in zip(result['_surface_polygon_parents'],polygons):
            if parent not in faces:
                normals[cursor:cursor+len(poly)]=obj['corner_normals'][loop_start[parent]:loop_start[parent+1]]
            cursor+=len(poly)
    result.update(vertices_local=local.tolist(), vertices_world_mm=vertices.tolist(), polygons=polygons,
                  corner_normals=normals.tolist(), polygon_smooth=smooth,
                  triangles=[[p[0], p[k], p[k+1]] for p in polygons for k in range(1, len(p)-1)],
                  triangle_polygons=[i for i,p in enumerate(polygons) for _ in range(len(p)-2)])
    physical = M.physical(result, reference=obj)
    if kind != 'repair_shading':
        reports['minimum_thickness_preservation'] = preserve_minimum_thickness(obj,result,faces)
    return result, {**reports, 'physical': physical}


def state(source, operations):
    """Pure ordered operation replay, including surface-following marking transport."""
    from . import owner_mesh as M, owner_markings as markings
    result = copy.deepcopy(source)
    objects = M.objects(result)
    reports = []
    retopologized=set()
    for op in operations:
        prior_normals={mid:(obj['corner_normals'],obj['polygons']) for mid,obj in objects.items()}
        oid = op['scope']['object_id']; old = objects[oid]
        if oid in retopologized:
            error('a topology-changing operation must be last for this object; capture a fresh map for a later edit','E_OWNER_SCOPE')
        if op['kind']=='transform_marking':
            sid=op['params']['surface_object_id']
            if sid not in objects:error('named marking surface is absent','E_OWNER_MARKING')
            after,report=owner_extended.transform_marking(old,objects[sid],op)
            objects[oid]=after
            reports.append({'id':op['id'],'kind':op['kind'],'params':copy.deepcopy(op['params']),**report})
        elif op['kind'] in OPERATION_KINDS:
            after, report = plan_object(old, op)
            world = {i: np.asarray(o['vertices_world_mm'], float).copy() for i, o in objects.items()}
            if '_surface_vertex_remap' not in after and op['kind']!='edge_shape':
                markings.follow(objects, world, op, np.asarray(old['vertices_world_mm']), np.asarray(after['vertices_world_mm'])[:len(old['vertices_world_mm'])])
            if ('_surface_vertex_remap' not in after and len(after['vertices_world_mm']) != len(old['vertices_world_mm']) or
                    op['kind'] in ('edge_shape','thickness','scale_region','bend_region')):
                for attachment in op.get('marking_attachments', []):
                    mid = attachment['object_id']; ids = [b['vertex_id'] for b in attachment['bindings']]
                    points = world[mid][ids]
                    previous = markings.nearest(np.asarray(objects[mid]['vertices_world_mm'])[ids], np.asarray(old['vertices_world_mm']), old['triangles'],orientation_sign(old))
                    found = markings.nearest(points, np.asarray(after['vertices_world_mm']), after['triangles'],orientation_sign(after))
                    for vid, prior, row in zip(ids, previous, found):
                        triangle = np.asarray(after['vertices_world_mm'])[after['triangles'][row[0]]]
                        n = np.cross(triangle[1]-triangle[0], triangle[2]-triangle[0]); n *= orientation_sign(after)/np.linalg.norm(n)
                        world[mid][vid] = np.asarray(row[1]) @ triangle+prior[3]*n
                    # A long lettering triangle can cut through a refined curved
                    # patch between its correctly transported vertices. Preserve
                    # its complete shape with the minimum outward clearance lift.
                    from . import owner_thickness
                    normal=np.mean(face_vectors(np.asarray(after['vertices_world_mm']),
                                     [after['triangles'][r[0]] for r in found],orientation_sign(after)),axis=0)
                    normal/=np.linalg.norm(normal)
                    gap=owner_thickness.marking_gap(world[mid],objects[mid]['triangles'],
                            np.asarray(after['vertices_world_mm']),after['triangles'],normal)
                    if gap<0:
                        if set(ids)!=set(range(len(world[mid]))):
                            error('partial marking support cannot authorize a complete clearance lift','E_OWNER_MARKING')
                        baseline_gap=owner_thickness.marking_gap(np.asarray(objects[mid]['vertices_world_mm']),objects[mid]['triangles'],
                                np.asarray(old['vertices_world_mm']),old['triangles'],normal)
                        # Outward float32 rounding protects exact nonintersection;
                        # it changes the proposed movement, never the acceptance threshold.
                        rounding=float(abs(np.spacing(np.float32(np.max(abs(world[mid]))))))*4
                        lift=max(0.,float(min(baseline_gap,0)-gap))+rounding
                        world[mid]+=normal*lift
                        report.setdefault('marking_clearance',[]).append({'object_id':mid,'object_name':objects[mid]['name'],
                            'outward_lift_mm':lift,'normal':normal.tolist(),'reason':'triangle-interior clearance after curved refinement'})
            objects[oid] = after
            if after['polygons']!=old['polygons'] or len(after['vertices_local'])!=len(old['vertices_local']):retopologized.add(oid)
            for mid in world:
                if mid != oid and not np.array_equal(world[mid], np.asarray(objects[mid]['vertices_world_mm'])):
                    mark = objects[mid]; matrix = np.asarray(mark.get('matrix_world', np.eye(4)))
                    local = ((world[mid]+source['origin_mm']-matrix[:3,3]) @ np.linalg.inv(matrix[:3,:3]).T).astype(np.float32).astype(float)
                    mark['corner_normals']=transport_normals(mark,local)
                    mark['vertices_local'] = local.tolist(); mark['vertices_world_mm'] = (local @ matrix[:3,:3].T+matrix[:3,3]-source['origin_mm']).tolist()
                    markings.validate_marking(mark, np.asarray(M.objects(source)[mid]['vertices_world_mm']), np.asarray(mark['vertices_world_mm']))
            reports.append({'id': op['id'], 'kind': op['kind'], 'params': copy.deepcopy(op['params']), **report})
        else:
            # Finite transforms use the same float32 geometry, normal transport
            # and physical proof whether alone or followed by a surface edit.
            snapshot = {**result, 'objects': list(objects.values())}
            if old.get('unsupported_edit_state') or old.get('unsupported_normal_edit_state'):
                error('finite edits need an editable mesh without unsupported state')
            world, _, rows = M.deformation(snapshot, [op])
            for mid, vertices in world.items():
                obj = objects[mid]; matrix = np.asarray(obj.get('matrix_world', np.eye(4)))
                prior=copy.deepcopy(obj)
                local = ((vertices+source['origin_mm']-matrix[:3,3]) @ np.linalg.inv(matrix[:3,:3]).T).astype(np.float32).astype(float)
                same=np.all(vertices==np.asarray(obj['vertices_world_mm']),axis=1)
                local[same]=np.asarray(obj['vertices_local'])[same]
                weights,_=M.scope_vertices(op['scope'],old)
                uniform=(not op['scope'].get('fixed_vertex_ids') and
                         set(weights)==set(range(len(old['vertices_local']))) and all(w==1 for w in weights.values()))
                obj['corner_normals']=(rigid_rotation_normals(obj,op) if op['kind']=='rotate_region' and uniform
                                       else transport_normals(obj,local))
                obj['vertices_local'] = local.tolist(); obj['vertices_world_mm'] = (local @ matrix[:3,:3].T+matrix[:3,3]-source['origin_mm']).tolist()
                if mid==oid:
                    rows[0]['physical']=M.physical(obj,reference=prior)
                    if obj['vertices_local']!=prior['vertices_local']:
                        reference=prior
                        rigid=(op['kind'] in ('translate_region','rotate_region') and
                               uniform and
                               M.temple_constraints(prior) is None)
                        if rigid:
                            # Compare in the requested rigid pose. A source-fixed
                            # axis would mistake rotated side walls for a thin rim.
                            # The captured float32 target still must meet the exact
                            # ideal-source floor; no rounding allowance is granted.
                            reference=copy.deepcopy(prior)
                            reference['vertices_world_mm']=vertices.tolist()
                            rows[0]['rigid_reference_pose']=op['kind']
                        rows[0]['minimum_thickness']=preserve_minimum_thickness(reference,obj,op['scope']['affected_polygon_ids'])
            reports.extend(rows)
        for mid,obj in objects.items():
            normals,polygons=prior_normals[mid]
            if obj['corner_normals']!=normals or obj['polygons']!=polygons:
                # A captured encoder witness describes the preceding normals.
                # Later batch operations must transport the newly planned ones.
                obj.pop('normal_intent',None)
        result['objects'] = list(objects.values())
        for attachment in op.get('marking_attachments', []):
            mid, sid = attachment['object_id'], attachment['surface_object_id']
            original = M.objects(source)
            before_mark, after_mark = original[mid], objects[mid]
            before_surface, after_surface = original[sid], objects[sid]
            old_points = np.asarray(before_mark['vertices_world_mm']); new_points = np.asarray(after_mark['vertices_world_mm'])
            old_surface = np.asarray(before_surface['vertices_world_mm']); new_surface = np.asarray(after_surface['vertices_world_mm'])
            old_sd = np.array([r[3] for r in markings.nearest(old_points, old_surface, before_surface['triangles'],orientation_sign(before_surface))])
            new_sd = np.array([r[3] for r in markings.nearest(new_points, new_surface, after_surface['triangles'],orientation_sign(after_surface))])
            if np.any((old_sd > M.POSITION_TOL_MM) & (new_sd < -M.POSITION_TOL_MM)):
                error('surface operation would bury a marking', 'E_OWNER_MARKING')
            old_cross = markings._pierced(old_points, before_mark['triangles'], old_surface, before_surface['triangles'])
            new_cross = markings._pierced(new_points, after_mark['triangles'], new_surface, after_surface['triangles'])
            if new_cross-old_cross:
                error('surface operation would create a marking/surface intersection', 'E_OWNER_MARKING')
    from . import owner_normal_plan
    fan_reports=owner_normal_plan.plan(source,result,operations)
    if reports and fan_reports:reports[-1]['normal_fan_encoding']=fan_reports
    return result, reports


def signature(snapshot):
    """State relevant to deterministic amounts; includes shading-only and topology changes."""
    from .owner_mesh import digest
    return digest([{key: obj.get(key) for key in ('id', 'vertices_local', 'polygons', 'corner_normals',
                   'polygon_smooth', 'polygon_material', 'uv_layers','material_definitions')} for obj in snapshot['objects']])


def verify(source, current, operations):
    """An exact planned result or unchanged source; no free mutation inside selected bounds."""
    from . import owner_mesh as M, owner_markings as markings
    expected, reports = state(source, operations)
    old, target, actual = M.objects(source), M.objects(expected), M.objects(current)
    if set(old) != set(actual) or source['origin_mm'] != current['origin_mm']:
        error('object identities or bridge origin changed', 'E_OWNER_SCOPE')
    touched = {op['scope']['object_id'] for op in operations}
    touched.update(a['object_id'] for op in operations for a in op.get('marking_attachments', []))
    variable = {'vertices_local', 'vertices_world_mm', 'positions_sha256', 'topology_sha256', 'edges', 'loops',
                'polygons', 'triangles', 'triangle_polygons', 'corner_normals', 'normal_attributes',
                'has_custom_normals', 'polygon_smooth', 'polygon_material', 'uv_layers', 'attributes',
                'other_attributes_sha256', 'aliases', 'evaluated', '_surface_polygon_parents','_surface_vertex_parents','_surface_vertex_remap','_surface_sharp_edges','_surface_edge_splits','_owner_material_edits','normal_intent','side'}
    checks = {}
    for oid, prior in old.items():
        now, want = actual[oid], target[oid]
        material_edit=want.get('_owner_material_edits')
        object_variable=variable|({'material_names','material_definitions','material_sha256'} if material_edit else set())
        radius_edit=want.get('edge_radius_recipe')!=prior.get('edge_radius_recipe')
        if radius_edit:object_variable|={'edge_radius_recipe','mesh_properties','mesh_properties_sha256'}
        if oid not in touched:
            if prior != now: error('unrequested object changed: '+prior['name'], 'E_OWNER_SCOPE')
            continue
        for key in set(prior) | set(now):
            if key not in object_variable and prior.get(key) != now.get(key):
                error('unrequested '+key+' changed: '+prior['name'], 'E_OWNER_SCOPE')
        if material_edit and (now.get('material_names')!=want.get('material_names') or now.get('material_definitions')!=want.get('material_definitions')):
            error('material graph or parameters differ from the confirmed adjustment','E_OWNER_AMOUNT')
        if radius_edit and (now.get('edge_radius_recipe')!=want.get('edge_radius_recipe') or now.get('mesh_properties')!=want.get('mesh_properties')):
            error('authored fillet recipe or unrelated mesh properties changed','E_OWNER_SCOPE')
        for key in ('vertices_local', 'polygons', 'polygon_material', 'polygon_smooth'):
            if now.get(key, []) != want.get(key, []):
                error('actual '+key+' differs from the confirmed surface operation', 'E_OWNER_AMOUNT')
        if 'scene_state' in current and now.get('side')!=(1 if np.mean(np.asarray(want['vertices_world_mm'])[:,0])>0 else -1):
            error('derived side does not match planned geometry','E_OWNER_SCOPE')
        if len(now.get('uv_layers', [])) != len(want.get('uv_layers', [])):
            error('UV layers changed outside interpolation', 'E_OWNER_SCOPE')
        for a, b in zip(now.get('uv_layers', []), want.get('uv_layers', [])):
            if ({k:v for k,v in a.items() if k!='uv'} != {k:v for k,v in b.items() if k!='uv'} or
                    not np.array_equal(np.asarray(a['uv'], np.float32), np.asarray(b['uv'], np.float32))):
                error('UVs differ from the exact face interpolation', 'E_OWNER_SCOPE')
        a, b = np.asarray(now['corner_normals']), np.asarray(want['corner_normals'])
        # Compare the exact intended float32 vectors and Blender's exact encoded
        # shorts, not a tolerance on the lossy decoded angular representation.
        witness=now.get('normal_intent')
        normals_changed=want['corner_normals']!=prior['corner_normals'] or want['polygons']!=prior['polygons']
        if witness is not None and (not material_edit or normals_changed):
            intent=np.asarray(witness['intent_normals'],np.float32)
            intended=b.astype(np.float32)
            matches=np.array_equal(intent,intended)
            if (not witness.get('encoded_matches') or not witness.get('normal_space_matches')
                    or not witness.get('decoded_within_quantization')
                    or not matches):
                error('normal intent or encoded normals differ from the confirmed plan','E_OWNER_AMOUNT',
                    object_id=oid,object_name=prior['name'],normal_witness={key:witness.get(key) for key in
                        ('encoded_matches','normal_space_matches','decoded_within_quantization')},
                    planned_normal_vectors_match=matches,
                    mismatched_normal_corners=(int(np.count_nonzero(np.any(intent!=intended,axis=1)))
                        if intent.shape==intended.shape else None))
        elif 'scene_state' in current and normals_changed:
            error('planned shading needs an exact Blender encoding witness','E_OWNER_AMOUNT')
        elif a.shape!=b.shape or not np.array_equal(a,b):
            error('shading differs from the exact planned normals','E_OWNER_AMOUNT')
        ev = now.get('evaluated', {})
        if not ev.get('topology_matches_source') or not ev.get('positions_match_source'):
            error('edited evaluated mesh no longer corresponds to source')
        marking = any(a['object_id'] == oid for op in operations for a in op.get('marking_attachments', []))
        geometry_unchanged=all(now.get(k)==prior.get(k) for k in ('vertices_local','polygons'))
        material_only=material_edit and geometry_unchanged and not normals_changed
        if material_only:
            if ev.get('corner_normals')!=prior.get('evaluated',{}).get('corner_normals'):
                error('material adjustment changed evaluated normals','E_OWNER_NORMALS')
        elif ev.get('normals_match_source') is False:
            error('evaluated normals override the confirmed authored normals','E_OWNER_NORMALS')
        checks[oid] = ({'status':'unchanged','geometry_unchanged':True} if material_only else
                       markings.validate_marking(now, np.asarray(prior['vertices_world_mm']), np.asarray(now['vertices_world_mm']))
                       if marking else M.physical(now, reference=prior))
        if not marking and not geometry_unchanged and now.get('triangles')!=want.get('triangles'):
            # The captured Blender tessellation must preserve the clearance
            # already certified on the proposed geometry, including concave ngons.
            scope={f for op in operations if op['scope']['object_id']==oid for f in op['scope']['affected_polygon_ids']}
            parents=want.get('_surface_polygon_parents',list(range(len(want['polygons']))))
            faces=[i for i,parent in enumerate(parents) if parent in scope]
            checks[oid]['captured_tessellation_clearance']=preserve_minimum_thickness(want,now,faces)
        # Non-shading data on unchanged topology is byte-for-byte protected. For a
        # refinement the planner refuses unknown attributes before any mutation.
        if prior['polygons'] == now['polygons'] and len(prior['vertices_local'])==len(now['vertices_local']):
            def attrs(obj):
                excluded={'position', 'custom_normal', 'sharp_face','g3d_owner_normal_intent'}
                if '_surface_sharp_edges' in want:excluded.add('sharp_edge')
                return {a['name']: a for a in obj.get('attributes', []) if a['name'] not in excluded}
            if attrs(prior) != attrs(now): error('unrequested mesh attribute changed', 'E_OWNER_SCOPE')
            if '_surface_sharp_edges' in want:
                from .owner_normal_plan import sharp_edges
                # Capture uses attributes; an injected private planning key grants no authority.
                raw={k:v for k,v in now.items() if k!='_surface_sharp_edges'}
                if sharp_edges(raw)!={tuple(e) for e in want['_surface_sharp_edges']}:
                    error('sharp flags differ from the exact planned normal fans','E_OWNER_SCOPE')
        else:
            known = {'position', '.edge_verts', '.corner_vert', '.corner_edge', 'material_index',
                     'sharp_face', 'sharp_edge', 'custom_normal', 'g3d_owner_normal_intent', '.select_vert', '.select_edge', '.select_poly',
                     *[u['name'] for u in prior.get('uv_layers', [])]}
            if any(a['name'] not in known for a in now.get('attributes', [])):
                error('topology edit introduced an unrelated attribute', 'E_OWNER_SCOPE')
            def sharp(obj):
                values = next((a['values'] for a in obj.get('attributes', []) if a['name']=='sharp_edge'), None)
                return {tuple(sorted(e)): bool(values[i][0]) if values is not None else False
                        for i,e in enumerate(obj.get('edges', []))}
            old_sharp, new_sharp = sharp(prior), sharp(now)
            old_sharp=split_edge_values(old_sharp,want.get('_surface_edge_splits',[]))
            remap=want.get('_surface_vertex_remap')
            if remap is not None:
                old_sharp={tuple(sorted((remap[a],remap[b]))):value for (a,b),value in old_sharp.items() if remap[a] is not None and remap[b] is not None}
                old_sharp={edge:value for edge,value in old_sharp.items() if value or edge in new_sharp}
            if '_surface_sharp_edges' in want:
                if {edge for edge,value in new_sharp.items() if value}!={tuple(sorted(e)) for e in want['_surface_sharp_edges']}:error('sharp flags differ from the exact fillet plan','E_OWNER_SCOPE')
            elif any(new_sharp.get(edge) != value for edge,value in old_sharp.items()) or any(
                    value for edge,value in new_sharp.items() if edge not in old_sharp):
                error('refinement changed an original sharp edge or added one', 'E_OWNER_SCOPE')
            for name in ('.select_vert', '.select_poly', '.select_edge'):
                values = next((a['values'] for a in prior.get('attributes', []) if a['name']==name), None)
                actual_values = next((a['values'] for a in now.get('attributes', []) if a['name']==name), None)
                if values is None:
                    if actual_values and any(v[0] for v in actual_values):error('refinement changed selection state')
                    continue
                if name == '.select_vert':
                    parents=want.get('_surface_vertex_parents')
                    wanted = [values[i] for i in parents] if parents is not None else values+[[False]]*(len(now['vertices_local'])-len(prior['vertices_local']))
                elif name == '.select_poly':
                    wanted = [values[i] for i in want['_surface_polygon_parents']]
                else:
                    by_edge = {tuple(sorted((remap[a],remap[b]) if remap is not None else (a,b))):values[i] for i,(a,b) in enumerate(prior['edges']) if remap is None or remap[a] is not None and remap[b] is not None}
                    by_edge=split_edge_values(by_edge,want.get('_surface_edge_splits',[]))
                    wanted = [by_edge.get(tuple(sorted(e)),[False]) for e in now['edges']]
                if actual_values != wanted:error('refinement changed selection state outside interpolation')
    if 'scene_state' in source and 'scene_state' in current:
        a, b = copy.deepcopy(source['scene_state']), copy.deepcopy(current['scene_state'])
        for oid in touched:
            for value in (a, b):
                value.get('meshes', {}).pop(old[oid]['mesh_name'], None)
                value.get('summary', {}).get(old[oid]['name'], {}).pop('bbox_mm', None)
                value.get('summary', {}).get(old[oid]['name'], {}).pop('vertices', None)
                value.get('summary', {}).get(old[oid]['name'], {}).pop('polygons', None)
                value.get('summary', {}).get(old[oid]['name'], {}).pop('triangles', None)
            material_edits=target[oid].get('_owner_material_edits')
            if material_edits:
                for edit in material_edits:b.get('materials',{}).pop(edit['name'],None)
                if 'materials' in b.get('counts',{}):b['counts']['materials']-=len(material_edits)
                for value in (a,b):
                    value.get('objects',{}).pop(old[oid]['name'],None)
                    value.get('summary',{}).get(old[oid]['name'],{}).pop('materials',None)
        if a != b: error('unrequested scene or datablock state changed', 'E_OWNER_SCOPE')
    # Compare markings against the new surface's actual topology.
    return {'ok': True, 'status': 'ok', 'physical': checks, 'operations': reports,
            'outside_support_unchanged': True, 'confirmed_amounts_match': True,
            'changes': [{'object_id':o,'object':old[o]['name'],'maximum_move_mm':float(np.max(np.linalg.norm(
                np.asarray(actual[o]['vertices_world_mm'])[:len(target[o].get('_surface_vertex_parents',old[o]['vertices_world_mm']))]-
                np.asarray(old[o]['vertices_world_mm'])[target[o].get('_surface_vertex_parents',list(range(len(old[o]['vertices_world_mm']))))],axis=1)))} for o in touched]}
