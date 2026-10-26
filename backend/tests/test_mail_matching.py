from io import BytesIO

import pytest
from openpyxl import Workbook

from app.mail_matching import build_payments, parse_ledger, rank_receipts


def mail(mid='m1', sha='abc', **changes):
    fields = {k: [dict(value=v, page=1, line=0, quote=str(v), confidence=.99, box=None)]
              for k, v in dict(payer='PT. Example Trading', date='2026-09-20', amount='1234.00').items()}
    fields['amount'][0]['currency'] = 'IDR'
    result = dict(id=mid, subject='Payment SLIND-0925-0038', sender='sender@example.test',
                  received='2026-09-21T09:00:00+07:00', latest_body='', quoted_body='',
                  attachment_details=[dict(id='a1', sha256=sha, result={'pages': [dict(number=1, fields=fields)]})])
    result.update(changes)
    return result


def receipt(**changes):
    result = dict(id='r1', amount='1234.00', currency='IDR', payer='EXAMPLETRADING', date='2026-09-20')
    result.update(changes)
    return result


def test_csv_normalizes_locales_zero_and_invalid_rows():
    raw = ('EFF DATE;DESCRIPTION;CREDIT;REF NO;CLOSING STATUS\n'
           '20 Sept 2026;BANK UN Example Trading;1.234,00;00012;Closed\n'
           '2026-09-20;No payer;0;00012;Closed\n'
           'bad;Bad;10;3;Closed\n'
           '2026-09-20;Bad;12,34,56;4;Closed\n').encode()
    parsed = parse_ledger(raw, 'ledger.csv')
    assert parsed['header_row'] == 1
    assert parsed['entries'][0]['amount'] == '1234.00'
    assert parsed['entries'][0]['payer'] == 'Example Trading'
    assert parsed['entries'][0]['ref'] == '00012'
    assert parsed['entries'][1]['amount'] == '0.00'
    assert parsed['entries'][1]['payer'] == ''
    assert parsed['skipped'] == 2
    assert any('duplicate' in w.lower() for w in parsed['warnings'])
    assert all(e['status'] == 'Closed' for e in parsed['entries'])


def test_xlsx_preferred_sheet_header_search_numeric_ref():
    book = Workbook()
    book.active.title = 'Notes'
    sheet = book.create_sheet('Bank Transactions')
    sheet.append(['Report'])
    sheet.append(['日期', '付款人', '收入金额', '流水号'])
    sheet.append(['2026-09-20', 'Example Trading', 1234, 12])
    sheet['D3'].number_format = '000000'
    out = BytesIO(); book.save(out)
    parsed = parse_ledger(out.getvalue(), 'ledger.xlsx', currency='USD')
    assert parsed['sheet'] == 'Bank Transactions'
    assert parsed['available_sheets'] == ['Notes', 'Bank Transactions']
    assert parsed['header_row'] == 2
    assert parsed['entries'][0]['ref'] == '000012'
    assert parsed['entries'][0]['currency'] == 'USD'
    with pytest.raises(ValueError):
        parse_ledger(out.getvalue(), 'ledger.xlsx', sheet_name='absent')


def test_limits_and_invalid_headers():
    with pytest.raises(ValueError):
        parse_ledger(b'x' * (25 * 1024 * 1024 + 1), 'huge.csv')
    with pytest.raises(ValueError):
        parse_ledger(b'hello,world\n1,2', 'bad.csv')


def test_page_provenance_and_related_clues():
    source = mail(quoted_body='BL SITABC1234567 container ABCD1234567 https://host.test/FAKE1234567')
    payment = build_payments(source)[0]
    assert payment['amount'] == '1234.00'
    assert payment['page'] == 1 and payment['attachment_id'] == 'a1'
    assert payment['business_ids'] == ['SLIND-0925-0038', 'SITABC1234567', 'ABCD1234567']
    assert payment['evidence']['amount'][0]['quote'] == '1234.00'
    assert '+07:00' in payment['search_info']
    assert payment['keyword'] == 'SLIND-0925-0038'
    assert payment['id'] == build_payments(source)[0]['id']
    assert 'allocation' in ' '.join(payment['warnings']).lower()


def test_ambiguous_page_not_scoreable_and_pages_not_combined():
    source = mail()
    page = source['attachment_details'][0]['result']['pages'][0]
    page['fields']['amount'].append(dict(value='999.00', currency='IDR'))
    payments = build_payments(source)
    assert payments and payments[0]['amount'] is None
    assert rank_receipts([receipt()], payments)[0]['candidates'] == []


def test_latest_body_only_and_no_sender_or_received_fallback():
    source = mail(attachment_details=[], latest_body='Amount: IDR 1,234.00',
                  body='Payer Name: Old Company\nAmount: IDR 9,000.00', quoted_body='20 Sept 2026')
    payment = build_payments(source)[0]
    assert payment['payer'] is None and payment['date'] is None
    assert payment['amount'] == '1234.00'
    assert build_payments(mail(attachment_details=[], latest_body='', body='Amount: IDR 100')) == []


@pytest.mark.parametrize('gap,points', [(0,20), (1,15), (2,10), (3,5)])
def test_scores_dates_and_ocr_spacing(gap, points):
    payment = build_payments(mail())[0]
    payment['date'] = f'2026-09-{20+gap}'
    candidate = rank_receipts([receipt()], [payment])[0]['candidates'][0]
    assert candidate['breakdown'] == dict(amount=50, payer=30, date=points)
    assert candidate['score'] == 80 + points


def test_currency_amount_date_filters_and_missing_fields():
    payment = build_payments(mail())[0]
    for changes in [dict(amount='1235'), dict(currency='USD'), dict(date='2026-10-01')]:
        assert not rank_receipts([receipt()], [{**payment, **changes}])[0]['candidates']
    candidate = rank_receipts([receipt()], [{**payment, 'currency': '', 'payer': None, 'date': None}])[0]['candidates'][0]
    assert candidate['score'] == 0
    assert any('currency' in w for w in candidate['warnings'])
    wrong = rank_receipts([receipt()], [{**payment, 'payer': 'Completely Different'}])[0]['candidates'][0]
    assert wrong['breakdown']['payer'] == 0
    assert any('conflict' in w for w in wrong['warnings'])


def test_dedup_same_bytes_keeps_search_info_and_no_score_boost():
    payments = build_payments(mail()) + build_payments(mail('m2', subject='Forwarded receipt'))
    result = rank_receipts([receipt()], payments)[0]
    assert result['candidate_count'] == 1
    candidate = result['candidates'][0]
    assert candidate['score'] == 100
    assert candidate['related_mail_ids'] == ['m1', 'm2']
    assert len(candidate['related_search_info']) == 2


def test_fingerprint_requires_same_strong_invoice_and_top_three_count():
    payments = []
    for i in range(5):
        payments += build_payments(mail(f'm{i}', f'sha{i}', subject=f'Payment SLIND-0925-00{i:02}'))
    result = rank_receipts([receipt()], payments)[0]
    assert result['candidate_count'] == 5 and len(result['candidates']) == 3
    copies = build_payments(mail('a', 'one')) + build_payments(mail('b', 'two'))
    assert rank_receipts([receipt()], copies)[0]['candidate_count'] == 1
    for p in copies: p['business_ids'] = []
    assert rank_receipts([receipt()], copies)[0]['candidate_count'] == 2


def test_truncated_and_fuzzy_payer_scores():
    payment = build_payments(mail())[0]
    truncated = rank_receipts([receipt()], [{**payment, 'payer': 'Example Trad'}])[0]['candidates'][0]
    assert truncated['breakdown']['payer'] == 24
    fuzzy = rank_receipts([receipt()], [{**payment, 'payer': 'Example Tradlng'}])[0]['candidates'][0]
    assert 0 < fuzzy['breakdown']['payer'] <= 23


def test_two_digit_year_and_missing_calendar_components():
    parsed = parse_ledger(b'DATE,AMOUNT\n20-Sept-26,10\nSept 2026,10\n', 'a.csv')
    assert parsed['entries'][0]['date'] == '2026-09-20'
    assert parsed['skipped'] == 1


def test_pages_never_supply_each_others_fields_or_dates():
    source = mail()
    pages = source['attachment_details'][0]['result']['pages']
    payer = pages[0]['fields'].pop('payer')
    pages.append(dict(number=2, fields=dict(payer=payer)))
    payments = build_payments(source)
    assert len(payments) == 2
    assert payments[0]['payer'] is None
    assert payments[1]['amount'] is None and payments[1]['date'] is None
    candidate = rank_receipts([receipt()], payments)[0]['candidates'][0]
    assert candidate['score'] == 70


def test_conflicting_page_currency_and_same_bytes_variants_are_not_hidden():
    source = mail()
    source['attachment_details'][0]['result']['pages'][0]['fields']['amount'].append(
        dict(value='1234.00', currency='USD'))
    assert build_payments(source)[0]['ambiguous']
    payments = build_payments(mail('one')) + build_payments(mail('two'))
    payments[1]['payer'] = 'Another Payer'
    assert rank_receipts([receipt()], payments)[0]['candidate_count'] == 2


def test_csv_row_limit_and_header_boundary():
    parsed = parse_ledger(('DATE,AMOUNT\n' + '2026-09-20,0\n' * 20001).encode(), 'a.csv')
    assert len(parsed['entries']) == 20000
    assert any('20000' in w for w in parsed['warnings'])
    raw = ('preamble\n' * 19 + 'DATE,AMOUNT\n2026-09-20,1\n').encode()
    assert parse_ledger(raw, 'a.csv')['header_row'] == 20
    with pytest.raises(ValueError):
        parse_ledger(b'preamble\n' + raw, 'a.csv')


@pytest.mark.parametrize('value,expected', [('1,234.56','1234.56'), ('1.234,56','1234.56'),
    ('1.234','1234.00'), ('1,234','1234.00'), ('1234,50','1234.50'),
    ('0,00','0.00'), ('12,34,56',None), ('1.23.456',None), ('NaN',None)])
def test_strict_amount_formats(value, expected):
    parsed = parse_ledger(f'DATE;AMOUNT\n2026-09-20;{value}\n'.encode(), 'a.csv')
    assert (parsed['entries'][0]['amount'] if parsed['entries'] else None) == expected


def test_same_day_timestamp_and_source_without_business_ids_stays_separate():
    payments = build_payments(mail('one', 'one', subject='Receipt')) + build_payments(mail('two', 'two', subject='Receipt'))
    result = rank_receipts([receipt(date='2026-09-20T23:59:00+08:00')], payments)[0]
    assert result['candidate_count'] == 2
    assert result['candidates'][0]['breakdown']['date'] == 20


def test_invoice_fingerprint_normalizes_ocr_payer_and_keeps_both_sources():
    payments = build_payments(mail('one', 'one')) + build_payments(mail('two', 'two'))
    payments[1]['payer'] = 'EXAMPLETRADING'
    result = rank_receipts([receipt()], payments)[0]
    assert result['candidate_count'] == 1
    assert result['candidates'][0]['related_mail_ids'] == ['one', 'two']


def test_searchable_metadata_and_remark_include_mail_clues_or_subject():
    payment=build_payments(mail())[0]
    assert 'SLIND-0925-0038' in payment['remark']
    assert '检索关键词' in payment['search_info'] and 'sender@example.test' in payment['search_info']
    plain=build_payments(mail(subject='Customer payment notice'))[0]
    assert 'Customer payment notice' in plain['remark']


def test_beneficiary_name_is_not_a_business_id():
    payment=build_payments(mail(subject='SITCLOGISTIKINDONESIA'))[0]
    assert payment['business_ids']==[]
