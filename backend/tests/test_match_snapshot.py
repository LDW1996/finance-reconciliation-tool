"""Shared derived results must not re-rank for view-only actions."""
from threading import Event
from concurrent.futures import ThreadPoolExecutor
import pytest
from app.outlook_probe import MailStore
from app.mail_workspace import MailWorkspace
from test_mail_workspace import ledger_bytes, payment


def workspace(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3');ws=MailWorkspace(store)
    ws.import_ledger(ledger_bytes(),'bank.xlsx');store.set_payments('mail',[payment()])
    return ws


def test_views_reverse_lookup_and_export_reuse_same_generation(tmp_path,monkeypatch):
    from app import mail_matching
    from app.mail_insights import related_receipts
    ws=workspace(tmp_path);calls=[];original=mail_matching.rank_receipts
    def counted(*args,**kwargs):
        calls.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(mail_matching,'rank_receipts',counted)
    first=ws.matching()
    assert len(first['rows'])==1
    assert ws.matching(scope='all',candidate_limit=20)['total_rows']==2
    assert ws.matching(scope='all',keyword='closed-ref',sort_by='score')['total_rows']==1
    assert related_receipts(ws,'mail')['total_rows']==2
    ws.export()
    assert len(calls)==1  # both ledger rows now share one batch


def test_snapshot_survives_restart_and_invalidates_on_payment_change(tmp_path,monkeypatch):
    ws=workspace(tmp_path);ws.matching()
    reopened=MailWorkspace(ws.store)
    from app import mail_matching
    original=mail_matching.rank_receipts;calls=[]
    def counted(*args,**kwargs):calls.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(mail_matching,'rank_receipts',counted)
    assert reopened.matching()['rows'][0]['candidates'][0]['payer']=='TEST PAYER'
    assert not calls
    ws.store.set_payments('mail',[{**payment(),'payer':'NEW PAYER'}])
    assert reopened.matching()['rows'][0]['candidates'][0]['payer']=='NEW PAYER'
    assert len(calls)==1  # both ledger rows now share one batch


def test_nonblocking_snapshot_shares_worker_and_preserves_success_on_failure(tmp_path,monkeypatch):
    ws=workspace(tmp_path);ws.matching();ws.store.set_payments('mail',[{**payment(),'payer':'CHANGED'}])
    from app import mail_matching
    started=Event();release=Event();calls=[]
    def fail(*args,**kwargs):
        calls.append(1);started.set();assert release.wait(5);raise RuntimeError('broken derived result')
    monkeypatch.setattr(mail_matching,'rank_receipts',fail)
    try:
        old=ws.matching_snapshot()
        assert started.wait(2)
        assert old['rows'][0]['candidates'][0]['payer']=='TEST PAYER'
        assert old['computation']['stale'] is True
        assert ws.matching_snapshot()['computation']['state']=='running'
    finally:release.set()
    assert ws.matches.wait(5)
    state=ws.match_state()
    assert state['state']=='failed'
    assert ws.matching_snapshot()['rows'][0]['candidates'][0]['payer']=='TEST PAYER'
    assert len(calls)==1
    assert ws.store.payment_rows()[0]['payer']=='CHANGED'


def test_reverse_lookup_keeps_candidate_beyond_top_three(tmp_path):
    from app.mail_insights import related_receipts
    ws=workspace(tmp_path)
    for i in range(5):ws.store.set_payments(f'm{i}',[{**payment(),'id':f'p{i}','payment_id':f'p{i}','mail_id':f'm{i}','duplicate_key':f'k{i}','amount':str(1400000+i)}])
    rows=related_receipts(ws,'m4')['rows']
    assert len(rows)==2
    assert rows[0]['candidates'][0]['payment_id']=='p4'


def test_snapshot_api_is_nonblocking_and_review_tabs_filter_without_rerank(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import mail_tasks
    ws=workspace(tmp_path);manager=mail_tasks.MailManager(ws.store)
    monkeypatch.setattr(mail_tasks,'_manager',manager)
    client=TestClient(app);headers={'X-Finance-Local':'1','Origin':'http://127.0.0.1:5173'}
    response=client.get('/api/mail/matching/snapshot',headers=headers)
    assert response.status_code==200
    assert response.json()['computation']['state'] in ('running','ready')
    assert manager.workspace.matches.wait(5)
    data=client.get('/api/mail/matching/snapshot',headers=headers).json()
    row=data['rows'][0]
    manager.workspace.review(data['ledger']['id'],row['receipt']['id'],'payment','confirmed',3)
    assert client.get('/api/mail/matching/snapshot?review_filter=pending',headers=headers).json()['total_rows']==0
    confirmed=client.get('/api/mail/matching/snapshot?review_filter=confirmed',headers=headers).json()
    assert confirmed['total_rows']==1
    assert client.get('/api/mail/matching/state',headers=headers).json()['state']=='ready'


def test_same_online_ledger_identity_with_changed_rows_rebuilds(tmp_path):
    ws=workspace(tmp_path);old=ws.matching();data=ws._dataset()
    data['entries'][0]['amount']='7654321.00'
    with ws.store.db() as db:db.execute('UPDATE mail_ledger SET metadata=?',(ws.store.encode(data),))
    new=ws.matching()
    assert new['revision']!=old['revision']
    assert new['rows'][0]['receipt']['amount']=='7654321.00'


def test_reverse_lookup_selected_duplicate_source_still_returns_receipt(tmp_path):
    from app.mail_insights import related_receipts
    ws=workspace(tmp_path)
    ws.store.set_payments('mail',[{**payment(),'attachment_sha256':'same'}])
    ws.store.set_payments('copy',[{**payment(),'id':'zzz-copy-payment','mail_id':'copy','attachment_sha256':'same'}])
    rows=related_receipts(ws,'copy',payment_id='zzz-copy-payment')['rows']
    assert len(rows)==2
    assert all(row['candidates'][0]['payment_id']=='zzz-copy-payment' for row in rows)


def test_paging_loads_full_evidence_only_for_visible_rows(tmp_path,monkeypatch):
    ws=workspace(tmp_path);ws.matching(scope='all')
    reads=[];original=ws.matches.full_row
    def counted(*args,**kwargs):reads.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(ws.matches,'full_row',counted)
    assert len(ws.matching(scope='all',page_size=1)['rows'])==1
    assert len(reads)==1


def test_data_arriving_during_calculation_never_marks_old_version_current(tmp_path,monkeypatch):
    ws=workspace(tmp_path);ws.matching();ws.store.set_payments('mail',[{**payment(),'payer':'VERSION TWO'}])
    from app import mail_matching
    started=Event();release=Event();original=mail_matching.rank_receipts
    def blocked(*args,**kwargs):started.set();assert release.wait(5);return original(*args,**kwargs)
    monkeypatch.setattr(mail_matching,'rank_receipts',blocked)
    try:
        ws.matching_snapshot();assert started.wait(2)
        ws.store.set_payments('mail',[{**payment(),'payer':'VERSION THREE'}])
    finally:release.set()
    assert ws.matches.wait(5)
    assert ws.matches.state()['stale'] is True
    assert ws.matches.state()['available'] is True
    assert ws.matching()['rows'][0]['candidates'][0]['payer']=='VERSION THREE'


def test_status_poll_does_not_decode_full_ledger_or_customer_mapping(tmp_path,monkeypatch):
    ws=workspace(tmp_path);ws.matching()
    with ws.store.db(write=False) as db:ledger=db.execute('SELECT metadata FROM mail_ledger').fetchone()[0]
    original=ws.store.decode
    def decode(value):
        assert value!=ledger,'status polling decoded the full ledger'
        return original(value)
    monkeypatch.setattr(ws.store,'decode',decode)
    assert ws.match_state()['state']=='ready'


def test_reverse_lookup_keeps_only_requested_top_evidence_in_memory(tmp_path,monkeypatch):
    from app.mail_insights import related_receipts
    ws=workspace(tmp_path)
    class Evidence(dict):
        live=0;peak=0
        def __init__(self):
            super().__init__();Evidence.live+=1;Evidence.peak=max(Evidence.peak,Evidence.live)
        def __del__(self):Evidence.live-=1
    def rows(*args,**kwargs):
        for i in range(30):
            candidate={**payment(),'score':i,'related_mail_ids':['mail'],'evidence':Evidence()}
            yield dict(receipt={'id':f'r{i}'},candidates=[candidate],candidate_count=1,warnings=[])
    monkeypatch.setattr(ws.matches,'rows',rows)
    result=related_receipts(ws,'mail',limit=2)
    assert result['total_rows']==30
    assert [r['candidates'][0]['score'] for r in result['rows']]==[29,28]
    assert Evidence.peak<=6


def test_english_matching_failure_keeps_actionable_reason():
    from app.mail_english import response
    result=response({'computation':{'error':'匹配结果保存空间不足，请检查保存容量和磁盘剩余空间后重试。'}})
    assert result['computation']['error']=='Insufficient space for matching results. Check the storage limit and free disk space, then retry.'


def test_recognition_load_defers_matching_and_keeps_last_result(tmp_path):
    ws=workspace(tmp_path);ws.matching()
    ws.store.set_payments('new',[payment()])
    ws.matches.load_status=lambda:dict(active=2,pending=12,workers=2)
    state=ws.match_state()
    assert state['state']=='waiting' and state['available'] and state['stale']
    assert state['recognition_active']==2
    assert ws.matches.running is None
    ws.matches.load_status=lambda:dict(active=0,pending=0,workers=2)
    ws.matches.quiet_seconds=0
    ws.matches.request(wait=True)
    assert ws.match_state()['state']=='ready'
