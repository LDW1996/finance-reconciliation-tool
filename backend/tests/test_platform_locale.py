from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.platform_locale import install_platform_locale, localize_response
from app.mail_online import OUTPUT_LANGUAGE


def test_generated_fields_only_and_original_business_data_unchanged():
    original = {'message': '任务不存在', 'subject': '完成', 'payer': '张三',
                'status': 'waiting', 'detail': '客户原始说明：请保留',
                'rows': [{'name': '等待中', 'reason': '客户提供的原文'}]}
    translated = localize_response(original)
    assert translated['message'] == 'Task not found'
    assert translated['subject'] == original['subject']
    assert translated['payer'] == original['payer']
    assert translated['status'] == original['status']
    assert translated['detail'] == original['detail']
    assert translated['rows'] == original['rows']
    assert original['message'] == '任务不存在'


def test_request_header_language_and_context_isolation():
    app = FastAPI()
    @app.get('/api/test')
    def endpoint():
        return {'message': '任务不存在', 'language': OUTPUT_LANGUAGE.get(), 'subject': '完成'}
    install_platform_locale(app)
    with TestClient(app) as client:
        english = client.get('/api/test', headers={'X-Platform-Language': 'en'})
        assert english.json() == {'message': 'Task not found', 'language': 'en', 'subject': '完成'}
        assert client.get('/api/test').json()['message'] == '任务不存在'
        assert client.get('/api/test').json()['language'] == 'zh-CN'
    assert OUTPUT_LANGUAGE.get() == 'zh-CN'
