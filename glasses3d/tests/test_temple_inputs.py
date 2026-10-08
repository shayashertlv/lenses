"""An explicit camera path must never silently switch a WP6 comparison's cameras."""
import pytest

from glasses3d.g3d_host import relief_views as RV


@pytest.mark.parametrize('kind', ['missing', 'directory', 'empty'])
def test_invalid_explicit_camera_refuses_before_loading_product(monkeypatch, tmp_path, kind):
    path = tmp_path/'missing.json' if kind == 'missing' else tmp_path if kind == 'directory' else ''
    monkeypatch.setattr(RV.FV, 'reference_files', lambda *a: pytest.fail('loaded product after invalid camera path'))
    with pytest.raises(FileNotFoundError, match='explicit camera file'):
        RV.Job(cameras_path=path)
