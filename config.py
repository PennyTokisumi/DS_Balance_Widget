"""
配置模块 — 读写 ~/.deepseek_widget/config.json
"""
import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".deepseek_widget"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "api_key": "",
    "refresh_interval": 1,            # 分钟
    "window_x": None,
    "window_y": None,
    "window_on_top": True,
    "opacity": 0.85,
    "warning_threshold": 10.0,
    "alert_threshold": 5.0,
    "auto_start": False,              # 开机自启动
}


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        config = dict(DEFAULT_CONFIG)
        config.update(saved)
        return config
    except (json.JSONDecodeError, IOError):
        return dict(DEFAULT_CONFIG)


def save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def get_api_key(config: dict) -> str:
    if config.get("api_key"):
        return config["api_key"]
    return os.environ.get("DEEPSEEK_API_KEY", "")
