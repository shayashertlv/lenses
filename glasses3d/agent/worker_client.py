"""Host side of the g3d Blender worker (glasses3d/plan/archive/20261019/WP2_DESIGN.md §3): start it, prove its identity, call it with deadlines, kill it.

    w = WorkerClient(job_root, start_blend=head_blend).start()
    w.call("exec", {"code": ..., "soft_timeout_s": 2.0}, deadline_s=32.0)
    w.close()

Start: a 32-byte token in the worker's allowlisted environment (agent/procs.worker_env; never argv), Blender inside its
own kill-on-close Job Object with EcoQoS opted out through its handle, stdout/stderr appended to `worker.log` (never a
PIPE), cwd an empty folder. The client waits up to 60 s for the receipt, checks pid == Popen.pid, the create time and
the token hash, connects to 127.0.0.1:<port> and says hello. Anything else kills the Job Object (no orphan; the Studio
session.py:88-99 left one).

Calls: one request at a time; the socket deadline is the HARD timeout. A deadline that passes, EOF, a frame error or a
dead process kills the worker (TerminateJobObject) and raises WorkerDied: a C call ignores the soft timeout, so the
hard path is mandatory. An error response raises WorkerError and leaves the worker running.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import select
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from ..g3d import rpc
from . import procs
from .tool_timeouts import (HELLO_TIMEOUT_S, READY_TIMEOUT_S, READ_DEADLINE_S, RECEIPT_ATTEMPTS,
                            WORKER_CLOSE_WAIT_S, WORKER_KILL_WAIT_S, WORKER_POLL_S, WORKER_SHUTDOWN_S)

G3D = Path(__file__).resolve().parents[1]
WORKER_PY = G3D / "g3d" / "worker.py"


class _DeadlineSocket:
    """Keep an RPC deadline absolute even when a frame arrives in many chunks."""
    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline

    def _remaining(self):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise socket.timeout("RPC deadline elapsed")
        self.sock.settimeout(left)

    def sendall(self, data):
        self._remaining()
        return self.sock.sendall(data)

    def recv(self, count):
        self._remaining()
        return self.sock.recv(count)


class WorkerError(RuntimeError):
    """The worker answered with an error (the worker is still alive)."""

    def __init__(self, etype: str, message: str, tb: str | None = None):
        super().__init__(f"{etype}: {message}")
        self.etype, self.message, self.traceback = etype, message, tb


class WorkerDied(RuntimeError):
    """The worker is gone (killed by the client or exited). `reason`: hard_timeout | eof | exited | frame_error |
    ready_timeout | identity | start_failed."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail


def blender_exe() -> Path:
    from ..g3d_host.blender import blender_exe as find
    exe = find()
    if exe is None:
        raise FileNotFoundError("Blender 5.2 not found (set G3D_BLENDER)")
    return exe


class WorkerClient:
    def __init__(self, root, *, start_blend=None, blender=None, selftest: bool = False, probe: bool = False,
                 ready_timeout_s: float = READY_TIMEOUT_S, worker_dir=None) -> None:
        self.root = Path(root).resolve()
        self.start_blend = Path(start_blend).resolve() if start_blend else None
        self.blender = Path(blender) if blender else None
        self.selftest = bool(selftest)
        self.probe = bool(probe)
        self.ready_timeout_s = float(ready_timeout_s)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        self.dir = Path(worker_dir) if worker_dir else self.root / "worker" / f"{stamp}-{secrets.token_hex(3)}"
        self.proc: subprocess.Popen | None = None
        self.job: procs.JobObject | None = None
        self.sock: socket.socket | None = None
        self.receipt: dict | None = None
        self.hello: dict | None = None
        self.create_time: float | None = None
        self.exe: str | None = None
        self.ready_s: float | None = None
        self.qos: tuple | None = None
        self._next_id = 0
        self._log = None
        self.dead_reason: str | None = None

    # ---------------------------------------------------------------------------------------------- lifecycle
    def start(self) -> "WorkerClient":
        self.dir.mkdir(parents=True, exist_ok=True)
        cwd = self.dir / "cwd"
        cwd.mkdir(exist_ok=True)
        token = secrets.token_bytes(32).hex()
        receipt_path = self.dir / "receipt.json"
        receipt_path.unlink(missing_ok=True)
        exe = self.blender or blender_exe()
        argv = [str(exe), "-b", "--factory-startup", "--disable-autoexec"]
        if self.start_blend is not None:
            argv.append(str(self.start_blend))
        argv += ["--python", str(WORKER_PY), "--", "--receipt", str(receipt_path), "--root", str(self.root)]
        if self.selftest:
            argv.append("--selftest")
        if self.probe:
            argv.append("--probe")
        env = procs.worker_env(token, self.dir / "tmp")
        self._log = procs.open_append(self.dir / "worker.log")
        t0 = time.perf_counter()
        try:
            self.proc, self.job = procs.spawn_in_job(argv, cwd=cwd, stdout=self._log, env=env)
        except OSError as exc:
            self._close_log()
            raise WorkerDied("start_failed", str(exc)) from None
        self.qos = procs.qos_state(self.proc)
        alive, self.create_time = procs.process_state(self.proc.pid)
        self.exe = str(exe)
        self.argv_marker = str(receipt_path)
        while not receipt_path.exists():
            if self.proc.poll() is not None:
                self._dead("start_failed", f"Blender exited with {self.proc.returncode} before it was ready; see "
                                           f"{self.dir / 'worker.log'}")
            if time.perf_counter() - t0 > self.ready_timeout_s:
                self._dead("ready_timeout", f"no receipt within {self.ready_timeout_s:g} s")
            time.sleep(WORKER_POLL_S)
        info = None
        for _ in range(RECEIPT_ATTEMPTS):          # the receipt is replaced atomically; a reader may still race it
            try:
                info = json.loads(receipt_path.read_text(encoding="utf-8"))
                break
            except (OSError, ValueError):
                time.sleep(WORKER_POLL_S)
        if not isinstance(info, dict):
            self._dead("identity", "unreadable receipt")
        self.receipt = info
        if info.get("pid") != self.proc.pid:
            self._dead("identity", f"receipt pid {info.get('pid')} is not the spawned pid {self.proc.pid}")
        if self.create_time is not None and info.get("create_time") is not None \
                and abs(float(info["create_time"]) - float(self.create_time)) > procs.SAME_PROCESS_TOLERANCE_S:
            self._dead("identity", f"receipt create_time {info['create_time']} != {self.create_time}")
        if info.get("token_sha256") != hashlib.sha256(token.encode()).hexdigest():
            self._dead("identity", "receipt token hash does not match")
        try:
            hello_deadline = time.monotonic() + HELLO_TIMEOUT_S
            self.sock = socket.create_connection(("127.0.0.1", int(info["port"])), timeout=HELLO_TIMEOUT_S)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._next_id = 1
            exchange = _DeadlineSocket(self.sock, hello_deadline)
            rpc.send_frame(exchange, {"id": 1, "method": "hello", "token": token})
            reply = rpc.recv_frame(exchange)
        except (OSError, rpc.FrameError) as exc:
            self._dead("start_failed", f"hello failed: {exc}")
        if not reply.get("ok"):
            self._dead("identity", f"hello refused: {reply.get('error')}")
        self.hello = reply["result"]
        self.ready_s = time.perf_counter() - t0
        return self

    def record(self) -> dict:
        """The worker's identity, for execution markers and cleanup (agent/procs.matches_record)."""
        return {"pid": self.proc.pid if self.proc else None, "create_time": self.create_time, "exe": self.exe,
                "argv_marker": getattr(self, "argv_marker", None), "dir": str(self.dir),
                "port": (self.receipt or {}).get("port")}

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None and self.dead_reason is None

    def cpu_seconds(self) -> float | None:
        try:
            import psutil
            t = psutil.Process(self.proc.pid).cpu_times()
            return t.user + t.system
        except Exception:  # noqa: BLE001
            return None

    # ---------------------------------------------------------------------------------------------- calls
    def call(self, method: str, params: dict | None = None, *, deadline_s: float = READ_DEADLINE_S, slow=None):
        """One request; returns the result. WorkerError on an error response; WorkerDied (worker killed) on a missed
        deadline, EOF, a frame error or a dead process. `slow=(after_s, callback)`: when no reply has started after
        `after_s`, `callback()` runs once (the session starts a warm spare worker then) and the wait goes on to
        the deadline; nothing is read until the reply is readable, so a frame is never split."""
        if not self.alive or self.sock is None:
            raise WorkerDied(self.dead_reason or "exited", "the worker is not running")
        self._next_id += 1
        rid = self._next_id
        try:
            t0 = time.monotonic()
            exchange = _DeadlineSocket(self.sock, t0 + float(deadline_s))
            rpc.send_frame(exchange, rpc.request(rid, method, params or {}, deadline_s))
            if slow is not None and slow[0] < deadline_s:
                until_slow = max(0.0, float(slow[0]) - (time.monotonic() - t0))
                readable, _, _ = select.select([self.sock], [], [], until_slow)
                if not readable:
                    slow[1]()
                left = float(deadline_s) - (time.monotonic() - t0)
                if left <= 0 or not select.select([self.sock], [], [], left)[0]:
                    raise socket.timeout()
            reply = rpc.recv_frame(exchange)
        except (socket.timeout, TimeoutError):
            self._dead("hard_timeout", f"{method}: no reply within {deadline_s:g} s; the worker was killed")
        except rpc.PeerClosed as exc:
            code = self.proc.poll()
            self._dead("eof" if code is None else "exited", f"{method}: {exc} (exit code {code})")
        except (rpc.FrameError, OSError) as exc:
            self._dead("frame_error", f"{method}: {exc}")
        why = rpc.check_response(reply, rid)
        if why:
            self._dead("frame_error", why)
        if not reply["ok"]:
            err = reply["error"]
            raise WorkerError(str(err.get("type")), str(err.get("message")), err.get("traceback"))
        return reply["result"]

    # ---------------------------------------------------------------------------------------------- endings
    def _dead(self, reason: str, detail: str):
        self.kill(reason)
        raise WorkerDied(reason, detail)

    def kill(self, reason: str = "killed") -> None:
        """Terminate the Job Object (Blender and anything it started) and wait for the process to end."""
        self.dead_reason = self.dead_reason or reason
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        members = self.job.pids() if self.job is not None else []
        if self.proc is not None and self.proc.pid not in members:
            members.append(self.proc.pid)
        if self.job is not None:
            self.job.terminate(1)
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.kill()
            except OSError:
                pass
        # Popen.wait is not proof of an end here: after TerminateJobObject, Popen.kill reads the exit code that the
        # termination set and records it while the process object is not yet signalled (measured: WAIT_TIMEOUT on a
        # fresh handle right after wait() returned). Wait for every member to be gone.
        t0 = time.monotonic()
        while time.monotonic() - t0 < WORKER_KILL_WAIT_S and any(procs.pid_alive(m) for m in members):
            time.sleep(WORKER_POLL_S)
        if self.job is not None:
            self.job.close()
            self.job = None
        self._close_log()

    def close(self, timeout_s: float = WORKER_CLOSE_WAIT_S) -> None:
        """Ask for a clean shutdown; kill when it does not end in time."""
        if self.alive and self.sock is not None:
            try:
                self.call("shutdown", deadline_s=WORKER_SHUTDOWN_S)
            except (WorkerDied, WorkerError):
                pass
            try:
                self.proc.wait(timeout_s)
            except subprocess.TimeoutExpired:
                pass
        self.dead_reason = self.dead_reason or "closed"
        self.kill(self.dead_reason)

    def _close_log(self) -> None:
        if self._log is not None:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None

    def __enter__(self) -> "WorkerClient":
        return self.start() if self.proc is None else self

    def __exit__(self, *exc) -> None:
        self.close()
