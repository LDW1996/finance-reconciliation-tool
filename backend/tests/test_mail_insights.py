from copy import deepcopy

from app.outlook_probe import MailStore
from app.mail_workspace import MailWorkspace
from test_mail_workspace import payment,ledger_bytes


def test_plain_payment_mail_without_any_number_gets_payments_and_searchable_remark():
    from app.mail_matching import build_payments
    mail=dict(id='plain',subject='Payment advice',sender='finance@example.invalid',received='2026-09-26T08:00:00+08:00',
              latest_body='Payer: TEST PAYER\nPayment Date: 2026-09-25\nAmount: IDR 1,400,000.00')
    payments=build_payments(mail)
    assert payments and payments[0]['business_ids']==[]
    assert payments[0]['payer']=='TEST PAYER'
    assert payments[0]['date']=='2026-09-25' and payments[0]['amount']=='1400000.00'
    assert 'Payment advice' in payments[0]['remark'] and 'finance@example.invalid' in payments[0]['search_info']


def test_no_business_number_still_matches_and_has_mail_remark(tmp_path):
    from app.mail_insights import mail_summary, related_receipts
    store=MailStore(tmp_path/'mail.sqlite3');workspace=MailWorkspace(store)
    workspace.import_ledger(ledger_bytes(),'bank.xlsx')
    p={**payment(),'business_ids':[],'subject':'Payment advice','remark':'邮件主题：Payment advice\n付款：TEST PAYER'}
    store.set_payments('mail',[p])
    summary=mail_summary({'subject':'Payment advice'},[p],indexed=True)
    assert summary['status']=='ready' and summary['business_ids']==[]
    result=related_receipts(workspace,'mail',3,5)
    assert result['total_rows']==2 and result['rows'][0]['candidates'][0]['score']==100
    assert 'Payment advice' in result['rows'][0]['candidates'][0]['remark']
    assert not workspace._review_map()


def test_payment_summary_does_not_fill_missing_payer_from_sender():
    from app.mail_insights import mail_summary
    p={**payment(),'payer':None,'business_ids':[]}
    summary=mail_summary({'sender':'someone@example.invalid'},[p],indexed=True)
    assert summary['payers']==[] and summary['status']=='partial' and summary['complete_count']==0
    assert mail_summary({},[],indexed=False)['status']=='pending'
    assert mail_summary({'attachment_details':[{'status':'failed'}]},[],indexed=True)['status']=='failed'


def test_two_pages_remain_separate_payment_candidates(tmp_path):
    from app.mail_insights import related_receipts
    store=MailStore(tmp_path/'mail.sqlite3');workspace=MailWorkspace(store)
    workspace.import_ledger(ledger_bytes(),'bank.xlsx')
    a={**payment(),'payer':None,'date':None}
    b={**payment(),'id':'other-page','page':2,'payer':'TEST PAYER','amount':None}
    store.set_payments('mail',[a,b])
    result=related_receipts(workspace,'mail',3,5)
    assert all(c['score']<100 for r in result['rows'] for c in r['candidates'])
    assert len(store.payment_rows())==2


def test_reverse_lookup_does_not_require_a_ledger(tmp_path):
    from app.mail_insights import related_receipts
    workspace=MailWorkspace(MailStore(tmp_path/'mail.sqlite3'))
    assert related_receipts(workspace,'mail',3,5)=={'ledger':None,'rows':[],'total_rows':0,'day_window':3}


def test_reverse_selected_payment_never_uses_other_page(tmp_path):
    import pytest
    from app.mail_insights import related_receipts
    store=MailStore(tmp_path/'mail.sqlite3');workspace=MailWorkspace(store)
    workspace.import_ledger(ledger_bytes(),'bank.xlsx')
    store.set_payments('mail',[payment(),{**payment(),'id':'different-payment','page':2,'amount':'999.00'}])
    assert related_receipts(workspace,'mail',payment_id='different-payment')['total_rows']==0
    assert related_receipts(workspace,'mail',payment_id='payment')['total_rows']==2
    with pytest.raises(ValueError):related_receipts(workspace,'mail',payment_id='not-owned')


def test_cached_summary_persists_and_clears_with_mail(tmp_path):
    store=MailStore(tmp_path/'mail.sqlite3')
    store.set_payments('mail',[payment()],{'status':'ready','payers':['TEST PAYER']})
    reopened=MailStore(tmp_path/'mail.sqlite3')
    assert reopened.payment_summaries(['mail'])['mail']['status']=='ready'
    assert reopened.payment_index(['missing'])=={}
    reopened.clear()
    assert reopened.payment_summaries()=={} and reopened.payment_index()=={}


def test_mail_routes_include_payment_fields_without_attachment_preview_in_list(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import mail_tasks
    store=MailStore(tmp_path/'mail.sqlite3')
    store.save([dict(id='mail',subject='Payment advice',body='text',sender='sender@example.invalid',received='',attachments=[],candidates=[])])
    store.set_payments('mail',[{**payment(),'business_ids':[]}])
    manager=mail_tasks.MailManager(store);manager.workspace.import_ledger(ledger_bytes(),'bank.xlsx')
    monkeypatch.setattr(mail_tasks,'_manager',manager)
    client=TestClient(app);headers={'X-Finance-Local':'1'}
    listed=client.get('/api/mail/messages',headers=headers).json()[0]
    assert listed['payment_summary']['payers']==['TEST PAYER']
    assert 'attachment_details' not in listed and 'payment_candidates' not in listed
    detail=client.get('/api/mail/messages/mail',headers=headers).json()
    assert detail['payment_candidates'][0]['amount']=='1400000.00'
    assert client.get('/api/mail/messages/mail/receipts').status_code==403
    matched=client.get('/api/mail/messages/mail/receipts?limit=1',headers=headers).json()
    assert matched['total_rows']==2 and len(matched['rows'])==1
    assert client.get('/api/mail/messages/missing/receipts',headers=headers).status_code==404
