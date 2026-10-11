""".desktop 文件扫描与解析。"""

import configparser
import os
import re
import shlex
from typing import Dict, List, Optional

from .models import AppEntry, SRC_FLATPAK, SRC_LOCAL, SRC_SNAP, SRC_UNKNOWN

DESKTOP_DIRS = [
    "/usr/share/applications",
    "/usr/local/share/applications",
    "/var/lib/snapd/desktop/applications",
    "/var/lib/flatpak/exports/share/applications",
    "~/.local/share/applications",
    "~/.local/share/flatpak/exports/share/applications",
]

_EXEC_STRIP = re.compile(r"%[fFuUdDnNickvm]")


def _expand(path: str) -> str:
    return os.path.expanduser(os.path.expandvars(path))


def parse_desktop_file(path: str) -> Optional[Dict[str, str]]:
    """解析 .desktop 文件，返回关键字段；解析失败返回 None。"""
    try:
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.optionxform = str  # 保留键名大小写
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            cp.read_file(fh, source=path)
    except Exception:
        return None
    if not cp.has_section("Desktop Entry"):
        return None
    return dict(cp.items("Desktop Entry"))


def _localized(entry: Dict[str, str], key: str) -> str:
    """优先取 zh_CN，其次 zh，最后默认。"""
    for k in (f"{key}[zh_CN]", f"{key}[zh]", key):
        v = entry.get(k, "").strip()
        if v:
            return v
    return ""


def is_hidden(entry: Dict[str, str]) -> bool:
    return (
        entry.get("NoDisplay", "").strip().lower() == "true"
        or entry.get("Hidden", "").strip().lower() == "true"
    )


def clean_exec(exec_line: str) -> str:
    """去掉 Exec 中的字段码与引号残留。"""
    line = _EXEC_STRIP.sub("", exec_line or "").strip()
    return line


def exec_binary(exec_line: str) -> str:
    """从 Exec 行取出实际可执行程序（不含参数）。"""
    line = clean_exec(exec_line)
    if not line:
        return ""
    # 处理 env VAR=val 前缀
    parts = shlex.split(line, posix=True)
    idx = 0
    while idx < len(parts) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", parts[idx]):
        idx += 1
    idx2 = idx
    while idx2 < len(parts) and parts[idx2] in ("env", "/usr/bin/env", "nice", "ionice"):
        idx2 += 1
        while idx2 < len(parts) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", parts[idx2]):
            idx2 += 1
    if idx2 < len(parts):
        return parts[idx2]
    return parts[idx] if idx < len(parts) else ""


def desktop_source_hint(path: str, entry: Dict[str, str]) -> str:
    """根据 .desktop 位置与内容快速判断来源（快速路径，无需执行外部命令）。"""
    norm = os.path.abspath(_expand(path))
    if entry.get("X-Flatpak") or "flatpak run" in (entry.get("Exec") or ""):
        return SRC_FLATPAK
    if norm.startswith("/var/lib/snapd/") or "/snap/" in norm:
        return SRC_SNAP
    if "snap run" in (entry.get("Exec") or ""):
        return SRC_SNAP
    return ""


def scan_desktop_entries() -> List[AppEntry]:
    """扫描所有 .desktop 目录，返回已解析并过滤的应用条目列表。"""
    results: List[AppEntry] = []
    seen = set()
    for raw_dir in DESKTOP_DIRS:
        directory = _expand(raw_dir)
        if not os.path.isdir(directory):
            continue
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        for name in names:
            if not name.endswith(".desktop"):
                continue
            path = os.path.join(directory, name)
            if not os.path.isfile(path):
                continue
            try:
                fields = parse_desktop_file(path)
            except Exception:
                fields = None
            if not fields:
                continue
            ftype = fields.get("Type", "Application").strip()
            if ftype != "Application":
                continue
            if is_hidden(fields):
                continue
            display = _localized(fields, "Name") or name[:-8]
            # 同一应用在多目录出现时按 desktop 文件名去重（优先系统目录）
            dedup_key = name
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            exec_line = fields.get("Exec", "")
            key = f"desktop:{path}"
            results.append(
                AppEntry(
                    key=key,
                    name=display,
                    source=desktop_source_hint(path, fields) or SRC_UNKNOWN,
                    pkg_id=fields.get("X-Flatpak", "").strip(),
                    version=fields.get("X-AppVersion", "").strip(),
                    comment=_localized(fields, "Comment") or _localized(fields, "GenericName"),
                    icon=fields.get("Icon", "").strip(),
                    desktop_path=path,
                    exec_line=exec_line,
                    binary_path=exec_binary(exec_line),
                    is_cli=False,
                    extra={
                        "X-Flatpak": fields.get("X-Flatpak", ""),
                        "TryExec": fields.get("TryExec", ""),
                        "Categories": fields.get("Categories", ""),
                    },
                )
            )
    results.sort(key=lambda e: e.name.lower())
    return results


def is_user_shortcut(path: str) -> bool:
    return os.path.abspath(_expand(path)).startswith(
        os.path.abspath(_expand("~/.local/share/applications"))
    )
