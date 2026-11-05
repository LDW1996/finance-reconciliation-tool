"""Shared iLog query syntax. Partial and numeric identifiers remain supported."""
import re

_BILL_PATTERN = re.compile(r"[A-Za-z0-9_/-]{1,80}", re.ASCII)


def valid_bill(value: str) -> bool:
    return _BILL_PATTERN.fullmatch(value) is not None


def normalize_bills(bills):
    if not isinstance(bills, list):
        raise ValueError('单号必须为列表')
    result = []
    seen = set()
    for bill in bills:
        if not isinstance(bill, str):
            raise ValueError('单号格式异常')
        bill = bill.strip().upper()
        if not valid_bill(bill):
            raise ValueError('存在格式异常的单号')
        if bill not in seen:
            result.append(bill)
            seen.add(bill)
    return result
