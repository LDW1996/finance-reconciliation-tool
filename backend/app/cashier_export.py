"""Bounded, view-only snapshot. No source rows, reviews or scan state are mutated."""
from datetime import datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from .mail_online import OUTPUT_LANGUAGE

MAX_ATTACHMENT_BYTES = 512 * 1024**2
MIN_FREE_BYTES = 128 * 1024**2


def build_cashier_package(workspace, day_window=3):
    english=OUTPUT_LANGUAGE.get()=='en'
    def label(zh,en):return en if english else zh
    # Snapshot matching and review validation under the existing workspace lock.
    with workspace.lock:
        result=workspace.matching(scope='all',page_size=20000,day_window=day_window,candidate_limit=3)
        if not result['ledger']:raise ValueError('请先读取或导入银行流水。')
        if len(result['rows'])!=result['total_rows']:raise ValueError('流水数量超出导出上限，请拆分流水后重试。')
    handle,name=tempfile.mkstemp(prefix='cashier-review-',suffix='.zip')
    os.close(handle);output=Path(name)
    workbook=Workbook(write_only=True)
    def sheet(zh,en,headers):
        page=workbook.create_sheet(label(zh,en));page.freeze_panes='A2'
        cells=[]
        for title in headers:
            cell=WriteOnlyCell(page,title);cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='245B78');cells.append(cell)
        for number in range(1,len(headers)+1):
            page.column_dimensions[get_column_letter(number)].width=24 if number<7 else 42
        page.append(cells)
        return page
    def append(page,values):
        cells=[]
        for value in values:
            cell=WriteOnlyCell(page,value if isinstance(value,(int,float,Decimal)) else str(value if value is not None else '')[:32760])
            if isinstance(cell.value,str):cell.data_type='s'
            cell.alignment=Alignment(vertical='top',wrap_text=True)
            cells.append(cell)
        page.append(cells)
    base=[label(a,b) for a,b in [('原流水行','Source row'),('流水号','Reference'),('日期','Date'),('币种','Currency'),('收款金额','Amount'),('银行付款人','Bank payer'),('银行摘要','Bank description'),('原收款状态','Bank status'),('核对状态','Review state'),('候选总数','Candidate count'),('最高匹配分','Top score'),('核对提示','Warnings')]]
    bank=sheet('银行流水','Bank transactions',base)
    pending=sheet('待核对','Needs review',base)
    evidence=sheet('候选凭证','Candidate evidence',[label(a,b) for a,b in [('原流水行','Source row'),('流水号','Reference'),('候选序号','Candidate number'),('关联状态','Link state'),('匹配分','Score'),('付款人','Payer'),('付款日期','Payment date'),('币种','Currency'),('付款金额','Payment amount'),('邮件主题','Subject'),('发件人','Sender'),('匹配依据','Matching evidence'),('风险提示','Warnings'),('凭证文件','Evidence file'),('附件页码','Page')]])
    missing=sheet('附件提示','Attachment issues',[label('原流水行','Source row'),label('流水号','Reference'),label('候选序号','Candidate number'),label('原因','Reason')])
    exported={};total_bytes=0;missing_count=0
    try:
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,allowZip64=True) as archive:
            for row in result['rows']:
                receipt=row['receipt'];review=row['review'];candidates=row['candidates']
                state=review['state'];confirmed=state=='confirmed'
                status=label('已确认关联','Confirmed') if confirmed else label('暂不关联','Rejected') if state=='rejected' else label('待人工核对','Pending review') if candidates else label('未匹配','No candidate')
                values=[receipt.get(k) for k in ('row','ref','date','currency','amount','payer','description','status')]+[status,row.get('candidate_count',len(candidates)),max((c['score'] for c in candidates),default=None),'\n'.join(row.get('warnings',[]))]
                append(bank,values)
                if not confirmed:append(pending,values)
                for number,candidate in enumerate(candidates,1):
                    selected=confirmed and review.get('payment_id')==(candidate.get('payment_id') or candidate.get('id'))
                    key=(candidate.get('mail_id'),candidate.get('attachment_id'))
                    attachment_path=exported.get(key)
                    if attachment_path is None:
                        asset=workspace.store.attachment(*key) if all(key) else None
                        raw=asset.get('raw') if asset else None
                        if raw:
                            total_bytes+=len(raw)
                            if total_bytes>MAX_ATTACHMENT_BYTES:raise ValueError('核对包附件超过 512 MB，请减少流水范围后重试。')
                            if shutil.disk_usage(output.parent).free<len(raw)+MIN_FREE_BYTES:raise ValueError('导出目录磁盘空间不足，请释放空间后重试。')
                            original=Path(str(asset.get('name') or candidate.get('attachment_name') or 'evidence.pdf').replace('\\','/')).name
                            safe=re.sub(r'[^\w.\- ]','_',original)[:100] or 'evidence'
                            attachment_path=f'attachments/{len(exported)+1:05d}/{safe}'
                            archive.writestr(attachment_path,raw)
                            exported[key]=attachment_path
                        else:
                            attachment_path=''
                            missing_count+=1
                            append(missing,[receipt.get('row'),receipt.get('ref'),number,label('原件缺失或该候选没有附件，请回业务机器核对。','Original unavailable or no attachment. Review on the source machine.')])
                    detail=[receipt.get('row'),receipt.get('ref'),number,label('已确认关联','Confirmed') if selected else label('待核对候选（非确认）','Candidate only (not confirmed)'),candidate.get('score'),candidate.get('payer'),candidate.get('date'),candidate.get('currency'),candidate.get('amount'),candidate.get('subject'),candidate.get('sender'),str(candidate.get('remark') or '')+'\n'+' / '.join(f'{title}: {candidate.get("breakdown",{}).get(key,0)}' for key,title in [('amount',label('金额分','Amount score')),('payer',label('付款人分','Payer score')),('date',label('日期分','Date score')),('mapping',label('映射分','Mapping score'))])+'\n'+str((candidate.get('mapping') or {}).get('label','')),'\n'.join(candidate.get('warnings',[])),attachment_path,candidate.get('page')]
                    cells=[]
                    for i,value in enumerate(detail):
                        cell=WriteOnlyCell(evidence,str(value if value is not None else '')[:32760]);cell.data_type='s'
                        cell.alignment=Alignment(vertical='top',wrap_text=True)
                        if i==13 and attachment_path:
                            cell.hyperlink=attachment_path;cell.font=Font(color='0563C1',underline='single')
                        cells.append(cell)
                    evidence.append(cells)
            bank.auto_filter.ref=f'A1:L{len(result["rows"])+1}'
            pending.auto_filter.ref=f'A1:L{1+sum(r["review"]["state"]!="confirmed" for r in result["rows"])}'
            evidence.auto_filter.ref=f'A1:O{1+sum(len(r["candidates"]) for r in result["rows"])}'
            with tempfile.TemporaryDirectory(prefix='cashier-workbook-') as staging:
                xlsx=Path(staging)/'cashier-review.xlsx';workbook.save(xlsx)
                archive.write(xlsx,'cashier-review.xlsx')
            note=label(
                '出纳只读核对包\n请先完整解压，再打开 cashier-review.xlsx。\n包含当前已导入的全部有效银行流水，最多前三条候选及已确认凭证。候选及分数不代表已确认，不自动核销。\n待核对表包括所有尚未确认的流水；原件缺失请查看附件提示表。\n金额、币种、付款人和日期请以银行记录及原始凭证人工核实。\n此文件是导出时的快照，不随业务机器后续读取自动更新，不支持导回确认。\n不包含平台密钥、账号配置或完整邮件数据库。\n',
                'Cashier view-only review package\nExtract the entire ZIP before opening cashier-review.xlsx.\nContains all valid imported bank rows, up to three candidates plus confirmed evidence. Scores and candidates do not confirm reconciliation.\nNeeds review contains all unconfirmed rows; see Attachment issues for missing originals.\nVerify amount, currency, payer and date against bank records and original evidence.\nThis is an export-time snapshot; it does not update or support importing confirmations.\nNo platform keys, account configuration or full mail database are included.\n')
            note+=f"\nExported UTC: {datetime.now(timezone.utc).isoformat()}\nLedger: {result['ledger'].get('name','')}\nRows: {len(result['rows'])}\nInvalid bank rows excluded at import: {result['ledger'].get('skipped',0)}\nMissing originals: {missing_count}\nDate window: +/- {day_window} days\n"
            archive.writestr('README.txt',note)
        return output
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    finally:
        for page in workbook.worksheets:
            if not page.closed:page.close()
            writer=getattr(page,'_writer',None)
            if writer is not None and Path(writer.out).exists():writer.cleanup()
        workbook.close()
