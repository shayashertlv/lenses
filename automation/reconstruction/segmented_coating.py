"""Photo hue coverage and visible-normal quantiles for coating hypotheses.

Coverage is evidence in the front product photograph. Mapping that coverage to
the generated surface's front-view incidence distribution is an explicit
uncalibrated-camera approximation, not a recovered physical coating law.
"""
from pathlib import Path
import hashlib
import io

import numpy as np
from PIL import Image
from scipy import ndimage

from .lens_appearance import srgb_to_linear


def derive_coating_coverage(prepared, sampling, measurements, aperture_engine):
    result={'method':'photo_hue_coverage_normal_quantiles_v2','status':'unsupported','accepted':False,
        'camera_assumption':'Source front is approximately aligned to canonical +Z; orthographic visible-normal coverage is a conditional approximation.',
        'physical_angle_identification':False,'reasons':[]}
    rows=[r for r in measurements['observations'] if r['status']=='conditional_supported' and
        r['role']=='colored_coating_reflection' and r['view']=='front']
    near=[r for r in rows if r['incidence_class']=='near_normal']
    side=[r for r in rows if r['incidence_class']=='oblique']
    if not near or not side or aperture_engine is None:
        result['reasons']=['front_two_hue_regions_or_aperture_support_missing'];return result
    identifiers={r['photo_id'] for r in near+side}
    if len(identifiers)!=1:
        result['reasons']=['front_source_is_not_unique'];return result
    source=next(r for r in sampling['image_manifest'] if r['photo_id'] in identifiers)
    raw=Path(source['local_path']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=source['sha256']:raise ValueError('Coverage photo changed')
    rgba=np.asarray(Image.open(io.BytesIO(raw)).convert('RGBA'))
    proposal=aperture_engine.propose(rgba[:,:,:3])
    masks=[]
    for variant in proposal['variants'].values():
        masks.append(np.asarray(variant['mask'])&np.asarray(variant['known_domain']))
    mask=ndimage.binary_erosion(np.logical_and.reduce(masks)&(rgba[:,:,3]==255),iterations=5)
    linear=srgb_to_linear(rgba[:,:,:3].astype(float)/255.)
    maximum=linear.max(axis=2);minimum=linear.min(axis=2)
    mask &= (maximum>.025)&((maximum-minimum)/np.maximum(maximum,1e-9)>.15)
    if mask.sum()<128:
        result['reasons']=['insufficient_chromatic_aperture_pixels'];return result
    def chromatic_component(value):
        value=np.asarray(value,float)
        value=value-value.min(axis=-1,keepdims=True)
        return value/np.maximum(value.sum(axis=-1,keepdims=True),1e-9)
    normal=chromatic_component(np.median([r['linear_rgb_median'] for r in near],axis=0))
    oblique=chromatic_component(np.median([r['linear_rgb_median'] for r in side],axis=0))
    direction=oblique-normal
    if np.dot(direction,direction)<.005:
        result['reasons']=['source_hue_classes_not_distinguishable'];return result
    observed=linear[mask];chromaticity=chromatic_component(observed)
    position=(chromaticity-normal)@direction/np.dot(direction,direction)
    near_fraction=float(np.mean(position<.35));oblique_fraction=float(np.mean(position>.65));transition=1-near_fraction-oblique_fraction
    if min(near_fraction,oblique_fraction)<.03:
        result['reasons']=['both_source_hue_classes_need_visible_support'];return result
    import open3d as o3d
    vertices=[];faces=[];normals=[];offset=0
    for group in prepared.values():
        for primitive in group['primitives']:
            vertices.append(primitive['positions']);normals.append(primitive['normals'])
            faces.append(primitive['indices']+offset);offset+=len(primitive['positions'])
    vertices=np.concatenate(vertices);normals=np.concatenate(normals);faces=np.concatenate(faces)
    lo,hi=vertices.min(axis=0),vertices.max(axis=0);span=hi-lo
    if min(span[:2])<=0:raise ValueError('Coating geometry lacks a front aperture')
    width,height=720,360
    xx,yy=np.meshgrid(np.linspace(lo[0],hi[0],width),np.linspace(lo[1],hi[1],height))
    origins=np.stack((xx,yy,np.full_like(xx,hi[2]+max(span)*3)),axis=2)
    rays=np.concatenate((origins,np.broadcast_to([0,0,-1],origins.shape)),axis=2).astype(np.float32)
    scene=o3d.t.geometry.RaycastingScene(nthreads=2)
    scene.add_triangles(o3d.core.Tensor(vertices.astype(np.float32)),o3d.core.Tensor(faces.astype(np.uint32)))
    hits=scene.cast_rays(o3d.core.Tensor(rays));valid=np.isfinite(hits['t_hit'].numpy())
    face_id=hits['primitive_ids'].numpy()[valid];uv=hits['primitive_uvs'].numpy()[valid]
    bary=np.column_stack((1-uv.sum(axis=1),uv));direction=np.sum(normals[faces[face_id]]*bary[:,:,None],axis=1)
    length=np.linalg.norm(direction,axis=1)
    if np.any(length<=0):raise ValueError('Visible coating normal is invalid')
    angles=np.degrees(np.arccos(np.clip(np.abs(direction[:,2])/length,0,1)))
    # Preserve a broad central class instead of forcing a linear blend from 0.
    quantiles=[near_fraction*.75,near_fraction+transition*.5,min(.98,near_fraction+transition+oblique_fraction*.25)]
    knots=np.quantile(angles,quantiles)
    if not 0<knots[0]<knots[2]<89:
        result['reasons']=['visible_incidence_quantiles_do_not_support_distinct_knots'];return result
    positions=np.sum(vertices[faces[face_id]]*bary[:,:,None],axis=1)
    camera_hypotheses=[]
    for distance_widths in (1.5,3.):
        # Close cameras change incidence across a curved shield. Preserve this
        # ambiguity as a bounded alternative, rather than silently treating a
        # product-photo orthographic approximation as the runtime camera.
        camera=(lo+hi)/2
        camera[2]=hi[2]+span[0]*distance_widths
        view=camera-positions
        cosine=np.abs(np.sum(direction*view,axis=1))/(length*np.linalg.norm(view,axis=1))
        perspective_angles=np.degrees(np.arccos(np.clip(cosine,0,1)))
        perspective_knots=np.quantile(perspective_angles,quantiles)
        if 0<perspective_knots[0]<perspective_knots[2]<89:
            camera_hypotheses.append({'camera_model':'finite_distance_on_front_orthographic_support',
                'distance_in_lens_widths':distance_widths,'camera_position':camera.tolist(),
                'proposed_plateau_end_degrees':float(perspective_knots[0]),
                'proposed_oblique_peak_degrees':float(perspective_knots[2]),
                'assumption':'Uncalibrated finite-distance camera alternative; projected support/occlusion remains orthographic.'})
    result.update(status='conditional_coverage_hypothesis',source_photo_id=source['photo_id'],source_sha256=source['sha256'],
        source_chromatic_pixels=int(mask.sum()),source_near_hue_fraction=near_fraction,source_oblique_hue_fraction=oblique_fraction,
        source_transition_fraction=transition,source_hue_chromaticities={'near':normal.tolist(),'oblique':oblique.tolist()},
        chromaticity_policy='Subtract neutral minimum channel, then normalize the remaining RGB sum; pale/bright versions of a hue retain its class.',
        source_region_ids=[r['region_id'] for r in near+side],visible_surface_pixels=int(valid.sum()),
        visible_incidence_quantiles_degrees={str(q):float(np.quantile(angles,q)) for q in (.05,.25,.5,.75,.95)},
        proposed_plateau_end_degrees=float(knots[0]),proposed_oblique_peak_degrees=float(knots[2]),
        camera_hypotheses=camera_hypotheses,
        sampled_quantiles=quantiles,renderer_camera_fit=False,
        limitations=['Whole optical surface projection omits frame occlusion and unknown source perspective.',
            'Color classification uses source chromaticity; transmitted color, rear objects and lighting remain confounded.',
            'Source hue coverage constrains an empirical candidate, not an independently calibrated angular coating.'])
    return result
