#!/usr/bin/python3
"""自检脚本：覆盖各来源检测、命令生成、安全策略与路径白名单。

运行：python3 tests/test_all.py
不需要 root，也不需要真实安装各类包管理器（用桩数据模拟）。
"""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
sys.path.insert(0, SRC)

from core.detectors import Detector, DpkgIndex  # noqa: E402
from core.models import AppEntry  # noqa: E402
from core import uninstall as U  # noqa: E402
from core.validation import path_allowed_for_rm, valid_name  # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ✔ {name}")
    else:
        FAIL += 1
        print(f"  ✘ {name}\n      得到: {got!r}\n      期望: {want!r}")


def check_true(name: str, got) -> None:
    check(name, bool(got), True)


# ---------------------------------------------------------------- 1. 来源检测
print("\n[1] 安装来源检测（使用桩数据模拟各包管理器）")

det = Detector()
# 注入桩数据
det._cache = {
    "snap": {"firefox": "132.0", "code": "1.90"},
    "flatpak": {"org.gimp.GIMP": ("GIMP", "3.0")},
    "brew": ({"wget"}, {"google-chrome"}),
    "nix": {"hello-2.12.1"},
    "guix": {"emacs"},
    "pip": {"requests": "2.32.3"},
    "npm": {"yarn": "1.22.22"},
    "cargo": {"ripgrep"},
    "gem": {"jekyll"},
    "conda": {"numpy": "1.26"},
    "distrobox": ["arch-box"],
    "docker": [("image", "abc123", "ubuntu:24.04"), ("container", "def456", "web")],
    "appimage": [os.path.expanduser("~/Applications/BalenaEtcher.AppImage")],
    "opt": {"SomeApp": "/opt/SomeApp"},
}

# 1.1 Flatpak（X-Flatpak）
e = AppEntry(key="k", name="GIMP", desktop_path="/var/lib/flatpak/exports/share/applications/org.gimp.GIMP.desktop",
             exec_line="flatpak run org.gimp.GIMP", extra={"X-Flatpak": "org.gimp.GIMP"})
det.detect(e)
check("Flatpak 来源", (e.source, e.pkg_id, e.version), ("flatpak", "org.gimp.GIMP", "3.0"))

# 1.2 Snap（桌面文件位于 snapd 目录）
e = AppEntry(key="k", name="Firefox", source="snap",
             desktop_path="/var/lib/snapd/desktop/applications/firefox_firefox.desktop",
             exec_line="env BAMF_DESKTOP_FILE_HINT=/var/lib/snapd/... firefox")
det.detect(e)
check("Snap 来源", (e.source, e.pkg_id, e.version), ("snap", "firefox", "132.0"))

# 1.3 Snap（Exec 含 snap run）
e = AppEntry(key="k", name="VS Code", desktop_path="/usr/share/applications/code.desktop",
             exec_line="snap run code")
det.detect(e)
check("Snap（Exec 含 snap run）", (e.source, e.pkg_id), ("snap", "code"))

# 1.4 APT（通过 dpkg 索引命中 .desktop 文件）
tmp = tempfile.mkdtemp(prefix="su-dpkg-")
info = os.path.join(tmp, "info")
os.makedirs(info)
with open(os.path.join(info, "myapp.list"), "w", encoding="utf-8") as fh:
    fh.write("/usr/bin\n/usr/bin/myapp\n/usr/share/applications/myapp.desktop\n")
idx = DpkgIndex(info_dir=info)
idx.build()
check_true("dpkg 索引构建", idx.ready)
det2 = Detector()
det2.dpkg = idx
e = AppEntry(key="k", name="My App", desktop_path="/usr/share/applications/myapp.desktop",
             exec_line="myapp")
det2.detect(e)
check("APT 来源（.desktop 命中）", (e.source, e.pkg_id), ("apt", "myapp"))

# 1.5 APT（通过可执行文件命中）
e = AppEntry(key="k", name="My App 2", desktop_path="/usr/share/applications/myapp2.desktop",
             binary_path="/usr/bin/myapp", exec_line="myapp")
det2.detect(e)
check("APT 来源（可执行文件命中）", (e.source, e.pkg_id), ("apt", "myapp"))

# 1.6 本地快捷方式（无归属）
home_desktop = os.path.expanduser("~/.local/share/applications/zz-local.desktop")
e = AppEntry(key="k", name="本地快捷方式", desktop_path=home_desktop)
det2.detect(e)
check("本地快捷方式", e.source, "local-shortcut")

# 1.7 名称匹配类来源
for name, expect_src, expect_id in (
    ("yarn", "npm", "yarn"),
    ("requests", "pip", "requests"),
    ("ripgrep", "cargo", "ripgrep"),
    ("jekyll", "gem", "jekyll"),
    ("numpy", "conda", "numpy"),
    ("wget", "brew", "wget"),
    ("emacs", "guix", "emacs"),
):
    e = AppEntry(key="k", name=name, desktop_path="/usr/share/applications/%s.desktop" % name)
    det.detect(e)
    check(f"名称匹配 {name}", (e.source, e.pkg_id), (expect_src, expect_id))

# 1.8 Go 二进制
gobin = os.path.expanduser("~/go/bin/mytool")
os.makedirs(os.path.dirname(gobin), exist_ok=True)
with open(gobin, "w") as fh:
    fh.write("#!/bin/sh\n")
os.chmod(gobin, 0o755)
e = AppEntry(key="k", name="mytool", binary_path=gobin, exec_line="mytool")
det.detect(e)
check("Go 二进制", e.source, "go")

# 1.9 源码编译（/usr/local/bin）
e = AppEntry(key="k", name="myprog", binary_path="/usr/local/bin/myprog", exec_line="myprog")
det.detect(e)
check("源码编译", e.source, "source")

# 1.10 AppImage
e = AppEntry(key="k", name="BalenaEtcher")
det.detect(e)
check("AppImage", e.source, "appimage")

shutil.rmtree(tmp, ignore_errors=True)
os.remove(gobin)

# ---------------------------------------------------------------- 2. 命令生成
print("\n[2] 卸载命令生成")

cases = [
    ("apt", "vlc", [], ["apt-get purge -y vlc"]),
    ("snap", "firefox", [], ["snap remove --purge firefox"]),
    ("flatpak", "org.gimp.GIMP", [], ["flatpak uninstall --delete-data -y org.gimp.GIMP"]),
    ("npm", "@vue/cli", [], ["npm uninstall -g @vue/cli"]),
    ("cargo", "ripgrep", [], ["cargo uninstall ripgrep"]),
    ("gem", "jekyll", [], ["gem uninstall -x -I --force jekyll"]),
    ("conda", "numpy", [], ["conda remove -y numpy"]),
    ("brew", "wget", [], ["brew uninstall wget"]),
    ("nix", "hello", [], ["nix-env --uninstall hello"]),
    ("guix", "emacs", [], ["guix remove emacs"]),
    ("distrobox", "arch-box", [], ["distrobox-rm --force arch-box"]),
    ("docker", "image:abc123", [], ["docker rmi -f abc123"]),
    ("docker", "container:def456", [], ["docker rm -f def456"]),
]
for src, pid, _extra, want_cmds in cases:
    e = AppEntry(key="k", name=pid, source=src, pkg_id=pid)
    plan = U.build_plan(e)
    got = [" ".join(c.argv) for c in plan.commands]
    check(f"{src} 命令", got, want_cmds)

# pip
e = AppEntry(key="k", name="requests", source="pip", pkg_id="requests")
plan = U.build_plan(e)
check_true("pip 命令", any(c.argv[0] in ("pip3", "pip") and c.argv[-1] == "requests" for c in plan.commands))

# apt + autoremove
e = AppEntry(key="k", name="vlc", source="apt", pkg_id="vlc")
plan = U.build_plan(e, autoremove=True)
check("apt + autoremove", [" ".join(c.argv) for c in plan.commands],
      ["apt-get purge -y vlc", "apt-get autoremove --purge -y"])

# AppImage / go / 本地快捷方式（rm）
appimage = os.path.expanduser("~/Applications/Foo.AppImage")
e = AppEntry(key="k", name="Foo", source="appimage", pkg_id=appimage)
plan = U.build_plan(e)
check("AppImage 命令", [c.argv for c in plan.commands], [["rm", "-f", appimage]])
check("AppImage 无需 root", plan.commands[0].needs_root, False)

e = AppEntry(key="k", name="tool", source="go", pkg_id=os.path.expanduser("~/go/bin/tool"))
check("Go 删除命令", [" ".join(c.argv) for c in U.build_plan(e).commands],
      ["rm -f " + os.path.expanduser("~/go/bin/tool")])

e = AppEntry(key="k", name="local", source="local-shortcut",
             pkg_id="x.desktop", desktop_path=home_desktop)
check("本地快捷方式命令", [" ".join(c.argv) for c in U.build_plan(e).commands],
      ["rm -f " + home_desktop])

# 脚本安装：找到卸载脚本
optdir = tempfile.mkdtemp(prefix="su-opt-")
os.makedirs(os.path.join(optdir, "SomeApp"))
uscript = os.path.join(optdir, "SomeApp", "uninstall.sh")
open(uscript, "w").write("#!/bin/sh\necho bye\n")
e = AppEntry(key="k", name="SomeApp", source="script", pkg_id=os.path.join(optdir, "SomeApp"))
check("脚本安装（有卸载脚本）", [" ".join(c.argv) for c in U.build_plan(e).commands],
      ["bash " + uscript])
shutil.rmtree(optdir, ignore_errors=True)

# ---------------------------------------------------------------- 3. 安全策略
print("\n[3] 安全策略")

for pkg in ("ubuntu-desktop", "gnome-shell", "systemd", "apt", "dpkg", "snapd",
            "linux-image-6.8.0-31-generic", "linux-headers-6.8.0-31"):
    e = AppEntry(key="k", name=pkg, source="apt", pkg_id=pkg)
    plan = U.build_plan(e)
    check(f"关键包禁止卸载：{pkg}", (plan.allowed, bool(plan.commands)), (False, False))

e = AppEntry(key="k", name="core20", source="snap", pkg_id="core20")
check("Snap 核心禁止卸载", U.build_plan(e).allowed, False)

e = AppEntry(key="k", name="Platform", source="flatpak", pkg_id="org.gnome.Platform")
check("Flatpak 运行时禁止卸载", U.build_plan(e).allowed, False)

for bad in ("evil; rm -rf /", "a b c", "$(whoami)", "../../etc", ""):
    e = AppEntry(key="k", name="bad", source="apt", pkg_id=bad)
    check(f"非法包名拒绝：{bad!r}", U.build_plan(e).allowed, False)

e = AppEntry(key="k", name="x", source="appimage", pkg_id="/usr/bin/important")
check("rm 越界路径拒绝", U.build_plan(e).allowed, False)

e = AppEntry(key="k", name="unknown app", source="unknown")
check("未识别来源拒绝", U.build_plan(e).allowed, False)

check("Firefox 允许卸载（仅警告）", U.build_plan(
    AppEntry(key="k", name="firefox", source="apt", pkg_id="firefox")).allowed, True)

# ---------------------------------------------------------------- 4. 校验工具
print("\n[4] 名称与路径校验")

check("Debian 包名合法", valid_name("apt", "libreoffice-calc"), True)
check("Debian 包名非法", valid_name("apt", "libre office"), False)
check("npm scope 包名", valid_name("npm", "@vue/cli"), True)
check("Flatpak ID", valid_name("flatpak", "org.gimp.GIMP"), True)
check("Shell 注入被拒", valid_name("pip", "requests; rm -rf /"), False)

check("/opt 允许删除", path_allowed_for_rm("/opt/SomeApp"), True)
check("~/Applications 允许删除", path_allowed_for_rm(os.path.expanduser("~/Applications/a.AppImage")), True)
check("~/go/bin 允许删除", path_allowed_for_rm(os.path.expanduser("~/go/bin/x")), True)
check("/usr/bin 禁止删除", path_allowed_for_rm("/usr/bin/foo"), False)
check("/etc 禁止删除", path_allowed_for_rm("/etc/passwd"), False)
check("/var 禁止删除", path_allowed_for_rm("/var/lib/foo"), False)
check("/usr/share 禁止删除", path_allowed_for_rm("/usr/share/applications/a.desktop"), False)
check("路径穿越被拒", path_allowed_for_rm("/opt/../../etc/passwd"), False)

# ---------------------------------------------------------------- 5. 提权助手
print("\n[5] 提权助手白名单（root 侧二次校验）")

HELPER = os.path.join(SRC, "privileged-helper")
if os.path.exists(HELPER):
    import subprocess

    def helper(args):
        p = subprocess.run(["/usr/bin/python3", HELPER] + args,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return p.returncode, (p.stdout + p.stderr).strip()

    code, out = helper(["rm", "-f", "/etc/passwd"])
    check_true("助手拒绝删除 /etc/passwd", code == 2 and "拒绝执行" in out)
    code, out = helper(["apt-get", "purge", "-y", "gnome-shell"])
    check_true("助手拒绝卸载关键包", code == 2 and "关键组件" in out)
    code, out = helper(["rm", "-rf", "/"])
    check_true("助手拒绝 rm -rf /", code == 2 and "拒绝执行" in out)
    code, out = helper(["curl", "http://evil"])
    check_true("助手拒绝白名单外命令", code == 2 and "白名单" in out)

    # make -C <目录> uninstall 应放行（目录存在）
    srcdir = tempfile.mkdtemp(prefix="su-src-")
    open(os.path.join(srcdir, "Makefile"), "w").write("uninstall:\n\t@echo nope\n")
    code, out = helper(["make", "-C", srcdir, "uninstall"])
    check_true("助手允许 make -C 目录 uninstall", "拒绝执行" not in out)
    import shutil as _sh
    _sh.rmtree(srcdir, ignore_errors=True)

    # 卸载脚本：允许 /opt 下的 uninstall.sh，拒绝普通脚本
    # 用家目录下的目录代替 /opt（沙箱/权限限制下同样命中白名单规则）
    optdir = os.path.expanduser("~/.su-test-opt")
    os.makedirs(optdir, exist_ok=True)
    good = os.path.join(optdir, "uninstall.sh")
    open(good, "w").write("#!/bin/sh\necho bye\n")
    os.chmod(good, 0o755)
    code, out = helper(["bash", good])
    check_true("助手允许执行 uninstall.sh", "拒绝执行" not in out)
    bad = os.path.join(optdir, "evil.sh")
    open(bad, "w").write("#!/bin/sh\n")
    code, out = helper(["bash", bad])
    check_true("助手拒绝执行非卸载脚本", code == 2 and "拒绝执行" in out)
    _sh.rmtree(optdir, ignore_errors=True)
    # 合法命令：删除家目录内的临时文件（安全）
    probe = os.path.expanduser("~/.su-helper-probe")
    open(probe, "w").write("x")
    code, out = helper(["rm", "-f", probe])
    check("助手放行家目录内删除", (code, os.path.exists(probe)), (0, False))
else:
    print("  （未找到 privileged-helper，跳过）")

# ---------------------------------------------------------------- 6. 拼音检索
print("\n[6] 拼音检索")

from core.pinyin import search_keys, pinyin_variants, initials_variants  # noqa: E402

keys = search_keys("计算器")
check("全拼变体含 jisuanqi", "jisuanqi" in keys, True)
check("首字母变体含 jsq", "jsq" in keys, True)
check("原串保留", "计算器" in keys, True)

e = AppEntry(key="k", name="计算器", source="apt", pkg_id="gnome-calculator")
hay = e.search_haystack()
check("首字母 jsq 可搜到“计算器”", "jsq" in hay, True)
check("全拼 jisuanqi 可搜到", "jisuanqi" in hay, True)
check("部分全拼 suan 可搜到", "suan" in hay, True)
check("中文原名可搜到", "计算器" in hay, True)
check("包名可搜到", "gnome-calculator" in hay, True)

e2 = AppEntry(key="k", name="LibreOffice Calc", source="apt", pkg_id="libreoffice-calc")
check("英文名不受影响", "calc" in e2.search_haystack(), True)

check("空串安全", search_keys(""), [])
check("纯英文不展开拼音", pinyin_variants("firefox"), [])
check("首字母：音乐", "yy" in initials_variants("音乐"), True)
check("多音字变体（重）", any("chong" in v for v in pinyin_variants("重")) and
      any("zhong" in v for v in pinyin_variants("重")), True)

# ---------------------------------------------------------------- 7. autoremove 预览回归
print("\n[7] autoremove 勾选后的命令预览（回归）")

e3 = AppEntry(key="k", name="VLC", source="apt", pkg_id="vlc")
plan_off = U.build_plan(e3, False)
plan_on = U.build_plan(e3, True)
check("未勾选时预览非空", bool(plan_off.preview()), True)
check("勾选后预览非空", bool(plan_on.preview()), True)
check("勾选后包含 autoremove", "autoremove" in plan_on.preview(), True)
check("勾选后仍允许卸载", plan_on.allowed, True)
check("勾选后命令数为 2", len(plan_on.commands), 2)
check("勾选前后首条命令一致", plan_off.commands[0].argv, plan_on.commands[0].argv)

# ---------------------------------------------------------------- 汇总
print(f"\n结果：通过 {PASS} 项，失败 {FAIL} 项")
sys.exit(1 if FAIL else 0)
