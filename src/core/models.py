"""数据模型：应用条目与卸载计划。"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------- 来源常量
SRC_APT = "apt"
SRC_SNAP = "snap"
SRC_FLATPAK = "flatpak"
SRC_HOMEBREW = "brew"
SRC_NIX = "nix"
SRC_GUIX = "guix"
SRC_PIP = "pip"
SRC_NPM = "npm"
SRC_CARGO = "cargo"
SRC_GO = "go"
SRC_GEM = "gem"
SRC_CONDA = "conda"
SRC_DISTROBOX = "distrobox"
SRC_DOCKER = "docker"
SRC_APPIMAGE = "appimage"
SRC_SOURCE = "source"
SRC_SCRIPT = "script"
SRC_LOCAL = "local-shortcut"
SRC_UNKNOWN = "unknown"

# 来源 -> 中文显示名
SOURCE_LABELS: Dict[str, str] = {
    SRC_APT: "APT / DPKG",
    SRC_SNAP: "Snap",
    SRC_FLATPAK: "Flatpak",
    SRC_HOMEBREW: "Homebrew",
    SRC_NIX: "Nix",
    SRC_GUIX: "Guix",
    SRC_PIP: "pip (Python)",
    SRC_NPM: "npm (Node.js)",
    SRC_CARGO: "cargo (Rust)",
    SRC_GO: "Go",
    SRC_GEM: "gem (Ruby)",
    SRC_CONDA: "conda",
    SRC_DISTROBOX: "Distrobox 容器",
    SRC_DOCKER: "Docker",
    SRC_APPIMAGE: "AppImage",
    SRC_SOURCE: "源码编译",
    SRC_SCRIPT: "脚本安装",
    SRC_LOCAL: "本地快捷方式",
    SRC_UNKNOWN: "未知来源",
}

SOURCES = list(SOURCE_LABELS.keys())

APP_TITLE = "软件卸载工具"
APP_VERSION = "1.0.2"


def source_label(src: str) -> str:
    return SOURCE_LABELS.get(src, src)


@dataclass
class AppEntry:
    """一个可卸载条目（.desktop 应用或命令行工具）。"""

    key: str                      # 唯一标识
    name: str                     # 显示名
    source: str = SRC_UNKNOWN     # 来源类型
    pkg_id: str = ""              # 包名 / 应用 ID / 容器名 / 文件路径
    version: str = ""
    comment: str = ""
    icon: str = ""                # 图标名或路径
    desktop_path: str = ""        # .desktop 文件路径（可为空）
    exec_line: str = ""           # Exec 原始行
    binary_path: str = ""         # 解析出的可执行文件路径
    is_cli: bool = False          # 是否来自“命令行工具”页
    extra: Dict[str, str] = field(default_factory=dict)

    _haystack: str = field(default="", repr=False, compare=False)

    def label_source(self) -> str:
        return source_label(self.source)

    def search_haystack(self) -> str:
        """用于搜索匹配的字符串（名称 / 包名 / 来源 / 描述 + 拼音），结果缓存。"""
        if self._haystack:
            return self._haystack
        from .pinyin import search_keys

        parts = [
            self.name or "",
            self.pkg_id or "",
            self.label_source(),
            self.comment or "",
            os.path.basename(self.binary_path or ""),
        ]
        # 名称与包名额外展开拼音（全拼 + 首字母）
        parts.extend(search_keys(self.name))
        parts.extend(search_keys(os.path.basename(self.pkg_id or "")))
        self._haystack = " ".join(p for p in parts if p).lower()
        return self._haystack


@dataclass
class Command:
    """一条待执行的命令。"""

    argv: List[str]               # 参数列表（不含 pkexec）
    needs_root: bool = False
    cwd: str = ""
    desc: str = ""                # 人类可读说明

    def pretty(self) -> str:
        base = " ".join(self.argv)
        if self.cwd:
            base = f"(cd {self.cwd} && {base})"
        if self.needs_root:
            base = "sudo " + base
        return base


@dataclass
class UninstallPlan:
    """卸载计划：一组命令 + 安全评估结果。"""

    entry: AppEntry
    commands: List[Command] = field(default_factory=list)
    targets: List[str] = field(default_factory=list)
    allowed: bool = True          # 是否允许执行
    reason: str = ""              # 禁止/警告原因
    warning: str = ""             # 额外警告文案

    def preview(self) -> str:
        return "\n".join(c.pretty() for c in self.commands)


@dataclass
class ExecResult:
    ok: bool
    code: int
    output: str
    error: str
    cmd: str = ""

    @property
    def combined(self) -> str:
        return (self.output or "") + (self.error or "")


def make_key(source: str, pkg_id: str, fallback: str = "") -> str:
    return f"{source}:{pkg_id or fallback}"
