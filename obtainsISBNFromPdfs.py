#!/usr/bin/env python3
"""
扫描文件夹下所有 PDF 和 EPUB 文件,提取 ISBN。

用法:
    python obtainsISBNFromPdfs.py <目录路径> [--output result.csv] [--pages 10]

依赖:
    pip install pypdf ebooklib beautifulsoup4
"""

import argparse
import csv
import re
import sys
import zipfile
from pathlib import Path


# ISBN 正则:匹配 ISBN-10 或 ISBN-13,允许中间有连字符/空格
ISBN_PATTERN = re.compile(
    r'ISBN(?:[-\s]*1[03])?[:\s]*'           # 可选的 ISBN / ISBN-10 / ISBN-13 前缀
    r'((?:97[89][-\s]?)?'                   # 可选的 978/979 前缀
    r'(?:\d[-\s]?){9}[\dXx])',              # 9 位数字 + 校验位 (数字或 X)
    re.IGNORECASE
)

# 兜底:裸 ISBN(无前缀),要求 13 位(更严格,减少误报)
ISBN13_BARE = re.compile(r'(?<!\d)(97[89](?:[-\s]?\d){10})(?!\d)')


def clean_isbn(raw: str) -> str:
    """去掉连字符和空格,统一大小写。"""
    return re.sub(r'[-\s]', '', raw).upper()


def validate_isbn(isbn: str) -> bool:
    """校验 ISBN-10 或 ISBN-13 的校验位。"""
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
    """从文本中提取所有合法 ISBN,按出现顺序去重。"""
    found = []
    seen = set()

    for match in ISBN_PATTERN.finditer(text):
        isbn = clean_isbn(match.group(1))
        if validate_isbn(isbn) and isbn not in seen:
            found.append(isbn)
            seen.add(isbn)

    for match in ISBN13_BARE.finditer(text):
        isbn = clean_isbn(match.group(1))
        if validate_isbn(isbn) and isbn not in seen:
            found.append(isbn)
            seen.add(isbn)

    return found


def extract_pdf_text(path: Path, max_pages: int) -> str:
    """提取 PDF 前 max_pages 页和后 3 页的文本(版权页可能在开头或结尾)。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise RuntimeError("缺少 pypdf,请运行: pip install pypdf")

    try:
        reader = PdfReader(str(path))
        total = len(reader.pages)
        # 取前 N 页 + 后 3 页(有些书 ISBN 在封底)
        indices = list(range(min(max_pages, total)))
        indices += [i for i in range(max(0, total - 3), total) if i not in indices]

        parts = []
        for i in indices:
            try:
                parts.append(reader.pages[i].extract_text() or '')
            except Exception:
                continue
        return '\n'.join(parts)
    except Exception as e:
        return f'__ERROR__: {e}'


def extract_pdf_metadata(path: Path) -> str:
    """把 PDF 元数据拼成字符串,方便正则扫描。"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        meta = reader.metadata or {}
        return ' '.join(str(v) for v in meta.values())
    except Exception:
        return ''


def extract_epub_text(path: Path) -> str:
    """EPUB 本质是 zip,直接读里面的 opf 和前几个 xhtml 文件。"""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            # 优先读 opf(元数据)和前几个内容文件
            targets = [n for n in names if n.lower().endswith('.opf')]
            content_files = sorted(
                n for n in names
                if n.lower().endswith(('.xhtml', '.html', '.htm'))
            )
            targets += content_files[:5]  # 前 5 个内容文件

            parts = []
            for name in targets:
                try:
                    with z.open(name) as f:
                        data = f.read().decode('utf-8', errors='ignore')
                        parts.append(data)
                except Exception:
                    continue
            return '\n'.join(parts)
    except Exception as e:
        return f'__ERROR__: {e}'


def process_file(path: Path, max_pages: int) -> tuple[list[str], str]:
    """返回 (isbn 列表, 错误信息)。"""
    suffix = path.suffix.lower()
    text = ''

    if suffix == '.pdf':
        text = extract_pdf_metadata(path) + '\n' + extract_pdf_text(path, max_pages)
    elif suffix == '.epub':
        text = extract_epub_text(path)
    else:
        return [], 'unsupported'

    if text.startswith('__ERROR__'):
        return [], text.replace('__ERROR__: ', '')

    return find_isbns(text), ''


def main():
    parser = argparse.ArgumentParser(description='从 PDF / EPUB 批量提取 ISBN')
    parser.add_argument('directory', help='要扫描的目录')
    parser.add_argument('-o', '--output', help='输出 CSV 文件路径(可选)')
    parser.add_argument('-p', '--pages', type=int, default=10,
                        help='PDF 每本扫描的前 N 页(默认 10)')
    parser.add_argument('-r', '--recursive', action='store_true', default=True,
                        help='递归扫描子目录(默认开启)')
    args = parser.parse_args()

    root = Path(args.directory).expanduser().resolve()
    if not root.is_dir():
        print(f'错误: {root} 不是目录', file=sys.stderr)
        sys.exit(1)

    pattern = '**/*' if args.recursive else '*'
    files = [
        p for p in root.glob(pattern)
        if p.is_file() and p.suffix.lower() in ('.pdf', '.epub')
    ]

    if not files:
        print('没找到 PDF 或 EPUB 文件')
        return

    print(f'找到 {len(files)} 个文件,开始处理...\n')

    results = []
    for i, path in enumerate(files, 1):
        rel = path.relative_to(root)
        print(f'[{i}/{len(files)}] {rel}')
        isbns, err = process_file(path, args.pages)
        if err:
            print(f'    ⚠️  {err}')
        if isbns:
            print(f'    ✓ {", ".join(isbns)}')
        else:
            print('    ✗ 未找到 ISBN')
        results.append({
            'file': str(rel),
            'isbns': ';'.join(isbns),
            'error': err,
        })

    # 汇总
    hit = sum(1 for r in results if r['isbns'])
    print(f'\n完成: {hit}/{len(results)} 个文件提取到 ISBN')

    if args.output:
        out = Path(args.output)
        with out.open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['file', 'isbns', 'error'])
            writer.writeheader()
            writer.writerows(results)
        print(f'结果已写入: {out}')


if __name__ == '__main__':
    main()