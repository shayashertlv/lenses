"""Native observations reach the specialist without inventing correspondence."""
from copy import deepcopy
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from glasses3d.agent.temple_lab import arm_evidence as E, cli, package
from glasses3d.agent.temple_lab.lab import read,sha,write_json_atomic
from glasses3d.g3d_host import pose
from glasses3d.g3d_host.camera import project_mm
from glasses3d.tests.agent.test_temple_stage_package import source_job
from glasses3d.tests.agent.test_temple_lab_cli import PrepLab


def test_native_contours_are_actual_pixel_observations():
    matte=np.zeros((100,140),np.uint8)
    cv2.fillPoly(matte,[np.array([[10,20],[110,30],[75,80],[25,70]],np.int32)],1)
    chains=E.contours(matte)
    pixels=np.vstack(chains).astype(int)
    assert matte[pixels[:,1],pixels[:,0]].all()
    profile=E.image_profile(chains,np.array([[10,20],[110,30],[75,80],[25,70]]),matte)
    observed={tuple(p) for p in pixels.tolist()}
    for station in profile['stations']:
        for group in station['photo_edge_groups_px']:
            assert all(tuple(p) in observed for p in group)
    assert 'no 3D correspondence' in profile['coordinate_space']


def test_tip_crossing_is_conditional_and_curved_trace_is_not_precise_corner():
    mask=np.zeros((200,200),np.uint8)
    cv2.fillPoly(mask,[np.array([[20,180],[180,180],[100,20]],np.int32)],1)
    chain=max(E.contours(mask),key=len)
    i=int(np.argmin(chain[:,1]))
    crossing=E.trace_crossing(chain,i)
    assert crossing['status']=='extrapolated_candidate'
    np.testing.assert_allclose(crossing['uv_px'],[100,20],atol=1.)
    assert np.array(crossing['cov_px2']).shape==(2,2)
    mask[:]=0
    cv2.circle(mask,(100,100),65,1,-1)
    chain=max(E.contours(mask),key=len)
    assert E.trace_crossing(chain,int(np.argmin(chain[:,1])))['status']=='ambiguous'


def test_automatic_package_evidence_reaches_first_input_without_parent_readings(tmp_path):
    scene,basis,_=source_job(tmp_path)
    out=package.prepare(scene,tmp_path/'stage','automatic',lab_class=PrepLab)
    root=tmp_path/'stage'
    manifest=read(root/'inputs/lab.json')
    report=read(root/'inputs/arm-evidence/report.json')
    assert report['parent_readings']['verified']==[]
    assert len(report['photos'])==len(report['images'])==2
    assert out['status']=='prepared'
    assert any('Poor arm silhouette' in warning for photo in report['photos']
               for arm in photo['arms'].values() for warning in arm['warnings'])
    refs=cli.references(SimpleNamespace(root=root,manifest=manifest))
    assert len(refs)==4
    assert [p.path for p in refs[:2]]==[p['path'] for p in manifest['photos']]
    assert all(p.label.startswith('ARM OBSERVATION EVIDENCE') for p in refs[2:])
    assets={r['path']:r['sha256'] for r in manifest['assets']}
    for p in refs[2:]:
        assert assets[p.path]==sha(root/'inputs'/p.path)
    assert out['photo_ids']==['left view','custom.front'] # evidence is not a new photo identity
    brief=(root/'inputs/brief.txt').read_text(encoding='utf-8')
    assert 'plan/hook image profile' in brief and 'All intersections are retained' in brief
    assert 'cannot be matched together' not in brief
    path=root/'inputs'/refs[-1].path
    path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='changed'):
        cli.references(SimpleNamespace(root=root,manifest=manifest))


def _parent_reading(scene,basis):
    from glasses3d.agent.photo_read import measure
    xyz=np.array([[65.,3.,-70.]])
    picks=[]
    for rid in basis.refs:
        th,axis=pose.parse_pose(basis.pose(rid))[1]
        point=pose.rot_about_hinge(xyz,1,th,axis)
        uv=project_mm(point,basis.camera(rid),basis.frame)[0]
        picks.append({'photo':rid,'uv':uv.tolist(),'edges':None,'sd_px':1.})
    return measure(scene.root,'parent-read',[{'name':'Tip','kind':'corner','temple_side':1,'picks':picks}])


@pytest.mark.parametrize('damage',[None,'basis','result'])
def test_parent_observations_are_recomputed_and_stale_or_modified_records_named(tmp_path,damage):
    scene,basis,_=source_job(tmp_path)
    result=_parent_reading(scene,basis)
    assert result['status']=='ok'
    path=scene.root/result['report_path']
    if damage:
        document=read(path)
        if damage=='basis':
            document['basis_sha256']='old'
        else:
            document['readings'][0]['xyz_mm'][0]+=1
        write_json_atomic(path,document)
    transferred=E.parent_readings(scene.root,basis)
    assert len(transferred['verified'])==(0 if damage else 1)
    assert len(transferred['excluded'])==(1 if damage else 0)
    if not damage:
        assert transferred['verified'][0]['record']==result['readings'][0]
        assert transferred['verified'][0]['source_sha256']==sha(path)


def test_unreadable_parent_report_is_an_exclusion_not_missing_evidence(tmp_path,monkeypatch):
    scene,basis,_=source_job(tmp_path)
    result=_parent_reading(scene,basis)
    old=E.read
    def damaged(path):
        if str(path).endswith('report.json'):
            raise OSError('unreadable fixture')
        return old(path)
    monkeypatch.setattr(E,'read',damaged)
    transferred=E.parent_readings(scene.root,basis)
    assert not transferred['verified']
    assert 'unreadable fixture' in transferred['excluded'][0]['reason']


def test_occluded_tip_has_no_invented_coordinate():
    chains=[np.array([[2.,2.],[15,2],[15,15],[2,15]])]
    hidden=np.zeros((30,30),bool)
    result=E._tip(chains,np.array([[10,10],[11,11]]),hidden,hidden,(30,30))
    assert result['status']=='occluded' and result['uv_px'] is None
