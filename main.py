"""
主入口 — 悬浮窗口 + 系统托盘 + 自动刷新 + 单实例检测
"""
__version__ = "1.0"
import ctypes
import sys
import os
import threading
import tkinter as tk
from ctypes import wintypes
from tkinter import messagebox
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import load_config, save_config, get_api_key, CONFIG_DIR
from api import fetch_balance, BalanceError
from ui import BalanceWindow, COLOR_GREEN
from tray import create_tray, update_tray_tooltip

PID_FILE = CONFIG_DIR / "widget.pid"
MUTEX_NAME = r"Local\DeepSeekWidget_SingleInstance"


def ensure_single_instance() -> bool:
    """
    确保只有一个实例在运行。
    返回 True 表示可以继续启动，False 表示已有实例在运行。
    CreateMutexW + GetLastError 是内核级原子操作，无竞态窗口。
    """
    kernel32 = ctypes.windll.kernel32

    # 方法1: Windows 命名互斥体（原子检测+创建）
    try:
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE

        handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
        last_err = kernel32.GetLastError()
        if handle and last_err == 183:  # ERROR_ALREADY_EXISTS
            # 互斥体已存在 — 另一个实例在运行
            kernel32.CloseHandle(handle)
            _bring_existing_to_front()
            return False
        # 新创建的互斥体，handle 保持打开直到进程退出
    except Exception:
        pass  # 回退到 PID 文件方式

    # 方法2: PID 文件检测（兜底）
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            h = kernel32.OpenProcess(0x0400, False, old_pid)
            if h:
                kernel32.CloseHandle(h)
                _bring_existing_to_front()
                return False
        except (ValueError, OSError):
            pass
        try:
            PID_FILE.unlink()
        except OSError:
            pass

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    return True


def _bring_existing_to_front():
    """激活已有的 widget 窗口"""
    try:
        user32 = ctypes.windll.user32
        SW_RESTORE = 9

        # 按窗口标题查找
        hwnd = user32.FindWindowW(None, "DS 余额")
        if not hwnd:
            hwnd = user32.FindWindowW(None, "DS 余额 — 设置")

        if hwnd:
            # 如果最小化则恢复
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass  # 静默失败，新实例直接退出即可


def cleanup_instance():
    """退出时清理 PID 文件和互斥体"""
    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
    except OSError:
        pass


class DeepSeekWidget:
    def __init__(self):
        self.config = load_config()
        self._refresh_timer = None
        self._running = False
        self._refreshing = False    # 防止重复刷新线程堆积
        self._settings_dlg = None   # 追踪设置窗口引用，防止重复打开

        self.window = BalanceWindow(
            self.config,
            on_refresh=self.manual_refresh,
            on_settings=self.show_settings,
            on_exit=self.exit_app,
        )
        self.tray = create_tray(
            on_show=self.toggle_window,
            on_refresh=self.manual_refresh,
            on_settings=self.show_settings,
            on_exit=self.exit_app,
        )

    def start(self):
        self._running = True

        # 同步开机自启动状态（保持路径最新）
        if self.config.get("auto_start", False):
            script_dir = os.path.dirname(os.path.abspath(__file__))
            set_auto_start(True, script_dir)

        api_key = get_api_key(self.config)
        if not api_key:
            self.window.root.after(500, self.show_settings)
        else:
            self.window.root.after(300, self.manual_refresh)

        threading.Thread(target=self.tray.run, daemon=True).start()
        self._schedule_auto_refresh()
        # 启动时窗口隐藏，首次刷新会自动弹出
        self.window.root.mainloop()

    def toggle_window(self, *_):
        """托盘切换：显示/隐藏悬浮窗"""
        self.window.root.after(0, self._do_toggle)

    def _do_toggle(self):
        if self.window.is_visible():
            self.window.hide()
        else:
            self.window.popup(persistent=False)

    def exit_app(self, *_):
        self._running = False
        if self._refresh_timer:
            self.window.root.after_cancel(self._refresh_timer)
        cleanup_instance()
        try:
            self.tray.stop()
        except Exception:
            pass
        # quit() 只是设置标志让 mainloop 退出，跨线程调用安全
        self.window.root.quit()

    def manual_refresh(self, *_):
        # 确保在 tkinter 主线程中执行，避免托盘回调线程安全问题
        self.window.root.after(0, self._do_refresh)

    def _do_refresh(self):
        if self._refreshing:
            return  # 上一次刷新还未完成，跳过
        api_key = get_api_key(self.config)
        if not api_key:
            self.window.update_balance(error="请先配置 API Key")
            return

        self._refreshing = True

        def _run():
            try:
                data = fetch_balance(api_key)
                self.window.root.after(0, lambda: self._on_ok(data))
            except BalanceError as e:
                self.window.root.after(0, lambda: self._on_err(str(e)))

        threading.Thread(target=_run, daemon=True).start()

    def _on_ok(self, data):
        self._refreshing = False
        if not self._running:
            return  # 已退出，忽略迟到的回调
        self.window.update_balance(data)
        self._update_tray(data.get("total_balance", 0))
        if self._settings_dlg is None:
            self.window.popup(persistent=False)

    def _on_err(self, error):
        self._refreshing = False
        if not self._running:
            return  # 已退出，忽略迟到的回调
        self.window.update_balance(error=error)
        self._update_tray_error(error)
        if self._settings_dlg is None:
            self.window.popup(persistent=False)

    def _auto_refresh(self):
        if not self._running:
            return
        self.manual_refresh()
        self._schedule_auto_refresh()

    def _schedule_auto_refresh(self):
        interval = self.config.get("refresh_interval", 1) * 60 * 1000
        self._refresh_timer = self.window.root.after(interval, self._auto_refresh)

    def _update_tray(self, total: float):
        update_tray_tooltip(self.tray, f"DS 余额: ¥ {total:,.2f}")

    def _update_tray_error(self, error: str):
        update_tray_tooltip(self.tray, f"DS 余额 - {error}")

    def show_settings(self, *_):
        # 通过 root.after() 确保在 tkinter 主线程中执行
        # 托盘菜单回调在 pystray 线程，直接操作 tk 会导致卡死
        self.window.root.after(0, self._do_show_settings)

    def _do_show_settings(self):
        """打开设置窗口（确保只有一个实例）"""
        # 如果已有设置窗口打开，则激活已有窗口
        if self._settings_dlg is not None:
            try:
                if self._settings_dlg.dlg.winfo_exists():
                    self._settings_dlg.dlg.lift()
                    self._settings_dlg.dlg.focus_force()
                    return
            except Exception:
                pass
            self._settings_dlg = None

        # 如果主窗口当前可见，让它保持显示（不自动淡出）
        was_visible = self.window.is_visible()
        if was_visible:
            self.window.popup(persistent=True)

        dlg = SettingsDialog(
            self.window.root, self.config, on_save=self._on_saved
        )
        self._settings_dlg = dlg
        dlg.wait()                # 阻塞直到用户关闭设置窗口
        self._settings_dlg = None

        # 设置关闭后刷新余额，恢复正常的自动淡出
        self.manual_refresh()

    def _on_saved(self, new_config: dict):
        self.config = new_config
        save_config(self.config)
        # 立即用新间隔重新调度自动刷新，不等旧定时器到期
        if self._refresh_timer:
            self.window.root.after_cancel(self._refresh_timer)
            self._refresh_timer = None
        self._schedule_auto_refresh()
        # _do_show_settings 在设置窗口关闭后会统一刷新余额


# ── 开机自启动管理 ────────────────────────────────────

def _get_startup_path() -> Path:
    """Windows 启动文件夹路径"""
    import os as _os
    startup = Path(_os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return startup / "deepseek-widget.bat"


def set_auto_start(enabled: bool, script_dir: str) -> None:
    """设置或取消开机自启动"""
    startup_file = _get_startup_path()
    if enabled:
        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable  # venv 环境回退到 python.exe
        content = (
            f'@echo off\n'
            f'cd /d "{script_dir}"\n'
            f'start "" /B "{pythonw}" main.py\n'
        )
        # 仅内容变化时才写入，避免无意义的磁盘 IO
        if not startup_file.exists() or startup_file.read_text(encoding="utf-8") != content:
            startup_file.parent.mkdir(parents=True, exist_ok=True)
            startup_file.write_text(content, encoding="utf-8")
    else:
        try:
            startup_file.unlink()
        except FileNotFoundError:
            pass


def is_auto_start_enabled() -> bool:
    return _get_startup_path().exists()


class SettingsDialog:
    def __init__(self, parent, config: dict, on_save=None):
        self.config = config
        self.on_save = on_save

        self.dlg = tk.Toplevel(parent)
        self.dlg.title("DS 余额 — 设置")
        self.dlg.geometry("460x350")
        self.dlg.resizable(False, False)
        self.dlg.configure(bg="#1E1E2E")
        # 不设置 transient：避免主窗口淡出隐藏时设置窗口也被隐藏
        self.dlg.attributes('-topmost', True)
        self.dlg.grab_set()
        # 点击窗口关闭按钮 (X) 等同于取消
        self.dlg.protocol("WM_DELETE_WINDOW", self.dlg.destroy)
        self._build()

    def wait(self):
        """阻塞直到用户关闭设置窗口（保存或取消）"""
        self.dlg.wait_window()

    def _build(self):
        FG = "#CDD6F4"; BG = "#1E1E2E"; SUB = "#A6ADC8"; EBG = "#313244"

        tk.Label(
            self.dlg, text="⚙ 设置",
            fg=FG, bg=BG, font=("Microsoft YaHei UI", 12, "bold")
        ).pack(pady=(14, 6))

        tk.Label(
            self.dlg,
            text=(
                "输入 DeepSeek API Key 即可开始监控余额\n"
                "在 platform.deepseek.com/api_keys 创建"
            ),
            fg=SUB, bg=BG, font=("Microsoft YaHei UI", 9), justify="center"
        ).pack(pady=(0, 12))

        # ── API Key ──
        f = tk.Frame(self.dlg, bg=BG)
        f.pack(fill=tk.X, padx=24)
        self.key_var = tk.StringVar(value=self.config.get("api_key", ""))
        self.entry = tk.Entry(
            f, textvariable=self.key_var,
            bg=EBG, fg=FG, insertbackground=FG,
            font=("Consolas", 10), show="•", relief="flat"
        )
        self.entry.pack(fill=tk.X, ipady=4)

        self._show = False
        self.toggle_btn = tk.Label(
            self.dlg, text="👁 显示", fg=SUB, bg=BG,
            font=("Microsoft YaHei UI", 8), cursor="hand2"
        )
        self.toggle_btn.pack(pady=(4, 8))
        self.toggle_btn.bind("<Button-1>", self._toggle)

        # ── 刷新间隔 (分钟) ──
        f = tk.Frame(self.dlg, bg=BG)
        f.pack(fill=tk.X, padx=24, pady=(4, 4))
        tk.Label(f, text="刷新间隔 (分钟):", fg=SUB, bg=BG,
                 font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT)
        self.int_var = tk.StringVar(value=str(self.config.get("refresh_interval", 1)))
        tk.Spinbox(
            f, textvariable=self.int_var,
            from_=1, to=60, increment=1, width=4,
            bg=EBG, fg=FG, buttonbackground=BG,
            font=("Consolas", 10), relief="flat"
        ).pack(side=tk.RIGHT)

        # ── 开机自启动 ──
        f = tk.Frame(self.dlg, bg=BG)
        f.pack(fill=tk.X, padx=24, pady=(4, 12))
        self.auto_start_var = tk.BooleanVar(value=self.config.get("auto_start", False))
        cb = tk.Checkbutton(
            f, text="开机自动启动", variable=self.auto_start_var,
            fg=SUB, bg=BG, selectcolor=EBG,
            font=("Microsoft YaHei UI", 9),
            activebackground=BG, activeforeground=FG,
        )
        cb.pack(side=tk.LEFT)

        # ── 按钮 ──
        f = tk.Frame(self.dlg, bg=BG)
        f.pack(pady=(4, 14))
        tk.Button(
            f, text="保存", command=self._save,
            bg=COLOR_GREEN, fg="#FFF", font=("Microsoft YaHei UI", 10),
            relief="flat", padx=28, pady=4, cursor="hand2"
        ).pack(side=tk.LEFT, padx=6)
        tk.Button(
            f, text="取消", command=self.dlg.destroy,
            bg="#45475A", fg=FG, font=("Microsoft YaHei UI", 10),
            relief="flat", padx=28, pady=4, cursor="hand2"
        ).pack(side=tk.LEFT, padx=6)

    def _toggle(self, event):
        self._show = not self._show
        self.entry.config(show="" if self._show else "•")
        self.toggle_btn.config(text="🙈 隐藏" if self._show else "👁 显示")

    def _save(self):
        key = self.key_var.get().strip()
        if not key:
            messagebox.showwarning("提示", "API Key 不能为空", parent=self.dlg)
            return
        try:
            interval = int(self.int_var.get())
            if interval < 1:
                interval = 1
        except ValueError:
            interval = 1

        enable_auto = self.auto_start_var.get()

        self.config["api_key"] = key
        self.config["refresh_interval"] = interval
        self.config["auto_start"] = enable_auto

        # 处理开机自启动
        script_dir = os.path.dirname(os.path.abspath(__file__))
        set_auto_start(enable_auto, script_dir)

        self.dlg.destroy()
        if self.on_save:
            self.on_save(self.config)


def main():
    if not ensure_single_instance():
        print("DeepSeek 用量小工具已在运行中，激活已有窗口。")
        return
    try:
        DeepSeekWidget().start()
    finally:
        cleanup_instance()


if __name__ == "__main__":
    main()
