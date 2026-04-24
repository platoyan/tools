#!/usr/bin/env python3
"""
扫描文件夹下所有 PDF 和 EPUB 文件,提取 ISBN(支持 ISBN-10 和 ISBN-13)。
支持文字版 PDF 和扫描版 PDF(自动 OCR 回退)。

用法:
    python extract_isbn.py <目录路径> [-t isbns.txt] [-o mapping.csv]
    python extract_isbn.py <目录路径> --no-ocr          # 禁用 OCR
    python extract_isbn.py <目录路径> --force-ocr       # 强制所有 PDF 都 OCR

Python 依赖:
    pip install pypdf pdf2image pytesseract pillow

系统依赖(OCR 需要):
    # macOS(tesseract-lang 一次装齐所有语言):
    brew install tesseract tesseract-lang poppler
    # Ubuntu/Debian:
    sudo apt install tesseract-ocr poppler-utils \\
        tesseract-ocr-chi-sim tesseract-ocr-chi-tra \\
        tesseract-ocr-jpn tesseract-ocr-jpn-vert
"""

import argparse
import csv
import re
import sys
import zipfile
from pathlib import Path


# 分隔符字符类
_WS = r'[\s\u3000]'                                        # 空白:分隔
_DASH = r'[-\u2010-\u2015\u2212\uFE63\uFF0D]'              # 连字符/破折号:延续
_SEP = r'(?:' + _WS + r'|' + _DASH + r')'                  # 综合分隔符

# ========== ISBN-13 ==========
_ISBN13_CORE = r'97[89](?:' + _SEP + r'*\d){10}'
_NOT_CONTINUED_13 = r'(?!' + _DASH + r'*\d)'

ISBN13_WITH_PREFIX = re.compile(
    r'ISBN(?:' + _SEP + r'*13)?[:\s]*' + _SEP + r'*'
    r'(' + _ISBN13_CORE + r')' + _NOT_CONTINUED_13,
    re.IGNORECASE
)
ISBN13_BARE = re.compile(
    r'(?<![\d])(' + _ISBN13_CORE + r')' + _NOT_CONTINUED_13
)
# EAN-13 条码格式: "9 787111 641247" (1-6-6 空白分组)
ISBN13_BARCODE = re.compile(
    r'(?<![\d])'
    r'(9' + _WS + r'+7[89]\d{4}' + _WS + r'+\d{6})'
    r'(?![\d])'
)

# ========== ISBN-10 ==========
# 9 位数字 + 校验位(数字或 X),中间可有分隔符
_ISBN10_CORE = r'\d(?:' + _SEP + r'*\d){8}' + _SEP + r'*[\dXx]'
_NOT_CONTINUED_10 = r'(?!' + _DASH + r'*[\dXx])'

# ISBN-10 只接受带 "ISBN" 前缀的,避免跟其他 10 位数字/身份证片段/电话冲突
ISBN10_WITH_PREFIX = re.compile(
    r'ISBN(?:' + _SEP + r'*10)?[:\s]*' + _SEP + r'*'
    r'(' + _ISBN10_CORE + r')' + _NOT_CONTINUED_10,
    re.IGNORECASE
)

TEXT_THRESHOLD = 100
OCR_FRONT_PAGES = 8
OCR_BACK_PAGES = 3


def clean_isbn(raw: str) -> str:
    """去掉所有分隔符,统一大写。"""
    return re.sub(_SEP, '', raw).upper()


def validate_isbn13(isbn: str) -> bool:
    isbn = clean_isbn(isbn)
    if len(isbn) != 13 or not isbn.isdigit():
        return False
    if not (isbn.startswith('978') or isbn.startswith('979')):
        return False
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(isbn))
    return total % 10 == 0


def validate_isbn10(isbn: str) -> bool:
    isbn = clean_isbn(isbn)
    if len(isbn) != 10:
        return False
    if not re.fullmatch(r'\d{9}[\dX]', isbn):
        return False
    total = sum((10 - i) * (10 if c == 'X' else int(c)) for i, c in enumerate(isbn))
    return total % 11 == 0


def find_isbns(text: str) -> list[str]:
    """提取合法 ISBN-13 和 ISBN-10,按首次出现顺序去重。

    策略:ISBN-13 优先,匹配到的区间对 ISBN-10 阻塞,
    避免 ISBN-13 的前 10 位被误识别为独立的 ISBN-10。
    """
    found = []
    seen = set()

    def add13(raw: str) -> None:
        digits = clean_isbn(raw)
        if len(digits) >= 13:
            candidate = digits[:13]
            if validate_isbn13(candidate) and candidate not in seen:
                found.append(candidate)
                seen.add(candidate)

    def add10(raw: str) -> None:
        digits = clean_isbn(raw)
        if len(digits) >= 10:
            candidate = digits[:10]
            if validate_isbn10(candidate) and candidate not in seen:
                found.append(candidate)
                seen.add(candidate)

    # 1. 先抓 ISBN-13,记录所有候选区间(无论校验是否通过)
    blocked_spans = []

    for m in ISBN13_WITH_PREFIX.finditer(text):
        blocked_spans.append((m.start(), m.end()))
        add13(m.group(1))
    for m in ISBN13_BARE.finditer(text):
        blocked_spans.append((m.start(), m.end()))
        add13(m.group(1))
    for m in ISBN13_BARCODE.finditer(text):
        blocked_spans.append((m.start(), m.end()))
        add13(m.group(1))

    # 2. 再抓 ISBN-10,跳过与 ISBN-13 候选区间重叠的匹配
    def overlaps_blocked(start: int, end: int) -> bool:
        return any(not (end <= s or start >= e) for s, e in blocked_spans)

    for m in ISBN10_WITH_PREFIX.finditer(text):
        if overlaps_blocked(m.start(), m.end()):
            continue
        add10(m.group(1))

    return found


# -------------------- PDF 文本提取 --------------------

def extract_pdf_text(path: Path, max_pages: int) -> tuple[str, int]:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    total = len(reader.pages)
    indices = list(range(min(max_pages, total)))
    indices += [i for i in range(max(0, total - 3), total) if i not in indices]
    parts = []
    for i in indices:
        try:
            parts.append(reader.pages[i].extract_text() or '')
        except Exception:
            continue
    return '\n'.join(parts), total


def extract_pdf_metadata(path: Path) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        meta = reader.metadata or {}
        return ' '.join(str(v) for v in meta.values())
    except Exception:
        return ''


# -------------------- PDF OCR --------------------

_ocr_checked = False
_ocr_available = False
_ocr_error = ''
_ocr_langs: list[str] = []


def check_ocr_available() -> tuple[bool, str]:
    global _ocr_checked, _ocr_available, _ocr_error, _ocr_langs
    if _ocr_checked:
        return _ocr_available, _ocr_error
    _ocr_checked = True

    try:
        import pdf2image  # noqa: F401
        import pytesseract
    except ImportError as e:
        _ocr_error = f'缺少 Python 包: {e.name}。请 pip install pdf2image pytesseract pillow'
        return False, _ocr_error

    try:
        pytesseract.get_tesseract_version()
    except Exception as e:
        _ocr_error = f'找不到 tesseract 命令: {e}'
        return False, _ocr_error

    try:
        _ocr_langs = list(pytesseract.get_languages())
        if 'eng' not in _ocr_langs:
            _ocr_error = f'tesseract 缺少 eng 语言包,已有: {_ocr_langs}'
            return False, _ocr_error
        cjk_langs = [l for l in ('chi_sim', 'chi_tra', 'jpn', 'jpn_vert') if l in _ocr_langs]
        if not cjk_langs:
            _ocr_error = (f'tesseract 缺少中日文语言包(需要 chi_sim/chi_tra/jpn 任一),'
                          f'已有: {_ocr_langs}')
            return False, _ocr_error
    except Exception as e:
        _ocr_error = f'无法查询 tesseract 语言: {e}'
        return False, _ocr_error

    _ocr_available = True
    return True, ''


def ocr_pdf_pages(path: Path, total_pages: int) -> str:
    """通过 subprocess 直接调用 tesseract,避开 pytesseract 的临时 PPM 文件问题。"""
    from pdf2image import convert_from_path
    import subprocess
    import io

    front_end = min(OCR_FRONT_PAGES, total_pages)
    front = list(range(1, front_end + 1))
    back_start = max(front_end + 1, total_pages - OCR_BACK_PAGES + 1)
    back = list(range(back_start, total_pages + 1)) if total_pages > OCR_FRONT_PAGES else []

    lang_parts = [l for l in ('chi_sim', 'chi_tra', 'jpn', 'jpn_vert', 'eng')
                  if l in _ocr_langs]
    lang = '+'.join(lang_parts) if lang_parts else 'eng'

    def ocr_via_stdin(img) -> str:
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        proc = subprocess.run(
            ['tesseract', 'stdin', 'stdout', '-l', lang, '--psm', '6'],
            input=buf.getvalue(),
            capture_output=True,
            timeout=60,
        )
        if proc.returncode != 0:
            err = proc.stderr.decode('utf-8', errors='ignore')
            raise RuntimeError(f'tesseract exit {proc.returncode}: {err[:200]}')
        return proc.stdout.decode('utf-8', errors='ignore')

    texts = []
    for page_num in front + back:
        try:
            images = convert_from_path(
                str(path), dpi=300,
                first_page=page_num, last_page=page_num,
                fmt='png',
            )
            for img in images:
                texts.append(ocr_via_stdin(img))
        except Exception as e:
            texts.append(f'[OCR page {page_num} failed: {e}]')
    return '\n'.join(texts)


# -------------------- EPUB --------------------

_HTML_TAG = re.compile(r'<[^>]+>')
_DC_NS = '{http://purl.org/dc/elements/1.1/}'
_OPF_NS = '{http://www.idpf.org/2007/opf}'


def _parse_opf_identifiers(opf_content: str) -> list[str]:
    """从 OPF XML 里结构化提取 dc:identifier 和 ISBN 相关 meta。"""
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(opf_content)
    except ET.ParseError:
        return []

    texts = []
    for elem in root.iter(_DC_NS + 'identifier'):
        if elem.text:
            texts.append(elem.text)
    for elem in root.iter():
        if elem.tag.endswith('}meta') or elem.tag == 'meta':
            prop = (elem.get('property') or elem.get('name') or '').lower()
            if 'isbn' in prop and elem.text:
                texts.append(elem.text)
    return texts


def _strip_html(content: str) -> str:
    return _HTML_TAG.sub(' ', content)


def extract_epub_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            opf_files = [n for n in names if n.lower().endswith('.opf')]
            content_files = sorted(
                n for n in names
                if n.lower().endswith(('.xhtml', '.html', '.htm'))
            )

            opf_parts = []
            opf_identifier_texts = []
            for name in opf_files:
                try:
                    with z.open(name) as f:
                        content = f.read().decode('utf-8', errors='ignore')
                    opf_identifier_texts.extend(_parse_opf_identifiers(content))
                    opf_parts.append(content)
                except Exception:
                    continue

            probe_text = '\n'.join(opf_identifier_texts) + '\n' + '\n'.join(opf_parts)
            if find_isbns(probe_text):
                return probe_text

            parts = list(opf_parts)
            for name in content_files:
                try:
                    with z.open(name) as f:
                        content = f.read().decode('utf-8', errors='ignore')
                    parts.append(_strip_html(content))
                except Exception:
                    continue
            return '\n'.join(parts)
    except zipfile.BadZipFile:
        return ''


# -------------------- 主处理 --------------------

def process_pdf(path: Path, max_pages: int, ocr_mode: str) -> tuple[list[str], str, str]:
    try:
        text, total_pages = extract_pdf_text(path, max_pages)
    except Exception as e:
        return [], f'PDF 读取失败: {e}', ''

    metadata = extract_pdf_metadata(path)
    text_isbns = find_isbns(metadata + '\n' + text)

    need_ocr = False
    if ocr_mode == 'force':
        need_ocr = True
    elif ocr_mode == 'auto':
        if len(text.strip()) < TEXT_THRESHOLD or not text_isbns:
            need_ocr = True

    if not need_ocr:
        return text_isbns, '', 'text' if text_isbns else ''

    ok, err = check_ocr_available()
    if not ok:
        return text_isbns, f'OCR 不可用: {err}', 'text' if text_isbns else ''

    try:
        ocr_text = ocr_pdf_pages(path, total_pages)
    except Exception as e:
        return text_isbns, f'OCR 执行失败: {e}', 'text' if text_isbns else ''

    ocr_isbns = find_isbns(ocr_text)

    all_isbns = list(text_isbns)
    seen = set(all_isbns)
    for isbn in ocr_isbns:
        if isbn not in seen:
            all_isbns.append(isbn)
            seen.add(isbn)

    if text_isbns and ocr_isbns:
        source = 'both'
    elif ocr_isbns:
        source = 'ocr'
    elif text_isbns:
        source = 'text'
    else:
        source = ''
    return all_isbns, '', source


def process_file(path: Path, max_pages: int, ocr_mode: str) -> dict:
    suffix = path.suffix.lower()
    if suffix == '.pdf':
        isbns, err, source = process_pdf(path, max_pages, ocr_mode)
    elif suffix == '.epub':
        try:
            text = extract_epub_text(path)
            isbns = find_isbns(text)
            err = ''
            source = 'text' if isbns else ''
        except Exception as e:
            isbns, err, source = [], f'EPUB 读取失败: {e}', ''
    else:
        return {'isbns': [], 'error': 'unsupported', 'source': ''}
    return {'isbns': isbns, 'error': err, 'source': source}


def _worker_process(args_tuple: tuple) -> dict:
    """在子进程里处理单个文件。必须是模块级函数,才能被 pickle 传递。

    每个子进程第一次被调用时,会独立做一次 OCR 环境检查,
    因为 `_ocr_checked` 等全局变量在子进程里是初始值。
    """
    idx, total, file_str, root_str, max_pages, ocr_mode = args_tuple
    path = Path(file_str)
    root = Path(root_str)
    try:
        r = process_file(path, max_pages, ocr_mode)
    except Exception as e:
        r = {'isbns': [], 'error': f'未捕获异常: {e}', 'source': ''}
    return {
        'idx': idx,
        'total': total,
        'file': str(path.relative_to(root)),
        'isbns': r['isbns'],
        'source': r['source'],
        'error': r['error'],
    }


def _run_parallel(files: list, root: Path, max_pages: int,
                  ocr_mode: str, workers: int) -> list:
    """多进程并行处理,按完成顺序打印进度,按原始文件顺序返回结果。"""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    tasks = [
        (i + 1, len(files), str(p), str(root), max_pages, ocr_mode)
        for i, p in enumerate(files)
    ]

    # 按原始顺序预置占位,完成一个填一个
    results_by_idx: dict = {}

    # workers=1 时退化为单进程,便于调试
    if workers <= 1:
        for t in tasks:
            r = _worker_process(t)
            _print_progress(r)
            results_by_idx[r['idx']] = r
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            future_to_idx = {pool.submit(_worker_process, t): t[0] for t in tasks}
            for fut in as_completed(future_to_idx):
                try:
                    r = fut.result()
                except Exception as e:
                    # 极端情况:子进程直接崩了(OOM、段错误等)
                    idx = future_to_idx[fut]
                    r = {
                        'idx': idx, 'total': len(files),
                        'file': str(files[idx - 1].relative_to(root)),
                        'isbns': [], 'source': '',
                        'error': f'子进程崩溃: {e}',
                    }
                _print_progress(r)
                results_by_idx[r['idx']] = r

    # 按原始文件顺序输出
    return [
        {
            'file': results_by_idx[i + 1]['file'],
            'isbns': ';'.join(results_by_idx[i + 1]['isbns']),
            'source': results_by_idx[i + 1]['source'],
            'error': results_by_idx[i + 1]['error'],
        }
        for i in range(len(files))
    ]


def _print_progress(r: dict) -> None:
    """打印单个文件的处理结果。"""
    tag = f'({r["source"]})' if r['source'] else ''
    prefix = f'[{r["idx"]}/{r["total"]}] {r["file"]}'
    if r['isbns']:
        print(f'{prefix}\n    ✓ {tag} {", ".join(r["isbns"])}')
    elif r['error']:
        print(f'{prefix}\n    ⚠️  {r["error"]}')
    else:
        print(f'{prefix}\n    ✗ 未找到 ISBN')


def main():
    parser = argparse.ArgumentParser(description='从 PDF / EPUB 批量提取 ISBN(ISBN-10/13)')
    parser.add_argument('directory', help='要扫描的目录')
    parser.add_argument('-t', '--txt', default='isbns.txt',
                        help='去重后的 ISBN 列表输出路径(默认 isbns.txt)')
    parser.add_argument('-f', '--failed', default='failed.txt',
                        help='识别失败的文件清单输出路径(默认 failed.txt)')
    parser.add_argument('-o', '--output', help='输出 CSV 文件路径(可选)')
    parser.add_argument('-p', '--pages', type=int, default=10,
                        help='PDF 文本层扫描的前 N 页(默认 10)')
    parser.add_argument('-j', '--jobs', type=int, default=0,
                        help='并行进程数(默认 0 = 自动选择 CPU 核数的一半,至少 2)')
    parser.add_argument('--no-ocr', action='store_true', help='禁用 OCR')
    parser.add_argument('--force-ocr', action='store_true',
                        help='所有 PDF 强制 OCR(更准但慢)')
    args = parser.parse_args()

    if args.no_ocr and args.force_ocr:
        print('错误: --no-ocr 和 --force-ocr 不能同时使用', file=sys.stderr)
        sys.exit(1)

    ocr_mode = 'off' if args.no_ocr else ('force' if args.force_ocr else 'auto')

    root = Path(args.directory).expanduser().resolve()
    if not root.is_dir():
        print(f'错误: {root} 不是目录', file=sys.stderr)
        sys.exit(1)

    files = [
        p for p in root.glob('**/*')
        if p.is_file() and p.suffix.lower() in ('.pdf', '.epub')
    ]
    if not files:
        print('没找到 PDF 或 EPUB 文件')
        return

    if ocr_mode != 'off':
        ok, err = check_ocr_available()
        if ok:
            print(f'✓ OCR 环境就绪,语言: {_ocr_langs}')
        else:
            print(f'⚠️  OCR 不可用,将只用文本层提取: {err}')

    # 决定并行度
    import os
    if args.jobs > 0:
        workers = args.jobs
    else:
        cpu = os.cpu_count() or 4
        workers = 7
    # 文件数少时不用上那么多进程
    workers = min(workers, len(files))

    print(f'找到 {len(files)} 个文件,OCR 模式: {ocr_mode},并行进程数: {workers}\n')

    results = _run_parallel(files, root, args.pages, ocr_mode, workers)

    hit = sum(1 for r in results if r['isbns'])
    ocr_hit = sum(1 for r in results if r['source'] in ('ocr', 'both'))
    print(f'\n完成: {hit}/{len(results)} 个文件提取到 ISBN(其中 {ocr_hit} 个借助了 OCR)')

    unique_isbns = []
    seen = set()
    for r in results:
        if not r['isbns']:
            continue
        for isbn in r['isbns'].split(';'):
            if isbn and isbn not in seen:
                unique_isbns.append(isbn)
                seen.add(isbn)

    txt_out = Path(args.txt)
    txt_out.write_text('\n'.join(unique_isbns) + '\n', encoding='utf-8')
    print(f'去重后共 {len(unique_isbns)} 个 ISBN,已写入: {txt_out}')

    failed_records = [r for r in results if not r['isbns']]
    failed_out = Path(args.failed)
    if failed_records:
        lines = []
        for r in failed_records:
            reason = r['error'] or '未找到 ISBN'
            lines.append(f'{r["file"]}\t{reason}')
        failed_out.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        print(f'失败清单 {len(failed_records)} 个文件,已写入: {failed_out}')
    else:
        if failed_out.exists():
            failed_out.unlink()
        print('没有失败文件 🎉')

    if args.output:
        out = Path(args.output)
        with out.open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['file', 'isbns', 'source', 'error'])
            writer.writeheader()
            writer.writerows(results)
        print(f'文件映射已写入: {out}')


if __name__ == '__main__':
    main()