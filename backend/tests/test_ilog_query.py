import pytest

from app.ilog_query import validate_bill


def test_bill_normalization():
    assert validate_bill(' TRBTSTZH2606S ') == 'TRBTSTZH2606S'


@pytest.mark.parametrize('bill', ['', 'x\nother', 'x' * 81, '<script>'])
def test_invalid_bill(bill):
    with pytest.raises(ValueError):
        validate_bill(bill)
