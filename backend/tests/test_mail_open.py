from contextlib import contextmanager
from app import mail_open

class Store:
    def get(self,identity):return dict(subject='Test',sender='Sender',received='2026-11-02',outlook_entry_id='entry',outlook_store_id='store')

def test_display_only_and_correct_locator(monkeypatch):
    calls=[]
    class Item:
        Class=43
        def Display(self,modal):calls.append(('display',modal))
    class Session:
        def GetItemFromID(self,*args):calls.append(args);return Item()
    @contextmanager
    def session():yield Session()
    monkeypatch.setattr(mail_open.sys,'platform','win32');monkeypatch.setattr(mail_open,'outlook_session',session)
    assert mail_open.open_original(Store(),'id')['opened']
    assert calls==[('entry','store'),('display',False)]

def test_unsupported_returns_search_information(monkeypatch):
    monkeypatch.setattr(mail_open.sys,'platform','darwin')
    result=mail_open.open_original(Store(),'id')
    assert not result['opened'] and result['search']['subject']=='Test'
