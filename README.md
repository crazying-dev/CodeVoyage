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
