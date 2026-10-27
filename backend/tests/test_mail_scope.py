from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

from app import outlook_probe as probe
from app.outlook_probe import MailStore


class Collection:
    def __init__(self, rows): self.rows=rows; self.Count=len(rows)
    def Item(self, index): return self.rows[index-1]


def folder(identity, name, children=()):
    return SimpleNamespace(EntryID=identity, Name=name, FolderPath=name, DefaultItemType=0, Folders=Collection(list(children)))


def session_for(monkeypatch, roots):
    stores=[SimpleNamespace(StoreID=key, DisplayName=name, GetRootFolder=lambda root=root:root) for key,name,root in roots]
    mapping={(s.StoreID,f.EntryID):f for s in stores for f in walk(s.GetRootFolder())}
    session=SimpleNamespace(Stores=Collection(stores),GetFolderFromID=lambda fid,sid:mapping[(sid,fid)])
    @contextmanager
    def opened(): yield session
    monkeypatch.setattr(probe,'outlook_session',opened)


def walk(root):
    yield root
    for child in root.Folders.rows: yield from walk(child)


def test_directory_tree_includes_archive_siblings_and_deep_folders(monkeypatch):
    leaf=folder('payment','巴淡收款')
    for i in range(7): leaf=folder(str(i),'层'+str(i),[leaf])
    session_for(monkeypatch,[('pst','archive.pst',folder('root','存档',[folder('inbox','收件箱'),leaf]))])
    result=probe.list_folders()
    assert any(r['id']=='payment' for r in result['folders'])
    assert next(r for r in result['folders'] if r['id']=='6')['parent']=='root'
    assert result['folders'][0]['store_label']=='archive.pst'


def test_scope_pages_share_limit_and_resume_without_duplicate_children(monkeypatch):
    from app.mail_scope import read_scope_page
    root=folder('r','存档',[folder('a','业务'),folder('b','巴淡')])
    session_for(monkeypatch,[('pst','archive',root)])
    calls=[]
    def scan(f,s,start,end,limit,cursor):
        calls.append((f.EntryID,limit,cursor))
        return dict(rows=[dict(id=f.EntryID,source='outlook')],errors=[],partial=False,cursor=None,read=1)
    monkeypatch.setattr(probe,'scan_folder_page',scan)
    selections=[dict(store='pst',folder='r'),dict(store='pst',folder='a')]
    now=datetime(2026,10,27,tzinfo=timezone.utc)
    page=read_scope_page(selections,True,now,now,2)
    assert len(page['rows'])==2 and page['partial']
    second=read_scope_page(selections,True,now,now,2,page['cursor'])
    assert not second['partial']
    assert [r['id'] for r in page['rows']+second['rows']]==['r','a','b']
    assert calls[1][1]==1


def test_duplicate_archive_copies_merge_sources_but_not_different_content(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3')
    base=dict(source='outlook',internet_message_id='<same@example.com>',subject='付款',body='金额 100',sender='payer@example.com',received='2026-10-01',attachments=[])
    assert store.save([{**base,'id':'a','folder':'邮箱 / 收件箱'}])==1
    assert store.save([{**base,'id':'b','folder':'存档 / 巴淡'}])==0
    assert len(store.rows())==1
    assert len(store.get('a')['sources'])==2
    assert store.save([{**base,'id':'c','body':'金额 200','folder':'其他'}])==1


def test_fixed_date_window_is_persistent_and_does_not_turn_incremental(tmp_path):
    from app.mail_sync import MailSync
    sync=MailSync(MailStore(tmp_path/'mail.sqlite3'))
    now=datetime(2026,10,27,tzinfo=timezone.utc)
    p=sync.plan('pst','scope',90,now,start_date='2026-08-01',end_date='2026-08-31')
    assert datetime.fromisoformat(p['end']).astimezone(timezone.utc).day==31  # UTC+8 end-of-day boundary
    sync.commit(p,dict(rows=[],partial=False,cursor=None))
    assert sync.plan('pst','scope',90,now,start_date='2026-08-01',end_date='2026-08-31')['complete']


def test_one_failed_directory_does_not_block_next(monkeypatch):
    from app.mail_scope import read_scope_page
    session_for(monkeypatch,[('pst','archive',folder('r','存档',[folder('a','无权'),folder('b','正常')]))])
    def scan(f,*args):
        if f.EntryID=='a': raise RuntimeError('permission')
        return dict(rows=[dict(id=f.EntryID)],errors=[],partial=False,cursor=None)
    monkeypatch.setattr(probe,'scan_folder_page',scan)
    now=datetime.now(timezone.utc)
    result=read_scope_page([dict(store='pst',folder='a'),dict(store='pst',folder='b')],False,now,now,100)
    assert [r['id'] for r in result['rows']]==['b']
    assert '无权' in result['errors'][0]


def test_same_amount_without_message_identity_is_not_merged(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3')
    base=dict(source='outlook',body='付款 100',subject='付款',attachments=[])
    assert store.save([{**base,'id':'a'},{**base,'id':'b'}])==2


def test_partial_attachment_copies_are_not_merged(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3')
    base=dict(source='outlook',internet_message_id='<same>',body='100',subject='付款',attachments=['pay.png'])
    assert store.save([{**base,'id':'a'},{**base,'id':'b'}])==2


def test_scope_api_validates_dates_and_accepts_multiple_folders():
    import pytest
    from pydantic import ValidationError
    from app.mail_tasks import MailSelection
    targets=[dict(store='pst',folder='a'),dict(store='other',folder='b')]
    selection=MailSelection(folders=targets,start_date='2026-08-01',end_date='2026-09-01')
    assert len(selection.options()['folders'])==2
    assert selection.options()['start_date']=='2026-08-01'
    for data in [dict(folders=[]),dict(folders=targets,start_date='2026-08-01'),dict(folders=targets,start_date='2026-09-01',end_date='2026-08-01')]:
        with pytest.raises(ValidationError): MailSelection(**data)


def test_manager_persists_manual_scope_and_continues_after_restart(tmp_path,monkeypatch):
    from app.mail_tasks import MailManager
    from app import mail_scope
    from test_mail_tasks import wait
    calls=[]
    def reader(targets,children,start,end,limit,cursor):
        calls.append(cursor)
        return dict(rows=[],errors=[],partial=cursor is None,cursor={'position':1} if cursor is None else None,read=0,folders_total=2,folders_done=1 if cursor is None else 2)
    monkeypatch.setattr(mail_scope,'read_scope_page',reader)
    store=MailStore(tmp_path/'mail.sqlite3')
    options=dict(folders=[dict(store='pst',folder='a',label='存档')],include_children=True,start_date='2026-08-01',end_date='2026-08-31')
    first=MailManager(store)
    first.start('pst','a',90,100,**options);wait(first)
    second=MailManager(store)
    assert second.status()['folders']==options['folders']
    second.start('pst','a',90,100,**options);wait(second)
    assert calls==[None,{'position':1}]
    assert second.status()['sync_complete']


def test_fixed_date_poll_stops_when_archive_range_finishes(tmp_path,monkeypatch):
    from app.mail_tasks import MailManager
    from app import mail_scope
    from test_mail_tasks import wait
    monkeypatch.setattr(mail_scope,'read_scope_page',lambda *a:dict(rows=[],errors=[],partial=False,cursor=None,read=0,folders_total=1,folders_done=1))
    class NoTimer:
        def __init__(self,*args): raise AssertionError('Fixed range must not schedule another poll')
    manager=MailManager(MailStore(tmp_path/'mail.sqlite3'),timer_factory=NoTimer)
    manager.configure(True,'pst','a',90,100,300,folders=[dict(store='pst',folder='a')],start_date='2026-08-01',end_date='2026-08-31')
    wait(manager)
    assert manager.status()['enabled'] is False
    assert manager.store.get_state('poll_settings')['enabled'] is False


def test_non_mail_folders_are_not_scanned_but_children_can_be(monkeypatch):
    from app.mail_scope import read_scope_page
    root=folder('r','日历',[folder('mail','邮件')]);root.DefaultItemType=1
    session_for(monkeypatch,[('pst','archive',root)])
    calls=[]
    def scan(f,*args):
        calls.append(f.EntryID);return dict(rows=[],partial=False,errors=[],cursor=None)
    monkeypatch.setattr(probe,'scan_folder_page',scan)
    now=datetime.now(timezone.utc)
    read_scope_page([dict(store='pst',folder='r')],True,now,now,100)
    assert calls==['mail']
