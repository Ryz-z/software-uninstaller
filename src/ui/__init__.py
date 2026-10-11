"""界面层：优先 GTK4（PyGObject），不可用时回退 Tkinter。"""

from typing import List

BACKEND_GTK = "gtk"
BACKEND_TK = "tk"


def pick_backend() -> str:
    try:
        import gi  # noqa: F401

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk  # noqa: F401

        return BACKEND_GTK
    except Exception:
        try:
            import tkinter  # noqa: F401

            return BACKEND_TK
        except Exception:
            return ""


def run_ui(args: List[str]) -> int:
    backend = pick_backend()
    if backend == BACKEND_GTK:
        try:
            from .gtk_ui import main as gtk_main

            return gtk_main(args)
        except Exception as exc:  # 无显示环境 / GTK 初始化失败时回退
            print(f"GTK 界面启动失败（{exc}），正在尝试 Tkinter…")
    if backend in (BACKEND_GTK, BACKEND_TK):
        try:
            from .tk_ui import main as tk_main

            return tk_main(args)
        except Exception as exc:
            print(f"Tkinter 界面启动失败：{exc}")
    raise SystemExit(
        "未找到可用的图形界面后端。\n"
        "请安装 GTK4 支持：sudo apt install python3-gi gir1.2-gtk-4.0\n"
        "或安装 Tkinter 支持：sudo apt install python3-tk"
    )
