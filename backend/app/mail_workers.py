"""Bounded, spawned attachment workers; children never access the mail database."""
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from queue import Queue
from threading import Event, Lock, Thread
from tempfile import mkdtemp
import shutil
from time import monotonic
import pickle
import math
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
            requested = int(os.environ.get('FINANCE_OCR_WORKERS', '2'))
        except ValueError:
            requested = 1
    return worker_limit(max(1, min(4, requested)), os.cpu_count(), available_memory())


def initialize_worker():
    # Limit native threading, including libraries imported after initialization.
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[name] = '1'
    import cv2
    cv2.setNumThreads(1)


OCR_TIMEOUT_SECONDS = 600
_quarantined_slots = []
_quarantine_lock = Lock()
_reaper_thread = None
_retired_roots = set()


def configured_timeout_seconds():
    try:
        value = float(os.environ.get('FINANCE_OCR_TIMEOUT_SECONDS', OCR_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        return OCR_TIMEOUT_SECONDS
    return value if math.isfinite(value) and 30 <= value <= 3600 else OCR_TIMEOUT_SECONDS


class OCRWorkerQuarantined(BrokenProcessPool):
    ocr_quarantined = True


def _check_worker_admission_locked():
    # Retain the block until join/reap succeeds, even if exit is not yet known.
    if _quarantined_slots:
        raise OCRWorkerQuarantined('OCR worker termination blocked; check permissions or restart')


class AttachmentOCRTimeout(TimeoutError):
    """The individual OCR process was stopped; other slots are unaffected."""


class AttachmentOCRExhausted(ValueError):
    pass


def _slot_worker(connection, directory, initializer):
    # Public spawn Process/Pipe APIs also work with Windows 3.11 freeze_support.
    try:
        if initializer is not None:
            initializer()
        while connection.recv_bytes(16) == b'run':
            with (Path(directory)/'input').open('rb') as source:
                function, args, kwargs = pickle.load(source)
            try:
                reply = (True, function(*args, **kwargs))
            except Exception as error:
                reply = (False, error)
            target = Path(directory)/'output'
            try:
                with target.with_suffix('.tmp').open('wb') as output:
                    pickle.dump(reply, output, protocol=pickle.HIGHEST_PROTOCOL)
            except Exception:
                with target.with_suffix('.tmp').open('wb') as output:
                    pickle.dump((False, RuntimeError('OCR worker result serialization failed')), output)
            os.replace(target.with_suffix('.tmp'), target)
            connection.send_bytes(b'done')
    except (EOFError, BrokenPipeError):
        pass
    finally:
        connection.close()


class RecyclableOCRPool:
    """One persistent process per slot, with bounded termination and no private APIs.

    Large requests/results use private temporary files. Pipes carry only tiny
    notifications, so partial large pipe writes cannot trap a receiver on timeout.
    Each controller supervises its own slot; timeout never discards another job.
    """
    def __init__(self, workers, timeout_seconds=None, initializer=initialize_worker):
        if timeout_seconds is None:
            timeout_seconds = configured_timeout_seconds()
        if not 0 < timeout_seconds <= 3600:
            raise ValueError('Invalid OCR deadline')
        with _quarantine_lock:
            _check_worker_admission_locked()
        self.timeout_seconds = timeout_seconds
        self.initializer = initializer
        self.context = multiprocessing.get_context('spawn')
        self.closed = Event()
        self.shutdown_lock = Lock()
        self.directory = Path(mkdtemp(prefix='finance-ocr-'))
        self.slots = []
        self.available = Queue()
        for index in range(max(1, min(4, workers))):
            directory = self.directory/str(index)
            directory.mkdir(mode=0o700)
            self.slots.append(dict(process=None, connection=None, directory=directory, root=self.directory, quarantined=False, retired=False, stop_lock=Lock()))
            self.available.put(index)
        self.executor = ThreadPoolExecutor(max_workers=len(self.slots), thread_name_prefix='ocr-watch')

    def submit(self, function, *args, **kwargs):
        if self.closed.is_set():
            raise RuntimeError('OCR pool closed')
        return self.executor.submit(self._run, function, args, kwargs)

    @staticmethod
    def _stop(slot):
        with slot['stop_lock']:
            RecyclableOCRPool._stop_locked(slot)

    @staticmethod
    def _stop_locked(slot):
        process = slot['process']
        if process is not None:
            pid = process.pid
            try:
                if process.is_alive():
                    try:
                        process.terminate()
                    except OSError:
                        pass
                process.join(1)
                if process.is_alive():
                    try:
                        process.kill()
                    except OSError:
                        pass
                    process.join(1)
                alive = process.is_alive()
            except (OSError, AssertionError):
                # If process state cannot be verified, preserve its files/handle.
                alive = True
            if alive:
                slot['quarantined'] = True
                with _quarantine_lock:
                    if not any(item is slot for item in _quarantined_slots):
                        _quarantined_slots.append(slot)
                from .diagnostics import record
                record('ocr_worker_quarantined', worker_pid=pid, slot_isolated=True)
                raise OCRWorkerQuarantined('OCR process could not be terminated; slot quarantined')
            from .diagnostics import record
            record('ocr_worker_reaped', worker_pid=pid, exit_code=process.exitcode)
            process.close()
            slot['process'] = None
            slot['quarantined'] = False
            with _quarantine_lock:
                _quarantined_slots[:] = [item for item in _quarantined_slots if item is not slot]
        if slot['connection'] is not None:
            slot['connection'].close()
            slot['connection'] = None

    def _run(self, function, args, kwargs):
        index = self.available.get()
        slot = self.slots[index]
        started = monotonic()
        try:
            if self.closed.is_set():
                raise BrokenProcessPool('OCR pool closed')
            if slot['quarantined']:
                raise OCRWorkerQuarantined('OCR slot quarantined')
            with (slot['directory']/'input').open('wb') as output:
                pickle.dump((function, args, kwargs), output, protocol=pickle.HIGHEST_PROTOCOL)
            if slot['process'] is None:
                parent, child = self.context.Pipe()
                process = self.context.Process(target=_slot_worker,
                    args=(child, str(slot['directory']), self.initializer), daemon=True)
                try:
                    # Atomic with quarantine publication: automatic recovery
                    # cannot add children while an unreaped child holds a slot.
                    with _quarantine_lock:
                        _check_worker_admission_locked()
                        process.start()
                except BaseException:
                    parent.close()
                    child.close()
                    raise
                child.close()
                slot.update(process=process, connection=parent)
                from .diagnostics import record
                record('ocr_worker_spawned', worker_pid=process.pid, slot=index)
            slot['connection'].send_bytes(b'run')
            while True:
                if self.closed.is_set():
                    raise BrokenProcessPool('OCR pool closed')
                elapsed = monotonic()-started
                if elapsed >= self.timeout_seconds:
                    from .diagnostics import record
                    record('ocr_attachment_timeout', worker_pid=slot['process'].pid, slot=index,
                           elapsed_seconds=elapsed, timeout_seconds=self.timeout_seconds)
                    raise AttachmentOCRTimeout('OCR attachment exceeded its deadline')
                if slot['connection'].poll(min(.1, self.timeout_seconds-elapsed)):
                    if slot['connection'].recv_bytes(16) != b'done':
                        raise BrokenProcessPool('Invalid OCR worker notification')
                    break
                if not slot['process'].is_alive():
                    raise BrokenProcessPool('OCR worker exited before returning a result')
            with (slot['directory']/'output').open('rb') as source:
                success, value = pickle.load(source)
            if not success:
                raise value
            return value
        except (EOFError, BrokenPipeError, ConnectionResetError) as error:
            self._stop(slot)
            raise BrokenProcessPool('OCR worker connection closed unexpectedly') from error
        except BaseException:
            self._stop(slot)
            raise
        finally:
            # Erase temporary originals/results only after the child has stopped
            # or completed the request; no filename contains attachment identity.
            if not slot['quarantined']:
                for name in ('input', 'output', 'output.tmp'):
                    (slot['directory']/name).unlink(missing_ok=True)
            self.available.put(index)

    def shutdown(self, wait=True, **kwargs):
        with self.shutdown_lock:
            if self.closed.is_set():
                return
            self.closed.set()
            # Supervisors check closed every 100ms and terminate their children.
            # Never rely on Executor shutdown to stop a hung native OCR call.
            self.executor.shutdown(wait=True, cancel_futures=True)
            failures = []
            with _quarantine_lock:
                _retired_roots.add(self.directory)
            for slot in self.slots:
                slot['retired'] = True
                try:
                    self._stop(slot)
                except BrokenProcessPool as error:
                    failures.append(error)
            reap_quarantined_workers()
            _start_quarantine_reaper()
            if failures:
                # No TemporaryDirectory finalizer: live/quarantined child files
                # and public Process handles must survive pool disposal.
                raise failures[0]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.shutdown()


def reap_quarantined_workers():
    """Retry retired children, then erase originals only after confirmed reap."""
    with _quarantine_lock:
        retired = [slot for slot in _quarantined_slots if slot['retired']]
    for slot in retired:
        try:
            RecyclableOCRPool._stop(slot)
        except BrokenProcessPool:
            continue
    with _quarantine_lock:
        roots = list(_retired_roots)
        live_roots = {slot['root'] for slot in _quarantined_slots}
    for root in roots:
        if root in live_roots:
            continue
        try:
            shutil.rmtree(root)
        except FileNotFoundError:
            pass
        except OSError as error:
            from .diagnostics import record
            record('ocr_temporary_cleanup_retry', error)
            continue
        with _quarantine_lock:
            _retired_roots.discard(root)


def _quarantine_reaper():
    global _reaper_thread
    while True:
        reap_quarantined_workers()
        with _quarantine_lock:
            if not _retired_roots:
                _reaper_thread = None
                return
        Event().wait(5)


def _start_quarantine_reaper():
    global _reaper_thread
    with _quarantine_lock:
        if _retired_roots and _reaper_thread is None:
            _reaper_thread = Thread(target=_quarantine_reaper, name='ocr-reaper', daemon=True)
            _reaper_thread.start()


def analysis_pool(workers, *, timeout_seconds=None, initializer=initialize_worker):
    return RecyclableOCRPool(workers, timeout_seconds, initializer)
