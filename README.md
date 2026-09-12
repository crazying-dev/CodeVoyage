# CodeVoyage

---

[![Stars](https://img.shields.io/github/stars/crazying-dev/CodeVoyage?style=flat&logo=data:image/svg%2bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZlcnNpb249IjEiIHdpZHRoPSIxNiIgaGVpZ2h0PSIxNiI%2bPHBhdGggZD0iTTggLjI1YS43NS43NSAwIDAgMSAuNjczLjQxOGwxLjg4MiAzLjgxNSA0LjIxLjYxMmEuNzUgMCAwIDEgLjQxNiAxLjI3OWwtMy4wNDYgMi45Ny43MTkgNC4xOTJhLjc1MS43NTEgMCAwIDEtMS4wODguNzkxTDggMTIuMzQ3bC0zLjc2NiAxLjk4YS43NS43NSAwIDAgMS0xLjA4OC0uNzlsLjcyLTQuMTk0TC44MTggNi4zNzRhLjc1Ljc1IDAgMCAxIC40MTYtMS4yOGw0LjIxLS42MTFMNy4zMjcuNjY4QS43NS43NSAwIDAgMSA4IC4yNVoiIGZpbGw9IiNlYWM1NGYiLz48L3N2Zz4%3d&logoSize=auto&label=Stars&labelColor=444444&color=eac54f)](https://github.com/crazying-dev/CodeVoyage)

---

![https://count.getloli.com/@crazying-dev](https://count.getloli.com/@crazying-dev)

---

## 开始之前
CodeVoyage是一个通过[`Action Workflow`](https://docs.github.com/zh/actions)和用户本机的使用AI对[`Issue`](https://docs.github.com/en/issues)进行快速的代码处理

[`本项目`](https://github.com/crazying-dev/CodeVoyage)旨在帮助`摆烂的开发者`快速处理来自`未知人员`的Issue任务，解放双手，哦不，是ctrl a，ctrl c和ctrl v

---

## 如何使用
使用CodeVoyage非常简单，只需要注册，然后下载安装，然后使用  
是不是非常简单  
好吧，我承认，是我懒得写文档了  
哦对了，如果你想要可视化控制可以在[安装](https://CodeVoyage.yjlt.top/install)并运行后通过[`http://127.0.0.1:5431`](http://127.0.0.1:5431)，但是貌似也没有其他控制方式

---

## 这个产品有哪些优点
1. [`传统GitHub API(即 Rest API)`](https://docs.github.com/zh/rest/about-the-rest-api)或[`GitHub GraphQL API`](https://docs.github.com/zh/graphql)的Token与LLM配置由服务端**加密存储**，控制台只显示脱敏值；本机仅保留一份同步缓存用于断网时继续工作，AI由用户自行配置
2. AI处理后的代码以[`Pull requests`](https://docs.github.com/en/rest/pulls)提交，全程更透明，不用担心AI发疯导致仓库损坏
3. AI处理沙盒运行，不导致AI发疯导致电脑错误
4. PR合并进主分支后，本次工作分支（`codevoyage/*`）会被自动销毁，仓库分支列表不会堆积
5. PR若与主分支冲突（例如主分支在任务执行期间前进过），CodeVoyage会自动合并主分支并解决冲突，尽量让PR保持「可合并」
6. 任务结束会自动回复Issue（PR提交后附带PR链接与结论摘要；失败 / 用户放弃也会给出原因），提交者不必盯着PR列表看结果；若PR存在无法自动安全解决的冲突，回复里会写明原因，避免PR静静停在冲突状态

---

## 安全说明
1. **上报地址与签名密钥不再写死**：workflow 中的 `CODEVOYAGE_WEBHOOK_URL` / `CODEVOYAGE_WEBHOOK_SECRET`
   从仓库 `Settings → Secrets and variables → Actions` 读取（Variables 优先，其次 Secrets）；
   未配置时上报步骤直接跳过，Issue 数据不会发往任何外部服务，且只接受 `https://` 地址。
2. **文件操作边界**：AI 的文件工具（读取 / 写入 / 列表）与 Git 工具统一做路径校验，
   绝对路径、`..`、符号链接逃逸一律拒绝；工作区必须位于 CodeVoyage 数据目录之下。
3. **本地敏感数据加密**：`local.json` / `repo_tokens.json` / `credentials_cache.json` 使用
   带认证的标准加密（`cryptography` 的 Fernet；缺少依赖时回退 HMAC-SHA256 流加密 + 认证标签），
   `secret.key` 与各敏感文件权限收紧为 0600；不希望凭据落盘可设置 `CODEVOYAGE_DISABLE_CRED_CACHE=1`。
4. **GitHub 代理默认关闭**：借用他人代连（`enabled`）与「本机作为代连节点」（`as_helper`）默认都不开启；
   开启代连节点必须显式授权（`confirm`），且只允许转发白名单内的公网目标，并限制并发、记录审计日志。
5. **远端回执脱敏**：回执给服务端的错误信息会去掉本机路径、令牌样式与带凭据的 URL。
6. **自动回复只写评论且先脱敏**：通知工作流只在 Issue / PR 下写评论，不改代码、不改分支、
   不关闭 / 不锁定 Issue、不加标签；正文（含结论摘要与失败原因）统一经 `centre.sanitize`
   去掉本机路径、令牌样式与带凭据 URL，避免把本机信息写到公开页面。

---

## 大致工作流程
> 一下内容中我的服务器称作服务器Server  

一切前提，用户已登录,登录凭证在`~/.CodeVoyage/user/conf.json`

Server -> `GetIssue`  本连接长轮询  
当GetIssue组件发现服务器获取到了绑定的仓库有新的Issue时将Issue的URL发送给本地中转中心5431端口  
5431发现从GetIssue传来的Issue链接时将其以`type:Issue message:{IssueURL}`的格式存入Agent的当前仓库处理队列 目录:`~/.CodeVoyage/Agent/repo/{"仓库作者/仓库名称"的哈希值}/wait/list.json`

Agent服务循环获取最新任务，若获取到的任务的仓库未在执行就弹窗提醒有新的Issue并附上Issue信息若用户10s未操作或点击Yes就开始执行，执行完成后删除本条Issue并将Issue的记录告诉Server已完成，若点击No就删除本条Issue队列，并告诉Server用户放弃执行
执行完成后将内容以Pr的方式提交，PR提交信息就填Conclusion(见后文)的内容

工作分支的生命周期（见Issue #13）：
1. Agent执行任务时创建`codevoyage/issue-<编号>-<uuid前6位>`工作分支并推送，然后创建PR
2. PR创建成功后，该分支被登记到本机待清理队列 目录:`~/.CodeVoyage/Agent/repo/{"仓库作者/仓库名称"的哈希值}/branches.json`
3. Agent空闲时（默认10分钟一次，任务结束后也会立即巡检一次）检查PR是否**已合并进仓库默认分支**：
   - 已合并 -> 调用GitHub API删除该工作分支，并记录日志
   - 未合并 / PR关闭但未合并 / 合并目标不是默认分支 -> 保留分支，不做任何破坏性操作
4. 安全约束：只处理`codevoyage/*`前缀分支，默认分支（main/master等）与受保护分支永不删除；除队列外还会做一次兜底发现，清理历史遗留的已合并工作分支
5. 手动查看或立即清理：调用`cleanup_branches`工具（`run=false`查看待清理队列，`run=true`立即销毁已合并的工作分支）

PR冲突自动处理（见Issue #19）：
1. **读PR**：`GithubTool/pulls.py` 提供 PR 列表、可合并状态（`mergeable` / `mergeable_state`）、
   改动文件与「用 base 更新 head」等能力；`mergeable=False` 或 `mergeable_state=dirty` 即判定为冲突
2. **处理PR**：`centre/pr_conflict.py` 负责把目标分支 `merge` 进 PR 源分支（**不用 rebase**），
   逐个冲突文件按策略解决，然后提交并**普通推送**（绝不 force push），最后在 PR 下留言说明改了什么
   - 解决策略：`auto`（默认，只解决可安全判定的冲突）/ `ours` / `theirs` / `union`
   - `auto` 只处理「一侧为空（纯新增）」或「仅空白差异」的冲突块，其余不猜测，整体回滚交给人工
3. **健壮约束**：只处理同仓库的 `codevoyage/*` 工作分支（Fork / 外部贡献者分支拒绝）；
   只处理 open、未合并、目标为默认分支的 PR；冲突文件数量与单文件体积有上限；二进制冲突不自动解决；
   工作区必须干净；解决后校验无残留冲突标记；任何失败都 `merge --abort` + `reset --hard` 回到处理前状态
4. **自动触发**：任务创建 PR 后程序自动检查一次冲突并尝试解决（失败只记轨迹，不影响任务结果）；
   AI 也可以主动调用工具处理其它 PR
5. 手动使用：`pr_conflicts(pr="")` 查看冲突中的 PR；`pr_conflicts(pr=15)` 看单个 PR 详情；
   `resolve_pr_conflicts(pr=15, dry_run=true)` 只看计划；确认后 `resolve_pr_conflicts(pr=15)` 执行

通知工作流（见Issue #20）：
1. **成功**：任务创建PR后程序自动在Issue下回复一条通知，附PR链接、工作分支（`codevoyage/*` → 默认分支）、
   **结论摘要**与**PR可合并状态**（含冲突是否已自动解决、没解决的原因）；有PR时同时在PR下留一条同样的通知
2. **失败**：在Issue下回复，附脱敏后的失败原因与「修正后可再次评论触发词重试」的提示
3. **放弃**：用户在本机控制台 / 右下角弹窗选择放弃时，在Issue下回复说明本次未改动任何内容
4. **幂等**：同一任务的同类通知只发一次，记录在`~/.CodeVoyage/Agent/repo/{"仓库作者/仓库名称"的哈希值}/notify.json`；
   已发送的通知也会写进任务步骤（控制台任务列表可见）
5. **可关闭**：设置环境变量`CODEVOYAGE_DISABLE_PR_NOTIFY=1`，或在本地配置中设置`pr_notify=false`
6. **安全约束**：只写评论（不关闭 / 不锁定Issue、不加标签、不改代码与分支）；正文先经`centre.sanitize`
   脱敏（本机路径、令牌样式、带凭据URL一律替换）；令牌走本仓库（细粒度 → 传统）顺序回退，
   不需要服务端配合；通知失败只记日志，绝不影响任务结果
7. 手动使用：AI 在任务过程中可调用`notify_issue(message, issue?, pr?)`在Issue / PR下留言（同样只写评论、先脱敏）

触发关键词（Issue #19 评论）：
- workflow 里的关键词匹配**不区分大小写、忽略多余空白**：`Ai Run` / `ai run` / `AI RUN` / `Ai  Run` 都能触发；
- **不限制触发人身份**：任何对该 Issue 有评论权限的人（包括其他合规、维护人员）都可以用关键词唤起 CodeVoyage，
  上报里会带上 `comment_author`，便于事后审计是谁触发的；
- 关键词列表由服务端下发（控制台生成 workflow 时写入 `KEYWORDS_JSON`），本仓库模板见 `workflows/base.yaml`；
  已安装的 workflow 需要重新生成（或在控制台重新提交）才能获得新的匹配规则。

Agent的工具(每个工具都单独一个文件):
1. Git仓库获取
2. Issue内容获取
3. 网络搜索
4. Plan
5. 文件写入
6. 文件读取
7. 工作分支清理（`cleanup_branches`）
8. PR冲突查看（`pr_conflicts`，只读）
9. PR冲突处理（`resolve_pr_conflicts`）
10. Issue / PR 通知回复（`notify_issue`，只写评论且先脱敏）

AI返回格式:
1. 标准Markdown格式
2. 包含Introduce(开始，包含对本问题的计划),Body(问题中，包含对问题的解决),Conclusion(结尾，包含对问题的解决方式的总结)

AI记忆保存位置:
`~/.CodeVoyage/Agent/repo/{"仓库作者/仓库名称"的哈希值}/history.json`  
对话信息保存位置(包括仓库目录，对话名等内容):
`~/.CodeVoyage/Agent/repo/{"仓库作者/仓库名称"的哈希值}/Info.json`

其他配置，如API Key,Girhub API token加密后储存在`~/.CodeVoyage/`下的文件中
