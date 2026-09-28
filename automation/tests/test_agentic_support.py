"""Shared temp-folder helpers of the test_agentic_* suites (this module holds no tests).

Job folders need a SHORT root because of the Windows path limit: ``LENSES_TEST_TMP`` when it is set (use it when TMP/TEMP
point at a long folder), otherwise ``tempfile.gettempdir()``. Each suite keeps its own ``lag-<name>`` folder there and any
per-suite override it had (LAG_OWNER_TMP, LAG_REBUILD_TMP, LAG_RECOVERY_TMP, LAG_SESSION_TMP, LAG_TOOLS_TMP). Nothing is
created at import time: a root is made on first use. ``fresh_dir`` registers the removal of the folder it makes; a path
that cannot be removed (an open SQLite or image handle on Windows) is reported as a ``LeftoverWarning`` (an error with
``LENSES_TEST_STRICT_CLEANUP=1``), never hidden.
"""
from __future__ import annotations

import gc
import os
from pathlib import Path
import shutil
import sys
import tempfile
import warnings


class LeftoverWarning(UserWarning):
    """A test folder that could not be removed completely."""


def short_tmp() -> Path:
    return Path(os.environ.get("LENSES_TEST_TMP") or tempfile.gettempdir())


def lag_root(name: str, env: str | None = None) -> Path:
    """``<short tmp>/lag-<name>`` (or the folder the ``env`` variable names), created on first use."""
    override = os.environ.get(env) if env else None
    root = Path(override) if override else short_tmp() / f"lag-{name}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def rmtree_reporting(path) -> list[str]:
    """Remove ``path`` after a garbage collection (which closes unreferenced stores and images); the paths that could not
    be removed are returned and reported, never silently left behind."""
    path = Path(path)
    if not path.exists():
        return []
    gc.collect()
    failed: list[str] = []

    def record(_func, p, _exc) -> None:
        failed.append(str(p))
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=record)
    else:  # pragma: no cover - the project runs 3.12
        shutil.rmtree(path, onerror=record)
    if failed:
        msg = f"test folder {path} left {len(failed)} path(s) behind (an open handle?): {failed[:5]}"
        if os.environ.get("LENSES_TEST_STRICT_CLEANUP"):
            raise AssertionError(msg)
        warnings.warn(msg, LeftoverWarning, stacklevel=2)
    return failed


def fresh_dir(owner, name: str, prefix: str = "", env: str | None = None) -> Path:
    """A new empty folder under ``lag_root(name, env)``. ``owner`` is a TestCase (removed at its cleanup), a TestCase
    class (removed at its class cleanup, for setUpClass) or None (the caller removes it)."""
    d = Path(tempfile.mkdtemp(prefix=prefix, dir=str(lag_root(name, env))))
    if isinstance(owner, type):
        owner.addClassCleanup(rmtree_reporting, d)
    elif owner is not None:
        owner.addCleanup(rmtree_reporting, d)
    return d
