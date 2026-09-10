<template>
  <HeadDOM />
  <div id="HomeBackgroundImg">
    <div>
      第一个背景
    </div>
    <div>

    </div>
  </div>
  <div id="one" class="HomePart">
    <p id="HomeOneTitle" ref="HomeOneTitleRef"></p>
  </div>
  <div id="two" class="HomePart">
    <p>1234</p>
  </div>
  
  <footerDOM />
  <button class="scroll-down" aria-label="向下翻页" @click="scrollDown">
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <polyline points="6 9 12 15 18 9" />
    </svg>
  </button>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import HeadDOM from './header.vue'
import footerDOM from './footer.vue'

function scrollDown() {
  const sections = Array.from(document.querySelectorAll<HTMLElement>('.HomePart'))
  const idx = Math.round(window.scrollY / window.innerHeight)
  const next = idx + 1
  if (next >= sections.length) {
    window.scrollTo({ top: 0, behavior: 'smooth' })
  } else {
    sections[next].scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}


// DOM ref
const HomeOneTitleRef = ref<HTMLElement | null>(null)
let timer: number | null = null
const targetText = "Hello,stranger\nWelcome\n\tCodeVoyage"

/**
 * 打字机动画
 */
const updateHomeonetitle = () => {
  const el = HomeOneTitleRef.value
  if (!el) return

  // 清除上一次定时器，防止重复调用叠加
  if (timer !== null) {
    clearInterval(timer)
    timer = null
  }

  el.innerText = ''
  let index = 0

  timer = window.setInterval(() => {
    index++
    el.innerText = targetText.slice(0, index)
    if (index >= targetText.length) {
      clearInterval(timer!)
      timer = null
    }
  }, 100)
}

// DOM挂载完成执行（等价 DOMContentLoaded）
onMounted(() => {
  updateHomeonetitle()
})

// 组件销毁，清除定时器，防止内存泄漏
onUnmounted(() => {
  if (timer !== null) {
    clearInterval(timer)
  }
})
</script>

<style scoped>
#one {
  display: flex;
  align-items: center;
  justify-content: center;
}

#HomeOneTitle {
  white-space: pre; /* 关键！让 \n \t 换行、制表符生效 */
  text-align: left; /* 行首对齐，制表符才能正确缩进第二行 */
  color: white;
  font-size: 48px;
}
</style>