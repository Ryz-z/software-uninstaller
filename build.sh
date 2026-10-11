#!/usr/bin/env bash
# 构建 software-uninstaller 的 .deb 安装包
#
# 用法：
#   ./build.sh              # 自动：有 debhelper 用 dpkg-buildpackage，否则用 dpkg-deb 直构
#   ./build.sh dpkg-deb     # 强制使用 dpkg-deb 手工构建（无需 debhelper）
#   ./build.sh debhelper    # 强制使用 dpkg-buildpackage
#   ./build.sh clean        # 清理构建产物
#
# 环境变量：
#   VERSION=1.0.0           版本号
#   OUT_DIR=/path/to/output .deb 输出目录（默认：/home/flower/Wbwork/5/第一版）
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 版本号以 debian/changelog 首行为准，可用 VERSION= 覆盖
VERSION="${VERSION:-$(sed -n '1s/^[^ ]* (\([^)]*\)).*/\1/p' "$PROJECT_DIR/debian/changelog")}"
VERSION="${VERSION:-1.0.0}"
OUT_DIR="${OUT_DIR:-/home/flower/Wbwork/5/第一版}"
BUILD_DIR="${BUILD_DIR:-$PROJECT_DIR/build}"
PKG_NAME="software-uninstaller"
ARCH="all"
DEB_NAME="${PKG_NAME}_${VERSION}_${ARCH}.deb"
MODE="${1:-auto}"

log() { printf '\033[1;34m[build]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; }

clean() {
    rm -rf "$BUILD_DIR"
    rm -f "$PROJECT_DIR"/../${PKG_NAME}_*.deb "$PROJECT_DIR"/../${PKG_NAME}_*.dsc \
          "$PROJECT_DIR"/../${PKG_NAME}_*.tar.* "$PROJECT_DIR"/../${PKG_NAME}_*.buildinfo \
          "$PROJECT_DIR"/../${PKG_NAME}_*.changes 2>/dev/null || true
    rm -rf "$PROJECT_DIR/debian/.debhelper" "$PROJECT_DIR/debian/${PKG_NAME}" \
           "$PROJECT_DIR/debian/files" "$PROJECT_DIR/debian/${PKG_NAME}.substvars" 2>/dev/null || true
    log "已清理构建产物"
}

# ---------------------------------------------------------------- 手工构建
build_manual() {
    log "使用 dpkg-deb 手工构建（无需 debhelper）"
    local root="$BUILD_DIR/root"
    rm -rf "$BUILD_DIR"
    mkdir -p "$root/DEBIAN"

    # 1. 控制文件（去掉 debhelper 变量占位）
    sed -e 's/, *\${misc:Depends}//' -e 's/\${misc:Depends}, *//' \
        "$PROJECT_DIR/debian/control" > "$root/DEBIAN/control"
    # 手工构建不含 Build-Depends / Source 段
    VERSION="$VERSION" python3 - "$root/DEBIAN/control" <<'PY'
import os
import sys

path = sys.argv[1]
version = os.environ.get("VERSION", "1.0.0")
lines = open(path, encoding="utf-8").read().splitlines()

# 从 debian/control 提取源段落里的 Maintainer
maintainer = ""
for line in lines:
    if line.startswith("Maintainer:"):
        maintainer = line.split(":", 1)[1].strip()
        break
maintainer = maintainer or "Zhang <a672377745@gmail.com>"

# 只保留二进制包段落（从 "Package:" 开始），并补上 Version / Maintainer
start = next((i for i, l in enumerate(lines) if l.startswith("Package:")), 0)
out = []
for line in lines[start:]:
    if line.startswith(("Source:", "Build-Depends:", "Standards-Version:",
                        "Rules-Requires-Root:", "Maintainer:")):
        continue
    out.append(line)
    if line.startswith("Package:"):
        out.append("Version: " + version)
        out.append("Maintainer: " + maintainer)
open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
PY

    # 2. 维护脚本
    install -m 0755 "$PROJECT_DIR/debian/postinst" "$root/DEBIAN/postinst"

    # 3. 程序文件
    install -d "$root/usr/lib/$PKG_NAME/src"
    cp -a "$PROJECT_DIR/src/." "$root/usr/lib/$PKG_NAME/src/"
    find "$root/usr/lib/$PKG_NAME/src" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
    find "$root/usr/lib/$PKG_NAME/src" -name '*.pyc' -delete 2>/dev/null || true
    install -d "$root/usr/bin"
    install -m 0755 "$PROJECT_DIR/bin/$PKG_NAME" "$root/usr/bin/$PKG_NAME"

    # 4. 桌面文件 / 图标 / polkit 策略
    install -D -m 0644 "$PROJECT_DIR/data/$PKG_NAME.desktop" \
        "$root/usr/share/applications/$PKG_NAME.desktop"
    install -D -m 0644 "$PROJECT_DIR/data/icons/hicolor/scalable/apps/$PKG_NAME.svg" \
        "$root/usr/share/icons/hicolor/scalable/apps/$PKG_NAME.svg"
    install -D -m 0644 "$PROJECT_DIR/data/$PKG_NAME.policy" \
        "$root/usr/share/polkit-1/actions/$PKG_NAME.policy"

    # 5. 权限
    chmod 0755 "$root/usr/lib/$PKG_NAME/src/main.py" "$root/usr/lib/$PKG_NAME/src/privileged-helper"
    find "$root/usr/lib/$PKG_NAME/src" -name '*.py' -exec chmod 0644 {} +

    # 6. 打包
    mkdir -p "$OUT_DIR"
    dpkg-deb --build --root-owner-group "$root" "$OUT_DIR/$DEB_NAME"
    log "已生成：$OUT_DIR/$DEB_NAME"
}

# ---------------------------------------------------------------- debhelper
build_debhelper() {
    log "使用 dpkg-buildpackage 构建"
    cd "$PROJECT_DIR"
    dpkg-buildpackage -us -uc -b
    mkdir -p "$OUT_DIR"
    mv -f "$PROJECT_DIR"/../${PKG_NAME}_*.deb "$OUT_DIR/"
    log "已生成：$OUT_DIR/$DEB_NAME"
}

case "$MODE" in
    clean)
        clean
        exit 0
        ;;
    debhelper)
        build_debhelper
        ;;
    dpkg-deb|manual)
        build_manual
        ;;
    auto)
        if command -v dh >/dev/null 2>&1 && [ -d /usr/share/debhelper ]; then
            build_debhelper || { err "dpkg-buildpackage 失败，回退到 dpkg-deb"; build_manual; }
        else
            log "未检测到 debhelper（dh），改用 dpkg-deb 手工构建"
            build_manual
        fi
        ;;
    *)
        err "未知模式：$MODE"
        echo "用法：$0 [auto|dpkg-deb|debhelper|clean]"
        exit 2
        ;;
esac

# ---------------------------------------------------------------- 校验
DEB="$OUT_DIR/$DEB_NAME"
if [ -f "$DEB" ]; then
    log "包信息："
    dpkg-deb -I "$DEB" | sed -n '1,20p'
    log "文件清单："
    dpkg-deb -c "$DEB" | awk '{print "  " $6 " " $7 " " $8}'
    log "构建完成 ✔"
else
    err "未找到生成的 .deb：$DEB"
    exit 1
fi
