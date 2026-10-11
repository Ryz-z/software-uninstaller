#!/usr/bin/python3
"""GTK4 API 静态校验：确认用到的类、方法、属性与枚举都存在。

无需显示环境即可运行：python3 tests/test_gtk_api.py
（构建环境通常没有 DISPLAY，用它来兜住 API 拼写错误。）
"""

import os
import py_compile
import sys

PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✔ {name}")
    else:
        FAIL += 1
        print(f"  ✘ {name} {detail}")


# ---------------------------------------------------------------- 1. 语法编译
print("[1] 全部 Python 源文件语法检查")
SRC = "/home/flower/Wbwork/5/data1/software-uninstaller/src"
files = []
for root, _dirs, names in os.walk(SRC):
    for n in names:
        if n.endswith(".py") or n == "privileged-helper":
            files.append(os.path.join(root, n))
for f in sorted(files):
    try:
        py_compile.compile(f, doraise=True, cfile="/tmp/py_compile_tmp.pyc")
        check(os.path.relpath(f, SRC), True)
    except Exception as exc:
        check(os.path.relpath(f, SRC), False, str(exc))

# ---------------------------------------------------------------- 2. GTK API
print("\n[2] GTK4 类 / 方法 / 属性 / 枚举检查")
try:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, GLib, Gtk, Pango
    gtk_available = True
except Exception as exc:
    print(f"  （跳过：GTK4 不可用 —— {exc}）")
    gtk_available = False

if gtk_available:
    classes = ["Application", "ApplicationWindow", "Window", "Box", "Button", "Label",
               "Entry", "Grid", "ListBox", "ListBoxRow", "ScrolledWindow", "TextView",
               "Frame", "Expander", "CheckButton", "ProgressBar", "Spinner",
               "HeaderBar", "Paned", "Stack", "StackSwitcher", "Image", "MenuButton",
               "TextBuffer"]
    for c in classes:
        check(f"Gtk.{c}", hasattr(Gtk, c))

    methods = {
        "Box": ["append", "remove", "get_first_child"],
        "ListBox": ["append", "remove", "get_row_at_index", "select_row", "set_selection_mode"],
        "ListBoxRow": ["set_child", "get_child"],
        "ScrolledWindow": ["set_child", "set_min_content_height", "set_policy"],
        "TextView": ["set_editable", "set_monospace", "set_wrap_mode", "get_buffer",
                     "scroll_to_mark"],
        "Label": ["set_xalign", "set_wrap", "set_markup", "set_ellipsize",
                  "set_max_width_chars", "set_selectable", "add_css_class"],
        "Image": ["set_from_file", "set_from_icon_name", "set_pixel_size"],
        "Window": ["set_child", "set_titlebar", "set_default_size", "present", "destroy"],
        "ApplicationWindow": ["set_titlebar"],
        "HeaderBar": ["pack_start", "pack_end"],
        "Stack": ["add_titled"],
        "StackSwitcher": ["set_stack"],
        "Paned": ["set_start_child", "set_end_child", "set_position"],
        "Expander": ["set_child", "set_expanded"],
        "Frame": ["set_child"],
        "ProgressBar": ["set_show_text", "set_text", "pulse", "set_visible"],
        "Spinner": ["start", "stop"],
        "TextBuffer": ["set_text", "insert", "get_end_iter", "create_mark"],
        "CheckButton": ["set_active", "get_active"],
        "Button": ["set_sensitive", "add_css_class"],
        "Application": ["add_action", "run"],
    }
    for cls, meths in methods.items():
        klass = getattr(Gtk, cls, None)
        for m in meths:
            check(f"Gtk.{cls}.{m}", klass is not None and hasattr(klass, m))

    props = {
        "Box": ["orientation", "spacing"],
        "Label": ["label", "wrap", "xalign"],
        "Button": ["label"],
        "Entry": ["placeholder-text"],
        "Expander": ["label", "expanded"],
        "CheckButton": ["label", "active"],
        "MenuButton": ["icon-name", "menu-model"],
        "TextView": ["editable", "monospace", "wrap-mode"],
        "ProgressBar": ["show-text", "text", "visible"],
        "Paned": ["orientation", "position"],
        "ApplicationWindow": ["application", "title"],
        "Window": ["title", "transient-for", "modal"],
        "Application": ["application-id"],
    }
    for cls, names in props.items():
        klass = getattr(Gtk, cls, None)
        if klass is None:
            continue
        have = {p.name for p in klass.list_properties()} if hasattr(klass, "list_properties") else set()
        for n in names:
            check(f"{cls} 属性 {n}", n in have, f"（可用：{sorted(have)[:8]}…）")

    enums = [
        ("Orientation", "HORIZONTAL"), ("Orientation", "VERTICAL"),
        ("WrapMode", "WORD_CHAR"), ("Align", "END"), ("Align", "START"),
        ("SelectionMode", "SINGLE"), ("StackTransitionType", "CROSSFADE"),
        ("PolicyType", "AUTOMATIC"), ("Justification", "LEFT"),
    ]
    for enum, val in enums:
        check(f"Gtk.{enum}.{val}", hasattr(getattr(Gtk, enum, object), val))

    check("Gio.Menu", hasattr(Gio, "Menu"))
    check("Gio.SimpleAction", hasattr(Gio, "SimpleAction"))
    check("GLib.idle_add", hasattr(GLib, "idle_add"))
    check("GLib.markup_escape_text", hasattr(GLib, "markup_escape_text"))
    check("Pango.EllipsizeMode", hasattr(Pango, "EllipsizeMode"))
    check("Gtk.Image.new_from_icon_name", hasattr(Gtk.Image, "new_from_icon_name"))

# ---------------------------------------------------------------- 3. 模块导入
print("\n[3] 模块导入（不需要显示）")
sys.path.insert(0, SRC)
for mod in ("core.models", "core.validation", "core.safety", "core.desktop",
            "core.detectors", "core.uninstall"):
    try:
        __import__(mod)
        check(mod, True)
    except Exception as exc:
        check(mod, False, str(exc))

if gtk_available:
    try:
        import ui.gtk_ui  # noqa: F401
        check("ui.gtk_ui 导入", True)
    except Exception as exc:
        check("ui.gtk_ui 导入", False, str(exc))

print(f"\n结果：通过 {PASS} 项，失败 {FAIL} 项")
sys.exit(1 if FAIL else 0)
