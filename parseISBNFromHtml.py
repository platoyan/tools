#!/usr/bin/env python3
"""
从 HTML 文件中提取所有 ISBN，输出到 txt 文件。
用法: python extract_isbn.py <input.html> [output.txt]
"""

import re
import sys
from pathlib import Path
from html.parser import HTMLParser


def clean_html(html: str) -> str:
    """去除 HTML 标签，保留纯文本。"""
    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.chunks = []

        def handle_data(self, data):
            self.chunks.append(data)

    parser = TextExtractor()
    parser.feed(html)
    return " ".join(parser.chunks)


def extract_isbns(text: str) -> list[str]:
    """
    从文本中提取所有 ISBN-10 / ISBN-13。
    支持格式：
      - 纯数字：9787302539681
      - 带连字符：978-7-302-53968-1
      - 带空格：978 7 302 53968 1
      - 前缀 ISBN: / ISBN：
    """
    # 匹配含分隔符的原始字符串
    raw_pattern = re.compile(
        r'(?:ISBN[:\s：-]*)(\d[\d\s\-]{8,17}\d)',
        re.IGNORECASE
    )

    candidates = []
    for m in raw_pattern.finditer(text):
        raw = m.group(1)
        digits = re.sub(r'[\s\-]', '', raw)
        candidates.append((digits, m.group(0).strip()))

    # 也捕获不带前缀的纯数字串（13位或10位）
    plain_pattern = re.compile(r'\b(\d{13}|\d{10})\b')
    plain_digits = set()
    for m in plain_pattern.finditer(text):
        plain_digits.add(m.group(1))

    # 验证函数
    def valid_isbn10(s: str) -> bool:
        if len(s) != 10:
            return False
        total = 0
        for i, c in enumerate(s[:-1]):
            if not c.isdigit():
                return False
            total += int(c) * (10 - i)
        check = s[-1]
        total += 10 if check in ('X', 'x') else int(check) if check.isdigit() else -999
        return total % 11 == 0

    def valid_isbn13(s: str) -> bool:
        if len(s) != 13 or not s.isdigit():
            return False
        total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(s[:-1]))
        check = (10 - total % 10) % 10
        return check == int(s[-1])

    seen = set()
    results = []

    def add(digits, display):
        if digits not in seen:
            if (len(digits) == 13 and valid_isbn13(digits)) or \
               (len(digits) == 10 and valid_isbn10(digits)):
                seen.add(digits)
                results.append(display)

    for digits, display in candidates:
        add(digits, display)

    for digits in plain_digits:
        add(digits, digits)

    return results


def main():
    if len(sys.argv) < 2:
        print("用法: python extract_isbn.py <input.html> [output.txt]")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2]) if len(sys.argv) >= 3 else input_path.with_suffix('.isbn.txt')

    if not input_path.exists():
        print(f"错误：找不到文件 {input_path}")
        sys.exit(1)

    html = input_path.read_text(encoding='utf-8', errors='replace')

    # 先从原始 HTML 提取（含属性值里的 ISBN）
    isbns_raw = extract_isbns(html)

    # 再从纯文本提取（避免跨标签断开）
    text = clean_html(html)
    isbns_text = extract_isbns(text)

    # 合并去重，保持顺序
    seen = set()
    all_isbns = []
    for isbn in isbns_raw + isbns_text:
        digits = re.sub(r'[\s\-]', '', isbn.split()[-1])  # 取最后一段纯数字
        key = re.sub(r'[\s\-]', '', isbn)
        if key not in seen:
            seen.add(key)
            all_isbns.append(isbn)

    output_path.write_text('\n'.join(all_isbns) + ('\n' if all_isbns else ''), encoding='utf-8')

    print(f"共找到 {len(all_isbns)} 个 ISBN，已写入 {output_path}")
    for isbn in all_isbns:
        print(f"  {isbn}")


if __name__ == '__main__':
    main()