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
1. 全部的[`传统GitHub API(即 Rest API)`](https://docs.github.com/zh/rest/about-the-rest-api)或[`GitHub GraphQL API`](https://docs.github.com/zh/graphql)的Token都储存在本地，严格拒绝上传，AI由用户自行配置，隐私信息更有保障
2. AI处理后的代码以[`Pull requests`](https://docs.github.com/en/rest/pulls)提交，全程更透明，不用担心AI发疯导致仓库损坏
3. AI处理沙盒运行，不导致AI发疯导致电脑错误

---

## 大致工作流程
用户登录:通过Email 和 Password验证用户，通过邮件发送登录提醒，返回Token（Eamil，ID，Password的集合的哈希值）  
当从/api/Github/Issue收到信息时将对应Issue和仓库，绑定的用户储存在数据库中，状态为wait，并赋予一个UUID  
当Issue被获取后状态标记为Ready  
当收到用户发来某个UUID所对应的Issue被用户抛弃时将状态标记为PutOut  
完成时标记OK  
用户可以随时查看完成记录  


注：每个仓库只能被一个用户绑定，绑定后用户设置关键词，生成workflow文件并指导用户放在指定位置，当某个Issue中的某个对话由管理员发起且包含关键词就将Issue的URL给到https://CodeVoyage.yjlt.top/api/Github/Issue

---

## 环境变量

在项目根目录放一个 `.env`（可用 `.env.example` 作模板）：

| 变量 | 说明 |
| --- | --- |
| `PORT` | 监听端口，默认 5431 |
| `WEBHOOK_SECRET` | workflow 上报的签名密钥。生成 workflow 时会**直接写进文件**，无需再配置仓库 Secrets |
| `PUBLIC_BASE_URL` | 对外可访问的服务地址，用于写死进 workflow；留空则按请求推断（非本机默认 https） |
| `DATABASE_URL` | 数据库；留空为 `instance/codevoyage.db`，也可填 PostgreSQL DSN |
| `CREDENTIAL_KEY` | 凭据加密主密钥；留空时自动生成并保存到 `instance/credential.key`，**更换后已存凭据将无法解密，请备份** |
| `MAIL_*` | SMTP 邮件配置，不填则邮件停用（只写日志） |

## 凭据存储

用户的 GitHub Token 与 LLM 配置存于服务端 `credentials` 表，落库前加密（前缀 `f1:` = Fernet，`v1:` = 标准库回退）。
控制台列表接口只返回脱敏值，明文仅在 `/api/user/credentials/reveal` 返回给已登录的本人。

接口一览：

- `/api/user/credentials/list`　脱敏列表（按顺序即回退顺序）
- `/api/user/credentials/add`　新增（LLM 需带 `base_url` / `model`；仓库专属令牌带 `repo`）
- `/api/user/credentials/update`　修改
- `/api/user/credentials/remove`　删除
- `/api/user/credentials/move`　上移 / 下移（`direction=up|down`）
- `/api/user/credentials/reveal`　取回明文（仅本人）
