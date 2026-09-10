<script setup lang="ts">
import { ref, onMounted } from 'vue'
import HeadDOM from './header.vue'
import footerDOM from './footer.vue'

interface Collaborator {
  login: string
  avatar_url: string
  html_url: string
}

const collaborators = ref<Collaborator[]>([])

onMounted(async () => {
  try {
    // GitHub 协作者接口必须认证(匿名请求返回 401)；在 .env.local 配置 VITE_GITHUB_TOKEN 后走协作者接口
    const token: string | undefined = import.meta.env.VITE_GITHUB_TOKEN
    const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {}
    const collaboratorsUrl = 'https://api.github.com/repos/crazying-dev/CodeVoyage/collaborators'
    const contributorsUrl = 'https://api.github.com/repos/crazying-dev/CodeVoyage/contributors'

    // 无 token 时直接使用公开的 contributors 接口，避免无效的 401 请求
    let res = await fetch(token ? collaboratorsUrl : contributorsUrl, { headers })
    if (!res.ok && token) {
      res = await fetch(contributorsUrl, { headers })
    }
    if (res.ok) {
      collaborators.value = await res.json()
    }
  } catch (e) {
    console.error('获取协作者失败', e)
  }
})
</script>

<template>
  <HeadDOM />
  <div id="page-main">
    <div id="head">
      <h1>CodeVoyage</h1>
    </div>
    <hr>
    <div id="cont">
      <a href="https://github.com/crazying-dev/CodeVoyage"><img src="https://img.shields.io/github/stars/crazying-dev/CodeVoyage?style=flat&logo=data:image/svg%2bxml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZlcnNpb249IjEiIHdpZHRoPSIxNiIgaGVpZ2h0PSIxNiI%2bPHBhdGggZD0iTTggLjI1YS43NS43NSAwIDAgMSAuNjczLjQxOGwxLjg4MiAzLjgxNSA0LjIxLjYxMmEuNzUuNzUgMCAwIDEgLjQxNiAxLjI3OWwtMy4wNDYgMi45Ny43MTkgNC4xOTJhLjc1MS43NTEgMCAwIDEtMS4wODguNzkxTDggMTIuMzQ3bC0zLjc2NiAxLjk4YS43NS43NSAwIDAgMS0xLjA4OC0uNzlsLjcyLTQuMTk0TC44MTggNi4zNzRhLjc1Ljc1IDAgMCAxIC40MTYtMS4yOGw0LjIxLS42MTFMNy4zMjcuNjY4QS43NS43NSAwIDAgMSA4IC4yNVoiIGZpbGw9IiNlYWM1NGYiLz48L3N2Zz4%3d&logoSize=auto&label=Stars&labelColor=444444&color=eac54f" alt=""></a>
    </div>
    <hr>
    <img src="https://count.getloli.com/@crazying-dev" alt="count">
    <hr>
    <div id="doc-start">
      <h2>开始之前</h2>
      <p>CodeVoyage是一个通过<a href="https://docs.github.com/zh/actions">Action Workflow</a>和用户本机的使用AI对<a href="https://docs.github.com/en/issues">Issue</a>进行快速的代码处理</p>
      <p><a href="https://github.com/crazying-dev/CodeVoyage">本项目</a>旨在帮助<code>摆烂的开发者</code>快速处理来自<code>未知人员</code>的Issue任务，解放双手，哦不，是ctrl a，ctrl c和ctrl v</p>
    </div>
    <hr>
    <div id="doc-how">
      <h2>如何使用</h2>
      <p>使用CodeVoyage非常简单，只需要注册，然后下载安装，然后使用</p>
      <p>是不是非常简单</p>
      <p>好吧，我承认，是我懒得写文档了</p>
      <p>哦对了，如果你想要可视化控制可以在<a href="/install">安装</a>并运行后通过<a href="http://127.0.0.1:5431">http://127.0.0.1:5431</a>，但是貌似也没有其他控制方式</p>
    </div>
    <hr>
    <div id="doc-advantages">
      <h2>这个产品有哪些优点</h2>
      <ol>
        <li>全部的<a href="https://docs.github.com/zh/rest/about-the-rest-api">传统GitHub API(即 Rest API)</a>或<a href="https://docs.github.com/zh/graphql">GitHub GraphQL API</a>的Token都储存在本地，严格拒绝上传，AI由用户自行配置，隐私信息更有保障</li>
        <li>AI处理后的代码以<a href="https://docs.github.com/en/rest/pulls">Pull requests</a>提交，全程更透明，不用担心AI发疯导致仓库损坏</li>
        <li>AI处理沙盒运行，不导致AI发疯导致电脑错误</li>
      </ol>
    </div>
    <hr>
    <div id="collaborator">
      <h2>协作者</h2>
      <div id="collaborator-cards">
        <a v-for="c in collaborators" :key="c.login" class="collaborator-card" :href="c.html_url" target="_blank">
          <img :src="c.avatar_url" :alt="c.login">
          <span>{{ c.login }}</span>
        </a>
      </div>
    </div>
  </div>
  <footerDOM />
</template>

<style scoped>
#cont {
  height: 50px;
}

#cont * {
  padding-top: 15px;
}

#page-main p,
#page-main li {
  color: white;
  font-size: 18px;
  line-height: 1.6;
}

#page-main a {
  font-size: inherit;
}

#page-main h2 {
  margin: 16px 0 8px;
}

#page-main code {
  color: white;
  background: rgb(255 255 255 / 0.15);
  padding: 2px 6px;
  border-radius: 4px;
}

#collaborator-cards {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-top: 12px;
}

.collaborator-card {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 18px;
  background: rgb(40 40 40);
  padding: 12px;
  border-radius: 8px;
}

.collaborator-card:hover {
  background: rgb(60 60 60);
}

.collaborator-card img {
  width: 40px;
  height: 40px;
  border-radius: 50%;
}
</style>
