#!/usr/bin/env python3
"""Locate the Google Drive Desktop snapshot directory on macOS."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

RELATIVE_PATHS = (
    Path("My Drive") / "10_岗位池与公司研究" / "01_每日职位原始快照",
    Path("我的云端硬盘") / "10_岗位池与公司研究" / "01_每日职位原始快照",
)


def candidates() -> list[Path]:
    home = Path.home()
    roots: list[Path] = []
    cloud = home / "Library" / "CloudStorage"
    if cloud.exists():
        roots.extend(sorted(path for path in cloud.glob("GoogleDrive-*") if path.is_dir()))
    roots.extend(
        path
        for path in (
            home / "Google Drive",
            home / "GoogleDrive",
        )
        if path.exists()
    )

    found: list[Path] = []
    for root in roots:
        for relative in RELATIVE_PATHS:
            path = root / relative
            if path.exists() and path.is_dir():
                found.append(path.resolve())
    return list(dict.fromkeys(found))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()
    found = candidates()
    if not found:
        print(
            "未找到Google Drive Desktop同步目录。请先安装并登录Google Drive桌面版，"
            "确保‘10_岗位池与公司研究/01_每日职位原始快照’可在Finder中打开，"
            "然后把该目录完整路径作为setup_mac.sh的第一个参数。",
            file=sys.stderr,
        )
        return 2
    if len(found) == 1 or not args.interactive:
        print(found[0])
        return 0

    print("检测到多个Google Drive目录：", file=sys.stderr)
    for index, path in enumerate(found, start=1):
        print(f"  {index}. {path}", file=sys.stderr)
    while True:
        value = input("请选择编号: ").strip()
        if value.isdigit() and 1 <= int(value) <= len(found):
            print(found[int(value) - 1])
            return 0
        print("请输入有效编号。", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
