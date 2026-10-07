"""Processes of a glasses3d run (WP2_DESIGN §1, §2, §3, §11): the worker's allowlisted environment, a runner-owned
kill-on-close Job Object, the Windows power-throttling (EcoQoS) opt-out, process identity checks, and detached starts
(breakaway, WMI fallback) for long runs.

Ports (PORT_REVIEW §11): the EcoQoS functions come from source-before/modeler/winqos.py (opt_out, opt_out_self, state,
descendants, DescendantsOptOut; measured there: a detached job ran its fits 2.2x slower until it opted out, and the
opt-out is not inherited) and the identity/detach functions from source-before/modeler/detach.py (process_state with
the kernel process list for processes OpenProcess refuses, same_process, stop_tree with the root killed first,
open_append, the breakaway starter -> keeper -> command chain and the WMI `Win32_Process.Create` fallback, the spec
file that keeps arguments out of any command line a shell or WMI parses). New here: `worker_env` (an allowlist, not a
deny-list), `JobObject` (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: measured, a Blender child died with its killed runner),
`spawn_in_job`, and `matches_record` (pid + create time + exe + an argv marker such as `--receipt <path>`).

Off Windows the Job Object and EcoQoS calls do nothing and return False/None: they are guards and speed measures,
never conditions for running.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]          # lenses/: where `python -m glasses3d.agent.procs` imports from
MODULE = "glasses3d.agent.procs"
SPEC_VERSION = 1

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
DETACHED_FLAGS = CREATE_BREAKAWAY_FROM_JOB | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
EXIT_KEEPER_REFUSED = 3
EXIT_COMMAND_NOT_STARTED = 127
SAME_PROCESS_TOLERANCE_S = 1.0
WMI_TIMEOUT_S = 90
STARTER_TIMEOUT_S = 60

#: the worker's environment: exactly these variables (WP2_DESIGN §1), TEMP/TMP pointed at the run's temp folder
WORKER_ENV_KEYS = ("SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "NUMBER_OF_PROCESSORS", "USERPROFILE",
                   "LOCALAPPDATA", "APPDATA")

PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
PROCESS_POWER_THROTTLING_INFORMATION_CLASS = 4
PROCESS_SET_INFORMATION = 0x0200
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
TH32CS_SNAPPROCESS = 0x00000002
SWEEP_INTERVAL_S = 0.2

WMI_SCRIPT = (
    "$ErrorActionPreference = 'Stop'; "
    "$si = New-CimInstance -ClassName Win32_ProcessStartup -Namespace root/cimv2 -ClientOnly -Property @{ShowWindow = [uint16]0}; "
    "$r = Invoke-CimMethod -ClassName Win32_Process -Namespace root/cimv2 -MethodName Create -Arguments "
    "@{CommandLine = $env:G3D_DETACH_COMMAND_LINE; CurrentDirectory = $env:G3D_DETACH_CWD; ProcessStartupInformation = $si}; "
    "[Console]::Out.Write(('{{\"return_value\": {0}, \"pid\": {1}}}' -f [int]$r.ReturnValue, [int]$r.ProcessId))")


def available() -> bool:
    return os.name == "nt"


# --------------------------------------------------------------------------- environment
def worker_env(token: str, temp_dir: Path, source: dict | None = None) -> dict:
    """The allowlisted environment of a Blender worker: WORKER_ENV_KEYS from `source` (default os.environ), TEMP and
    TMP pointing at `temp_dir`, the token and PYTHONDONTWRITEBYTECODE. Nothing else: no API keys, no tokens of other
    tools, no PYTHONPATH."""
    src = os.environ if source is None else source
    upper = {k.upper(): v for k, v in src.items()}
    env = {k: upper[k] for k in WORKER_ENV_KEYS if k in upper}
    Path(temp_dir).mkdir(parents=True, exist_ok=True)
    env["TEMP"] = env["TMP"] = str(temp_dir)
    env["G3D_WORKER_TOKEN"] = token
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


# --------------------------------------------------------------------------- kernel32
def _k32():
    """A private kernel32 handle with argument types set (ctypes.windll.kernel32 is shared: not ours to configure)."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.GetCurrentProcess.argtypes = ()
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    k32.CloseHandle.restype = wintypes.BOOL
    k32.SetProcessInformation.restype = wintypes.BOOL
    k32.SetProcessInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    k32.GetProcessInformation.restype = wintypes.BOOL
    k32.GetProcessInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    k32.QueryInformationJobObject.restype = wintypes.BOOL
    k32.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.DWORD))
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    k32.TerminateJobObject.restype = wintypes.BOOL
    k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    k32.SetThreadExecutionState.restype = wintypes.DWORD
    k32.SetThreadExecutionState.argtypes = (wintypes.DWORD,)
    return k32


# --------------------------------------------------------------------------- Job Object
class JobObject:
    """A Windows Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, owned by this (runner) process: every process
    assigned to it dies when it is terminated, closed, or when the runner dies (its handle closes with it). Use one
    per worker so a hard kill reaps Blender and anything Blender started, and nothing else."""

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3

    def __init__(self, name: str | None = None) -> None:
        self.handle = None
        if not available():
            return
        import ctypes
        k32 = _k32()
        h = k32.CreateJobObjectW(None, name)
        if not h:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = _extended_limit_info()()
        info.BasicLimitInformation.LimitFlags = self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(h, self.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
                                           ctypes.sizeof(info)):
            err = ctypes.get_last_error()
            k32.CloseHandle(h)
            raise OSError(err, "SetInformationJobObject(KILL_ON_JOB_CLOSE) failed")
        self.handle = h

    def assign(self, proc_or_handle) -> bool:
        """Put a process (a Popen, or a process handle) into the job. True when Windows accepted it."""
        if self.handle is None:
            return False
        h = proc_or_handle._handle if isinstance(proc_or_handle, subprocess.Popen) else proc_or_handle
        return bool(_k32().AssignProcessToJobObject(self.handle, int(h)))

    def pids(self) -> list[int]:
        """The process ids currently in the job."""
        if self.handle is None:
            return []
        import ctypes
        from ctypes import wintypes

        class Ids(ctypes.Structure):
            _fields_ = [("NumberOfAssignedProcesses", wintypes.DWORD), ("NumberOfProcessIdsInList", wintypes.DWORD),
                        ("ProcessIdList", ctypes.c_size_t * 256)]
        ids = Ids()
        ret = wintypes.DWORD(0)
        if not _k32().QueryInformationJobObject(self.handle, self.JOB_OBJECT_BASIC_PROCESS_ID_LIST, ctypes.byref(ids),
                                                ctypes.sizeof(ids), ctypes.byref(ret)):
            return []
        return [int(ids.ProcessIdList[i]) for i in range(ids.NumberOfProcessIdsInList)]

    def terminate(self, exit_code: int = 1) -> bool:
        if self.handle is None:
            return False
        return bool(_k32().TerminateJobObject(self.handle, exit_code))

    def close(self) -> None:
        """Close the handle: with KILL_ON_JOB_CLOSE every process still in the job is killed."""
        if self.handle is not None:
            _k32().CloseHandle(self.handle)
            self.handle = None

    def __del__(self):  # pragma: no cover - interpreter shutdown order
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass


def _extended_limit_info():
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes",
                                                    "OtherBytes")]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]
    return Extended


def spawn_in_job(argv: list, *, cwd, stdout, env: dict, job: JobObject | None = None,
                 creationflags: int | None = None) -> tuple[subprocess.Popen, JobObject]:
    """Start `argv` with stdout/stderr to `stdout` (a file, never a PIPE: an unread PIPE hangs Blender once EEVEE's
    log fills it, defect 7), stdin from DEVNULL, a hidden console and its own process group; put it in `job` (a new
    kill-on-close Job Object by default) and opt it out of EcoQoS through its handle."""
    job = job or JobObject()
    flags = (CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP) if creationflags is None else creationflags
    kwargs = {"creationflags": flags} if available() else {"start_new_session": True}
    proc = subprocess.Popen([str(a) for a in argv], cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=stdout,
                            stderr=subprocess.STDOUT, env=env, close_fds=True, **kwargs)
    if available() and not job.assign(proc):
        import ctypes
        err = ctypes.get_last_error()
        proc.kill()
        proc.wait(10)
        raise OSError(err, "AssignProcessToJobObject failed; the child was killed")
    opt_out(proc)
    return proc, job


# --------------------------------------------------------------------------- EcoQoS (port of modeler/winqos.py)
def _qos_state_struct():
    import ctypes
    from ctypes import wintypes

    class State(ctypes.Structure):
        _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]
    return State


def _set_on_handle(handle) -> bool:
    import ctypes
    k32, State = _k32(), _qos_state_struct()
    st = State(PROCESS_POWER_THROTTLING_CURRENT_VERSION, PROCESS_POWER_THROTTLING_EXECUTION_SPEED, 0)
    return bool(k32.SetProcessInformation(int(handle), PROCESS_POWER_THROTTLING_INFORMATION_CLASS, ctypes.byref(st),
                                          ctypes.sizeof(st)))


def _is_handle(value) -> bool:
    handle_type = getattr(subprocess, "Handle", None)
    return handle_type is not None and isinstance(value, handle_type)


def opt_out(handle_or_pid) -> bool:
    """Opt one process out of power throttling (a Popen, a process handle, or a pid). True when Windows accepted
    it; False off Windows, for an ended process, a process this user may not change, or any error (never raises)."""
    if not available() or handle_or_pid is None or isinstance(handle_or_pid, bool):
        return False
    try:
        if isinstance(handle_or_pid, subprocess.Popen):
            h = getattr(handle_or_pid, "_handle", None)
            if h is None or handle_or_pid.poll() is not None:
                return False
            return _set_on_handle(h)
        if _is_handle(handle_or_pid):
            return _set_on_handle(handle_or_pid)
        pid = int(handle_or_pid)
        if pid <= 0 or pid > 0xFFFFFFFF:
            return False
        k32 = _k32()
        h = k32.OpenProcess(PROCESS_SET_INFORMATION, False, pid)
        if not h:
            return False
        try:
            return _set_on_handle(h)
        finally:
            k32.CloseHandle(h)
    except (OSError, AttributeError, ValueError, TypeError, OverflowError):
        return False


def opt_out_self() -> bool:
    """Opt this process out (the runner, a fit-pool worker). Not inherited by children: see `opt_out`."""
    if not available():
        return False
    try:
        return _set_on_handle(_k32().GetCurrentProcess())
    except (OSError, AttributeError):
        return False


def qos_state(handle_or_pid=None) -> tuple[int, int] | None:
    """(ControlMask, StateMask) as GetProcessInformation reads it: (1, 0) after an opt-out, (1, 1) under explicit
    EcoQoS, (0, 0) when the system decides. None when unreadable or off Windows."""
    if not available():
        return None
    import ctypes
    try:
        k32, State = _k32(), _qos_state_struct()
        own = None
        if handle_or_pid is None:
            h = k32.GetCurrentProcess()
        elif isinstance(handle_or_pid, subprocess.Popen):
            h = handle_or_pid._handle
        elif _is_handle(handle_or_pid):
            h = handle_or_pid
        else:
            own = h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(handle_or_pid))
            if not h:
                return None
        try:
            st = State(PROCESS_POWER_THROTTLING_CURRENT_VERSION, 0, 0)
            if not k32.GetProcessInformation(int(h), PROCESS_POWER_THROTTLING_INFORMATION_CLASS, ctypes.byref(st),
                                             ctypes.sizeof(st)):
                return None
            return (int(st.ControlMask), int(st.StateMask))
        finally:
            if own:
                k32.CloseHandle(own)
    except (OSError, AttributeError, ValueError, TypeError, OverflowError):
        return None


def _process_table() -> list[tuple[int, int]]:
    """[(pid, parent pid)] of every running process (Toolhelp32 snapshot). Empty on any failure."""
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    k32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(Entry))
    k32.Process32FirstW.restype = wintypes.BOOL
    k32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(Entry))
    k32.Process32NextW.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    out = []
    try:
        e = Entry()
        e.dwSize = ctypes.sizeof(Entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            out.append((int(e.th32ProcessID), int(e.th32ParentProcessID)))
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def children(pid: int) -> list[int]:
    if not available():
        return []
    try:
        return [p for p, pp in _process_table() if pp == int(pid) and p != pp]
    except (OSError, AttributeError):
        return []


def descendants(pid: int) -> list[int]:
    """Every running descendant of `pid`, breadth first, from one snapshot (ids are only numbers: opt out, never stop)."""
    if not available():
        return []
    try:
        table = _process_table()
    except (OSError, AttributeError):
        return []
    kids: dict[int, list[int]] = {}
    for p, pp in table:
        if p != pp:
            kids.setdefault(pp, []).append(p)
    out, seen, queue = [], {int(pid)}, [int(pid)]
    while queue:
        for c in kids.get(queue.pop(0), ()):
            if c not in seen:
                seen.add(c)
                out.append(c)
                queue.append(c)
    return out


class DescendantsOptOut:
    """`with DescendantsOptOut(pid): ...` opts every new descendant of `pid` (default: this process) out of power
    throttling every `interval` seconds while the block runs (node + Chromium of arview, pool workers)."""

    def __init__(self, pid: int | None = None, interval: float = SWEEP_INTERVAL_S):
        self.pid = os.getpid() if pid is None else int(pid)
        self.interval = float(interval)
        self.seen: set[int] = set()
        self.opted_out = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sweep(self) -> int:
        n = 0
        for p in descendants(self.pid):
            if p in self.seen:
                continue
            self.seen.add(p)
            if opt_out(p):
                n += 1
        self.opted_out += n
        return n

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.sweep()
            except Exception:  # noqa: BLE001 - a speed measure never stops the phase
                pass
            self._stop.wait(self.interval)

    def __enter__(self) -> "DescendantsOptOut":
        if available():
            self._thread = threading.Thread(target=self._loop, name=f"qos-optout-{self.pid}", daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            try:
                self.sweep()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- identity (port of modeler/detach.py)
def _process_list_entry():
    import ctypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort), ("Buffer", ctypes.c_void_p)]

    class Entry(ctypes.Structure):
        _fields_ = [("NextEntryOffset", ctypes.c_ulong), ("NumberOfThreads", ctypes.c_ulong),
                    ("WorkingSetPrivateSize", ctypes.c_longlong), ("HardFaultCount", ctypes.c_ulong),
                    ("NumberOfThreadsHighWatermark", ctypes.c_ulong), ("CycleTime", ctypes.c_ulonglong),
                    ("CreateTime", ctypes.c_longlong), ("UserTime", ctypes.c_longlong),
                    ("KernelTime", ctypes.c_longlong), ("ImageName", UnicodeString), ("BasePriority", ctypes.c_long),
                    ("UniqueProcessId", ctypes.c_void_p)]
    return Entry


def _snapshot_process(pid: int) -> tuple[bool, float | None] | None:
    """(running, create time) of `pid` from the kernel's process list (works for processes OpenProcess refuses)."""
    import ctypes
    try:
        query = ctypes.WinDLL("ntdll").NtQuerySystemInformation
        query.restype = ctypes.c_long
        query.argtypes = (ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong))
    except (OSError, AttributeError):
        return None
    need, got = 1 << 20, ctypes.c_ulong(0)
    for _ in range(8):
        buf = ctypes.create_string_buffer(need)
        status = query(5, buf, need, ctypes.byref(got)) & 0xFFFFFFFF
        if status == 0:
            break
        if status != 0xC0000004:
            return None
        need = max(need * 2, got.value + (256 << 10))
    else:
        return None
    entry = _process_list_entry()
    off, end = 0, min(need, got.value or need)
    while off + ctypes.sizeof(entry) <= end:
        e = entry.from_buffer(buf, off)
        if (e.UniqueProcessId or 0) == pid:
            return (True, e.CreateTime / 1e7 - 11644473600.0)
        if not e.NextEntryOffset:
            return (False, None)
        off += e.NextEntryOffset
    return None


def _windows_process_state(pid: int) -> tuple[bool, float | None]:
    import ctypes
    from ctypes import wintypes
    k32 = _k32()
    query, synchronize = 0x1000, 0x00100000
    h = k32.OpenProcess(query | synchronize, False, pid)
    waitable = bool(h)
    if not h:
        h = k32.OpenProcess(query, False, pid)
    if not h:
        if ctypes.get_last_error() != 5:
            return (False, None)
        snap = _snapshot_process(pid)
        return snap if snap is not None else (True, None)
    try:
        if waitable:
            running = k32.WaitForSingleObject(h, 0) == 0x102
        else:
            code = wintypes.DWORD()
            running = bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259
        if not running:
            return (False, None)
        c, e, k, u = (wintypes.FILETIME() for _ in range(4))
        if not k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
            snap = _snapshot_process(pid)
            return (True, snap[1] if snap else None)
        return (True, ((c.dwHighDateTime << 32) | c.dwLowDateTime) / 1e7 - 11644473600.0)
    finally:
        k32.CloseHandle(h)


def process_state(pid) -> tuple[bool, float | None]:
    """(running, create time in epoch seconds or None) of a process id; (False, None) for no process."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return (False, None)
    if os.name == "nt":
        return _windows_process_state(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return (False, None)
    except PermissionError:
        return (True, None)
    except OSError:
        return (False, None)
    return (True, None)


def pid_alive(pid) -> bool:
    return process_state(pid)[0]


def process_create_time(pid) -> float | None:
    return process_state(pid)[1]


def same_process(pid, create_time) -> bool:
    """The process recorded as (pid, create time) still runs (a different create time is another process)."""
    alive, created = process_state(pid)
    if not alive:
        return False
    if create_time is None or created is None:
        return True
    try:
        return abs(float(created) - float(create_time)) <= SAME_PROCESS_TOLERANCE_S
    except (TypeError, ValueError):
        return True


def matches_record(record: dict) -> bool:
    """A recorded process {pid, create_time, exe, argv_marker} is still exactly that process: same pid, a create
    time that agrees, the same executable and (when recorded) the marker in its command line (the worker's
    `--receipt <path>`). Used before any cleanup kill, so a reused pid is never touched."""
    pid, created = record.get("pid"), record.get("create_time")
    if not isinstance(pid, int) or not same_process(pid, created):
        return False
    if created is None:
        return False
    try:
        import psutil
        p = psutil.Process(pid)
        if record.get("exe") and os.path.normcase(p.exe()) != os.path.normcase(str(record["exe"])):
            return False
        marker = record.get("argv_marker")
        if marker and marker not in p.cmdline():
            return False
    except Exception:  # noqa: BLE001 - unreadable means 'not provably ours'
        return False
    return True


def kill_recorded(record: dict, timeout_s: float = 10.0) -> bool:
    """Kill a recorded orphan (the second line behind the Job Object) only when `matches_record` holds."""
    if not matches_record(record):
        return False
    import psutil
    try:
        p = psutil.Process(record["pid"])
        p.kill()
        p.wait(timeout_s)
    except psutil.NoSuchProcess:
        pass
    except psutil.TimeoutExpired:
        return False
    return True


def _taskkill(args: list[str]) -> int:
    return subprocess.run(["taskkill", *args], capture_output=True, timeout=60, stdin=subprocess.DEVNULL,
                          creationflags=CREATE_NO_WINDOW).returncode


def stop_tree(pid, create_time=None) -> bool:
    """Stop a recorded process and the trees of the children created after it (root first: a keeper killed before
    its child records nothing, so a killed command reads 'gone', never 'exited'). False when the id is not that
    process."""
    if not same_process(pid, create_time):
        return False
    if os.name == "nt":
        created = create_time if create_time is not None else process_create_time(pid)
        ok = _taskkill(["/F", "/PID", str(int(pid))]) == 0
        t0 = time.monotonic()
        while same_process(pid, created) and time.monotonic() - t0 < 5.0:
            time.sleep(0.02)
        for c in children(int(pid)):
            born = process_create_time(c)
            if born is not None and (created is None or born >= float(created) - SAME_PROCESS_TOLERANCE_S):
                _taskkill(["/T", "/F", "/PID", str(int(c))])
        return ok
    import signal
    try:
        os.killpg(int(pid), signal.SIGTERM)
    except ProcessLookupError:
        return False
    except PermissionError:
        os.kill(int(pid), signal.SIGTERM)
    return True


# --------------------------------------------------------------------------- small files
def open_append(path):
    """`path` opened for appending only (FILE_APPEND_DATA without FILE_WRITE_DATA on Windows), so a child given the
    handle always appends, whoever else appended since."""
    if os.name != "nt":
        return open(path, "ab", buffering=0)
    import _winapi
    import msvcrt
    file_append_data, synchronize = 0x0004, 0x00100000
    share_all, open_always, attribute_normal = 0x1 | 0x2 | 0x4, 4, 0x80
    h = _winapi.CreateFile(str(path), file_append_data | synchronize, share_all, 0, open_always, attribute_normal, 0)
    try:
        fd = msvcrt.open_osfhandle(h, os.O_APPEND)
    except OSError:
        _winapi.CloseHandle(h)
        raise
    return os.fdopen(fd, "ab", buffering=0)


REPLACE_DELAYS_S = (0.01, 0.025, 0.05, 0.1, 0.2, 0.4, 0.8, 1.6)


def replace_with_retry(src, dst) -> None:
    """os.replace with the Windows sharing-violation retry (~3.2 s in all; Defender denies ~1% of renames here)."""
    for delay in REPLACE_DELAYS_S + (None,):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)


def write_json_atomic(path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(value, indent=1, default=str), encoding="utf-8")
    replace_with_retry(tmp, path)


def read_json(path) -> dict | None:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def spec_path_for(status_path: Path) -> Path:
    status_path = Path(status_path)
    return status_path.with_name(status_path.stem + ".spec.json")


def utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------- detached commands
class DetachError(RuntimeError):
    """A detached command could not be started (nothing is running)."""


@dataclass
class Detached:
    pid: int
    create_time: float | None
    method: str                                  # "breakaway" | "wmi" | "posix"
    status_path: Path
    proc: subprocess.Popen | None = field(default=None, repr=False)

    def status(self) -> dict | None:
        return read_json(self.status_path)

    def state(self) -> tuple[str, int | None]:
        if self.proc is not None:
            self.proc.poll()
        state, code, _ = command_state(self.pid, self.create_time, self.status_path)
        return state, code

    def wait(self, timeout: float | None = None, interval: float = 0.25) -> int | None:
        t0 = time.monotonic()
        while True:
            state, code = self.state()
            if state != "running":
                return code
            if timeout is not None and time.monotonic() - t0 > timeout:
                raise subprocess.TimeoutExpired(f"detached pid {self.pid}", timeout)
            time.sleep(interval)

    def record(self) -> dict:
        return {"pid": self.pid, "create_time": self.create_time, "method": self.method,
                "status_file": str(self.status_path)}


def command_state(pid, create_time, status_path) -> tuple[str, int | None, dict | None]:
    """("exited", code, status) once recorded; ("running", None, status) while the keeper or command runs; ("gone",
    None, status) when neither runs and no exit was recorded (killed)."""
    st = read_json(status_path) if status_path else None
    if st and isinstance(st.get("exit_code"), int):
        return "exited", st["exit_code"], st
    if same_process(pid, create_time):
        return "running", None, st
    if st and st.get("child_pid") and same_process(st.get("child_pid"), st.get("child_create_time")):
        return "running", None, st
    st = read_json(status_path) if status_path else None
    if st and isinstance(st.get("exit_code"), int):
        return "exited", st["exit_code"], st
    return "gone", None, st


def keeper_python() -> str:
    """The interpreter for the starter and keeper: the base interpreter when this one runs from a venv. A Windows
    venv's python.exe is a launcher that runs the real interpreter as its child inside a kill-on-close job with
    silent breakaway, so a keeper started through it is two processes, the recorded pid is the launcher's, and
    killing the launcher first kills the keeper before its tree can be walked (measured: the command survived
    stop_tree). The keeper needs only the standard library and `glasses3d.agent.procs` (cwd = the repo)."""
    base = getattr(sys, "_base_executable", None)
    if sys.prefix != sys.base_prefix and base and Path(base).is_file():
        return str(base)
    return sys.executable


def _powershell() -> str:
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    p = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(p)


def wmi_create(argv: list[str], cwd: Path) -> int:
    """Create `argv` through WMI (Win32_Process.Create, hidden window; parent WmiPrvSE, outside every job and tree
    of the caller); returns its pid. The command line reaches PowerShell in an environment variable."""
    env = dict(os.environ, G3D_DETACH_COMMAND_LINE=subprocess.list2cmdline([str(a) for a in argv]),
               G3D_DETACH_CWD=str(cwd))
    try:
        r = subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                            WMI_SCRIPT], env=env, capture_output=True, text=True, timeout=WMI_TIMEOUT_S,
                           stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise DetachError(f"WMI process creation failed: {type(e).__name__}: {e}") from None
    try:
        out = json.loads((r.stdout or "").strip())
    except ValueError:
        raise DetachError(f"WMI process creation failed (exit {r.returncode}): "
                          f"{(r.stderr or r.stdout or '').strip()[:400]}") from None
    if int(out.get("return_value", -1)) != 0 or int(out.get("pid", 0)) <= 0:
        raise DetachError(f"Win32_Process.Create returned {out.get('return_value')}")
    return int(out["pid"])


def spawn_detached(argv: list, *, cwd, log_path, status_path, env: dict | None = None, keep_awake: bool = False,
                   python: str | None = None, allow_wmi: bool = True, breakaway: bool = True) -> Detached:
    """Start `argv` so that it outlives the caller (a preview server's tree kill, a closed console, the caller's
    kill-on-close job): starter (breakaway) -> keeper -> command, or WMI when breakaway is refused (or
    `breakaway=False`). Output appends to `log_path`; the end is recorded in `status_path`. The keeper opts the
    command out of EcoQoS. Raises DetachError when nothing could start."""
    if not isinstance(argv, list) or not argv:
        raise DetachError("a detached command is a non-empty argument list")
    status_path = Path(status_path)
    spec_path = spec_path_for(status_path)
    spec = {"version": SPEC_VERSION, "argv": [str(a) for a in argv], "cwd": str(cwd), "log": str(log_path),
            "status": str(status_path), "env": {str(k): str(v) for k, v in (env or {}).items()},
            "keep_awake": bool(keep_awake), "created_utc": utcnow()}
    write_json_atomic(spec_path, spec)
    status_path.unlink(missing_ok=True)
    py = str(python or keeper_python())
    keeper = [py, "-m", MODULE, "keep", str(spec_path)]
    starter_py = str(python or sys.executable)    # measured: breakaway from the caller's jobs works through the venv
    #                                               launcher's job (silent breakaway), not from the base interpreter
    if os.name != "nt":
        p = subprocess.Popen(keeper, cwd=str(REPO), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
        return Detached(p.pid, process_create_time(p.pid), "posix", status_path, proc=p)
    refused = "breakaway not attempted"
    if breakaway:
        try:
            r = subprocess.run([starter_py, "-m", MODULE, "start", str(spec_path)], cwd=str(REPO),
                               stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=STARTER_TIMEOUT_S,
                               creationflags=DETACHED_FLAGS, close_fds=True)
        except OSError as e:
            refused = f"the caller's job object refused breakaway ({e})"
        except subprocess.TimeoutExpired:
            raise DetachError(f"the starter did not report within {STARTER_TIMEOUT_S} s") from None
        else:
            if r.returncode == 0:
                try:
                    out = json.loads(r.stdout.strip().splitlines()[-1])
                    return Detached(int(out["pid"]), out.get("create_time"), "breakaway", status_path)
                except (ValueError, KeyError, IndexError, TypeError):
                    raise DetachError(f"the starter printed no keeper record: {r.stdout[-300:]!r}") from None
            if r.returncode != EXIT_KEEPER_REFUSED:
                raise DetachError(f"the starter failed (exit {r.returncode}): {(r.stderr or r.stdout).strip()[-600:]}")
            refused = f"the starter could not create the keeper: {(r.stderr or '').strip()[-300:]}"
    if not allow_wmi:
        raise DetachError(f"cannot detach: {refused}; WMI not allowed")
    pid = wmi_create(keeper, REPO)
    return Detached(pid, process_create_time(pid), "wmi", status_path)


def _start(spec_path: str) -> int:
    try:
        p = subprocess.Popen([keeper_python(), "-m", MODULE, "keep", spec_path], cwd=str(REPO),
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             close_fds=True, creationflags=DETACHED_FLAGS)
    except OSError as e:
        print(f"cannot create the keeper: {e}", file=sys.stderr)
        return EXIT_KEEPER_REFUSED
    print(json.dumps({"pid": p.pid, "create_time": process_create_time(p.pid)}), flush=True)
    return 0


def _keep(spec_path: str) -> int:
    spec = read_json(spec_path)
    if not spec or spec.get("version") != SPEC_VERSION:
        return 2
    opt_out_self()
    status_path = Path(spec["status"])
    status = {"version": SPEC_VERSION, "keeper_pid": os.getpid(), "keeper_create_time": process_create_time(os.getpid()),
              "argv": spec["argv"], "cwd": spec["cwd"], "started_utc": utcnow(), "child_pid": None,
              "child_create_time": None, "exit_code": None, "finished_utc": None}
    try:
        with open_append(spec["log"]) as logf:
            env = dict(os.environ, **(spec.get("env") or {}))
            if spec.get("keep_awake") and os.name == "nt":
                try:
                    _k32().SetThreadExecutionState(0x80000000 | 0x00000001)
                except (OSError, AttributeError):
                    pass
            try:
                child = subprocess.Popen(spec["argv"], cwd=spec["cwd"], stdin=subprocess.DEVNULL, stdout=logf,
                                         stderr=subprocess.STDOUT, env=env, close_fds=True)
            except OSError as e:
                logf.write(f"=== {utcnow()} could not start the command: {type(e).__name__}: {e}\n".encode())
                status.update(exit_code=EXIT_COMMAND_NOT_STARTED, finished_utc=utcnow(), error=f"{type(e).__name__}: {e}")
                write_json_atomic(status_path, status)
                return 0
            status["child_power_throttling_opt_out"] = opt_out(child)
            status.update(child_pid=child.pid, child_create_time=process_create_time(child.pid))
            write_json_atomic(status_path, status)
            code = child.wait()
            logf.write(f"=== {utcnow()} exit code {code}\n".encode())
        status.update(exit_code=code, finished_utc=utcnow())
        write_json_atomic(status_path, status)
        return 0
    except BaseException as e:  # noqa: BLE001 - the log is the only place a keeper failure can be seen
        import traceback
        try:
            with open_append(spec["log"]) as logf:
                logf.write(f"=== {utcnow()} detach keeper failed: {type(e).__name__}: {e}\n"
                           f"{traceback.format_exc()}".encode("utf-8", "replace"))
        except OSError:
            pass
        return 1


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] not in ("start", "keep"):
        print(f"usage: python -m {MODULE} start|keep SPEC.json (started by spawn_detached, not by hand)",
              file=sys.stderr)
        return 2
    return _start(args[1]) if args[0] == "start" else _keep(args[1])


if __name__ == "__main__":
    raise SystemExit(main())
