"""Independent closed/open/flipped geometry controls for the print's visibility warning."""
import numpy as np

from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.tests.test_pose import synthetic_head


def check(transform):
    from glasses3d.g3d_host.photoprint.geometry import winding_report
    mesh, _ = synthetic_head()
    mesh.F = transform(mesh.F.copy())
    mesh.part = mesh.part[:len(mesh.F)]
    job = IN.Job(mesh, mesh.frame, np.zeros(3), {})
    before = job.V.tobytes(), job.F.tobytes()
    report = winding_report(job, [0])
    assert before == (job.V.tobytes(), job.F.tobytes())
    return report


def test_inward_component_is_reported_not_silently_filled():
    report = check(lambda f: np.flip(f, axis=1))
    assert report['parts'][0]['inward_faces'] == 12
    assert report['parts'][0]['inward_face_share'] == 1
    assert 'Repair the mesh' in report['warnings'][0]


def test_single_flipped_triangle_reports_inconsistent_winding():
    def flip(f):
        f[0] = f[0, ::-1]
        return f
    report = check(flip)
    assert report['parts'][0]['misoriented_edges'] == 3
    assert report['parts'][0]['components'][0]['orientation'] == 'undetermined'
    assert report['warnings']


def test_outward_and_open_components_do_not_get_false_inward_claims():
    assert check(lambda f: f)['warnings'] == []
    report = check(lambda f: f[:1])
    assert report['parts'][0]['inward_faces'] == 0
    assert report['parts'][0]['components'][0]['orientation'] == 'undetermined'


def test_closed_object_split_across_materials_is_checked_as_one_body():
    from glasses3d.g3d_host.photoprint.geometry import winding_report
    mesh, _ = synthetic_head()
    mesh.F = mesh.F[:, ::-1]
    mesh.part[6:12] = len(mesh.names)
    mesh.names.append(mesh.names[0])
    mesh.node_names.append(mesh.node_names[0])
    job = IN.Job(mesh, mesh.frame, np.zeros(3), {})
    report = winding_report(job, [0, 5])
    assert len(report['parts']) == 1
    assert report['parts'][0]['inward_faces'] == 12
    assert report['parts'][0]['primitives'] == [0, 5]
    assert report['warnings']
