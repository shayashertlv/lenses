"""Conservative multiview removal of supported lighting baked into frame textures.

Correspondences are actual UV surface points projected by source-bound cameras.
Two agreeing darker views and a directionally distinct brighter view are needed
to correct a texel. Persistent bright details remain; uncovered and ambiguous
texels retain the original. This is a conditional diffuse-color proposal, not
calibrated intrinsic decomposition or a measurement of metallic/roughness.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from reconstruction.camera import Camera, project
from reconstruction.mesh import TriangleMesh, load_glb_bytes
from reconstruction.raster import rasterize
from reconstruction.surface_transfer import _chunks, _pack, _rows


METHOD = 'source_bound_multiview_frame_lighting_v2'


@dataclass(frozen=True)
class FrameAppearancePolicy:
    atlas_resolution: int = 384
    maximum_tracks_per_material: int = 120_000
    minimum_shared_tracks: int = 32
    minimum_views: int = 3
    minimum_direction_separation_degrees: float = 10.
    minimum_highlight_linear: float = .065
    maximum_dark_consensus_codes: float = 16.
    maximum_bake_match_codes: float = 40.
    minimum_correction_ratio: float = .3
    correction_strength: float = .75
    maximum_materials: int = 32


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def _linear(rgb):
    x = np.asarray(rgb, float) / 255.
    return np.where(x <= .04045, x / 12.92, ((x+.055)/1.055)**2.4)


def _codes(rgb):
    x = np.clip(rgb, 0, 1)
    return 255*np.where(x <= .0031308, 12.92*x, 1.055*x**(1/2.4)-.055)


def _policy(policy):
    policy = policy or FrameAppearancePolicy()
    if not isinstance(policy, FrameAppearancePolicy):
        raise ValueError('Supply a FrameAppearancePolicy')
    if (type(policy.atlas_resolution) is not int or not 32 <= policy.atlas_resolution <= 1024
            or type(policy.maximum_tracks_per_material) is not int or not 32 <= policy.maximum_tracks_per_material <= 1_000_000
            or type(policy.minimum_shared_tracks) is not int or policy.minimum_shared_tracks < 3
            or type(policy.minimum_views) is not int or not 3 <= policy.minimum_views <= 12
            or type(policy.maximum_materials) is not int or not 1 <= policy.maximum_materials <= 64
            or not 0 < policy.minimum_direction_separation_degrees <= 90
            or not 0 < policy.minimum_highlight_linear < 1
            or not 0 < policy.maximum_dark_consensus_codes <= 64
            or not 0 < policy.maximum_bake_match_codes <= 64
            or not 0 < policy.minimum_correction_ratio <= 1
            or not 0 < policy.correction_strength <= 1
            or not np.isfinite(list(asdict(policy).values())).all()):
        raise ValueError('Invalid bounded frame appearance policy')
    return policy


def separate_frame_tracks(samples_linear, valid, baseline_linear, directions, *, policy=None):
    """Return per-track multiplicative correction and explicit consensus support.

    Input arrays are [view, track, RGB], [view, track], [track, RGB] and
    [view,3] or [view,track,3]. Articulated directions are transported into each
    surface's rest frame. Exposure is a bounded scalar from shared tracks.
    No spatial smoothing joins logos, material regions or unrelated UV islands.
    """
    policy = _policy(policy)
    samples, valid = np.asarray(samples_linear, float), np.asarray(valid, bool)
    baseline, directions = np.asarray(baseline_linear, float), np.asarray(directions, float)
    if (samples.ndim != 3 or samples.shape[2] != 3 or valid.shape != samples.shape[:2]
            or baseline.shape != samples.shape[1:] or directions.shape not in ((len(samples), 3), samples.shape)
            or not np.isfinite(samples[valid]).all() or not np.isfinite(baseline).all()
            or np.any(samples[valid] < 0) or np.any(samples[valid] > 1)
            or np.any(baseline < 0) or np.any(baseline > 1)
            or not np.isfinite(directions).all() or np.any(np.linalg.norm(directions, axis=-1) <= 0)):
        raise ValueError('Finite source-bound multiview RGB tracks required')
    n = samples.shape[1]
    gains = np.ones(len(samples))
    luminance = samples @ np.array([.2126, .7152, .0722])
    # The first view is a gauge, not a photometric calibration. Direct overlap
    # with that gauge is required; disconnected views retain gain one.
    exposure_supported = np.zeros(len(samples), bool)
    if len(samples):
        exposure_supported[0] = True
    for view in range(1, len(samples)):
        shared = valid[0] & valid[view] & (luminance[0] > .015) & (luminance[view] > .015)
        if shared.sum() >= policy.minimum_shared_tracks:
            ratios = np.log(luminance[view, shared] / luminance[0, shared])
            median = float(np.median(ratios))
            stable = np.abs(ratios-median) < .18
            if stable.sum() >= policy.minimum_shared_tracks:
                estimate = np.exp(np.median(ratios[stable]))
                if .7 <= estimate <= 1.4:
                    gains[view] = estimate
                    exposure_supported[view] = True
    adjusted = samples / gains[:, None, None]
    adjusted = np.clip(adjusted, 0, 1)
    luma = np.where(valid, adjusted @ np.array([.2126, .7152, .0722]), np.inf)
    order = np.argsort(luma, axis=0, kind='stable')
    count = valid.sum(axis=0)
    ratio = np.ones((n, 3))
    accepted = np.zeros(n, bool)
    low = np.zeros((n, 3)); high = np.zeros((n, 3))
    persistent = np.zeros(n, bool)
    if len(samples) >= policy.minimum_views:
        cols = np.arange(n)
        first, second = order[0], order[1]
        brightest = order[np.maximum(count-1, 0), cols]
        low = .5*(adjusted[first, cols] + adjusted[second, cols])
        high = adjusted[brightest, cols]
        agree = np.max(np.abs(_codes(adjusted[first, cols])-_codes(adjusted[second, cols])), axis=1)
        addition = high-low
        spread = np.max(_codes(high)-_codes(low), axis=1)
        normal = directions / np.linalg.norm(directions, axis=-1)[..., None]
        first_direction = normal[first] if normal.ndim == 2 else normal[first, cols]
        bright_direction = normal[brightest] if normal.ndim == 2 else normal[brightest, cols]
        angle = np.degrees(np.arccos(np.clip(np.sum(first_direction*bright_direction, axis=1), -1, 1)))
        bake_match = np.max(np.abs(_codes(baseline)-_codes(high)), axis=1)
        supported = count >= policy.minimum_views
        persistent = supported & (spread <= policy.maximum_dark_consensus_codes)
        accepted = (supported & (agree <= policy.maximum_dark_consensus_codes)
                    & (angle >= policy.minimum_direction_separation_degrees)
                    & (addition.min(axis=1) >= -.015)
                    & ((addition @ np.array([.2126, .7152, .0722])) >= policy.minimum_highlight_linear)
                    & (bake_match <= policy.maximum_bake_match_codes)
                    & exposure_supported[first] & exposure_supported[second] & exposure_supported[brightest])
        target = np.clip(low / np.maximum(high, .005), policy.minimum_correction_ratio, 1.)
        ratio[accepted] = 1 + policy.correction_strength*(target[accepted]-1)
    return {'ratio_rgb': ratio, 'corrected': accepted, 'persistent': persistent, 'support_views': count,
            'low_consensus_linear_rgb': low, 'bright_linear_rgb': high,
            'exposure_gain': gains, 'exposure_supported': exposure_supported,
            'summary': {'tracks': n, 'three_view_tracks': int((count >= policy.minimum_views).sum()),
                        'corrected_tracks': int(accepted.sum()), 'persistent_tracks': int(persistent.sum()),
                        'exposure_gain': gains.tolist(), 'exposure_supported': exposure_supported.tolist(),
                        'inference': 'Positive view-dependent illumination under fitted cameras; specular versus diffuse lighting is not identified.'}}


def _image(doc, binary, texture):
    row = doc['textures'][texture]
    if 'source' not in row or 'extensions' in row:
        raise ValueError('Only ordinary embedded base-color textures are supported')
    image = doc['images'][row['source']]
    if 'bufferView' not in image:
        raise ValueError('Frame textures must be embedded')
    view = doc['bufferViews'][image['bufferView']]
    raw = binary[view.get('byteOffset', 0):view.get('byteOffset', 0)+view['byteLength']]
    with Image.open(io.BytesIO(raw)) as loaded:
        if loaded.width*loaded.height > 4096**2:
            raise ValueError('Frame texture exceeds supported pixel capacity')
        return np.asarray(loaded.convert('RGBA')).copy()


def _attribute(doc, binary, index):
    acc, rows = _rows(doc, binary, index)
    if acc.get('normalized'):
        if rows.dtype.kind not in 'ui':
            raise ValueError('Unsupported normalized attribute type')
        rows = rows.astype(float) / np.iinfo(rows.dtype).max
    return rows.astype(float)


def _material_tracks(mesh, parts, doc, binary, material, pixels, policy):
    triangles, uv_triangles, factors, source_faces = [], [], [], []
    invalid_uv_faces = 0
    base_factor = np.asarray(doc['materials'][material].get('pbrMetallicRoughness', {}).get('baseColorFactor', [1,1,1,1])[:3])
    for part in parts:
        primitive = doc['meshes'][part['mesh_index']]['primitives'][part['primitive_index']]
        attrs = primitive['attributes']
        if 'TEXCOORD_0' not in attrs:
            raise ValueError('Frame texture requires explicit TEXCOORD_0')
        uv = _attribute(doc, binary, attrs['TEXCOORD_0'])
        if uv.shape != (part['vertex_count'], 2) or not np.isfinite(uv).all():
            raise ValueError('Malformed or nonfinite frame UVs are unsupported')
        start, count = part['face_start'], part['face_count']
        face = mesh.faces[start:start+count]
        local = face-part['vertex_start']
        inside = np.all((uv[local]>=0)&(uv[local]<=1),axis=(1,2))
        invalid_uv_faces += int((~inside).sum())
        face,local = face[inside],local[inside]
        colors = _attribute(doc, binary, attrs['COLOR_0'])[:, :3] if 'COLOR_0' in attrs else np.ones((len(uv),3))
        triangles.append(mesh.vertices[face]); uv_triangles.append(uv[local]); factors.append(colors[local]*base_factor)
        source_faces.append(np.arange(start, start+count)[inside])
    xyz, uv, factor, face_ids = map(np.concatenate, (triangles, uv_triangles, factors, source_faces))
    if not len(xyz):
        raise ValueError('No non-repeating in-range UV triangles have support')
    h, w = pixels.shape[:2]
    scale = min(1., policy.atlas_resolution/max(w,h)); aw, ah = max(2,round(w*scale)), max(2,round(h*scale))
    # Separate UV face depths allow a second hit to reveal overlapped atlas
    # islands. Noncoincident surfaces sharing texels are never corrected.
    uv_vertices = np.column_stack((uv[...,0].ravel()*(aw-1), -uv[...,1].ravel()*(ah-1), np.repeat(np.arange(len(xyz)),3)))
    uv_mesh = TriangleMesh(uv_vertices, np.arange(len(uv_vertices)).reshape(-1,3), [])
    camera = Camera(0,0,0,0,1,0,0)
    first = rasterize(uv_mesh, camera, (ah,aw))
    second = rasterize(uv_mesh, camera, (ah,aw), after_depth=first.depth)
    rows, cols = np.nonzero(first.mask)
    tri, bary = first.face_index[rows,cols], first.barycentric[rows,cols]
    world = np.einsum('ni,nij->nj', bary, xyz[tri])
    overlapping = second.mask[rows,cols]
    if overlapping.any():
        other = np.einsum('ni,nij->nj', second.barycentric[rows[overlapping],cols[overlapping]], xyz[second.face_index[rows[overlapping],cols[overlapping]]])
        bad = np.linalg.norm(other-world[overlapping],axis=1) > max(np.ptp(mesh.vertices,axis=0).max()*1e-6,1e-12)
        overlapping[np.flatnonzero(overlapping)] = bad
    keep = np.flatnonzero(~overlapping)
    if len(keep) > policy.maximum_tracks_per_material:
        keep = keep[np.linspace(0,len(keep)-1,policy.maximum_tracks_per_material).round().astype(int)]
    rows, cols, tri, bary, world = rows[keep], cols[keep], tri[keep], bary[keep], world[keep]
    factor = np.einsum('ni,nij->nj',bary,factor[tri])
    tx = np.clip(np.rint(cols/(aw-1)*(w-1)).astype(int),0,w-1)
    ty = np.clip(np.rint(rows/(ah-1)*(h-1)).astype(int),0,h-1)
    baseline = _linear(pixels[ty,tx,:3])*factor
    valid_factor = np.all(factor > .01,axis=1) & np.all(factor <= 1,axis=1) & (pixels[ty,tx,3] == 255)
    return {'world':world,'source_faces':face_ids[tri],'barycentric':bary,'source_triangles':xyz[tri],
            'rows':rows,'cols':cols,'shape':(ah,aw),
            'baseline':np.clip(baseline,0,1),'valid_factor':valid_factor,
            'overlapping_uv_texels_excluded':int(overlapping.sum()), 'occupied_uv_texels':int(first.mask.sum()),
            'out_of_range_uv_faces_excluded':invalid_uv_faces}


def _photo_rasters(mesh, regions, source_sha, pins, *, region_directory, support_output):
    result = []
    for photo in regions['photos']:
        projection = photo.get('candidate_projection', {})
        if projection.get('status') != 'candidate_conditioned_projection':
            continue
        if projection.get('candidate_sha256') != source_sha:
            raise ValueError('Frame camera belongs to a different candidate')
        path = Path(photo['source']).resolve(); raw = path.read_bytes()
        if _sha(raw) != photo['source_sha256']:
            raise ValueError('Frame photograph source hash changed')
        pins[str(path)] = _sha(raw)
        with Image.open(io.BytesIO(raw)) as image:
            if image.info.get('icc_profile') or image.getexif().get(274,1) != 1:
                raise ValueError('Frame observations require normalized sRGB photographs')
            pixels = np.asarray(image.convert('RGBA')).copy()
        if photo['image_size'] != [pixels.shape[1],pixels.shape[0]]:
            raise ValueError('Frame photograph grid changed')
        from qa.staged_frame_membership.frame_image_support import build_frame_image_support
        support = build_frame_image_support(photo,region_directory,support_output/photo['id'],pins=pins)
        size = projection['working_size']
        if len(size) != 2 or any(type(v) is not int or not 2 <= v <= 1024 for v in size):
            raise ValueError('Invalid frame camera working grid')
        norm = projection['normalization']; center, extent = np.asarray(norm['center']), norm['extent']
        if center.shape != (3,) or not np.isfinite(center).all() or not np.isfinite(extent) or extent <= 0:
            raise ValueError('Invalid frame camera normalization')
        posed_mesh, articulation = mesh, None
        if projection.get('view_scene') is not None:
            from reconstruction.view_scene import pose_mesh_from_contract
            contract = projection['view_scene']['contract']
            posed = pose_mesh_from_contract(mesh,contract,photo['id'],photo_sha256=photo['source_sha256'])
            posed_mesh, articulation = posed['mesh'], posed['report']
            # The contract validates this captured rest asset before posing;
            # keep it pinned until the complete stage has finished as well.
            pins[str(Path(contract['source_model']).resolve())] = contract['source_sha256']
        normalized = TriangleMesh((posed_mesh.vertices-center)/extent, posed_mesh.faces, posed_mesh.parts)
        camera = Camera(**projection['camera'])
        first = rasterize(normalized,camera,(size[1],size[0]))
        frame_faces = np.ones(len(mesh.faces),bool)
        for part in mesh.parts:
            if part['has_lens_appearance_extension'] or part.get('transmission',0)>0:
                frame_faces[part['face_start']:part['face_start']+part['face_count']] = False
        frame = first.mask & frame_faces[np.maximum(first.face_index,0)]
        interior = ndimage.binary_erosion(frame,iterations=1)
        yaw,pitch = np.radians([camera.yaw,camera.pitch])
        result.append({'id':photo['id'],'pixels':pixels,'camera':camera,'normalization':norm,
                       'first':first,'interior':interior,'image_support':support,'mesh':normalized,'size':size,'articulation':articulation,
                       'direction':np.array([np.cos(pitch)*np.sin(yaw),np.sin(pitch),np.cos(pitch)*np.cos(yaw)])})
    return result


def _triangle_basis(triangles):
    edge = triangles[:,1]-triangles[:,0]
    normal = np.cross(edge,triangles[:,2]-triangles[:,0])
    good = (np.linalg.norm(edge,axis=1)>1e-15)&(np.linalg.norm(normal,axis=1)>1e-15)
    edge /= np.maximum(np.linalg.norm(edge,axis=1),1e-30)[:,None]
    normal /= np.maximum(np.linalg.norm(normal,axis=1),1e-30)[:,None]
    basis = np.stack((edge,np.cross(normal,edge),normal),axis=2)
    basis[~good] = np.eye(3)
    return basis,good


def _observe_tracks(tracks, photos):
    points = tracks['world']; count = len(points)
    samples, valid = np.zeros((len(photos),count,3)), np.zeros((len(photos),count),bool)
    directions = np.zeros((len(photos),count,3))
    geometric_eligible = np.zeros((len(photos),count),bool)
    membership = np.zeros((len(photos),count),np.uint8)
    alpha_codes = np.zeros((len(photos),count),np.uint8)
    rest_basis,rest_good = _triangle_basis(tracks['source_triangles'])
    for vi, photo in enumerate(photos):
        posed_triangles = photo['mesh'].vertices[photo['mesh'].faces[tracks['source_faces']]]
        normalized = np.einsum('ni,nij->nj',tracks['barycentric'],posed_triangles)
        posed_basis,posed_good = _triangle_basis(posed_triangles)
        # Rest-basis * posed-basis transpose is the inverse rigid surface
        # rotation. Camera movement cancelled by a hinge is not new evidence.
        toward = photo['direction'][None,:]-photo['camera'].perspective*normalized
        toward /= np.linalg.norm(toward,axis=1)[:,None]
        directions[vi] = np.einsum('nij,nkj,nk->ni',rest_basis,posed_basis,toward)
        xy = project(normalized,photo['camera']); x,y = np.rint(xy).astype(int).T
        w,h = photo['size']; inbounds = (x>=0)&(y>=0)&(x<w)&(y<h)&tracks['valid_factor']&rest_good&posed_good
        ids = np.flatnonzero(inbounds); xx,yy = x[ids],y[ids]
        good = photo['interior'][yy,xx]
        ids,xx,yy = ids[good],xx[good],yy[good]
        if not len(ids):
            continue
        hit = photo['first'].surface_points(photo['mesh'],yy,xx)
        good = np.linalg.norm(hit-normalized[ids],axis=1) <= 1.5/photo['camera'].scale
        ids,xx,yy = ids[good],xx[good],yy[good]
        pixels = photo['pixels']; ph,pw = pixels.shape[:2]
        # Same native/working center convention as the optical observer.
        px = np.clip(np.rint((xy[ids,0]+.5)*pw/w-.5).astype(int),0,pw-1)
        py = np.clip(np.rint((xy[ids,1]+.5)*ph/h-.5).astype(int),0,ph-1)
        rgba = pixels[py,px]
        opaque = rgba[:,3] == 255
        samples[vi,ids] = _linear(rgba[:,:3])
        geometric_eligible[vi,ids] = True
        membership[vi,ids] = photo['image_support']['membership'][py,px]
        alpha_codes[vi,ids] = rgba[:,3]
        valid[vi,ids] = opaque & (membership[vi,ids] == 1)
    audit = {'geometric_eligible':geometric_eligible,'image_membership':membership,'source_alpha':alpha_codes,
        'photos':[{'photo_id':photo['id'],'geometrically_eligible':int(geometric_eligible[i].sum()),
                   'supported':int(valid[i].sum()),
                   'unknown_image_membership':int((geometric_eligible[i] & (membership[i] == 0)).sum()),
                   'authored_exterior':int((geometric_eligible[i] & (membership[i] == 2)).sum()),
                   'nonopaque_alpha':int((geometric_eligible[i] & (alpha_codes[i] != 255)).sum())}
                  for i,photo in enumerate(photos)]}
    return samples,valid,directions,audit


def _write_variant(source_doc, source_binary, corrections, *, roughness=None):
    doc, binary = deepcopy(source_doc), bytearray(source_binary)
    replacements = {}
    for material, rgba in corrections.items():
        original = doc['materials'][material]
        changed = deepcopy(original)
        pbr = changed.setdefault('pbrMetallicRoughness',{})
        if rgba is not None:
            stream = io.BytesIO(); Image.fromarray(rgba).save(stream,format='PNG')
            blob = stream.getvalue(); binary.extend(b'\0'*(-len(binary)%4))
            doc['bufferViews'].append({'buffer':0,'byteOffset':len(binary),'byteLength':len(blob)}); binary.extend(blob)
            doc.setdefault('images',[]).append({'bufferView':len(doc['bufferViews'])-1,'mimeType':'image/png'})
            source_texture = doc['textures'][pbr['baseColorTexture']['index']]
            doc['textures'].append({**deepcopy(source_texture),'source':len(doc['images'])-1})
            pbr['baseColorTexture']['index'] = len(doc['textures'])-1
        if roughness is not None:
            # A factor multiplying an unknown packed map is not the requested
            # scalar hypothesis. Preserve that map as the baseline alternative.
            pbr.pop('metallicRoughnessTexture',None)
            pbr['roughnessFactor'] = roughness
            pbr.setdefault('metallicFactor',0.)
        changed.setdefault('extras',{})['intrinsicFrameHypothesis'] = {'method':METHOD,'roughness':roughness,'accepted':False}
        doc['materials'].append(changed); replacements[material] = len(doc['materials'])-1
    for mesh in doc.get('meshes',[]):
        for primitive in mesh.get('primitives',[]):
            if primitive.get('material') in replacements:
                primitive['material'] = replacements[primitive['material']]
    doc['buffers'][0]['byteLength'] = len(binary)
    return _pack(doc,binary)


def run_intrinsic_frame_stage(model, export_receipt, region_report, output, *, policy=None):
    """Export baseline and bounded intrinsic-frame alternatives for automatic jobs.

    ``export_receipt`` is the selected optical candidate's receipt (path/dict),
    or None only when the region cameras bind the input GLB itself. The selected
    conservative correction retains existing PBR; roughness variants are
    proposals for the actual AR acceptance/selection gate, never measurements.
    """
    policy = _policy(policy); model,region_report,output = map(lambda p:Path(p).resolve(),(model,region_report,output))
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a fresh intrinsic-frame output directory')
    raw = model.read_bytes(); region_raw = region_report.read_bytes(); regions = json.loads(region_raw)
    pins = {str(model):_sha(raw),str(region_report):_sha(region_raw)}
    receipt = deepcopy(export_receipt) if isinstance(export_receipt,dict) else None
    if export_receipt is not None and receipt is None:
        path = Path(export_receipt).resolve(); data = path.read_bytes(); pins[str(path)] = _sha(data); receipt = json.loads(data)
    if receipt is not None:
        from reconstruction.optical_group_asset import read_optical_group_candidate
        loaded = read_optical_group_candidate(model,receipt,expected_sha256=_sha(raw))
        mesh = loaded['mesh']; source_sha = receipt['source_sha256']
    else:
        mesh = load_glb_bytes(raw); source_sha = _sha(raw)
    if regions['candidate_sha256'] != source_sha:
        raise ValueError('Frame region report and candidate source differ')
    doc,binary = _chunks(raw)
    photos = _photo_rasters(mesh,regions,source_sha,pins,region_directory=region_report.parent,
                            support_output=output/'photo-support')
    by_material, skipped = {}, []
    for part in mesh.parts:
        mi = part['material_index']
        if part['has_lens_appearance_extension'] or part.get('transmission',0)>0:
            continue
        if mi is None:
            skipped.append({'part':part['name'],'reason':'no_authored_material'}); continue
        by_material.setdefault(mi,[]).append(part)
    if len(by_material)>policy.maximum_materials:
        raise ValueError('Frame material capacity exceeded')
    output.mkdir(parents=True,exist_ok=True)
    corrections, material_reports, coverage_evidence = {}, [], []
    for mi,parts in sorted(by_material.items()):
        material = doc['materials'][mi]; pbr = material.get('pbrMetallicRoughness',{})
        info = pbr.get('baseColorTexture',{})
        if (material.get('alphaMode','OPAQUE') != 'OPAQUE' or not info or info.get('texCoord',0) != 0 or info.get('extensions')):
            skipped.append({'material_index':mi,'reason':'unsupported_translucent_untextured_or_transformed_texture'}); continue
        try:
            pixels = _image(doc,binary,info['index'])
            tracks = _material_tracks(mesh,parts,doc,binary,mi,pixels,policy)
        except ValueError as error:
            skipped.append({'material_index':mi,'reason':str(error)}); continue
        samples,valid,directions,membership_audit = _observe_tracks(tracks,photos)
        coverage_evidence.append({'source_faces':tracks['source_faces'],'barycentric':tracks['barycentric'],
                                  'valid':valid,'directions':directions})
        separated = separate_frame_tracks(samples,valid,tracks['baseline'],directions,policy=policy)
        ratio = np.ones((*tracks['shape'],3)); mask = np.zeros(tracks['shape'],bool)
        ratio[tracks['rows'],tracks['cols']] = separated['ratio_rgb']
        mask[tracks['rows'],tracks['cols']] = separated['corrected']
        full_rows = np.rint(np.linspace(0,tracks['shape'][0]-1,len(pixels))).astype(int)
        full_cols = np.rint(np.linspace(0,tracks['shape'][1]-1,pixels.shape[1])).astype(int)
        full_ratio = ratio[full_rows[:,None],full_cols[None,:]]
        corrected = pixels.copy(); corrected[:,:,:3] = np.rint(_codes(_linear(pixels[:,:,:3])*full_ratio)).astype(np.uint8)
        if separated['corrected'].any():
            corrections[mi] = corrected
        evidence_name = f'frame-material-{mi}-tracks.npz'
        np.savez_compressed(output/evidence_name,world=tracks['world'],source_faces=tracks['source_faces'],
            barycentric=tracks['barycentric'],rest_frame_view_directions=directions,
            atlas_xy=np.column_stack((tracks['cols'],tracks['rows'])),sample_linear_rgb=samples,valid=valid,
            geometric_eligible=membership_audit['geometric_eligible'],image_membership=membership_audit['image_membership'],
            source_alpha=membership_audit['source_alpha'],
            baseline_linear_rgb=tracks['baseline'],ratio_rgb=separated['ratio_rgb'],corrected=separated['corrected'],
            persistent=separated['persistent'],support_views=separated['support_views'])
        Image.fromarray((mask*255).astype(np.uint8)).save(output/f'frame-material-{mi}-correction-support.png')
        material_reports.append({'material_index':mi,'parts':[p['name'] for p in parts],**separated['summary'],
            'occupied_uv_texels':tracks['occupied_uv_texels'],'overlapping_uv_texels_excluded':tracks['overlapping_uv_texels_excluded'],
            'out_of_range_uv_faces_excluded':tracks['out_of_range_uv_faces_excluded'],
            'evidence':{'path':evidence_name,'sha256':_sha((output/evidence_name).read_bytes())},
            'image_membership_exclusions':membership_audit['photos'],
            'corrected_texture_pixels':int(np.count_nonzero(np.any(corrected[:,:,:3]!=pixels[:,:,:3],axis=2)))})
    candidates = []
    variants = [('baseline',None,None)]
    if corrections:
        variants += [('supported_lighting_removed',corrections,None),('frame_roughness_035',corrections,.35),('frame_roughness_070',corrections,.7)]
    baseline_mesh = load_glb_bytes(raw)
    for name,changes,roughness in variants:
        candidate_raw = raw if changes is None else _write_variant(doc,binary,changes,roughness=roughness)
        candidate_mesh = load_glb_bytes(candidate_raw)
        if not np.array_equal(candidate_mesh.vertices,baseline_mesh.vertices) or not np.array_equal(candidate_mesh.faces,baseline_mesh.faces):
            raise ValueError('Intrinsic frame export changed geometry')
        path = output/(name+'.glb'); path.write_bytes(candidate_raw)
        row = {'id':name,'path':str(path),'sha256':_sha(candidate_raw),'roughness_hypothesis':roughness,'geometry_unchanged':True}
        if receipt is not None:
            updated = deepcopy(receipt); updated.update(output=str(path),output_sha256=row['sha256'])
            # Declaration/groups remain exactly the original optical contract.
            updated['frame_appearance'] = {'method':METHOD,'source_sha256':_sha(raw),'candidate_id':name}
            from reconstruction.optical_group_asset import read_optical_group_candidate, _seal
            updated.pop('receipt_sha256',None)
            updated = _seal(updated)
            read_optical_group_candidate(path,updated,expected_sha256=row['sha256'])
            receipt_path = output/(name+'.export.json'); _json(receipt_path,updated)
            row['export'] = {'path':str(receipt_path),'sha256':_sha(receipt_path.read_bytes())}
        candidates.append(row)
    for path,digest in pins.items():
        if _sha(Path(path).read_bytes()) != digest:
            raise ValueError('Frame appearance source changed during the stage')
    from reconstruction.frame_surface_coverage import assess_frame_surface_coverage
    surface_coverage = assess_frame_surface_coverage(mesh,coverage_evidence)
    report = {'schema_version':1,'method':METHOD,'status':'frame_appearance_candidates_available' if corrections else 'insufficient_multiview_frame_support',
        'accepted':False,'quality_verdict':'unmeasured','input_sha256':pins,'policy':asdict(policy),
        'photo_ids':[p['id'] for p in photos],'materials':material_reports,'skipped':skipped,'candidates':candidates,
        'view_scenes':{p['id']:p['articulation'] for p in photos if p['articulation'] is not None},
        'surface_coverage':surface_coverage,
        'photo_image_support':{p['id']:p['image_support']['report'] for p in photos},
        'selected':candidates[1] if corrections else candidates[0],
        'selection_basis':'Conservative supported lighting correction with retained source PBR; bounded roughness alternatives require actual AR comparison.',
        'remaining_limitations':['Unsupported, single-view and ambiguous frame texture remains the original provider bake.',
            'Camera registration and unknown studio illumination can confound surface albedo with shading.',
            'Metalness and roughness are not physically identified from these photographs.',
            'UV seams, overlaps, translucent frames and untextured materials are not filled with inferred colors.']}
    _json(output/'report.json',report)
    return report
