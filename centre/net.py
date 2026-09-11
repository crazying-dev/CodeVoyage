"""统一的网络访问配置。

背景：Windows/公司网络里常见 HTTP_PROXY / HTTPS_PROXY 环境变量指向一个不可用的代理，
会导致 requests 报 ProxyError（Unable to connect to proxy），把一切远端调用拖死。
这里的策略：
- 默认 **忽略系统代理**（trust_env = False），直连；
- 如需代理，用环境变量 CODEVOYAGE_PROXY 显式指定（例如 http://127.0.0.1:7890）；
- 网络类错误按 CODEVOYAGE_RETRY / CODEVOYAGE_RETRY_INTERVAL 重试（默认 5 次、间隔 5s）。
"""
import os
import time

import requests

DEFAULT_RETRY = 5
DEFAULT_RETRY_INTERVAL = 5.0

# 值得重试的网络异常
NETWORK_ERRORS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ProxyError,
    requests.exceptions.SSLError,
    requests.exceptions.ChunkedEncodingError,
)


def proxy_url() -> str:
    return (os.getenv("CODEVOYAGE_PROXY") or "").strip()


def apply_proxy_policy() -> None:
    """全局代理策略：默认剔除系统代理环境变量，仅保留 CODEVOYAGE_PROXY 指定的代理。

    这样 requests / PyGithub / git 子进程都不会再被失效的系统代理拖死。
    """
    proxy = proxy_url()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    if proxy:
        os.environ["HTTP_PROXY"] = os.environ["http_proxy"] = proxy
        os.environ["HTTPS_PROXY"] = os.environ["https_proxy"] = proxy


apply_proxy_policy()


def retry_config() -> tuple[int, float]:
    """返回 (重试次数, 重试间隔秒)。"""
    try:
        times = int(os.getenv("CODEVOYAGE_RETRY", str(DEFAULT_RETRY)))
    except ValueError:
        times = DEFAULT_RETRY
    try:
        interval = float(os.getenv("CODEVOYAGE_RETRY_INTERVAL", str(DEFAULT_RETRY_INTERVAL)))
    except ValueError:
        interval = DEFAULT_RETRY_INTERVAL
    return max(0, times), max(0.0, interval)


def _log(message: str) -> None:
    try:
        from centre import paths

        paths.append_log(message)
    except Exception:
        pass


def new_session() -> requests.Session:
    session = requests.Session()
    # 默认忽略系统/环境代理，避免被失效代理拖死
    session.trust_env = False
    proxy = proxy_url()
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    return session


def request(method: str, url: str, timeout: float = 30, retries: int | None = None, **kwargs) -> requests.Response:
    """带重试的请求。默认沿用 CODEVOYAGE_RETRY（5 次 / 5 秒）。"""
    times = retry_config()[0] if retries is None else max(0, retries)
    interval = retry_config()[1]
    last_error: Exception | None = None
    for attempt in range(times + 1):
        try:
            with new_session() as session:
                return session.request(method, url, timeout=timeout, **kwargs)
        except NETWORK_ERRORS as e:
            last_error = e
            if attempt >= times:
                break
            _log(f"网络请求失败（第 {attempt + 1}/{times + 1} 次）：{url} -> {e}；{interval:.0f}s 后重试")
            time.sleep(interval)
    assert last_error is not None
    raise last_error


def latency(url: str, timeout: float = 8) -> dict:
    """探测单个地址的往返延迟（毫秒），用于实时展示 GitHub 可达性。不重试，避免拖长等待。"""
    start = time.perf_counter()
    try:
        with new_session() as session:
            resp = session.head(url, timeout=timeout, allow_redirects=False)
        return {
            "ok": True,
            "status": resp.status_code,
            "ms": int((time.perf_counter() - start) * 1000),
            "reason": "",
        }
    except Exception as e:
        return {
            "ok": False,
            "status": 0,
            "ms": int((time.perf_counter() - start) * 1000),
            "reason": friendly_error(e),
        }


def friendly_error(err) -> str:
    """把网络异常翻译成可操作提示。"""
    text = str(err)
    if isinstance(err, requests.exceptions.ProxyError) or "proxy" in text.lower():
        return (
            "网络代理不可用：系统/环境变量里的 HTTP(S)_PROXY 指向的代理连不上。\n"
            "已默认忽略系统代理直连；如需代理，请设置环境变量 CODEVOYAGE_PROXY（例如 http://127.0.0.1:7890）后重启。\n"
            f"原始信息：{text[:200]}"
        )
    if isinstance(err, requests.exceptions.ConnectTimeout) or "timed out" in text.lower():
        return f"连接超时（已重试）：{text[:200]}"
    if isinstance(err, requests.exceptions.SSLError):
        return f"TLS/证书错误：{text[:200]}"
    return text[:300]
