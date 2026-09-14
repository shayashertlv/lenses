"""Atomic state, immutable revisions and verified artifact downloads."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

_REPLACE_RETRY_DELAYS = (0.01, 0.03, 0.06, 0.1, 0.2)

def now():
    return datetime.now(timezone.utc).isoformat()

def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows scanners can briefly deny a rename despite a complete,
        # closed temporary file. Retry only this same atomic local replacement;
        # never rewrite the bytes, remove the destination or repeat a provider.
        for attempt in range(len(_REPLACE_RETRY_DELAYS) + 1):
            try:
                os.replace(temporary, path)
                break
            except PermissionError as error:
                if (getattr(error, 'winerror', None) not in {5, 32, 33}
                        or attempt == len(_REPLACE_RETRY_DELAYS)):
                    raise
                time.sleep(_REPLACE_RETRY_DELAYS[attempt])
    finally:
        temporary.unlink(missing_ok=True)

class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.jobs = self.root / 'jobs'
        self.jobs.mkdir(parents=True, exist_ok=True)
    def directory(self, identifier):
        if str(uuid.UUID(identifier)) != identifier:
            raise ValueError('Invalid job identifier')
        return self.jobs / identifier
    def path(self, job, path):
        resolved = Path(path).resolve(strict=True)
        if not resolved.is_relative_to(self.directory(job['id']).resolve()):
            raise ValueError('Artifact is outside this job')
        return resolved
    def load(self, identifier):
        return json.loads((self.directory(identifier) / 'job.json').read_text(encoding='utf-8'))
    def save(self, job):
        atomic_json(self.directory(job['id']) / 'job.json', job)
    def all(self):
        return [json.loads(p.read_text(encoding='utf-8')) for p in sorted(self.jobs.glob('*/job.json'))]
    def artifact(self, job, path):
        path = self.path(job, path)
        checksum = sha(path)
        for item in job['artifacts'].values():
            if item['path'] == str(path) and item['sha256'] == checksum:
                return item['url']
        identifier = uuid.uuid4().hex
        item = {'path': str(path), 'sha256': checksum, 'size': path.stat().st_size,
                'url': f'/api/jobs/{job["id"]}/files/{identifier}'}
        job['artifacts'][identifier] = item
        return item['url']
    def verified_artifact(self, job, identifier):
        item = job['artifacts'].get(identifier)
        if item is None:
            raise KeyError(identifier)
        path = self.path(job, item['path'])
        if path.stat().st_size != item['size'] or sha(path) != item['sha256']:
            raise ValueError('Saved artifact changed; original receipt was retained')
        return path

class ServiceLock:
    """Hold a real OS lock for this store, including idle time."""
    def __init__(self, root):
        self.path = Path(root) / '.service.lock'
        self.stream = None
    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a+b')
        try:
            self.stream.seek(0)
            if not self.stream.read(1):
                self.stream.write(b'0'); self.stream.flush()
            self.stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.stream.close(); self.stream = None
            raise RuntimeError('This Modeling Auto store already has a running service')
    def close(self):
        if self.stream:
            self.stream.close(); self.stream = None
