#!/usr/bin/env python3
"""
从 output.txt 中删除已出现在其他文件里的 ISBN。

用法:
  python3 dedup_isbn.py output.txt A-output.txt B-output.txt ...
  python3 dedup_isbn.py output.txt A-output.txt B-output.txt --inplace   # 直接覆盖原文件
"""

import re
import sys
from pathlib import Path


def normalize(isbn: str) -> str:
    """去掉前缀、连字符、空格，统一为纯数字串（X 大写）。"""
    s = re.sub(r'(?i)^ISBN[: ：\-]*', '', isbn.strip())
    return re.sub(r'[ \-]', '', s).upper()


def extract_isbns_from_file(path: Path) -> set[str]:
    """从文件中提取所有 ISBN，返回规范化后的集合。"""
    isbns = set()
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key = normalize(line)
        if key:
            isbns.add(key)
    return isbns


def main():
    args = sys.argv[1:]
    inplace = '--inplace' in args
    args = [a for a in args if a != '--inplace']

    if len(args) < 2:
        print("用法: python dedup_isbn.py output.txt A-output.txt [B-output.txt ...] [--inplace]")
        sys.exit(1)

    target_path = Path(args[0])
    ref_paths = [Path(a) for a in args[1:]]

    # 检查文件存在
    for p in [target_path] + ref_paths:
        if not p.exists():
            print(f"错误：找不到文件 {p}")
            sys.exit(1)

    # 从参考文件收集要排除的 ISBN
    excluded: set[str] = set()
    for p in ref_paths:
        isbns = extract_isbns_from_file(p)
        print(f"  {p.name}: 读取到 {len(isbns)} 个 ISBN")
        excluded |= isbns
    print(f"  共 {len(excluded)} 个不重复 ISBN 需要排除\n")

    # 处理 output.txt，保留注释行和空行结构，只删除匹配的 ISBN 行
    lines = target_path.read_text(encoding='utf-8').splitlines()
    kept = []
    removed_count = 0

    for line in lines:
        stripped = line.strip()
        # 注释行和空行直接保留
        if not stripped or stripped.startswith('#'):
            kept.append(line)
            continue
        key = normalize(stripped)
        if key in excluded:
            removed_count += 1
            print(f"  删除: {stripped}")
        else:
            kept.append(line)

    # 清理多余空行（连续空行只保留一个）
    cleaned = []
    prev_blank = False
    for line in kept:
        is_blank = not line.strip()
        if is_blank and prev_blank:
            continue
        cleaned.append(line)
        prev_blank = is_blank

    result = '\n'.join(cleaned).rstrip() + '\n'

    if inplace:
        target_path.write_text(result, encoding='utf-8')
        print(f"\n完成！删除 {removed_count} 个 ISBN，已覆盖写回 {target_path.name}")
    else:
        out_path = target_path.with_stem(target_path.stem + '-cleaned')
        out_path.write_text(result, encoding='utf-8')
        print(f"\n完成！删除 {removed_count} 个 ISBN，结果写入 {out_path.name}")


if __name__ == '__main__':
    main()