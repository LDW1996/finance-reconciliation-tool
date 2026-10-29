from io import BytesIO
import hashlib
from PIL import Image
from app.outlook_probe import MailStore


def image_bytes():
    out=BytesIO();Image.new('RGB',(40,40),'white').save(out,format='PNG');return out.getvalue()


def test_identical_raw_attachments_share_one_blob_but_keep_two_sources(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3');raw=image_bytes()
    store.save([dict(id=key,body=key,_attachments=[dict(name=key+'.png',raw=raw,status='pending')]) for key in ('a','b')])
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM mail_blob').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM mail_attachment').fetchone()[0]==2
        assert db.execute('SELECT SUM(LENGTH(content)) FROM mail_attachment').fetchone()[0]==0
    for key in ('a','b'):
        asset=store.get(key)['attachment_details'][0]
        assert store.attachment(key,asset['id'])['raw']==raw


def test_new_recognition_results_do_not_persist_preview_images(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3');store.save([dict(id='a',_attachments=[dict(name='a.png',raw=image_bytes(),status='pending')])])
    asset=store.get('a')['attachment_details'][0]
    store.update_attachment('a',asset['id'],status='done',result=dict(pages=[dict(number=1,preview='x'*100000,text='付款文字')],fields={}))
    with store.db() as db:
        metadata=store.decode(db.execute('SELECT metadata FROM mail_attachment').fetchone()[0])
    assert not metadata['result']['pages'][0].get('preview')
    assert metadata['result']['pages'][0]['text']=='付款文字'


def test_on_demand_preview_is_encrypted_bounded_and_can_be_rebuilt(tmp_path):
    from app.mail_assets import PreviewCache
    store=MailStore(tmp_path/'mail.sqlite3');raw=image_bytes()
    store.save([dict(id='a',_attachments=[dict(name='a.png',raw=raw,status='done')])])
    aid=store.get('a')['attachment_details'][0]['id']
    cache=PreviewCache(store)
    first=cache.get('a',aid,1)
    assert first.startswith(b'\x89PNG')
    files=list(cache.directory.glob('*.cache'))
    assert files and not files[0].read_bytes().startswith(b'\x89PNG')
    cache.clear()
    assert cache.get('a',aid,1)==first
    assert cache.used_bytes()<=cache.limit_bytes()


def legacy_store(tmp_path):
    import base64
    store=MailStore(tmp_path/'mail.sqlite3');raw=image_bytes()
    store.save([dict(id=key,body='保留正文',_attachments=[dict(name='proof.png',raw=raw,status='done')]) for key in ('a','b')])
    with store.db() as db:
        for identity,payload in db.execute('SELECT id,metadata FROM mail_attachment').fetchall():
            metadata=store.decode(payload)
            metadata['result']=dict(pages=[dict(number=1,text='保留识别文字',preview=base64.b64encode(raw).decode()*3000)],fields={})
            db.execute('UPDATE mail_attachment SET metadata=?,content=? WHERE id=?',(store.encode(metadata),store.cipher.encrypt(raw),identity))
        db.execute('DELETE FROM mail_attachment_blob_ref');db.execute('DELETE FROM mail_blob')
    return store,raw


def test_legacy_optimization_preserves_evidence_reviews_and_reclaims_file(tmp_path):
    from app.mail_maintenance import MailMaintenance
    from app.mail_workspace import MailWorkspace
    store,raw=legacy_store(tmp_path);MailWorkspace(store)
    with store.db() as db:db.execute('INSERT INTO mail_review VALUES(?,?)',('receipt',store.encode(dict(state='confirmed',payment_id='original'))))
    maintenance=MailMaintenance(store)
    preview=maintenance.preview()
    assert preview['preview_pages']==2 and preview['duplicate_bytes']>0
    before=store.path.stat().st_size
    result=maintenance.optimize(compact=True)
    assert result['compacted'] and store.path.stat().st_size<before
    assert store.mail_count()==2
    with store.db() as db:
        assert store.decode(db.execute('SELECT payload FROM mail_review').fetchone()[0])['state']=='confirmed'
        assert db.execute('SELECT COUNT(*) FROM mail_blob').fetchone()[0]==1
        for payload, in db.execute('SELECT metadata FROM mail_attachment'):
            metadata=store.decode(payload)
            assert metadata['result']['pages'][0]['text']=='保留识别文字'
            assert not metadata['result']['pages'][0].get('preview')
    for identity in ('a','b'):
        asset=store.get(identity)['attachment_details'][0]
        assert store.attachment(identity,asset['id'])['raw']==raw
    assert maintenance.optimize(compact=False)['errors']==0


def test_preview_without_rebuildable_original_is_protected(tmp_path):
    from app.mail_maintenance import MailMaintenance
    store,raw=legacy_store(tmp_path)
    with store.db() as db:db.execute('UPDATE mail_attachment SET content=?',(store.cipher.encrypt(b''),))
    result=MailMaintenance(store).optimize(compact=False)
    assert result['protected_preview_pages']==2
    with store.db() as db:
        assert all(store.decode(row[0])['result']['pages'][0]['preview'] for row in db.execute('SELECT metadata FROM mail_attachment'))


def test_cache_eviction_is_bounded_and_keeps_originals(tmp_path,monkeypatch):
    from app.mail_assets import PreviewCache
    store=MailStore(tmp_path/'mail.sqlite3')
    for key,color in [('one','red'),('two','blue')]:
        out=BytesIO();Image.new('RGB',(40,40),color).save(out,format='PNG')
        store.save([dict(id=key,_attachments=[dict(name=key+'.png',raw=out.getvalue(),status='done')])])
    cache=PreviewCache(store);monkeypatch.setattr(cache,'limit_bytes',lambda:300)
    first_id=store.get('one')['attachment_details'][0]['id']
    first=cache.get('one',first_id,1)
    second_id=store.get('two')['attachment_details'][0]['id']
    cache.get('two',second_id,1)
    assert cache.used_bytes()<=300 and len(cache.files())<=1
    assert cache.get('one',first_id,1)==first
    assert store.attachment('one',first_id)['raw']


def test_corrupted_shared_original_never_replaces_legacy_evidence(tmp_path):
    from app.mail_maintenance import MailMaintenance
    store,raw=legacy_store(tmp_path)
    with store.db() as db:db.execute('INSERT INTO mail_blob VALUES(?,?)',(hashlib.sha256(raw).hexdigest(),store.cipher.encrypt(b'broken')))
    result=MailMaintenance(store).optimize(compact=False)
    assert result['errors']==2
    with store.db() as db:
        for metadata,content in db.execute('SELECT metadata,content FROM mail_attachment'):
            assert store.cipher.decrypt(content)==raw
            assert store.decode(metadata)['result']['pages'][0]['preview']


def test_maintenance_api_requires_preview_and_exposes_progress(tmp_path,monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from app.main import app
    from app import mail_tasks
    from app.mail_tasks import MailManager
    store,raw=legacy_store(tmp_path)
    for key in ('a','b'):store.set_payments(key,[])
    manager=MailManager(store,reader=lambda *x:dict(rows=[],errors=[],partial=False))
    monkeypatch.setattr(mail_tasks,'_manager',manager)
    client=TestClient(app);headers={'X-Finance-Local':'1'}
    assert client.post('/api/mail/storage/maintenance/preview').status_code==403
    assert client.post('/api/mail/storage/maintenance/optimize',headers=headers,json=dict(confirmed=True,preview_id='none')).status_code==409
    response=client.post('/api/mail/storage/maintenance/preview',headers=headers)
    assert response.status_code==202
    def done():
        for _ in range(300):
            job=client.get('/api/mail/storage/maintenance',headers=headers).json()
            if job.get('status')!='running':return job
            time.sleep(.01)
        raise AssertionError('Maintenance did not finish')
    preview=done();assert preview['status']=='completed'
    response=client.post('/api/mail/storage/maintenance/optimize',headers=headers,json=dict(confirmed=True,preview_id=preview['id'],compact=True))
    assert response.status_code==202
    job=done();assert job['status']=='completed' and job['result']['compacted']
    aid=store.get('a')['attachment_details'][0]['id']
    preview=client.get(f'/api/mail/messages/a/attachments/{aid}/preview/1',headers=headers)
    assert preview.status_code==200 and preview.content.startswith(b'\x89PNG')
    stats=client.get('/api/mail/storage',headers=headers).json()
    assert stats['preview_cache_bytes']>0 and stats['unique_attachments']==1


def test_not_enough_compaction_space_keeps_successful_cleanup(tmp_path,monkeypatch):
    import app.mail_maintenance as maintenance_module
    store,raw=legacy_store(tmp_path)
    monkeypatch.setattr(maintenance_module.shutil,'disk_usage',lambda path:type('Space',(),{'free':512*1024**2})())
    result=maintenance_module.MailMaintenance(store).optimize(compact=True)
    assert not result['compacted'] and result['compact_note']
    aid=store.get('a')['attachment_details'][0]['id']
    assert store.attachment('a',aid)['raw']==raw
    with store.db() as db:assert not store.decode(db.execute('SELECT metadata FROM mail_attachment').fetchone()[0])['result']['pages'][0].get('preview')


def test_maintenance_drains_active_ocr_and_resumes_pending_queue(tmp_path,monkeypatch):
    import time
    from threading import Event
    from app.mail_tasks import MailManager
    from app.mail_maintenance import MailMaintenance
    store=MailStore(tmp_path/'mail.sqlite3');started=Event();release=Event();checking=Event();finish=Event();calls=[]
    for key in ['one','two']:
        store.save([dict(id=key,_attachments=[dict(name=key+'.png',raw=key.encode(),status='pending')])])
    def analyze(raw,name):
        calls.append(name);started.set()
        assert release.wait(5)
        return dict(pages=[],fields={},warnings=[])
    original=MailMaintenance.preview
    def preview(self,progress):
        checking.set();assert finish.wait(5)
        return original(self,progress)
    monkeypatch.setattr(MailMaintenance,'preview',preview)
    manager=MailManager(store,analyzer=analyze,analysis_workers=1)
    assert started.wait(5)
    manager.maintain_storage('preview')
    assert manager.maintenance_active
    release.set();assert checking.wait(5)
    assert len(calls)==1
    assert len(manager.analysis_queue)==1
    finish.set()
    for _ in range(500):
        if not manager.maintenance_active and not manager.status()['analysis_busy']:break
        time.sleep(.01)
    assert sorted(calls)==['one.png','two.png']
    assert manager.maintenance_job()['status']=='completed'
    assert len(manager.analysis_queue)==0


def test_failed_migration_transaction_can_be_retried_without_losing_original(tmp_path,monkeypatch):
    import app.mail_maintenance as module
    store,raw=legacy_store(tmp_path)
    original=module.save_blob
    def fail_after_insert(*args):
        original(*args)
        raise RuntimeError('simulated interruption before reference commit')
    monkeypatch.setattr(module,'save_blob',fail_after_insert)
    result=module.MailMaintenance(store).optimize(compact=False)
    assert result['errors']==2
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM mail_blob').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM mail_attachment_blob_ref').fetchone()[0]==0
    monkeypatch.setattr(module,'save_blob',original)
    assert module.MailMaintenance(store).optimize(compact=False)['errors']==0
    aid=store.get('a')['attachment_details'][0]['id']
    assert store.attachment('a',aid)['raw']==raw


def test_completed_preview_cannot_reject_next_job_while_history_is_persisted(tmp_path,monkeypatch):
    """Force the Windows-observed gap between publishing completion and unlocking."""
    from threading import Event, Thread
    from app.mail_tasks import MailManager
    store,raw=legacy_store(tmp_path)
    for key in ('a','b'):store.set_payments(key,[])
    manager=MailManager(store,reader=lambda *x:dict(rows=[],errors=[],partial=False))
    published=Event();release=Event();finished=Event();outcome=[]
    original=store.set_state
    def delayed_history(key,value):
        if key=='mail_maintenance_history' and value[0]['mode']=='preview':
            published.set()
            assert release.wait(5)
        return original(key,value)
    monkeypatch.setattr(store,'set_state',delayed_history)
    manager.maintain_storage('preview')
    assert published.wait(5)
    preview=manager.maintenance_job()
    assert preview['status']=='completed'
    def start_next():
        try:outcome.append(manager.maintain_storage('optimize',preview['id'],compact=False))
        except ValueError as error:outcome.append(error)
        finally:finished.set()
    caller=Thread(target=start_next);caller.start()
    # Old code immediately rejects with 409 while maintenance_active is true.
    rejected_early=finished.wait(.1)
    release.set();caller.join(5)
    assert not rejected_early, outcome
    assert finished.is_set() and isinstance(outcome[0],dict), outcome
    import time
    for _ in range(500):
        if not manager.maintenance_active:break
        time.sleep(.01)
    assert manager.maintenance_job()['status']=='completed'
