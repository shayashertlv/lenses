"""Launch a dedicated live Blender instance with the unmodified upstream addon."""
from __future__ import annotations

import argparse
from importlib.metadata import distribution
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--seed", type=Path, help="Copy an existing .blend into the new session")
    source.add_argument("--empty", action="store_true", help="Start from a truly empty scene in numeric millimetres")
    parser.add_argument("--output", type=Path, required=True, help="New session directory")
    parser.add_argument("--port", type=int, default=9876)
    parser.add_argument("--blender", type=Path, default=Path(
        r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"))
    parser.add_argument("--show", action="store_true", help="Open a visible Blender window")
    return parser


def initialized_checkpoint(path, working, mode):
    """The receipt is written only after Blender has saved its initial scene."""
    if not path.is_file() or not working.is_file():
        return None
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if (receipt.get("mode") != mode or not receipt.get("checkpoint_saved")
            or Path(receipt.get("working_copy", "")).resolve() != working.resolve()):
        raise RuntimeError("Blender initialization receipt does not match the requested session")
    if mode == "empty" and any(receipt["initial_counts"].values()):
        raise RuntimeError("Empty Blender initialization retained objects or geometry datablocks")
    return receipt


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    seed = args.seed.resolve() if args.seed else None
    output, blender = args.output.resolve(), args.blender.resolve()
    mode = "empty" if args.empty else "seed"
    if (seed is not None and not seed.is_file()) or not blender.is_file():
        parser.error("The Blender executable and any requested seed .blend must exist")
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            parser.error(f"Port {args.port} already has a listener; choose another")
    addon = Path(distribution("mcp-for-blender").locate_file("blender_mcp/bundled/addon.py"))
    if not addon.is_file():
        parser.error("Install mcp-for-blender from requirements.txt first")
    output.mkdir(parents=True, exist_ok=False)
    working = output / "working.blend"
    initialization_path = output / "initialization.json"
    if seed is not None:
        shutil.copy2(seed, working)
    env = {k: v for k, v in os.environ.items()
           if not any(x in k.upper() for x in ("API_KEY", "APIKEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL", "ACCESS_KEY", "PRIVATE_KEY"))}
    env.update(BLENDERMCP_NO_UPDATE_CHECK="1", BLENDER_MCP_DISABLE_TELEMETRY="1")
    startup = None
    if os.name == "nt" and not args.show:
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
    with (output / "blender.stdout.log").open("w") as stdout, (output / "blender.stderr.log").open("w") as stderr:
        command = [str(blender), "--factory-startup", "--disable-autoexec", "--window-geometry",
                   "0", "0", "1600", "1100"]
        if seed is not None:
            command.append(str(working))
        command.extend(["--python", str(Path(__file__).with_name("bootstrap.py")), "--",
                        "--addon", str(addon), "--port", str(args.port), "--mode", mode,
                        "--working", str(working), "--receipt", str(initialization_path)])
        process = subprocess.Popen(
            command,
            stdout=stdout, stderr=stderr, env=env, startupinfo=startup,
        )
    receipt = {"pid": process.pid, "port": args.port, "mode": mode,
               "source": str(seed) if seed else None,
               "working_copy": str(working), "addon": str(addon), "ready": False}
    receipt_path = output / "session.json"
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError(f"Blender exited; inspect logs under {output}")
        initialized = initialized_checkpoint(initialization_path, working, mode)
        if initialized is not None:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", args.port)) == 0:
                    receipt.update(initialized, ready=True)
                    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
                    print(json.dumps(receipt, indent=2))
                    return
        time.sleep(0.5)
    raise TimeoutError(f"Blender PID {process.pid} did not open MCP port; inspect {output}")


if __name__ == "__main__":
    main()
