"""Request-scoped localization of generated response fields; never persisted."""
import json
from starlette.responses import Response
from . import mail_english
from .mail_online import OUTPUT_LANGUAGE

EXACT = {
    '请先读取或导入银行流水。': 'Read or import bank transactions first.',
    '流水数量超出导出上限，请拆分流水后重试。': 'Too many transactions to export. Split the ledger and retry.',
    '核对包附件超过 512 MB，请减少流水范围后重试。': 'Package attachments exceed 512 MB. Import a smaller ledger and retry.',
    '导出目录磁盘空间不足，请释放空间后重试。': 'Insufficient disk space for export. Free space and retry.',
    '任务不存在': 'Task not found', '截图不存在': 'Screenshot not found',
    '账号不存在，请刷新页面后重试': 'Account not found. Refresh and retry.',
    '请选择已保存密码的业务账号': 'Select a business account with a saved password.',
    '缺少浏览器或自动化依赖，无法执行': 'Browser or automation dependencies are unavailable.',
    '账号名称和用户名不能为空': 'Account name and username are required.',
    '请通过本机账号管理页面修改': 'Use the local account management page.',
    '无法读取本机账号数据，请检查应用数据目录': 'Cannot read local accounts. Check the application data directory.',
    '保存失败，请检查应用数据目录是否可写': 'Save failed. Check that the application data directory is writable.',
    '删除失败，请检查应用数据目录是否可写': 'Delete failed. Check that the application data directory is writable.',
    '兜底账号必须存在、已保存密码，且与主账号不同': 'The fallback account must exist, have a saved password, and differ from the primary account.',
}

def generated_text(value):
    if not isinstance(value, str):
        return value
    if value in EXACT:
        return EXACT[value]
    translated = mail_english.text(value)
    # Unknown upstream or business text is retained verbatim, never replaced by
    # a support hash or guessed translation in the unified application.
    return value if translated.startswith('Operation requires attention (diagnostic ') else translated


def localize_response(value, path=()):
    if isinstance(value, dict):
        return {key: localize_response(item, path + (key,)) for key, item in value.items()}
    if isinstance(value, list):
        return [localize_response(item, path) for item in value]
    key = path[-1] if path else None
    if key in {'remark', 'search_info'} and isinstance(value, str):
        return mail_english.generated_lines(value)
    if key == 'label' and len(path) > 1 and path[-2] in {'mapping', 'customer_mapping'}:
        return generated_text(value)
    return generated_text(value) if key in mail_english.GENERATED else value


def install_platform_locale(app):
    @app.middleware('http')
    async def platform_locale(request, call_next):
        english = request.headers.get('x-platform-language') == 'en'
        token = OUTPUT_LANGUAGE.set('en' if english else 'zh-CN')
        try:
            result = await call_next(request)
            if not english or not request.url.path.startswith('/api/'):
                return result
            export = request.url.path == '/api/mail/matching/export' and result.status_code == 200
            if not export and 'application/json' not in result.headers.get('content-type', ''):
                return result
            raw = b''.join([part async for part in result.body_iterator])
            headers = dict(result.headers)
            headers.pop('content-length', None)
            if export:
                raw = mail_english.english_export(raw)
                headers['content-disposition'] = 'attachment; filename=mail-reconciliation.xlsx'
            else:
                raw = json.dumps(localize_response(json.loads(raw)), ensure_ascii=False).encode('utf-8')
            return Response(raw, status_code=result.status_code, headers=headers, background=result.background)
        finally:
            OUTPUT_LANGUAGE.reset(token)
