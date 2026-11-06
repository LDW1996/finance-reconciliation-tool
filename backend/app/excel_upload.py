"""Bound spreadsheet inputs before pandas/openpyxl allocate a workbook."""
from io import BytesIO
from zipfile import ZipFile, BadZipFile
from fastapi import HTTPException

MAX_UPLOAD_BYTES = 50 * 1024**2
MAX_EXPANDED_BYTES = 256 * 1024**2
MAX_ZIP_ENTRIES = 10000

async def read_excel_upload(upload):
    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, '每个 Excel 文件不能超过 50 MB，请按月份或公司拆分。')
    return data


def check_excel_archive(data):
    # Legacy .xls is not ZIP. Validate ZIP content regardless of its filename.
    if not data.startswith(b'PK'):
        return
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES or sum(e.file_size for e in entries) > MAX_EXPANDED_BYTES:
                raise HTTPException(413, 'Excel 解压后内容过大，请拆分后重试。')
            total = 0
            for entry in entries:
                with archive.open(entry) as stream:
                    while chunk := stream.read(1024 * 1024):
                        total += len(chunk)
                        if total > MAX_EXPANDED_BYTES:
                            raise HTTPException(413, 'Excel 解压后内容过大，请拆分后重试。')
    except BadZipFile as exc:
        raise HTTPException(400, 'Excel 文件损坏或格式无效，请重新导出后上传。') from exc


def run_excel(function, *inputs, **options):
    for item in inputs:
        if isinstance(item, BytesIO):
            check_excel_archive(item.getvalue())
    return function(*inputs, **options)
