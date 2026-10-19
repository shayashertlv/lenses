"""Local detached dev server: ``python -m glasses3d.dev_ui [--status|--stop]``."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import socket
import sys
from pathlib import Path

from glasses3d.agent import procs
from .contracts import Config, now_iso

MODULE = "glasses3d.dev_ui"


def parser():
    out = argparse.ArgumentParser(description="Local glasses3d development UI")
    modes = out.add_mutually_exclusive_group()
    modes.add_argument("--foreground", action="store_true", help="run the server worker in this terminal")
    modes.add_argument("--status", action="store_true", help="show the recorded server's status")
    modes.add_argument("--stop", action="store_true", help="stop the identity-checked server")
    out.add_argument("--port", type=int, default=8260)
    out.add_argument("--state-root", type=Path, help="local UI state directory")
    out.add_argument("--jobs-root", type=Path, help="local jobs directory")
    out.add_argument("--static-root", type=Path, help="local page directory")
    return out


def _paths(config):
    root = config.state_root / "server" / str(config.port)
    return root, root / "server.json", root / "runner-status.json"


def _argv(config):
    return [str(config.python), "-m", MODULE, "--foreground", "--port", str(config.port),
            "--state-root", str(config.state_root), "--jobs-root", str(config.jobs_root),
            "--static-root", str(config.static_root)]


def _identities(config, record, status):
    """Only identities bound to this port's saved command may be inspected/stopped."""
    if not record or record.get("worker_argv") != _argv(config):
        return []
    _, _, status_path = _paths(config)
    candidates = [dict(pid=record.get("pid"), create_time=record.get("create_time"),
                       exe=procs.keeper_python(), argv_marker=str(procs.spec_path_for(status_path)))]
    if status and status.get("argv") == _argv(config):
        candidates.append(dict(pid=status.get("child_pid"), create_time=status.get("child_create_time"),
                               exe=str(config.python), argv_marker=MODULE))
    # Do not let the generic helper's PID-only fallback weaken this CLI's identity check.
    return [row for row in candidates if type(row["pid"]) is int
            and isinstance(row["create_time"], (int, float))
            and not isinstance(row["create_time"], bool) and procs.matches_record(row)]


def server_status(config):
    _, record_path, status_path = _paths(config)
    record, status = procs.read_json(record_path), procs.read_json(status_path)
    identities = _identities(config, record, status)
    return {"status": "running" if identities else "stopped", "url": f"http://127.0.0.1:{config.port}/",
            "port": config.port, "pid": identities[0]["pid"] if identities else None,
            "started_at": (status or {}).get("started_utc"),
            "exit_code": (status or {}).get("exit_code"), "log": str(record_path.parent / "server.log")}


@contextmanager
def _server_guard(config):
    # This is a short startup/status transaction, separate from the paid-run lock.
    from glasses3d.agent.budget import _open_locked, _os_unlock
    root, _, _ = _paths(config)
    stream = _open_locked(root / ".server.guard")
    if stream is None:
        raise ValueError("Another command is starting or stopping this dev server")
    try:
        yield
    finally:
        _os_unlock(stream)


def start_server(config):
    with _server_guard(config):
        state = server_status(config)
        if state["status"] == "running":
            return state
        # Never claim a different service already bound on this port is our UI.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", config.port))
            except OSError:
                raise ValueError(f"Port {config.port} is already in use") from None
        root, record_path, status_path = _paths(config)
        root.mkdir(parents=True, exist_ok=True)
        argv = _argv(config)
        child = procs.spawn_detached(argv, cwd=config.repo_root, log_path=root / "server.log",
                                     status_path=status_path)
        record = {"schema": "g3d.dev_server/1", "port": config.port, "worker_argv": argv,
                  "created_utc": now_iso(), **child.record()}
        procs.write_json_atomic(record_path, record)
        return {"status": "starting", "url": f"http://127.0.0.1:{config.port}/", **record}


def stop_server(config):
    with _server_guard(config):
        _, record_path, status_path = _paths(config)
        record, status = procs.read_json(record_path), procs.read_json(status_path)
        identities = _identities(config, record, status)
        stopped = False
        for identity in identities:
            # Rechecked by stop_tree, including the recorded creation time.
            stopped = procs.stop_tree(identity["pid"], identity["create_time"]) or stopped
        if record and stopped:
            record["stopped_utc"] = now_iso()
            procs.write_json_atomic(record_path, record)
        return {**server_status(config), "stop_requested": stopped}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "task":
        task_parser = argparse.ArgumentParser(prog=f"python -m {MODULE} task")
        task_parser.add_argument("file", type=Path)
        task_args = task_parser.parse_args(args[1:])
        from .tasks import run_task_file
        result = run_task_file(task_args.file.resolve())
        return int(result) if type(result) is int else 0
    options = parser().parse_args(args)
    try:
        kwargs = {name: getattr(options, name) for name in ("state_root", "jobs_root", "static_root")
                  if getattr(options, name) is not None}
        config = Config(port=options.port, **kwargs)
        if options.foreground:
            from .server import create_app
            import uvicorn
            uvicorn.run(create_app(config), host="127.0.0.1", port=config.port, workers=1,
                        reload=False, access_log=False)
            return 0
        result = (server_status(config) if options.status else stop_server(config) if options.stop
                  else start_server(config))
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0
    except (OSError, ValueError, procs.DetachError) as exc:
        print(json.dumps({"error": {"code": "server_error", "message": str(exc)}}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
