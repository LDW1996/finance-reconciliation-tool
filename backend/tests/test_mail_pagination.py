from datetime import datetime,timedelta,timezone
from app.outlook_probe import MailStore


def test_all_saved_mail_is_accessible_beyond_500_and_searches_old_records(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3')
    store.save([dict(id=str(i),subject='old-needle' if i==0 else f'mail {i}',body='private body',sender='a',received='',attachments=[],candidates=[]) for i in range(971)])
    result=store.message_page(page=20,page_size=50)
    assert result['total']==971 and result['total_saved']==971 and len(result['rows'])==21
    assert all('body' not in r for r in result['rows'])
    result=store.message_page(keyword='old-needle')
    assert [r['id'] for r in result['rows']]==['0']


def test_payment_filter_and_payer_search_cover_entire_store(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3')
    store.save([dict(id=str(i),subject='test',body='body') for i in range(510)])
    store.set_payment_summary('0',dict(count=1,status='ready',payers=['Historical Payer'],dates=['2025-09-01'],amounts=[]))
    result=store.message_page(keyword='Historical Payer',payment_filter='payment')
    assert result['total']==1 and result['total_saved']==510
    assert result['rows'][0]['id']=='0'


def test_restricted_time_overlap_does_not_stall_paging():
    from app.outlook_probe import scan_folder_page
    from test_outlook_probe import Mail
    boundary=datetime(2026,10,22,tzinfo=timezone.utc)
    messages=[]
    for i in range(400):
        item=Mail('newer'+str(i));item.ReceivedTime=boundary+timedelta(microseconds=500000);messages.append(item)
    item=Mail('older');item.ReceivedTime=boundary-timedelta(seconds=1);messages.append(item)
    class Items:
        Count=len(messages)
        def Restrict(self,*a):return self
        def Sort(self,*a):pass
        def Item(self,i):return messages[i-1]
    folder=type('Folder',(),{'Items':Items()})()
    result=scan_folder_page(folder,'s',boundary-timedelta(days=1),boundary+timedelta(days=1),100,{'before':boundary.isoformat(),'seen':[]})
    assert len(result['rows'])==1 and not result['partial']


def test_paged_api_returns_total_and_preserves_local_access_check(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import mail_tasks
    store=MailStore(tmp_path/'mail.sqlite3')
    manager=mail_tasks.MailManager(store)
    store.save([dict(id=str(i),subject='page test',body='private body',sender='',received='',attachments=[],candidates=[]) for i in range(501)])
    monkeypatch.setattr(mail_tasks,'_manager',manager)
    client=TestClient(app)
    assert client.get('/api/mail/messages/page').status_code==403
    result=client.get('/api/mail/messages/page?page=11&page_size=50',headers={'X-Finance-Local':'1'})
    assert result.status_code==200
    data=result.json()
    assert data['total_saved']==501 and len(data['rows'])==1
    assert 'body' not in data['rows'][0]
    assert client.get('/api/mail/status',headers={'X-Finance-Local':'1'}).json()['saved_total']==501


def test_5000_identical_timestamps_resume_without_repeated_prefix_scans():
    import json
    from app.outlook_probe import scan_folder_page
    from test_outlook_probe import Mail
    stamp=datetime(2026,10,22,tzinfo=timezone.utc)
    messages=[Mail(str(i)) for i in range(5000)]
    mapping={m.EntryID:m for m in messages}
    class Items:
        Count=len(messages)
        reads=0
        def Restrict(self,*a):return self
        def Sort(self,*a):pass
        def Item(self,i):self.reads+=1;return messages[i-1]
    items=Items()
    class Session:
        def GetItemFromID(self,identity,store):return mapping[identity]
    folder=type('Folder',(),{'Items':items,'Session':Session()})()
    cursor=None;ids=[]
    for _ in range(60):
        page=scan_folder_page(folder,'s',stamp-timedelta(days=1),stamp+timedelta(days=1),100,cursor)
        ids.extend(row['id'] for row in page['rows'])
        if not page['partial']:break
        assert page['cursor']!=cursor
        cursor=json.loads(json.dumps(page['cursor']))  # restart-safe persisted cursor
        messages.reverse()  # Outlook can reorder rows with equal ReceivedTime.
    else:raise AssertionError('Same-timestamp paging never completed')
    assert len(ids)==len(set(ids))==5000
    assert items.reads<10000  # Metadata read once, not N prefixes over N pages.


def test_equal_time_id_snapshot_continues_to_older_fractional_bucket():
    from app.outlook_probe import scan_folder_page
    from test_outlook_probe import Mail
    stamp=datetime(2026,10,22,tzinfo=timezone.utc)
    messages=[]
    for i in range(2200):
        row=Mail(str(i));row.ReceivedTime=stamp+timedelta(microseconds=500000 if i<1500 else 0);messages.append(row)
    mapping={m.EntryID:m for m in messages}
    class Items:
        Count=len(messages)
        def Restrict(self,*a):return self
        def Sort(self,*a):messages.sort(key=lambda m:m.ReceivedTime,reverse=True)
        def Item(self,i):return messages[i-1]
    class Session:
        def GetItemFromID(self,identity,store):return mapping[identity]
    folder=type('Folder',(),{'Items':Items(),'Session':Session()})()
    cursor=None;ids=[]
    for _ in range(30):
        result=scan_folder_page(folder,'s',stamp-timedelta(days=1),stamp+timedelta(days=1),100,cursor)
        ids.extend(row['id'] for row in result['rows'])
        if not result['partial']:break
        cursor=result['cursor'];messages.reverse()
    else:raise AssertionError('Did not reach older timestamp bucket')
    assert len(ids)==len(set(ids))==2200


def test_payment_filter_excludes_partial_and_pending_candidates(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3')
    store.save([dict(id=identity,subject=identity,body='') for identity in ('ready','partial','pending','empty')])
    for identity in ('ready','partial','pending','empty'):
        store.set_payment_summary(identity,dict(count=0 if identity=='empty' else 1,status=identity))
    assert [r['id'] for r in store.message_page(payment_filter='payment')['rows']]==['ready']
    assert [r['id'] for r in store.message_page(payment_filter='partial')['rows']]==['partial']
    assert [r['id'] for r in store.message_page(payment_filter='pending')['rows']]==['pending']
