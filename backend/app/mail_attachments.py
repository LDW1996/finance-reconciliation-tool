"""Offline attachment text recognition and evidence-backed payment suggestions."""

import base64
from collections import OrderedDict
from hashlib import sha256
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
import math
import re
from threading import Lock

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_MAIL_BYTES = 25 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS = 20
MAX_PAGES = 10
MAX_PIXELS = 12_000_000
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff')
_engine = None
_inference_lock = Lock()
_image_text_cache = OrderedDict()
_image_text_cache_lock = Lock()


def capture_attachment(name, raw=None, size=None):
    name = str(name or '未命名附件').replace('\\', '/').rsplit('/', 1)[-1][:240]
    supported = name.lower().endswith((*IMAGE_EXTENSIONS, '.pdf'))
    size = len(raw) if raw is not None else (size or 0)
    error = ''
    status = 'pending'
    if not supported:
        status, error = 'unsupported', '当前支持图片及 PDF。'
    elif size > MAX_ATTACHMENT_BYTES:
        status, error = 'skipped', '附件超过 10 MB，请拆分后导入。'
    elif raw is None:
        status, error = 'failed', '附件内容未读取，请重新导入或读取邮件。'
    return dict(name=name, size=size, mime='application/pdf' if name.lower().endswith('.pdf') else 'image',
                status=status, error=error, raw=raw if status == 'pending' else b'')


def run_ocr(image):
    global _engine
    # Each worker owns its engine; retain a lock for direct in-process calls.
    with _inference_lock:
        if _engine is None:
            import onnxruntime
            onnxruntime.disable_telemetry_events()
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1,
                              det_use_cuda=False, cls_use_cuda=False, rec_use_cuda=False)
        import numpy as np
        rows, _ = _engine(np.array(image.convert('RGB')))
    width, height = image.size
    return [dict(text=str(text), confidence=round(float(score), 4),
                 box=[[round(float(x)/width, 5), round(float(y)/height, 5)] for x, y in box])
            for box, text, score in (rows or [])]


def _preview(image):
    small = image.copy()
    small.thumbnail((1200, 1600))
    output = BytesIO()
    small.save(output, format='PNG')
    small.close()
    return base64.b64encode(output.getvalue()).decode('ascii')


def _compact(text):
    return re.sub(r'[\s:/：.,&()_-]+', '', text).lower()


def _nearby(lines, index):
    """Prefer same-row values, then vertically aligned values below the label."""
    label = lines[index]
    box = label.get('box')
    if not box:
        return [(i, lines[i]['text']) for i in range(index+1, min(len(lines), index+3))]
    x0, y0 = box[0]; x1, y1 = box[2]
    height = max(y1-y0, .006)
    found = []
    for i, line in enumerate(lines):
        other = line.get('box')
        if i == index or not other: continue
        ox, oy = other[0]; ex, ey = other[2]
        if ox >= x1-.01 and abs((oy+ey-y0-y1)/2) < height*.8:
            found.append((0, ox-x1, i, line['text']))
        elif oy >= y1-.004 and oy-y1 < height*2.8 and abs(ox-x0) < .07:
            found.append((1, oy-y1, i, line['text']))
    return [(i, text) for _, _, i, text in sorted(found)[:3]]


def _money(text):
    text = text.strip().strip(':： ')
    matched = re.fullmatch(r'(?:(IDR|Rp|USD|EUR|CNY)\s*)?([\d][\d., ]*)(?:\s*(IDR|USD|EUR|CNY))?', text, re.I)
    if not matched: return None
    value = matched[2].replace(' ', '')
    if ',' in value and '.' in value:
        value = value.replace('.', '').replace(',', '.') if value.rfind(',') > value.rfind('.') else value.replace(',', '')
    elif ',' in value:
        value = value.replace(',', '.') if len(value.rsplit(',', 1)[1]) == 2 else value.replace(',', '')
    elif '.' in value:
        groups=value.split('.')
        if len(groups)>2 and not all(len(g)==3 for g in groups[1:]): return None
        if len(groups)>2 or len(groups[-1])==3: value=value.replace('.', '')
    try:
        number = Decimal(value)
        if not number.is_finite() or number <= 0: return None
    except InvalidOperation: return None
    currency = (matched[1] or matched[3] or '').upper()
    return format(number.quantize(Decimal('.01')), 'f'), ('IDR' if currency == 'RP' else currency)


def _dates(text):
    # Common OCR zero/O confusion is corrected only in a month name.
    text = re.sub(r'(?i)0ct', 'Oct', text)
    date_end=r'(?=\d{2}:|\D|$)'
    patterns = [r'(?<!\d)(\d{1,2})[-/](\d{1,2})[-/](20\d{2}|\d{2})'+date_end,
                r'(?<!\d)(\d{1,2})[\s-]*([A-Za-z]{3,9})[\s-]*(20\d{2}|\d{2})'+date_end,
                r'(?<!\d)(20\d{2})[-/](\d{1,2})[-/](\d{1,2})(?!\d)']
    results = []
    months = {m.lower(): i for i, m in enumerate(['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'], 1)}
    for kind, pattern in enumerate(patterns):
        for match in re.finditer(pattern, text):
            a,b,c=match.groups()
            try:
                if kind == 2: year, month, day = int(a), int(b), int(c)
                else:
                    day, month, year = int(a), (months[b[:3].lower()] if kind == 1 else int(b)), int(c)
                    if year < 100: year += 2000
                value = datetime(year,month,day).date().isoformat()
                if value not in results: results.append(value)
            except (ValueError, KeyError): pass
    return results


def extract_fields(lines, page):
    fields = {key: [] for key in ('payer', 'date', 'amount', 'fee', 'identifiers')}
    # Labels may lose whitespace and slashes during OCR.
    labels = {
        'payer': ('debitacname', 'namapengirim', 'sendername', 'payername', '付款人', '打款人', 'payer'),
        'amount': ('transactionamount', 'transferamount', 'amount', 'nominal', '付款金额', '转账金额'),
        'fee': ('transferfee', 'servicecharges', 'fee', 'biayaadmin', '手续费'),
    }
    def add(kind, value, index, label_index=None, **extra):
        if any(f['value'] == value and f.get('currency', '') == extra.get('currency', '') for f in fields[kind]): return
        line = lines[index]
        confidence = line.get('confidence')
        if label_index is not None and lines[label_index].get('confidence') is not None:
            confidence = min(confidence if confidence is not None else 1, lines[label_index]['confidence'])
        fields[kind].append(dict(value=value, page=page, line=index, quote=line['text'],
                                 confidence=confidence, box=line.get('box'), **extra))
    for index, line in enumerate(lines):
        text, compact = line['text'], _compact(line['text'])
        for kind, names in labels.items():
            for label in names:
                if not compact.startswith(label): continue
                if label=='payer' and not re.match(r'^\s*payer\s*[:：]',text,re.I):continue
                # Extract the value from the same line by consuming the label's letters.
                pattern = r'^\s*' + r'[\s/:：.,&()_-]*'.join(re.escape(c) for c in label) + r'[\s/:：.,&()_-]*'
                remainder = re.sub(pattern, '', text, count=1, flags=re.I)
                options = [(index, remainder)] if remainder != text and remainder.strip() else []
                options += _nearby(lines, index)
                for value_index, value in options:
                    value = value.strip().strip(':： ')
                    if kind == 'payer':
                        if not re.search(r'[A-Za-z\u4e00-\u9fff]{2}', value) or any(word in _compact(value) for word in ('account','currency','rek ening','bankname','creditac','debitac')): continue
                        add(kind, value, value_index, index); break
                    elif kind == 'fee' and 'FREE' in value.upper():
                        add(kind, '0.00', value_index, index, currency='IDR', note='凭证标注 FREE，原费用可能为划线金额'); break
                    else:
                        parsed = _money(value)
                        if parsed:
                            number, currency = parsed
                            if not currency:
                                # A bare number requires an explicit currency elsewhere on this page.
                                currencies = set(re.findall(r'\b(IDR|USD|EUR|CNY)\b', '\n'.join(l['text'] for l in lines), re.I))
                                if len(currencies) == 1: currency = next(iter(currencies)).upper()
                            add(kind, number, value_index, index, currency=currency); break
                break
        for value in _dates(text): add('date', value, index)
        for value in re.findall(r'(?<![A-Za-z0-9])(?:INV\s*)?(SLINXTF\d+|SLIND-\d{4}-\d+|SIT[A-Z0-9]{7,30}|S20\d{10,20})(?![A-Za-z0-9])', text.upper()):
            if any(c.isdigit() for c in value): add('identifiers', value, index)
    # BI-FAST forms place the payer's account name inside the From section.
    start = next((i for i,l in enumerate(lines) if _compact(l['text']) == 'from'), None)
    end = next((i for i,l in enumerate(lines) if start is not None and i > start and _compact(l['text']) == 'to'), len(lines))
    if not fields['payer'] and start is not None:
        account = next((i for i in range(start+1,end) if _compact(lines[i]['text']) == 'accountname'), None)
        if account is not None:
            options = _nearby(lines, account)
        else: options = _nearby(lines, start)
        for i, value in options:
            if i >= end: continue
            value = value.strip(':： ')
            if re.search(r'[A-Za-z]{3}', value) and not any(x in _compact(value) for x in ('bank', 'account', 'currency', 'companyname')):
                add('payer', value, i, account if account is not None else start); break
    return fields


def _page_result(image, number, native_text=''):
    if native_text:
        lines = [dict(text=t.strip(), confidence=None, box=None) for t in native_text.splitlines() if t.strip()][:3000]
        method = 'pdf_text'
    else:
        lines, method = run_ocr(image), 'ocr'
    return dict(number=number, method=method, width=image.width, height=image.height,
                preview='', lines=lines, text='\n'.join(l['text'] for l in lines),
                fields=extract_fields(lines, number))


def _native_pdf_text(page, native, number):
    """Plan text extraction; image regions still need a text audit before use."""
    text = native.strip()
    if len(text) < 20 or '\ufffd' in text or any(ord(c) < 32 and c not in '\n\r\t' for c in text):
        return '', []
    try:
        images = list(page.get_objects(filter=[3]))
        if not images:
            return text, []
        fields = extract_fields([dict(text=t.strip(), confidence=None, box=None)
                                 for t in text.splitlines() if t.strip()], number)
        if not all(fields.get(k) for k in ('payer', 'amount', 'date')):
            return '', []
        width, height = page.get_size()
        # The crop mapping below is deliberately limited to unrotated pages
        # whose visible PDF coordinate origin is (0,0).
        if page.get_rotation() or tuple(page.get_bbox()) != (0, 0, width, height):
            return '', []
        regions, total_area = [], 0
        for image in images:
            left, bottom, right, top = image.get_pos()
            iw, ih = right-left, top-bottom
            if not all(math.isfinite(v) for v in (left, bottom, right, top, iw, ih)):
                return '', []
            if (iw <= 0 or ih <= 0 or left < 0 or bottom < 0 or right > width or top > height
                    or iw > width*.25 or ih > height*.25 or iw*ih > width*height*.02):
                return '', []
            total_area += iw*ih
            regions.append((left, bottom, right, top))
        return (text, regions) if total_area <= width*height*.03 else ('', [])
    except MemoryError:
        raise
    except Exception:
        return '', []


def _pdf_images_are_textless(image, regions, width, height):
    """Tiny images can contain corrections. Check crops; any text keeps full OCR."""
    sx, sy = image.width/width, image.height/height
    for left, bottom, right, top in regions:
        bounds = (max(0, math.floor(left*sx)-2), max(0, math.floor((height-top)*sy)-2),
                  min(image.width, math.ceil(right*sx)+2), min(image.height, math.ceil((height-bottom)*sy)+2))
        crop = image.crop(bounds)
        try:
            key = (crop.size, sha256(crop.tobytes()).digest())
            with _image_text_cache_lock:
                contains_text = _image_text_cache.get(key)
                if contains_text is not None:
                    _image_text_cache.move_to_end(key)
            if contains_text is None:
                try:
                    contains_text = any(line.get('text', '').strip() for line in run_ocr(crop))
                except MemoryError:
                    raise
                except Exception:
                    return False
                with _image_text_cache_lock:
                    _image_text_cache[key] = contains_text
                    _image_text_cache.move_to_end(key)
                    if len(_image_text_cache) > 128:
                        _image_text_cache.popitem(last=False)
            if contains_text:
                return False
        finally:
            crop.close()
    return True


def analyze_attachment(raw, name):
    if len(raw) > MAX_ATTACHMENT_BYTES: raise ValueError('附件超过 10 MB，请拆分后导入。')
    pages, warnings = [], []
    if name.lower().endswith('.pdf'):
        import pypdfium2 as pdfium
        try: document = pdfium.PdfDocument(raw)
        except Exception as error: raise ValueError('PDF 无法打开，可能已损坏或需要密码。') from error
        try:
            if len(document) > MAX_PAGES: warnings.append(f'PDF 共 {len(document)} 页，仅识别前 {MAX_PAGES} 页。')
            for number in range(min(len(document), MAX_PAGES)):
                page = document[number]
                bitmap = textpage = None
                try:
                    width, height = page.get_size()
                    if width <= 0 or height <= 0: raise ValueError('页面尺寸无效')
                    try:
                        textpage = page.get_textpage()
                        native, regions = _native_pdf_text(page, textpage.get_text_bounded(), number+1)
                    except MemoryError:
                        raise
                    except Exception:
                        native, regions = '', []
                    scale = min(2.0, 1200/width, 1600/height) if native and not regions else min(2.0, math.sqrt(MAX_PIXELS/(width*height)))
                    bitmap = page.render(scale=scale)
                    image = bitmap.to_pil().convert('RGB')
                    try:
                        if native and regions and not _pdf_images_are_textless(image, regions, width, height):
                            native = ''
                        pages.append(_page_result(image, number+1, native))
                    finally: image.close()
                except MemoryError:
                    raise
                except Exception as error:
                    warnings.append(f'第 {number+1} 页识别失败（{type(error).__name__}），请核对原 PDF。')
                finally:
                    if textpage: textpage.close()
                    if bitmap: bitmap.close()
                    page.close()
        finally: document.close()
        if not pages: raise ValueError('PDF 未能读取任何页面，请检查文件或本地识别组件。')
    else:
        try:
            with Image.open(BytesIO(raw)) as source:
                if source.width*source.height > MAX_PIXELS: raise ValueError('图片超过 1200 万像素，请缩小后导入。')
                if getattr(source,'n_frames',1) > 1: warnings.append('多帧图片仅识别第一帧。')
                image = ImageOps.exif_transpose(source).convert('RGB')
                try: pages.append(_page_result(image, 1))
                finally: image.close()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
            raise ValueError('图片无法打开，可能格式不受支持或已损坏。') from error
    fields = {key: [item for p in pages for item in p['fields'][key]] for key in ('payer','date','amount','fee','identifiers')}
    for field, label in [('payer','付款人'),('date','付款日期'),('amount','付款金额')]:
        unique = set(f['value'] for f in fields[field])
        if not unique: warnings.append(f'未明确识别到{label}，请查看凭证。')
        elif len(unique) > 1: warnings.append(f'{label}存在多个候选，请逐页核对。')
    if any(l['confidence'] is not None and l['confidence'] < .85 for p in pages for l in p['lines']):
        warnings.append('部分文字识别置信度较低，请核对原图；置信度不代表付款信息已确认。')
    return dict(pages=pages, fields=fields, warnings=warnings, engine='RapidOCR / PDFium（本地）')
