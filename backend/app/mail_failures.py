"""Privacy-safe classification of background analysis failures."""
import errno
import sqlite3
from concurrent.futures.process import BrokenProcessPool
from datetime import datetime, timezone


class AnalysisFailure(Exception):
    def __init__(self, cause: Exception, phase: str = '任务调度'):
        self.cause = cause
        self.phase = phase
        super().__init__('分析任务执行失败')


def describe_failure(error, phase='任务调度', attempt=0, limit=3):
    while isinstance(error, AnalysisFailure):
        phase = error.phase
        error = error.cause
    code = getattr(error, 'sqlite_errorcode', None) if isinstance(error, sqlite3.Error) else None
    base = code & 0xff if isinstance(code, int) else None
    kind = 'internal_error'
    retryable = False
    if isinstance(error, BrokenProcessPool):
        kind, retryable = 'worker_pool_broken', True
    elif isinstance(error, MemoryError):
        kind = 'memory_low'
    elif base in (5, 6) or (code is None and isinstance(error, sqlite3.OperationalError) and str(error).strip().lower() in ('database is locked', 'database table is locked', 'database is busy')):
        kind, retryable = 'database_busy', True
    elif base == 13 or (isinstance(error, OSError) and (error.errno == errno.ENOSPC or getattr(error, 'winerror', None) in (112, 39))):
        kind = 'disk_full'
    elif base in (11, 26):
        kind = 'database_corrupt'
    elif base == 8 or isinstance(error, PermissionError) or (isinstance(error, OSError) and error.errno in (errno.EACCES, errno.EPERM)):
        kind = 'permission_denied'
    elif base == 10:
        kind = 'storage_io'
    elif isinstance(error, sqlite3.Error):
        kind = 'unknown_db'
    elif isinstance(error, OSError):
        kind = 'unknown_io'
    details = {
        'worker_pool_broken': ('分析进程意外退出', '重新建立分析进程后重试'),
        'database_busy': ('数据库暂时被占用', '稍后重新执行任务'),
        'disk_full': ('存储空间不足', '释放磁盘空间后手动重试'),
        'database_corrupt': ('数据库文件异常', '检查并修复数据库或从备份恢复'),
        'permission_denied': ('存储访问权限不足', '检查存储访问权限后手动重试'),
        'memory_low': ('可用内存不足', '释放内存后手动重试'),
        'storage_io': ('存储读写失败', '检查磁盘和存储连接后手动重试'),
        'unknown_db': ('数据库操作失败', '检查数据库状态后手动重试'),
        'unknown_io': ('文件读写失败', '检查存储状态后手动重试'),
        'internal_error': ('任务执行异常', '检查任务状态后手动重试'),
    }
    reason, action = details[kind]
    recovery = (f'自动恢复第 {attempt}/{limit} 次' if attempt < limit else '已达到恢复上限，任务暂停') if retryable else '任务暂停，需处理后手动重试'
    return dict(kind=kind, phase=phase, error_type=type(error).__name__, error_code=code if isinstance(code, int) else (error.errno if isinstance(error, OSError) else None), retryable=retryable, attempt=attempt, limit=limit, reason=reason, action=action, message=f'{phase}失败（{type(error).__name__}）：{reason}；{recovery}（{attempt}/{limit}）。{action}', at=datetime.now(timezone.utc).isoformat())
