from email.message import EmailMessage

import pytest

from app.outlook_probe import parse_eml


def test_reply_signature_and_history_keep_original():
    body = 'Attached payment.\nBest regards,\nAlice\nFrom: Bob\nSent: Monday\nSubject: Invoice\nSLIND-0925-0038\n'
    mail = EmailMessage()
    mail.set_content(body)
    row = parse_eml(mail.as_bytes())
    assert row['latest_body'] == 'Attached payment.'
    assert 'Alice' in row['signature']
    assert 'SLIND-0925-0038' in row['quoted_body']
    assert row['body'] == body


def test_ordinary_from_line_is_not_history():
    mail = EmailMessage()
    mail.set_content('From: our bank account\nPayment tomorrow\nThank you for checking the transfer.')
    row = parse_eml(mail.as_bytes())
    assert row['quoted_body'] == ''
    assert 'Payment tomorrow' in row['latest_body']
    assert 'Thank you for checking' in row['latest_body']


def test_invalid_msg_is_rejected():
    from app.outlook_probe import parse_msg
    with pytest.raises(ValueError, match='MSG'):
        parse_msg(b'not a message')


def test_bulleted_forward_headers_and_signature():
    mail = EmailMessage()
    mail.set_content('Attached Payment\nThanks & Best Regards\nAlice\nOriginal:\n*\tFrom：Bob\n*\tDate：2026-09-25\n*\tSubject：Invoice\nOld text')
    row = parse_eml(mail.as_bytes())
    assert row['latest_body'] == 'Attached Payment'
    assert 'Alice' in row['signature']
    assert row['quoted_body'].startswith('Original:')


def test_chinese_quote_boundary_and_html_lines():
    mail = EmailMessage()
    mail.set_content('<p>付款完成</p><p>发件人: Alice</p><p>发送时间: 昨天</p><p>主题: 发票</p><p>旧正文</p>', subtype='html')
    row = parse_eml(mail.as_bytes())
    assert row['latest_body'] == '付款完成'
    assert '旧正文' in row['quoted_body']
