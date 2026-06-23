"""
系统托盘模块 — pystray 托盘图标 (DeepSeek 鲸鱼) + 右键菜单
"""
import os
import pystray
from PIL import Image

# 托盘图标路径
_ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ds_icon.png")


def _load_icon() -> Image.Image:
    """加载鲸鱼图标"""
    if os.path.exists(_ICON_PATH):
        return Image.open(_ICON_PATH)
    # 回退：纯色圆形
    from PIL import ImageDraw
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([3, 3, 29, 29], fill="#4F6EF7")
    return img


def create_tray(
    on_show=None,
    on_refresh=None,
    on_settings=None,
    on_exit=None,
):
    def _show(icon, item):
        if on_show:
            on_show()

    def _refresh(icon, item):
        if on_refresh:
            on_refresh()

    def _settings(icon, item):
        if on_settings:
            on_settings()

    def _exit(icon, item):
        # 不要在回调里 icon.stop() — exit_app 会统一处理，避免双重停止
        if on_exit:
            on_exit()

    menu = pystray.Menu(
        pystray.MenuItem("显示/隐藏窗口", _show, default=True),
        pystray.MenuItem("🔄 刷新", _refresh),
        pystray.MenuItem("⚙ 设置", _settings),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", _exit),
    )

    icon = pystray.Icon(
        "deepseek_widget",
        _load_icon(),
        "DS 余额",
        menu=menu,
    )
    return icon


def update_tray_tooltip(icon: pystray.Icon, text: str):
    """更新托盘悬浮提示文字"""
    icon.title = text
