"""离开本机文本的脱敏（Issue #20：通知工作流）。

Agent 回执给服务端的错误信息、自动回复到 Issue / PR 的通知正文，都会离开本机
（服务端日志 / GitHub 页面）。所以统一在这里做一次脱敏，原始内容只留在本机日志：

- 本机目录：CodeVoyage 数据目录 / 工作区 / 用户主目录 → `<本地目录>`；
- 其它路径：Windows 绝对路径、UNC 路径、常见 *nix 用户目录 → `<路径>`；
- 凭据样式：`github_pat_*`、`ghp_/gho_/ghu_/ghs_/ghr_*`、`sk-*`、`Bearer xxx`、
  `Authorization: xxx`、URL 内嵌用户名密码 → 占位符 `***`；
- 结果按 limit 截断，避免把整段日志贴到公开页面。

纯函数、无副作用：Agent 回执（Agent._sanitize_error）与通知回复（centre.pr_notify）
共用这一份实现，避免两处规则不一致导致漏脱敏。
"""
import re

from centre import paths

# 需要脱敏的凭据样式
SECRET_PATTERNS = (
    re.compile(r"github_pat_[A-Za-z0-9_]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)\bauthorization\s*[:=]\s*\S+"),
    re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@"),          # URL 内嵌凭据
)

# 需要脱敏的本机路径样式
PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:\\[^\s\"'<>|,;)]+"),              # Windows 绝对路径
    re.compile(r"\\\\[^\s\"'<>|,;)]+"),                     # UNC 路径
    re.compile(r"/(?:home|Users|root|tmp)/[^\s\"'<>|,;)]+"),  # 常见 *nix 用户目录
)


def sanitize(text, limit: int = 800) -> str:
    """脱敏并截断文本。

    任何异常都退回「原始文本截断」，绝不因为脱敏失败而打断调用方（回执 / 通知都是
    辅助动作，不能影响任务本身）。
    """
    raw = str(text or "")
    if not raw:
        return ""
    try:
        for known in (paths.BASE_DIR, paths.REPO_DIR, paths.HOME_DIR):
            if known:
                raw = raw.replace(known, "<本地目录>")
        for pat in SECRET_PATTERNS:
            raw = pat.sub(lambda m: (m.group(1) + "***") if m.groups() else "***", raw)
        for pat in PATH_PATTERNS:
            raw = pat.sub("<路径>", raw)
    except Exception:
        pass
    raw = raw.strip()
    if limit and len(raw) > limit:
        raw = raw[: int(limit)] + "…"
    return raw
