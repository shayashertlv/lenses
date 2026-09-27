"""Preserve established arm support when a camera update adds little evidence."""
from dataclasses import asdict,dataclass

import numpy as np
from scipy import ndimage


@dataclass(frozen=True)
class CameraSupportPolicy:
    minimum_front_train_points: int = 12
    minimum_front_holdout_points: int = 4
    sampling_floor_px: float = .5
    sampling_width_fraction: float = .005


def front_camera_split(targets_xy, *, policy=CameraSupportPolicy()):
    """One frozen image-grid split, reused for every camera hypothesis."""
    xy=np.asarray(targets_xy,float)
    if xy.ndim!=2 or xy.shape[1]!=2 or not np.isfinite(xy).all():
        raise ValueError('Camera witnesses require finite image coordinates')
    holdout=((xy[:,0].astype(int)//5)+(xy[:,1].astype(int)//5))%3==0
    supported=(np.count_nonzero(~holdout)>=policy.minimum_front_train_points and
               np.count_nonzero(holdout)>=policy.minimum_front_holdout_points)
    return {'train':~holdout,'holdout':holdout,'supported':bool(supported),
            'report':{'method':'fixed_image_grid_spatial_split_v1','cell_pixels':5,
                      'train_points':int((~holdout).sum()),'holdout_points':int(holdout.sum()),
                      'supported':bool(supported)}}


def measure_front_camera_witness(front_mask,targets_xy,holdout):
    mask=np.asarray(front_mask,bool);targets=np.asarray(targets_xy,float);holdout=np.asarray(holdout,bool)
    if targets.shape!=(len(holdout),2) or mask.ndim!=2 or not np.isfinite(targets).all():
        raise ValueError('Invalid frozen front witness arrays')
    boundary=mask&~ndimage.binary_erosion(mask)
    if not boundary.any() or not holdout.any():
        return {'measured':False,'points':int(holdout.sum()),'mean_px':None,'p95_px':None}
    distance=ndimage.distance_transform_edt(~boundary)
    values=ndimage.map_coordinates(distance,targets[holdout].T[::-1],order=1,mode='constant',cval=float(max(mask.shape)))
    return {'measured':True,'points':len(values),'mean_px':float(values.mean()),
            'p95_px':float(np.quantile(values,.95))}


def _supported_arms(pose_report):
    sides=[]
    for side,row in pose_report.items():
        quality=row.get('absolute_correspondence_quality' if row.get('retained') else 'baseline_correspondence_quality',{})
        if row.get('status')!='unobserved' and quality.get('supported') is True:
            sides.append(side)
    return sorted(sides)


def choose_camera_with_pose_support(coarse_front,refined_front,coarse_arms,refined_arms,
                                    reference_width_px,*,policy=CameraSupportPolicy()):
    """Camera estimation stays front-only; this is a photographic veto afterward.

    Count supported arms rather than enforcing per-side visibility: a modest
    camera change can legitimately hide one arm and reveal the other.
    """
    if not np.isfinite(reference_width_px) or reference_width_px<=0:
        raise ValueError('Camera support needs a positive projected object width')
    before,after=_supported_arms(coarse_arms),_supported_arms(refined_arms)
    tolerance=max(policy.sampling_floor_px,reference_width_px*policy.sampling_width_fraction)
    available=all(row.get('measured') is True and row.get('points',0)>=policy.minimum_front_holdout_points
                  and all(isinstance(row.get(k),(int,float,np.number)) and np.isfinite(row[k]) for k in ('mean_px','p95_px'))
                  for row in (coarse_front,refined_front))
    mean_gain=coarse_front['mean_px']-refined_front['mean_px'] if available else None
    p95_gain=coarse_front['p95_px']-refined_front['p95_px'] if available else None
    lost=len(after)<len(before)
    little_gain=available and mean_gain<=tolerance and p95_gain<=tolerance
    front_regressed=available and (mean_gain < -tolerance or p95_gain < -tolerance)
    keep_coarse=not available or front_regressed or (lost and little_gain)
    return {'method':'front_camera_arm_support_nonregression_v1','policy':asdict(policy),
        'selected':'whole_scene' if keep_coarse else 'front_refined',
        'reason':'insufficient_front_witnesses' if not available else
                 'front_witness_regressed_beyond_sampling_uncertainty' if front_regressed else
                 'front_gain_below_sampling_uncertainty_and_absolute_arm_support_lost' if keep_coarse else
                 'front_camera_update_retained',
        'coarse_front_witness':coarse_front,'refined_front_witness':refined_front,
        'coarse_supported_arms':before,'refined_supported_arms':after,
        'supported_arm_count_decreased':lost,'front_witness_regressed':bool(front_regressed),
        'front_mean_gain_px':mean_gain,'front_p95_gain_px':p95_gain,
        'sampling_tolerance_px':tolerance,'reference_projected_width_px':float(reference_width_px),
        'independent_validation':False,
        'scope':'Internal frozen spatial witnesses; whole-scene initialization already used the photograph. No ground-truth camera or product acceptance.'}
