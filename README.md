# CodeVoyage

---

[![Stars](https://img.shields.io/github/stars/crazying-dev/CodeVoyage?style=flat&logo=data:image/svg%2bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZlcnNpb249IjEiIHdpZHRoPSIxNiIgaGVpZ2h0PSIxNiI%2bPHBhdGggZD0iTTggLjI1YS43NS43NSAwIDAgMSAuNjczLjQxOGwxLjg4MiAzLjgxNSA0LjIxLjYxMmEuNzUuNzUgMCAwIDEgLjQxNiAxLjI3OWwtMy4wNDYgMi45Ny43MTkgNC4xOTJhLjc1MS43NTEgMCAwIDEtMS4wODguNzkxTDggMTIuMzQ3bC0zLjc2NiAxLjk4YS43NS43NSAwIDAgMS0xLjA4OC0uNzlsLjcyLTQuMTk0TC44MTggNi4zNzRhLjc1Ljc1IDAgMCAxIC40MTYtMS4yOGw0LjIxLS42MTFMNy4zMjcuNjY4QS43NS43NSAwIDAgMSA4IC4yNVoiIGZpbGw9IiNlYWM1NGYiLz48L3N2Zz4%3d&logoSize=auto&label=Stars&labelColor=444444&color=eac54f)](https://github.com/crazying-dev/CodeVoyage)

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

Agent的工具(每个工具都单独一个文件):
1. Git仓库获取
2. Issue内容获取
3. 网络搜索
4. Plan
5. 文件写入
6. 文件读取
7. 工作分支清理（`cleanup_branches`）

AI返回格式:
1. 标准Markdown格式
2. 包含Introduce(开始，包含对本问题的计划),Body(问题中，包含对问题的解决),Conclusion(结尾，包含对问题的解决方式的总结)

AI记忆保存位置:
`~/.CodeVoyage/Agent/repo/{"仓库作者/仓库名称"的哈希值}/history.json`  
对话信息保存位置(包括仓库目录，对话名等内容):
`~/.CodeVoyage/Agent/repo/{"仓库作者/仓库名称"的哈希值}/Info.json`

其他配置，如API Key,Girhub API token加密后储存在`~/.CodeVoyage/`下的文件中
