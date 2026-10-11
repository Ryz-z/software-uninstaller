#!/usr/bin/python3
"""软件卸载工具 —— 入口。

用法：
  software-uninstaller             启动图形界面
  software-uninstaller --list      列出所有条目（文本）
  software-uninstaller --json      以 JSON 输出检测结果
  software-uninstaller --plan <关键字>  显示某个软件的卸载计划
  software-uninstaller --version
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.detectors import Detector  # noqa: E402
from core.desktop import scan_desktop_entries  # noqa: E402
from core.models import AppEntry, APP_VERSION  # noqa: E402
from core import uninstall as U  # noqa: E402

VERSION = APP_VERSION


def collect(detector: Detector, with_cli: bool = True):
    apps = scan_desktop_entries()
    detector.detect_all(apps)
    cli = detector.cli_entries() if with_cli else []
    return apps, cli


def entry_to_dict(e: AppEntry) -> dict:
    return {
        "name": e.name,
        "source": e.source,
        "source_label": e.label_source(),
        "pkg_id": e.pkg_id,
        "version": e.version,
        "desktop": e.desktop_path,
        "binary": e.binary_path,
        "cli": e.is_cli,
    }


def cmd_list(args) -> int:
    det = Detector()
    apps, cli = collect(det, with_cli=not args.no_cli)
    if args.json:
        data = [entry_to_dict(e) for e in apps] + [entry_to_dict(e) for e in cli]
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f"图形应用（{len(apps)}）：")
    for e in apps:
        print(f"  {e.name:<38} {e.label_source():<16} {e.pkg_id}")
    if cli:
        print(f"\n命令行工具（{len(cli)}）：")
        for e in cli:
            print(f"  {e.name:<38} {e.label_source():<16} {e.version}")
    return 0


def cmd_plan(args) -> int:
    det = Detector()
    apps, cli = collect(det)
    keyword = args.keyword.lower()
    hits = [e for e in apps + cli
            if keyword in (e.name + " " + e.pkg_id).lower()]
    if not hits:
        print(f"未找到匹配 “{args.keyword}” 的条目")
        return 1
    for e in hits[: args.limit]:
        plan = U.build_plan(e)
        print("=" * 72)
        print(f"名称: {e.name}")
        print(f"来源: {e.label_source()}")
        print(f"标识: {e.pkg_id}   版本: {e.version or '—'}")
        print(f"允许卸载: {'是' if plan.allowed else '否'}")
        if plan.reason:
            print(f"原因: {plan.reason}")
        if plan.warning:
            print(f"警告: {plan.warning}")
        print("命令:")
        for c in plan.commands:
            print("   " + c.pretty())
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="software-uninstaller",
        description="检测软件安装来源并安全卸载（APT / Snap / Flatpak / AppImage / pip / npm …）",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--list", action="store_true", help="列出所有检测到的条目")
    parser.add_argument("--no-cli", action="store_true", help="--list 时不含命令行工具")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--plan", metavar="KEYWORD", dest="keyword",
                        help="显示匹配条目的卸载计划")
    parser.add_argument("--limit", type=int, default=5, help="--plan 最多显示条数")
    args = parser.parse_args(argv)

    if args.list or args.json:
        return cmd_list(args)
    if args.keyword:
        return cmd_plan(args)

    if os.geteuid() == 0:
        print("提示：请不要以 root 直接运行本程序；需要提权时会自动通过 pkexec 申请。")

    try:
        from ui import run_ui
    except Exception as exc:  # pragma: no cover
        print(f"界面加载失败：{exc}")
        return 2
    return run_ui([])


if __name__ == "__main__":
    sys.exit(main())
