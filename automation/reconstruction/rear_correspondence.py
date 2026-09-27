"""Observed straight rear-object continuations and bounded image registration.

Templates contain only measured pixels outside lens apertures. Generated mesh
textures never supply rear radiance. Straight, stable appearance is a tested
correspondence hypothesis, and missing support produces no optical constraint.
"""
from __future__ import annotations

import hashlib
import json

import cv2
import numpy as np
from scipy import ndimage

from .appearance_anchors import estimate_transmission_from_contrast
from .lens_appearance import srgb_to_linear


def register_rear_template(target,template,mask,*,template_uncertainty=.01,maximum_shift_pixels=2):
    """Search a fixed translation window and retain registration ambiguity."""
    if type(maximum_shift_pixels)is not int or not 0<=maximum_shift_pixels<=4:
        raise ValueError('Rear registration shift bound must be 0..4 pixels')
    target,template,mask=np.asarray(target,float),np.asarray(template,float),np.asarray(mask)
    if target.shape!=template.shape or target.ndim!=3 or target.shape[-1]!=3 or mask.shape!=target.shape[:2] or mask.dtype!=bool:
        raise ValueError('Rear registration requires aligned image patches and a boolean support mask')
    # Support is fixed for every displacement. No shift may improve the fit by
    # dropping difficult border pixels from its scored patch.
    support=mask.copy();r=maximum_shift_pixels
    if r:
        support[:r]=False;support[-r:]=False;support[:,:r]=False;support[:,-r:]=False
    results=[]
    for dy in range(-r,r+1):
        for dx in range(-r,r+1):
            shifted=ndimage.shift(template,(dy,dx,0),order=1,mode='nearest',prefilter=False)
            fitted=estimate_transmission_from_contrast(target,support,shifted,template_uncertainty=template_uncertainty)
            if fitted['status']=='conditional_supported':
                results.append({'shift_xy':[dx,dy],**fitted})
    if not results:
        return {'status':'unsupported','reason':'no_supported_registration_in_bounded_window','registration_candidates':0}
    results.sort(key=lambda r:(sum(r['residual_rms_rgb']),sum(abs(v) for v in r['shift_xy']),r['shift_xy']))
    best=results[0];threshold=sum(best['residual_rms_rgb'])+.006
    plausible=[r for r in results if sum(r['residual_rms_rgb'])<=threshold]
    intervals=np.asarray([r['transmission_interval_rgb'] for r in plausible])
    return {**best,'transmission_interval_rgb':np.column_stack((intervals[:,:,0].min(axis=0),intervals[:,:,1].max(axis=0))).tolist(),
        'registration_candidates':len(results),'plausible_shifts':[r['shift_xy'] for r in plausible],
        'maximum_shift_pixels':maximum_shift_pixels,'evaluation_support_pixels':int(support.sum()),
        'registration_support_frozen':True}


def find_rear_template_correspondences(rgb,aperture_mask,*,reflection_mask=None,opaque_mask=None,maximum_pairs=6):
    """Propose measured profiles continuing through a lens boundary.

    A line is insufficient by itself: outside profiles must be stable, an
    inside profile must support bounded registration, and downstream geometry
    must independently associate the inside region with rear-object rays.
    """
    rgb=np.asarray(rgb);aperture=np.asarray(aperture_mask)
    if rgb.dtype!=np.uint8 or rgb.ndim!=3 or rgb.shape[-1]!=3 or aperture.dtype!=bool or aperture.shape!=rgb.shape[:2]:
        raise ValueError('Rear correspondence needs source RGB codes and a boolean aperture grid')
    if type(maximum_pairs)is not int or not 1<=maximum_pairs<=12:
        raise ValueError('Rear correspondence budget must be 1..12 pairs')
    reflection=np.zeros_like(aperture) if reflection_mask is None else np.asarray(reflection_mask,bool)
    if reflection.shape!=aperture.shape:
        raise ValueError('Reflection exclusion grid differs')
    opaque=np.ones_like(aperture) if opaque_mask is None else np.asarray(opaque_mask)
    if opaque.dtype!=bool or opaque.shape!=aperture.shape:
        raise ValueError('Opaque source eligibility grid differs')
    h,w=aperture.shape;scale=min(1.,1000/max(h,w))
    small=cv2.resize(rgb,(round(w*scale),round(h*scale)),interpolation=cv2.INTER_AREA)
    edges=cv2.Canny(cv2.cvtColor(small,cv2.COLOR_RGB2GRAY),40,110)
    lines=cv2.HoughLinesP(edges,1,np.pi/180,threshold=25,minLineLength=35,maxLineGap=10)
    if lines is None:
        return []
    lines=sorted((line[0].astype(float)/scale for line in lines),key=lambda p:-np.linalg.norm(p[2:]-p[:2]))[:80]
    linear=srgb_to_linear(rgb/255.)
    outside=ndimage.distance_transform_edt(~aperture)>6/scale
    inside=ndimage.distance_transform_edt(aperture)>6/scale
    results=[];centers=[]
    for line in lines:
        origin=(line[:2]+line[2:])/2;direction=line[2:]-line[:2];direction/=np.linalg.norm(direction)
        normal=np.array([-direction[1],direction[0]])
        t=np.arange(-max(h,w),max(h,w),2/scale)
        n=np.arange(-12,13)/scale
        xy=origin+t[:,None,None]*direction+n[None,:,None]*normal
        valid=((xy[:,:,0]>=1)&(xy[:,:,0]<w-1)&(xy[:,:,1]>=1)&(xy[:,:,1]<h-1)).all(axis=1)
        # Linear sampling must not mix even partly transparent RGB into either
        # side of the observed contrast. Alpha is geometry, not a known light.
        valid &= (ndimage.map_coordinates(opaque.astype(float),[xy[:,:,1],xy[:,:,0]],order=1,mode='constant',cval=0)>=1-1e-12).all(axis=1)
        centerxy=np.rint(origin+t[:,None]*direction).astype(int)
        centerxy[:,0]=np.clip(centerxy[:,0],0,w-1);centerxy[:,1]=np.clip(centerxy[:,1],0,h-1)
        out=valid & outside[centerxy[:,1],centerxy[:,0]]
        # Every reference pixel, not just its center line, must be independently
        # observed outside the lens; the strip can straddle a curved aperture.
        out &= (ndimage.map_coordinates(aperture.astype(float),[xy[:,:,1],xy[:,:,0]],order=0,mode='constant',cval=1)==0).all(axis=1)
        inn=valid & inside[centerxy[:,1],centerxy[:,0]] & ~reflection[centerxy[:,1],centerxy[:,0]]
        if out.sum()<16 or inn.sum()<16:
            continue
        # The observed reference must be adjacent to the same proposed line,
        # not an arbitrary visually similar object elsewhere in the picture.
        out_indices=np.flatnonzero(out);in_indices=np.flatnonzero(inn)
        nearest=min(out_indices,key=lambda i:min(abs(in_indices-i)))
        reference=out_indices[np.argsort(abs(out_indices-nearest))[:32]]
        target_indices=in_indices[np.argsort(abs(in_indices-nearest))[:32]]
        if abs(t[target_indices].mean()-t[reference].mean())>220/scale:
            continue
        target_xy=xy[target_indices];reference_xy=xy[reference]
        center=target_xy.mean(axis=(0,1))
        if any(np.linalg.norm(center-c)<35/scale for c in centers):
            continue
        def sample(points):
            return np.stack([ndimage.map_coordinates(linear[:,:,ch],[points[:,:,1],points[:,:,0]],order=1,mode='nearest') for ch in range(3)],axis=-1)
        observed=sample(reference_xy);profile=np.median(observed,axis=0)
        uncertainty=np.median(np.abs(observed-profile),axis=(0,1))*1.4826+.005
        if np.max(uncertainty)>.06 or np.min(np.std(profile,axis=0))<.04:
            continue
        template=np.broadcast_to(profile,(len(target_indices),*profile.shape)).copy()
        target=sample(target_xy)
        selected=np.stack([ndimage.map_coordinates(inside.astype(float),[target_xy[:,:,1],target_xy[:,:,0]],order=0),
                           1-ndimage.map_coordinates(reflection.astype(float),[target_xy[:,:,1],target_xy[:,:,0]],order=0)]).all(axis=0)
        fitted=register_rear_template(target,template,selected,template_uncertainty=uncertainty)
        if fitted['status']!='conditional_supported':
            continue
        clipped=bool(np.any(observed>=1-1e-6))
        if clipped:
            # A saturated unoccluded backdrop underestimates its true radiance;
            # retain an upper bound, not a falsely precise transmission point.
            interval=np.asarray(fitted['transmission_interval_rgb']);interval[:,0]=0
            fitted['transmission_interval_rgb']=interval.tolist()
        row={'method':'observed_straight_rear_continuation_v1','status':'conditional_correspondence',
             'target_center_xy':center.tolist(),'direction_xy':direction.tolist(),'normal_xy':normal.tolist(),
             'target_extent_along_normal':[[float(t[target_indices].min()),float(t[target_indices].max())],[float(n.min()),float(n.max())]],
             'line_origin_xy':origin.tolist(),'target_bbox_xyxy':[*target_xy.min(axis=(0,1)).tolist(),*target_xy.max(axis=(0,1)).tolist()],
             'reference_bbox_xyxy':[*reference_xy.min(axis=(0,1)).tolist(),*reference_xy.max(axis=(0,1)).tolist()],
             'template_uncertainty_linear_rgb':uncertainty.tolist(),'reference_clipping_censors_transmission':clipped,
             'transmission':fitted,'template_source':'observed_outside_aperture_pixels_only',
             'assumptions':['same_straight_rear_object_profile_continues_through_lens','local_transmission_constant','reflected_light_smooth_over_patch'],
             'independent_geometry_association_required':True}
        row['correspondence_sha256']=hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()
        results.append(row);centers.append(center)
        if len(results)>=maximum_pairs:
            break
    return results


def collect_rear_transmission_anchors(semantic,grounding,groups):
    manifest={r['photo_id']:r for r in semantic['image_manifest']}
    result={'method':'observed_rear_correspondence_stage_v1','accepted':False,'photos':[],'anchors_by_group':{},
        'limitations':['Same-object continuity is a geometric/image hypothesis, not product identity.',
                      'No generated texture supplies the reference template. Clipped reference radiance yields censored intervals.']}
    for photo,rgba in grounding['images'].items():
        aperture=grounding['aperture_masks'][photo]
        reflection=np.zeros_like(aperture)
        from .image_appearance_evidence import _box_mask
        for region in semantic['regions']:
            if region['photo_id']==photo and region['kind']=='reflection':
                reflection|=_box_mask(aperture.shape,region['bbox_xyxy_normalized'])
        proposals=find_rear_template_correspondences(rgba[:,:,:3],aperture,reflection_mask=reflection,opaque_mask=rgba[:,:,3]==255)
        ledger={'photo_id':photo,'source_sha256':manifest[photo]['sha256'],'correspondences':proposals}
        result['photos'].append(ledger)
        for match in proposals:
            associated=[]
            for group in groups:
                samples={}
                for row in group['observations']:
                    if row['photo_id']!=photo or row['source_sha256']!=manifest[photo]['sha256']:
                        continue
                    xy=np.asarray(row['xy']);local=xy-np.asarray(match['line_origin_xy'])
                    u=local@np.asarray(match['direction_xy']);v=local@np.asarray(match['normal_xy'])
                    bounds=match['target_extent_along_normal']
                    selected=(u>=bounds[0][0])&(u<=bounds[0][1])&(v>=bounds[1][0])&(v<=bounds[1][1])
                    for index in np.flatnonzero(selected):
                        if not np.isfinite([row['intrinsic_v'][index],row['incidence_degrees'][index]]).all():
                            continue
                        samples[tuple(xy[index])]=(float(row.get('rear_weight',np.zeros(len(xy)))[index]),float(row['intrinsic_v'][index]),float(row['incidence_degrees'][index]))
                if len(samples)<3:
                    continue
                values=np.asarray(list(samples.values()))
                rear=values[values[:,0]>.5]
                if len(rear)<3 or len(rear)/len(values)<.1 or np.ptp(rear[:,1])>.2 or np.ptp(rear[:,2])>15 or np.max(rear[:,2])>=85:
                    continue
                associated.append((group['surface_binding'],rear))
            if len(associated)!=1:
                match['geometry_association_status']='unresolved_or_not_rear_content';continue
            binding,values=associated[0];gid=binding['material_group_id']
            anchor={'photo_id':photo,'source_sha256':manifest[photo]['sha256'],'prepared_glb_sha256':binding['prepared_glb_sha256'],
                'v':float(values[:,1].mean()),'incidence_degrees':float(values[:,2].mean()),
                'v_range':[float(values[:,1].min()),float(values[:,1].max())],
                'incidence_range':[float(values[:,2].min()),float(values[:,2].max())],
                'transmission_interval_rgb':match['transmission']['transmission_interval_rgb'],'sigma':.05,'weight':.1,
                'correspondence_sha256':match['correspondence_sha256'],'source_sample_count':len(values),
                'status':'conditional_observed_contrast_interval','template_source':'observed_outside_aperture_pixels_only'}
            result['anchors_by_group'].setdefault(gid,[]).append(anchor)
            match['geometry_association_status']='unique_projected_rear_object_support';match['group_id']=gid
    return result
