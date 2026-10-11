"""GTK4 图形界面。

布局：左侧应用列表（图形应用 / 命令行工具两个标签页）+ 右侧详情面板 + 底部执行日志。
所有耗时操作（扫描、检测、卸载）都在后台线程执行，通过 GLib.idle_add 回到主线程更新界面。
"""

import os
import sys
import threading
from typing import Callable, List, Optional

# 保证 core 包可被导入（无论从 /usr/bin 包装脚本启动还是从源码目录启动）
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

from core.detectors import Detector  # noqa: E402
from core.desktop import scan_desktop_entries  # noqa: E402
from core.models import AppEntry, APP_VERSION, UninstallPlan  # noqa: E402
from core.models import SRC_APT, SRC_UNKNOWN  # noqa: E402
from core import uninstall as U  # noqa: E402
from core.safety import is_risky_package  # noqa: E402

APP_ID = "cn.linux.software-uninstaller"
TITLE = "软件卸载工具"


# ------------------------------------------------------------------ 行控件
class AppRow(Gtk.Box):
    """列表中的一行：图标 + 名称 + 来源标签。"""

    def __init__(self, entry: AppEntry) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.entry = entry
        self.set_margin_top(4)
        self.set_margin_bottom(4)
        self.set_margin_start(8)
        self.set_margin_end(8)

        self.image = Gtk.Image.new_from_icon_name("application-x-executable")
        self.image.set_pixel_size(32)
        self.append(self.image)
        self._load_icon(entry)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vbox.set_hexpand(True)
        name = Gtk.Label(label=entry.name)
        name.set_xalign(0.0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(34)
        vbox.append(name)

        sub = Gtk.Label(label=f"{entry.label_source()}" + (f" · {entry.version}" if entry.version else ""))
        sub.set_xalign(0.0)
        sub.add_css_class("dim-label")
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        sub.set_max_width_chars(40)
        vbox.append(sub)
        self.append(vbox)

        if entry.source == SRC_UNKNOWN:
            tag = Gtk.Label(label="未识别")
            tag.add_css_class("dim-label")
            self.append(tag)

    def _load_icon(self, entry: AppEntry) -> None:
        icon = (entry.icon or "").strip()
        if not icon:
            return
        if os.path.isabs(icon) and os.path.exists(icon):
            try:
                self.image.set_from_file(icon)
                self.image.set_pixel_size(32)
            except Exception:
                pass
        else:
            self.image.set_from_icon_name(icon)


class ListPage(Gtk.Box):
    """一个标签页：搜索 + 列表。"""

    def __init__(self, on_select: Callable[[AppEntry], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.on_select = on_select
        self.entries: List[AppEntry] = []
        self.filter_text = ""

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.set_show_separators(True)
        self.listbox.connect("row-selected", self._on_row_selected)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        scroll.set_child(self.listbox)
        self.append(scroll)

        self.count_label = Gtk.Label(label="")
        self.count_label.set_xalign(0.0)
        self.count_label.add_css_class("dim-label")
        self.count_label.set_margin_start(8)
        self.count_label.set_margin_bottom(4)
        self.append(self.count_label)

    def _on_row_selected(self, _box, row) -> None:
        if row is None:
            return
        child = row.get_child()
        if isinstance(child, AppRow):
            self.on_select(child.entry)

    def set_entries(self, entries: List[AppEntry]) -> None:
        self.entries = entries
        self.refill()

    def set_filter(self, text: str) -> None:
        self.filter_text = (text or "").strip().lower()
        self.refill()

    def refill(self) -> None:
        # 清空
        while True:
            row = self.listbox.get_row_at_index(0)
            if row is None:
                break
            self.listbox.remove(row)

        shown = 0
        for entry in self.entries:
            if self.filter_text and self.filter_text not in entry.search_haystack():
                continue
            row = Gtk.ListBoxRow()
            row.set_child(AppRow(entry))
            self.listbox.append(row)
            shown += 1
        self.count_label.set_text(f"共 {shown} 项")


    def select_first(self) -> None:
        row = self.listbox.get_row_at_index(0)
        if row:
            self.listbox.select_row(row)


# ------------------------------------------------------------------ 确认对话框
class ConfirmDialog(Gtk.Window):
    def __init__(self, parent: Gtk.Window, entry: AppEntry, plan: UninstallPlan,
                 autoremove: bool, on_confirm: Callable[[], None]) -> None:
        super().__init__(title="确认卸载", transient_for=parent, modal=True)
        self.on_confirm = on_confirm
        self.set_default_size(620, 460)
        self.set_resizable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        self.set_child(box)

        title = Gtk.Label(label=f"即将卸载：{entry.name}")
        title.add_css_class("title-2")
        title.set_xalign(0.0)
        box.append(title)

        risky = entry.source == SRC_APT and is_risky_package(entry.pkg_id)
        warn_lines: List[str] = []
        warn_lines.append("卸载系统关键组件可能导致系统无法启动或桌面环境损坏，请确认你清楚自己在做什么。")
        if plan.warning:
            warn_lines.append(plan.warning)
        if autoremove:
            warn_lines.append("已勾选自动清理依赖（autoremove），将一并移除不再被依赖的包。")
        warn = Gtk.Label()
        warn.set_xalign(0.0)
        warn.set_wrap(True)
        warn.set_markup(
            '<span foreground="#c01c28">⚠ ' + "\n⚠ ".join(
                GLib.markup_escape_text(w) for w in warn_lines
            ) + "</span>"
        )
        box.append(warn)

        grid = Gtk.Grid(column_spacing=12, row_spacing=6)
        grid.set_margin_top(6)
        row = 0

        def add_row(label: str, value: str) -> None:
            nonlocal row
            l = Gtk.Label(label=label)
            l.set_xalign(0.0)
            l.add_css_class("dim-label")
            v = Gtk.Label(label=value)
            v.set_xalign(0.0)
            v.set_selectable(True)
            v.set_wrap(True)
            v.set_max_width_chars(52)
            grid.attach(l, 0, row, 1, 1)
            grid.attach(v, 1, row, 1, 1)
            row += 1

        add_row("来源", entry.label_source())
        add_row("包名 / 标识", entry.pkg_id or "—")
        add_row("版本", entry.version or "—")
        if entry.desktop_path:
            add_row("快捷方式", entry.desktop_path)
        box.append(grid)

        cmd_label = Gtk.Label(label="将执行的命令：")
        cmd_label.set_xalign(0.0)
        cmd_label.set_margin_top(6)
        box.append(cmd_label)

        view = Gtk.TextView()
        view.set_editable(False)
        view.set_monospace(True)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        buf = view.get_buffer()
        buf.set_text(plan.preview() or "（无可执行命令）")
        frame = Gtk.Frame()
        frame.set_child(view)
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(110)
        scroll.set_child(frame)
        box.append(scroll)

        self.ack = Gtk.CheckButton(label="我已确认以上信息，并理解卸载系统组件的风险。")
        box.append(self.ack)

        if risky:
            self.ack.set_active(False)
            self.confirm_button = Gtk.Button(label="确认卸载")
            self.confirm_button.set_sensitive(False)
            self.ack.connect("toggled", self._on_ack)
        else:
            self.ack.set_active(True)
            self.confirm_button = Gtk.Button(label="确认卸载")
        self.confirm_button.add_css_class("destructive-action")
        self.confirm_button.connect("clicked", self._on_ok)

        cancel = Gtk.Button(label="取消")
        cancel.connect("clicked", lambda _b: self.destroy())

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.set_halign(Gtk.Align.END)
        bar.append(cancel)
        bar.append(self.confirm_button)
        box.append(bar)

    def _on_ack(self, _btn) -> None:
        self.confirm_button.set_sensitive(self.ack.get_active())

    def _on_ok(self, _btn) -> None:
        if not self.ack.get_active():
            return
        cb = self.on_confirm
        self.destroy()
        cb()


# ------------------------------------------------------------------ 主窗口
class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title=TITLE)
        self.set_default_size(1060, 700)
        self.detector = Detector()
        self.current: Optional[AppEntry] = None
        self.current_plan: Optional[UninstallPlan] = None
        self.autoremove = False
        self.busy = False
        self._pulse_src = 0

        self._build_header()
        self._build_body()
        self._reload_async(initial=True)

    # ------------------------------------------------------------ 界面构建
    def _build_header(self) -> None:
        header = Gtk.HeaderBar()
        self.set_titlebar(header)

        self.switcher = Gtk.StackSwitcher()
        header.pack_start(self.switcher)

        self.search = Gtk.Entry()
        self.search.set_placeholder_text("搜索名称 / 包名 / 拼音（如 jsq、jisuanqi）")
        self.search.set_size_request(220, -1)
        self.search.connect("changed", self._on_search_changed)
        header.pack_end(self.search)

        self.refresh_btn = Gtk.Button(label="刷新")
        self.refresh_btn.connect("clicked", lambda _b: self._reload_async())
        header.pack_end(self.refresh_btn)

        self.spinner = Gtk.Spinner()
        header.pack_end(self.spinner)

        menu = Gio.Menu.new()
        menu.append("关于", "app.about")
        menu.append("退出", "app.quit")
        btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_end(btn)

    def _build_body(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_margin_top(8)
        root.set_margin_bottom(8)
        root.set_margin_start(8)
        root.set_margin_end(8)
        self.set_child(root)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_vexpand(True)
        paned.set_position(430)
        root.append(paned)

        # 左侧：两个标签页
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.switcher.set_stack(self.stack)

        self.page_apps = ListPage(self._on_select_entry)
        self.page_cli = ListPage(self._on_select_entry)
        self.stack.add_titled(self.page_apps, "apps", "图形应用")
        self.stack.add_titled(self.page_cli, "cli", "命令行工具")
        paned.set_start_child(self.stack)

        # 右侧：详情面板
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right.set_margin_start(10)
        paned.set_end_child(right)

        self.detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        right.append(self.detail)

        self.placeholder = Gtk.Label(label="从左侧选择一个软件以查看安装来源和卸载命令")
        self.placeholder.add_css_class("dim-label")
        self.placeholder.set_vexpand(True)
        self.detail.append(self.placeholder)

        self.detail_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.detail_content.set_visible(False)
        self.detail.append(self.detail_content)
        self._build_detail_panel()

        # 日志
        expander = Gtk.Expander(label="执行日志")
        expander.set_expanded(False)
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(150)
        scroll.set_child(self.log_view)
        expander.set_child(scroll)
        right.append(expander)

        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        self.progress.set_text("")
        self.progress.set_visible(False)
        root.append(self.progress)

    # ------------------------------------------------------------ 详情面板
    def _build_detail_panel(self) -> None:
        """只构建一次详情面板控件，之后通过 _update_detail 更新内容（不再重建）。"""
        box = self.detail_content

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.d_icon = Gtk.Image.new_from_icon_name("application-x-executable")
        self.d_icon.set_pixel_size(48)
        head.append(self.d_icon)

        tbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.d_name = Gtk.Label(label="")
        self.d_name.add_css_class("title-2")
        self.d_name.set_xalign(0.0)
        self.d_name.set_ellipsize(Pango.EllipsizeMode.END)
        tbox.append(self.d_name)
        self.d_comment = Gtk.Label(label="")
        self.d_comment.set_xalign(0.0)
        self.d_comment.add_css_class("dim-label")
        self.d_comment.set_ellipsize(Pango.EllipsizeMode.END)
        tbox.append(self.d_comment)
        head.append(tbox)
        box.append(head)

        grid = Gtk.Grid(column_spacing=14, row_spacing=5)
        grid.set_margin_top(6)
        self.d_rows = {}
        for i, key in enumerate(("安装来源", "包名 / 标识", "版本", "快捷方式", "可执行文件")):
            k = Gtk.Label(label=key)
            k.set_xalign(0.0)
            k.add_css_class("dim-label")
            v = Gtk.Label(label="—")
            v.set_xalign(0.0)
            v.set_selectable(True)
            v.set_wrap(True)
            v.set_max_width_chars(46)
            grid.attach(k, 0, i, 1, 1)
            grid.attach(v, 1, i, 1, 1)
            self.d_rows[key] = (k, v)
        box.append(grid)

        lbl = Gtk.Label(label="卸载命令预览：")
        lbl.set_xalign(0.0)
        lbl.set_margin_top(8)
        box.append(lbl)

        self.d_preview = Gtk.TextView()
        self.d_preview.set_editable(False)
        self.d_preview.set_monospace(True)
        self.d_preview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        preview_scroll = Gtk.ScrolledWindow()
        preview_scroll.set_min_content_height(120)
        preview_scroll.set_child(self.d_preview)
        box.append(preview_scroll)

        self.d_warn = Gtk.Label(label="")
        self.d_warn.set_xalign(0.0)
        self.d_warn.set_wrap(True)
        self.d_warn.set_visible(False)
        box.append(self.d_warn)

        self.d_autoremove = Gtk.CheckButton(label="同时清理不再需要的依赖（apt autoremove --purge）")
        self.d_autoremove.connect("toggled", self._on_autoremove)
        box.append(self.d_autoremove)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bar.set_margin_top(10)
        self.d_btn_uninstall = Gtk.Button(label="卸载此软件")
        self.d_btn_uninstall.add_css_class("destructive-action")
        self.d_btn_uninstall.connect("clicked", lambda _b: self._on_uninstall())
        bar.append(self.d_btn_uninstall)
        self.d_btn_sim = Gtk.Button(label="模拟执行（预览影响）")
        self.d_btn_sim.connect("clicked", lambda _b: self._on_simulate())
        bar.append(self.d_btn_sim)
        box.append(bar)

    def _set_row(self, key: str, value: str) -> None:
        k_label, v_label = self.d_rows[key]
        visible = bool(value)
        k_label.set_visible(visible)
        v_label.set_visible(visible)
        if visible:
            v_label.set_text(value)

    def _update_detail(self, entry: Optional[AppEntry]) -> None:
        """填充详情面板（不重建控件）。"""
        if entry is None:
            self.placeholder.set_visible(True)
            self.detail_content.set_visible(False)
            return
        self.placeholder.set_visible(False)
        self.detail_content.set_visible(True)

        self.d_icon.set_from_icon_name("application-x-executable")
        if entry.icon:
            if os.path.isabs(entry.icon) and os.path.exists(entry.icon):
                try:
                    self.d_icon.set_from_file(entry.icon)
                except Exception:
                    pass
            else:
                self.d_icon.set_from_icon_name(entry.icon)
        self.d_icon.set_pixel_size(48)

        self.d_name.set_text(entry.name or "")
        self.d_comment.set_text(entry.comment or "")
        self.d_comment.set_visible(bool(entry.comment))

        self._set_row("安装来源", entry.label_source())
        self._set_row("包名 / 标识", entry.pkg_id or "—")
        self._set_row("版本", entry.version or "—")
        self._set_row("快捷方式", entry.desktop_path or "")
        self._set_row("可执行文件", entry.binary_path or "" if not entry.is_cli else "")

        # 复选框状态同步（阻断信号，避免递归刷新）
        self.d_autoremove.handler_block_by_func(self._on_autoremove)
        self.d_autoremove.set_active(self.autoremove)
        self.d_autoremove.handler_unblock_by_func(self._on_autoremove)

        self._refresh_plan_view()

    def _refresh_plan_view(self) -> None:
        """根据当前条目与选项刷新命令预览、警告与按钮状态。"""
        entry = self.current
        if entry is None:
            return
        plan = U.build_plan(entry, self.autoremove)
        self.current_plan = plan

        buf = self.d_preview.get_buffer()
        buf.set_text(plan.preview() or "（该来源无法自动生成卸载命令）")

        if not plan.allowed:
            self.d_warn.set_markup(
                '<span foreground="#c01c28">⛔ '
                + GLib.markup_escape_text(plan.reason or "该条目不允许卸载")
                + "</span>"
            )
            self.d_warn.set_visible(True)
        elif plan.warning:
            self.d_warn.set_markup(
                '<span foreground="#e5a50a">⚠ '
                + GLib.markup_escape_text(plan.warning)
                + "</span>"
            )
            self.d_warn.set_visible(True)
        else:
            self.d_warn.set_visible(False)

        is_apt = entry.source == SRC_APT
        self.d_autoremove.set_visible(is_apt)
        self.d_btn_uninstall.set_sensitive(plan.allowed and not self.busy)
        self.d_btn_sim.set_sensitive(is_apt and not self.busy)

    @staticmethod
    def _clear(container: Gtk.Box) -> None:
        while True:
            child = container.get_first_child()
            if child is None:
                break
            container.remove(child)

    # ------------------------------------------------------------ 数据加载
    def _reload_async(self, initial: bool = False) -> None:
        if self.busy:
            return
        self.busy = True
        self.spinner.start()
        self.refresh_btn.set_sensitive(False)
        if initial:
            self._set_status("正在扫描已安装软件…")

        def worker() -> None:
            det = Detector()
            entries = scan_desktop_entries()
            det.detect_all(entries)
            cli = det.cli_entries()
            GLib.idle_add(self._apply_results, det, entries, cli)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_results(self, det: Detector, apps: List[AppEntry], cli: List[AppEntry]) -> bool:
        self.detector = det
        self.page_apps.set_entries(apps)
        self.page_cli.set_entries(cli)
        self.page_apps.set_filter(self.search.get_text())
        self.page_cli.set_filter(self.search.get_text())
        self.spinner.stop()
        self.refresh_btn.set_sensitive(True)
        self.busy = False
        self._set_status(f"图形应用 {len(apps)} 个 · 命令行工具 {len(cli)} 个")
        if self.current:
            # 刷新后尝试保持选中
            key = self.current.key
            page = self.page_cli if self.current.is_cli else self.page_apps
            for i, e in enumerate(page.entries):
                if e.key == key:
                    row = page.listbox.get_row_at_index(i)
                    if row:
                        page.listbox.select_row(row)
                    break
            else:
                self._update_detail(None)
        return False

    def _set_status(self, text: str) -> None:
        self.set_title(f"{TITLE} — {text}" if text else TITLE)

    def _on_search_changed(self, entry: Gtk.Entry) -> None:
        text = entry.get_text()
        self.page_apps.set_filter(text)
        self.page_cli.set_filter(text)

    # ------------------------------------------------------------ 详情面板
    def _on_select_entry(self, entry: AppEntry) -> None:
        """选中条目：只更新详情内容，不重建控件。"""
        self.current = entry
        self._update_detail(entry)

    def _on_autoremove(self, btn: Gtk.CheckButton) -> None:
        """勾选 autoremove：只刷新命令预览，不重建面板。"""
        self.autoremove = btn.get_active()
        if self.current:
            self._refresh_plan_view()


    # ------------------------------------------------------------ 卸载
    def _on_uninstall(self) -> None:
        entry = self.current
        if entry is None:
            self._log("请先在左侧选择一个软件。")
            return
        if self.busy:
            self._log("正在处理其它操作，请稍候…")
            return
        plan = U.build_plan(entry, self.autoremove)
        if not plan.allowed:
            self._log(f"⛔ 无法卸载：{plan.reason or '该条目不允许卸载'}")
            return
        try:
            dialog = ConfirmDialog(self, entry, plan, self.autoremove, self._do_uninstall)
            dialog.present()
        except Exception as exc:  # 对话框创建失败也要给出可见反馈
            self._log(f"确认对话框创建失败：{exc}")

    def _do_uninstall(self) -> None:
        entry = self.current
        plan = U.build_plan(entry, self.autoremove)
        self.busy = True
        self.progress.set_visible(True)
        self.progress.set_text("正在卸载…")
        self.progress.pulse()
        self._pulse_src = GLib.timeout_add(120, self._pulse)
        self._log_clear()
        self._log(f"开始卸载：{entry.name}（{entry.label_source()}）\n")

        def worker() -> None:
            results = U.execute_plan(plan, on_line=lambda line: GLib.idle_add(self._log, line))
            ok = all(r.ok for r in results) and bool(results)
            GLib.idle_add(self._uninstall_done, ok, entry)

        threading.Thread(target=worker, daemon=True).start()

    def _pulse(self) -> bool:
        if not self.busy:
            self._pulse_src = 0
            return False
        self.progress.pulse()
        return True

    def _uninstall_done(self, ok: bool, entry: AppEntry) -> bool:
        if self._pulse_src:
            GLib.source_remove(self._pulse_src)
            self._pulse_src = 0
        self.progress.set_visible(False)
        self.busy = False
        if ok:
            self._log("\n完成。正在刷新列表…")
            U.refresh_after(U.build_plan(entry))
            self.current = None
            self._reload_async()
        else:
            self._log("\n卸载未完成，请查看上方日志（可能需要管理员授权或命令不存在）。")
        return False

    def _on_simulate(self) -> None:
        entry = self.current
        if entry is None or self.busy:
            return
        plan = U.build_plan(entry, self.autoremove)
        self._log_clear()
        self._log(f"模拟执行：{entry.name}\n")

        def worker() -> None:
            U.simulate_plan(plan, on_line=lambda line: GLib.idle_add(self._log, line))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------ 日志
    def _log_clear(self) -> None:
        buf = self.log_view.get_buffer()
        buf.set_text("")

    def _log(self, line: str) -> bool:
        buf = self.log_view.get_buffer()
        end = buf.get_end_iter()
        buf.insert(end, line + "\n")
        mark = buf.create_mark(None, buf.get_end_iter(), False)
        self.log_view.scroll_to_mark(mark, 0.0, False, 0.0, 1.0)
        return False


# ------------------------------------------------------------------ 应用
class UninstallerApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)
        self.window: Optional[MainWindow] = None
        self.connect("activate", self.on_activate)

        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self.on_about)
        self.add_action(about)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda _a, _p: self.quit())
        self.add_action(quit_action)

    def on_activate(self, _app) -> None:
        if self.window is None:
            self.window = MainWindow(self)
        self.window.present()

    def on_about(self, _action, _param) -> None:
        dialog = Gtk.Window(title="关于", transient_for=self.window, modal=True)
        dialog.set_default_size(420, 240)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(20)
        box.set_margin_bottom(20)
        box.set_margin_start(20)
        box.set_margin_end(20)
        dialog.set_child(box)

        title = Gtk.Label(label=f"{TITLE}  {APP_VERSION}")
        title.add_css_class("title-2")
        box.append(title)
        desc = Gtk.Label(
            label="检测软件安装来源（APT / Snap / Flatpak / Homebrew / Nix / Guix / pip / "
                  "npm / cargo / go / gem / conda / Distrobox / Docker / AppImage / "
                  "源码编译 / 脚本安装），并在二次确认后执行卸载。"
        )
        desc.set_wrap(True)
        desc.set_max_width_chars(52)
        box.append(desc)
        tip = Gtk.Label(label="卸载系统关键组件会影响系统稳定性，请谨慎操作。")
        tip.set_wrap(True)
        tip.add_css_class("dim-label")
        box.append(tip)

        close = Gtk.Button(label="关闭")
        close.connect("clicked", lambda _b: dialog.destroy())
        close.set_halign(Gtk.Align.END)
        box.append(close)
        dialog.present()


def main(args: List[str]) -> int:
    app = UninstallerApp()
    return app.run(args)
