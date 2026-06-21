"""
API 调用模块 — 调用 DeepSeek /user/balance 接口获取余额
使用标准库 urllib，无需第三方依赖
"""
import json
import time
import socket
import urllib.request
import urllib.error

API_URL = "https://api.deepseek.com/user/balance"
TIMEOUT = 10
MAX_RETRIES = 2


class BalanceError(Exception):
    """余额查询错误"""
    pass


def fetch_balance(api_key: str) -> dict:
    """
    使用 API Key 查询余额。
    返回格式: {"total_balance": float}
    """
    if not api_key:
        raise BalanceError("API Key 未配置")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }

    req = urllib.request.Request(API_URL, headers=headers, method="GET")

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = urllib.request.urlopen(req, timeout=TIMEOUT)
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            return _parse_response(data)

        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise BalanceError("API Key 无效或已过期 (401)")
            elif e.code == 429:
                raise BalanceError("请求太频繁，请稍后重试 (429)")
            else:
                last_error = BalanceError(f"API 返回错误: HTTP {e.code}")

        except socket.timeout:
            last_error = BalanceError("请求超时")

        except urllib.error.URLError as e:
            if isinstance(e.reason, socket.timeout):
                last_error = BalanceError("请求超时")
            elif isinstance(e.reason, (ConnectionRefusedError, ConnectionResetError, OSError)):
                last_error = BalanceError("网络连接失败")
            else:
                last_error = BalanceError(f"网络请求异常: {e.reason}")

        except OSError as e:
            last_error = BalanceError(f"网络连接失败: {e}")

        except BalanceError:
            raise

        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as e:
            last_error = BalanceError(f"响应解析失败: {e}")

        if attempt < MAX_RETRIES:
            time.sleep(1.5 * (attempt + 1))

    raise last_error


def _parse_response(data: dict) -> dict:
    """解析 API 响应"""
    balance_infos = data.get("balance_infos", [])
    if not balance_infos:
        return {"total_balance": 0.0}

    info = balance_infos[0]
    return {
        "total_balance": float(info.get("total_balance", 0)),
    }
