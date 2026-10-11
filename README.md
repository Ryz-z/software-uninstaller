# 软件卸载工具 Software Uninstaller

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Build](https://github.com/Ryz-z/software-uninstaller/actions/workflows/ci.yml/badge.svg)]
[![Release](https://github.com/Ryz-z/software-uninstaller)]

Ubuntu 桌面上的图形化软件卸载工具：浏览 / 搜索已安装的软件，**自动识别它的安装来源**，
展示将要执行的卸载命令，二次确认后执行并输出实时日志。


## 一、功能

- **应用列表**：扫描 `/usr/share/applications`、`~/.local/share/applications`、
  `/var/lib/snapd/desktop/applications`、`/var/lib/flatpak/exports/share/applications`、
  `~/.local/share/flatpak/exports/share/applications` 下的 `.desktop` 文件，
  自动过滤 `NoDisplay=true` / `Hidden=true`，支持中文名（`Name[zh_CN]` 优先）与模糊搜索。
- **拼音搜索**：内置 8105 字离线拼音表（零第三方依赖），中文应用名可用
  全拼 `jisuanqi`、首字母 `jsq`、部分拼音 `suan` 搜到；多音字会生成多个变体。
- **命令行工具页**：汇总 pip / npm / cargo / go / gem / conda / Homebrew / Nix / Guix /
  Distrobox / Docker / AppImage / `/usr/local/bin` / `~/.local/bin` / `/opt` 的非桌面安装项。
- **来源检测（按优先级，命中即停）**：Flatpak → Snap → APT(dpkg) → 可执行文件归属 →
  Homebrew/Nix/Guix/pip/npm/cargo/gem/conda 名称匹配 → Go 二进制 → `/usr/local`(源码编译) →
  `/opt`(脚本安装) → AppImage → 本地快捷方式。
- **卸载执行**：APT `purge`、Snap `remove --purge`、Flatpak `uninstall --delete-data`、
  Homebrew / Nix / Guix / pip / npm / cargo / gem / conda / Distrobox / Docker /
  `rm`(AppImage、Go 二进制、脚本安装目录、`~/.local` 快捷方式)、`make uninstall`(源码编译)。
- **安全策略**：系统关键包默认禁止卸载、包名正则校验、`rm` 路径白名单、
  提权统一走 `pkexec` + 白名单助手（root 侧二次校验）、所有删除操作二次确认。
- **APT 模拟执行**：卸载前可先跑 `apt-get purge -s`，预览会被连带删除的包。

## 二、目录结构

```
software-uninstaller/
├── src/
│   ├── main.py                 # 入口（GUI / --list / --plan / --json）
│   ├── privileged-helper       # pkexec 提权助手（root 侧白名单二次校验）
│   ├── core/
│   │   ├── models.py           # AppEntry / UninstallPlan / Command 数据模型
│   │   ├── desktop.py          # .desktop 扫描与解析
│   │   ├── detectors.py        # 各来源检测引擎（含 dpkg 文件索引）
│   │   ├── uninstall.py        # 卸载计划生成与流式执行
│   │   ├── safety.py           # 关键包保护与风险评估
│   │   ├── validation.py       # 名称正则与路径白名单校验
│   │   ├── pinyin.py           # 拼音检索（全拼 / 首字母 / 多音字变体）
│   │   └── pinyin_data.py      # 离线汉字拼音表（自动生成，8105 字）
│   └── ui/
│       ├── gtk_ui.py           # GTK4 界面（主界面）
│       └── tk_ui.py            # Tkinter 回退界面
├── bin/software-uninstaller    # /usr/bin 启动包装脚本
├── data/
│   ├── software-uninstaller.desktop
│   ├── software-uninstaller.policy    # polkit 策略（自定义提权提示文案）
│   └── icons/hicolor/scalable/apps/software-uninstaller.svg
├── debian/                     # control / rules / changelog / install / postinst / copyright
├── tools/gen_pinyin_data.py    # 生成离线拼音表的一次性工具
├── tests/
│   ├── test_all.py             # 检测、命令生成、安全策略、助手白名单（73 项）
│   └── test_gtk_api.py         # GTK4 API 静态校验（145 项）
├── build.sh                    # 构建脚本（dpkg-deb / dpkg-buildpackage）
└── README.md
```

## 三、安装

```bash
sudo apt install ./software-uninstaller_1.0.2_all.deb
```

依赖：`python3`、`python3-gi`、`gir1.2-gtk-4.0`、`polkitd | policykit-1`。
其中 polkit 在 Ubuntu 24.10+ 上已改名为 `polkitd`（`policykit-1` 被移除），
包里用的是 `polkitd | policykit-1` 备选依赖，22.04 / 24.04 / 26.04 都能装。
可选（放 Suggests，缺了不影响安装）：`npm`、`cargo`、`python3-pip`、`rubygems`、
`distrobox`、`docker.io`、`python3-tk`；推荐 `flatpak`、`snapd`。
未安装某个包管理器时，对应来源自动跳过，不影响其他功能。

卸载本工具：`sudo apt purge software-uninstaller`

安装后在应用菜单中搜索“软件卸载工具”启动，或终端执行 `software-uninstaller`。

## 四、使用说明

1. **图形应用** 标签页：浏览 / 搜索桌面应用，每行显示名称、来源、版本。
2. **命令行工具** 标签页：pip / npm / cargo / go / gem / conda / AppImage / 脚本安装等条目。
3. 选中任一条目，右侧显示：安装来源、包名 / 应用 ID / 路径、版本、**卸载命令预览**。
4. APT 条目可勾选“同时清理不再需要的依赖（autoremove）”，勾选后命令预览会立刻同步；
   可点“模拟执行”先看会被连带删除哪些包。
5. 点“卸载此软件” → 二次确认框显示目标、完整命令与风险警告 → 确认后弹出 polkit 授权框。
6. 执行过程在“执行日志”中实时输出，完成后自动刷新列表。

关键包（如 `ubuntu-desktop`、`gnome-shell`、`systemd`、`apt`、`linux-image-*` 等）的卸载按钮默认禁用；
`/usr`、`/etc`、`/var` 等系统目录禁止删除，路径校验失败会直接拒绝。

## 五、命令行用法（无图形界面时也能用）

```bash
software-uninstaller --list              # 列出所有条目
software-uninstaller --list --no-cli     # 只看 .desktop 应用
software-uninstaller --list --json       # JSON 输出
software-uninstaller --plan 关键字        # 显示匹配条目的卸载计划
software-uninstaller --version
```

## 六、构建

```bash
./build.sh                 # 自动：有 debhelper 用 dpkg-buildpackage，否则用 dpkg-deb
./build.sh dpkg-deb        # 强制手工构建（只需要 dpkg-deb，任何 Ubuntu 都行）
./build.sh debhelper       # 强制 dpkg-buildpackage -us -uc -b
./build.sh clean
VERSION=1.1.0 OUT_DIR=/tmp ./build.sh    # 自定义版本与输出目录
```

产物默认输出到 `/home/flower/Wbwork/5/第一版/software-uninstaller_<版本>_all.deb`
（版本号取自 `debian/changelog` 首行）。

本机构建环境缺少 `debhelper`（apt 无可用软件源），因此实际使用的是
`dpkg-deb --build --root-owner-group`，安装包内容与 debhelper 构建结果一致；
在有 debhelper 的机器上可直接 `dpkg-buildpackage -us -uc -b`。

## 七、自检

```bash
python3 tests/test_all.py      # 95 项：检测 / 命令生成 / 安全策略 / 路径白名单 / 助手 / 拼音
python3 tests/test_gtk_api.py  # 147 项：语法编译、GTK4 类-方法-属性-枚举、模块导入
```

## 八、已知限制与后续改进

- GUI 未在本机实机启动验证（构建环境无 DISPLAY / 无 X 授权），已通过 GTK API 静态校验（145 项）
  与逻辑自检（73 项）覆盖；安装后首次启动如有异常请把终端输出发我。
- Flatpak / Snap 的检测依赖对应 CLI 输出格式，未来版本若格式变化需同步适配解析。
- 源码编译安装只在能找到 Makefile 时提供 `make uninstall`，否则退化为删除 `/usr/local/bin` 下的二进制，
  可能残留 man page / data 文件。
- Docker / Distrobox 需要当前用户在 `docker` 组，否则删除会失败（日志中会显示权限错误）。
- conda 多环境仅按当前环境处理，未做环境切换选择。
- 暂不支持批量卸载、卸载快照/回滚。

## 九、第三方数据

- 拼音检索使用的汉字拼音表由 `tools/gen_pinyin_data.py` 生成，
  原始数据来自 [mozillazg/pinyin-data](https://github.com/mozillazg/pinyin-data)
  的 `kTGHZ2013.txt`（《通用规范汉字表》2013）。
  **发布前请确认该项目的数据许可与 MIT 兼容**（仓库未随附原始 166KB 数据文件，
  需要时用 `tools/gen_pinyin_data.py` 重新生成即可）。
- 生成结果 `src/core/pinyin_data.py` 是派生数据文件，已随仓库提供，避免用户联网。

## 十、参与贡献

1. Fork 本仓库，新建分支：`git checkout -b feat/xxx`
2. 改动后跑自检：`python3 tests/test_all.py` 与 `python3 tests/test_gtk_api.py`
3. 提交 PR，说明改动动机与测试情况

代码约定：Python 3.8+ 语法、所有外部命令用参数列表调用（禁止 `shell=True`）、
新增来源检测要同时补充 `tests/test_all.py` 里的用例。

