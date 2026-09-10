"""配置化 SMTP 邮件模块。

从 .env 读取：
    MAIL_HOST      smtp 服务器（不配置则邮件功能停用，仅记日志）
    MAIL_PORT      端口，默认 465
    MAIL_USER      账号
    MAIL_PASS      授权码/密码
    MAIL_FROM      发件人（默认取 MAIL_USER）
    MAIL_USE_SSL   1/true 走 SSL（默认），否则 STARTTLS

未配置时 send_mail 返回 False，不影响业务主流程。
"""
import logging
import os
import smtplib
from email.header import Header
from email.mime.text import MIMEText

logger = logging.getLogger("CodeVoyage.mail")


def _cfg() -> dict | None:
    host = os.getenv("MAIL_HOST", "").strip()
    if not host:
        return None
    user = os.getenv("MAIL_USER", "").strip()
    return {
        "host": host,
        "port": int(os.getenv("MAIL_PORT", "465") or 465),
        "user": user,
        "pass": os.getenv("MAIL_PASS", ""),
        "from": os.getenv("MAIL_FROM", "").strip() or user,
        "ssl": str(os.getenv("MAIL_USE_SSL", "1")).lower() in ("1", "true", "yes", "on"),
    }


def send_mail(to: str, subject: str, text: str) -> bool:
    """发送纯文本邮件，失败/未配置时记录日志并返回 False。"""
    cfg = _cfg()
    if not cfg or not cfg["user"] or not cfg["pass"] or not to:
        logger.info("[mail disabled] to=%s subject=%s", to, subject)
        return False
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = cfg["from"]
    msg["To"] = to
    try:
        if cfg["ssl"]:
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=10)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=10)
            server.starttls()
        with server:
            server.login(cfg["user"], cfg["pass"])
            server.sendmail(cfg["from"], [to], msg.as_string())
        logger.info("[mail sent] to=%s subject=%s", to, subject)
        return True
    except Exception as e:  # 邮件失败不影响主流程
        logger.warning("[mail failed] to=%s subject=%s error=%s", to, subject, e)
        return False


def login_reminder(to: str, email: str) -> bool:
    return send_mail(
        to,
        "CodeVoyage 登录提醒",
        "您的 CodeVoyage 账号刚刚完成登录。\n"
        f"账号邮箱：{email}\n"
        "如非本人操作，请及时修改密码并检查仓库绑定信息。",
    )


def task_done_mail(to: str, issue_url: str, pr_url: str, repo_full: str, issue_number: int) -> bool:
    return send_mail(
        to,
        f"CodeVoyage 任务完成：{repo_full}#{issue_number}",
        "AI 已完成一个 Issue 任务并提交 Pull Request。\n"
        f"仓库：{repo_full}\nIssue：{issue_url}\nPull Request：{pr_url}",
    )


def task_failed_mail(to: str, issue_url: str, repo_full: str, issue_number: int, error: str) -> bool:
    return send_mail(
        to,
        f"CodeVoyage 任务失败：{repo_full}#{issue_number}",
        "AI 处理 Issue 时失败。\n"
        f"仓库：{repo_full}\nIssue：{issue_url}\n错误信息：{error or '未知错误'}",
    )
