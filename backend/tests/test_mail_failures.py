import errno
import json
import sqlite3
import unittest
from concurrent.futures.process import BrokenProcessPool
from app.mail_failures import AnalysisFailure, describe_failure


class MailFailureTests(unittest.TestCase):
    def test_sqlite_codes(self):
        for code, kind in [(5, 'database_busy'), (6, 'database_busy'), (13, 'disk_full'), (11, 'database_corrupt'), (26, 'database_corrupt'), (8, 'permission_denied'), (10, 'storage_io'), (1, 'unknown_db'), (5 | (2 << 8), 'database_busy')]:
            error = sqlite3.OperationalError('secret')
            error.sqlite_errorcode = code
            result = describe_failure(error)
            self.assertEqual(result['kind'], kind)
            self.assertEqual(result['retryable'], kind == 'database_busy')

    def test_narrow_fallback(self):
        for text in ['database is locked', 'database table is locked', 'database is busy']:
            self.assertTrue(describe_failure(sqlite3.OperationalError(text))['retryable'])
        for error in [ValueError('database is locked'), sqlite3.IntegrityError('database is locked'), sqlite3.OperationalError('database is locked: secret')]:
            self.assertFalse(describe_failure(error)['retryable'])

    def test_os_and_pool(self):
        for error, kind in [(OSError(errno.ENOSPC, 'secret'), 'disk_full'), (PermissionError('secret'), 'permission_denied'), (OSError(errno.EACCES, 'secret'), 'permission_denied'), (OSError('secret'), 'unknown_io'), (MemoryError('secret'), 'memory_low'), (BrokenProcessPool('secret'), 'worker_pool_broken')]:
            self.assertEqual(describe_failure(error)['kind'], kind)
        for code in [112, 39]:
            error = OSError('secret')
            error.winerror = code
            self.assertEqual(describe_failure(error)['kind'], 'disk_full')

    def test_privacy_phase_and_budget(self):
        error = AnalysisFailure(BrokenProcessPool('/private/mail password=secret'), '附件分析')
        self.assertNotIn('secret', str(error))
        result = describe_failure(error, attempt=1)
        self.assertEqual(result['phase'], '附件分析')
        self.assertNotIn('/private', json.dumps(result))
        self.assertNotIn('secret', json.dumps(result))
        self.assertIn('自动恢复第 1/3 次', result['message'])
        self.assertIn('已达到恢复上限', describe_failure(error, attempt=3)['message'])
        self.assertNotIn('自动恢复', describe_failure(MemoryError(), attempt=1)['message'])
