"""Frozen English mail edition: offline OCR/PDF, imports, isolation and restart."""
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import httpx
from openpyxl import load_workbook
from smoke_windows_exe import (APP_URL,wait_for_health,assert_mail_local_services,assert_mail_restart_preserved,stop_process_tree)


def check_local_ledgers():
    headers={'X-Finance-Local':'1'}
    bank=b'DATE,PAYER,CREDIT,CURRENCY,REF NO,CLOSING STATUS\n2026-09-25,Synthetic,1400000,IDR,smoke1,UNIDENTIFIED\n'
    response=httpx.post(APP_URL+'/api/mail/ledger/import',headers=headers,files={'workbook':('bank.csv',bank)},timeout=30)
    response.raise_for_status()
    contacts=b'Customer Name,Customer Bank Name Reff,Email\nSynthetic Customer,Synthetic,smoke@example.test\nMissing Payer,,other@example.test\n'
    response=httpx.post(APP_URL+'/api/mail/customer-mapping/import',headers=headers,files={'workbook':('contacts.csv',contacts)},timeout=30)
    response.raise_for_status()
    preview=httpx.get(APP_URL+'/api/mail/customer-mapping/preview',headers=headers,params={'status':'excluded','keyword':'Bank payer is missing'},timeout=30)
    preview.raise_for_status()
    assert preview.json()['total']==1
    assert preview.json()['rows'][0]['reasons']==['Bank payer is missing']
    export=httpx.get(APP_URL+'/api/mail/matching/export',headers=headers,params={'scope':'all'},timeout=30)
    export.raise_for_status()
    book=load_workbook(BytesIO(export.content))
    assert [cell.value for cell in book.active[1]][-3:]==['Mail association notes','Match score','Mail search information']
    book.close()


def main():
    exe=Path(sys.argv[1]).resolve()
    try:httpx.get(APP_URL+'/api/health',timeout=2)
    except httpx.RequestError:pass
    else:raise RuntimeError('Close the existing application on port 8765 before running smoke tests.')
    diagnostics=Path('build/smoke-diagnostics/mail-en').resolve();diagnostics.mkdir(parents=True,exist_ok=True)
    startup=diagnostics/'startup.log';startup.write_text('',encoding='utf-8')
    report=diagnostics/'probe.json'
    with tempfile.TemporaryDirectory(prefix='mail-en-smoke-') as temp:
        root=Path(temp);env={**os.environ,'FINANCE_RECONCILE_DATA_DIR':str(root/'data'),'FINANCE_RECONCILE_STARTUP_LOG':str(startup)}
        checked=subprocess.run([str(exe),'--verify-bundle',str(report)],env=env,timeout=90)
        if checked.returncode or not report.exists():
            raise AssertionError('Mail runtime probe failed: '+startup.read_text(encoding='utf-8',errors='replace')[-12000:])
        result=json.loads(report.read_text(encoding='utf-8'))
        if not result.get('ok'):raise AssertionError('Mail runtime probe failed: '+str(result))
        process=subprocess.Popen([str(exe)],env=env)
        try:
            wait_for_health(process=process,startup_log=startup)
            assert httpx.get(APP_URL+'/api/health').json()['edition']=='mail_en'
            for path in ['/api/ilog/accounts','/api/reconcile','/api/analyze-combined','/api/excel/status']:
                for method in ('GET','POST'):
                    assert httpx.request(method,APP_URL+path,timeout=10).status_code==404
            frontend=httpx.get(APP_URL);frontend.raise_for_status()
            assert 'lang="en"' in frontend.text
            sample=assert_mail_local_services()
            check_local_ledgers()
            (diagnostics/'mail-performance.json').write_text(json.dumps(sample['performance'],indent=2),encoding='utf-8')
            key=root/'data'/'mail'/'mail.key';before=key.read_bytes()
            stop_process_tree(process);process=subprocess.Popen([str(exe)],env=env)
            wait_for_health(process=process,startup_log=startup)
            assert_mail_restart_preserved(sample['mail'])
            assert key.read_bytes()==before
            (diagnostics/'mail-restart.json').write_text(json.dumps(dict(ok=True,key_preserved=True,completed_results_preserved=True)),encoding='utf-8')
        finally:stop_process_tree(process)
    return 0
if __name__=='__main__':raise SystemExit(main())
