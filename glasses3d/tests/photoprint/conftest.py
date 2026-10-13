import pytest


@pytest.fixture(scope="session")
def ft1119_job():
    from glasses3d.g3d_host.photoprint import inputs as IN
    return IN.load_job(IN.FT1119["cameras"])
