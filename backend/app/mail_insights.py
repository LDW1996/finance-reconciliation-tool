"""Payment-first mail summaries and read-only, receipt-centric reverse lookup."""
import heapq
from .mail_matching import _business_ids, rank_receipts
from .mail_workspace import signature, translated


def mail_summary(mail,payments,indexed=True):
    def distinct(values):return list(dict.fromkeys(str(v) for v in values if v is not None and str(v).strip()))
    complete=sum(all(p.get(k) not in (None,'') for k in ('payer','date','amount','currency')) and not p.get('ambiguous') for p in payments)
    assets=mail.get('attachment_details') or []
    pending=not indexed or any(a.get('status') in ('pending','running') for a in assets)
    failed=any(a.get('status')=='failed' for a in assets)
    status='pending' if pending else 'failed' if failed and not payments else 'empty' if not payments else 'ready' if complete==len(payments) and not failed else 'partial'
    amounts=[]
    for p in payments:
        value={'amount':p.get('amount'),'currency':p.get('currency') or ''}
        if value['amount'] not in (None,'') and value not in amounts:amounts.append(value)
    ids=distinct([v for p in payments for v in p.get('business_ids',[])])
    if not ids:ids=_business_ids('\n'.join(str(mail.get(k) or '') for k in ('subject','latest_body','quoted_body')))
    return dict(count=len(payments),complete_count=int(complete),payers=distinct(p.get('payer') for p in payments),
                dates=distinct(p.get('date') for p in payments),amounts=amounts,business_ids=ids,status=status)


def related_receipts(workspace,mail_id,day_window=3,limit=5,payment_id=None,wait=True):
    with workspace.lock:
        data=workspace._dataset()
        if not data:return dict(ledger=None,rows=[],total_rows=0,day_window=day_window)
        if payment_id and not any(p.get('id')==payment_id for p in workspace.store.payment_index([mail_id]).get(mail_id,[])):
            raise ValueError('该付款凭证已更新，请重新选择邮件。')
        top=[];total=0
        for ordinal,row in enumerate(workspace.matches.rows(day_window,wait=wait,full=True,mail_id=mail_id)):
            candidates=[candidate for candidate in row['candidates']
                        if mail_id in candidate.get('related_mail_ids',[candidate.get('mail_id')])
                        and (not payment_id or payment_id in candidate.get('related_payment_ids',[candidate.get('payment_id') or candidate.get('id')]))]
            if candidates:
                total+=1;row['candidates']=candidates[:3];row['candidate_count']=len(candidates)
                item=(candidates[0]['score'],-ordinal,row)
                if len(top)<limit:heapq.heappush(top,item)
                elif item[:2]>top[0][:2]:heapq.heapreplace(top,item)
        rows=[item[2] for item in sorted(top,key=lambda item:(-item[0],-item[1]))]
        reviews=workspace._review_map()
        for row in rows:
            if payment_id and row['candidates'][0].get('payment_id')!=payment_id:
                selected=workspace._candidate(row['receipt'],payment_id,day_window,wait=wait)
                row['candidates']=[selected] if selected else []
            review=reviews.get(row['receipt']['id'],dict(state='pending',payment_id=None))
            if review['state']=='confirmed':
                candidate=workspace._candidate(row['receipt'],review.get('payment_id'),day_window,wait=wait)
                if not candidate or signature(candidate)!=review.get('signature'):review=dict(state='pending',payment_id=None)
            row.pop('_generation',None)
            row['review']={k:v for k,v in review.items() if k!='signature'}
            row['warnings']=[translated(w) for w in row.get('warnings',[])]
            for candidate in row['candidates']:
                candidate['evidence_signature']=signature(candidate)
                candidate['warnings']=[translated(w) for w in candidate.get('warnings',[])]
        result=dict(ledger=workspace.ledger(),rows=rows,total_rows=total,day_window=day_window)
        if not wait:result['computation']=workspace.matches.state(day_window)
        return result
