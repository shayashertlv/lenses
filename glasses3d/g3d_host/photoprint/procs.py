"""Process pool with the Windows EcoQoS opt-out, and progress lines (WP5 step 0).

Ported from the scratch `g3d-wp5/noeco.py` (PORT_REVIEW section 10). A process started from a detached parent on this
machine runs under EcoQoS execution-speed throttling (measured about 2.2x slower host compute), and the opt-out is NOT
inherited, so every pool worker opts itself out in the pool initializer. Elsewhere (non-Windows) the opt-out is a no-op.

``pool_map`` returns results in JOB order (never completion order), so a run with a pool and a serial run produce the
same output (A7 determinism).
"""
from __future__ import annotations

import os
import sys
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


def pool_map(fn, jobs: list, workers: int | None = None) -> list:
    """``[fn(*job) for job in jobs]``, in a process pool when ``workers`` > 1 (each worker opted out of EcoQoS).
    Results come back in job order."""
    jobs = list(jobs)
    if not jobs:
        return []
    w = default_workers(len(jobs)) if workers is None else max(1, int(workers))
    opt_out_ecoqos(None)
    if w <= 1 or len(jobs) == 1:
        return [fn(*j) for j in jobs]
    with ProcessPoolExecutor(max_workers=min(w, len(jobs)), initializer=_init_worker) as ex:
        futs = [ex.submit(fn, *j) for j in jobs]
        return [f.result() for f in futs]


class Progress:
    """Prints ``[tag] message`` at most every ``every_s`` seconds (and always when ``force``)."""

    def __init__(self, tag: str, log=print, every_s: float = PROGRESS_EVERY_S):
        self.tag, self.log, self.every_s = tag, log, every_s
        self.t0 = self.last = time.perf_counter()

    def __call__(self, msg: str, force: bool = False) -> None:
        now = time.perf_counter()
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
