"""Match generation lifecycle: reuse, failure isolation, retention and reads.

The index is derived data rebuilt in a background thread. These tests pin the
promises the rest of the mail workspace relies on: an unchanged signature must
not trigger a rebuild, a failed build must publish nothing, and readers must be
able to finish against the generation they started with.
"""
from io import BytesIO

import openpyxl
import pytest

from app.outlook_probe import MailStore


def ledger_bytes(rows=2):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = 'Bank Transactions'
    sheet.append(['NO', 'EFF DATE', 'DESCRIPTION', 'CREDIT', 'BUSINESS TYPE', 'REF NO', 'CLOSING STATUS'])
    for index in range(1, rows + 1):
        sheet.append([index, '25 Sep 2026', 'BFS FR BANK UN TEST PAYER', 1400000, 'FUEL',
                      f'00123456{index:04d}', 'UNIDENTIFIED'])
    out = BytesIO()
    workbook.save(out)
    return out.getvalue()


def payment():
    return dict(id='payment', payment_id='payment', mail_id='mail', attachment_id='attachment', page=1,
                payer='TEST PAYER', date='2026-09-25', amount='1400000.00', currency='IDR',
                subject='Payment TESTBL123456', sender='sender@example.invalid',
                received='2026-09-26T12:00:00+08:00', keyword='TESTBL123456',
                business_ids=['TESTBL123456'], remark='=Not a formula',
                search_info='主题：Payment TESTBL123456\n发件人：sender@example.invalid',
                evidence={}, warnings=[], duplicate_key='evidence')


@pytest.fixture
def workspace(tmp_path):
    from app.mail_workspace import MailWorkspace
    store = MailStore(tmp_path / 'mail.sqlite3')
    return MailWorkspace(store)


def build(workspace, window=3):
    """Run one build to completion without relying on the request loop."""
    workspace.matches.request(window, wait=True)
    return workspace.matches.metadata(window)


def test_unchanged_signature_reuses_generation(workspace):
    workspace.import_ledger(ledger_bytes(), 'bank.xlsx')
    workspace.store.set_payments('mail', [payment()])
    first = build(workspace, 3)
    assert first and first['signature']
    build(workspace, 3)
    assert workspace.matches.metadata(3)['generation'] == first['generation']


def test_state_goes_stale_then_ready(workspace):
    workspace.import_ledger(ledger_bytes(), 'bank.xlsx')
    before = workspace.matches.state(3)
    assert before['stale'] is True and before['available'] is False
    build(workspace, 3)
    after = workspace.matches.state(3)
    assert after['state'] == 'ready' and after['stale'] is False and after['available'] is True


def test_ledger_change_invalidates_signature(workspace):
    workspace.import_ledger(ledger_bytes(), 'bank.xlsx')
    build(workspace, 3)
    workspace.import_ledger(ledger_bytes(rows=3), 'bank.xlsx')
    assert workspace.matches.state(3)['stale'] is True
    second = build(workspace, 3)
    assert second is not None


def test_new_payments_invalidate_signature(workspace):
    """新邮件入库必须让旧匹配失效，否则财务看到的候选是过期快照。"""
    workspace.import_ledger(ledger_bytes(), 'bank.xlsx')
    before = build(workspace, 3)['signature']
    workspace.store.set_payments('mail', [payment()])
    assert workspace.matches.identity(3)[0] != before
    assert workspace.matches.state(3)['stale'] is True


def test_failed_build_publishes_nothing(workspace, monkeypatch):
    """第一笔先真正入库，第二笔才失败——否则表本来就是空的，清理逻辑等于没测。"""
    from app import mail_matching
    workspace.import_ledger(ledger_bytes(rows=3), 'bank.xlsx')
    real = mail_matching.rank_receipts
    calls = []

    def explode_on_second(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError('ranking unavailable')
        return real(*args, **kwargs)

    monkeypatch.setattr(mail_matching, 'rank_receipts', explode_on_second)
    workspace.matches.request(3, wait=False)
    workspace.matches.wait(10)
    assert len(calls) > 1, '至少一笔成功入库后才失败，才能验证部分结果被清理'
    state = workspace.matches.state(3)
    assert state['state'] == 'failed'
    assert 'RuntimeError' in state['error']
    assert workspace.matches.metadata(3) is None
    with workspace.store.db(write=False) as db:
        assert db.execute('SELECT COUNT(*) FROM mail_match_row').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM mail_match_related').fetchone()[0] == 0


def test_failure_blocks_repeat_until_retry(workspace, monkeypatch):
    from app import mail_matching
    workspace.import_ledger(ledger_bytes(), 'bank.xlsx')
    calls = []

    def explode(*args, **kwargs):
        calls.append(1)
        raise RuntimeError('ranking unavailable')

    monkeypatch.setattr(mail_matching, 'rank_receipts', explode)
    workspace.matches.request(3, wait=False)
    workspace.matches.wait(10)
    with pytest.raises(ValueError, match='RuntimeError'):
        workspace.matches.request(3, wait=True)
    assert len(calls) == 1
    workspace.matches.request(3, wait=False, retry=True)
    workspace.matches.wait(10)
    assert len(calls) == 2


def test_full_row_and_rows_return_the_same_receipt(workspace):
    workspace.import_ledger(ledger_bytes(), 'bank.xlsx')
    workspace.store.set_payments('mail', [payment()])
    meta = build(workspace, 3)
    rows = list(workspace.matches.rows(3, full=False))
    assert len(rows) == 2
    receipt_id = rows[0]['receipt']['id']
    full = workspace.matches.full_row(receipt_id, meta['generation'])
    assert full['receipt']['id'] == receipt_id
    assert full['candidates']
    assert workspace.matches.full_row('missing-receipt', meta['generation']) is None


def test_rows_page_through_more_than_one_block(workspace):
    workspace.import_ledger(ledger_bytes(rows=40), 'bank.xlsx')
    build(workspace, 3)
    rows = list(workspace.matches.rows(3))
    assert len(rows) == 40
    assert [row['_generation'] for row in rows] == [rows[0]['_generation']] * 40
    assert len({row['receipt']['id'] for row in rows}) == 40


def test_rows_filter_by_related_mail(workspace):
    workspace.import_ledger(ledger_bytes(), 'bank.xlsx')
    workspace.store.set_payments('mail', [payment()])
    build(workspace, 3)
    every = list(workspace.matches.rows(3))
    matched = list(workspace.matches.rows(3, mail_id='mail'))
    # 两笔流水条件相同，都被同一封邮件命中；关联表 join 不能把行数放大。
    assert len(every) == len(matched) == 2
    assert len({row['receipt']['id'] for row in matched}) == 2
    assert list(workspace.matches.rows(3, mail_id='unknown-mail')) == []


def test_reader_keeps_its_generation_alive(workspace):
    """翻页途中后台重建，不能把读者手里那一代的行删掉，否则用户看到空白。"""
    workspace.import_ledger(ledger_bytes(), 'bank.xlsx')
    build(workspace, 3)
    with workspace.matches.view(3) as held:
        assert held is not None
        # 新增付款让签名变化，但账本 id 不变，读者仍能用旧 metadata 读。
        workspace.store.set_payments('mail', [payment()])
        key, data = workspace.matches.identity(3)
        workspace.matches._build(key, 3, data)
        assert workspace.matches.metadata(3)['generation'] != held['generation'], '应已发布新一代'
        # 此时旧代已不在 generation 表里，成了孤儿；再一次构建才会触发清理。
        key5, data5 = workspace.matches.identity(5)
        workspace.matches._build(key5, 5, data5)
        rows = list(workspace.matches.rows(3, metadata=held))
        assert len(rows) == 2, '读者持有的 generation 被后台构建清理了'
    assert workspace.matches.metadata(3)['generation'] != held['generation']


def test_only_three_day_windows_are_retained(workspace):
    workspace.import_ledger(ledger_bytes(), 'bank.xlsx')
    for window in (1, 2, 3, 4):
        build(workspace, window)
    with workspace.store.db(write=False) as db:
        kept = {row[0] for row in db.execute('SELECT day_window FROM mail_match_generation')}
    assert len(kept) == 3
    assert 1 not in kept
    assert workspace.matches.metadata(4) is not None
