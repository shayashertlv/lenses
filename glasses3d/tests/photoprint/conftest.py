import pytest


@pytest.fixture(scope="session")
def ft1119_job():
    from glasses3d.tests.fixtures import legacy_ft1119
    return legacy_ft1119.load_job()


@pytest.fixture(scope="session")
def ft1119_fixture():
    from glasses3d.tests.fixtures import legacy_ft1119
    return legacy_ft1119.cached_fixture()
