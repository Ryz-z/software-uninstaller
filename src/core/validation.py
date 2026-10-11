"""名称与路径校验：所有进入命令行的用户输入必须先过这里。"""

import os
import re
from typing import List, Optional

# ------------------------------------------------------------ 名称正则
# Debian 包名
RE_DEB = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9+.-]+$")
# Snap 名称
RE_SNAP = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
# Flatpak 应用 ID
RE_FLATPAK = re.compile(r"^[a-zA-Z0-9._-]+$")
# npm 包名（支持 @scope/name）
RE_NPM = re.compile(r"^(@[a-zA-Z0-9._-]+/)?[a-zA-Z0-9._-]+$")
# pip / cargo / gem / brew / conda / nix / guix / distrobox
RE_GENERIC = re.compile(r"^[a-zA-Z0-9._-]+$")
RE_GENERIC_LOOSE = re.compile(r"^[a-zA-Z0-9._+:-]+$")

PATTERNS = {
    "apt": RE_DEB,
    "snap": RE_SNAP,
    "flatpak": RE_FLATPAK,
    "npm": RE_NPM,
    "pip": RE_GENERIC,
    "cargo": RE_GENERIC,
    "gem": RE_GENERIC_LOOSE,
    "conda": RE_GENERIC_LOOSE,
    "brew": RE_GENERIC_LOOSE,
    "nix": RE_GENERIC_LOOSE,
    "guix": RE_GENERIC_LOOSE,
    "distrobox": RE_GENERIC,
    "docker": RE_GENERIC_LOOSE,
    "go": RE_GENERIC,
}


def valid_name(kind: str, value: str) -> bool:
    """校验包名 / 应用 ID 是否合法。"""
    if not value or len(value) > 200:
        return False
    pattern: Optional[re.Pattern] = PATTERNS.get(kind, RE_GENERIC)
    return bool(pattern.match(value))


def valid_arg(arg: str) -> bool:
    """通用参数校验：不允许 shell 元字符与空白。"""
    if not arg or len(arg) > 4096:
        return False
    return not re.search(r"[;&|`$<>\\\"'\n\r\t ]", arg)


# ------------------------------------------------------------ 路径校验
# 允许 rm 删除的前缀（用户级目录与 /opt、/usr/local）
RM_ALLOWED_PREFIXES: List[str] = [
    "/opt/",
    "/usr/local/",
]

# 明令禁止删除的系统目录
RM_FORBIDDEN_PREFIXES: List[str] = [
    "/usr/bin",
    "/usr/sbin",
    "/usr/lib",
    "/usr/share",
    "/usr/include",
    "/etc",
    "/var",
    "/bin",
    "/sbin",
    "/lib",
    "/boot",
    "/root",
    "/proc",
    "/sys",
    "/dev",
]


def _user_prefixes() -> List[str]:
    home = os.path.expanduser("~")
    return [
        os.path.join(home, ".local") + os.sep,
        os.path.join(home, "Applications") + os.sep,
        os.path.join(home, "go", "bin") + os.sep,
        os.path.join(home, ".cargo", "bin") + os.sep,
        os.path.join(home, ".local", "share", "applications") + os.sep,
    ]


def normalize_path(path: str) -> str:
    """展开 ~ 与变量，取绝对路径的规范化形式（不解析符号链接）。"""
    expanded = os.path.expanduser(os.path.expandvars(path))
    return os.path.normpath(os.path.abspath(expanded))


def path_allowed_for_rm(path: str) -> bool:
    """判断该路径是否允许被删除。"""
    if not path:
        return False
    if ".." in path.split(os.sep):
        return False
    real = normalize_path(path)
    # 先判禁止目录（含符号链接解析后的真实路径，防逃逸）
    try:
        resolved = os.path.realpath(real)
    except OSError:
        resolved = real
    for bad in RM_FORBIDDEN_PREFIXES:
        if resolved == bad or resolved.startswith(bad + os.sep):
            return False
        if real == bad or real.startswith(bad + os.sep):
            return False
    allowed = RM_ALLOWED_PREFIXES + _user_prefixes()
    for good in allowed:
        if real == good.rstrip(os.sep) or real.startswith(good):
            return True
    # Home 目录内的其他文件（如 ~/Applications、~/foo.AppImage）也放行
    home = os.path.expanduser("~")
    if real.startswith(home + os.sep) and resolved.startswith(home + os.sep):
        return True
    return False


def safe_flag(arg: str) -> bool:
    """允许出现在命令中的固定参数（白名单）。"""
    return arg in {
        "-y", "--yes", "--purge", "--delete-data", "-f", "--force",
        "--autoremove", "-x", "-I", "--ignore-dependencies", "--cask",
        "--uninstall", "uninstall", "remove", "purge", "-g", "--global",
        "-n", "--name", "-q", "--quiet",
    }
