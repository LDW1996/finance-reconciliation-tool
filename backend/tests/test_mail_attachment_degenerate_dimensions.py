from io import BytesIO
from unittest.mock import Mock
import pytest
from PIL import Image
from app import mail_attachments as attachments


def image_bytes(size):
    with Image.new('RGB', size, 'white') as image:
        stream = BytesIO()
        image.save(stream, format='PNG')
        return stream.getvalue()


@pytest.mark.parametrize('size', [(1624, 1), (1, 1624), (1624, 2), (2, 1624)])
def test_degenerate_attachment_skipped_before_engine(monkeypatch, size):
    engine = Mock(side_effect=AssertionError('unsafe image reached engine'))
    monkeypatch.setattr(attachments, '_engine', engine)
    result = attachments.analyze_attachment(image_bytes(size), 'strip.png')
    assert result['status'] == 'skipped'
    assert result['reason'] == 'degenerate_image_dimensions'
    assert result['pages'][0]['method'] == 'skipped'
    assert result['pages'][0]['lines'] == []
    assert (result['pages'][0]['width'], result['pages'][0]['height']) == size
    engine.assert_not_called()


@pytest.mark.parametrize('size', [(120, 12), (12, 120), (40, 8), (8, 8)])
def test_small_text_images_still_reach_ocr(monkeypatch, size):
    engine = Mock(return_value=([], None))
    monkeypatch.setattr(attachments, '_engine', engine)
    result = attachments.analyze_attachment(image_bytes(size), 'text.png')
    assert result.get('status') != 'skipped'
    assert result['pages'][0]['method'] == 'ocr'
    engine.assert_called_once()


def test_direct_ocr_guard_logs_only_safe_dimensions(monkeypatch):
    from app import diagnostics
    log = Mock()
    monkeypatch.setattr(diagnostics, 'record', log)
    monkeypatch.setattr(attachments, '_engine', None)
    with Image.new('RGB', (1624, 1)) as image:
        with pytest.raises(attachments.UnsafeOCRDimensions, match='degenerate_image_dimensions'):
            attachments.run_ocr(image)
    assert attachments._engine is None
    log.assert_called_once_with('ocr_skipped_degenerate_dimensions', width=1624, height=1,
                                pixels=1624, reason_degenerate_dimensions=True)
