"""Bound stage observations, separate from predictions of the captured model.

Every stage gets native photo-matte traces and opening-aware visible-arm
residuals, including when its parent never called photo_read. Image-plane
profiles and tip-line intersections are conditional observations in one photo;
they are never silently paired across photos or converted into 3D measurements.
Only reverified parent photo_read picks supply cross-photo coordinates.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from glasses3d.g3d_host import cameras, compare as C, fitmesh, pose
from glasses3d.g3d_host.camera import project_mm
from . import readings as R
from .lab import read, sha, write_json_atomic

SCHEMA = "g3d.arm_evidence/1"
TRACE_SD_PX = .7              # same shared trace-offset floor as photo_read
POOR_P90_PX = 5.              # informational warning only; never a fit/finish gate
COLORS = {1:(210,35,165), -1:(25,135,220)}
NOTE = ("Green traces are observations from the frozen photo matte; coloured arm lines are model predictions "
        "at this photo's frozen camera and opening. Profiles use a model-selected image window, not a matched "
        "3D section. Tip crossings are extrapolated 2D line intersections, not sharp physical vertices or "
        "cross-photo correspondences. Errors are conditional on the matte, line choice, camera and opening; "
        "shape, pose, occlusion, correspondence and matte errors can all contribute to residuals.")


def contours(mask):
    """Closed native-pixel chains; no simplification or resampling of photo edges."""
    rows, _ = cv2.findContours(np.asarray(mask, np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    return [r[:,0,:].astype(float) for r in rows if len(r) >= 3]


def trace_crossing(chain, index):
    """A scale-checked intersection of two local contour traces, or ambiguity.

    The corner location selects local traces only. No model point enters either
    line fit. Two support lengths must agree; rounded/short/parallel traces do
    not acquire an invented precise tip.
    """
    q = np.asarray(chain, float)
    out = {"status":"ambiguous", "meaning":"extrapolated 2D contour-line intersection only",
           "trace_sd_px":TRACE_SD_PX, "uv_px":None}
    if len(q) < 90:
        return {**out,"reason":"contour too short for two independent support lengths"}
    fits = []
    for a,b in ((4,24),(8,40)):
        indices = np.rint(np.linspace(a,b,6)).astype(int)
        edges = [q[(index + sign*indices) % len(q)] for sign in (-1,1)]
        try:
            lines = [R.edge_line(e,TRACE_SD_PX) for e in edges]
            uv,cov,angle = R.crossing_pixel(*lines)
        except ValueError as exc:
            return {**out,"reason":str(exc),"edges_px":[e.tolist() for e in edges]}
        fits.append({"uv_px":uv.tolist(),"cov_px2":cov.tolist(),"angle_deg":angle,
                     "edges_px":[e.tolist() for e in edges],"rms_px":[e['rms'] for e in lines]})
    shift = float(np.linalg.norm(np.array(fits[0]['uv_px'])-fits[1]['uv_px']))
    chosen = fits[0]
    out.update(supports=fits, support_shift_px=shift, edges_px=chosen['edges_px'])
    if min(f['angle_deg'] for f in fits) < 15 or max(max(f['rms_px']) for f in fits) > 1.:
        return {**out,"reason":"curved or shallow trace support; a unique tip crossing is not identified"}
    if shift > 2. or np.linalg.norm(np.array(chosen['uv_px'])-q[index]) > 12.:
        return {**out,"reason":"crossing depends on the trace support or lies far from its contour corner"}
    return {**out,**chosen,"status":"extrapolated_candidate", "reason":None,
            "sd_uv_px":np.sqrt(np.diag(chosen['cov_px2'])).tolist(),
            "uncertainty":"conditional line-fit covariance with 0.7px shared trace error; excludes feature identity"}


def image_profile(chains, predicted_uv, visible_mask):
    """Actual image-edge samples in a model-selected arm window, with all crossings.

    Retains multiple edge crossings; never chooses the closest edge as the same
    feature in another photo. The image axis is a model prediction, not depth.
    """
    q = np.asarray(predicted_uv, float)
    center = q.mean(0)
    _,_,vt = np.linalg.svd(q-center,full_matrices=False)
    along = vt[0]
    if along[0] < 0:
        along = -along
    across = np.array([-along[1],along[0]])
    low,high = q.min(0),q.max(0)
    pad = max(24., .08*float(np.ptp(q,axis=0).max()))
    points = np.vstack(chains) if chains else np.empty((0,2))
    selected = points[((points >= low-pad)&(points <= high+pad)).all(1)]
    a = (q-center)@along
    stations=[]
    for coordinate in np.linspace(float(a.min()),float(a.max()),13):
        keep = np.abs((selected-center)@along-coordinate) <= 1.
        samples=selected[keep]
        n=(samples-center)@across
        order=np.argsort(n)
        groups=[]
        for j in order:
            if not groups or abs(float(n[j]-n[groups[-1][-1]])) > 3.:
                groups.append([])
            groups[-1].append(int(j))
        stations.append({"along_px":coordinate,"photo_edge_groups_px":[samples[g].tolist() for g in groups],
                         "assignment":"unassigned photo edges; multiple crossings may be other parts"})
    return {"coordinate_space":"native photo pixels; no 3D correspondence",
            "window_selection":"captured model arm projection, padded; full photo traces also retained",
            "window_xyxy":[*(low-pad).tolist(),*(high+pad).tolist()],
            "axis_origin_px":center.tolist(),"axis_along":along.tolist(),"axis_across":across.tolist(),
            "visible_model_pixels":int(visible_mask.sum()),"stations":stations}


def parent_readings(parent_root, basis):
    """Recalculate saved picks under the exact current parent basis; stale data is named.

    The saved report's model/camera/photo hashes and full numerical record must
    match. Bound observations are not promoted into editable stage-node readings
    because the stage chart and side-selection semantics differ.
    """
    from glasses3d.agent.photo_read import read_point
    root=Path(parent_root).resolve()
    expected={"basis":basis.id,"basis_sha256":sha(basis.dir/'basis.json'),
              "cameras_sha256":sha(basis.dir/'cameras.json'),
              "model_sha256":(basis.meta.get('model') or {}).get('sha256'),
              "photo_sha256":{k:r['photo_sha256'] for k,r in basis.refs.items()}}
    accepted,excluded=[],[]
    for path in sorted((root/'work/photo_read').glob('*/report.json')):
        rel=path.relative_to(root).as_posix()
        if not path.resolve().is_relative_to(root):
            excluded.append({"report":rel,"reason":"source escaped parent job"}); continue
        try:
            document=read(path)
            mismatch=[k for k,v in expected.items() if document.get(k)!=v]
            if document.get('schema')!='g3d.photo_read/1' or mismatch:
                excluded.append({"report":rel,"reason":"different frozen inputs", "fields":mismatch}); continue
            for rec in document.get('readings',[]):
                if rec.get('status')!='ok':
                    excluded.append({"report":rel,"name":rec.get('name'),"reason":"source reading refused"}); continue
                actual=read_point(basis,rec['name'],rec['picks'],kind=rec['kind'],temple_side=rec.get('temple_side'))
                if actual!=rec:
                    excluded.append({"report":rel,"name":rec.get('name'),"reason":"saved reading differs from recomputed picks"}); continue
                accepted.append({"source_report":rel,"source_sha256":sha(path),"record":deepcopy(rec)})
        except (OSError,ValueError,KeyError,TypeError,np.linalg.LinAlgError) as exc:
            excluded.append({"report":rel,"reason":f"reading could not be reverified: {exc}"})
    return {"verified":accepted,"excluded":excluded,"binding":expected,
            "note":"Verified parent picks remain observations; no stage node or geometry is changed."}


def _tip(chains, projection, visible, shape):
    """Locate a conditional tip-window observation, without a cross-photo match."""
    q=np.asarray(projection,float)
    expected=q.mean(0)
    u,v=np.rint(expected).astype(int)
    h,w=shape
    result={"model_expected_uv_px":expected.tolist(),"status":"ambiguous","uv_px":None}
    if not (1<=u<w-1 and 1<=v<h-1):
        return {**result,"reason":"predicted tip is clipped or outside the photo"}
    if not visible[max(0,v-2):v+3,max(0,u-2):u+3].any():
        return {**result,"status":"occluded","reason":"predicted tip has no native first-hit arm pixels"}
    radius=max(24.,float(np.ptp(q,axis=0).max())+12.)
    candidates=[]
    for n,chain in enumerate(chains):
        distances=np.linalg.norm(chain-expected,axis=1)
        k=int(np.argmin(distances))
        if distances[k]<=radius:
            candidates.append((float(distances[k]),n,k))
    if not candidates:
        return {**result,"reason":"no photo contour in the model-selected tip window"}
    _,n,k=min(candidates)
    return {**result,**trace_crossing(chains[n],k),"photo_contour_index":n,"photo_contour_vertex":k,
            "selection":"nearest contour in a model-selected tip window; identity remains conditional"}


def photo_evidence(mesh, roles, basis, rid):
    rec=basis.refs[rid]
    cam=basis.camera(rid)
    posed=pose.posed(mesh,basis.pose(rid),roles)
    drawing=posed.scene().render(cam,basis.shape(rid))
    sides=pose.side_of_faces(mesh,roles)
    hit=drawing['face_id']
    mask=drawing['mask']
    photo=basis.matte(rid)
    chains=contours(photo)
    distance=cameras._dist_to(cameras._edge(photo))
    # Only full-scene exterior arm edges are comparable with the photo silhouette;
    # an internal part-occlusion boundary is not evidence of an outside shape.
    outside=cameras._edge(mask)
    arms={}
    overlays=[]
    for side in (1,-1):
        key=f'{side:+d}'
        visible=(hit>=0)&(sides[np.maximum(hit,0)]==side)
        edge=outside&visible
        values=distance[edge]
        ids=pose.temple_parts(mesh,side,roles)
        vertices=np.unique(mesh.F[np.isin(mesh.part,ids)])
        details=(rec.get('opening_fit') or {}).get('per_side',{}).get(key,{})
        row={"theta_deg":(rec.get('theta_deg') or {}).get(key),
             "theta_observed":(rec.get('theta_observed') or {}).get(key,False),
             "identifiability":details.get('identifiability','not reported'),
             "opening_evidence":deepcopy(details),"visible_pixels":int(visible.sum()),
             "outline_fit":{"direction":"visible model exterior arm edge to nearest photo-matte edge",
                            "meaning":"one-photo residual; no feature correspondence or camera-conflict verdict",
                            "samples":len(values),"mean_px":float(values.mean()) if len(values) else None,
                            "p90_px":float(np.percentile(values,90)) if len(values) else None},
             "warnings":[]}
        if not len(values):
            row['warnings'].append('No visible exterior arm edge; its outline fit is unobserved.')
        elif row['outline_fit']['p90_px']>POOR_P90_PX:
            row['warnings'].append(f"Poor arm silhouette agreement: p90 exceeds {POOR_P90_PX:g} native pixels; "
                                   "inspect shape, pose, occlusion and matte. This is information, not a gate.")
        if not row['theta_observed']:
            row['warnings'].append('Opening is not observed; its retained value is not a measured angle.')
        if len(vertices):
            projection=project_mm(posed.V[vertices],cam,basis.frame)
            row['image_plane_profile']=image_profile(chains,projection,visible)
            # The authored model identifies only the tip-window location. The
            # observations inside it come solely from native photo contours.
            original=mesh.V[vertices]
            tip_ids=vertices[original[:,2]<=original[:,2].min()+2.]
            tip_uv=project_mm(posed.V[tip_ids],cam,basis.frame)
            row['tip_crossing']=_tip(chains,tip_uv,visible,basis.shape(rid))
            row['model_projection']={"tip_vertices_uv_px":tip_uv.tolist(),
                                     "visible_contours_px":[c.tolist() for c in contours(visible)]}
        arms[key]=row
        overlays.append((visible,side))
    return {"photo":rid,"photo_sha256":rec['photo_sha256'],"matte_sha256":rec['matte_sha256'],
            "shape":list(basis.shape(rid)),"view":rec.get('view_kind'),"camera":deepcopy(rec['camera']),
            "photo_observations":{"source":"frozen photo matte", "contours_px":[c.tolist() for c in chains]},
            "arms":arms},overlays


def _overlay(basis,rid,row,overlays,path):
    rgb=basis.photo(rid).copy()
    rgb[cameras._edge(basis.matte(rid))]=(30,185,80)
    for visible,side in overlays:
        rgb[cameras._edge(visible)]=COLORS[side]
    im=Image.fromarray(rgb)
    draw=ImageDraw.Draw(im)
    for key,arm in row['arms'].items():
        tip=arm.get('tip_crossing') or {}
        for edge in tip.get('edges_px',[]):
            draw.line([tuple(p) for p in edge],fill=(240,95,20),width=2)
        if tip.get('uv_px') is not None:
            x,y=tip['uv_px']
            draw.ellipse((x-4,y-4,x+4,y+4),outline=(240,95,20),width=2)
    out=Image.new('RGB',(im.width,im.height+64),(255,255,255))
    out.paste(im,(0,64))
    d=ImageDraw.Draw(out)
    d.text((8,5),f"{rid}: photo observations green; model +X magenta / -X blue; tip trace candidates orange",fill=(20,20,20))
    d.text((8,24),"Native pixels below header: v_photo = v_image - 64. Profile/crossing identity is conditional.",fill=(20,20,20))
    d.text((8,43),"No automatic cross-photo correspondence or 3D tip measurement. Read the photo and uncertainty.",fill=(20,20,20))
    out.save(path)
    return list(out.size)


def prepare(inputs, basis, glb, photos, *, parent_root=None, parent_basis=None):
    """Create immutable JSON/overlay assets before opening the private lab."""
    inputs=Path(inputs)
    folder=inputs/'arm-evidence'
    folder.mkdir(parents=True,exist_ok=False)
    mesh=fitmesh.load_fit_mesh(glb,skip=basis.skip,frame=basis.frame)
    roles=pose.part_roles(glb)
    rows,images,assets=[],[],[]
    for i,p in enumerate(photos):
        rid=p['basis_ref']
        if rid not in basis.refs or p['role']=='reference':
            continue
        row,overlays=photo_evidence(mesh,roles,basis,rid)
        row['photo_id']=p['id']
        path=folder/f'photo-{i:03d}.png'
        size=_overlay(basis,rid,row,overlays,path)
        rel=path.relative_to(inputs).as_posix()
        digest=sha(path)
        images.append({"path":rel,"sha256":digest,"size_px":size,"photo":p['id'],"view":p['view'],
                       "label":f"ARM OBSERVATION EVIDENCE {p['id']} (photo traces and model predictions; native pixels below64px header)"})
        assets.append({"path":rel,"sha256":digest})
        rows.append(row)
    parent=parent_readings(parent_root,parent_basis) if parent_root is not None else {"verified":[],"excluded":[]}
    report={"schema":SCHEMA,"basis":basis.id,"basis_sha256":sha(basis.dir/'basis.json'),
            "model_sha256":sha(glb),"note":NOTE,"photos":rows,"parent_readings":parent,
            "images":images,"paid_calls":0}
    path=folder/'report.json'
    write_json_atomic(path,report)
    assets.append({"path":path.relative_to(inputs).as_posix(),"sha256":sha(path)})
    return report,assets


def brief_text(report):
    lines=["", "Bound arm observation evidence:",NOTE]
    for photo in report['photos']:
        for side,row in photo['arms'].items():
            fit=row['outline_fit']
            residual='unobserved' if fit['p90_px'] is None else f"mean {fit['mean_px']:.2f}, p90 {fit['p90_px']:.2f} native px"
            tip=row.get('tip_crossing') or {}
            uv=tip.get('uv_px')
            lines.append(f"{photo['photo_id']} arm {side}: opening {row['theta_deg']} deg, observed {row['theta_observed']}; "
                         f"{row['identifiability']}; exterior contour residual {residual}. "
                         f"Tip crossing {tip.get('status','unavailable')}"+
                         (f" at native pixel ({uv[0]:.2f},{uv[1]:.2f}), conditional sd {tip.get('sd_uv_px')} px." if uv else
                          f": {tip.get('reason','no projected arm')}."))
            lines.extend(row['warnings'])
            profile=row.get('image_plane_profile')
            if profile:
                lines.append(f"{photo['photo_id']} arm {side} plan/hook image profile: axis origin "
                             f"{np.round(profile['axis_origin_px'],2).tolist()}, along "
                             f"{np.round(profile['axis_along'],5).tolist()}, across "
                             f"{np.round(profile['axis_across'],5).tolist()}; window "
                             f"{np.round(profile['window_xyxy'],1).tolist()} native px. "
                             "Stations below are [along_px, photo-edge groups]; each group is "
                             "[mean_u,mean_v,min_u,max_u,min_v,max_v]. All intersections are retained; "
                             "groups have no cross-photo identity or measured depth.")
                stations=[]
                for station in profile['stations']:
                    groups=[]
                    for group in station['photo_edge_groups_px']:
                        points=np.array(group,float)
                        groups.append(np.round([*points.mean(0),points[:,0].min(),points[:,0].max(),
                                                points[:,1].min(),points[:,1].max()],2).tolist())
                    stations.append([round(station['along_px'],2),groups])
                lines.append(json.dumps(stations,separators=(',',':')))
    parent=report['parent_readings']
    lines.append(f"Verified parent photo_read observations: {len(parent['verified'])}; excluded stale/refused/changed observations: {len(parent['excluded'])}.")
    for item in parent['verified']:
        r=item['record']
        lines.append(f"Reading {r['name']} ({', '.join(r['photos'])}; temple_side {r.get('temple_side')}): "
                     f"neutral xyz {r['xyz_mm']} mm, conditional sd {r['sd_xyz_mm']} mm; "
                     f"residual pixels {r['residual_px']}; consistent_with_stated_error={r['consistent_with_stated_error']}. "
                     "These residuals alone do not establish an irreconcilable camera conflict.")
        lines.extend(str(w) for w in r.get('warnings',[]))
    lines.append("The startup evidence images show the native traces. The bound arm-evidence/report.json preserves every trace, image-plane profile and source hash. "
                 "No evidence reading changes the model or removes any tool capability.")
    return '\n'.join(lines)+'\n'
