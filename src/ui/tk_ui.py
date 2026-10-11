"""Tkinter 回退界面（当系统缺少 PyGObject/GTK4 时使用）。

功能与 GTK 版一致：列表 + 搜索 + 详情 + 卸载确认 + 日志。
"""

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import List, Optional

_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from core.detectors import Detector  # noqa: E402
from core.desktop import scan_desktop_entries  # noqa: E402
from core.models import AppEntry  # noqa: E402
from core.models import SRC_APT  # noqa: E402
from core import uninstall as U  # noqa: E402

TITLE = "软件卸载工具"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(TITLE)
        self.geometry("980x660")
        self.detector = Detector()
        self.entries: List[AppEntry] = []
        self.cli_entries: List[AppEntry] = []
        self.current: Optional[AppEntry] = None
        self.autoremove = tk.BooleanVar(value=False)
        self.busy = False
        self.q: "queue.Queue" = queue.Queue()
        self._build()
        self._reload()
        self._pump()

    # ------------------------------------------------------------ 界面
    def _build(self) -> None:
        top = ttk.Frame(self, padding=6)
        top.pack(fill=tk.X)
        ttk.Label(top, text="搜索：").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        ent = ttk.Entry(top, textvariable=self.search_var, width=30)
        ent.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ent.bind("<KeyRelease>", lambda _e: self._refill())
        ttk.Button(top, text="刷新", command=self._reload).pack(side=tk.RIGHT)
        self.status = ttk.Label(top, text="")
        self.status.pack(side=tk.RIGHT, padx=8)

        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        left = ttk.Frame(paned)
        self.notebook = ttk.Notebook(left)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self.list_apps = tk.Listbox(self.notebook)
        self.list_apps.pack(fill=tk.BOTH, expand=True)
        self.list_cli = tk.Listbox(self.notebook)
        self.list_cli.pack(fill=tk.BOTH, expand=True)
        self.notebook.add(self.list_apps, text="图形应用")
        self.notebook.add(self.list_cli, text="命令行工具")
        for lb in (self.list_apps, self.list_cli):
            lb.bind("<<ListboxSelect>>", self._on_select)
        paned.add(left, weight=1)

        right = ttk.Frame(paned)
        paned.add(right, weight=2)
        self.detail = tk.Text(right, height=16, wrap=tk.WORD)
        self.detail.pack(fill=tk.BOTH, expand=True)
        self.detail.configure(state=tk.DISABLED)

        opts = ttk.Frame(right)
        opts.pack(fill=tk.X, pady=4)
        self.chk_auto = ttk.Checkbutton(opts, text="同时清理不再需要的依赖（autoremove）",
                                        variable=self.autoremove,
                                        command=self._on_autoremove_toggle)
        self.chk_auto.pack(side=tk.LEFT)
        ttk.Button(opts, text="卸载此软件", command=self._on_uninstall).pack(side=tk.RIGHT)

        ttk.Label(right, text="执行日志").pack(anchor=tk.W)
        self.log = tk.Text(right, height=10, wrap=tk.WORD)
        self.log.pack(fill=tk.BOTH, expand=True)
        self.log.configure(state=tk.DISABLED)

    # ------------------------------------------------------------ 数据
    def _reload(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.status.config(text="扫描中…")

        def worker() -> None:
            det = Detector()
            apps = scan_desktop_entries()
            det.detect_all(apps)
            cli = det.cli_entries()
            self.q.put(("reload", det, apps, cli))

        threading.Thread(target=worker, daemon=True).start()

    def _pump(self) -> None:
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "reload":
                    _, det, apps, cli = item
                    self.detector = det
                    self.entries = apps
                    self.cli_entries = cli
                    self.busy = False
                    self.status.config(text=f"图形应用 {len(apps)} · 命令行 {len(cli)}")
                    self._refill()
                elif kind == "log":
                    self._append(self.log, item[1])
                elif kind == "done":
                    self.busy = False
                    self._append(self.log, "完成。")
                    if item[1]:
                        self._reload()
        except queue.Empty:
            pass
        self.after(120, self._pump)

    def _refill(self) -> None:
        text = self.search_var.get().strip().lower()
        for lb, items in ((self.list_apps, self.entries), (self.list_cli, self.cli_entries)):
            lb.delete(0, tk.END)
            shown = []
            for e in items:
                hay = e.search_haystack()
                if text and text not in hay:
                    continue
                lb.insert(tk.END, f"{e.name}  [{e.label_source()}]")
                shown.append(e)
            lb._items = shown

    def _on_select(self, event) -> None:
        widget = event.widget
        sel = widget.curselection()
        if not sel:
            return
        items = getattr(widget, "_items", [])
        if sel[0] >= len(items):
            return
        entry = items[sel[0]]
        self.current = entry
        self._show_detail(entry)

    def _on_autoremove_toggle(self) -> None:
        """勾选 autoremove 后立即刷新命令预览。"""
        if self.current is not None:
            self._show_detail(self.current)

    def _show_detail(self, entry: AppEntry) -> None:
        plan = U.build_plan(entry, self.autoremove.get())
        lines = [
            f"名称：{entry.name}",
            f"安装来源：{entry.label_source()}",
            f"包名 / 标识：{entry.pkg_id or '—'}",
            f"版本：{entry.version or '—'}",
        ]
        if entry.desktop_path:
            lines.append(f"快捷方式：{entry.desktop_path}")
        if entry.binary_path:
            lines.append(f"可执行文件：{entry.binary_path}")
        lines.append("")
        lines.append("卸载命令预览：")
        lines.append(plan.preview() or "（无法自动生成命令）")
        if not plan.allowed:
            lines.append("")
            lines.append("⛔ " + (plan.reason or "不允许卸载"))
        elif plan.warning:
            lines.append("")
            lines.append("⚠ " + plan.warning)
        self.detail.configure(state=tk.NORMAL)
        self.detail.delete("1.0", tk.END)
        self.detail.insert(tk.END, "\n".join(lines))
        self.detail.configure(state=tk.DISABLED)
        self.chk_auto.configure(state=tk.NORMAL if entry.source == SRC_APT else tk.DISABLED)

    # ------------------------------------------------------------ 卸载
    def _on_uninstall(self) -> None:
        entry = self.current
        if entry is None or self.busy:
            return
        plan = U.build_plan(entry, self.autoremove.get())
        if not plan.allowed:
            messagebox.showerror("禁止卸载", plan.reason or "该条目不允许卸载")
            return
        msg = (f"即将卸载：{entry.name}\n"
               f"来源：{entry.label_source()}\n"
               f"标识：{entry.pkg_id}\n\n"
               f"将执行：\n{plan.preview()}\n\n"
               f"警告：卸载系统关键组件可能导致系统异常。确定继续？")
        if not messagebox.askyesno("确认卸载", msg):
            return
        self.busy = True
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)

        def worker() -> None:
            results = U.execute_plan(plan, on_line=lambda line: self.q.put(("log", line)))
            ok = all(r.ok for r in results) and bool(results)
            if ok:
                U.refresh_after(plan)
            self.q.put(("done", ok))

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _append(widget: tk.Text, line: str) -> None:
        widget.configure(state=tk.NORMAL)
        widget.insert(tk.END, line + "\n")
        widget.see(tk.END)
        widget.configure(state=tk.DISABLED)


def main(args: List[str]) -> int:
    app = App()
    app.mainloop()
    return 0
