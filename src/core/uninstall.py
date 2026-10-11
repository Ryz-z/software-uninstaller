"""卸载计划生成与执行。

安全约定：
1. 所有命令均为参数列表，绝不使用 shell=True；
2. 包名 / 应用 ID 必须先通过 validation 正则校验；
3. rm 类操作的目标路径必须通过白名单校验；
4. 需要 root 的命令统一走 pkexec + privileged-helper（root 侧再校验一次）。
"""

import os
import shutil
import subprocess
from typing import Callable, List, Optional

from .detectors import Detector, _env
from .models import (
    AppEntry,
    Command,
    ExecResult,
    UninstallPlan,
    SRC_APPIMAGE,
    SRC_APT,
    SRC_CARGO,
    SRC_CONDA,
    SRC_DISTROBOX,
    SRC_DOCKER,
    SRC_FLATPAK,
    SRC_GEM,
    SRC_GO,
    SRC_GUIX,
    SRC_HOMEBREW,
    SRC_LOCAL,
    SRC_NIX,
    SRC_NPM,
    SRC_PIP,
    SRC_SCRIPT,
    SRC_SNAP,
    SRC_SOURCE,
    SRC_UNKNOWN,
)
from .safety import assess, critical_reason, path_warning
from .validation import path_allowed_for_rm, valid_name, valid_arg

HELPER_PATHS = [
    "/usr/lib/software-uninstaller/src/privileged-helper",
    "/usr/lib/software-uninstaller/privileged-helper",
]
HOME = os.path.expanduser("~")


def _helper() -> str:
    for p in HELPER_PATHS:
        if os.path.exists(p):
            return p
    return ""


def _pkexec_argv(argv: List[str], needs_root: bool) -> List[str]:
    if not needs_root:
        return argv
    helper = _helper()
    if helper:
        return ["pkexec", helper] + argv
    return ["pkexec"] + argv


def _rm_cmd(path: str, recursive: bool = False) -> Command:
    argv = ["rm", "-rf" if recursive else "-f", path]
    needs_root = not path.startswith(HOME)
    return Command(argv=argv, needs_root=needs_root, desc=f"删除 {path}")


# ------------------------------------------------------------------ 计划生成
def build_plan(entry: AppEntry, autoremove: bool = False) -> UninstallPlan:
    """根据条目来源生成卸载计划（含安全评估）。"""
    plan = UninstallPlan(entry=entry)
    src = entry.source
    pkg = (entry.pkg_id or "").strip()
    targets = [pkg] if pkg else []
    cmds: List[Command] = []

    if src == SRC_APT:
        if not valid_name("apt", pkg):
            return _reject(plan, "Debian 包名校验失败，拒绝执行。")
        cmds.append(Command(argv=["apt-get", "purge", "-y", pkg],
                            needs_root=True, desc=f"APT 彻底卸载 {pkg}（含配置文件）"))
        if autoremove:
            cmds.append(Command(argv=["apt-get", "autoremove", "--purge", "-y"],
                                needs_root=True, desc="清理不再需要的依赖包"))

    elif src == SRC_SNAP:
        if not valid_name("snap", pkg):
            return _reject(plan, "Snap 名称校验失败，拒绝执行。")
        cmds.append(Command(argv=["snap", "remove", "--purge", pkg],
                            needs_root=True, desc=f"移除 Snap 应用 {pkg}（含数据快照）"))

    elif src == SRC_FLATPAK:
        if not valid_name("flatpak", pkg):
            return _reject(plan, "Flatpak 应用 ID 校验失败，拒绝执行。")
        cmds.append(Command(argv=["flatpak", "uninstall", "--delete-data", "-y", pkg],
                            needs_root=True, desc=f"卸载 Flatpak 应用 {pkg}（含用户数据）"))

    elif src == SRC_HOMEBREW:
        if not valid_name("brew", pkg):
            return _reject(plan, "Homebrew 包名校验失败，拒绝执行。")
        is_cask = (entry.extra or {}).get("brew_kind") == "cask"
        argv = ["brew", "uninstall"] + (["--cask"] if is_cask else []) + [pkg]
        cmds.append(Command(argv=argv, needs_root=False, desc=f"Homebrew 卸载 {pkg}"))

    elif src == SRC_NIX:
        if not valid_name("nix", pkg):
            return _reject(plan, "Nix 包名校验失败，拒绝执行。")
        cmds.append(Command(argv=["nix-env", "--uninstall", pkg],
                            needs_root=False, desc=f"Nix 卸载 {pkg}"))

    elif src == SRC_GUIX:
        if not valid_name("guix", pkg):
            return _reject(plan, "Guix 包名校验失败，拒绝执行。")
        cmds.append(Command(argv=["guix", "remove", pkg],
                            needs_root=False, desc=f"Guix 卸载 {pkg}"))

    elif src == SRC_PIP:
        if not valid_name("pip", pkg):
            return _reject(plan, "pip 包名校验失败，拒绝执行。")
        tool = "pip3" if shutil.which("pip3", path=_env()["PATH"]) else (
            "pip" if shutil.which("pip", path=_env()["PATH"]) else "pip3")
        cmds.append(Command(argv=[tool, "uninstall", "-y", pkg],
                            needs_root=False, desc=f"pip 卸载 {pkg}"))

    elif src == SRC_NPM:
        if not valid_name("npm", pkg):
            return _reject(plan, "npm 包名校验失败，拒绝执行。")
        cmds.append(Command(argv=["npm", "uninstall", "-g", pkg],
                            needs_root=True, desc=f"npm 全局卸载 {pkg}"))

    elif src == SRC_CARGO:
        if not valid_name("cargo", pkg):
            return _reject(plan, "cargo 包名校验失败，拒绝执行。")
        cmds.append(Command(argv=["cargo", "uninstall", pkg],
                            needs_root=False, desc=f"cargo 卸载 {pkg}"))

    elif src == SRC_GO:
        path = pkg if os.path.isabs(pkg) else os.path.join(HOME, "go", "bin", pkg)
        warn = path_warning(path)
        if warn:
            return _reject(plan, warn)
        cmds.append(_rm_cmd(path))

    elif src == SRC_GEM:
        if not valid_name("gem", pkg):
            return _reject(plan, "gem 包名校验失败，拒绝执行。")
        cmds.append(Command(argv=["gem", "uninstall", "-x", "-I", "--force", pkg],
                            needs_root=False, desc=f"gem 卸载 {pkg}"))

    elif src == SRC_CONDA:
        if not valid_name("conda", pkg):
            return _reject(plan, "conda 包名校验失败，拒绝执行。")
        env_name = (entry.extra or {}).get("conda_env", "")
        argv = ["conda", "remove", "-y"]
        if env_name and valid_arg(env_name):
            argv += ["-n", env_name]
        argv.append(pkg)
        cmds.append(Command(argv=argv, needs_root=False, desc=f"conda 移除 {pkg}"))

    elif src == SRC_DISTROBOX:
        if not valid_name("distrobox", pkg):
            return _reject(plan, "Distrobox 容器名校验失败，拒绝执行。")
        cmds.append(Command(argv=["distrobox-rm", "--force", pkg],
                            needs_root=False, desc=f"删除 Distrobox 容器 {pkg}"))

    elif src == SRC_DOCKER:
        kind, _, ident = pkg.partition(":")
        if kind not in ("image", "container") or not valid_name("docker", ident):
            return _reject(plan, "Docker 标识校验失败，拒绝执行。")
        if kind == "image":
            cmds.append(Command(argv=["docker", "rmi", "-f", ident],
                                needs_root=False, desc=f"删除 Docker 镜像 {ident}"))
        else:
            cmds.append(Command(argv=["docker", "rm", "-f", ident],
                                needs_root=False, desc=f"删除 Docker 容器 {ident}"))

    elif src == SRC_APPIMAGE:
        warn = path_warning(pkg)
        if warn:
            return _reject(plan, warn)
        cmds.append(_rm_cmd(pkg, recursive=False))
        desk = entry.desktop_path
        if desk and desk.startswith(HOME):
            cmds.append(_rm_cmd(desk, recursive=False))

    elif src == SRC_SOURCE:
        src_dir = (entry.extra or {}).get("src_dir", "")
        bin_path = pkg if os.path.isabs(pkg) else (entry.extra or {}).get("bin_path", "")
        if src_dir and os.path.isdir(src_dir) and os.path.exists(os.path.join(src_dir, "Makefile")):
            cmds.append(Command(argv=["make", "-C", src_dir, "uninstall"],
                                needs_root=True, desc=f"在源码目录执行 make uninstall：{src_dir}"))
        elif bin_path:
            warn = path_warning(bin_path)
            if warn:
                return _reject(plan, warn)
            cmds.append(_rm_cmd(bin_path, recursive=False))
        else:
            return _reject(plan, "未找到源码目录或可执行文件，无法自动卸载。")

    elif src == SRC_SCRIPT:
        target = pkg
        if os.path.isdir(target):
            script = _find_uninstall_script(target)
            if script:
                cmds.append(Command(argv=["bash", script], needs_root=not target.startswith(HOME),
                                    desc=f"执行官方卸载脚本：{script}"))
            else:
                warn = path_warning(target)
                if warn:
                    return _reject(plan, warn)
                cmds.append(_rm_cmd(target, recursive=True))
        else:
            warn = path_warning(target)
            if warn:
                return _reject(plan, warn)
            cmds.append(_rm_cmd(target, recursive=False))
            desk = entry.desktop_path
            if desk and desk.startswith(HOME):
                cmds.append(_rm_cmd(desk, recursive=False))

    elif src == SRC_LOCAL:
        desk = entry.desktop_path
        if not desk:
            return _reject(plan, "该条目没有 .desktop 文件，无法删除。")
        warn = path_warning(desk)
        if warn:
            return _reject(plan, warn)
        cmds.append(_rm_cmd(desk, recursive=False))

    else:
        return _reject(plan, "未能识别安装来源，为避免误删已拒绝自动卸载。")

    # 安全评估：不允许时不保留任何可执行命令
    allowed, reason, warning = assess(entry)
    if not allowed:
        return _reject(plan, reason or "该条目不允许卸载")
    plan.commands = cmds
    plan.targets = targets
    plan.allowed = bool(cmds)
    plan.reason = ""
    plan.warning = warning
    return plan


def _reject(plan: UninstallPlan, reason: str) -> UninstallPlan:
    plan.allowed = False
    plan.reason = reason
    plan.commands = []
    return plan


def _find_uninstall_script(directory: str) -> str:
    """在目录中查找官方卸载脚本。"""
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return ""
    for n in names:
        low = n.lower()
        if low.startswith("uninstall") and low.endswith((".sh", "")):
            p = os.path.join(directory, n)
            if os.path.isfile(p):
                return p
    for n in names:
        if n.lower() == "uninstall.sh" or n.lower() == "remove.sh":
            return os.path.join(directory, n)
    return ""


def find_source_dir(name: str) -> str:
    """尽力查找源码编译目录（含 Makefile）。"""
    candidates = [
        os.path.join(HOME, name),
        os.path.join(HOME, "src", name),
        os.path.join(HOME, "Downloads", name),
        os.path.join("/usr/local/src", name),
        os.path.join("/opt", name),
    ]
    for c in candidates:
        if os.path.isdir(c) and os.path.exists(os.path.join(c, "Makefile")):
            return c
    return ""


# ------------------------------------------------------------------ 执行
def _stream(argv: List[str], on_line: Optional[Callable[[str], None]]) -> ExecResult:
    """流式执行命令，逐行回调输出。"""
    if on_line:
        on_line("$ " + " ".join(argv))
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=_env(),
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        msg = f"未找到命令：{argv[0]}"
        if on_line:
            on_line(msg)
        return ExecResult(False, 127, "", msg, " ".join(argv))
    except PermissionError:
        msg = f"权限不足：{argv[0]}"
        if on_line:
            on_line(msg)
        return ExecResult(False, 126, "", msg, " ".join(argv))

    buf: List[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            buf.append(line)
            if on_line:
                on_line(line.rstrip("\n"))
        proc.wait()
    except Exception as exc:  # pragma: no cover
        if on_line:
            on_line(f"读取输出失败：{exc}")
    code = proc.returncode if proc.returncode is not None else -1
    return ExecResult(ok=code == 0, code=code, output="".join(buf), error="", cmd=" ".join(argv))


def execute_plan(plan: UninstallPlan,
                 on_line: Optional[Callable[[str], None]] = None,
                 autoremove: bool = False) -> List[ExecResult]:
    """按序执行计划中的命令；返回结果列表。"""
    results: List[ExecResult] = []
    for cmd in plan.commands:
        argv = _pkexec_argv(cmd.argv, cmd.needs_root)
        res = _stream(argv, on_line)
        results.append(res)
        if not res.ok:
            if on_line:
                on_line(f"[失败] 命令返回码 {res.code}，已停止后续操作。")
            break
    return results


def simulate_plan(plan: UninstallPlan,
                  on_line: Optional[Callable[[str], None]] = None) -> None:
    """模拟执行（仅 APT / Snap 支持），用于预览将被连带删除的包。"""
    entry = plan.entry
    if entry.source == SRC_APT and plan.targets:
        argv = ["apt-get", "purge", "-s", "-y", plan.targets[0]]
        _stream(_pkexec_argv(argv, False), on_line)
    elif entry.source == SRC_SNAP and plan.targets:
        _stream(["snap", "remove", "--purge", "--no-wait", "-h"], on_line)
        if on_line:
            on_line("提示：Snap 无模拟模式，请直接查看下方命令的影响范围。")
    else:
        if on_line:
            on_line("该来源不支持模拟执行，请直接查看将要执行的命令。")


def refresh_after(plan: UninstallPlan) -> None:
    """卸载后刷新桌面数据库（用户级快捷方式删除后生效）。"""
    entry = plan.entry
    if entry.source in (SRC_LOCAL, SRC_APPIMAGE, SRC_SCRIPT):
        try:
            subprocess.run(["update-desktop-database",
                            os.path.join(HOME, ".local", "share", "applications")],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=15, check=False)
        except Exception:
            pass
