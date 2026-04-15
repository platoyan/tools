#!/usr/bin/env python3
"""
从入口 URL 提取 Menu 下的所有链接，连同入口 URL 本身组成列表 A，
再从列表 A 的每个页面提取 ISBN，汇总输出到 txt 文件。

用法:
  python3 extract_isbn_mit.py <url> [output.txt]
  python3 extract_isbn_mit.py -f urls.txt output.txt   # 多个入口 URL
"""

import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urljoin


# ── 网络 ──────────────────────────────────────────────────────────────────────

def fetch_html(url: str):  # -> str | None
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        )
    }
    try:
        req = urllib.request.Request(url, headers=headers)
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


# ── Menu 链接提取 ─────────────────────────────────────────────────────────────

class CourseNavParser(HTMLParser):
    """
    提取 <nav class="course-nav"> 内所有导航链接。
    MIT OCW 页面中，课程导航（Syllabus / Lecture Notes / Assignments 等）
    位于 class="course-nav" 的 <nav> 标签内，与页面上的 "Menu" 按钮文字
    没有父子关系，因此直接定位该 nav 元素更可靠。
    同一页面可能存在多个 course-nav（移动端 + 桌面端），会自动去重。
    """

    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.links: list[tuple[str, str]] = []

        self._in_course_nav = False
        self._nav_depth = 0
        self._current_depth = 0
        self._in_a = False
        self._a_href = ''
        self._a_text_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        self._current_depth += 1
        attrs_dict = dict(attrs)

        # 进入 course-nav
        if tag == 'nav' and 'course-nav' in attrs_dict.get('class', ''):
            self._in_course_nav = True
            self._nav_depth = self._current_depth
            return

        if self._in_course_nav and tag == 'a':
            self._in_a = True
            self._a_href = attrs_dict.get('href', '')
            self._a_text_parts = []

    def handle_endtag(self, tag):
        # 离开 course-nav（匹配进入时的深度）
        if self._in_course_nav and tag == 'nav' and self._current_depth == self._nav_depth:
            self._in_course_nav = False

        if self._in_course_nav and tag == 'a' and self._in_a:
            text = ''.join(self._a_text_parts).strip()
            href = self._a_href.strip()
            if text and href and not href.startswith(('#', 'mailto:', 'javascript:')):
                self.links.append((text, urljoin(self.base_url, href)))
            self._in_a = False
            self._a_href = ''
            self._a_text_parts = []

        self._current_depth -= 1

    def handle_data(self, data):
        if self._in_course_nav and self._in_a:
            self._a_text_parts.append(data)


class MenuTextParser(HTMLParser):
    """
    后备解析器：在找不到 course-nav 时，
    回退到原来的策略——找到 'Menu' 文字后收集后续链接。
    """
    BLOCK_TAGS = {'div', 'section', 'footer', 'header', 'main', 'aside', 'article', 'nav'}

    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.links: list[tuple[str, str]] = []
        self._menu_found = False
        self._menu_depth = 0
        self._current_depth = 0
        self._in_a = False
        self._a_href = ''
        self._a_text_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self._menu_found and tag in self.BLOCK_TAGS and self._current_depth <= self._menu_depth:
            self._menu_found = False
        self._current_depth += 1
        if not self._menu_found:
            return
        if tag == 'a':
            self._in_a = True
            self._a_href = dict(attrs).get('href', '')
            self._a_text_parts = []

    def handle_endtag(self, tag):
        if self._menu_found and tag == 'a' and self._in_a:
            text = ''.join(self._a_text_parts).strip()
            href = self._a_href.strip()
            if text and href and not href.startswith(('#', 'mailto:', 'javascript:')):
                self.links.append((text, urljoin(self.base_url, href)))
            self._in_a = False
            self._a_href = ''
            self._a_text_parts = []
        self._current_depth -= 1

    def handle_data(self, data):
        if not self._menu_found and data.strip() == 'Menu':
            self._menu_found = True
            self._menu_depth = self._current_depth
            return
        if self._menu_found and self._in_a:
            self._a_text_parts.append(data)


def extract_menu_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """
    优先从 <nav class="course-nav"> 提取导航链接；
    找不到时回退到 'Menu' 文字定位策略。
    结果去重（同一 URL 只保留第一次出现）。
    """
    # 优先策略：course-nav
    parser = CourseNavParser(base_url)
    parser.feed(html)

    # 回退策略：Menu 文字
    if not parser.links:
        fallback = MenuTextParser(base_url)
        fallback.feed(html)
        parser.links = fallback.links

    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for text, url in parser.links:
        if url not in seen:
            seen.add(url)
            result.append((text, url))
    return result


# ── ISBN 提取 ─────────────────────────────────────────────────────────────────

def clean_html(html: str) -> str:
    """去除标签，文本节点以换行拼接（避免跨节点数字误合并）。"""
    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.chunks: list[str] = []
        def handle_data(self, data: str):
            self.chunks.append(data)
    p = TextExtractor()
    p.feed(html)
    return "\n".join(p.chunks)


def valid_isbn10(s: str) -> bool:
    """校验 ISBN-10，末位可为 X。"""
    if len(s) != 10:
        return False
    if not all(c.isdigit() for c in s[:9]):   # 先验证前9位
        return False
    total = sum(int(c) * (10 - i) for i, c in enumerate(s[:9]))
    check = s[9]
    total += 10 if check in ('X', 'x') else int(check) if check.isdigit() else -999
    return total % 11 == 0


def valid_isbn13(s: str) -> bool:
    """校验 ISBN-13。"""
    if len(s) != 13 or not s.isdigit():
        return False
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(s[:12]))
    return (10 - total % 10) % 10 == int(s[12])


def normalize(isbn: str) -> str:
    """去掉 'ISBN' 前缀、连字符、空格，统一为纯数字串（X 大写）。"""
    s = re.sub(r'(?i)^ISBN[: ：\-]*', '', isbn.strip())
    return re.sub(r'[ \-]', '', s).upper()


def extract_isbns(text: str) -> list[str]:
    """从文本中提取所有校验通过的 ISBN，返回规范化纯数字串列表。"""
    seen: set[str] = set()
    results: list[str] = []

    def add(raw: str):
        key = normalize(raw)
        if key in seen:
            return
        if (len(key) == 13 and valid_isbn13(key)) or \
           (len(key) == 10 and valid_isbn10(key)):
            seen.add(key)
            results.append(key)

    # 带 ISBN 前缀（不跨行）
    for m in re.compile(r'ISBN[: ：\-]*(\d[\d \-]{8,16}[\dXx])', re.IGNORECASE).finditer(text):
        add(m.group(1))

    # 不带前缀的裸数字串（10 或 13 位，前后不能紧邻数字）
    for m in re.compile(r'(?<![0-9A-Za-z])([0-9]{12}[0-9]|[0-9]{9}[0-9Xx])(?![0-9A-Za-z])').finditer(text):
        add(m.group(1))

    return results


def get_isbns_from_url(url: str, html=None) -> list[str]:
    """
    抓取 url（或复用已有 html），提取并去重 ISBN。
    返回规范化纯数字串列表。
    """
    if html is None:
        html = fetch_html(url)
    if not html:
        return []

    seen: set[str] = set()
    merged: list[str] = []
    for isbn in extract_isbns(html) + extract_isbns(clean_html(html)):
        if isbn not in seen:
            seen.add(isbn)
            merged.append(isbn)
    return merged


# ── 主流程 ────────────────────────────────────────────────────────────────────

def build_list_a(entry_url: str):
    """
    抓取入口页面，提取 Menu 链接，返回：
      (list_a, html)
    list_a = [(label, url), ...]，入口 URL 排第一。
    html 供后续复用，避免重复请求。
    """
    print(f"\n>>> 入口页面：{entry_url}")
    html = fetch_html(entry_url)
    if not html:
        return [], None

    menu_links = extract_menu_links(html, entry_url)
    if menu_links:
        print(f"  Menu 下找到 {len(menu_links)} 个链接：")
        for text, url in menu_links:
            print(f"    · {text}  →  {url}")
    else:
        print("  未找到 Menu 链接，仅处理入口页面本身")

    list_a: list[tuple[str, str]] = [("(入口页面)", entry_url)]
    for label, url in menu_links:
        if url != entry_url:
            list_a.append((label, url))
    return list_a, html


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    entry_urls: list[str] = []
    output_path = Path("isbns.txt")

    if args[0] == '-f':
        if len(args) < 2:
            print("错误：-f 后需要指定 URL 列表文件")
            sys.exit(1)
        url_file = Path(args[1])
        if not url_file.exists():
            print(f"错误：找不到文件 {url_file}")
            sys.exit(1)
        lines = url_file.read_text(encoding='utf-8').splitlines()
        entry_urls = [l.strip() for l in lines
                      if l.strip() and not l.startswith('#')
                      and l.strip().startswith(('http://', 'https://'))]
        if len(args) >= 3:
            output_path = Path(args[2])
    else:
        for a in args:
            if a.startswith(('http://', 'https://')):
                entry_urls.append(a)
            else:
                output_path = Path(a)

    if not entry_urls:
        print("错误：没有找到有效的 URL（需以 http:// 或 https:// 开头）")
        sys.exit(1)

    # 第一步：构建列表 A，缓存入口页 HTML 避免重复请求
    list_a: list[tuple[str, str]] = []
    html_cache: dict = {}
    seen_urls: set[str] = set()

    for entry in entry_urls:
        items, html = build_list_a(entry)
        html_cache[entry] = html
        for label, url in items:
            if url not in seen_urls:
                seen_urls.add(url)
                list_a.append((label, url))

    if not list_a:
        print("错误：所有入口页面均请求失败")
        sys.exit(1)

    print(f"\n列表 A 共 {len(list_a)} 个页面，开始提取 ISBN...\n")

    # 第二步：逐页提取 ISBN（入口页面复用缓存 HTML）
    global_seen: set[str] = set()
    output_lines: list[str] = []
    total = 0

    for i, (label, url) in enumerate(list_a, 1):
        print(f"[{i}/{len(list_a)}] {label}  {url}")
        cached_html = html_cache.get(url)  # 入口页有缓存则不再请求
        isbns = get_isbns_from_url(url, html=cached_html)

        new_isbns = [isbn for isbn in isbns if isbn not in global_seen]
        global_seen.update(new_isbns)

        if new_isbns:
            output_lines.append(f"# [{label}] {url}")
            output_lines.extend(new_isbns)
            output_lines.append("")
            total += len(new_isbns)
            print(f"  ✓ 找到 {len(new_isbns)} 个新 ISBN")
        else:
            print(f"  - 未找到新 ISBN")

        if i < len(list_a):
            time.sleep(0.5)

    output_path.write_text('\n'.join(output_lines), encoding='utf-8')
    print(f"\n完成！共提取 {total} 个不重复 ISBN，已写入：{output_path}")


if __name__ == '__main__':
    main()