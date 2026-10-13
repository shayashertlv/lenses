"""Local implementation receipt and source archive (WP2_DESIGN section 9). No network or job input access."""
from __future__ import annotations

import hashlib
import importlib.metadata
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

from .budget import create_json_exclusive
from ..g3d_host.blender import blender_exe

EXCLUDED = frozenset({"data", ".venv", "node_modules", "__pycache__", ".env", ".git"})
# A fixed-size pathspec keeps git's argv below Windows' command-line limit as the
# source tree grows. Match both a component itself and anything beneath it, just
# like the archive filter below (including an accidentally tracked secret file).
DIFF_PATHSPECS = ("glasses3d",) + tuple(
    f":(top,glob,exclude)glasses3d/**/{component}{suffix}"
    for component in (*sorted(EXCLUDED), ".env.*")
    for suffix in ("", "/**")
)


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout


def _version(executable, *args) -> str | None:
    if executable is None:
        return None
    result = subprocess.run([str(executable), *args], capture_output=True, check=True, text=True)
    return result.stdout.splitlines()[0]


def runtime_versions() -> dict:
    return {"python": sys.version, "executable": sys.executable,
            "packages": dict(sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions()
                                    if d.metadata.get("Name"))),
            "blender": _version(blender_exe(), "--version"), "node": _version(shutil.which("node"), "--version")}


def capture(run_dir, *, repo_root=None) -> dict:
    """Freeze exact current source bytes before inference, including untracked source during development.

    git supplies paths; data/environments/dependencies/secrets are excluded component by component. Symlinks and
    junction escapes are refused. The archive is built from the very bytes hashed (no second file read race).
    """
    root = Path(repo_root or Path(__file__).resolve().parents[2]).resolve()
    run = Path(run_dir)
    run.mkdir(parents=True, exist_ok=True)
    names = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "glasses3d")
    paths = []
    for raw in names.split(b"\0"):
        if not raw:
            continue
        name = raw.decode("utf-8")
        relative = Path(name)
        if any(part in EXCLUDED or part.startswith(".env.") for part in relative.parts):
            continue
        path = root / relative
        if not path.exists():  # a deletion is in the diff; it must not reappear in the archive
            continue
        if path.is_symlink() or path.resolve() != path.absolute() or not path.is_file():
            raise ValueError("implementation source must be a regular file inside the repository")
        paths.append((name, path.read_bytes()))
    paths.sort()
    # Git includes tracked deletions without enumerating every path in argv.
    diff = _git(root, "diff", "HEAD", "--binary", "--", *DIFF_PATHSPECS)
    archive = run / "src.tar.gz"
    with archive.open("xb") as stream:
        with tarfile.open(fileobj=stream, mode="w:gz") as tar:
            for name, payload in paths:
                info = tarfile.TarInfo(name)
                info.size, info.mtime, info.mode = len(payload), 0, 0o644
                tar.addfile(info, io.BytesIO(payload))
    lock_name = "glasses3d/arview/package-lock.json"
    hashes = {name: hashlib.sha256(payload).hexdigest() for name, payload in paths}
    receipt = {"schema": "g3d.implementation/1", "git_head": _git(root, "rev-parse", "HEAD").decode().strip(),
               "diff_head_sha256": hashlib.sha256(diff).hexdigest(), "files": hashes,
               "archive": archive.name, "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
               "arview_lock_sha256": hashes.get(lock_name), "runtime": runtime_versions()}
    raw = create_json_exclusive(run / "implementation.json", receipt)
    return {"path": "implementation.json", "sha256": hashlib.sha256(raw).hexdigest(),
            "archive_sha256": receipt["archive_sha256"]}
