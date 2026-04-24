#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
读取 txt 文件里的 ISBN 列表：
  - 如果是 ISBN-13，转成 ISBN-10,添加到该行下一行
  - 如果是 ISBN-10,转成 ISBN-13,添加到该行下一行
  - 无法转换则保持原样(例如 979 开头的 ISBN-13 没有对应的 ISBN-10)

用法:
    python isbn_convert.py input.txt              # 覆盖写回 input.txt
    python isbn_convert.py input.txt output.txt   # 写到 output.txt
"""

import sys
from pathlib import Path

import isbnlib


def convert_line(isbn_raw: str):
    """
    输入原始字符串(可能带空格/横线),返回要追加的一行 ISBN,
    若无法转换返回 None。
    """
    isbn = isbnlib.canonical(isbn_raw)  # 去掉横线/空格,规范化
    if not isbn:
        return None

    if len(isbn) == 13 and isbnlib.is_isbn13(isbn):
        converted = isbnlib.to_isbn10(isbn)  # 979 开头的会返回空字符串
        return converted if converted else None

    if len(isbn) == 10 and isbnlib.is_isbn10(isbn):
        converted = isbnlib.to_isbn13(isbn)
        return converted if converted else None

    return None


def process(in_path: Path, out_path: Path):
    lines = in_path.read_text(encoding="utf-8").splitlines()

    result = []
    converted_count = 0
    skipped_count = 0

    for line in lines:
        result.append(line)
        stripped = line.strip()
        if not stripped:
            continue

        converted = convert_line(stripped)
        if converted:
            result.append(converted)
            converted_count += 1
        else:
            # 非空但无法识别/无法转换的行
            if isbnlib.canonical(stripped):
                skipped_count += 1

    out_path.write_text("\n".join(result) + "\n", encoding="utf-8")

    print(f"完成: 已写入 {out_path}")
    print(f"  转换成功: {converted_count}")
    print(f"  无法转换: {skipped_count}")


def main():
    if len(sys.argv) < 2:
        print("用法: python isbn_convert.py <输入文件> [输出文件]")
        sys.exit(1)

    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) >= 3 else in_path

    if not in_path.is_file():
        print(f"找不到文件: {in_path}")
        sys.exit(1)

    process(in_path, out_path)


if __name__ == "__main__":
    main()