#!/usr/bin/env python3
"""
扫描文件夹下所有 PDF 和 EPUB 文件,提取 ISBN。
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


# ISBN-13:13 位数字,中间允许连字符/空格,可选 ISBN 前缀,必须 978/979 开头
ISBN13_WITH_PREFIX = re.compile(
    r'ISBN(?:[-\s]*13)?[:\s]*-?\s*'
    r'(97[89][\d\-\s]{9,16}\d)',       # 978/979 + 10~17 字符(含分隔符) + 末尾数字
    re.IGNORECASE
)
ISBN13_BARE = re.compile(r'(?<![\d])(97[89][\d\-\s]{9,16}\d)(?![\d])')

# ISBN-10:必须有 ISBN 前缀,避免跟其他 10 位数字冲突
ISBN10_WITH_PREFIX = re.compile(
    r'ISBN(?:[-\s]*10)?[:\s]*-?\s*'
    r'(\d[\d\-\s]{7,14}[\dXx])',
    re.IGNORECASE
)

TEXT_THRESHOLD = 100      # 低于这个字符数,判定为扫描版
OCR_FRONT_PAGES = 8       # OCR 扫前 8 页
OCR_BACK_PAGES = 3        # OCR 扫后 3 页(封底)


def clean_isbn(raw: str) -> str:
    return re.sub(r'[-\s]', '', raw).upper()


def validate_isbn(isbn: str) -> bool:
    isbn = clean_isbn(isbn)
    if len(isbn) == 10:
        if not re.fullmatch(r'\d{9}[\dX]', isbn):
            return False
        total = sum((10 - i) * (10 if c == 'X' else int(c)) for i, c in enumerate(isbn))
        return total % 11 == 0
    if len(isbn) == 13:
        if not isbn.isdigit():
            return False
        total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(isbn))
        return total % 10 == 0
    return False


def find_isbns(text: str) -> list[str]:
    """按 ISBN-13 优先的策略提取,避免把 13 位的前 10 位误当成 ISBN-10。"""
    found = []
    seen = set()

    def add(candidate: str) -> bool:
        if validate_isbn(candidate) and candidate not in seen:
            found.append(candidate)
            seen.add(candidate)
            return True
        return False

    # 1. 先抓 ISBN-13(带前缀或裸号)
    isbn13_matches = []
    for m in ISBN13_WITH_PREFIX.finditer(text):
        isbn13_matches.append((m.start(), m.end(), m.group(1)))
    for m in ISBN13_BARE.finditer(text):
        isbn13_matches.append((m.start(), m.end(), m.group(1)))

    # 阻塞区间:所有 ISBN-13 **候选位置**(无论校验是否通过),
    # 避免该区域内的数字串被 ISBN-10 规则误抓
    blocked_spans = [(s, e) for s, e, _ in isbn13_matches]

    for _, _, raw in isbn13_matches:
        digits = clean_isbn(raw)
        if len(digits) >= 13:
            add(digits[:13])

    # 2. 再抓 ISBN-10(仅限带 ISBN 前缀的,避免和 13 位串冲突)
    for m in ISBN10_WITH_PREFIX.finditer(text):
        # 跳过与 ISBN-13 区间重叠的
        if any(s <= m.start() < e or s < m.end() <= e for s, e in blocked_spans):
            continue
        digits = clean_isbn(m.group(1))
        if len(digits) >= 10:
            add(digits[:10])

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
    """检查 OCR 依赖,只跑一次,结果缓存。"""
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
    """只 OCR 前 OCR_FRONT_PAGES + 后 OCR_BACK_PAGES 页。

    通过 subprocess 直接调用 tesseract,用 stdin 传入 PNG 数据,
    避开 pytesseract 默认的 PPM 临时文件方式(在某些 Nix tesseract 构建下失败)。
    """
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
        """把 PIL 图转成 PNG bytes,通过 stdin 送入 tesseract。"""
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        png_bytes = buf.getvalue()

        # tesseract 约定:输入用 `-` 表示 stdin,输出用 `-` 表示 stdout
        proc = subprocess.run(
            ['tesseract', 'stdin', 'stdout', '-l', lang, '--psm', '6'],
            input=png_bytes,
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

def extract_epub_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        targets = [n for n in names if n.lower().endswith('.opf')]
        content_files = sorted(
            n for n in names
            if n.lower().endswith(('.xhtml', '.html', '.htm'))
        )
        targets += content_files[:5]
        parts = []
        for name in targets:
            try:
                with z.open(name) as f:
                    parts.append(f.read().decode('utf-8', errors='ignore'))
            except Exception:
                continue
        return '\n'.join(parts)


# -------------------- 主处理 --------------------

def process_pdf(path: Path, max_pages: int, ocr_mode: str) -> tuple[list[str], str, str]:
    """ocr_mode: 'auto' | 'off' | 'force'。返回 (isbns, error, source)。"""
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


def main():
    parser = argparse.ArgumentParser(description='从 PDF / EPUB 批量提取 ISBN(支持 OCR)')
    parser.add_argument('directory', help='要扫描的目录')
    parser.add_argument('-t', '--txt', default='isbns.txt',
                        help='去重后的 ISBN 列表输出路径(默认 isbns.txt)')
    parser.add_argument('-f', '--failed', default='failed.txt',
                        help='识别失败的文件清单输出路径(默认 failed.txt)')
    parser.add_argument('-o', '--output', help='输出 CSV 文件路径(可选)')
    parser.add_argument('-p', '--pages', type=int, default=10,
                        help='PDF 文本层扫描的前 N 页(默认 10)')
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

    print(f'找到 {len(files)} 个文件,OCR 模式: {ocr_mode}\n')

    results = []
    for i, path in enumerate(files, 1):
        rel = path.relative_to(root)
        print(f'[{i}/{len(files)}] {rel}')
        r = process_file(path, args.pages, ocr_mode)
        tag = f'({r["source"]})' if r['source'] else ''
        if r['error']:
            print(f'    ⚠️  {r["error"]}')
        if r['isbns']:
            print(f'    ✓ {tag} {", ".join(r["isbns"])}')
        else:
            print('    ✗ 未找到 ISBN')
        results.append({
            'file': str(rel),
            'isbns': ';'.join(r['isbns']),
            'source': r['source'],
            'error': r['error'],
        })

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

    # 失败清单:没提取到 ISBN 的文件(含出错的)
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
        # 没有失败就清掉可能存在的旧文件,避免误导
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