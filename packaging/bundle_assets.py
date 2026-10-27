"""Minimal explicit package data and a source-path-free build size inventory."""
import json
from collections import defaultdict
from pathlib import Path


def runtime_data(collect_data_files):
    # NumPy, pandas, ONNX Runtime, PDFium and Playwright ship maintained hooks.
    # Keep only data that has no upstream hook; Python modules are analyzed normally.
    return [
        *collect_data_files('rapidocr_onnxruntime',includes=['config.yaml','models/*.onnx']),
        *collect_data_files('extract_msg',includes=['data/**/*']),
    ]


def validate_runtime_assets(entries, windows=True):
    names={name.replace('\\','/') for name,_,_ in entries}
    required={
        'rapidocr_onnxruntime/config.yaml',
        'rapidocr_onnxruntime/models/ch_PP-OCRv4_det_infer.onnx',
        'rapidocr_onnxruntime/models/ch_ppocr_mobile_v2.0_cls_infer.onnx',
        'rapidocr_onnxruntime/models/ch_PP-OCRv4_rec_infer.onnx',
    }
    if windows:
        required.update({'playwright/driver/node.exe','pypdfium2_raw/pdfium.dll'})
    missing=required-names
    if missing:raise RuntimeError('Required runtime assets missing: '+', '.join(sorted(missing)))


def write_bundle_report(entries, output, exe_path=None):
    files={}
    for name,source,_ in entries:
        path=Path(source)
        if path.is_file():files[name.replace('\\','/')]=path.stat().st_size
    groups=defaultdict(int)
    for name,size in files.items():groups[name.split('/',1)[0]]+=size
    data=dict(total_bytes=sum(files.values()),file_count=len(files),
              groups=dict(sorted(groups.items(),key=lambda pair:pair[1],reverse=True)),
              largest_files=[dict(name=name,bytes=size) for name,size in sorted(files.items(),key=lambda pair:pair[1],reverse=True)[:40]])
    if exe_path is not None:
        exe=Path(exe_path);data.update(exe_name=exe.name,exe_bytes=exe.stat().st_size)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"Bundle inventory: {data['file_count']} files, {data['total_bytes']/1024/1024:.1f} MiB before EXE compression")
    for group,size in list(data['groups'].items())[:15]:print(f'  {group}: {size/1024/1024:.1f} MiB')
    if exe_path is not None:print(f"Single EXE: {data['exe_bytes']/1024/1024:.1f} MiB")
