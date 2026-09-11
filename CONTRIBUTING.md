# 贡献指南

本项目接受 Issue 与 Pull Request，也支持由 CodeVoyage Agent 自动处理 Issue。
为了让历史可读、可追溯、可用于生成 CHANGELOG，**所有提交信息（以及 PR 标题）统一遵循
[Conventional Commits](https://www.conventionalcommits.org/zh-hans/) 规范**（对应 Issue #15）。

---

## 1. 提交信息格式

```
<type>(<scope>): <description>

<body>

<footer>
```

- 第一行是标题（subject），**不超过 72 字符**；
- 标题与正文之间、正文与页脚之间**各留一个空行**；
- 一个提交只做一件事；改动较大时用正文说清「为什么改」。

### type（必填，小写）

| type | 用途 | 示例 |
| --- | --- | --- |
| `feat` | 新增功能 | `feat(agent): 支持任务执行轨迹实时查看` |
| `fix` | 修复缺陷 | `fix(tools): 修复提交信息为空时的兜底逻辑` |
| `docs` | 仅文档变更 | `docs(contributing): 补充提交规范示例` |
| `style` | 格式/排版（不影响逻辑） | `style(console): 统一控制台缩进` |
| `refactor` | 重构（不改变行为） | `refactor(centre): 收敛凭据缓存读写` |
| `perf` | 性能优化 | `perf(agent): 减少历史会话重复写入` |
| `test` | 测试相关 | `test(tools): 补充提交信息校验用例` |
| `build` | 构建/依赖/打包 | `build(deps): 升级 requests 到 2.32` |
| `ci` | CI / workflow 配置 | `ci(workflow): 上报地址改为 Variables 配置` |
| `chore` | 其他杂项（默认兜底） | `chore(config): 整理本地默认配置` |
| `revert` | 回滚提交 | `revert: 回滚 <hash> 的提交` |

### scope（可选，小写）

表示受影响模块，取 **改动最集中的顶层目录**，常用值：

`agent`（Agent/）、`centre`（centre/）、`tools`（tools/）、`console`（src/）、
`info`、`github`（GithubTool/）、`ci`（.github/）、`docs`、`build`、`config`。

跨模块或全局改动可省略 scope，例如 `chore: 整理仓库根目录配置`。

### description（必填）

- 用**一句话**说清改了什么，中文即可；**结尾不加句号**；
- 使用祈使/陈述语气（“修复…”“新增…”），不要写“更新”“改了点东西”“fix bug”这类含糊描述。

### body（可选）

说明改动的动机、影响面、取舍，每行不超过 72 字符；不要重复标题内容。

### footer（可选）

| 页脚 | 用途 |
| --- | --- |
| `Refs #15` | 关联 Issue（不自动关闭） |
| `Closes #15` | 合并/进入默认分支后自动关闭 Issue |
| `BREAKING CHANGE: 说明` | 不兼容变更（必写迁移方式） |

---

## 2. 示例

推荐：

```
fix(tools): 修复提交信息缺少类型前缀的问题

原来 AI 只回传一句话，历史里看不出改动性质；
现在统一经 centre/commit_msg.py 规范化后再提交。

Refs #15
```

```
docs(contributing): 新增提交规范与示例

补充 type/scope/description 规则、页脚约定与正反例，
便于人工与 Agent 提交保持一致。

Refs #15
```

不推荐（会被规范化为合规信息，但请尽量一次写对）：

```
更新                     # 含糊，无 type、无 description
fix bug                  # 无 scope、无说明
feat: 新增功能。          # description 结尾多余句号
feat(懒加载): ...         # scope 需小写英文
```

---

## 3. 程序侧保障

- `centre/commit_msg.py` 是规范唯一实现：
  - `infer_type()` / `infer_scope()` / `summarize()`：从 Issue 标题与改动文件推断出合规信息；
  - `normalize()`：把不合规信息改写为合规形式（合规部分原样保留）；
  - `validate()`：校验提交信息，返回不合规原因；
  - `ensure()`：提交前统一入口，返回最终信息与改写说明；
  - `pr_title()`：生成符合规范的 PR 标题（`<type>(<scope>): <description> (#15)`）。
- `tools/RepoOps.py`：
  - `commit_and_push` 提交前调用 `ensure()`，自动规范化并补 `Refs #Issue`；
  - `create_pull_request` 用 `pr_title()` 规范化 PR 标题。
- `Agent/main.py`：AI 漏掉提交/建 PR 时的兜底信息同样走上述规范化，不会写入含糊的提交信息。
- `config.py`（AgentSystem 第 14 条）与 `Info/Agent.py` 工具描述：要求 AI 直接按规范产出行信息。

人工提交可启用仓库自带模板：

```
git config commit.template .gitmessage
```

---

## 4. PR 约定

- 标题同样遵循 `<type>(<scope>): <description> (#Issue编号)`；
- 正文说明：改了什么、为什么改、如何验证；
- CodeVoyage Agent 创建的 PR 使用 `codevoyage/issue-<编号>-<uuid前6位>` 分支，PR 合并进默认分支后由后台巡检自动销毁（见 Issue #13）。
