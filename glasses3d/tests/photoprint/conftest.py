import pytest


def require_ft1119(*, pin=False):
    import os
    from glasses3d.tests.fixtures import legacy_ft1119
    missing = legacy_ft1119.missing_inputs(pin=pin)
    if missing:
        reason = 'local-only FT1119 data absent: ' + ', '.join(missing)
        if os.environ.get('G3D_REQUIRE_DATA') == '1':
            pytest.fail(reason)
        pytest.skip(reason)


@pytest.fixture(scope="session")
def ft1119_job(ft1119_fixture):
    from glasses3d.g3d_host.photoprint import inputs
    return inputs.load_job(ft1119_fixture['cameras'])


@pytest.fixture(scope="session")
def ft1119_fixture(tmp_path_factory):
    from glasses3d.tests.fixtures import legacy_ft1119
    require_ft1119()
    return legacy_ft1119.prepare(tmp_path_factory.mktemp('ft1119') / 'inputs')
