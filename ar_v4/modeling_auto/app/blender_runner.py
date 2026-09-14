"""Owned, cancellable native Blender processes for immutable revision outputs."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import struct
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")


class BlenderError(RuntimeError):
    pass


def timeout_message(output: Path, seconds: float) -> str:
    message = f"Blender exceeded {seconds:g} seconds"
    labels = {'load': 'loading the model', 'input_checks': 'checking the input',
              'astra_edit': 'executing the saved Astra edit', 'scope_checks': 'checking edit scope',
              'output_checks': 'checking the edited model', 'render_proofs': 'rendering previews',
              'pack_save': 'packing and saving', 'export_glb': 'exporting GLB',
              'reopen_checks': 'verifying the saved model', 'artifact_hashes': 'verifying artifact hashes'}
    try:
        path = output / 'native_progress.json'
        if path.stat().st_size <= 16_384:
            phase = json.loads(path.read_text(encoding='utf-8')).get('current', {}).get('phase')
            if isinstance(phase, str) and phase in labels:
                message += ' while ' + labels[phase]
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return message


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def contained(path: Path) -> Path:
    value = path.resolve()
    if not value.is_relative_to(ROOT):
        raise BlenderError("Blender paths must stay inside modeling_auto")
    return value


def validate_embedded_glb(path: Path) -> None:
    """Allow native complete assets, but never external paths from a downloaded GLB."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(12)
        if len(header) != 12:
            raise BlenderError("Truncated GLB header")
        magic, version, length = struct.unpack("<4sII", header)
        if magic != b"glTF" or version != 2 or length != size:
            raise BlenderError("Invalid GLB v2 container")
        chunks = []
        document = None
        while stream.tell() < size:
            chunk = stream.read(8)
            if len(chunk) != 8:
                raise BlenderError("Truncated GLB chunk header")
            count, kind = struct.unpack("<I4s", chunk)
            if count % 4 or stream.tell() + count > size:
                raise BlenderError("Invalid GLB chunk bounds")
            if not chunks and kind != b"JSON":
                raise BlenderError("GLB JSON must be the first chunk")
            if kind == b"JSON":
                if document is not None or count > 64 * 1024 * 1024:
                    raise BlenderError("Invalid or oversized GLB JSON chunk")
                try:
                    document = json.loads(stream.read(count).decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as exc:
                    raise BlenderError("Invalid GLB JSON") from exc
            else:
                stream.seek(count, 1)
            chunks.append((kind, count))
    if not isinstance(document, dict):
        raise BlenderError("Missing GLB scene document")
    if sum(kind == b"BIN\x00" for kind, _ in chunks) > 1:
        raise BlenderError("Multiple GLB binary chunks")
    for group in ("buffers", "images"):
        values = document.get(group, [])
        if not isinstance(values, list):
            raise BlenderError(f"Invalid GLB {group}")
        for entry in values:
            if not isinstance(entry, dict):
                raise BlenderError(f"Invalid GLB {group} entry")
            uri = entry.get("uri")
            if uri is not None and (not isinstance(uri, str) or not uri.startswith("data:")):
                raise BlenderError("GLB must embed all buffers and images; external files and URLs are unavailable")


class BlenderRunner:
    def __init__(self, executable=None, timeout=600, resolution=768, samples=24):
        self.executable = str(executable or os.environ.get("MODELING_AUTO_BLENDER") or DEFAULT_BLENDER)
        self.timeout = float(timeout)
        self.resolution = int(resolution)
        self.samples = int(samples)

    async def run(self, action, input_path, output_dir, *, script=None, stage=None,
                  dimensions=None, cancel=None):
        if action not in {"import", "edit", "inspect"}:
            raise BlenderError("Unknown Blender action")
        source = contained(Path(input_path))
        output = contained(Path(output_dir))
        if not source.is_file():
            raise BlenderError("Blender input is missing")
        if output == source.parent or source.is_relative_to(output):
            raise BlenderError("Blender output must be separate from the input")
        output.mkdir(parents=True, exist_ok=True)
        if any((output / name).exists() for name in ("result.json", "master.blend", "model.glb")):
            raise BlenderError("Refusing to overwrite an existing Blender revision")
        if action == "edit":
            from app.script_validation import validate_script
            validate_script(script or "")
        if cancel is not None and cancel.is_set():
            raise asyncio.CancelledError("Cancelled before Blender")
        if source.suffix.lower() == ".glb":
            validate_embedded_glb(source)
        before = sha256(source)
        request = dict(action=action, input_path=str(source), output_dir=str(output),
                       script=script, stage=stage, dimensions=dimensions or {},
                       resolution=self.resolution, samples=self.samples)
        (output / "request.json").write_text(json.dumps(request, indent=2), encoding="utf-8")
        native_home = output / "native_home"
        native_home.mkdir(exist_ok=True)
        env = dict(os.environ)
        env.update(TEMP=str(native_home), TMP=str(native_home),
                   HOME=str(native_home), USERPROFILE=str(native_home),
                   BLENDER_USER_RESOURCES=str(native_home),
                   BLENDER_USER_CONFIG=str(native_home / "config"),
                   BLENDER_USER_SCRIPTS=str(native_home / "scripts"),
                   PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
        # Paid provider credentials never enter the script process environment.
        for key in list(env):
            if any(word in key.upper() for word in ("API_KEY", "OPENAI", "MESHY", "TOKEN", "SECRET")):
                env.pop(key, None)
        log_path = output / "blender.log"
        started = time.monotonic()
        process = None
        with log_path.open("wb") as log:
            try:
                process = await asyncio.create_subprocess_exec(
                    self.executable, "--background", "--factory-startup", "--disable-autoexec",
                    "--python", str(ROOT / "blender" / "worker.py"), "--", str(output / "request.json"),
                    cwd=str(ROOT), env=env, stdout=log, stderr=asyncio.subprocess.STDOUT,
                )
                waiter = asyncio.create_task(process.wait())
                while process.returncode is None:
                    if cancel is not None and cancel.is_set():
                        raise asyncio.CancelledError("Blender operation cancelled")
                    if time.monotonic() - started > self.timeout:
                        raise BlenderError(timeout_message(output, self.timeout))
                    try:
                        await asyncio.wait_for(asyncio.shield(waiter), timeout=0.2)
                    except TimeoutError:
                        pass
            finally:
                if process is not None and process.returncode is None:
                    if os.name == "nt":
                        killer = await asyncio.create_subprocess_exec(
                            "taskkill", "/PID", str(process.pid), "/T", "/F",
                            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                        await killer.wait()
                    else:
                        process.kill()
                    await process.wait()
        if sha256(source) != before:
            raise BlenderError("Blender input changed unexpectedly")
        result_path = output / "result.json"
        if not result_path.is_file():
            raise BlenderError(f"Blender exited without a result (exit {process.returncode}); inspect retained blender.log")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if process.returncode or not result.get("ok"):
            raise BlenderError(result.get("error") or f"Blender exited {process.returncode}")
        for key in ("blend_path", "model_path", "closeup_path"):
            file = Path(result[key]).resolve()
            if not file.is_relative_to(output) or not file.is_file():
                raise BlenderError("Blender returned an invalid artifact path")
        for file in result.get("proofs", {}).values():
            if not Path(file).resolve().is_relative_to(output) or not Path(file).is_file():
                raise BlenderError("Blender returned an invalid proof")
        if set(result.get("proofs", {})) != {"front", "back", "left", "right", "angled"}:
            raise BlenderError("Blender did not return all five current views")
        for rel, digest in result["hashes"].items():
            file = (output / rel).resolve()
            if not file.is_relative_to(output) or sha256(file) != digest:
                raise BlenderError("Blender artifact receipt mismatch")
        return result
