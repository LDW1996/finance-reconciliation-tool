from io import BytesIO

from PIL import Image
import pytest

from app import mail_attachments
from test_mail_attachments import text_pdf


@pytest.fixture(autouse=True)
def reset_image_checks():
    mail_attachments._image_text_cache.clear()


def pdf_with_image(size=(30, 20), complete_text=True, image_text=None):
    import pypdfium2 as pdfium
    original = text_pdf()
    if not complete_text:
        for value in (b'Debit A/C Name: TEST PAYER', b'Amount: IDR 1,400,000.00', b'Value Date: 25-09-2026'):
            original = original.replace(value, b'General heading'.ljust(len(value)))
    document = pdfium.PdfDocument(original)
    try:
        page = document[0]
        if image_text:
            from PIL import ImageDraw, ImageFont
            graphic = Image.new('RGB', (480, 100), 'white')
            ImageDraw.Draw(graphic).text((2, 2), image_text, fill='black', font=ImageFont.load_default(size=32))
        else:
            graphic = Image.new('RGB', (30, 20), 'red')
        bitmap = pdfium.PdfBitmap.from_pil(graphic)
        image = pdfium.PdfImage.new(document)
        image.set_bitmap(bitmap)
        image.set_matrix(pdfium.PdfMatrix(size[0], 0, 0, size[1], 10, 10))
        page.insert_obj(image)
        page.gen_content()
        out = BytesIO()
        document.save(out)
        page.close()
        bitmap.close()
        raw = out.getvalue()
    finally:
        document.close()
    return raw


def test_complete_text_with_small_logo_only_checks_image_region(monkeypatch):
    calls = []
    monkeypatch.setattr(mail_attachments, 'run_ocr', lambda image: calls.append(image.size) or [])
    result = mail_attachments.analyze_attachment(pdf_with_image(), 'receipt.pdf')
    assert result['pages'][0]['method'] == 'pdf_text'
    assert result['fields']['amount'][0]['value'] == '1400000.00'
    assert result['fields']['payer'][0]['value'] == 'TEST PAYER'
    assert result['fields']['date'][0]['value'] == '2026-09-25'
    assert len(calls) == 1 and calls[0][0] < 100 and calls[0][1] < 100


@pytest.mark.parametrize('size,complete_text', [((400, 230), True), ((30, 20), False)])
def test_large_image_or_incomplete_text_keeps_ocr(monkeypatch, size, complete_text):
    calls = []
    monkeypatch.setattr(mail_attachments, 'run_ocr', lambda image: calls.append(image.size) or [])
    result = mail_attachments.analyze_attachment(pdf_with_image(size, complete_text), 'receipt.pdf')
    assert len(calls) == 1
    assert result['pages'][0]['method'] == 'ocr'


def test_text_page_is_rendered_at_preview_size(monkeypatch):
    calls = []
    original = mail_attachments._preview
    monkeypatch.setattr(mail_attachments, '_preview', lambda image: calls.append(image.size) or original(image))
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(text_pdf())
    try:
        page = document[0]
        page.set_mediabox(0, 0, 2000, 3000)
        out = BytesIO()
        document.save(out)
        page.close()
    finally:
        document.close()
    result = mail_attachments.analyze_attachment(out.getvalue(), 'text.pdf')
    assert result['pages'][0]['method'] == 'pdf_text'
    # Large native-text pages need only a preview raster, not a 12 MP OCR raster.
    assert calls[0][0] <= 1200 and calls[0][1] <= 1600


def test_unreadable_text_layer_falls_back_to_ocr(monkeypatch):
    import pypdfium2 as pdfium
    calls = []
    def unavailable(page):
        raise RuntimeError('text layer unavailable')
    monkeypatch.setattr(pdfium.PdfPage, 'get_textpage', unavailable)
    monkeypatch.setattr(mail_attachments, 'run_ocr', lambda image: calls.append(image.size) or [])
    result = mail_attachments.analyze_attachment(text_pdf(), 'receipt.pdf')
    assert len(calls) == 1
    assert result['pages'][0]['method'] == 'ocr'


def test_small_image_containing_money_falls_back_to_full_ocr(monkeypatch):
    calls = []
    def ocr(image):
        calls.append(image.size)
        texts = ['Amount: IDR 900.00'] if image.width < 500 else ['Amount: IDR 1,400,000.00', 'Amount: IDR 900.00']
        return [dict(text=t, confidence=.99, box=None) for t in texts]
    monkeypatch.setattr(mail_attachments, 'run_ocr', ocr)
    result = mail_attachments.analyze_attachment(pdf_with_image((120, 25)), 'receipt.pdf')
    assert result['pages'][0]['method'] == 'ocr'
    assert len(calls) == 2 and calls[0][0] < calls[1][0]
    assert {v['value'] for v in result['fields']['amount']} == {'1400000.00', '900.00'}
    assert any('付款金额存在多个候选' in w for w in result['warnings'])


def test_real_small_image_amount_is_not_silently_lost():
    raw = pdf_with_image((120, 25), image_text='Amount: IDR 900.00')
    result = mail_attachments.analyze_attachment(raw, 'mixed.pdf')
    assert result['pages'][0]['method'] == 'ocr'
    assert {v['value'] for v in result['fields']['amount']} == {'1400000.00', '900.00'}
    assert any('付款金额存在多个候选' in w for w in result['warnings'])


def test_identical_textless_image_crop_is_checked_once(monkeypatch):
    calls = []
    monkeypatch.setattr(mail_attachments, 'run_ocr', lambda image: calls.append(image.size) or [])
    raw = pdf_with_image()
    for _ in range(2):
        assert mail_attachments.analyze_attachment(raw, 'receipt.pdf')['pages'][0]['method'] == 'pdf_text'
    assert len(calls) == 1
