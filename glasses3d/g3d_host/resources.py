"""Memory-aware process budgets for host-only numerical tools."""
import ctypes
import math
import os
from contextlib import contextmanager


@contextmanager
def single_threaded_children():
    """Spawned numerical workers import BLAS with one thread; restore the caller's environment."""
    names = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS')
    saved = {name: os.environ.get(name) for name in names}
    os.environ.update(dict.fromkeys(names, '1'))
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def free_commit_bytes() -> int:
    if os.name == 'nt':
        class Memory(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in ('total_phys', 'avail_phys', 'total_page', 'avail_page',
                                                       'total_virtual', 'avail_virtual', 'avail_extended')]
        state = Memory()
        state.length = ctypes.sizeof(state)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
            raise OSError('cannot measure free commit for the camera pool')
        return int(state.avail_page)
    import psutil
    return int(psutil.virtual_memory().available + psutil.swap_memory().free)


def fit_workers(requested=None, *, per_worker_gib=1.3):
    if requested is not None and (type(requested) is not int or requested < 1):
        raise ValueError('workers must be a positive integer or null')
    maximum = max(1, min(14, max(1, (os.cpu_count() or 1)-2),
                         math.floor((free_commit_bytes()/2**30-2)/per_worker_gib)))
    return min(maximum, requested) if requested is not None else maximum
