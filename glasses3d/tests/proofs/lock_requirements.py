"""Record the installed Windows/Python 3.12 dependency closure with PyPI wheel hashes.

Run only when deliberately refreshing the lock, from the tested glasses3d venv:
    python -m glasses3d.tests.proofs.lock_requirements
No package is installed or upgraded. Each recorded release must have a wheel
compatible with this interpreter. PyPI release JSON is the only network input.
"""
from __future__ import annotations

from datetime import date
from importlib import metadata
import json
from pathlib import Path
import platform
import sys
import urllib.request

from packaging.requirements import Requirement
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename

ROOT = Path(__file__).resolve().parents[2]
ROOTS = {
    'requirements.txt': ('numpy', 'scipy', 'opencv-python', 'scikit-image', 'pillow', 'pypatchmatch',
                         'open3d', 'pytest', 'pytest-xdist'),
    'requirements-agent.txt': ('openai', 'openai-agents', 'psutil', 'jsonschema'),
}


def installed_closure(roots):
    """Evaluate dependency markers including every transitively requested extra."""
    found, extras, queue = {}, {}, [(Requirement(name), '') for name in roots]
    while queue:
        requirement, parent_extra = queue.pop()
        if requirement.marker and not requirement.marker.evaluate({'extra': parent_extra}):
            continue
        name = canonicalize_name(requirement.name)
        dist = metadata.distribution(name)
        if dist.version not in requirement.specifier:
            raise ValueError(f'{name}=={dist.version} does not satisfy {requirement}')
        requested = set(requirement.extras) | {''}
        new = requested - extras.get(name, set())
        if not new:
            continue
        extras.setdefault(name, set()).update(new)
        found[name] = dist.version
        for extra in new:
            queue.extend((Requirement(value), extra) for value in dist.requires or ())
    return dict(sorted(found.items()))


def wheel_hash(name, version):
    url = f'https://pypi.org/pypi/{name}/{version}/json'
    with urllib.request.urlopen(url, timeout=30) as response:
        release = json.load(response)
    priority = {tag: index for index, tag in enumerate(sys_tags())}
    wheels = []
    for row in release['urls']:
        if row['packagetype'] != 'bdist_wheel' or row.get('yanked'):
            continue
        _, _, _, tags = parse_wheel_filename(row['filename'])
        matching = set(tags) & priority.keys()
        if matching:
            wheels.append((min(priority[tag] for tag in matching), row['filename'], row))
    if not wheels:
        raise ValueError(f'no compatible wheel for {name}=={version}: {url}')
    row = min(wheels)[2]
    digest = row['digests']['sha256']
    if len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest):
        raise ValueError(f'invalid PyPI hash for {name}')
    return row['filename'], digest


def main():
    if sys.version_info[:2] != (3, 12) or platform.system() != 'Windows' or platform.machine() != 'AMD64':
        raise SystemExit('This lock targets CPython 3.12, Windows AMD64 only; use the tested project venv.')
    closures = {filename: installed_closure(roots) for filename, roots in ROOTS.items()}
    releases = dict(item for closure in closures.values() for item in closure.items())
    wheels = {name: wheel_hash(name, version) for name, version in sorted(releases.items())}
    for filename, closure in closures.items():
        lines = [
            '# Complete installed dependency closure: CPython 3.12 / Windows AMD64.',
            f'# Recorded {date.today().isoformat()} from the tested glasses3d/.venv; no version upgrades.',
            '# Source: https://pypi.org/pypi/<name>/<version>/json (compatible wheel SHA-256).',
            '# Regenerate deliberately: python -m glasses3d.tests.proofs.lock_requirements',
            '# Install from repository root:',
            f'# glasses3d/.venv/Scripts/python.exe -m pip install --require-hashes -r glasses3d/{filename}',
            '# Direct roots: ' + ', '.join(ROOTS[filename]),
        ]
        if filename == 'requirements.txt':
            lines += [
                '# scipy 1.16.0 preserves the prototype fits; test_cameras_repro.py has one',
                '# bit-exact test gated by its recorded fingerprint (PORT_REVIEW section 9).',
                '# Suite scheduling and serial timing pass: see tests/README.md.',
            ]
        lines.append('')
        for name, version in closure.items():
            wheel, digest = wheels[name]
            lines += [f'# {wheel}', f'{name}=={version} --hash=sha256:{digest}']
        (ROOT / filename).write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({filename: len(closure) for filename, closure in closures.items()}))


if __name__ == '__main__':
    main()
