import pytest
from fastapi.testclient import TestClient

from app.accounts import AccountStore, account_data_dir
from app.main import app


def test_accounts_persist_without_plaintext_passwords(tmp_path):
    store = AccountStore(tmp_path)
    account = store.create("测试账号", "test-user", "test-only-secret")
    reopened = AccountStore(tmp_path)
    assert reopened.list_accounts() == [account]
    assert reopened.password_for(account["id"]) == "test-only-secret"
    assert b"test-only-secret" not in store.db_path.read_bytes()
    assert "password" not in account


@pytest.mark.parametrize("new_password, expected", [(None, "old-test"), ("", "old-test"), (" new-test ", " new-test ")])
def test_password_edit_preserves_blank_and_other_accounts(tmp_path, new_password, expected):
    store = AccountStore(tmp_path)
    one = store.create("One", "one", "old-test")
    two = store.create("Two", "two", "other-test")
    store.update(one["id"], "Changed", "changed", new_password)
    reopened = AccountStore(tmp_path)
    assert reopened.password_for(one["id"]) == expected
    assert reopened.password_for(two["id"]) == "other-test"
    assert reopened.list_accounts()[0]["name"] == "Changed"


def test_account_without_password_can_add_one_and_delete(tmp_path):
    store = AccountStore(tmp_path)
    account = store.create("One", "one")
    assert account["hasPassword"] is False
    assert store.update(account["id"], "One", "one", "new-test")["hasPassword"] is True
    store.delete(account["id"])
    assert AccountStore(tmp_path).list_accounts() == []
    with pytest.raises(LookupError):
        store.update(account["id"], "Missing", "missing")


def test_missing_encryption_key_does_not_overwrite_existing_credentials(tmp_path):
    store = AccountStore(tmp_path)
    store.create("One", "one", "secret-test")
    store.key_path.unlink()
    with pytest.raises(RuntimeError):
        AccountStore(tmp_path)
    assert not store.key_path.exists()


def test_windows_uses_local_app_data(tmp_path, monkeypatch):
    monkeypatch.delenv("FINANCE_RECONCILE_DATA_DIR", raising=False)
    monkeypatch.setattr("app.accounts.sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert account_data_dir() == tmp_path / "FinanceReconciliation"


def test_account_api_crud_and_write_protection(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_RECONCILE_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    payload = {"name": "Example", "username": "test", "password": "test-only"}
    headers = {"X-Finance-Local": "1"}
    assert client.post("/api/ilog/accounts", json=payload).status_code == 403
    assert client.post("/api/ilog/accounts", json=payload, headers={**headers, "Origin": "https://example.com"}).status_code == 403
    created = client.post("/api/ilog/accounts", json=payload, headers=headers)
    assert created.status_code == 201
    account = created.json()
    assert "password" not in account
    url = "/api/ilog/accounts/" + account["id"]
    assert client.get("/api/ilog/accounts").json() == [account]
    assert client.put(url, json={**payload, "password": "updated-test"}, headers=headers).status_code == 200
    assert AccountStore(tmp_path).password_for(account["id"]) == "updated-test"
    assert client.put(url, json={**payload, "name": " "}, headers=headers).status_code == 400
    assert client.delete(url, headers=headers).status_code == 204
    assert client.get("/api/ilog/accounts").json() == []
    assert client.put(url, json=payload, headers=headers).status_code == 404
