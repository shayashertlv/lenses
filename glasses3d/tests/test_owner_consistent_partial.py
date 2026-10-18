"""Partial backward-normal repair follows the face, preserving authored normals."""
import numpy as np
import pytest

from glasses3d.g3d import owner_health as H
from glasses3d.tests.test_owner_health import box


@pytest.mark.parametrize('strength', [.25, .5, 1.])
def test_partial_backward_normal_repair_turns_in_the_face_normal_plane(strength):
    obj, op = box()
    old = np.array([.8, 0., .6])  # The bottom face points along negative Z.
    obj['corner_normals'][0] = old.tolist()
    op.update(kind='consistent_normals', params={'strength': strength})
    after, report = H.plan_object(obj, op)
    normal = np.array([0., 0., -1.])
    tangent = normal-old*(normal@old)
    tangent /= np.linalg.norm(tangent)
    expected = -old if strength == 1 else old*np.cos(np.pi*strength)+tangent*np.sin(np.pi*strength)
    np.testing.assert_allclose(after['corner_normals'][0], expected, atol=2e-15, rtol=0)
    assert after['corner_normals'][1:] == obj['corner_normals'][1:]
    assert np.dot(after['corner_normals'][0], normal) > np.dot(old, normal)
    assert report['backward_corner_normals'] == 1


def test_exactly_antipodal_partial_repair_uses_a_finite_face_tangent():
    obj, op = box()
    obj['corner_normals'][0] = [0., 0., 1.]
    op.update(kind='consistent_normals', params={'strength': .5})
    after, report = H.plan_object(obj, op)
    normal = np.asarray(after['corner_normals'][0])
    assert np.isfinite(normal).all()
    assert np.linalg.norm(normal) == pytest.approx(1.)
    assert abs(normal[2]) < 1e-15
    assert after['corner_normals'][1:] == obj['corner_normals'][1:]
    assert report['backward_corner_normals'] == 1
