"""Build a proof basis privately, then publish through same-volume renames.

Windows cannot replace a populated directory in one rename. The old basis is
therefore kept under a unique sibling backup until the new directory is in place;
a failed second rename restores it. A process crash between renames still leaves
the complete old basis at the reported backup path, never deleted before rebuild.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4


def rebuild_basis(destination, build):
    destination = Path(destination).absolute()
    if destination.is_symlink() or destination.is_junction():
        raise ValueError('basis destination must not be a link or junction')
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = destination.with_name(f'.{destination.name}-previous-{uuid4().hex}')
    with tempfile.TemporaryDirectory(prefix=f'.{destination.name}-build-', dir=destination.parent) as temporary:
        candidate = Path(temporary) / 'basis'
        result = build(candidate, destination)
        if not candidate.is_dir():
            raise ValueError('basis builder did not produce a candidate directory')
        had_basis = destination.exists()
        if had_basis:
            if not destination.is_dir():
                raise ValueError('basis destination must be a directory')
            os.replace(destination, backup)
        try:
            os.replace(candidate, destination)
        except BaseException:
            if had_basis:
                try:
                    os.replace(backup, destination)
                except OSError as exc:
                    raise RuntimeError(f'publication failed; intact old basis retained at {backup}') from exc
            raise
        if had_basis:
            # Only our unique sibling backup is eligible for recursive cleanup.
            if backup.resolve().parent != destination.parent or backup.is_symlink() or backup.is_junction():
                raise ValueError(f'unsafe basis backup cleanup path: {backup}')
            shutil.rmtree(backup)
        return result
