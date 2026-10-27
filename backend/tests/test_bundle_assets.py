import importlib.util
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


def load_assets():
    spec=importlib.util.spec_from_file_location('bundle_assets',ROOT/'packaging'/'bundle_assets.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_report_counts_actual_binary_bytes_and_keeps_source_paths_private(tmp_path):
    module=load_assets()
    first=tmp_path/'secret-workspace'/'model.onnx';first.parent.mkdir();first.write_bytes(b'x'*17)
    second=tmp_path/'driver.dll';second.write_bytes(b'y'*8)
    report=tmp_path/'report.json'
    module.write_bundle_report([('ocr/model.onnx',str(first),'DATA'),('driver.dll',str(second),'BINARY')],report)
    data=json.loads(report.read_text(encoding='utf-8'))
    assert data['total_bytes']==25
    assert data['largest_files'][0]=={'name':'ocr/model.onnx','bytes':17}
    assert str(tmp_path) not in report.read_text(encoding='utf-8')
    assert data['groups']=={'ocr':17,'driver.dll':8}


def test_missing_ocr_models_fails_before_building_exe():
    import pytest
    with pytest.raises(RuntimeError,match='ch_PP-OCRv4_rec_infer.onnx'):
        load_assets().validate_runtime_assets([],windows=False)


def test_frozen_probe_reports_failures_without_starting_app_or_creating_user_data(tmp_path,monkeypatch):
    from app.bundle_probe import write_report
    monkeypatch.setenv('FINANCE_RECONCILE_DATA_DIR',str(tmp_path/'user-data'))
    def broken():raise ValueError('Synthetic missing runtime file')
    output=tmp_path/'probe.json'
    assert write_report(output,{'good':lambda:None,'bad':broken}) is False
    result=json.loads(output.read_text(encoding='utf-8'))
    assert result['checks']['good']['ok'] is True
    assert result['checks']['bad']['ok'] is False
    assert result['checks']['bad']['error']=='ValueError: Synthetic missing runtime file'
    assert not (tmp_path/'user-data').exists()


def test_report_includes_final_exe_size_without_full_path(tmp_path):
    module=load_assets()
    exe=tmp_path/'ilog解锁工具.exe';exe.write_bytes(b'x'*23)
    report=tmp_path/'report.json'
    module.write_bundle_report([],report,exe)
    data=json.loads(report.read_text(encoding='utf-8'))
    assert data['exe_name']=='ilog解锁工具.exe' and data['exe_bytes']==23
    assert str(tmp_path) not in report.read_text(encoding='utf-8')


def test_numpy_dynamic_runtime_module_is_explicitly_kept():
    module=load_assets()
    assert 'numpy._core._exceptions' in module.NUMPY_RUNTIME_IMPORTS
    assert all(not name.startswith(('numpy.tests','numpy._core.tests')) for name in module.NUMPY_RUNTIME_IMPORTS)
