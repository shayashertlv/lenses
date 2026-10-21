"""Reproducible local bundle of arview's renderer for the lens colour editor."""
from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARVIEW = HERE.parents[1] / "arview"
BUILD_ROOT = HERE.parent / ".lens-renderer"
_LOCK = threading.Lock()
_ALLOWED = frozenset({
    "lens-renderer.js", "models/canonical-face.json",
    "decoders/draco/draco_decoder.js", "decoders/draco/draco_decoder.wasm",
    "decoders/draco/draco_wasm_wrapper.js", "decoders/basis/basis_transcoder.js",
    "decoders/basis/basis_transcoder.wasm",
})


def _source_digest() -> str:
    """Bind the cache to code, lockfile, pinned Three package and canonical geometry."""
    files = list((ARVIEW / "src").rglob("*.ts"))
    files += list(HERE.glob("*.js")) + list(HERE.glob("*.mjs"))
    files += [Path(__file__), ARVIEW / "qa/capture/cameras.ts", ARVIEW / "package-lock.json",
              ARVIEW / "node_modules/three/package.json",
              ARVIEW / "public/models/canonical-face.json"]
    digest = hashlib.sha256()
    for file in sorted(files):
        digest.update(str(file.relative_to(HERE.parents[1])).encode("utf-8"))
        digest.update(file.read_bytes())
    return digest.hexdigest()


def ensure_renderer_build() -> Path:
    """Build once when source changed; call off the HTTP event loop. Returns the build root."""
    with _LOCK:
        digest = _source_digest()
        receipt = BUILD_ROOT / "build.json"
        try:
            previous = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        complete = all((BUILD_ROOT / name).is_file() for name in _ALLOWED)
        if previous.get("source_sha256") == digest and complete:
            return BUILD_ROOT
        result = subprocess.run(["node", str(HERE / "build.mjs")], cwd=HERE.parents[2],
                                capture_output=True, text=True, encoding="utf-8",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError("The lens preview could not be built: " + (result.stderr or result.stdout)[-3000:])
        if not all((BUILD_ROOT / name).is_file() for name in _ALLOWED):
            raise RuntimeError("The lens preview build is incomplete.")
        receipt.write_text(json.dumps({"source_sha256": digest, "files": sorted(_ALLOWED)}, indent=2), encoding="utf-8")
        return BUILD_ROOT


def renderer_asset(name: str) -> Path:
    """Resolve only the renderer's public files, confined even through local junctions."""
    if name not in _ALLOWED:
        raise FileNotFoundError(name)
    root = ensure_renderer_build().resolve()
    file = (root / name).resolve()
    if not file.is_relative_to(root) or not file.is_file():
        raise FileNotFoundError(name)
    return file
