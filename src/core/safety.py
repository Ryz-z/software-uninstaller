"""安全策略：关键包保护、可卸载性判定。"""

import fnmatch
import os
import re

from .models import AppEntry, SRC_APT, SRC_LOCAL, SRC_SNAP, SRC_FLATPAK, SRC_DOCKER
from .validation import path_allowed_for_rm

# ------------------------------------------------------- 禁止卸载的关键包
CRITICAL_PACKAGES = {
    # 基础系统
    "apt", "apt-utils", "dpkg", "dpkg-dev", "snapd", "systemd", "systemd-sysv",
    "init", "initramfs-tools", "coreutils", "bash", "dash", "sudo", "dbus",
    "policykit-1", "polkitd", "pkexec", "login", "util-linux", "procps",
    # 桌面环境
    "ubuntu-desktop", "ubuntu-desktop-minimal", "ubuntu-minimal", "ubuntu-standard",
    "ubuntu-server", "ubuntu-release-upgrader-core", "ubuntu-drivers-common",
    "gnome-shell", "gnome-session", "gnome-session-bin", "gnome-session-common",
    "gnome-control-center", "gnome-terminal", "gnome-settings-daemon",
    "gdm3", "sddm", "lightdm", "nautilus", "mutter", "mutter-common",
    "xorg", "xserver-xorg", "xserver-xorg-core", "xwayland", "wayland-protocols",
    # 核心运行库 / 网络
    "libc6", "libc-bin", "libgcc-s1", "libstdc++6", "zlib1g",
    "network-manager", "netplan.io", "systemd-resolved", "openssh-client",
    "python3", "python3-minimal", "python3-gi", "perl-base", "perl-modules",
    "gir1.2-gtk-4.0", "gir1.2-gtk-3.0", "gir1.2-glib-2.0",
    "libgtk-4-1", "libgtk-3-0", "libadwaita-1-0",
    # 引导
    "grub-common", "grub-efi", "grub-efi-amd64", "grub-pc", "grub2-common",
    "shim-signed", "efibootmgr", "os-prober",
}

CRITICAL_PATTERNS = [
    "linux-image-*",
    "linux-headers-*",
    "linux-modules-*",
    "linux-generic*",
    "linux-firmware",
    "systemd-*",
    "libc6*",
    "python3.1*",
    "gnome-shell-*",
    "ubuntu-desktop*",
]

# ------------------------------------------------------- 高风险但允许（强警告）
RISKY_PATTERNS = [
    "libreoffice*",
    "firefox*",
    "thunderbird*",
    "libgtk*",
    "python3-*",
    "gir1.2-*",
    "x11-*",
    "fonts-*",
]


def is_critical_package(name: str) -> bool:
    """Debian 包名是否属于系统关键组件。"""
    if not name:
        return False
    if name in CRITICAL_PACKAGES:
        return True
    return any(fnmatch.fnmatch(name, p) for p in CRITICAL_PATTERNS)


def is_risky_package(name: str) -> bool:
    """卸载后可能影响其他软件的包（强警告，但不禁止）。"""
    return any(fnmatch.fnmatch(name, p) for p in RISKY_PATTERNS)


CRITICAL_SNAPS = {"snapd", "core", "core18", "core20", "core22", "core24",
                  "snap-store", "snapd-desktop-integration", "gtk-common-themes"}

CRITICAL_FLATPAKS = {
    "org.gnome.Platform", "org.freedesktop.Platform", "org.kde.Platform",
    "org.gnome.Sdk", "org.freedesktop.Sdk",
}


def critical_reason(source: str, pkg_id: str) -> str:
    """返回禁止卸载的原因；空字符串表示允许。"""
    if source == SRC_APT and is_critical_package(pkg_id):
        return "该 Debian 包属于系统关键组件，卸载可能导致系统无法启动或桌面环境损坏。"
    if source == SRC_SNAP and pkg_id in CRITICAL_SNAPS:
        return "该 Snap 是 snapd 运行时的核心组件，卸载会导致所有 Snap 应用失效。"
    if source == SRC_FLATPAK and pkg_id.split("/")[0] in CRITICAL_FLATPAKS:
        return "该 Flatpak 是共享运行时（Runtime），卸载会导致依赖它的应用无法启动。"
    return ""


def risky_warning(source: str, pkg_id: str) -> str:
    if source == SRC_APT and is_risky_package(pkg_id):
        return "该包可能被其他软件依赖，卸载前请仔细查看下面的“将被一同移除的包”预览。"
    if source == SRC_DOCKER:
        return "Docker 镜像/容器删除后不可恢复，请先确认没有重要数据。"
    if source == SRC_SNAP:
        return "Snap 应用的数据快照会被保留 31 天，使用 --purge 会一并清除用户数据。"
    return ""


def path_warning(path: str) -> str:
    if not path:
        return ""
    if not path_allowed_for_rm(path):
        return "目标路径不在允许删除的范围内（仅允许 /opt/、/usr/local/、~/Applications/、~/.local/、~/go/bin/ 及家目录内文件）。"
    if path.startswith("/usr/local/"):
        return "删除 /usr/local/ 下的文件属于源码编译安装的清理操作，可能残留配置文件。"
    return ""


def assess(entry: AppEntry) -> tuple:
    """评估一个条目是否允许卸载，返回 (allowed, reason, warning)。"""
    reason = critical_reason(entry.source, entry.pkg_id)
    if reason:
        return False, reason, ""
    if entry.source == SRC_LOCAL:
        # 本地快捷方式：只允许删除 .desktop 文件
        if not entry.desktop_path:
            return False, "该条目没有对应的 .desktop 文件。", ""
        warn = path_warning(entry.desktop_path)
        if warn:
            return False, warn, ""
        return True, "", "仅删除该快捷方式文件，不会卸载软件本体。"
    warning = risky_warning(entry.source, entry.pkg_id)
    return True, "", warning


def autoremove_warning() -> str:
    return ("自动清理（autoremove）会移除所有不再被依赖的包，"
            "请先查看终端输出中的 “The following packages will be REMOVED” 列表再确认。")
