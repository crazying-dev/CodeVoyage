"""Git 提交信息规范（Conventional Commits）—— 对应 Issue #15。

问题：仓库历史里的提交信息要么含糊（“更新”“改了点东西”“fix bug”），要么不符合任何约定，
无法据此回溯改动、也无法自动生成 CHANGELOG，PR 与提交的对应关系难以维护。

做法：把规范收敛成一处实现，Agent 提交 / 建 PR 时统一走这里：
  1. 从 Issue 标题与改动文件推断 type / scope / description；
  2. AI 给出的提交信息不合规时自动改写（合规的原样保留）；
  3. 提供 validate 供调用方判断，避免不合规信息落到历史里。

规范格式：
  <type>(<scope>): <description>
  <空行>
  <body>
  <空行>
  <footer>          # Refs #15 / Closes #15 / BREAKING CHANGE: ...

完整说明与示例见仓库根目录 CONTRIBUTING.md。
"""
import os
import re

# 标题（type(scope)!: description）建议不超过 72 字符，PR 标题上限 100 字符
MAX_SUBJECT = 72
MAX_PR_TITLE = 100
# 提交正文上限（兜底提交可能把整段结论塞进来，这里做一次收口）
MAX_BODY = 500

# 允许的 type：与 Conventional Commits 对齐（可自行扩展，但需同步 CONTRIBUTING.md）
TYPES = ("feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert")

# 从 Issue 标题推断 type 的关键词（顺序即优先级；命中的第一组生效）
TYPE_KEYWORDS = (
    ("revert", ("回滚", "撤销提交", "revert")),
    ("fix", ("修复", "修好", "修正", "解决", "错误", "报错", "异常", "崩溃", "漏洞",
             "bug", "fix", "hotfix", "缺陷")),
    ("feat", ("新增", "添加", "增加", "支持", "实现", "feature", "support", "add", "introduce")),
    ("perf", ("性能", "提速", "perf", "performance")),
    ("refactor", ("重构", "refactor", "cleanup")),
    ("docs", ("文档", "注释", "readme", "doc", "docs")),
    ("test", ("测试", "单测", "用例", "test", "pytest", "vitest")),
    ("build", ("构建", "依赖", "打包", "build", "deps", "dependency")),
    ("ci", ("持续集成", "流水线", "ci", "workflow", "action")),
    ("style", ("格式", "排版", "缩进", "style", "format", "lint")),
)

# 改动文件全是某一类时，用于兜底推断 type（顺序即优先级）
_FILES_TYPE_RULES = (
    ("docs", (".md", ".mdx", ".rst", ".txt")),
    ("ci", (".github/workflows/", ".github/actions/")),
    ("test", ("tests/", "test/", "_test.", ".test.", ".spec.")),
    ("style", (".css", ".scss")),
    ("build", ("requirements.txt", "package.json", "package-lock.json", "tsconfig.json",
               "vite.config.ts", "pyproject.toml", "setup.py", "dockerfile")),
)

# 顶部目录 → scope 别名（scope 统一小写、简短、可读）
_SCOPE_ALIASES = {
    ".github": "ci",
    "workflows": "workflow",
    "src": "console",
    "tools": "tools",
    "centre": "centre",
    "agent": "agent",
    "infos": "info",
}

# 根目录单文件 → scope 别名
_FILE_SCOPE_ALIASES = {
    "readme": "docs",
    "contributing": "docs",
    "changelog": "docs",
    "license": "docs",
    "requirements": "build",
    "package": "build",
    "package-lock": "build",
    "tsconfig": "build",
    "vite.config": "build",
    "pyproject": "build",
    "setup": "build",
    "dockerfile": "build",
    "index": "console",
}

# 页脚关键字：这些行单独成段，被视为 footer 而不是 body
_FOOTER_PREFIXES = (
    "refs ", "refs:", "closes ", "closes:", "fixes ", "fixes:", "resolves ", "resolves:",
    "breaking change", "breaking-change:", "signed-off-by", "co-authored-by",
)

SUBJECT_RE = re.compile(
    r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^()]*)\))?(?P<breaking>!)?:\s*(?P<desc>.+)$"
)
_SCOPE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")


# ------------------------------------------------------------------ 推断
def _hit(text_low: str, word: str) -> bool:
    """英文关键词按词边界匹配（避免 `prefix` 命中 `fix`），中文关键词直接子串匹配。"""
    word = word.lower()
    if word.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text_low) is not None
    return word in text_low


def infer_type(text: str = "", files=None) -> str:
    """从 Issue 标题（或提交信息正文）推断 type；标题给不出信号时看改动文件，最后兜底 chore。"""
    low = str(text or "").lower()
    if low:
        for type_, words in TYPE_KEYWORDS:
            if any(_hit(low, w) for w in words):
                return type_
    return infer_type_from_files(files) or "chore"


def infer_type_from_files(files) -> str:
    """改动文件全部命中同一类规则时才给出 type（避免误判跨模块改动）。"""
    items = [str(f).replace("\\", "/").lower() for f in (files or []) if str(f).strip()]
    if not items:
        return ""
    for type_, markers in _FILES_TYPE_RULES:
        if all(any(marker in item for marker in markers) for item in items):
            return type_
    return ""


def infer_scope(files) -> str:
    """从改动文件推断 scope：取出现次数最多的顶层目录（根目录单文件取文件名）。"""
    counts: dict = {}
    for raw in files or []:
        path = str(raw).replace("\\", "/").strip().strip('"')
        parts = [p for p in path.split("/") if p not in ("", ".")]
        if not parts:
            continue
        if len(parts) == 1:
            stem = os.path.splitext(parts[0])[0].lower()
            head = _FILE_SCOPE_ALIASES.get(stem, stem)
        else:
            head = parts[0].lower()
            head = _SCOPE_ALIASES.get(head, head)
        if head:
            counts[head] = counts.get(head, 0) + 1
    if not counts:
        return ""
    scope = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return scope if _valid_scope(scope) else ""


def _valid_scope(scope: str) -> bool:
    return bool(scope) and bool(_SCOPE_RE.match(scope))


def summarize(text: str, limit: int = 60) -> str:
    """取第一行有效文本作为 description：压平空白、去掉 Markdown 标记与结尾标点。"""
    line = ""
    for raw in str(text or "").splitlines():
        line = raw.strip().lstrip("-*#> ").strip()
        if line:
            break
    line = re.sub(r"\s+", " ", line).strip(" \t。.;；,，、:：!！?？")
    return _truncate(line, limit)


def _truncate(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(1, limit - 1)].rstrip()}…"


def _truncate_block(text: str, limit: int) -> str:
    """按整体长度截断多行文本（保留换行结构）。"""
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    body = text[: max(1, limit - 1)].rstrip()
    return f"{body}…"


# ------------------------------------------------------------------ 组装
def build(type_: str, scope: str = "", description: str = "", body: str = "", footer: str = "") -> str:
    """按规范拼装提交信息（type 非法时回退 chore）。"""
    type_ = (type_ or "").strip().lower()
    if type_ not in TYPES:
        type_ = "chore"
    scope = (scope or "").strip().lower()
    head = f"{type_}({scope})" if _valid_scope(scope) else type_
    desc = summarize(description) or "更新项目代码"
    desc = _truncate(desc, max(12, MAX_SUBJECT - len(head) - 2))
    out = f"{head}: {desc}"
    if str(body or "").strip():
        out += f"\n\n{_truncate_block(body, MAX_BODY)}"
    if str(footer or "").strip():
        out += f"\n\n{str(footer).strip()}"
    return out


def issue_footer(issue_number, close: bool = False) -> str:
    """Issue 关联页脚：Refs #15（关联）/ Closes #15（合并后自动关闭）。"""
    number = str(issue_number or "").strip()
    if not number or number == "None":
        return ""
    return f"{'Closes' if close else 'Refs'} #{number}"


def _with_issue_footer(message: str, issue_number) -> str:
    ref = issue_footer(issue_number)
    text = str(message or "").strip()
    if not ref or not text or f"#{issue_number}" in text:
        return text
    return f"{text}\n\n{ref}"


# ------------------------------------------------------------------ 校验 / 改写
def validate(message: str) -> tuple[bool, str]:
    """校验提交信息是否合规，返回 (是否合规, 不合规原因)。"""
    text = str(message or "").strip()
    if not text:
        return False, "提交信息不能为空"
    lines = text.splitlines()
    subject = lines[0].strip()
    m = SUBJECT_RE.match(subject)
    if not m:
        return False, "标题需为 `<type>(<scope>): <description>`（如 `fix(agent): 修复兜底提交信息`）"
    type_ = m.group("type")
    if type_ != type_.lower():
        return False, "type 需使用小写"
    if type_.lower() not in TYPES:
        return False, f"未知 type `{type_}`，允许：{'、'.join(TYPES)}"
    scope = (m.group("scope") or "").strip()
    if scope and not _valid_scope(scope.lower()):
        return False, f"scope `{scope}` 需为小写字母/数字，可含 . _ / -"
    desc = (m.group("desc") or "").strip()
    if not desc:
        return False, "description 不能为空"
    if desc.endswith(("。", ".")):
        return False, "description 结尾不要加句号"
    if len(subject) > MAX_SUBJECT:
        return False, f"标题需 ≤ {MAX_SUBJECT} 字符（当前 {len(subject)}）"
    if len(lines) > 1 and lines[1].strip():
        return False, "标题与正文之间需要一个空行"
    if len(text) > 4000:
        return False, "提交信息过长（超过 4000 字符），请把细节放到 PR 描述里"
    return True, ""


def _split_body_footer(text: str) -> tuple[str, str]:
    """把正文末尾的页脚段落拆出来（页脚段落每行都以 Refs/Closes/BREAKING CHANGE 等开头）。"""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", str(text or "").strip()) if b.strip()]
    footer_blocks: list = []
    while blocks:
        last_lines = [l.strip() for l in blocks[-1].splitlines() if l.strip()]
        if last_lines and all(l.lower().startswith(_FOOTER_PREFIXES) for l in last_lines):
            footer_blocks.insert(0, blocks.pop())
        else:
            break
    return "\n\n".join(blocks), "\n\n".join(footer_blocks)


def normalize(message: str = "", *, type_hint: str = "", scope_hint: str = "",
              files=None, issue_number=None, title: str = "") -> str:
    """把任意提交信息改写为合规形式：合规的 type/scope 保留，其余按推断补齐。

    保证返回值通过 validate（标题长度、结尾标点、空行等都已处理）。
    """
    text = str(message or "").strip()
    lines = text.splitlines()
    subject_raw = lines[0].strip() if lines else ""
    body_raw = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""

    m = SUBJECT_RE.match(subject_raw)
    if m:
        desc = summarize(m.group("desc"))
        breaking = bool(m.group("breaking"))
    else:
        desc = summarize(subject_raw) or summarize(title)
        breaking = False
    if not desc:
        desc = summarize(title) or "更新项目代码"

    type_ = (m.group("type").lower() if m else "") or (type_hint or "").strip().lower()
    if type_ not in TYPES:
        type_ = infer_type(title or subject_raw, files)

    scope = ((m.group("scope") or "").strip().lower() if m else "") or (scope_hint or "").strip().lower()
    if not _valid_scope(scope):
        scope = infer_scope(files)

    head = f"{type_}({scope})" if scope else type_
    if breaking:
        head += "!"
    desc = _truncate(desc, max(12, MAX_SUBJECT - len(head) - 2))

    body, footer = _split_body_footer(body_raw) if body_raw else ("", "")
    if issue_number and f"#{issue_number}" not in footer:
        ref = issue_footer(issue_number)
        footer = f"{footer}\n\n{ref}".strip() if footer else ref

    out = f"{head}: {desc}"
    if body:
        out += f"\n\n{_truncate_block(body, MAX_BODY)}"
    if footer:
        out += f"\n\n{footer}"
    return out


def ensure(message: str = "", *, files=None, issue_number=None, title: str = "",
           scope_hint: str = "") -> tuple[str, str]:
    """提交前统一入口：返回 (合规提交信息, 说明)。说明为空表示原信息已合规。"""
    text = str(message or "").strip()
    if validate(text)[0]:
        out = _with_issue_footer(text, issue_number)
        note = "" if out == text else "提交信息已符合规范，补充 Issue 关联页脚"
        return out, note
    fixed = normalize(text, files=files, issue_number=issue_number, title=title,
                      scope_hint=scope_hint)
    if not validate(fixed)[0]:
        fixed = build(infer_type(title, files), scope_hint or infer_scope(files),
                      summarize(title) or summarize(text) or "更新项目代码",
                      footer=issue_footer(issue_number))
    original = summarize(text, 40) or "（空）"
    return fixed, f"提交信息不合规，已按 Conventional Commits 改写（原信息：{original}）"


def pr_title(title: str = "", issue_number=None, files=None) -> str:
    """PR 标题同样走规范：`<type>(<scope>): <description> (#15)`（GitHub 上限 100 字符）。"""
    subject = normalize(title, files=files, title=title).splitlines()[0].strip()
    ref = f" (#{issue_number})" if issue_number else ""
    return f"{subject[: max(1, MAX_PR_TITLE - len(ref))].rstrip()}{ref}"
