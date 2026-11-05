import importlib.util
from pathlib import Path
import json
import pytest
from app.platform_auth import PlatformAuth, verify_password


def generator():
    path=Path(__file__).resolve().parents[2]/'packaging'/'prepare_platform_accounts.py'
    spec=importlib.util.spec_from_file_location('prepare_platform_accounts',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_build_input_contains_only_compatible_hashes(tmp_path,monkeypatch):
    passwords={name:'unique-'+name for name in ('excel','mail','ilog','full')}
    destination=tmp_path/'platform_accounts.json'
    generator().prepare(passwords,destination)
    data=json.loads(destination.read_text())
    assert set(data)==set(passwords)
    assert all(verify_password(passwords[role],value) for role,value in data.items())
    assert not any(value in destination.read_text() for value in passwords.values())
    import sys
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'_MEIPASS',str(tmp_path),raising=False)
    assert PlatformAuth().accounts()==data


@pytest.mark.parametrize('values',[{}, {'full':'private-secret'}, {'excel':'x','mail':'x','ilog':'x','full':''}])
def test_invalid_build_input_never_creates_fallback_accounts(tmp_path,values):
    target=tmp_path/'platform_accounts.json'
    with pytest.raises(ValueError):generator().prepare(values,target)
    assert not target.exists()
