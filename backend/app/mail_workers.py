"""Bounded, spawned attachment workers; children never access the mail database."""
from concurrent.futures import ProcessPoolExecutor
import ctypes
import multiprocessing
import os
from pathlib import Path
import sys

GIB = 1024**3


def available_memory():
    """Available physical memory on Windows/Linux; unknown is conservative."""
    try:
        if sys.platform == 'win32':
            class MemoryStatus(ctypes.Structure):
                _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong) for name in (
                        'total_phys', 'avail_phys', 'total_page', 'avail_page',
                        'total_virtual', 'avail_virtual', 'avail_extended')]
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.avail_phys
        elif sys.platform.startswith('linux'):
            for line in Path('/proc/meminfo').read_text().splitlines():
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, AttributeError):
        pass
    return None


def worker_limit(requested, cpu_count, available):
    # Reserve 2 GiB for the desktop and budget 1 GiB per process. This is
    # admission control, not a hard guarantee of peak native memory usage.
    memory_limit = 1 if available is None else max(1, (available - 2*GIB)//GIB)
    return max(1, min(4, requested, max(1, (cpu_count or 1)//2), memory_limit))


def configured_workers(requested=None):
    if requested is None:
        try:
            requested = int(os.environ.get('FINANCE_OCR_WORKERS', '4'))
        except ValueError:
            requested = 1
    return worker_limit(max(1, min(4, requested)), os.cpu_count(), available_memory())


def initialize_worker():
    # Limit native threading, including libraries imported after initialization.
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[name] = '1'
    import cv2
    cv2.setNumThreads(1)


def analysis_pool(workers):
    return ProcessPoolExecutor(max_workers=workers,
                               mp_context=multiprocessing.get_context('spawn'),
                               initializer=initialize_worker)
