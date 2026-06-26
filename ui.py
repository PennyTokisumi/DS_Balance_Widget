"""
UI 模块 — tkinter 悬浮窗口，弹出 5 秒后渐隐回到托盘
"""
import tkinter as tk
from datetime import datetime

from config import save_config

# 颜色
COLOR_GREEN  = "#4CAF50"
COLOR_YELLOW = "#FFC107"
COLOR_ORANGE = "#FF5722"   # 介于警告黄 #FFC107 与错误红 #F44336 之间
COLOR_RED    = "#F44336"
COLOR_BG     = "#1E1E2E"
COLOR_TEXT   = "#CDD6F4"
COLOR_SUB    = "#A6ADC8"

FONT = "Microsoft YaHei UI"
POPUP_SECONDS = 5          # 弹出显示秒数
FADE_MS       = 600        # 渐隐动画毫秒
FADE_STEPS    = 12         # 渐隐步数


class BalanceWindow:
    def __init__(self, config: dict, on_refresh=None, on_settings=None, on_exit=None):
        self.config = config
        self.on_refresh = on_refresh
        self.on_settings = on_settings
        self.on_exit = on_exit
        self.balance_data = None
        self.error_msg = None
        self.last_refresh = None
        self._drag_start = None
        self._hide_timer = None
        self._fade_timer = None
        self._persistent = False   # True = 用户手动显示，不自动隐藏

        self.root = tk.Tk()
        self.root.title("DS 余额")
        self.root.geometry("300x200")
        self.root.configure(bg=COLOR_BG)
        self.root.wm_attributes("-topmost", config.get("window_on_top", True))
        self.root.wm_attributes("-alpha", config.get("opacity", 0.92))
        self.root.overrideredirect(True)

        self.root.bind("<Button-1>", self._on_drag_start)
        self.root.bind("<B1-Motion>", self._on_drag_move)
        self.root.bind("<ButtonRelease-1>", self._on_drag_end)
        self.root.bind("<Button-3>", self._on_right_click)

        self._position_window()
        self._build_ui()
        self._build_context_menu()

    # ── 定位 ──────────────────────────────────────

    def _position_window(self):
        x = self.config.get("window_x")
        y = self.config.get("window_y")
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        if x is None or y is None:
            x = 10
            y = sh - 260
        else:
            # 边界检查：窗口至少保留 40px 在屏幕内，防止拖出屏幕后找不回来
            x = max(-260, min(x, sw - 40))
            y = max(0, min(y, sh - 40))
        self.root.geometry(f"+{x}+{y}")

    # ── UI ────────────────────────────────────────

    def _build_ui(self):
        # 标题栏
        bar = tk.Frame(self.root, bg=COLOR_BG, height=28)
        bar.pack(fill=tk.X, side=tk.TOP)
        bar.pack_propagate(False)

        tk.Label(
            bar, text="DS 余额",
            fg=COLOR_TEXT, bg=COLOR_BG, font=(FONT, 10, "bold"),
            anchor="w"
        ).pack(side=tk.LEFT, padx=10, pady=3)

        btn = tk.Label(
            bar, text="✕", fg=COLOR_SUB, bg=COLOR_BG,
            font=(FONT, 11), cursor="hand2"
        )
        btn.pack(side=tk.RIGHT, padx=10)
        btn.bind("<Button-1>", lambda e: (self._cancel_popup(), self.hide()))

        # ── 余额主体 ──
        body = tk.Frame(self.root, bg=COLOR_BG)
        body.pack(expand=True, fill=tk.BOTH)

        # ¥ 符号 (小字号) + 数字 (大字号) 水平排列
        num_frame = tk.Frame(body, bg=COLOR_BG)
        num_frame.pack(expand=True)

        self.lbl_currency = tk.Label(
            num_frame, text="¥",
            fg=COLOR_TEXT, bg=COLOR_BG,
            font=(FONT, 18, "bold")
        )
        self.lbl_currency.pack(side=tk.LEFT, padx=(0, 4))

        self.lbl_total = tk.Label(
            num_frame, text="--.--",
            fg=COLOR_TEXT, bg=COLOR_BG,
            font=(FONT, 30, "bold")
        )
        self.lbl_total.pack(side=tk.LEFT)

        # 刷新时间
        self.lbl_refresh = tk.Label(
            body, text="等待刷新...",
            fg=COLOR_SUB, bg=COLOR_BG,
            font=(FONT, 9)
        )
        self.lbl_refresh.pack(pady=(0, 12))

    # ── 右键菜单 ─────────────────────────────────

    def _build_context_menu(self):
        self.ctx_menu = tk.Menu(
            self.root, tearoff=0, bg="#313244", fg=COLOR_TEXT, font=(FONT, 9)
        )
        self.ctx_menu.add_command(label="🔄 刷新", command=lambda: self.on_refresh and self.on_refresh())
        self.ctx_menu.add_command(label="📌 固定显示", command=self._pin)
        self.ctx_menu.add_command(label="⚙ 设置", command=lambda: self.on_settings and self.on_settings())
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="✕ 退出", command=lambda: self.on_exit and self.on_exit())

    def _on_right_click(self, event):
        try:
            self.ctx_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.ctx_menu.grab_release()

    def _pin(self):
        """固定显示：取消自动隐藏"""
        self._persistent = True
        self._cancel_popup()

    # ── 拖拽 ─────────────────────────────────────

    def _on_drag_start(self, event):
        self._drag_start = (event.x_root, event.y_root)

    def _on_drag_move(self, event):
        if self._drag_start is None:
            return
        dx = event.x_root - self._drag_start[0]
        dy = event.y_root - self._drag_start[1]
        x = self.root.winfo_x() + dx
        y = self.root.winfo_y() + dy
        self.root.geometry(f"+{x}+{y}")
        self._drag_start = (event.x_root, event.y_root)

    def _on_drag_end(self, event):
        """拖拽结束时保存窗口位置"""
        self._drag_start = None
        self._save_position()

    # ── 更新余额 ─────────────────────────────────

    def update_balance(self, data: dict = None, error: str = None):
        self.balance_data = data
        self.error_msg = error
        self.last_refresh = datetime.now()

        if error:
            self.lbl_currency.config(fg=COLOR_RED)
            self.lbl_total.config(text="⚠", fg=COLOR_RED)
        elif data:
            total = data.get("total_balance", 0)
            warn = self.config.get("warning_threshold", 20)
            alert = self.config.get("alert_threshold", 5)
            if total <= alert:
                color = COLOR_ORANGE
            elif total <= warn:
                color = COLOR_YELLOW
            else:
                color = COLOR_GREEN

            self.lbl_currency.config(fg=color)
            if total >= 1000:
                self.lbl_total.config(text=f"{total:,.0f}", fg=color)
            elif total >= 1:
                self.lbl_total.config(text=f"{total:,.2f}", fg=color)
            else:
                self.lbl_total.config(text=f"{total:.4f}", fg=color)

        ts = self.last_refresh.strftime("%Y-%m-%d %H:%M")
        self.lbl_refresh.config(text=f"刷新时间: {ts}")

    # ── 弹出 / 渐隐 ──────────────────────────────

    def popup(self, persistent: bool = None):
        """淡入显示 → 停留 N 秒 → 淡出隐藏

        persistent=None: 保持当前状态不变
        persistent=True: 固定显示，不自动隐藏
        persistent=False: 弹出后自动隐藏
        """
        self._cancel_popup(reset_alpha=False)
        if persistent is not None:
            self._persistent = persistent
        self.show()
        self._fade_in(0)

    def _fade_in(self, step):
        """逐帧淡入"""
        if self._persistent:
            return
        target = self.config.get("opacity", 0.92)
        alpha = target * min(1.0, step / FADE_STEPS)
        self.root.wm_attributes("-alpha", max(0.05, alpha))
        if step < FADE_STEPS:
            delay = FADE_MS // FADE_STEPS
            self._fade_timer = self.root.after(delay, lambda: self._fade_in(step + 1))
        else:
            # 淡入完成，停留 N 秒后淡出
            self._hide_timer = self.root.after(
                POPUP_SECONDS * 1000, lambda: self._fade_out(0)
            )

    def _fade_out(self, step):
        """逐帧淡出"""
        if self._persistent:
            return
        target = self.config.get("opacity", 0.92)
        alpha = target * (1 - step / FADE_STEPS)
        if step >= FADE_STEPS:
            # 直接 withdraw，不经过 hide() / _cancel_popup()
            # 否则 _cancel_popup 会把 alpha 恢复成 0.92，造成闪烁
            self.root.wm_attributes("-alpha", 0.0)
            self.root.withdraw()
            return
        self.root.wm_attributes("-alpha", max(0.02, alpha))
        delay = FADE_MS // FADE_STEPS
        self._fade_timer = self.root.after(delay, lambda: self._fade_out(step + 1))

    def _cancel_popup(self, reset_alpha=True):
        """取消所有定时器，reset_alpha=False 跳过透明度复位以避免闪烁"""
        if self._hide_timer:
            self.root.after_cancel(self._hide_timer)
            self._hide_timer = None
        if self._fade_timer:
            self.root.after_cancel(self._fade_timer)
            self._fade_timer = None
        if reset_alpha:
            self.root.wm_attributes("-alpha", self.config.get("opacity", 0.92))

    # ── 显示 / 隐藏 ──────────────────────────────

    def show(self):
        self.root.deiconify()
        self.root.lift()

    def hide(self):
        self._cancel_popup()
        self.root.withdraw()

    def is_visible(self):
        return self.root.state() != "withdrawn"

    def get_position(self) -> tuple:
        return (self.root.winfo_x(), self.root.winfo_y())

    def _save_position(self):
        """将当前窗口位置持久化到配置文件（仅位置变化时写入）"""
        try:
            x, y = self.get_position()
            if x != self.config.get("window_x") or y != self.config.get("window_y"):
                self.config["window_x"] = x
                self.config["window_y"] = y
                save_config(self.config)
        except Exception:
            pass  # 窗口可能已被销毁

    def destroy(self):
        self._cancel_popup()
        self._save_position()
        self.root.destroy()
