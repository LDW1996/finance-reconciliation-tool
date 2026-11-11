import pytest

from app.ilog_query import validate_bill


def test_bill_normalization():
    assert validate_bill(' TRBTSTZH2606S ') == 'TRBTSTZH2606S'


@pytest.mark.parametrize('bill', ['', 'x\nother', 'x' * 81, '<script>'])
def test_invalid_bill(bill):
    with pytest.raises(ValueError):
        validate_bill(bill)


def test_platform_address_defaults_and_persists(tmp_path, monkeypatch):
    from app import ilog_query

    monkeypatch.setattr(ilog_query, 'account_data_dir', lambda: tmp_path)
    assert ilog_query.platform_url() == 'https://jk.sitc.com/ilog/'
    new_address = ilog_query.normalize_platform_url(' https://alt.sitc.com/ilog ')
    ilog_query.save_platform_url(new_address)
    assert ilog_query.platform_url() == 'https://alt.sitc.com/ilog/'


def test_platform_address_rejects_untrusted_origins():
    import pytest
    from app.ilog_query import normalize_platform_url

    for address in (
        'http://jk.sitc.com/ilog/',
        'https://jk.sitc.com.example.org/ilog/',
        'https://jk.sitc.com@example.org/ilog/',
        'https://user@jk.sitc.com/ilog/',
        'https://jk.sitc.com:8080/ilog/',
        'https://jk.sitc.com/ilog/?next=https://example.org',
    ):
        with pytest.raises(ValueError):
            normalize_platform_url(address)
