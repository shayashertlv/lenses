"""Articulated output, immutable evidence and private-stage provenance integration."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import shutil
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from glasses3d.agent.temple_lab.pose_basis import pose_metadata
from glasses3d.g3d_host import cameras, compare as C, fit_views as FV, fitmesh, hinge_axes, pose, raster
from glasses3d.tests.agent.test_rigid_pose import captured


def test_shared_axis_recognition_preserves_existing_nonfinite_geometry_refusal(tmp_path,monkeypatch):
    from glasses3d.agent.temple_lab import rigid_pose
    path,digest = captured(tmp_path)
    mesh = fitmesh.load_fit_mesh(path)
    vertices = mesh.V.copy()
    vertices[0,0] = np.nan
    monkeypatch.setattr(hinge_axes.fitmesh,'load_fit_mesh',lambda *_:replace(mesh,V=vertices))
    with pytest.raises(ValueError,match='no finite geometry'):
        rigid_pose.derive_axes(path,digest)


def make_basis(tmp_path, monkeypatch, *, input_matches=False):
    path, sha = captured(tmp_path)
    mesh = fitmesh.load_fit_mesh(path)
    roles = pose.part_roles(path)
    axes = hinge_axes.derive_axes(path)['hinge_axis_xz_mm']
    theta, observed = {'+1':7., '-1':-3.}, {'+1':True, '-1':False}
    posed = pose.posed(mesh, {k:{'theta_deg':v,'axis_xz_mm':axes[k]} for k,v in theta.items()}, roles)
    cam = raster.view_camera(mesh.frame, 35., 20., .75, (200,300))
    fg = (mesh if input_matches else posed).scene().render(cam, (200,300))['mask']
    photo = tmp_path/'photo.png'
    Image.fromarray(np.where(fg[...,None], [40,50,60], [240,240,240]).astype(np.uint8)).save(photo)
    monkeypatch.setattr(FV.M, 'matte', lambda *_,**kw:fg)
    monkeypatch.setattr(FV.cameras.ViewFit, 'pitch_twin', lambda *_:{'flag':None})
    metrics = cameras.native_metrics(mesh.scene(), cam, fg, cameras.matte_roi(fg))
    best = {'camera':cam, 'view':'angled', 'metrics':metrics, 'sign':{}, 'loss':.5, 'common_loss':.5,
            'refine':{'origin':'test'}, 'fit':SimpleNamespace(evals=1)}
    monkeypatch.setattr(FV.cameras, 'fit_best', lambda *_,**kw:(best,[best]))
    diagnostic = {'schema':'g3d.opening_fit/1', 'per_side':{
        k:{'theta_deg':v,'observed':observed[k],'visible_pixels_final':42 if observed[k] else 0,
           'identifiability':'locally_sensitive' if observed[k] else 'unobserved'} for k,v in theta.items()}}
    monkeypatch.setattr(FV.hinge_fit, 'refine', lambda *_,**kw:{'camera':cam,'mesh':posed,
        'theta_deg':theta,'theta_observed':observed,'opening_fit':deepcopy(diagnostic)})
    directory = tmp_path/'cameras'/'basis-01'
    directory.mkdir(parents=True)
    row = FV.fit_image(path, photo, 'angled', overlay_path=directory/'overlay_photo.png')
    assert row['iou'] == 1.
    expected_theta = dict.fromkeys(theta,0.) if input_matches else theta
    expected_observed = dict.fromkeys(theta,False) if input_matches else observed
    assert row['theta_deg'] == expected_theta and row['theta_observed'] == expected_observed
    assert row['all_candidates'][0]['theta_deg'] == expected_theta
    assert row['all_candidates'][0]['theta_observed'] == expected_observed
    assert row['all_candidates'][0]['opening_selection'] == ('input_pose' if input_matches else 'articulated')
    assert 'rigid_fit' not in row
    assert row['pose_provenance']['source_model_sha256'] == sha
    doc = {'model':row.pop('model'), 'images':{'photo.png':row}}
    C.write_json(directory/'cameras.json', doc)
    basis = C.freeze_basis(tmp_path, directory, {'photo.png':photo}, reason='test',call_id='fit',source='test')
    return basis, row, mesh, posed


def test_articulated_metrics_freeze_and_stage_keep_unobserved_provenance(tmp_path, monkeypatch):
    basis, row, mesh, posed = make_basis(tmp_path, monkeypatch)
    reference = basis.refs['photo']
    for key in ('theta_deg','theta_observed','hinge_axis_xz_mm','pose_provenance','opening_fit'):
        assert reference[key] == row[key]
    assert pose_metadata(reference) == row['pose_provenance']
    result = pose.posed(mesh, basis.pose('photo'), pose.part_roles(tmp_path/'current.glb'))
    np.testing.assert_array_equal(result.V, posed.V)
    assert not np.array_equal(mesh.V, posed.V)


def test_rejected_opening_reports_selected_input_visibility_and_unobserved_sensitivity(tmp_path, monkeypatch):
    basis,row,mesh,_ = make_basis(tmp_path, monkeypatch, input_matches=True)
    cam = C.Camera.from_dict(row['camera'])
    fid = mesh.scene().render(cam,(200,300))['face_id']
    tags = pose.side_of_faces(mesh,pose.part_roles(tmp_path/'current.glb'))
    for key, side in row['opening_fit']['per_side'].items():
        count = int(np.count_nonzero((fid>=0) & (tags[np.maximum(fid,0)]==int(key))))
        assert side['visible_pixels_final'] == count
        assert not side['observed'] and side['identifiability'] == 'unobserved'
        assert side['theta_deg'] == 0. and side['proposed_deg'] in (7.,-3.)
        assert side['status'] == 'input_pose_selected_on_common_loss'


@pytest.mark.parametrize('field', ['theta_deg','theta_observed','hinge_axis_xz_mm','pose_provenance','opening_fit'])
def test_frozen_pose_and_diagnostics_cannot_be_changed_independently(tmp_path, monkeypatch, field):
    basis, _, _, _ = make_basis(tmp_path, monkeypatch)
    metadata = deepcopy(basis.meta)
    metadata['refs']['photo'][field] = {'tampered':True}
    C.write_json(basis.dir/'basis.json', metadata)
    with pytest.raises(ValueError, match='pose|lineage'):
        C.Basis(basis.dir,tmp_path)


def test_coordinated_pose_tamper_still_fails_bound_source_hash(tmp_path, monkeypatch):
    basis, _, _, _ = make_basis(tmp_path, monkeypatch)
    raw = json.loads((basis.dir/'cameras.json').read_bytes())
    raw['images']['photo.png']['theta_deg']['+1'] += 1.
    C.write_json(basis.dir/'cameras.json',raw)
    metadata = deepcopy(basis.meta)
    metadata['refs']['photo']['theta_deg']['+1'] += 1.
    metadata['cameras_json_sha256'] = hashlib.sha256((basis.dir/'cameras.json').read_bytes()).hexdigest()
    C.write_json(basis.dir/'basis.json',metadata)
    with pytest.raises(ValueError, match='source hashes|provenance'):
        C.Basis(basis.dir,tmp_path)


def test_private_stage_copy_retains_articulated_lineage_and_uncertainty(tmp_path, monkeypatch):
    from glasses3d.agent.temple_lab import pose_basis
    basis,row,_,_ = make_basis(tmp_path,monkeypatch)
    stage = tmp_path/'stage'
    (stage/'inputs/images').mkdir(parents=True)
    shutil.copyfile(tmp_path/'photo.png',stage/'inputs/images/photo.png')
    model = tmp_path/'current.glb'
    copied = pose_basis.build(basis,stage,[{'role':'target','basis_ref':'photo','path':'images/photo.png'}],
        call_id='stage',captured_glb=model,captured_sha256=hashlib.sha256(model.read_bytes()).hexdigest())
    for field in ('theta_deg','theta_observed','hinge_axis_xz_mm','pose_provenance','opening_fit'):
        assert copied.refs['photo'][field] == row[field]
    assert copied.pose('photo') == basis.pose('photo')
