import pytest


@pytest.fixture(scope="session")
def ft1119_job():
    from glasses3d.g3d_host.photoprint import inputs as IN
    return IN.load_job()


@pytest.fixture(scope="session")
def synthetic(ft1119_job):
    """(job handed to registration, truth) of the A0 synthetic FT1119 (tests/photoprint/synth.py)."""
    from glasses3d.tests.photoprint import synth
    return synth.synthetic_job(ft1119_job)
