"""Offline proof authority, private-file policy and reproducible test infrastructure."""
from dataclasses import replace
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest
from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename

from glasses3d.tests.proofs import atomic_basis, lock_requirements

REPO = Path(__file__).resolve().parents[2]
G3D = REPO / 'glasses3d'


def test_git_ignore_keeps_private_fixtures_and_pipeline_assets_out():
    suffixes = ('png', 'jpg', 'jpeg', 'webp', 'bmp', 'gif', 'exr', 'hdr', 'tif', 'tiff',
                'glb', 'gltf', 'bin', 'obj', 'ply', 'stl', 'fbx', 'blend', 'blend1',
                'mp4', 'mov', 'avi', 'npy', 'npz', 'pkl', 'log')
    private = [f'glasses3d/{folder}/private.{suffix}'
               for folder in ('tests/fixtures', 'tests/fixtures/sub', 'scratch') for suffix in suffixes]
    private += [f'{folder}/{name}' for folder in ('', 'glasses3d/tests/fixtures', 'glasses3d/scratch')
                for name in ('.env', '.env.local', '.env.production')]
    private = [path.lstrip('/') for path in private]
    private += ['glasses3d/data/job.json', 'glasses3d/data/photos/private.jpg']
    public = ['glasses3d/tests/fixtures/example.py', 'glasses3d/tests/fixtures/sub/cases.json']
    done = subprocess.run(['git', 'check-ignore', '--no-index', '-z', '--stdin'], cwd=REPO,
                          input=('\0'.join(private + public) + '\0').encode(), capture_output=True, check=True)
    ignored = set(done.stdout.decode().split('\0')) - {''}
    assert set(private) <= ignored, sorted(set(private) - ignored)
    assert not set(public) & ignored


@pytest.mark.parametrize('filename', lock_requirements.ROOTS)
def test_requirements_lock_contains_the_complete_installed_closure(filename):
    pinned, wheels = {}, {}
    wheel = None
    for line in (G3D / filename).read_text(encoding='utf-8').splitlines():
        if line.startswith('# ') and line.endswith('.whl'):
            wheel = line[2:]
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([a-z0-9-]+)==([^ ]+) --hash=sha256:([0-9a-f]{64})', line)
        assert match, line
        name, version, _ = match.groups()
        assert name not in pinned
        pinned[name] = version
        wheels[name] = wheel
        parsed_name, parsed_version, _, tags = parse_wheel_filename(wheel)
        assert parsed_name == name and str(parsed_version) == version
        assert tags & set(sys_tags()), f'{wheel} is incompatible with this interpreter'
    assert pinned == lock_requirements.installed_closure(lock_requirements.ROOTS[filename])


def test_existing_proof_request_can_only_be_authorized_offline(tmp_path, monkeypatch):
    from glasses3d.agent import approvals as A, job_run, jobs, replay
    from glasses3d.agent.loop import _check_bound_request
    from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire
    from glasses3d.tests.agent.test_jobs import setup_inputs
    from glasses3d.tests.proofs.offline_job_approval import approve_offline_request
    monkeypatch.setenv('G3D_OFFLINE', '1')
    args, _ = setup_inputs(tmp_path)
    root = jobs.create_job(**args)
    transport = replay.ScriptedOpenAI([])
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        spec, _ = job_run.prepare_run(root, 'proof', write_request=True, offline_handler=transport.handle)
        run = root / 'runs' / spec.run_id
        with pytest.raises(A.ApprovalRefused, match='approval'):
            A.check_approval(run, current_request_sha256=spec.current_request_sha256)
        approve_offline_request(run, transport.handle, cap='23')
        assert json.loads((run / 'approval-request.json').read_text())['offline_only'] is True
        assert json.loads((run / 'approval.json').read_text())['authorized_by'] == 'offline_fixture'
        _check_bound_request(run, spec)
        A.check_approval(run, current_request_sha256=spec.current_request_sha256)
        with pytest.raises(A.ApprovalRefused, match='cannot authorize a production run'):
            _check_bound_request(run, replace(spec, offline_handler=None, offline_api_key=None))
        with pytest.raises(ValueError, match='cannot replace'):
            approve_offline_request(run, transport.handle, cap='23')
        assert not A.is_consumed(run)
    assert not network.attempts and not clients.constructions
    assert not transport.counts and not transport.creates


def test_proofs_never_write_owner_approvals():
    # The regular writer is tested in test_approvals; standalone proofs must not
    # leave spendable authority behind when their later preflight fails.
    for source in (G3D / 'tests/proofs').glob('*.py'):
        assert not re.search(r'\bwrite_approval\s*\(', source.read_text(encoding='utf-8')), source


def test_sdk_import_error_is_not_a_skip():
    code = '''
import importlib.abc, runpy, sys
class BrokenSDK(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'agents':
            raise ImportError('required SDK failed to import')
sys.meta_path.insert(0, BrokenSDK())
try:
    runpy.run_module('glasses3d.tests.agent.test_budget_transport')
except ImportError as exc:
    assert 'required SDK failed to import' in str(exc)
else:
    raise AssertionError('missing SDK was accepted')
'''
    done = subprocess.run([sys.executable, '-B', '-c', code], cwd=REPO, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_standalone_cached_fixture_removes_its_private_directory_at_exit(tmp_path):
    receipt = tmp_path / 'path.json'
    code = '''
import json, sys
from glasses3d.tests.fixtures import legacy_ft1119 as fixture
def prepare(path):
    path.mkdir()
    (path / 'model.glb').write_bytes(b'synthetic')
    return {'root': path}
fixture.prepare = prepare
root = fixture.cached_fixture()['root']
assert root == fixture.cached_fixture()['root'] and root.is_dir()
open(sys.argv[1], 'w').write(json.dumps(str(root.parent)))
'''
    done = subprocess.run([sys.executable, '-B', '-c', code, str(receipt)], cwd=REPO,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert not Path(json.loads(receipt.read_text())).exists()


@pytest.mark.parametrize('failure', ['build', 'publish', None])
def test_basis_rebuild_keeps_last_good_basis_on_failure(tmp_path, monkeypatch, failure):
    live = tmp_path / 'basis'
    live.mkdir()
    (live / 'cameras.json').write_bytes(b'last-good-basis')
    original_replace = atomic_basis.os.replace
    def replace_path(source, destination):
        if failure == 'publish' and Path(source).name == 'basis' and Path(source).parent != tmp_path:
            raise OSError('publication refused')
        return original_replace(source, destination)
    monkeypatch.setattr(atomic_basis.os, 'replace', replace_path)
    def build(candidate, published):
        assert published == live and (live / 'cameras.json').read_bytes() == b'last-good-basis'
        candidate.mkdir()
        (candidate / 'cameras.json').write_bytes(b'new-verified-basis')
        if failure == 'build':
            raise ValueError('transport validation failed')
        return {'published': str(published)}
    if failure:
        with pytest.raises((ValueError, OSError), match='failed|refused'):
            atomic_basis.rebuild_basis(live, build)
        assert (live / 'cameras.json').read_bytes() == b'last-good-basis'
    else:
        assert atomic_basis.rebuild_basis(live, build) == {'published': str(live)}
        assert (live / 'cameras.json').read_bytes() == b'new-verified-basis'
    assert list(tmp_path.iterdir()) == [live]


def test_basis_publication_retains_backup_if_windows_refuses_restoration(tmp_path, monkeypatch):
    live = tmp_path / 'basis'
    live.mkdir()
    (live / 'cameras.json').write_bytes(b'last-good-basis')
    original_replace = atomic_basis.os.replace
    def refuse_destination(source, destination):
        if Path(destination) == live:
            raise PermissionError('Windows refused rename')
        return original_replace(source, destination)
    monkeypatch.setattr(atomic_basis.os, 'replace', refuse_destination)
    def build(candidate, published):
        candidate.mkdir()
        (candidate / 'cameras.json').write_bytes(b'new-verified-basis')
    with pytest.raises(RuntimeError, match='intact old basis retained at') as error:
        atomic_basis.rebuild_basis(live, build)
    backups = list(tmp_path.glob('.basis-previous-*'))
    assert len(backups) == 1 and str(backups[0]) in str(error.value)
    assert (backups[0] / 'cameras.json').read_bytes() == b'last-good-basis'
