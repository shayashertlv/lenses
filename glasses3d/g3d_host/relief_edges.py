"""Coherent edge candidates near a 2D guide, without choosing their meaning.

Unlike snapping each cross-section to its strongest peak, retain competing
signed ridges. This is useful for a narrow top surface whose two boundaries
alternate in contrast. Nothing here changes frozen crease tracing or gates.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage
from scipy.signal import find_peaks


def trace_edge_candidates(lightness, p0, p1, *, half=12., valid_mask=None):
    """Return straight ridge candidates and their support in native pixels.

    Both gradient signs are retained. Deterministic line proposals use peaks
    near eight uniformly spaced cross-sections; each section votes at most once.
    At least 40% of the sampled sections must agree within one pixel. This is
    evidence extraction, not an automatic topology or edge-identity decision.
    """
    L=np.asarray(lightness,float)
    if L.ndim!=2:raise ValueError('lightness must be a 2D array')
    p0,p1=np.asarray(p0,float),np.asarray(p1,float)
    length=float(np.linalg.norm(p1-p0))
    if not np.isfinite(length) or length<12:raise ValueError('guide must span at least 12 pixels')
    if not np.isfinite(half) or half<=1:raise ValueError('half must exceed one pixel')
    tangent=(p1-p0)/length;normal=np.array([-tangent[1],tangent[0]])
    valid=np.isfinite(L)
    if valid_mask is not None:
        mask=np.asarray(valid_mask,bool)
        if mask.shape!=L.shape:raise ValueError('valid mask must match lightness')
        valid &= mask
    support=ndimage.binary_erosion(valid,structure=np.ones((13,13),bool)).astype(float)
    gy,gx=np.gradient(ndimage.gaussian_filter(np.where(valid,L,0.),1.))
    gradient=gx*normal[0]+gy*normal[1]
    offsets=np.arange(-half,half+.01,.25)
    stations=np.arange(max(3.,.1*length),min(length-3.,.9*length),2.)
    peaks=[];supported=0
    for index,s in enumerate(stations):
        xy=p0+s*tangent+offsets[:,None]*normal
        if not np.all(ndimage.map_coordinates(support,[xy[:,1],xy[:,0]],order=1,mode='constant',cval=0.)>=1.-1e-12):
            continue
        supported+=1
        profile=ndimage.map_coordinates(gradient,[xy[:,1],xy[:,0]],order=1,mode='constant',cval=0.)
        for sign in (-1,1):
            ids,_=find_peaks(sign*profile,height=.3,prominence=.3)
            for j in ids:
                vals=sign*profile[j-1:j+2]
                denominator=vals[0]-2*vals[1]+vals[2]
                shift=0. if denominator==0 else float(np.clip(.5*(vals[0]-vals[2])/denominator,-1.,1.))
                peaks.append([index,s,offsets[j]+shift*.25,sign,float(sign*profile[j])])
    if supported<8:return {'candidates':[],'profiles':supported,'peak_count':len(peaks)}
    all_peaks=np.asarray(peaks,float).reshape(-1,5);candidates=[]
    minimum=max(8,int(np.ceil(.4*supported)))
    for sign in (-1,1):
        rows=all_peaks[all_peaks[:,3]==sign].copy()
        for _ in range(4):
            if len(rows)<minimum:break
            sections=np.unique(rows[:,0])
            proposal_sections=sections[np.unique(np.linspace(0,len(sections)-1,min(8,len(sections))).astype(int))]
            proposals=rows[np.isin(rows[:,0],proposal_sections)]
            best=None
            for i,a in enumerate(proposals):
                for b in proposals[i+1:]:
                    if abs(b[1]-a[1])<.35*length:continue
                    slope=(b[2]-a[2])/(b[1]-a[1]);intercept=a[2]-slope*a[1]
                    if abs(slope)>.15:continue
                    distances=np.abs(rows[:,2]-(intercept+slope*rows[:,1]))/np.sqrt(1+slope*slope)
                    good=np.flatnonzero(distances<=1.)
                    # A section with several nearby peaks still gets one vote.
                    unique=[]
                    for section in np.unique(rows[good,0]):
                        ids=good[rows[good,0]==section]
                        unique.append(ids[np.argmin(distances[ids])])
                    if len(unique)<minimum:continue
                    unique=np.asarray(unique,int)
                    score=(len(unique),-float(np.mean(distances[unique]**2)))
                    if best is None or score>best[0]:best=(score,unique)
            if best is None:break
            selected=best[1]
            for _ in range(3):
                used=rows[selected]
                slope,intercept=np.polyfit(used[:,1],used[:,2],1)
                residual=rows[:,2]-(intercept+slope*rows[:,1])
                choices=[]
                for section in np.unique(rows[:,0]):
                    ids=np.flatnonzero((rows[:,0]==section)&(np.abs(residual)<=1.))
                    if len(ids):choices.append(ids[np.argmin(np.abs(residual[ids]))])
                selected=np.asarray(choices,int)
            used=rows[selected]
            if len(used)<minimum:break
            s=np.array([used[:,1].min(),used[:,1].max()]);off=intercept+slope*s
            ends=p0+s[:,None]*tangent+off[:,None]*normal
            candidates.append({'p0':ends[0].tolist(),'p1':ends[1].tolist(),'sign':sign,
                'support':len(used),'support_fraction':len(used)/supported,
                'rms_px':float(np.sqrt(np.mean((used[:,2]-intercept-slope*used[:,1])**2)/(1+slope*slope))),
                'guide_mid_offset_px':float(intercept+slope*length/2),
                'guide_angle_delta_deg':float(np.degrees(np.arctan(slope))),
                'mean_gradient':float(np.mean(used[:,4]))})
            rows=rows[np.abs(rows[:,2]-intercept-slope*rows[:,1])>1.5]
    candidates.sort(key=lambda r:(-r['support'],r['rms_px'],abs(r['guide_mid_offset_px'])))
    return {'candidates':candidates,'profiles':supported,'peak_count':len(all_peaks)}
