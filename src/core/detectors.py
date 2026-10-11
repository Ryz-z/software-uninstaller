"""安装来源检测引擎。

所有外部命令都通过参数列表调用（绝不使用 shell=True），
并带超时，避免某个包管理器卡死拖住界面。
"""

import os
import re
import shlex
import shutil
import subprocess
from typing import Callable, Dict, List, Optional, Set, Tuple

from .desktop import is_user_shortcut, scan_desktop_entries
from .models import (
    AppEntry,
    ExecResult,
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

HOME = os.path.expanduser("~")
TIMEOUT_SHORT = 12
TIMEOUT_LONG = 25


def run(argv: List[str], timeout: int = TIMEOUT_SHORT) -> ExecResult:
    """安全执行命令：参数列表、无 shell、带超时。"""
    try:
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env=_env(),
        )
        return ExecResult(
            ok=proc.returncode == 0,
            code=proc.returncode,
            output=_decode(proc.stdout),
            error=_decode(proc.stderr),
            cmd=" ".join(argv),
        )
    except FileNotFoundError:
        return ExecResult(False, 127, "", f"未找到命令：{argv[0]}")
    except subprocess.TimeoutExpired:
        return ExecResult(False, 124, "", f"命令超时（{timeout}s）：{' '.join(argv)}")
    except Exception as exc:  # pragma: no cover
        return ExecResult(False, 1, "", f"执行失败：{exc}")


def _decode(data: bytes) -> str:
    return (data or b"").decode("utf-8", errors="replace")


def _env() -> Dict[str, str]:
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    # 让 Homebrew / cargo / go 等用户级工具也能被找到
    for extra in (f"{HOME}/.linuxbrew/bin", "/home/linuxbrew/.linuxbrew/bin",
                  f"{HOME}/.cargo/bin", f"{HOME}/go/bin", f"{HOME}/.local/bin",
                  "/snap/bin"):
        if extra not in env["PATH"]:
            env["PATH"] = extra + os.pathsep + env["PATH"]
    return env


def norm_name(name: str) -> str:
    """归一化名称用于模糊匹配：小写，去掉分隔符。"""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _first_token(text: str) -> str:
    return (text or "").strip().split()[0] if text.strip() else ""


# ------------------------------------------------------------ dpkg 索引
class DpkgIndex:
    """通过读取 /var/lib/dpkg/info/*.list 建立 文件 -> 包名 索引（比逐个 dpkg -S 快得多）。

    环境变量 SU_DPKG_INFO_DIR 可覆盖数据库目录（仅用于测试）。
    """

    def __init__(self, info_dir: Optional[str] = None) -> None:
        self.desktop_map: Dict[str, str] = {}
        self.bin_map: Dict[str, str] = {}
        self.ready = False
        self.info_dir = info_dir or os.environ.get("SU_DPKG_INFO_DIR") or "/var/lib/dpkg/info"

    def build(self) -> None:
        info_dir = self.info_dir
        if not os.path.isdir(info_dir):
            self.ready = False
            return
        bin_prefixes = ("/usr/bin/", "/usr/sbin/", "/usr/games/", "/bin/", "/sbin/")
        try:
            for name in os.listdir(info_dir):
                if not name.endswith(".list"):
                    continue
                pkg = name[:-5].split(":")[0]
                path = os.path.join(info_dir, name)
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        for line in fh:
                            p = line.strip()
                            if not p:
                                continue
                            if p.endswith(".desktop") and "/applications/" in p:
                                self.desktop_map[p] = pkg
                            elif p.startswith(bin_prefixes) or p.startswith("/usr/local/bin/"):
                                self.bin_map[p] = pkg
                except OSError:
                    continue
            self.ready = True
        except OSError:
            self.ready = False

    def lookup_file(self, path: str) -> str:
        """返回拥有该文件的 Debian 包名，找不到返回空字符串。"""
        if path in self.desktop_map:
            return self.desktop_map[path]
        if path in self.bin_map:
            return self.bin_map[path]
        # 兜底：调用 dpkg -S
        res = run(["dpkg", "-S", path], timeout=TIMEOUT_SHORT)
        if res.ok and res.output.strip():
            return res.output.strip().split(":")[0].strip()
        return ""


# ------------------------------------------------------------ 检测引擎
class Detector:
    """懒加载各包管理器的已安装列表，并据此判断软件安装来源。"""

    def __init__(self) -> None:
        self.dpkg = DpkgIndex()
        self._cache: Dict[str, object] = {}
        self._done: Set[str] = set()
        self._versions: Dict[str, str] = {}

    # ------------------------------------------------ 工具是否存在
    @staticmethod
    def has(tool: str) -> bool:
        return shutil.which(tool, path=_env()["PATH"]) is not None

    # ------------------------------------------------ 各来源列表（懒加载）
    def _once(self, key: str, builder: Callable[[], object]) -> object:
        if key not in self._cache:
            self._cache[key] = builder()
            self._done.add(key)
        return self._cache[key]

    def snap_pkgs(self) -> Dict[str, str]:
        def build() -> Dict[str, str]:
            out: Dict[str, str] = {}
            if not self.has("snap"):
                return out
            res = run(["snap", "list"], timeout=TIMEOUT_SHORT)
            for line in res.output.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2:
                    out[parts[0]] = parts[1] if len(parts) > 1 else ""
            return out
        return self._once("snap", build)  # type: ignore[return-value]

    def flatpak_pkgs(self) -> Dict[str, Tuple[str, str]]:
        def build() -> Dict[str, Tuple[str, str]]:
            out: Dict[str, Tuple[str, str]] = {}
            if not self.has("flatpak"):
                return out
            res = run(
                ["flatpak", "list", "--app", "--columns=name,application,version"],
                timeout=TIMEOUT_LONG,
            )
            for line in res.output.splitlines():
                parts = [p.strip() for p in line.split("\t")]
                if len(parts) >= 2 and parts[1]:
                    out[parts[1]] = (parts[0], parts[2] if len(parts) > 2 else "")
            return out
        return self._once("flatpak", build)  # type: ignore[return-value]

    def brew_pkgs(self) -> Tuple[Set[str], Set[str]]:
        def build() -> Tuple[Set[str], Set[str]]:
            formula: Set[str] = set()
            cask: Set[str] = set()
            if not self.has("brew"):
                return formula, cask
            for flag, target in (("--formula", formula), ("--cask", cask)):
                res = run(["brew", "list", flag], timeout=TIMEOUT_LONG)
                if res.ok:
                    target.update(p.strip() for p in res.output.split() if p.strip())
            return formula, cask
        return self._once("brew", build)  # type: ignore[return-value]

    def nix_pkgs(self) -> Set[str]:
        def build() -> Set[str]:
            if not self.has("nix-env"):
                return set()
            res = run(["nix-env", "-q"], timeout=TIMEOUT_LONG)
            return set(l.strip() for l in res.output.splitlines() if l.strip())
        return self._once("nix", build)  # type: ignore[return-value]

    def guix_pkgs(self) -> Set[str]:
        def build() -> Set[str]:
            if not self.has("guix"):
                return set()
            res = run(["guix", "package", "--list-installed"], timeout=TIMEOUT_LONG)
            return set(_first_token(l) for l in res.output.splitlines() if l.strip())
        return self._once("guix", build)  # type: ignore[return-value]

    def pip_pkgs(self) -> Dict[str, str]:
        def build() -> Dict[str, str]:
            out: Dict[str, str] = {}
            for tool in ("pip3", "pip"):
                if not self.has(tool):
                    continue
                res = run([tool, "list", "--format=freeze"], timeout=TIMEOUT_LONG)
                if not res.ok:
                    res = run([tool, "list"], timeout=TIMEOUT_LONG)
                for line in res.output.splitlines():
                    if "==" in line:
                        n, v = line.split("==", 1)
                        out[n.strip()] = v.strip()
                    elif line.strip() and not line.lower().startswith(("package", "---")):
                        parts = line.split()
                        if len(parts) >= 2:
                            out[parts[0]] = parts[1]
                if out:
                    break
            return out
        return self._once("pip", build)  # type: ignore[return-value]

    def npm_pkgs(self) -> Dict[str, str]:
        def build() -> Dict[str, str]:
            out: Dict[str, str] = {}
            if not self.has("npm"):
                return out
            res = run(["npm", "list", "-g", "--depth=0"], timeout=TIMEOUT_LONG)
            for line in res.output.splitlines():
                m = re.match(r"^[├└│\s]*([@A-Za-z0-9._/-]+)@([^\s]+)", line)
                if m:
                    out[m.group(1)] = m.group(2)
            return out
        return self._once("npm", build)  # type: ignore[return-value]

    def cargo_pkgs(self) -> Set[str]:
        def build() -> Set[str]:
            if not self.has("cargo"):
                return set()
            res = run(["cargo", "install", "--list"], timeout=TIMEOUT_LONG)
            found: Set[str] = set()
            for line in res.output.splitlines():
                if line and not line.startswith(" ") and " " not in line.strip():
                    found.add(line.strip())
                elif line.strip().endswith(":"):
                    pass
            return found
        return self._once("cargo", build)  # type: ignore[return-value]

    def go_bins(self) -> Dict[str, str]:
        def build() -> Dict[str, str]:
            out: Dict[str, str] = {}
            d = os.path.join(HOME, "go", "bin")
            if os.path.isdir(d):
                for n in sorted(os.listdir(d)):
                    p = os.path.join(d, n)
                    if os.path.isfile(p) and os.access(p, os.X_OK):
                        out[n] = p
            return out
        return self._once("go", build)  # type: ignore[return-value]

    def gem_pkgs(self) -> Set[str]:
        def build() -> Set[str]:
            if not self.has("gem"):
                return set()
            res = run(["gem", "list"], timeout=TIMEOUT_LONG)
            found: Set[str] = set()
            for line in res.output.splitlines():
                m = re.match(r"^([A-Za-z0-9._-]+)\s*\(([^)]*)\)", line)
                if m:
                    found.add(m.group(1))
            return found
        return self._once("gem", build)  # type: ignore[return-value]

    def conda_pkgs(self) -> Dict[str, str]:
        def build() -> Dict[str, str]:
            out: Dict[str, str] = {}
            if not self.has("conda"):
                return out
            res = run(["conda", "list"], timeout=TIMEOUT_LONG)
            for line in res.output.splitlines():
                if line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    out[parts[0]] = parts[1]
            return out
        return self._once("conda", build)  # type: ignore[return-value]

    def distrobox_list(self) -> List[str]:
        def build() -> List[str]:
            if not self.has("distrobox-list"):
                return []
            res = run(["distrobox-list", "--no-color"], timeout=TIMEOUT_LONG)
            names: List[str] = []
            for line in res.output.splitlines()[1:]:
                parts = [p.strip() for p in line.split("|")]
                if parts and parts[0]:
                    names.append(parts[0])
            return names
        return self._once("distrobox", build)  # type: ignore[return-value]

    def docker_items(self) -> List[Tuple[str, str, str]]:
        """[(类型 image/container, 标识, 名称)]"""
        def build() -> List[Tuple[str, str, str]]:
            items: List[Tuple[str, str, str]] = []
            if not self.has("docker"):
                return items
            res = run(
                ["docker", "images", "--format", "{{.ID}}\t{{.Repository}}:{{.Tag}}"],
                timeout=TIMEOUT_LONG,
            )
            for line in res.output.splitlines():
                parts = line.split("\t")
                if len(parts) == 2 and parts[0]:
                    items.append(("image", parts[0], parts[1]))
            res = run(
                ["docker", "ps", "-a", "--format", "{{.ID}}\t{{.Names}}\t{{.Image}}"],
                timeout=TIMEOUT_LONG,
            )
            for line in res.output.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2 and parts[0]:
                    items.append(("container", parts[0], parts[1]))
            return items
        return self._once("docker", build)  # type: ignore[return-value]

    def appimages(self) -> List[str]:
        def build() -> List[str]:
            found: List[str] = []
            roots = [
                os.path.join(HOME, "Applications"),
                os.path.join(HOME, ".local", "bin"),
                os.path.join(HOME, "Downloads"),
                os.path.join(HOME, "AppImages"),
                "/opt",
            ]
            for root in roots:
                if not os.path.isdir(root):
                    continue
                for dirpath, _dirs, files in os.walk(root):
                    if dirpath.count(os.sep) - root.count(os.sep) > 3:
                        continue
                    for f in files:
                        if f.lower().endswith((".appimage",)):
                            found.append(os.path.join(dirpath, f))
            return sorted(set(found))
        return self._once("appimage", build)  # type: ignore[return-value]

    def local_bins(self) -> Dict[str, str]:
        """~/.local/bin 下的可执行文件（脚本安装 / pipx / 用户脚本）。"""
        def build() -> Dict[str, str]:
            out: Dict[str, str] = {}
            d = os.path.join(HOME, ".local", "bin")
            if os.path.isdir(d):
                for n in sorted(os.listdir(d)):
                    p = os.path.join(d, n)
                    if os.path.isfile(p) and os.access(p, os.X_OK):
                        out[n] = p
            return out
        return self._once("localbin", build)  # type: ignore[return-value]

    def usr_local_bins(self) -> Dict[str, str]:
        """/usr/local/bin 下的可执行文件（源码编译安装的典型位置）。"""
        def build() -> Dict[str, str]:
            out: Dict[str, str] = {}
            d = "/usr/local/bin"
            if os.path.isdir(d):
                try:
                    for n in sorted(os.listdir(d)):
                        p = os.path.join(d, n)
                        if os.path.isfile(p) and os.access(p, os.X_OK):
                            out[n] = p
                except OSError:
                    pass
            return out
        return self._once("usrlocalbin", build)  # type: ignore[return-value]

    def opt_dirs(self) -> Dict[str, str]:
        """/opt 下的软件目录（脚本安装的典型位置）。"""
        def build() -> Dict[str, str]:
            out: Dict[str, str] = {}
            if os.path.isdir("/opt"):
                try:
                    for n in sorted(os.listdir("/opt")):
                        p = os.path.join("/opt", n)
                        if os.path.isdir(p):
                            out[n] = p
                except OSError:
                    pass
            return out
        return self._once("opt", build)  # type: ignore[return-value]

    def apt_version(self, pkg: str) -> str:
        if pkg in self._versions:
            return self._versions[pkg]
        res = run(["dpkg-query", "-W", "-f=${Version}", pkg], timeout=TIMEOUT_SHORT)
        ver = res.output.strip() if res.ok else ""
        self._versions[pkg] = ver
        return ver

    # ------------------------------------------------ 解析：Snap / Flatpak 名
    def snap_name_from_desktop(self, entry: AppEntry) -> str:
        base = os.path.basename(entry.desktop_path or "")
        stem = base[:-8] if base.endswith(".desktop") else base
        m = re.match(r"^([^_]+)_", stem)
        candidate = m.group(1) if m else stem
        snaps = self.snap_pkgs()
        if candidate in snaps:
            return candidate
        # 从 Exec 行取 snap run <name>
        m2 = re.search(r"snap\s+run\s+([\w.-]+)", entry.exec_line or "")
        if m2 and m2.group(1) in snaps:
            return m2.group(1)
        # 名称归一化兜底
        for s in snaps:
            if norm_name(s) == norm_name(candidate):
                return s
        return candidate if snaps else ""

    def _match_named(self, name: str) -> Tuple[str, str, str]:
        """在 brew / nix / guix / pip / npm / cargo / gem / conda 中按名称匹配。"""
        target = norm_name(name)
        if not target:
            return "", "", ""
        for src, items in (
            (SRC_NPM, self.npm_pkgs()),
            (SRC_PIP, self.pip_pkgs()),
            (SRC_CARGO, self.cargo_pkgs()),
            (SRC_GEM, self.gem_pkgs()),
            (SRC_CONDA, self.conda_pkgs()),
        ):
            for key in items:
                if norm_name(key.split("/")[-1]) == target:
                    ver = items[key] if isinstance(items, dict) else ""
                    return src, key, ver or ""
        formula, cask = self.brew_pkgs()
        for key in formula:
            if norm_name(key) == target:
                return SRC_HOMEBREW, key, ""
        for key in cask:
            if norm_name(key) == target:
                return SRC_HOMEBREW, key, ""
        for key in self.nix_pkgs():
            if norm_name(key) == target:
                return SRC_NIX, key, ""
        for key in self.guix_pkgs():
            if norm_name(key) == target:
                return SRC_GUIX, key, ""
        return "", "", ""

    # ------------------------------------------------ 主检测流程
    def detect(self, entry: AppEntry) -> AppEntry:
        """按优先级检测一个 .desktop 条目的安装来源，就地填充并返回。"""
        # 1) Flatpak
        flat_id = (entry.extra or {}).get("X-Flatpak", "").strip()
        if not flat_id:
            m = re.search(r"flatpak\s+run\s+([\w.-]+)", entry.exec_line or "")
            flat_id = m.group(1) if m else ""
        if flat_id:
            entry.source = SRC_FLATPAK
            entry.pkg_id = flat_id
            apps = self.flatpak_pkgs()
            if flat_id in apps:
                entry.version = apps[flat_id][1]
            return entry

        # 2) Snap
        if (entry.source == SRC_SNAP or "/var/lib/snapd/" in (entry.desktop_path or "")
                or "snap run" in (entry.exec_line or "")):
            snap_name = self.snap_name_from_desktop(entry)
            if snap_name:
                entry.source = SRC_SNAP
                entry.pkg_id = snap_name
                entry.version = self.snap_pkgs().get(snap_name, "")
                return entry

        # 3) APT：先看 .desktop 文件归属
        if not self.dpkg.ready:
            self.dpkg.build()
        if entry.desktop_path:
            pkg = self.dpkg.lookup_file(os.path.abspath(entry.desktop_path))
            if pkg:
                entry.source = SRC_APT
                entry.pkg_id = pkg
                entry.version = self.apt_version(pkg)
                return entry

        # 4) 解析可执行文件路径（可能多个候选：原始路径、realpath、which 结果）
        binary = (entry.binary_path or "").strip()
        candidates: List[str] = []
        if binary:
            if os.path.isabs(binary):
                candidates.append(binary)
                if os.path.exists(binary):
                    candidates.append(os.path.realpath(binary))
            else:
                found = shutil.which(binary, path=_env()["PATH"])
                if found:
                    candidates.append(os.path.realpath(found))
        if not candidates:
            try_exec = (entry.extra or {}).get("TryExec", "").strip()
            if try_exec:
                found = shutil.which(try_exec, path=_env()["PATH"])
                if found:
                    candidates.append(os.path.realpath(found))
        bin_path = candidates[0] if candidates else binary
        entry.binary_path = bin_path or binary

        if bin_path:
            # 4a) 可执行文件是否属于某个 Debian 包
            for cand in candidates:
                pkg = self.dpkg.lookup_file(os.path.abspath(cand))
                if pkg:
                    entry.source = SRC_APT
                    entry.pkg_id = pkg
                    entry.version = self.apt_version(pkg)
                    return entry
            # 4b) 按路径位置判断
            base = os.path.basename(bin_path)
            if bin_path.startswith(os.path.join(HOME, "go", "bin")):
                entry.source = SRC_GO
                entry.pkg_id = base
                return entry
            if bin_path.startswith(os.path.join(HOME, ".cargo", "bin")):
                cargo_set = self.cargo_pkgs()
                for c in cargo_set:
                    if norm_name(c) == norm_name(base):
                        entry.source = SRC_CARGO
                        entry.pkg_id = c
                        return entry
            if bin_path.startswith(os.path.join(HOME, ".local", "bin")):
                src, pkg_id, ver = self._match_named(base)
                if src:
                    entry.source = src
                    entry.pkg_id = pkg_id
                    entry.version = ver
                    return entry
                entry.source = SRC_SCRIPT
                entry.pkg_id = bin_path
                return entry
            if bin_path.startswith("/usr/local/"):
                entry.source = SRC_SOURCE
                entry.pkg_id = base
                entry.extra["bin_path"] = bin_path
                return entry
            if bin_path.startswith("/opt/"):
                opt = self.opt_dirs()
                for name, path in opt.items():
                    if bin_path.startswith(path + os.sep) or norm_name(name) == norm_name(base):
                        entry.source = SRC_SCRIPT
                        entry.pkg_id = path
                        entry.extra["opt_dir"] = path
                        return entry

        # 5) 按名称在各包管理器中匹配
        for probe in (entry.name, os.path.basename(entry.desktop_path or "")[:-8],
                      os.path.basename(entry.binary_path or "")):
            if not probe:
                continue
            src, pkg_id, ver = self._match_named(probe)
            if src:
                entry.source = src
                entry.pkg_id = pkg_id
                entry.version = ver or entry.version
                return entry

        # 6) AppImage 匹配
        for path in self.appimages():
            stem = os.path.basename(path)
            for ext in (".AppImage", ".appimage"):
                if stem.endswith(ext):
                    stem = stem[: -len(ext)]
            if norm_name(stem) == norm_name(entry.name) or norm_name(stem) in norm_name(entry.name):
                entry.source = SRC_APPIMAGE
                entry.pkg_id = path
                return entry

        # 7) 本地快捷方式
        if entry.desktop_path and is_user_shortcut(entry.desktop_path):
            entry.source = SRC_LOCAL
            entry.pkg_id = os.path.basename(entry.desktop_path)
            return entry

        entry.source = SRC_UNKNOWN
        if not entry.pkg_id:
            entry.pkg_id = os.path.basename(entry.desktop_path or "") or entry.name
        return entry

    def detect_all(self, entries: List[AppEntry],
                   progress: Optional[Callable[[int, int], None]] = None) -> List[AppEntry]:
        total = len(entries)
        for i, entry in enumerate(entries, 1):
            try:
                self.detect(entry)
            except Exception:
                entry.source = SRC_UNKNOWN
            if progress and (i % 10 == 0 or i == total):
                progress(i, total)
        return entries

    # ------------------------------------------------ 命令行工具列表
    def cli_entries(self) -> List[AppEntry]:
        """汇总各包管理器的非 .desktop 安装项。"""
        out: List[AppEntry] = []

        def add(source: str, name: str, pkg_id: str, version: str = "", desc: str = "") -> None:
            out.append(
                AppEntry(
                    key=f"{source}:{pkg_id}:{name}",
                    name=name,
                    source=source,
                    pkg_id=pkg_id,
                    version=version,
                    comment=desc,
                    is_cli=True,
                )
            )

        for name, ver in sorted(self.pip_pkgs().items()):
            add(SRC_PIP, name, name, ver, "Python 包")
        for name, ver in sorted(self.npm_pkgs().items()):
            add(SRC_NPM, name, name, ver, "Node.js 全局包")
        for name in sorted(self.cargo_pkgs()):
            add(SRC_CARGO, name, name, "", "Rust crate")
        for name, path in sorted(self.go_bins().items()):
            add(SRC_GO, name, path, "", f"Go 二进制：{path}")
        for name in sorted(self.gem_pkgs()):
            add(SRC_GEM, name, name, "", "Ruby gem")
        for name, ver in sorted(self.conda_pkgs().items()):
            add(SRC_CONDA, name, name, ver, "conda 包")
        formula, cask = self.brew_pkgs()
        for name in sorted(formula):
            add(SRC_HOMEBREW, name, name, "", "Homebrew formula")
        for name in sorted(cask):
            add(SRC_HOMEBREW, name, name, "", "Homebrew cask")
        for name in sorted(self.nix_pkgs()):
            add(SRC_NIX, name, name, "", "Nix 包")
        for name in sorted(self.guix_pkgs()):
            add(SRC_GUIX, name, name, "", "Guix 包")
        for name in sorted(self.distrobox_list()):
            add(SRC_DISTROBOX, name, name, "", "Distrobox 容器")
        for kind, ident, label in self.docker_items():
            add(SRC_DOCKER, label, f"{kind}:{ident}", "", f"Docker {kind}")
        for path in self.appimages():
            add(SRC_APPIMAGE, os.path.basename(path), path, "", "AppImage 单文件应用")
        for name, path in sorted(self.usr_local_bins().items()):
            add(SRC_SOURCE, name, path, "", "源码编译（/usr/local/bin）")
        for name, path in sorted(self.local_bins().items()):
            if not any(e.pkg_id == path for e in out):
                add(SRC_SCRIPT, name, path, "", "用户脚本（~/.local/bin）")
        for name, path in sorted(self.opt_dirs().items()):
            add(SRC_SCRIPT, name, path, "", "脚本安装（/opt）")
        return out


def scan_apps(detector: Optional[Detector] = None,
              progress: Optional[Callable[[int, int], None]] = None) -> List[AppEntry]:
    """扫描 .desktop 应用并检测来源。"""
    det = detector or Detector()
    entries = scan_desktop_entries()
    return det.detect_all(entries, progress)
