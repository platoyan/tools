#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合并多个 txt 文件里的 ISBN 列表,去掉重复项。

去重规则:
  - 用 isbnlib.canonical() 规范化后比较(忽略横线/空格)
  - 保持首次出现的顺序
  - 空行和无法识别为 ISBN 的行会被丢弃

用法:
    python isbn_merge.py a.txt b.txt c.txt -o merged.txt
    python isbn_merge.py *.txt -o merged.txt
    python isbn_merge.py *.txt                # 默认输出到 merged.txt
"""

import argparse
import glob
import sys
from pathlib import Path

import isbnlib


def collect_isbns(paths):
    """按顺序读取所有文件,返回去重后的 ISBN 列表(保持首次出现顺序)"""
    seen = set()
    result = []
    total = 0
    invalid = 0
    duplicate = 0

    for path in paths:
        if not path.is_file():
            print(f"[警告] 跳过不存在的文件: {path}", file=sys.stderr)
            continue

        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            total += 1
            isbn = isbnlib.canonical(stripped)

            # 只保留能被识别为合法 ISBN-10 或 ISBN-13 的
            if not isbn or not (isbnlib.is_isbn10(isbn) or isbnlib.is_isbn13(isbn)):
                invalid += 1
                continue

            if isbn in seen:
                duplicate += 1
                continue

            seen.add(isbn)
            result.append(isbn)

    return result, total, invalid, duplicate


def expand_paths(patterns):
    """展开通配符,返回 Path 列表(保持顺序、去掉重复路径)"""
    seen = set()
    paths = []
    for pattern in patterns:
        matches = glob.glob(pattern) or [pattern]  # 无匹配时保留原样,便于给出警告
        for m in matches:
            p = Path(m).resolve()
            if p not in seen:
                seen.add(p)
                paths.append(Path(m))
    return paths


def main():
    parser = argparse.ArgumentParser(description="合并多个 ISBN txt 文件并去重")
    parser.add_argument("inputs", nargs="+", help="输入的 txt 文件(支持通配符)")
    parser.add_argument("-o", "--output", default="merged.txt", help="输出文件(默认 merged.txt)")
    args = parser.parse_args()

    paths = expand_paths(args.inputs)
    if not paths:
        print("没有找到任何输入文件", file=sys.stderr)
        sys.exit(1)

    isbns, total, invalid, duplicate = collect_isbns(paths)

    out_path = Path(args.output)
    out_path.write_text("\n".join(isbns) + "\n", encoding="utf-8")

    print(f"输入文件数: {len(paths)}")
    print(f"总行数(非空): {total}")
    print(f"  无效/非 ISBN: {invalid}")
    print(f"  重复丢弃:     {duplicate}")
    print(f"  写入唯一 ISBN: {len(isbns)}")
    print(f"输出: {out_path}")


if __name__ == "__main__":
    main()