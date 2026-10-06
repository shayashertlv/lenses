"""Process pool with the Windows EcoQoS opt-out, and progress lines (WP5 step 0).

Ported from the scratch `g3d-wp5/noeco.py` (PORT_REVIEW section 10). A process started from a detached parent on this
machine runs under EcoQoS execution-speed throttling (measured about 2.2x slower host compute), and the opt-out is NOT
inherited, so every pool worker opts itself out in the pool initializer. Elsewhere (non-Windows) the opt-out is a no-op.

``pool_map`` returns results in JOB order (never completion order), so a run with a pool and a serial run produce the
same output (A7 determinism). It is never silent: with a pool the parent prints 'k / n jobs done' at least every
``PROGRESS_EVERY_S``; on the serial path (``workers`` = 1, the A7 serial bake) a heartbeat thread prints the same line
plus the running job's index at least that often, however long one job takes (fix round 1: the serial A7 bake ran
836 s with no output). ``Progress.heartbeat()`` does the same for any long stretch of non-pool work.
"""
from __future__ import annotations

import contextlib
import os
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor

PROCESS_SET_INFORMATION = 0x0200
PROCESS_POWER_THROTTLING = 4             # PROCESS_INFORMATION_CLASS ProcessPowerThrottling
PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
PROGRESS_EVERY_S = 30.0                  # long loops print at least this often (never silent for ~2 minutes)


def opt_out_ecoqos(pid: int | None = None) -> bool:
    """Opt a process (default: this one) out of EcoQoS execution-speed throttling. False when not on Windows or the
    call fails; never raises."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class _State(ctypes.Structure):
            _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        h = k32.GetCurrentProcess() if pid is None else k32.OpenProcess(PROCESS_SET_INFORMATION, False, int(pid))
        if not h:
            return False
        s = _State(PROCESS_POWER_THROTTLING_CURRENT_VERSION, PROCESS_POWER_THROTTLING_EXECUTION_SPEED, 0)
        ok = bool(k32.SetProcessInformation(wintypes.HANDLE(h), PROCESS_POWER_THROTTLING, ctypes.byref(s),
                                            ctypes.sizeof(s)))
        if pid is not None:
            k32.CloseHandle(wintypes.HANDLE(h))
        return ok
    except Exception:
        return False


def _init_worker() -> None:
    opt_out_ecoqos(None)


def default_workers(n_jobs: int) -> int:
    return max(1, min(n_jobs, (os.cpu_count() or 2) // 2))


def pool_map(fn, jobs: list, workers: int | None = None, progress=None, tag: str | None = None) -> list:
    """``[fn(*job) for job in jobs]``, in a process pool when ``workers`` > 1 (each worker opted out of EcoQoS).
    Results come back in job order. While waiting, a line '[tag] k / n jobs done' is printed at least every
    ``PROGRESS_EVERY_S`` (``progress``: a ``Progress`` or a print-like callable; default print with ``tag`` or the
    function's name), so a long pool is never silent."""
    jobs = list(jobs)
    if not jobs:
        return []
    w = default_workers(len(jobs)) if workers is None else max(1, int(workers))
    opt_out_ecoqos(None)
    name = tag or getattr(fn, "__name__", "pool")
    say = progress if progress is not None else Progress(name)

    def _say(msg: str) -> None:
        if isinstance(say, Progress):
            say(msg, force=True)
        elif say:
            say(f"[{name}] {msg}")

    if w <= 1 or len(jobs) == 1:
        out = []
        state = {"k": 0}
        stop = threading.Event()

        def beat():
            while not stop.wait(PROGRESS_EVERY_S):
                _say(f"{state['k']} / {len(jobs)} jobs done (serial, running job {state['k'] + 1})")

        th = threading.Thread(target=beat, daemon=True)
        th.start()
        try:
            for j in jobs:
                out.append(fn(*j))
                state["k"] += 1
        finally:
            stop.set()
            th.join(timeout=1.0)
        return out
    from concurrent.futures import FIRST_COMPLETED, wait
    with ProcessPoolExecutor(max_workers=min(w, len(jobs)), initializer=_init_worker) as ex:
        futs = [ex.submit(fn, *j) for j in jobs]
        pending = set(futs)
        while pending:
            _done, pending = wait(pending, timeout=PROGRESS_EVERY_S, return_when=FIRST_COMPLETED)
            msg = f"{len(futs) - len(pending)} / {len(futs)} jobs done"
            if isinstance(say, Progress):
                say(msg)
            elif say:
                say(f"[{name}] {msg}")
        return [f.result() for f in futs]


class Progress:
    """Prints ``[tag] message`` at most every ``every_s`` seconds (and always when ``force``)."""

    def __init__(self, tag: str, log=print, every_s: float = PROGRESS_EVERY_S):
        self.tag, self.log, self.every_s = tag, log, every_s
        self.t0 = self.last = time.perf_counter()
        self.stage = "start"
        self._lock = threading.Lock()

    def __call__(self, msg: str, force: bool = False) -> None:
        now = time.perf_counter()
        self.stage = msg
        if force or now - self.last >= self.every_s:
            self.last = now
            if self.log is not None:
                self.log(f"[{self.tag} {now - self.t0:7.1f}s] {msg}")
                try:
                    sys.stdout.flush()
                except Exception:
                    pass

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.t0

    @contextlib.contextmanager
    def heartbeat(self, every_s: float | None = None):
        """While the block runs, print '[tag] still working (last: <message>)' whenever nothing was printed for
        ``every_s`` (default this Progress's interval), from a daemon thread; silent when ``log`` is None."""
        every = float(every_s or self.every_s)
        stop = threading.Event()

        def beat():
            while not stop.wait(min(every, 5.0)):
                if time.perf_counter() - self.last >= every:
                    st = self.stage
                    self(f"still working (last: {st})", force=True)
                    self.stage = st

        th = threading.Thread(target=beat, daemon=True)
        if self.log is not None:
            th.start()
        try:
            yield self
        finally:
            stop.set()
            if th.is_alive():
                th.join(timeout=1.0)
