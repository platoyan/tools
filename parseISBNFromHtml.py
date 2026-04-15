#!/usr/bin/env python3
"""
从多个 URL 获取网页内容，提取所有 ISBN，汇总输出到 txt 文件。
用法:
  python extract_isbn.py <url1> <url2> ...        # 直接传 URL
  python extract_isbn.py -f urls.txt [output.txt] # 从文件读取 URL 列表
"""

import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from html.parser import HTMLParser


def fetch_html(url: str) -> str | None:
    """从 URL 获取网页 HTML 内容，失败返回 None。"""
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        )
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            charset = resp.headers.get_content_charset() or 'utf-8'
            return resp.read().decode(charset, errors='replace')
    except urllib.error.HTTPError as e:
        print(f"  ✗ HTTP 错误：{e.code} {e.reason}")
    except urllib.error.URLError as e:
        print(f"  ✗ 请求失败：{e.reason}")
    except Exception as e:
        print(f"  ✗ 未知错误：{e}")
    return None


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
    return (10 - total % 10) % 10 == int(s[-1])


def extract_isbns(text: str) -> list[str]:
    seen = set()
    results = []

    def add(digits: str, display: str):
        if digits not in seen:
            if (len(digits) == 13 and valid_isbn13(digits)) or \
               (len(digits) == 10 and valid_isbn10(digits)):
                seen.add(digits)
                results.append(display)

    prefixed = re.compile(r'ISBN[:\s：\-]*(\d[\d\s\-]{8,17}\d)', re.IGNORECASE)
    for m in prefixed.finditer(text):
        add(re.sub(r'[\s\-]', '', m.group(1)), m.group(0).strip())

    for m in re.compile(r'\b(\d{13}|\d{10})\b').finditer(text):
        add(m.group(1), m.group(1))

    return results


def process_url(url: str) -> list[str]:
    """抓取单个 URL，返回去重后的 ISBN 列表。"""
    html = fetch_html(url)
    if html is None:
        return []
    raw = extract_isbns(html)
    txt = extract_isbns(clean_html(html))

    seen = set()
    merged = []
    for isbn in raw + txt:
        key = re.sub(r'[\s\-]', '', isbn).upper()
        if key not in seen:
            seen.add(key)
            merged.append(isbn)
    return merged


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    # 解析参数
    urls = []
    output_path = Path("isbns.txt")

    if args[0] == '-f':
        # 从文件读取 URL 列表
        if len(args) < 2:
            print("错误：-f 后需要指定 URL 列表文件")
            sys.exit(1)
        url_file = Path(args[1])
        if not url_file.exists():
            print(f"错误：找不到文件 {url_file}")
            sys.exit(1)
        urls = [line.strip() for line in url_file.read_text(encoding='utf-8').splitlines()
                if line.strip() and not line.startswith('#')]
        if len(args) >= 3:
            output_path = Path(args[2])
    else:
        # 直接从命令行参数读取 URL
        for a in args:
            if a.startswith(('http://', 'https://')):
                urls.append(a)
            else:
                output_path = Path(a)  # 最后一个非 URL 参数作为输出文件

    if not urls:
        print("错误：没有找到有效的 URL")
        sys.exit(1)

    print(f"共 {len(urls)} 个 URL，输出到：{output_path}\n")

    # 全局去重集合
    global_seen = set()
    lines = []  # 输出行（含来源注释）
    total = 0

    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] {url}")
        isbns = process_url(url)

        new_isbns = []
        for isbn in isbns:
            key = re.sub(r'[\s\-]', '', isbn).upper()
            if key not in global_seen:
                global_seen.add(key)
                new_isbns.append(isbn)

        if new_isbns:
            lines.append(f"# {url}")
            lines.extend(new_isbns)
            lines.append("")
            total += len(new_isbns)
            print(f"  ✓ 找到 {len(new_isbns)} 个新 ISBN")
        else:
            print(f"  - 未找到新 ISBN")

        if i < len(urls):
            time.sleep(0.5)  # 礼貌性延迟

    output_path.write_text('\n'.join(lines), encoding='utf-8')
    print(f"\n完成！共提取 {total} 个不重复 ISBN，已写入：{output_path}")


if __name__ == '__main__':
    main()